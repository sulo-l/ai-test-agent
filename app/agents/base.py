# -*- coding: utf-8 -*-
# app/agents/base.py

import json
import re
from typing import Any, Dict

from app.llm.client import llm


class BaseAgent:
    """
    所有 Agent 的基类
    统一通过 llm.call(prompt) 调用大模型
    """

    system_prompt: str = ""

    def build_user_prompt(self, data: dict) -> str:
        """
        子类必须实现：构建 user prompt
        """
        raise NotImplementedError

    def post_process(self, llm_output: dict, data: dict) -> dict:
        """
        子类可选：对 LLM 输出做补充 / 修正
        """
        return llm_output

    def run(self, data: dict) -> Dict[str, Any]:
        if not self.system_prompt:
            raise RuntimeError("Agent 未定义 system_prompt")

        user_prompt = self.build_user_prompt(data)

        prompt = f"""
{self.system_prompt}

{user_prompt}
"""

        result = llm.call(prompt)

        if not isinstance(result, dict):
            raise RuntimeError("LLM 返回非 JSON")

        return self.post_process(result, data)
