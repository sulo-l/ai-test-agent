# -*- coding: utf-8 -*-
"""
app/testcase_app/worker_settings.py

启动方式：
  arq app.testcase_app.worker_settings.WorkerSettings

请确认实际启动命令就是上面这一条。
"""

import os
import logging
import traceback
from typing import Any, Dict, Optional, List

from arq.connections import RedisSettings

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except Exception:
        return default


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


# =========================
# Env configs
# =========================
REDIS_URL = _env_str("REDIS_URL", "redis://127.0.0.1:6379/0")
TC_QUEUE_NAME = _env_str("TC_QUEUE_NAME", "tc_queue")

# worker 级并发
# 建议你根据机器资源逐步调大，例如 2 / 3 / 4
TC_MAX_JOBS = _env_int("TC_MAX_JOBS", 2)

# ARQ job 总超时
TC_JOB_TIMEOUT_SEC = _env_int("TC_JOB_TIMEOUT_SEC", 60 * 60 * 2)
TC_KEEP_RESULT_SEC = _env_int("TC_KEEP_RESULT_SEC", 0)

# LLM stream 开关
TC_LLM_STREAM_ENABLED_DEFAULT = _env_bool("TC_LLM_STREAM_ENABLED", True)

# 调试期通常不建议重试太多
TC_MAX_TRIES = _env_int("TC_MAX_TRIES", 1)

# 是否注册 analysis 任务
TC_ENABLE_ANALYSIS_TASK = _env_bool("TC_ENABLE_ANALYSIS_TASK", True)

# 下面这些是为了和 testcase pipeline / tasks 对齐
TC_TASK_SOFT_TIMEOUT_SEC = _env_int("TC_TASK_SOFT_TIMEOUT_SEC", 0)

TC_LLM_TIMEOUT_ANALYSIS = _env_int("TC_LLM_TIMEOUT_ANALYSIS", 600)
TC_LLM_TIMEOUT_DESIGN = _env_int("TC_LLM_TIMEOUT_DESIGN", 180)
TC_LLM_TIMEOUT_REVIEW = _env_int("TC_LLM_TIMEOUT_REVIEW", 180)
TC_LLM_TIMEOUT_REFINE = _env_int("TC_LLM_TIMEOUT_REFINE", 240)

TC_MAX_TEST_POINTS = _env_int("TC_MAX_TEST_POINTS", 80)
TC_MAX_TESTCASES = _env_int("TC_MAX_TESTCASES", 200)

TC_POINTS_PER_CHUNK = _env_int("TC_POINTS_PER_CHUNK", 10)
TC_MAX_CHUNKS = _env_int("TC_MAX_CHUNKS", 18)
TC_CHUNK_MIN_CHARS = _env_int("TC_CHUNK_MIN_CHARS", 500)
TC_CHUNK_MAX_CHARS = _env_int("TC_CHUNK_MAX_CHARS", 2200)

TC_DESIGN_PARALLELISM = _env_int("TC_DESIGN_PARALLELISM", 3)
TC_REVIEW_PARALLELISM = _env_int("TC_REVIEW_PARALLELISM", 3)
TC_REFINE_PARALLELISM = _env_int("TC_REFINE_PARALLELISM", 3)

TC_WORKER_HEARTBEAT_ENABLED = _env_bool("TC_WORKER_HEARTBEAT_ENABLED", True)
TC_WORKER_HEARTBEAT_SEC = _env_float("TC_WORKER_HEARTBEAT_SEC", 10.0)

TC_ANALYSIS_PROGRESS_ENABLED = _env_bool("TC_ANALYSIS_PROGRESS_ENABLED", True)
TC_ANALYSIS_PROGRESS_SEC = _env_float("TC_ANALYSIS_PROGRESS_SEC", 2.0)

TC_STREAM_TTL_SEC = _env_int("TC_STREAM_TTL_SEC", 3600)
TC_JOB_TTL_SEC = _env_int("TC_JOB_TTL_SEC", TC_STREAM_TTL_SEC)

TC_WS_BLOCK_MS = _env_int("TC_WS_BLOCK_MS", 1000)
TC_WS_COUNT = _env_int("TC_WS_COUNT", 50)
TC_WS_BOOTSTRAP_TAIL = _env_int("TC_WS_BOOTSTRAP_TAIL", 20)
TC_WS_HEARTBEAT_SEC = _env_float("TC_WS_HEARTBEAT_SEC", 10.0)

TC_STREAM_MAXLEN = _env_int("TC_STREAM_MAXLEN", 4000)


def _mask_redis_url(url: str) -> str:
    if "@" in url:
        prefix, rest = url.split("@", 1)
        if ":" in prefix:
            return "redis://***@" + rest
    return url


def _build_functions() -> List[str]:
    funcs = [
        "app.testcase_app.tasks.generate_testcase",
    ]
    if TC_ENABLE_ANALYSIS_TASK:
        funcs.append("app.testcase_app.tasks.analyze_requirement")
    return funcs


REGISTERED_FUNCTIONS = _build_functions()


def _log_key_envs() -> None:
    keys = [
        # 基础
        "REDIS_URL",
        "TC_QUEUE_NAME",
        "TC_MAX_JOBS",
        "TC_MAX_TRIES",
        "TC_JOB_TIMEOUT_SEC",
        "TC_KEEP_RESULT_SEC",
        "TC_ENABLE_ANALYSIS_TASK",
        # stream / ws / job
        "TC_STREAM_TTL_SEC",
        "TC_JOB_TTL_SEC",
        "TC_STREAM_MAXLEN",
        "TC_WS_BLOCK_MS",
        "TC_WS_COUNT",
        "TC_WS_BOOTSTRAP_TAIL",
        "TC_WS_HEARTBEAT_SEC",
        # worker runtime
        "TC_WORKER_HEARTBEAT_ENABLED",
        "TC_WORKER_HEARTBEAT_SEC",
        "TC_ANALYSIS_PROGRESS_ENABLED",
        "TC_ANALYSIS_PROGRESS_SEC",
        "TC_TASK_SOFT_TIMEOUT_SEC",
        # llm / testcase pipeline
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "TC_LLM_STREAM_ENABLED",
        "TC_LLM_TIMEOUT_ANALYSIS",
        "TC_LLM_TIMEOUT_DESIGN",
        "TC_LLM_TIMEOUT_REVIEW",
        "TC_LLM_TIMEOUT_REFINE",
        "TC_MAX_TEST_POINTS",
        "TC_MAX_TESTCASES",
        "TC_POINTS_PER_CHUNK",
        "TC_MAX_CHUNKS",
        "TC_CHUNK_MIN_CHARS",
        "TC_CHUNK_MAX_CHARS",
        "TC_DESIGN_PARALLELISM",
        "TC_REVIEW_PARALLELISM",
        "TC_REFINE_PARALLELISM",
    ]
    snap = {k: os.getenv(k, "") for k in keys}
    logger.info("Worker env snapshot: %s", snap)


def _validate_runtime_env() -> None:
    if TC_MAX_JOBS <= 0:
        logger.warning("TC_MAX_JOBS <= 0, fallback to 1")
    if TC_JOB_TIMEOUT_SEC <= 0:
        logger.warning("TC_JOB_TIMEOUT_SEC <= 0, worker may timeout immediately")
    if TC_DESIGN_PARALLELISM <= 0:
        logger.warning("TC_DESIGN_PARALLELISM <= 0, should be >= 1")
    if TC_REVIEW_PARALLELISM <= 0:
        logger.warning("TC_REVIEW_PARALLELISM <= 0, should be >= 1")
    if TC_REFINE_PARALLELISM <= 0:
        logger.warning("TC_REFINE_PARALLELISM <= 0, should be >= 1")
    if TC_MAX_TEST_POINTS <= 0:
        logger.warning("TC_MAX_TEST_POINTS <= 0, analysis output may be empty")
    if TC_MAX_TESTCASES <= 0:
        logger.warning("TC_MAX_TESTCASES <= 0, design output may be empty")


# =========================
# Hooks
# =========================
async def on_startup(ctx: Dict[str, Any]) -> None:
    """
    worker 进程启动时兜底注入 env
    """
    os.environ.setdefault("TC_LLM_STREAM_ENABLED", "1" if TC_LLM_STREAM_ENABLED_DEFAULT else "0")

    logger.info(
        "ARQ worker starting | pid=%s | redis=%s | queue=%s | max_jobs=%s | job_timeout=%ss | keep_result=%ss | max_tries=%s",
        os.getpid(),
        _mask_redis_url(REDIS_URL),
        TC_QUEUE_NAME,
        TC_MAX_JOBS,
        TC_JOB_TIMEOUT_SEC,
        TC_KEEP_RESULT_SEC,
        TC_MAX_TRIES,
    )
    logger.info("ARQ worker registered functions: %s", REGISTERED_FUNCTIONS)

    _validate_runtime_env()
    _log_key_envs()

    try:
        redis = ctx.get("redis")
        if redis is None:
            logger.warning("ARQ ctx.redis is None (unexpected), skip ping")
            return
        pong = await redis.ping()
        logger.info("ARQ worker redis ping: %s", pong)
    except Exception:
        logger.exception("ARQ worker redis ping failed (check REDIS_URL and Redis service)")


async def on_shutdown(ctx: Dict[str, Any]) -> None:
    logger.info(
        "ARQ worker shutting down | pid=%s | queue=%s | functions=%s",
        os.getpid(),
        TC_QUEUE_NAME,
        REGISTERED_FUNCTIONS,
    )


async def on_job_start(ctx: Dict[str, Any]) -> None:
    """
    每个 job 开始时触发
    """
    job = ctx.get("job")
    job_id = getattr(job, "job_id", None) or getattr(job, "id", None)
    func_name = getattr(job, "function_name", None)
    logger.info("ARQ job start | job_id=%s | func=%s", job_id, func_name)

    logger.info(
        "Job env summary | "
        "TC_LLM_STREAM_ENABLED=%s | "
        "TC_JOB_TIMEOUT_SEC=%s | "
        "TC_TASK_SOFT_TIMEOUT_SEC=%s | "
        "TC_DESIGN_PARALLELISM=%s | "
        "TC_REVIEW_PARALLELISM=%s | "
        "TC_REFINE_PARALLELISM=%s",
        os.getenv("TC_LLM_STREAM_ENABLED", ""),
        os.getenv("TC_JOB_TIMEOUT_SEC", ""),
        os.getenv("TC_TASK_SOFT_TIMEOUT_SEC", ""),
        os.getenv("TC_DESIGN_PARALLELISM", ""),
        os.getenv("TC_REVIEW_PARALLELISM", ""),
        os.getenv("TC_REFINE_PARALLELISM", ""),
    )


async def on_job_end(ctx: Dict[str, Any]) -> None:
    """
    每个 job 成功结束触发
    """
    job = ctx.get("job")
    job_id = getattr(job, "job_id", None) or getattr(job, "id", None)
    func_name = getattr(job, "function_name", None)
    logger.info("ARQ job end | job_id=%s | func=%s", job_id, func_name)


async def on_job_error(ctx: Dict[str, Any]) -> None:
    """
    每个 job 异常触发
    """
    job = ctx.get("job")
    job_id = getattr(job, "job_id", None) or getattr(job, "id", None)
    func_name = getattr(job, "function_name", None)
    exc: Optional[BaseException] = ctx.get("exception")
    tb = traceback.format_exc()

    logger.error("ARQ job error | job_id=%s | func=%s | exc=%s", job_id, func_name, repr(exc))
    logger.error("ARQ job error traceback:\n%s", tb)


# =========================
# Worker Settings
# =========================
class WorkerSettings:
    """
    functions 用字符串引用更稳
    """
    redis_settings = RedisSettings.from_dsn(REDIS_URL)

    queue_name = TC_QUEUE_NAME
    max_tries = TC_MAX_TRIES
    functions = REGISTERED_FUNCTIONS

    max_jobs = TC_MAX_JOBS
    job_timeout = TC_JOB_TIMEOUT_SEC
    keep_result = TC_KEEP_RESULT_SEC

    on_startup = on_startup
    on_shutdown = on_shutdown
    on_job_start = on_job_start
    on_job_end = on_job_end
    on_job_error = on_job_error