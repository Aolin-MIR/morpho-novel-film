"""Shared frame restrictions for editing and for deriving production references."""
from fractions import Fraction
import json
from math import ceil, floor, isfinite


def source_rule(archive, digest):
    ledger = archive / 'review' / 'source-windows.json'
    try:
        entries = json.loads(ledger.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if not isinstance(entries, dict):
        raise ValueError('已审源帧清单必须按原片 SHA-256 登记。')
    if digest not in entries:
        return None
    rule = entries[digest]
    if not isinstance(rule, dict) or not isinstance(rule.get('reason'), str) or not rule['reason'].strip():
        raise ValueError('已审源帧清单缺少退件原因。')
    recognized = ('approved_frames', 'blocked_frames')
    if not any(key in rule for key in recognized):
        raise ValueError('已审源帧清单须明确准用或退件范围。')
    for key in recognized:
        if key not in rule:
            continue
        windows = rule[key]
        if not isinstance(windows, list) or any(
                not isinstance(window, list) or len(window) != 2
                or any(type(point) is not int for point in window)
                or not 0 <= window[0] < window[1] for window in windows):
            raise ValueError('已审源帧清单的区间格式无效。')
    return rule


def require_window(rule, start, end):
    if rule is None:
        return
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise ValueError('请提供有效的源帧范围。')
    approved = rule.get('approved_frames')
    blocked = rule.get('blocked_frames', ())
    if (any(a < end and start < b for a, b in blocked)
            or (approved is not None and not any(a <= start < end <= b for a, b in approved))):
        raise ValueError(f'所选源帧 [{start},{end}) 含已退素材，请改选已审片段。{rule["reason"]}')


def require_clean_source_window(archive, digest, start, end):
    require_window(source_rule(archive, digest), start, end)


def require_archived_window(path, digest, start, end):
    for archive in path.parents:
        require_clean_source_window(archive, digest, start, end)


def require_time_window(archive, digest, cut, read_streams):
    rule = source_rule(archive, digest)
    if rule is None:
        return
    stream = next((item for item in read_streams() if item.get('codec_type') == 'video'), None)
    try:
        count = int(stream['nb_frames'])
        fps = Fraction(stream['avg_frame_rate'])
        if count < 1 or fps <= 0 or fps != Fraction(stream['r_frame_rate']):
            raise ValueError
    except (TypeError, KeyError, ZeroDivisionError, ValueError) as exc:
        raise ValueError('此受限视频缺少可换算的固定帧率和帧数。') from exc
    if cut is None:
        require_window(rule, 0, count)
        return
    if (not isinstance(cut, (list, tuple)) or len(cut) != 2
            or any(type(value) not in (int, float) or not isfinite(value) for value in cut)
            or not 0 <= cut[0] < cut[1]):
        raise ValueError('受限视频的剪辑范围需要有效的起止时间。')
    bounds = [value * float(fps) for value in cut]
    # Do not turn the usual 24 fps round-trip representations into extra frames.
    bounds = [round(frame) if abs(frame-round(frame)) < 1e-8 else frame for frame in bounds]
    start, end = floor(bounds[0]), ceil(bounds[1])
    if end > count:
        raise ValueError('剪辑范围超过源视频。')
    require_window(rule, start, end)
