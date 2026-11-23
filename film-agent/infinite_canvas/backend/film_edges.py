"""Verify authored cut points against the actual picture and sound of an export."""
import base64
import json
from itertools import islice
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

from film_cut_methods import validate_plan
from media_segments import concat_segments, fingerprint

BATCH_SIZE = 4
HANDLES = 0.75


class CutRejected(ValueError):
    pass


def authored_edges(shots):
    for index, (left, right) in enumerate(zip(shots, shots[1:]), 1):
        plan = (right.get('direction') or {}).get('editorial_join')
        if plan:
            yield index, left, right, validate_plan(plan)


def validate_distinct_cut_plans(shots):
    seen = {}
    for _, left, right, plan in authored_edges(shots):
        signature = tuple(re.sub(r'[^\w]', '', plan[field]).casefold()
                          for field in ('before', 'after', 'reason'))
        if signature in seen:
            raise ValueError(
                f'切点「{left["id"]} → {right["id"]}」复制了「{seen[signature]}」的进出动作与动机。')
        seen[signature] = f'{left["id"]} → {right["id"]}'


def extract_cut(media, at, left_window, right_window, destination):
    before = min(HANDLES, left_window)
    after = min(HANDLES, right_window)
    if min(before, after) < 1 / 24:
        raise ValueError('实剪切点缺少可核对的前后画面。')
    temporary = destination.with_name(destination.stem + '.part.mp4')
    try:
        subprocess.run([
            'ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-y', '-ss', f'{at-before:.6f}',
            '-i', str(media), '-t', f'{before+after:.6f}', '-map', '0:v:0', '-map', '0:a:0',
            '-vf', 'scale=480:480:force_original_aspect_ratio=decrease:force_divisible_by=2',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-threads', '2',
            '-c:a', 'aac', '-b:a', '64k', str(temporary),
        ], check=True, capture_output=True, timeout=35)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return before


def write_receipt(path, data):
    temporary = path.with_suffix('.part')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def check_exported_cuts(media, shots, choices, project, judge, on_progress=None):
    edges = list(authored_edges(shots))
    if not edges:
        return []
    validate_distinct_cut_plans(shots)
    if len(shots) != len(choices):
        raise ValueError('切点核查的实际选片数量与分镜不符。')
    durations = [end - start for _, (start, end) in choices]
    timeline = [0.]
    for length in durations:
        timeline.append(timeline[-1] + length)
    directory = project / 'cut-evidence'
    directory.mkdir(exist_ok=True)
    receipt = project / '逐切实剪回执.json'
    media_hash = fingerprint(media)
    verified = []
    pending = iter(edges)
    while group := list(islice(pending, BATCH_SIZE)):
        if on_progress:
            on_progress(f'正在核对实际切点 {len(verified) + 1}–{len(verified) + len(group)}/{len(edges)}')
        instructions = [{
            'type': 'text', 'text':
            '审查一条视频中拼接的邻镜短样本。review_cut_seconds 是每个样本在这条审核视频中的真实切点。'
            '每个样本限定在 review_start_seconds 和 review_end_seconds，片段与片段之间的接缝不参与审查。'
            '观察切前、切后动作和声源，逐个判断计划的出点、入点和叙事动机能否从片段看见或听见。'
            '连续动作判断接触、道具归属、方向、重心；时空转换判断是否有可感知线索；声音过门核对实际跨点声源。'
            '同物换材质须实际渐变且地标可辨；普通直切可成立。只用可见可听的证据，不凭文字规格补完。'
            '证据不足就写须补哪一侧的可剪柄。输出 JSON '
            '{"cuts":[{"index":1,"accepted":true,"evidence":"具体时间与对象",'
            '"fix":"通过留空，未通过写明处理：改切点、补桥或重拍以及理由"}]}。'
        }]
        records = []
        clips = []
        review_lengths = []
        review_at = 0.
        for index, left, right, plan in group:
            filename = f'cut-{index:04d}.mp4'
            clip = directory / filename
            at = round(timeline[index] * 24) / 24
            cut_in_clip = extract_cut(media, at, durations[index - 1], durations[index], clip)
            review_length = cut_in_clip + min(HANDLES, durations[index])
            record = {'index': index, 'left_shot': left['id'], 'right_shot': right['id'],
                      'film_seconds': at, 'cut_in_clip': cut_in_clip,
                      'review_start_seconds': round(review_at, 4),
                      'review_cut_seconds': round(review_at + cut_in_clip, 4),
                      'review_end_seconds': round(review_at + review_length, 4),
                      'clip': f'cut-evidence/{filename}', 'plan': plan}
            records.append(record)
            clips.append(clip)
            review_lengths.append(review_length)
            review_at += review_length
            instructions.append({'type': 'text', 'text': json.dumps(record, ensure_ascii=False)})
        write_receipt(receipt, {'media_sha256': media_hash, 'planned': len(edges),
                                'cuts': verified, 'pending_review': records})
        with TemporaryDirectory(prefix='review-cuts-', dir=project) as temporary:
            review_media = clips[0]
            if len(clips) > 1:
                review_media = Path(temporary) / 'batch.mp4'
                concat_segments(clips, review_media, durations=review_lengths)
            video = base64.b64encode(review_media.read_bytes()).decode('ascii')
        instructions.append({'type': 'video_url', 'video_url': {'url': 'data:video/mp4;base64,' + video, 'fps': 12.0}})
        response = judge(instructions)
        verdicts = response.get('cuts') if isinstance(response, dict) else None
        if not isinstance(verdicts, list) or len(verdicts) != len(records):
            raise ValueError('切点审片没有返回完整结果。')
        observed = {}
        for verdict in verdicts:
            if (not isinstance(verdict, dict) or type(verdict.get('index')) is not int
                    or verdict['index'] in observed or type(verdict.get('accepted')) is not bool
                    or not isinstance(verdict.get('evidence'), str) or not verdict['evidence'].strip()
                    or not isinstance(verdict.get('fix'), str)
                    or (not verdict['accepted'] and not verdict['fix'].strip())):
                raise ValueError('切点审片缺少明确证据或修正说明。')
            observed[verdict['index']] = verdict
        if set(observed) != {record['index'] for record in records}:
            raise ValueError('切点审片未对应实际邻镜编号。')
        for record in records:
            verdict = observed[record['index']]
            record.update(accepted=verdict['accepted'], evidence=verdict['evidence'][:400], fix=verdict['fix'][:400])
        verified.extend(records)
        write_receipt(receipt, {'media_sha256': media_hash, 'planned': len(edges), 'cuts': verified})
    rejected = [row for row in verified if not row['accepted']]
    if rejected:
        labels = '、'.join(str(row['index']) for row in rejected)
        raise CutRejected(f'切点 {labels} 的实际画面或声音未通过，工程内已保存证据和修正说明。')
    return verified
