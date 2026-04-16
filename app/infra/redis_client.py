#! /usr/bin/python3
# coding=utf-8
# app/infra/redis_client.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import logging
from typing import Optional, Dict, Any

import redis.asyncio as redis

logger = logging.getLogger(__name__)


# =====================================================
# Env helpers
# =====================================================

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


def _get_redis_url() -> str:
    # local:  redis://127.0.0.1:6379/0
    # docker: redis://redis:6379/0
    return os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")


# =====================================================
# Base config
# =====================================================

REDIS_URL = _get_redis_url()

# decode -> str，避免 bytes 到处处理
REDIS_DECODE_RESPONSES = os.getenv("REDIS_DECODE_RESPONSES", "1") == "1"

# 长连接健康检查
REDIS_HEALTH_CHECK_INTERVAL = _get_env_int("REDIS_HEALTH_CHECK_INTERVAL", 30)

# 命令超时自动重试
REDIS_RETRY_ON_TIMEOUT = os.getenv("REDIS_RETRY_ON_TIMEOUT", "1") == "1"


# =====================================================
# Normal pool (短命令：GET/SET/XADD/EXPIRE/HGETALL...)
# =====================================================

REDIS_MAX_CONNECTIONS = _get_env_int("REDIS_MAX_CONNECTIONS", 50)
REDIS_SOCKET_TIMEOUT = _get_env_float("REDIS_SOCKET_TIMEOUT", 10.0)
REDIS_SOCKET_CONNECT_TIMEOUT = _get_env_float("REDIS_SOCKET_CONNECT_TIMEOUT", 5.0)


# =====================================================
# Blocking pool (长等待：XREAD BLOCK / BRPOP 等)
# =====================================================

REDIS_BLOCKING_MAX_CONNECTIONS = _get_env_int("REDIS_BLOCKING_MAX_CONNECTIONS", 10)
REDIS_BLOCKING_SOCKET_TIMEOUT = _get_env_float("REDIS_BLOCKING_SOCKET_TIMEOUT", 75.0)


# =====================================================
# Singleton clients
# =====================================================

_redis_client: Optional[redis.Redis] = None
_redis_blocking_client: Optional[redis.Redis] = None


# =====================================================
# Client builders
# =====================================================

def get_redis() -> redis.Redis:
    """
    普通 Redis 客户端（短命令）：
    用于 GET / SET / XADD / EXPIRE / HGETALL 等。
    """
    global _redis_client

    if _redis_client is not None:
        return _redis_client

    _redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=REDIS_DECODE_RESPONSES,
        max_connections=REDIS_MAX_CONNECTIONS,
        socket_timeout=REDIS_SOCKET_TIMEOUT,
        socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
        health_check_interval=REDIS_HEALTH_CHECK_INTERVAL,
        retry_on_timeout=REDIS_RETRY_ON_TIMEOUT,
    )
    return _redis_client


def get_redis_blocking() -> redis.Redis:
    """
    blocking 专用 Redis 客户端：
    只用于 XREAD BLOCK / BRPOP 这种长阻塞命令。
    """
    global _redis_blocking_client

    if _redis_blocking_client is not None:
        return _redis_blocking_client

    _redis_blocking_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=REDIS_DECODE_RESPONSES,
        max_connections=REDIS_BLOCKING_MAX_CONNECTIONS,
        socket_timeout=REDIS_BLOCKING_SOCKET_TIMEOUT,
        socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
        health_check_interval=REDIS_HEALTH_CHECK_INTERVAL,
        retry_on_timeout=REDIS_RETRY_ON_TIMEOUT,
    )
    return _redis_blocking_client


# =====================================================
# Lazy proxy
# 目的：兼容这种写法
# from app.infra.redis_client import redis_client
# await redis_client.get(...)
# =====================================================

class _RedisLazyProxy:
    """
    懒代理：
    在真正访问属性时，才获取 singleton redis client。
    这样可兼容历史代码，同时避免导入时就强行初始化连接。
    """

    def __getattr__(self, item: str):
        client = get_redis()
        return getattr(client, item)

    def __repr__(self) -> str:
        return "<RedisLazyProxy normal>"


class _RedisBlockingLazyProxy:
    """
    blocking redis 的懒代理。
    """

    def __getattr__(self, item: str):
        client = get_redis_blocking()
        return getattr(client, item)

    def __repr__(self) -> str:
        return "<RedisLazyProxy blocking>"


# 对外兼容导出
redis_client = _RedisLazyProxy()
redis_blocking_client = _RedisBlockingLazyProxy()


# =====================================================
# Lifecycle
# =====================================================

async def init_redis() -> None:
    """
    FastAPI startup / lifespan 调用：
    初始化并检查 normal + blocking 两个 client。
    """
    r = get_redis()
    rb = get_redis_blocking()

    try:
        ok1 = await r.ping()
        ok2 = await rb.ping()

        if ok1 is not True or ok2 is not True:
            raise RuntimeError(f"Redis ping returned: normal={ok1}, blocking={ok2}")

        logger.info("Redis connected: %s", REDIS_URL)
        logger.info(
            "Redis pools: normal(max=%s, timeout=%.1fs), blocking(max=%s, timeout=%.1fs)",
            REDIS_MAX_CONNECTIONS,
            REDIS_SOCKET_TIMEOUT,
            REDIS_BLOCKING_MAX_CONNECTIONS,
            REDIS_BLOCKING_SOCKET_TIMEOUT,
        )
    except Exception:
        logger.exception("Redis init failed: %s", REDIS_URL)
        raise


async def _close_one_client(client: Optional[redis.Redis], name: str) -> None:
    if client is None:
        return

    try:
        # 兼容不同版本 redis.asyncio
        if hasattr(client, "aclose"):
            await client.aclose()
        else:
            await client.close()

        pool = getattr(client, "connection_pool", None)
        if pool is not None and hasattr(pool, "disconnect"):
            maybe_coro = pool.disconnect(inuse_connections=True)
            if maybe_coro is not None:
                await maybe_coro

        logger.info("Redis closed: %s", name)

    except Exception:
        logger.exception("Redis close failed: %s", name)


async def close_redis() -> None:
    """
    FastAPI shutdown / lifespan 调用：
    优雅关闭 normal + blocking 两个 client。
    """
    global _redis_client, _redis_blocking_client

    await _close_one_client(_redis_client, "normal")
    await _close_one_client(_redis_blocking_client, "blocking")

    _redis_client = None
    _redis_blocking_client = None


# =====================================================
# Debug helpers
# =====================================================

async def redis_healthcheck() -> Dict[str, Any]:
    """
    返回 Redis 当前信息摘要，便于排查连接池/延迟问题。
    """
    r = get_redis()
    info = await r.info()

    return {
        "url": REDIS_URL,
        "redis_version": info.get("redis_version"),
        "connected_clients": info.get("connected_clients"),
        "blocked_clients": info.get("blocked_clients"),
        "used_memory_human": info.get("used_memory_human"),
        "uptime_in_seconds": info.get("uptime_in_seconds"),
        "normal_socket_timeout": REDIS_SOCKET_TIMEOUT,
        "blocking_socket_timeout": REDIS_BLOCKING_SOCKET_TIMEOUT,
        "normal_max_connections": REDIS_MAX_CONNECTIONS,
        "blocking_max_connections": REDIS_BLOCKING_MAX_CONNECTIONS,
        "decode_responses": REDIS_DECODE_RESPONSES,
        "retry_on_timeout": REDIS_RETRY_ON_TIMEOUT,
        "health_check_interval": REDIS_HEALTH_CHECK_INTERVAL,
    }