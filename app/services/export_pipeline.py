#! /usr/bin/python3
# coding=utf-8
# @Author: sulo
# @Desc: Export Pipeline (JSON / Excel / Meta · Company-grade)

from typing import Dict, Any, List, Optional
import os
import json
import logging
from datetime import datetime

from app.workflow.state import get_workflow, update_workflow
from app.workflow.models import WorkflowStage

# Excel exporter（你现有的）
from app.services.excel_exporter import export_cases_to_excel

# ===============================
# Logger
# ===============================
logger = logging.getLogger(__name__)


# =====================================================
# 工具函数
# =====================================================
def _now_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_filename(name: str) -> str:
    return "".join(
        c for c in name if c.isalnum() or c in ("_", "-", ".")
    ).strip()


# =====================================================
# Export Pipeline 主入口
# =====================================================
def export_pipeline(
    *,
    workflow_id: str,
    export_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Export Pipeline（工程级 · 稳定版）

    职责：
    - 从 workflow 读取最终产物
    - 导出：
        1. testcases.json（完整结构化）
        2. testcases.xlsx（交付给 QA / PM）
        3. meta.json（可追溯元数据）
    - 写回 workflow.excel_path / updated_at
    - 不修改业务逻辑、不依赖 SSE

    返回：
    {
        "json_path": str,
        "excel_path": str,
        "meta_path": str,
    }
    """

    task = get_workflow(workflow_id)
    if not task:
        raise RuntimeError("Workflow not found")

    if task.stage != WorkflowStage.GENERATED:
        raise RuntimeError(
            f"Workflow not ready for export (stage={task.stage})"
        )

    test_cases: List[Dict[str, Any]] = task.test_cases or []
    if not test_cases:
        raise RuntimeError("No test cases to export")

    requirement_id = task.requirement_id or workflow_id
    safe_req = _safe_filename(requirement_id)

    # =====================================================
    # 1️⃣ 目录准备
    # =====================================================
    if not export_dir:
        export_dir = os.path.join(
            os.getcwd(),
            "exports",
            safe_req,
        )

    os.makedirs(export_dir, exist_ok=True)

    timestamp = _now_str()

    json_path = os.path.join(
        export_dir,
        f"testcases_{timestamp}.json",
    )
    meta_path = os.path.join(
        export_dir,
        f"meta_{timestamp}.json",
    )

    # =====================================================
    # 2️⃣ 导出 JSON（完整结构）
    # =====================================================
    json_payload = {
        "workflow_id": workflow_id,
        "requirement_id": task.requirement_id,
        "generated_at": timestamp,
        "summary": task.analysis_result.get("summary")
        if task.analysis_result else None,
        "test_cases": test_cases,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            json_payload,
            f,
            ensure_ascii=False,
            indent=2,
        )

    logger.info("[EXPORT] JSON exported: %s", json_path)

    # =====================================================
    # 3️⃣ 导出 Excel（给业务用）
    # =====================================================
    excel_path = export_cases_to_excel(
        test_cases=test_cases,
        requirement_id=task.requirement_id,
    )

    logger.info("[EXPORT] Excel exported: %s", excel_path)

    # =====================================================
    # 4️⃣ 导出 Meta（可追溯）
    # =====================================================
    meta_payload = {
        "workflow_id": workflow_id,
        "requirement_id": task.requirement_id,
        "stage": task.stage.value,
        "total_cases": len(test_cases),
        "created_at": task.created_at.isoformat()
        if task.created_at else None,
        "exported_at": timestamp,
        "files": {
            "json": json_path,
            "excel": excel_path,
        },
        "review": {
            "design_notes": bool(task.design_notes),
            "critic_result": bool(task.critic_result),
        },
    }

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            meta_payload,
            f,
            ensure_ascii=False,
            indent=2,
        )

    logger.info("[EXPORT] Meta exported: %s", meta_path)

    # =====================================================
    # 5️⃣ 写回 workflow（唯一副作用）
    # =====================================================
    update_workflow(
        workflow_id=workflow_id,
        excel_path=excel_path,
    )

    return {
        "json_path": json_path,
        "excel_path": excel_path,
        "meta_path": meta_path,
    }
