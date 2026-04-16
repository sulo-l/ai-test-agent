#! /usr/bin/python3
# coding=utf-8
# @Author: sulo
# @Desc: Unified Workflow SSE Event Protocol (FINAL · Stable)

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Union
import time
import uuid
import json


# =====================================================
# Event Type（协议级：只描述“发生了什么”）
# =====================================================

class EventType(str, Enum):
    CONNECTED = "connected"
    PING = "ping"

    STAGE = "stage"                 # payload: { stage: string }
    PROGRESS = "progress"           # payload: { progress?, message? }

    ANALYSIS = "analysis"
    TEST_POINT = "test_point"
    TESTCASE = "testcase"
    REVIEW = "review"

    DOWNLOAD_READY = "download_ready"
    DONE = "done"

    ERROR = "error"


# =====================================================
# ⭐ 核心 WsEvent（SSE 唯一事实模型）
# =====================================================

@dataclass
class WsEvent:
    """
    SSE 事件统一模型

    设计铁律：
    - SSE 层只接受 WsEvent.to_sse()
    - ❌ 不定义 WorkflowStage
    - ❌ 不做业务判断
    """

    type: EventType
    payload: Union[Dict[str, Any], None] = None
    workflow_id: Optional[str] = None

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    final: bool = False

    # -----------------------------
    # 内部 dict（仅序列化）
    # -----------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "payload": self.payload,
            "workflow_id": self.workflow_id,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "final": self.final,
        }

    # -----------------------------
    # ⭐ SSE 唯一出口
    # -----------------------------
    def to_sse(self) -> str:
        data = self.to_dict()
        return (
            f"event: {data['type']}\n"
            f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        )


# =====================================================
# 标准事件构造器（全部返回 WsEvent）
# =====================================================

def event_connected(workflow_id: Optional[str] = None) -> WsEvent:
    return WsEvent(
        type=EventType.CONNECTED,
        workflow_id=workflow_id,
    )


def event_ping() -> WsEvent:
    return WsEvent(type=EventType.PING)


def event_stage(stage_value: str, workflow_id: str) -> WsEvent:
    """
    stage_value 必须来自 workflow/models.py 的 WorkflowStage.value
    """
    return WsEvent(
        type=EventType.STAGE,
        workflow_id=workflow_id,
        payload={"stage": stage_value},
    )


def event_analysis(data: Any, workflow_id: str) -> WsEvent:
    return WsEvent(
        type=EventType.ANALYSIS,
        workflow_id=workflow_id,
        payload=data,
    )


def event_test_point(test_point: Any, workflow_id: str) -> WsEvent:
    return WsEvent(
        type=EventType.TEST_POINT,
        workflow_id=workflow_id,
        payload=test_point,
    )


def event_testcase(testcase: Any, workflow_id: str) -> WsEvent:
    return WsEvent(
        type=EventType.TESTCASE,
        workflow_id=workflow_id,
        payload=testcase,
    )


def event_review(review_result: Any, workflow_id: str) -> WsEvent:
    return WsEvent(
        type=EventType.REVIEW,
        workflow_id=workflow_id,
        payload=review_result,
    )


def event_download_ready(file_path: str, workflow_id: str) -> WsEvent:
    return WsEvent(
        type=EventType.DOWNLOAD_READY,
        workflow_id=workflow_id,
        payload={"file_path": file_path},
    )


def event_done(workflow_id: str) -> WsEvent:
    return WsEvent(
        type=EventType.DONE,
        workflow_id=workflow_id,
        final=True,
    )


def event_error(
    message: str,
    workflow_id: Optional[str] = None,
    detail: Optional[Any] = None,
) -> WsEvent:
    # Ensure `detail` is passed or provide a default error message
    detail = detail or "No additional details provided."
    return WsEvent(
        type=EventType.ERROR,
        workflow_id=workflow_id,
        payload={
            "message": message,
            "detail": detail,
        },
        final=True,
    )


# =====================================================
# 向后兼容（但不引入新语义）
# =====================================================

def event_progress(
    progress: Optional[int] = None,
    message: Optional[str] = None,
    workflow_id: Optional[str] = None,
) -> WsEvent:
    payload: Dict[str, Any] = {}
    if progress is not None:
        payload["progress"] = progress
    if message:
        payload["message"] = message

    return WsEvent(
        type=EventType.PROGRESS,
        workflow_id=workflow_id,
        payload=payload,
    )
