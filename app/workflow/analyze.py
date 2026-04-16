#! /usr/bin/python3
# coding=utf-8
# @Author: sulo
"""
AnalysisFlow
============
A 分支 · 需求分析系统流程编排器

职责：
- 调用共享输入准备层（PreparedRequirement）
- 调用 A 分支 Agent：
  - RequirementAnalysisAgent
  - TestPointAnalyzer（分析级）
- 将结果事件化（供 Controller / SSE 使用）
- 写回 workflow 业务数据（⚠️ 仅限 models.py 已定义字段）
"""

from typing import Generator, Dict, Any

from app.workflow.state import update_workflow_data
from app.services.requirement_preparer import PreparedRequirement
from app.agents.requirement_agent import RequirementAnalysisAgent
from app.agents.test_point_analyzer import TestPointAnalyzer


# =====================================================
# A 分支主流程（流式）
# =====================================================

def run_analysis_flow(
    *,
    workflow_id: str,
    prepared: PreparedRequirement,
    module_name: str = "需求模块",
) -> Generator[Dict[str, Any], None, Dict[str, Any]]:
    """
    A 分支 · 需求分析系统主流程（流式）

    ⚠️ 注意：
    - 不更新 workflow.stage（由 Controller 负责）
    - 只写入 WorkflowTask 中已定义字段
    """

    # =================================================
    # 0️⃣ 输入校验
    # =================================================
    if not prepared or not prepared.final_text.strip():
        empty_result = {
            "quality": 0,
            "comment": "未检测到有效需求文本，无法进行需求分析",
        }

        update_workflow_data(
            workflow_id=workflow_id,
            requirement_quality=None,
            test_point_spec=None,   # ⭐ NEW：显式清空，防止脏数据
        )

        yield {
            "type": "done",
            "payload": empty_result,
        }
        return empty_result

    yield {
        "type": "log",
        "payload": "已完成需求输入准备，开始进行需求质量分析",
    }

    # =================================================
    # 1️⃣ 需求质量分析
    # =================================================
    req_agent = RequirementAnalysisAgent()
    req_result = req_agent.run(prepared)

    update_workflow_data(
        workflow_id=workflow_id,
        requirement_quality=req_result,
    )

    yield {
        "type": "requirement_summary",
        "payload": {
            "score": req_result.score,
            "level": req_result.level,
            "summary": req_result.summary,
        },
    }

    for issue in req_result.issues:
        yield {
            "type": "requirement_issue",
            "payload": issue.dict(),
        }

    # =================================================
    # 2️⃣ 测试点说明书生成（A 分支 · 分析级）
    # =================================================
    yield {
        "type": "log",
        "payload": "开始生成测试点说明书（分析级，不生成测试用例）",
    }

    tp_analyzer = TestPointAnalyzer()
    analysis_tp_spec = tp_analyzer.run(
        module_name=module_name,
        prepared=prepared,
    )

    # =================================================
    # ⭐ NEW：把【测试点说明书】写回 workflow
    # 这是 B 分支生成测试用例的【唯一数据源】
    # =================================================
    update_workflow_data(
        workflow_id=workflow_id,
        test_point_spec={
            "module": analysis_tp_spec.module,
            "sections": [
                {
                    "category": section.category,
                    "points": [
                        {
                            "title": p.title,
                            "description": p.description,
                            "priority": p.priority,
                            "rationale": p.rationale,
                        }
                        for p in section.points
                    ],
                }
                for section in analysis_tp_spec.sections
            ],
        },
    )

    # =================================================
    # 仅用于前端展示（不再是唯一来源）
    # =================================================
    yield {
        "type": "test_point_spec",
        "payload": {
            "module": analysis_tp_spec.module,
            "sections": [
                {
                    "category": section.category,
                    "points": [
                        {
                            "title": p.title,
                            "description": p.description,
                            "priority": p.priority,
                            "rationale": p.rationale,
                        }
                        for p in section.points
                    ],
                }
                for section in analysis_tp_spec.sections
            ],
        },
    }

    # =================================================
    # 3️⃣ 完成
    # =================================================
    final_payload = {
        "score": req_result.score,
        "level": req_result.level,
        "module": analysis_tp_spec.module,
        "total_test_points": sum(
            len(s.points) for s in analysis_tp_spec.sections
        ),
    }

    yield {
        "type": "done",
        "payload": final_payload,
    }

    return final_payload
