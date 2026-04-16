# -*- coding: utf-8 -*-
"""
app/testcase_app/ws.py

目标：
- 不依赖 meta 校验（避免 /run 未写 meta 导致 subscribe 直接 invalid）
- subscribe 只读 Redis Stream（stream_store），并补发 tail
- 心跳事件统一：{"type":"heartbeat","data":"ping","ts":...}
- 修复：Redis 断连/无事件时避免空转（backoff）
- 修复：终态判断更全（stage / stage_event / final_result / error）
- 修复：bootstrap/读流事件去重（按 _id；无 _id 时弱去重）
- 兼容新版结构化事件：
  - connected
  - heartbeat
  - stage
  - stage_event
  - metric
  - progress
  - analysis_result
  - design_result
  - review_result
  - refine_result
  - download
  - final_result
  - final_summary
  - error
"""

import asyncio
import json
import time
import uuid
import os
import logging
import hashlib
from typing import Dict, AsyncGenerator, Any, List, Set, Tuple

from app.testcase_app import stream_store

logger = logging.getLogger(__name__)

# WS subscribe 参数（尽量短 block 更稳）
WS_BLOCK_MS = int(os.getenv("TC_WS_BLOCK_MS", "1000"))
WS_COUNT = int(os.getenv("TC_WS_COUNT", "50"))
WS_BOOTSTRAP_TAIL = int(os.getenv("TC_WS_BOOTSTRAP_TAIL", "20"))
WS_HEARTBEAT_SEC = float(os.getenv("TC_WS_HEARTBEAT_SEC", "10.0"))

# 空事件/断连退避，避免 subscribe 空转
WS_EMPTY_BACKOFF_SEC = float(os.getenv("TC_WS_EMPTY_BACKOFF_SEC", "0.1"))

# 去重窗口（只记最近 N 个 key）
WS_DEDUP_WINDOW = int(os.getenv("TC_WS_DEDUP_WINDOW", "3000"))

# 终态后再补等一会，尽量让 final_result / final_summary / download 一起发完
WS_TERMINAL_GRACE_SEC = float(os.getenv("TC_WS_TERMINAL_GRACE_SEC", "1.2"))

# bootstrap 最多补发的终态后续事件数
WS_TERMINAL_DRAIN_BATCH = int(os.getenv("TC_WS_TERMINAL_DRAIN_BATCH", "100"))


def _now_ts() -> int:
    return int(time.time())


def _safe_json_dumps(v: Any) -> str:
    try:
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(v)


def _weak_key(ev: Dict[str, Any]) -> str:
    """
    当没有 _id 时，做一个弱去重 key（避免 bootstrap + read 重复刷）
    """
    try:
        t = str(ev.get("type", ""))
        ts = str(ev.get("ts", ""))
        data = ev.get("data", "")
        d = _safe_json_dumps(data)
        extra = _safe_json_dumps(ev.get("extra", ""))
        raw = f"{t}|{ts}|{d}|{extra}"[:4000]
        return hashlib.md5(raw.encode("utf-8", errors="ignore")).hexdigest()
    except Exception:
        return uuid.uuid4().hex


def _normalize_event(ev: Dict[str, Any]) -> Dict[str, Any]:
    """
    统一补齐字段，避免前端解析时字段缺失。
    """
    if not isinstance(ev, dict):
        return {"type": "error", "data": f"invalid event type: {type(ev)}", "ts": _now_ts()}

    out = dict(ev)

    if "type" not in out or not str(out.get("type") or "").strip():
        out["type"] = "unknown"

    if "ts" not in out:
        out["ts"] = _now_ts()

    et = str(out.get("type") or "").strip().lower()

    if et == "stage_event":
        data = out.get("data")
        if not isinstance(data, dict):
            data = {}
        data.setdefault("stage", "")
        data.setdefault("status", "")
        data.setdefault("title", "")
        data.setdefault("message", "")
        data.setdefault("progress", 0)
        data.setdefault("extra", {})
        out["data"] = data

    elif et == "progress":
        extra = out.get("extra")
        if not isinstance(extra, dict):
            extra = {}
        if "percent" in extra:
            try:
                extra["percent"] = max(0, min(100, int(extra["percent"])))
            except Exception:
                extra["percent"] = 0
        out["extra"] = extra

    elif et == "metric":
        data = out.get("data")
        if not isinstance(data, dict):
            data = {}
        if "duration_ms" in data:
            try:
                data["duration_ms"] = int(data["duration_ms"])
            except Exception:
                pass
        if "input_count" in data:
            try:
                data["input_count"] = int(data["input_count"])
            except Exception:
                pass
        if "output_count" in data:
            try:
                data["output_count"] = int(data["output_count"])
            except Exception:
                pass
        out["data"] = data

    elif et in {
        "analysis_result",
        "design_result",
        "review_result",
        "refine_result",
        "download",
        "final_result",
        "final_summary",
    }:
        data = out.get("data")
        if not isinstance(data, dict):
            data = {}
        out["data"] = data

    return out


class WebSocketStreamManager:
    """
    Redis Streams 版 WebSocketStreamManager

    对外接口：
    - create_stream()  -> 生成 stream_id（仅生成，不做 meta）
    - publish()        -> 写入 Redis Stream
    - close()          -> 写终态 stage DONE
    - subscribe()      -> yield JSON string（适合 websocket.send_text）
    """

    async def create_stream(self) -> str:
        return uuid.uuid4().hex

    async def publish(self, stream_id: str, event: Dict[str, Any]):
        if not isinstance(event, dict):
            logger.error("publish event must be dict, got=%s", type(event))
            return

        event = _normalize_event(event)
        await stream_store.emit(stream_id, event)

    async def close(self, stream_id: str):
        """
        主动补一个终态 stage。
        """
        try:
            emit_stage = getattr(stream_store, "emit_stage", None)
            if callable(emit_stage):
                await emit_stage(stream_id, "DONE")
                return
        except Exception:
            logger.warning("stream_store.emit_stage failed in close, stream_id=%s", stream_id, exc_info=True)

        await stream_store.emit(stream_id, {"type": "stage", "data": "DONE", "ts": _now_ts()})

    async def subscribe(self, stream_id: str) -> AsyncGenerator[str, None]:
        """
        不做 meta 校验：允许“先连 ws 再 run”
        bootstrap tail，避免漏掉关键事件
        空事件/断连退避，避免 CPU 空转
        去重：优先 _id；无 _id 时用弱 key
        终态：stage / stage_event / final_result / error 都能触发退出
        """
        logger.info("Subscribed to stream %s (redis).", stream_id)

        yield self._format_event({
            "type": "connected",
            "stream_id": stream_id,
            "ts": _now_ts(),
        })

        last_id = "0-0"
        last_hb_ts = 0.0

        seen_keys: List[str] = []
        seen_set: Set[str] = set()

        def _mark_seen(key: str) -> bool:
            """
            True=新事件；False=重复
            """
            if not key:
                return True
            if key in seen_set:
                return False
            seen_set.add(key)
            seen_keys.append(key)
            if len(seen_keys) > WS_DEDUP_WINDOW:
                old = seen_keys.pop(0)
                seen_set.discard(old)
            return True

        TERMINAL_STAGES = {
            "DONE",
            "ERROR",
            "CANCELLED",
            "CANCELLED_BEFORE_START",
            "PIPELINE_DONE",
            "ANALYSIS_PIPELINE_DONE",
            "FINISHED",
        }

        TERMINAL_STAGE_EVENT_STATUS = {"completed", "error", "done"}
        TERMINAL_STAGE_EVENT_STAGE = {
            "finished",
            "export_testcases",
            "export",
            "done",
        }

        def _is_terminal_event(ev: Dict[str, Any]) -> bool:
            """
            只要满足任意条件就认为到达终态。
            """
            et = str(ev.get("type") or "").lower()

            # 1) 旧 stage 协议
            if et == "stage":
                st = str(ev.get("data", "")).upper()
                return st in TERMINAL_STAGES

            # 2) 新 stage_event 协议
            if et == "stage_event":
                data = ev.get("data") or {}
                if isinstance(data, dict):
                    status = str(data.get("status") or "").lower()
                    stage = str(data.get("stage") or "").lower()

                    if stage in TERMINAL_STAGE_EVENT_STAGE and status in TERMINAL_STAGE_EVENT_STATUS:
                        return True

                    if stage == "finished":
                        return True

                return False

            # 3) 新 final_result 协议
            if et == "final_result":
                return True

            # 4) 兼容旧 final
            if et == "final":
                return True

            # 5) final_summary 不是终态触发器，但通常是终态后补充，不单独结束
            if et == "final_summary":
                return False

            # 6) 明确 error
            if et == "error":
                data = str(ev.get("data") or "").strip().upper()
                if (
                    "PIPELINE_RUNTIME_ERROR" in data
                    or "REQUIREMENT_TEXT_EMPTY_OR_TOO_SHORT" in data
                    or "测试用例流水线执行失败" in data
                ):
                    return True
                return False

            return False

        def _is_interesting_event(ev: Dict[str, Any]) -> bool:
            """
            当前全部下发，不做裁剪。
            """
            return isinstance(ev, dict) and bool(ev.get("type"))

        async def _drain_after_terminal(current_last_id: str) -> Tuple[str, List[Dict[str, Any]]]:
            """
            终态后短暂再读一轮，把 final_result / final_summary / download 尽量补全。
            """
            drain_last_id = current_last_id
            merged: List[Dict[str, Any]] = []
            deadline = time.time() + max(0.1, WS_TERMINAL_GRACE_SEC)

            while time.time() < deadline:
                try:
                    drain_last_id, events = await stream_store.read_batch(
                        stream_id=stream_id,
                        last_id=drain_last_id,
                        block_ms=min(300, WS_BLOCK_MS),
                        count=WS_TERMINAL_DRAIN_BATCH,
                    )
                except Exception:
                    break

                if not events:
                    await asyncio.sleep(0.05)
                    continue

                merged.extend(events)

            return drain_last_id, merged

        # -------------------------------------------------
        # 1) bootstrap：补发 tail，并把 last_id 移到最后一条
        # -------------------------------------------------
        try:
            if WS_BOOTSTRAP_TAIL > 0:
                tail_id, tail_events = await stream_store.peek_tail(stream_id, WS_BOOTSTRAP_TAIL)

                for ev in tail_events:
                    ev = _normalize_event(ev)

                    if not _is_interesting_event(ev):
                        continue

                    key = str(ev.get("_id") or "") or _weak_key(ev)
                    if not _mark_seen(key):
                        continue

                    yield self._format_event(ev)

                    if _is_terminal_event(ev):
                        logger.info("Stream %s terminal reached during bootstrap: %s", stream_id, ev.get("type"))

                        try:
                            drain_last_id, more_events = await _drain_after_terminal(tail_id or last_id)
                            for e2 in more_events:
                                e2 = _normalize_event(e2)
                                if not _is_interesting_event(e2):
                                    continue
                                key2 = str(e2.get("_id") or "") or _weak_key(e2)
                                if not _mark_seen(key2):
                                    continue
                                yield self._format_event(e2)
                            if drain_last_id and drain_last_id != "0-0":
                                last_id = drain_last_id
                        except Exception:
                            logger.warning("terminal drain failed during bootstrap stream=%s", stream_id, exc_info=True)
                        return

                if tail_id and tail_id != "0-0":
                    last_id = tail_id
        except Exception as e:
            logger.warning("bootstrap tail failed stream=%s err=%s", stream_id, str(e), exc_info=True)

        # -------------------------------------------------
        # 2) 正式读流
        # -------------------------------------------------
        try:
            while True:
                try:
                    last_id, events = await stream_store.read_batch(
                        stream_id=stream_id,
                        last_id=last_id,
                        block_ms=WS_BLOCK_MS,
                        count=WS_COUNT,
                    )
                except Exception as e:
                    logger.warning("read_batch failed stream=%s err=%s", stream_id, str(e), exc_info=True)

                    if WS_EMPTY_BACKOFF_SEC > 0:
                        await asyncio.sleep(WS_EMPTY_BACKOFF_SEC)

                    now = time.time()
                    if now - last_hb_ts >= WS_HEARTBEAT_SEC:
                        last_hb_ts = now
                        yield self._format_event({
                            "type": "heartbeat",
                            "data": "ping",
                            "ts": int(now),
                        })
                    continue

                if not events:
                    now = time.time()
                    if now - last_hb_ts >= WS_HEARTBEAT_SEC:
                        last_hb_ts = now
                        yield self._format_event({
                            "type": "heartbeat",
                            "data": "ping",
                            "ts": int(now),
                        })

                    if WS_EMPTY_BACKOFF_SEC > 0:
                        await asyncio.sleep(WS_EMPTY_BACKOFF_SEC)
                    continue

                terminal_reached = False

                for ev in events:
                    ev = _normalize_event(ev)

                    if not _is_interesting_event(ev):
                        continue

                    key = str(ev.get("_id") or "") or _weak_key(ev)
                    if not _mark_seen(key):
                        continue

                    yield self._format_event(ev)

                    if _is_terminal_event(ev):
                        logger.info("Stream %s reached terminal event: %s", stream_id, ev.get("type"))
                        terminal_reached = True
                        break

                if terminal_reached:
                    try:
                        last_id, more_events = await _drain_after_terminal(last_id)
                        for e2 in more_events:
                            e2 = _normalize_event(e2)

                            if not _is_interesting_event(e2):
                                continue

                            key2 = str(e2.get("_id") or "") or _weak_key(e2)
                            if not _mark_seen(key2):
                                continue

                            yield self._format_event(e2)
                    except Exception:
                        logger.warning("terminal drain failed stream=%s", stream_id, exc_info=True)
                    return

        except asyncio.CancelledError:
            logger.info("subscribe cancelled: %s", stream_id)
            return
        except Exception as e:
            logger.exception("subscribe error stream_id=%s err=%s", stream_id, e)
            yield self._format_event({
                "type": "error",
                "data": str(e),
                "ts": _now_ts(),
            })
            return

    @staticmethod
    def _format_event(event: Dict[str, Any]) -> str:
        event = _normalize_event(event)
        return json.dumps(event, ensure_ascii=False)


# 新架构主导出
testcase_ws_manager = WebSocketStreamManager()

# 旧代码兼容别名
testcase_sse_manager = testcase_ws_manager