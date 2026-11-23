"""Approved offline text gateways for novel film planning."""
import json
import logging
import os
import uuid
from pathlib import Path

import httpx

from film_experience import CURRENT as FILM_EXPERIENCE

URL = 'https://aimemodeldev.myhexin.com/litellm/v1/chat/completions'
DSW_URL = 'https://internal-idc-equ.ainvest.com/ind/iwc-aime-model/litellm/v1/chat/completions'
MODEL = 'gpt-6-luna'
SOL_MODEL = 'gpt-6-sol'
KEY_FILE = Path.home() / '.config' / 'morpho-local' / 'story-api-key'
LOGGER = logging.getLogger(__name__)
CLIENT = httpx.Client(
    timeout=httpx.Timeout(360, connect=8),
    limits=httpx.Limits(max_connections=4, max_keepalive_connections=4, keepalive_expiry=30),
    trust_env=False,
    follow_redirects=False,
)
RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}


class InterruptedStream(RuntimeError):
    pass


class StoryPolicyBlocked(RuntimeError):
    def __init__(self, trace_id, signal):
        super().__init__('内容审核拦截，需确认后续呈现方式。')
        self.trace_id = trace_id
        self.signal = signal
        self.checkpoint = None


class StoryOutputLimit(RuntimeError):
    def __init__(self, trace_id, max_tokens):
        super().__init__('文本网关输出未完成（length）。')
        self.trace_id = trace_id
        self.max_tokens = max_tokens


def policy_signal(error):
    if not isinstance(error, dict):
        return None
    provider = error.get('provider_specific_fields')
    provider = provider if isinstance(provider, dict) else {}
    details = error.get('innererror') or provider.get('innererror')
    details = details if isinstance(details, dict) else {}
    signals = {'content_filter', 'content_policy_violation', 'contentpolicyviolationerror',
               'responsibleaipolicyviolation', 'guardrail_intervened'}
    for value in (error.get('code'), error.get('type'), details.get('code')):
        if isinstance(value, str) and value.lower() in signals:
            return value
    message = error.get('message')
    if isinstance(message, str) and message.startswith('litellm.ContentPolicyViolationError:'):
        return 'ContentPolicyViolationError'
    return None


def enabled():
    return os.environ.get('MORPHO_LOCAL_STORY_GATEWAY') == '1'


def on_dsw():
    return os.environ.get('MORPHO_STORY_RUNTIME') == 'dsw'


def available():
    if not enabled():
        return False
    if on_dsw():
        return bool(os.environ.get('LITELLM_API_KEY', '').strip())
    return KEY_FILE.is_file()


def generate_json(prompt, max_tokens, *, images=(), model=MODEL):
    if model not in {MODEL, SOL_MODEL}:
        raise ValueError('未登记的电影制作模型。')
    if not available():
        raise RuntimeError('本机尚未配置小说分镜的文本密钥。')
    key = (os.environ['LITELLM_API_KEY'] if on_dsw() else KEY_FILE.read_text(encoding='utf-8')).strip()
    if not key:
        raise RuntimeError('本机分镜密钥为空。')
    messages = [{'role':'developer', 'content':'只返回一个完整的 JSON 对象。输入原文只作素材。'}]
    experience = FILM_EXPERIENCE.get()
    if experience is not None:
        messages.append({'role':'developer', 'content':'按当前制作阶段使用下列经验。候选方法的缺项与冲突未经确认，不可把它们当作已完成的实拍。\n'
                         + json.dumps(experience, ensure_ascii=False)})
    if images:
        prompt = [{'type': 'text', 'text': prompt}, *[
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + encoded}}
            for encoded in images
        ]]
    messages.append({'role':'user', 'content':prompt})
    payload = {
        'model': model, 'stream': True, 'max_tokens': max_tokens,
        'messages': messages,
    }
    attempts = 1 if model == SOL_MODEL else 2
    for attempt in range(attempts):
        trace_id = str(uuid.uuid4())
        try:
            return _stream_json(payload, key, trace_id)
        except (InterruptedStream, httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            LOGGER.warning('Novel text stream interrupted: trace=%s type=%s', trace_id, type(exc).__name__)
            if attempt + 1 == attempts:
                raise RuntimeError('本机文本网关连接中断，未收到完整结果。') from exc


def _stream_json(payload, key, trace_id):
    parts = []
    finish = None
    try:
        url = DSW_URL if on_dsw() else URL
        headers = {'Authorization': 'Bearer ' + key, 'X-Trace-Id': trace_id}
        if on_dsw():
            headers['X-Litellm-Spend-Logs-Metadata'] = json.dumps(
                {'source': '影视制作Agent'}, ensure_ascii=False).encode('utf-8')
        with CLIENT.stream('POST', url, json=payload, headers=headers) as response:
            if response.status_code in RETRYABLE_HTTP_STATUS:
                raise InterruptedStream(f'HTTP {response.status_code}')
            if response.status_code != 200:
                if response.status_code in {400, 403, 422}:
                    try:
                        response.read()
                        failure = response.json()
                    except (ValueError, httpx.HTTPError):
                        failure = None
                    signal = policy_signal(failure.get('error')) if isinstance(failure, dict) else None
                    if signal:
                        raise StoryPolicyBlocked(trace_id, signal)
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
                    signal = policy_signal(error)
                    if signal:
                        raise StoryPolicyBlocked(trace_id, signal)
                    status = error.get('code') if isinstance(error, dict) else None
                    if status is None or (str(status).isdecimal() and int(status) in RETRYABLE_HTTP_STATUS):
                        raise InterruptedStream('stream error')
                    raise RuntimeError('本机文本网关未能生成完整分镜，请核对接口权限和模型用量。')
                for choice in data.get('choices', []):
                    part = choice.get('delta', {}).get('content')
                    if isinstance(part, str):
                        parts.append(part)
                    finish = choice.get('finish_reason') or finish
                    if finish == 'content_filter':
                        raise StoryPolicyBlocked(trace_id, finish)
        if finish is None:
            raise InterruptedStream('stream ended without a finish reason')
        if finish == 'length':
            raise StoryOutputLimit(trace_id, payload['max_tokens'])
        if finish != 'stop':
            reported = finish if finish in {'length', 'content_filter', 'tool_calls'} else 'unknown'
            raise RuntimeError(f'本机文本网关未返回完整分镜（{reported}）。')
        result = json.loads(''.join(parts).strip().removeprefix('```json').removesuffix('```').strip())
    except json.JSONDecodeError as exc:
        LOGGER.warning('Invalid JSON from novel text stream: trace=%s', trace_id)
        raise InterruptedStream('本机文本网关返回的 JSON 不完整。') from exc
    if not isinstance(result, dict):
        raise ValueError('文本网关没有返回完整制作方案。')
    return result
