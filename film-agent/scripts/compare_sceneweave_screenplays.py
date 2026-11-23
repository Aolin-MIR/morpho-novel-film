"""Run the production trailer path and compare its text with a paired archived cut."""
import argparse
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import patch
import json
import sys
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'infinite_canvas' / 'backend'))
import novel_film
from dsw_film_agent import configure_text, runtime
from novel_adaptation import read_novel

CRITERIA = ('opening', 'causality', 'fidelity', 'dialogue', 'editing', 'ending')


def store(path, document):
    with NamedTemporaryFile('w', dir=path.parent, encoding='utf-8', delete=False) as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
        temporary = Path(stream.name)
    temporary.replace(path)


def original_review(slug):
    opener = build_opener(ProxyHandler({}))
    with opener.open(f'http://127.0.0.1:18824/api/review-films/{slug}', timeout=15) as response:
        film = json.load(response)
    # Compare the first minute when the old edition is a whole-book feature.
    scenes = []
    for scene in film['script']['scenes']:
        if scene['start'] >= 60:
            break
        scenes.append({
            'at': scene['start'], 'action': scene['action'],
            'dialogue': [row['text'] for row in scene['lines'] if row['kind'] == '对白' and row['start'] < 60],
            'narration': [row['text'] for row in scene['lines'] if row['kind'] == '旁白' and row['start'] < 60],
        })
    return {'title': film['title'], 'sha256': film['sha256'], 'full_duration': film['duration'],
            'duration_compared': min(60, film['duration']), 'scenes': scenes}


def fresh_review(package):
    scene = package['scenes'][0]
    film = package['film']
    cues = {cue['shot_id']: cue['text'] for cue in film['narration']['cues']}
    output = []
    position = 0
    for shot in scene['shots']:
        beat = shot['screenplay']['event_id']
        output.append({
            'at': position, 'action': shot['screen_action'], 'proof': shot['source_excerpt'],
            'cut': shot['handoff'], 'dialogue': [f"{shot['dialogue_speaker']}：{shot['dialogue']}"] if shot['dialogue'] else [],
            'narration': [cues[shot['id']]] if shot['id'] in cues else [],
            'purpose': film['screenplay']['beats'][beat]['turn'],
        })
        position += shot['clip_seconds']
    return {'title': scene['title'], 'duration_compared': position, 'question': film['audience_question'],
            'scenes': output}


def assess(title, original, candidate, call):
    report = call(
        '独立比对同一本书的两个台本。评估文字能直接提供的叙事作用。基线可能是整书成片的前一分钟，'
        '这种情况下只比开场力度、因果、对白可听性及剪辑设计。结尾一项只比各自这一分钟的停点。'
        '不要评估实际演员、嘴型、光线或音乐效果。具体写秒数及可见/可听证据。'
        '六项 opening、causality、fidelity、dialogue、editing、ending 每项判 A、B 或 tie。'
        '若有不合格项，patch 给只修故障的办法，优先保留原著事件与已成立的现稿优点。'
        '返回 JSON {"criteria":{"opening":{"winner":"A","evidence":"动作和秒数"},'
        '"causality":{},"fidelity":{},"dialogue":{},"editing":{},"ending":{}},"blocking":["真实剧情缺陷"],'
        '"patch":"可执行的修订或空串"}。',
        {'title': title, 'A': original, 'B': candidate}, {}, 5600)
    rows = report.get('criteria')
    if not isinstance(rows, dict) or set(rows) != set(CRITERIA) or any(
            not isinstance(row, dict) or row.get('winner') not in {'A', 'B', 'tie'}
            or not isinstance(row.get('evidence'), str) or not row['evidence'].strip() for row in rows.values()):
        raise ValueError('独立审稿缺少逐项证据。')
    if not isinstance(report.get('blocking'), list) or not isinstance(report.get('patch'), str):
        raise ValueError('独立审稿缺少阻断问题和修改结果。')
    question = candidate['question']
    if question.count('？') + question.count('?') > 1:
        report['blocking'].append('现稿明确并列两个观众问题，宣传片主冲突不够集中。')
        report['patch'] += '\n将两条线改为同一个问题的原因与后果。保留有作用的视听证据。'
    report['verdict'] = ('needs_revision' if report['blocking'] or
                         sum(row['winner'] == 'B' for row in rows.values()) <
                         sum(row['winner'] == 'A' for row in rows.values()) else 'at_least_comparable')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--style', required=True, choices=('night-jade', 'printmaking'))
    parser.add_argument('--text-revision', type=Path, help='Apply an editor’s written notes to the locked screenplay.')
    parser.add_argument('--screenplay', type=Path, help='Render a shot plan from this reviewed trailer screenplay.')
    args = parser.parse_args()
    if args.screenplay and args.text_revision:
        parser.error('Choose either an existing screenplay or revision notes.')
    runtime("text")
    configure_text()
    source, _ = read_novel(args.source.read_bytes(), args.source.name)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    cache = output / 'checkpoints'
    cache.mkdir(exist_ok=True)
    caller = novel_film.call_json

    def call(instruction, context, settings, budget):
        key = sha256(json.dumps([instruction, context, settings, budget], ensure_ascii=False).encode()).hexdigest()
        receipt = cache / (key + '.json')
        if receipt.exists():
            return json.loads(receipt.read_text(encoding='utf-8'))
        data = caller(instruction, context, settings, budget)
        store(receipt, data)
        return data

    original = original_review(args.baseline)
    store(output / 'original.json', original)
    if args.text_revision:
        previous = json.loads((output / 'screenplay.json').read_text(encoding='utf-8'))
        notes = args.text_revision.read_text(encoding='utf-8').strip()
        if not notes:
            raise ValueError('台本修订须提供具体编辑意见。')
        settings = novel_film.options({'style': args.style, 'adapter': 'minimax', 'mode': 'references'})
        settings['style_guide'] = next(s['guide'] for s in novel_film.STYLES if s['id'] == settings['style'])
        with patch.object(novel_film, 'call_json', call):
            context, editorial, script = novel_film.prepare_short_screenplay(
                source, args.title, 'trailer', settings, lambda *words: print(*words, flush=True),
                revision={'notes': notes, 'previous_editorial': previous['editorial'],
                          'previous': previous['screenplay']})
        store(output / 'editorial-revision.json',
              {'title': args.title, 'source_sha256': previous['source_sha256'],
               'notes': notes, 'source': context, 'editorial': editorial, 'screenplay': script})
        return
    prepared = json.loads(args.screenplay.read_text(encoding='utf-8')) if args.screenplay else None
    feedback = None
    iterations = []
    with patch.object(novel_film, 'call_json', call):
        for attempt in range(1, 4):
            print(args.title, '台本与分镜修订轮次', attempt, flush=True)
            package = novel_film.generate({}, source, args.title, 'trailer', {'style': args.style},
                                          lambda *words: print(*words, flush=True),
                                          on_screenplay=lambda script: store(output / 'screenplay.json', script),
                                          screenplay_revision=feedback,
                                          prepared_screenplay=prepared if attempt == 1 else None)
            candidate = fresh_review(package)
            report = assess(args.title, original, candidate, call)
            iterations.append({'attempt': attempt, 'report': report, 'candidate': candidate})
            store(output / 'film-plan.json', package)
            store(output / 'comparison.json', {'iterations': iterations, **report})
            print(args.title, report['verdict'], flush=True)
            if report['verdict'] == 'at_least_comparable':
                break
            if not report['patch'].strip():
                raise ValueError('审稿指出了问题，需要给出具体修改后再试。')
            feedback = {'diagnosis': report['blocking'], 'notes': report['patch'],
                        'previous_editorial': package['film']['promo_editorial'],
                        'previous': candidate}


if __name__ == '__main__':
    main()
