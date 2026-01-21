#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/14 22:45
# @Author: sulo

from typing import Dict, Any
from app.agents.base import BaseAgent


class TestCaseAgent(BaseAgent):
    """
    TestCaseAgent（工程级 · 强制覆盖感知版）

    - 针对【单个测试点】生成测试用例
    - mandatory / focus 测试点会被放大
    """

    system_prompt = """
你是【资深测试工程师】。

你的任务：
- 针对给定的【测试点】生成测试用例
- 输出必须可直接用于测试执行
- 不要解释，不要额外文本，只输出 JSON

⚠️ 关键规则（必须严格遵守）：
1. 每条测试用例【必须包含 precondition】
2. precondition 表示【执行该用例前必须满足的状态】
3. precondition 不能包含操作步骤（操作只能写在 steps）
4. precondition 不允许为空
5. 如果无特殊前置条件，请明确写：
   “无特殊前置条件”

⚠️ 覆盖规则：
- 如果测试点标记为 mandatory / focus，说明这是【用户指定重点】
- 对于重点测试点，必须生成【更严格的测试用例】
- 必须覆盖：正常 / 异常 / 边界 / 极端情况
- 禁止只生成 happy path

输出 JSON 结构如下：
{
  "case_name": "",
  "module": "",
  "test_point_id": "",
  "test_point_name": "",
  "origin": "mandatory | inferred",
  "coverage_item": "",
  "precondition": "",
  "steps": [],
  "expected": ""
}
"""

    # =====================================================
    # 构建 Prompt
    # =====================================================
    def build_user_prompt(self, test_point: Dict[str, Any]) -> str:
        prompt = f"""
【测试点】
ID: {test_point.get("id")}
名称: {test_point.get("name")}
模块: {test_point.get("module")}
"""

        if test_point.get("origin") == "mandatory":
            prompt += f"""

【⚠️ 用户指定重点测试点（必须重点覆盖）】
覆盖来源：
{test_point.get("source_requirement")}

要求：
- 该测试用例必须体现该重点
- 必须考虑异常 / 边界 / 极端情况
"""

        prompt += """
请严格按照 system prompt 输出 JSON。
"""
        return prompt

    # =====================================================
    # 输出工程化（🔥关键兜底点）
    # =====================================================
    def post_process(
        self,
        llm_output: Dict[str, Any],
        test_point: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        将 LLM 输出转为工程可用测试用例
        """

        # ===== steps 兜底 =====
        steps = llm_output.get("steps") or []
        if isinstance(steps, str):
            steps = [steps]

        # ===== precondition 工程级兜底 =====
        precondition = llm_output.get("precondition")

        if not precondition or not str(precondition).strip():
            precondition = "无特殊前置条件"

        return {
            "case_name": llm_output.get("case_name") or f"{test_point.get('name')} - 测试用例",
            "module": llm_output.get("module") or test_point.get("module"),
            "test_point_id": test_point.get("id"),
            "test_point_name": test_point.get("name"),
            "origin": test_point.get("origin"),
            "coverage_item": test_point.get("source_requirement"),
            "precondition": precondition,
            "steps": steps,
            "expected": llm_output.get("expected", ""),
        }
