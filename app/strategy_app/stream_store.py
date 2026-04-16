#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/stream_store.py

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =====================================================
# 配置
# =====================================================

STRATEGY_STREAM_TTL_SEC = int(os.getenv("STRATEGY_STREAM_TTL_SEC", "86400"))
STRATEGY_STREAM_EVENT_LIMIT = int(os.getenv("STRATEGY_STREAM_EVENT_LIMIT", "1000"))
STRATEGY_STREAM_REDIS_PREFIX = os.getenv("STRATEGY_STREAM_REDIS_PREFIX", "strategy_stream_store")


# =====================================================
# 工具函数
# =====================================================

def _safe_json_dumps(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        try:
            return json.dumps({"repr": repr(data)}, ensure_ascii=False)
        except Exception:
            return "{}"


def _safe_json_loads(text: Any) -> Any:
    if text is None:
        return None

    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8", errors="ignore")
        except Exception:
            return None

    if not isinstance(text, str):
        return None

    text = text.strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        return None


def _now_ts() -> int:
    return int(time.time())


def _redis_stream_meta_key(stream_id: str) -> str:
    return f"{STRATEGY_STREAM_REDIS_PREFIX}:stream:{stream_id}:meta"


def _redis_stream_events_key(stream_id: str) -> str:
    return f"{STRATEGY_STREAM_REDIS_PREFIX}:stream:{stream_id}:events"


def _redis_stream_result_key(stream_id: str) -> str:
    return f"{STRATEGY_STREAM_REDIS_PREFIX}:stream:{stream_id}:result"


def _redis_job_state_key(job_id: str) -> str:
    return f"{STRATEGY_STREAM_REDIS_PREFIX}:job:{job_id}:state"


def _redis_job_cancel_key(job_id: str) -> str:
    return f"{STRATEGY_STREAM_REDIS_PREFIX}:job:{job_id}:cancelled"


def _redis_stream_cancel_key(stream_id: str) -> str:
    return f"{STRATEGY_STREAM_REDIS_PREFIX}:stream:{stream_id}:cancelled"


# =====================================================
# StreamStore
# =====================================================

class StrategyStreamStore:
    """
    策略流式结果存储（企业级增强版）

    功能：
    1. stream 维度：事件、meta、result
    2. job 维度：state、heartbeat、cancel flag
    3. 兼容 tasks.py / router.py / controller.py 中的多种调用别名
    4. 内存主读写 + Redis 兜底
    """

    def __init__(self) -> None:
        self._streams: Dict[str, Dict[str, Any]] = {}
        self._job_states: Dict[str, Dict[str, Any]] = {}
        self._job_cancelled: Dict[str, bool] = {}
        self._stream_cancelled: Dict[str, bool] = {}
        self._lock = asyncio.Lock()
        self._redis: Any = None

    # -------------------------------------------------
    # 生命周期 / redis 绑定
    # -------------------------------------------------
    def bind_redis(self, redis: Any) -> None:
        self._redis = redis

    async def set_redis(self, redis: Any) -> None:
        self._redis = redis

    # -------------------------------------------------
    # stream 初始化
    # -------------------------------------------------
    async def ensure_stream(self, stream_id: str) -> None:
        if not stream_id:
            return

        async with self._lock:
            if stream_id not in self._streams:
                self._streams[stream_id] = {
                    "stream_id": stream_id,
                    "events": [],
                    "result": None,
                    "status": "running",
                    "created_at": _now_ts(),
                    "updated_at": _now_ts(),
                    "finished_at": None,
                }

        await self._sync_stream_meta_to_redis(stream_id)

    async def create_stream(self, stream_id: str) -> None:
        await self.ensure_stream(stream_id)

    # -------------------------------------------------
    # 事件追加（主入口）
    # -------------------------------------------------
    async def append_stream_event(self, stream_id: str, event: Dict[str, Any]) -> None:
        await self._append_event_impl(stream_id, event)

    async def append_event(self, stream_id: str, event: Dict[str, Any]) -> None:
        await self._append_event_impl(stream_id, event)

    async def append(self, stream_id: str, event: Dict[str, Any]) -> None:
        await self._append_event_impl(stream_id, event)

    async def push_event(self, stream_id: str, event: Dict[str, Any]) -> None:
        await self._append_event_impl(stream_id, event)

    async def add_event(self, stream_id: str, event: Dict[str, Any]) -> None:
        await self._append_event_impl(stream_id, event)

    async def save_event(self, stream_id: str, event: Dict[str, Any]) -> None:
        await self._append_event_impl(stream_id, event)

    async def write_event(self, stream_id: str, event: Dict[str, Any]) -> None:
        await self._append_event_impl(stream_id, event)

    async def emit_event(self, stream_id: str, event: Dict[str, Any]) -> None:
        await self._append_event_impl(stream_id, event)

    async def _append_event_impl(self, stream_id: str, event: Dict[str, Any]) -> None:
        if not stream_id or not isinstance(event, dict):
            return

        event = dict(event)
        event.setdefault("ts", _now_ts())

        await self.ensure_stream(stream_id)

        async with self._lock:
            stream = self._streams[stream_id]
            events: List[Dict[str, Any]] = stream["events"]
            events.append(event)

            if len(events) > STRATEGY_STREAM_EVENT_LIMIT:
                overflow = len(events) - STRATEGY_STREAM_EVENT_LIMIT
                del events[:overflow]

            stream["updated_at"] = _now_ts()

            event_type = str(event.get("type") or "").strip().lower()
            stage = str(event.get("stage") or "").strip().upper()

            if event_type == "error" or stage == "ERROR":
                stream["status"] = "error"
                stream["finished_at"] = _now_ts()
            elif stage in {"DONE", "RESULT_READY"}:
                if stage == "DONE":
                    stream["status"] = "done"
                    stream["finished_at"] = _now_ts()
            elif stage == "CANCELLED":
                stream["status"] = "cancelled"
                stream["finished_at"] = _now_ts()

        await self._append_stream_event_to_redis(stream_id, event)
        await self._sync_stream_meta_to_redis(stream_id)

    # -------------------------------------------------
    # 最终结果写入
    # -------------------------------------------------
    async def set_result(self, stream_id: str, result: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        await self._set_result_impl(stream_id, result, ttl_sec=ttl_sec)

    async def save_result(self, stream_id: str, result: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        await self._set_result_impl(stream_id, result, ttl_sec=ttl_sec)

    async def write_result(self, stream_id: str, result: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        await self._set_result_impl(stream_id, result, ttl_sec=ttl_sec)

    async def upsert_result(self, stream_id: str, result: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        await self._set_result_impl(stream_id, result, ttl_sec=ttl_sec)

    async def set_strategy_result(self, stream_id: str, result: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        await self._set_result_impl(stream_id, result, ttl_sec=ttl_sec)

    async def save_strategy_result(self, stream_id: str, result: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        await self._set_result_impl(stream_id, result, ttl_sec=ttl_sec)

    async def _set_result_impl(self, stream_id: str, result: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        if not stream_id:
            return

        await self.ensure_stream(stream_id)

        async with self._lock:
            stream = self._streams[stream_id]
            stream["result"] = result if isinstance(result, dict) else {}
            stream["updated_at"] = _now_ts()
            stream["status"] = "done"
            stream["finished_at"] = _now_ts()

        await self._set_stream_result_to_redis(
            stream_id,
            result if isinstance(result, dict) else {},
            ttl_sec=ttl_sec,
        )
        await self._sync_stream_meta_to_redis(stream_id, ttl_sec=ttl_sec)

    # -------------------------------------------------
    # stream 状态更新
    # -------------------------------------------------
    async def set_status(self, stream_id: str, status: str) -> None:
        if not stream_id:
            return

        await self.ensure_stream(stream_id)

        async with self._lock:
            stream = self._streams[stream_id]
            stream["status"] = str(status or "").strip() or stream["status"]
            stream["updated_at"] = _now_ts()
            if stream["status"] in {"done", "error", "cancelled"}:
                stream["finished_at"] = _now_ts()

        await self._sync_stream_meta_to_redis(stream_id)

    # -------------------------------------------------
    # job state
    # -------------------------------------------------
    async def set_job_state(self, job_id: str, payload: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        await self._set_job_state_impl(job_id, payload, ttl_sec=ttl_sec)

    async def update_job_state(self, job_id: str, payload: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        await self._set_job_state_impl(job_id, payload, ttl_sec=ttl_sec)

    async def save_job_state(self, job_id: str, payload: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        await self._set_job_state_impl(job_id, payload, ttl_sec=ttl_sec)

    async def set_state(self, job_id: str, payload: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        await self._set_job_state_impl(job_id, payload, ttl_sec=ttl_sec)

    async def update_state(self, job_id: str, payload: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        await self._set_job_state_impl(job_id, payload, ttl_sec=ttl_sec)

    async def _set_job_state_impl(self, job_id: str, payload: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        if not job_id or not isinstance(payload, dict):
            return

        data = dict(payload)
        data.setdefault("job_id", job_id)
        data.setdefault("updated_at", _now_ts())

        async with self._lock:
            old = self._job_states.get(job_id, {})
            merged = dict(old)
            merged.update(data)
            self._job_states[job_id] = merged

        await self._set_job_state_to_redis(job_id, self._job_states[job_id], ttl_sec=ttl_sec)

    async def get_job_state(self, job_id: str) -> Dict[str, Any]:
        if not job_id:
            return {}

        async with self._lock:
            state = self._job_states.get(job_id)
            if state:
                return dict(state)

        data = await self._load_job_state_from_redis(job_id)
        return data or {}

    async def read_job_state(self, job_id: str) -> Dict[str, Any]:
        return await self.get_job_state(job_id)

    async def load_job_state(self, job_id: str) -> Dict[str, Any]:
        return await self.get_job_state(job_id)

    async def get_state(self, job_id: str) -> Dict[str, Any]:
        return await self.get_job_state(job_id)

    # -------------------------------------------------
    # heartbeat
    # -------------------------------------------------
    async def touch_heartbeat(self, job_id: str, payload: Optional[Dict[str, Any]] = None, ttl_sec: Optional[int] = None) -> None:
        payload = dict(payload or {})
        payload.setdefault("heartbeat_at", _now_ts())
        payload.setdefault("updated_at", _now_ts())
        await self._set_job_state_impl(job_id, payload, ttl_sec=ttl_sec)

    async def update_heartbeat(self, job_id: str, payload: Optional[Dict[str, Any]] = None, ttl_sec: Optional[int] = None) -> None:
        await self.touch_heartbeat(job_id, payload, ttl_sec=ttl_sec)

    async def save_heartbeat(self, job_id: str, payload: Optional[Dict[str, Any]] = None, ttl_sec: Optional[int] = None) -> None:
        await self.touch_heartbeat(job_id, payload, ttl_sec=ttl_sec)

    async def set_heartbeat(self, job_id: str, payload: Optional[Dict[str, Any]] = None, ttl_sec: Optional[int] = None) -> None:
        await self.touch_heartbeat(job_id, payload, ttl_sec=ttl_sec)

    # -------------------------------------------------
    # cancel flag
    # -------------------------------------------------
    async def set_job_cancelled(self, job_id: str, ttl_sec: Optional[int] = None) -> None:
        if not job_id:
            return

        async with self._lock:
            self._job_cancelled[job_id] = True

        await self._set_job_cancel_flag_to_redis(job_id, True, ttl_sec=ttl_sec)

    async def mark_cancelled(self, target_id: str, ttl_sec: Optional[int] = None) -> None:
        # 默认按 job 处理，兼容旧调用
        await self.set_job_cancelled(target_id, ttl_sec=ttl_sec)

    async def set_cancelled(self, target_id: str, ttl_sec: Optional[int] = None) -> None:
        # 默认按 job 处理，兼容旧调用
        await self.set_job_cancelled(target_id, ttl_sec=ttl_sec)

    async def set_cancel_flag(self, target_id: str, ttl_sec: Optional[int] = None) -> None:
        # 默认按 job 处理，兼容旧调用
        await self.set_job_cancelled(target_id, ttl_sec=ttl_sec)

    async def is_job_cancelled(self, job_id: str) -> bool:
        if not job_id:
            return False

        async with self._lock:
            if job_id in self._job_cancelled:
                return bool(self._job_cancelled[job_id])

        value = await self._load_job_cancel_flag_from_redis(job_id)
        return bool(value)

    async def get_job_cancelled(self, job_id: str) -> bool:
        return await self.is_job_cancelled(job_id)

    async def job_is_cancelled(self, job_id: str) -> bool:
        return await self.is_job_cancelled(job_id)

    async def set_stream_cancelled(self, stream_id: str, ttl_sec: Optional[int] = None) -> None:
        if not stream_id:
            return

        async with self._lock:
            self._stream_cancelled[stream_id] = True

        await self._set_stream_cancel_flag_to_redis(stream_id, True, ttl_sec=ttl_sec)

    async def mark_stream_cancelled(self, stream_id: str, ttl_sec: Optional[int] = None) -> None:
        await self.set_stream_cancelled(stream_id, ttl_sec=ttl_sec)

    async def is_stream_cancelled(self, stream_id: str) -> bool:
        if not stream_id:
            return False

        async with self._lock:
            if stream_id in self._stream_cancelled:
                return bool(self._stream_cancelled[stream_id])

        value = await self._load_stream_cancel_flag_from_redis(stream_id)
        return bool(value)

    async def get_stream_cancelled(self, stream_id: str) -> bool:
        return await self.is_stream_cancelled(stream_id)

    async def stream_is_cancelled(self, stream_id: str) -> bool:
        return await self.is_stream_cancelled(stream_id)

    async def is_cancelled(self, target_id: str) -> bool:
        # 兼容旧接口，优先按 job 查，再按 stream 查
        if await self.is_job_cancelled(target_id):
            return True
        if await self.is_stream_cancelled(target_id):
            return True
        return False

    async def get_cancel_flag(self, target_id: str) -> bool:
        return await self.is_cancelled(target_id)

    # -------------------------------------------------
    # 读取 stream
    # -------------------------------------------------
    async def get_events(self, stream_id: str, last_offset: Optional[int] = None) -> List[Dict[str, Any]]:
        if not stream_id:
            return []

        async with self._lock:
            stream = self._streams.get(stream_id)
            if stream:
                events = list(stream.get("events") or [])
                if isinstance(last_offset, int) and last_offset >= 0:
                    return events[last_offset:]
                return events

        events = await self._load_stream_events_from_redis(stream_id)
        if isinstance(last_offset, int) and last_offset >= 0:
            return events[last_offset:]
        return events or []

    async def get_stream_events(self, stream_id: str, last_offset: Optional[int] = None) -> List[Dict[str, Any]]:
        return await self.get_events(stream_id, last_offset=last_offset)

    async def read_stream_events(self, stream_id: str, last_offset: Optional[int] = None) -> List[Dict[str, Any]]:
        return await self.get_events(stream_id, last_offset=last_offset)

    async def load_stream_events(self, stream_id: str, last_offset: Optional[int] = None) -> List[Dict[str, Any]]:
        return await self.get_events(stream_id, last_offset=last_offset)

    async def list_events(self, stream_id: str, last_offset: Optional[int] = None) -> List[Dict[str, Any]]:
        return await self.get_events(stream_id, last_offset=last_offset)

    async def get_result(self, stream_id: str) -> Optional[Dict[str, Any]]:
        if not stream_id:
            return None

        async with self._lock:
            stream = self._streams.get(stream_id)
            if stream and isinstance(stream.get("result"), dict):
                return dict(stream.get("result"))

        return await self._load_stream_result_from_redis(stream_id)

    async def read_result(self, stream_id: str) -> Optional[Dict[str, Any]]:
        return await self.get_result(stream_id)

    async def load_result(self, stream_id: str) -> Optional[Dict[str, Any]]:
        return await self.get_result(stream_id)

    async def get_strategy_result(self, stream_id: str) -> Optional[Dict[str, Any]]:
        return await self.get_result(stream_id)

    async def read_strategy_result(self, stream_id: str) -> Optional[Dict[str, Any]]:
        return await self.get_result(stream_id)

    async def get_stream(self, stream_id: str) -> Dict[str, Any]:
        if not stream_id:
            return {
                "stream_id": "",
                "status": "not_found",
                "events": [],
                "result": None,
                "created_at": None,
                "updated_at": None,
                "finished_at": None,
            }

        async with self._lock:
            stream = self._streams.get(stream_id)
            if stream:
                return {
                    "stream_id": stream_id,
                    "status": stream.get("status", "running"),
                    "events": list(stream.get("events") or []),
                    "result": stream.get("result"),
                    "created_at": stream.get("created_at"),
                    "updated_at": stream.get("updated_at"),
                    "finished_at": stream.get("finished_at"),
                }

        meta = await self._load_stream_meta_from_redis(stream_id)
        events = await self._load_stream_events_from_redis(stream_id)
        result = await self._load_stream_result_from_redis(stream_id)

        if not meta and not events and not result:
            return {
                "stream_id": stream_id,
                "status": "not_found",
                "events": [],
                "result": None,
                "created_at": None,
                "updated_at": None,
                "finished_at": None,
            }

        return {
            "stream_id": stream_id,
            "status": (meta or {}).get("status", "running"),
            "events": events or [],
            "result": result,
            "created_at": (meta or {}).get("created_at"),
            "updated_at": (meta or {}).get("updated_at"),
            "finished_at": (meta or {}).get("finished_at"),
        }

    async def stream_exists(self, stream_id: str) -> bool:
        if not stream_id:
            return False

        async with self._lock:
            if stream_id in self._streams:
                return True

        meta = await self._load_stream_meta_from_redis(stream_id)
        return bool(meta)

    # -------------------------------------------------
    # 删除 / 清理
    # -------------------------------------------------
    async def delete_stream(self, stream_id: str) -> None:
        if not stream_id:
            return

        async with self._lock:
            self._streams.pop(stream_id, None)
            self._stream_cancelled.pop(stream_id, None)

        await self._delete_stream_from_redis(stream_id)

    async def delete_job(self, job_id: str) -> None:
        if not job_id:
            return

        async with self._lock:
            self._job_states.pop(job_id, None)
            self._job_cancelled.pop(job_id, None)

        await self._delete_job_from_redis(job_id)

    async def cleanup_expired(self) -> int:
        now = _now_ts()
        removed = 0

        async with self._lock:
            stream_to_delete = []
            for stream_id, stream in self._streams.items():
                updated_at = int(stream.get("updated_at") or 0)
                if updated_at and (now - updated_at) > STRATEGY_STREAM_TTL_SEC:
                    stream_to_delete.append(stream_id)

            for stream_id in stream_to_delete:
                self._streams.pop(stream_id, None)
                self._stream_cancelled.pop(stream_id, None)
                removed += 1

            job_to_delete = []
            for job_id, state in self._job_states.items():
                updated_at = int(state.get("updated_at") or 0)
                if updated_at and (now - updated_at) > STRATEGY_STREAM_TTL_SEC:
                    job_to_delete.append(job_id)

            for job_id in job_to_delete:
                self._job_states.pop(job_id, None)
                self._job_cancelled.pop(job_id, None)

        return removed

    # -------------------------------------------------
    # Redis：stream
    # -------------------------------------------------
    async def _sync_stream_meta_to_redis(self, stream_id: str, ttl_sec: Optional[int] = None) -> None:
        if self._redis is None or not stream_id:
            return

        ttl = int(ttl_sec or STRATEGY_STREAM_TTL_SEC)

        async with self._lock:
            stream = self._streams.get(stream_id)
            if not stream:
                return
            meta = {
                "stream_id": stream_id,
                "status": stream.get("status", "running"),
                "created_at": stream.get("created_at"),
                "updated_at": stream.get("updated_at"),
                "finished_at": stream.get("finished_at"),
            }

        try:
            await self._redis.set(
                _redis_stream_meta_key(stream_id),
                _safe_json_dumps(meta),
                ex=ttl,
            )
        except Exception:
            logger.warning("[strategy.stream_store] sync stream meta to redis failed", exc_info=True)

    async def _append_stream_event_to_redis(self, stream_id: str, event: Dict[str, Any]) -> None:
        if self._redis is None or not stream_id:
            return

        key = _redis_stream_events_key(stream_id)

        try:
            await self._redis.rpush(key, _safe_json_dumps(event))

            try:
                length = await self._redis.llen(key)
                if isinstance(length, int) and length > STRATEGY_STREAM_EVENT_LIMIT:
                    trim_start = length - STRATEGY_STREAM_EVENT_LIMIT
                    await self._redis.ltrim(key, trim_start, -1)
            except Exception:
                logger.warning("[strategy.stream_store] trim redis stream event list failed", exc_info=True)

            await self._redis.expire(key, STRATEGY_STREAM_TTL_SEC)
        except Exception:
            logger.warning("[strategy.stream_store] append stream event to redis failed", exc_info=True)

    async def _set_stream_result_to_redis(self, stream_id: str, result: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        if self._redis is None or not stream_id:
            return

        ttl = int(ttl_sec or STRATEGY_STREAM_TTL_SEC)

        try:
            await self._redis.set(
                _redis_stream_result_key(stream_id),
                _safe_json_dumps(result),
                ex=ttl,
            )
        except Exception:
            logger.warning("[strategy.stream_store] set stream result to redis failed", exc_info=True)

    async def _load_stream_meta_from_redis(self, stream_id: str) -> Optional[Dict[str, Any]]:
        if self._redis is None or not stream_id:
            return None

        try:
            raw = await self._redis.get(_redis_stream_meta_key(stream_id))
            data = _safe_json_loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            logger.warning("[strategy.stream_store] load stream meta from redis failed", exc_info=True)
            return None

    async def _load_stream_events_from_redis(self, stream_id: str) -> List[Dict[str, Any]]:
        if self._redis is None or not stream_id:
            return []

        try:
            rows = await self._redis.lrange(_redis_stream_events_key(stream_id), 0, -1)
            result: List[Dict[str, Any]] = []
            for row in rows or []:
                data = _safe_json_loads(row)
                if isinstance(data, dict):
                    result.append(data)
            return result
        except Exception:
            logger.warning("[strategy.stream_store] load stream events from redis failed", exc_info=True)
            return []

    async def _load_stream_result_from_redis(self, stream_id: str) -> Optional[Dict[str, Any]]:
        if self._redis is None or not stream_id:
            return None

        try:
            raw = await self._redis.get(_redis_stream_result_key(stream_id))
            data = _safe_json_loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            logger.warning("[strategy.stream_store] load stream result from redis failed", exc_info=True)
            return None

    async def _set_stream_cancel_flag_to_redis(self, stream_id: str, value: bool, ttl_sec: Optional[int] = None) -> None:
        if self._redis is None or not stream_id:
            return

        ttl = int(ttl_sec or STRATEGY_STREAM_TTL_SEC)

        try:
            await self._redis.set(_redis_stream_cancel_key(stream_id), "1" if value else "0", ex=ttl)
        except Exception:
            logger.warning("[strategy.stream_store] set stream cancel flag to redis failed", exc_info=True)

    async def _load_stream_cancel_flag_from_redis(self, stream_id: str) -> bool:
        if self._redis is None or not stream_id:
            return False

        try:
            raw = await self._redis.get(_redis_stream_cancel_key(stream_id))
            if raw is None:
                return False
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")
            return str(raw).strip() in {"1", "true", "True"}
        except Exception:
            logger.warning("[strategy.stream_store] load stream cancel flag from redis failed", exc_info=True)
            return False

    async def _delete_stream_from_redis(self, stream_id: str) -> None:
        if self._redis is None or not stream_id:
            return

        try:
            await self._redis.delete(
                _redis_stream_meta_key(stream_id),
                _redis_stream_events_key(stream_id),
                _redis_stream_result_key(stream_id),
                _redis_stream_cancel_key(stream_id),
            )
        except Exception:
            logger.warning("[strategy.stream_store] delete stream from redis failed", exc_info=True)

    # -------------------------------------------------
    # Redis：job
    # -------------------------------------------------
    async def _set_job_state_to_redis(self, job_id: str, payload: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
        if self._redis is None or not job_id:
            return

        ttl = int(ttl_sec or STRATEGY_STREAM_TTL_SEC)

        try:
            await self._redis.set(
                _redis_job_state_key(job_id),
                _safe_json_dumps(payload),
                ex=ttl,
            )
        except Exception:
            logger.warning("[strategy.stream_store] set job state to redis failed", exc_info=True)

    async def _load_job_state_from_redis(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self._redis is None or not job_id:
            return None

        try:
            raw = await self._redis.get(_redis_job_state_key(job_id))
            data = _safe_json_loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            logger.warning("[strategy.stream_store] load job state from redis failed", exc_info=True)
            return None

    async def _set_job_cancel_flag_to_redis(self, job_id: str, value: bool, ttl_sec: Optional[int] = None) -> None:
        if self._redis is None or not job_id:
            return

        ttl = int(ttl_sec or STRATEGY_STREAM_TTL_SEC)

        try:
            await self._redis.set(_redis_job_cancel_key(job_id), "1" if value else "0", ex=ttl)
        except Exception:
            logger.warning("[strategy.stream_store] set job cancel flag to redis failed", exc_info=True)

    async def _load_job_cancel_flag_from_redis(self, job_id: str) -> bool:
        if self._redis is None or not job_id:
            return False

        try:
            raw = await self._redis.get(_redis_job_cancel_key(job_id))
            if raw is None:
                return False
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")
            return str(raw).strip() in {"1", "true", "True"}
        except Exception:
            logger.warning("[strategy.stream_store] load job cancel flag from redis failed", exc_info=True)
            return False

    async def _delete_job_from_redis(self, job_id: str) -> None:
        if self._redis is None or not job_id:
            return

        try:
            await self._redis.delete(
                _redis_job_state_key(job_id),
                _redis_job_cancel_key(job_id),
            )
        except Exception:
            logger.warning("[strategy.stream_store] delete job from redis failed", exc_info=True)


# =====================================================
# 单例
# =====================================================

strategy_stream_store = StrategyStreamStore()


# =====================================================
# 模块级兼容函数
# =====================================================

async def ensure_stream(stream_id: str) -> None:
    await strategy_stream_store.ensure_stream(stream_id)


async def create_stream(stream_id: str) -> None:
    await strategy_stream_store.create_stream(stream_id)


async def append_stream_event(stream_id: str, event: Dict[str, Any]) -> None:
    await strategy_stream_store.append_stream_event(stream_id, event)


async def append_event(stream_id: str, event: Dict[str, Any]) -> None:
    await strategy_stream_store.append_event(stream_id, event)


async def push_event(stream_id: str, event: Dict[str, Any]) -> None:
    await strategy_stream_store.push_event(stream_id, event)


async def add_event(stream_id: str, event: Dict[str, Any]) -> None:
    await strategy_stream_store.add_event(stream_id, event)


async def emit_event(stream_id: str, event: Dict[str, Any]) -> None:
    await strategy_stream_store.emit_event(stream_id, event)


async def set_result(stream_id: str, result: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.set_result(stream_id, result, ttl_sec=ttl_sec)


async def save_result(stream_id: str, result: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.save_result(stream_id, result, ttl_sec=ttl_sec)


async def set_strategy_result(stream_id: str, result: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.set_strategy_result(stream_id, result, ttl_sec=ttl_sec)


async def save_strategy_result(stream_id: str, result: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.save_strategy_result(stream_id, result, ttl_sec=ttl_sec)


async def get_result(stream_id: str) -> Optional[Dict[str, Any]]:
    return await strategy_stream_store.get_result(stream_id)


async def read_result(stream_id: str) -> Optional[Dict[str, Any]]:
    return await strategy_stream_store.read_result(stream_id)


async def load_result(stream_id: str) -> Optional[Dict[str, Any]]:
    return await strategy_stream_store.load_result(stream_id)


async def get_strategy_result(stream_id: str) -> Optional[Dict[str, Any]]:
    return await strategy_stream_store.get_strategy_result(stream_id)


async def read_strategy_result(stream_id: str) -> Optional[Dict[str, Any]]:
    return await strategy_stream_store.read_strategy_result(stream_id)


async def get_stream(stream_id: str) -> Dict[str, Any]:
    return await strategy_stream_store.get_stream(stream_id)


async def get_stream_events(stream_id: str, last_offset: Optional[int] = None) -> List[Dict[str, Any]]:
    return await strategy_stream_store.get_stream_events(stream_id, last_offset=last_offset)


async def read_stream_events(stream_id: str, last_offset: Optional[int] = None) -> List[Dict[str, Any]]:
    return await strategy_stream_store.read_stream_events(stream_id, last_offset=last_offset)


async def load_stream_events(stream_id: str, last_offset: Optional[int] = None) -> List[Dict[str, Any]]:
    return await strategy_stream_store.load_stream_events(stream_id, last_offset=last_offset)


async def list_events(stream_id: str, last_offset: Optional[int] = None) -> List[Dict[str, Any]]:
    return await strategy_stream_store.list_events(stream_id, last_offset=last_offset)


async def set_job_state(job_id: str, payload: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.set_job_state(job_id, payload, ttl_sec=ttl_sec)


async def update_job_state(job_id: str, payload: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.update_job_state(job_id, payload, ttl_sec=ttl_sec)


async def save_job_state(job_id: str, payload: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.save_job_state(job_id, payload, ttl_sec=ttl_sec)


async def set_state(job_id: str, payload: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.set_state(job_id, payload, ttl_sec=ttl_sec)


async def update_state(job_id: str, payload: Dict[str, Any], ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.update_state(job_id, payload, ttl_sec=ttl_sec)


async def get_job_state(job_id: str) -> Dict[str, Any]:
    return await strategy_stream_store.get_job_state(job_id)


async def read_job_state(job_id: str) -> Dict[str, Any]:
    return await strategy_stream_store.read_job_state(job_id)


async def load_job_state(job_id: str) -> Dict[str, Any]:
    return await strategy_stream_store.load_job_state(job_id)


async def get_state(job_id: str) -> Dict[str, Any]:
    return await strategy_stream_store.get_state(job_id)


async def touch_heartbeat(job_id: str, payload: Optional[Dict[str, Any]] = None, ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.touch_heartbeat(job_id, payload, ttl_sec=ttl_sec)


async def update_heartbeat(job_id: str, payload: Optional[Dict[str, Any]] = None, ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.update_heartbeat(job_id, payload, ttl_sec=ttl_sec)


async def save_heartbeat(job_id: str, payload: Optional[Dict[str, Any]] = None, ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.save_heartbeat(job_id, payload, ttl_sec=ttl_sec)


async def set_heartbeat(job_id: str, payload: Optional[Dict[str, Any]] = None, ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.set_heartbeat(job_id, payload, ttl_sec=ttl_sec)


async def set_job_cancelled(job_id: str, ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.set_job_cancelled(job_id, ttl_sec=ttl_sec)


async def get_job_cancelled(job_id: str) -> bool:
    return await strategy_stream_store.get_job_cancelled(job_id)


async def is_job_cancelled(job_id: str) -> bool:
    return await strategy_stream_store.is_job_cancelled(job_id)


async def job_is_cancelled(job_id: str) -> bool:
    return await strategy_stream_store.job_is_cancelled(job_id)


async def set_stream_cancelled(stream_id: str, ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.set_stream_cancelled(stream_id, ttl_sec=ttl_sec)


async def mark_stream_cancelled(stream_id: str, ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.mark_stream_cancelled(stream_id, ttl_sec=ttl_sec)


async def get_stream_cancelled(stream_id: str) -> bool:
    return await strategy_stream_store.get_stream_cancelled(stream_id)


async def is_stream_cancelled(stream_id: str) -> bool:
    return await strategy_stream_store.is_stream_cancelled(stream_id)


async def stream_is_cancelled(stream_id: str) -> bool:
    return await strategy_stream_store.stream_is_cancelled(stream_id)


async def is_cancelled(target_id: str) -> bool:
    return await strategy_stream_store.is_cancelled(target_id)


async def get_cancel_flag(target_id: str) -> bool:
    return await strategy_stream_store.get_cancel_flag(target_id)


async def set_cancelled(target_id: str, ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.set_cancelled(target_id, ttl_sec=ttl_sec)


async def mark_cancelled(target_id: str, ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.mark_cancelled(target_id, ttl_sec=ttl_sec)


async def set_cancel_flag(target_id: str, ttl_sec: Optional[int] = None) -> None:
    await strategy_stream_store.set_cancel_flag(target_id, ttl_sec=ttl_sec)


async def stream_exists(stream_id: str) -> bool:
    return await strategy_stream_store.stream_exists(stream_id)


async def delete_stream(stream_id: str) -> None:
    await strategy_stream_store.delete_stream(stream_id)


async def delete_job(job_id: str) -> None:
    await strategy_stream_store.delete_job(job_id)


async def cleanup_expired() -> int:
    return await strategy_stream_store.cleanup_expired()