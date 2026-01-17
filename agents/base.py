#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/14 22:44
# @Author: sulo

import json
import re
from llm.client import call_llm


class BaseAgent:
    system_prompt = ""

    def run(self, data: dict):
        raw = call_llm(
            self.system_prompt,
            json.dumps(data, ensure_ascii=False)
        )

        if not raw or not raw.strip():
            raise ValueError("LLM returned empty response")

        raw = raw.strip()

        # ===============================
        # 1️⃣ 优先提取 ```json ``` 代码块（对象或数组）
        # ===============================
        code_block = re.search(r"```json\s*([\s\S]*?)\s*```", raw)
        if code_block:
            return self._safe_json_load(code_block.group(1), raw)

        # ===============================
        # 2️⃣ 再尝试直接解析整个内容（防止无代码块）
        # ===============================
        return self._safe_json_load(raw, raw)

    def _safe_json_load(self, text: str, raw: str):
        text = text.strip()

        # 去掉可能的前后说明文字，只保留 JSON 起止
        json_text = self._extract_json_text(text)
        if not json_text:
            print("[ERROR] No JSON structure found")
            print("====== RAW LLM OUTPUT ======")
            print(raw)
            raise ValueError("No JSON structure found")

        # 第一次尝试
        try:
            return json.loads(json_text)
        except json.JSONDecodeError:
            # 🔴 修复非法转义
            fixed = self._fix_invalid_escapes(json_text)
            try:
                return json.loads(fixed)
            except Exception as e:
                print("[ERROR] JSON parse failed even after escape fix")
                print("====== ORIGINAL JSON TEXT ======")
                print(json_text)
                print("====== FIXED JSON TEXT ======")
                print(fixed)
                print("====== RAW LLM OUTPUT ======")
                print(raw)
                raise e

    def _extract_json_text(self, text: str) -> str | None:
        """
        从文本中提取完整 JSON：
        - 优先数组 [...]
        - 再对象 {...}
        """
        # 先找数组
        array_match = re.search(r"(\[[\s\S]*\])", text)
        if array_match:
            return array_match.group(1)

        # 再找对象
        obj_match = re.search(r"(\{[\s\S]*\})", text)
        if obj_match:
            return obj_match.group(1)

        return None

    def _fix_invalid_escapes(self, text: str) -> str:
        """
        修复非法反斜杠转义
        """
        return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)
