#! /usr/bin/python3
# coding=utf-8
# app/workflow/state.py

from typing import Dict, Optional, Any
from threading import Lock
from datetime import datetime
import uuid
import logging

from .models import (
    WorkflowTask,
    WorkflowStage,
    WorkflowProgress,
)

# =====================================================
# logging
# =====================================================
logger = logging.getLogger(__name__)

# =====================================================
# 内存态 Workflow Store（不落库）
# =====================================================
_WORKFLOWS: Dict[str, WorkflowTask] = {}
_LOCK = Lock()


# =====================================================
# 创建新的 Workflow
# =====================================================
def create_workflow(
    *,
    workflow_id: Optional[str] = None,
    requirement_id: Optional[str] = None,
    focus_requirements: Optional[str] = None,
) -> WorkflowTask:
    with _LOCK:
        wid = workflow_id or str(uuid.uuid4())

        task = WorkflowTask(
            workflow_id=wid,
            stage=WorkflowStage.IDLE,
            progress=_default_progress_for_stage(WorkflowStage.IDLE),
            message=_default_message_for_stage(WorkflowStage.IDLE),

            requirement_id=requirement_id,
            focus_requirements=focus_requirements,

            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        # =================================================
        # strategy 智能体运行态字段（兼容 models.py 未声明时动态挂载）
        # =================================================
        if not hasattr(task, "strategy_status"):
            task.strategy_status = "idle"
        if not hasattr(task, "strategy_stream_id"):
            task.strategy_stream_id = None
        if not hasattr(task, "strategy_result"):
            task.strategy_result = None
        if not hasattr(task, "strategy_error"):
            task.strategy_error = None
        if not hasattr(task, "strategy_updated_at"):
            task.strategy_updated_at = None

        _WORKFLOWS[wid] = task

    logger.info("Workflow created: %s", wid)
    return task


# =====================================================
# 获取 Workflow
# =====================================================
def get_workflow(workflow_id: str) -> Optional[WorkflowTask]:
    return _WORKFLOWS.get(workflow_id)


# =====================================================
# 🚨 更新业务数据（禁止修改 stage / progress / message）
# =====================================================
def update_workflow_data(
    workflow_id: str,
    **kwargs,
) -> Optional[WorkflowTask]:
    forbidden = {"stage", "progress", "message"}
    if forbidden & set(kwargs.keys()):
        raise RuntimeError("update_workflow_data 禁止修改 stage / progress / message")

    with _LOCK:
        task = _WORKFLOWS.get(workflow_id)
        if not task:
            return None

        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
            else:
                # 兼容尚未在 WorkflowTask 中声明的新字段
                try:
                    setattr(task, key, value)
                except Exception:
                    logger.warning("update_workflow_data skip unknown key=%s", key)

        task.updated_at = datetime.utcnow()
        return task


# =====================================================
# ⭐ 阶段跃迁（唯一入口）
# =====================================================
def update_workflow_stage(
    workflow_id: str,
    stage: WorkflowStage,
    *,
    message: Optional[str] = None,
) -> Optional[WorkflowTask]:
    """
    ⭐ 公司级约定：
    - CASE_ANALYZING / CASE_DESIGNING 等即代表“生成系统进行中”
    - state 层不再感知任何已废弃 stage
    """
    with _LOCK:
        task = _WORKFLOWS.get(workflow_id)
        if not task:
            return None

        task.stage = stage
        task.progress = _default_progress_for_stage(stage)
        task.message = message or _default_message_for_stage(stage)

        # ⭐ 一旦进入用例生成系统，锁定幂等
        if stage in (
            WorkflowStage.CASE_ANALYZING,
            WorkflowStage.CASE_DESIGNING,
            WorkflowStage.CASE_REVIEWING,
            WorkflowStage.CASE_REFINING,
            WorkflowStage.CASE_EXPORTING,
        ):
            task.generation_started = True

        task.updated_at = datetime.utcnow()
        return task


# =====================================================
# ⭐ 流式进度推进（SSE/WS 专用）
# =====================================================
def update_workflow_progress(
    workflow_id: str,
    *,
    progress: Optional[int] = None,
    message: Optional[str] = None,
) -> Optional[WorkflowTask]:
    with _LOCK:
        task = _WORKFLOWS.get(workflow_id)
        if not task:
            return None

        if progress is not None:
            task.progress = max(0, min(int(progress), 100))

        if message is not None:
            task.message = message

        task.updated_at = datetime.utcnow()
        return task


# =====================================================
# strategy 运行态：开始执行
# =====================================================
def set_strategy_running(
    workflow_id: str,
    *,
    stream_id: str,
) -> Optional[WorkflowTask]:
    with _LOCK:
        task = _WORKFLOWS.get(workflow_id)
        if not task:
            return None

        task.strategy_status = "running"
        task.strategy_stream_id = stream_id
        task.strategy_error = None
        task.strategy_updated_at = datetime.utcnow()
        task.updated_at = datetime.utcnow()
        return task


# =====================================================
# strategy 运行态：保存结果
# =====================================================
def set_strategy_result(
    workflow_id: str,
    *,
    result: Any,
    status: str = "done",
) -> Optional[WorkflowTask]:
    with _LOCK:
        task = _WORKFLOWS.get(workflow_id)
        if not task:
            return None

        task.strategy_result = result
        task.strategy_status = status
        task.strategy_error = None
        task.strategy_updated_at = datetime.utcnow()
        task.updated_at = datetime.utcnow()
        return task


# =====================================================
# strategy 运行态：记录异常
# =====================================================
def set_strategy_error(
    workflow_id: str,
    *,
    error: str,
) -> Optional[WorkflowTask]:
    with _LOCK:
        task = _WORKFLOWS.get(workflow_id)
        if not task:
            return None

        task.strategy_status = "error"
        task.strategy_error = error
        task.strategy_updated_at = datetime.utcnow()
        task.updated_at = datetime.utcnow()
        return task


# =====================================================
# strategy 运行态：获取结果
# =====================================================
def get_strategy_result(workflow_id: str) -> Any:
    task = _WORKFLOWS.get(workflow_id)
    if not task:
        return None
    return getattr(task, "strategy_result", None)


# =====================================================
# strategy 运行态：获取上下文
# =====================================================
def get_strategy_context_snapshot(workflow_id: str) -> Optional[dict]:
    task = _WORKFLOWS.get(workflow_id)
    if not task:
        return None

    requirement_text = (
        getattr(task, "requirement_text", None)
        or getattr(task, "pdf_text", None)
        or ""
    )

    return {
        "workflow_id": task.workflow_id,
        "requirement_id": getattr(task, "requirement_id", None),
        "has_requirement": bool(str(requirement_text).strip()),
        "has_analysis_result": bool(getattr(task, "requirement_quality", None) or getattr(task, "analysis_result", None)),
        "has_testcase_result": bool(
            getattr(task, "final_cases", None)
            or getattr(task, "test_case_drafts", None)
            or getattr(task, "testcase_result", None)
        ),
        "strategy_status": getattr(task, "strategy_status", "idle"),
        "strategy_stream_id": getattr(task, "strategy_stream_id", None),
        "strategy_error": getattr(task, "strategy_error", None),
    }


# =====================================================
# 阶段默认进度（UI 辅助）
# =====================================================
def _default_progress_for_stage(stage: WorkflowStage) -> int:
    return {
        WorkflowStage.IDLE: 0,
        WorkflowStage.FILE_READY: 10,

        # ===== 分支 A =====
        WorkflowStage.REQ_ANALYZING: 30,
        WorkflowStage.REQ_ANALYSIS_DONE: 50,

        # ===== 分支 B =====
        WorkflowStage.CASE_ANALYZING: 60,
        WorkflowStage.CASE_DESIGNING: 70,
        WorkflowStage.CASE_REVIEWING: 80,
        WorkflowStage.CASE_REFINING: 90,
        WorkflowStage.CASE_EXPORTING: 95,

        # ===== 终态 =====
        WorkflowStage.DONE: 100,
        WorkflowStage.ERROR: 0,
    }.get(stage, 0)


# =====================================================
# 阶段默认文案（UI 专用）
# =====================================================
def _default_message_for_stage(stage: WorkflowStage) -> str:
    return {
        WorkflowStage.IDLE: "等待上传需求文档",
        WorkflowStage.FILE_READY: "需求文档已上传并解析完成",

        WorkflowStage.REQ_ANALYZING: "正在进行需求质量分析",
        WorkflowStage.REQ_ANALYSIS_DONE: "需求分析完成",

        WorkflowStage.CASE_ANALYZING: "正在分析测试点",
        WorkflowStage.CASE_DESIGNING: "正在设计测试用例",
        WorkflowStage.CASE_REVIEWING: "正在评审测试用例",
        WorkflowStage.CASE_REFINING: "根据评审意见优化用例",
        WorkflowStage.CASE_EXPORTING: "正在整理并导出测试用例",

        WorkflowStage.DONE: "流程完成",
        WorkflowStage.ERROR: "流程发生错误，可重试",
    }.get(stage, "")


# =====================================================
# 前端状态快照
# =====================================================
def get_workflow_progress(workflow_id: str) -> Optional[WorkflowProgress]:
    task = _WORKFLOWS.get(workflow_id)
    if not task:
        return None
    return task.to_progress()


# =====================================================
# 重置 Workflow
# =====================================================
def reset_workflow(workflow_id: str) -> Optional[WorkflowTask]:
    with _LOCK:
        task = _WORKFLOWS.get(workflow_id)
        if not task:
            return None

        task.stage = WorkflowStage.IDLE
        task.progress = _default_progress_for_stage(WorkflowStage.IDLE)
        task.message = _default_message_for_stage(WorkflowStage.IDLE)

        task.requirement_quality = None
        task.test_point_spec = None
        task.test_case_drafts = None
        task.review_report = None
        task.final_cases = None
        task.excel_path = None
        task.total_cases = None
        task.pdf_path = None
        task.pdf_text = None
        task.generation_started = False

        # strategy 相关重置
        task.strategy_status = "idle"
        task.strategy_stream_id = None
        task.strategy_result = None
        task.strategy_error = None
        task.strategy_updated_at = None

        task.updated_at = datetime.utcnow()
        return task


# =====================================================
# ⭐ 序列化
# =====================================================
def serialize_workflow(task: WorkflowTask) -> dict:
    return {
        "workflow_id": task.workflow_id,
        "stage": task.stage.value,
        "progress": task.progress,
        "message": task.message,
        "requirement_id": task.requirement_id,
        "focus_requirements": task.focus_requirements,
        "pdf_path": getattr(task, "pdf_path", None),
        "pdf_text": getattr(task, "pdf_text", None),
        "generation_started": getattr(task, "generation_started", False),

        # analysis / testcase 现有字段（按你当前模型已有的来）
        "requirement_quality": getattr(task, "requirement_quality", None),
        "test_point_spec": getattr(task, "test_point_spec", None),
        "test_case_drafts": getattr(task, "test_case_drafts", None),
        "review_report": getattr(task, "review_report", None),
        "final_cases": getattr(task, "final_cases", None),
        "excel_path": getattr(task, "excel_path", None),
        "total_cases": getattr(task, "total_cases", None),

        # strategy 新增字段
        "strategy_status": getattr(task, "strategy_status", "idle"),
        "strategy_stream_id": getattr(task, "strategy_stream_id", None),
        "strategy_result": getattr(task, "strategy_result", None),
        "strategy_error": getattr(task, "strategy_error", None),
        "strategy_updated_at": (
            getattr(task, "strategy_updated_at").isoformat()
            if getattr(task, "strategy_updated_at", None)
            else None
        ),

        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }