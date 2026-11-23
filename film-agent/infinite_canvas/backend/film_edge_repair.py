"""Evidence-driven, bounded preview repairs for the active working cut's next edge."""
from base64 import b64encode
import json
from pathlib import Path
import subprocess

from film_edges import write_receipt
from media_segments import fingerprint


def sample(source, seconds, *, crop=None):
    filters = ['scale=768:-2']
    if crop:
        x, y, width, height = crop
        filters.insert(0, f'crop={width}:{height}:{x}:{y}')
    return b64encode(subprocess.check_output([
        'ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-ss', str(seconds),
        '-i', str(source), '-map', '0:v:0', '-vf', ','.join(filters),
        '-frames:v', '1', '-threads', '1', '-q:v', '3', '-f', 'image2pipe', '-vcodec', 'mjpeg', '-',
    ], timeout=20)).decode('ascii')


def validate_action(action, source_frames):
    if not isinstance(action, dict) or action.get('tool') not in {'keep', 'trim', 'crop', 'hold'}:
        raise ValueError('返修必须使用已登记的非破坏性操作。')
    if not isinstance(action.get('reason'), str) or not 0 < len(action['reason'].strip()) <= 400:
        raise ValueError('返修须有可核对的原因。')
    if action['tool'] == 'hold':
        return None
    window = action.get('frames')
    if (not isinstance(window, list) or len(window) != 2 or not all(type(n) is int for n in window)
            or not 0 <= window[0] < window[1] <= source_frames
            or not 30 <= window[1] - window[0] <= 120):
        raise ValueError('相邻镜候选须提供有效的连续帧区间。')
    crop = action.get('crop')
    if action['tool'] == 'crop':
        if not isinstance(crop, list) or len(crop) != 4 or any(type(n) is not int or n % 2 for n in crop):
            raise ValueError('构图返修须提供偶数像素坐标。')
        x, y, width, height = crop
        if not (0 <= x <= 1536 - width and 0 <= y <= 864 - height
                and width >= 832 and height >= 468 and width * 9 == height * 16):
            raise ValueError('构图返修超出原画幅或裁掉过多细节。')
    elif crop is not None:
        raise ValueError('只有构图操作可以改变画幅。')
    if action['tool'] == 'keep' and window != [0, min(source_frames, 96)]:
        raise ValueError('保留原切点时须保持原镜头的入点和样本长度。')
    return window, crop


def preview(left, right, tail_frames, left_frames, window, crop, destination):
    start, end = window
    right_len = (end - start) / 24
    left_len = tail_frames / 24
    right_filter = 'fps=24,setsar=1'
    if crop:
        x, y, width, height = crop
        right_filter = f'crop={width}:{height}:{x}:{y},scale=1536:864:flags=lanczos,' + right_filter
    graph = (
        f'[0:v]trim=end_frame={tail_frames},setpts=PTS-STARTPTS,setsar=1[lv];'
        f'[0:a]atrim=duration={left_len},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo[la];'
        f'[1:v]{right_filter},trim=end_frame={end-start},setpts=PTS-STARTPTS[rv];'
        f'[2:a]atrim=duration={right_len},asetpts=PTS-STARTPTS[ra];'
        '[lv][la][rv][ra]concat=n=2:v=1:a=1[v][a]'
    )
    subprocess.run([
        'ffmpeg', '-nostdin', '-v', 'error', '-filter_complex_threads', '2',
        '-ss', str((left_frames - tail_frames) / 24), '-i', str(left),
        '-ss', str(start / 24), '-i', str(right),
        '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-filter_complex', graph,
        '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '19',
        '-pix_fmt', 'yuv420p', '-threads', '2', '-c:a', 'aac', '-b:a', '160k',
        '-movflags', '+faststart', '-y', str(destination),
    ], capture_output=True, check=True, timeout=100)


def alternative_view(source, frame_count, context, labels, decision):
    # Representative reframes are evidence for the model's choice, never preapproved cuts.
    width, height = 832, 468
    rectangles = [(0, 0), (352, 0), (704, 0), (0, 396), (704, 396)]
    window = [frame_count * 27 // 100, frame_count * 54 // 100]
    if window[1] - window[0] < 30:
        return {'tool':'hold','reason':'候选画面动作窗太短。'}
    choices = []
    views = list(context[:2])
    for x, y in rectangles:
        crop = [x, y, width, height]
        for point in (window[0], window[1] - 1):
            views.append(sample(source, point/24, crop=crop))
        choices.append(crop)
    data = {'options': choices, 'frames': window, 'constraints': labels['requirements']}
    response = decision(
        '直切未通过。前两图是已建立状态，后续每两图是一个从原片读取的替代构图首尾。'
        '判断哪些构图仍保留连续动作、人物衣着和此前必要信息。可以在构图里自然移开当前不需交代的区域，'
        '需要让观众看见的证据须始终保留。任一方案有疑问可选择 hold。'
        '输出 {"tool":"crop|hold","option":从1开始的构图编号或null,"reason":"可见依据"}。'
        + json.dumps(data, ensure_ascii=False), views)
    tool = response.get('tool')
    if tool == 'hold':
        return {'tool':tool,'reason':response.get('reason')}
    index = response.get('option')
    if tool != 'crop' or type(index) is not int or not 1 <= index <= len(choices):
        raise ValueError('候选构图选择无效。')
    return {'tool':'crop','frames':window,'crop':choices[index-1],'reason':response.get('reason')}


def assess(left, reference, right, out, decision, inspect_video, *, try_alternative=False):
    """Run one plan and one recheck. A preview is kept even when recheck is inconclusive."""
    prior_frames = left['frames']
    tail = min(prior_frames, 76)
    source_count = right['frames']
    ref_start, ref_end = reference['timeline_frames']
    first_in = prior_frames - tail
    context_points = [(ref_start + (ref_end - ref_start) // 3)/24, (ref_end - 1)/24,
                      (first_in + tail // 3)/24, (prior_frames - 2)/24]
    images = [sample(left['path'], instant) for instant in context_points]
    right_times = [round((source_count - 1) * part / 8) / 24 for part in (0, 1, 2, 3, 4, 6)]
    incoming = [sample(right['path'], instant) for instant in right_times]
    labels = {'earlier_reference_seconds': context_points[:2], 'left_edit_seconds': context_points[2:],
              'right_source_seconds': right_times, 'right_frame_count': source_count,
              'right_geometry': [1536, 864], 'fps': 24, 'requirements': right['requirements']}
    proposal = decision(
        '按时间检查一个真实接点。前两图是同一场更早的道具状态，第三四图是实际出点。其余图按时码来自候选入镜。'
        '先核对同一对象的外观、数量、位置和持有人，再判断新动作能否成立。每个判断用图号或区间给出证据。'
        '普通接法成立可 keep，若只需改入出点选 trim。若跨镜冒出的细节属于非必要信息，且裁切后仍能看到动作起因、'
        '主体和结果，可选择 crop。按照右镜原始 1536×864 像素 x,y,w,h 给偶数裁切，最小832×468，16:9。'
        '优先保住完整可见的动作和稳定落点。还需要别的证据或关键表演则 hold。'
        '输出 JSON {"observed":"发现与时码","action":{"tool":"keep|trim|crop|hold",'
        '"frames":[起帧,末帧],"crop":null或[x,y,w,h],"reason":"理由"}}。'
        + json.dumps(labels, ensure_ascii=False), images + incoming)
    if not isinstance(proposal.get('observed'), str) or not proposal['observed'].strip():
        raise ValueError('返修决定缺少可见证据。')
    choice = validate_action(proposal.get('action'), source_count)
    receipt = out / 'edge-repair.json'
    record = {'state': 'held', 'left_sha256': left['sha256'], 'right_sha256': right['sha256'],
              'reference': reference, 'proposal': proposal, 'comparison': labels}
    write_receipt(receipt, record)
    if choice is None and try_alternative:
        # Evaluate actual before/after frames for each alternative in a single request.
        alternative = alternative_view(right['path'], source_count, images, labels, decision)
        choice = validate_action(alternative, source_count)
        record['alternative'] = alternative
        write_receipt(receipt, record)
    if choice is None:
        return record
    window, crop = choice
    candidate = out / 'edge-preview.mp4'
    preview(left['path'], right['path'], tail, prior_frames, window, crop, candidate)
    metadata = inspect_video(candidate)
    if metadata['frames'] != tail + window[1] - window[0]:
        raise ValueError('实际预览帧数与选定切点不一致。')
    checks = [(tail + round((window[1] - window[0] - 1) * part / 4)) / 24 for part in range(5)]
    view = [sample(candidate, instant) for instant in checks]
    checked = decision(
        '独立复查实际剪接候选。前两图是同场此前的物件，第三四图是出镜，'
        '后五图来自已经实际导出的入镜。只根据画面判断，不假定有任何修法。'
        '核对画面是否保持或避免错误地显示关键道具变化；保留要求的完整入镜动作和落点。'
        '给出 JSON {"continuity":"pass|fail|uncertain","action":"pass|fail|uncertain","evidence":"具体图号与可见事实"}。'
        + json.dumps(labels['requirements'], ensure_ascii=False), images + view)
    if (any(checked.get(key) not in {'pass', 'fail', 'uncertain'} for key in ('continuity', 'action'))
            or not isinstance(checked.get('evidence'), str) or not checked['evidence'].strip()):
        raise ValueError('返修复查缺少结构化结论。')
    record.update(state='verified_candidate' if checked['continuity'] == checked['action'] == 'pass' else 'held',
                  candidate=str(candidate), candidate_sha256=fingerprint(candidate), cut_frame=tail,
                  incoming_audio='preview_muted_unverified', verification=checked,
                  frame_count=metadata['frames'])
    write_receipt(receipt, record)
    return record
