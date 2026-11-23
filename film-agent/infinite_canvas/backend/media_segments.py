"""Shared FFmpeg preparation of one always independently cuttable video segment."""
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys


def prepare_segment(path, target, width, height, cut=None, narration=None, picture_filter=None, threads=None, normalize_clip=True):
    probe=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],capture_output=True,text=True,check=True,timeout=30)
    info=json.loads(probe.stdout)
    if not any(s['codec_type']=='video' for s in info['streams']):raise ValueError('素材没有视频轨道。')
    duration=float(info.get('format',{}).get('duration',0))
    if not 0<duration<=60:raise ValueError('镜头时长无效或超过 60 秒。')
    start = 0
    if cut is not None:
        start, end = cut
        if not 0 <= start < end <= duration:
            raise ValueError('剪辑时间超出镜头范围。')
        duration = end - start
    has_audio=any(s['codec_type']=='audio' for s in info['streams'])
    command=['ffmpeg','-v','error','-nostdin','-y','-ss',str(start),'-i',str(path)]
    if not has_audio:command+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
    if narration:
        voice, delay, tempo = narration
        command += ['-i', str(voice)]
    command+=['-map','0:v:0','-map','0:a:0' if has_audio else '1:a:0','-t',str(duration),'-vf',f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2',str(target)]
    if picture_filter:
        command[command.index('-vf') + 1] += ',' + picture_filter
    if narration:
        bed = '0:a:0' if has_audio else '1:a:0'
        voice_track = '1:a:0' if has_audio else '2:a:0'
        mix = (f'[{bed}]aformat=channel_layouts=stereo[bed];[{voice_track}]aformat=channel_layouts=mono,'
               f'pan=stereo|c0=0.707*c0|c1=0.707*c0,atempo={tempo:.6f},adelay={delay}:all=1,'
               f'apad=whole_dur={duration:g},asplit=2[vo][control];'
               '[bed][control]sidechaincompress=threshold=0.06:ratio=2:attack=14:release=180:mix=0.6[shaped];'
               '[shaped][vo]amix=inputs=2:duration=first:normalize=0,apad=pad_dur=0.005,alimiter=limit=0.85:attack=5:level=false,atrim=start=0.005,asetpts=PTS-STARTPTS[mix]')
        command[command.index('-map') + 3] = '[mix]'
        command[-1:-1] = ['-filter_complex', mix]
    elif cut is not None and has_audio and normalize_clip:
        command[-1:-1] = ['-af', 'loudnorm=I=-16:TP=-1.5:LRA=11']
    if threads:
        command[1:1] = ['-filter_threads', str(threads), '-filter_complex_threads', str(threads)]
        command[command.index('-i'):command.index('-i')] = ['-threads', str(threads)]
        command[-1:-1] = ['-threads', str(threads)]
    subprocess.run(command,capture_output=True,check=True,timeout=180)


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def concat_segments(paths, output, *, durations=None):
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='morpho-concat-', dir=output.parent) as work:
        root = Path(work)
        for index, path in enumerate(paths):
            (root / f'{index:04d}.mp4').symlink_to(path.resolve(strict=True))
        manifest = root / 'concat.txt'
        lines = []
        if durations is not None and (len(durations) != len(paths) or any(d <= 0 for d in durations)):
            raise ValueError('Explicit clip durations must cover all timeline segments.')
        for index in range(len(paths)):
            lines.append(f"file '{index:04d}.mp4'\n")
            if durations is not None:
                lines.append(f'duration {durations[index]:.9f}\n')
        manifest.write_text(''.join(lines))
        target = root / 'film.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'concat', '-safe', '1', '-i',
                        str(manifest), '-c', 'copy', '-movflags', '+faststart', str(target)],
                       capture_output=True, check=True, timeout=180)
        target.replace(output)


def encode_request(workspace, request):
    work = workspace / 'cpu-edit'
    media = workspace / 'output'
    cache = work / 'segments'
    cache.mkdir(exist_ok=True)
    key = request['key']
    if not re.fullmatch('[0-9a-f]{64}', key):
        raise ValueError('Invalid cache key.')
    output = cache / (key + '.mp4')
    if not output.exists():
        source = (workspace / request['source']).resolve(strict=True)
        if not (source.is_relative_to(media) or source.is_relative_to(work / 'staged')):
            raise ValueError('Source lies outside personal media directories.')
        if fingerprint(source) != request['source_sha256']:
            raise ValueError('Remote source differs from its approved local take.')
        voice = request['voice']
        if voice:
            path = (work / 'staged' / (voice[0] + '.wav')).resolve(strict=True)
            if not path.is_relative_to(work / 'staged') or fingerprint(path) != voice[0]:
                raise ValueError('Remote voice fingerprint mismatch.')
            voice = (path, *voice[1:])
        with TemporaryDirectory(prefix='encode-', dir=cache) as name:
            candidate = Path(name) / 'segment.mp4'
            prepare_segment(source, candidate, 1280, 720, request['cut'], voice, request['filter'], threads=2)
            candidate.replace(output)
    with output.open('rb') as stream:
        shutil.copyfileobj(stream, sys.stdout.buffer)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace', type=Path)
    args = parser.parse_args()
    root = args.workspace.resolve(strict=True)
    expected = Path('/mnt/AIRSSD/user_workspace/liaolin2')
    if not root.is_relative_to(expected) or root == expected:
        raise ValueError('Remote CPU work requires the personal project directory.')
    try:
        encode_request(root, json.load(sys.stdin))
    except subprocess.CalledProcessError as exc:
        sys.stderr.buffer.write(exc.stderr or b'')
        raise
