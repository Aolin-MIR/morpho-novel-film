"""Validate and expose edition-bound screenplay notes."""
from math import isfinite
from urllib.parse import urlsplit
from unicodedata import category
import re

KINDS = frozenset(('原著', 'SceneWeave', '第三方框架', 'Skill', 'Harness', '模型', '制作记录'))
USES = frozenset(('实际使用', '借鉴方法', '仅供参考', '待核实'))
LINES = frozenset(('对白', '旁白', '画面文字', '字幕记录'))


def words(value, limit):
    return (isinstance(value, str) and 0 < len(value.strip()) <= limit
            and all(char.isprintable() or char == '\n' or category(char) == 'Zs' for char in value))


def valid_time(value):
    return type(value) in (int, float) and isfinite(value)


def validate_script(script, digest, duration):
    if (not isinstance(script, dict) or not re.fullmatch('[a-f0-9]{64}', digest)
            or script.get('sha256') != digest):
        raise ValueError('台本须与当前影片版本对应。')
    if script.get('edition') not in ('实剪台本', '拍摄台本') or not words(script.get('note'), 400):
        raise ValueError('台本须注明版本和整理依据。')
    origins = script.get('sources')
    if not isinstance(origins, list) or not origins:
        raise ValueError('台本须注明来源。')
    source_ids = set()
    for row in origins:
        if (not isinstance(row, dict) or row.get('kind') not in KINDS or row.get('use') not in USES
                or not all(words(row.get(key), limit) for key, limit in (
                    ('id', 32), ('name', 90), ('role', 400)))):
            raise ValueError('台本来源字段不完整。')
        if row['id'] in source_ids:
            raise ValueError('台本来源重复。')
        if 'url' in row:
            target = urlsplit(row['url'])
            if target.scheme != 'https' or not target.hostname or target.username or target.password:
                raise ValueError('请提供公开的 HTTPS 来源地址。')
        source_ids.add(row['id'])
    scenes = script.get('scenes')
    if not isinstance(scenes, list) or not scenes:
        raise ValueError('请提供有时码的实际台本。')
    previous = 0
    for scene in scenes:
        start, end = scene.get('start'), scene.get('end')
        if not all(map(valid_time, (start, end))) or not previous - .001 <= start < end <= duration + .001:
            raise ValueError('台本时码超出当前版本或顺序有误。')
        previous = end
        if not words(scene.get('title'), 120) or not words(scene.get('action'), 900):
            raise ValueError('台本场次须有标题与画面内容。')
        links = scene.get('sources', [])
        if not isinstance(links, list) or any(link not in source_ids for link in links):
            raise ValueError('台本场次引用了未登记的来源。')
        last = start
        for line in scene.get('lines', []):
            begin, stop = line.get('start'), line.get('end')
            if (not all(map(valid_time, (begin, stop))) or not (last - .001 <= begin < min(end, stop) and stop <= duration + .001)
                    or line.get('kind') not in LINES or not words(line.get('text'), 650)
                    or not words(line.get('evidence'), 100)
                    or ('speaker' in line and not words(line['speaker'], 60))):
                raise ValueError('台词、旁白和画面文字须对应自己的实际时段和依据。')
            last = begin
    return script
