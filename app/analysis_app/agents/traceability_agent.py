# app/analysis_app/agents/traceability_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Dict, Any, List, Optional
import json
import re
import logging

from app.analysis_app.agents.base_agent import BaseAgent


logger = logging.getLogger(__name__)


class TraceabilityAgent(BaseAgent):
    """
    企业级 V4 可追踪性分析 Agent

    目标：
    - 建立需求条目与规则、测试点、问题、风险之间的追踪关系
    - 输出结构与 RequirementTraceabilityResult 对齐
    """

    name = "traceability"

    SYSTEM_PROMPT = (
        "你是一名资深需求评审专家和测试架构师。"
        "你的任务是建立需求条目与规则、测试点、问题、风险之间的追踪关系。"
        "必须只输出 JSON，不允许解释，不允许 markdown。"
    )

    LINK_TYPES = {"rule", "test_point", "issue", "risk"}

    MAX_REQUIREMENTS = 20
    MAX_LINKS = 60

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
            "requirement_items": [],
            "traceability_links": [],
            "uncovered_requirements": [],
            "orphan_rules": [],
            "orphan_test_points": [],
            "recommendations": [],
        }

    # =====================================================
    # Prompt
    # =====================================================

    def _build_prompt(
        self,
        requirement_text: str,
        structure: Optional[Dict[str, Any]] = None,
        rules: Optional[Dict[str, Any]] = None,
        issues: Optional[List[Dict[str, Any]]] = None,
        testability: Optional[Dict[str, Any]] = None,
        risk_report: Optional[Dict[str, Any]] = None,
    ) -> str:
        context = {
            "structure": structure or {},
            "rules": self._simplify_rules(rules or {}),
            "issues": self._simplify_issues(issues or []),
            "testability": self._simplify_testability(testability or {}),
            "risk_report": self._simplify_risk_report(risk_report or {}),
        }

        return f"""
请建立需求追踪关系，并严格输出 JSON 对象。

输出 JSON 格式如下：

{{
  "requirement_items": [
    {{
      "id": "REQ-001",
      "name": "申诉提交",
      "description": "用户满足条件后可提交申诉"
    }}
  ],
  "traceability_links": [
    {{
      "requirement_id": "REQ-001",
      "link_type": "rule",
      "target_id": "RULE-001",
      "target_name": "申诉资格规则",
      "reason": "该规则用于约束申诉提交条件"
    }}
  ],
  "uncovered_requirements": [
    {{
      "requirement_id": "REQ-003",
      "name": "异常处理",
      "reason": "未建立对应规则、测试点或问题追踪关系"
    }}
  ],
  "orphan_rules": [
    {{
      "name": "重复提交限制",
      "reason": "存在规则定义，但未映射到明确需求条目"
    }}
  ],
  "orphan_test_points": [
    {{
      "name": "重复提交校验",
      "reason": "存在测试点，但未映射到明确需求条目"
    }}
  ],
  "recommendations": [
    "建议建立需求-规则-测试点-问题的统一追踪矩阵"
  ]
}}

要求：
1. 必须输出 JSON
2. link_type 只能是 rule / test_point / issue / risk
3. requirement_id 必须采用 REQ-xxx 格式
4. 不允许输出解释
5. 不要臆造不存在的关系
6. requirement_items 只保留核心、可追踪的需求条目
7. uncovered_requirements 只保留确实未覆盖的需求
8. orphan_rules / orphan_test_points 只保留确实无归属的项
9. recommendations 聚焦如何补齐追踪链路

需求文本：

\"\"\"
{requirement_text}
\"\"\"

上下文：

{json.dumps(context, ensure_ascii=False, indent=2)}
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
            max_tokens=2600,
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

    # =====================================================
    # JSON解析
    # =====================================================

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
    # 上下文裁剪
    # =====================================================

    def _simplify_rules(self, rules: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(rules, dict):
            return {}

        return {
            "rules": (rules.get("rules") or [])[:20],
            "conditions": (rules.get("conditions") or [])[:20],
            "states": (rules.get("states") or [])[:20],
            "validations": (rules.get("validations") or [])[:20],
            "exceptions": (rules.get("exceptions") or [])[:20],
        }

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
                }
            )

        return result[:30]

    def _simplify_testability(self, testability: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(testability, dict):
            return {}

        return {
            "test_points": (testability.get("test_points") or [])[:30],
            "coverage_gaps": (testability.get("coverage_gaps") or [])[:20],
        }

    def _simplify_risk_report(self, risk_report: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(risk_report, dict):
            return {}

        return {
            "top_risks": (risk_report.get("top_risks") or [])[:10],
            "high_risks": (risk_report.get("high_risks") or [])[:10],
            "medium_risks": (risk_report.get("medium_risks") or [])[:10],
        }

    # =====================================================
    # normalize
    # =====================================================

    def _normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return self._empty_result()

        requirements = self._normalize_requirement_items(data.get("requirement_items"))[: self.MAX_REQUIREMENTS]
        links = self._normalize_links(data.get("traceability_links"))[: self.MAX_LINKS]

        return {
            "requirement_items": requirements,
            "traceability_links": links,
            "uncovered_requirements": self._normalize_uncovered(data.get("uncovered_requirements")),
            "orphan_rules": self._normalize_orphans(data.get("orphan_rules")),
            "orphan_test_points": self._normalize_orphans(data.get("orphan_test_points")),
            "recommendations": self._normalize_str_list(data.get("recommendations")),
        }

    def _normalize_requirement_items(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        results: List[Dict[str, str]] = []

        for idx, item in enumerate(items, start=1):
            if isinstance(item, dict):
                req_id = str(item.get("id") or item.get("requirement_id") or f"REQ-{idx:03d}").strip()
                name = str(item.get("name") or item.get("title") or "").strip()
                description = str(item.get("description") or "").strip()
            else:
                req_id = f"REQ-{idx:03d}"
                name = str(item or "").strip()
                description = ""

            if not req_id.startswith("REQ-"):
                req_id = f"REQ-{idx:03d}"

            if self._is_blocked_text(req_id, name, description):
                continue

            if not name and not description:
                continue

            if not name:
                name = self._build_short_name(description, default="未命名需求")
            if not description:
                description = name

            results.append(
                {
                    "id": req_id,
                    "name": name,
                    "description": description,
                }
            )

        return self._unique_dict_items(results, keys=("id", "name", "description"))

    def _normalize_links(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        results: List[Dict[str, str]] = []

        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue

            requirement_id = str(item.get("requirement_id") or "").strip()
            link_type = str(item.get("link_type") or "").strip()
            target_id = str(item.get("target_id") or item.get("link_id") or "").strip()
            target_name = str(item.get("target_name") or "").strip()
            reason = str(item.get("reason") or "").strip()

            if not requirement_id.startswith("REQ-"):
                continue

            if link_type not in self.LINK_TYPES:
                continue

            if not target_id:
                if link_type == "rule":
                    target_id = f"RULE-{idx:03d}"
                elif link_type == "test_point":
                    target_id = f"TP-{idx:03d}"
                elif link_type == "issue":
                    target_id = f"ISSUE-{idx:03d}"
                else:
                    target_id = f"RISK-{idx:03d}"

            if not target_name:
                target_name = target_id

            if self._is_blocked_text(requirement_id, link_type, target_id, target_name, reason):
                continue

            results.append(
                {
                    "requirement_id": requirement_id,
                    "link_type": link_type,
                    "target_id": target_id,
                    "target_name": target_name,
                    "reason": reason,
                }
            )

        return self._unique_dict_items(
            results,
            keys=("requirement_id", "link_type", "target_id", "target_name"),
        )

    def _normalize_uncovered(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        results: List[Dict[str, str]] = []

        for idx, item in enumerate(items, start=1):
            if isinstance(item, dict):
                requirement_id = str(item.get("requirement_id") or f"REQ-{idx:03d}").strip()
                name = str(item.get("name") or "").strip()
                reason = str(item.get("reason") or "").strip()
            else:
                requirement_id = f"REQ-{idx:03d}"
                name = str(item or "").strip()
                reason = ""

            if not requirement_id.startswith("REQ-"):
                requirement_id = f"REQ-{idx:03d}"

            if self._is_blocked_text(requirement_id, name, reason):
                continue

            if not name and not reason:
                continue

            if not name:
                name = self._build_short_name(reason, default="未覆盖需求")

            results.append(
                {
                    "requirement_id": requirement_id,
                    "name": name,
                    "reason": reason,
                }
            )

        return self._unique_dict_items(results, keys=("requirement_id", "name", "reason"))

    def _normalize_orphans(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        results: List[Dict[str, str]] = []

        for item in items:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                reason = str(item.get("reason") or "").strip()
            else:
                name = str(item or "").strip()
                reason = ""

            if self._is_blocked_text(name, reason):
                continue

            if not name and not reason:
                continue

            if not name:
                name = self._build_short_name(reason, default="孤立项")

            results.append(
                {
                    "name": name,
                    "reason": reason,
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
    # 工具
    # =====================================================

    def _ensure_list(self, value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if value is None:
            return []
        return [value]

    def _build_short_name(self, text: str, default: str = "未命名项", max_len: int = 16) -> str:
        s = re.sub(r"\s+", " ", str(text or "")).strip()
        if not s:
            return default
        return s if len(s) <= max_len else s[:max_len] + "..."

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

    def run(
        self,
        requirement_text: str,
        structure: Optional[Dict[str, Any]] = None,
        rules: Optional[Dict[str, Any]] = None,
        issues: Optional[List[Dict[str, Any]]] = None,
        testability: Optional[Dict[str, Any]] = None,
        risk_report: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not requirement_text or len(requirement_text.strip()) < 5:
            return self._empty_result()

        prompt = self._build_prompt(
            requirement_text,
            structure,
            rules,
            issues,
            testability,
            risk_report,
        )

        try:
            raw = self._call_llm(prompt)
            json_text = self._extract_json(raw)
            data = self._safe_json(json_text)
            result = self._normalize(data)

            if not result["requirement_items"]:
                return self._empty_result()

            return result

        except Exception as e:
            logger.exception("TraceabilityAgent llm call failed: %s", e)
            return self._empty_result()


traceability_agent = TraceabilityAgent()