"""Versioned CPU-only sampling for static film evidence."""
from bisect import bisect_left
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic


BASELINE = 'individual_seek'
CANDIDATE = 'select_in_one_decode'
FILTER = 'scale=960:-2'
COUNT = 12


def scope(metadata):
    picture = next(stream for stream in metadata['streams'] if stream['codec_type'] == 'video')
    return (picture.get('codec_name') == 'h264'
            and (picture.get('width'), picture.get('height')) == (1536, 864)
            and picture.get('r_frame_rate') == picture.get('avg_frame_rate') == '24/1'
            and picture.get('start_time') == metadata['format'].get('start_time') == '0.000000'
            and 2 <= float(metadata['format']['duration']) <= 15)


def profile_signature():
    return sha256(Path(__file__).read_bytes()).hexdigest()


def active_method(profile_path, metadata):
    if not profile_path.is_file() or not scope(metadata):
        return BASELINE
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    if profile['signature'] != profile_signature():
        return BASELINE
    if profile['method'] != CANDIDATE or profile['state'] != 'promoted':
        raise ValueError('抽帧工具当前版本没有完成回归验证。')
    return CANDIDATE


def samples(source, duration, output, method):
    times = [round(duration * offset / COUNT, 3) for offset in range(COUNT)]
    paths = [output / f'frame-{number:02d}.jpg' for number in range(1, COUNT + 1)]
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1']
    encoder = ['-threads', '1', '-q:v', '3', '-y']
    if method == BASELINE:
        for timecode, target in zip(times, paths, strict=True):
            subprocess.run([*command, '-ss', str(timecode), '-i', str(source), '-map', '0:v:0',
                            '-vf', FILTER, '-frames:v', '1', *encoder, str(target)],
                           capture_output=True, check=True, timeout=12)
    elif method == CANDIDATE:
        stamps = json.loads(subprocess.check_output([
            'ffprobe', '-v', 'error', '-threads', '1', '-select_streams', 'v:0',
            '-show_frames', '-show_entries', 'frame=best_effort_timestamp_time',
            '-of', 'json', str(source),
        ], timeout=15))
        timestamps = [Decimal(frame['best_effort_timestamp_time']) for frame in stamps['frames']]
        if not timestamps or timestamps[0] != 0 or any(a >= b for a, b in zip(timestamps, timestamps[1:])):
            raise ValueError('回片的视频时间戳不适用批量精确抽帧。')
        indices = [bisect_left(timestamps, Decimal(str(t))) for t in times]
        if len(set(indices)) != COUNT or indices[-1] >= len(timestamps):
            raise ValueError('该回片的抽帧时间点缺少对应画面。')
        selected = '+'.join(f'eq(n\\,{n})' for n in indices)
        subprocess.run([*command, '-i', str(source), '-map', '0:v:0',
                        '-vf', f'select={selected},{FILTER}', '-vsync', '0',
                        '-frames:v', str(COUNT), *encoder, str(output / 'frame-%02d.jpg')],
                       capture_output=True, check=True, timeout=20)
    else:
        raise ValueError('未知的抽帧工具。')
    return [(timecode, target.read_bytes()) for timecode, target in zip(times, paths, strict=True)]


def compare(source, duration, work):
    elapsed = {BASELINE: [], CANDIDATE: []}
    matches = []
    for order in ((BASELINE, CANDIDATE), (CANDIDATE, BASELINE)):
        round_samples = {}
        for method in order:
            with TemporaryDirectory(dir=work) as directory:
                start = monotonic()
                frames = samples(source, duration, Path(directory), method)
                elapsed[method].append(monotonic() - start)
                round_samples[method] = frames
        matches.append(round_samples[BASELINE] == round_samples[CANDIDATE])
    return {'same_frames_and_bytes': all(matches), 'frames': COUNT,
            'baseline_seconds': round(median(elapsed[BASELINE]), 4),
            'candidate_seconds': round(median(elapsed[CANDIDATE]), 4)}


def qualified(results):
    return (len(results) >= 3 and len({entry['project'] for entry in results}) >= 2
            and len({entry['source_sha256'] for entry in results}) == len(results)
            and all(entry['same_frames_and_bytes'] and entry['frames'] == COUNT
                    and entry['candidate_seconds'] <= 0.9 * entry['baseline_seconds'] for entry in results))
