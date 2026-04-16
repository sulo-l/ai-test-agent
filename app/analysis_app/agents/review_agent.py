#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/3/8 16:42
# @Author: sulo
# app/analysis_app/agents/review_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Dict, Any, List, Optional
import json
import re
import logging

from app.analysis_app.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class ReviewAgent(BaseAgent):
    """
    企业级 V4 需求分析复核 Agent

    作用：
        对需求分析流水线的中间结果进行 AI 自检与复核，包括：

        - missing_findings（遗漏项）
        - duplicate_findings（重复项）
        - category_corrections（分类修正建议）
        - severity_corrections（严重级别修正建议）
        - suggestion_improvements（建议优化）
        - final_top_issues（最终关键问题）
        - overall_review（总体复核结论）
    """

    name = "review"

    SYSTEM_PROMPT = (
        "你是一名资深需求评审专家和QA架构师。"
        "你的任务是复核已有的需求分析结果，找出遗漏、重复、不准确分类、严重级别不合理以及建议不充分的问题。"
        "必须只输出 JSON，不允许输出解释，不允许输出 markdown。"
    )

    ALLOWED_QUALITY = {"excellent", "good", "fair", "poor"}
    ALLOWED_DECISION = {"pass", "conditional_pass", "fail"}
    ALLOWED_SEVERITIES = {"blocker", "critical", "major", "minor", "suggestion"}

    # 与 models.py 对齐后的标准分类
    CANONICAL_CATEGORIES = {
        "完整性",
        "清晰性",
        "一致性",
        "业务规则",
        "流程逻辑",
        "异常处理",
        "边界场景",
        "状态流转",
        "数据定义",
        "接口契约",
        "依赖约束",
        "权限安全",
        "合规性",
        "可测试性",
        "可追踪性",
        "可维护性",
        "可扩展性",
        "性能",
        "可观测性",
        "需求质量",
    }

    # LLM 常见别名 -> 标准分类
    CATEGORY_ALIASES = {
        "安全": "权限安全",
        "权限": "权限安全",
        "权限控制": "权限安全",
        "角色权限": "权限安全",
        "数据": "数据定义",
        "数据口径": "数据定义",
        "数据约束": "数据定义",
        "状态机": "状态流转",
        "状态": "状态流转",
        "状态流转": "状态流转",
        "依赖": "依赖约束",
        "依赖接口": "依赖约束",
        "接口": "接口契约",
        "接口定义": "接口契约",
        "接口契约": "接口契约",
        "测试": "可测试性",
        "测试性": "可测试性",
        "追踪": "可追踪性",
        "追溯": "可追踪性",
        "可追踪": "可追踪性",
        "合规": "合规性",
    }

    BLOCKED_KEYWORDS = [
        "json",
        "markdown",
        "llm",
        "提示词",
        "解析",
        "api key",
        "base url",
        "系统错误",
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
        issues: List[Dict[str, Any]],
        structure: Optional[Dict[str, Any]] = None,
        rules: Optional[Dict[str, Any]] = None,
        risks: Optional[Dict[str, Any]] = None,
        testability: Optional[Dict[str, Any]] = None,
    ) -> str:
        structure = structure or {}
        rules = rules or {}
        risks = risks or {}
        testability = testability or {}

        payload = {
            "issues": self._simplify_issues(issues or []),
            "structure": self._simplify_structure(structure),
            "rules": self._simplify_rules(rules),
            "risks": self._simplify_risks(risks),
            "testability": self._simplify_testability(testability),
        }

        return f"""
请复核以下需求分析结果，重点检查：

1. 是否遗漏了关键问题
2. 是否存在重复问题
3. 是否存在问题分类不准确
4. 是否存在问题严重级别不合理
5. 是否存在建议过于空泛或不可执行
6. 是否需要提炼最终关键问题 Top Issues
7. 是否应给出最终复核裁决（pass / conditional_pass / fail）

请严格输出 JSON 对象，格式如下：

{{
  "missing_findings": [
    {{
      "item_id": "",
      "reason": "遗漏原因",
      "suggestion": "补充建议",
      "title": "遗漏的关键问题标题",
      "message": "遗漏问题描述",
      "category": "完整性"
    }}
  ],
  "duplicate_findings": [
    {{
      "item_id": "",
      "reason": "为什么判定重复",
      "suggestion": "建议合并处理",
      "issue_ids": ["ISSUE-001", "ISSUE-005"]
    }}
  ],
  "category_corrections": [
    {{
      "item_id": "ISSUE-003",
      "reason": "修正原因",
      "suggestion": "建议改为更准确分类",
      "issue_id": "ISSUE-003",
      "from": "清晰性",
      "to": "业务规则"
    }}
  ],
  "severity_corrections": [
    {{
      "item_id": "ISSUE-004",
      "reason": "严重级别偏高或偏低的原因",
      "suggestion": "建议调整严重级别",
      "issue_id": "ISSUE-004",
      "from": "major",
      "to": "critical"
    }}
  ],
  "suggestion_improvements": [
    {{
      "item_id": "ISSUE-002",
      "reason": "原建议不够具体",
      "suggestion": "补充更可执行的建议",
      "issue_id": "ISSUE-002",
      "original": "补充错误处理和重试策略",
      "improved": "补充失败场景、错误码、回滚逻辑、幂等约束和重试机制"
    }}
  ],
  "final_top_issues": [
    "问题1",
    "问题2",
    "问题3"
  ],
  "overall_review": {{
    "quality": "good",
    "decision": "conditional_pass",
    "summary": "整体复核结论",
    "should_refine": true,
    "gate_reason": [
      "存在关键业务规则未闭环",
      "异常处理定义不足"
    ]
  }}
}}

要求：
1. 必须输出 JSON 对象
2. 不允许输出 markdown
3. 不允许输出解释性文字
4. 如果某类内容不存在，返回空数组
5. overall_review.quality 只能是 excellent / good / fair / poor
6. overall_review.decision 只能是 pass / conditional_pass / fail
7. overall_review.should_refine 只能是 true 或 false
8. 不要臆造不存在的问题，必须基于需求文本和现有分析结果判断
9. final_top_issues 最多输出 5 条，必须是最关键、最影响交付质量的问题
10. severity_corrections 的 to 只能是 blocker / critical / major / minor / suggestion
11. category_corrections 的 to 必须使用业务分类，如：完整性 / 清晰性 / 一致性 / 业务规则 / 异常处理 / 边界场景 / 状态流转 / 数据定义 / 接口契约 / 依赖约束 / 权限安全 / 合规性 / 可测试性 / 可追踪性 / 可维护性 / 可扩展性 / 性能 / 可观测性 / 需求质量

需求文本：

\"\"\"
{requirement_text}
\"\"\"

已有分析结果：

{json.dumps(payload, ensure_ascii=False, indent=2)}
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
            max_tokens=6000,
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
                    "severity": str(item.get("severity") or "").strip(),
                    "category": str(item.get("category") or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "message": str(item.get("message") or "").strip()[:300],
                    "suggestion": str(item.get("suggestion") or "").strip()[:200],
                    "solution": str(item.get("solution") or "").strip()[:200],
                }
            )

        return result[:40]

    def _simplify_structure(self, structure: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(structure, dict):
            return {}
        return {
            "actors": (structure.get("actors") or [])[:10],
            "modules": (structure.get("modules") or [])[:10],
            "business_goals": (structure.get("business_goals") or [])[:8],
            "missing_sections": (structure.get("missing_sections") or [])[:10],
            "workflows": self._shrink_list_of_dicts(structure.get("workflows") or [], limit=8, field_limit=6),
            "interfaces": self._shrink_list_of_dicts(structure.get("interfaces") or [], limit=8, field_limit=6),
        }

    def _simplify_rules(self, rules: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(rules, dict):
            return {}
        return {
            "rules": self._shrink_list_of_dicts(rules.get("rules") or [], limit=15, field_limit=6),
            "conditions": self._shrink_list_of_dicts(rules.get("conditions") or [], limit=10, field_limit=6),
            "states": self._shrink_list_of_dicts(rules.get("states") or [], limit=10, field_limit=6),
            "validations": self._shrink_list_of_dicts(rules.get("validations") or [], limit=10, field_limit=6),
            "exceptions": self._shrink_list_of_dicts(rules.get("exceptions") or [], limit=10, field_limit=6),
            "unresolved_rules": (rules.get("unresolved_rules") or [])[:10],
            "ambiguous_rules": (rules.get("ambiguous_rules") or [])[:10],
        }

    def _simplify_risks(self, risks: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(risks, dict):
            return {}
        return {
            "top_risks": (risks.get("top_risks") or risks.get("topRisks") or [])[:10],
            "high_risks": self._shrink_list_of_dicts(risks.get("high_risks") or [], limit=8, field_limit=6),
            "medium_risks": self._shrink_list_of_dicts(risks.get("medium_risks") or [], limit=8, field_limit=6),
            "summary": risks.get("risk_summary") or risks.get("riskSummary") or "",
        }

    def _simplify_testability(self, testability: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(testability, dict):
            return {}
        return {
            "test_points": self._shrink_list_of_dicts(testability.get("test_points") or [], limit=15, field_limit=5),
            "coverage_gaps": (testability.get("coverage_gaps") or [])[:10],
            "acceptance_criteria_gaps": (testability.get("acceptance_criteria_gaps") or [])[:10],
        }

    def _shrink_list_of_dicts(
        self,
        items: List[Any],
        limit: int = 10,
        field_limit: int = 6,
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []

        for item in items[:limit]:
            if not isinstance(item, dict):
                continue

            row: Dict[str, Any] = {}
            for idx, (k, v) in enumerate(item.items()):
                if idx >= field_limit:
                    break
                if isinstance(v, (str, int, float, bool)) or v is None:
                    row[str(k)] = v
                elif isinstance(v, list):
                    row[str(k)] = v[:5]
                elif isinstance(v, dict):
                    row[str(k)] = {kk: vv for kk, vv in list(v.items())[:5]}
                else:
                    row[str(k)] = str(v)
            result.append(row)

        return result

    # =====================================================
    # 归一化
    # =====================================================

    def _normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            data = {}

        normalized = {
            "missing_findings": self._normalize_missing_findings(data.get("missing_findings")),
            "duplicate_findings": self._normalize_duplicate_findings(data.get("duplicate_findings")),
            "category_corrections": self._normalize_category_corrections(data.get("category_corrections")),
            "severity_corrections": self._normalize_severity_corrections(data.get("severity_corrections")),
            "suggestion_improvements": self._normalize_suggestion_improvements(data.get("suggestion_improvements")),
            "final_top_issues": self._normalize_final_top_issues(data.get("final_top_issues")),
            "overall_review": self._normalize_overall_review(data.get("overall_review")),
        }

        normalized["overall_review"]["summary"] = self._build_review_summary_if_empty(normalized)
        return normalized

    def _normalize_missing_findings(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        results: List[Dict[str, str]] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            title = str(item.get("title") or "").strip()
            message = str(item.get("message") or "").strip()
            category = self._normalize_category(item.get("category"))
            reason = str(item.get("reason") or "").strip()
            suggestion = str(item.get("suggestion") or "").strip()
            item_id = str(item.get("item_id") or "").strip()

            if self._is_blocked_text(title, message, category, reason, suggestion, item_id):
                continue
            if not message:
                continue

            if not title:
                title = self._build_short_name(message, default="复核遗漏项")

            results.append(
                {
                    "item_id": item_id,
                    "reason": reason,
                    "suggestion": suggestion,
                    "title": title,
                    "message": message,
                    "category": category,
                }
            )

        return self._unique_dict_items(results, keys=("title", "message", "category"))

    def _normalize_duplicate_findings(self, value: Any) -> List[Dict[str, Any]]:
        items = self._ensure_list(value)
        results: List[Dict[str, Any]] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            item_id = str(item.get("item_id") or "").strip()
            reason = str(item.get("reason") or "").strip()
            suggestion = str(item.get("suggestion") or "").strip()
            issue_ids = self._ensure_list(item.get("issue_ids"))
            issue_ids = [str(x).strip() for x in issue_ids if str(x).strip()]

            if self._is_blocked_text(item_id, reason, suggestion, issue_ids):
                continue
            if len(issue_ids) < 2:
                continue

            results.append(
                {
                    "item_id": item_id,
                    "reason": reason,
                    "suggestion": suggestion,
                    "issue_ids": self._unique_keep_order(issue_ids)[:10],
                }
            )

        return self._unique_dict_items(results, keys=("reason", "suggestion"))

    def _normalize_category_corrections(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        results: List[Dict[str, str]] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            item_id = str(item.get("item_id") or item.get("issue_id") or "").strip()
            source = self._normalize_category(item.get("from"), allow_empty=True)
            target = self._normalize_category(item.get("to"))
            reason = str(item.get("reason") or "").strip()
            suggestion = str(item.get("suggestion") or "").strip()

            if self._is_blocked_text(item_id, source, target, reason, suggestion):
                continue
            if not item_id or not target:
                continue

            results.append(
                {
                    "item_id": item_id,
                    "reason": reason,
                    "suggestion": suggestion,
                    "issue_id": item_id,
                    "from": source,
                    "to": target,
                }
            )

        return self._unique_dict_items(results, keys=("issue_id", "from", "to"))

    def _normalize_severity_corrections(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        results: List[Dict[str, str]] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            item_id = str(item.get("item_id") or item.get("issue_id") or "").strip()
            source = self._normalize_severity(item.get("from"), allow_empty=True)
            target = self._normalize_severity(item.get("to"))
            reason = str(item.get("reason") or "").strip()
            suggestion = str(item.get("suggestion") or "").strip()

            if self._is_blocked_text(item_id, source, target, reason, suggestion):
                continue
            if not item_id or not target:
                continue

            results.append(
                {
                    "item_id": item_id,
                    "reason": reason,
                    "suggestion": suggestion,
                    "issue_id": item_id,
                    "from": source,
                    "to": target,
                }
            )

        return self._unique_dict_items(results, keys=("issue_id", "from", "to"))

    def _normalize_suggestion_improvements(self, value: Any) -> List[Dict[str, str]]:
        items = self._ensure_list(value)
        results: List[Dict[str, str]] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            item_id = str(item.get("item_id") or item.get("issue_id") or "").strip()
            original = str(item.get("original") or "").strip()
            improved = str(item.get("improved") or "").strip()
            reason = str(item.get("reason") or "").strip()
            suggestion = str(item.get("suggestion") or "").strip()

            if self._is_blocked_text(item_id, original, improved, reason, suggestion):
                continue
            if not improved:
                continue

            results.append(
                {
                    "item_id": item_id,
                    "reason": reason,
                    "suggestion": suggestion,
                    "issue_id": item_id,
                    "original": original,
                    "improved": improved,
                }
            )

        return self._unique_dict_items(results, keys=("issue_id", "original", "improved"))

    def _normalize_final_top_issues(self, value: Any) -> List[str]:
        items = self._ensure_list(value)
        results: List[str] = []

        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            if self._is_blocked_text(text):
                continue
            if text in results:
                continue
            results.append(text)

        return results[:5]

    def _normalize_overall_review(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            value = {}

        quality = str(value.get("quality") or "fair").strip().lower()
        if quality not in self.ALLOWED_QUALITY:
            quality = "fair"

        should_refine = bool(value.get("should_refine", False))

        decision = str(value.get("decision") or "").strip().lower()
        if decision not in self.ALLOWED_DECISION:
            decision = self._infer_decision(quality=quality, should_refine=should_refine)

        summary = str(value.get("summary") or "").strip()

        gate_reason_raw = self._ensure_list(value.get("gate_reason"))
        gate_reason: List[str] = []
        for item in gate_reason_raw:
            text = str(item or "").strip()
            if not text:
                continue
            if self._is_blocked_text(text):
                continue
            if text in gate_reason:
                continue
            gate_reason.append(text)

        return {
            "quality": quality,
            "decision": decision,
            "summary": summary,
            "should_refine": should_refine,
            "gate_reason": gate_reason[:8],
        }

    def _infer_decision(self, quality: str, should_refine: bool) -> str:
        if quality == "excellent":
            return "pass"
        if quality == "good" and not should_refine:
            return "pass"
        if quality == "poor":
            return "fail"
        if quality == "fair":
            return "conditional_pass"
        if should_refine:
            return "conditional_pass"
        return "conditional_pass"

    # =====================================================
    # 分类 / 严重级别归一
    # =====================================================

    def _normalize_category(self, value: Any, allow_empty: bool = False) -> str:
        text = str(value or "").strip()
        if not text:
            return "" if allow_empty else "需求质量"

        if text in self.CANONICAL_CATEGORIES:
            return text

        mapped = self.CATEGORY_ALIASES.get(text)
        if mapped:
            return mapped

        lower_map = {k.lower(): v for k, v in self.CATEGORY_ALIASES.items()}
        canonical_lower = {x.lower(): x for x in self.CANONICAL_CATEGORIES}

        low = text.lower()
        if low in lower_map:
            return lower_map[low]
        if low in canonical_lower:
            return canonical_lower[low]

        return "" if allow_empty else "需求质量"

    def _normalize_severity(self, value: Any, allow_empty: bool = False) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return "" if allow_empty else "major"

        alias = {
            "high": "critical",
            "medium": "major",
            "low": "minor",
            "suggest": "suggestion",
        }
        text = alias.get(text, text)

        if text in self.ALLOWED_SEVERITIES:
            return text
        return "" if allow_empty else "major"

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
        text = " ".join(
            " ".join(str(x or "") for x in p) if isinstance(p, list) else str(p or "")
            for p in parts
        ).lower()
        return any(k.lower() in text for k in self.BLOCKED_KEYWORDS)

    def _build_short_name(self, text: str, default: str = "未命名项", max_len: int = 16) -> str:
        s = re.sub(r"\s+", " ", str(text or "")).strip()
        if not s:
            return default
        return s if len(s) <= max_len else s[:max_len] + "..."

    def _unique_keep_order(self, items: List[str]) -> List[str]:
        seen = set()
        result: List[str] = []

        for item in items:
            value = str(item).strip()
            if not value:
                continue
            if value in seen:
                continue
            seen.add(value)
            result.append(value)

        return result

    def _unique_dict_items(
        self,
        items: List[Dict[str, Any]],
        keys: tuple[str, ...],
    ) -> List[Dict[str, Any]]:
        seen = set()
        results: List[Dict[str, Any]] = []

        for item in items:
            key = tuple(str(item.get(k) or "").strip() for k in keys)
            if key in seen:
                continue
            seen.add(key)
            results.append(item)

        return results

    def _build_review_summary_if_empty(self, normalized: Dict[str, Any]) -> str:
        overall = normalized.get("overall_review", {}) or {}
        summary = str(overall.get("summary") or "").strip()
        if summary:
            return summary

        missing_count = len(normalized.get("missing_findings", []) or [])
        dup_count = len(normalized.get("duplicate_findings", []) or [])
        cat_count = len(normalized.get("category_corrections", []) or [])
        sev_count = len(normalized.get("severity_corrections", []) or [])
        sug_count = len(normalized.get("suggestion_improvements", []) or [])
        decision = str(overall.get("decision") or "conditional_pass").strip()

        if missing_count or dup_count or cat_count or sev_count or sug_count:
            return (
                f"已完成自动复核；发现遗漏 {missing_count} 项、重复 {dup_count} 项、"
                f"分类修正 {cat_count} 项、严重级别修正 {sev_count} 项、建议优化 {sug_count} 项，"
                f"复核结论为 {decision}。"
            )

        return f"已完成自动复核，未发现明显新增复核项，复核结论为 {decision}。"

    def _empty_result(
        self,
        quality: str = "fair",
        decision: str = "conditional_pass",
        summary: str = "",
        should_refine: bool = False,
    ) -> Dict[str, Any]:
        return {
            "missing_findings": [],
            "duplicate_findings": [],
            "category_corrections": [],
            "severity_corrections": [],
            "suggestion_improvements": [],
            "final_top_issues": [],
            "overall_review": {
                "quality": quality,
                "decision": decision,
                "summary": summary,
                "should_refine": should_refine,
                "gate_reason": [],
            },
        }

    # =====================================================
    # 主入口
    # =====================================================

    def run(
        self,
        requirement_text: str,
        issues: List[Dict[str, Any]],
        structure: Optional[Dict[str, Any]] = None,
        rules: Optional[Dict[str, Any]] = None,
        risks: Optional[Dict[str, Any]] = None,
        testability: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not requirement_text or len(requirement_text.strip()) < 5:
            return self._empty_result(
                quality="poor",
                decision="fail",
                summary="需求文本为空，无法进行复核。",
                should_refine=False,
            )

        prompt = self._build_prompt(
            requirement_text=requirement_text,
            issues=issues,
            structure=structure,
            rules=rules,
            risks=risks,
            testability=testability,
        )

        try:
            raw = self._call_llm(prompt)
        except Exception as e:
            logger.exception("ReviewAgent llm call failed: %s", e)
            return self._empty_result(
                quality="fair",
                decision="conditional_pass",
                summary="复核模型调用失败，未完成自动复核。",
                should_refine=False,
            )

        json_text = self._extract_json(raw)
        data = self._safe_json(json_text)
        normalized = self._normalize(data)

        return normalized