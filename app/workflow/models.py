#! /usr/bin/python3
# coding=utf-8
# app/workflow/models.py

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Literal
from enum import Enum
from pydantic import BaseModel, Field


# =====================================================
# ✅ Workflow 阶段枚举（全系统唯一权威）
# =====================================================
class WorkflowStage(str, Enum):
    IDLE = "idle"
    FILE_READY = "file_ready"

    # ===== A 分支 =====
    REQ_ANALYZING = "req_analyzing"
    REQ_ANALYSIS_DONE = "req_analysis_done"

    # ===== B 分支 =====
    CASE_ANALYZING = "case_analyzing"
    CASE_DESIGNING = "case_designing"
    CASE_REVIEWING = "case_reviewing"
    CASE_REFINING = "case_refining"
    CASE_EXPORTING = "case_exporting"

    # ===== 终态 =====
    DONE = "done"
    ERROR = "error"


# =====================================================
# 🧠 分支 A：需求质量问题模型
# =====================================================
class RequirementIssue(BaseModel):
    type: Literal[
        "incomplete",
        "ambiguous",
        "risk",
        "inconsistent",
        "suggestion",
    ]
    description: str
    impact: Optional[str] = None
    suggestion: Optional[str] = None
    source_ref: Optional[str] = None


class RequirementQualityReport(BaseModel):
    score: int = Field(..., ge=0, le=100)
    level: Literal["A", "B", "C", "D", "E", "F"]
    summary: str
    issues: List[RequirementIssue] = Field(default_factory=list)


# =====================================================
# 🧩 测试点说明书（Analysis Agent 输出 · 结构化）
# =====================================================
class TestPoint(BaseModel):
    id: str
    title: str
    category: Literal["normal", "abnormal", "boundary"]
    description: str
    source_ref: Optional[str] = None
    implicit: bool = False


class TestPointGroup(BaseModel):
    module: str
    group: str
    test_points: List[TestPoint]


class TestPointSpec(BaseModel):
    """
    A 分支产物：
    - 保留结构
    - 不直接用于生成用例
    """
    modules: List[TestPointGroup]
    total_points: int


# =====================================================
# 🧪 测试用例草稿（Design Agent 输出）
# =====================================================
class TestCaseDraft(BaseModel):
    case_id: str
    test_point_id: str
    title: str
    preconditions: List[str]
    steps: List[str]
    expected_results: List[str]
    priority: Literal["P0", "P1", "P2", "P3"]
    tags: List[str]


# =====================================================
# 🔍 Review Agent 输出
# =====================================================
class ReviewFinding(BaseModel):
    case_id: str
    problem: str
    type: Literal[
        "logic_error",
        "incomplete",
        "ambiguous",
        "not_executable",
        "redundant",
    ]
    suggestion: str
    severity: Literal["low", "medium", "high"]


class ReviewReport(BaseModel):
    summary: str
    findings: List[ReviewFinding] = Field(default_factory=list)


# =====================================================
# ✅ 最终测试用例（可导出）
# =====================================================
class FinalTestCase(BaseModel):
    case_id: str
    module: str
    title: str
    preconditions: str
    steps: str
    expected: str
    priority: str
    tags: str
    test_point_id: Optional[str] = None
    source_ref: Optional[str] = None


# =====================================================
# 🧠 C 分支：测试策略智能体模型
# =====================================================
class StrategyRiskItem(BaseModel):
    title: str
    level: Literal["P0", "P1", "P2", "P3"] = "P2"
    category: Optional[str] = None
    reason: str = ""
    impact: Optional[str] = None
    suggestion: Optional[str] = None
    related_modules: List[str] = Field(default_factory=list)
    related_flows: List[str] = Field(default_factory=list)


class StrategyScopeItem(BaseModel):
    title: str
    reason: Optional[str] = None
    priority: Optional[str] = None
    related_modules: List[str] = Field(default_factory=list)
    related_flows: List[str] = Field(default_factory=list)


class StrategyLayerAdviceItem(BaseModel):
    title: str
    reason: Optional[str] = None
    related_scope: List[str] = Field(default_factory=list)
    related_risks: List[str] = Field(default_factory=list)


class StrategyResourcePlanItem(BaseModel):
    title: str
    scope: List[str] = Field(default_factory=list)
    focus: List[str] = Field(default_factory=list)
    note: Optional[str] = None


class StrategyBlockerItem(BaseModel):
    title: str
    reason: str = ""
    owner: Optional[str] = None
    suggestion: Optional[str] = None


class StrategyPendingConfirmationItem(BaseModel):
    title: str
    reason: str = ""
    owner: Optional[str] = None
    impact: Optional[str] = None


class StrategyChecklistItem(BaseModel):
    title: str
    reason: Optional[str] = None
    required: bool = True


class StrategyResult(BaseModel):
    workflow_id: Optional[str] = None
    requirement_id: Optional[str] = None

    summary: Dict[str, Any] = Field(default_factory=dict)

    impact_modules: List[Dict[str, Any]] = Field(default_factory=list)
    impact_roles: List[Dict[str, Any]] = Field(default_factory=list)
    affected_flows: List[Dict[str, Any]] = Field(default_factory=list)

    risk_items: List[StrategyRiskItem] = Field(default_factory=list)

    must_test: List[StrategyScopeItem] = Field(default_factory=list)
    should_test: List[StrategyScopeItem] = Field(default_factory=list)
    defer_test: List[StrategyScopeItem] = Field(default_factory=list)
    smoke_scope: List[StrategyScopeItem] = Field(default_factory=list)
    regression_scope: List[StrategyScopeItem] = Field(default_factory=list)

    test_layer_advice: Dict[str, List[StrategyLayerAdviceItem]] = Field(
        default_factory=lambda: {
            "ui": [],
            "api": [],
            "manual": [],
            "automation_candidate": [],
        }
    )

    resource_plan: Dict[str, List[StrategyResourcePlanItem]] = Field(
        default_factory=lambda: {
            "one_day": [],
            "two_days": [],
            "three_days": [],
        }
    )

    execution_order: List[Dict[str, Any]] = Field(default_factory=list)
    blockers: List[StrategyBlockerItem] = Field(default_factory=list)
    pending_confirmations: List[StrategyPendingConfirmationItem] = Field(default_factory=list)
    release_checklist: List[StrategyChecklistItem] = Field(default_factory=list)

    assumptions: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    raw_context_meta: Dict[str, Any] = Field(default_factory=dict)


# =====================================================
# 🧠 WorkflowTask（内存态唯一权威）
# =====================================================
@dataclass
class WorkflowTask:
    workflow_id: str

    # ===== 状态 =====
    stage: WorkflowStage = WorkflowStage.IDLE
    progress: int = 0
    message: Optional[str] = None

    # ===== PDF / Requirement =====
    pdf_path: Optional[str] = None
    pdf_text: Optional[str] = None
    requirement_text: Optional[str] = None

    # ===== A 分支：分析态 =====
    requirement_quality: Optional[RequirementQualityReport] = None
    test_point_spec: Optional[TestPointSpec] = None

    # =================================================
    # ✅ B 分支：执行态（非常重要）
    # =================================================

    # flatten 后、经硬约束裁剪的测试点（Orchestrator / SSE 使用）
    test_points: List[Dict[str, Any]] = field(default_factory=list)

    # 正在生成 / 已生成的测试用例（Review / Export 使用）
    test_cases: List[Dict[str, Any]] = field(default_factory=list)

    # ===== Review / Final =====
    test_case_drafts: Optional[List[TestCaseDraft]] = None
    review_report: Optional[ReviewReport] = None
    final_cases: Optional[List[FinalTestCase]] = None

    # 为了兼容 strategy controller / pipeline 的复用读取
    analysis_result: Optional[Dict[str, Any]] = None
    testcase_result: Optional[Dict[str, Any]] = None

    # ===== C 分支：测试策略智能体 =====
    strategy_status: str = "idle"
    strategy_stream_id: Optional[str] = None
    strategy_result: Optional[StrategyResult | Dict[str, Any]] = None
    strategy_error: Optional[str] = None
    strategy_updated_at: Optional[datetime] = None

    # ===== 产物 =====
    excel_path: Optional[str] = None
    total_cases: Optional[int] = None

    # ===== 输入约束 =====
    requirement_id: Optional[str] = None
    focus_requirements: Optional[str] = None

    # ===== 控制 =====
    generation_started: bool = False

    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    # =================================================
    def is_running(self) -> bool:
        return self.stage not in (WorkflowStage.DONE, WorkflowStage.ERROR)

    def is_done(self) -> bool:
        return self.stage == WorkflowStage.DONE

    def is_error(self) -> bool:
        return self.stage == WorkflowStage.ERROR

    def to_progress(self) -> "WorkflowProgress":
        return WorkflowProgress(
            stage=self.stage.value,
            progress=self.progress,
            message=self.message,
        )


# =====================================================
# 请求模型
# =====================================================
class GenerateRequest(BaseModel):
    workflow_id: Optional[str] = None
    requirement_id: Optional[str] = None
    focus_requirements: Optional[str] = None


class AnalyzeRequest(BaseModel):
    workflow_id: Optional[str] = None
    requirement_id: Optional[str] = None


class StrategyRunRequest(BaseModel):
    workflow_id: Optional[str] = None
    requirement_id: Optional[str] = None
    force_refresh: bool = False
    use_analysis_result: bool = True
    use_testcase_result: bool = True


# =====================================================
# 前端唯一可信 Progress
# =====================================================
class WorkflowProgress(BaseModel):
    stage: str
    progress: int
    message: Optional[str] = None