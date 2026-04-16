#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/controller.py

from __future__ import annotations

import asyncio
import inspect
import logging
import traceback
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.workflow.state import get_workflow

logger = logging.getLogger(__name__)

try:
    from app.strategy_app.tasks import generate_strategy
except Exception:  # pragma: no cover
    generate_strategy = None  # type: ignore

try:
    import app.strategy_app.stream_store as strategy_stream_store
except Exception:  # pragma: no cover
    strategy_stream_store = None  # type: ignore

try:
    import app.strategy_app.ws as strategy_ws
except Exception:  # pragma: no cover
    strategy_ws = None  # type: ignore

try:
    from app.infra.redis_client import get_redis
except Exception:  # pragma: no cover
    get_redis = None  # type: ignore

try:
    from app.services import workflow_store as workflow_store_service
except Exception:  # pragma: no cover
    workflow_store_service = None  # type: ignore

try:
    from app.services import requirement_store as requirement_store_service
except Exception:  # pragma: no cover
    requirement_store_service = None  # type: ignore


ARQ_TASK_GENERATE_STRATEGY = "app.strategy_app.tasks.generate_strategy"
STRATEGY_QUEUE_NAME = "strategy_queue"


# =====================================================
# 工具函数
# =====================================================

def _workflow_get(workflow: Any, key: str, default: Any = None) -> Any:
    if workflow is None:
        return default
    if isinstance(workflow, dict):
        return workflow.get(key, default)
    return getattr(workflow, key, default)


def _workflow_set(workflow: Any, key: str, value: Any) -> None:
    if workflow is None:
        return
    if isinstance(workflow, dict):
        workflow[key] = value
        return
    try:
        setattr(workflow, key, value)
    except Exception:
        pass


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _pick_first_str(*values: Any, default: str = "") -> str:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


def _ensure_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_result_to_dict(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None

    if isinstance(raw, dict):
        return raw

    model_dump = getattr(raw, "model_dump", None)
    if callable(model_dump):
        try:
            data = model_dump()
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    dict_fn = getattr(raw, "dict", None)
    if callable(dict_fn):
        try:
            data = dict_fn()
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return None


def _resolve_attr(module: Any, names: list[str]) -> Any:
    if module is None:
        return None
    for name in names:
        if hasattr(module, name):
            return getattr(module, name)
    return None


async def _call_module_func(module: Any, names: list[str], *args: Any, **kwargs: Any) -> Any:
    fn = _resolve_attr(module, names)
    if fn is None:
        return None
    ret = fn(*args, **kwargs)
    return await _maybe_await(ret)


async def _maybe_persist_workflow(workflow_id: str, workflow: Any) -> None:
    if workflow_store_service is None:
        return

    candidate_calls = [
        ("save_workflow", (workflow_id, workflow)),
        ("update_workflow", (workflow_id, workflow)),
        ("set_workflow", (workflow_id, workflow)),
        ("upsert_workflow", (workflow_id, workflow)),
    ]

    for fn_name, args in candidate_calls:
        fn = getattr(workflow_store_service, fn_name, None)
        if callable(fn):
            try:
                ret = fn(*args)
                if inspect.isawaitable(ret):
                    await ret
                return
            except Exception:
                logger.warning("[strategy.controller] persist workflow failed by %s", fn_name, exc_info=True)


async def _get_workflow_safe(workflow_id: str) -> Any:
    workflow = get_workflow(workflow_id)
    if inspect.isawaitable(workflow):
        workflow = await workflow
    return workflow


async def _resolve_requirement_text(
    workflow_id: str,
    workflow: Any,
    requirement_id: Optional[str] = None,
) -> str:
    for key in ("requirement_text", "pdf_text", "text", "content", "requirement_content"):
        value = _workflow_get(workflow, key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if workflow_store_service is not None:
        candidate_calls = [
            ("get_requirement_text_by_workflow_id", (workflow_id,)),
            ("load_workflow_object", (workflow_id,)),
        ]

        for fn_name, args in candidate_calls:
            fn = getattr(workflow_store_service, fn_name, None)
            if not callable(fn):
                continue
            try:
                ret = fn(*args)
                if inspect.isawaitable(ret):
                    ret = await ret

                if isinstance(ret, str) and ret.strip():
                    return ret.strip()

                if isinstance(ret, dict):
                    for key in ("requirement_text", "pdf_text", "text", "content"):
                        value = ret.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()
            except TypeError:
                continue
            except Exception:
                logger.warning("[strategy.controller] resolve requirement text failed by %s", fn_name, exc_info=True)

    if requirement_store_service is not None:
        candidate_calls = []

        if requirement_id:
            candidate_calls.extend([
                ("load_requirement_text", (workflow_id, requirement_id)),
                ("get_requirement_text", (workflow_id, requirement_id)),
                ("read_requirement_text", (workflow_id, requirement_id)),
                ("get_requirement_text", (requirement_id,)),
                ("read_requirement_text", (requirement_id,)),
                ("get_requirement", (requirement_id,)),
            ])

        candidate_calls.extend([
            ("get_requirement_text", (workflow_id,)),
            ("read_requirement_text", (workflow_id,)),
            ("get_requirement", (workflow_id,)),
        ])

        for fn_name, args in candidate_calls:
            fn = getattr(requirement_store_service, fn_name, None)
            if not callable(fn):
                continue
            try:
                ret = fn(*args)
                if inspect.isawaitable(ret):
                    ret = await ret

                if isinstance(ret, str) and ret.strip():
                    return ret.strip()

                if isinstance(ret, dict):
                    for key in ("requirement_text", "pdf_text", "text", "content"):
                        value = ret.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()
            except TypeError:
                continue
            except Exception:
                logger.warning("[strategy.controller] resolve requirement text failed by %s", fn_name, exc_info=True)

    return ""


async def _append_stream_event(stream_id: str, event: Dict[str, Any]) -> None:
    event = dict(event or {})
    event.setdefault("stream_id", stream_id)

    await _call_module_func(
        strategy_stream_store,
        ["append_stream_event", "append_event", "push_event", "add_event", "emit_event"],
        stream_id,
        event,
    )

    await _call_module_func(
        strategy_ws,
        ["broadcast_event", "publish_event", "push_event", "append_event", "emit_event"],
        stream_id,
        event,
    )

    manager = _resolve_attr(
        strategy_ws,
        ["strategy_sse_manager", "strategy_ws_manager", "sse_manager", "ws_manager"],
    )
    if manager is not None:
        for method_name in ["publish", "push", "emit", "append_event", "broadcast"]:
            fn = getattr(manager, method_name, None)
            if callable(fn):
                try:
                    ret = fn(stream_id, event)
                    if inspect.isawaitable(ret):
                        await ret
                    break
                except Exception:
                    logger.warning("[strategy.controller] publish stream event failed by %s", method_name, exc_info=True)


async def _set_job_state(job_id: str, payload: Dict[str, Any]) -> None:
    await _call_module_func(
        strategy_stream_store,
        ["set_job_state", "update_job_state", "save_job_state", "set_state", "update_state"],
        job_id,
        payload,
    )


async def _get_job_state(job_id: str) -> Dict[str, Any]:
    data = await _call_module_func(
        strategy_stream_store,
        ["get_job_state", "read_job_state", "load_job_state", "get_state"],
        job_id,
    )
    return _ensure_dict(data)


async def _get_stream_result(stream_id: str) -> Dict[str, Any]:
    data = await _call_module_func(
        strategy_stream_store,
        ["get_result", "read_result", "load_result", "get_strategy_result", "read_strategy_result"],
        stream_id,
    )
    if isinstance(data, dict) and "result" in data and isinstance(data["result"], dict):
        return data["result"]
    return _ensure_dict(data)


async def _set_cancel_flag(job_id: Optional[str], stream_id: Optional[str]) -> None:
    if job_id:
        await _call_module_func(
            strategy_stream_store,
            ["set_job_cancelled", "mark_cancelled", "set_cancelled", "set_cancel_flag"],
            job_id,
        )
    if stream_id:
        await _call_module_func(
            strategy_stream_store,
            ["set_stream_cancelled", "mark_stream_cancelled", "set_cancelled", "set_cancel_flag"],
            stream_id,
        )


async def _ensure_stream(stream_id: str) -> None:
    await _call_module_func(strategy_stream_store, ["ensure_stream", "create_stream"], stream_id)

    manager = _resolve_attr(
        strategy_ws,
        ["strategy_sse_manager", "strategy_ws_manager", "sse_manager", "ws_manager"],
    )
    if manager is not None:
        for method_name in ["create_stream", "ensure_stream"]:
            fn = getattr(manager, method_name, None)
            if callable(fn):
                try:
                    ret = fn(stream_id)
                    if inspect.isawaitable(ret):
                        await ret
                    break
                except Exception:
                    logger.warning("[strategy.controller] ensure sse stream failed by %s", method_name, exc_info=True)


async def _enqueue_by_arq(payload: Dict[str, Any]) -> bool:
    if get_redis is None:
        return False

    try:
        redis = get_redis()
        if inspect.isawaitable(redis):
            redis = await redis
        if redis is None:
            return False

        enqueue_fn = getattr(redis, "enqueue_job", None)
        if callable(enqueue_fn):
            ret = enqueue_fn(ARQ_TASK_GENERATE_STRATEGY, payload, _queue_name=STRATEGY_QUEUE_NAME)
            if inspect.isawaitable(ret):
                await ret
            return True

        arq_redis = getattr(redis, "arq_redis", None) or getattr(redis, "arq", None)
        enqueue_fn = getattr(arq_redis, "enqueue_job", None) if arq_redis else None
        if callable(enqueue_fn):
            ret = enqueue_fn(ARQ_TASK_GENERATE_STRATEGY, payload, _queue_name=STRATEGY_QUEUE_NAME)
            if inspect.isawaitable(ret):
                await ret
            return True

    except Exception:
        logger.warning("[strategy.controller] enqueue by arq failed", exc_info=True)

    return False


async def _strategy_worker_local(payload: Dict[str, Any]) -> None:
    if generate_strategy is None:
        logger.error("[strategy.controller] generate_strategy not available for local fallback")
        return

    try:
        await generate_strategy(None, payload)
    except Exception:
        logger.exception("[strategy.controller] local strategy worker failed")


async def _load_context_from_workflow(
    workflow_id: str,
    requirement_id: Optional[str],
    use_analysis_result: bool,
    use_testcase_result: bool,
) -> Dict[str, Any]:
    workflow = await _get_workflow_safe(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow 不存在")

    resolved_requirement_id = requirement_id or _workflow_get(workflow, "requirement_id")
    requirement_text = await _resolve_requirement_text(
        workflow_id=workflow_id,
        workflow=workflow,
        requirement_id=resolved_requirement_id,
    )

    analysis_result = None
    testcase_result = None

    if use_analysis_result:
        analysis_result = _normalize_result_to_dict(_workflow_get(workflow, "analysis_result"))

    if use_testcase_result:
        testcase_result = _normalize_result_to_dict(_workflow_get(workflow, "testcase_result"))

    return {
        "workflow": workflow,
        "workflow_id": workflow_id,
        "requirement_id": resolved_requirement_id,
        "requirement_text": requirement_text,
        "analysis_result": analysis_result,
        "testcase_result": testcase_result,
        "has_requirement": bool(requirement_text and requirement_text.strip()),
        "has_analysis_result": bool(analysis_result),
        "has_testcase_result": bool(testcase_result),
    }


def _set_strategy_running(workflow: Any, stream_id: str, job_id: str) -> None:
    _workflow_set(workflow, "strategy_status", "running")
    _workflow_set(workflow, "strategy_stream_id", stream_id)
    _workflow_set(workflow, "strategy_job_id", job_id)
    _workflow_set(workflow, "strategy_error", None)


def _set_strategy_done(workflow: Any, result: Dict[str, Any]) -> None:
    _workflow_set(workflow, "strategy_status", "done")
    _workflow_set(workflow, "strategy_result", result)
    _workflow_set(workflow, "strategy_error", None)


def _set_strategy_error(workflow: Any, message: str) -> None:
    _workflow_set(workflow, "strategy_status", "error")
    _workflow_set(workflow, "strategy_error", message)


# =====================================================
# 对外方法
# =====================================================

async def get_strategy_context(workflow_id: str) -> Dict[str, Any]:
    ctx = await _load_context_from_workflow(
        workflow_id=workflow_id,
        requirement_id=None,
        use_analysis_result=True,
        use_testcase_result=True,
    )

    workflow = ctx["workflow"]

    return {
        "workflow_id": workflow_id,
        "requirement_id": ctx["requirement_id"],
        "has_requirement": ctx["has_requirement"],
        "has_analysis_result": ctx["has_analysis_result"],
        "has_testcase_result": ctx["has_testcase_result"],
        "strategy_status": _workflow_get(workflow, "strategy_status", "idle"),
        "strategy_stream_id": _workflow_get(workflow, "strategy_stream_id"),
        "strategy_job_id": _workflow_get(workflow, "strategy_job_id"),
        "strategy_error": _workflow_get(workflow, "strategy_error"),
        "message": "context loaded",
    }


async def get_strategy_result(workflow_id: str) -> Any:
    workflow = await _get_workflow_safe(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow 不存在")

    result = _workflow_get(workflow, "strategy_result")
    if result:
        return result

    stream_id = _workflow_get(workflow, "strategy_stream_id")
    if stream_id:
        result = await _get_stream_result(stream_id)
        if result:
            return result

    return None


async def get_strategy_status(workflow_id: str) -> Dict[str, Any]:
    workflow = await _get_workflow_safe(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow 不存在")

    job_id = _pick_first_str(_workflow_get(workflow, "strategy_job_id"))
    stream_id = _pick_first_str(_workflow_get(workflow, "strategy_stream_id"))
    workflow_status = _pick_first_str(_workflow_get(workflow, "strategy_status"), default="idle")
    workflow_error = _pick_first_str(_workflow_get(workflow, "strategy_error"), default="")

    state = {}
    if job_id:
        state = await _get_job_state(job_id)

    result = None
    if state.get("result_ready") and stream_id:
        result = await _get_stream_result(stream_id)

    return {
        "ok": True,
        "workflow_id": workflow_id,
        "job_id": job_id or None,
        "stream_id": stream_id or None,
        "status": _pick_first_str(state.get("status"), workflow_status, default="idle"),
        "progress": state.get("progress"),
        "result_ready": bool(state.get("result_ready")) if "result_ready" in state else bool(result),
        "last_stage": _pick_first_str(state.get("last_stage")) or None,
        "last_stage_status": _pick_first_str(state.get("last_stage_status")) or None,
        "last_stage_title": _pick_first_str(state.get("last_stage_title")) or None,
        "last_stage_message": _pick_first_str(state.get("last_stage_message")) or None,
        "error_type": _pick_first_str(state.get("error_type")) or None,
        "error_message": _pick_first_str(state.get("error_message"), workflow_error) or None,
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "duration_ms": state.get("duration_ms"),
        "result": result or None,
    }


async def start_strategy_run(
    workflow_id: str,
    requirement_id: Optional[str],
    force_refresh: bool = False,
    use_analysis_result: bool = True,
    use_testcase_result: bool = True,
) -> Dict[str, Any]:
    ctx = await _load_context_from_workflow(
        workflow_id=workflow_id,
        requirement_id=requirement_id,
        use_analysis_result=use_analysis_result,
        use_testcase_result=use_testcase_result,
    )

    workflow = ctx["workflow"]

    if not ctx["has_requirement"]:
        raise HTTPException(status_code=400, detail="当前 workflow 缺少需求文档内容，无法运行测试策略智能体")

    existing_result = _workflow_get(workflow, "strategy_result")
    existing_status = _workflow_get(workflow, "strategy_status", "idle")
    existing_stream_id = _workflow_get(workflow, "strategy_stream_id")
    existing_job_id = _workflow_get(workflow, "strategy_job_id")

    if existing_result and not force_refresh:
        return {
            "ok": True,
            "job_id": existing_job_id or uuid.uuid4().hex,
            "stream_id": existing_stream_id or uuid.uuid4().hex,
            "status": existing_status or "done",
            "message": "strategy result already exists",
        }

    job_id = uuid.uuid4().hex
    stream_id = uuid.uuid4().hex

    await _ensure_stream(stream_id)

    _set_strategy_running(workflow, stream_id, job_id)
    await _maybe_persist_workflow(workflow_id, workflow)

    payload = {
        "job_id": job_id,
        "stream_id": stream_id,
        "workflow_id": workflow_id,
        "requirement_id": ctx["requirement_id"],
        "requirement_text": ctx["requirement_text"],
        "analysis_result": ctx["analysis_result"],
        "testcase_result": ctx["testcase_result"],
    }

    await _set_job_state(
        job_id,
        {
            "job_id": job_id,
            "stream_id": stream_id,
            "workflow_id": workflow_id,
            "requirement_id": ctx["requirement_id"],
            "status": "queued",
            "progress": 0,
            "result_ready": False,
        },
    )

    await _append_stream_event(
        stream_id,
        {
            "type": "stage",
            "stage": "ENQUEUED",
            "status": "done",
            "title": "策略任务已入队",
            "message": "测试策略任务已创建，等待 worker 执行。",
            "progress": 0,
            "job_id": job_id,
            "workflow_id": workflow_id,
            "requirement_id": ctx["requirement_id"],
        },
    )

    enqueued = await _enqueue_by_arq(payload)
    if not enqueued:
        logger.warning("[strategy.controller] fallback to local background task, workflow_id=%s", workflow_id)
        asyncio.create_task(_strategy_worker_local(payload))
        await _append_stream_event(
            stream_id,
            {
                "type": "stage",
                "stage": "ENQUEUE_FALLBACK",
                "status": "done",
                "title": "策略任务转为本地后台执行",
                "message": "当前未使用队列，已转为本地后台执行。",
                "progress": 1,
                "job_id": job_id,
            },
        )
    else:
        await _append_stream_event(
            stream_id,
            {
                "type": "stage",
                "stage": "ENQUEUE_OK",
                "status": "done",
                "title": "策略任务入队成功",
                "message": "任务已成功提交到 worker 队列。",
                "progress": 1,
                "job_id": job_id,
            },
        )

    return {
        "ok": True,
        "job_id": job_id,
        "stream_id": stream_id,
        "status": "queued",
        "message": "strategy started",
    }


async def cancel_strategy_run(
    workflow_id: str,
    job_id: Optional[str] = None,
    stream_id: Optional[str] = None,
) -> Dict[str, Any]:
    workflow = await _get_workflow_safe(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow 不存在")

    resolved_job_id = _pick_first_str(job_id, _workflow_get(workflow, "strategy_job_id"), default="")
    resolved_stream_id = _pick_first_str(stream_id, _workflow_get(workflow, "strategy_stream_id"), default="")

    if not resolved_job_id and not resolved_stream_id:
        raise HTTPException(status_code=400, detail="未找到可取消的策略任务")

    await _set_cancel_flag(resolved_job_id or None, resolved_stream_id or None)

    if resolved_job_id:
        state = await _get_job_state(resolved_job_id)
        merged = dict(state or {})
        merged.update(
            {
                "status": "cancelling",
                "last_stage": "CANCEL_REQUESTED",
                "last_stage_status": "done",
                "last_stage_title": "已请求取消任务",
                "last_stage_message": "任务已收到取消请求，等待 worker 安全中止。",
            }
        )
        await _set_job_state(resolved_job_id, merged)

    _workflow_set(workflow, "strategy_status", "cancelling")
    await _maybe_persist_workflow(workflow_id, workflow)

    if resolved_stream_id:
        await _append_stream_event(
            resolved_stream_id,
            {
                "type": "stage",
                "stage": "CANCEL_REQUESTED",
                "status": "done",
                "title": "已请求取消策略任务",
                "message": "取消信号已写入，等待 worker 安全终止。",
                "progress": 100,
                "job_id": resolved_job_id or None,
            },
        )

    return {
        "ok": True,
        "workflow_id": workflow_id,
        "job_id": resolved_job_id or None,
        "stream_id": resolved_stream_id or None,
        "status": "cancelling",
        "message": "cancel signal sent",
    }