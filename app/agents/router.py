#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/13 18:45
# @Author: sulo
# agents/router.py

from app.agents.llms import OpenAILLM, GeminiLLM


class LLMRouter:
    def __init__(self, provider: str = "openai"):
        self.provider = provider

        if provider == "openai":
            self.llm = OpenAILLM()
        elif provider == "gemini":
            self.llm = GeminiLLM()
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def ask(self, prompt: str) -> str:
        return self.llm.ask(prompt)
