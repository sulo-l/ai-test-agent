#! /usr/bin/python3
# coding=utf-8
# app/workflow/state.py

from typing import Dict, Optional
from threading import Lock
from datetime import datetime
import uuid

from .models import WorkflowTask, WorkflowStage, WorkflowProgress

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
    stage: WorkflowStage = WorkflowStage.IDLE,
    progress: int = 0,
    message: Optional[str] = None,
    focus_requirements: Optional[str] = None,  # ⭐ 新增
) -> WorkflowTask:
    """
    新建 workflow：
    - 默认 stage=IDLE（允许直接上传 PDF）
    """
    with _LOCK:
        wid = workflow_id or str(uuid.uuid4())

        task = WorkflowTask(
            workflow_id=wid,
            stage=stage,
            progress=progress,
            message=message or _default_message_for_stage(stage),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            test_points=None,

            # ⭐ 补充测试重点（核心新增）
            focus_requirements=focus_requirements,
        )

        _WORKFLOWS[wid] = task
        return task


# =====================================================
# 获取 Workflow
# =====================================================
def get_workflow(workflow_id: str) -> Optional[WorkflowTask]:
    return _WORKFLOWS.get(workflow_id)


# =====================================================
# 🚨 通用更新函数（仅允许写业务字段）
# =====================================================
def update_workflow(
    workflow_id: str,
    **kwargs,
) -> Optional[WorkflowTask]:
    if "stage" in kwargs or "progress" in kwargs or "message" in kwargs:
        raise RuntimeError(
            "禁止通过 update_workflow 修改 stage/progress/message，"
            "请使用 update_workflow_stage"
        )

    with _LOCK:
        task = _WORKFLOWS.get(workflow_id)
        if not task:
            return None

        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)

        task.updated_at = datetime.utcnow()
        return task


# =====================================================
# ⭐ 阶段更新唯一入口
# =====================================================
def update_workflow_stage(
    workflow_id: str,
    stage: WorkflowStage,
    *,
    message: Optional[str] = None,
) -> Optional[WorkflowTask]:
    """
    所有 stage 变化必须走这里
    """
    with _LOCK:
        task = _WORKFLOWS.get(workflow_id)
        if not task:
            return None

        task.stage = stage
        task.progress = _default_progress_for_stage(stage)
        task.message = message or _default_message_for_stage(stage)
        task.updated_at = datetime.utcnow()
        return task


# =====================================================
# 阶段默认进度 / 文案（与前端强对齐）
# =====================================================
def _default_progress_for_stage(stage: WorkflowStage) -> int:
    return {
        WorkflowStage.IDLE: 0,
        WorkflowStage.FILE_READY: 10,
        WorkflowStage.ANALYZING: 30,
        WorkflowStage.ANALYSIS_DONE: 60,
        WorkflowStage.GENERATING: 70,
        WorkflowStage.GENERATED: 100,
        WorkflowStage.ERROR: 0,
    }.get(stage, 0)


def _default_message_for_stage(stage: WorkflowStage) -> str:
    return {
        WorkflowStage.IDLE: "等待上传需求文档",
        WorkflowStage.FILE_READY: "需求文档已上传",
        WorkflowStage.ANALYZING: "正在进行需求分析",
        WorkflowStage.ANALYSIS_DONE: "需求分析完成",
        WorkflowStage.GENERATING: "正在生成测试用例",
        WorkflowStage.GENERATED: "测试用例生成完成",
        WorkflowStage.ERROR: "流程发生错误，可重试",
    }.get(stage, "")


# =====================================================
# 前端状态快照（唯一权威）
# =====================================================
def get_workflow_progress(workflow_id: str) -> Optional[WorkflowProgress]:
    task = _WORKFLOWS.get(workflow_id)
    if not task:
        return None

    return WorkflowProgress(
        stage=task.stage.value,
        progress=task.progress,
        message=task.message,
    )


# =====================================================
# 重置 Workflow（安全重置）
# =====================================================
def reset_workflow(workflow_id: str) -> Optional[WorkflowTask]:
    with _LOCK:
        task = _WORKFLOWS.get(workflow_id)
        if not task:
            return None

        task.stage = WorkflowStage.IDLE
        task.progress = 0
        task.message = "已重置，等待上传需求文档"

        # 清理业务数据
        task.task_id = None
        task.excel_path = None
        task.total_cases = None
        task.analysis_result = None
        task.test_points = None
        task.pdf_path = None
        task.pdf_text = None

        # ⭐ 同时清空补充测试重点（符合直觉）
        task.focus_requirements = None

        task.updated_at = datetime.utcnow()
        return task


# =====================================================
# 序列化（内部 / 调试用）
# =====================================================
def serialize_workflow(task: WorkflowTask) -> dict:
    return {
        "workflow_id": task.workflow_id,
        "stage": task.stage.value,
        "progress": task.progress,
        "message": task.message,
        "task_id": task.task_id,
        "excel_path": task.excel_path,
        "total_cases": task.total_cases,
        "analysis_result": task.analysis_result,
        "test_points": task.test_points,
        "pdf_path": task.pdf_path,

        # ⭐ 新增可观测字段
        "focus_requirements": task.focus_requirements,

        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }
