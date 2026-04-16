# -*- coding: utf-8 -*-
"""
Confirmed Extractor
===================
职责：
- 从 PDF/文本中抽取【高置信、可锚定的信息】
- 从前端补充中抽取【用户明确指定的重点测试方向】
- 输出规范化结果结构，便于用于后续 Prompt 或智能分析
- ❌ 不调用 LLM
- ❌ 不做语义推理
"""

import re
from typing import List, Dict, Any, Optional


# =====================================================
# 内部工具：解析前端重点测试方向
# =====================================================
def _extract_focus_points(requirement: Optional[str]) -> List[Dict[str, str]]:
    """
    将前端补充的 requirement（用户重点测试方向）
    拆解为结构化列表。

    输入可能包含多个句子或用符号分隔的要点。
    """
    if not requirement:
        return []

    # 用逗号、分号、换行符等符号分割
    parts = re.split(r"[，,；;、\n]", requirement)

    results: List[Dict[str, str]] = []

    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) < 2:
            continue

        results.append({
            "text": p,
            "source": "user_focus",  # 标记：来自用户重点补充
        })

    return results


# =====================================================
# 主入口
# =====================================================
def extract_confirmed_items(
        text: str,
        requirement: Optional[str] = None,
) -> Dict[str, Any]:
    """
    从需求原始文本中抽取“高置信”的实体/行为片段，
    同时合并前端指定的重点测试方向。

    返回结构：
    {
        "confirmed_items": [
            {"type": "xxx", "value": "yyy"}
        ],
        "focus_points": [
            {"text": "...", "source": "user_focus"}
        ],
        "prompt_hint": str,
        "has_focus": bool
    }
    """

    confirmed: List[Dict[str, str]] = []

    if text:
        # 常用能够高置信提取的模式
        patterns = [
            ("url", r"https?://[^\s]+"),
            ("api", r"/api/[a-zA-Z0-9/_\-]+"),
            ("action", r"登录|注册|退出|新增|删除|修改|查询"),
            ("entity", r"用户|账号|用户名|密码|验证码|权限|角色"),
            ("status", r"成功|失败|错误|异常|超时"),
        ]

        seen = set()

        for item_type, pattern in patterns:
            for m in re.findall(pattern, text):
                key = f"{item_type}:{m}"
                if key in seen:
                    continue
                seen.add(key)

                confirmed.append({
                    "type": item_type,
                    "value": m.strip(),
                })

    # ================
    # 前端重点测试方向（最高优先级）
    # ================
    focus_points = _extract_focus_points(requirement)

    # ================
    # 生成 Prompt Hint 提示片段
    # ================
    prompt_lines: List[str] = []

    if focus_points:
        prompt_lines.append("【用户明确要求重点关注的测试方向】")
        for fp in focus_points:
            prompt_lines.append(f"- {fp['text']}")

    if confirmed:
        # 仅当存在高置信抽取内容时添加
        prompt_lines.append("\n【从需求文本中识别到的客观实体/行为】")
        for c in confirmed:
            prompt_lines.append(f"- ({c['type']}) {c['value']}")

    # 如果既没用户 focus，又没抽取出高置信实体
    if not prompt_lines:
        prompt_hint = (
            "未从需求文本中识别到明确的高置信实体，"
            "请基于整体需求文本思考测试设计方向。"
        )
    else:
        prompt_hint = "\n".join(prompt_lines)

    return {
        # 抽取到的高置信信息
        "confirmed_items": confirmed,

        # 用户主动指定的重点测试方向
        "focus_points": focus_points,

        # 是否存在用户 • 重点关注方向
        "has_focus": bool(focus_points),

        # 可直接插入 Prompt 的文案片段
        "prompt_hint": prompt_hint,
    }
