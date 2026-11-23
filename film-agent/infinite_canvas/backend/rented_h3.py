"""Native ComfyUI H3 on DSW, reached through a local SSH tunnel."""
import base64
import logging
import os
import re
import secrets
import subprocess
import uuid

import requests

from dsw_h3_graph import AUDIO_VAE, CLIP, OUTPUT, TURBO, UNET, VIDEO_VAE, profile

MODEL = 'ComfyUI MiniMax H3'
logger = logging.getLogger(__name__)


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


def ready(base, reference_counts=(), *, motion=False, preview=False, voice_count=0, check_weights=True):
    try:
        with session() as client:
            response = client.get(base + '/object_info', timeout=5, allow_redirects=False)
            response.raise_for_status()
            nodes = response.json()
        required = profile(max(reference_counts, default=1), motion=motion, preview=preview,
                           voice_count=voice_count)['prompt']
        if not all(node['class_type'] in nodes for node in required.values()):
            return False
        if voice_count and 'ref_audios' not in nodes['MiniMaxH3ReferenceToVideo']['input']['optional']:
            return False
        if not check_weights:
            return True
        weights = [('UNETLoader', 'unet_name', UNET), ('CLIPLoader', 'clip_name', CLIP),
                   ('VAELoader', 'vae_name', VIDEO_VAE), ('VAELoader', 'vae_name', AUDIO_VAE)]
        if preview:
            weights.append(('LoraLoaderModelOnly', 'lora_name', TURBO))
        return all(value in nodes[node]['input']['required'][field][0] for node, field, value in weights)
    except (requests.RequestException, ValueError, AttributeError, KeyError, IndexError, TypeError):
        return False


def upload_input(client, base, raw, extension, content_type):
    from video_providers import RejectedRequest

    name = uuid.uuid4().hex + extension
    try:
        upload = client.post(base + '/upload/image', data={'type': 'input'},
                             files={'image': (name, raw, content_type)},
                             timeout=(5, 90), allow_redirects=False)
        upload.raise_for_status()
        file = upload.json()
        if file.get('name') != name or file.get('subfolder') or file.get('type') != 'input':
            raise ValueError('Unexpected upload response')
        return name
    except (requests.RequestException, ValueError) as exc:
        raise RejectedRequest('镜头参考素材未能上传，镜头尚未提交。') from exc


def submit(base, prompt, duration, ratio, refs, *, motion_reference=None, voice_references=(), preview=False,
           size=None, seed=None, identifier=None):
    from video_providers import RejectedRequest

    spec = profile(len(refs), motion=motion_reference is not None, preview=preview, voice_count=len(voice_references))
    graph = spec['prompt']
    width, height = size or ((1344, 768) if ratio == '16:9' else (768, 1344))
    if min(width, height) < 256 or max(width, height) > 1536 or width % 32 or height % 32:
        raise ValueError('H3 分辨率必须在 256 至 1536 间，并且是 32 的倍数。')
    values = {'text': prompt, 'width': width, 'height': height,
              'frames': 5 + 17 * round((duration * 24 - 5) / 17)}
    for name, value in values.items():
        node, field = spec['fields'][name]
        graph[node]['inputs'][field] = value
    graph['noise']['inputs']['noise_seed'] = secrets.randbits(32) if seed is None else seed
    identifier = str(uuid.UUID(identifier)) if identifier is not None else str(uuid.uuid4())
    graph[OUTPUT]['inputs']['filename_prefix'] = f'morpho-h3/{identifier}'
    with session() as client:
        for (node, field), ref in zip(spec['references'], refs):
            try:
                _, encoded = ref.split(',', 1)
                raw = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise RejectedRequest('参考图无法解码，镜头尚未提交。') from exc
            if raw.startswith(b'\x89PNG\r\n\x1a\n'):
                extension, content_type = '.png', 'image/png'
            elif raw.startswith(b'\xff\xd8\xff'):
                extension, content_type = '.jpg', 'image/jpeg'
            elif raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
                extension, content_type = '.webp', 'image/webp'
            else:
                raise RejectedRequest('参考图格式仅支持 PNG、JPEG 或 WebP。')
            graph[node]['inputs'][field] = upload_input(client, base, raw, extension, content_type)
        for node, voice in zip(spec['voices'], voice_references, strict=True):
            if voice.suffix.lower() != '.wav' or not 0 < voice.stat().st_size <= 4_000_000:
                raise RejectedRequest('角色声音参考须是 4 MB 以内的 WAV。')
            with voice.open('rb') as recording:
                graph[node]['inputs']['audio'] = upload_input(client, base, recording, '.wav', 'audio/wav')
        if motion_reference is not None:
            if motion_reference.suffix.lower() != '.mp4' or not 0 < motion_reference.stat().st_size < 90_000_000:
                raise RejectedRequest('动作参考必须是小于 90 MB 的 MP4 文件。')
            with motion_reference.open('rb') as video:
                graph['motion']['inputs']['file'] = upload_input(client, base, video, '.mp4', 'video/mp4')
        response = client.post(base + '/prompt', json={'prompt': graph, 'prompt_id': identifier},
                               timeout=(5, 45), allow_redirects=False)
        if 400 <= response.status_code < 500:
            logger.warning('H3 rejected prompt %s: %.3500s', identifier, response.text)
            raise RejectedRequest('此镜头的素材或渲染参数不符合 H3 要求，请核对分镜和参考文件。')
        if response.status_code != 200:
            raise RuntimeError('ComfyUI 提交结果待核对。')
        if response.json().get('prompt_id') != identifier:
            raise RuntimeError('ComfyUI 未返回预期的任务编号。')
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


def output_file(client, base, identifier):
    result = history(client, base, identifier)
    outputs = (result or {}).get('outputs', {}).get(OUTPUT, {})
    videos = [item for files in outputs.values() if isinstance(files, list)
              for item in files if isinstance(item, dict) and str(item.get('filename', '')).endswith('.mp4')]
    if len(videos) != 1 or videos[0].get('type') != 'output':
        raise ValueError('工作流没有返回唯一的 MP4 成片。')
    return videos[0]


def download(base, identifier, path):
    partial = path.with_suffix('.part')
    try:
        with session() as client:
            file = output_file(client, base, identifier)
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
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-xerror', '-i', str(partial),
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
    def listening():
        try:
            with session() as client:
                response = client.get(base + '/queue', timeout=4, allow_redirects=False)
                return response.ok and isinstance(response.json().get('queue_running'), list)
        except (requests.RequestException, TypeError, AttributeError, ValueError):
            return False

    if listening():
        return base
    alias = os.environ.get('MORPHO_H3_SSH_HOST', '')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', alias):
        raise ValueError('请配置 DSW 的 SSH 主机别名。')
    remote = os.environ.get('MORPHO_H3_COMFY_PORT', '8188')
    if not remote.isdecimal() or not 1 <= int(remote) <= 65535:
        raise ValueError('ComfyUI 端口无效。')
    local_port = base.rsplit(':', 1)[1]
    try:
        subprocess.run(['ssh', '-fNT', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                        '-o', 'StrictHostKeyChecking=yes', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15',
                        '-o', 'ServerAliveCountMax=3', '-L', f'127.0.0.1:{local_port}:127.0.0.1:{remote}',
                        alias], check=True, timeout=20, capture_output=True)
    except (subprocess.SubprocessError, OSError) as exc:
        if not listening():
            raise ValueError('DSW SSH 隧道未建立，请检查远端服务和本机 SSH 登录配置。') from exc
    return base
