#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/3/9 20:51
# @Author: sulo
#! /usr/bin/python3
# coding=utf-8
# app/analysis_app/worker_settings.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Dict, Any


# =====================================================
# env helpers
# =====================================================

def _get_env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _get_env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _get_env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


# =====================================================
# settings model
# =====================================================

@dataclass(frozen=True)
class AnalysisWorkerSettings:
    """
    analysis_app 统一运行配置

    说明：
    - controller / pipeline / sse / stream_store 建议统一读取这里
    - 所有值都可通过环境变量覆盖
    """

    # -------------------------------------------------
    # 基础标识
    # -------------------------------------------------
    app_name: str
    env: str

    # -------------------------------------------------
    # 并发控制
    # -------------------------------------------------
    analysis_concurrency: int
    pipeline_enable_parallel: bool
    pipeline_max_parallel_agents: int
    pipeline_agent_timeout_sec: int

    # -------------------------------------------------
    # 缓存
    # -------------------------------------------------
    cache_enabled: bool
    cache_ttl_sec: int
    cache_key_prefix: str

    # -------------------------------------------------
    # Stream / SSE
    # -------------------------------------------------
    stream_ttl_sec: int
    stream_key_prefix: str
    sse_heartbeat_interval_sec: float
    sse_queue_maxsize: int
    sse_close_timeout_sec: int

    # -------------------------------------------------
    # 结果存储
    # -------------------------------------------------
    result_ttl_sec: int
    meta_ttl_sec: int

    # -------------------------------------------------
    # 文本保护
    # -------------------------------------------------
    requirement_min_length: int
    requirement_max_length: int
    prompt_context_max_items: int

    # -------------------------------------------------
    # LLM / Agent
    # -------------------------------------------------
    llm_timeout_sec: int
    llm_max_retries: int
    agent_default_timeout_sec: int

    # -------------------------------------------------
    # 监控 / 调试
    # -------------------------------------------------
    log_slow_task_threshold_ms: int
    debug_enabled: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =====================================================
# singleton settings
# =====================================================

_SETTINGS = AnalysisWorkerSettings(
    # 基础标识
    app_name=_get_env_str("ANALYSIS_APP_NAME", "analysis_app"),
    env=_get_env_str("APP_ENV", _get_env_str("ENV", "dev")),

    # 并发控制
    analysis_concurrency=_get_env_int("ANALYSIS_CONCURRENCY", 5),
    pipeline_enable_parallel=_get_env_bool("ANALYSIS_PIPELINE_ENABLE_PARALLEL", True),
    pipeline_max_parallel_agents=_get_env_int("ANALYSIS_PIPELINE_MAX_PARALLEL_AGENTS", 4),
    pipeline_agent_timeout_sec=_get_env_int("ANALYSIS_PIPELINE_AGENT_TIMEOUT_SEC", 180),

    # 缓存
    cache_enabled=_get_env_bool("ANALYSIS_CACHE_ENABLED", True),
    cache_ttl_sec=_get_env_int("ANALYSIS_CACHE_TTL_SEC", 60 ),
    cache_key_prefix=_get_env_str("ANALYSIS_CACHE_KEY_PREFIX", "analysis:cache:"),

    # Stream / SSE
    stream_ttl_sec=_get_env_int("ANALYSIS_STREAM_TTL_SEC", 60 * 60),
    stream_key_prefix=_get_env_str("ANALYSIS_STREAM_KEY_PREFIX", "analysis:stream:"),
    sse_heartbeat_interval_sec=_get_env_float("ANALYSIS_SSE_HEARTBEAT_INTERVAL_SEC", 15.0),
    sse_queue_maxsize=_get_env_int("ANALYSIS_SSE_QUEUE_MAXSIZE", 0),
    sse_close_timeout_sec=_get_env_int("ANALYSIS_SSE_CLOSE_TIMEOUT_SEC", 3),

    # 结果存储
    result_ttl_sec=_get_env_int("ANALYSIS_RESULT_TTL_SEC", 60 * 60),
    meta_ttl_sec=_get_env_int("ANALYSIS_META_TTL_SEC", 60 * 60),

    # 文本保护
    requirement_min_length=_get_env_int("ANALYSIS_REQUIREMENT_MIN_LENGTH", 5),
    requirement_max_length=_get_env_int("ANALYSIS_REQUIREMENT_MAX_LENGTH", 200000),
    prompt_context_max_items=_get_env_int("ANALYSIS_PROMPT_CONTEXT_MAX_ITEMS", 30),

    # LLM / Agent
    llm_timeout_sec=_get_env_int("ANALYSIS_LLM_TIMEOUT_SEC", 120),
    llm_max_retries=_get_env_int("ANALYSIS_LLM_MAX_RETRIES", 2),
    agent_default_timeout_sec=_get_env_int("ANALYSIS_AGENT_DEFAULT_TIMEOUT_SEC", 120),

    # 监控 / 调试
    log_slow_task_threshold_ms=_get_env_int("ANALYSIS_LOG_SLOW_TASK_THRESHOLD_MS", 3000),
    debug_enabled=_get_env_bool("ANALYSIS_DEBUG_ENABLED", False),
)


# =====================================================
# public api
# =====================================================

def get_analysis_worker_settings() -> AnalysisWorkerSettings:
    return _SETTINGS


def get_analysis_settings_dict() -> Dict[str, Any]:
    return _SETTINGS.to_dict()


# =====================================================
# shortcuts
# =====================================================

SETTINGS = _SETTINGS

ANALYSIS_CONCURRENCY = SETTINGS.analysis_concurrency

ANALYSIS_CACHE_ENABLED = SETTINGS.cache_enabled
ANALYSIS_CACHE_TTL_SEC = SETTINGS.cache_ttl_sec
ANALYSIS_CACHE_KEY_PREFIX = SETTINGS.cache_key_prefix

ANALYSIS_STREAM_TTL_SEC = SETTINGS.stream_ttl_sec
ANALYSIS_STREAM_KEY_PREFIX = SETTINGS.stream_key_prefix

ANALYSIS_SSE_HEARTBEAT_INTERVAL_SEC = SETTINGS.sse_heartbeat_interval_sec
ANALYSIS_SSE_QUEUE_MAXSIZE = SETTINGS.sse_queue_maxsize
ANALYSIS_SSE_CLOSE_TIMEOUT_SEC = SETTINGS.sse_close_timeout_sec

ANALYSIS_PIPELINE_ENABLE_PARALLEL = SETTINGS.pipeline_enable_parallel
ANALYSIS_PIPELINE_MAX_PARALLEL_AGENTS = SETTINGS.pipeline_max_parallel_agents
ANALYSIS_PIPELINE_AGENT_TIMEOUT_SEC = SETTINGS.pipeline_agent_timeout_sec

ANALYSIS_REQUIREMENT_MIN_LENGTH = SETTINGS.requirement_min_length
ANALYSIS_REQUIREMENT_MAX_LENGTH = SETTINGS.requirement_max_length
ANALYSIS_PROMPT_CONTEXT_MAX_ITEMS = SETTINGS.prompt_context_max_items

ANALYSIS_LLM_TIMEOUT_SEC = SETTINGS.llm_timeout_sec
ANALYSIS_LLM_MAX_RETRIES = SETTINGS.llm_max_retries
ANALYSIS_AGENT_DEFAULT_TIMEOUT_SEC = SETTINGS.agent_default_timeout_sec

ANALYSIS_RESULT_TTL_SEC = SETTINGS.result_ttl_sec
ANALYSIS_META_TTL_SEC = SETTINGS.meta_ttl_sec

ANALYSIS_LOG_SLOW_TASK_THRESHOLD_MS = SETTINGS.log_slow_task_threshold_ms
ANALYSIS_DEBUG_ENABLED = SETTINGS.debug_enabled