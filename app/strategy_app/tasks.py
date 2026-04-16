#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/tasks.py

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import time
import traceback
from typing import Any, Awaitable, Callable, Dict, Optional

from app.strategy_app.pipeline import StrategyPipeline
from app.strategy_app.exceptions import StrategyPipelineCancelled

logger = logging.getLogger(__name__)


# =====================================================
# 可选依赖导入
# =====================================================

try:
    from app.workflow.state import get_workflow
except Exception:  # pragma: no cover
    get_workflow = None  # type: ignore

try:
    import app.strategy_app.stream_store as strategy_stream_store
except Exception:  # pragma: no cover
    strategy_stream_store = None  # type: ignore

try:
    import app.strategy_app.ws as strategy_ws
except Exception:  # pragma: no cover
    strategy_ws = None  # type: ignore

try:
    import app.strategy_app.worker_settings as worker_settings
except Exception:  # pragma: no cover
    worker_settings = None  # type: ignore


# =====================================================
# 类型
# =====================================================

EmitFunc = Callable[[str, Dict[str, Any]], Awaitable[None]]


# =====================================================
# 默认配置
# =====================================================

DEFAULT_TASK_TIMEOUT_SEC = int(os.getenv("STRATEGY_TASK_TIMEOUT_SEC", "1800"))
DEFAULT_JOB_TTL_SEC = int(os.getenv("STRATEGY_JOB_TTL_SEC", "86400"))
DEFAULT_HEARTBEAT_SEC = int(os.getenv("STRATEGY_WORKER_HEARTBEAT_SEC", "5"))
DEFAULT_HEARTBEAT_ENABLED = os.getenv("STRATEGY_WORKER_HEARTBEAT_ENABLED", "true").lower() in {
    "1", "true", "yes", "on"
}
DEFAULT_PROGRESS_ENABLED = os.getenv("STRATEGY_PROGRESS_ENABLED", "true").lower() in {
    "1", "true", "yes", "on"
}


# =====================================================
# 通用工具
# =====================================================

def _now_ms() -> int:
    return int(time.time() * 1000)


def _ensure_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _pick_first_str(*values: Any, default: str = "") -> str:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


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
    return await _maybe_await(fn(*args, **kwargs))


async def _call_manager_method(manager: Any, names: list[str], *args: Any, **kwargs: Any) -> Any:
    fn = _resolve_attr(manager, names)
    if fn is None:
        return None
    return await _maybe_await(fn(*args, **kwargs))


# =====================================================
# worker settings 读取
# =====================================================

def _get_worker_setting(name: str, default: Any) -> Any:
    if worker_settings is None:
        return default
    if hasattr(worker_settings, name):
        return getattr(worker_settings, name)
    return default


def _task_timeout_sec() -> int:
    return _safe_int(
        _get_worker_setting("STRATEGY_TASK_TIMEOUT_SEC", DEFAULT_TASK_TIMEOUT_SEC),
        DEFAULT_TASK_TIMEOUT_SEC,
    )


def _job_ttl_sec() -> int:
    return _safe_int(
        _get_worker_setting("STRATEGY_JOB_TTL_SEC", DEFAULT_JOB_TTL_SEC),
        DEFAULT_JOB_TTL_SEC,
    )


def _heartbeat_sec() -> int:
    return _safe_int(
        _get_worker_setting("STRATEGY_WORKER_HEARTBEAT_SEC", DEFAULT_HEARTBEAT_SEC),
        DEFAULT_HEARTBEAT_SEC,
    )


def _heartbeat_enabled() -> bool:
    value = _get_worker_setting("STRATEGY_WORKER_HEARTBEAT_ENABLED", DEFAULT_HEARTBEAT_ENABLED)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _progress_enabled() -> bool:
    value = _get_worker_setting("STRATEGY_PROGRESS_ENABLED", DEFAULT_PROGRESS_ENABLED)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


# =====================================================
# emit / ws / store 统一出口
# =====================================================

async def _noop_emit(_: str, __: Dict[str, Any]) -> None:
    return


async def _dispatch_emit(
    emit: Optional[EmitFunc],
    stream_id: str,
    event: Dict[str, Any],
) -> None:
    if emit is None:
        return
    await emit(stream_id, event)


# =====================================================
# stream / ws 适配层
# =====================================================

class StrategyStoreAdapter:
    """
    兼容不同 stream_store / ws 实现的适配层
    """

    def __init__(
        self,
        stream_id: str,
        workflow_id: str,
        requirement_id: str,
        job_id: str,
        emit: Optional[EmitFunc] = None,
    ) -> None:
        self.stream_id = stream_id
        self.workflow_id = workflow_id
        self.requirement_id = requirement_id
        self.job_id = job_id
        self.emit = emit or _noop_emit

    async def append_event(self, event: Dict[str, Any]) -> None:
        event = dict(event or {})
        event.setdefault("stream_id", self.stream_id)
        event.setdefault("workflow_id", self.workflow_id)
        event.setdefault("requirement_id", self.requirement_id)
        event.setdefault("job_id", self.job_id)
        event.setdefault("ts", _now_ms())

        # 1) 先走直接 emit（优先给 websocket 在线连接）
        with contextlib.suppress(Exception):
            await _dispatch_emit(self.emit, self.stream_id, event)

        # 2) 落 stream_store
        await _call_module_func(
            strategy_stream_store,
            [
                "append_stream_event",
                "append_event",
                "push_event",
                "add_event",
                "emit_event",
            ],
            self.stream_id,
            event,
        )

        # 3) 兼容模块级 ws 方法
        await _call_module_func(
            strategy_ws,
            [
                "broadcast_event",
                "publish_event",
                "push_event",
                "append_event",
                "emit_event",
            ],
            self.stream_id,
            event,
        )

        # 4) 兼容 manager 单例
        manager = _resolve_attr(
            strategy_ws,
            [
                "strategy_ws_manager",
                "strategy_sse_manager",
                "ws_manager",
                "sse_manager",
            ],
        )
        if manager is not None:
            await _call_manager_method(
                manager,
                ["publish", "push", "emit", "append_event", "broadcast", "push_event"],
                self.stream_id,
                event,
            )

    async def set_status(self, status: str, extra: Optional[Dict[str, Any]] = None) -> None:
        payload = {
            "job_id": self.job_id,
            "stream_id": self.stream_id,
            "workflow_id": self.workflow_id,
            "requirement_id": self.requirement_id,
            "status": status,
            "updated_at": _now_ms(),
        }
        if extra:
            payload.update(extra)

        await _call_module_func(
            strategy_stream_store,
            [
                "set_job_state",
                "update_job_state",
                "save_job_state",
                "set_state",
                "update_state",
            ],
            self.job_id,
            payload,
            ttl_sec=_job_ttl_sec(),
        )

        # 尝试同步到 ws manager / ws module
        await _call_module_func(
            strategy_ws,
            ["set_status", "update_status"],
            self.stream_id,
            status,
            payload,
        )

        manager = _resolve_attr(
            strategy_ws,
            [
                "strategy_ws_manager",
                "strategy_sse_manager",
                "ws_manager",
                "sse_manager",
            ],
        )
        if manager is not None:
            with contextlib.suppress(Exception):
                await _call_manager_method(manager, ["set_status"], self.stream_id, status)

    async def save_result(self, result: Dict[str, Any]) -> None:
        """
        这里只存真正的最终结果 dict，不额外包一层 result。
        """
        result = dict(result or {})
        result.setdefault("job_id", self.job_id)
        result.setdefault("stream_id", self.stream_id)
        result.setdefault("workflow_id", self.workflow_id)
        result.setdefault("requirement_id", self.requirement_id)
        result.setdefault("updated_at", _now_ms())

        await _call_module_func(
            strategy_stream_store,
            [
                "save_result",
                "set_result",
                "update_result",
                "save_strategy_result",
                "set_strategy_result",
            ],
            self.stream_id,
            result,
            ttl_sec=_job_ttl_sec(),
        )

        manager = _resolve_attr(
            strategy_ws,
            [
                "strategy_ws_manager",
                "strategy_sse_manager",
                "ws_manager",
                "sse_manager",
            ],
        )
        if manager is not None:
            with contextlib.suppress(Exception):
                await _call_manager_method(manager, ["emit_result"], self.stream_id, result)

    async def set_cancelled(self) -> None:
        await _call_module_func(
            strategy_stream_store,
            [
                "set_job_cancelled",
                "mark_cancelled",
                "set_cancelled",
            ],
            self.job_id,
            ttl_sec=_job_ttl_sec(),
        )

    async def is_cancelled(self) -> bool:
        value = await _call_module_func(
            strategy_stream_store,
            [
                "is_job_cancelled",
                "get_job_cancelled",
                "job_is_cancelled",
                "is_cancelled",
                "get_cancel_flag",
            ],
            self.job_id,
        )
        if value is None:
            value = await _call_module_func(
                strategy_stream_store,
                [
                    "is_stream_cancelled",
                    "get_stream_cancelled",
                    "stream_is_cancelled",
                    "is_cancelled",
                    "get_cancel_flag",
                ],
                self.stream_id,
            )
        return bool(value)

    async def touch_heartbeat(self, extra: Optional[Dict[str, Any]] = None) -> None:
        payload = {
            "job_id": self.job_id,
            "stream_id": self.stream_id,
            "workflow_id": self.workflow_id,
            "requirement_id": self.requirement_id,
            "heartbeat_at": _now_ms(),
            "updated_at": _now_ms(),
        }
        if extra:
            payload.update(extra)

        await _call_module_func(
            strategy_stream_store,
            [
                "touch_heartbeat",
                "update_heartbeat",
                "save_heartbeat",
                "set_heartbeat",
            ],
            self.job_id,
            payload,
            ttl_sec=_job_ttl_sec(),
        )


# =====================================================
# workflow 适配层
# =====================================================

async def _update_workflow_strategy_runtime(
    workflow_id: str,
    requirement_id: str,
    patch: Dict[str, Any],
) -> None:
    if get_workflow is None or not workflow_id:
        return

    try:
        workflow = get_workflow(workflow_id)
        workflow = await _maybe_await(workflow)

        if workflow is None:
            return

        if isinstance(workflow, dict):
            strategy_state = workflow.setdefault("strategy", {})
            strategy_state.update(patch)
            return

        current = getattr(workflow, "strategy", None)
        if current is None:
            setattr(workflow, "strategy", dict(patch))
            return

        if isinstance(current, dict):
            current.update(patch)
            return

        for k, v in patch.items():
            with contextlib.suppress(Exception):
                setattr(current, k, v)

    except Exception:
        logger.warning("[strategy.tasks] update workflow runtime failed", exc_info=True)


# =====================================================
# 事件发射器
# =====================================================

def _build_event_emitter(store: StrategyStoreAdapter):
    async def _emitter(event_type: str, payload: Dict[str, Any]) -> None:
        event = dict(payload or {})
        event.setdefault("type", event_type)

        if event_type == "stage":
            stage = _pick_first_str(event.get("stage"), default="")
            status = _pick_first_str(event.get("status"), default="")
            if stage and status:
                mapped_status = "running"
                upper_stage = stage.upper()
                if upper_stage == "DONE":
                    mapped_status = "done"
                elif upper_stage == "ERROR":
                    mapped_status = "error"
                elif upper_stage == "CANCELLED":
                    mapped_status = "cancelled"

                await store.set_status(
                    status=mapped_status,
                    extra={
                        "last_stage": upper_stage,
                        "last_stage_status": status,
                        "last_stage_title": event.get("title"),
                        "last_stage_message": event.get("message"),
                        "progress": event.get("progress"),
                    },
                )

        await store.append_event(event)

    return _emitter


# =====================================================
# 心跳任务
# =====================================================

async def _worker_heartbeat_loop(
    store: StrategyStoreAdapter,
    stop_event: asyncio.Event,
) -> None:
    if not _heartbeat_enabled():
        return

    interval = max(1, _heartbeat_sec())

    while not stop_event.is_set():
        try:
            await store.touch_heartbeat({"status": "running"})
            await store.append_event(
                {
                    "type": "heartbeat",
                    "message": "worker heartbeat",
                    "interval_sec": interval,
                }
            )
        except Exception:
            logger.warning("[strategy.tasks] heartbeat failed", exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


# =====================================================
# 结果序列化
# =====================================================

def _serialize_result(result: Any) -> Dict[str, Any]:
    if result is None:
        return {}

    if isinstance(result, dict):
        return dict(result)

    model_dump = getattr(result, "model_dump", None)
    if callable(model_dump):
        with contextlib.suppress(Exception):
            data = model_dump()
            if isinstance(data, dict):
                return data

    dict_fn = getattr(result, "dict", None)
    if callable(dict_fn):
        with contextlib.suppress(Exception):
            data = dict_fn()
            if isinstance(data, dict):
                return data

    data: Dict[str, Any] = {}
    for key in dir(result):
        if key.startswith("_"):
            continue
        with contextlib.suppress(Exception):
            value = getattr(result, key)
            if callable(value):
                continue
            data[key] = value
    return data


def _patch_runtime_fields(
    result_dict: Dict[str, Any],
    *,
    job_id: str,
    stream_id: str,
    workflow_id: str,
    requirement_id: str,
    status: str,
    duration_ms: int,
) -> Dict[str, Any]:
    result_dict = dict(result_dict or {})
    result_dict["job_id"] = job_id
    result_dict["stream_id"] = stream_id
    result_dict["workflow_id"] = workflow_id
    result_dict["requirement_id"] = requirement_id
    result_dict["status"] = status
    result_dict["duration_ms"] = duration_ms
    return result_dict


# =====================================================
# 核心执行逻辑
# =====================================================

async def _run_strategy_core(
    *,
    payload: Dict[str, Any],
    emit: Optional[EmitFunc] = None,
    ctx: Any = None,
) -> Dict[str, Any]:
    started_at = time.time()
    payload = _ensure_dict(payload)

    ctx_job_id = getattr(ctx, "job_id", None) if ctx is not None else None
    job_id = _pick_first_str(
        payload.get("job_id"),
        ctx_job_id,
        default=f"strategy-{int(started_at * 1000)}",
    )
    stream_id = _pick_first_str(payload.get("stream_id"), default=job_id)
    workflow_id = _pick_first_str(payload.get("workflow_id"), default="")
    requirement_id = _pick_first_str(payload.get("requirement_id"), default="")
    requirement_text = _pick_first_str(payload.get("requirement_text"), default="")
    operator = _pick_first_str(payload.get("operator"), default="system")

    analysis_result = payload.get("analysis_result")
    testcase_result = payload.get("testcase_result")
    extra = _ensure_dict(payload.get("extra"))

    store = StrategyStoreAdapter(
        stream_id=stream_id,
        workflow_id=workflow_id,
        requirement_id=requirement_id,
        job_id=job_id,
        emit=emit,
    )
    event_emitter = _build_event_emitter(store)

    stop_event = asyncio.Event()
    heartbeat_task: Optional[asyncio.Task[Any]] = None

    async def cancel_checker() -> bool:
        return await store.is_cancelled()

    try:
        logger.info(
            "[strategy.tasks] start strategy task, job_id=%s, stream_id=%s, workflow_id=%s, requirement_id=%s",
            job_id,
            stream_id,
            workflow_id,
            requirement_id,
        )

        await store.set_status(
            "running",
            {
                "started_at": _now_ms(),
                "operator": operator,
                "workflow_id": workflow_id,
                "requirement_id": requirement_id,
                "stream_id": stream_id,
            },
        )

        await _update_workflow_strategy_runtime(
            workflow_id=workflow_id,
            requirement_id=requirement_id,
            patch={
                "job_id": job_id,
                "stream_id": stream_id,
                "status": "running",
                "started_at": _now_ms(),
            },
        )

        await store.append_event(
            {
                "type": "stage",
                "stage": "WORKER_RECEIVED",
                "status": "done",
                "title": "Worker 已接收任务",
                "message": "测试策略任务已被接收，准备开始执行。",
                "progress": 1,
            }
        )

        await store.append_event(
            {
                "type": "stage",
                "stage": "PIPELINE_START",
                "status": "start",
                "title": "开始执行策略 Pipeline",
                "message": "正在初始化测试策略执行链路…",
                "progress": 2,
            }
        )

        if extra:
            await store.append_event(
                {
                    "type": "metric",
                    "name": "task_extra",
                    "value": extra,
                }
            )

        if _heartbeat_enabled():
            heartbeat_task = asyncio.create_task(_worker_heartbeat_loop(store, stop_event))

        pipeline = StrategyPipeline(
            workflow_id=workflow_id,
            requirement_id=requirement_id,
            stream_id=stream_id,
            event_emitter=event_emitter,
            cancel_checker=cancel_checker,
        )

        timeout_sec = _task_timeout_sec()
        await store.append_event(
            {
                "type": "metric",
                "name": "task_meta",
                "value": {
                    "timeout_sec": timeout_sec,
                    "heartbeat_enabled": _heartbeat_enabled(),
                    "progress_enabled": _progress_enabled(),
                    "requirement_length": len(requirement_text or ""),
                    "has_analysis_result": bool(analysis_result),
                    "has_testcase_result": bool(testcase_result),
                },
            }
        )

        result = await asyncio.wait_for(
            pipeline.run(
                requirement_text=requirement_text,
                analysis_result=analysis_result,
                testcase_result=testcase_result,
            ),
            timeout=timeout_sec,
        )

        duration_ms = int((time.time() - started_at) * 1000)
        result_dict = _serialize_result(result)
        result_dict = _patch_runtime_fields(
            result_dict,
            job_id=job_id,
            stream_id=stream_id,
            workflow_id=workflow_id,
            requirement_id=requirement_id,
            status="done",
            duration_ms=duration_ms,
        )

        await store.save_result(result_dict)
        await store.set_status(
            "done",
            {
                "finished_at": _now_ms(),
                "duration_ms": duration_ms,
                "result_ready": True,
            },
        )

        await _update_workflow_strategy_runtime(
            workflow_id=workflow_id,
            requirement_id=requirement_id,
            patch={
                "status": "done",
                "finished_at": _now_ms(),
                "duration_ms": duration_ms,
                "result": result_dict,
            },
        )

        await store.append_event(
            {
                "type": "result",
                "message": "strategy result ready",
                "data": result_dict,
            }
        )

        await store.append_event(
            {
                "type": "stage",
                "stage": "RESULT_READY",
                "status": "done",
                "title": "策略结果已生成",
                "message": "测试策略结果已生成，可前端实时渲染展示。",
                "progress": 100,
                "duration_ms": duration_ms,
            }
        )

        await store.append_event(
            {
                "type": "stage",
                "stage": "DONE",
                "status": "done",
                "title": "策略任务完成",
                "message": "测试策略任务已全部执行完成。",
                "progress": 100,
                "duration_ms": duration_ms,
            }
        )

        logger.info(
            "[strategy.tasks] done strategy task, job_id=%s, duration_ms=%s",
            job_id,
            duration_ms,
        )

        return {
            "ok": True,
            "job_id": job_id,
            "stream_id": stream_id,
            "workflow_id": workflow_id,
            "requirement_id": requirement_id,
            "status": "done",
            "duration_ms": duration_ms,
            "result": result_dict,
        }

    except StrategyPipelineCancelled:
        logger.info("[strategy.tasks] cancelled, job_id=%s", job_id)

        duration_ms = int((time.time() - started_at) * 1000)

        await store.set_cancelled()
        await store.set_status(
            "cancelled",
            {
                "finished_at": _now_ms(),
                "duration_ms": duration_ms,
            },
        )
        await _update_workflow_strategy_runtime(
            workflow_id=workflow_id,
            requirement_id=requirement_id,
            patch={
                "status": "cancelled",
                "finished_at": _now_ms(),
                "duration_ms": duration_ms,
            },
        )

        await store.append_event(
            {
                "type": "stage",
                "stage": "CANCELLED",
                "status": "done",
                "title": "测试策略任务已取消",
                "message": "任务收到取消信号，已安全终止。",
                "progress": 100,
            }
        )

        return {
            "ok": False,
            "job_id": job_id,
            "stream_id": stream_id,
            "workflow_id": workflow_id,
            "requirement_id": requirement_id,
            "status": "cancelled",
            "duration_ms": duration_ms,
        }

    except asyncio.TimeoutError:
        logger.error("[strategy.tasks] timeout, job_id=%s", job_id)

        duration_ms = int((time.time() - started_at) * 1000)
        error_message = f"测试策略任务执行超时，超过 {_task_timeout_sec()} 秒"

        await store.set_status(
            "error",
            {
                "finished_at": _now_ms(),
                "duration_ms": duration_ms,
                "error_type": "timeout",
                "error_message": error_message,
            },
        )
        await _update_workflow_strategy_runtime(
            workflow_id=workflow_id,
            requirement_id=requirement_id,
            patch={
                "status": "error",
                "error": "strategy task timeout",
                "error_type": "timeout",
                "finished_at": _now_ms(),
            },
        )

        await store.append_event(
            {
                "type": "error",
                "message": error_message,
                "error_type": "timeout",
            }
        )
        await store.append_event(
            {
                "type": "stage",
                "stage": "ERROR",
                "status": "done",
                "title": "测试策略任务超时",
                "message": error_message,
                "progress": 100,
            }
        )

        return {
            "ok": False,
            "job_id": job_id,
            "stream_id": stream_id,
            "workflow_id": workflow_id,
            "requirement_id": requirement_id,
            "status": "error",
            "error": "strategy task timeout",
            "error_type": "timeout",
            "duration_ms": duration_ms,
        }

    except Exception as e:
        trace = traceback.format_exc()
        logger.exception("[strategy.tasks] strategy task failed, job_id=%s", job_id)

        duration_ms = int((time.time() - started_at) * 1000)

        await store.set_status(
            "error",
            {
                "finished_at": _now_ms(),
                "duration_ms": duration_ms,
                "error_type": e.__class__.__name__,
                "error_message": str(e),
            },
        )
        await _update_workflow_strategy_runtime(
            workflow_id=workflow_id,
            requirement_id=requirement_id,
            patch={
                "status": "error",
                "error": str(e),
                "error_type": e.__class__.__name__,
                "trace": trace,
                "finished_at": _now_ms(),
            },
        )

        await store.append_event(
            {
                "type": "error",
                "message": str(e),
                "error_type": e.__class__.__name__,
                "trace": trace,
            }
        )
        await store.append_event(
            {
                "type": "stage",
                "stage": "ERROR",
                "status": "done",
                "title": "测试策略任务失败",
                "message": str(e),
                "progress": 100,
            }
        )

        return {
            "ok": False,
            "job_id": job_id,
            "stream_id": stream_id,
            "workflow_id": workflow_id,
            "requirement_id": requirement_id,
            "status": "error",
            "error": str(e),
            "error_type": e.__class__.__name__,
            "trace": trace,
            "duration_ms": duration_ms,
        }

    finally:
        stop_event.set()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(Exception):
                await heartbeat_task


# =====================================================
# 对外入口 1：ARQ worker 任务入口
# =====================================================

async def generate_strategy(ctx: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    ARQ worker 任务入口
    """
    return await _run_strategy_core(payload=payload, emit=None, ctx=ctx)


# =====================================================
# 对外入口 2：WS 直接启动异步任务
# =====================================================

async def start_strategy_ws_task(
    *,
    stream_id: str,
    payload: Dict[str, Any],
    emit: Optional[EmitFunc] = None,
) -> None:
    """
    给 ws.py 调用的入口。
    websocket 收到 start_strategy 后，直接启动后台异步任务。

    用法：
        asyncio.create_task(start_strategy_ws_task(...))
    """
    payload = _ensure_dict(payload)
    payload["stream_id"] = _pick_first_str(payload.get("stream_id"), stream_id, default=stream_id)

    async def _runner() -> None:
        await _run_strategy_core(payload=payload, emit=emit, ctx=None)

    asyncio.create_task(_runner())


# =====================================================
# 可选：任务注册清单
# =====================================================

STRATEGY_TASK_FUNCTIONS = [
    generate_strategy,
]