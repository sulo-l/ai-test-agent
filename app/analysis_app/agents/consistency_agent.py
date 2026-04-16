#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/3/8 16:54
# @Author: sulo
# app/analysis_app/agents/consistency_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Dict, Any, List, Optional
import json
import re
import logging

from app.analysis_app.agents.base_agent import BaseAgent


logger = logging.getLogger(__name__)


class ConsistencyAgent(BaseAgent):
    """
    企业级 V4 一致性分析 Agent

    作用：
        从需求文本及已有结构化结果中识别一致性问题，包括：
        - rule_conflicts：业务规则冲突
        - state_conflicts：状态流转冲突
        - role_conflicts：角色/权限冲突
        - flow_conflicts：流程前后矛盾
        - term_conflicts：术语口径不一致
        - data_conflicts：数据口径不一致
        - consistency_gaps：一致性缺口
        - recommendations：修正建议
    """

    name = "consistency"

    SYSTEM_PROMPT = (
        "你是一名资深需求评审专家，擅长识别需求中的前后矛盾、规则冲突、状态冲突、"
        "角色权限冲突、术语不一致和数据口径不一致问题。"
        "请基于输入信息识别一致性问题。"
        "必须只输出 JSON，不允许输出解释，不允许输出 markdown。"
    )

    BLOCKED_KEYWORDS = [
        "模型返回格式异常",
        "输出异常",
        "解析失败",
        "json",
        "markdown",
        "提示词",
        "llm",
        "api key",
        "base url",
        "模型配置",
        "系统错误",
        "analysis failed",
        "openai",
        "langchain",
        "client",
    ]

    # =====================================================
    # Prompt
    # =====================================================

    def _build_prompt(
        self,
        requirement_text: str,
        structure: Optional[Dict[str, Any]] = None,
        rules: Optional[Dict[str, Any]] = None,
        issues: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        structure = structure or {}
        rules = rules or {}
        issues = issues or []

        safe_issues = self._simplify_issues(issues)

        context = {
            "structure": structure,
            "rules": rules,
            "issues": safe_issues,
        }

        return f"""
请对以下需求进行一致性分析，并严格输出 JSON 对象。

输出格式必须如下：

{{
  "rule_conflicts": [
    {{
      "title": "业务规则冲突标题",
      "message": "冲突描述",
      "reason": "为什么判定冲突",
      "related_terms": ["相关术语1", "相关术语2"]
    }}
  ],
  "state_conflicts": [
    {{
      "title": "状态冲突标题",
      "message": "冲突描述",
      "reason": "为什么判定冲突",
      "related_terms": ["状态A", "状态B"]
    }}
  ],
  "role_conflicts": [
    {{
      "title": "角色权限冲突标题",
      "message": "冲突描述",
      "reason": "为什么判定冲突",
      "related_terms": ["用户", "管理员"]
    }}
  ],
  "flow_conflicts": [
    {{
      "title": "流程冲突标题",
      "message": "冲突描述",
      "reason": "为什么判定冲突",
      "related_terms": ["提交", "审核"]
    }}
  ],
  "term_conflicts": [
    {{
      "title": "术语不一致标题",
      "message": "不一致描述",
      "reason": "为什么判定不一致",
      "related_terms": ["申诉", "Appeal"]
    }}
  ],
  "data_conflicts": [
    {{
      "title": "数据口径冲突标题",
      "message": "冲突描述",
      "reason": "为什么判定冲突",
      "related_terms": ["状态", "类型", "金额"]
    }}
  ],
  "consistency_gaps": [
    "当前需求中未明确统一术语定义，可能导致理解偏差"
  ],
  "recommendations": [
    "统一关键术语定义、角色权限口径、状态流转规则和数据字段说明"
  ]
}}

要求：
1. 必须输出 JSON 对象
2. 不允许输出 markdown，不允许输出解释
3. 所有内容使用中文
4. 若某类信息不存在，返回空数组
5. 不要臆造不存在的冲突，必须基于文本和上下文判断
6. 重点关注：
   - 同一业务对象是否前后规则矛盾
   - 状态定义和流转是否冲突
   - 角色与权限描述是否不一致
   - 流程前置条件/结果是否矛盾
   - 术语、字段、枚举值是否口径不一致
   - 已识别问题中是否暴露出一致性缺口
7. 不要输出任何与模型、解析、JSON、提示词、系统错误相关的内容
8. recommendation 应聚焦“如何统一口径、如何补齐定义、如何消除冲突”

需求文本：

\"\"\"
{requirement_text}
\"\"\"

已有上下文：

{json.dumps(context, ensure_ascii=False, indent=2)}
"""

    # =====================================================
    # LLM 调用
    # =====================================================

    def _call_llm(self, prompt: str) -> str:
        result = self.llm.call(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            force_json_object=True,
            temperature=0.2,
            max_tokens=2600,
            timeout=120,
        )
        return (result or "").strip()

    # =====================================================
    # JSON 提取 / 修复
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
    # 空结构
    # =====================================================

    def _empty_result(self) -> Dict[str, Any]:
        return {
            "rule_conflicts": [],
            "state_conflicts": [],
            "role_conflicts": [],
            "flow_conflicts": [],
            "term_conflicts": [],
            "data_conflicts": [],
            "consistency_gaps": [],
            "recommendations": [],
        }

    # =====================================================
    # 输入裁剪
    # =====================================================

    def _simplify_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []

        for item in issues or []:
            if not isinstance(item, dict):
                continue

            result.append(
                {
                    "id": str(item.get("id") or "").strip(),
                    "level": str(item.get("level") or "").strip(),
                    "category": str(item.get("category") or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "message": str(item.get("message") or "").strip(),
                    "severity": str(item.get("severity") or "").strip(),
                }
            )

        return result[:30]

    # =====================================================
    # 归一化
    # =====================================================

    def _normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            data = {}

        return {
            "rule_conflicts": self._normalize_conflict_items(data.get("rule_conflicts")),
            "state_conflicts": self._normalize_conflict_items(data.get("state_conflicts")),
            "role_conflicts": self._normalize_conflict_items(data.get("role_conflicts")),
            "flow_conflicts": self._normalize_conflict_items(data.get("flow_conflicts")),
            "term_conflicts": self._normalize_conflict_items(data.get("term_conflicts")),
            "data_conflicts": self._normalize_conflict_items(data.get("data_conflicts")),
            "consistency_gaps": self._normalize_str_list(data.get("consistency_gaps")),
            "recommendations": self._normalize_str_list(data.get("recommendations")),
        }

    def _normalize_conflict_items(self, value: Any) -> List[Dict[str, Any]]:
        items = self._ensure_list(value)
        result: List[Dict[str, Any]] = []

        for item in items:
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                message = str(item.get("message") or "").strip()
                reason = str(item.get("reason") or "").strip()
                related_terms = self._normalize_str_list(item.get("related_terms"))
            else:
                title = ""
                message = str(item or "").strip()
                reason = ""
                related_terms = []

            if not message:
                continue

            if self._is_blocked_text(title, message, reason, related_terms):
                continue

            if not title:
                title = self._build_short_title(message)

            result.append(
                {
                    "title": title,
                    "message": message,
                    "reason": reason,
                    "related_terms": related_terms,
                }
            )

        return self._unique_conflicts(result)

    def _normalize_str_list(self, value: Any) -> List[str]:
        items = self._ensure_list(value)
        result: List[str] = []

        for item in items:
            s = str(item or "").strip()
            if not s:
                continue
            if self._is_blocked_text(s):
                continue
            if s in result:
                continue
            result.append(s)

        return result

    # =====================================================
    # 工具方法
    # =====================================================

    def _ensure_list(self, value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if value is None:
            return []
        return [value]

    def _build_short_title(self, text: str, max_len: int = 16) -> str:
        s = re.sub(r"\s+", " ", str(text or "")).strip()
        if not s:
            return "一致性问题"
        return s if len(s) <= max_len else s[:max_len] + "..."

    def _is_blocked_text(self, *parts: Any) -> bool:
        text = " ".join(
            [
                " ".join(p) if isinstance(p, list) else str(p or "")
                for p in parts
            ]
        ).lower()

        return any(k.lower() in text for k in self.BLOCKED_KEYWORDS)

    def _unique_keep_order(self, items: List[str]) -> List[str]:
        seen = set()
        result: List[str] = []

        for item in items:
            value = str(item or "").strip()
            if not value:
                continue
            if value in seen:
                continue
            seen.add(value)
            result.append(value)

        return result

    def _unique_conflicts(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        result: List[Dict[str, Any]] = []

        for item in items:
            key = (
                str(item.get("title") or "").strip(),
                str(item.get("message") or "").strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(item)

        return result

    # =====================================================
    # 主入口
    # =====================================================

    def run(
        self,
        requirement_text: str,
        structure: Optional[Dict[str, Any]] = None,
        rules: Optional[Dict[str, Any]] = None,
        issues: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if not requirement_text or len(requirement_text.strip()) < 5:
            return self._empty_result()

        prompt = self._build_prompt(
            requirement_text=requirement_text,
            structure=structure,
            rules=rules,
            issues=issues,
        )

        try:
            raw = self._call_llm(prompt)
        except Exception as e:
            logger.exception("ConsistencyAgent llm call failed: %s", e)
            return self._empty_result()

        json_text = self._extract_json(raw)
        data = self._safe_json(json_text)

        if not isinstance(data, dict):
            return self._empty_result()

        return self._normalize(data)


# 单例
consistency_agent = ConsistencyAgent()