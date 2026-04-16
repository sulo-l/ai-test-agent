# app/analysis_app/agents/summary_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import List, Dict, Any

from app.analysis_app.models import RequirementIssue, RequirementAnalysisResult


class RequirementSummaryAgent:
    """
    V4 企业级需求总结 Agent

    设计原则：
    1. 不依赖 LLM
    2. 不产生 hallucination
    3. 汇总全链路分析结果
    4. 输出与 RequirementSummaryReport 对齐
    """

    def generate_summary_report(
        self,
        result: RequirementAnalysisResult,
        issues: List[RequirementIssue],
        score: int,
        quality_level: str,
    ) -> Dict[str, Any]:
        issues = issues or []

        score_result = self._to_dict(getattr(result, "scoreResult", None))
        decision = str(score_result.get("decision") or getattr(result, "decision", "unknown")).strip()
        gate_reasons = score_result.get("gate_reasons", []) or self._get_nested_value(result, "qualityGate.reasons", [])

        summary_report: Dict[str, Any] = {}

        summary_report["executive_summary"] = self._generate_executive_summary(
            score=score,
            quality_level=quality_level,
            decision=decision,
            result=result,
            issues=issues,
        )

        summary_report["overall_quality"] = self._generate_overall_quality_summary(
            score=score,
            quality_level=quality_level,
            decision=decision,
            result=result,
        )

        summary_report["major_issues"] = self._generate_major_issues_summary(
            result=result,
            issues=issues,
        )

        summary_report["risk_assessment"] = self._generate_risk_assessment(
            result=result,
            issues=issues,
        )

        summary_report["improvement_suggestions"] = self._generate_improvement_suggestions(
            result=result,
            issues=issues,
        )

        summary_report["conclusion"] = self._generate_conclusion(
            score=score,
            decision=decision,
            gate_reasons=gate_reasons,
            result=result,
            issues=issues,
        )

        summary_report["maintainability_and_scalability"] = self._generate_maintainability_and_scalability(
            result=result,
            issues=issues,
        )

        summary_report["compliance_check"] = self._generate_compliance_check(
            result=result,
            issues=issues,
        )

        summary_report["next_action"] = self._generate_next_action(
            result=result,
            issues=issues,
            decision=decision,
        )

        return summary_report

    # =====================================================
    # 执行摘要
    # =====================================================

    def _generate_executive_summary(
        self,
        score: int,
        quality_level: str,
        decision: str,
        result: RequirementAnalysisResult,
        issues: List[RequirementIssue],
    ) -> str:
        quality_map = {
            "excellent": "优秀",
            "good": "良好",
            "fair": "一般",
            "poor": "较弱",
        }

        quality_text = quality_map.get(str(quality_level or "").strip(), "一般")

        total_issues = self._get_nested_value(result, "statistics.totalIssues", 0)
        high_count = self._get_nested_value(result, "statistics.highCount", 0)
        blocker_count = self._get_nested_value(result, "statistics.blockerCount", 0)
        critical_count = self._get_nested_value(result, "statistics.criticalCount", 0)

        parts: List[str] = [
            f"当前需求分析评分为 {score} 分，整体质量等级为“{quality_text}”。",
            f"共识别问题 {total_issues} 项，其中高优先级 {high_count} 项。",
        ]

        if blocker_count:
            parts.append(f"存在 blocker 级问题 {blocker_count} 项。")
        if critical_count:
            parts.append(f"存在 critical 级问题 {critical_count} 项。")

        if decision == "pass":
            parts.append("整体已满足进入下一阶段的基本条件。")
        elif decision == "conditional_pass":
            parts.append("需求可继续推进，但需优先补齐关键规则、异常分支和验收口径。")
        elif decision == "fail":
            parts.append("当前需求关键问题较多，不建议直接进入设计与开发阶段。")

        return " ".join(self._unique_keep_order(parts))

    # =====================================================
    # 总体质量
    # =====================================================

    def _generate_overall_quality_summary(
        self,
        score: int,
        quality_level: str,
        decision: str,
        result: RequirementAnalysisResult,
    ) -> str:
        quality_map = {
            "excellent": "优秀",
            "good": "良好",
            "fair": "一般",
            "poor": "较弱",
        }

        quality_text = quality_map.get(quality_level, "一般")

        total_issues = self._get_nested_value(result, "statistics.totalIssues", 0)
        high_count = self._get_nested_value(result, "statistics.highCount", 0)
        medium_count = self._get_nested_value(result, "statistics.mediumCount", 0)
        low_count = self._get_nested_value(result, "statistics.lowCount", 0)

        high_risk_count = len(self._ensure_list(self._get_nested_value(result, "riskReport.high_risks", [])))
        medium_risk_count = len(self._ensure_list(self._get_nested_value(result, "riskReport.medium_risks", [])))

        text = (
            f"需求质量评分为 {score} 分，质量等级为“{quality_text}”。"
            f" 当前识别问题 {total_issues} 项，其中高优先级 {high_count} 项、中优先级 {medium_count} 项、低优先级 {low_count} 项。"
        )

        if high_risk_count or medium_risk_count:
            text += f" 风险评估显示高风险 {high_risk_count} 项、中风险 {medium_risk_count} 项。"

        if decision == "pass":
            text += " 当前质量门禁判定为通过。"
        elif decision == "conditional_pass":
            text += " 当前需求可继续推进，但建议补齐关键细节。"
        elif decision == "fail":
            text += " 当前需求质量未通过门禁。"

        return text

    # =====================================================
    # 主要问题
    # =====================================================

    def _generate_major_issues_summary(
        self,
        result: RequirementAnalysisResult,
        issues: List[RequirementIssue],
    ) -> str:
        lines: List[str] = []

        sorted_issues = sorted(
            issues,
            key=lambda x: (
                0 if str(x.level) == "high" else 1 if str(x.level) == "medium" else 2,
                0 if str(x.severity) == "blocker" else
                1 if str(x.severity) == "critical" else
                2 if str(x.severity) == "major" else
                3
            )
        )

        for issue in sorted_issues:
            if issue.level in ["high", "medium"]:
                title = issue.title or "未命名问题"
                message = issue.message or ""
                lines.append(f"[{issue.level.upper()}] {title}: {message}")

        if not lines:
            return "未识别出明显关键问题，但仍建议关注边界场景、异常流程与验收口径。"

        return "\n".join(lines[:5])

    # =====================================================
    # 风险
    # =====================================================

    def _generate_risk_assessment(
        self,
        result: RequirementAnalysisResult,
        issues: List[RequirementIssue],
    ) -> str:
        risk_summary = self._get_nested_value(result, "riskReport.risk_summary", "")
        if risk_summary:
            return str(risk_summary)

        top_risks = self._ensure_list(self._get_nested_value(result, "riskReport.top_risks", []))
        if top_risks:
            return "重点风险包括：" + "；".join([str(x).strip() for x in top_risks[:3] if str(x).strip()]) + "。"

        return "当前未识别明确高风险项，但建议关注异常流程、安全控制与依赖联动风险。"

    # =====================================================
    # 改进建议
    # =====================================================

    def _generate_improvement_suggestions(
        self,
        result: RequirementAnalysisResult,
        issues: List[RequirementIssue],
    ) -> str:
        suggestions: List[str] = []

        for issue in issues:
            if issue.suggestion:
                suggestions.append(str(issue.suggestion).strip())

        score_suggestions = self._ensure_list(
            self._get_nested_value(result, "scoreResult.suggestions", [])
        )
        suggestions.extend([str(x).strip() for x in score_suggestions if str(x).strip()])

        coverage_recommendations = self._ensure_list(
            self._get_nested_value(result, "coverage.recommendations", [])
        )
        suggestions.extend([str(x).strip() for x in coverage_recommendations if str(x).strip()])

        consistency_recommendations = self._ensure_list(
            self._get_nested_value(result, "consistency.recommendations", [])
        )
        suggestions.extend([str(x).strip() for x in consistency_recommendations if str(x).strip()])

        traceability_recommendations = self._ensure_list(
            self._get_nested_value(result, "traceability.recommendations", [])
        )
        suggestions.extend([str(x).strip() for x in traceability_recommendations if str(x).strip()])

        suggestions = self._unique_keep_order(suggestions)

        if not suggestions:
            return "建议补充业务规则、异常流程、边界场景和验收标准。"

        return "\n".join(suggestions[:6])

    # =====================================================
    # 结论
    # =====================================================

    def _generate_conclusion(
        self,
        score: int,
        decision: str,
        gate_reasons: List[str],
        result: RequirementAnalysisResult,
        issues: List[RequirementIssue],
    ) -> str:
        if decision == "pass":
            conclusion = "需求质量较高，可进入设计与开发阶段。"
        elif decision == "conditional_pass":
            conclusion = "需求可继续推进，但仍需补充部分关键细节。"
        else:
            conclusion = "需求质量不足，建议补齐关键规则后再推进。"

        gate_reasons = [str(x).strip() for x in (gate_reasons or []) if str(x).strip()]

        if gate_reasons:
            reason_text = "；".join(gate_reasons[:3])
            conclusion += f" 主要原因包括：{reason_text}。"

        if score < 60:
            conclusion += " 当前评分偏低，建议优先解决高优先级问题后再复评。"
        elif score < 75 and decision != "pass":
            conclusion += " 建议在评审闭环后再进入下一阶段。"

        return conclusion

    # =====================================================
    # 可维护性
    # =====================================================

    def _generate_maintainability_and_scalability(
        self,
        result: RequirementAnalysisResult,
        issues: List[RequirementIssue],
    ) -> str:
        issues_list = [
            i for i in issues
            if i.category in {"可维护性", "可扩展性", "可追踪性"}
        ]

        if not issues_list:
            return "当前未发现明显可维护性或可扩展性问题。"

        lines: List[str] = []

        for issue in issues_list:
            title = issue.title or "未命名问题"
            message = issue.message or ""
            lines.append(f"{title}: {message}")

        return "\n".join(lines[:4])

    # =====================================================
    # 合规
    # =====================================================

    def _generate_compliance_check(
        self,
        result: RequirementAnalysisResult,
        issues: List[RequirementIssue],
    ) -> str:
        issues_list = [
            i for i in issues
            if i.category == "合规性"
        ]

        compliance_gaps = self._ensure_list(
            self._get_nested_value(result, "compliance.compliance_gaps", [])
        )

        if not issues_list and not compliance_gaps:
            return "当前未识别明确合规问题。"

        lines: List[str] = []

        for issue in issues_list[:3]:
            title = issue.title or "合规问题"
            message = issue.message or ""
            lines.append(f"{title}: {message}")

        for gap in compliance_gaps[:3]:
            text = str(gap).strip()
            if text:
                lines.append(text)

        return "\n".join(self._unique_keep_order(lines)[:4])

    # =====================================================
    # 下一步行动
    # =====================================================

    def _generate_next_action(
        self,
        result: RequirementAnalysisResult,
        issues: List[RequirementIssue],
        decision: str,
    ) -> List[str]:
        actions: List[str] = []

        blocker_count = self._get_nested_value(result, "statistics.blockerCount", 0)
        critical_count = self._get_nested_value(result, "statistics.criticalCount", 0)

        if blocker_count or critical_count:
            actions.append("优先整改 blocker / critical 级问题，并组织针对性复审。")

        if any(i.category in {"业务规则", "状态机", "状态流转"} for i in issues):
            actions.append("补齐业务规则、状态流转条件和例外分支说明。")

        if any(i.category in {"异常处理", "边界场景"} for i in issues):
            actions.append("补充异常流程、边界场景、失败回滚和兜底策略。")

        if any(i.category in {"安全", "权限安全"} for i in issues):
            actions.append("补充认证鉴权、权限控制、输入校验和审计要求。")

        if any(i.category in {"合规性"} for i in issues):
            actions.append("补充隐私、审计、监管和数据处理相关约束。")

        if any(i.category in {"依赖", "依赖约束"} for i in issues):
            actions.append("明确上下游依赖接口、回调机制、失败处理和联调约束。")

        if decision == "pass":
            actions.append("进入详细设计前，整理需求条目与验收标准形成评审基线。")
        elif decision == "conditional_pass":
            actions.append("在进入下一阶段前，完成关键问题补强并更新评审结论。")
        else:
            actions.append("暂停推进，待关键规则和验收口径补齐后再发起复评。")

        if not actions:
            actions.append("进一步细化需求条目、业务规则和验收标准。")

        return self._unique_keep_order(actions)[:6]

    # =====================================================
    # 工具
    # =====================================================

    def _to_dict(self, value: Any) -> Dict[str, Any]:
        if value is None:
            return {}

        if isinstance(value, dict):
            return value

        if hasattr(value, "model_dump"):
            try:
                return value.model_dump()
            except Exception:
                return {}

        if hasattr(value, "dict"):
            try:
                return value.dict()
            except Exception:
                return {}

        return {}

    def _get_nested_value(self, obj: Any, path: str, default: Any = None) -> Any:
        current = obj

        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part, default)
            else:
                current = getattr(current, part, default)

            if current is None:
                return default

        return current

    def _ensure_list(self, value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if value is None:
            return []
        return [value]

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


summary_agent = RequirementSummaryAgent()