"""Reusable film production guidance and versioned evidence inventory."""
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

STAGES = frozenset(('planning', 'capture', 'edit', 'review', 'delivery'))
RULES = Path(__file__).with_suffix('.json')
ART = Path(__file__).resolve().parents[2] / 'skills' / 'film-art-direction'
CURRENT = ContextVar('film_production_experience', default=None)

FAILURE = 'reference_background'
GUIDANCE = ('人物图只用于身份、脸型、发型和已锁的服装。场景位置与光向只取自现场参考。'
            '人物图里的家具、植被、文字及装饰不属于当前场景。先使用干净的人物板，并保留当前场景的地标。')


def prepare_evidence(db):
    db.execute('''CREATE TABLE IF NOT EXISTS film_reference_trials (
        id INTEGER PRIMARY KEY, owner TEXT NOT NULL, project TEXT NOT NULL, shot TEXT NOT NULL,
        take TEXT NOT NULL, mode TEXT NOT NULL, verdict TEXT NOT NULL, baseline TEXT, media TEXT NOT NULL,
        UNIQUE(owner, project, shot, take)
    )''')


def record(db, owner, project, shot, trial, verdict, media):
    mode = trial['mode']
    baseline = trial.get('baseline_sha256')
    if verdict.get('method') != 'omni' or mode not in {'paired', 'adopted'}:
        return
    if mode == 'paired' and (not baseline or baseline == media):
        raise ValueError('比较试镜须提供两份各自独立的媒体证据。')
    if verdict.get('accepted') is True:
        outcome = 'pass'
    elif verdict.get('failure_kind') == FAILURE:
        outcome = 'regression'
    else:
        return
    with db:
        prepare_evidence(db)
        db.execute('''INSERT INTO film_reference_trials(owner,project,shot,take,mode,verdict,baseline,media)
                      VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(owner,project,shot,take) DO NOTHING''',
                   (owner, project, shot, media, mode, outcome, baseline, media))


def approved(db, owner, project):
    prepare_evidence(db)
    count, distinct_media = db.execute('''
        SELECT count(DISTINCT project), count(DISTINCT media) FROM film_reference_trials
        WHERE owner=? AND project<>? AND mode='paired' AND verdict='pass'
          AND id>coalesce((SELECT max(id) FROM film_reference_trials
                           WHERE owner=? AND mode='adopted' AND verdict='regression'), 0)
    ''', (owner, project, owner)).fetchone()
    return count >= 2 and distinct_media >= 2


def document(stage):
    if stage not in STAGES:
        raise ValueError('须指定已登记的影片工作阶段。')
    raw = RULES.read_bytes()
    data = json.loads(raw)
    rules = data['rules']
    if (not data['version'] or len({r['id'] for r in rules}) != len(rules)
            or any(not r['id'] or not r['guidance'].strip() or not r['stages']
                   or not set(r['stages']) <= STAGES for r in rules)
            or set().union(*(set(r['stages']) for r in rules)) != STAGES):
        raise ValueError('制作经验版本的规则不完整。')
    return {'version':data['version'], 'sha256':sha256(raw).hexdigest(), 'stage':stage,
            'rules':[{'id':r['id'], 'guidance':r['guidance']} for r in rules if stage in r['stages']]}


def inventory():
    references = ART / 'references'
    library = json.loads((references/'catalog.json').read_text(encoding='utf-8'))
    verified = json.loads((references/'verified-methods.json').read_text(encoding='utf-8'))
    return {'catalog_entries':len(library), 'reviewed_methods':len(verified['methods']),
            'art_negative_examples':len(verified['negative_evidence'])}


def methods(needs, conditions):
    if not needs:
        return None
    if (not isinstance(needs, list) or not isinstance(conditions, list)
            or len(needs) > 4 or len(conditions) > 20
            or any(not isinstance(word, str) or not word.strip() or len(word) > 80
                   for word in (*needs, *conditions))):
        raise ValueError('美学方法只接受登记的需求和已确认场景条件。')
    args = [sys.executable, str(ART/'scripts/select_verified_methods.py')]
    for flag, words in (('--need', needs),('--conditions', conditions)):
        for word in words:
            args.append(f'{flag}={word}')
    result = json.loads(subprocess.check_output(args,text=True,timeout=20))
    result['candidates'] = [{key:row[key] for key in (
        'id','title','scope','production','required_conditions','unmet_conditions','verified_result',
        'mechanism_scope','unverified_claims')}
        for row in result['candidates'][:4]]
    result['excluded_due_to_conflict'] = [row['id'] for row in result['excluded_due_to_conflict']]
    return result


@contextmanager
def applying(stage, *, needs=(), conditions=()):
    experience = document(stage)
    selected = methods(list(needs),list(conditions))
    if selected is not None:
        experience['method_candidates'] = selected
    token = CURRENT.set(experience)
    try:
        yield experience
    finally:
        CURRENT.reset(token)
