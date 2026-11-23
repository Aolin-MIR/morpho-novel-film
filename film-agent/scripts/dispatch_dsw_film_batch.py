"""Stage a harness-authored slate on DSW and register every shot for local readback."""
import argparse
import fcntl
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'infinite_canvas' / 'backend'))
from film_continuity import validate_slate
from h3_prompt import compile_reference_prompt, with_current_prompt_policy
from film_art_routes import assign, capture_contract
from film_reference_checks import check_reference
from h3_reference_plate import derive_plate
from readback_dsw_film import REMOTE, SSH, checksum

SSH_STDIN = [arg for arg in SSH if arg != '-n']


STUDIO_UPDATE = r"""
from pathlib import Path
import fcntl
import json
import sys

directory=Path(sys.argv[1])
directory.mkdir(mode=0o700,exist_ok=True)
patch=json.load(sys.stdin)
with (directory/'jobs.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    path=directory/'jobs.json'
    existing=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    existing.update(patch)
    temporary=path.with_suffix('.part')
    temporary.write_text(json.dumps(existing,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)
"""


def publish_records(rows):
    if rows:
        remote=shlex.join(['python3','-c',STUDIO_UPDATE,REMOTE+'/studio-data'])
        subprocess.run([*SSH_STDIN, remote],input=json.dumps(rows,ensure_ascii=False).encode(),
                       check=True,timeout=25)


def register_readback(manifest, identifier, destination):
    # Multiple batches can finish uploading while the same watcher is reading the manifest.
    with manifest.with_suffix('.write.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        known = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {}
        if identifier in known and known[identifier] != destination:
            raise ValueError('同一任务已登记到不同的归档路径。')
        known[identifier] = destination
        temporary = manifest.with_suffix('.part')
        temporary.write_text(json.dumps(known, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(manifest)


def dispatch(slate, manifest, *, preflight_only=False, require_continuity=False):
    rows = json.loads(slate.read_text(encoding='utf-8'))
    validate_slate(rows, required=require_continuity)
    assets = {}
    jobs = []
    destinations = []
    art_choices = []
    plate_histories = []
    for row in rows:
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9-]{1,42}', row['shot']):
            raise ValueError('镜号格式无效。')
        methods = assign(row['art']) if 'art' in row else []
        art_choices.append(methods)
        shot = with_current_prompt_policy(capture_contract(row['contract'], methods))
        motion = row.get('motion')
        compile_reference_prompt(shot, row['duration'], len(row['references']), len(row.get('voices', [])), motion is not None)
        contract = json.dumps(shot, ensure_ascii=False, sort_keys=True).encode()
        contract_name = sha256(contract).hexdigest() + '.json'
        assets[contract_name] = contract
        def stage_path(value):
            path = Path(value).resolve(strict=True)
            name = checksum(path) + path.suffix.lower()
            assets[name] = path
            return f'morpho-personal/film-agent/inputs/film-batch/{name}'
        references, history = [], []
        for position, value in enumerate(row['references'], 1):
            picture = shot['pictures'][position - 1]
            if isinstance(value, str):
                if 'continuity' in shot:
                    check_reference(picture, Path(value))
                references.append(stage_path(value))
            else:
                contents, origin = derive_plate(value)
                if 'continuity' in shot:
                    check_reference(picture, contents)
                name = origin['plate_sha256'] + '.png'
                assets[name] = contents
                references.append(f'morpho-personal/film-agent/inputs/film-batch/{name}')
                history.append({'picture': position, **origin})
        plate_histories.append(history)
        inputs = {
            'shot_contract': f'morpho-personal/film-agent/inputs/film-batch/{contract_name}',
            'references': references,
            'voices': [stage_path(value) for value in row.get('voices', [])],
            'duration': row['duration'], 'quality': 'final', 'size': '1536x864',
        }
        if motion is not None:
            motion_path = Path(motion).resolve(strict=True)
            if motion_path.suffix.lower() != '.mp4' or not 0 < motion_path.stat().st_size < 90_000_000:
                raise ValueError('动作参考须为小于 90 MB 的 MP4。')
            inputs['motion'] = stage_path(motion_path)
        jobs.append({'project': row['project'], 'lane': row['lane'], 'kind': 'render', 'inputs': inputs})
        targets = Path(row['output']).resolve()
        if not targets.is_relative_to(Path('/mnt/e/speed/book-previews')):
            raise ValueError('本地产物应位于影片归档目录。')
        destinations.append(str(targets))

    if preflight_only:
        return len(jobs)

    manifest.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as temporary:
        payload = Path(temporary) / 'inputs.tar'
        with tarfile.open(payload, 'w') as bundle:
            for name, contents in sorted(assets.items()):
                if isinstance(contents, bytes):
                    info = tarfile.TarInfo(name)
                    info.size = len(contents)
                    bundle.addfile(info, BytesIO(contents))
                else:
                    bundle.add(contents, arcname=name, recursive=False)
        folder = shlex.quote(REMOTE + '/inputs/film-batch')
        install = (
            f'set -eu; umask 077; mkdir -p {folder}; '
            f'stage=$(mktemp -d {folder}/.stage.XXXXXXXX); '
            "trap 'rm -rf -- \"$stage\"' EXIT; "
            'tar -C "$stage" -xf -; '
            f'for file in "$stage"/*; do target={folder}/"${{file##*/}}"; '
            'if ! ln -- "$file" "$target" 2>/dev/null; then cmp -- "$file" "$target"; fi; done')
        with payload.open('rb') as source:
            subprocess.run([*SSH_STDIN, install], stdin=source, check=True, timeout=150)
    agent = REMOTE + '/code/scripts/dsw_film_agent.py'
    records = {}
    for job, destination, row, art, history in zip(jobs, destinations, rows, art_choices, plate_histories, strict=True):
        identifier = subprocess.check_output(
            [*SSH_STDIN, 'python3', agent, 'submit'],
            input=json.dumps(job, ensure_ascii=False).encode(), timeout=25).decode().strip()
        if not re.fullmatch('[a-f0-9]{24}', identifier):
            raise ValueError('DSW 未返回可核对的任务编号。')
        register_readback(manifest, identifier, destination)
        editorial = row.get('record') or {}
        records[identifier] = {'name': editorial.get('name', row['shot']), 'section': editorial.get('section', ''),
                               'intent': editorial.get('intent', row['contract']['outcome']),
                               'skill': editorial.get('skill', 'H3 参考分工和原声表演'),
                               'change': editorial.get('change', '')}
        if history:
            records[identifier]['reference_lineage'] = history
        if art:
            records[identifier]['skill'] = '；'.join(item['name'] for item in art)
            records[identifier]['art'] = art
        print(identifier, job['lane'], destination, flush=True)
    publish_records(records)


def queue_dialogue(slate, manifest):
    rows = json.loads(slate.read_text(encoding='utf-8'))
    known = json.loads(manifest.read_text(encoding='utf-8'))
    by_path = {path: identifier for identifier, path in known.items()}
    agent = REMOTE + '/code/scripts/dsw_film_agent.py'
    for row in rows:
        speech = row['contract'].get('speech')
        if not speech:
            continue
        destination = Path(row['output']).resolve()
        shot_id = by_path[str(destination)]
        shot = f'morpho-personal/film-agent/artifacts/{row["project"]}/{shot_id}/shot.mp4'
        spec = {'project': row['project'], 'kind': 'dialogue-evidence', 'dependencies': [shot_id],
                'inputs': {'source': shot, 'lines': (
                    [{'in': line['window'][0], 'out': line['window'][1], 'text': line['text'],
                      'picture': line['picture']} for line in speech]
                    if 'continuity' in row['contract'] else
                    [{'in': 0, 'out': None, 'text': ' '.join(line['text'] for line in speech)}])}}
        identifier = subprocess.check_output(
            [*SSH_STDIN, 'python3', agent, 'submit'],
            input=json.dumps(spec, ensure_ascii=False).encode(), timeout=25).decode().strip()
        if not re.fullmatch('[a-f0-9]{24}', identifier):
            raise ValueError('DSW 未返回可核对的对白任务编号。')
        register_readback(manifest, identifier, str(destination.with_name(f'{row["shot"]}-同期证据.json')))
        print(row['shot'], identifier, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('slate', type=Path)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--dialogue-only', action='store_true')
    parser.add_argument('--preflight', action='store_true')
    continuity = parser.add_mutually_exclusive_group()
    continuity.add_argument('--require-continuity', dest='require_continuity', action='store_true')
    continuity.add_argument('--legacy-slate', dest='require_continuity', action='store_false',
                            help='仅用于按原规格重新提交已经独立核对的旧拍摄单。')
    parser.set_defaults(require_continuity=True)
    args = parser.parse_args()
    if args.dialogue_only and args.preflight:
        parser.error('预检与对白派单互斥。')
    if args.dialogue_only:
        queue_dialogue(args.slate, args.manifest)
    else:
        count = dispatch(args.slate, args.manifest, preflight_only=args.preflight,
                         require_continuity=args.require_continuity)
        if args.preflight:
            print(f'{count} 镜通过派单预检')


if __name__ == '__main__':
    main()
