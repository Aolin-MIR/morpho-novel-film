"""Select available film methods by story evidence and the current camera setup."""
import argparse
import json
from pathlib import Path
from unicodedata import normalize

CATALOG = (Path(__file__).resolve().parents[1] / 'skills' / 'film-art-direction'
           / 'references' / 'catalog.json')
VERIFIED_METHODS = CATALOG.with_name('verified-methods.json')


def catalog():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def glitch_is_readable(intent):
    return (isinstance(intent, dict) and intent.get('mode') in ('absurd', 'visibly-stylized')
            and all(isinstance(intent.get(key), str) and intent[key].strip()
                    for key in ('setup', 'trigger', 'pattern', 'payoff')))


def normalized_cue(text):
    return ' '.join(normalize('NFKC', text).casefold().split())


def matches_condition(condition, observed):
    return any(normalized_cue(text) in observed
               for text in [condition['key'], *condition['keywords']])


def verified_choices(observed, catalog_ids, glitch_ids, glitch_intent):
    registry = json.loads(VERIFIED_METHODS.read_text(encoding='utf-8'))
    if registry['schema_version'] != 1:
        raise ValueError('已审美学方法的证据版本不受支持。')
    choices = []
    seen = set(catalog_ids)
    for row in registry['methods']:
        identifier = row['id']
        if identifier in seen:
            raise ValueError(f'美学机制 ID 重复：{identifier}')
        seen.add(identifier)
        if row['scope'] != 'single-scene-tested':
            continue
        if not all(matches_condition(condition, observed) for condition in row['required_conditions']):
            continue
        if any(matches_condition(condition, observed) for condition in row['avoid_conditions']):
            continue
        linked_ids = {row['method_id'], *row['related_method_ids']}
        if (linked_ids & glitch_ids or row.get('requires_glitch_intent')) and not glitch_is_readable(glitch_intent):
            continue

        evidence = row['evidence']
        production = row.get('production')
        if production not in {'h3', 'cpu-edit'}:
            raise ValueError(f'不支持的美学制作路径：{production}')
        choices.append({**row, 'name': row['title'], 'source': 'verified-registry', 'production': production})
    return choices


def select(cues, *, glitch_intent=None):
    if not isinstance(cues, list) or not all(isinstance(item, str) and item for item in cues):
        raise ValueError('使用一组具体场面线索检索。')
    evidence = set(cues)
    entries = catalog()
    legacy = [item for item in entries
              if item['status'] == '当前可用' and set(item['signals']) <= evidence
              and (not item.get('requires_glitch_intent') or glitch_is_readable(glitch_intent))]
    return legacy + verified_choices(
        {normalized_cue(cue) for cue in cues},
        {item['id'] for item in entries},
        {item['id'] for item in entries if item.get('requires_glitch_intent')},
        glitch_intent,
    )


def assign(art):
    eligible = {item['id']: item for item in select(art['cues'], glitch_intent=art.get('glitch_intent'))}
    methods = art['methods']
    if not isinstance(methods, dict) or not methods:
        raise ValueError('逐镜美学选择须写明使用的方法以及画面执行。')
    if any(key not in eligible or not isinstance(value, str) or not value.strip()
           for key, value in methods.items()):
        raise ValueError('所选美学手法缺少适用线索或画面执行说明。')
    capture = art.get('capture', {})
    if not isinstance(capture, dict) or any(key not in methods or not isinstance(note, str)
                                            or not note.strip() for key, note in capture.items()):
        raise ValueError('底片拍法须对应本镜选定的美学方法，并说明具体表演或摄影。')
    records = []
    for key, execution in methods.items():
        selected = eligible[key]
        registry_entry = selected.get('source') == 'verified-registry'
        record = {'name': f"{key} · {selected['name']}", 'execution': execution.strip(), 'id': key,
                  'method_id': selected['method_id'] if registry_entry else key,
                  'source': 'verified-registry' if registry_entry else 'catalog'}
        if registry_entry:
            record.update({field: selected[field]
                           for field in ('directions', 'classification_status', 'classification_basis')})
            for field in ('scope', 'evidence', 'production', 'mechanism_scope', 'unverified_claims',
                          'sources', 'edit_timeline'):
                if field in selected:
                    record[field] = selected[field]
        if key in capture:
            record['capture'] = capture[key].strip()
        records.append(record)
    return records


def capture_contract(contract, methods):
    """Include only selected on-set directions in the model input, leaving editorial work to post."""
    if any(item.get('production') == 'cpu-edit' for item in methods):
        raise ValueError('CPU 剪辑方法须在后期执行，不能直接派发为 H3 镜头。')
    notes = [item['capture'] for item in methods if 'capture' in item]
    if not notes:
        return contract
    return {**contract, 'action': ' '.join([contract['action'], *notes])}



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scene', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    scene = json.loads(args.scene.read_text(encoding='utf-8'))
    choices = select(scene['cues'], glitch_intent=scene.get('glitch_intent'))
    result = {'shot': scene['shot'], 'intent': scene['intent'], 'choices': choices}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
