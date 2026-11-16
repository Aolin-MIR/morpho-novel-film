import copy
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from flask import g
from byok import pop_credential, set_credential, reset_credential
from generation_queue import get_redis_connection

import app as app_module
from auth_store import (
    append_generation_event,
    finish_generation_run,
    load_generation_run,
    touch_generation_run,
    update_generation_run_status,
)
from workspace_runtime import manager_from_payload
from generation_runtime.evidence import merge_runtime_evidence
from generation_runtime.repository import GenerationRepository
from generation_runtime.service import GenerationService
from generation_heartbeat import generation_run_heartbeat


_LOCK_PATH = Path(__file__).resolve().parent / 'temp' / 'generation_worker.lock'
_RUNTIME_REPOSITORY = GenerationRepository(Path(__file__).resolve().parent / 'studio.db')
_HEARTBEAT_SECONDS = max(5.0, float(os.getenv('GENERATION_HEARTBEAT_SECONDS', '15')))


@contextmanager
def _byok_run_context(run_id: str, *, local_film=False):
    credential = pop_credential(get_redis_connection(), run_id)
    if os.getenv('MORPHO_BYOK_REQUIRED') == '1' and credential is None and not local_film:
        raise RuntimeError('API Key 已过期；请重新提交生成任务')
    token = set_credential(credential)
    try:
        yield
    finally:
        reset_credential(token)


@contextmanager
def _generation_worker_lock(run_id: str):
    """Serialize generation jobs across inline/RQ workers in this backend checkout."""
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = _LOCK_PATH.open('a+')
    try:
        if os.name == 'nt':
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _runner_for_type(generation_type: str) -> Tuple[str, Any]:
    mapping = {
        'structure': ('/structure/generate', app_module.generate_complete_structure),
        'script_full': ('/script/generate', app_module.generate_script),
        'script_device': ('/plot_devices/apply-impact', app_module.apply_plot_device_impact),
        'source_preparation': ('/source-preparation/generate', __import__('source_library').generate_source),
        'video_prompts': ('/video-prompts/generate', app_module.generate_video_prompts),
        'script_partial': ('/script/generate-partial', app_module.generate_partial_script),
        'script_polish': ('/script/polish', app_module.polish_script),
        'script_adaptation': ('/novel-adaptation/generate', app_module.novel_adaptation_generate),
    }
    if generation_type not in mapping:
        raise ValueError(f'Unsupported generation type: {generation_type}')
    return mapping[generation_type]


def _prepare_request_globals(
    user_id: str,
    project_id: str,
    run_id: str,
    workspace_payload: Dict[str, Any],
    workspace_hash: str,
) -> None:
    manager = manager_from_payload(workspace_payload or {}, strict=True)
    worldview = workspace_payload.get('worldview') if isinstance(workspace_payload, dict) else {}
    workspace_meta = workspace_payload.get('workspace_meta') if isinstance(workspace_payload, dict) else {}

    g.current_user = {'id': user_id}
    g.current_project = {'id': project_id}
    g.generation_run_id = run_id
    g.skip_workspace_autosave = False
    g.workspace_validation_issues = []
    g.workspace_base_hash = workspace_hash
    g.runtime_state = {
        'worldview': worldview or {},
        'workspace_meta': workspace_meta or {},
        'structured_story_payload': copy.deepcopy(workspace_payload or {}),
        'workflow_state': app_module._default_workflow_state(),
        'workflow_history': [dict(app_module._default_workflow_state())],
    }
    g.drama_manager = manager
    app_module.app.config['WORLDVIEW'] = worldview or {}


def _coerce_response(response: Any) -> Tuple[Any, int]:
    if isinstance(response, tuple):
        flask_response = response[0]
        status_code = int(response[1])
        return flask_response, status_code
    return response, int(getattr(response, 'status_code', 200))


def _merge_generation_http_body_into_result_payload(
    record: Optional[Dict[str, Any]],
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge the Flask JSON body into the run's result_payload for polling clients.

    Script routes return {'result': {...}} while structure routes return a flat dict.
    Handlers often call finish_generation_run with a trimmed payload; the worker must
    reconcile with the full HTTP response so GET /api/generation-runs/:id includes
    story_text, structured_story_payload, etc.
    """
    existing = dict((record or {}).get('result_payload') or {})
    if not isinstance(body, dict):
        return existing
    inner = body.get('result')
    if isinstance(inner, dict):
        return {**existing, **inner}
    return {**existing, **body}


def process_generation_run(
    run_id: str,
    generation_type: str,
    user_id: str,
    project_id: str,
    payload: Dict[str, Any],
    workspace_payload: Dict[str, Any],
    workspace_hash: str,
) -> Dict[str, Any]:
    _RUNTIME_REPOSITORY.migrate()
    path, runner = _runner_for_type(generation_type)
    update_generation_run_status(run_id, 'running')
    append_generation_event(
        run_id,
        'queued_worker_started',
        '后台任务已启动',
        f'类型: {generation_type}',
        {'generation_type': generation_type},
    )
    try:
        with generation_run_heartbeat(
            run_id, touch_generation_run, interval_seconds=_HEARTBEAT_SECONDS
        ), _generation_worker_lock(run_id), _byok_run_context(
            run_id, local_film=(generation_type == 'video_prompts' and bool(payload.get('film_kind'))
                                and __import__('local_story_gateway').available())
        ):
            append_generation_event(
                run_id,
                'queued_worker_lock_acquired',
                '后台任务获得串行执行锁',
                f'类型: {generation_type}',
                {'generation_type': generation_type},
            )
            with app_module.app.app_context():
                generated: Dict[str, Any] = {}

                def invoke(enriched_payload: Dict[str, Any]) -> Dict[str, Any]:
                    append_generation_event(
                        run_id,
                        'model_generation_started',
                        '模型生成已开始',
                        f'类型: {generation_type}',
                        {'generation_type': generation_type},
                    )
                    with app_module.app.test_request_context(
                        path,
                        method='POST',
                        json=enriched_payload,
                        headers={'X-Internal-Queue': '1'},
                    ):
                        _prepare_request_globals(user_id, project_id, run_id, workspace_payload or {}, workspace_hash)
                        response = runner()
                        flask_response, status_code = _coerce_response(response)
                        body = flask_response.get_json(silent=True) or {}
                        generated.update({'body': body, 'status_code': status_code})
                        if status_code >= 400:
                            message = body.get('error') or body.get('message') or f'Generation failed with status {status_code}'
                            raise RuntimeError(message)
                        append_generation_event(
                            run_id,
                            'model_generation_completed',
                            '模型生成已返回',
                            f'HTTP {status_code}',
                            {'generation_type': generation_type, 'status_code': status_code},
                        )
                        return body

                outcome = GenerationService(_RUNTIME_REPOSITORY).execute(
                    run_id=run_id,
                    project_id=project_id,
                    generation_type=generation_type,
                    payload=payload or {},
                    generator=invoke,
                )
                body = generated.get('body') or {}
                status_code = int(generated.get('status_code') or (200 if outcome.error is None else 500))
                if outcome.error:
                    append_generation_event(
                        run_id,
                        'queued_worker_failed',
                        '后台任务失败',
                        outcome.error,
                        {'generation_type': generation_type, 'status_code': status_code, 'stage': outcome.stage},
                    )
                    finish_generation_run(run_id, 'failed', None, outcome.error)
                    return {'status': 'failed', 'error': outcome.error, 'status_code': status_code}
                record = load_generation_run(run_id)
                merged = _merge_generation_http_body_into_result_payload(record, body)
                merged = merge_runtime_evidence(
                    merged,
                    artifact_ids=outcome.output_artifact_ids,
                    stage=outcome.stage or generation_type,
                )
                finish_generation_run(run_id, 'completed', merged)
                append_generation_event(
                    run_id,
                    'queued_worker_completed',
                    '后台任务完成',
                    f'{generation_type} 已完成',
                    {'generation_type': generation_type, 'status_code': status_code},
                )
                return {'status': 'completed', 'body': body, 'status_code': status_code}
    except Exception as exc:
        message = str(exc) or 'Generation worker crashed'
        append_generation_event(
            run_id,
            'queued_worker_exception',
            '后台任务异常',
            message,
            {'generation_type': generation_type},
        )
        finish_generation_run(run_id, 'failed', None, message)
        raise


def handle_generation_failure(job, _connection, exc_type, exc_value, _traceback):
    """Persist failures that kill the RQ work-horse before Python can catch them."""
    run_id = str(getattr(job, 'id', '') or '')
    if not run_id:
        return
    message = str(exc_value or getattr(exc_type, '__name__', '') or 'Generation work-horse terminated')
    append_generation_event(
        run_id,
        'queued_worker_terminated',
        '后台任务进程异常终止',
        message,
        {'failure_type': getattr(exc_type, '__name__', str(exc_type or 'unknown'))},
    )
    finish_generation_run(run_id, 'failed', None, message)
