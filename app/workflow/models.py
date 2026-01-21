#! /usr/bin/python3
# coding=utf-8
# app/workflow/models.py

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum
from pydantic import BaseModel


# =====================================================
# Workflow 阶段枚举（⚠️ 必须与前端完全一致）
# =====================================================
class WorkflowStage(str, Enum):
    """
    ⚠️ value 必须与前端完全一致
    """

    IDLE = "idle"
    FILE_READY = "fileReady"

    ANALYZING = "analyzing"
    ANALYSIS_DONE = "analysisDone"

    GENERATING = "generating"
    GENERATED = "generated"

    ERROR = "error"


# =====================================================
# WorkflowTask（内存态 Workflow，全量）
# =====================================================
@dataclass
class WorkflowTask:
    """
    一个 workflow = 用户一次完整操作
    """

    # =================================================
    # 🆔 核心标识
    # =================================================
    workflow_id: str

    # =================================================
    # 🚦 当前阶段
    # =================================================
    stage: WorkflowStage = WorkflowStage.IDLE

    # =================================================
    # 📊 进度（0~100）
    # =================================================
    progress: int = 0

    # =================================================
    # 📝 当前状态文案（给 UI 用）
    # =================================================
    message: Optional[str] = None

    # =================================================
    # 📄 PDF 相关（上传阶段）
    # =================================================
    pdf_path: Optional[str] = None
    pdf_text: Optional[str] = None

    # =================================================
    # 🤖 AI 分析 / 生成相关
    # =================================================
    analysis_result: Optional[Dict[str, Any]] = None
    test_points: Optional[List[Dict[str, Any]]] = None

    task_id: Optional[str] = None
    excel_path: Optional[str] = None
    total_cases: Optional[int] = None

    # =================================================
    # 🎯 补充测试重点（⭐核心新增）
    # =================================================
    focus_requirements: Optional[str] = None

    # =================================================
    # 📈 重点命中统计（⭐为后续扩展预留）
    # =================================================
    focus_hit_cases: Optional[int] = None

    # =================================================
    # 🕒 时间
    # =================================================
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    # =================================================
    # 🔍 状态判断
    # =================================================
    def is_running(self) -> bool:
        return self.stage in (
            WorkflowStage.ANALYZING,
            WorkflowStage.GENERATING,
        )

    def is_done(self) -> bool:
        return self.stage in (
            WorkflowStage.ANALYSIS_DONE,
            WorkflowStage.GENERATED,
        )

    def is_error(self) -> bool:
        return self.stage == WorkflowStage.ERROR


# =====================================================
# SSE / Generate Request
# =====================================================
class GenerateRequest(BaseModel):
    workflow_id: Optional[str] = None

    # ⚠️ 原始需求（如前端 textarea）
    requirement: Optional[str] = None

    # ⭐ 补充测试重点（推荐前端显式传）
    focus_requirements: Optional[str] = None


# =====================================================
# WorkflowProgress（⭐前端 /status 专用）
# =====================================================
class WorkflowProgress(BaseModel):
    """
    给前端使用的「工作流状态快照」
    """
    stage: WorkflowStage
    progress: int
    message: Optional[str] = None
