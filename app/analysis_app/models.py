#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/2/8 21:56
# @Author: sulo
# app/analysis_app/models.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import List, Optional, Literal, Dict, Any, Union
from pydantic import BaseModel, Field, ConfigDict


# =====================================================
# 基础 BaseModel
# =====================================================

class AnalysisBaseModel(BaseModel):
    """
    统一基础模型配置：
    - extra='allow'：兼容 agent / pipeline 动态附加字段
    - populate_by_name=True：提高序列化兼容性
    """
    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
    )


# =====================================================
# 基础枚举定义
# =====================================================

IssueLevel = Literal[
    "high",
    "medium",
    "low",
]

IssueSeverity = Literal[
    "blocker",
    "critical",
    "major",
    "minor",
    "suggestion",
]

IssueImpact = Literal[
    "high",
    "medium",
    "low",
]

IssueStatus = Literal[
    "open",
    "accepted",
    "mitigated",
    "resolved",
]

QualityLevel = Literal[
    "excellent",
    "good",
    "fair",
    "poor",
]

ReviewDecision = Literal[
    "pass",
    "conditional_pass",
    "fail",
]

RiskLevel = Literal[
    "high",
    "medium",
    "low",
]

ConfidenceLevel = Literal[
    "high",
    "medium",
    "low",
]

# 注意：这里按 pipeline 实际产出做了兼容扩展
IssueCategory = Literal[
    "完整性",
    "清晰性",
    "一致性",
    "业务规则",
    "流程逻辑",
    "异常处理",
    "边界场景",
    "状态流转",
    "状态机",
    "数据定义",
    "数据",
    "接口契约",
    "依赖约束",
    "依赖",
    "权限安全",
    "安全",
    "合规性",
    "可测试性",
    "可追踪性",
    "可维护性",
    "可扩展性",
    "性能",
    "可观测性",
    "需求质量",
    "系统",
    "解析",
]

IssueDimension = Literal[
    "structure",
    "rule",
    "consistency",
    "coverage",
    "testability",
    "traceability",
    "security",
    "compliance",
    "risk",
    "summary",
    "general",
]


# =====================================================
# 通用基础模型
# =====================================================

class BaseMetaModel(AnalysisBaseModel):
    """
    通用基础元数据
    """
    source_agent: Optional[str] = Field(default=None, description="来源智能体")
    source_stage: Optional[str] = Field(default=None, description="来源阶段")
    confidence: ConfidenceLevel = Field(default="medium", description="判断置信度")


class RequirementReference(AnalysisBaseModel):
    """
    需求引用定位
    """
    ref_id: Optional[str] = Field(default=None, description="引用ID，如 REQ-001")
    section: Optional[str] = Field(default=None, description="章节名")
    clause: Optional[str] = Field(default=None, description="条款名")
    text: Optional[str] = Field(default=None, description="引用原文")
    start_index: Optional[int] = Field(default=None, description="起始位置")
    end_index: Optional[int] = Field(default=None, description="结束位置")


class EvidenceItem(AnalysisBaseModel):
    """
    证据项
    """
    quote: str = Field(default="", description="证据原文")
    reason: Optional[str] = Field(default=None, description="证据解释")
    ref: Optional[RequirementReference] = Field(default=None, description="来源引用")


class RecommendationItem(AnalysisBaseModel):
    """
    整改建议项
    """
    title: Optional[str] = Field(default=None, description="建议标题")
    action: str = Field(default="", description="建议动作")
    priority: IssueLevel = Field(default="medium", description="建议优先级")
    owner_hint: Optional[str] = Field(default=None, description="建议责任角色")
    expected_benefit: Optional[str] = Field(default=None, description="预期收益")


# =====================================================
# 单条问题
# =====================================================

class RequirementIssue(AnalysisBaseModel):
    """
    需求分析识别出的一条问题
    """

    id: Optional[str] = Field(default=None, description="问题ID，如 ISSUE-001")
    level: IssueLevel = Field(..., description="问题等级：high / medium / low")
    severity: IssueSeverity = Field(default="major", description="严重性")
    impact: IssueImpact = Field(default="medium", description="影响面")
    status: IssueStatus = Field(default="open", description="问题状态")

    category: Optional[IssueCategory] = Field(default=None, description="问题分类")
    dimension: IssueDimension = Field(default="general", description="归属分析维度")

    title: Optional[str] = Field(default=None, description="问题标题")
    message: str = Field(..., description="问题描述")
    reason: Optional[str] = Field(default=None, description="问题成因")
    risk: Optional[str] = Field(default=None, description="潜在风险")
    suggestion: Optional[str] = Field(default=None, description="简要建议")
    solution: Optional[str] = Field(default=None, description="建议解决方案")

    evidence: List[EvidenceItem] = Field(default_factory=list, description="证据链")
    requirement_refs: List[RequirementReference] = Field(default_factory=list, description="需求引用")
    tags: List[str] = Field(default_factory=list, description="标签")
    duplicate_keys: List[str] = Field(default_factory=list, description="去重归一键")

    source_agent: Optional[str] = Field(default=None, description="来源智能体")
    source_stage: Optional[str] = Field(default=None, description="来源阶段")
    confidence: ConfidenceLevel = Field(default="medium", description="判断置信度")


# =====================================================
# 统计信息
# =====================================================

class IssueStatistics(AnalysisBaseModel):
    """
    问题统计汇总
    """

    totalIssues: int = Field(default=0, description="问题总数")
    highCount: int = Field(default=0, description="高优先级问题数")
    mediumCount: int = Field(default=0, description="中优先级问题数")
    lowCount: int = Field(default=0, description="低优先级问题数")

    blockerCount: int = Field(default=0, description="blocker 问题数")
    criticalCount: int = Field(default=0, description="critical 问题数")
    majorCount: int = Field(default=0, description="major 问题数")
    minorCount: int = Field(default=0, description="minor 问题数")
    suggestionCount: int = Field(default=0, description="suggestion 问题数")

    byCategory: Dict[str, int] = Field(default_factory=dict, description="按分类统计")
    byDimension: Dict[str, int] = Field(default_factory=dict, description="按维度统计")


# =====================================================
# 结构化分析结果
# =====================================================

class RequirementAnalysisDetail(AnalysisBaseModel):
    """
    结构化需求分析内容
    """

    strengths: List[str] = Field(default_factory=list, description="需求已有优点")
    risks: List[str] = Field(default_factory=list, description="主要风险点")
    ambiguities: List[str] = Field(default_factory=list, description="歧义项")
    missingInfo: List[str] = Field(default_factory=list, description="缺失信息")
    businessRules: List[str] = Field(default_factory=list, description="业务规则识别")
    dependencies: List[str] = Field(default_factory=list, description="依赖项识别")
    assumptions: List[str] = Field(default_factory=list, description="分析假设")
    acceptanceHints: List[str] = Field(default_factory=list, description="验收补充建议")
    outOfScope: List[str] = Field(default_factory=list, description="疑似范围外内容")


# =====================================================
# 需求结构识别
# =====================================================

class WorkflowStep(AnalysisBaseModel):
    name: Optional[str] = Field(default=None, description="流程名称")
    steps: List[str] = Field(default_factory=list, description="流程步骤")
    preconditions: List[str] = Field(default_factory=list, description="前置条件")
    postconditions: List[str] = Field(default_factory=list, description="后置条件")
    exceptions: List[str] = Field(default_factory=list, description="异常流程")


class DataObject(AnalysisBaseModel):
    name: str = Field(default="", description="数据对象名称")
    fields: List[str] = Field(default_factory=list, description="字段列表")
    constraints: List[str] = Field(default_factory=list, description="字段约束")


class InterfaceInfo(AnalysisBaseModel):
    name: str = Field(default="", description="接口名称")
    method: Optional[str] = Field(default=None, description="HTTP 方法")
    path: Optional[str] = Field(default=None, description="接口路径")
    request_fields: List[str] = Field(default_factory=list, description="请求字段")
    response_fields: List[str] = Field(default_factory=list, description="响应字段")
    error_codes: List[str] = Field(default_factory=list, description="错误码")


class RequirementStructureResult(AnalysisBaseModel):
    """
    需求结构解析结果
    """

    actors: List[str] = Field(default_factory=list, description="参与角色")
    modules: List[str] = Field(default_factory=list, description="功能模块")
    business_goals: List[str] = Field(default_factory=list, description="业务目标")
    scenarios: List[str] = Field(default_factory=list, description="业务场景")
    workflows: List[WorkflowStep] = Field(default_factory=list, description="流程定义")
    data_objects: List[DataObject] = Field(default_factory=list, description="数据对象")
    interfaces: List[InterfaceInfo] = Field(default_factory=list, description="接口定义")
    constraints: List[str] = Field(default_factory=list, description="全局约束")
    non_functional_requirements: List[str] = Field(default_factory=list, description="非功能要求")
    missing_sections: List[str] = Field(default_factory=list, description="缺失章节")


# =====================================================
# 业务规则识别
# =====================================================

class RuleItem(AnalysisBaseModel):
    id: Optional[str] = Field(default=None, description="规则ID")
    name: str = Field(default="", description="规则名称")
    condition: Optional[str] = Field(default=None, description="触发条件")
    action: Optional[str] = Field(default=None, description="规则动作")
    priority: Optional[str] = Field(default=None, description="优先级")
    exception: Optional[str] = Field(default=None, description="例外规则")
    source: Optional[str] = Field(default=None, description="规则来源原文")


class StateItem(AnalysisBaseModel):
    name: str = Field(default="", description="状态名")
    from_state: Optional[str] = Field(default=None, description="来源状态")
    to_state: Optional[str] = Field(default=None, description="目标状态")
    trigger: Optional[str] = Field(default=None, description="触发条件")
    guard: Optional[str] = Field(default=None, description="守卫条件")


class ValidationItem(AnalysisBaseModel):
    field: Optional[str] = Field(default=None, description="字段名")
    rule: str = Field(default="", description="校验规则")
    message: Optional[str] = Field(default=None, description="错误提示")
    severity: Optional[str] = Field(default=None, description="校验强度")


class ExceptionRuleItem(AnalysisBaseModel):
    name: Optional[str] = Field(default=None, description="异常名称")
    trigger: Optional[str] = Field(default=None, description="触发条件")
    behavior: str = Field(default="", description="异常处理行为")
    recovery: Optional[str] = Field(default=None, description="恢复机制")


class RequirementRuleResult(AnalysisBaseModel):
    """
    业务规则提取结果
    """

    rules: List[RuleItem] = Field(default_factory=list, description="业务规则")
    conditions: List[RuleItem] = Field(default_factory=list, description="条件/限制")
    states: List[StateItem] = Field(default_factory=list, description="状态与流转")
    validations: List[ValidationItem] = Field(default_factory=list, description="校验规则")
    exceptions: List[ExceptionRuleItem] = Field(default_factory=list, description="异常与例外规则")
    unresolved_rules: List[str] = Field(default_factory=list, description="未闭环规则")
    ambiguous_rules: List[str] = Field(default_factory=list, description="歧义规则")


# =====================================================
# 可测试性分析
# =====================================================

class TestPointItem(AnalysisBaseModel):
    name: str = Field(default="", description="测试点名称")
    type: Optional[str] = Field(default=None, description="测试点类型")
    precondition: Optional[str] = Field(default=None, description="前置条件")
    expected: Optional[str] = Field(default=None, description="预期结果")
    trace_to: Optional[str] = Field(default=None, description="追踪目标")


class AutomationCandidateItem(AnalysisBaseModel):
    name: str = Field(default="", description="自动化候选名称")
    reason: str = Field(default="", description="入选原因")
    priority: IssueLevel = Field(default="medium", description="自动化优先级")


class RequirementTestabilityResult(AnalysisBaseModel):
    """
    可测试性分析结果
    """

    test_points: List[TestPointItem] = Field(default_factory=list, description="测试点")
    coverage_gaps: List[str] = Field(default_factory=list, description="覆盖缺口")
    automation_candidates: List[AutomationCandidateItem] = Field(default_factory=list, description="自动化候选")
    test_data_requirements: List[str] = Field(default_factory=list, description="测试数据要求")
    environment_dependencies: List[str] = Field(default_factory=list, description="环境依赖")
    observability_gaps: List[str] = Field(default_factory=list, description="可观测性缺口")
    acceptance_criteria_gaps: List[str] = Field(default_factory=list, description="验收标准缺口")


# =====================================================
# AI 复核结果
# =====================================================

class ReviewOverallResult(AnalysisBaseModel):
    quality: QualityLevel = Field(default="fair", description="复核质量等级")
    decision: ReviewDecision = Field(default="conditional_pass", description="复核结论")
    summary: str = Field(default="", description="复核总结")
    should_refine: bool = Field(default=False, description="是否建议进一步细化")
    gate_reason: List[str] = Field(default_factory=list, description="门禁原因")


class ReviewCorrectionItem(AnalysisBaseModel):
    item_id: Optional[str] = Field(default=None, description="关联项ID")
    issue_id: Optional[str] = Field(default=None, description="关联问题ID")
    reason: str = Field(default="", description="修正原因")
    suggestion: Optional[str] = Field(default=None, description="修正建议")
    category: Optional[str] = Field(default=None, description="问题分类")
    title: Optional[str] = Field(default=None, description="问题标题")
    message: Optional[str] = Field(default=None, description="问题描述")
    from_value: Optional[str] = Field(default=None, alias="from", description="修正前值")
    to: Optional[str] = Field(default=None, description="修正后目标级别/分类")
    issue_ids: List[str] = Field(default_factory=list, description="重复问题ID集合")


class RequirementReviewResult(AnalysisBaseModel):
    """
    AI 复核结果
    """

    missing_findings: List[ReviewCorrectionItem] = Field(default_factory=list, description="遗漏项")
    duplicate_findings: List[ReviewCorrectionItem] = Field(default_factory=list, description="重复项")
    category_corrections: List[ReviewCorrectionItem] = Field(default_factory=list, description="分类修正")
    severity_corrections: List[ReviewCorrectionItem] = Field(default_factory=list, description="严重级别修正")
    suggestion_improvements: List[ReviewCorrectionItem] = Field(default_factory=list, description="建议优化")
    final_top_issues: List[str] = Field(default_factory=list, description="最终关键问题")
    overall_review: ReviewOverallResult = Field(default_factory=ReviewOverallResult, description="总体复核")


# =====================================================
# 一致性分析
# =====================================================

class ConsistencyConflictItem(AnalysisBaseModel):
    title: str = Field(default="", description="冲突标题")
    message: str = Field(default="", description="冲突描述")
    reason: str = Field(default="", description="冲突原因")
    related_terms: List[str] = Field(default_factory=list, description="相关术语")


class RequirementConsistencyResult(AnalysisBaseModel):
    """
    一致性分析结果
    """

    rule_conflicts: List[ConsistencyConflictItem] = Field(default_factory=list, description="业务规则冲突")
    state_conflicts: List[ConsistencyConflictItem] = Field(default_factory=list, description="状态冲突")
    role_conflicts: List[ConsistencyConflictItem] = Field(default_factory=list, description="角色权限冲突")
    flow_conflicts: List[ConsistencyConflictItem] = Field(default_factory=list, description="流程冲突")
    term_conflicts: List[ConsistencyConflictItem] = Field(default_factory=list, description="术语冲突")
    data_conflicts: List[ConsistencyConflictItem] = Field(default_factory=list, description="数据口径冲突")
    consistency_gaps: List[str] = Field(default_factory=list, description="一致性缺口")
    recommendations: List[str] = Field(default_factory=list, description="一致性改进建议")


# =====================================================
# 覆盖率分析
# =====================================================

class CoverageDimensionItem(AnalysisBaseModel):
    dimension: str = Field(default="", description="覆盖维度")
    reason: str = Field(default="", description="判断原因")


class RequirementCoverageResult(AnalysisBaseModel):
    """
    覆盖率分析结果
    """

    covered_dimensions: List[CoverageDimensionItem] = Field(default_factory=list, description="已覆盖维度")
    missing_dimensions: List[CoverageDimensionItem] = Field(default_factory=list, description="缺失维度")
    weak_dimensions: List[CoverageDimensionItem] = Field(default_factory=list, description="薄弱维度")
    coverage_gaps: List[str] = Field(default_factory=list, description="覆盖缺口")
    recommendations: List[str] = Field(default_factory=list, description="覆盖率改进建议")
    coverage_score: int = Field(default=0, ge=0, le=100, description="覆盖率评分")


# =====================================================
# 安全分析
# =====================================================

class RequirementSecurityResult(AnalysisBaseModel):
    """
    安全分析结果
    """

    authentication_requirements: List[str] = Field(default_factory=list, description="认证要求")
    authorization_requirements: List[str] = Field(default_factory=list, description="授权要求")
    sensitive_data_requirements: List[str] = Field(default_factory=list, description="敏感数据保护要求")
    input_validation_requirements: List[str] = Field(default_factory=list, description="输入校验要求")
    operation_security_requirements: List[str] = Field(default_factory=list, description="关键操作安全要求")
    audit_security_requirements: List[str] = Field(default_factory=list, description="安全审计要求")
    abuse_prevention_requirements: List[str] = Field(default_factory=list, description="滥用防控要求")
    security_gaps: List[str] = Field(default_factory=list, description="安全缺口")
    recommendations: List[str] = Field(default_factory=list, description="安全改进建议")


# =====================================================
# 合规分析
# =====================================================

class RequirementComplianceResult(AnalysisBaseModel):
    """
    合规分析结果
    """

    privacy_requirements: List[str] = Field(default_factory=list, description="隐私要求")
    audit_requirements: List[str] = Field(default_factory=list, description="审计要求")
    regulatory_requirements: List[str] = Field(default_factory=list, description="监管要求")
    data_retention_requirements: List[str] = Field(default_factory=list, description="数据保留要求")
    access_control_requirements: List[str] = Field(default_factory=list, description="访问控制要求")
    user_notification_requirements: List[str] = Field(default_factory=list, description="用户告知要求")
    cross_border_requirements: List[str] = Field(default_factory=list, description="跨境数据要求")
    compliance_gaps: List[str] = Field(default_factory=list, description="合规缺口")
    recommendations: List[str] = Field(default_factory=list, description="合规改进建议")


# =====================================================
# 可追踪性分析
# =====================================================

class RequirementTraceabilityItem(AnalysisBaseModel):
    id: str = Field(default="", description="需求条目标识，如 REQ-001")
    name: str = Field(default="", description="需求条目名称")
    description: str = Field(default="", description="需求条目描述")


class TraceabilityLinkItem(AnalysisBaseModel):
    requirement_id: str = Field(default="", description="需求条目标识")
    link_type: Literal["rule", "test_point", "issue", "risk"] = Field(..., description="链路类型")
    target_id: str = Field(default="", description="目标标识")
    target_name: str = Field(default="", description="目标名称")
    reason: str = Field(default="", description="追踪原因")


class UncoveredRequirementItem(AnalysisBaseModel):
    requirement_id: str = Field(default="", description="未覆盖需求ID")
    name: str = Field(default="", description="需求名称")
    reason: str = Field(default="", description="未覆盖原因")


class OrphanItem(AnalysisBaseModel):
    name: str = Field(default="", description="孤立项名称")
    reason: str = Field(default="", description="孤立原因")


class RequirementTraceabilityResult(AnalysisBaseModel):
    """
    可追踪性分析结果
    """

    requirement_items: List[RequirementTraceabilityItem] = Field(default_factory=list, description="需求条目")
    traceability_links: List[TraceabilityLinkItem] = Field(default_factory=list, description="追踪链路")
    uncovered_requirements: List[UncoveredRequirementItem] = Field(default_factory=list, description="未覆盖需求")
    orphan_rules: List[OrphanItem] = Field(default_factory=list, description="孤立规则")
    orphan_test_points: List[OrphanItem] = Field(default_factory=list, description="孤立测试点")
    recommendations: List[str] = Field(default_factory=list, description="追踪改进建议")


# =====================================================
# 风险报告（精简版，适配新版 pipeline）
# =====================================================

class RequirementRiskSummary(AnalysisBaseModel):
    total: int = Field(default=0, description="风险总数")
    high: int = Field(default=0, description="高风险数量")
    medium: int = Field(default=0, description="中风险数量")
    low: int = Field(default=0, description="低风险数量")


class RequirementRiskReport(AnalysisBaseModel):
    """
    风险评估报告（精简结构）
    """

    summary: RequirementRiskSummary = Field(default_factory=RequirementRiskSummary, description="风险统计摘要")
    riskSummary: str = Field(default="", description="风险总结文本")
    topRisks: List[str] = Field(default_factory=list, description="关键风险摘要")
    highIssueIds: List[str] = Field(default_factory=list, description="高风险关联问题ID")
    mediumIssueIds: List[str] = Field(default_factory=list, description="中风险关联问题ID")
    lowIssueIds: List[str] = Field(default_factory=list, description="低风险关联问题ID")


# =====================================================
# 评分维度
# =====================================================

class ScoreDimension(AnalysisBaseModel):
    """
    单个评分维度
    """

    points: int = Field(default=0, ge=0, le=100, description="维度得分")
    comments: List[str] = Field(default_factory=list, description="维度说明")
    issue_ids: List[str] = Field(default_factory=list, description="关联问题ID")


class RequirementScoreBreakdown(AnalysisBaseModel):
    """
    评分维度明细
    """

    completeness: ScoreDimension = Field(default_factory=ScoreDimension)
    clarity: ScoreDimension = Field(default_factory=ScoreDimension)
    consistency: ScoreDimension = Field(default_factory=ScoreDimension)
    rules: ScoreDimension = Field(default_factory=ScoreDimension)
    coverage: ScoreDimension = Field(default_factory=ScoreDimension)
    testability: ScoreDimension = Field(default_factory=ScoreDimension)
    traceability: ScoreDimension = Field(default_factory=ScoreDimension)
    maintainability: ScoreDimension = Field(default_factory=ScoreDimension)
    security: ScoreDimension = Field(default_factory=ScoreDimension)
    compliance: ScoreDimension = Field(default_factory=ScoreDimension)
    risk: ScoreDimension = Field(default_factory=ScoreDimension)


class RequirementScoreReasons(AnalysisBaseModel):
    """
    按优先级归类的问题原因
    """

    high: List[str] = Field(default_factory=list)
    medium: List[str] = Field(default_factory=list)
    low: List[str] = Field(default_factory=list)


class RequirementScoreResult(AnalysisBaseModel):
    """
    需求质量评分结果
    """

    score: int = Field(..., ge=0, le=100, description="总评分")
    quality_level: QualityLevel = Field(default="fair", description="质量等级")
    decision: ReviewDecision = Field(default="conditional_pass", description="评分结论")
    summary: str = Field(default="", description="评分总结")

    breakdown: RequirementScoreBreakdown = Field(default_factory=RequirementScoreBreakdown)
    reasons: RequirementScoreReasons = Field(default_factory=RequirementScoreReasons)
    suggestions: List[str] = Field(default_factory=list, description="改进建议")
    gate_reasons: List[str] = Field(default_factory=list, description="质量门禁原因")


# =====================================================
# 汇总结论（仅 debug / report 使用）
# =====================================================

class RequirementSummaryReport(AnalysisBaseModel):
    """
    总结报告
    """

    executive_summary: str = Field(default="", description="执行摘要")
    overall_quality: str = Field(default="", description="总体质量总结")
    major_issues: str = Field(default="", description="主要问题概览")
    risk_assessment: str = Field(default="", description="风险评估")
    improvement_suggestions: str = Field(default="", description="改进建议")
    conclusion: str = Field(default="", description="最终总结")
    maintainability_and_scalability: str = Field(default="", description="可维护性与可扩展性分析")
    compliance_check: str = Field(default="", description="合规性检查")
    next_action: List[str] = Field(default_factory=list, description="下一步动作建议")


# =====================================================
# Agent 级输出
# =====================================================

class AgentExecutionMeta(AnalysisBaseModel):
    """
    Agent 执行元数据
    """
    name: str = Field(default="", description="agent 名称")
    enabled: bool = Field(default=True, description="是否启用")
    success: bool = Field(default=True, description="是否执行成功")
    durationMs: Optional[int] = Field(default=None, description="执行耗时毫秒")
    message: Optional[str] = Field(default=None, description="执行说明")
    error: Optional[str] = Field(default=None, description="错误信息")


class AgentAnalysisResult(AnalysisBaseModel):
    """
    单个 agent 标准输出
    """

    agent: str = Field(default="", description="agent 名称")
    issues: List[RequirementIssue] = Field(default_factory=list, description="该 agent 识别的问题")
    summary: Optional[str] = Field(default=None, description="agent 摘要")
    statistics: Optional[IssueStatistics] = Field(default=None, description="该 agent 统计")
    payload: Optional[Dict[str, Any]] = Field(default=None, description="原始/结构化扩展结果")
    execution: Optional[AgentExecutionMeta] = Field(default=None, description="执行信息")


# =====================================================
# 企业级门禁结果
# =====================================================

class QualityGateResult(AnalysisBaseModel):
    """
    企业级质量门禁
    """

    passed: bool = Field(default=False, description="是否通过")
    decision: ReviewDecision = Field(default="conditional_pass", description="门禁结论")
    reasons: List[str] = Field(default_factory=list, description="门禁原因")
    blocker_issue_ids: List[str] = Field(default_factory=list, description="阻塞问题")
    critical_issue_ids: List[str] = Field(default_factory=list, description="关键问题")


# =====================================================
# Pipeline 中间结果模型（兼容新版 pipeline 内部构建）
# =====================================================

class RequirementAnalysisResult(AnalysisBaseModel):
    """
    需求分析流水线内部结果模型
    注意：该模型主要用于 pipeline 内部中间态，不代表最终对前端的精简返回结构
    """

    summary: str = Field(default="", description="分析总结")
    score: int = Field(default=0, ge=0, le=100, description="需求质量评分")
    qualityLevel: QualityLevel = Field(default="fair", description="质量等级")
    decision: ReviewDecision = Field(default="conditional_pass", description="最终裁决")
    qualityGate: QualityGateResult = Field(default_factory=QualityGateResult, description="质量门禁结果")

    issues: List[RequirementIssue] = Field(default_factory=list, description="问题列表")
    statistics: IssueStatistics = Field(default_factory=IssueStatistics, description="问题统计")
    analysis: RequirementAnalysisDetail = Field(default_factory=RequirementAnalysisDetail, description="结构化分析")

    structure: Optional[RequirementStructureResult] = Field(default=None, description="需求结构识别结果")
    rules: Optional[RequirementRuleResult] = Field(default=None, description="业务规则识别结果")
    testability: Optional[RequirementTestabilityResult] = Field(default=None, description="可测试性分析结果")
    consistency: Optional[RequirementConsistencyResult] = Field(default=None, description="一致性分析结果")
    coverage: Optional[RequirementCoverageResult] = Field(default=None, description="覆盖率分析结果")
    security: Optional[RequirementSecurityResult] = Field(default=None, description="安全分析结果")
    compliance: Optional[RequirementComplianceResult] = Field(default=None, description="合规分析结果")
    traceability: Optional[RequirementTraceabilityResult] = Field(default=None, description="可追踪性分析结果")
    review: Optional[RequirementReviewResult] = Field(default=None, description="AI 复核结果")
    scoreResult: Optional[RequirementScoreResult] = Field(default=None, description="完整评分结果")
    riskReport: Optional[RequirementRiskReport] = Field(default=None, description="风险评估结果")
    summaryReport: Optional[RequirementSummaryReport] = Field(default=None, description="总结报告")

    agentResults: List[AgentAnalysisResult] = Field(default_factory=list, description="各 agent 输出")
    analysisMarkdown: Optional[str] = Field(default=None, description="Markdown 版完整分析报告")

    runId: Optional[str] = Field(default=None, description="本次运行ID")
    cacheHit: bool = Field(default=False, description="是否命中缓存")
    cacheKey: Optional[str] = Field(default=None, description="缓存键")
    durationMs: Optional[int] = Field(default=None, description="总耗时毫秒")
    failedAgents: List[str] = Field(default_factory=list, description="失败的 agent 列表")


# =====================================================
# SSE 事件
# =====================================================

SSEEventType = Literal[
    "stage",
    "agent_start",
    "agent_done",
    "issue",
    "statistics",
    "analysis",
    "score",
    "review",
    "risk",
    "report",
    "partial",
    "progress",
    "cache_hit",
    "done",
    "error",
    "heartbeat",
]


class AnalysisSSEEvent(AnalysisBaseModel):
    """
    Analysis SSE 统一事件结构
    """

    type: SSEEventType = Field(..., description="事件类型")

    stage: Optional[str] = Field(default=None, description="阶段名称")
    agent: Optional[str] = Field(default=None, description="agent 名称")
    status: Optional[str] = Field(default=None, description="状态")
    progress: Optional[int] = Field(default=None, description="进度百分比")

    id: Optional[str] = Field(default=None)
    level: Optional[IssueLevel] = Field(default=None)
    category: Optional[str] = Field(default=None)
    dimension: Optional[str] = Field(default=None)
    title: Optional[str] = Field(default=None)
    message: Optional[str] = Field(default=None)
    suggestion: Optional[str] = Field(default=None)
    severity: Optional[IssueSeverity] = Field(default=None)
    impact: Optional[IssueImpact] = Field(default=None)
    solution: Optional[str] = Field(default=None)

    value: Optional[Union[Dict[str, Any], str]] = Field(default=None, description="通用载荷")

    score: Optional[int] = Field(default=None)
    qualityLevel: Optional[QualityLevel] = Field(default=None)
    decision: Optional[ReviewDecision] = Field(default=None)
    summary: Optional[str] = Field(default=None)

    cache_hit: Optional[bool] = Field(default=None, description="是否命中缓存")
    reused_inflight: Optional[bool] = Field(default=None, description="是否复用正在执行中的请求")

    trace: Optional[str] = Field(default=None, description="错误堆栈")
    ts: Optional[int] = Field(default=None, description="时间戳")
    durationMs: Optional[int] = Field(default=None, description="耗时毫秒")

# =====================================================
# API 输出模型（企业级标准返回）
# =====================================================

class AnalysisOverview(AnalysisBaseModel):
    """
    前端概览信息
    """

    summary: str = Field(default="", description="分析总结")

    score: int = Field(default=0, ge=0, le=100)
    qualityLevel: QualityLevel = Field(default="fair")
    decision: ReviewDecision = Field(default="conditional_pass")

    issueCount: int = Field(default=0)
    highCount: int = Field(default=0)

    durationMs: Optional[int] = Field(default=None)


class AnalysisMeta(AnalysisBaseModel):
    """
    运行元信息
    """

    runId: Optional[str] = Field(default=None)
    cacheHit: bool = Field(default=False)
    durationMs: Optional[int] = Field(default=None)

    schemaVersion: str = Field(default="1.0")


class AnalysisPanelAnalysis(AnalysisBaseModel):
    """
    analysis 面板
    """

    data: RequirementAnalysisDetail = Field(default_factory=RequirementAnalysisDetail)


class AnalysisPanelCoverage(AnalysisBaseModel):
    """
    coverage 面板
    """

    data: RequirementCoverageResult = Field(default_factory=RequirementCoverageResult)


class AnalysisPanelReview(AnalysisBaseModel):
    """
    review 面板
    """

    data: RequirementReviewResult = Field(default_factory=RequirementReviewResult)


class AnalysisPanelScore(AnalysisBaseModel):
    """
    score 面板
    """

    data: RequirementScoreResult = Field(default_factory=RequirementScoreResult)


class AnalysisPanelRisk(AnalysisBaseModel):
    """
    risk 面板
    """

    data: RequirementRiskReport = Field(default_factory=RequirementRiskReport)


class AnalysisPanels(AnalysisBaseModel):
    """
    所有分析面板
    """

    analysis: Optional[AnalysisPanelAnalysis] = None
    coverage: Optional[AnalysisPanelCoverage] = None
    review: Optional[AnalysisPanelReview] = None
    score: Optional[AnalysisPanelScore] = None
    risk: Optional[AnalysisPanelRisk] = None


# =====================================================
# Top Issues
# =====================================================

class TopIssueItem(AnalysisBaseModel):

    issueId: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None
    severity: Optional[str] = None
    level: Optional[str] = None


# =====================================================
# Lite Result（前端默认接口）
# =====================================================

class AnalysisResultLite(AnalysisBaseModel):

    overview: AnalysisOverview

    qualityGate: Optional[QualityGateResult] = None

    topIssues: List[TopIssueItem] = Field(default_factory=list)

    statistics: Optional[IssueStatistics] = None

    recommendations: List[str] = Field(default_factory=list)

    meta: AnalysisMeta = Field(default_factory=AnalysisMeta)


# =====================================================
# Detail Result（完整分析）
# =====================================================

class AnalysisResultDetail(AnalysisResultLite):

    issues: List[RequirementIssue] = Field(default_factory=list)

    panels: Optional[AnalysisPanels] = None


# =====================================================
# API 统一返回结构
# =====================================================

class AnalysisApiResponse(AnalysisBaseModel):

    ok: bool = Field(default=True)

    workflowId: Optional[str] = None

    requirementId: Optional[str] = None

    status: Literal[
        "idle",
        "running",
        "done",
        "error",
    ] = "done"

    hasResult: bool = Field(default=False)

    schemaVersion: str = Field(default="1.0")

    result: Optional[Union[
        AnalysisResultLite,
        AnalysisResultDetail
    ]] = None

    error: Optional[str] = None

    message: Optional[str] = None