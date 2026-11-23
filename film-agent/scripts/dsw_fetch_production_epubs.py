"""Download and verify project EPUBs using the workspace's company catalog client."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from html import unescape
import json
from pathlib import Path, PurePosixPath
import re
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile

PROJECTS = [
    ('mirror-flowers', '镜花缘', '李汝珍', 245000, ('唐敖', '唐小山', '第一百回'), '碑影'),
    ('cat-country', '猫城记', '老舍', 55000, ('迷叶', '大蝎', '小蝎'), '坠机后的围猎'),
    ('romance-of-three-kingdoms', '三国演义', '罗贯中', 390000, ('第一百二十回', '诸葛亮', '司马炎'), '火烧赤壁'),
    ('the-three-body-problem', '三体', '刘慈欣', 135000, ('古筝行动', '红岸', '叶文洁', '汪淼'), '三日凌空'),
]


def plain(text):
    return re.sub(r'\s+', '', re.sub(r'<[^>]+>', '', unescape(text)))


def verify(path, title, author, minimum, markers):
    with ZipFile(path) as epub:
        if epub.testzip() is not None or epub.read('mimetype') != b'application/epub+zip':
            raise ValueError('EPUB 格式或压缩校验有误')
        container = ET.fromstring(epub.read('META-INF/container.xml'))
        package_path = next(node.attrib['full-path'] for node in container.iter() if node.tag.endswith('}rootfile'))
        package = ET.fromstring(epub.read(package_path))
        metadata = next(node for node in package if node.tag.endswith('}metadata'))
        titles = [node.text or '' for node in metadata if node.tag.endswith('}title')]
        authors = [node.text or '' for node in metadata if node.tag.endswith('}creator')]
        language = [node.text for node in metadata if node.tag.endswith('}language')]
        if titles and not any(title in value for value in titles):
            raise ValueError('书内题名不匹配')
        if authors and not any(author in value.replace(' ', '') for value in authors):
            raise ValueError('书内作者不匹配')
        bindings = {node.attrib['id']: node.attrib['href'] for node in package.iter()
                    if node.tag.endswith('}item') and node.get('media-type') == 'application/xhtml+xml'}
        pages = [bindings[node.attrib['idref']] for node in package.iter()
                 if node.tag.endswith('}itemref') and node.get('idref') in bindings]
        from posixpath import normpath
        from urllib.parse import unquote
        members = [normpath(str(PurePosixPath(package_path).parent / unquote(item.split('#')[0]))) for item in pages]
        text = ''.join(plain(epub.read(item).decode('utf-8', 'replace')) for item in dict.fromkeys(members))
        hz = len(re.findall(r'[\u3400-\u9fff]', text))
        if hz < minimum or not all(mark in text for mark in markers):
            raise ValueError(f'正文未达到全书门槛（{hz:,} 汉字）')
        if title == '三体' and hz > 385000:
            raise ValueError('版本含其他分卷')
        return {'title': titles, 'author': authors, 'language': language, 'spine_items': len(pages),
                'chinese_characters': hz, 'sentinel_passed': list(markers),
                'sha256': sha256(path.read_bytes()).hexdigest()}


def run_one(workspace, project):
    import check_downloadable_books as search
    import download_goldmines as download
    slug, title, author, count, markers, icon = project
    root = workspace / 'morpho-projects' / slug
    root.mkdir(parents=True, exist_ok=True)
    if root.resolve(strict=True) != root:
        raise ValueError('项目目录指向了个人工作区以外')
    for directory in ('source', 'assets', 'storyboard', 'renders', 'exports', 'review'):
        (root / directory).mkdir(exist_ok=True)
    source = root / 'source'
    if source.resolve(strict=True) != source:
        raise ValueError('原著目录指向了个人工作区以外')
    path = source / 'original.epub'
    project_path = root / 'project.json'

    def save_json(path, data):
        temp = path.with_suffix('.part')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temp.replace(path)

    plan = json.loads(project_path.read_text()) if project_path.is_file() else {'title': title, 'stage': 'planning'}
    plan['deliverables'] = [
        {'id': 'feature', 'name': '整书版', 'minimum_seconds': 601, 'mode': 'full-book'},
        {'id': 'classic-scene', 'name': '经典场景版', 'scene': icon, 'mode': 'standalone-scene'},
    ]
    plan['workflow'] = 'Morpho P0/P1'
    save_json(project_path, plan)
    if path.is_file():
        check = verify(path, title, author, count, markers)
    else:
        with search.make_session(4) as session, download.make_session() as fetch:
            payload = {'queryList': [{'key': 'title', 'query': title}, {'key': 'author', 'query': author}],
                       'page': 1, 'size': 30}
            response = session.post(search.ADV_SEARCH_URL, json=payload, timeout=(5, 20))
            response.raise_for_status()
            data = response.json()
            if data.get('code') != 0:
                raise RuntimeError('公司书目查询未成功')
            rejected = []
            for item in data.get('data', {}).get('books', []):
                if (item.get('title') != title or author not in (item.get('author') or '')
                        or (item.get('extension') or '').lower() != 'epub' or not item.get('s3_id')):
                    continue
                pending = source / 'verifying.epub'
                try:
                    download.download_file(fetch, item['s3_id'], pending)
                    check = verify(pending, title, author, count, markers)
                    pending.replace(path)
                    break
                except (OSError, ValueError, KeyError, ET.ParseError) as exc:
                    rejected.append(type(exc).__name__ + ': ' + str(exc)[:85])
                    pending.unlink(missing_ok=True)
                if len(rejected) >= 8:
                    break
            else:
                raise ValueError(f'{title} 无可用 EPUB，已检验 {len(rejected)} 个版本 {rejected}')
            if not path.is_file():
                raise ValueError(f'{title} 版本校验未通过：{rejected}')
    save_json(source / 'edition.json', {'provider': '公司搜书接口', 'file': 'original.epub', **check})
    plan['source'] = 'source/original.epub'
    plan['source_sha256'] = check['sha256']
    if plan['stage'] == 'planning':
        plan['stage'] = 'source_ready'
    save_json(project_path, plan)
    return title, check['chinese_characters'], check['spine_items'], path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace', type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    if workspace != Path('/mnt/AIRSSD/user_workspace/liaolin2'):
        raise ValueError('仅允许在已确认的 DSW 个人工作区运行')
    sys.path[:0] = [str(workspace/'scripts'), str(workspace/'books')]
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(run_one, workspace, item) for item in PROJECTS]
        errors = []
        for item, job in zip(PROJECTS, jobs, strict=True):
            try:
                print(*job.result(), flush=True)
            except Exception as exc:
                errors.append(item[1])
                print(item[1], type(exc).__name__, str(exc)[:400], flush=True)
    if errors:
        raise SystemExit('待核对 ' + '、'.join(errors))

