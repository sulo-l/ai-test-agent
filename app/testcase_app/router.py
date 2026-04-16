# -*- coding: utf-8 -*-

import os
import json
import uuid
import time
import logging
import inspect
from typing import Optional, Dict, Any, Tuple

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.infra.redis_client import get_redis
from app.infra.arq_pool import get_arq_pool
from app.testcase_app import stream_store
from app.testcase_app.tasks import job_key
from app.services.file_store import get_file_path_by_id
from app.testcase_app.ws import testcase_ws_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/testcase", tags=["testcase"])

TC_QUEUE_NAME = os.getenv("TC_QUEUE_NAME", "tc_queue")
ARQ_TASK_GENERATE_TESTCASE = os.getenv(
    "TC_ARQ_TASK_GENERATE_TESTCASE",
    "app.testcase_app.tasks.generate_testcase",
)
JOB_TTL_SEC = int(os.getenv("TC_JOB_TTL_SEC", "3600"))

REQ_CACHE_PREFIX = os.getenv("TC_REQ_CACHE_PREFIX", "tc:req:")
REQ_CACHE_TTL_SEC = int(os.getenv("TC_REQ_CACHE_TTL_SEC", str(JOB_TTL_SEC)))


# =====================================================
# Request Model
# =====================================================
class TestcaseRunRequest(BaseModel):
    workflow_id: str
    requirement_id: str
    extra_requirement: Optional[str] = None
    owner: Optional[str] = None


# =====================================================
# 内部工具
# =====================================================
def _ts() -> int:
    return int(time.time())


def _ts_ms() -> int:
    return int(time.time() * 1000)


def _normalize_owner(owner: Optional[str]) -> Optional[str]:
    value = (owner or "").strip()
    return value or None


def _safe_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_bool_str(v: Any) -> bool:
    s = str(v or "").strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _try_json_loads(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s


def _req_cache_key(workflow_id: str, requirement_id: str) -> str:
    return f"{REQ_CACHE_PREFIX}{workflow_id}:{requirement_id}"


def _extract_text_from_any(v: Any) -> str:
    if v is None:
        return ""

    if isinstance(v, str):
        return v.strip()

    if isinstance(v, dict):
        for item in (
            v.get("final_text"),
            v.get("requirement_text"),
            v.get("text"),
            v.get("content"),
            v.get("clean_text"),
            v.get("body"),
            v.get("raw_text"),
        ):
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
        for item in (
            v.get("pdf_path"),
            v.get("file_path"),
            v.get("path"),
            v.get("local_path"),
        ):
            p = _safe_text(item)
            if p:
                return p

    for attr in ("pdf_path", "file_path", "path", "local_path"):
        try:
            p = _safe_text(getattr(v, attr, ""))
            if p:
                return p
        except Exception:
            continue

    return ""


def _normalize_requirement_cache_payload(
    *,
    workflow_id: str,
    requirement_id: str,
    requirement_text: str = "",
    pdf_path: str = "",
    source: str = "",
) -> Dict[str, Any]:
    return {
        "workflow_id": workflow_id,
        "requirement_id": requirement_id,
        "requirement_text": _safe_text(requirement_text),
        "pdf_path": _safe_text(pdf_path),
        "source": _safe_text(source),
        "updated_at": _ts(),
    }


async def _set_job_status(stream_id: str, status: str, extra: Optional[dict] = None) -> None:
    r = get_redis()
    payload = {
        "stream_id": stream_id,
        "status": status,
        "updated_at": str(_ts()),
    }

    if extra:
        for k, v in extra.items():
            if v is None:
                payload[k] = ""
            elif isinstance(v, (dict, list, tuple)):
                payload[k] = json.dumps(v, ensure_ascii=False)
            else:
                payload[k] = str(v)

    await r.hset(job_key(stream_id), mapping=payload)

    try:
        await r.expire(job_key(stream_id), JOB_TTL_SEC)
    except Exception:
        logger.warning("expire job key failed, stream_id=%s", stream_id, exc_info=True)


async def _decode_requirement_cache(workflow_id: str, requirement_id: str) -> Dict[str, Any]:
    r = get_redis()
    raw = await r.get(_req_cache_key(workflow_id, requirement_id))
    parsed = _try_json_loads(raw)
    return parsed if isinstance(parsed, dict) else {}


async def _write_requirement_cache(
    workflow_id: str,
    requirement_id: str,
    requirement_text: str = "",
    pdf_path: str = "",
    source: str = "",
) -> Dict[str, Any]:
    payload = _normalize_requirement_cache_payload(
        workflow_id=workflow_id,
        requirement_id=requirement_id,
        requirement_text=requirement_text,
        pdf_path=pdf_path,
        source=source,
    )
    r = get_redis()
    await r.set(
        _req_cache_key(workflow_id, requirement_id),
        json.dumps(payload, ensure_ascii=False),
        ex=REQ_CACHE_TTL_SEC,
    )
    return payload


async def _call_external_loader(
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
    return await _run_in_thread(func, workflow_id, requirement_id)


async def _run_in_thread(func, *args, **kwargs):
    import asyncio
    return await asyncio.to_thread(func, *args, **kwargs)


async def _load_from_external_sources(
    workflow_id: str,
    requirement_id: str,
) -> Tuple[str, str, str]:
    """
    返回: (requirement_text, pdf_path, source)
    这里不依赖 testcase 自己，直接尝试 workflow 侧真实来源。
    """
    candidates = [
        ("app.workflow.controller", "load_prepared_requirement"),
        ("app.workflow.controller", "get_prepared_requirement"),
        ("app.workflow.controller", "load_requirement_text"),
        ("app.workflow.controller", "get_requirement_text"),
        ("app.workflow.controller", "get_requirement_content"),
        ("app.workflow.controller", "load_requirement_content"),
        ("app.workflow.controller", "load_requirement_file"),
        ("app.workflow.controller", "get_requirement_file"),

        ("app.workflow.router", "load_prepared_requirement"),
        ("app.workflow.router", "get_prepared_requirement"),
        ("app.workflow.router", "load_requirement_text"),
        ("app.workflow.router", "get_requirement_text"),
        ("app.workflow.router", "get_requirement_content"),
        ("app.workflow.router", "load_requirement_content"),
        ("app.workflow.router", "load_requirement_file"),
        ("app.workflow.router", "get_requirement_file"),

        ("app.workflow_app.controller", "load_prepared_requirement"),
        ("app.workflow_app.controller", "get_prepared_requirement"),
        ("app.workflow_app.controller", "load_requirement_text"),
        ("app.workflow_app.controller", "get_requirement_text"),
        ("app.workflow_app.controller", "get_requirement_content"),
        ("app.workflow_app.controller", "load_requirement_content"),
        ("app.workflow_app.controller", "load_requirement_file"),
        ("app.workflow_app.controller", "get_requirement_file"),

        ("app.workflow_app.router", "load_prepared_requirement"),
        ("app.workflow_app.router", "get_prepared_requirement"),
        ("app.workflow_app.router", "load_requirement_text"),
        ("app.workflow_app.router", "get_requirement_text"),
        ("app.workflow_app.router", "get_requirement_content"),
        ("app.workflow_app.router", "load_requirement_content"),
        ("app.workflow_app.router", "load_requirement_file"),
        ("app.workflow_app.router", "get_requirement_file"),
    ]

    for module_name, func_name in candidates:
        try:
            raw = await _call_external_loader(
                module_name=module_name,
                func_name=func_name,
                workflow_id=workflow_id,
                requirement_id=requirement_id,
            )
            if raw is None:
                continue

            requirement_text = _extract_text_from_any(raw)
            pdf_path = _extract_pdf_path_from_any(raw)
            if requirement_text or pdf_path:
                source = f"{module_name}.{func_name}"
                logger.info(
                    "[router._load_from_external_sources] hit | workflow_id=%s | requirement_id=%s | source=%s | text_len=%s | has_pdf_path=%s | pdf_path=%s",
                    workflow_id,
                    requirement_id,
                    source,
                    len(requirement_text or ""),
                    bool(pdf_path),
                    pdf_path or "",
                )
                return requirement_text, pdf_path, source

        except ModuleNotFoundError:
            continue
        except Exception as e:
            logger.warning(
                "[router._load_from_external_sources] failed | workflow_id=%s | requirement_id=%s | module=%s | func=%s | err=%s",
                workflow_id,
                requirement_id,
                module_name,
                func_name,
                repr(e),
                exc_info=True,
            )

    return "", "", ""


async def _warm_requirement_cache(workflow_id: str, requirement_id: str) -> Dict[str, Any]:
    cached = await _decode_requirement_cache(workflow_id, requirement_id)
    if cached.get("requirement_text") or cached.get("pdf_path"):
        logger.info(
            "[router._warm_requirement_cache] hit cache | workflow_id=%s | requirement_id=%s | text_len=%s | has_pdf_path=%s",
            workflow_id,
            requirement_id,
            len(_safe_text(cached.get("requirement_text"))),
            bool(_safe_text(cached.get("pdf_path"))),
        )
        return cached

    requirement_text, pdf_path, source = await _load_from_external_sources(workflow_id, requirement_id)
    if requirement_text or pdf_path:
        return await _write_requirement_cache(
            workflow_id=workflow_id,
            requirement_id=requirement_id,
            requirement_text=requirement_text,
            pdf_path=pdf_path,
            source=source,
        )

    logger.warning(
        "[router._warm_requirement_cache] no requirement source found | workflow_id=%s | requirement_id=%s",
        workflow_id,
        requirement_id,
    )
    return {}


# =====================================================
# 给 tasks.py 反查用的 loader
# =====================================================
async def load_prepared_requirement(workflow_id: str, requirement_id: str) -> Dict[str, Any]:
    payload = await _warm_requirement_cache(workflow_id, requirement_id)
    return {
        "workflow_id": workflow_id,
        "requirement_id": requirement_id,
        "requirement_text": _safe_text(payload.get("requirement_text")),
        "final_text": _safe_text(payload.get("requirement_text")),
        "pdf_path": _safe_text(payload.get("pdf_path")),
        "source": _safe_text(payload.get("source")),
    }


async def get_prepared_requirement(workflow_id: str, requirement_id: str) -> Dict[str, Any]:
    return await load_prepared_requirement(workflow_id, requirement_id)


async def load_requirement_text(workflow_id: str, requirement_id: str) -> str:
    payload = await _warm_requirement_cache(workflow_id, requirement_id)
    return _safe_text(payload.get("requirement_text"))


async def get_requirement_text(workflow_id: str, requirement_id: str) -> str:
    return await load_requirement_text(workflow_id, requirement_id)


async def get_requirement_content(workflow_id: str, requirement_id: str) -> str:
    return await load_requirement_text(workflow_id, requirement_id)


async def load_requirement_content(workflow_id: str, requirement_id: str) -> str:
    return await load_requirement_text(workflow_id, requirement_id)


async def load_requirement_file(workflow_id: str, requirement_id: str) -> Dict[str, Any]:
    payload = await _warm_requirement_cache(workflow_id, requirement_id)
    return {
        "workflow_id": workflow_id,
        "requirement_id": requirement_id,
        "pdf_path": _safe_text(payload.get("pdf_path")),
        "file_path": _safe_text(payload.get("pdf_path")),
        "source": _safe_text(payload.get("source")),
    }


async def get_requirement_file(workflow_id: str, requirement_id: str) -> Dict[str, Any]:
    return await load_requirement_file(workflow_id, requirement_id)


def _decode_hgetall(data: Dict[Any, Any]) -> Dict[str, str]:
    safe: Dict[str, str] = {}
    for k, v in (data or {}).items():
        kk = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
        vv = v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
        safe[kk] = vv
    return safe


def _build_runtime_snapshot_fallback(formatted: Dict[str, Any]) -> Dict[str, Any]:
    stage = _safe_text(formatted.get("stage"))
    artifact = formatted.get("artifact") if isinstance(formatted.get("artifact"), dict) else {}
    stage_costs_ms = formatted.get("stage_costs_ms") if isinstance(formatted.get("stage_costs_ms"), dict) else {}

    return {
        "current_stage": stage or "READ_REQUIREMENT",
        "stages": [],
        "totals": {
            "test_points_total": _safe_int(formatted.get("test_point_count"), 0),
            "draft_testcases_total": _safe_int(formatted.get("draft_case_count"), 0),
            "final_testcases_total": _safe_int(formatted.get("testcase_count"), 0),
            "review_issues_total": _safe_int(formatted.get("review_note_count"), 0),
            "covered_points": _safe_int(formatted.get("covered_points"), 0),
            "uncovered_points": _safe_int(formatted.get("uncovered_points"), 0),
            "coverage_rate": _safe_float(formatted.get("coverage_rate"), 0.0),
            "total_duration_ms": _safe_int(formatted.get("total_duration_ms"), 0),
            "stage_durations": stage_costs_ms,
        },
        "artifacts": artifact,
        "current_content": None,
        "final_message": _safe_text(formatted.get("last_message")),
    }


def _format_job_status(raw: Dict[str, str]) -> Dict[str, Any]:
    if not raw:
        return {}

    out: Dict[str, Any] = dict(raw)

    int_fields = {
        "started_at",
        "updated_at",
        "finished_at",
        "last_emit_at",
        "progress_percent",
        "tp_count",
        "tc_count",
        "review_count",
        "refined_count",
        "test_point_count",
        "draft_case_count",
        "testcase_count",
        "review_note_count",
        "covered_points",
        "uncovered_points",
        "total_duration_ms",
    }
    for k in int_fields:
        if k in out:
            out[k] = _safe_int(out.get(k), 0)

    float_fields = {
        "coverage_rate",
    }
    for k in float_fields:
        if k in out:
            out[k] = _safe_float(out.get(k), 0.0)

    bool_fields = {
        "download_ready",
        "has_requirement_text",
        "has_pdf_path",
    }
    for k in bool_fields:
        if k in out:
            out[k] = _safe_bool_str(out.get(k))

    json_fields = {
        "stage_costs_ms",
        "stage_summary",
        "analysis_statistics",
        "testcase_statistics",
        "coverage_summary",
        "review_result",
        "artifact",
        "plan_result",
        "runtime_snapshot",
        "final_summary",
    }
    for k in json_fields:
        if k in out:
            out[k] = _try_json_loads(out.get(k))

    if not isinstance(out.get("runtime_snapshot"), dict) or not out.get("runtime_snapshot"):
        out["runtime_snapshot"] = _build_runtime_snapshot_fallback(out)

    if not isinstance(out.get("final_summary"), dict) or not out.get("final_summary"):
        out["final_summary"] = {
            "total_points": out.get("test_point_count", 0),
            "draft_cases": out.get("draft_case_count", 0),
            "total_cases": out.get("testcase_count", 0),
            "review_issue_count": out.get("review_note_count", 0),
            "covered_points": out.get("covered_points", 0),
            "uncovered_points": out.get("uncovered_points", 0),
            "coverage_rate": out.get("coverage_rate", 0.0),
            "total_duration_ms": out.get("total_duration_ms", 0),
            "stage_costs_ms": out.get("stage_costs_ms", {}),
        }

    return out


def _safe_filename(name: str, default: str = "testcases.xlsx") -> str:
    fn = (name or default).strip()
    if not fn:
        fn = default

    fn = fn.replace("\\", "_").replace("/", "_").replace("\x00", "_")

    if not fn.lower().endswith(".xlsx"):
        fn += ".xlsx"

    if len(fn) > 180:
        fn = fn[:180]
        if not fn.lower().endswith(".xlsx"):
            fn += ".xlsx"

    return fn


async def _emit_stage(stream_id: str, stage: str, extra: Optional[Dict[str, Any]] = None) -> None:
    try:
        emit_stage = getattr(stream_store, "emit_stage", None)
        if callable(emit_stage):
            await emit_stage(stream_id, stage, extra=extra)
            return
    except Exception:
        logger.warning("stream_store.emit_stage failed, stream_id=%s, stage=%s", stream_id, stage, exc_info=True)

    await stream_store.emit(
        stream_id,
        {
            "type": "stage",
            "data": stage,
            "extra": extra or {},
            "ts": _ts_ms(),
        },
    )


async def _emit_stage_event(
    stream_id: str,
    stage: str,
    status: str,
    title: str,
    message: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        emit_stage_event = getattr(stream_store, "emit_stage_event", None)
        if callable(emit_stage_event):
            await emit_stage_event(
                stream_id=stream_id,
                stage=stage,
                status=status,
                title=title,
                message=message,
                extra=extra,
            )
            return
    except Exception:
        logger.warning("stream_store.emit_stage_event failed, stream_id=%s", stream_id, exc_info=True)

    await stream_store.emit(
        stream_id,
        {
            "type": "stage_event",
            "data": {
                "stage": stage,
                "status": status,
                "title": title,
                "message": message,
                "extra": extra or {},
            },
            "ts": _ts_ms(),
        },
    )


async def _emit_error(stream_id: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
    try:
        emit_error = getattr(stream_store, "emit_error", None)
        if callable(emit_error):
            await emit_error(stream_id, message, extra=extra)
            return
    except Exception:
        logger.warning("stream_store.emit_error failed, stream_id=%s", stream_id, exc_info=True)

    await stream_store.emit(
        stream_id,
        {
            "type": "error",
            "data": message,
            "extra": extra or {},
            "ts": _ts_ms(),
        },
    )


# =====================================================
# 1️⃣ 启动测试用例生成（POST）
# =====================================================
@router.post("/run")
async def run_testcase(req: TestcaseRunRequest):
    workflow_id = _safe_text(req.workflow_id)
    requirement_id = _safe_text(req.requirement_id)
    extra_requirement = _safe_text(req.extra_requirement)
    owner = _normalize_owner(req.owner)

    if not workflow_id:
        raise HTTPException(status_code=400, detail="workflow_id is required")
    if not requirement_id:
        raise HTTPException(status_code=400, detail="requirement_id is required")

    stream_id = uuid.uuid4().hex
    job_id = uuid.uuid4().hex

    try:
        req_payload = await _warm_requirement_cache(workflow_id, requirement_id)
        cached_text = _safe_text(req_payload.get("requirement_text"))
        cached_pdf_path = _safe_text(req_payload.get("pdf_path"))

        await _emit_stage(
            stream_id,
            "ENQUEUED",
            extra={
                "queue": TC_QUEUE_NAME,
                "workflow_id": workflow_id,
                "requirement_id": requirement_id,
                "extra_requirement": extra_requirement,
                "owner": owner or "",
                "has_requirement_text": bool(cached_text),
                "has_pdf_path": bool(cached_pdf_path),
            },
        )
        await _emit_stage_event(
            stream_id,
            stage="enqueue",
            status="running",
            title="任务已创建",
            message="正在准备进入任务队列…",
            extra={
                "queue": TC_QUEUE_NAME,
                "workflow_id": workflow_id,
                "requirement_id": requirement_id,
                "extra_requirement": extra_requirement,
                "owner": owner or "",
                "has_requirement_text": bool(cached_text),
                "has_pdf_path": bool(cached_pdf_path),
            },
        )

        await _set_job_status(
            stream_id,
            "ENQUEUED",
            extra={
                "workflow_id": workflow_id,
                "requirement_id": requirement_id,
                "extra_requirement": extra_requirement,
                "queue": TC_QUEUE_NAME,
                "job_id": job_id,
                "task": ARQ_TASK_GENERATE_TESTCASE,
                "owner": owner or "",
                "progress_percent": 0,
                "last_message": "任务已创建，等待入队",
                "stage": "ENQUEUED",
                "has_requirement_text": "1" if cached_text else "0",
                "has_pdf_path": "1" if cached_pdf_path else "0",
                "requirement_text_len": len(cached_text or ""),
                "pdf_path": cached_pdf_path,
            },
        )

        pool = await get_arq_pool()
        await pool.enqueue_job(
            ARQ_TASK_GENERATE_TESTCASE,
            stream_id,
            workflow_id,
            requirement_id,
            extra_requirement,
            owner,
            _queue_name=TC_QUEUE_NAME,
            _job_id=job_id,
        )

        logger.info(
            "ARQ enqueue ok | queue=%s | job_id=%s | task=%s | stream_id=%s | workflow_id=%s | requirement_id=%s | extra_requirement=%s | owner=%s | has_requirement_text=%s | requirement_text_len=%s | has_pdf_path=%s | pdf_path=%s",
            TC_QUEUE_NAME,
            job_id,
            ARQ_TASK_GENERATE_TESTCASE,
            stream_id,
            workflow_id,
            requirement_id,
            extra_requirement,
            owner or "",
            bool(cached_text),
            len(cached_text or ""),
            bool(cached_pdf_path),
            cached_pdf_path,
        )

        await _emit_stage(
            stream_id,
            "ENQUEUE_OK",
            extra={
                "queue": TC_QUEUE_NAME,
                "job_id": job_id,
                "task": ARQ_TASK_GENERATE_TESTCASE,
                "workflow_id": workflow_id,
                "requirement_id": requirement_id,
                "extra_requirement": extra_requirement,
                "owner": owner or "",
                "has_requirement_text": bool(cached_text),
                "has_pdf_path": bool(cached_pdf_path),
            },
        )
        await _emit_stage_event(
            stream_id,
            stage="enqueue",
            status="completed",
            title="已成功入队",
            message="任务已进入队列，等待 worker 执行",
            extra={
                "queue": TC_QUEUE_NAME,
                "job_id": job_id,
                "task": ARQ_TASK_GENERATE_TESTCASE,
                "workflow_id": workflow_id,
                "requirement_id": requirement_id,
                "extra_requirement": extra_requirement,
                "owner": owner or "",
                "has_requirement_text": bool(cached_text),
                "has_pdf_path": bool(cached_pdf_path),
            },
        )

        await _set_job_status(
            stream_id,
            "ENQUEUE_OK",
            extra={
                "job_id": job_id,
                "extra_requirement": extra_requirement,
                "owner": owner or "",
                "progress_percent": 1,
                "last_message": "任务已成功入队，等待 worker 执行",
                "stage": "ENQUEUE_OK",
                "has_requirement_text": "1" if cached_text else "0",
                "has_pdf_path": "1" if cached_pdf_path else "0",
                "requirement_text_len": len(cached_text or ""),
                "pdf_path": cached_pdf_path,
            },
        )

        await stream_store.emit(
            stream_id,
            {
                "type": "log",
                "data": f"任务已成功入队，等待 worker 执行。job_id={job_id}",
                "extra": {
                    "queue": TC_QUEUE_NAME,
                    "job_id": job_id,
                    "task": ARQ_TASK_GENERATE_TESTCASE,
                    "workflow_id": workflow_id,
                    "requirement_id": requirement_id,
                    "extra_requirement": extra_requirement,
                    "owner": owner or "",
                    "has_requirement_text": bool(cached_text),
                    "has_pdf_path": bool(cached_pdf_path),
                },
                "ts": _ts_ms(),
            },
        )

        return {
            "ok": True,
            "stream_id": stream_id,
            "data": {
                "stream_id": stream_id,
                "job_id": job_id,
                "queue": TC_QUEUE_NAME,
                "workflow_id": workflow_id,
                "requirement_id": requirement_id,
                "extra_requirement": extra_requirement,
                "owner": owner or "",
                "status": "ENQUEUE_OK",
                "progress_percent": 1,
                "message": "任务已成功入队，等待 worker 执行",
                "has_requirement_text": bool(cached_text),
                "has_pdf_path": bool(cached_pdf_path),
            },
        }

    except Exception as e:
        logger.exception("Failed to enqueue testcase job, stream_id=%s", stream_id)

        try:
            await _emit_error(
                stream_id,
                f"enqueue_failed: {str(e)}",
                extra={
                    "where": "router.run",
                    "workflow_id": workflow_id,
                    "requirement_id": requirement_id,
                    "extra_requirement": extra_requirement,
                    "owner": owner or "",
                },
            )
            await _emit_stage(
                stream_id,
                "ERROR",
                extra={
                    "where": "router.run",
                    "workflow_id": workflow_id,
                    "requirement_id": requirement_id,
                    "extra_requirement": extra_requirement,
                    "owner": owner or "",
                },
            )
            await _emit_stage_event(
                stream_id,
                stage="enqueue",
                status="error",
                title="入队失败",
                message=str(e),
                extra={
                    "where": "router.run",
                    "workflow_id": workflow_id,
                    "requirement_id": requirement_id,
                    "extra_requirement": extra_requirement,
                    "owner": owner or "",
                },
            )
        except Exception:
            logger.warning("emit error stage failed, stream_id=%s", stream_id, exc_info=True)

        try:
            await _set_job_status(
                stream_id,
                "ERROR",
                extra={
                    "error": str(e),
                    "job_id": job_id,
                    "workflow_id": workflow_id,
                    "requirement_id": requirement_id,
                    "extra_requirement": extra_requirement,
                    "owner": owner or "",
                    "last_message": str(e),
                    "stage": "ERROR",
                },
            )
        except Exception:
            logger.warning("set job error status failed, stream_id=%s", stream_id, exc_info=True)

        raise HTTPException(status_code=500, detail=f"Failed to start testcase generation: {str(e)}")


# =====================================================
# 2️⃣ WebSocket 流式输出（WS）
# =====================================================
@router.websocket("/stream")
async def websocket_endpoint(websocket: WebSocket, stream_id: str = Query(...)):
    safe_stream_id = _safe_text(stream_id)
    if not safe_stream_id:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    try:
        async for ev_json in testcase_ws_manager.subscribe(safe_stream_id):
            await websocket.send_text(ev_json)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected, stream_id=%s", safe_stream_id)
        return
    except Exception as e:
        logger.exception("WebSocket stream error, stream_id=%s", safe_stream_id)
        try:
            await websocket.send_text(
                json.dumps(
                    {"type": "error", "data": str(e), "ts": _ts_ms()},
                    ensure_ascii=False,
                )
            )
        except Exception:
            logger.warning("websocket send error failed, stream_id=%s", safe_stream_id, exc_info=True)
        return


# =====================================================
# 3️⃣ 取消任务（POST）
# =====================================================
@router.post("/cancel")
async def cancel_testcase(stream_id: str = Query(..., description="stream_id")):
    safe_stream_id = _safe_text(stream_id)
    if not safe_stream_id:
        raise HTTPException(status_code=400, detail="stream_id is required")

    await stream_store.set_cancel(safe_stream_id, reason="user_cancelled")

    try:
        await _set_job_status(
            safe_stream_id,
            "CANCELLED",
            extra={
                "last_message": "已发送取消请求，等待任务停止",
                "stage": "CANCEL_REQUESTED",
            },
        )
        await _emit_stage(
            safe_stream_id,
            "CANCEL_REQUESTED",
            extra={"reason": "user_cancelled"},
        )
        await _emit_stage_event(
            safe_stream_id,
            stage="cancel",
            status="completed",
            title="已请求取消",
            message="已发送取消请求，等待任务停止",
            extra={"reason": "user_cancelled"},
        )
        await stream_store.emit(
            safe_stream_id,
            {
                "type": "log",
                "data": "已发送取消请求，等待任务停止。",
                "extra": {"reason": "user_cancelled"},
                "ts": _ts_ms(),
            },
        )
    except Exception:
        logger.warning("cancel testcase side effects failed, stream_id=%s", safe_stream_id, exc_info=True)

    return {"ok": True, "stream_id": safe_stream_id}


# =====================================================
# 4️⃣ 查询任务状态（GET）
# =====================================================
@router.get("/status")
async def get_testcase_status(stream_id: str = Query(..., description="stream_id")):
    safe_stream_id = _safe_text(stream_id)
    if not safe_stream_id:
        raise HTTPException(status_code=400, detail="stream_id is required")

    r = get_redis()
    raw = await r.hgetall(job_key(safe_stream_id))
    if not raw:
        return {
            "ok": True,
            "data": {
                "stream_id": safe_stream_id,
                "status": "NOT_FOUND",
            }
        }

    decoded = _decode_hgetall(raw)
    formatted = _format_job_status(decoded)

    return {
        "ok": True,
        "data": formatted,
    }


# =====================================================
# 5️⃣ 查询 stream 是否存在（GET）
# =====================================================
@router.get("/stream/status")
async def get_stream_status(stream_id: str = Query(..., description="stream_id")):
    safe_stream_id = _safe_text(stream_id)
    if not safe_stream_id:
        raise HTTPException(status_code=400, detail="stream_id is required")

    try:
        length = await stream_store.xlen(safe_stream_id)
        return {
            "ok": True,
            "stream_id": safe_stream_id,
            "exists": length > 0,
            "length": length,
            "message": "stream exists" if length > 0 else "stream not found",
        }
    except Exception as e:
        logger.exception("get stream status failed, stream_id=%s", safe_stream_id)
        return {
            "ok": False,
            "stream_id": safe_stream_id,
            "exists": False,
            "length": 0,
            "message": str(e),
        }


# =====================================================
# 6️⃣ 下载最终 Excel（GET）
# =====================================================
@router.get("/download")
async def download_testcase(
    file_id: str = Query(..., description="file_id from final event"),
    filename: str = Query("testcases.xlsx", description="download filename"),
    owner: Optional[str] = Query(None, description="testcase owner, only for tracing/debug"),
):
    safe_file_id = _safe_text(file_id)
    if not safe_file_id:
        raise HTTPException(status_code=400, detail="file_id is required")

    safe_owner = _normalize_owner(owner)
    file_path = get_file_path_by_id(safe_file_id)

    if not file_path or not os.path.exists(file_path):
        logger.warning(
            "download testcase file not found | file_id=%s | filename=%s | owner=%s | path=%s",
            safe_file_id,
            filename,
            safe_owner or "",
            file_path,
        )
        raise HTTPException(status_code=404, detail="File not found")

    safe_name = _safe_filename(filename, default="testcases.xlsx")

    logger.info(
        "download testcase file ok | file_id=%s | filename=%s | safe_name=%s | owner=%s | path=%s",
        safe_file_id,
        filename,
        safe_name,
        safe_owner or "",
        file_path,
    )

    return FileResponse(
        path=file_path,
        filename=safe_name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )