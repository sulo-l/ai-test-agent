#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/2/16 10:04
# @Author: sulo
# app/infra/arq_pool.py
# -*- coding: utf-8 -*-

import os
import logging
from typing import Optional

from arq.connections import ArqRedis, RedisSettings, create_pool

logger = logging.getLogger(__name__)

# =========================
# Config
# =========================
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")


# =========================
# Singleton pool
# =========================
_pool: Optional[ArqRedis] = None


async def init_arq_pool() -> ArqRedis:
    """
    建议在 FastAPI startup/lifespan 里调用一次，提前建立连接池。
    """
    global _pool
    if _pool is not None:
        return _pool

    _pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
    logger.info("ARQ pool initialized: %s", REDIS_URL)

    # 健康检查：ping 一下，避免运行中才发现连接失败
    try:
        pong = await _pool.ping()
        logger.info("ARQ redis ping: %s", pong)
    except Exception:
        logger.exception("ARQ redis ping failed")
        raise

    return _pool


async def get_arq_pool() -> ArqRedis:
    """
    路由层调用：获取 ARQ redis pool（enqueue_job 用）
    如果还没 init，会懒加载创建。
    """
    if _pool is None:
        return await init_arq_pool()
    return _pool


async def close_arq_pool() -> None:
    """
    建议在 FastAPI shutdown/lifespan 里调用，优雅关闭连接池。
    """
    global _pool
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
    p = await get_arq_pool()
    try:
        pong = await p.ping()
    except Exception as e:
        return {"ok": False, "error": str(e), "redis_url": REDIS_URL}
    return {"ok": True, "ping": pong, "redis_url": REDIS_URL}
