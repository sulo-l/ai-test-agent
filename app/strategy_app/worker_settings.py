#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/worker_settings.py

from __future__ import annotations

import os
from typing import Any, Dict


# =====================================================
# 基础工具
# =====================================================

def _get_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on", "y"}:
        return True
    if s in {"0", "false", "no", "off", "n"}:
        return False
    return default


# =====================================================
# 队列 / worker 基础配置
# =====================================================

STRATEGY_QUEUE_NAME: str = _get_str("STRATEGY_QUEUE_NAME", "strategy_queue")

ARQ_TASK_GENERATE_STRATEGY: str = _get_str(
    "ARQ_TASK_GENERATE_STRATEGY",
    "app.strategy_app.tasks.generate_strategy",
)

STRATEGY_TASK_TIMEOUT_SEC: int = _get_int(
    "STRATEGY_TASK_TIMEOUT_SEC",
    1800,   # 30 分钟
)

STRATEGY_JOB_TTL_SEC: int = _get_int(
    "STRATEGY_JOB_TTL_SEC",
    86400,  # 24 小时
)

STRATEGY_ALLOW_LOCAL_FALLBACK: bool = _get_bool(
    "STRATEGY_ALLOW_LOCAL_FALLBACK",
    True,
)


# =====================================================
# 心跳 / 进度 / 流式配置
# =====================================================

STRATEGY_WORKER_HEARTBEAT_ENABLED: bool = _get_bool(
    "STRATEGY_WORKER_HEARTBEAT_ENABLED",
    True,
)

STRATEGY_WORKER_HEARTBEAT_SEC: int = _get_int(
    "STRATEGY_WORKER_HEARTBEAT_SEC",
    5,
)

STRATEGY_PROGRESS_ENABLED: bool = _get_bool(
    "STRATEGY_PROGRESS_ENABLED",
    True,
)

STRATEGY_PROGRESS_INTERVAL_SEC: int = _get_int(
    "STRATEGY_PROGRESS_INTERVAL_SEC",
    2,
)

STRATEGY_STREAM_TTL_SEC: int = _get_int(
    "STRATEGY_STREAM_TTL_SEC",
    86400,
)

STRATEGY_STREAM_EVENT_LIMIT: int = _get_int(
    "STRATEGY_STREAM_EVENT_LIMIT",
    1000,
)

STRATEGY_STREAM_REDIS_PREFIX: str = _get_str(
    "STRATEGY_STREAM_REDIS_PREFIX",
    "strategy_stream_store",
)


# =====================================================
# SSE / WS 管理器配置
# =====================================================

STRATEGY_SSE_STREAM_TTL_SEC: int = _get_int(
    "STRATEGY_SSE_STREAM_TTL_SEC",
    3600,
)

STRATEGY_SSE_HEARTBEAT_SEC: int = _get_int(
    "STRATEGY_SSE_HEARTBEAT_SEC",
    15,
)


# =====================================================
# Pipeline 并发 / 超时控制（可选）
# =====================================================

STRATEGY_AGENT_TIMEOUT_SEC: int = _get_int(
    "STRATEGY_AGENT_TIMEOUT_SEC",
    180,
)

STRATEGY_SUBTASK_TIMEOUT_SEC: int = _get_int(
    "STRATEGY_SUBTASK_TIMEOUT_SEC",
    120,
)

STRATEGY_PARALLEL_ENABLED: bool = _get_bool(
    "STRATEGY_PARALLEL_ENABLED",
    True,
)

STRATEGY_MAX_CONCURRENCY: int = _get_int(
    "STRATEGY_MAX_CONCURRENCY",
    4,
)


# =====================================================
# 调试 / 日志配置
# =====================================================

STRATEGY_DEBUG_LOG_ENABLED: bool = _get_bool(
    "STRATEGY_DEBUG_LOG_ENABLED",
    False,
)

STRATEGY_SAVE_RAW_AGENT_OUTPUTS: bool = _get_bool(
    "STRATEGY_SAVE_RAW_AGENT_OUTPUTS",
    True,
)

STRATEGY_SAVE_CONTEXT_META: bool = _get_bool(
    "STRATEGY_SAVE_CONTEXT_META",
    True,
)


# =====================================================
# 默认质量门禁 / 执行策略偏好
# =====================================================

STRATEGY_DEFAULT_GATE_DECISION: str = _get_str(
    "STRATEGY_DEFAULT_GATE_DECISION",
    "conditional_pass",
)

STRATEGY_PRIORITIZE_SMOKE_FIRST: bool = _get_bool(
    "STRATEGY_PRIORITIZE_SMOKE_FIRST",
    True,
)

STRATEGY_PRIORITIZE_RISK_FIRST: bool = _get_bool(
    "STRATEGY_PRIORITIZE_RISK_FIRST",
    True,
)

STRATEGY_ENABLE_ENTERPRISE_DEFAULTS: bool = _get_bool(
    "STRATEGY_ENABLE_ENTERPRISE_DEFAULTS",
    True,
)


# =====================================================
# 导出配置（便于打印 / 调试）
# =====================================================

def to_dict() -> Dict[str, Any]:
    return {
        "STRATEGY_QUEUE_NAME": STRATEGY_QUEUE_NAME,
        "ARQ_TASK_GENERATE_STRATEGY": ARQ_TASK_GENERATE_STRATEGY,
        "STRATEGY_TASK_TIMEOUT_SEC": STRATEGY_TASK_TIMEOUT_SEC,
        "STRATEGY_JOB_TTL_SEC": STRATEGY_JOB_TTL_SEC,
        "STRATEGY_ALLOW_LOCAL_FALLBACK": STRATEGY_ALLOW_LOCAL_FALLBACK,
        "STRATEGY_WORKER_HEARTBEAT_ENABLED": STRATEGY_WORKER_HEARTBEAT_ENABLED,
        "STRATEGY_WORKER_HEARTBEAT_SEC": STRATEGY_WORKER_HEARTBEAT_SEC,
        "STRATEGY_PROGRESS_ENABLED": STRATEGY_PROGRESS_ENABLED,
        "STRATEGY_PROGRESS_INTERVAL_SEC": STRATEGY_PROGRESS_INTERVAL_SEC,
        "STRATEGY_STREAM_TTL_SEC": STRATEGY_STREAM_TTL_SEC,
        "STRATEGY_STREAM_EVENT_LIMIT": STRATEGY_STREAM_EVENT_LIMIT,
        "STRATEGY_STREAM_REDIS_PREFIX": STRATEGY_STREAM_REDIS_PREFIX,
        "STRATEGY_SSE_STREAM_TTL_SEC": STRATEGY_SSE_STREAM_TTL_SEC,
        "STRATEGY_SSE_HEARTBEAT_SEC": STRATEGY_SSE_HEARTBEAT_SEC,
        "STRATEGY_AGENT_TIMEOUT_SEC": STRATEGY_AGENT_TIMEOUT_SEC,
        "STRATEGY_SUBTASK_TIMEOUT_SEC": STRATEGY_SUBTASK_TIMEOUT_SEC,
        "STRATEGY_PARALLEL_ENABLED": STRATEGY_PARALLEL_ENABLED,
        "STRATEGY_MAX_CONCURRENCY": STRATEGY_MAX_CONCURRENCY,
        "STRATEGY_DEBUG_LOG_ENABLED": STRATEGY_DEBUG_LOG_ENABLED,
        "STRATEGY_SAVE_RAW_AGENT_OUTPUTS": STRATEGY_SAVE_RAW_AGENT_OUTPUTS,
        "STRATEGY_SAVE_CONTEXT_META": STRATEGY_SAVE_CONTEXT_META,
        "STRATEGY_DEFAULT_GATE_DECISION": STRATEGY_DEFAULT_GATE_DECISION,
        "STRATEGY_PRIORITIZE_SMOKE_FIRST": STRATEGY_PRIORITIZE_SMOKE_FIRST,
        "STRATEGY_PRIORITIZE_RISK_FIRST": STRATEGY_PRIORITIZE_RISK_FIRST,
        "STRATEGY_ENABLE_ENTERPRISE_DEFAULTS": STRATEGY_ENABLE_ENTERPRISE_DEFAULTS,
    }


def print_settings() -> None:
    for k, v in to_dict().items():
        print(f"{k}={v}")


# =====================================================
# ARQ WorkerSettings
# =====================================================
import logging
import traceback
from typing import Optional
from arq.connections import RedisSettings

logger = logging.getLogger(__name__)

_REDIS_URL: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")


async def on_startup(ctx: Dict[str, Any]) -> None:
    logger.info(
        "Strategy ARQ worker starting | pid=%s | queue=%s",
        os.getpid(),
        STRATEGY_QUEUE_NAME,
    )
    try:
        redis = ctx.get("redis")
        if redis is not None:
            pong = await redis.ping()
            logger.info("Strategy worker redis ping: %s", pong)
    except Exception:
        logger.exception("Strategy worker redis ping failed")


async def on_shutdown(ctx: Dict[str, Any]) -> None:
    logger.info("Strategy ARQ worker shutting down | pid=%s", os.getpid())


async def on_job_start(ctx: Dict[str, Any]) -> None:
    job = ctx.get("job")
    job_id = getattr(job, "job_id", None) or getattr(job, "id", None)
    func_name = getattr(job, "function_name", None)
    logger.info("Strategy job start | job_id=%s | func=%s", job_id, func_name)


async def on_job_end(ctx: Dict[str, Any]) -> None:
    job = ctx.get("job")
    job_id = getattr(job, "job_id", None) or getattr(job, "id", None)
    func_name = getattr(job, "function_name", None)
    logger.info("Strategy job end | job_id=%s | func=%s", job_id, func_name)


async def on_job_error(ctx: Dict[str, Any]) -> None:
    job = ctx.get("job")
    job_id = getattr(job, "job_id", None) or getattr(job, "id", None)
    func_name = getattr(job, "function_name", None)
    exc: Optional[Any] = ctx.get("exception")
    tb = traceback.format_exc()
    logger.error("Strategy job error | job_id=%s | func=%s | exc=%s", job_id, func_name, repr(exc))
    logger.error("Strategy job error traceback:\n%s", tb)


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(_REDIS_URL)
    queue_name = STRATEGY_QUEUE_NAME
    max_tries = 1
    functions = [ARQ_TASK_GENERATE_STRATEGY]
    max_jobs = 2
    job_timeout = STRATEGY_TASK_TIMEOUT_SEC
    keep_result = 0
    on_startup = on_startup
    on_shutdown = on_shutdown
    on_job_start = on_job_start
    on_job_end = on_job_end
    on_job_error = on_job_error