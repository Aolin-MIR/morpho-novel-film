"""Render an H3 pilot through the same DSW adapter as the Morpho production queue."""
import argparse
import base64
from hashlib import sha256
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'infinite_canvas' / 'backend'))
import requests

import rented_h3
from film_direction import bind_direction_shots, picture_direction
from film_reference_checks import check_reference
from h3_prompt import compile_reference_prompt
from video_providers import RejectedRequest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, action='append', required=True)
    parser.add_argument('--motion', type=Path)
    parser.add_argument('--voice-reference', type=Path, action='append', default=[])
    parser.add_argument('--direction-plan', type=Path)
    parser.add_argument('--direction-shot', type=int)
    prompt_args = parser.add_mutually_exclusive_group(required=True)
    prompt_args.add_argument('--prompt-file', type=Path)
    prompt_args.add_argument('--shot-contract', type=Path)
    parser.add_argument('--wait-weights', type=int, default=0)
    parser.add_argument('--final-quality', action='store_true')
    parser.add_argument('--size', default='832x480')
    parser.add_argument('--duration', type=float, default=6)
    parser.add_argument('output', type=Path)
    args = parser.parse_args(argv)
    try:
        size = tuple(map(int, args.size.split('x')))
        if len(size) != 2:
            raise ValueError
    except ValueError:
        parser.error('--size must be WIDTHxHEIGHT')
    if args.shot_contract:
        if args.direction_plan is not None or args.direction_shot is not None:
            parser.error('镜头规格已经包含摄影、动作和音轨。')
        shot = json.loads(args.shot_contract.read_text(encoding='utf-8'))
        prompt = compile_reference_prompt(shot, args.duration, len(args.reference),
                                          len(args.voice_reference), bool(args.motion))
        if 'continuity' in shot:
            for picture, source in zip(shot['pictures'], args.reference, strict=True):
                check_reference(picture, source)
    else:
        prompt = args.prompt_file.read_text(encoding='utf-8')
    if (args.direction_plan is None) != (args.direction_shot is None):
        parser.error('--direction-plan and --direction-shot must be used together')
    if args.direction_plan is not None:
        plan = json.loads(args.direction_plan.read_text(encoding='utf-8'))
        rows = plan['shots']
        if not 1 <= args.direction_shot <= len(rows):
            parser.error('--direction-shot is out of range')
        selected = args.direction_shot - 1
        shots = [{'clip_seconds': row.get('clip_seconds', args.duration),
                  'spectacle': row.get('spectacle')} for row in rows]
        if 'camera_plan' in rows[selected] and shots[selected]['clip_seconds'] != args.duration:
            parser.error('--duration must match the selected camera plan')
        bind_direction_shots(shots, rows, plan['profile'])
        direction = shots[selected]['direction']
        prompt += '\n' + picture_direction(direction)
        if direction['music'] == 'space':
            prompt += '\n保留现场环境和动作声，音乐留白。'
        else:
            prompt += '\n仅生成同期声，配乐由后期沿剪辑时间线加入。'
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    state = output.with_suffix('.job.json')
    if output.exists():
        raise FileExistsError(output)
    preview = not args.final_quality
    inputs = {
        'prompt': sha256(prompt.encode('utf-8')).hexdigest(),
        'pictures': [sha256(path.read_bytes()).hexdigest() for path in args.reference],
        'voice': [sha256(path.read_bytes()).hexdigest() for path in args.voice_reference],
        'motion': sha256(args.motion.read_bytes()).hexdigest() if args.motion is not None else None,
        'size': list(size), 'duration': args.duration, 'preview': preview,
    }
    if state.exists():
        receipt = json.loads(state.read_text())
        if 'inputs' in receipt and inputs != receipt['inputs']:
            raise ValueError('现有任务的原始输入不同。保留任务编号，使用新镜头版本的输出路径。')
        identifier = receipt['id']
        base = rented_h3.ensure_tunnel()
    else:
        base = rented_h3.ensure_tunnel()
        deadline = time.monotonic() + args.wait_weights
        while not rented_h3.ready(base, motion=bool(args.motion), preview=preview, voice_count=len(args.voice_reference)):
            if time.monotonic() >= deadline:
                raise SystemExit('ComfyUI has not registered the requested H3 weights.')
            time.sleep(12)
        identifier = str(uuid.uuid4())
        with state.open('x') as file:
            json.dump({'id': identifier, 'preview': preview, 'inputs': inputs, 'submitted_prompt': prompt}, file, ensure_ascii=False)
        refs = ['data:image/;base64,' + base64.b64encode(image.read_bytes()).decode('ascii')
                for image in args.reference]
        try:
            rented_h3.submit(base, prompt, args.duration, '16:9', refs,
                             motion_reference=args.motion, voice_references=args.voice_reference, preview=preview,
                             size=size, seed=62197421, identifier=identifier)
        except RejectedRequest:
            state.unlink()
            raise
    print(identifier, flush=True)
    deadline = time.monotonic() + 2100
    previous = ''
    connection_errors = 0
    while time.monotonic() < deadline:
        try:
            status = rented_h3.poll(base, identifier)
            if status == 'completed':
                rented_h3.download(base, identifier, output)
        except requests.RequestException:
            connection_errors += 1
            if connection_errors == 3:
                raise
            time.sleep(6)
            continue
        connection_errors = 0
        if status != previous:
            print(status, flush=True)
            previous = status
        if status == 'completed':
            print(output, flush=True)
            return
        if status not in {'queued', 'running'}:
            with rented_h3.session() as client:
                result = rented_h3.history(client, base, identifier)
            raise RuntimeError((result or {}).get('status', status))
        time.sleep(5)
    raise TimeoutError(identifier)


if __name__ == '__main__':
    main()
