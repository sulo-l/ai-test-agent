#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/ws.py

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect


logger = logging.getLogger(__name__)


try:
    from app.strategy_app.stream_store import strategy_stream_store  # type: ignore
except Exception:  # pragma: no cover
    strategy_stream_store = None

try:
    from app.strategy_app.tasks import start_strategy_ws_task  # type: ignore
except Exception:  # pragma: no cover
    start_strategy_ws_task = None


# =====================================================
# Stream / Connection State
# =====================================================

FINAL_STATUSES = {"done", "error", "cancelled"}
HEARTBEAT_EVENT = {"type": "heartbeat"}


@dataclass
class StrategyConnection:
    """
    单个 websocket 连接
    """
    websocket: WebSocket
    connected_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)


@dataclass
class StrategyStreamState:
    """
    单个 stream 的运行状态
    """
    stream_id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished: bool = False
    status: str = "running"   # running / done / error / cancelled
    result: Optional[Dict[str, Any]] = None
    last_error: Optional[Dict[str, Any]] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    connections: Set[int] = field(default_factory=set)


# =====================================================
# WS Manager
# =====================================================

class StrategyWSManager:
    """
    真正的 WebSocket 推送管理器（兼容原 ws.py 入口习惯）

    功能：
    1. 维护 stream 状态
    2. 维护 websocket 连接
    3. 接收前端 start_strategy 指令
    4. 持续推送 stage / partial / result / error
    5. 支持从 stream_store 恢复部分状态
    """

    def __init__(
        self,
        stream_ttl_sec: int = 3600,
        heartbeat_sec: int = 15,
        max_cached_events: int = 500,
    ) -> None:
        self._streams: Dict[str, StrategyStreamState] = {}
        self._connections: Dict[int, StrategyConnection] = {}
        self._lock = asyncio.Lock()
        self._stream_ttl_sec = stream_ttl_sec
        self._heartbeat_sec = heartbeat_sec
        self._max_cached_events = max_cached_events

    # -------------------------------------------------
    # 基础 stream 管理
    # -------------------------------------------------
    async def create_stream(self, stream_id: str) -> None:
        if not stream_id:
            return

        async with self._lock:
            self._cleanup_expired_locked()
            if stream_id not in self._streams:
                self._streams[stream_id] = StrategyStreamState(stream_id=stream_id)
                logger.info("[strategy.ws] create stream: %s", stream_id)

        await self._ensure_store_stream(stream_id)

    async def ensure_stream(self, stream_id: str) -> None:
        await self.create_stream(stream_id)

    async def exists(self, stream_id: str) -> bool:
        if not stream_id:
            return False

        async with self._lock:
            self._cleanup_expired_locked()
            if stream_id in self._streams:
                return True

        if strategy_stream_store is not None:
            try:
                ret = strategy_stream_store.stream_exists(stream_id)
                if asyncio.iscoroutine(ret):
                    ret = await ret
                return bool(ret)
            except Exception:
                logger.warning("[strategy.ws] store exists check failed", exc_info=True)

        return False

    async def finish(self, stream_id: str) -> None:
        state = await self._get_or_restore_state(stream_id)
        if not state:
            return
        if state.finished:
            return

        state.finished = True
        state.status = "done"
        state.updated_at = time.time()

        await self.push_stage_event(
            stream_id=stream_id,
            stage="DONE",
            status="done",
            title="策略任务已完成",
            message="strategy task finished",
        )
        await self._sync_status_to_store(stream_id, "done")

    async def set_status(self, stream_id: str, status: str) -> None:
        state = await self._get_or_restore_state(stream_id)
        if not state:
            return

        normalized = str(status or "").strip() or state.status
        state.status = normalized
        state.updated_at = time.time()
        if normalized in FINAL_STATUSES:
            state.finished = True

        await self._sync_status_to_store(stream_id, normalized)

    async def get_state_meta(self, stream_id: str) -> Optional[Dict[str, Any]]:
        state = await self._get_or_restore_state(stream_id)
        if not state:
            return None
        return {
            "stream_id": state.stream_id,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
            "finished": state.finished,
            "status": state.status,
            "connection_count": len(state.connections),
            "event_count": len(state.events),
        }

    async def get_result(self, stream_id: str) -> Optional[Dict[str, Any]]:
        state = await self._get_or_restore_state(stream_id)
        if state and isinstance(state.result, dict):
            return state.result

        if strategy_stream_store is not None:
            try:
                ret = strategy_stream_store.get_result(stream_id)
                ret = await ret if asyncio.iscoroutine(ret) else ret
                if isinstance(ret, dict):
                    return ret
            except Exception:
                logger.warning("[strategy.ws] get result from store failed", exc_info=True)

        return None

    # -------------------------------------------------
    # WebSocket 连接管理
    # -------------------------------------------------
    async def connect(self, stream_id: str, websocket: WebSocket) -> None:
        """
        接受 websocket 连接，并绑定到 stream
        """
        await websocket.accept()
        await self.create_stream(stream_id)

        conn_id = id(websocket)
        connection = StrategyConnection(websocket=websocket)

        async with self._lock:
            self._connections[conn_id] = connection
            state = self._streams.get(stream_id)
            if state:
                state.connections.add(conn_id)
                state.updated_at = time.time()

        logger.info("[strategy.ws] websocket connected, stream_id=%s conn_id=%s", stream_id, conn_id)

        # 先发 connected
        await self._safe_send_json(
            websocket,
            {
                "type": "connected",
                "stream_id": stream_id,
                "message": "websocket connected",
                "ts": int(time.time()),
            },
        )

        # 再补发历史事件，避免用户刷新页面后丢上下文
        await self._replay_cached_events(stream_id, websocket)

    async def disconnect(self, stream_id: str, websocket: WebSocket) -> None:
        conn_id = id(websocket)

        async with self._lock:
            self._connections.pop(conn_id, None)
            state = self._streams.get(stream_id)
            if state:
                state.connections.discard(conn_id)
                state.updated_at = time.time()

        logger.info("[strategy.ws] websocket disconnected, stream_id=%s conn_id=%s", stream_id, conn_id)

    async def handle_websocket(self, websocket: WebSocket, stream_id: str) -> None:
        """
        router 中直接调用的 websocket 处理入口
        """
        await self.connect(stream_id, websocket)

        heartbeat_task = asyncio.create_task(self._heartbeat_loop(stream_id, websocket))
        try:
            while True:
                raw = await websocket.receive_text()
                await self._mark_connection_active(websocket)
                await self._handle_client_message(stream_id=stream_id, websocket=websocket, raw_message=raw)
        except WebSocketDisconnect:
            logger.info("[strategy.ws] client disconnected, stream_id=%s", stream_id)
        except Exception:
            logger.exception("[strategy.ws] websocket handler failed, stream_id=%s", stream_id)
            await self.emit_error(stream_id, "websocket internal error")
        finally:
            heartbeat_task.cancel()
            await self.disconnect(stream_id, websocket)

    # -------------------------------------------------
    # 前端请求处理
    # -------------------------------------------------
    async def _handle_client_message(self, stream_id: str, websocket: WebSocket, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message or "{}")
        except Exception:
            await self._safe_send_json(
                websocket,
                {
                    "type": "error",
                    "message": "invalid json message",
                    "stream_id": stream_id,
                    "ts": int(time.time()),
                },
            )
            return

        msg_type = str(payload.get("type") or "").strip()

        if msg_type == "ping":
            await self._safe_send_json(
                websocket,
                {
                    "type": "pong",
                    "stream_id": stream_id,
                    "ts": int(time.time()),
                },
            )
            return

        if msg_type == "start_strategy":
            await self._handle_start_strategy(stream_id=stream_id, websocket=websocket, payload=payload)
            return

        if msg_type == "get_state":
            meta = await self.get_state_meta(stream_id)
            await self._safe_send_json(
                websocket,
                {
                    "type": "state",
                    "stream_id": stream_id,
                    "data": meta or {},
                    "ts": int(time.time()),
                },
            )
            return

        if msg_type == "get_result":
            result = await self.get_result(stream_id)
            await self._safe_send_json(
                websocket,
                {
                    "type": "result",
                    "stream_id": stream_id,
                    "data": result or {},
                    "ts": int(time.time()),
                },
            )
            return

        await self._safe_send_json(
            websocket,
            {
                "type": "error",
                "message": f"unsupported message type: {msg_type or 'unknown'}",
                "stream_id": stream_id,
                "ts": int(time.time()),
            },
        )

    async def _handle_start_strategy(
        self,
        stream_id: str,
        websocket: WebSocket,
        payload: Dict[str, Any],
    ) -> None:
        body = payload.get("payload") or {}
        if not isinstance(body, dict):
            body = {}

        await self.create_stream(stream_id)

        await self._safe_send_json(
            websocket,
            {
                "type": "ack",
                "stream_id": stream_id,
                "message": "strategy task accepted",
                "ts": int(time.time()),
            },
        )

        await self.push_stage_event(
            stream_id=stream_id,
            stage="ACCEPTED",
            status="done",
            title="策略任务已接收",
            message="websocket request accepted",
        )

        if start_strategy_ws_task is None:
            await self.emit_error(
                stream_id=stream_id,
                message="start_strategy_ws_task is not available",
            )
            return

        try:
            maybe_coro = start_strategy_ws_task(
                stream_id=stream_id,
                payload=body,
                emit=self.push_event,
            )
            if asyncio.iscoroutine(maybe_coro):
                asyncio.create_task(maybe_coro)
        except Exception as exc:
            logger.exception("[strategy.ws] start strategy task failed, stream_id=%s", stream_id)
            await self.emit_error(stream_id=stream_id, message=f"start strategy failed: {exc}")

    # -------------------------------------------------
    # 统一推送事件
    # -------------------------------------------------
    async def push_event(self, stream_id: str, payload: Dict[str, Any]) -> None:
        await self.create_stream(stream_id)
        state = await self._get_or_restore_state(stream_id)
        if not state:
            logger.warning("[strategy.ws] push_event skipped, stream not found: %s", stream_id)
            return

        event = dict(payload or {})
        event.setdefault("stream_id", stream_id)
        event.setdefault("ts", int(time.time()))
        event_type = str(event.get("type") or "message").strip() or "message"

        state.updated_at = time.time()

        # 状态同步
        stage = str(event.get("stage") or "").strip().upper()
        if event_type == "error" or stage == "ERROR":
            state.status = "error"
            state.finished = True
            state.last_error = event
        elif stage == "CANCELLED":
            state.status = "cancelled"
            state.finished = True
        elif stage == "DONE":
            state.status = "done"
            state.finished = True
        elif not state.finished:
            state.status = "running"

        if event_type == "result":
            data = event.get("data")
            if isinstance(data, dict):
                state.result = data

        # 缓存事件
        self._append_event_to_state(state, event)

        # 推送在线连接
        await self._fanout_to_stream_connections(stream_id=stream_id, payload=event)

        # 落库
        await self._mirror_event_to_store(stream_id, event)
        await self._sync_status_to_store(stream_id, state.status)

        if event_type == "result":
            await self._set_result_to_store(stream_id, event.get("data"))

    async def publish(self, stream_id: str, payload: Dict[str, Any]) -> None:
        await self.push_event(stream_id, payload)

    async def emit(self, stream_id: str, payload: Dict[str, Any]) -> None:
        await self.push_event(stream_id, payload)

    async def push(self, stream_id: str, payload: Dict[str, Any]) -> None:
        await self.push_event(stream_id, payload)

    async def send_event(self, stream_id: str, payload: Dict[str, Any]) -> None:
        await self.push_event(stream_id, payload)

    async def broadcast(self, stream_id: str, payload: Dict[str, Any]) -> None:
        await self.push_event(stream_id, payload)

    async def append_event(self, stream_id: str, payload: Dict[str, Any]) -> None:
        await self.push_event(stream_id, payload)

    async def push_stage_event(
        self,
        stream_id: str,
        stage: str,
        status: str,
        title: str,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "type": "stage",
            "stage": str(stage or "").strip().upper(),
            "status": str(status or "").strip().lower() or "running",
            "title": title,
            "message": message,
        }
        if extra:
            payload["extra"] = extra
        await self.push_event(stream_id, payload)

    async def emit_partial(
        self,
        stream_id: str,
        block: str,
        data: Any,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "type": "partial",
            "block": block,
            "data": data,
        }
        if extra:
            payload["extra"] = extra
        await self.push_event(stream_id, payload)

    async def emit_result(self, stream_id: str, result: Dict[str, Any]) -> None:
        payload = {
            "type": "result",
            "data": result or {},
        }
        await self.push_event(stream_id, payload)

    async def emit_error(self, stream_id: str, message: str, trace: str = "") -> None:
        payload = {
            "type": "error",
            "message": message,
            "trace": trace,
        }
        await self.push_event(stream_id, payload)

    # -------------------------------------------------
    # 兼容旧 SSE 调用
    # -------------------------------------------------
    async def stream(self, stream_id: str) -> AsyncGenerator[str, None]:
        """
        兼容旧 router/旧代码可能仍然使用 StreamingResponse + SSE 的情况。
        新链路建议直接走 websocket。
        """
        state = await self._get_or_restore_state(stream_id)
        if not state:
            yield self._format_sse(
                event="error",
                data={"type": "error", "message": "stream not found", "stream_id": stream_id},
            )
            return

        yield self._format_sse(
            event="connected",
            data={
                "type": "connected",
                "stream_id": stream_id,
                "message": "stream connected",
                "ts": int(time.time()),
            },
        )

        sent = 0
        while True:
            state = await self._get_or_restore_state(stream_id)
            if not state:
                break

            events = state.events
            while sent < len(events):
                event = events[sent]
                sent += 1
                event_type = str(event.get("type") or "message").strip() or "message"
                yield self._format_sse(event=event_type, data=event)

            if state.finished:
                break

            await asyncio.sleep(1)

    async def subscribe(self, stream_id: str) -> AsyncGenerator[str, None]:
        async for chunk in self.stream(stream_id):
            yield chunk

    async def listen(self, stream_id: str) -> AsyncGenerator[str, None]:
        async for chunk in self.stream(stream_id):
            yield chunk

    async def iter_events(self, stream_id: str) -> AsyncGenerator[str, None]:
        async for chunk in self.stream(stream_id):
            yield chunk

    # -------------------------------------------------
    # 内部工具
    # -------------------------------------------------
    async def _get_state(self, stream_id: str) -> Optional[StrategyStreamState]:
        async with self._lock:
            self._cleanup_expired_locked()
            return self._streams.get(stream_id)

    async def _get_or_restore_state(self, stream_id: str) -> Optional[StrategyStreamState]:
        state = await self._get_state(stream_id)
        if state:
            return state
        return await self._restore_from_store(stream_id)

    def _append_event_to_state(self, state: StrategyStreamState, event: Dict[str, Any]) -> None:
        state.events.append(event)
        if len(state.events) > self._max_cached_events:
            overflow = len(state.events) - self._max_cached_events
            if overflow > 0:
                del state.events[:overflow]

    async def _fanout_to_stream_connections(self, stream_id: str, payload: Dict[str, Any]) -> None:
        state = await self._get_or_restore_state(stream_id)
        if not state or not state.connections:
            return

        dead_conn_ids: List[int] = []

        for conn_id in list(state.connections):
            conn = self._connections.get(conn_id)
            if not conn:
                dead_conn_ids.append(conn_id)
                continue

            ok = await self._safe_send_json(conn.websocket, payload)
            if ok:
                conn.last_active_at = time.time()
            else:
                dead_conn_ids.append(conn_id)

        if dead_conn_ids:
            async with self._lock:
                cur_state = self._streams.get(stream_id)
                if cur_state:
                    for conn_id in dead_conn_ids:
                        cur_state.connections.discard(conn_id)
                        self._connections.pop(conn_id, None)

    async def _replay_cached_events(self, stream_id: str, websocket: WebSocket) -> None:
        state = await self._get_or_restore_state(stream_id)
        if not state:
            return

        for event in state.events:
            ok = await self._safe_send_json(websocket, event)
            if not ok:
                return

    async def _mark_connection_active(self, websocket: WebSocket) -> None:
        conn = self._connections.get(id(websocket))
        if conn:
            conn.last_active_at = time.time()

    async def _heartbeat_loop(self, stream_id: str, websocket: WebSocket) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_sec)
                ok = await self._safe_send_json(
                    websocket,
                    {
                        **HEARTBEAT_EVENT,
                        "stream_id": stream_id,
                        "ts": int(time.time()),
                    },
                )
                if not ok:
                    break
        except asyncio.CancelledError:
            return
        except Exception:
            logger.debug("[strategy.ws] heartbeat loop stopped, stream_id=%s", stream_id, exc_info=True)

    async def _safe_send_json(self, websocket: WebSocket, payload: Dict[str, Any]) -> bool:
        try:
            await websocket.send_text(json.dumps(payload, ensure_ascii=False, default=str))
            return True
        except Exception:
            return False

    def _format_sse(self, event: str, data: Dict[str, Any]) -> str:
        safe_event = event or "message"
        payload = json.dumps(data, ensure_ascii=False, default=str)
        return f"event: {safe_event}\ndata: {payload}\n\n"

    async def _mirror_event_to_store(self, stream_id: str, payload: Dict[str, Any]) -> None:
        if strategy_stream_store is None:
            return
        try:
            ret = strategy_stream_store.append_event(stream_id, payload)
            if asyncio.iscoroutine(ret):
                await ret
        except Exception:
            logger.warning("[strategy.ws] mirror event to store failed", exc_info=True)

    async def _sync_status_to_store(self, stream_id: str, status: str) -> None:
        if strategy_stream_store is None:
            return
        try:
            ret = strategy_stream_store.set_status(stream_id, status)
            if asyncio.iscoroutine(ret):
                await ret
        except Exception:
            logger.warning("[strategy.ws] set store status failed", exc_info=True)

    async def _ensure_store_stream(self, stream_id: str) -> None:
        if strategy_stream_store is None:
            return
        try:
            ret = strategy_stream_store.ensure_stream(stream_id)
            if asyncio.iscoroutine(ret):
                await ret
        except Exception:
            logger.warning("[strategy.ws] ensure stream in store failed", exc_info=True)

    async def _set_result_to_store(self, stream_id: str, result: Any) -> None:
        if strategy_stream_store is None or not isinstance(result, dict):
            return
        try:
            ret = strategy_stream_store.set_result(stream_id, result)
            if asyncio.iscoroutine(ret):
                await ret
        except Exception:
            logger.warning("[strategy.ws] set_result failed", exc_info=True)

    async def _restore_from_store(self, stream_id: str) -> Optional[StrategyStreamState]:
        if strategy_stream_store is None or not stream_id:
            return None

        try:
            ret = strategy_stream_store.get_stream(stream_id)
            data = await ret if asyncio.iscoroutine(ret) else ret
            if not isinstance(data, dict):
                return None

            status = str(data.get("status") or "running").strip() or "running"
            result = data.get("result")
            events = data.get("events") or []

            state = StrategyStreamState(
                stream_id=stream_id,
                created_at=float(data.get("created_at") or time.time()),
                updated_at=float(data.get("updated_at") or time.time()),
                finished=status in FINAL_STATUSES,
                status=status,
                result=result if isinstance(result, dict) else None,
            )

            if isinstance(events, list):
                for event in events:
                    if isinstance(event, dict):
                        state.events.append(event)

            async with self._lock:
                self._cleanup_expired_locked()
                if stream_id not in self._streams:
                    self._streams[stream_id] = state
                else:
                    state = self._streams[stream_id]

            return state

        except Exception:
            logger.warning("[strategy.ws] restore stream from store failed", exc_info=True)
            return None

    def _cleanup_expired_locked(self) -> None:
        now = time.time()
        expired_ids: List[str] = []

        for stream_id, state in self._streams.items():
            age = now - state.created_at
            idle = now - state.updated_at
            if age > self._stream_ttl_sec or idle > self._stream_ttl_sec:
                expired_ids.append(stream_id)

        for stream_id in expired_ids:
            self._streams.pop(stream_id, None)
            logger.info("[strategy.ws] cleanup expired stream: %s", stream_id)


# =====================================================
# 单例
# =====================================================

strategy_ws_manager = StrategyWSManager()

# 向后兼容旧名字，减少其它文件改动量
strategy_sse_manager = strategy_ws_manager