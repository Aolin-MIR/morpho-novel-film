"""Private read-through view of DSW shots, creative records and archival receipts."""
from contextlib import closing
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / 'infinite_canvas' / 'backend'))
from film_art_routes import catalog
from h3_prompt import compile_reference_prompt
from film_speech import H3_SPEECH, mandarin_word_match
from film_script import validate_script
from publish_film_screening import SLUG, current_editions, media_digest
from study_classification import classification_fields

PERSONAL = Path('/mnt/AIRSSD/user_workspace/liaolin2')
ROOT = PERSONAL / 'morpho-personal' / 'film-agent'
STATE = ROOT / 'studio-data'
STATIC = CODE / 'studio'
BOOKS = [
    ('shouhuo', '受活', '幕前的承诺，幕后的人'),
    ('cat-country', '猫城记', '在被定义的世界里保住清醒'),
    ('mirror-flowers', '镜花缘', '谁能替她们规定花期'),
    ('romance-of-three-kingdoms', '三国演义', '谋略最后落在谁身上'),
    ('the-three-body-problem', '三体', '证据怎样改变人对世界的判断'),
    ('five-spice-street', '五香街', '谁替她写下证词'),
    ('subway-hansong', '地铁', '还有多远才算到了'),
    ('tile-cat-geliang', '瓦猫', '手松开后，泥上留下什么'),
]
ID = re.compile(r'[a-f0-9]{24}')
STUDY_LABELS_ROUTE = '/api/review-films/study-labels'
LABEL_ORIGINS = {'http://127.0.0.1:18824', 'http://localhost:18824'}
CONTINUITY_RISKS = ('', 'easy', 'unlikely')
MAX_SCENE_LABELS = 8
MAX_SCENE_LABEL_LENGTH = 24
MAX_STUDY_ISSUE_LENGTH = 1000
METHOD_CLASSIFICATION_KINDS = ('art-style', 'expression-direction', 'technique', 'unconfirmed')


def document(path):
    return json.loads(path.read_text(encoding='utf-8'))


def archived_edition(slug):
    if not SLUG.fullmatch(slug):
        return None
    record = STATE / 'screenings' / slug / 'published.json'
    return document(record) if record.is_file() else None


@lru_cache(maxsize=64)
def verified_master(path, device, inode, size, modified, changed):
    # Rehash only when a different file/version occupies the playback address.
    return media_digest(path)


def master_sha(path):
    obj = path.resolve(strict=True)
    info = obj.stat()
    return verified_master(obj, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def published_edition(slug, selection=None):
    if not SLUG.fullmatch(slug):
        return None
    if selection is None:
        selection = current_editions(STATE/'screenings')
    chosen = next((entry for entry in selection if entry['slug']==slug), None)
    if chosen is None:
        return None
    record = archived_edition(slug)
    media = STATE/'screenings'/slug/'film.mp4'
    if (not record or record.get('slug')!=slug or record.get('sha256')!=chosen['sha256']
            or not media.is_file() or master_sha(media)!=chosen['sha256']):
        return None
    return record


def screening_list(archive=False):
    directory = STATE/'screenings'
    if archive:
        files = sorted(directory.glob('*/published.json'),
                       key=lambda p: (p.stat().st_mtime_ns,p.parent.name), reverse=True)
        rows = [document(path) for path in files]
    else:
        selection = current_editions(directory)
        rows = [rec for item in reversed(selection) if (rec:=published_edition(item['slug'], selection))]
    return [{key: r[key] for key in ('slug','book','title','edition','summary','duration')} for r in rows]


def film_page(record, collection):
    path = STATE / collection / record['slug'] / 'script.json'
    if not path.is_file():
        return record
    try:
        script = validate_script(document(path), record['sha256'], record['duration'])
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {**record, 'script_state': '台本暂时无法与当前版本对应。'}
    return {**record, 'script': script}


def reviewing_list():
    root = STATE / 'review-films'
    files = sorted(root.glob('review-*/published.json'))
    return [{key: row[key] for key in ('slug', 'book', 'title', 'edition', 'summary', 'duration')}
            for file in files if (row := reviewing_film(file.parent.name))]


def reviewing_film(slug):
    if not SLUG.fullmatch(slug) or not slug.startswith('review-'):
        return None
    folder = STATE / 'review-films' / slug
    record = folder / 'published.json'
    movie = folder / 'film.mp4'
    if not record.is_file() or not movie.is_file():
        return None
    row = document(record)
    if (row.get('slug') == slug and re.fullmatch(r'[a-f0-9]{64}', row.get('sha256', ''))
            and type(row.get('media_bytes')) is int and row['media_bytes'] == movie.stat().st_size):
        return row
    return None


def gallery_studies():
    reviewed = document(STATIC / 'study-selections.json')
    live = [dict(row, **reviewed['overrides'].get(row['id'], {}))
            for row in screening_studies() if row['id'] not in reviewed['archived']]
    folder = STATE / 'library-studies'
    index = folder / 'index.json'
    if not index.is_file():
        return live
    extra = [row for row in document(index)
             if SLUG.fullmatch(row['id']) and (folder / (row['id'] + '.mp4')).is_file()]
    return [*live, *extra]


def classified_studies(rows):
    path = STATE / 'library-studies' / 'style-annotations.json'
    classifications = document(path) if path.is_file() else {}
    if not isinstance(classifications, dict):
        raise ValueError('Expected style annotations keyed by published study ID.')
    pending = {'directions': [], 'methods': [], 'classification_status': 'pending'}
    result = []
    for row in rows:
        if 'classification_status' in row or 'directions' in row:
            classification = classification_fields(row)
        elif row['id'] in classifications:
            classification = classification_fields(classifications[row['id']])
        else:
            classification = pending
        result.append({**row, **classification})
    return result


def classified_method_catalog():
    references = CODE / 'skills/film-art-direction/references'
    catalog_path = references / 'catalog.json'
    catalog_rows = document(catalog_path)
    mapping = document(references / 'style-method-map.json')
    if mapping.get('catalog_sha256') != sha256(catalog_path.read_bytes()).hexdigest():
        raise ValueError('Method classification does not match the current catalog.')
    if not isinstance(mapping, dict) or mapping.get('schema_version') != 1:
        raise ValueError('Unexpected method classification schema.')
    entries = mapping.get('catalog_entries')
    if not isinstance(entries, list):
        raise ValueError('Expected method classifications keyed by catalog ID.')
    classified = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
            raise ValueError('Invalid catalog ID in method classification.')
        identifier = entry['id']
        if identifier in classified:
            raise ValueError(f'Duplicate classification for catalog ID {identifier}.')
        classified[identifier] = entry
    if set(classified) != {row['id'] for row in catalog_rows}:
        raise ValueError('Method classification IDs differ from the catalog.')
    result = []
    for row in catalog_rows:
        classification = classified[row['id']]
        if classification.get('name') != row['name']:
            raise ValueError(f"Method classification name differs for catalog ID {row['id']}.")
        kind = classification.get('kind')
        directions = classification.get('directions')
        verified_methods = classification.get('verified_method_ids')
        if (kind not in METHOD_CLASSIFICATION_KINDS
                or any(not isinstance(values, list) or any(not isinstance(value, str) for value in values)
                       for values in (directions, verified_methods))):
            raise ValueError(f"Invalid classification for catalog ID {row['id']}.")
        fields = {key: row[key] for key in ('id', 'name', 'group', 'example', 'method')}
        result.append({**fields, 'classification_kind': kind, 'directions': directions,
                       'verified_method_ids': verified_methods})
    return result


def production_rows():
    with closing(sqlite3.connect(f'file:{ROOT}/queue.sqlite3?mode=ro', uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM jobs WHERE kind IN ('render','assemble','openfilm','cut-evidence','dialogue-evidence') ORDER BY rowid")
        return [dict(row) for row in rows]


COMPOSITED_SHOTS = {'309ebd23fb1c5b6e9a3878dc': 'metalepsis-performance.mp4'}


def shot_playback(identifier):
    composite = COMPOSITED_SHOTS.get(identifier)
    if composite and (STATE / 'studies' / composite).is_file():
        return '/studies/' + composite
    return '/media/' + identifier


def screening_studies():
    names = {slug: name for slug, name, _ in BOOKS}
    metadata = labels()
    studies = []
    index = STATE / "study-mixes" / "index.json"
    mixes = document(index) if index.is_file() else {}
    for row in production_rows():
        tags = metadata.get(row['id'], {})
        if row['kind'] != 'render' or row['state'] != 'completed' or not tags.get('art'):
            continue
        artifact = json.loads(row['result']).get('artifact', '')
        if not artifact.endswith('.mp4'):
            continue
        try:
            safe_input(artifact)
        except (OSError, ValueError):
            continue
        playback = shot_playback(row['id'])
        composite = playback.startswith('/studies/')
        source = STATE / playback[1:] if composite else safe_input(artifact)
        mix = mixes.get(row['id'])
        if mix and master_sha(source) == mix['source_sha256']:
            playback = f"/study-mixes/{row['id']}.mp4?v={mix['sha256']}"
        studies.append({'id': row['id'], 'book': names.get(row['project'], row['project']),
                        'title': tags['name'], 'playback': playback,
                        'composited': composite,
                        'methods': [item['name'] for item in tags['art']]})
    return sorted(studies, key=lambda row: (not row['composited'], row['book'], row['title']))


def notes_db():
    db = sqlite3.connect(STATE / 'notes.sqlite3', timeout=8)
    db.execute('CREATE TABLE IF NOT EXISTS notes (shot TEXT PRIMARY KEY, text TEXT NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS study_labels (
        study TEXT PRIMARY KEY,
        scenes TEXT NOT NULL DEFAULT '[]',
        continuity_risk TEXT NOT NULL DEFAULT '' CHECK(continuity_risk IN ('', 'easy', 'unlikely'))
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS study_issues (
        study TEXT PRIMARY KEY,
        text TEXT NOT NULL DEFAULT ''
    )''')
    return db


def study_annotation(scenes, risk, issue):
    return {'scenes': json.loads(scenes), 'continuity_risk': risk, 'issue': issue}


def annotated_studies():
    studies = classified_studies(gallery_studies())
    with closing(notes_db()) as db:
        annotations = {study: study_annotation(scenes, risk, issue) for study, scenes, risk, issue in
                       db.execute('''SELECT labels.study, labels.scenes, labels.continuity_risk,
                                     COALESCE(issues.text, '')
                                     FROM study_labels AS labels
                                     LEFT JOIN study_issues AS issues ON issues.study=labels.study''')}
    return [{**row, 'annotation': annotations[row['id']]} if row['id'] in annotations else row
            for row in studies]


def change_study_annotation(data):
    operations = {'add_scene', 'remove_scene', 'continuity_risk', 'issue'}
    if not isinstance(data, dict) or len(data) != 2 or 'id' not in data:
        raise ValueError('请选择一条试片再标注。')
    operation, = (key for key in data if key != 'id')
    study, value = data['id'], data[operation]
    if operation not in operations or not isinstance(study, str) or not isinstance(value, str):
        raise ValueError('请选择有效的标注内容。')
    if not any(row['id'] == study for row in gallery_studies()):
        raise LookupError('这条试片已不在审片页，刷新后再试。')

    if operation == 'continuity_risk':
        if value not in CONTINUITY_RISKS:
            raise ValueError('请选择有效的穿帮判断。')
    elif operation == 'issue':
        value = value.replace('\r\n', '\n').replace('\r', '\n').strip()
        if len(value) > MAX_STUDY_ISSUE_LENGTH:
            raise ValueError(f'问题请写在 {MAX_STUDY_ISSUE_LENGTH} 字以内。')
    else:
        value = ' '.join(value.split())
        if not 1 <= len(value) <= MAX_SCENE_LABEL_LENGTH:
            raise ValueError(f'场景标签请写在 {MAX_SCENE_LABEL_LENGTH} 个字以内。')

    with closing(notes_db()) as db, db:
        db.execute('INSERT OR IGNORE INTO study_labels(study) VALUES (?)', (study,))
        current = study_annotation(*db.execute(
            '''SELECT labels.scenes, labels.continuity_risk, COALESCE(issues.text, '')
               FROM study_labels AS labels LEFT JOIN study_issues AS issues ON issues.study=labels.study
               WHERE labels.study=?''', (study,)).fetchone())
        scenes = current['scenes']
        if operation == 'add_scene' and value not in scenes:
            if len(scenes) >= MAX_SCENE_LABELS:
                raise ValueError(f'每条试片最多保存 {MAX_SCENE_LABELS} 个场景标签。')
            scenes.append(value)
        elif operation == 'remove_scene':
            current['scenes'] = [label for label in scenes if label != value]
        elif operation == 'continuity_risk':
            current['continuity_risk'] = value
        elif operation == 'issue':
            db.execute('''INSERT INTO study_issues(study,text) VALUES (?,?)
                          ON CONFLICT(study) DO UPDATE SET text=excluded.text''', (study,value))
            current['issue'] = value
        db.execute('UPDATE study_labels SET scenes=?, continuity_risk=? WHERE study=?',
                   (json.dumps(current['scenes'], ensure_ascii=False), current['continuity_risk'], study))
    return current


def synced(row):
    result = row.get('result')
    marker = STATE / 'received' / (row['id'] + '.json')
    return bool(result and marker.is_file() and document(marker).get('sha256') == json.loads(result).get('sha256'))


def labels():
    path = STATE / 'jobs.json'
    return document(path) if path.is_file() else {}


def safe_input(path):
    source = (PERSONAL / path).resolve(strict=True)
    if not source.is_relative_to(PERSONAL) or not source.is_file():
        raise ValueError('素材不可用。')
    return source


def shot_title(row, metadata, inputs):
    if row['kind'] != 'render':
        return {'assemble':'剪辑审看', 'openfilm':'可编辑工程', 'cut-evidence':'切点核对',
                'dialogue-evidence':'原声对白核对'}[row['kind']]
    if row['id'] in metadata:
        return metadata[row['id']]['name']
    if contract := inputs.get('shot_contract'):
        return safe_input(contract).stem if not re.fullmatch(r'[a-f0-9]{64}', Path(contract).stem) else '叙事镜头'
    return Path(inputs['prompt']).stem.replace('-', ' ')


def site_state():
    labels_by_id = labels()
    rows = production_rows()
    journal = document(STATE / 'journal.json')
    editions = screening_list()
    reviews = reviewing_list()
    work = []
    for slug, name, intent in BOOKS:
        jobs = []
        for row in rows:
            if row['project'] != slug or row['state'] == 'withdrawn':
                continue
            inputs = json.loads(row['inputs'])
            tags = labels_by_id.get(row['id'], {})
            jobs.append({'id': row['id'], 'title': shot_title(row, labels_by_id, inputs),
                         'kind': row['kind'], 'state': row['state'], 'received': synced(row),
                         'section': tags.get('section', ''), 'duration': inputs.get('duration'),
                         'updated_at': row['updated_at']})
        records = journal.get(slug, [])
        work.append({'slug': slug, 'title': name, 'intent': intent, 'jobs': jobs, 'records': records,
                     'screenings': [{'slug': e['slug'], 'title': e['title'], 'edition': e['edition']} for e in editions if e['book'] == name],
                     'scripts': [{'title': e['title'], 'edition': e['edition'], 'url': f'/{collection}/{e["slug"]}#film-script'}
                                 for collection, entries in (('review-films', reviews), ('screenings', editions))
                                 for e in entries if e['book'] == name]})
    return {'books': work, 'catalog': catalog(), 'time': datetime.now().isoformat(timespec='seconds')}


def detail(row):
    inputs = json.loads(row['inputs'])
    meta = labels().get(row['id'], {})
    result = json.loads(row['result']) if row['result'] else {}
    contract = None
    prompt = None
    prompt_state = None
    if row['kind'] == 'render':
        if path := inputs.get('shot_contract'):
            contract = document(safe_input(path))
            prompt = compile_reference_prompt(contract, inputs['duration'], len(inputs['references']),
                                              len(inputs.get('voices', [])), bool(inputs.get('motion')))
        else:
            prompt = safe_input(inputs['prompt']).read_text(encoding='utf-8')
        prompt_state = '拍摄方案'
        receipt = ROOT / 'artifacts' / row['project'] / row['id'] / 'shot.job.json'
        if receipt.is_file():
            reference = document(receipt)
            expected = reference.get('inputs', {}).get('prompt')
            candidates = [reference.get('submitted_prompt'), prompt, prompt.replace(' ' + H3_SPEECH, '', 1)]
            for candidate in candidates:
                if candidate and sha256(candidate.encode('utf-8')).hexdigest() == expected:
                    prompt, prompt_state = candidate, '实际提交'
                    break
    details = {}
    if row['kind'] in {'cut-evidence', 'dialogue-evidence'} and result.get('artifact'):
        detail_data = document(safe_input(result['artifact']))
        if row['kind'] == 'dialogue-evidence':
            details['lines'] = [{key: item[key] for key in ('expected', 'transcript', 'exact')}
                                | {'word_match': mandarin_word_match(item['expected'], item['transcript'])}
                                for item in detail_data['lines']]
        else:
            details['cuts'] = [{key: value for key, value in item.items()
                                if key in {'at', 'before', 'after', 'eligible_methods'}} for item in detail_data['cuts']]
    with closing(notes_db()) as db:
        note = db.execute('SELECT text FROM notes WHERE shot=?', (row['id'],)).fetchone()
    return {'id':row['id'], 'title':shot_title(row,labels(),inputs), 'kind':row['kind'], 'state':row['state'],
            'received':synced(row), 'creative':meta, 'contract':contract, 'prompt':prompt, 'prompt_state':prompt_state, 'evidence':details,
            'has_media': bool(result.get('artifact','').endswith('.mp4')), 'playback': shot_playback(row['id']),
            'note': note[0] if note else '',
            'error': '这一镜遇到问题，请核对素材和拍摄规格。' if row['error'] else ''}


class Studio(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def json(self, value, status=200):
        payload = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def file(self, file):
        if not file.is_file():
            self.send_error(404)
            return
        size = file.stat().st_size
        start, end, status = 0, size-1, 200
        if requested := self.headers.get('Range'):
            requested = re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
            if not requested or not any(requested.groups()):
                self.send_error(416)
                return
            lo, hi = requested.groups()
            start = int(lo) if lo else max(0,size-int(hi))
            end = min(size-1,int(hi)) if lo and hi else size-1
            if not 0 <= start <= end < size:
                self.send_error(416)
                return
            status = 206
        self.send_response(status)
        self.send_header('Content-Type', mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(end-start+1))
        self.send_header('Accept-Ranges','bytes')
        self.send_header('X-Content-Type-Options','nosniff')
        if status == 206:
            self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        if self.command == 'HEAD':
            return
        with file.open('rb') as stream:
            stream.seek(start)
            remaining=end-start+1
            while remaining:
                chunk=stream.read(min(remaining,256*1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining-=len(chunk)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        route = urlsplit(self.path).path
        try:
            if route in {'/','/studio.css','/studio.js'}:
                self.file(STATIC/('index.html' if route=='/' else route[1:]))
            elif route in {'/screenings.js','/screenings.css'}:
                self.file(STATIC / route[1:])
            elif route in {'/screenings','/archive/screenings','/review-films'}:
                self.file(STATIC / 'screenings.html')
            elif route == '/api/review-films':
                self.json(reviewing_list())
            elif route == '/api/review-films/studies':
                self.json(annotated_studies())
            elif route == '/api/review-films/methods':
                self.json(classified_method_catalog())
            elif route.startswith('/api/review-films/'):
                slug = route.removeprefix('/api/review-films/')
                row = reviewing_film(slug)
                if row is None:
                    self.send_error(404)
                else:
                    self.json(film_page(row, 'review-films'))
            elif route.startswith('/review-films/'):
                parts = route.removeprefix('/review-films/').split('/')
                slug = parts[0]
                if reviewing_film(slug) is None:
                    self.send_error(404)
                elif len(parts) == 1:
                    self.file(STATIC / 'screenings.html')
                elif len(parts) == 2 and parts[1] in {'film.mp4','poster.jpg','director.vtt'}:
                    self.file(STATE / 'review-films' / slug / parts[1])
                else:
                    self.send_error(404)
            elif route.startswith('/library-studies/'):
                study = route.removeprefix('/library-studies/')
                if any(row['playback'] == route for row in gallery_studies()):
                    self.file(STATE / 'library-studies' / study)
                else:
                    self.send_error(404)
            elif route in {'/api/screenings','/api/archive/screenings'}:
                self.json(screening_list(archive=route.startswith('/api/archive/')))
            elif route in {'/api/screenings/studies','/api/archive/screenings/studies'}:
                studies = screening_studies()
                if route == '/api/screenings/studies':
                    review = document(STATIC/'study-selections.json')
                    studies = [dict(row, **review['overrides'].get(row['id'], {}))
                               for row in studies if row['id'] not in review['archived']]
                self.json(classified_studies(studies))
            elif route.startswith(('/api/screenings/','/api/archive/screenings/')):
                archive = route.startswith('/api/archive/')
                slug = route.removeprefix('/api/archive/screenings/' if archive else '/api/screenings/')
                row = archived_edition(slug) if archive else published_edition(slug)
                if row is None:
                    self.send_error(404)
                else:
                    self.json(film_page(row, 'screenings'))
            elif route.startswith(('/screenings/','/archive/screenings/')):
                archive = route.startswith('/archive/')
                parts = route.removeprefix('/archive/screenings/' if archive else '/screenings/').split('/')
                slug=parts[0]
                row = archived_edition(slug) if archive else published_edition(slug)
                if row is None:
                    self.send_error(404)
                elif len(parts) == 1:
                    self.file(STATIC / 'screenings.html')
                elif len(parts) == 2 and parts[1] in {'film.mp4','poster.jpg','director.vtt'}:
                    self.file(STATE / 'screenings' / slug / parts[1])
                else:
                    self.send_error(404)
            elif route=='/api/state':
                self.json(site_state())
            elif route in {'/art/stage.jpg', '/art/village.jpg'}:
                self.file(STATE / route.split('/')[-1])
            elif re.fullmatch(r'/study-mixes/[a-f0-9]{24}\.mp4', route):
                self.file(STATE / route[1:])
            elif route in {'/studies/five-spice.jpg', '/studies/subway.jpg', '/studies/tile-cat.jpg',
                           '/studies/metalepsis.mp4', '/studies/metalepsis-performance.mp4'}:
                self.file(STATE / 'studies' / route.rsplit('/', 1)[-1])
            elif route.startswith(('/api/shots/', '/media/', '/review/')):
                key=route.rsplit('/',1)[-1]
                row=next((row for row in production_rows() if row['id']==key),None) if ID.fullmatch(key) else None
                if row is None or row['state'] == 'withdrawn':
                    self.send_error(404)
                elif route.startswith('/api/'):
                    self.json(detail(row))
                else:
                    if route.startswith('/review/') and row['kind'] == 'cut-evidence':
                        source = json.loads(row['inputs'])['source']
                    else:
                        result = json.loads(row['result']) if row['result'] else {}
                        source = result.get('artifact', '')
                    if not source.endswith('.mp4'):
                        self.send_error(404)
                        return
                    self.file(safe_input(source))
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, ValueError, sqlite3.Error):
            self.json({'error':'暂时无法读取制作记录。'},503)

    def save_study_annotation(self):
        length = self.headers.get('Content-Length', '')
        if (self.headers.get('Origin') not in LABEL_ORIGINS
                or self.headers.get_content_type() != 'application/json'
                or not length.isascii() or not length.isdecimal() or len(length) > 4
                or not 1 <= int(length) <= 4096):
            self.json({'error': '无法保存这次标注。'}, 403)
            return
        try:
            data = json.loads(self.rfile.read(int(length)))
            self.json(change_study_annotation(data))
        except json.JSONDecodeError:
            self.json({'error': '无法读取这次标注。'}, 400)
        except UnicodeDecodeError:
            self.json({'error': '无法读取这次标注。'}, 400)
        except ValueError as exc:
            self.json({'error': str(exc)}, 400)
        except LookupError as exc:
            self.json({'error': str(exc)}, 404)
        except (OSError, sqlite3.Error):
            self.json({'error': '暂时无法保存，稍后再试。'}, 503)

    def do_POST(self):
        route = urlsplit(self.path).path
        if route == STUDY_LABELS_ROUTE:
            self.save_study_annotation()
            return
        if route != '/api/notes':
            self.send_error(404)
            return
        origin = self.headers.get('Origin')
        allowed = {'http://127.0.0.1:18824', 'http://localhost:18824'}
        length = self.headers.get('Content-Length','')
        if (origin not in allowed or self.headers.get_content_type() != 'application/json'
                or not length.isdecimal() or not 1 <= int(length) <= 24000):
            self.send_error(403)
            return
        try:
            data = json.loads(self.rfile.read(int(length)))
            shot, content = data['id'], data['text'].strip()
            if len(content)>3000 or not any(row['id']==shot for row in production_rows()):
                self.send_error(400)
                return
            with closing(notes_db()) as db:
                with db:
                    db.execute('INSERT INTO notes(shot,text) VALUES (?,?) ON CONFLICT(shot) DO UPDATE SET text=excluded.text',
                               (shot,content))
            self.json({'saved':True})
        except (ValueError, AttributeError, KeyError, TypeError):
            self.send_error(400)


if __name__=='__main__':
    if ROOT.resolve(strict=True) != ROOT:
        raise PermissionError('须在 DSW 个人工作区启动。')
    STATE.mkdir(mode=0o700,exist_ok=True)
    ThreadingHTTPServer(('127.0.0.1',18824),Studio).serve_forever()
