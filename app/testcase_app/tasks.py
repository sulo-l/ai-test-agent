# -*- coding: utf-8 -*-

import os
import time
import json
import traceback
import asyncio
import logging
import inspect
from typing import Any, Dict, Optional, Callable, Awaitable, Tuple

from app.infra.redis_client import get_redis
from app.testcase_app import stream_store
from app.services.requirement_store import get_requirement
from app.testcase_app.constants import (
    EVENT_ANALYSIS_RESULT,
    EVENT_DESIGN_RESULT,
    EVENT_DOWNLOAD,
    EVENT_ERROR,
    EVENT_FINAL_RESULT,
    EVENT_FINAL_SUMMARY,
    EVENT_HEARTBEAT,
    EVENT_METRIC,
    EVENT_PROGRESS,
    EVENT_REFINE_RESULT,
    EVENT_REVIEW_RESULT,
    EVENT_RUNTIME_SNAPSHOT,
    EVENT_STAGE,
    EVENT_STAGE_CONTENT,
    EVENT_STAGE_EVENT,
    EVENT_STAGE_METRIC,
    EVENT_STAGE_SNAPSHOT,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_DONE,
    JOB_STATUS_ERROR,
    JOB_STATUS_RUNNING,
)
from app.testcase_app.pipeline import run_pipeline

logger = logging.getLogger(__name__)

JOB_PREFIX = os.getenv("TC_JOB_PREFIX", "tc:job:")
JOB_TTL_SEC = int(os.getenv("TC_JOB_TTL_SEC", os.getenv("TC_STREAM_TTL_SEC", "3600")))

WORKER_HEARTBEAT_SEC = float(os.getenv("TC_WORKER_HEARTBEAT_SEC", "10.0"))
WORKER_HEARTBEAT_ENABLED = os.getenv("TC_WORKER_HEARTBEAT_ENABLED", "1") == "1"

ANALYSIS_PROGRESS_ENABLED = os.getenv("TC_ANALYSIS_PROGRESS_ENABLED", "1") == "1"
ANALYSIS_PROGRESS_SEC = float(os.getenv("TC_ANALYSIS_PROGRESS_SEC", "2.0"))

TASK_SOFT_TIMEOUT_SEC = int(os.getenv("TC_TASK_SOFT_TIMEOUT_SEC", "0"))


def job_key(stream_id: str) -> str:
    return f"{JOB_PREFIX}{stream_id}"


def _now() -> int:
    return int(time.time())


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalize_owner(owner: Optional[str]) -> Optional[str]:
    value = (owner or "").strip()
    return value or None


def _safe_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _safe_json_dumps(v: Any) -> str:
    try:
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        try:
            return json.dumps(str(v), ensure_ascii=False)
        except Exception:
            return ""


def _safe_json_loads(v: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    text = str(v).strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _extract_text_from_any(v: Any) -> str:
    if v is None:
        return ""

    if isinstance(v, str):
        return v.strip()

    if isinstance(v, dict):
        candidates = [
            v.get("final_text"),
            v.get("requirement_text"),
            v.get("text"),
            v.get("content"),
            v.get("clean_text"),
            v.get("body"),
            v.get("raw_text"),
        ]
        for item in candidates:
            text = _safe_text(item)
            if text:
                return text

    for attr in (
        "final_text",
        "requirement_text",
        "text",
        "content",
        "clean_text",
        "body",
        "raw_text",
    ):
        try:
            text = _safe_text(getattr(v, attr, ""))
            if text:
                return text
        except Exception:
            continue

    return ""


def _extract_pdf_path_from_any(v: Any) -> str:
    if v is None:
        return ""

    if isinstance(v, dict):
        candidates = [
            v.get("pdf_path"),
            v.get("file_path"),
            v.get("path"),
            v.get("local_path"),
        ]
        for item in candidates:
            p = _safe_text(item)
            if p and os.path.exists(p):
                return p

    for attr in ("pdf_path", "file_path", "path", "local_path"):
        try:
            p = _safe_text(getattr(v, attr, ""))
            if p and os.path.exists(p):
                return p
        except Exception:
            continue

    return ""


async def _call_candidate_loader(
    module_name: str,
    func_name: str,
    workflow_id: str,
    requirement_id: str,
) -> Any:
    module = __import__(module_name, fromlist=[func_name])
    func = getattr(module, func_name, None)
    if func is None:
        return None

    if inspect.iscoroutinefunction(func):
        return await func(workflow_id, requirement_id)

    return await asyncio.to_thread(func, workflow_id, requirement_id)


# ─── Requirement loaders ──────────────────────────────────────────────────────
# These are discovered dynamically by _try_requirement_text_loaders and
# _try_pdf_path_loaders (probing "app.testcase_app.tasks.*").

async def load_requirement_text(workflow_id: str, requirement_id: str) -> str:
    """Read pre-parsed requirement text from requirement_store (Redis)."""
    try:
        data = await get_requirement(workflow_id, requirement_id)
        if data and isinstance(data, dict):
            return (data.get("requirement_text") or "").strip()
    except Exception:
        logger.warning(
            "[load_requirement_text] failed | workflow_id=%s | requirement_id=%s",
            workflow_id, requirement_id, exc_info=True,
        )
    return ""


async def load_requirement_file(workflow_id: str, requirement_id: str) -> str:
    """Read pdf_path from requirement_store (Redis)."""
    try:
        data = await get_requirement(workflow_id, requirement_id)
        if data and isinstance(data, dict):
            return (data.get("pdf_path") or "").strip()
    except Exception:
        logger.warning(
            "[load_requirement_file] failed | workflow_id=%s | requirement_id=%s",
            workflow_id, requirement_id, exc_info=True,
        )
    return ""


async def _try_requirement_text_loaders(
    workflow_id: str,
    requirement_id: str,
) -> str:
    candidates = [
        ("app.testcase_app.tasks", "load_requirement_text"),
        ("app.testcase_app.tasks", "get_requirement_text"),
        ("app.testcase_app.tasks", "get_requirement_content"),
        ("app.testcase_app.tasks", "load_requirement_content"),
        ("app.testcase_app.router", "load_requirement_text"),
        ("app.testcase_app.router", "get_requirement_text"),
        ("app.testcase_app.router", "get_requirement_content"),
        ("app.testcase_app.router", "load_requirement_content"),
        ("app.testcase_app.controller", "load_requirement_text"),
        ("app.testcase_app.controller", "get_requirement_text"),
        ("app.testcase_app.controller", "get_requirement_content"),
        ("app.testcase_app.controller", "load_requirement_content"),
        ("app.workflow_app.router", "load_requirement_text"),
        ("app.workflow_app.router", "get_requirement_text"),
        ("app.workflow_app.controller", "load_requirement_text"),
        ("app.workflow_app.controller", "get_requirement_text"),
        ("app.workflow.router", "load_requirement_text"),
        ("app.workflow.router", "get_requirement_text"),
        ("app.workflow.controller", "load_requirement_text"),
        ("app.workflow.controller", "get_requirement_text"),
    ]

    for module_name, func_name in candidates:
        try:
            raw = await _call_candidate_loader(
                module_name=module_name,
                func_name=func_name,
                workflow_id=workflow_id,
                requirement_id=requirement_id,
            )
            text = _extract_text_from_any(raw)
            if text:
                logger.info(
                    "[_try_requirement_text_loaders] hit | module=%s | func=%s | workflow_id=%s | requirement_id=%s | text_len=%s",
                    module_name,
                    func_name,
                    workflow_id,
                    requirement_id,
                    len(text),
                )
                return text
        except ModuleNotFoundError:
            continue
        except Exception as e:
            logger.warning(
                "[_try_requirement_text_loaders] failed | module=%s | func=%s | workflow_id=%s | requirement_id=%s | err=%s",
                module_name,
                func_name,
                workflow_id,
                requirement_id,
                repr(e),
                exc_info=True,
            )

    return ""


async def _try_pdf_path_loaders(
    workflow_id: str,
    requirement_id: str,
) -> str:
    candidates = [
        ("app.testcase_app.tasks", "load_prepared_requirement"),
        ("app.testcase_app.tasks", "get_prepared_requirement"),
        ("app.testcase_app.tasks", "load_requirement_file"),
        ("app.testcase_app.tasks", "get_requirement_file"),
        ("app.testcase_app.router", "load_prepared_requirement"),
        ("app.testcase_app.router", "get_prepared_requirement"),
        ("app.testcase_app.router", "load_requirement_file"),
        ("app.testcase_app.router", "get_requirement_file"),
        ("app.testcase_app.controller", "load_prepared_requirement"),
        ("app.testcase_app.controller", "get_prepared_requirement"),
        ("app.testcase_app.controller", "load_requirement_file"),
        ("app.testcase_app.controller", "get_requirement_file"),
        ("app.workflow_app.router", "load_prepared_requirement"),
        ("app.workflow_app.router", "get_prepared_requirement"),
        ("app.workflow_app.router", "load_requirement_file"),
        ("app.workflow_app.router", "get_requirement_file"),
        ("app.workflow_app.controller", "load_prepared_requirement"),
        ("app.workflow_app.controller", "get_prepared_requirement"),
        ("app.workflow_app.controller", "load_requirement_file"),
        ("app.workflow_app.controller", "get_requirement_file"),
        ("app.workflow.router", "load_prepared_requirement"),
        ("app.workflow.router", "get_prepared_requirement"),
        ("app.workflow.router", "load_requirement_file"),
        ("app.workflow.router", "get_requirement_file"),
        ("app.workflow.controller", "load_prepared_requirement"),
        ("app.workflow.controller", "get_prepared_requirement"),
        ("app.workflow.controller", "load_requirement_file"),
        ("app.workflow.controller", "get_requirement_file"),
    ]

    for module_name, func_name in candidates:
        try:
            raw = await _call_candidate_loader(
                module_name=module_name,
                func_name=func_name,
                workflow_id=workflow_id,
                requirement_id=requirement_id,
            )
            pdf_path = _extract_pdf_path_from_any(raw)
            if pdf_path:
                logger.info(
                    "[_try_pdf_path_loaders] hit | module=%s | func=%s | workflow_id=%s | requirement_id=%s | pdf_path=%s",
                    module_name,
                    func_name,
                    workflow_id,
                    requirement_id,
                    pdf_path,
                )
                return pdf_path
        except ModuleNotFoundError:
            continue
        except Exception as e:
            logger.warning(
                "[_try_pdf_path_loaders] failed | module=%s | func=%s | workflow_id=%s | requirement_id=%s | err=%s",
                module_name,
                func_name,
                workflow_id,
                requirement_id,
                repr(e),
                exc_info=True,
            )

    return ""


async def _resolve_requirement_inputs(
    workflow_id: str,
    requirement_id: str,
) -> Tuple[str, str]:
    """
    返回: (requirement_text, pdf_path)
    优先拿 requirement_text，拿不到再尝试 pdf_path。
    """
    requirement_text = await _try_requirement_text_loaders(workflow_id, requirement_id)
    pdf_path = await _try_pdf_path_loaders(workflow_id, requirement_id)

    logger.info(
        "[_resolve_requirement_inputs] resolved | workflow_id=%s | requirement_id=%s | text_len=%s | has_pdf_path=%s | pdf_path=%s",
        workflow_id,
        requirement_id,
        len(requirement_text or ""),
        bool(pdf_path),
        pdf_path or "",
    )

    return requirement_text, pdf_path


async def _job_set(stream_id: str, fields: Dict[str, Any]) -> None:
    r = get_redis()
    safe_fields: Dict[str, str] = {}

    for k, v in (fields or {}).items():
        if v is None:
            safe_fields[k] = ""
        elif isinstance(v, (dict, list, tuple)):
            safe_fields[k] = _safe_json_dumps(v)
        else:
            safe_fields[k] = str(v)

    await r.hset(job_key(stream_id), mapping=safe_fields)
    await r.expire(job_key(stream_id), JOB_TTL_SEC)


async def _job_mark_start(
    stream_id: str,
    workflow_id: str,
    requirement_id: str,
    job_id: Optional[str] = None,
    owner: Optional[str] = None,
    extra_requirement: Optional[str] = None,
) -> None:
    now = _now()
    await _job_set(
        stream_id,
        {
            "stream_id": stream_id,
            "workflow_id": workflow_id,
            "requirement_id": requirement_id,
            "extra_requirement": _safe_text(extra_requirement),
            "owner": owner or "",
            "status": JOB_STATUS_RUNNING,
            "stage": "WORKER_RECEIVED",
            "job_id": job_id or "",
            "started_at": now,
            "updated_at": now,
            "last_emit_at": now,
            "progress_percent": 0,

            "tp_count": 0,
            "tc_count": 0,
            "review_count": 0,
            "refined_count": 0,
            "test_point_count": 0,
            "draft_case_count": 0,
            "testcase_count": 0,
            "review_note_count": 0,

            "covered_points": 0,
            "uncovered_points": 0,
            "coverage_rate": 0,

            "total_duration_ms": 0,
            "stage_costs_ms": "{}",
            "analysis_statistics": "{}",
            "testcase_statistics": "{}",
            "coverage_summary": "{}",
            "review_result": "{}",
            "runtime_snapshot": "{}",
            "artifact": "{}",

            "last_type": "",
            "last_message": "",
            "last_stage_title": "",
            "last_stage_status": "",
        },
    )


async def _job_mark_stage(stream_id: str, stage: str) -> None:
    await _job_set(
        stream_id,
        {
            "stage": stage,
            "updated_at": _now(),
        },
    )


async def _job_mark_emit(stream_id: str) -> None:
    await _job_set(stream_id, {"last_emit_at": _now(), "updated_at": _now()})


async def _job_mark_cancelled(
    stream_id: str,
    *,
    extra_requirement: Optional[str] = None,
    owner: Optional[str] = None,
) -> None:
    await _job_set(
        stream_id,
        {
            "status": JOB_STATUS_CANCELLED,
            "extra_requirement": _safe_text(extra_requirement),
            "owner": owner or "",
            "finished_at": _now(),
            "updated_at": _now(),
        },
    )


async def _job_mark_error(
    stream_id: str,
    err: str,
    *,
    extra_requirement: Optional[str] = None,
    owner: Optional[str] = None,
) -> None:
    await _job_set(
        stream_id,
        {
            "status": JOB_STATUS_ERROR,
            "error": (err or "")[:4000],
            "extra_requirement": _safe_text(extra_requirement),
            "owner": owner or "",
            "finished_at": _now(),
            "updated_at": _now(),
        },
    )


async def _job_mark_done(stream_id: str, extra: Optional[Dict[str, Any]] = None) -> None:
    payload: Dict[str, Any] = {
        "status": JOB_STATUS_DONE,
        "finished_at": _now(),
        "updated_at": _now(),
        "progress_percent": 100,
    }
    if extra:
        payload.update(extra)
    await _job_set(stream_id, payload)


async def _emit(stream_id: str, event: Dict[str, Any]) -> None:
    if isinstance(event, dict) and "ts" not in event:
        event = {**event, "ts": _now_ms()}

    try:
        await stream_store.emit(stream_id, event)
        await _job_mark_emit(stream_id)
    except Exception:
        logger.exception("stream_store.emit failed (ignored) | stream_id=%s", stream_id)

    try:
        et = event.get("type")
        if et == EVENT_STAGE:
            stage = event.get("data", "")
            if stage:
                await _job_mark_stage(stream_id, str(stage))
        elif et in {EVENT_STAGE_EVENT, EVENT_STAGE_SNAPSHOT}:
            data = event.get("data") or {}
            if isinstance(data, dict):
                stage = str(data.get("stage") or data.get("key") or "").strip()
                if stage:
                    await _job_mark_stage(stream_id, stage)
    except Exception:
        logger.exception("job_mark_stage failed (ignored) | stream_id=%s", stream_id)


def _make_cancel_checker(stream_id: str) -> Callable[[], Awaitable[bool]]:
    async def _checker() -> bool:
        return await stream_store.is_cancelled(stream_id)
    return _checker


def _extract_job_id(ctx: Dict[str, Any]) -> str:
    try:
        if not ctx:
            return ""
        if "job_id" in ctx and ctx["job_id"]:
            return str(ctx["job_id"])
        job = ctx.get("job")
        if job is not None:
            jid = getattr(job, "job_id", None) or getattr(job, "id", None)
            if jid:
                return str(jid)
    except Exception:
        pass
    return ""


async def _worker_heartbeat_loop(
    stream_id: str,
    stop_event: asyncio.Event,
    get_stage: Callable[[], str],
    get_metrics: Callable[[], Dict[str, Any]],
) -> None:
    if not WORKER_HEARTBEAT_ENABLED:
        return

    last_sent_at = 0.0

    while not stop_event.is_set():
        now = time.time()
        if (now - last_sent_at) >= WORKER_HEARTBEAT_SEC:
            try:
                stage = get_stage() or ""
            except Exception:
                stage = ""

            try:
                metrics = get_metrics() or {}
            except Exception:
                metrics = {}

            try:
                await _emit(
                    stream_id,
                    {
                        "type": EVENT_HEARTBEAT,
                        "data": {
                            "alive": True,
                            "stage": stage,
                            **metrics,
                        },
                    },
                )
                last_sent_at = now
            except Exception:
                pass

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.0)
            break
        except asyncio.TimeoutError:
            continue


async def _analysis_progress_loop(
    stream_id: str,
    stop_event: asyncio.Event,
    get_stage: Callable[[], str],
    get_metrics: Callable[[], Dict[str, Any]],
) -> None:
    if not ANALYSIS_PROGRESS_ENABLED:
        return

    last_sent = 0.0

    def _is_analysis_stage(stage: str) -> bool:
        s = (stage or "").upper()
        return s in {"ANALYZE_REQUIREMENT", "ANALYZE_TEST_POINTS", "ANALYSIS"} or s.startswith("ANALYSIS_")

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=0.5)
            break
        except asyncio.TimeoutError:
            pass

        if stop_event.is_set():
            break

        try:
            stage = get_stage() or ""
        except Exception:
            stage = ""

        if not _is_analysis_stage(stage):
            continue

        now = time.time()
        if (now - last_sent) < ANALYSIS_PROGRESS_SEC:
            continue

        try:
            extra = get_metrics() or {}
        except Exception:
            extra = {}

        try:
            await _emit(
                stream_id,
                {
                    "type": EVENT_PROGRESS,
                    "data": {
                        "stage": stage,
                        "message": "ANALYSIS_TICK",
                        "percent": extra.get("progress_percent", 0),
                        **extra,
                    },
                },
            )
            last_sent = now
        except Exception:
            pass


async def _soft_timeout_watchdog(
    stream_id: str,
    stop_event: asyncio.Event,
    started_at: float,
    seconds: int,
    get_stage: Callable[[], str],
    extra_requirement: Optional[str] = None,
    owner: Optional[str] = None,
) -> None:
    if seconds <= 0:
        return

    try:
        while not stop_event.is_set():
            await asyncio.sleep(1.0)
            if stop_event.is_set():
                break

            if (time.time() - started_at) > seconds:
                try:
                    stage = get_stage() or ""
                except Exception:
                    stage = ""

                err = f"Task soft-timeout > {seconds}s (stage={stage})"

                try:
                    await _emit(stream_id, {"type": EVENT_ERROR, "data": {"message": err}})
                    await _emit(stream_id, {"type": EVENT_STAGE, "data": "ERROR"})
                    await _job_mark_error(
                        stream_id,
                        err,
                        extra_requirement=extra_requirement,
                        owner=owner,
                    )
                except Exception:
                    pass
                return
    except Exception:
        return


async def generate_testcase(
    ctx: Dict[str, Any],
    stream_id: str,
    workflow_id: str,
    requirement_id: str,
    extra_requirement: Optional[str] = None,
    owner: Optional[str] = None,
) -> None:
    started = time.time()
    started_ms = _now_ms()
    cancel_checker = _make_cancel_checker(stream_id)
    job_id = _extract_job_id(ctx)
    owner = _normalize_owner(owner)
    extra_requirement = _safe_text(extra_requirement)

    stage_holder = {"stage": "WORKER_RECEIVED"}

    metrics_holder: Dict[str, Any] = {
        "tp_count": 0,
        "tc_count": 0,
        "review_count": 0,
        "refined_count": 0,
        "test_point_count": 0,
        "draft_case_count": 0,
        "testcase_count": 0,
        "review_note_count": 0,

        "covered_points": 0,
        "uncovered_points": 0,
        "coverage_rate": 0.0,

        "progress_percent": 0,
        "last_type": "",
        "last_message": "",
        "last_stage_title": "",
        "last_stage_status": "",

        "stage_costs_ms": {},
        "analysis_statistics": {},
        "testcase_statistics": {},
        "coverage_summary": {},
        "review_result": {},
        "runtime_snapshot": {},
        "artifact": {},
        "total_duration_ms": 0,

        "extra_requirement": extra_requirement,
        "owner": owner or "",
    }

    final_payload_holder: Dict[str, Any] = {}

    def _get_stage() -> str:
        return stage_holder.get("stage", "")

    def _get_metrics() -> Dict[str, Any]:
        return {
            "tp_count": metrics_holder.get("tp_count", 0),
            "tc_count": metrics_holder.get("tc_count", 0),
            "review_count": metrics_holder.get("review_count", 0),
            "refined_count": metrics_holder.get("refined_count", 0),
            "test_point_count": metrics_holder.get("test_point_count", 0),
            "draft_case_count": metrics_holder.get("draft_case_count", 0),
            "testcase_count": metrics_holder.get("testcase_count", 0),
            "review_note_count": metrics_holder.get("review_note_count", 0),
            "covered_points": metrics_holder.get("covered_points", 0),
            "uncovered_points": metrics_holder.get("uncovered_points", 0),
            "coverage_rate": metrics_holder.get("coverage_rate", 0),
            "progress_percent": metrics_holder.get("progress_percent", 0),
            "last_type": metrics_holder.get("last_type", ""),
            "last_message": metrics_holder.get("last_message", ""),
            "last_stage_title": metrics_holder.get("last_stage_title", ""),
            "last_stage_status": metrics_holder.get("last_stage_status", ""),
            "extra_requirement": metrics_holder.get("extra_requirement", ""),
            "owner": owner or "",
        }

    stop_bg = asyncio.Event()
    hb_task: Optional[asyncio.Task] = None
    prog_task: Optional[asyncio.Task] = None
    to_task: Optional[asyncio.Task] = None

    logger.info(
        "[generate_testcase] ENTER | job_id=%s | stream_id=%s | workflow_id=%s | requirement_id=%s | extra_requirement_len=%s | owner=%s",
        job_id,
        stream_id,
        workflow_id,
        requirement_id,
        len(extra_requirement),
        owner or "",
    )

    async def _sync_job_runtime_snapshot() -> None:
        try:
            await _job_set(
                stream_id,
                {
                    "owner": owner or "",
                    "extra_requirement": extra_requirement,
                    "stage": stage_holder.get("stage", ""),
                    "tp_count": metrics_holder.get("tp_count", 0),
                    "tc_count": metrics_holder.get("tc_count", 0),
                    "review_count": metrics_holder.get("review_count", 0),
                    "refined_count": metrics_holder.get("refined_count", 0),
                    "test_point_count": metrics_holder.get("test_point_count", 0),
                    "draft_case_count": metrics_holder.get("draft_case_count", 0),
                    "testcase_count": metrics_holder.get("testcase_count", 0),
                    "review_note_count": metrics_holder.get("review_note_count", 0),
                    "covered_points": metrics_holder.get("covered_points", 0),
                    "uncovered_points": metrics_holder.get("uncovered_points", 0),
                    "coverage_rate": metrics_holder.get("coverage_rate", 0),
                    "progress_percent": metrics_holder.get("progress_percent", 0),
                    "last_type": metrics_holder.get("last_type", ""),
                    "last_message": metrics_holder.get("last_message", ""),
                    "last_stage_title": metrics_holder.get("last_stage_title", ""),
                    "last_stage_status": metrics_holder.get("last_stage_status", ""),
                    "stage_costs_ms": metrics_holder.get("stage_costs_ms", {}),
                    "analysis_statistics": metrics_holder.get("analysis_statistics", {}),
                    "testcase_statistics": metrics_holder.get("testcase_statistics", {}),
                    "coverage_summary": metrics_holder.get("coverage_summary", {}),
                    "review_result": metrics_holder.get("review_result", {}),
                    "runtime_snapshot": metrics_holder.get("runtime_snapshot", {}),
                    "artifact": metrics_holder.get("artifact", {}),
                    "total_duration_ms": metrics_holder.get("total_duration_ms", 0),
                    "updated_at": _now(),
                },
            )
        except Exception:
            logger.exception("sync job runtime snapshot failed | stream_id=%s", stream_id)

    async def emit_with_tracking(sid: str, event: Dict[str, Any]) -> None:
        try:
            if isinstance(event, dict):
                et = str(event.get("type") or "")
                metrics_holder["last_type"] = et

                if et == EVENT_STAGE:
                    s = str(event.get("data") or "").strip()
                    if s:
                        stage_holder["stage"] = s

                elif et == EVENT_STAGE_EVENT:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        stage = str(data.get("stage") or "").strip()
                        if stage:
                            stage_holder["stage"] = stage

                        message = str(data.get("message") or "").strip()
                        title = str(data.get("title") or "").strip()
                        status = str(data.get("status") or "").strip()
                        metrics_holder["last_message"] = message or title
                        metrics_holder["last_stage_title"] = title
                        metrics_holder["last_stage_status"] = status

                        progress = data.get("progress")
                        if isinstance(progress, (int, float)):
                            metrics_holder["progress_percent"] = max(0, min(100, int(progress)))

                elif et == EVENT_STAGE_SNAPSHOT:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        stage = str(data.get("key") or data.get("stage") or "").strip()
                        if stage:
                            stage_holder["stage"] = stage

                        metrics_holder["last_stage_title"] = str(data.get("title") or "").strip()
                        metrics_holder["last_stage_status"] = str(data.get("status") or "").strip()
                        metrics_holder["last_message"] = str(
                            data.get("summary") or data.get("message") or ""
                        ).strip()

                        progress = data.get("progress")
                        if isinstance(progress, (int, float)):
                            metrics_holder["progress_percent"] = max(0, min(100, int(progress)))

                elif et == EVENT_STAGE_CONTENT:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        title = str(data.get("title") or "").strip()
                        if title:
                            metrics_holder["last_message"] = title

                elif et == EVENT_PROGRESS:
                    data = event.get("data") or {}
                    extra = event.get("extra") or {}
                    if isinstance(data, dict):
                        percent = data.get("percent")
                        if isinstance(percent, (int, float)):
                            metrics_holder["progress_percent"] = max(0, min(100, int(percent)))
                        msg = str(data.get("message") or "").strip()
                        if msg:
                            metrics_holder["last_message"] = msg
                        stage = str(data.get("stage") or "").strip()
                        if stage:
                            stage_holder["stage"] = stage
                    elif isinstance(extra, dict):
                        percent = extra.get("percent")
                        if isinstance(percent, (int, float)):
                            metrics_holder["progress_percent"] = max(0, min(100, int(percent)))
                        msg = str(extra.get("message") or "").strip()
                        if msg:
                            metrics_holder["last_message"] = msg

                elif et == EVENT_ANALYSIS_RESULT:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        total_points = data.get("total_points")
                        if isinstance(total_points, int):
                            metrics_holder["tp_count"] = total_points
                            metrics_holder["test_point_count"] = total_points

                        statistics = data.get("statistics")
                        if isinstance(statistics, dict):
                            metrics_holder["analysis_statistics"] = statistics

                        summary = str(data.get("summary") or "").strip()
                        if summary:
                            metrics_holder["last_message"] = summary

                elif et == EVENT_DESIGN_RESULT:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        total_cases = data.get("total_cases")
                        if isinstance(total_cases, int):
                            metrics_holder["tc_count"] = total_cases
                            metrics_holder["draft_case_count"] = total_cases

                        statistics = data.get("statistics")
                        if isinstance(statistics, dict):
                            metrics_holder["testcase_statistics"] = statistics

                elif et == EVENT_REVIEW_RESULT:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        issue_count = data.get("issue_count")
                        if isinstance(issue_count, int):
                            metrics_holder["review_count"] = issue_count
                            metrics_holder["review_note_count"] = issue_count
                        metrics_holder["review_result"] = data

                elif et == EVENT_REFINE_RESULT:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        total_cases = data.get("total_cases")
                        if isinstance(total_cases, int):
                            metrics_holder["refined_count"] = total_cases
                            metrics_holder["testcase_count"] = total_cases

                        statistics = data.get("statistics")
                        if isinstance(statistics, dict):
                            metrics_holder["testcase_statistics"] = statistics

                        coverage_summary = data.get("coverage_summary")
                        if isinstance(coverage_summary, dict):
                            metrics_holder["coverage_summary"] = coverage_summary
                            metrics_holder["covered_points"] = _safe_int(
                                coverage_summary.get("covered_points"), 0
                            )
                            metrics_holder["uncovered_points"] = _safe_int(
                                coverage_summary.get("uncovered_points"), 0
                            )
                            metrics_holder["coverage_rate"] = _safe_float(
                                coverage_summary.get("coverage_rate"), 0.0
                            )

                elif et == EVENT_METRIC:
                    d = event.get("data") or {}
                    if isinstance(d, dict):
                        st = str(d.get("stage") or "").upper()
                        duration_ms = d.get("duration_ms")
                        output_count = d.get("output_count")

                        if isinstance(duration_ms, (int, float)) and st:
                            sc = dict(metrics_holder.get("stage_costs_ms", {}) or {})
                            sc[st] = int(duration_ms)
                            metrics_holder["stage_costs_ms"] = sc

                        if isinstance(output_count, int):
                            if st == "ANALYZE_TEST_POINTS":
                                metrics_holder["tp_count"] = output_count
                                metrics_holder["test_point_count"] = output_count
                            elif st == "DESIGN_TESTCASES":
                                metrics_holder["tc_count"] = output_count
                                metrics_holder["draft_case_count"] = output_count
                            elif st == "REVIEW_TESTCASES":
                                metrics_holder["review_count"] = output_count
                                metrics_holder["review_note_count"] = output_count
                            elif st == "REFINE_TESTCASES":
                                metrics_holder["refined_count"] = output_count
                                metrics_holder["testcase_count"] = output_count

                elif et == EVENT_STAGE_METRIC:
                    d = event.get("data") or {}
                    if isinstance(d, dict):
                        st = str(d.get("stage") or "").upper()
                        duration_ms = d.get("duration_ms")
                        if isinstance(duration_ms, (int, float)) and st:
                            sc = dict(metrics_holder.get("stage_costs_ms", {}) or {})
                            sc[st] = int(duration_ms)
                            metrics_holder["stage_costs_ms"] = sc

                elif et == EVENT_DOWNLOAD:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        metrics_holder["artifact"] = data

                elif et == EVENT_RUNTIME_SNAPSHOT:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        metrics_holder["runtime_snapshot"] = data
                        totals = data.get("totals") or {}
                        artifacts = data.get("artifacts") or {}
                        if isinstance(totals, dict):
                            metrics_holder["test_point_count"] = _safe_int(
                                totals.get("test_points_total"),
                                metrics_holder.get("test_point_count", 0),
                            )
                            metrics_holder["draft_case_count"] = _safe_int(
                                totals.get("draft_testcases_total"),
                                metrics_holder.get("draft_case_count", 0),
                            )
                            metrics_holder["testcase_count"] = _safe_int(
                                totals.get("final_testcases_total"),
                                metrics_holder.get("testcase_count", 0),
                            )
                            metrics_holder["review_note_count"] = _safe_int(
                                totals.get("review_issues_total"),
                                metrics_holder.get("review_note_count", 0),
                            )
                            metrics_holder["covered_points"] = _safe_int(
                                totals.get("covered_points"),
                                metrics_holder.get("covered_points", 0),
                            )
                            metrics_holder["uncovered_points"] = _safe_int(
                                totals.get("uncovered_points"),
                                metrics_holder.get("uncovered_points", 0),
                            )
                            metrics_holder["coverage_rate"] = _safe_float(
                                totals.get("coverage_rate"),
                                metrics_holder.get("coverage_rate", 0.0),
                            )
                            metrics_holder["total_duration_ms"] = _safe_int(
                                totals.get("total_duration_ms"),
                                metrics_holder.get("total_duration_ms", 0),
                            )
                            if isinstance(totals.get("stage_durations"), dict):
                                metrics_holder["stage_costs_ms"] = totals.get("stage_durations") or {}

                        if isinstance(artifacts, dict):
                            merged_artifact = dict(metrics_holder.get("artifact", {}) or {})
                            merged_artifact.update(artifacts)
                            metrics_holder["artifact"] = merged_artifact

                elif et == EVENT_FINAL_SUMMARY:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        metrics_holder["test_point_count"] = _safe_int(
                            data.get("total_points"),
                            metrics_holder.get("test_point_count", 0),
                        )
                        metrics_holder["draft_case_count"] = _safe_int(
                            data.get("draft_cases"),
                            metrics_holder.get("draft_case_count", 0),
                        )
                        metrics_holder["testcase_count"] = _safe_int(
                            data.get("total_cases"),
                            metrics_holder.get("testcase_count", 0),
                        )
                        metrics_holder["review_note_count"] = _safe_int(
                            data.get("review_issue_count"),
                            metrics_holder.get("review_note_count", 0),
                        )
                        metrics_holder["covered_points"] = _safe_int(
                            data.get("covered_points"),
                            metrics_holder.get("covered_points", 0),
                        )
                        metrics_holder["uncovered_points"] = _safe_int(
                            data.get("uncovered_points"),
                            metrics_holder.get("uncovered_points", 0),
                        )
                        metrics_holder["coverage_rate"] = _safe_float(
                            data.get("coverage_rate"),
                            metrics_holder.get("coverage_rate", 0.0),
                        )
                        metrics_holder["total_duration_ms"] = _safe_int(
                            data.get("total_duration_ms"),
                            metrics_holder.get("total_duration_ms", 0),
                        )
                        if isinstance(data.get("stage_costs_ms"), dict):
                            metrics_holder["stage_costs_ms"] = data.get("stage_costs_ms") or {}
                        if isinstance(data.get("artifact"), dict):
                            metrics_holder["artifact"] = data.get("artifact") or {}
                        if isinstance(data.get("coverage_summary"), dict):
                            metrics_holder["coverage_summary"] = data.get("coverage_summary") or {}

                elif et == EVENT_FINAL_RESULT:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        final_payload_holder.clear()
                        final_payload_holder.update(data)

                        metrics_holder["test_point_count"] = _safe_int(
                            data.get("total_points"),
                            metrics_holder.get("test_point_count", 0),
                        )
                        metrics_holder["draft_case_count"] = _safe_int(
                            data.get("draft_cases"),
                            metrics_holder.get("draft_case_count", 0),
                        )
                        metrics_holder["testcase_count"] = _safe_int(
                            data.get("total_cases"),
                            metrics_holder.get("testcase_count", 0),
                        )
                        metrics_holder["review_note_count"] = _safe_int(
                            data.get("review_issue_count"),
                            metrics_holder.get("review_note_count", 0),
                        )
                        metrics_holder["covered_points"] = _safe_int(
                            data.get("covered_points"),
                            metrics_holder.get("covered_points", 0),
                        )
                        metrics_holder["uncovered_points"] = _safe_int(
                            data.get("uncovered_points"),
                            metrics_holder.get("uncovered_points", 0),
                        )
                        metrics_holder["coverage_rate"] = _safe_float(
                            data.get("coverage_rate"),
                            metrics_holder.get("coverage_rate", 0.0),
                        )
                        metrics_holder["total_duration_ms"] = _safe_int(
                            data.get("total_duration_ms"),
                            metrics_holder.get("total_duration_ms", 0),
                        )

                        if isinstance(data.get("stage_costs_ms"), dict):
                            metrics_holder["stage_costs_ms"] = data.get("stage_costs_ms") or {}
                        if isinstance(data.get("artifact"), dict):
                            metrics_holder["artifact"] = data.get("artifact") or {}
                        if isinstance(data.get("runtime_snapshot"), dict):
                            metrics_holder["runtime_snapshot"] = data.get("runtime_snapshot") or {}
                        if isinstance(data.get("analysis_result"), dict):
                            metrics_holder["analysis_statistics"] = data.get("analysis_result", {}).get("statistics", {}) or {}
                        if isinstance(data.get("refine_result"), dict):
                            metrics_holder["testcase_statistics"] = data.get("refine_result", {}).get("statistics", {}) or {}
                            metrics_holder["coverage_summary"] = data.get("refine_result", {}).get("coverage_summary", {}) or {}
                        if isinstance(data.get("review_result"), dict):
                            metrics_holder["review_result"] = data.get("review_result") or {}

                        metrics_holder["progress_percent"] = 100
                        metrics_holder["last_message"] = "pipeline finished"

                if not metrics_holder.get("total_duration_ms"):
                    metrics_holder["total_duration_ms"] = max(0, _now_ms() - started_ms)

        except Exception:
            logger.exception("emit_with_tracking parse event failed | stream_id=%s", sid)

        await _emit(sid, event)
        await _sync_job_runtime_snapshot()

    try:
        await emit_with_tracking(stream_id, {"type": EVENT_STAGE, "data": "WORKER_RECEIVED"})
        await _job_mark_start(
            stream_id,
            workflow_id,
            requirement_id,
            job_id=job_id,
            owner=owner,
            extra_requirement=extra_requirement,
        )

        hb_task = asyncio.create_task(_worker_heartbeat_loop(stream_id, stop_bg, _get_stage, _get_metrics))
        prog_task = asyncio.create_task(_analysis_progress_loop(stream_id, stop_bg, _get_stage, _get_metrics))

        if TASK_SOFT_TIMEOUT_SEC > 0:
            to_task = asyncio.create_task(
                _soft_timeout_watchdog(
                    stream_id,
                    stop_bg,
                    started,
                    TASK_SOFT_TIMEOUT_SEC,
                    _get_stage,
                    extra_requirement=extra_requirement,
                    owner=owner,
                )
            )

        if await cancel_checker():
            stage_holder["stage"] = "CANCELLED_BEFORE_START"
            await emit_with_tracking(stream_id, {"type": EVENT_STAGE, "data": "CANCELLED_BEFORE_START"})
            await _job_mark_cancelled(
                stream_id,
                extra_requirement=extra_requirement,
                owner=owner,
            )
            return

        stage_holder["stage"] = "PIPELINE_START"
        await emit_with_tracking(stream_id, {"type": EVENT_STAGE, "data": "PIPELINE_START"})

        requirement_text, pdf_path = await _resolve_requirement_inputs(
            workflow_id=workflow_id,
            requirement_id=requirement_id,
        )

        logger.info(
            "[generate_testcase] before run_pipeline | stream_id=%s | workflow_id=%s | requirement_id=%s | extra_requirement_len=%s | owner=%s | requirement_text_len=%s | has_pdf_path=%s | pdf_path=%s",
            stream_id,
            workflow_id,
            requirement_id,
            len(extra_requirement),
            owner or "",
            len(requirement_text or ""),
            bool(pdf_path),
            pdf_path or "",
        )

        await run_pipeline(
            stream_id=stream_id,
            workflow_id=workflow_id,
            requirement_id=requirement_id,
            extra_requirement=extra_requirement,
            emit=emit_with_tracking,
            cancel_checker=cancel_checker,
            owner=owner,
            requirement_text=requirement_text or None,
            pdf_path=pdf_path or None,
        )

        logger.info(
            "[generate_testcase] after run_pipeline | stream_id=%s | final_payload_keys=%s",
            stream_id,
            list(final_payload_holder.keys()),
        )

        artifact = metrics_holder.get("artifact", {}) if isinstance(metrics_holder.get("artifact"), dict) else {}
        analysis_statistics = metrics_holder.get("analysis_statistics", {})
        testcase_statistics = metrics_holder.get("testcase_statistics", {})
        coverage_summary = metrics_holder.get("coverage_summary", {})
        review_result = metrics_holder.get("review_result", {})
        runtime_snapshot = metrics_holder.get("runtime_snapshot", {})
        total_duration_ms = metrics_holder.get("total_duration_ms", 0)
        stage_costs_ms = metrics_holder.get("stage_costs_ms", {})

        await _job_mark_done(
            stream_id,
            extra={
                "owner": owner or "",
                "extra_requirement": extra_requirement,
                "file_id": artifact.get("file_id", ""),
                "filename": artifact.get("filename", ""),
                "download_url": artifact.get("download_url", ""),
                "download_ready": "1" if artifact.get("ready") else "0",
                "export_error": artifact.get("error", ""),

                "test_point_count": metrics_holder.get("test_point_count", 0),
                "draft_case_count": metrics_holder.get("draft_case_count", 0),
                "testcase_count": metrics_holder.get("testcase_count", 0),
                "review_note_count": metrics_holder.get("review_note_count", 0),

                "tp_count": metrics_holder.get("tp_count", 0),
                "tc_count": metrics_holder.get("tc_count", 0),
                "review_count": metrics_holder.get("review_count", 0),
                "refined_count": metrics_holder.get("refined_count", 0),

                "covered_points": metrics_holder.get("covered_points", 0),
                "uncovered_points": metrics_holder.get("uncovered_points", 0),
                "coverage_rate": metrics_holder.get("coverage_rate", 0),

                "last_type": metrics_holder.get("last_type", ""),
                "last_message": metrics_holder.get("last_message", ""),
                "last_stage_title": metrics_holder.get("last_stage_title", ""),
                "last_stage_status": metrics_holder.get("last_stage_status", ""),
                "progress_percent": 100,

                "total_duration_ms": total_duration_ms,
                "stage_costs_ms": stage_costs_ms,
                "analysis_statistics": analysis_statistics,
                "testcase_statistics": testcase_statistics,
                "coverage_summary": coverage_summary,
                "review_result": review_result,
                "runtime_snapshot": runtime_snapshot,
                "artifact": artifact,
            },
        )

    except asyncio.CancelledError:
        stage_holder["stage"] = "CANCELLED"
        try:
            await emit_with_tracking(stream_id, {"type": EVENT_STAGE, "data": "CANCELLED"})
            await _job_mark_cancelled(
                stream_id,
                extra_requirement=extra_requirement,
                owner=owner,
            )
        except Exception:
            pass
        raise

    except Exception as e:
        tb = traceback.format_exc()
        logger.error("[generate_testcase] ERROR | stream_id=%s | exc=%r\n%s", stream_id, e, tb)
        try:
            stage_holder["stage"] = "ERROR"
            await emit_with_tracking(
                stream_id,
                {
                    "type": EVENT_ERROR,
                    "data": {
                        "message": str(e),
                        "traceback": tb[:2000],
                    },
                },
            )
            await emit_with_tracking(stream_id, {"type": EVENT_STAGE, "data": "ERROR"})
            await _job_mark_error(
                stream_id,
                tb,
                extra_requirement=extra_requirement,
                owner=owner,
            )
        except Exception:
            pass
        return

    finally:
        try:
            stop_bg.set()
            tasks = []
            if hb_task:
                tasks.append(hb_task)
            if prog_task:
                tasks.append(prog_task)
            if to_task:
                tasks.append(to_task)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            pass


# =========================
# Optional: requirement analysis only (A branch)
# =========================
async def analyze_requirement(
    ctx: Dict[str, Any],
    stream_id: str,
    workflow_id: str,
    requirement_id: str,
    owner: Optional[str] = None,
) -> None:
    cancel_checker = _make_cancel_checker(stream_id)
    job_id = _extract_job_id(ctx)
    started = time.time()
    owner = _normalize_owner(owner)

    stage_holder = {"stage": "WORKER_RECEIVED"}
    metrics_holder: Dict[str, Any] = {
        "last_type": "",
        "last_message": "",
        "last_stage_title": "",
        "last_stage_status": "",
        "progress_percent": 0,
        "runtime_snapshot": {},
        "owner": owner or "",
    }

    def _get_stage() -> str:
        return stage_holder.get("stage", "")

    def _get_metrics() -> Dict[str, Any]:
        return {
            "last_type": metrics_holder.get("last_type", ""),
            "last_message": metrics_holder.get("last_message", ""),
            "last_stage_title": metrics_holder.get("last_stage_title", ""),
            "last_stage_status": metrics_holder.get("last_stage_status", ""),
            "progress_percent": metrics_holder.get("progress_percent", 0),
            "owner": owner or "",
        }

    stop_bg = asyncio.Event()
    hb_task: Optional[asyncio.Task] = None
    prog_task: Optional[asyncio.Task] = None
    to_task: Optional[asyncio.Task] = None

    logger.info(
        "[analyze_requirement] ENTER | job_id=%s | stream_id=%s | workflow_id=%s | requirement_id=%s | owner=%s",
        job_id,
        stream_id,
        workflow_id,
        requirement_id,
        owner or "",
    )

    async def _sync_job_runtime_snapshot() -> None:
        try:
            await _job_set(
                stream_id,
                {
                    "owner": owner or "",
                    "stage": stage_holder.get("stage", ""),
                    "last_type": metrics_holder.get("last_type", ""),
                    "last_message": metrics_holder.get("last_message", ""),
                    "last_stage_title": metrics_holder.get("last_stage_title", ""),
                    "last_stage_status": metrics_holder.get("last_stage_status", ""),
                    "progress_percent": metrics_holder.get("progress_percent", 0),
                    "runtime_snapshot": metrics_holder.get("runtime_snapshot", {}),
                    "updated_at": _now(),
                },
            )
        except Exception:
            logger.exception("sync analysis job runtime snapshot failed | stream_id=%s", stream_id)

    async def emit_with_stage(sid: str, event: Dict[str, Any]) -> None:
        try:
            if isinstance(event, dict):
                et = str(event.get("type") or "")
                metrics_holder["last_type"] = et

                if et == EVENT_STAGE:
                    s = str(event.get("data") or "").strip()
                    if s:
                        stage_holder["stage"] = s

                elif et == EVENT_STAGE_EVENT:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        stage = str(data.get("stage") or "").strip()
                        if stage:
                            stage_holder["stage"] = stage

                        message = str(data.get("message") or "").strip()
                        title = str(data.get("title") or "").strip()
                        status = str(data.get("status") or "").strip()

                        metrics_holder["last_message"] = message or title
                        metrics_holder["last_stage_title"] = title
                        metrics_holder["last_stage_status"] = status

                elif et == EVENT_STAGE_SNAPSHOT:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        stage = str(data.get("key") or data.get("stage") or "").strip()
                        if stage:
                            stage_holder["stage"] = stage
                        metrics_holder["last_message"] = str(
                            data.get("summary") or data.get("message") or ""
                        ).strip()
                        metrics_holder["last_stage_title"] = str(data.get("title") or "").strip()
                        metrics_holder["last_stage_status"] = str(data.get("status") or "").strip()

                        progress = data.get("progress")
                        if isinstance(progress, (int, float)):
                            metrics_holder["progress_percent"] = max(0, min(100, int(progress)))

                elif et == EVENT_RUNTIME_SNAPSHOT:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        metrics_holder["runtime_snapshot"] = data
                        totals = data.get("totals") or {}
                        if isinstance(totals, dict):
                            percent = totals.get("progress_percent")
                            if isinstance(percent, (int, float)):
                                metrics_holder["progress_percent"] = max(0, min(100, int(percent)))

                elif et == EVENT_PROGRESS:
                    data = event.get("data") or {}
                    if isinstance(data, dict):
                        percent = data.get("percent")
                        if isinstance(percent, (int, float)):
                            metrics_holder["progress_percent"] = max(0, min(100, int(percent)))
                        msg = str(data.get("message") or "").strip()
                        if msg:
                            metrics_holder["last_message"] = msg
        except Exception:
            logger.exception("emit_with_stage parse event failed | stream_id=%s", sid)

        await _emit(sid, event)
        await _sync_job_runtime_snapshot()

    try:
        await emit_with_stage(stream_id, {"type": EVENT_STAGE, "data": "WORKER_RECEIVED"})
        await _job_mark_start(
            stream_id,
            workflow_id,
            requirement_id,
            job_id=job_id,
            owner=owner,
            extra_requirement=None,
        )

        hb_task = asyncio.create_task(_worker_heartbeat_loop(stream_id, stop_bg, _get_stage, _get_metrics))
        prog_task = asyncio.create_task(_analysis_progress_loop(stream_id, stop_bg, _get_stage, _get_metrics))

        if TASK_SOFT_TIMEOUT_SEC > 0:
            to_task = asyncio.create_task(
                _soft_timeout_watchdog(
                    stream_id,
                    stop_bg,
                    started,
                    TASK_SOFT_TIMEOUT_SEC,
                    _get_stage,
                    extra_requirement=None,
                    owner=owner,
                )
            )

        if await cancel_checker():
            stage_holder["stage"] = "CANCELLED_BEFORE_START"
            await emit_with_stage(stream_id, {"type": EVENT_STAGE, "data": "CANCELLED_BEFORE_START"})
            await _job_mark_cancelled(stream_id, extra_requirement=None, owner=owner)
            return

        stage_holder["stage"] = "ANALYSIS_PIPELINE_START"
        await emit_with_stage(stream_id, {"type": EVENT_STAGE, "data": "ANALYSIS_PIPELINE_START"})

        try:
            from app.analysis_app.pipeline import run_analysis_pipeline  # type: ignore
        except Exception as e:
            err = f"analysis_app not available: {e!r}"
            stage_holder["stage"] = "ERROR"
            await emit_with_stage(stream_id, {"type": EVENT_ERROR, "data": {"message": err}})
            await emit_with_stage(stream_id, {"type": EVENT_STAGE, "data": "ERROR"})
            await _job_mark_error(stream_id, err, extra_requirement=None, owner=owner)
            return

        await run_analysis_pipeline(
            stream_id=stream_id,
            workflow_id=workflow_id,
            requirement_id=requirement_id,
            emit=emit_with_stage,
            cancel_checker=cancel_checker,
        )

        stage_holder["stage"] = "ANALYSIS_PIPELINE_DONE"
        await emit_with_stage(stream_id, {"type": EVENT_STAGE, "data": "ANALYSIS_PIPELINE_DONE"})
        await _job_mark_done(
            stream_id,
            extra={
                "owner": owner or "",
                "last_type": metrics_holder.get("last_type", ""),
                "last_message": metrics_holder.get("last_message", ""),
                "last_stage_title": metrics_holder.get("last_stage_title", ""),
                "last_stage_status": metrics_holder.get("last_stage_status", ""),
                "progress_percent": 100,
                "runtime_snapshot": metrics_holder.get("runtime_snapshot", {}),
            },
        )

    except asyncio.CancelledError:
        try:
            stage_holder["stage"] = "CANCELLED"
            await emit_with_stage(stream_id, {"type": EVENT_STAGE, "data": "CANCELLED"})
            await _job_mark_cancelled(stream_id, extra_requirement=None, owner=owner)
        except Exception:
            pass
        raise

    except Exception as e:
        tb = traceback.format_exc()
        logger.error("[analyze_requirement] ERROR | stream_id=%s | exc=%r\n%s", stream_id, e, tb)
        try:
            stage_holder["stage"] = "ERROR"
            await emit_with_stage(
                stream_id,
                {
                    "type": EVENT_ERROR,
                    "data": {
                        "message": str(e),
                        "traceback": tb[:2000],
                    },
                },
            )
            await emit_with_stage(stream_id, {"type": EVENT_STAGE, "data": "ERROR"})
            await _job_mark_error(stream_id, tb, extra_requirement=None, owner=owner)
        except Exception:
            pass
        return

    finally:
        try:
            stop_bg.set()
            tasks = []
            if hb_task:
                tasks.append(hb_task)
            if prog_task:
                tasks.append(prog_task)
            if to_task:
                tasks.append(to_task)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            pass