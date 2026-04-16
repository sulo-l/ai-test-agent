# app/testcase_app/stream_store.py
# -*- coding: utf-8 -*-

import os
import json
import time
import uuid
import logging
import asyncio
from typing import Any, Dict, List, Tuple, Optional, AsyncGenerator

from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import ConnectionError as RedisConnectionError

from app.infra.redis_client import get_redis, get_redis_blocking

logger = logging.getLogger(__name__)

# =========================
# Config
# =========================
STREAM_PREFIX = os.getenv("TC_STREAM_PREFIX", "tc:stream:")
CANCEL_PREFIX = os.getenv("TC_CANCEL_PREFIX", "tc:cancel:")

STREAM_MAXLEN = int(os.getenv("TC_STREAM_MAXLEN", "4000"))
STREAM_TTL_SEC = int(os.getenv("TC_STREAM_TTL_SEC", "3600"))

DEFAULT_BLOCK_MS = int(os.getenv("TC_XREAD_BLOCK_MS", "1000"))
DEFAULT_COUNT = int(os.getenv("TC_XREAD_COUNT", "50"))

DEFAULT_PEEK_N = int(os.getenv("TC_PEEK_N", "30"))

# 断连/读空时退避，避免 read_forever 空转刷屏
READ_EMPTY_BACKOFF_SEC = float(os.getenv("TC_READ_EMPTY_BACKOFF_SEC", "0.2"))

# 心跳节流：读空时，至少间隔多少秒才发一次 heartbeat
HEARTBEAT_THROTTLE_SEC = float(os.getenv("TC_HEARTBEAT_THROTTLE_SEC", "2.0"))


# =========================
# Key helpers
# =========================
def stream_key(stream_id: str) -> str:
    return f"{STREAM_PREFIX}{stream_id}"


def cancel_key(stream_id: str) -> str:
    return f"{CANCEL_PREFIX}{stream_id}"


# =========================
# Stream id helper
# =========================
async def create_stream() -> str:
    return uuid.uuid4().hex


# =========================
# JSON helpers
# =========================
def _to_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="ignore")
    return str(v)


def _ensure_dict(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _json_dumps(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        try:
            return json.dumps(str(payload), ensure_ascii=False)
        except Exception:
            return '{"type":"error","data":"json serialize failed"}'


def _json_loads(s: Any) -> Any:
    try:
        ss = _to_str(s)
        return json.loads(ss)
    except Exception:
        return {"type": "error", "data": f"invalid json payload: {_to_str(s)[:200]}"}


def _get_payload(fields: Any) -> Any:
    if not isinstance(fields, dict):
        return None
    return fields.get("payload") or fields.get(b"payload")


def _attach_id(ev: Any, msg_id: str) -> Dict[str, Any]:
    """
    给事件附加 Redis Stream msg_id，方便 WS last_id 推进与调试
    """
    if isinstance(ev, dict):
        if "_id" not in ev:
            ev["_id"] = msg_id
        return ev
    return {"type": "log", "data": str(ev), "_id": msg_id}


def _ensure_event_dict(event: Any) -> Dict[str, Any]:
    """
    强健：保证写入 stream 的永远是 dict
    """
    if isinstance(event, dict):
        return event
    return {"type": "log", "data": str(event)}


def _normalize_stage_event_data(data: Any) -> Dict[str, Any]:
    d = _ensure_dict(data)
    d.setdefault("stage", "")
    d.setdefault("status", "")
    d.setdefault("title", "")
    d.setdefault("message", "")
    d.setdefault("progress", 0)
    d.setdefault("duration_ms", 0)
    d.setdefault("started_at", None)
    d.setdefault("ended_at", None)
    if "extra" not in d or not isinstance(d.get("extra"), dict):
        d["extra"] = {}
    return d


def _normalize_metric_data(data: Any) -> Dict[str, Any]:
    d = _ensure_dict(data)
    d.setdefault("stage", "")
    d.setdefault("duration_ms", 0)
    d.setdefault("input_count", 0)
    d.setdefault("output_count", 0)
    if "extra" not in d or not isinstance(d.get("extra"), dict):
        d["extra"] = {}

    for k in ("duration_ms", "input_count", "output_count"):
        try:
            d[k] = int(d.get(k, 0))
        except Exception:
            d[k] = 0
    return d


def _normalize_result_data(data: Any) -> Dict[str, Any]:
    return _ensure_dict(data)


def _normalize_event_for_store(event: Any) -> Dict[str, Any]:
    """
    统一事件结构，保证 ts / type 稳定。
    """
    ev = _ensure_event_dict(event)

    if "ts" not in ev:
        ev["ts"] = int(time.time() * 1000)

    if not str(ev.get("type") or "").strip():
        ev["type"] = "unknown"

    et = str(ev.get("type") or "").strip().lower()

    if et == "stage_event":
        ev["data"] = _normalize_stage_event_data(ev.get("data"))

    elif et == "progress":
        if "extra" in ev and not isinstance(ev.get("extra"), dict):
            ev["extra"] = {}
        elif "extra" not in ev:
            ev["extra"] = {}

    elif et == "metric":
        ev["data"] = _normalize_metric_data(ev.get("data"))

    elif et in {
        "analysis_result",
        "design_result",
        "review_result",
        "refine_result",
        "download",
        "final_result",
        "final_summary",
        "pipeline_summary",
    }:
        ev["data"] = _normalize_result_data(ev.get("data"))

    elif et == "stage":
        if "extra" in ev and not isinstance(ev.get("extra"), dict):
            ev["extra"] = {}
        elif "extra" not in ev:
            ev["extra"] = {}

    elif et == "error":
        if "extra" in ev and not isinstance(ev.get("extra"), dict):
            ev["extra"] = {}
        elif "extra" not in ev:
            ev["extra"] = {}

    return ev


# =========================
# Emit events (XADD)
# =========================
async def emit(stream_id: str, event: Dict[str, Any]) -> str:
    """
    写 redis stream。
    返回 msg_id；失败返回 ""。
    """
    r = get_redis()
    event = _normalize_event_for_store(event)
    payload = _json_dumps(event)

    try:
        msg_id = await r.xadd(
            stream_key(stream_id),
            {"payload": payload},
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
    except (RedisTimeoutError, RedisConnectionError, OSError) as e:
        logger.warning("stream_store.emit xadd failed | stream_id=%s | err=%s", stream_id, str(e))
        return ""
    except Exception as e:
        logger.error("stream_store.emit xadd failed | stream_id=%s | err=%s", stream_id, str(e), exc_info=True)
        return ""

    try:
        await r.expire(stream_key(stream_id), STREAM_TTL_SEC)
    except Exception:
        logger.debug("stream_store.emit expire failed (ignored) | stream_id=%s", stream_id)

    return _to_str(msg_id)


async def emit_stage(stream_id: str, stage: str, extra: Optional[Dict[str, Any]] = None) -> str:
    event: Dict[str, Any] = {
        "type": "stage",
        "data": stage,
    }
    if extra:
        event["extra"] = _ensure_dict(extra)
    return await emit(stream_id, event)


async def emit_error(stream_id: str, err: Any, extra: Optional[Dict[str, Any]] = None) -> str:
    event: Dict[str, Any] = {
        "type": "error",
        "data": err,
    }
    if extra:
        event["extra"] = _ensure_dict(extra)
    return await emit(stream_id, event)


async def emit_heartbeat(stream_id: str, extra: Optional[Dict[str, Any]] = None) -> str:
    event: Dict[str, Any] = {
        "type": "heartbeat",
        "data": "ping",
    }
    if extra:
        event["extra"] = _ensure_dict(extra)
    return await emit(stream_id, event)


# =========================
# UI-friendly helper emits
# =========================
async def emit_stage_event(
    stream_id: str,
    stage: str,
    status: str,
    title: str,
    message: str = "",
    extra: Optional[Dict[str, Any]] = None,
    progress: int = 0,
    duration_ms: int = 0,
    started_at: Optional[int] = None,
    ended_at: Optional[int] = None,
) -> str:
    """
    给前端 UI 用的阶段事件（开始/进行中/完成 + 详细内容）
    新协议统一支持：
    - status: pending / running / completed / error
    """
    data: Dict[str, Any] = {
        "stage": stage,
        "status": status,
        "title": title,
        "message": message,
        "progress": int(progress) if isinstance(progress, (int, float)) else 0,
        "duration_ms": int(duration_ms) if isinstance(duration_ms, (int, float)) else 0,
        "started_at": started_at,
        "ended_at": ended_at,
        "extra": _ensure_dict(extra),
    }
    return await emit(stream_id, {"type": "stage_event", "data": data})


async def emit_progress(
    stream_id: str,
    code: str,
    message: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    payload: Dict[str, Any] = {
        "type": "progress",
        "data": code,
        "extra": {},
    }
    if message:
        payload["extra"]["message"] = message
    if extra:
        payload["extra"].update(_ensure_dict(extra))
    return await emit(stream_id, payload)


async def emit_metric(
    stream_id: str,
    stage: str,
    duration_ms: int,
    input_count: int = 0,
    output_count: int = 0,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """
    新协议 metric:
    {
      "type": "metric",
      "data": {
        "stage": "...",
        "duration_ms": 123,
        "input_count": 1,
        "output_count": 10,
        "extra": {...}
      }
    }
    """
    data: Dict[str, Any] = {
        "stage": stage,
        "duration_ms": int(duration_ms),
        "input_count": int(input_count),
        "output_count": int(output_count),
        "extra": _ensure_dict(extra),
    }
    return await emit(stream_id, {"type": "metric", "data": data})


async def emit_analysis_result(
    stream_id: str,
    data: Dict[str, Any],
) -> str:
    return await emit(stream_id, {"type": "analysis_result", "data": _ensure_dict(data)})


async def emit_design_result(
    stream_id: str,
    data: Dict[str, Any],
) -> str:
    return await emit(stream_id, {"type": "design_result", "data": _ensure_dict(data)})


async def emit_review_result(
    stream_id: str,
    data: Dict[str, Any],
) -> str:
    return await emit(stream_id, {"type": "review_result", "data": _ensure_dict(data)})


async def emit_refine_result(
    stream_id: str,
    data: Dict[str, Any],
) -> str:
    return await emit(stream_id, {"type": "refine_result", "data": _ensure_dict(data)})


async def emit_final(
    stream_id: str,
    test_point_count: int,
    testcase_count: int,
    *,
    download_ready: bool = False,
    file_id: Optional[str] = None,
    filename: Optional[str] = None,
    download_url: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """
    旧 final 兼容保留
    """
    data: Dict[str, Any] = {
        "test_point_count": int(test_point_count),
        "testcase_count": int(testcase_count),
        "download_ready": bool(download_ready),
        "file_id": file_id,
        "filename": filename,
        "download_url": download_url,
    }
    ex = _ensure_dict(extra)
    if ex:
        for k, v in ex.items():
            if k not in data:
                data[k] = v
    return await emit(stream_id, {"type": "final", "data": data})


async def emit_download(
    stream_id: str,
    file_id: str,
    filename: str,
    download_url: Optional[str] = None,
    ready: bool = True,
    error: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    payload: Dict[str, Any] = {
        "type": "download",
        "data": {
            "ready": bool(ready),
            "file_id": file_id,
            "filename": filename,
            "download_url": download_url,
            "error": error,
        },
    }
    ex = _ensure_dict(extra)
    if ex:
        payload["data"]["extra"] = ex
    return await emit(stream_id, payload)


async def emit_pipeline_summary(
    stream_id: str,
    summary: Dict[str, Any],
) -> str:
    return await emit(stream_id, {"type": "pipeline_summary", "data": _ensure_dict(summary)})


async def emit_final_result(
    stream_id: str,
    data: Dict[str, Any],
) -> str:
    return await emit(stream_id, {"type": "final_result", "data": _ensure_dict(data)})


async def emit_final_summary(
    stream_id: str,
    data: Dict[str, Any],
) -> str:
    return await emit(stream_id, {"type": "final_summary", "data": _ensure_dict(data)})


# =========================
# Cancel flags
# =========================
async def set_cancel(stream_id: str, reason: str = "user_cancelled") -> None:
    r = get_redis()
    try:
        await r.set(cancel_key(stream_id), "1", ex=STREAM_TTL_SEC)
    except Exception:
        logger.warning("set_cancel redis.set failed (ignored) | stream_id=%s", stream_id)

    await emit_stage(stream_id, "CANCEL_SIGNALLED", {"reason": reason})


async def is_cancelled(stream_id: str) -> bool:
    r = get_redis()
    try:
        return (await r.exists(cancel_key(stream_id))) == 1
    except Exception:
        # Redis 挂了就当未取消，避免误杀任务
        return False


async def clear_cancel(stream_id: str) -> None:
    r = get_redis()
    try:
        await r.delete(cancel_key(stream_id))
    except Exception:
        pass


# =========================
# Read events (XREAD)
# =========================
async def read_batch(
    stream_id: str,
    last_id: str,
    block_ms: int = DEFAULT_BLOCK_MS,
    count: int = DEFAULT_COUNT,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    从 Redis Stream 读取一批事件，返回 (new_last_id, events)
    """
    rb = get_redis_blocking()
    key = stream_key(stream_id)

    if not last_id:
        last_id = "0-0"

    if block_ms is None or block_ms < 0:
        block_ms = DEFAULT_BLOCK_MS

    try:
        resp = await rb.xread({key: last_id}, block=block_ms, count=count)

    except asyncio.CancelledError:
        raise

    except (asyncio.TimeoutError, RedisTimeoutError):
        return last_id, []

    except (RedisConnectionError, OSError) as e:
        logger.warning("Redis connection issue on stream=%s: %s", stream_id, str(e))
        return last_id, []

    except Exception as e:
        logger.error("Error while reading from Redis stream %s: %s", stream_id, str(e), exc_info=True)
        return last_id, []

    if not resp:
        return last_id, []

    _, messages = resp[0]

    events: List[Dict[str, Any]] = []
    new_last_id = last_id

    for msg_id, fields in messages:
        mid = _to_str(msg_id)
        new_last_id = mid

        payload = _get_payload(fields)
        if payload is None:
            continue

        ev = _json_loads(payload)
        ev = _normalize_event_for_store(ev)
        events.append(_attach_id(ev, mid))

    return new_last_id, events


async def read_forever(
    stream_id: str,
    last_id: str = "0-0",
    block_ms: int = DEFAULT_BLOCK_MS,
    count: int = DEFAULT_COUNT,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    异步生成器：持续读取事件
    无事件/断连时 backoff + 心跳节流
    """
    cur = last_id or "0-0"
    last_hb_ts = 0.0

    while True:
        cur, events = await read_batch(stream_id, cur, block_ms=block_ms, count=count)

        if not events:
            now = time.time()

            if HEARTBEAT_THROTTLE_SEC <= 0 or (now - last_hb_ts >= HEARTBEAT_THROTTLE_SEC):
                last_hb_ts = now
                yield {"type": "heartbeat", "ts": int(now * 1000), "data": "ping"}

            if READ_EMPTY_BACKOFF_SEC > 0:
                await asyncio.sleep(READ_EMPTY_BACKOFF_SEC)
            continue

        for ev in events:
            yield ev


# =========================
# Tail helpers
# =========================
async def peek_tail(stream_id: str, count: int = DEFAULT_PEEK_N) -> Tuple[str, List[Dict[str, Any]]]:
    """
    取 stream 最后 N 条（按时间正序返回）
    返回：(last_msg_id, events)
    """
    r = get_redis()
    key = stream_key(stream_id)

    try:
        raw = await r.xrevrange(key, max="+", min="-", count=count)
    except Exception:
        return "0-0", []

    if not raw:
        return "0-0", []

    raw.reverse()

    events: List[Dict[str, Any]] = []
    last_msg_id = "0-0"

    for msg_id, fields in raw:
        mid = _to_str(msg_id)
        last_msg_id = mid

        payload = _get_payload(fields)
        if payload is None:
            continue

        ev = _json_loads(payload)
        ev = _normalize_event_for_store(ev)
        events.append(_attach_id(ev, mid))

    return last_msg_id, events


# =========================
# Debug helpers
# =========================
async def xlen(stream_id: str) -> int:
    r = get_redis()
    try:
        return int(await r.xlen(stream_key(stream_id)))
    except Exception:
        return 0


async def peek_last(stream_id: str, n: int = DEFAULT_PEEK_N) -> List[Dict[str, Any]]:
    r = get_redis()
    key = stream_key(stream_id)

    try:
        rows = await r.xrevrange(key, count=n)
        out: List[Dict[str, Any]] = []
        for msg_id, fields in rows:
            mid = _to_str(msg_id)
            payload = _get_payload(fields)
            if payload is None:
                continue
            ev = _json_loads(payload)
            ev = _normalize_event_for_store(ev)
            out.append(_attach_id(ev, mid))
        out.reverse()
        return out
    except Exception:
        return []


# =========================
# Maintenance helpers
# =========================
async def trim_stream(stream_id: str, maxlen: int = STREAM_MAXLEN) -> None:
    r = get_redis()
    try:
        await r.xtrim(stream_key(stream_id), maxlen=maxlen, approximate=True)
    except Exception:
        pass


async def delete_stream(stream_id: str) -> None:
    r = get_redis()
    try:
        await r.delete(stream_key(stream_id))
        await r.delete(cancel_key(stream_id))
    except Exception:
        pass