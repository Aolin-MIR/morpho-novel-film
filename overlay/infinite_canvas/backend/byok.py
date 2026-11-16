"""Request-scoped, allowlisted user credentials for Morpho generation."""
from __future__ import annotations

from contextvars import ContextVar
import json
import re
import time
from typing import Any

import httpx


_CREDENTIAL: ContextVar[dict[str, str] | None] = ContextVar('morpho_byok', default=None)
from model_providers import CATALOG, model_config, prepare_request, parse_response
_PROVIDERS = {key:(v['endpoint'],v['models'][0]['id']) for key,v in CATALOG.items()}
_MEDIA_HEADER = 'X-Morpho-Qwen-Api-Key'


def validate_credential(provider: str, key: str) -> dict[str, str]:
    provider = str(provider or '').strip().lower()
    key = str(key or '').strip()
    if provider not in _PROVIDERS:
        raise ValueError('请选择受支持的 API 服务商')
    if not 8 <= len(key) <= 512 or re.search(r'\s', key):
        raise ValueError('API Key 格式无效')
    return {'provider': provider, 'key': key}


def _validate_optional_key(key: str) -> str:
    key = str(key or '').strip()
    if key and (not 8 <= len(key) <= 512 or re.search(r'\s', key)):
        raise ValueError('Qwen API Key 格式无效')
    return key


def set_credential(value: dict[str, str] | None):
    return _CREDENTIAL.set(value)


def reset_credential(token: Any) -> None:
    _CREDENTIAL.reset(token)


def current_credential() -> dict[str, str] | None:
    return _CREDENTIAL.get()


def recovery_notice(message):
    from flask import g, has_request_context
    if has_request_context() and getattr(g,'generation_run_id',None):
        from auth_store import append_generation_event
        append_generation_event(g.generation_run_id,'model_recovery','正在自动调整',message,
            {'stage':'recovery','state':message})


def generate_text(prompt: str, *, system_prompt: str | None = None,
                  temperature: float = 0.7, max_tokens: int | None = 2000,
                  timeout: float = 120, model: str | None = None,
                  json_response: bool = False, enable_thinking: bool | None = None, _attempt: int = 0) -> str:
    credential = current_credential()
    if not credential:
        raise RuntimeError('请先添加自己的模型 API Key；本站不会使用服务器密钥')
    endpoint,payload,headers,protocol=prepare_request(credential,prompt,system_prompt,temperature,max_tokens,json_response,enable_thinking,model)
    def retry_request(reason):
        if _attempt>=2:
            raise RuntimeError(reason+'；已自动重试两次，现有内容已保留，可稍后继续。')
        recovery_notice(f'{reason}，正在自动重试（{_attempt+1}/2）')
        time.sleep(2**_attempt)
        return generate_text(prompt,system_prompt=system_prompt,temperature=temperature,
            max_tokens=max_tokens,timeout=timeout,model=model,json_response=json_response,
            enable_thinking=enable_thinking,_attempt=_attempt+1)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(endpoint, json=payload, headers=headers)
        if response.status_code in {401, 403}:
            raise RuntimeError('模型服务拒绝了 API Key，请检查服务商和权限')
        if response.status_code == 429:
            return retry_request('服务繁忙')
        if response.status_code in {408,500,502,503,504}:
            return retry_request('服务暂时未响应')
        if response.status_code >= 400:
            raise RuntimeError(f'模型服务返回 HTTP {response.status_code}')
        data = response.json()
        content,finish=parse_response(data,protocol)
        if finish in {'content_filter','safety','SAFETY'}:
            raise RuntimeError('服务商未接受本次内容，请调整创作要求后继续；已有稿件已保留。')
        if not isinstance(content, str) or not content.strip():
            return retry_request('暂未收到有效正文')
        malformed = False
        if json_response:
            candidate = content.strip().lstrip('\ufeff')
            if candidate.startswith('```') and candidate.endswith('```'):
                candidate = re.sub(r'^```(?:json)?\s*|\s*```$', '', candidate)
            try:
                parsed = json.loads(candidate)
                malformed = not isinstance(parsed, dict)
            except (ValueError, TypeError):
                # Remove explanatory wrappers only; never fabricate missing fields.
                try:
                    parsed,end=json.JSONDecoder().raw_decode(candidate[candidate.index('{'):])
                    malformed=not isinstance(parsed,dict)
                    if not malformed:candidate=json.dumps(parsed,ensure_ascii=False)
                except (ValueError,TypeError):malformed=True
            if not malformed:
                content = candidate
        if finish == 'length' or malformed:
            if _attempt < 2:
                recovery_notice('回复尚未完整，正在调整篇幅与格式后继续生成')
                return generate_text(prompt + '\n\n[上次输出与失败信息，仅供校正]\n' + json.dumps({'previous_response':content[:12000], 'validation_error':'输出达到长度限制，正文未结束' if finish=='length' else '输出不是可解析的完整JSON对象', 'correction_requirements':'修正格式并保留所有必需字段。重新返回完整结果，不能编造原文引文或省略必需场面。'},ensure_ascii=False), system_prompt=(system_prompt or '') +
                    '\n输出须完整且精炼。缩短描述与列表，保留全部必要字段；只返回完整JSON对象。' if json_response else
                    (system_prompt or '') + '\n请压缩文字，在本次输出内完整结束，不要留下未完成的段落。',
                    temperature=temperature, max_tokens=min(max(max_tokens or 2000, 4000)*2, 32000),
                    timeout=timeout, model=model, json_response=json_response,
                    enable_thinking=enable_thinking, _attempt=_attempt+1)
            raise RuntimeError('内容过长或格式未完整，已自动重新生成两次。请减少本次场面数量或分章生成。')
        return content.strip()
    except httpx.TimeoutException as exc:
        return retry_request('本次等待较久')
    except httpx.RequestError as exc:
        return retry_request('连接暂时中断')
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise RuntimeError('模型响应格式异常，请检查所选服务商') from exc


def credential_from_headers(headers: Any) -> dict[str, str] | None:
    provider = headers.get('X-Morpho-Provider', '')
    key = headers.get('X-Morpho-Api-Key', '')
    qwen_key = _validate_optional_key(headers.get(_MEDIA_HEADER, ''))
    workspace_id = str(headers.get('X-Morpho-Qwen-Workspace-Id', '') or '').strip()
    if workspace_id and not re.fullmatch(r'[A-Za-z0-9_-]{4,64}', workspace_id):
        raise ValueError('Qwen Workspace ID 格式无效')
    if not provider and not key and not qwen_key:
        return None
    credential = validate_credential(provider, key) if provider or key else {}
    thinking = str(headers.get('X-Morpho-Thinking', '') or '').strip()
    if thinking:
        if thinking not in {'on', 'off'}:
            raise ValueError('思考模式设置无效。')
        credential['thinking'] = thinking
    selected_model = str(headers.get('X-Morpho-Model', '') or '').strip()
    if selected_model:
        model_config(credential.get('provider'),selected_model)
        credential['model'] = selected_model
    if qwen_key:
        credential['qwen_key'] = qwen_key
    if workspace_id:
        credential['qwen_workspace_id'] = workspace_id
    return credential


def cache_credential(redis: Any, run_id: str, credential: dict[str, str]) -> None:
    redis.setex(f'morpho:byok:{run_id}', 7200, json.dumps(credential))


def pop_credential(redis: Any, run_id: str) -> dict[str, str] | None:
    key = f'morpho:byok:{run_id}'
    pipe = redis.pipeline()
    pipe.get(key)
    pipe.delete(key)
    raw, _ = pipe.execute()
    if not raw:
        return None
    value = json.loads(raw)
    return credential_from_headers({
        'X-Morpho-Provider': value.get('provider', ''),
        'X-Morpho-Api-Key': value.get('key', ''),
        'X-Morpho-Model': value.get('model', ''),
        'X-Morpho-Thinking': value.get('thinking', ''),
        'X-Morpho-Qwen-Api-Key': value.get('qwen_key', ''),
        'X-Morpho-Qwen-Workspace-Id': value.get('qwen_workspace_id', ''),
    })
