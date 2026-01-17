#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/14 22:44
# @Author: sulo

from app.agents.base import BaseAgent


class TestPointAgent(BaseAgent):
    system_prompt = """
你是【资深测试工程师】。

你的任务：
1. 根据输入的【测试子任务】生成测试点
2. 如果子任务来自 mandatory coverage（强制覆盖），必须明确体现
3. 测试点必须可验证、可执行
4. 不生成测试用例（只生成测试点）

⚠️ 重要规则：
- 如果任务中包含【coverage_item】，说明这是用户指定的【强制覆盖项】
- 对于强制覆盖项，必须生成【多个测试点】，至少包含：
  - 正常流程
  - 异常流程
  - 边界条件
- 每一个测试点都必须标注 source_requirement

输出 JSON 格式如下：
{
  "module": "",
  "test_points": [
    {
      "id": "",
      "name": "",
      "source_requirement": null
    }
  ]
}
"""

    def build_user_prompt(self, plan: dict) -> str:
        """
        构建 LLM 输入
        """
        prompt = f"""
【测试子任务】
{plan.get("instruction")}
"""

        if plan.get("type") == "mandatory":
            prompt += f"""

【强制覆盖项（必须逐条覆盖，不允许遗漏）】
{plan.get("coverage_item")}
"""

        prompt += """
请严格按照 system prompt 输出 JSON。
"""

        return prompt

    def post_process(self, llm_output: dict, plan: dict) -> dict:
        """
        对输出结果进行补充与兜底
        """
        test_points = llm_output.get("test_points", [])

        for idx, tp in enumerate(test_points):
            # 补 id
            if not tp.get("id"):
                tp["id"] = f"TP-{idx + 1}"

            # mandatory 测试点绑定来源
            if plan.get("type") == "mandatory":
                tp["source_requirement"] = plan.get("coverage_item")
            else:
                tp.setdefault("source_requirement", None)

        llm_output["test_points"] = test_points
        return llm_output
