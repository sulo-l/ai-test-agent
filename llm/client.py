# -*- coding: utf-8 -*-
# llm/client.py
# NOTE: This file must be saved as UTF-8 (no BOM)

import json
from openai import OpenAI
from settings import get_settings


class LLM:
    """
    OpenAI-compatible LLM wrapper.
    IMPORTANT: client is created per call (Docker/SSE safe).
    """

    def call(self, prompt: str) -> dict:
        cfg = get_settings()

        client = OpenAI(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
        )

        response = client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior QA engineer.\n"
                        "You MUST output valid JSON only.\n"
                        "Do NOT wrap with markdown.\n"
                        "Do NOT add explanations."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.2,
            timeout=120,
        )

        content = response.choices[0].message.content.strip()

        if content.startswith("```"):
            lines = content.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        try:
            return json.loads(content)
        except Exception as e:
            raise RuntimeError(
                "LLM response is not valid JSON after sanitize:\n%s" % content
            ) from e


# backward compatible singleton
llm = LLM()


def get_llm() -> LLM:
    return LLM()
