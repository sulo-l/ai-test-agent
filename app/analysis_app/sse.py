#! /usr/bin/python3
# coding=utf-8
# app/analysis_app/sse.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
import time
import uuid
import logging
from typing import Dict, Any, AsyncGenerator, Optional

from app.analysis_app.worker_settings import (
    ANALYSIS_SSE_HEARTBEAT_INTERVAL_SEC,
    ANALYSIS_STREAM_TTL_SEC,
    ANALYSIS_SSE_QUEUE_MAXSIZE,
)


logger = logging.getLogger(__name__)


class SSEStreamManager:
    """
    analysis_app 专属 SSE Stream 管理器

    特点：
    - 每个 stream_id 对应一个 asyncio.Queue
    - controller 负责 publish
    - router 负责 subscribe
    - heartbeat 保活
    - TTL 自动清理
    - queue 满时丢弃最旧事件，避免内存无限堆积
    """

    CLOSE_EVENT_TYPE = "__close__"

    def __init__(
        self,
        heartbeat_interval: float = 15.0,
        stream_ttl_sec: float = 1800.0,
        queue_maxsize: int = 200,
    ):
        self._streams: Dict[str, asyncio.Queue] = {}
        self._last_active: Dict[str, float] = {}
        self._lock = asyncio.Lock()

        self._heartbeat_interval = float(heartbeat_interval)
        self._stream_ttl_sec = float(stream_ttl_sec)
        self._queue_maxsize = int(queue_maxsize)

    # =====================================================
    # Stream 生命周期
    # =====================================================

    async def create_stream(self) -> str:
        stream_id = uuid.uuid4().hex

        async with self._lock:
            self._streams[stream_id] = asyncio.Queue(maxsize=self._queue_maxsize)
            self._last_active[stream_id] = time.time()

        logger.debug("analysis sse stream created: %s", stream_id)
        return stream_id

    async def exists(self, stream_id: str) -> bool:
        async with self._lock:
            return stream_id in self._streams

    async def publish(self, stream_id: str, event: Dict[str, Any]) -> None:
        if not isinstance(event, dict):
            event = {
                "type": "error",
                "message": f"invalid event payload: {type(event).__name__}",
            }

        async with self._lock:
            queue = self._streams.get(stream_id)
            if not queue:
                return
            self._last_active[stream_id] = time.time()

        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # 防止无限堆积：丢弃最旧事件，保留最新事件
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except Exception:
                logger.exception("analysis sse queue full fallback failed: %s", stream_id)

    async def close(self, stream_id: str) -> None:
        async with self._lock:
            queue = self._streams.get(stream_id)
            if not queue:
                return
            self._last_active[stream_id] = time.time()

        await self._safe_put(queue, {"type": self.CLOSE_EVENT_TYPE})

    # =====================================================
    # 订阅输出
    # =====================================================

    async def subscribe(self, stream_id: str) -> AsyncGenerator[str, None]:
        async with self._lock:
            queue = self._streams.get(stream_id)

        if not queue:
            yield self._format_event(
                {
                    "type": "error",
                    "message": "invalid stream_id",
                }
            )
            return

        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=self._heartbeat_interval,
                    )

                except asyncio.TimeoutError:
                    # 先发 heartbeat
                    yield self._format_event(
                        {
                            "type": "heartbeat",
                            "ts": int(time.time() * 1000),
                        }
                    )

                    # 再检查是否超时
                    if await self._is_idle_expired(stream_id):
                        await self.publish(
                            stream_id,
                            {
                                "type": "error",
                                "message": "stream expired",
                            },
                        )
                        await self.close(stream_id)

                    continue

                if not isinstance(event, dict):
                    event = {
                        "type": "error",
                        "message": "invalid event object",
                    }

                event_type = str(event.get("type") or "").strip()

                if event_type == self.CLOSE_EVENT_TYPE:
                    yield self._format_event({"type": "done"})
                    break

                async with self._lock:
                    if stream_id in self._last_active:
                        self._last_active[stream_id] = time.time()

                yield self._format_event(event)

        except asyncio.CancelledError:
            logger.info("analysis sse client disconnected: %s", stream_id)

        finally:
            await self._cleanup(stream_id)

    # =====================================================
    # 清理
    # =====================================================

    async def _cleanup(self, stream_id: str) -> None:
        async with self._lock:
            self._streams.pop(stream_id, None)
            self._last_active.pop(stream_id, None)
        logger.debug("analysis sse stream cleaned: %s", stream_id)

    async def _is_idle_expired(self, stream_id: str) -> bool:
        async with self._lock:
            last_active = self._last_active.get(stream_id)
            exists = stream_id in self._streams

        if not exists or last_active is None:
            return False

        return (time.time() - last_active) > self._stream_ttl_sec

    async def _safe_put(self, queue: asyncio.Queue, item: Dict[str, Any]) -> None:
        try:
            await queue.put(item)
        except Exception:
            logger.exception("analysis sse safe_put failed")

    # =====================================================
    # SSE 格式
    # =====================================================

    @staticmethod
    def _format_event(event: Dict[str, Any]) -> str:
        safe_event = dict(event or {})
        event_type = str(safe_event.get("type") or "message").strip() or "message"
        payload = json.dumps(safe_event, ensure_ascii=False)
        return f"event: {event_type}\ndata: {payload}\n\n"


# =====================================================
# 单例
# =====================================================

analysis_sse_manager = SSEStreamManager(
    heartbeat_interval=ANALYSIS_SSE_HEARTBEAT_INTERVAL_SEC,
    stream_ttl_sec=ANALYSIS_STREAM_TTL_SEC,
    queue_maxsize=ANALYSIS_SSE_QUEUE_MAXSIZE,
)