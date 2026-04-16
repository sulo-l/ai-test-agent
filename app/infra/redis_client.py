#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/2/16 09:39
# @Author: sulo
# app/infra/redis_client.py
# -*- coding: utf-8 -*-

import os
import logging
from typing import Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# -----------------------------
# 配置（优先环境变量，其次默认值）
# -----------------------------
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
    # 你可以在 docker-compose 里配置：REDIS_URL=redis://redis:6379/0
    return os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")


# 连接参数（你可按压测结果调整）
REDIS_URL = _get_redis_url()
REDIS_MAX_CONNECTIONS = _get_env_int("REDIS_MAX_CONNECTIONS", 50)

# socket 超时：读写超时 / 连接超时
REDIS_SOCKET_TIMEOUT = _get_env_float("REDIS_SOCKET_TIMEOUT", 5.0)
REDIS_SOCKET_CONNECT_TIMEOUT = _get_env_float("REDIS_SOCKET_CONNECT_TIMEOUT", 3.0)

# 健康检查：redis 会定期 ping，避免长连接被中间网络设备断掉后“假活”
REDIS_HEALTH_CHECK_INTERVAL = _get_env_int("REDIS_HEALTH_CHECK_INTERVAL", 30)

# 遇到超时是否重试（redis-py 的行为是“重试同一次命令”）
REDIS_RETRY_ON_TIMEOUT = os.getenv("REDIS_RETRY_ON_TIMEOUT", "1") == "1"

# 是否把返回值 decode 成 str（建议 True，省去 bytes 处理）
REDIS_DECODE_RESPONSES = os.getenv("REDIS_DECODE_RESPONSES", "1") == "1"


# -----------------------------
# 单例客户端（进程内复用）
# -----------------------------
_redis_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    """
    获取全局 Redis 客户端（asyncio 版本）。
    - 进程内单例复用连接池
    - decode_responses=True：返回 str
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    # 统一连接池配置
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


async def init_redis() -> None:
    """
    建议在 FastAPI startup/lifespan 里调用，做一次连通性检查。
    """
    r = get_redis()
    try:
        ok = await r.ping()
        if ok is not True:
            raise RuntimeError(f"Redis ping returned: {ok}")
        logger.info("Redis connected: %s", REDIS_URL)
    except Exception:
        logger.exception("Redis init failed: %s", REDIS_URL)
        raise


async def close_redis() -> None:
    """
    建议在 FastAPI shutdown/lifespan 里调用，优雅关闭连接池。
    """
    global _redis_client
    if _redis_client is None:
        return

    try:
        # redis.asyncio 在 4.x/5.x 关闭方式略有差异，这里兼容写法
        await _redis_client.close()
        # 某些版本需要显式断开连接池
        if hasattr(_redis_client, "connection_pool"):
            await _redis_client.connection_pool.disconnect(inuse_connections=True)
        logger.info("Redis closed")
    except Exception:
        logger.exception("Redis close failed")
    finally:
        _redis_client = None


# -----------------------------
# 可选：健康检查辅助函数（调试用）
# -----------------------------
async def redis_healthcheck() -> dict:
    """
    返回 Redis 当前信息摘要，便于排查连接池/延迟问题。
    """
    r = get_redis()
    info = await r.info()
    return {
        "url": REDIS_URL,
        "redis_version": info.get("redis_version"),
        "connected_clients": info.get("connected_clients"),
        "used_memory_human": info.get("used_memory_human"),
        "uptime_in_seconds": info.get("uptime_in_seconds"),
    }
