#! /usr/bin/python3
# coding=utf-8
# app/workflow/merge.py

from typing import List, Optional, Dict, Any


# =====================================================
# 权重配置（仅用于优先级描述，不参与内容拼接）
# =====================================================
DEFAULT_WEIGHTS = {
    "focus_requirements": 1.2,   # 用户明确指定的测试重点
    "user_requirement": 1.0,     # 用户补充说明（测试视角）
    "ai_suggestion": 0.8,        # AI 分析建议（风险 / 覆盖）
}


def merge_generation_context(
    *,
    raw_requirements: str,
    user_requirement: Optional[str] = None,
    focus_requirements: Optional[str] = None,
    analysis_result: Optional[Dict[str, Any]] = None,
    weights: Dict[str, float] = DEFAULT_WEIGHTS,
) -> Dict[str, Any]:
    """
    ⚠️【生成阶段工程级硬约束】

    本函数用于【测试点 / 测试用例生成阶段】上下文合并：

    ✅ 允许：
    - 覆盖策略
    - 测试视角
    - 风险提示
    - 覆盖缺口

    ❌ 严禁：
    - raw_requirements 原文进入生成阶段
    - 引入 PDF 中不存在的新功能描述
    """

    merged_blocks: List[str] = []
    priority_items: List[str] = []

    # =================================================
    # 0️⃣ 用户明确指定的【测试覆盖重点】（最高优先级）
    # =================================================
    if focus_requirements:
        merged_blocks.append(
            f"""【测试覆盖重点（最高优先级）】
{focus_requirements.strip()}

⚠️ 强制约束：
- 仅用于指导测试覆盖方式（异常 / 边界 / 组合 / 顺序）
- 不视为新增需求
- 不允许引入 PDF 中不存在的功能
"""
        )
        priority_items.append("focus_requirements")

    # =================================================
    # 1️⃣ 用户补充说明（测试视角，不是需求）
    # =================================================
    if user_requirement:
        merged_blocks.append(
            f"""【用户补充测试说明】
{user_requirement.strip()}

⚠️ 说明：
- 仅用于补充测试思路 / 关注点
- 不等同于需求变更
"""
        )
        priority_items.append("user_requirement")

    # =================================================
    # 2️⃣ AI 分析结果（⚠️ 只允许“风险 / 建议 / 覆盖缺口”）
    # =================================================
    if analysis_result:
        risks = analysis_result.get("risks") or []
        issues = analysis_result.get("issues") or []
        suggestions = analysis_result.get("suggestions") or []

        if risks:
            merged_blocks.append(
                "【AI 识别的测试风险（仅供覆盖参考）】\n"
                + "\n".join(f"- {r}" for r in risks)
                + "\n⚠️ 不允许据此新增业务功能，仅用于补充测试场景"
            )

        if issues:
            merged_blocks.append(
                "【AI 识别的覆盖缺口 / 需求问题】\n"
                + "\n".join(f"- {i}" for i in issues)
            )

        if suggestions:
            merged_blocks.append(
                "【AI 给出的测试建议】\n"
                + "\n".join(f"- {s}" for s in suggestions)
            )

        priority_items.append("ai_analysis")

    # =================================================
    # ❌ 3️⃣ 原始需求文本（raw_requirements）
    # =================================================
    # ⚠️ 明确禁止：
    # - raw_requirements 只能用于 A 分支分析
    # - 在 B 分支生成阶段绝不拼接
    # - 不允许模型基于全文“自由理解需求”

    merged_text = "\n\n".join(merged_blocks).strip()

    return {
        # ⚠️ 注意命名：这是“生成上下文”，不是“需求原文”
        "merged_requirements": merged_text,

        # 仅用于调试 / 解释优先级
        "priority_items": priority_items,

        # 明确的工程元信息（防误用）
        "meta": {
            "has_focus_requirements": bool(focus_requirements),
            "has_user_requirement": bool(user_requirement),
            "has_analysis_result": bool(analysis_result),
            "raw_requirement_used": False,   # ⭐ 工程级声明
            "generation_scope": "coverage_strategy_only",
        },
    }
