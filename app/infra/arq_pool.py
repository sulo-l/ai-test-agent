# -*- coding: utf-8 -*-
# app/infra/arq_pool.py

import os
import logging
import asyncio
from typing import Optional

from arq.connections import ArqRedis, RedisSettings, create_pool

logger = logging.getLogger(__name__)

# =========================
# Config
# =========================
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

# 可选：连接/命令超时（不同 arq/redis 版本字段可能不同，不生效也没事）
ARQ_CONN_TIMEOUT = float(os.getenv("ARQ_CONN_TIMEOUT", "5"))
ARQ_CMD_TIMEOUT = float(os.getenv("ARQ_CMD_TIMEOUT", "5"))

# =========================
# Singleton pool + lock
# =========================
_pool: Optional[ArqRedis] = None
_pool_lock = asyncio.Lock()


def _mask_redis_url(url: str) -> str:
    """
    redis://:password@host:port/db -> redis://***@host:port/db
    """
    try:
        if "@" in url:
            prefix, rest = url.split("@", 1)
            if "://" in prefix and ":" in prefix:
                return "redis://***@" + rest
    except Exception:
        pass
    return url


def _make_redis_settings() -> RedisSettings:
    """
    兼容不同版本：尽量设置 timeout，不支持就忽略。
    """
    rs = RedisSettings.from_dsn(REDIS_URL)

    # 有些版本 RedisSettings 支持这些字段；不支持就跳过
    for attr, val in (
        ("conn_timeout", ARQ_CONN_TIMEOUT),
        ("command_timeout", ARQ_CMD_TIMEOUT),
    ):
        try:
            if hasattr(rs, attr):
                setattr(rs, attr, val)
        except Exception:
            pass

    return rs


async def init_arq_pool() -> ArqRedis:
    """
    建议在 FastAPI startup/lifespan 里调用一次，提前建立连接池。
    该实现是并发安全的：不会被同时初始化多次。
    """
    global _pool

    async with _pool_lock:
        # 双重检查：避免重复初始化
        if _pool is not None:
            return _pool

        rs = _make_redis_settings()
        _pool = await create_pool(rs)

        logger.info("ARQ pool initialized: %s", _mask_redis_url(REDIS_URL))

        # 健康检查：ping 一下，避免运行中才发现连接失败
        try:
            pong = await _pool.ping()
            logger.info("ARQ redis ping: %s", pong)
        except Exception:
            logger.exception("ARQ redis ping failed (check REDIS_URL and redis service)")
            # 初始化失败要清掉 pool，避免后续拿到坏对象
            try:
                _pool.close()
                await _pool.wait_closed()
            except Exception:
                pass
            _pool = None
            raise

        return _pool


async def get_arq_pool() -> ArqRedis:
    """
    路由层调用：获取 ARQ redis pool（enqueue_job 用）
    如果还没 init，会懒加载创建。
    若连接断了，会尝试重建。
    """
    global _pool

    if _pool is None:
        return await init_arq_pool()

    # 连接可能已断：做一个轻量 ping 验证，不通过就重建
    try:
        await _pool.ping()
        return _pool
    except Exception:
        logger.warning("ARQ pool ping failed, recreating pool... redis=%s", _mask_redis_url(REDIS_URL))

    # 重建（加锁避免并发重建）
    async with _pool_lock:
        # 其他协程可能已经重建好了
        if _pool is not None:
            try:
                await _pool.ping()
                return _pool
            except Exception:
                pass

        # 关闭旧的
        try:
            if _pool is not None:
                _pool.close()
                await _pool.wait_closed()
        except Exception:
            pass
        _pool = None

        return await init_arq_pool()


async def close_arq_pool() -> None:
    """
    建议在 FastAPI shutdown/lifespan 里调用，优雅关闭连接池。
    """
    global _pool

    async with _pool_lock:
        if _pool is None:
            return
        try:
            _pool.close()
            await _pool.wait_closed()
            logger.info("ARQ pool closed")
        except Exception:
            logger.exception("ARQ pool close failed")
        finally:
            _pool = None


async def arq_healthcheck() -> dict:
    """
    调试/监控用：返回当前连接信息摘要
    """
    try:
        p = await get_arq_pool()
        pong = await p.ping()
        return {"ok": True, "ping": pong, "redis_url": _mask_redis_url(REDIS_URL)}
    except Exception as e:
        return {"ok": False, "error": str(e), "redis_url": _mask_redis_url(REDIS_URL)}
