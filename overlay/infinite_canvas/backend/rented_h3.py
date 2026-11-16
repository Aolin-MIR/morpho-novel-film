"""ComfyUI H3 on a rented GPU, reached through a local SSH tunnel."""
import base64
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

import requests

MODEL = 'ComfyUI MiniMax H3'


def endpoint():
    port = os.environ.get('MORPHO_H3_SSH_PORT')
    if port is None:
        return None
    if not port.isdecimal() or not 1024 <= int(port) <= 65535:
        raise ValueError('H3 SSH 隧道端口无效。')
    return f'http://127.0.0.1:{port}'


def session():
    client = requests.Session()
    client.trust_env = False
    return client


def profile(reference_count):
    root = Path.home() / '.config' / 'morpho-local'
    try:
        spec = json.loads((root / f'h3-workflow-{reference_count}.json').read_text())
        graph = spec['prompt']
        for name in ('text', 'width', 'height', 'frames'):
            node, field = spec['fields'][name]
            graph[node]['inputs'][field]
        for node, field in spec['references']:
            graph[node]['inputs'][field]
        graph[spec['output']]
        if not 1 <= reference_count <= 5 or len(spec['references']) != reference_count:
            raise ValueError
        if not any('MiniMaxH3' in node['class_type'] for node in graph.values()):
            raise ValueError
        return spec
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError('请先绑定算家云实例导出的 H3 工作流及输入输出节点。') from exc


def ready(base, reference_counts=()):
    try:
        specs = [profile(count) for count in sorted(set(reference_counts))]
        with session() as client:
            response = client.get(base + '/object_info', timeout=5, allow_redirects=False)
            if response.status_code == 200:
                nodes = response.json()
                return bool(nodes) and all(node['class_type'] in nodes for spec in specs for node in spec['prompt'].values())
    except (requests.RequestException, ValueError, AttributeError):
        return False
    return False


def submit(base, prompt, duration, ratio, refs):
    from video_providers import RejectedRequest

    spec = profile(len(refs))
    graph = spec['prompt']
    if len(refs) != len(spec['references']):
        raise RejectedRequest('镜头参考图数量与绑定的 H3 工作流不一致。')
    width, height = (1344, 768) if ratio == '16:9' else (768, 1344)
    values = {'text': prompt, 'width': width, 'height': height,
              'frames': 5 + 17 * round((duration * 24 - 5) / 17)}
    for name, value in values.items():
        node, field = spec['fields'][name]
        graph[node]['inputs'][field] = value
    with session() as client:
        for (node, field), ref in zip(spec['references'], refs):
            raw = base64.b64decode(ref.split(',', 1)[1], validate=True)
            try:
                upload = client.post(base + '/upload/image', data={'type': 'input'},
                                     files={'image': (uuid.uuid4().hex + '.jpg', raw, 'image/jpeg')},
                                     timeout=(5, 60), allow_redirects=False)
                if upload.status_code != 200:
                    raise ValueError
                file = upload.json()
                if not re.fullmatch(r'[a-f0-9]{32}\.jpg', file['name']) or file.get('subfolder'):
                    raise ValueError
            except (requests.RequestException, ValueError, KeyError) as exc:
                raise RejectedRequest('参考图无法上传至算家云，本镜尚未提交。') from exc
            graph[node]['inputs'][field] = file['name']
        response = client.post(base + '/prompt', json={'prompt': graph}, timeout=(5, 30), allow_redirects=False)
        if 400 <= response.status_code < 500:
            raise RejectedRequest('ComfyUI 拒绝此工作流，请核对节点、模型和参数。')
        if response.status_code != 200:
            raise RuntimeError('ComfyUI 提交结果待核对。')
        identifier = response.json().get('prompt_id', '')
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,160}', identifier):
        raise RuntimeError('ComfyUI 未返回有效任务编号。')
    return identifier


def history(client, base, identifier):
    response = client.get(f'{base}/history/{identifier}', timeout=(5, 15), allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError('暂时无法查询云端镜头。')
    return response.json().get(identifier)


def poll(base, identifier):
    with session() as client:
        result = history(client, base, identifier)
        if result is None:
            queue = client.get(base + '/queue', timeout=(5, 15), allow_redirects=False)
            if queue.status_code != 200:
                raise RuntimeError('暂时无法查询云端队列。')
            state = queue.json()
            if any(len(row) > 1 and row[1] == identifier for row in state.get('queue_running', [])):
                return 'running'
            if any(len(row) > 1 and row[1] == identifier for row in state.get('queue_pending', [])):
                return 'queued'
            # History and queue are independent reads. The job may have finished between them.
            result = history(client, base, identifier)
            if result is None:
                return 'missing'
    status = result.get('status', {})
    return 'completed' if status.get('completed') and status.get('status_str') == 'success' else 'failed'


def download(base, identifier, path, reference_count):
    partial = path.with_suffix('.part')
    try:
        with session() as client:
            result = history(client, base, identifier)
            outputs = (result or {}).get('outputs', {}).get(profile(reference_count)['output'], {})
            videos = [item for files in outputs.values() if isinstance(files, list)
                      for item in files if isinstance(item, dict) and str(item.get('filename', '')).endswith('.mp4')]
            if len(videos) != 1 or videos[0].get('type') != 'output':
                raise ValueError('工作流没有返回唯一的 MP4 成片。')
            file = videos[0]
            params = {key: file[key] for key in ('filename', 'subfolder', 'type')}
            with client.get(base + '/view', params=params, stream=True, timeout=(5, 120),
                            allow_redirects=False) as response:
                if response.status_code != 200:
                    raise RuntimeError('无法取回云端镜头。')
                with partial.open('wb') as output:
                    size = 0
                    for chunk in response.iter_content(1024 * 1024):
                        size += len(chunk)
                        if size > 300_000_000:
                            raise ValueError('单镜文件超过 300 MB。')
                        output.write(chunk)
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(partial),
                        '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
                       check=True, capture_output=True, timeout=120)
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def ensure_tunnel():
    """Start forwarding to a configured SSH alias when the instance is reachable."""
    base = endpoint()
    if not base:
        raise ValueError('请配置本机 H3 SSH 隧道端口。')
    if ready(base):
        return base
    alias = os.environ.get('MORPHO_H3_SSH_HOST', '')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', alias):
        raise ValueError('请配置算家云实例的 SSH 主机别名。')
    remote = os.environ.get('MORPHO_H3_COMFY_PORT', '8188')
    if not remote.isdecimal() or not 1 <= int(remote) <= 65535:
        raise ValueError('ComfyUI 端口无效。')
    local_port = base.rsplit(':', 1)[1]
    try:
        subprocess.run(['ssh', '-fNT', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                        '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15',
                        '-o', 'ServerAliveCountMax=3', '-L', f'127.0.0.1:{local_port}:127.0.0.1:{remote}',
                        alias], check=True, timeout=20, capture_output=True)
    except (subprocess.SubprocessError, OSError) as exc:
        if not ready(base):
            raise ValueError('算家云 SSH 隧道未建立，请检查实例和本机 SSH 登录配置。') from exc
    return base
