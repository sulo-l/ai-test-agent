import asyncio
import json
import time
import uuid
from typing import Dict, AsyncGenerator, Any
import logging
from fastapi import WebSocket, WebSocketDisconnect

# 配置日志记录
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WebSocketStreamManager:
    """
    每个 stream_id 对应一个 WebSocket 连接管理

    对外保证的接口（controller 依赖）：
    - create_stream()
    - publish()
    - close()
    - subscribe()
    """

    def __init__(self):
        self._streams: Dict[str, WebSocket] = {}  # 用于管理 WebSocket 连接
        self._last_active: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    # =====================================================
    # 创建 WebSocket 连接（这里简化为创建 stream_id）
    # =====================================================
    async def create_stream(self) -> str:
        stream_id = uuid.uuid4().hex
        async with self._lock:
            self._streams[stream_id] = None  # 初始化 WebSocket 连接为 None
            self._last_active[stream_id] = time.time()
        logger.info(f"Stream {stream_id} created.")  # Log stream creation
        return stream_id

    # =====================================================
    # 推送事件
    # =====================================================
    async def publish(self, stream_id: str, event: Dict[str, Any]):
        async with self._lock:
            websocket = self._streams.get(stream_id)
            if not websocket:
                logger.error(f"Stream {stream_id} not found. Event not published.")  # Log if stream is not found
                return
            self._last_active[stream_id] = time.time()

        logger.info(f"Publishing event to stream {stream_id}: {event}")  # Log event publication
        if websocket:
            await websocket.send_text(self._format_event(event))  # Send the event over WebSocket
            logger.info(f"Event successfully pushed to stream {stream_id}")  # Log successful event publication

    # =====================================================
    # 关闭 WebSocket 连接
    # =====================================================
    async def close(self, stream_id: str):
        async with self._lock:
            websocket = self._streams.get(stream_id)
            if not websocket:
                logger.error(f"Stream {stream_id} not found. Cannot close.")  # Log if stream not found
                return
        logger.info(f"Closing stream {stream_id}")  # Log stream closure
        if websocket:
            await websocket.send_text(self._format_event({"type": "__close__"}))  # Signal that the stream should close

    # =====================================================
    # WebSocket 订阅（接收 WebSocket 连接并推送事件）
    # =====================================================
    async def subscribe(self, websocket: WebSocket, stream_id: str):
        async with self._lock:
            if stream_id in self._streams:
                self._streams[stream_id] = websocket  # 关联 WebSocket 到 stream_id
            else:
                raise ValueError(f"Stream {stream_id} not found.")

        logger.info(f"Subscribed to stream {stream_id}")  # Log subscription
        await websocket.accept()

        # ✅ 关键 1：首帧，立刻告诉客户端“我活着”
        await websocket.send_text(self._format_event({
            "type": "connected",
            "stream_id": stream_id,
            "ts": time.time(),
        }))

        try:
            while True:
                await asyncio.sleep(10)  # You can adjust this as needed
                # 心跳，防止 WebSocket 连接超时
                await websocket.send_text(self._format_event({
                    "type": "ping",
                    "ts": time.time(),
                }))
        except WebSocketDisconnect:
            logger.info(f"Stream {stream_id} disconnected.")
        finally:
            await self._cleanup(stream_id)  # 清理连接

    # =====================================================
    # 内部清理
    # =====================================================
    async def _cleanup(self, stream_id: str):
        async with self._lock:
            self._streams.pop(stream_id, None)
            self._last_active.pop(stream_id, None)
        logger.info(f"Stream {stream_id} cleaned up.")  # Log cleanup

    # =====================================================
    # WebSocket 格式化
    # =====================================================
    @staticmethod
    def _format_event(event: Dict[str, Any]) -> str:
        # 检查 event 的格式，确保其符合要求
        if not isinstance(event, dict):
            raise ValueError("Invalid event format: must be a dictionary")
        payload = json.dumps(event, ensure_ascii=False)
        return f"data: {payload}\n\n"


# =====================================================
# 单例（B 分支唯一）
# =====================================================
testcase_ws_manager = WebSocketStreamManager()
