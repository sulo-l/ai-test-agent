#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/2/8 21:56
# @Author: sulo
# @Desc: Analysis Controller (Pipeline V4 Compact Result)

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import traceback
import logging
from typing import Any, Dict, Optional, Tuple

from app.analysis_app.pipeline import RequirementAnalysisPipeline
from app.analysis_app.sse import analysis_sse_manager
from app.analysis_app.worker_settings import (
    ANALYSIS_CONCURRENCY,
    ANALYSIS_CACHE_TTL_SEC,
    ANALYSIS_CACHE_KEY_PREFIX,
    ANALYSIS_LLM_TIMEOUT_SEC,
)
from app.infra.redis_client import get_redis
from app.workflow.state import get_workflow

logger = logging.getLogger(__name__)


# =====================================================
# 配置
# =====================================================

# 最大并发分析任务数（防止同时打太多 LLM）
_ANALYSIS_CONCURRENCY = max(1, int(ANALYSIS_CONCURRENCY))

# 单次分析超时（秒）
# pipeline 内部已有 agent 级 timeout，这里保留 controller 级总超时
_ANALYSIS_TIMEOUT_SEC = max(120, int(ANALYSIS_LLM_TIMEOUT_SEC) * 4)

# 缓存 TTL
CACHE_TTL = max(60, int(ANALYSIS_CACHE_TTL_SEC))

# 缓存版本：分析逻辑大改后改这里，让旧缓存失效
CACHE_VERSION = "v4_compact"

# Redis client
redis_client = get_redis()


# =====================================================
# 全局并发控制
# =====================================================

analysis_semaphore = asyncio.Semaphore(_ANALYSIS_CONCURRENCY)

# 用于“同一份需求文本 + requirement_id”去重，避免同一时刻重复跑相同分析
_inflight_lock = asyncio.Lock()
_inflight_tasks: Dict[str, asyncio.Future] = {}


# =====================================================
# 基础工具
# =====================================================

def _task_get(task: Any, key: str, default: Any = None) -> Any:
    if task is None:
        return default

    if isinstance(task, dict):
        return task.get(key, default)

    return getattr(task, key, default)


def _task_set(task: Any, key: str, value: Any) -> None:
    if task is None:
        return

    if isinstance(task, dict):
        task[key] = value
        return

    setattr(task, key, value)


def _safe_json_dumps(value: Any) -> str:
    """
    尽量把任意结果安全序列化为 JSON 字符串。
    兼容 dict / list / pydantic / 普通对象
    """
    def _default(obj: Any):
        # pydantic v2
        if hasattr(obj, "model_dump"):
            try:
                return obj.model_dump()
            except Exception:
                pass

        # pydantic v1
        if hasattr(obj, "dict"):
            try:
                return obj.dict()
            except Exception:
                pass

        # 普通对象
        if hasattr(obj, "__dict__"):
            try:
                return obj.__dict__
            except Exception:
                pass

        return str(obj)

    return json.dumps(value, ensure_ascii=False, default=_default)


def _normalize_result(result: Any) -> Dict[str, Any]:
    """
    把 pipeline 返回值统一规整成 dict，避免缓存/前端消费报错。
    """
    if result is None:
        return {}

    if isinstance(result, dict):
        return result

    # pydantic v2
    if hasattr(result, "model_dump"):
        try:
            data = result.model_dump()
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    # pydantic v1
    if hasattr(result, "dict"):
        try:
            data = result.dict()
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return {
        "overview": {
            "summary": str(result),
            "score": 0,
            "qualityLevel": "poor",
            "decision": "fail",
            "passed": False,
            "issueCount": 0,
            "highCount": 0,
            "mediumCount": 0,
            "lowCount": 0,
            "criticalCount": 0,
            "durationMs": 0,
        },
        "qualityGate": {
            "passed": False,
            "decision": "fail",
            "reasons": ["分析结果结构异常"],
            "blocker_issue_ids": [],
            "critical_issue_ids": [],
        },
        "topIssues": [],
        "issues": [],
        "statistics": {
            "totalIssues": 0,
            "highCount": 0,
            "mediumCount": 0,
            "lowCount": 0,
            "blockerCount": 0,
            "criticalCount": 0,
            "majorCount": 0,
            "minorCount": 0,
            "suggestionCount": 0,
            "byCategory": {},
            "byDimension": {},
        },
        "panels": {},
        "recommendations": [],
        "meta": {},
        "raw_result": str(result),
    }


def _make_publish(stream_id: str):
    async def _publish(event: Dict[str, Any]) -> None:
        await analysis_sse_manager.publish(stream_id, event)
    return _publish


def _ensure_result_shape(
    result: Dict[str, Any],
    *,
    workflow_id: str,
    requirement_id: str,
    cache_hit: bool,
    reused_inflight: bool,
    duration_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    给 pipeline 结果补充 controller 维度的统一元信息。
    """
    data = _normalize_result(result)

    overview = data.get("overview")
    if not isinstance(overview, dict):
        overview = {}
        data["overview"] = overview

    meta = data.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        data["meta"] = meta

    if duration_ms is not None:
        overview["durationMs"] = duration_ms
        meta["durationMs"] = duration_ms

    meta["workflowId"] = workflow_id
    meta["requirementId"] = requirement_id
    meta["cacheHit"] = bool(cache_hit)
    meta["reusedInflight"] = bool(reused_inflight)
    meta["cacheVersion"] = CACHE_VERSION

    data["workflowId"] = workflow_id
    data["requirementId"] = requirement_id
    data["cacheHit"] = bool(cache_hit)
    data["reusedInflight"] = bool(reused_inflight)

    # 保底字段
    data.setdefault("qualityGate", {
        "passed": False,
        "decision": "fail",
        "reasons": [],
        "blocker_issue_ids": [],
        "critical_issue_ids": [],
    })
    data.setdefault("topIssues", [])
    data.setdefault("issues", [])
    data.setdefault("statistics", {
        "totalIssues": 0,
        "highCount": 0,
        "mediumCount": 0,
        "lowCount": 0,
        "blockerCount": 0,
        "criticalCount": 0,
        "majorCount": 0,
        "minorCount": 0,
        "suggestionCount": 0,
        "byCategory": {},
        "byDimension": {},
    })
    data.setdefault("panels", {})
    data.setdefault("recommendations", [])

    return data


# =====================================================
# 缓存
# =====================================================

def _make_cache_key(
    requirement_text: str,
    requirement_id: Optional[str] = None,
) -> str:
    """
    缓存 key 包含：
    - cache version
    - requirement_id（有则带上）
    - text hash
    """
    text_md5 = hashlib.md5(requirement_text.encode("utf-8")).hexdigest()
    rid = (requirement_id or "no_requirement_id").strip() or "no_requirement_id"
    return f"{ANALYSIS_CACHE_KEY_PREFIX}{CACHE_VERSION}:{rid}:{text_md5}"


async def _get_cache(
    requirement_text: str,
    requirement_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    try:
        key = _make_cache_key(requirement_text, requirement_id)
        data = await redis_client.get(key)
        if not data:
            return None

        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="ignore")

        parsed = json.loads(data)
        if isinstance(parsed, dict):
            return parsed
        return None
    except Exception:
        logger.exception("analysis cache read failed")
        return None


async def _set_cache(
    requirement_text: str,
    result: Dict[str, Any],
    requirement_id: Optional[str] = None,
) -> None:
    try:
        key = _make_cache_key(requirement_text, requirement_id)
        payload = _safe_json_dumps(result)
        await redis_client.set(key, payload, ex=CACHE_TTL)
    except Exception:
        logger.exception("analysis cache write failed")


# =====================================================
# 同 key 请求去重
# =====================================================

async def _get_or_create_inflight_future(cache_key: str) -> Tuple[asyncio.Future, bool]:
    """
    返回 (future, is_owner)
    - is_owner=True: 当前请求负责真正执行分析
    - is_owner=False: 当前请求等待已有分析结果
    """
    async with _inflight_lock:
        existing = _inflight_tasks.get(cache_key)
        if existing is not None and not existing.done():
            return existing, False

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        _inflight_tasks[cache_key] = fut
        return fut, True


async def _clear_inflight_future(cache_key: str) -> None:
    async with _inflight_lock:
        _inflight_tasks.pop(cache_key, None)


# =====================================================
# SSE 输出
# =====================================================

async def _publish_stage(
    stream_id: str,
    stage: str,
    message: str,
    **extra: Any,
) -> None:
    payload = {
        "type": "stage",
        "stage": stage,
        "message": message,
    }
    if extra:
        payload.update(extra)
    await analysis_sse_manager.publish(stream_id, payload)


async def _publish_analysis(
    stream_id: str,
    value: Dict[str, Any],
) -> None:
    await analysis_sse_manager.publish(
        stream_id,
        {
            "type": "analysis",
            "value": value,
        },
    )


async def _publish_done(stream_id: str) -> None:
    await analysis_sse_manager.publish(stream_id, {"type": "done"})


async def _publish_error(
    stream_id: str,
    exc: Exception,
    trace: Optional[str] = None,
) -> None:
    await analysis_sse_manager.publish(
        stream_id,
        {
            "type": "error",
            "message": str(exc),
            "trace": trace or traceback.format_exc(),
        },
    )


# =====================================================
# Router 入口
# =====================================================

async def start_requirement_analysis(
    stream_id: str,
    workflow_id: str,
    requirement_id: str,
):
    """
    入口函数：
    - 立即返回
    - 真正分析放后台 task
    """
    asyncio.create_task(
        _analysis_worker(
            stream_id=stream_id,
            workflow_id=workflow_id,
            requirement_id=requirement_id,
        )
    )


# =====================================================
# Worker
# =====================================================

async def _analysis_worker(
    stream_id: str,
    workflow_id: str,
    requirement_id: str,
):
    task: Optional[Any] = None
    cache_key: Optional[str] = None
    inflight_future: Optional[asyncio.Future] = None
    is_owner: bool = False
    started_at = time.perf_counter()

    try:
        # =====================================================
        # 获取 workflow / requirement_text
        # =====================================================
        task = get_workflow(workflow_id)
        if not task:
            raise RuntimeError(f"workflow not found: {workflow_id}")

        requirement_text = (_task_get(task, "pdf_text", "") or "").strip()
        if not requirement_text:
            raise RuntimeError("requirement text empty")

        # =====================================================
        # 更新 workflow 状态
        # =====================================================
        _task_set(task, "analysis_status", "running")
        _task_set(task, "analysis_error", None)
        _task_set(task, "analysis_requirement_id", requirement_id)
        _task_set(task, "analysis_started_at", int(time.time() * 1000))

        await _publish_stage(
            stream_id,
            "CACHE_CHECK",
            "正在检查分析缓存",
        )

        # =====================================================
        # 先查缓存
        # =====================================================
        cached = await _get_cache(
            requirement_text=requirement_text,
            requirement_id=requirement_id,
        )
        if cached is not None:
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            cached = _ensure_result_shape(
                cached,
                workflow_id=workflow_id,
                requirement_id=requirement_id,
                cache_hit=True,
                reused_inflight=False,
                duration_ms=duration_ms,
            )

            _task_set(task, "analysis_result", cached)
            _task_set(task, "analysis_status", "done")
            _task_set(task, "analysis_error", None)
            _task_set(task, "analysis_duration_ms", duration_ms)

            await _publish_stage(
                stream_id,
                "CACHE_HIT",
                "命中缓存，直接返回分析结果",
                cache_hit=True,
                duration_ms=duration_ms,
            )
            await _publish_analysis(stream_id, cached)
            await _publish_done(stream_id)
            return

        # =====================================================
        # 同 key 请求去重
        # =====================================================
        cache_key = _make_cache_key(
            requirement_text=requirement_text,
            requirement_id=requirement_id,
        )

        inflight_future, is_owner = await _get_or_create_inflight_future(cache_key)

        if not is_owner:
            await _publish_stage(
                stream_id,
                "WAIT_INFLIGHT",
                "相同需求正在分析中，等待已有任务结果",
            )

            try:
                shared_result = await inflight_future
            except Exception as e:
                raise RuntimeError(f"shared inflight analysis failed: {e}") from e

            duration_ms = int((time.perf_counter() - started_at) * 1000)
            shared_result = _ensure_result_shape(
                shared_result,
                workflow_id=workflow_id,
                requirement_id=requirement_id,
                cache_hit=False,
                reused_inflight=True,
                duration_ms=duration_ms,
            )

            _task_set(task, "analysis_result", shared_result)
            _task_set(task, "analysis_status", "done")
            _task_set(task, "analysis_error", None)
            _task_set(task, "analysis_duration_ms", duration_ms)

            await _publish_stage(
                stream_id,
                "RESULT_READY",
                "复用已有分析结果",
                cache_hit=False,
                reused_inflight=True,
                duration_ms=duration_ms,
            )
            await _publish_analysis(stream_id, shared_result)
            await _publish_done(stream_id)
            return

        # =====================================================
        # 真正执行分析（owner）
        # =====================================================
        await _publish_stage(
            stream_id,
            "QUEUE_WAIT",
            "等待分析资源",
        )

        async with analysis_semaphore:
            await _publish_stage(
                stream_id,
                "ANALYSIS_START",
                "开始进行企业级需求评审分析",
            )

            pipeline = RequirementAnalysisPipeline()
            publish = _make_publish(stream_id)

            raw_result = await asyncio.wait_for(
                pipeline.run_async(
                    requirement_text=requirement_text,
                    publish=publish,
                    include_debug=False,
                ),
                timeout=_ANALYSIS_TIMEOUT_SEC,
            )

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        result = _ensure_result_shape(
            raw_result,
            workflow_id=workflow_id,
            requirement_id=requirement_id,
            cache_hit=False,
            reused_inflight=False,
            duration_ms=duration_ms,
        )

        # =====================================================
        # 写缓存
        # =====================================================
        await _publish_stage(
            stream_id,
            "CACHE_SAVE",
            "正在写入分析缓存",
        )

        await _set_cache(
            requirement_text=requirement_text,
            result=result,
            requirement_id=requirement_id,
        )

        # =====================================================
        # 写 workflow
        # =====================================================
        _task_set(task, "analysis_result", result)
        _task_set(task, "analysis_status", "done")
        _task_set(task, "analysis_error", None)
        _task_set(task, "analysis_requirement_id", requirement_id)
        _task_set(task, "analysis_duration_ms", duration_ms)

        # =====================================================
        # SSE 输出
        # =====================================================
        await _publish_stage(
            stream_id,
            "RESULT_READY",
            "分析结果生成完成",
            duration_ms=duration_ms,
        )

        await _publish_analysis(
            stream_id,
            result,
        )

        await _publish_stage(
            stream_id,
            "ANALYSIS_DONE",
            "需求分析完成",
            duration_ms=duration_ms,
        )

        await _publish_done(stream_id)

        # 唤醒其他等待同 key 结果的请求
        if inflight_future is not None and not inflight_future.done():
            inflight_future.set_result(result)

    except asyncio.TimeoutError:
        timeout_exc = TimeoutError(f"analysis timeout after {_ANALYSIS_TIMEOUT_SEC}s")

        if task is not None:
            _task_set(task, "analysis_status", "error")
            _task_set(task, "analysis_error", str(timeout_exc))

        if inflight_future is not None and not inflight_future.done():
            inflight_future.set_exception(timeout_exc)

        await _publish_error(stream_id, timeout_exc, trace="controller timeout")

    except Exception as e:
        if task is not None:
            _task_set(task, "analysis_status", "error")
            _task_set(task, "analysis_error", str(e))

        if inflight_future is not None and not inflight_future.done():
            inflight_future.set_exception(e)

        await _publish_error(stream_id, e)

    finally:
        if is_owner and cache_key:
            await _clear_inflight_future(cache_key)

        await analysis_sse_manager.close(stream_id)