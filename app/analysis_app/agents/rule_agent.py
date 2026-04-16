#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/3/8 16:41
# @Author: sulo
# app/analysis_app/agents/rule_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Dict, Any, List
import json
import re
import logging

from app.analysis_app.agents.base_agent import BaseAgent


logger = logging.getLogger(__name__)


class RuleAgent(BaseAgent):
    """
    企业级 V4 业务规则提取 Agent

    作用：
        从需求文本中提取业务规则相关信息，包括：
        - rules：业务规则
        - conditions：前置条件 / 触发条件 / 限制条件
        - states：状态定义与流转
        - validations：数据校验规则
        - exceptions：异常与例外规则
        - unresolved_rules：未闭环规则
        - ambiguous_rules：歧义规则
    """

    name = "rule"

    SYSTEM_PROMPT = (
        "你是一名资深业务分析师和需求评审专家。"
        "你的任务是从需求文本中提取业务规则、条件、状态机、校验规则和异常规则。"
        "必须只输出 JSON，不允许输出解释，不允许输出 markdown。"
    )

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
        "langchain",
        "openai",
        "client",
    ]

    ALLOWED_PRIORITIES = {"high", "medium", "low"}
    ALLOWED_SEVERITIES = {"critical", "major", "minor", "blocker", "suggestion"}

    # =====================================================
    # 空结果
    # =====================================================

    def _empty_result(self) -> Dict[str, Any]:
        return {
            "rules": [],
            "conditions": [],
            "states": [],
            "validations": [],
            "exceptions": [],
            "unresolved_rules": [],
            "ambiguous_rules": [],
        }

    # =====================================================
    # Prompt
    # =====================================================

    def _build_prompt(self, requirement_text: str) -> str:
        return f"""
请从以下需求文本中提取业务规则信息。

请严格输出 JSON 对象，结构如下：

{{
  "rules": [
    {{
      "id": "RULE-001",
      "name": "申诉资格规则",
      "condition": "用户已完成KYC且存在业务限制",
      "action": "允许发起申诉",
      "priority": "high",
      "exception": "审核中事项不可重复提交",
      "source": "原始需求中的相关规则描述"
    }}
  ],
  "conditions": [
    {{
      "id": "COND-001",
      "name": "登录条件",
      "condition": "用户必须已登录",
      "action": "方可进入申诉页面",
      "priority": "medium",
      "exception": "",
      "source": "原始需求中的条件描述"
    }}
  ],
  "states": [
    {{
      "name": "待审核",
      "from_state": "已提交",
      "to_state": "审核通过",
      "trigger": "审核通过操作",
      "guard": "材料齐全且满足规则"
    }}
  ],
  "validations": [
    {{
      "field": "申诉材料",
      "rule": "必须上传至少一份有效材料",
      "message": "缺少有效申诉材料",
      "severity": "major"
    }}
  ],
  "exceptions": [
    {{
      "name": "无可申诉事项",
      "trigger": "用户不存在任何可申诉限制",
      "behavior": "进入暂无可申诉事项页面",
      "recovery": "返回帮助中心或查看限制详情"
    }}
  ],
  "unresolved_rules": [
    "未明确重复提交时的最终处理策略"
  ],
  "ambiguous_rules": [
    "审核通过的判定标准描述不明确"
  ]
}}

要求：
1. 必须输出 JSON 对象
2. 不要输出 markdown，不要输出解释
3. 如果某一类信息不存在，返回空数组
4. 尽量提取明确规则，不要臆造
5. 所有描述必须使用中文
6. states 中字段必须是字符串
7. unresolved_rules / ambiguous_rules 只写真正未闭环或有歧义的规则点
8. 不要输出任何与模型、JSON、系统错误相关的内容
9. rules 关注“什么条件下做什么”
10. conditions 关注“前置/限制/触发条件”
11. validations 关注“字段/参数/输入输出校验”
12. exceptions 关注“异常、例外、失败、兜底、恢复”

需求文本：

\"\"\"
{requirement_text}
\"\"\"
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
    # JSON 提取
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
            "rules": self._normalize_rules(data.get("rules")),
            "conditions": self._normalize_conditions(data.get("conditions")),
            "states": self._normalize_states(data.get("states")),
            "validations": self._normalize_validations(data.get("validations")),
            "exceptions": self._normalize_exceptions(data.get("exceptions")),
            "unresolved_rules": self._normalize_str_list(data.get("unresolved_rules")),
            "ambiguous_rules": self._normalize_str_list(data.get("ambiguous_rules")),
        }

    def _normalize_rules(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        result: List[Dict[str, str]] = []

        for idx, item in enumerate(items, start=1):
            if isinstance(item, dict):
                rule_id = str(item.get("id") or f"RULE-{idx:03d}").strip()
                name = str(item.get("name") or "").strip()
                condition = str(item.get("condition") or "").strip()
                action = str(item.get("action") or item.get("description") or item.get("rule") or "").strip()
                priority = self._normalize_priority(item.get("priority"))
                exception = str(item.get("exception") or "").strip()
                source = str(item.get("source") or "").strip()
            else:
                rule_id = f"RULE-{idx:03d}"
                name = ""
                condition = ""
                action = str(item).strip()
                priority = "medium"
                exception = ""
                source = ""

            if self._is_blocked_text(rule_id, name, condition, action, exception, source):
                continue

            if not action and not condition:
                continue

            if not name:
                name = self._build_short_name(action or condition)

            result.append(
                {
                    "id": rule_id,
                    "name": name,
                    "condition": condition,
                    "action": action,
                    "priority": priority,
                    "exception": exception,
                    "source": source,
                }
            )

        return self._unique_by_keys(result, keys=("name", "condition", "action", "exception"))

    def _normalize_conditions(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        result: List[Dict[str, str]] = []

        for idx, item in enumerate(items, start=1):
            if isinstance(item, dict):
                cond_id = str(item.get("id") or f"COND-{idx:03d}").strip()
                name = str(item.get("name") or "").strip()
                condition = str(item.get("condition") or item.get("description") or "").strip()
                action = str(item.get("action") or "").strip()
                priority = self._normalize_priority(item.get("priority"))
                exception = str(item.get("exception") or "").strip()
                source = str(item.get("source") or "").strip()
            else:
                cond_id = f"COND-{idx:03d}"
                name = ""
                condition = str(item).strip()
                action = ""
                priority = "medium"
                exception = ""
                source = ""

            if self._is_blocked_text(cond_id, name, condition, action, exception, source):
                continue

            if not condition:
                continue

            if not name:
                name = self._build_short_name(condition)

            result.append(
                {
                    "id": cond_id,
                    "name": name,
                    "condition": condition,
                    "action": action,
                    "priority": priority,
                    "exception": exception,
                    "source": source,
                }
            )

        return self._unique_by_keys(result, keys=("name", "condition", "action"))

    def _normalize_states(self, value: Any) -> List[Dict[str, Any]]:
        items = self._ensure_list(value)
        result: List[Dict[str, Any]] = []

        for item in items:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("state") or "").strip()
                from_state = str(item.get("from_state") or "").strip()
                to_state = str(item.get("to_state") or "").strip()
                trigger = str(item.get("trigger") or "").strip()
                guard = str(item.get("guard") or "").strip()

                transitions = self._ensure_list(item.get("transitions"))
                if not to_state and transitions:
                    to_state = " / ".join([str(x).strip() for x in transitions if str(x).strip()])
            else:
                name = str(item).strip()
                from_state = ""
                to_state = ""
                trigger = ""
                guard = ""

            if self._is_blocked_text(name, from_state, to_state, trigger, guard):
                continue

            if not name:
                continue

            result.append(
                {
                    "name": name,
                    "from_state": from_state,
                    "to_state": to_state,
                    "trigger": trigger,
                    "guard": guard,
                }
            )

        return self._unique_by_keys(result, keys=("name", "from_state", "to_state", "trigger", "guard"))

    def _normalize_validations(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        result: List[Dict[str, str]] = []

        for item in items:
            if isinstance(item, dict):
                field = str(item.get("field") or "").strip()
                rule = str(item.get("rule") or item.get("description") or "").strip()
                message = str(item.get("message") or "").strip()
                severity = self._normalize_severity(item.get("severity"))
            else:
                field = ""
                rule = str(item).strip()
                message = ""
                severity = "major"

            if self._is_blocked_text(field, rule, message, severity):
                continue

            if not rule:
                continue

            result.append(
                {
                    "field": field,
                    "rule": rule,
                    "message": message,
                    "severity": severity,
                }
            )

        return self._unique_by_keys(result, keys=("field", "rule", "message"))

    def _normalize_exceptions(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        result: List[Dict[str, str]] = []

        for item in items:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("scenario") or "").strip()
                trigger = str(item.get("trigger") or "").strip()
                behavior = str(item.get("behavior") or item.get("handling") or item.get("description") or "").strip()
                recovery = str(item.get("recovery") or "").strip()
            else:
                name = ""
                trigger = ""
                behavior = str(item).strip()
                recovery = ""

            if self._is_blocked_text(name, trigger, behavior, recovery):
                continue

            if not behavior:
                continue

            if not name:
                name = self._build_short_name(behavior)

            result.append(
                {
                    "name": name,
                    "trigger": trigger,
                    "behavior": behavior,
                    "recovery": recovery,
                }
            )

        return self._unique_by_keys(result, keys=("name", "behavior", "trigger"))

    # =====================================================
    # 工具方法
    # =====================================================

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

    def _normalize_priority(self, value: Any) -> str:
        s = str(value or "").strip().lower()
        return s if s in self.ALLOWED_PRIORITIES else "medium"

    def _normalize_severity(self, value: Any) -> str:
        s = str(value or "").strip().lower()
        return s if s in self.ALLOWED_SEVERITIES else "major"

    def _ensure_list(self, value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if value is None:
            return []
        return [value]

    def _build_short_name(self, text: str, max_len: int = 12) -> str:
        s = re.sub(r"\s+", " ", str(text or "")).strip()
        if not s:
            return "未命名规则"
        return s if len(s) <= max_len else s[:max_len] + "..."

    def _is_blocked_text(self, *parts: Any) -> bool:
        text = " ".join(str(x or "") for x in parts).lower()
        return any(k.lower() in text for k in self.BLOCKED_KEYWORDS)

    def _unique_by_keys(self, items: List[Dict[str, Any]], keys: tuple[str, ...]) -> List[Dict[str, Any]]:
        seen = set()
        result: List[Dict[str, Any]] = []

        for item in items:
            key = tuple(str(item.get(k) or "").strip() for k in keys)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)

        return result

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
            logger.exception("RuleAgent llm call failed: %s", e)
            return self._empty_result()

        json_text = self._extract_json(raw)
        data = self._safe_json(json_text)

        if not isinstance(data, dict):
            return self._empty_result()

        return self._normalize(data)