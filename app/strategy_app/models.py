#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/models.py

from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field


# =====================================================
# 字面量定义
# =====================================================

RiskLevel = Literal["P0", "P1", "P2", "P3"]
OverallRiskLevel = Literal["高", "中", "低"]
ChangeScopeLevel = Literal["大", "中", "小"]

BusinessDomain = Literal[
    "登录注册",
    "用户中心",
    "现货",
    "合约",
    "充值",
    "提现",
    "划转",
    "P2P",
    "跟单",
    "撮合",
    "风控",
    "KYC",
    "资产",
    "通用",
]

TestLevelType = Literal["UI", "API", "SERVICE", "DB", "E2E", "MANUAL"]

TestTypeLiteral = Literal[
    "功能测试",
    "接口测试",
    "联调测试",
    "冒烟测试",
    "回归测试",
    "异常流测试",
    "边界值测试",
    "权限测试",
    "风控测试",
    "并发测试",
    "幂等测试",
    "性能测试",
    "兼容性测试",
    "安全测试",
    "数据一致性测试",
    "可观测性验证",
]

PriorityLevel = Literal["P0", "P1", "P2", "P3"]
GateDecision = Literal["pass", "conditional_pass", "fail"]
GateLevel = Literal["blocker", "critical", "high", "medium", "low"]

RunStatus = Literal[
    "idle",
    "queued",
    "running",
    "done",
    "error",
    "cancelled",
    "cancelling",
]


# =====================================================
# 运行阶段 / 上下文模型
# =====================================================

class StrategyContextMeta(BaseModel):
    """
    上下文信息
    """
    has_requirement: bool = Field(default=False, description="是否有需求文本")
    has_analysis_result: bool = Field(default=False, description="是否有需求分析结果")
    has_testcase_result: bool = Field(default=False, description="是否有测试用例结果")
    requirement_length: int = Field(default=0, description="需求文本长度")
    business_domain_hint: Optional[str] = Field(default=None, description="业务域提示")
    source_types: List[str] = Field(default_factory=list, description="输入来源类型")


# =====================================================
# 基础模型
# =====================================================

class StrategySummary(BaseModel):
    """
    策略报告摘要
    """
    title: str = Field(default="测试策略分析结果", description="报告标题")
    business_domain: BusinessDomain = Field(
        default="通用",
        description="业务域识别结果",
    )
    change_scope: ChangeScopeLevel = Field(
        default="中",
        description="本次变更范围等级：大/中/小",
    )
    overall_risk: OverallRiskLevel = Field(
        default="中",
        description="整体风险等级：高/中/低",
    )
    objective: str = Field(
        default="识别高风险链路并给出可执行的测试策略建议",
        description="测试目标摘要",
    )
    core_reason: List[str] = Field(
        default_factory=list,
        description="核心结论原因",
    )
    test_objectives: List[str] = Field(
        default_factory=list,
        description="测试目标列表（兼容 summary 内联展示）",
    )
    context_completeness: Dict[str, bool] = Field(
        default_factory=dict,
        description="上下文完整度，例如 has_requirement / has_analysis_result / has_testcase_result",
    )


class ImpactModule(BaseModel):
    """
    受影响模块
    """
    name: str = Field(..., description="模块名称")
    reason: Optional[str] = Field(default=None, description="受影响原因")
    level: Optional[str] = Field(default=None, description="影响等级，可选")
    direct: bool = Field(default=True, description="是否直接影响")
    upstream: bool = Field(default=False, description="是否上游依赖")
    downstream: bool = Field(default=False, description="是否下游依赖")


class ImpactRole(BaseModel):
    """
    受影响角色
    """
    name: str = Field(..., description="角色名称")
    reason: Optional[str] = Field(default=None, description="影响原因")
    permissions: List[str] = Field(default_factory=list, description="涉及权限点")


class AffectedFlow(BaseModel):
    """
    受影响流程
    """
    name: str = Field(..., description="流程名称")
    steps: List[str] = Field(default_factory=list, description="关键流程步骤")
    reason: Optional[str] = Field(default=None, description="受影响原因")
    level: Optional[str] = Field(default=None, description="流程影响等级")
    is_core: bool = Field(default=False, description="是否核心流程")


class StrategyRiskItem(BaseModel):
    """
    风险项
    """
    risk_id: Optional[str] = Field(default=None, description="风险ID")
    title: str = Field(..., description="风险标题")
    level: RiskLevel = Field(default="P2", description="风险等级：P0/P1/P2/P3")
    category: Optional[str] = Field(default=None, description="风险分类")
    reason: str = Field(default="", description="风险原因")
    trigger_condition: Optional[str] = Field(default=None, description="触发条件")
    impact: Optional[str] = Field(default=None, description="影响说明")
    suggestion: Optional[str] = Field(default=None, description="测试或规避建议")
    related_modules: List[str] = Field(default_factory=list, description="关联模块")
    related_flows: List[str] = Field(default_factory=list, description="关联流程")
    test_types: List[str] = Field(default_factory=list, description="建议测试类型")
    automation_candidate: bool = Field(default=False, description="是否适合自动化")
    affects_release_gate: bool = Field(default=False, description="是否影响上线准入")

    # 企业级增强字段
    verify_points: List[str] = Field(default_factory=list, description="关键验证点")
    gate_level: Optional[GateLevel] = Field(default=None, description="门禁等级")
    data_dependencies: List[str] = Field(default_factory=list, description="依赖数据")
    api_dependencies: List[str] = Field(default_factory=list, description="依赖接口")
    job_dependencies: List[str] = Field(default_factory=list, description="依赖任务")
    monitor_points: List[str] = Field(default_factory=list, description="上线后监控点")


class ScopeItem(BaseModel):
    """
    范围项，用于 must_test / should_test / defer_test / smoke_scope / regression_scope / out_of_scope
    """
    title: str = Field(..., description="范围标题")
    reason: Optional[str] = Field(default=None, description="纳入该范围的原因")
    priority: Optional[PriorityLevel] = Field(default=None, description="优先级，例如 P0/P1")
    related_modules: List[str] = Field(default_factory=list, description="关联模块")
    related_flows: List[str] = Field(default_factory=list, description="关联流程")
    test_types: List[str] = Field(default_factory=list, description="建议测试类型")
    owner: Optional[str] = Field(default=None, description="建议负责角色/团队")


class LayerAdviceItem(BaseModel):
    """
    测试层级建议项
    """
    title: str = Field(..., description="建议标题")
    level_type: Optional[TestLevelType] = Field(default=None, description="测试层级类型")
    reason: Optional[str] = Field(default=None, description="建议原因")
    related_scope: List[str] = Field(default_factory=list, description="关联范围")
    related_risks: List[str] = Field(default_factory=list, description="关联风险")
    priority: Optional[PriorityLevel] = Field(default=None, description="建议优先级")


class StrategyLayerAdvice(BaseModel):
    """
    测试层级建议
    """
    ui: List[LayerAdviceItem] = Field(default_factory=list, description="UI 测试建议")
    api: List[LayerAdviceItem] = Field(default_factory=list, description="API 测试建议")
    service: List[LayerAdviceItem] = Field(default_factory=list, description="服务层测试建议")
    db: List[LayerAdviceItem] = Field(default_factory=list, description="数据层测试建议")
    e2e: List[LayerAdviceItem] = Field(default_factory=list, description="端到端测试建议")
    manual: List[LayerAdviceItem] = Field(default_factory=list, description="人工测试建议")
    automation_candidate: List[LayerAdviceItem] = Field(
        default_factory=list,
        description="自动化候选建议",
    )


class TestTypeAdviceItem(BaseModel):
    """
    测试类型建议项
    """
    type_name: TestTypeLiteral = Field(..., description="测试类型")
    necessary: bool = Field(default=True, description="是否必须")
    priority: PriorityLevel = Field(default="P1", description="优先级")
    scope: List[str] = Field(default_factory=list, description="适用范围")
    reason: Optional[str] = Field(default=None, description="建议原因")
    automation_candidate: bool = Field(default=False, description="是否适合自动化")
    related_risks: List[str] = Field(default_factory=list, description="关联风险")


class StrategyResourcePlanItem(BaseModel):
    """
    资源规划项
    """
    title: str = Field(..., description="资源规划标题")
    scope: List[str] = Field(default_factory=list, description="建议执行范围")
    focus: List[str] = Field(default_factory=list, description="重点关注点")
    note: Optional[str] = Field(default=None, description="备注")


class StrategyResourcePlan(BaseModel):
    """
    人天 / 时间资源规划
    """
    one_day: List[StrategyResourcePlanItem] = Field(
        default_factory=list,
        description="1 人天建议",
    )
    two_days: List[StrategyResourcePlanItem] = Field(
        default_factory=list,
        description="2 人天建议",
    )
    three_days: List[StrategyResourcePlanItem] = Field(
        default_factory=list,
        description="3 人天建议",
    )
    five_days: List[StrategyResourcePlanItem] = Field(
        default_factory=list,
        description="5 人天建议",
    )


class ExecutionOrderItem(BaseModel):
    """
    执行顺序建议
    """
    order: int = Field(..., description="执行顺序")
    title: str = Field(..., description="执行项标题")
    reason: Optional[str] = Field(default=None, description="排序原因")
    related_scope: List[str] = Field(default_factory=list, description="关联范围")
    related_risks: List[str] = Field(default_factory=list, description="关联风险")
    blocking: bool = Field(default=False, description="是否为阻塞前置项")


class BlockerItem(BaseModel):
    """
    阻塞项
    """
    title: str = Field(..., description="阻塞项标题")
    reason: str = Field(default="", description="阻塞原因")
    owner: Optional[str] = Field(default=None, description="责任方")
    suggestion: Optional[str] = Field(default=None, description="处理建议")
    severity: Optional[str] = Field(default=None, description="严重级别")


class PendingConfirmationItem(BaseModel):
    """
    待确认项
    """
    title: str = Field(..., description="待确认项标题")
    reason: str = Field(default="", description="待确认原因")
    owner: Optional[str] = Field(default=None, description="建议确认对象")
    impact: Optional[str] = Field(default=None, description="对测试策略的影响")
    blocking: bool = Field(default=False, description="是否阻塞执行")


class ReleaseChecklistItem(BaseModel):
    """
    发布前检查项
    """
    title: str = Field(..., description="检查项标题")
    reason: Optional[str] = Field(default=None, description="检查原因")
    required: bool = Field(default=True, description="是否必须检查")
    owner: Optional[str] = Field(default=None, description="建议责任方")
    related_risks: List[str] = Field(default_factory=list, description="关联风险")


class EntryCriteriaItem(BaseModel):
    """
    测试准入条件
    """
    title: str = Field(..., description="准入项")
    required: bool = Field(default=True, description="是否必须满足")
    reason: Optional[str] = Field(default=None, description="说明")
    owner: Optional[str] = Field(default=None, description="责任方")


class ExitCriteriaItem(BaseModel):
    """
    测试准出条件
    """
    title: str = Field(..., description="准出项")
    required: bool = Field(default=True, description="是否必须满足")
    reason: Optional[str] = Field(default=None, description="说明")
    owner: Optional[str] = Field(default=None, description="责任方")


class EnvironmentStrategyItem(BaseModel):
    """
    环境策略项
    """
    env_name: str = Field(..., description="环境名称")
    purpose: Optional[str] = Field(default=None, description="环境用途")
    required: bool = Field(default=True, description="是否必须")
    notes: List[str] = Field(default_factory=list, description="补充说明")


class TestDataStrategyItem(BaseModel):
    """
    测试数据策略项
    """
    title: str = Field(..., description="数据项标题")
    data_type: Optional[str] = Field(default=None, description="数据类型")
    purpose: Optional[str] = Field(default=None, description="用途")
    required: bool = Field(default=True, description="是否必须")
    notes: List[str] = Field(default_factory=list, description="补充说明")


class AutomationStrategyItem(BaseModel):
    """
    自动化策略项
    """
    title: str = Field(..., description="自动化建议标题")
    scope: List[str] = Field(default_factory=list, description="自动化覆盖范围")
    priority: PriorityLevel = Field(default="P1", description="自动化优先级")
    reason: Optional[str] = Field(default=None, description="建议原因")
    framework_hint: Optional[str] = Field(default=None, description="实现建议")


class RegressionStrategyItem(BaseModel):
    """
    回归策略项
    """
    title: str = Field(..., description="回归项标题")
    scope: List[str] = Field(default_factory=list, description="回归范围")
    reason: Optional[str] = Field(default=None, description="回归原因")
    priority: PriorityLevel = Field(default="P1", description="优先级")


class ReleaseStrategyItem(BaseModel):
    """
    发布策略项
    """
    title: str = Field(..., description="发布建议标题")
    reason: Optional[str] = Field(default=None, description="建议原因")
    required: bool = Field(default=False, description="是否建议强制执行")
    notes: List[str] = Field(default_factory=list, description="补充说明")


class RollbackStrategyItem(BaseModel):
    """
    回滚策略项
    """
    title: str = Field(..., description="回滚建议标题")
    trigger: Optional[str] = Field(default=None, description="触发条件")
    action: Optional[str] = Field(default=None, description="处理动作")
    notes: List[str] = Field(default_factory=list, description="补充说明")


class QualityGate(BaseModel):
    """
    质量门禁
    """
    decision: GateDecision = Field(default="conditional_pass", description="门禁结论")
    reasons: List[str] = Field(default_factory=list, description="门禁原因")
    blocker_risks: List[str] = Field(default_factory=list, description="阻塞风险")
    required_actions: List[str] = Field(default_factory=list, description="通过前必做动作")


class StrategyMetrics(BaseModel):
    """
    策略统计信息
    """
    impact_module_count: int = Field(default=0, description="影响模块数")
    impact_flow_count: int = Field(default=0, description="影响流程数")
    risk_count: int = Field(default=0, description="风险数")
    must_test_count: int = Field(default=0, description="必测范围数")
    regression_scope_count: int = Field(default=0, description="回归范围数")
    blocker_count: int = Field(default=0, description="阻塞项数")
    pending_confirmation_count: int = Field(default=0, description="待确认项数")


# =====================================================
# 主结果模型
# =====================================================

class StrategyResult(BaseModel):
    """
    测试策略智能体最终输出
    """
    workflow_id: Optional[str] = Field(default=None, description="工作流ID")
    requirement_id: Optional[str] = Field(default=None, description="需求ID")

    # 运行态字段：便于前后端联调 / 落库 / 结果追踪
    job_id: Optional[str] = Field(default=None, description="任务ID")
    stream_id: Optional[str] = Field(default=None, description="流ID")
    status: Optional[RunStatus] = Field(default=None, description="运行状态")
    duration_ms: Optional[int] = Field(default=None, description="运行耗时毫秒")

    summary: StrategySummary = Field(
        default_factory=StrategySummary,
        description="策略摘要",
    )

    test_objectives: List[str] = Field(
        default_factory=list,
        description="测试目标列表",
    )

    impact_modules: List[ImpactModule] = Field(
        default_factory=list,
        description="受影响模块",
    )
    impact_roles: List[ImpactRole] = Field(
        default_factory=list,
        description="受影响角色",
    )
    affected_flows: List[AffectedFlow] = Field(
        default_factory=list,
        description="受影响流程",
    )

    risk_items: List[StrategyRiskItem] = Field(
        default_factory=list,
        description="风险项列表",
    )

    must_test: List[ScopeItem] = Field(
        default_factory=list,
        description="必测范围",
    )
    should_test: List[ScopeItem] = Field(
        default_factory=list,
        description="建议测试范围",
    )
    defer_test: List[ScopeItem] = Field(
        default_factory=list,
        description="可延后测试范围",
    )
    out_of_scope: List[ScopeItem] = Field(
        default_factory=list,
        description="明确不测范围",
    )
    smoke_scope: List[ScopeItem] = Field(
        default_factory=list,
        description="冒烟测试范围",
    )
    regression_scope: List[ScopeItem] = Field(
        default_factory=list,
        description="回归测试范围",
    )

    test_layer_advice: StrategyLayerAdvice = Field(
        default_factory=StrategyLayerAdvice,
        description="测试层级建议",
    )

    test_type_matrix: List[TestTypeAdviceItem] = Field(
        default_factory=list,
        description="测试类型矩阵",
    )

    environment_strategy: List[EnvironmentStrategyItem] = Field(
        default_factory=list,
        description="环境策略",
    )
    test_data_strategy: List[TestDataStrategyItem] = Field(
        default_factory=list,
        description="测试数据策略",
    )
    automation_strategy: List[AutomationStrategyItem] = Field(
        default_factory=list,
        description="自动化策略",
    )
    regression_strategy: List[RegressionStrategyItem] = Field(
        default_factory=list,
        description="回归策略",
    )
    release_strategy: List[ReleaseStrategyItem] = Field(
        default_factory=list,
        description="发布策略",
    )
    rollback_strategy: List[RollbackStrategyItem] = Field(
        default_factory=list,
        description="回滚策略",
    )

    entry_criteria: List[EntryCriteriaItem] = Field(
        default_factory=list,
        description="测试准入条件",
    )
    exit_criteria: List[ExitCriteriaItem] = Field(
        default_factory=list,
        description="测试准出条件",
    )

    resource_plan: StrategyResourcePlan = Field(
        default_factory=StrategyResourcePlan,
        description="资源规划建议",
    )

    execution_order: List[ExecutionOrderItem] = Field(
        default_factory=list,
        description="执行顺序建议",
    )

    blockers: List[BlockerItem] = Field(
        default_factory=list,
        description="阻塞项",
    )
    pending_confirmations: List[PendingConfirmationItem] = Field(
        default_factory=list,
        description="待确认项",
    )
    release_checklist: List[ReleaseChecklistItem] = Field(
        default_factory=list,
        description="发布前检查项",
    )

    quality_gate: QualityGate = Field(
        default_factory=QualityGate,
        description="质量门禁结论",
    )

    assumptions: List[str] = Field(
        default_factory=list,
        description="策略分析时的假设前提",
    )
    notes: List[str] = Field(
        default_factory=list,
        description="补充说明",
    )

    metrics: StrategyMetrics = Field(
        default_factory=StrategyMetrics,
        description="统计信息",
    )

    markdown: str = Field(
        default="",
        description="最终策略 markdown 文本",
    )

    # 新增：直接兼容 pipeline 里传入的 context_meta
    context_meta: StrategyContextMeta = Field(
        default_factory=StrategyContextMeta,
        description="上下文元信息",
    )

    # 保留旧字段，兼容旧前端/旧落库
    raw_context_meta: Dict[str, Any] = Field(
        default_factory=dict,
        description="上下文元信息（兼容旧字段）",
    )

    raw_agent_outputs: Dict[str, Any] = Field(
        default_factory=dict,
        description="各 agent 原始输出",
    )

    def ensure_enterprise_defaults(self) -> "StrategyResult":
        """
        企业级兜底：
        当上游 agent / pipeline 某些字段缺失时，保证结果至少可用、可展示、可决策
        """
        if not self.summary:
            self.summary = StrategySummary()

        if not self.summary.title:
            self.summary.title = "测试策略分析结果"

        if not self.summary.objective:
            self.summary.objective = "识别高风险链路并给出可执行的测试策略建议"

        if not self.test_objectives:
            self.test_objectives = [
                "识别本次变更影响范围与高风险链路",
                "确保核心主流程、关键异常流和高风险联动场景得到覆盖",
                "为回归、自动化与上线决策提供可执行策略依据",
            ]

        # 同步到 summary，兼容前端直接从 summary 取值
        if not self.summary.test_objectives:
            self.summary.test_objectives = list(self.test_objectives)

        if not self.risk_items:
            self.risk_items = [
                StrategyRiskItem(
                    risk_id="RISK-001",
                    title="缺少风险识别结果",
                    level="P1",
                    category="分析缺失",
                    reason="当前未识别出任何风险项，策略结果不完整",
                    trigger_condition="上游风险识别为空或输出异常时",
                    impact="可能导致高风险场景遗漏，影响上线判断",
                    suggestion="建议补充风险识别，并优先检查主链路、资金、权限、状态流转相关场景",
                    related_modules=[],
                    related_flows=[],
                    test_types=["功能测试", "异常流测试"],
                    automation_candidate=False,
                    affects_release_gate=True,
                    verify_points=["补充高风险识别并确认核心链路验证范围"],
                    gate_level="critical",
                    data_dependencies=[],
                    api_dependencies=[],
                    job_dependencies=[],
                    monitor_points=[],
                )
            ]

        if not self.must_test:
            self.must_test = [
                ScopeItem(
                    title="核心主流程验证",
                    reason="缺少明确必测范围时，至少应覆盖核心主流程",
                    priority="P0",
                    related_modules=[],
                    related_flows=[],
                    test_types=["功能测试", "接口测试", "冒烟测试"],
                    owner="测试",
                )
            ]

        if not self.smoke_scope:
            self.smoke_scope = self.must_test[:2]

        if not self.regression_scope:
            self.regression_scope = self.must_test[:3]

        if not self.entry_criteria:
            self.entry_criteria = [
                EntryCriteriaItem(
                    title="需求说明已明确且可供测试理解",
                    required=True,
                    reason="避免测试预期偏差",
                    owner="产品",
                ),
                EntryCriteriaItem(
                    title="测试环境与关键依赖可用",
                    required=True,
                    reason="保证主链路可执行",
                    owner="研发/测试",
                ),
                EntryCriteriaItem(
                    title="测试账号与数据准备完成",
                    required=True,
                    reason="保证重点场景可覆盖",
                    owner="测试",
                ),
            ]

        if not self.exit_criteria:
            self.exit_criteria = [
                ExitCriteriaItem(
                    title="核心主流程验证通过",
                    required=True,
                    reason="主链路是最低上线保障",
                    owner="测试",
                ),
                ExitCriteriaItem(
                    title="阻塞级问题为 0",
                    required=True,
                    reason="阻塞问题不得带入线上",
                    owner="测试/研发",
                ),
                ExitCriteriaItem(
                    title="高风险场景已有明确测试结论",
                    required=True,
                    reason="确保上线风险可控",
                    owner="测试",
                ),
            ]

        if not self.environment_strategy:
            self.environment_strategy = [
                EnvironmentStrategyItem(
                    env_name="测试环境",
                    purpose="完成功能、异常流与联动验证",
                    required=True,
                    notes=[],
                )
            ]

        if not self.test_type_matrix:
            self.test_type_matrix = [
                TestTypeAdviceItem(
                    type_name="功能测试",
                    necessary=True,
                    priority="P0",
                    scope=["核心主流程"],
                    reason="作为基础验证类型必须覆盖",
                    automation_candidate=False,
                    related_risks=[],
                ),
                TestTypeAdviceItem(
                    type_name="接口测试",
                    necessary=True,
                    priority="P1",
                    scope=["关键规则与状态流转"],
                    reason="提高高风险逻辑验证效率",
                    automation_candidate=True,
                    related_risks=[],
                ),
                TestTypeAdviceItem(
                    type_name="冒烟测试",
                    necessary=True,
                    priority="P0",
                    scope=["发布前最低保障范围"],
                    reason="确保发布前关键能力可用",
                    automation_candidate=True,
                    related_risks=[],
                ),
                TestTypeAdviceItem(
                    type_name="回归测试",
                    necessary=True,
                    priority="P1",
                    scope=["受影响范围"],
                    reason="降低变更外溢风险",
                    automation_candidate=True,
                    related_risks=[],
                ),
            ]

        if not self.release_checklist:
            self.release_checklist = [
                ReleaseChecklistItem(
                    title="确认核心链路验证通过",
                    reason="避免关键功能带病上线",
                    required=True,
                    owner="测试",
                    related_risks=[],
                ),
                ReleaseChecklistItem(
                    title="确认高风险项已有测试结论",
                    reason="确保高风险问题不被遗漏",
                    required=True,
                    owner="测试",
                    related_risks=[],
                ),
            ]

        if not self.quality_gate:
            self.quality_gate = QualityGate(
                decision="conditional_pass",
                reasons=["默认门禁：需完成高风险项与核心主流程验证"],
                blocker_risks=[],
                required_actions=["完成高风险验证", "确认回归范围覆盖"],
            )

        # summary 自动收敛
        self.summary.business_domain = self.summary.business_domain or "通用"
        self.summary.core_reason = self.summary.core_reason or []
        self.summary.context_completeness = self.summary.context_completeness or {}

        # 兼容旧字段 raw_context_meta
        if not self.raw_context_meta:
            self.raw_context_meta = model_to_dict(self.context_meta)

        # metrics 自动刷新
        self.metrics = StrategyMetrics(
            impact_module_count=len(self.impact_modules or []),
            impact_flow_count=len(self.affected_flows or []),
            risk_count=len(self.risk_items or []),
            must_test_count=len(self.must_test or []),
            regression_scope_count=len(self.regression_scope or []),
            blocker_count=len(self.blockers or []),
            pending_confirmation_count=len(self.pending_confirmations or []),
        )

        return self


class StrategyPipelineResult(BaseModel):
    """
    pipeline 内部执行结果
    """
    ok: bool = Field(default=True, description="是否执行成功")
    result: StrategyResult = Field(default_factory=StrategyResult, description="策略结果")
    context_meta: StrategyContextMeta = Field(
        default_factory=StrategyContextMeta,
        description="上下文元信息",
    )
    message: str = Field(default="success", description="说明信息")
    stage_events: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="阶段事件",
    )
    metrics: Dict[str, Any] = Field(
        default_factory=dict,
        description="运行指标",
    )


# =====================================================
# 工具函数
# =====================================================

def model_to_dict(model: Any) -> Dict[str, Any]:
    """
    统一把 pydantic model 转成 dict
    兼容 pydantic v1 / v2
    """
    if model is None:
        return {}

    if isinstance(model, dict):
        return model

    model_dump = getattr(model, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump()
        except Exception:
            pass

    dict_fn = getattr(model, "dict", None)
    if callable(dict_fn):
        try:
            return dict_fn()
        except Exception:
            pass

    return {}


def safe_model_validate(model_cls: Any, data: Any, default: Any = None) -> Any:
    """
    安全构造 pydantic model
    兼容 pydantic v1 / v2
    """
    if data is None:
        return default if default is not None else model_cls()

    if isinstance(data, model_cls):
        return data

    model_validate = getattr(model_cls, "model_validate", None)
    if callable(model_validate):
        try:
            return model_validate(data)
        except Exception:
            pass

    parse_obj = getattr(model_cls, "parse_obj", None)
    if callable(parse_obj):
        try:
            return parse_obj(data)
        except Exception:
            pass

    return default if default is not None else model_cls()


def refresh_strategy_metrics(result: StrategyResult) -> StrategyResult:
    """
    根据当前结果自动刷新统计字段
    """
    if result is None:
        return StrategyResult()

    result.ensure_enterprise_defaults()

    result.metrics = StrategyMetrics(
        impact_module_count=len(result.impact_modules or []),
        impact_flow_count=len(result.affected_flows or []),
        risk_count=len(result.risk_items or []),
        must_test_count=len(result.must_test or []),
        regression_scope_count=len(result.regression_scope or []),
        blocker_count=len(result.blockers or []),
        pending_confirmation_count=len(result.pending_confirmations or []),
    )
    return result