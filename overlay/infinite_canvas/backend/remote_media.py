"""Remote Qwen media capabilities sharing the visitor's API key."""
from __future__ import annotations

import base64
import re
from urllib.parse import urlsplit

import httpx

from byok import current_credential


_MULTIMODAL = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
_MAX_DOWNLOAD = 24 * 1024 * 1024


def _qwen_settings(*, workspace_required: bool = False) -> tuple[str, str]:
    credential = current_credential() or {}
    key = credential.get('qwen_key') or (credential.get('key') if credential.get('provider') == 'qwen' else '')
    workspace = credential.get('qwen_workspace_id', '')
    if not key:
        raise RuntimeError('此能力需要 Qwen API Key')
    if workspace_required and not workspace:
        raise RuntimeError('音效、音乐和图像需要已开通的 Qwen Workspace ID')
    return key, workspace


def _post(url: str, payload: dict, key: str, *, timeout: float = 180) -> dict:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(url, json=payload, headers={'Authorization': f'Bearer {key}'})
        if response.status_code in {401, 403}:
            raise RuntimeError('Qwen 服务拒绝密钥或模型权限')
        if response.status_code == 429:
            raise RuntimeError('Qwen 服务限流，请稍后重试')
        if response.status_code >= 400:
            raise RuntimeError(f'Qwen 服务返回 HTTP {response.status_code}')
        data = response.json()
        if data.get('code') and data.get('code') not in {'200', 200}:
            raise RuntimeError(f'Qwen 服务返回 {data.get("code")}')
        return data
    except httpx.RequestError as exc:
        raise RuntimeError('Qwen 服务暂时不可连接') from exc


def _download_aliyun_asset(url: str) -> bytes:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or '').lower()
    if parsed.scheme not in {'https', 'http'} or not hostname.endswith('.aliyuncs.com'):
        raise RuntimeError('媒体服务返回了未允许的下载地址')
    try:
        with httpx.Client(timeout=90, follow_redirects=False) as client:
            with client.stream('GET', url) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > _MAX_DOWNLOAD:
                        raise RuntimeError('媒体文件超过 24 MB 限制')
                    chunks.append(chunk)
        result = b''.join(chunks)
    except httpx.RequestError as exc:
        raise RuntimeError('媒体文件下载失败') from exc
    if not result:
        raise RuntimeError('媒体服务返回空文件')
    return result


def _audio_bytes(result: dict) -> bytes:
    audio = (result.get('output') or {}).get('audio') or {}
    if audio.get('data'):
        try:
            return base64.b64decode(audio['data'], validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise RuntimeError('媒体服务返回无效音频数据') from exc
    if audio.get('url'):
        return _download_aliyun_asset(str(audio['url']))
    raise RuntimeError('媒体服务未返回音频')


def synthesize_speech(text: str, *, voice: str = 'Cherry', instructions: str = '', key: str | None = None) -> bytes:
    if key is None:
        key, _ = _qwen_settings()
    text = str(text or '').strip()
    if not text or len(text) > 1000:
        raise ValueError('单条配音文本须在 1 至 1,000 字之间')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{1,40}', voice):
        raise ValueError('音色名称无效')
    payload = {'model': 'qwen3-tts-flash', 'input': {'text': text, 'voice': voice}}
    if instructions:
        payload['model'] = 'qwen3-tts-instruct-flash'
        payload['input'].update(language_type='Chinese', instructions=instructions, optimize_instructions=True)
    result = _post(_MULTIMODAL, payload, key)
    return _audio_bytes(result)


def transcribe_speech(audio: bytes, *, mime_type: str = 'audio/wav') -> str:
    key, _ = _qwen_settings()
    if not audio or len(audio) > 7 * 1024 * 1024:
        raise ValueError('单次回听音频须在 7 MB 以内')
    if mime_type not in {'audio/wav', 'audio/mpeg'}:
        raise ValueError('仅支持 WAV 或 MP3 回听')
    data_uri = f'data:{mime_type};base64,{base64.b64encode(audio).decode("ascii")}'
    result = _post(_MULTIMODAL, {
        'model': 'qwen3-asr-flash',
        'input': {'messages': [{'role': 'user', 'content': [{'audio': data_uri}]}]},
        'parameters': {'asr_options': {'enable_itn': False}},
    }, key)
    try:
        content = result['output']['choices'][0]['message']['content']
        text = content[0]['text'] if isinstance(content, list) else content
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError('转写服务响应格式异常') from exc
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError('转写服务返回空内容')
    return text.strip()


def generate_sound(prompt: str, *, key: str | None = None, workspace: str | None = None) -> bytes:
    if key is None and workspace is None:
        key, workspace = _qwen_settings(workspace_required=True)
    if not key or not workspace or not re.fullmatch(r'[A-Za-z0-9_-]{4,64}', workspace):
        raise RuntimeError('音效生成需要有效的百炼密钥与 Workspace ID。')
    endpoint = f'https://{workspace}.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer'
    result = _post(endpoint, {'model': 'qwen-audio-3.1-tts-next',
                              'input': {'text_prompt': prompt[:1000], 'format': 'wav',
                                        'sample_rate': 48000, 'channels': 2}}, key, timeout=300)
    return _audio_bytes(result)


def generate_music(prompt: str) -> bytes:
    key, workspace = _qwen_settings(workspace_required=True)
    endpoint = f'https://{workspace}.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/music/generation'
    result = _post(endpoint, {'model': 'fun-music-v1', 'input': {'prompt': prompt[:2000]}}, key, timeout=300)
    return _audio_bytes(result)


def generate_image(prompt: str) -> bytes:
    key, workspace = _qwen_settings(workspace_required=True)
    endpoint = f'https://{workspace}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/images/generations'
    result = _post(endpoint, {'model': 'qwen-image-3.0', 'prompt': prompt[:2000],
                            'size': '1024x1024', 'n': 1}, key, timeout=180)
    try:
        row = result['data'][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError('图像服务响应格式异常') from exc
    if row.get('b64_json'):
        try:
            return base64.b64decode(row['b64_json'], validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise RuntimeError('图像服务返回无效图像数据') from exc
    if row.get('url'):
        return _download_aliyun_asset(str(row['url']))
    raise RuntimeError('图像服务未返回图片')
