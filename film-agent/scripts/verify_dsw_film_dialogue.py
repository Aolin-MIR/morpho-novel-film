"""Independently transcribe the actual H3 audio on the DSW CPU."""
import argparse
import base64
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'infinite_canvas' / 'backend'))
from film_speech import mandarin_word_match, normalized_words

REMOTE = '/mnt/AIRSSD/user_workspace/liaolin2'
TRANSCRIBE = r"""
import base64
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from funasr import AutoModel
from funasr.utils.postprocess_utils import rich_transcription_postprocess

base = Path(sys.argv[1])
workspace = base / 'morpho-personal/projects'
if workspace.resolve(strict=True) != workspace:
    raise ValueError('Unexpected personal workspace')
recording = base64.b64decode(sys.stdin.buffer.read(), validate=True)
model = AutoModel(model=str(base / 'voice_lab/out/models/sensevoice'),
                  device='cpu', ncpu=4, disable_update=True)
with TemporaryDirectory(prefix='film-transcribe-', dir=workspace) as directory:
    wav = Path(directory) / 'sample.wav'
    wav.write_bytes(recording)
    rows = model.generate(input=str(wav), language='zh', use_itn=True, cache={}, batch_size_s=30)
    text = rich_transcription_postprocess(rows[0]['text'])
print('FILM_TRANSCRIPT ' + json.dumps(text, ensure_ascii=True), flush=True)
"""


def verify(media, expected, start, end, control_socket, *, on_dsw=False):
    if not normalized_words(expected):
        raise ValueError('请输入需要核对的台词。')
    media = media.resolve(strict=True)
    duration = float(subprocess.check_output(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(media)],
        text=True, timeout=15).strip())
    # Whole-source receipts conservatively protect all the original pauses.
    if end is None:
        end = duration
    if (not all(math.isfinite(t) for t in (start, end, duration))
            or not 0 <= start < end <= duration or end - start > 30):
        raise ValueError('对白审看须在原片范围内，并且不超过 30 秒。')
    # Decode before seeking so AAC edit boundaries use decoded sample timestamps.
    args = ['ffmpeg', '-v', 'error', '-nostdin', '-xerror', '-i', str(media),
            '-ss', str(start), '-t', str(end - start)]
    wav = subprocess.run(args + ['-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000', '-f', 'wav', '-'],
                         check=True, capture_output=True, timeout=45).stdout
    if not 0 < len(wav) <= 31 * 16000 * 2:
        raise ValueError('音频为空或超过30秒。请给出实际对白镜范围。')

    runtime = ':'.join(f'{REMOTE}/{part}' for part in (
        'voice_lab/out/runtime/sensevoice', 'audio_drama/.venv/lib/python3.12/site-packages',
        'morpho-h3/tools/image-deps'))
    settings = {'CUDA_VISIBLE_DEVICES': '', 'OMP_NUM_THREADS': '4', 'HF_HUB_OFFLINE': '1',
                'TRANSFORMERS_OFFLINE': '1', 'PYTHONPATH': runtime}
    if on_dsw:
        base = Path(REMOTE)
        if base.resolve(strict=True) != base or not media.is_relative_to(base):
            raise ValueError('转写输入须位于 DSW 个人工作区。')
        interpreter = (base / 'morpho-h3/tools/h3-image-python').read_text().strip()
        command = ['nice', '-n', '10', interpreter, '-c', TRANSCRIBE, REMOTE]
        env = {**os.environ, **settings}
    else:
        base = shlex.quote(REMOTE)
        run = shlex.join(['-c', TRANSCRIBE, REMOTE])
        assignments = ' '.join(f'{key}={shlex.quote(value)}' for key, value in settings.items())
        remote = (f'test "$(realpath {base})" = {base} && export {assignments} && '
                  f'nice -n 10 "$(cat {base}/morpho-h3/tools/h3-image-python)" {run}')
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=7', '-o', 'StrictHostKeyChecking=yes']
        if control_socket:
            command.extend(['-S', control_socket])
        command.extend(['work_personal', remote])
        env = None
    worker = subprocess.run(command, env=env, input=base64.b64encode(wav),
                            check=True, capture_output=True, timeout=80)
    marker = 'FILM_TRANSCRIPT '
    lines = [line[len(marker):] for line in worker.stdout.decode('utf-8').splitlines() if line.startswith(marker)]
    if len(lines) != 1:
        raise ValueError('CPU转写未返回唯一结果。')
    transcript = json.loads(lines[0])
    digest = sha256()
    with media.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    match = mandarin_word_match(expected, transcript)
    return {'file': media.name, 'sha256': digest.hexdigest(), 'model': 'SenseVoice',
            'start_seconds': start, 'end_seconds': end, 'expected': expected, 'transcript': transcript,
            'exact': match == 'exact', 'word_match': match}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('media', type=Path)
    parser.add_argument('expected')
    parser.add_argument('receipt', type=Path)
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--end', type=float)
    parser.add_argument('--control-socket')
    args = parser.parse_args()
    proof = verify(args.media, args.expected, args.start, args.end, args.control_socket)
    result = json.dumps(proof, ensure_ascii=False, indent=2) + '\n'
    temporary = args.receipt.with_suffix('.part')
    temporary.write_text(result, encoding='utf-8')
    temporary.replace(args.receipt)
    print(result, end='')
