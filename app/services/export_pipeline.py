# -*- coding: utf-8 -*-
"""
Export Pipeline (FINAL · Excel Authority · Workflow Safe)

职责：
- 读取 workflow.final_cases
- 导出 JSON 和 Excel 文件
- 更新 workflow 数据：保存导出路径，更新状态
"""

from typing import Dict, Any, List, Optional
import os
import json
import logging
from datetime import datetime

from app.workflow.state import (
    get_workflow,
    update_workflow_data,
    update_workflow_stage,
)
from app.workflow.models import WorkflowStage

logger = logging.getLogger(__name__)


# =====================================================
# 工具函数
# =====================================================
def _now_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_filename(name: str) -> str:
    """
    安全文件名，去除不合法字符
    """
    return "".join(
        c for c in str(name or "") if c.isalnum() or c in ("_", "-", ".")
    ).strip()


def _normalize_owner(owner: Optional[str]) -> Optional[str]:
    """
    统一清洗责任人：
    - None / 空字符串 / 全空格 => None
    - 其他情况 => strip 后返回
    """
    value = (owner or "").strip()
    return value or None


def _inject_owner_into_cases(
    cases: List[Dict[str, Any]],
    owner: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    给导出用例统一补 owner：
    - case 已有 owner -> 保留
    - case 没有 owner -> 用传入 owner 补
    """
    safe_owner = _normalize_owner(owner)
    if not cases:
        return []

    out: List[Dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue

        item = dict(case)
        if safe_owner and not str(item.get("owner", "")).strip():
            item["owner"] = safe_owner
        out.append(item)

    return out


# =====================================================
# ✅ Controller 唯一允许调用（只导 Excel）
# =====================================================
def export_testcases(
    test_cases: List[Dict[str, Any]],
    *,
    requirement_id: Optional[str] = None,
    owner: Optional[str] = None,
) -> str:
    """
    Controller 专用接口（轻量）
    - 不读 workflow
    - 不写状态
    - 只返回 excel_path
    """

    if not test_cases:
        raise RuntimeError("export_testcases: test_cases is empty")

    rid = (requirement_id or "").strip() or "UNKNOWN_REQUIREMENT"
    safe_cases = _inject_owner_into_cases(test_cases, owner)

    # 延迟导入，避免循环导入
    from app.services.excel_exporter import export_cases_to_excel

    result = export_cases_to_excel(
        cases=safe_cases,
        requirement_id=rid,
    )

    excel_path = result.get("file_path")
    if not excel_path or not os.path.exists(excel_path):
        raise RuntimeError("export_testcases: Excel export failed")

    return excel_path


# =====================================================
# ✅ Controller 兼容函数
# =====================================================
def export_testcases_and_register_file(
    *,
    testcases: List[Dict[str, Any]],
    requirement_id: Optional[str] = None,
    owner: Optional[str] = None,
) -> str:
    """
    controller 兼容函数：
    - 调用 export_testcases
    - 返回给 controller 用的 file_id
    """

    excel_path = export_testcases(
        test_cases=testcases,
        requirement_id=requirement_id,
        owner=owner,
    )

    # controller 只关心 file_id
    # file_id = excel 文件名（不带路径、不带后缀）
    file_id = os.path.splitext(os.path.basename(excel_path))[0]

    return file_id


# =====================================================
# 🔧 Workflow 级完整导出（唯一闭环）
# =====================================================
def export_pipeline(
    *,
    workflow_id: str,
    export_dir: Optional[str] = None,
    owner: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Export Pipeline（最终版）

    职责：
    - 读取 workflow.final_cases
    - 导出：
        1️⃣ testcases.json
        2️⃣ testcases.xlsx（权威）
        3️⃣ meta.json
    - 写回：
        - workflow.excel_path
        - workflow.total_cases
        - workflow.stage → DONE
    """

    # 获取 workflow 数据
    task = get_workflow(workflow_id)
    if not task:
        raise RuntimeError(f"Workflow with ID {workflow_id} not found.")

    # =====================================================
    # 状态校验（只允许导出一次）
    # =====================================================
    if task.stage not in {
        WorkflowStage.CASE_EXPORTING,
        WorkflowStage.DONE,
    }:
        raise RuntimeError(
            f"Workflow not ready for export (current stage: {task.stage})"
        )

    raw_final_cases: List[Dict[str, Any]] = task.final_cases or []
    if not raw_final_cases:
        raise RuntimeError("No final_cases to export")

    safe_owner = _normalize_owner(owner)

    # 如果 workflow 自己已经存过 owner，也允许兜底取一下
    workflow_owner = None
    try:
        workflow_owner = _normalize_owner(getattr(task, "owner", None))
    except Exception:
        workflow_owner = None

    final_owner = safe_owner or workflow_owner
    final_cases = _inject_owner_into_cases(raw_final_cases, final_owner)

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
    # 2️⃣ 导出 JSON（审计 / 回溯）
    # =====================================================
    json_payload = {
        "workflow_id": workflow_id,
        "requirement_id": requirement_id,
        "generated_at": timestamp,
        "total_cases": len(final_cases),
        "owner": final_owner or "",
        "test_cases": final_cases,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            json_payload,
            f,
            ensure_ascii=False,
            indent=2,
        )

    # =====================================================
    # 3️⃣ 导出 Excel（唯一权威）
    # =====================================================
    from app.services.excel_exporter import export_cases_to_excel

    excel_result = export_cases_to_excel(
        cases=final_cases,
        requirement_id=requirement_id,
    )

    excel_path = excel_result.get("file_path")
    if not excel_path:
        raise RuntimeError("Excel export failed")

    # =====================================================
    # 4️⃣ 导出 Meta（工程审计）
    # =====================================================
    meta_payload = {
        "workflow_id": workflow_id,
        "requirement_id": requirement_id,
        "stage": WorkflowStage.DONE.value,
        "total_cases": len(final_cases),
        "owner": final_owner or "",
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "exported_at": timestamp,
        "files": {
            "json": json_path,
            "excel": excel_path,
        },
        "review": {
            "has_design_notes": bool(task.design_notes),
            "has_critic_result": bool(task.critic_result),
        },
    }

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            meta_payload,
            f,
            ensure_ascii=False,
            indent=2,
        )

    # =====================================================
    # 5️⃣ 写回 workflow（唯一副作用点）
    # =====================================================
    update_workflow_data(
        workflow_id=workflow_id,
        excel_path=excel_path,
        total_cases=len(final_cases),
    )

    update_workflow_stage(workflow_id, WorkflowStage.DONE)

    logger.info(
        "[EXPORT_DONE] workflow=%s cases=%s owner=%s",
        workflow_id,
        len(final_cases),
        final_owner or "",
    )

    return {
        "json_path": json_path,
        "excel_path": excel_path,
        "meta_path": meta_path,
        "total_cases": len(final_cases),
        "owner": final_owner or "",
    }