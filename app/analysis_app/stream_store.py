#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/3/9 20:19
# @Author: sulo
# app/analysis_app/stream_store.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import time
import logging
from typing import Any, Dict, List, Optional

from app.infra.redis_client import get_redis
from app.analysis_app.worker_settings import (
    ANALYSIS_STREAM_TTL_SEC,
    ANALYSIS_CACHE_TTL_SEC,
    ANALYSIS_STREAM_KEY_PREFIX,
    ANALYSIS_CACHE_KEY_PREFIX,
)

logger = logging.getLogger(__name__)


STREAM_TTL = int(ANALYSIS_STREAM_TTL_SEC)
CACHE_TTL = int(ANALYSIS_CACHE_TTL_SEC)


class AnalysisStreamStore:
    """
    需求分析流式状态存储

    Redis结构：

    1) 事件流
       {stream_prefix}{stream_id}:events

    2) 状态
       {stream_prefix}{stream_id}:meta

    3) 最终结果
       {stream_prefix}{stream_id}:result

    4) 缓存
       {cache_prefix}{cache_key}
    """

    # =========================================================
    # Redis
    # =========================================================

    @property
    def redis(self):
        # 每次动态获取，避免初始化顺序 / 重连问题
        return get_redis()

    # =========================================================
    # Key
    # =========================================================

    def _stream_base(self, stream_id: str) -> str:
        sid = str(stream_id or "").strip()
        return f"{ANALYSIS_STREAM_KEY_PREFIX}{sid}"

    def _events_key(self, stream_id: str) -> str:
        return f"{self._stream_base(stream_id)}:events"

    def _meta_key(self, stream_id: str) -> str:
        return f"{self._stream_base(stream_id)}:meta"

    def _result_key(self, stream_id: str) -> str:
        return f"{self._stream_base(stream_id)}:result"

    def _cache_key(self, cache_key: str) -> str:
        key = str(cache_key or "").strip()
        if not key:
            return ANALYSIS_CACHE_KEY_PREFIX.rstrip(":")
        if key.startswith(ANALYSIS_CACHE_KEY_PREFIX):
            return key
        return f"{ANALYSIS_CACHE_KEY_PREFIX}{key}"

    # =========================================================
    # JSON 工具
    # =========================================================

    def _json_dumps(self, value: Any) -> str:
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

    def _json_loads(self, raw: Any, default: Any = None) -> Any:
        if raw is None:
            return default

        try:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="ignore")
            return json.loads(raw)
        except Exception:
            return default

    async def _expire_all_stream_keys(self, stream_id: str) -> None:
        r = self.redis
        try:
            await r.expire(self._events_key(stream_id), STREAM_TTL)
            await r.expire(self._meta_key(stream_id), STREAM_TTL)
            await r.expire(self._result_key(stream_id), STREAM_TTL)
        except Exception:
            logger.exception("stream_store expire failed: %s", stream_id)

    # =========================================================
    # 初始化
    # =========================================================

    async def init_stream(self, stream_id: str) -> None:
        meta = {
            "status": "running",
            "stage": "INIT",
            "progress": 0,
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
        }

        r = self.redis

        await r.set(
            self._meta_key(stream_id),
            self._json_dumps(meta),
            ex=STREAM_TTL,
        )

        # 预热 TTL，避免后续某些 key 不存在时生命周期不一致
        await self._expire_all_stream_keys(stream_id)

    # =========================================================
    # 事件写入
    # =========================================================

    async def append_event(
        self,
        stream_id: str,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        event = {
            "type": str(event_type or "").strip() or "message",
            "data": data if isinstance(data, dict) else {"value": data},
            "ts": int(time.time() * 1000),
        }

        r = self.redis
        key = self._events_key(stream_id)

        await r.rpush(key, self._json_dumps(event))
        await self._expire_all_stream_keys(stream_id)

    # =========================================================
    # 状态更新
    # =========================================================

    async def set_status(
        self,
        stream_id: str,
        status: str,
        stage: Optional[str] = None,
        progress: Optional[int] = None,
    ) -> None:
        r = self.redis
        key = self._meta_key(stream_id)

        raw = await r.get(key)
        meta = self._json_loads(raw, default={}) or {}

        if not isinstance(meta, dict):
            meta = {}

        meta["status"] = str(status or "").strip() or "running"

        if stage is not None:
            meta["stage"] = str(stage).strip()

        if progress is not None:
            try:
                progress = int(progress)
            except Exception:
                progress = 0
            meta["progress"] = max(0, min(100, progress))

        if "created_at" not in meta:
            meta["created_at"] = int(time.time())

        meta["updated_at"] = int(time.time())

        await r.set(
            key,
            self._json_dumps(meta),
            ex=STREAM_TTL,
        )

        await self._expire_all_stream_keys(stream_id)

    # =========================================================
    # 结果存储
    # =========================================================

    async def set_result(
        self,
        stream_id: str,
        result: Dict[str, Any],
    ) -> None:
        r = self.redis
        payload = result if isinstance(result, dict) else {"result": result}

        await r.set(
            self._result_key(stream_id),
            self._json_dumps(payload),
            ex=STREAM_TTL,
        )

        await self._expire_all_stream_keys(stream_id)

    # =========================================================
    # 标记完成
    # =========================================================

    async def mark_done(
        self,
        stream_id: str,
        result: Dict[str, Any],
    ) -> None:
        await self.set_result(stream_id, result)

        await self.set_status(
            stream_id,
            status="done",
            stage="DONE",
            progress=100,
        )

        await self.append_event(
            stream_id,
            "done",
            result if isinstance(result, dict) else {"result": result},
        )

    # =========================================================
    # 标记错误
    # =========================================================

    async def mark_error(
        self,
        stream_id: str,
        error: str,
    ) -> None:
        await self.set_status(
            stream_id,
            status="error",
            stage="ERROR",
        )

        await self.append_event(
            stream_id,
            "error",
            {"message": str(error or "").strip() or "unknown error"},
        )

    # =========================================================
    # 读取事件
    # =========================================================

    async def get_events(
        self,
        stream_id: str,
        start: int = 0,
    ) -> List[Dict[str, Any]]:
        r = self.redis
        key = self._events_key(stream_id)

        try:
            start = int(start)
        except Exception:
            start = 0

        raw_events = await r.lrange(key, start, -1)

        events: List[Dict[str, Any]] = []

        for item in raw_events or []:
            parsed = self._json_loads(item, default=None)
            if isinstance(parsed, dict):
                events.append(parsed)

        return events

    # =========================================================
    # 读取状态
    # =========================================================

    async def get_status(
        self,
        stream_id: str,
    ) -> Optional[Dict[str, Any]]:
        r = self.redis
        raw = await r.get(self._meta_key(stream_id))

        data = self._json_loads(raw, default=None)
        return data if isinstance(data, dict) else None

    # =========================================================
    # 读取结果
    # =========================================================

    async def get_result(
        self,
        stream_id: str,
    ) -> Optional[Dict[str, Any]]:
        r = self.redis
        raw = await r.get(self._result_key(stream_id))

        data = self._json_loads(raw, default=None)
        return data if isinstance(data, dict) else None

    # =========================================================
    # 缓存读取
    # =========================================================

    async def get_cached_result(
        self,
        cache_key: str,
    ) -> Optional[Dict[str, Any]]:
        if not cache_key:
            return None

        r = self.redis
        raw = await r.get(self._cache_key(cache_key))

        data = self._json_loads(raw, default=None)
        return data if isinstance(data, dict) else None

    # =========================================================
    # 缓存写入
    # =========================================================

    async def set_cached_result(
        self,
        cache_key: str,
        result: Dict[str, Any],
    ) -> None:
        if not cache_key:
            return

        r = self.redis
        payload = result if isinstance(result, dict) else {"result": result}

        await r.set(
            self._cache_key(cache_key),
            self._json_dumps(payload),
            ex=CACHE_TTL,
        )

    # =========================================================
    # 清理
    # =========================================================

    async def delete_stream(self, stream_id: str) -> None:
        r = self.redis
        try:
            await r.delete(
                self._events_key(stream_id),
                self._meta_key(stream_id),
                self._result_key(stream_id),
            )
        except Exception:
            logger.exception("stream_store delete_stream failed: %s", stream_id)

    async def stream_exists(self, stream_id: str) -> bool:
        r = self.redis
        try:
            exists = await r.exists(
                self._events_key(stream_id),
                self._meta_key(stream_id),
                self._result_key(stream_id),
            )
            return bool(exists)
        except Exception:
            logger.exception("stream_store stream_exists failed: %s", stream_id)
            return False


# =========================================================
# 单例
# =========================================================

stream_store = AnalysisStreamStore()