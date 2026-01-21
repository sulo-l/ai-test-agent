from typing import Dict, Any, List
import traceback

from app.agents.orchestrator import Orchestrator
from app.workflow.state import update_workflow


def analyze_requirements(
    *,
    workflow_id: str,
    raw_requirements: str,
) -> Dict[str, Any]:
    """
    AI 需求分析（严格工程版 · 修复版）

    职责边界：
    - 做需求理解、风险识别、测试关注点推演
    - ✅ 生成 test_points（供后续用例生成）
    - ❌ 不控制 workflow 阶段
    """

    # =====================================================
    # 1️⃣ 输入校验
    # =====================================================
    if not raw_requirements or len(raw_requirements.strip()) < 100:
        raise ValueError("需求文本过短，无法进行 AI 需求分析")

    orch = Orchestrator()

    # =====================================================
    # 2️⃣ 调用 Orchestrator
    # =====================================================
    try:
        result = orch.run(
            raw_requirements=raw_requirements,
            confirmed_items=[],
            mode="DELIVERY",
        )
    except Exception as e:
        # 只抛异常，不改状态（由 router 决定）
        raise RuntimeError(f"Orchestrator 执行失败：{str(e)}") from e

    if not isinstance(result, dict):
        raise RuntimeError("Orchestrator 返回非法结果（非 dict）")

    # =====================================================
    # 3️⃣ 解析结果
    # =====================================================
    test_points: List[Dict[str, Any]] = result.get("test_points") or []
    summary = result.get("summary")
    issues = result.get("issues") or []
    risks = result.get("risks") or []
    suggestions = result.get("suggestions") or []

    # =====================================================
    # 4️⃣ 严格校验
    # =====================================================
    if not test_points:
        raise RuntimeError("AI 未生成有效测试关注点（test_points 为空）")

    if not summary:
        summary = {
            "quality": 70,
            "comment": "AI 已完成需求结构分析",
        }

    analysis_result: Dict[str, Any] = {
        "summary": summary,
        "requirements": [
            tp.get("name")
            for tp in test_points
            if isinstance(tp, dict) and tp.get("name")
        ],
        "issues": issues,
        "risks": risks,
        "suggestions": suggestions,
    }

    # =====================================================
    # 5️⃣ 写回 workflow（只写业务数据）
    # =====================================================
    update_workflow(
        workflow_id=workflow_id,
        analysis_result=analysis_result,
        test_points=test_points,
    )

    return analysis_result
