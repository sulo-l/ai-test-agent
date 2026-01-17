#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/14 22:44
# @Author: sulo

from app.agents.base import BaseAgent


class RequirementAgent(BaseAgent):
    system_prompt = """
你是【测试需求分析专家】。

你的任务：
1. 根据【原始需求文档】拆分测试模块
2. 【必须】严格处理用户提供的 Additional Test Requirements
3. 每一条 Additional Test Requirement 都必须原样输出
4. 不允许合并、忽略或自行推断

⚠️ 规则（非常重要）：
- 如果存在 Additional Test Requirements，必须全部输出
- 这些内容是【强制覆盖测试点】，不是参考建议

输出 JSON 格式如下：
{
  "modules": [
    {
      "module": "模块名称",
      "requirements": [
        "测试需求点1",
        "测试需求点2"
      ]
    }
  ],
  "mandatory_coverage": [
    "用户输入的补充测试需求1",
    "用户输入的补充测试需求2"
  ]
}
"""

    def build_user_prompt(
        self,
        requirement_text: str,
        additional_requirements: str | None = None
    ) -> str:
        """
        构建 LLM 输入
        """
        prompt = f"""
【原始需求文档】
{requirement_text}
"""

        if additional_requirements:
            prompt += f"""

【Additional Test Requirements（必须全部覆盖，不允许遗漏）】
{additional_requirements}
"""

        prompt += """
请严格按照 system prompt 要求输出 JSON。
"""

        return prompt

    def post_process(self, llm_output: dict) -> dict:
        """
        对 LLM 输出做一次兜底处理，防止 mandatory_coverage 缺失
        """
        if "mandatory_coverage" not in llm_output:
            llm_output["mandatory_coverage"] = []

        # 兜底：确保是 list[str]
        llm_output["mandatory_coverage"] = [
            str(item).strip()
            for item in llm_output.get("mandatory_coverage", [])
            if str(item).strip()
        ]

        return llm_output
