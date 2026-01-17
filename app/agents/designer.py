#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/13 20:07
# @Author: sulo
from app.agents.llms import GeminiLLM

class TestDesigner:
    def __init__(self):
        self.llm = GeminiLLM()

    def generate(self, plan: list[str]) -> list[dict]:
        test_cases = []
        for i, step in enumerate(plan):
            prompt = f"根据步骤生成测试用例: {step}"
            result = self.llm.complete(prompt)
            test_cases.append({"id": f"TC_{i+1:03d}", "step": step, "description": result})
        return test_cases
