#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/3/8 16:41
# @Author: sulo
# app/analysis_app/agents/testability_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Dict, Any, List
import json
import re
import logging

from app.analysis_app.agents.base_agent import BaseAgent


logger = logging.getLogger(__name__)


class TestabilityAgent(BaseAgent):
    """
    企业级 V4 可测试性分析 Agent

    作用：
        分析需求的测试可行性，包括：

        - test_points（测试点）
        - coverage_gaps（测试覆盖缺口）
        - automation_candidates（自动化测试候选）
        - test_data_requirements（测试数据需求）
        - environment_dependencies（环境依赖）
        - observability_gaps（可观测性缺口）
        - acceptance_criteria_gaps（验收标准缺口）
    """

    name = "testability"

    SYSTEM_PROMPT = (
        "你是一名资深测试架构师。"
        "你的任务是分析需求的测试可行性。"
        "必须只输出 JSON，不允许输出解释，不允许输出 markdown。"
    )

    ALLOWED_PRIORITY = {"high", "medium", "low"}

    BLOCKED_KEYWORDS = [
        "json",
        "markdown",
        "llm",
        "提示词",
        "模型",
        "解析",
        "api key",
        "base url",
        "系统错误",
        "openai",
        "langchain",
        "client",
    ]

    # =====================================================
    # 空结果
    # =====================================================

    def _empty_result(self) -> Dict[str, Any]:
        return {
            "test_points": [],
            "coverage_gaps": [],
            "automation_candidates": [],
            "test_data_requirements": [],
            "environment_dependencies": [],
            "observability_gaps": [],
            "acceptance_criteria_gaps": [],
        }

    # =====================================================
    # Prompt
    # =====================================================

    def _build_prompt(self, requirement_text: str) -> str:
        return f"""
请分析以下需求的可测试性，并严格输出 JSON。

输出 JSON 格式如下：

{{
  "test_points": [
    {{
      "name": "用户提交申诉",
      "type": "功能",
      "precondition": "用户已登录且已完成KYC",
      "expected": "用户可成功提交申诉",
      "trace_to": "申诉提交流程"
    }}
  ],
  "coverage_gaps": [
    "未说明申诉失败时的处理逻辑"
  ],
  "automation_candidates": [
    {{
      "name": "申诉提交流程",
      "reason": "流程稳定且可重复",
      "priority": "high"
    }}
  ],
  "test_data_requirements": [
    "已完成KYC用户",
    "存在业务限制的用户"
  ],
  "environment_dependencies": [
    "用户账户系统",
    "KYC服务"
  ],
  "observability_gaps": [
    "未说明关键状态变化的日志或埋点要求"
  ],
  "acceptance_criteria_gaps": [
    "未明确提交成功、提交失败、重复提交的验收标准"
  ]
}}

要求：

1. 必须输出 JSON
2. 不允许输出 markdown
3. 不允许解释
4. 若不存在信息返回空数组
5. 所有内容使用中文
6. automation_candidates.priority 只能是 high / medium / low
7. test_points 应尽量对应真实可验证场景，不要泛泛而谈
8. acceptance_criteria_gaps 应聚焦可验收标准缺失
9. observability_gaps 应聚焦日志、埋点、状态可观测性、告警等要求缺失
10. 不要输出与模型、JSON、系统错误相关的内容

需求文本：

\"\"\"
{requirement_text}
\"\"\"
"""

    # =====================================================
    # LLM调用
    # =====================================================

    def _call_llm(self, prompt: str) -> str:
        result = self.llm.call(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            force_json_object=True,
            temperature=0.2,
            max_tokens=2400,
            timeout=120,
        )
        return (result or "").strip()

    # =====================================================
    # JSON提取
    # =====================================================

    def _strip_fence(self, text: str) -> str:
        return re.sub(r"```json|```", "", text or "", flags=re.IGNORECASE).strip()

    def _extract_json(self, text: str) -> str:
        text = self._strip_fence(text)

        start = text.find("{")
        if start < 0:
            return text

        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(text)):
            ch = text[i]

            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue

            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

        return text

    def _safe_json(self, raw: str) -> Dict[str, Any]:
        if not raw:
            return {}

        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass

        try:
            fixed = raw.replace("\n", " ").replace("\t", " ")
            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
            parsed = json.loads(fixed)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass

        try:
            fixed = raw.replace("'", '"')
            fixed = fixed.replace("\n", " ").replace("\t", " ")
            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
            parsed = json.loads(fixed)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    # =====================================================
    # 归一化
    # =====================================================

    def _normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return self._empty_result()

        return {
            "test_points": self._normalize_test_points(data.get("test_points")),
            "coverage_gaps": self._normalize_str_list(data.get("coverage_gaps")),
            "automation_candidates": self._normalize_automation(data.get("automation_candidates")),
            "test_data_requirements": self._normalize_str_list(data.get("test_data_requirements")),
            "environment_dependencies": self._normalize_str_list(data.get("environment_dependencies")),
            "observability_gaps": self._normalize_str_list(data.get("observability_gaps")),
            "acceptance_criteria_gaps": self._normalize_str_list(data.get("acceptance_criteria_gaps")),
        }

    def _normalize_test_points(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        results: List[Dict[str, str]] = []

        for item in items:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                typ = str(item.get("type") or "").strip()
                precondition = str(item.get("precondition") or "").strip()
                expected = str(item.get("expected") or "").strip()
                trace_to = str(item.get("trace_to") or "").strip()

                # 兼容旧格式
                module = str(item.get("module") or "").strip()
                scenario = str(item.get("scenario") or "").strip()
                description = str(item.get("description") or "").strip()

                if not name:
                    name = scenario or description or module
                if not expected:
                    expected = description
            else:
                text = str(item).strip()
                name = text
                typ = ""
                precondition = ""
                expected = text
                trace_to = ""

            if self._is_blocked_text(name, typ, precondition, expected, trace_to):
                continue

            if not name and not expected:
                continue

            results.append(
                {
                    "name": name,
                    "type": typ,
                    "precondition": precondition,
                    "expected": expected,
                    "trace_to": trace_to,
                }
            )

        return self._unique_dict_items(results, keys=("name", "expected", "trace_to"))

    def _normalize_automation(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        results: List[Dict[str, str]] = []

        for item in items:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                reason = str(item.get("reason") or "").strip()
                priority = str(item.get("priority") or "medium").strip().lower()

                # 兼容旧格式
                typ = str(item.get("type") or "").strip()
                if not name and typ:
                    name = typ
            else:
                name = ""
                reason = str(item).strip()
                priority = "medium"

            if priority not in self.ALLOWED_PRIORITY:
                priority = "medium"

            if self._is_blocked_text(name, reason, priority):
                continue

            if not reason and not name:
                continue

            results.append(
                {
                    "name": name,
                    "reason": reason,
                    "priority": priority,
                }
            )

        return self._unique_dict_items(results, keys=("name", "reason"))

    def _normalize_str_list(self, value: Any) -> List[str]:
        items = self._ensure_list(value)
        results: List[str] = []

        for item in items:
            s = str(item or "").strip()
            if not s:
                continue
            if self._is_blocked_text(s):
                continue
            if s in results:
                continue
            results.append(s)

        return results

    # =====================================================
    # 工具方法
    # =====================================================

    def _ensure_list(self, value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if value is None:
            return []
        return [value]

    def _is_blocked_text(self, *parts: Any) -> bool:
        text = " ".join(str(x or "") for x in parts).lower()
        return any(k.lower() in text for k in self.BLOCKED_KEYWORDS)

    def _unique_dict_items(
        self,
        items: List[Dict[str, str]],
        keys: tuple[str, ...],
    ) -> List[Dict[str, str]]:
        seen = set()
        results: List[Dict[str, str]] = []

        for item in items:
            key = tuple(str(item.get(k) or "").strip() for k in keys)
            if key in seen:
                continue
            seen.add(key)
            results.append(item)

        return results

    # =====================================================
    # 主入口
    # =====================================================

    def run(self, requirement_text: str) -> Dict[str, Any]:
        if not requirement_text or len(requirement_text.strip()) < 5:
            return self._empty_result()

        prompt = self._build_prompt(requirement_text)

        try:
            raw = self._call_llm(prompt)
        except Exception as e:
            logger.exception("TestabilityAgent llm call failed: %s", e)
            return self._empty_result()

        json_text = self._extract_json(raw)
        data = self._safe_json(json_text)

        if not isinstance(data, dict):
            return self._empty_result()

        return self._normalize(data)