#! /usr/bin/python3
# coding=utf-8
# @Author: sulo

from typing import Dict, Any, List
import uuid

from app.agents.base import BaseAgent


class TestPointAgent(BaseAgent):
    """
    TestPointAgent（工程级版本 · 强化 mandatory / focus）

    - 只生成【测试点】
    - 不生成测试用例
    - mandatory 测试点具备“放大权重”
    """

    system_prompt = """
你是【资深测试工程师】。

你的任务：
1. 根据输入的【测试子任务】生成测试点
2. 如果子任务来自 mandatory coverage（强制覆盖），必须重点体现
3. 测试点必须可验证、可执行
4. 不生成测试用例（只生成测试点）

⚠️ 重要规则：
- 如果任务中包含【coverage_item】，说明这是用户指定的【强制覆盖项】
- 对于强制覆盖项，必须生成【多个测试点】：
  - 正常流程
  - 异常流程
  - 边界条件
- 【强制覆盖项的测试点数量不得少于 4 条】
- 每一个测试点都必须标注 source_requirement

输出 JSON 格式如下：
{
  "module": "",
  "test_points": [
    {
      "name": "",
      "source_requirement": null,
      "priority": "P2",
      "category": "functional"
    }
  ]
}
"""

    # =====================================================
    # 构建 Prompt
    # =====================================================
    def build_user_prompt(self, plan: dict) -> str:
        prompt = f"""
【测试子任务】
{plan.get("instruction")}
"""

        if plan.get("type") == "mandatory":
            prompt += f"""

【⚠️ 用户指定强制覆盖项（最高优先级，不允许弱化）】
{plan.get("coverage_item")}

要求：
- 该覆盖项必须被拆解为多个可执行测试点
- 每个测试点都应体现该覆盖项
"""

        prompt += """
请严格按照 system prompt 输出 JSON，不要包含多余解释。
"""

        return prompt

    # =====================================================
    # 输出工程化（非常关键）
    # =====================================================
    def post_process(self, llm_output: dict, plan: dict) -> Dict[str, Any]:
        """
        将 LLM 输出转为 Workflow 可直接消费的 test_points
        """

        module = llm_output.get("module") or plan.get("module") or "未分类模块"
        raw_points = llm_output.get("test_points") or []

        processed_points: List[Dict[str, Any]] = []

        for idx, tp in enumerate(raw_points):
            processed_points.append({
                # ⭐ 全局唯一 ID
                "id": f"TP-{uuid.uuid4().hex[:8]}",

                # ⭐ 展示 & 生成都依赖
                "name": tp.get("name") or f"未命名测试点-{idx + 1}",

                # ⭐ 模块归属
                "module": module,

                # ⭐ 强制覆盖来源
                "source_requirement": (
                    plan.get("coverage_item")
                    if plan.get("type") == "mandatory"
                    else tp.get("source_requirement")
                ),

                # ⭐ 生成策略标识
                "origin": (
                    "mandatory"
                    if plan.get("type") == "mandatory"
                    else "inferred"
                ),

                # ⭐ 后续统计 / 高亮可用
                "is_focus": plan.get("type") == "mandatory",

                # ⭐ 兜底字段
                "priority": tp.get("priority", "P2"),
                "category": tp.get("category", "functional"),
            })

        # =================================================
        # 🛟 mandatory 数量兜底（工程级防稀释）
        # =================================================
        if plan.get("type") == "mandatory" and len(processed_points) < 4:
            missing = 4 - len(processed_points)
            for i in range(missing):
                processed_points.append({
                    "id": f"TP-{uuid.uuid4().hex[:8]}",
                    "name": f"{plan.get('coverage_item')} - 补充测试点-{i + 1}",
                    "module": module,
                    "source_requirement": plan.get("coverage_item"),
                    "origin": "mandatory",
                    "is_focus": True,
                    "priority": "P1",
                    "category": "edge",
                })

        return {
            "module": module,
            "test_points": processed_points,
        }
