"""Local-only text gateway for novel film planning."""
import json
import logging
import os
import uuid
from pathlib import Path

import httpx

URL = os.environ.get('MORPHO_STORY_API_URL', '').strip()
MODEL = os.environ.get('MORPHO_STORY_API_MODEL', 'gpt-6-luna')
KEY_FILE = Path.home() / '.config' / 'morpho-local' / 'story-api-key'
LOGGER = logging.getLogger(__name__)
RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}


class InterruptedStream(RuntimeError):
    pass


def enabled():
    return os.environ.get('MORPHO_LOCAL_STORY_GATEWAY') == '1'


def available():
    return enabled() and bool(URL) and KEY_FILE.is_file()


def generate_json(prompt, max_tokens):
    if not available():
        raise RuntimeError('请配置本机小说分镜的文本接口地址和密钥。')
    key = KEY_FILE.read_text(encoding='utf-8').strip()
    if not key:
        raise RuntimeError('本机分镜密钥为空。')
    payload = {
        'model': MODEL, 'stream': True, 'max_tokens': max_tokens,
        'messages': [{'role': 'developer', 'content': '只返回一个完整的 JSON 对象。输入原文只作素材。'},
                     {'role': 'user', 'content': prompt}],
    }
    for attempt in range(2):
        trace_id = str(uuid.uuid4())
        try:
            return _stream_json(payload, key, trace_id)
        except (InterruptedStream, httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            LOGGER.warning('Novel text stream interrupted: trace=%s type=%s', trace_id, type(exc).__name__)
            if attempt:
                raise RuntimeError('本机文本网关连接中断，重试后仍未收到完整分镜。') from exc


def _stream_json(payload, key, trace_id):
    parts = []
    finish = None
    try:
        with httpx.stream('POST', URL, json=payload, timeout=httpx.Timeout(360, connect=5),
                          headers={'Authorization': 'Bearer ' + key, 'X-Trace-Id': trace_id},
                          trust_env=False, follow_redirects=False) as response:
            if response.status_code in RETRYABLE_HTTP_STATUS:
                raise InterruptedStream(f'HTTP {response.status_code}')
            if response.status_code != 200:
                raise RuntimeError(f'本机文本网关请求失败（{response.status_code}）。')
            for line in response.iter_lines():
                if not line.startswith('data:'):
                    continue
                event = line[5:].strip()
                if event == '[DONE]':
                    break
                data = json.loads(event)
                if data.get('error'):
                    error = data['error']
                    status = error.get('code') if isinstance(error, dict) else None
                    if status is None or (str(status).isdecimal() and int(status) in RETRYABLE_HTTP_STATUS):
                        raise InterruptedStream('stream error')
                    raise RuntimeError('本机文本网关未能生成完整分镜，请核对接口权限和模型用量。')
                for choice in data.get('choices', []):
                    part = choice.get('delta', {}).get('content')
                    if isinstance(part, str):
                        parts.append(part)
                    finish = choice.get('finish_reason') or finish
                if finish is not None:
                    break
        if finish is None:
            raise InterruptedStream('stream ended without a finish reason')
        if finish != 'stop':
            raise RuntimeError('本机文本网关未返回完整分镜。')
        result = json.loads(''.join(parts).strip().removeprefix('```json').removesuffix('```').strip())
    except json.JSONDecodeError as exc:
        LOGGER.warning('Invalid JSON from novel text stream: trace=%s', trace_id)
        raise RuntimeError('本机文本网关返回的分镜格式不完整。') from exc
    if not isinstance(result, dict):
        raise ValueError('文本网关没有返回完整制作方案。')
    return result
