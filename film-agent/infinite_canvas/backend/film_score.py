"""Licensed score choices and shot-aligned music placement."""
from array import array
from math import isfinite, sqrt
import subprocess

TRACKS = {
    'wonder': ('Floating Cities', 'USUAN1600018', '开阔、异世界、缓慢揭示'),
    'pressure': ('Lightless Dawn', 'USUAN1100655', '电子低频、渐进压力'),
    'afterglow': ('Leaving Home', 'USUAN1900002', '离别、余韵'),
    'unease': ('The Dread', 'USUAN1100048', '低音电子声场、异样感'),
    'reveal': ('Desert City', 'USUAN1100564', '遥远、异域空间'),
    'suspense': ('Unseen Horrors', 'USUAN1400043', '隐约威胁、黑暗环境'),
    'classical': ('Gymnopedie No. 1', 'USUAN1100787', '古典独奏钢琴、克制'),
    'jazz': ('I Knew a Guy', 'USUAN1100199', '慢速黑色爵士、冷幽默'),
    'orchestral': ('The Descent', 'USUAN1200094', '低铜管与弦乐、短促大场面'),
    'shoegaze': ('Gypsy Shoegazer No Voices', 'USUAN1200074', '朦胧吉他、后摇邻近质感'),
    'dream': ('Music Box Theme', 'USUAN1100417', '音乐盒、梦境和不安'),
    'still': ('Reawakening', 'USUAN1400017', '钢琴与大提琴、亲密和安静'),
}
LICENSE = 'https://creativecommons.org/licenses/by/4.0/'


def credits(track_ids):
    return [{'title': TRACKS[track][0], 'artist': 'Kevin MacLeod', 'license': 'CC BY 4.0',
             'license_url': LICENSE,
             'source': 'https://incompetech.com/music/royalty-free/index.html?Search=Search&isrc=' + TRACKS[track][1]}
            for track in dict.fromkeys(track_ids)]


def music_timeline(scenes, shots, cuts, directory, *, trailer=False):
    timings = {shot['id']: end - start for shot, (start, end) in zip(shots, cuts, strict=True)}
    elapsed = 0.0
    entries = []
    for scene in scenes:
        scene_shots = scene['shots']
        for cue in scene.get('music', []):
            start = elapsed + sum(timings[shot['id']] for shot in scene_shots[:cue['first'] - 1])
            duration = sum(timings[shot['id']] for shot in scene_shots[cue['first'] - 1:cue['last']])
            track = cue['track']
            path = directory / f'{track}.mp3'
            if not path.is_file():
                raise ValueError(f'本地音乐库缺少《{TRACKS[track][0]}》。')
            source_start = 0.0
            if trailer and 'accent_shot' in cue:
                impact = sum(timings[shot['id']] for shot in scene_shots[cue['first'] - 1:cue['accent_shot'] - 1])
                source_start = accent_offset(path, impact, duration)
            entries.append((path, start, duration, source_start))
        elapsed += sum(timings[shot['id']] for shot in scene_shots)
    return entries


def accent_offset(path, impact, duration):
    sample_rate = 8000
    window = sample_rate // 10
    try:
        result = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path), '-t', '45',
                                 '-map', '0:a:0', '-ac', '1', '-ar', str(sample_rate), '-f', 's16le', '-'],
                                check=True, capture_output=True, timeout=25)
    except subprocess.SubprocessError as exc:
        raise RuntimeError('无法分析本地预告片配乐。') from exc
    samples = array('h', result.stdout)
    energy = [sum(abs(value) for value in samples[i:i + window]) / window
              for i in range(0, len(samples) - window + 1, window)]
    candidates = []
    for index in range(max(3, round(impact * 10) + 1), min(round((impact + 16) * 10), len(energy) - 3)):
        offset = index / 10 - impact
        if offset + duration > len(energy) / 10:
            break
        before = sum(energy[index - 3:index]) / 3
        after = sum(energy[index:index + 3]) / 3
        if after >= 160 and after > before * 1.6:
            candidates.append((after - before, offset))
    return max(candidates)[1] if candidates else 0.0


def sound_timeline(scenes, shots, cuts, assets):
    timings = {}
    elapsed = 0.0
    for shot, (start, end) in zip(shots, cuts, strict=True):
        timings[shot['id']] = (elapsed, end - start)
        elapsed += end - start
    entries = []
    for scene in scenes:
        for cue in scene.get('sound_effects', []):
            shot_start, shot_duration = timings[cue['shot_id']]
            start = shot_start + cue['offset']
            duration = min(cue['duration'], elapsed - start)
            if not (0 <= start < shot_start + shot_duration and duration >= .08):
                raise ValueError(f'{cue["shot_id"]} 的空间音效入点已超出实剪时间，请校正触点。')
            entries.append((assets[(cue['source'], cue['duration'])], start, duration, cue))
    return entries


def spatial_filter(index, start, duration, cue):
    lowpass, distance_gain = {'near': (11000, 1), 'mid': (5300, .8), 'far': (2450, .6)}[cue['distance']]
    level = {'subtle': .18, 'normal': .30, 'peak': .56}[cue['level']] * distance_gain
    pan = cue['pan']
    left, right = sqrt((1 - pan) / 2), sqrt((1 + pan) / 2)
    # Short reflections preserve the direction of the dry sound.
    reflection = {'open': None, 'room': ('31|49', '0.19|0.12'), 'hall': ('67|101', '0.29|0.21')}[cue['space']]
    echo = f',aecho=0.9:0.75:{reflection[0]}:{reflection[1]}' if reflection else ''
    fade = min(.12, duration / 3)
    return (f'[{index}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono,'
            f'apad,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,lowpass=f={lowpass},'
            f'pan=stereo|c0={left:.4f}*c0|c1={right:.4f}*c0{echo},'
            f'atrim=duration={duration:.6f},'
            f'afade=t=in:d={min(.025, fade):.4f},afade=t=out:st={duration - fade:.4f}:d={fade:.4f},'
            f'volume={level:.4f},adelay={round(start * 1000)}:all=1[fx{index}]')


def voice_windows(shots, cuts, narration):
    """Resolve editorial silence and speech from the selected takes, not theme labels."""
    windows = []
    position = 0.
    for shot, (start, end), voice in zip(shots, cuts, narration, strict=True):
        length = end - start
        if shot.get('dialogue', '').strip():
            if voice is not None:
                raise ValueError(f'{shot["id"]} 的旁白和现场对白占同一镜。')
            windows.append((position, position + length))
        elif voice:
            path, delay, tempo = voice
            probe = subprocess.check_output(
                ['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                 '-show_entries', 'stream=duration', '-of', 'csv=p=0', str(path)],
                text=True, timeout=20)
            finish = delay / 1000 + float(probe.strip()) / tempo
            if not 0 <= delay / 1000 < finish <= length + .002:
                raise ValueError('实录旁白越过该镜的剪出点。')
            windows.append((position + delay / 1000, position + min(length, finish)))
        position += length
    return windows


def duck_at(windows, level):
    fade_in, fade_out = .12, .30
    groups = []
    if any(not isfinite(start) or not isfinite(end) or not 0 <= start < end
           for start, end in windows):
        raise ValueError('混音的对白与旁白时窗无效。')
    for start, end in sorted(windows):
        if groups and start < groups[-1][1] + fade_in + fade_out:
            groups[-1][1] = max(end, groups[-1][1])
        else:
            groups.append([start, end])
    if not groups:
        return 'anull'
    dips = [f'max(0,min(1,min((t-({start - fade_in:.6f}))/{fade_in},'
            f'({end + fade_out:.6f}-t)/{fade_out})))' for start, end in groups]
    return f"volume='1-{1 - level:.4f}*({'+'.join(dips)})':eval=frame"


def mix_score(source, output, entries, sound_entries=(), *, speech_windows=None):
    command = ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(source)]
    filters = []
    for index, (path, start, duration, source_start) in enumerate(entries, 1):
        command.extend(['-ss', f'{source_start:.3f}', '-stream_loop', '-1', '-i', str(path)])
        fade = min(1.2, duration / 3)
        filters.append(f'[{index}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,atrim=duration={duration:.6f},'
                       f'asetpts=PTS-STARTPTS,afade=t=in:d={fade:.4f},'
                       f'afade=t=out:st={duration - fade:.4f}:d={fade:.4f},'
                       f'volume=0.16,adelay={round(start * 1000)}:all=1[m{index}]')
    original = '[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo'
    tracks = []
    for index, (path, start, duration, cue) in enumerate(sound_entries, len(entries) + 1):
        command.extend(['-i', str(path)])
        filters.append(spatial_filter(index, start, duration, cue))
        tracks.append(f'[fx{index}]')
    filters.append(original + '[original]')
    if speech_windows is not None:
        if tracks:
            filters.append(f'{"".join(tracks)}amix=inputs={len(tracks)}:normalize=0,'
                           f'{duck_at(speech_windows, .38)}[effects]')
        buses = ['[original]'] + (['[effects]'] if tracks else [])
        if entries:
            labels = ''.join(f'[m{index}]' for index in range(1, len(entries) + 1))
            filters.append(f'{labels}amix=inputs={len(entries)}:normalize=0,'
                           f'{duck_at(speech_windows, .26)}[music]')
            buses.append('[music]')
        filters.append(f'{"".join(buses)}amix=inputs={len(buses)}:duration=first:normalize=0[sum]')
        mix = '[sum]'
    else:
        mix = legacy_mix(filters, tracks, len(entries))
    filters.append(mix + 'apad=pad_dur=0.005,alimiter=limit=0.84:attack=5:level=false,atrim=start=0.005,asetpts=PTS-STARTPTS[final]')
    command += ['-filter_complex', ';'.join(filters), '-map', '0:v:0', '-map', '[final]',
                '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(output)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=300)
    except subprocess.SubprocessError as exc:
        raise RuntimeError('成片混音失败，已保留本地素材。') from exc


def legacy_mix(filters, tracks, count):
    tracks = ['[original]', *tracks]
    filters.append(f'{"".join(tracks)}amix=inputs={len(tracks)}:duration=first:normalize=0[dry]')
    if count:
        labels = ''.join(f'[m{index}]' for index in range(1, count + 1))
        filters += [f'{labels}amix=inputs={count}:normalize=0,apad[score]',
                    '[dry]asplit=2[foreground][detector]',
                    '[score][detector]sidechaincompress=threshold=0.028:ratio=6:attack=15:release=420[ducked]',
                    '[foreground][ducked]amix=inputs=2:duration=first:normalize=0[sum]']
        mix = '[sum]'
    else:
        mix = '[dry]'
    return mix
