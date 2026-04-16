# app/analysis_app/pipeline.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Callable, Awaitable

from app.analysis_app.agents.analysis_agent import RequirementAnalysisAgent
from app.analysis_app.agents.score_agent import RequirementScoreAgent
from app.analysis_app.agents.risk_agent import RequirementRiskAgent
from app.analysis_app.agents.summary_agent import RequirementSummaryAgent

from app.analysis_app.agents.structure_agent import StructureAgent
from app.analysis_app.agents.rule_agent import RuleAgent
from app.analysis_app.agents.testability_agent import TestabilityAgent
from app.analysis_app.agents.review_agent import ReviewAgent
from app.analysis_app.agents.consistency_agent import ConsistencyAgent
from app.analysis_app.agents.coverage_agent import CoverageAgent
from app.analysis_app.agents.security_agent import SecurityAgent
from app.analysis_app.agents.compliance_agent import ComplianceAgent
from app.analysis_app.agents.traceability_agent import TraceabilityAgent

from app.analysis_app.models import (
    RequirementAnalysisResult,
    RequirementAnalysisDetail,
    IssueStatistics,
    RequirementStructureResult,
    RequirementRuleResult,
    RequirementTestabilityResult,
    RequirementConsistencyResult,
    RequirementCoverageResult,
    RequirementSecurityResult,
    RequirementComplianceResult,
    RequirementTraceabilityResult,
    RequirementReviewResult,
    RequirementScoreResult,
    RequirementRiskReport,
)

from app.analysis_app.utils.requirement_cleaner import requirement_cleaner
from app.analysis_app.utils.issue_normalizer import issue_normalizer
from app.analysis_app.issue_aggregator import issue_aggregator
from app.analysis_app.worker_settings import (
    ANALYSIS_PIPELINE_ENABLE_PARALLEL,
    ANALYSIS_PIPELINE_MAX_PARALLEL_AGENTS,
    ANALYSIS_PIPELINE_AGENT_TIMEOUT_SEC,
)

logger = logging.getLogger(__name__)

PublishFn = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]


class RequirementAnalysisPipeline:
    """
    企业级需求分析智能体流水线（接入 issue_aggregator 版）

    目标：
    1. 主返回只保留前端真正需要的结构化结果
    2. debug 信息单独放到 debug，避免主结果过胖
    3. 统一 decision / qualityGate / scoreResult 口径
    4. review 的修正建议不再混入 issues 主列表
    5. riskReport 只保留摘要与 issue 引用，不再复制整份问题列表
    """

    COVERAGE_DIMENSIONS = [
        "正常流程",
        "异常流程",
        "边界场景",
        "业务规则",
        "状态流转",
        "角色权限",
        "数据约束",
        "依赖接口",
        "安全要求",
        "性能要求",
        "可测试性",
        "合规要求",
    ]

    # 标准分类口径
    CATEGORY_CLARITY = {"清晰性"}
    CATEGORY_COMPLETENESS = {"完整性"}
    CATEGORY_RULES = {"业务规则"}
    CATEGORY_EXCEPTION = {"异常处理"}
    CATEGORY_BOUNDARY = {"边界场景"}
    CATEGORY_STATE = {"状态流转"}
    CATEGORY_DATA = {"数据定义"}
    CATEGORY_INTERFACE = {"接口契约"}
    CATEGORY_SECURITY = {"权限安全"}
    CATEGORY_COMPLIANCE = {"合规性"}
    CATEGORY_TESTABILITY = {"可测试性"}
    CATEGORY_TRACEABILITY = {"可追踪性"}
    CATEGORY_PERFORMANCE = {"性能"}
    CATEGORY_MAINTAINABILITY = {"可维护性"}
    CATEGORY_QUALITY = {"需求质量"}

    def __init__(self):
        self.analysis_agent = RequirementAnalysisAgent()
        self.score_agent = RequirementScoreAgent()
        self.risk_agent = RequirementRiskAgent()
        self.summary_agent = RequirementSummaryAgent()

        self.structure_agent = StructureAgent()
        self.rule_agent = RuleAgent()
        self.testability_agent = TestabilityAgent()
        self.review_agent = ReviewAgent()
        self.consistency_agent = ConsistencyAgent()
        self.coverage_agent = CoverageAgent()
        self.security_agent = SecurityAgent()
        self.compliance_agent = ComplianceAgent()
        self.traceability_agent = TraceabilityAgent()

        self.enable_parallel = bool(ANALYSIS_PIPELINE_ENABLE_PARALLEL)
        self.max_parallel_agents = max(1, int(ANALYSIS_PIPELINE_MAX_PARALLEL_AGENTS))
        self.agent_timeout_sec = max(10, int(ANALYSIS_PIPELINE_AGENT_TIMEOUT_SEC))

    # =====================================================
    # 主入口
    # =====================================================

    def run(self, requirement_text: str) -> Dict[str, Any]:
        return asyncio.run(self.run_async(requirement_text))

    async def run_async(
        self,
        requirement_text: str,
        publish: PublishFn = None,
        include_debug: bool = False,
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        clean_text = requirement_cleaner.clean_and_validate(requirement_text)
        agent_results: List[Dict[str, Any]] = []

        await self._emit_stage(publish, "PIPELINE_START", "开始执行需求分析流水线")

        # =====================================================
        # Layer 1：基础分析
        # =====================================================
        await self._emit_stage(publish, "LAYER1_START", "开始执行基础分析智能体")

        layer1_specs = [
            ("structure", self.structure_agent.run, (clean_text,), {}),
            ("rule", self.rule_agent.run, (clean_text,), {}),
            ("analysis", self.analysis_agent.run, (clean_text,), {}),
            ("testability", self.testability_agent.run, (clean_text,), {}),
            ("security", self.security_agent.run, (clean_text,), {}),
            ("compliance", self.compliance_agent.run, (clean_text,), {}),
        ]
        layer1_results = await self._run_parallel(layer1_specs)

        structure, structure_ms, structure_ok, structure_err = layer1_results["structure"]
        rules, rules_ms, rule_ok, rules_err = layer1_results["rule"]
        raw_analysis_issues, analysis_ms, analysis_ok, analysis_err = layer1_results["analysis"]
        testability, testability_ms, testability_ok, testability_err = layer1_results["testability"]
        security, security_ms, security_ok, security_err = layer1_results["security"]
        compliance, compliance_ms, compliance_ok, compliance_err = layer1_results["compliance"]

        structure = structure if isinstance(structure, dict) else {}
        rules = rules if isinstance(rules, dict) else {}
        testability = testability if isinstance(testability, dict) else {}
        security = security if isinstance(security, dict) else {}
        compliance = compliance if isinstance(compliance, dict) else {}

        analysis_issues = issue_normalizer.normalize_issues(raw_analysis_issues or [])
        analysis_issues = issue_aggregator.filter_non_business_issues(analysis_issues)
        analysis_issues = issue_aggregator.dedup_issues(analysis_issues)

        agent_results.append(
            self._build_agent_result(
                agent="structure",
                payload=structure,
                summary=self._brief_payload_summary(structure, "完成需求结构解析"),
                success=structure_ok,
                error=structure_err,
                duration_ms=structure_ms,
            )
        )
        agent_results.append(
            self._build_agent_result(
                agent="rule",
                payload=rules,
                summary=self._brief_payload_summary(rules, "完成业务规则识别"),
                success=rule_ok,
                error=rules_err,
                duration_ms=rules_ms,
            )
        )
        agent_results.append(
            self._build_agent_result(
                agent="analysis",
                payload={"issues": analysis_issues},
                summary=f"识别问题 {len(analysis_issues)} 项" if analysis_ok else "基础问题识别失败，已降级为空结果",
                issues=analysis_issues,
                statistics=self._build_issue_statistics(analysis_issues),
                success=analysis_ok,
                error=analysis_err,
                duration_ms=analysis_ms,
            )
        )
        agent_results.append(
            self._build_agent_result(
                agent="testability",
                payload=testability,
                summary=self._brief_payload_summary(testability, "完成可测试性分析"),
                success=testability_ok,
                error=testability_err,
                duration_ms=testability_ms,
            )
        )
        agent_results.append(
            self._build_agent_result(
                agent="security",
                payload=security,
                summary=self._brief_payload_summary(security, "完成安全分析"),
                success=security_ok,
                error=security_err,
                duration_ms=security_ms,
            )
        )
        agent_results.append(
            self._build_agent_result(
                agent="compliance",
                payload=compliance,
                summary=self._brief_payload_summary(compliance, "完成合规分析"),
                success=compliance_ok,
                error=compliance_err,
                duration_ms=compliance_ms,
            )
        )

        await self._emit_stage(publish, "LAYER1_DONE", "基础分析智能体执行完成")

        # =====================================================
        # Layer 2：consistency
        # =====================================================
        await self._emit_stage(publish, "LAYER2_START", "开始执行一致性分析")

        consistency, consistency_ms, consistency_ok, consistency_err = await self._run_single(
            "consistency",
            self.consistency_agent.run,
            requirement_text=clean_text,
            structure=structure,
            rules=rules,
            issues=analysis_issues,
        )
        consistency = consistency if isinstance(consistency, dict) else {}

        agent_results.append(
            self._build_agent_result(
                agent="consistency",
                payload=consistency,
                summary=self._brief_payload_summary(consistency, "完成一致性分析"),
                success=consistency_ok,
                error=consistency_err,
                duration_ms=consistency_ms,
            )
        )

        await self._emit_stage(publish, "LAYER2_DONE", "一致性分析完成")

        # =====================================================
        # pre-review issues
        # =====================================================
        pre_review_issues = issue_aggregator.aggregate(
            analysis_issues=analysis_issues,
            rules=rules,
            consistency=consistency,
            coverage={},
            security=security,
            compliance=compliance,
            traceability={},
            review_result={},
        )

        # =====================================================
        # Layer 3：coverage
        # =====================================================
        await self._emit_stage(publish, "LAYER3_START", "开始执行覆盖率分析")

        coverage, coverage_ms, coverage_ok, coverage_err = await self._run_single(
            "coverage",
            self.coverage_agent.run,
            requirement_text=clean_text,
            structure=structure,
            rules=rules,
            issues=pre_review_issues,
            testability=testability,
            consistency=consistency,
        )
        coverage = coverage if isinstance(coverage, dict) else {}
        coverage = self._repair_coverage_result(
            coverage=coverage,
            requirement_text=clean_text,
            structure=structure,
            rules=rules,
            issues=pre_review_issues,
            testability=testability,
            consistency=consistency,
            security=security,
            compliance=compliance,
        )

        agent_results.append(
            self._build_agent_result(
                agent="coverage",
                payload=coverage,
                summary=self._brief_payload_summary(coverage, "完成覆盖率分析"),
                success=coverage_ok,
                error=coverage_err,
                duration_ms=coverage_ms,
            )
        )

        await self._emit_stage(publish, "LAYER3_DONE", "覆盖率分析完成")

        # =====================================================
        # pre-traceability issues
        # =====================================================
        pre_traceability_issues = issue_aggregator.aggregate(
            analysis_issues=analysis_issues,
            rules=rules,
            consistency=consistency,
            coverage=coverage,
            security=security,
            compliance=compliance,
            traceability={},
            review_result={},
        )

        # =====================================================
        # Layer 4：traceability
        # =====================================================
        await self._emit_stage(publish, "LAYER4_START", "开始执行可追踪性分析")

        traceability, traceability_ms, traceability_ok, traceability_err = await self._run_single(
            "traceability",
            self.traceability_agent.run,
            requirement_text=clean_text,
            structure=structure,
            rules=rules,
            issues=pre_traceability_issues,
            testability=testability,
            risk_report={},
        )
        traceability = self._repair_traceability_result(traceability if isinstance(traceability, dict) else {})

        agent_results.append(
            self._build_agent_result(
                agent="traceability",
                payload=traceability,
                summary=self._brief_payload_summary(traceability, "完成可追踪性分析"),
                success=traceability_ok,
                error=traceability_err,
                duration_ms=traceability_ms,
            )
        )

        await self._emit_stage(publish, "LAYER4_DONE", "可追踪性分析完成")

        # =====================================================
        # pre-review-final issues
        # =====================================================
        pre_review_final_issues = issue_aggregator.aggregate(
            analysis_issues=analysis_issues,
            rules=rules,
            consistency=consistency,
            coverage=coverage,
            security=security,
            compliance=compliance,
            traceability=traceability,
            review_result={},
        )

        # =====================================================
        # Layer 5：review
        # =====================================================
        await self._emit_stage(publish, "LAYER5_START", "开始执行 AI 复核")

        review_result, review_ms, review_ok, review_err = await self._run_single(
            "review",
            self.review_agent.run,
            requirement_text=clean_text,
            issues=pre_review_final_issues,
            structure=structure,
            rules=rules,
            risks={},
            testability=testability,
        )
        review_result = review_result if isinstance(review_result, dict) else {}

        agent_results.append(
            self._build_agent_result(
                agent="review",
                payload=review_result,
                summary=self._extract_review_summary(review_result),
                success=review_ok,
                error=review_err,
                duration_ms=review_ms,
            )
        )

        await self._emit_stage(publish, "LAYER5_DONE", "AI 复核完成")

        # =====================================================
        # 最终统一问题池
        # =====================================================
        issues = issue_aggregator.aggregate(
            analysis_issues=analysis_issues,
            rules=rules,
            consistency=consistency,
            coverage=coverage,
            security=security,
            compliance=compliance,
            traceability=traceability,
            review_result=review_result,
        )
        issue_models = issue_normalizer.to_issue_models(issues)

        # =====================================================
        # Layer 6：risk / score 并发
        # =====================================================
        await self._emit_stage(publish, "LAYER6_START", "开始执行风险评估与质量评分")

        risk_context = {
            "security": security,
            "compliance": compliance,
            "coverage": coverage,
        }

        layer6_specs = [
            ("risk", self.risk_agent.generate_risk_assessment, (issue_models,), {"result": risk_context}),
            ("score", self.score_agent.run, (), {
                "requirement_text": clean_text,
                "issues": issues,
            }),
        ]
        layer6_results = await self._run_parallel(layer6_specs)

        raw_risk_report, risk_ms, risk_ok, risk_err = layer6_results["risk"]
        raw_score_result, score_ms, score_ok, score_err = layer6_results["score"]

        raw_risk_report = raw_risk_report if isinstance(raw_risk_report, dict) else {}
        raw_score_result = raw_score_result if isinstance(raw_score_result, dict) else {}

        score = self._safe_score(raw_score_result)
        score_summary = self._safe_summary(raw_score_result)
        quality_level = self._safe_quality_level(raw_score_result, score)

        agent_results.append(
            self._build_agent_result(
                agent="risk",
                payload=raw_risk_report,
                summary=self._brief_payload_summary(raw_risk_report, "完成风险评估"),
                success=risk_ok,
                error=risk_err,
                duration_ms=risk_ms,
            )
        )
        agent_results.append(
            self._build_agent_result(
                agent="score",
                payload=raw_score_result,
                summary=score_summary or f"完成质量评分，得分 {score}",
                success=score_ok,
                error=score_err,
                duration_ms=score_ms,
            )
        )

        await self._emit_stage(publish, "LAYER6_DONE", "风险评估与质量评分完成")

        # =====================================================
        # 统计 / 分析
        # =====================================================
        statistics = self._build_issue_statistics(issues)

        analysis = self._build_analysis_sections(
            issues=issues,
            requirement_text=clean_text,
            structure=structure,
            rules=rules,
            testability=testability,
            consistency=consistency,
            coverage=coverage,
            security=security,
            compliance=compliance,
            traceability=traceability,
            review_result=review_result,
        )

        decision = self._build_decision(
            score=score,
            issues=issues,
            review_result=review_result,
            score_result=raw_score_result,
        )
        quality_gate = self._build_quality_gate(
            issues=issues,
            decision=decision,
            review_result=review_result,
        )

        score_result = self._sanitize_score_result(
            raw_score_result=raw_score_result,
            decision=decision,
            score=score,
            quality_level=quality_level,
            summary=score_summary,
        )

        risk_report = self._build_compact_risk_report(
            issues=issues,
            raw_risk_report=raw_risk_report,
        )

        summary = self._build_summary(
            score=score,
            quality_level=quality_level,
            score_summary=score_summary,
            statistics=statistics,
            analysis=analysis,
            review=review_result,
            coverage=coverage,
            consistency=consistency,
        )

        result_model = RequirementAnalysisResult(
            summary=summary,
            score=score,
            qualityLevel=quality_level,
            decision=decision,
            qualityGate=quality_gate,
            issues=issue_models,
            statistics=IssueStatistics(**statistics),
            analysis=RequirementAnalysisDetail(**analysis),
            structure=self._safe_model(RequirementStructureResult, structure),
            rules=self._safe_model(RequirementRuleResult, rules),
            testability=self._safe_model(RequirementTestabilityResult, testability),
            consistency=self._safe_model(RequirementConsistencyResult, consistency),
            coverage=self._safe_model(RequirementCoverageResult, coverage),
            security=self._safe_model(RequirementSecurityResult, security),
            compliance=self._safe_model(RequirementComplianceResult, compliance),
            traceability=self._safe_model(RequirementTraceabilityResult, traceability),
            review=self._safe_model(RequirementReviewResult, review_result),
            scoreResult=self._safe_model(RequirementScoreResult, score_result),
            riskReport=self._safe_model(RequirementRiskReport, risk_report),
            summaryReport=None,
            agentResults=agent_results if include_debug else [],
            analysisMarkdown=None,
        )

        # =====================================================
        # Summary Agent（只保留到 debug）
        # =====================================================
        await self._emit_stage(publish, "SUMMARY_START", "开始生成总结报告")

        summary_report, summary_ms, summary_ok, summary_err = await self._run_single(
            "summary",
            self.summary_agent.generate_summary_report,
            result=result_model,
            issues=issue_models,
            score=score,
            quality_level=quality_level,
        )
        summary_report = summary_report if isinstance(summary_report, dict) else {}

        agent_results.append(
            self._build_agent_result(
                agent="summary",
                payload=summary_report,
                summary=self._extract_summary_report_text(summary_report),
                success=summary_ok,
                error=summary_err,
                duration_ms=summary_ms,
            )
        )

        final_summary = self._merge_summary(
            base_summary=summary,
            summary_report=summary_report,
            review_result=review_result,
        )

        total_ms = int((time.perf_counter() - started_at) * 1000)
        await self._emit_stage(publish, "PIPELINE_DONE", f"需求分析流水线执行完成，耗时 {total_ms} ms")

        recommendations = self._build_recommendations(
            analysis=analysis,
            coverage=coverage,
            consistency=consistency,
            review_result=review_result,
            score_result=score_result,
            summary_report=summary_report,
        )

        top_issues = issue_aggregator.build_top_issues(issues)

        result: Dict[str, Any] = {
            "overview": {
                "summary": final_summary,
                "score": score,
                "qualityLevel": quality_level,
                "decision": decision,
                "passed": bool(quality_gate.get("passed")),
                "issueCount": statistics.get("totalIssues", 0),
                "highCount": statistics.get("highCount", 0),
                "mediumCount": statistics.get("mediumCount", 0),
                "lowCount": statistics.get("lowCount", 0),
                "criticalCount": statistics.get("criticalCount", 0),
                "durationMs": total_ms,
            },
            "qualityGate": quality_gate,
            "topIssues": top_issues,
            "issues": issues,
            "statistics": statistics,
            "panels": {
                "analysis": analysis,
                "structure": structure,
                "rules": rules,
                "testability": testability,
                "consistency": consistency,
                "coverage": coverage,
                "security": security,
                "compliance": compliance,
                "traceability": traceability,
                "review": self._compact_review_result(review_result),
                "score": score_result,
                "risk": risk_report,
            },
            "recommendations": recommendations,
            "meta": {
                "durationMs": total_ms,
                "parallelEnabled": self.enable_parallel,
                "maxParallelAgents": self.max_parallel_agents,
                "agentTimeoutSec": self.agent_timeout_sec,
            },
        }

        if include_debug:
            result["debug"] = {
                "agentResults": agent_results,
                "summaryReport": summary_report,
                "rawRiskReport": raw_risk_report,
                "rawScoreResult": raw_score_result,
            }

        return result

    # =====================================================
    # 事件推送
    # =====================================================

    async def _emit_stage(self, publish: PublishFn, stage: str, message: str) -> None:
        if not publish:
            return
        try:
            await publish({"type": "stage", "stage": stage, "message": message})
        except Exception:
            logger.exception("pipeline publish stage failed: %s", stage)

    # =====================================================
    # 并发执行工具
    # =====================================================

    async def _run_single(self, agent_name: str, func, *args, **kwargs) -> Tuple[Any, int, bool, Optional[str]]:
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(func, *args, **kwargs),
                timeout=self.agent_timeout_sec,
            )
            duration_ms = int((time.perf_counter() - started) * 1000)
            return result, duration_ms, True, None
        except asyncio.TimeoutError:
            duration_ms = int((time.perf_counter() - started) * 1000)
            fallback = self._default_payload_for_agent(agent_name)
            return fallback, duration_ms, False, f"{agent_name} timeout after {self.agent_timeout_sec}s"
        except Exception as e:
            duration_ms = int((time.perf_counter() - started) * 1000)
            fallback = self._default_payload_for_agent(agent_name)
            return fallback, duration_ms, False, str(e)

    async def _run_parallel(self, specs: List[Tuple[str, Any, tuple, dict]]) -> Dict[str, Tuple[Any, int, bool, Optional[str]]]:
        if not specs:
            return {}

        if not self.enable_parallel or len(specs) <= 1:
            result_map: Dict[str, Tuple[Any, int, bool, Optional[str]]] = {}
            for name, func, args, kwargs in specs:
                result_map[name] = await self._run_single(name, func, *(args or ()), **(kwargs or {}))
            return result_map

        sem = asyncio.Semaphore(self.max_parallel_agents)

        async def _runner(name: str, func, args: tuple, kwargs: dict):
            async with sem:
                result = await self._run_single(name, func, *(args or ()), **(kwargs or {}))
                return name, result

        tasks = [_runner(name, func, args or (), kwargs or {}) for name, func, args, kwargs in specs]
        rows = await asyncio.gather(*tasks)

        result_map: Dict[str, Tuple[Any, int, bool, Optional[str]]] = {}
        for name, result in rows:
            result_map[name] = result
        return result_map

    def _default_payload_for_agent(self, agent_name: str) -> Any:
        mapping = {
            "structure": {},
            "rule": {},
            "analysis": [],
            "testability": {},
            "consistency": {},
            "coverage": {},
            "security": {},
            "compliance": {},
            "traceability": {},
            "review": {},
            "risk": {},
            "score": {},
            "summary": {},
        }
        return mapping.get(agent_name, {})

    # =====================================================
    # Agent Result
    # =====================================================

    def _build_agent_result(
        self,
        agent: str,
        payload: Optional[Dict[str, Any]] = None,
        summary: Optional[str] = None,
        issues: Optional[List[Dict[str, Any]]] = None,
        statistics: Optional[Dict[str, Any]] = None,
        success: bool = True,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        return {
            "agent": agent,
            "issues": issues or [],
            "summary": summary or "",
            "statistics": statistics,
            "payload": payload or {},
            "execution": {
                "name": agent,
                "enabled": True,
                "success": success,
                "durationMs": duration_ms,
                "message": summary or "",
                "error": error,
            },
        }

    def _brief_payload_summary(self, payload: Any, fallback: str) -> str:
        if not isinstance(payload, dict) or not payload:
            return fallback
        for key in ["summary", "risk_summary", "overall_quality", "conclusion", "executive_summary"]:
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        return fallback

    def _extract_review_summary(self, review_result: Any) -> str:
        if not isinstance(review_result, dict):
            return "完成 AI 复核"
        overall = review_result.get("overall_review", {}) or {}
        summary = str(overall.get("summary") or "").strip()
        if summary:
            return summary
        decision = str(overall.get("decision") or "").strip()
        if decision:
            return f"完成 AI 复核，结论：{decision}"
        return "完成 AI 复核"

    def _extract_summary_report_text(self, summary_report: Any) -> str:
        if not isinstance(summary_report, dict):
            return "完成专业总结"
        for key in ["executive_summary", "overall_quality", "conclusion"]:
            value = str(summary_report.get(key) or "").strip()
            if value:
                return value
        return "完成专业总结"

    # =====================================================
    # 统一决策
    # =====================================================

    def _build_decision(
        self,
        score: int,
        issues: List[Dict[str, Any]],
        review_result: Dict[str, Any],
        score_result: Dict[str, Any],
    ) -> str:
        blocker_exists = any(str(x.get("severity") or "").strip() == "blocker" for x in issues)
        critical_exists = any(str(x.get("severity") or "").strip() == "critical" for x in issues)
        high_count = sum(1 for x in issues if str(x.get("level") or "").strip() == "high")

        score_decision = str(score_result.get("decision") or "").strip() if isinstance(score_result, dict) else ""
        overall_review = review_result.get("overall_review", {}) if isinstance(review_result, dict) else {}
        review_decision = str(overall_review.get("decision") or "").strip()

        for candidate in [review_decision, score_decision]:
            if candidate in {"pass", "conditional_pass", "fail"}:
                if candidate == "fail":
                    return "fail"
                if candidate == "conditional_pass":
                    return "conditional_pass"
                if not blocker_exists and score >= 75:
                    return "pass"

        if blocker_exists:
            return "fail"
        if score < 60:
            return "fail"
        if critical_exists or high_count >= 3 or score < 75:
            return "conditional_pass"
        return "pass"

    def _build_quality_gate(
        self,
        issues: List[Dict[str, Any]],
        decision: str,
        review_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        blocker_issue_ids = [
            str(x.get("id") or "").strip()
            for x in issues
            if str(x.get("severity") or "").strip() == "blocker" and str(x.get("id") or "").strip()
        ]
        critical_issue_ids = [
            str(x.get("id") or "").strip()
            for x in issues
            if str(x.get("severity") or "").strip() == "critical" and str(x.get("id") or "").strip()
        ]

        reasons: List[str] = []

        if blocker_issue_ids:
            reasons.append("存在 blocker 级问题，未满足直接通过条件。")
        if critical_issue_ids:
            reasons.append("存在 critical 级问题，需优先整改后再进入下一阶段。")

        high_count = sum(1 for x in issues if str(x.get("level") or "").strip() == "high")
        if high_count:
            reasons.append(f"当前存在 {high_count} 个高优先级问题。")

        overall_review = review_result.get("overall_review", {}) if isinstance(review_result, dict) else {}
        for item in overall_review.get("gate_reason", []) or []:
            text = str(item).strip()
            if text:
                reasons.append(text)

        if decision == "pass" and not reasons:
            reasons.append("当前未发现阻塞性质量门禁问题。")
        elif decision == "conditional_pass" and not reasons:
            reasons.append("当前需求可继续推进，但需按建议完成补强与澄清。")
        elif decision == "fail" and not reasons:
            reasons.append("当前需求关键问题较多，不建议直接进入下一阶段。")

        return {
            "passed": decision == "pass",
            "decision": decision,
            "reasons": self._unique_keep_order(reasons),
            "blocker_issue_ids": blocker_issue_ids,
            "critical_issue_ids": critical_issue_ids,
        }

    # =====================================================
    # coverage / traceability 修复
    # =====================================================

    def _repair_coverage_result(
        self,
        coverage: Dict[str, Any],
        requirement_text: str,
        structure: Dict[str, Any],
        rules: Dict[str, Any],
        issues: List[Dict[str, Any]],
        testability: Dict[str, Any],
        consistency: Dict[str, Any],
        security: Dict[str, Any],
        compliance: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(coverage, dict):
            coverage = {}

        covered = coverage.get("covered_dimensions", []) or []
        missing = coverage.get("missing_dimensions", []) or []
        weak = coverage.get("weak_dimensions", []) or []

        if covered or missing or weak:
            try:
                score = int(coverage.get("coverage_score", 0) or 0)
            except Exception:
                score = 0
            if score <= 0 and (covered or weak):
                score = int((len(covered) + len(weak) * 0.5) / len(self.COVERAGE_DIMENSIONS) * 100)
                coverage["coverage_score"] = max(0, min(100, score))
            return coverage

        text = str(requirement_text or "").lower()

        covered_items: List[Dict[str, str]] = []
        missing_items: List[Dict[str, str]] = []
        weak_items: List[Dict[str, str]] = []

        def add_once(target: List[Dict[str, str]], dimension: str, reason: str) -> None:
            if not dimension or not reason:
                return
            if any(str(x.get("dimension") or "").strip() == dimension for x in target):
                return
            target.append({"dimension": dimension, "reason": reason})

        workflows = structure.get("workflows", []) if isinstance(structure, dict) else []
        interfaces = structure.get("interfaces", []) if isinstance(structure, dict) else []
        rule_items = rules.get("rules", []) if isinstance(rules, dict) else []
        states = rules.get("states", []) if isinstance(rules, dict) else []
        validations = rules.get("validations", []) if isinstance(rules, dict) else []
        exceptions = rules.get("exceptions", []) if isinstance(rules, dict) else []
        test_points = testability.get("test_points", []) if isinstance(testability, dict) else []
        security_gaps = security.get("security_gaps", []) if isinstance(security, dict) else []
        compliance_gaps = compliance.get("compliance_gaps", []) if isinstance(compliance, dict) else []
        consistency_gaps = consistency.get("consistency_gaps", []) if isinstance(consistency, dict) else []

        if workflows:
            add_once(covered_items, "正常流程", "需求中已包含主流程描述。")
        else:
            add_once(missing_items, "正常流程", "未看到清晰的主流程定义。")

        if exceptions or self._has_any_category(issues, self.CATEGORY_EXCEPTION):
            add_once(covered_items, "异常流程", "需求中已有异常或例外处理说明。")
        else:
            add_once(missing_items, "异常流程", "缺少失败处理、降级、重试或异常分支说明。")

        if self._has_any_category(issues, self.CATEGORY_BOUNDARY):
            add_once(weak_items, "边界场景", "边界场景已有提示但仍不充分。")
        elif any(k in text for k in ["边界", "空值", "极端", "重复", "最大", "最小"]):
            add_once(covered_items, "边界场景", "需求中体现了部分边界场景。")
        else:
            add_once(weak_items, "边界场景", "边界值和极端场景定义较弱。")

        if rule_items:
            add_once(covered_items, "业务规则", "需求中已定义核心业务规则。")
        else:
            add_once(weak_items, "业务规则", "业务规则描述不够系统。")

        if states:
            add_once(covered_items, "状态流转", "需求中存在状态或流转描述。")
        elif self._has_any_category(issues, self.CATEGORY_STATE):
            add_once(weak_items, "状态流转", "状态与流转存在问题但定义不足。")
        else:
            add_once(weak_items, "状态流转", "状态定义和流转规则不够清晰。")

        if any(k in text for k in ["角色", "权限", "登录", "鉴权"]) or self._has_any_category(issues, self.CATEGORY_SECURITY):
            add_once(covered_items, "角色权限", "需求中涉及角色、权限或鉴权内容。")
        else:
            add_once(missing_items, "角色权限", "未明确角色权限边界与访问控制要求。")

        if validations or self._has_any_category(issues, self.CATEGORY_DATA):
            add_once(covered_items, "数据约束", "需求中存在字段、格式或校验相关说明。")
        else:
            add_once(weak_items, "数据约束", "缺少系统化的数据约束和字段规则定义。")

        if interfaces or self._has_any_category(issues, self.CATEGORY_INTERFACE):
            add_once(covered_items, "依赖接口", "需求中存在接口或外部依赖说明。")
        else:
            add_once(weak_items, "依赖接口", "接口依赖、上下游约束和联调条件描述不足。")

        if security_gaps:
            add_once(weak_items, "安全要求", "已识别安全缺口，但需求未完整定义安全要求。")
        elif any(k in text for k in ["安全", "加密", "脱敏", "越权", "审计"]) or self._has_any_category(issues, self.CATEGORY_SECURITY):
            add_once(covered_items, "安全要求", "需求中涉及部分安全相关内容。")
        else:
            add_once(missing_items, "安全要求", "未明确安全控制要求。")

        if any(k in text for k in ["性能", "响应时间", "并发", "容量"]) or self._has_any_category(issues, self.CATEGORY_PERFORMANCE):
            add_once(covered_items, "性能要求", "需求中包含部分性能或容量相关内容。")
        else:
            add_once(weak_items, "性能要求", "性能指标和容量边界未明确定义。")

        if test_points or self._has_any_category(issues, self.CATEGORY_TESTABILITY):
            add_once(covered_items, "可测试性", "已生成测试点和测试准备信息。")
        else:
            add_once(weak_items, "可测试性", "可验证场景与验收口径不足。")

        if compliance_gaps:
            add_once(weak_items, "合规要求", "已识别合规缺口，但需求未完整定义合规要求。")
        elif any(k in text for k in ["合规", "审计", "隐私", "法规", "监管"]) or self._has_any_category(issues, self.CATEGORY_COMPLIANCE):
            add_once(covered_items, "合规要求", "需求中涉及部分合规内容。")
        else:
            add_once(weak_items, "合规要求", "未看到明确的合规约束。")

        coverage_gaps: List[str] = []
        recommendations: List[str] = []

        if missing_items:
            coverage_gaps.append("当前仍存在覆盖缺失维度，需在开发前补齐。")
            recommendations.append("优先补齐缺失维度，尤其是异常流程、角色权限、安全要求和主流程验收标准。")
        if weak_items:
            coverage_gaps.append("部分维度覆盖较弱，容易导致研发实现和验收口径不一致。")
            recommendations.append("对覆盖较弱维度补充规则、示例、边界条件和可验证标准。")
        if consistency_gaps:
            recommendations.append("结合一致性分析结果统一术语、规则口径和数据定义。")

        recommendations.append("建议按正常流程、异常流程、边界场景、状态流转四个维度完善需求与验收标准。")

        score = int((len(covered_items) + len(weak_items) * 0.5) / len(self.COVERAGE_DIMENSIONS) * 100)
        score = max(0, min(100, score))

        return {
            "covered_dimensions": covered_items,
            "missing_dimensions": missing_items,
            "weak_dimensions": weak_items,
            "coverage_gaps": self._unique_keep_order(coverage_gaps),
            "recommendations": self._unique_keep_order(recommendations),
            "coverage_score": score,
        }

    def _repair_traceability_result(self, traceability: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(traceability, dict):
            return {
                "requirement_items": [],
                "traceability_links": [],
                "uncovered_requirements": [],
                "orphan_rules": [],
                "orphan_test_points": [],
                "recommendations": [],
            }

        fixed_items: List[Dict[str, str]] = []
        for idx, item in enumerate(traceability.get("requirement_items", []) or [], start=1):
            if not isinstance(item, dict):
                continue
            req_id = str(item.get("id") or item.get("requirement_id") or f"REQ-{idx:03d}").strip()
            name = str(item.get("name") or item.get("title") or "").strip()
            description = str(item.get("description") or "").strip()

            if not req_id.startswith("REQ-"):
                req_id = f"REQ-{idx:03d}"
            if not name and description:
                name = description[:24]

            fixed_items.append({
                "id": req_id,
                "name": name,
                "description": description or name,
            })

        fixed_links: List[Dict[str, str]] = []
        for idx, item in enumerate(traceability.get("traceability_links", []) or [], start=1):
            if not isinstance(item, dict):
                continue
            requirement_id = str(item.get("requirement_id") or "").strip()
            link_type = str(item.get("link_type") or "").strip()
            target_id = str(item.get("target_id") or item.get("link_id") or "").strip()
            target_name = str(item.get("target_name") or target_id or "").strip()
            reason = str(item.get("reason") or "").strip()

            if not requirement_id or not link_type:
                continue

            if not target_id:
                if link_type == "rule":
                    target_id = f"RULE-{idx:03d}"
                elif link_type == "test_point":
                    target_id = f"TP-{idx:03d}"
                elif link_type == "issue":
                    target_id = f"ISSUE-{idx:03d}"
                elif link_type == "risk":
                    target_id = f"RISK-{idx:03d}"
                else:
                    target_id = f"TGT-{idx:03d}"

            if not target_name:
                target_name = target_id

            fixed_links.append({
                "requirement_id": requirement_id,
                "link_type": link_type,
                "target_id": target_id,
                "target_name": target_name,
                "reason": reason,
            })

        fixed_uncovered: List[Dict[str, str]] = []
        for idx, item in enumerate(traceability.get("uncovered_requirements", []) or [], start=1):
            if not isinstance(item, dict):
                continue
            requirement_id = str(item.get("requirement_id") or f"REQ-{idx:03d}").strip()
            name = str(item.get("name") or "").strip()
            reason = str(item.get("reason") or "").strip()
            fixed_uncovered.append({
                "requirement_id": requirement_id if requirement_id.startswith("REQ-") else f"REQ-{idx:03d}",
                "name": name,
                "reason": reason,
            })

        return {
            "requirement_items": fixed_items,
            "traceability_links": fixed_links,
            "uncovered_requirements": fixed_uncovered,
            "orphan_rules": traceability.get("orphan_rules", []) or [],
            "orphan_test_points": traceability.get("orphan_test_points", []) or [],
            "recommendations": traceability.get("recommendations", []) or [],
        }

    # =====================================================
    # 评分辅助
    # =====================================================

    def _safe_score(self, score_result: Any) -> int:
        if not isinstance(score_result, dict):
            return 60
        score = score_result.get("score", 60)
        try:
            score = int(score)
        except Exception:
            score = 60
        return max(0, min(100, score))

    def _safe_summary(self, score_result: Any) -> str:
        if not isinstance(score_result, dict):
            return ""
        return str(score_result.get("summary") or "").strip()

    def _safe_quality_level(self, score_result: Any, score: int) -> str:
        if isinstance(score_result, dict):
            quality = str(
                score_result.get("quality_level")
                or score_result.get("qualityLevel")
                or ""
            ).strip().lower()
            if quality in {"excellent", "good", "fair", "poor"}:
                return quality
        return self._map_quality_level(score)

    def _map_quality_level(self, score: int) -> str:
        if score >= 90:
            return "excellent"
        if score >= 75:
            return "good"
        if score >= 60:
            return "fair"
        return "poor"

    def _sanitize_score_result(
        self,
        raw_score_result: Dict[str, Any],
        decision: str,
        score: int,
        quality_level: str,
        summary: str,
    ) -> Dict[str, Any]:
        result = dict(raw_score_result or {})
        result["score"] = score
        result["quality_level"] = quality_level
        result["decision"] = decision
        if summary:
            result["summary"] = summary
        return result

    # =====================================================
    # 统计
    # =====================================================

    def _build_issue_statistics(self, issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        high_count = sum(1 for x in issues if x.get("level") == "high")
        medium_count = sum(1 for x in issues if x.get("level") == "medium")
        low_count = sum(1 for x in issues if x.get("level") == "low")

        blocker_count = sum(1 for x in issues if x.get("severity") == "blocker")
        critical_count = sum(1 for x in issues if x.get("severity") == "critical")
        major_count = sum(1 for x in issues if x.get("severity") == "major")
        minor_count = sum(1 for x in issues if x.get("severity") == "minor")
        suggestion_count = sum(1 for x in issues if x.get("severity") == "suggestion")

        by_category: Dict[str, int] = {}
        by_dimension: Dict[str, int] = {}

        for item in issues:
            category = str(item.get("category") or "").strip()
            dimension = str(item.get("dimension") or "").strip()

            if category:
                by_category[category] = by_category.get(category, 0) + 1
            if dimension:
                by_dimension[dimension] = by_dimension.get(dimension, 0) + 1

        return {
            "totalIssues": len(issues),
            "highCount": high_count,
            "mediumCount": medium_count,
            "lowCount": low_count,
            "blockerCount": blocker_count,
            "criticalCount": critical_count,
            "majorCount": major_count,
            "minorCount": minor_count,
            "suggestionCount": suggestion_count,
            "byCategory": by_category,
            "byDimension": by_dimension,
        }

    # =====================================================
    # 结构化分析
    # =====================================================

    def _build_analysis_sections(
        self,
        issues: List[Dict[str, Any]],
        requirement_text: str,
        structure: Dict[str, Any],
        rules: Dict[str, Any],
        testability: Dict[str, Any],
        consistency: Dict[str, Any],
        coverage: Dict[str, Any],
        security: Dict[str, Any],
        compliance: Dict[str, Any],
        traceability: Dict[str, Any],
        review_result: Dict[str, Any],
    ) -> Dict[str, List[str]]:
        strengths = self._extract_strengths(issues, requirement_text, structure, rules, coverage)
        risks = self._extract_risks(issues, security, compliance)
        ambiguities = self._extract_ambiguities(issues, consistency)
        missing_info = self._extract_missing_info(issues, coverage, review_result)
        business_rules = self._extract_business_rules(issues, consistency)
        dependencies = self._extract_dependencies(requirement_text, issues, structure, coverage)
        acceptance_hints = self._build_acceptance_hints(issues, testability, coverage, consistency, traceability)

        return {
            "strengths": strengths,
            "risks": risks,
            "ambiguities": ambiguities,
            "missingInfo": missing_info,
            "businessRules": business_rules,
            "dependencies": dependencies,
            "acceptanceHints": acceptance_hints,
            "assumptions": [],
            "outOfScope": [],
        }

    def _extract_strengths(self, issues, requirement_text, structure, rules, coverage) -> List[str]:
        strengths: List[str] = []

        if len(requirement_text) > 100:
            strengths.append("需求文本具备基础业务背景信息，可支持初步分析。")

        actors = structure.get("actors", []) if isinstance(structure, dict) else []
        modules = structure.get("modules", []) if isinstance(structure, dict) else []
        workflows = structure.get("workflows", []) if isinstance(structure, dict) else []

        if actors:
            strengths.append("需求中已识别出参与角色信息。")
        if modules:
            strengths.append("需求中已体现模块划分，可支持功能边界分析。")
        if workflows:
            strengths.append("需求中包含一定的业务流程描述。")

        rule_items = rules.get("rules", []) if isinstance(rules, dict) else []
        if rule_items:
            strengths.append("需求中已体现部分业务规则或约束条件。")

        coverage_score = 0
        if isinstance(coverage, dict):
            try:
                coverage_score = int(coverage.get("coverage_score", 0) or 0)
            except Exception:
                coverage_score = 0

        if coverage_score >= 70:
            strengths.append("需求在多个关键维度上具备一定覆盖基础。")

        if 0 < len(issues) <= 3:
            strengths.append("当前识别出的高风险问题较少，需求整体清晰度相对较好。")

        return self._unique_keep_order(strengths)[:6]

    def _extract_risks(self, issues, security, compliance) -> List[str]:
        results = self._extract_by_categories(
            issues,
            self.CATEGORY_EXCEPTION | self.CATEGORY_SECURITY | self.CATEGORY_PERFORMANCE | self.CATEGORY_COMPLIANCE,
        )
        if isinstance(security, dict):
            results.extend(security.get("security_gaps", []) or [])
        if isinstance(compliance, dict):
            results.extend(compliance.get("compliance_gaps", []) or [])
        return self._unique_keep_order(results)[:10]

    def _extract_ambiguities(self, issues, consistency) -> List[str]:
        results = self._extract_by_categories(issues, self.CATEGORY_CLARITY)
        if isinstance(consistency, dict):
            for group in ["term_conflicts", "data_conflicts", "flow_conflicts"]:
                for item in consistency.get(group, []) or []:
                    if isinstance(item, dict):
                        msg = str(item.get("message") or "").strip()
                        if msg:
                            results.append(msg)
        return self._unique_keep_order(results)[:10]

    def _extract_missing_info(self, issues, coverage, review_result) -> List[str]:
        results = self._extract_by_categories(
            issues,
            self.CATEGORY_COMPLETENESS | self.CATEGORY_DATA | self.CATEGORY_BOUNDARY | self.CATEGORY_INTERFACE
        )

        if isinstance(coverage, dict):
            for item in coverage.get("missing_dimensions", []) or []:
                if isinstance(item, dict):
                    reason = str(item.get("reason") or "").strip()
                    if reason:
                        results.append(reason)

        if isinstance(review_result, dict):
            for item in review_result.get("missing_findings", []) or []:
                if isinstance(item, dict):
                    msg = str(item.get("message") or item.get("reason") or "").strip()
                    if msg:
                        results.append(msg)

        return self._unique_keep_order(results)[:10]

    def _extract_business_rules(self, issues, consistency) -> List[str]:
        results = self._extract_by_categories(issues, self.CATEGORY_RULES | self.CATEGORY_STATE)
        if isinstance(consistency, dict):
            for item in consistency.get("rule_conflicts", []) or []:
                if isinstance(item, dict):
                    msg = str(item.get("message") or "").strip()
                    if msg:
                        results.append(msg)
        return self._unique_keep_order(results)[:10]

    def _extract_by_categories(self, issues: List[Dict[str, Any]], categories: set[str]) -> List[str]:
        results: List[str] = []
        category_set = {c.strip().lower() for c in categories}

        for issue in issues:
            category = str(issue.get("category") or "").strip().lower()
            if category in category_set:
                msg = str(issue.get("message") or "").strip()
                if msg:
                    results.append(msg)

        return self._unique_keep_order(results)[:8]

    def _extract_dependencies(self, requirement_text, issues, structure, coverage) -> List[str]:
        deps: List[str] = []
        text = requirement_text.lower()

        keyword_mapping = {
            "登录": ["登录", "鉴权", "token", "session", "auth"],
            "用户身份/KYC": ["kyc", "实名认证", "身份认证", "实名"],
            "风控/审核": ["风控", "审核", "审批"],
            "文件上传": ["上传", "附件", "图片", "材料", "文件"],
            "消息通知": ["通知", "短信", "邮件", "消息", "push"],
            "状态机/流程流转": ["状态", "流转", "节点", "审批流"],
            "外部接口/三方服务": ["api", "接口", "第三方", "回调"],
            "行情数据服务": ["行情", "k线", "蜡烛图"],
            "埋点与数据分析服务": ["埋点", "上报", "分析服务"],
            "本地存储组件": ["本地存储", "localstorage", "缓存"],
        }

        for dep_name, words in keyword_mapping.items():
            if any(word.lower() in text for word in words):
                deps.append(dep_name)

        for issue in issues:
            msg = f"{issue.get('title', '')} {issue.get('message', '')}".lower()
            if "接口" in msg or "api" in msg:
                deps.append("外部接口/三方服务")
            if "上传" in msg or "附件" in msg:
                deps.append("文件上传")
            if "登录" in msg or "鉴权" in msg:
                deps.append("登录")

        if isinstance(structure, dict):
            interfaces = structure.get("interfaces", []) or []
            if interfaces:
                deps.append("外部接口/三方服务")

        if isinstance(coverage, dict):
            for item in coverage.get("missing_dimensions", []) or []:
                if isinstance(item, dict) and str(item.get("dimension") or "").strip() == "依赖接口":
                    deps.append("外部接口/三方服务")

        return self._unique_keep_order(deps)

    def _build_acceptance_hints(self, issues, testability, coverage, consistency, traceability) -> List[str]:
        hints: List[str] = []

        has_rule_issue = self._has_any_category(
            issues,
            self.CATEGORY_RULES | self.CATEGORY_COMPLETENESS | self.CATEGORY_STATE
        )
        has_risk_issue = any(
            self._issue_category(issue) in (
                self.CATEGORY_EXCEPTION
                | self.CATEGORY_SECURITY
                | self.CATEGORY_PERFORMANCE
                | self.CATEGORY_COMPLIANCE
            )
            or str(issue.get("level") or "").strip() == "high"
            for issue in issues
        )

        if has_rule_issue:
            hints.append("建议补充明确的业务规则、状态流转条件与前置约束。")
        if has_risk_issue:
            hints.append("建议补充异常分支、失败回滚、幂等、权限控制及容量约束等验收条件。")

        if isinstance(testability, dict) and (testability.get("coverage_gaps", []) or []):
            hints.append("建议根据可测试性缺口补充可验证场景、测试数据和环境依赖说明。")

        if isinstance(coverage, dict) and (coverage.get("missing_dimensions", []) or []):
            hints.append("建议优先补齐覆盖缺失维度，尤其是异常流程、角色权限、安全要求和边界场景。")

        if isinstance(consistency, dict) and (consistency.get("consistency_gaps", []) or []):
            hints.append("建议统一术语、规则口径、状态流转和数据定义，避免研发与验收理解偏差。")

        if isinstance(traceability, dict) and (traceability.get("uncovered_requirements", []) or []):
            hints.append("建议为核心需求建立需求-规则-测试点-问题追踪矩阵，提升可追踪性。")

        hints.append("建议按正常流程、异常流程、边界场景、状态流转四个维度完善验收标准。")
        return self._unique_keep_order(hints)[:8]

    # =====================================================
    # 总结 / 风险 / 推荐
    # =====================================================

    def _build_summary(
        self,
        score: int,
        quality_level: str,
        score_summary: str,
        statistics: Dict[str, int],
        analysis: Dict[str, List[str]],
        review: Dict[str, Any],
        coverage: Dict[str, Any],
        consistency: Dict[str, Any],
    ) -> str:
        total = statistics.get("totalIssues", 0)
        high = statistics.get("highCount", 0)
        medium = statistics.get("mediumCount", 0)
        low = statistics.get("lowCount", 0)

        quality_text_map = {
            "excellent": "优秀",
            "good": "良好",
            "fair": "一般",
            "poor": "较弱",
        }
        quality_text = quality_text_map.get(quality_level, "一般")

        base = score_summary or f"当前需求质量评分为 {score} 分，整体质量水平为“{quality_text}”。"
        extra_parts: List[str] = [
            f"共识别出 {total} 项问题，其中高优先级 {high} 项，中优先级 {medium} 项，低优先级 {low} 项。"
        ]

        if analysis.get("risks"):
            extra_parts.append("已识别出若干潜在风险点，建议优先补齐关键异常与安全约束。")
        if analysis.get("ambiguities"):
            extra_parts.append("需求中存在表述不清或判定标准不明确的问题，可能影响研发实现与验收一致性。")

        if isinstance(coverage, dict):
            try:
                coverage_score = int(coverage.get("coverage_score", 0) or 0)
            except Exception:
                coverage_score = 0
            if coverage_score < 70:
                extra_parts.append("覆盖率分析显示当前需求在部分关键维度上仍存在明显缺口。")

        if isinstance(consistency, dict) and (consistency.get("consistency_gaps", []) or []):
            extra_parts.append("一致性分析显示存在术语、规则或流程口径不统一的问题。")

        if isinstance(review, dict):
            overall_review = review.get("overall_review", {}) or {}
            if bool(overall_review.get("should_refine", False)):
                extra_parts.append("复核结果显示当前分析仍有细化空间，建议结合复核建议进一步补强。")

        return " ".join([base] + extra_parts).strip()

    def _merge_summary(self, base_summary: str, summary_report: Dict[str, Any], review_result: Dict[str, Any]) -> str:
        overall_quality = str(summary_report.get("overall_quality") or "").strip() if isinstance(summary_report, dict) else ""
        conclusion = str(summary_report.get("conclusion") or "").strip() if isinstance(summary_report, dict) else ""
        review_summary = ""
        if isinstance(review_result, dict):
            overall = review_result.get("overall_review", {}) or {}
            review_summary = str(overall.get("summary") or "").strip()

        parts = [x for x in [overall_quality, base_summary, review_summary, conclusion] if x]
        return " ".join(self._unique_keep_order(parts))

    def _build_compact_risk_report(self, issues: List[Dict[str, Any]], raw_risk_report: Dict[str, Any]) -> Dict[str, Any]:
        risk_refs = issue_aggregator.build_risk_issue_refs(issues)

        high_issue_ids = risk_refs.get("highIssueIds", [])
        medium_issue_ids = risk_refs.get("mediumIssueIds", [])
        low_issue_ids = risk_refs.get("lowIssueIds", [])

        high_count = len(high_issue_ids)
        medium_count = len(medium_issue_ids)
        low_count = len(low_issue_ids)
        total_count = high_count + medium_count + low_count

        top_risks = raw_risk_report.get("top_risks", []) if isinstance(raw_risk_report, dict) else []
        if not isinstance(top_risks, list):
            top_risks = []

        summary_text = (
            f"共识别风险 {total_count} 项。"
            f" 其中高风险 {high_count} 项，"
            f"中风险 {medium_count} 项，"
            f"低风险 {low_count} 项。"
        )

        if top_risks:
            top_texts = [str(x).strip() for x in top_risks if str(x).strip()]
            if top_texts:
                summary_text += " 重点风险包括：" + "；".join(top_texts[:3]) + "。"

        return {
            "summary": {
                "total": total_count,
                "high": high_count,
                "medium": medium_count,
                "low": low_count,
            },
            "riskSummary": summary_text,
            "topRisks": top_risks[:5],
            "highIssueIds": high_issue_ids,
            "mediumIssueIds": medium_issue_ids,
            "lowIssueIds": low_issue_ids,
        }

    def _build_recommendations(
        self,
        analysis: Dict[str, Any],
        coverage: Dict[str, Any],
        consistency: Dict[str, Any],
        review_result: Dict[str, Any],
        score_result: Dict[str, Any],
        summary_report: Dict[str, Any],
    ) -> List[str]:
        recs: List[str] = []

        if isinstance(score_result, dict):
            recs.extend(score_result.get("suggestions", []) or [])

        if isinstance(coverage, dict):
            recs.extend(coverage.get("recommendations", []) or [])

        if isinstance(consistency, dict):
            recs.extend(consistency.get("recommendations", []) or [])

        if isinstance(analysis, dict):
            recs.extend(analysis.get("acceptanceHints", []) or [])

        if isinstance(review_result, dict):
            overall = review_result.get("overall_review", {}) or {}
            recs.extend(overall.get("gate_reason", []) or [])

        if isinstance(summary_report, dict):
            next_action = summary_report.get("next_action", []) or []
            recs.extend(next_action)

        return self._unique_keep_order([str(x).strip() for x in recs if str(x).strip()])[:12]

    def _compact_review_result(self, review_result: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(review_result, dict):
            return {}

        overall = review_result.get("overall_review", {}) or {}
        return {
            "overallReview": {
                "quality": overall.get("quality"),
                "decision": overall.get("decision"),
                "summary": overall.get("summary"),
                "shouldRefine": overall.get("should_refine"),
                "gateReason": overall.get("gate_reason", []) or [],
            },
            "finalTopIssues": review_result.get("final_top_issues", []) or [],
            "missingCount": len(review_result.get("missing_findings", []) or []),
            "duplicateCount": len(review_result.get("duplicate_findings", []) or []),
            "categoryCorrectionCount": len(review_result.get("category_corrections", []) or []),
            "severityCorrectionCount": len(review_result.get("severity_corrections", []) or []),
        }

    # =====================================================
    # 模型安全转换
    # =====================================================

    def _safe_model(self, model_cls, data: Any):
        try:
            if not isinstance(data, dict):
                return None
            return model_cls(**data)
        except Exception:
            return None

    # =====================================================
    # 分类辅助
    # =====================================================

    def _issue_category(self, issue: Dict[str, Any]) -> str:
        return str(issue.get("category") or "").strip()

    def _has_any_category(self, issues: List[Dict[str, Any]], category_set: set[str]) -> bool:
        for issue in issues or []:
            if self._issue_category(issue) in category_set:
                return True
        return False

    # =====================================================
    # 公共工具
    # =====================================================

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