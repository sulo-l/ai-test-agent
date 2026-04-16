# app/analysis_app/agents/score_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import List, Dict, Any
import re
import json
import logging

from app.analysis_app.agents.base_agent import BaseAgent


logger = logging.getLogger(__name__)


class RequirementScoreAgent(BaseAgent):
    """
    企业级 V4 需求质量评分 Agent

    能力：
    1. 输出企业级结构化评分
    2. 对齐 models.py
    3. 支持 quality_level / decision / gate_reasons
    4. 提供更稳的本地兜底评分
    5. 统一继承 BaseAgent
    """

    name = "score"

    SYSTEM_PROMPT = (
        "你是一名专业的软件需求质量评分专家。"
        "你必须基于需求文本和已识别问题进行审慎、结构化、可追溯的评分。"
        "必须只输出 JSON，不允许输出 markdown 和解释。"
    )

    DIMENSIONS = [
        "completeness",
        "clarity",
        "consistency",
        "rules",
        "coverage",
        "testability",
        "traceability",
        "maintainability",
        "security",
        "compliance",
        "risk",
    ]

    # =====================================================
    # Prompt 构建
    # =====================================================

    def _build_prompt(
        self,
        requirement_text: str,
        issues: List[Dict[str, Any]],
    ) -> str:
        issues_simple = []

        for item in issues or []:
            if not isinstance(item, dict):
                continue

            issues_simple.append(
                {
                    "id": str(item.get("id") or ""),
                    "level": str(item.get("level") or "medium"),
                    "category": str(item.get("category") or "需求质量"),
                    "dimension": str(item.get("dimension") or "general"),
                    "title": str(item.get("title") or ""),
                    "message": str(item.get("message") or ""),
                    "suggestion": str(item.get("suggestion") or ""),
                    "severity": str(item.get("severity") or ""),
                    "impact": str(item.get("impact") or ""),
                    "solution": str(item.get("solution") or ""),
                }
            )

        return f"""
你是一名高级软件需求评审专家，擅长从需求质量角度进行企业级结构化评分。

请根据“需求文本”和“已识别问题清单”，输出该需求的质量评分结果。

评分维度固定为以下几个项：

1. completeness（完整性）
2. clarity（清晰性）
3. consistency（一致性）
4. rules（业务规则）
5. coverage（覆盖完整度）
6. testability（可测试性）
7. traceability（可追踪性）
8. maintainability（可维护性）
9. security（安全性）
10. compliance（合规性）
11. risk（风险控制）

请严格输出 JSON 对象，不要输出解释，不要输出 markdown，不要输出代码块。

输出格式必须严格如下：

{{
  "score": 0到100之间的整数,
  "quality_level": "excellent|good|fair|poor",
  "decision": "pass|conditional_pass|fail",
  "summary": "对整体需求质量的中文专业总结",
  "breakdown": {{
    "completeness": {{
      "points": 0到100之间的整数,
      "comments": ["评论1", "评论2"],
      "issue_ids": ["ISSUE-001"]
    }},
    "clarity": {{
      "points": 0到100之间的整数,
      "comments": [],
      "issue_ids": []
    }},
    "consistency": {{
      "points": 0到100之间的整数,
      "comments": [],
      "issue_ids": []
    }},
    "rules": {{
      "points": 0到100之间的整数,
      "comments": [],
      "issue_ids": []
    }},
    "coverage": {{
      "points": 0到100之间的整数,
      "comments": [],
      "issue_ids": []
    }},
    "testability": {{
      "points": 0到100之间的整数,
      "comments": [],
      "issue_ids": []
    }},
    "traceability": {{
      "points": 0到100之间的整数,
      "comments": [],
      "issue_ids": []
    }},
    "maintainability": {{
      "points": 0到100之间的整数,
      "comments": [],
      "issue_ids": []
    }},
    "security": {{
      "points": 0到100之间的整数,
      "comments": [],
      "issue_ids": []
    }},
    "compliance": {{
      "points": 0到100之间的整数,
      "comments": [],
      "issue_ids": []
    }},
    "risk": {{
      "points": 0到100之间的整数,
      "comments": [],
      "issue_ids": []
    }}
  }},
  "reasons": {{
    "high": [],
    "medium": [],
    "low": []
  }},
  "suggestions": [],
  "gate_reasons": []
}}

评分要求：
1. 必须综合问题数量、问题等级、严重性、影响面和需求文本质量
2. 如果存在 blocker 级问题或多个关键 high 问题，decision 不应为 pass
3. score / quality_level / decision 三者必须自洽
4. comments 必须简洁专业
5. issue_ids 可以为空，但若可关联，请尽量关联
6. gate_reasons 应说明为什么 pass / conditional_pass / fail

以下是需求文本：

\"\"\"
{requirement_text}
\"\"\"

以下是已识别问题：

{json.dumps(issues_simple, ensure_ascii=False, indent=2)}

请严格只输出 JSON。
"""

    # =====================================================
    # LLM 调用
    # =====================================================

    def _call_llm(self, prompt: str) -> str:
        result = self.llm.call(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=5000,
            timeout=120,
            force_json_object=True,
        )
        return (result or "").strip()

    # =====================================================
    # JSON 提取
    # =====================================================

    def _strip_fence(self, text: str) -> str:
        return re.sub(r"```json|```", "", text or "", flags=re.IGNORECASE).strip()

    def _extract_json_object(self, text: str) -> str:
        clean_text = self._strip_fence(text)

        start = clean_text.find("{")
        end = clean_text.rfind("}")
        if start >= 0 and end > start:
            return clean_text[start:end + 1].strip()

        return clean_text

    def _safe_load_json_object(self, text: str) -> Dict[str, Any]:
        if not text:
            return {}

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        # 处理模型把 JSON 对象包成字符串的情况
        try:
            if text.startswith('"') and text.endswith('"'):
                inner = json.loads(text)
                if isinstance(inner, str):
                    parsed = json.loads(inner)
                    if isinstance(parsed, dict):
                        return parsed
        except Exception:
            pass

        # 尝试修复尾逗号
        try:
            repaired = re.sub(r",\s*([}\]])", r"\1", text)
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        return {}

    # =====================================================
    # 本地兜底评分
    # =====================================================

    def _local_fallback_score(
        self,
        requirement_text: str,
        issues: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        total_score = 100

        high_count = 0
        medium_count = 0
        low_count = 0

        blocker_count = 0
        critical_count = 0
        major_count = 0

        reasons_high: List[str] = []
        reasons_medium: List[str] = []
        reasons_low: List[str] = []

        for issue in issues or []:
            if not isinstance(issue, dict):
                continue

            level = str(issue.get("level") or "medium").lower()
            severity = str(issue.get("severity") or "major").lower()
            title = str(issue.get("title") or "").strip()
            message = str(issue.get("message") or "").strip()
            short_text = title or message or "未命名问题"

            if level == "high":
                total_score -= 10
                high_count += 1
                reasons_high.append(short_text)
            elif level == "medium":
                total_score -= 5
                medium_count += 1
                reasons_medium.append(short_text)
            else:
                total_score -= 2
                low_count += 1
                reasons_low.append(short_text)

            if severity == "blocker":
                total_score -= 15
                blocker_count += 1
            elif severity == "critical":
                total_score -= 8
                critical_count += 1
            elif severity == "major":
                total_score -= 4
                major_count += 1

        if len((requirement_text or "").strip()) < 80:
            total_score -= 8
            reasons_medium.append("需求文本整体信息量偏少，完整性基础不足。")

        total_score = max(0, min(100, total_score))
        quality_level = self._map_quality_level(total_score)
        decision = self._infer_decision(
            score=total_score,
            blocker_count=blocker_count,
            critical_count=critical_count,
            high_count=high_count,
        )

        gate_reasons = self._build_gate_reasons(
            decision=decision,
            blocker_count=blocker_count,
            critical_count=critical_count,
            high_count=high_count,
            score=total_score,
        )

        summary = (
            f"当前需求评分为 {total_score} 分。"
            f" 共识别高优先级问题 {high_count} 项，"
            f"中优先级问题 {medium_count} 项，"
            f"低优先级问题 {low_count} 项。"
        )

        breakdown = self._build_fallback_breakdown(
            score=total_score,
            high_count=high_count,
            blocker_count=blocker_count,
            critical_count=critical_count,
        )

        return {
            "score": int(total_score),
            "quality_level": quality_level,
            "decision": decision,
            "summary": summary,
            "breakdown": breakdown,
            "reasons": {
                "high": self._unique_keep_order(reasons_high)[:8],
                "medium": self._unique_keep_order(reasons_medium)[:8],
                "low": self._unique_keep_order(reasons_low)[:8],
            },
            "suggestions": self._build_fallback_suggestions(issues),
            "gate_reasons": gate_reasons,
        }

    def _build_fallback_breakdown(
        self,
        score: int,
        high_count: int,
        blocker_count: int,
        critical_count: int,
    ) -> Dict[str, Any]:
        base = max(0, min(100, score))

        def dim(points: int, comment: str) -> Dict[str, Any]:
            return {
                "points": max(0, min(100, points)),
                "comments": [comment] if comment else [],
                "issue_ids": [],
            }

        return {
            "completeness": dim(base - 3, "基于当前问题数量推断，完整性存在一定补充空间。"),
            "clarity": dim(base - 2, "需求描述整体可分析，但部分口径可能仍需明确。"),
            "consistency": dim(base - 2, "一致性维度未做专项精算，建议结合复核结果确认。"),
            "rules": dim(base - 3, "业务规则闭环程度受已识别问题影响。"),
            "coverage": dim(base - 4, "若存在高优先级问题，通常意味着覆盖维度仍有不足。"),
            "testability": dim(base - 3, "可测试性随需求清晰度和规则完备度变化。"),
            "traceability": dim(base - 2, "可追踪性需结合条目化需求与规则映射确认。"),
            "maintainability": dim(base - 1, "可维护性受复杂度与规则分散程度影响。"),
            "security": dim(base - 5 if (blocker_count or critical_count) else base - 2, "安全维度建议结合具体功能触发点复核。"),
            "compliance": dim(base - 2, "合规性需结合业务场景与数据要素进一步确认。"),
            "risk": dim(base - 6 if high_count else base - 2, "高优先级问题会显著影响整体风险控制评分。"),
        }

    def _build_fallback_suggestions(self, issues: List[Dict[str, Any]]) -> List[str]:
        suggestions: List[str] = []

        if any(str(x.get("category") or "") == "完整性" for x in issues or []):
            suggestions.append("优先补齐缺失流程、前置条件、约束条件和验收标准。")

        if any(str(x.get("category") or "") in {"业务规则", "状态机", "状态流转"} for x in issues or []):
            suggestions.append("补充业务规则、状态流转条件和例外处理逻辑。")

        if any(str(x.get("category") or "") in {"异常处理", "边界场景"} for x in issues or []):
            suggestions.append("补充异常流程、边界值、失败回滚和兜底策略。")

        if any(str(x.get("category") or "") in {"安全", "权限安全"} for x in issues or []):
            suggestions.append("补充与当前需求直接相关的安全控制要求和验收口径。")

        if any(str(x.get("category") or "") in {"合规性"} for x in issues or []):
            suggestions.append("补充隐私、审计、监管和数据处理边界要求。")

        if not suggestions:
            suggestions.append("建议进一步细化需求条目、判定规则和验收标准。")

        return self._unique_keep_order(suggestions)[:6]

    # =====================================================
    # 结果规范化
    # =====================================================

    def _normalize_result(
        self,
        score_obj: Dict[str, Any],
        requirement_text: str,
        issues: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not isinstance(score_obj, dict):
            return self._local_fallback_score(requirement_text, issues)

        score = self._safe_int(score_obj.get("score"), 60)
        score = max(0, min(100, score))

        quality_level = str(score_obj.get("quality_level") or "").strip().lower()
        if quality_level not in {"excellent", "good", "fair", "poor"}:
            quality_level = self._map_quality_level(score)

        decision = str(score_obj.get("decision") or "").strip().lower()
        if decision not in {"pass", "conditional_pass", "fail"}:
            decision = self._infer_decision_from_issues(score, issues)

        summary = str(score_obj.get("summary") or "").strip()
        if not summary:
            summary = f"当前需求评分为 {score} 分，整体质量等级为 {quality_level}。"

        breakdown = self._normalize_breakdown(score_obj.get("breakdown"))
        reasons = self._normalize_reasons(score_obj.get("reasons"))
        suggestions = self._normalize_str_list(score_obj.get("suggestions"), max_items=8)
        gate_reasons = self._normalize_str_list(score_obj.get("gate_reasons"), max_items=8)

        if not gate_reasons:
            gate_reasons = self._build_gate_reasons(
                decision=decision,
                blocker_count=sum(1 for x in issues if str(x.get("severity") or "").lower() == "blocker"),
                critical_count=sum(1 for x in issues if str(x.get("severity") or "").lower() == "critical"),
                high_count=sum(1 for x in issues if str(x.get("level") or "").lower() == "high"),
                score=score,
            )

        return {
            "score": score,
            "quality_level": quality_level,
            "decision": decision,
            "summary": summary,
            "breakdown": breakdown,
            "reasons": reasons,
            "suggestions": suggestions,
            "gate_reasons": gate_reasons,
        }

    def _normalize_breakdown(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            value = {}

        result: Dict[str, Any] = {}

        for dim_name in self.DIMENSIONS:
            raw = value.get(dim_name, {})
            if not isinstance(raw, dict):
                raw = {}

            points = self._safe_int(raw.get("points"), 0)
            points = max(0, min(100, points))

            comments = self._normalize_str_list(raw.get("comments"), max_items=5)
            issue_ids = self._normalize_str_list(raw.get("issue_ids"), max_items=20)

            result[dim_name] = {
                "points": points,
                "comments": comments,
                "issue_ids": issue_ids,
            }

        return result

    def _normalize_reasons(self, value: Any) -> Dict[str, List[str]]:
        if not isinstance(value, dict):
            value = {}

        return {
            "high": self._normalize_str_list(value.get("high"), max_items=10),
            "medium": self._normalize_str_list(value.get("medium"), max_items=10),
            "low": self._normalize_str_list(value.get("low"), max_items=10),
        }

    def _normalize_str_list(self, value: Any, max_items: int = 10) -> List[str]:
        if isinstance(value, list):
            items = value
        elif value is None:
            items = []
        else:
            items = [value]

        results: List[str] = []
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            if text in results:
                continue
            results.append(text)

        return results[:max_items]

    # =====================================================
    # 决策 / 评级
    # =====================================================

    def _map_quality_level(self, score: int) -> str:
        if score >= 90:
            return "excellent"
        if score >= 75:
            return "good"
        if score >= 60:
            return "fair"
        return "poor"

    def _infer_decision(
        self,
        score: int,
        blocker_count: int,
        critical_count: int,
        high_count: int,
    ) -> str:
        if blocker_count > 0:
            return "fail"
        if score < 60:
            return "fail"
        if critical_count > 0 or high_count >= 3 or score < 75:
            return "conditional_pass"
        return "pass"

    def _infer_decision_from_issues(
        self,
        score: int,
        issues: List[Dict[str, Any]],
    ) -> str:
        blocker_count = sum(1 for x in issues if str(x.get("severity") or "").lower() == "blocker")
        critical_count = sum(1 for x in issues if str(x.get("severity") or "").lower() == "critical")
        high_count = sum(1 for x in issues if str(x.get("level") or "").lower() == "high")
        return self._infer_decision(score, blocker_count, critical_count, high_count)

    def _build_gate_reasons(
        self,
        decision: str,
        blocker_count: int,
        critical_count: int,
        high_count: int,
        score: int,
    ) -> List[str]:
        reasons: List[str] = []

        if blocker_count > 0:
            reasons.append("存在 blocker 级问题，当前不满足直接通过条件。")
        if critical_count > 0:
            reasons.append("存在 critical 级问题，需优先整改关键缺陷。")
        if high_count > 0:
            reasons.append(f"当前存在 {high_count} 个高优先级问题。")
        if score < 75 and decision != "pass":
            reasons.append(f"当前评分为 {score}，尚未达到稳定通过阈值。")

        if not reasons:
            if decision == "pass":
                reasons.append("当前未发现阻塞性质量门禁问题。")
            elif decision == "conditional_pass":
                reasons.append("当前需求可继续推进，但需补齐关键细节与约束。")
            else:
                reasons.append("当前需求关键问题较多，不建议直接进入下一阶段。")

        return self._unique_keep_order(reasons)[:8]

    # =====================================================
    # 工具
    # =====================================================

    def _safe_int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

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

    # =====================================================
    # 主入口
    # =====================================================

    def run(
        self,
        requirement_text: str,
        issues: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        prompt = self._build_prompt(requirement_text, issues)

        try:
            raw_output = self._call_llm(prompt)
        except Exception as e:
            logger.exception("score_agent llm call failed: %s", e)
            return self._local_fallback_score(
                requirement_text,
                issues,
            )

        json_str = self._extract_json_object(raw_output)
        score_obj = self._safe_load_json_object(json_str)

        if not isinstance(score_obj, dict) or not score_obj:
            logger.warning("score_agent json parse failed, fallback used")
            return self._local_fallback_score(
                requirement_text,
                issues,
            )

        return self._normalize_result(score_obj, requirement_text, issues)