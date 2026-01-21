#! /usr/bin/python3
# coding=utf-8
# app/workflow/merge.py

from typing import List, Optional, Dict, Any


# =====================================================
# 权重配置（后续可调 / 可配置化）
# =====================================================
DEFAULT_WEIGHTS = {
    "focus_requirements": 1.2,   # ⭐ 用户明确指定的测试重点（最高优先级）
    "user_requirement": 1.0,     # 用户补充需求
    "ai_suggestion": 0.8,        # AI 分析建议
    "raw_requirement": 0.4,      # 原始需求文本（兜底）
}


# =====================================================
# 核心：合并生成上下文
# =====================================================
def merge_generation_context(
    *,
    raw_requirements: str,
    user_requirement: Optional[str] = None,
    focus_requirements: Optional[str] = None,   # ⭐ 新增
    analysis_result: Optional[Dict[str, Any]] = None,
    weights: Dict[str, float] = DEFAULT_WEIGHTS,
) -> Dict[str, Any]:
    """
    将【测试重点 + 用户需求 + AI 分析结果 + 原始需求】合并，
    生成测试用例生成阶段的统一输入上下文。

    返回：
        {
            "merged_requirements": str,
            "priority_items": List[str],
            "meta": {...}
        }
    """

    merged_blocks: List[str] = []
    priority_items: List[str] = []

    # =================================================
    # 0️⃣ 用户明确指定的测试重点（最高优先级）
    # =================================================
    if focus_requirements:
        merged_blocks.append(
            f"""【用户指定测试重点｜权重 {weights['focus_requirements']}｜最高优先级】
{focus_requirements.strip()}

⚠️ 要求：
- 以下测试用例必须明显偏向上述重点
- 不允许只覆盖 happy path
"""
        )
        priority_items.append("focus_requirements")

    # =================================================
    # 1️⃣ 用户手写 requirement
    # =================================================
    if user_requirement:
        merged_blocks.append(
            f"""【用户补充测试要求｜权重 {weights['user_requirement']}】
{user_requirement.strip()}"""
        )
        priority_items.append("user_requirement")

    # =================================================
    # 2️⃣ AI 分析建议
    # =================================================
    if analysis_result:
        suggestions = analysis_result.get("suggestions") or []
        issues = analysis_result.get("issues") or []
        risks = analysis_result.get("risks") or []

        if suggestions:
            merged_blocks.append(
                f"""【AI 测试建议｜权重 {weights['ai_suggestion']}】
""" + "\n".join(f"- {s}" for s in suggestions)
            )
            priority_items.append("ai_suggestions")

        if issues:
            merged_blocks.append(
                """【AI 识别的需求缺陷】
""" + "\n".join(f"- {i}" for i in issues)
            )

        if risks:
            merged_blocks.append(
                """【AI 识别的风险点】
""" + "\n".join(f"- {r}" for r in risks)
            )

    # =================================================
    # 3️⃣ 原始需求文本（兜底）
    # =================================================
    if raw_requirements:
        merged_blocks.append(
            f"""【原始需求文档｜权重 {weights['raw_requirement']}】
{raw_requirements.strip()}"""
        )
        priority_items.append("raw_requirement")

    # =================================================
    # 4️⃣ 合并结果
    # =================================================
    merged_text = "\n\n".join(merged_blocks)

    return {
        "merged_requirements": merged_text,
        "priority_items": priority_items,
        "meta": {
            "weights": weights,
            "has_focus_requirements": bool(focus_requirements),
            "has_user_requirement": bool(user_requirement),
            "has_analysis": bool(analysis_result),
        },
    }
