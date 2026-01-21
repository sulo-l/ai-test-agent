# -*- coding: utf-8 -*-
# app/llm/client.py
# NOTE: This file must be saved as UTF-8 (no BOM)

import json
from openai import OpenAI

from app.settings import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
)


class LLM:
    """
    OpenAI-compatible LLM wrapper (Chat Completions).
    - Compatible with 国内中转 / Gemini / Claude
    - Stable for SSE / multithread
    - Always returns parsed JSON (dict)
    """

    def call(self, prompt: str) -> dict:
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL,
        )

        try:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                timeout=120,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a senior QA engineer.\n"
                            "You MUST output valid JSON only.\n"
                            "Do NOT wrap with markdown.\n"
                            "Do NOT add explanations.\n"
                            "If unsure, output an empty JSON object {}."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=0.3,
            )
        except Exception as e:
            raise RuntimeError(f"LLM request failed: {e}") from e

        # ===============================
        # ✅ 读取内容（ChatCompletion 标准）
        # ===============================
        try:
            content = response.choices[0].message.content
        except Exception:
            raise RuntimeError(f"Invalid LLM response structure: {response}")

        if not content:
            raise RuntimeError("LLM returned empty response")

        content = content.strip()

        # ===============================
        # ✅ 清理 ```json / ``` 包裹
        # ===============================
        if content.startswith("```"):
            lines = content.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        # ===============================
        # ✅ 强制 JSON 解析
        # ===============================
        try:
            return json.loads(content)
        except Exception as e:
            raise RuntimeError(
                "LLM response is not valid JSON after sanitize:\n"
                f"{content}"
            ) from e


# =====================================================
# backward compatible singleton
# =====================================================
llm = LLM()


def get_llm() -> LLM:
    return LLM()
