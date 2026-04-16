# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional


try:
    from pydantic import BaseModel, Field, ConfigDict, model_validator
except Exception:  # pragma: no cover
    BaseModel = object  # type: ignore

    def Field(*args, **kwargs):  # type: ignore
        return None

    ConfigDict = dict  # type: ignore

    def model_validator(*args, **kwargs):  # type: ignore
        def deco(fn):
            return fn
        return deco


# =========================================================
# 基础类型定义
# =========================================================

PipelineStageType = Literal[
    "READ_REQUIREMENT",
    "ANALYZE_REQUIREMENT",
    "ANALYZE_TEST_POINTS",
    "DESIGN_TESTCASES",
    "REVIEW_TESTCASES",
    "REFINE_TESTCASES",
    "EXPORT_TESTCASES",
    "FINISHED",
]

StageStatusType = Literal["pending", "running", "completed", "error", "skipped"]

ScenarioType = Literal["normal", "exception", "boundary"]
TestPointType = ScenarioType

PriorityType = Literal["P0", "P1", "P2", "P3"]
TagType = Literal["功能测试", "边界测试", "异常测试", "UI测试", "接口测试", "冒烟测试"]
CaseStatusType = Literal["未开始", "执行中", "已执行", "已废弃"]

ReviewSeverityType = Literal["高", "中", "低"]
ReviewDecisionType = Literal["通过", "需优化", "驳回"]
ReviewIssueType = Literal[
    "覆盖缺失",
    "重复用例",
    "步骤不清",
    "预期空泛",
    "与需求不符",
    "字段缺失",
    "结构错误",
    "脏内容",
    "优先级不合理",
    "标题不规范",
    "前置条件不规范",
]


# =========================================================
# 基础工具
# =========================================================

def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _clean_str_list(values: Optional[List[Any]]) -> List[str]:
    if not values:
        return []
    result: List[str] = []
    seen = set()
    for item in values:
        text = _safe_text(item)
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _join_lines(values: Optional[List[Any]]) -> str:
    return "\n".join(_clean_str_list(values))


def _join_numbered_lines(values: Optional[List[Any]]) -> str:
    cleaned = _clean_str_list(values)
    result: List[str] = []
    for index, item in enumerate(cleaned, start=1):
        if item.startswith(f"{index}.") or item.startswith(f"{index}、"):
            result.append(item)
        else:
            result.append(f"{index}. {item}")
    return "\n".join(result)


def _is_pydantic_v2() -> bool:
    return hasattr(BaseModel, "model_validate")


class CompatBaseModel(BaseModel):
    """
    兼容 pydantic v1 / v2 的基础模型
    """

    if _is_pydantic_v2():
        model_config = ConfigDict(extra="ignore", populate_by_name=True)  # type: ignore

    def to_dict(self) -> Dict[str, Any]:
        if hasattr(self, "model_dump"):
            return self.model_dump(exclude_none=True)  # type: ignore[attr-defined]
        return self.dict(exclude_none=True)  # type: ignore[attr-defined]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        if hasattr(cls, "model_validate"):
            return cls.model_validate(data)  # type: ignore[attr-defined]
        return cls.parse_obj(data)  # type: ignore[attr-defined]


# =========================================================
# 阶段与事件模型
# =========================================================

class StageSnapshot(CompatBaseModel):
    """
    阶段快照：
    - 对外建议统一用 key/title/status/progress/summary
    - 为兼容旧代码，同时保留 stage/message/ended_at/extra
    """

    key: PipelineStageType = Field(..., description="阶段编码")
    title: str = Field(default="", description="阶段标题")
    status: StageStatusType = Field(default="pending", description="阶段状态")
    summary: str = Field(default="", description="阶段摘要")
    progress: int = Field(default=0, description="阶段进度 0-100")

    started_at: Optional[int] = Field(default=None, description="开始时间戳(ms)")
    finished_at: Optional[int] = Field(default=None, description="结束时间戳(ms)")
    duration_ms: int = Field(default=0, description="阶段耗时")
    extra: Dict[str, Any] = Field(default_factory=dict, description="扩展字段")

    # 兼容旧字段名
    stage: Optional[PipelineStageType] = Field(default=None, description="旧字段：阶段编码")
    message: str = Field(default="", description="旧字段：阶段消息")
    ended_at: Optional[int] = Field(default=None, description="旧字段：结束时间戳(ms)")

    @model_validator(mode="before")
    @classmethod
    def _fill_compat_fields(cls, data: Any):
        if not isinstance(data, dict):
            return data

        if not data.get("key") and data.get("stage"):
            data["key"] = data["stage"]
        if not data.get("stage") and data.get("key"):
            data["stage"] = data["key"]

        if not data.get("summary") and data.get("message"):
            data["summary"] = data["message"]
        if not data.get("message") and data.get("summary"):
            data["message"] = data["summary"]

        if data.get("finished_at") is None and data.get("ended_at") is not None:
            data["finished_at"] = data["ended_at"]
        if data.get("ended_at") is None and data.get("finished_at") is not None:
            data["ended_at"] = data["finished_at"]

        return data

    def normalize(self) -> "StageSnapshot":
        self.key = self.stage or self.key
        self.stage = self.key
        self.title = _safe_text(self.title)
        self.summary = _safe_text(self.summary or self.message)
        self.message = self.summary
        self.progress = max(0, min(100, _safe_int(self.progress, 0)))
        self.duration_ms = max(0, _safe_int(self.duration_ms, 0))
        if self.finished_at is None and self.ended_at is not None:
            self.finished_at = self.ended_at
        if self.ended_at is None and self.finished_at is not None:
            self.ended_at = self.finished_at
        return self


class StageMetric(CompatBaseModel):
    stage: PipelineStageType = Field(..., description="阶段")
    duration_ms: int = Field(default=0, description="阶段耗时")
    input_count: int = Field(default=0, description="输入数量")
    output_count: int = Field(default=0, description="输出数量")
    extra: Dict[str, Any] = Field(default_factory=dict, description="扩展字段")

    def normalize(self) -> "StageMetric":
        self.duration_ms = max(0, _safe_int(self.duration_ms))
        self.input_count = max(0, _safe_int(self.input_count))
        self.output_count = max(0, _safe_int(self.output_count))
        return self


class StageContent(CompatBaseModel):
    """
    当前阶段内容摘要，前端可以直接用于“阶段内容区”
    """

    stage: PipelineStageType = Field(..., description="所属阶段")
    title: str = Field(default="", description="内容标题")
    module: str = Field(default="", description="所属模块")
    content: Dict[str, Any] = Field(default_factory=dict, description="结构化内容")
    preview_lines: List[str] = Field(default_factory=list, description="预览文案")
    updated_at: int = Field(default=0, description="更新时间戳(ms)")


class PipelineRuntimeSnapshot(CompatBaseModel):
    """
    运行态唯一真相来源：
    顶部状态 / 阶段流 / 最终总结，后续都应尽量用这一套
    """

    current_stage: PipelineStageType = Field(default="READ_REQUIREMENT", description="当前阶段")
    stages: List[StageSnapshot] = Field(default_factory=list, description="所有阶段快照")
    totals: Dict[str, Any] = Field(default_factory=dict, description="全局统计")
    artifacts: Dict[str, Any] = Field(default_factory=dict, description="导出产物")
    current_content: Optional[StageContent] = Field(default=None, description="当前阶段内容")
    final_message: str = Field(default="", description="最终状态文案")


class SSEEvent(CompatBaseModel):
    type: str = Field(..., description="事件类型")
    stage: Optional[PipelineStageType] = Field(default=None, description="所属阶段")
    data: Optional[Dict[str, Any]] = Field(default=None, description="事件数据")
    message: str = Field(default="", description="事件消息")
    seq: int = Field(default=0, description="序号")
    ts: int = Field(default=0, description="时间戳(ms)")


# =========================================================
# 需求预处理 / 需求分析模型
# =========================================================

class RequirementPage(CompatBaseModel):
    page_no: int = Field(default=0, description="页码，从1开始")
    text: str = Field(default="", description="该页文本")
    text_length: int = Field(default=0, description="文本长度")
    source: str = Field(default="", description="text/ocr/mixed")
    image_like: bool = Field(default=False, description="是否为图片型页面")


class PreparedRequirementSummary(CompatBaseModel):
    requirement_id: str = Field(default="", description="需求ID")
    title: str = Field(default="", description="需求标题")
    source_file_name: str = Field(default="", description="来源文件名")
    final_text: str = Field(default="", description="最终供智能体使用的正文")
    clean_blocks: List[str] = Field(default_factory=list, description="清洗后的文本块")
    pages: List[RequirementPage] = Field(default_factory=list, description="页信息")
    total_pages: int = Field(default=0, description="总页数")
    usable_for_ai: bool = Field(default=True, description="是否可用于AI处理")


class RequirementAnalysisModule(CompatBaseModel):
    module: str = Field(default="", description="模块名称")
    summary: str = Field(default="", description="模块摘要")
    rules: List[str] = Field(default_factory=list, description="业务规则")
    constraints: List[str] = Field(default_factory=list, description="约束")
    risks: List[str] = Field(default_factory=list, description="风险点")


class RequirementAnalysisResult(CompatBaseModel):
    requirement_id: str = Field(default="", description="需求ID")
    summary: str = Field(default="", description="需求分析摘要")
    business_goal: str = Field(default="", description="业务目标")
    modules: List[RequirementAnalysisModule] = Field(default_factory=list, description="业务模块分析")
    rules: List[str] = Field(default_factory=list, description="全局规则")
    constraints: List[str] = Field(default_factory=list, description="全局约束")
    risks: List[str] = Field(default_factory=list, description="全局风险")
    assumptions: List[str] = Field(default_factory=list, description="假设项")


# =========================================================
# 测试点模型
# =========================================================

class TestPoint(CompatBaseModel):
    """
    新架构下唯一合法的测试点模型
    """

    point_id: str = Field(default="", description="测试点ID，例如 TP_001")
    module: str = Field(default="", description="所属模块")
    scenario_type: ScenarioType = Field(default="normal", description="场景类型")
    point_type: TestPointType = Field(default="normal", description="兼容字段：测试点类型")
    title: str = Field(default="", description="测试点标题")
    objective: str = Field(default="", description="测试目标/验证内容")

    preconditions: List[str] = Field(default_factory=list, description="前置条件")
    inputs: List[str] = Field(default_factory=list, description="输入/操作条件")
    check_items: List[str] = Field(default_factory=list, description="检查项")
    expected_direction: List[str] = Field(default_factory=list, description="预期方向")
    expected_results: List[str] = Field(default_factory=list, description="兼容字段：期望验证方向")

    priority: PriorityType = Field(default="P1", description="优先级")
    priority_hint: PriorityType = Field(default="P1", description="优先级建议")
    tags: List[str] = Field(default_factory=list, description="标签")
    source_requirement_refs: List[str] = Field(default_factory=list, description="需求原文依据")
    requirement_evidence: List[str] = Field(default_factory=list, description="需求依据片段")
    notes: List[str] = Field(default_factory=list, description="补充说明")

    @model_validator(mode="before")
    @classmethod
    def _fill_test_point_compat_fields(cls, data: Any):
        if not isinstance(data, dict):
            return data

        if not data.get("scenario_type") and data.get("point_type"):
            data["scenario_type"] = data["point_type"]
        if not data.get("point_type") and data.get("scenario_type"):
            data["point_type"] = data["scenario_type"]

        if not data.get("expected_direction") and data.get("expected_results"):
            data["expected_direction"] = data["expected_results"]
        if not data.get("expected_results") and data.get("expected_direction"):
            data["expected_results"] = data["expected_direction"]

        if not data.get("requirement_evidence") and data.get("source_requirement_refs"):
            data["requirement_evidence"] = data["source_requirement_refs"]
        if not data.get("source_requirement_refs") and data.get("requirement_evidence"):
            data["source_requirement_refs"] = data["requirement_evidence"]

        if not data.get("priority_hint") and data.get("priority"):
            data["priority_hint"] = data["priority"]

        return data

    def normalize(self) -> "TestPoint":
        self.point_id = _safe_text(self.point_id)
        self.module = _safe_text(self.module)
        self.title = _safe_text(self.title)
        self.objective = _safe_text(self.objective)
        self.preconditions = _clean_str_list(self.preconditions)
        self.inputs = _clean_str_list(self.inputs)
        self.check_items = _clean_str_list(self.check_items)
        self.expected_direction = _clean_str_list(self.expected_direction or self.expected_results)
        self.expected_results = list(self.expected_direction)
        self.tags = _clean_str_list(self.tags)
        self.source_requirement_refs = _clean_str_list(self.source_requirement_refs or self.requirement_evidence)
        self.requirement_evidence = list(self.source_requirement_refs)
        self.notes = _clean_str_list(self.notes)
        self.point_type = self.scenario_type or self.point_type
        self.scenario_type = self.point_type
        self.priority_hint = self.priority or self.priority_hint
        return self


class TestPointModule(CompatBaseModel):
    module: str = Field(default="", description="模块名称")
    normal_points: List[TestPoint] = Field(default_factory=list, description="正常流程测试点")
    exception_points: List[TestPoint] = Field(default_factory=list, description="异常测试点")
    boundary_points: List[TestPoint] = Field(default_factory=list, description="边界测试点")

    @property
    def total(self) -> int:
        return len(self.normal_points) + len(self.exception_points) + len(self.boundary_points)

    def all_points(self) -> List[TestPoint]:
        return [*self.normal_points, *self.exception_points, *self.boundary_points]


class TestPointStatistics(CompatBaseModel):
    total_points: int = Field(default=0, description="总测试点数")
    total_modules: int = Field(default=0, description="总模块数")
    normal_count: int = Field(default=0, description="正常测试点数")
    exception_count: int = Field(default=0, description="异常测试点数")
    boundary_count: int = Field(default=0, description="边界测试点数")
    module_counts: Dict[str, int] = Field(default_factory=dict, description="模块对应测试点数")
    priority_counts: Dict[str, int] = Field(default_factory=dict, description="优先级统计")


class AnalysisResult(CompatBaseModel):
    """
    保留原类名，继续表示“测试点分析结果”，避免旧代码 import 失效
    """

    summary: str = Field(default="", description="分析摘要")
    requirement_id: str = Field(default="", description="需求ID")
    modules: List[TestPointModule] = Field(default_factory=list, description="按模块组织的测试点")
    statistics: TestPointStatistics = Field(default_factory=TestPointStatistics, description="统计信息")

    def all_points(self) -> List[TestPoint]:
        result: List[TestPoint] = []
        for module in self.modules:
            result.extend(module.all_points())
        return result


# =========================================================
# 测试用例模型
# =========================================================

class TestCase(CompatBaseModel):
    """
    新架构下唯一合法的测试用例模型
    对外展示字段、导出字段、内部处理字段统一到这一套
    """

    case_id: str = Field(default="", description="用例ID，例如 TC_001")
    point_id: str = Field(default="", description="来源测试点ID")
    module: str = Field(default="", description="所属模块")
    title: str = Field(default="", description="用例标题")

    preconditions: List[str] = Field(default_factory=list, description="前置条件")
    steps: List[str] = Field(default_factory=list, description="步骤")
    expected_results: List[str] = Field(default_factory=list, description="预期结果")

    priority: PriorityType = Field(default="P1", description="优先级")
    tag: TagType = Field(default="功能测试", description="测试标签")
    status: CaseStatusType = Field(default="未开始", description="用例状态")
    remarks: str = Field(default="", description="备注")

    case_type: str = Field(default="", description="用例类型，例如 happy_path/boundary/invalid")
    source_requirement_refs: List[str] = Field(default_factory=list, description="需求依据")
    source_point_title: str = Field(default="", description="来源测试点标题")
    automation_candidate: bool = Field(default=False, description="是否适合自动化")
    owner: str = Field(default="", description="责任人")

    quality_score: float = Field(default=0.0, description="质量分")
    review_issues: List[str] = Field(default_factory=list, description="审核问题摘要")
    extra: Dict[str, Any] = Field(default_factory=dict, description="扩展字段")

    def normalize(self) -> "TestCase":
        self.case_id = _safe_text(self.case_id)
        self.point_id = _safe_text(self.point_id)
        self.module = _safe_text(self.module)
        self.title = _safe_text(self.title)
        self.preconditions = _clean_str_list(self.preconditions)
        self.steps = _clean_str_list(self.steps)
        self.expected_results = _clean_str_list(self.expected_results)
        self.remarks = _safe_text(self.remarks)
        self.case_type = _safe_text(self.case_type)
        self.source_requirement_refs = _clean_str_list(self.source_requirement_refs)
        self.source_point_title = _safe_text(self.source_point_title)
        self.owner = _safe_text(self.owner)
        self.review_issues = _clean_str_list(self.review_issues)
        self.quality_score = _safe_float(self.quality_score, 0.0)
        return self

    # ========================
    # 导出 / 前端兼容字段
    # ========================
    @property
    def ID(self) -> str:
        return self.case_id

    @property
    def 用例名称(self) -> str:
        return self.title

    @property
    def 所属模块(self) -> str:
        return self.module

    @property
    def 前置条件(self) -> str:
        return _join_numbered_lines(self.preconditions)

    @property
    def 步骤描述(self) -> str:
        return _join_numbered_lines(self.steps)

    @property
    def 预期结果(self) -> str:
        return _join_numbered_lines(self.expected_results)

    @property
    def 标签(self) -> str:
        return self.tag

    @property
    def 用例等级(self) -> str:
        return self.priority

    @property
    def 用例状态(self) -> str:
        return self.status

    @property
    def 备注(self) -> str:
        return self.remarks

    def to_export_dict(self) -> Dict[str, Any]:
        return {
            "ID": self.ID,
            "用例名称": self.用例名称,
            "所属模块": self.所属模块,
            "前置条件": self.前置条件,
            "备注": self.备注,
            "步骤描述": self.步骤描述,
            "预期结果": self.预期结果,
            "编辑模式": "创建",
            "标签": self.标签,
            "用例等级": self.用例等级,
            "用例状态": self.用例状态,
            "test_point_id": self.point_id,
            "owner": self.owner,
            "quality_score": self.quality_score,
        }


class TestCaseModule(CompatBaseModel):
    module: str = Field(default="", description="模块名称")
    cases: List[TestCase] = Field(default_factory=list, description="该模块下的用例")

    @property
    def total(self) -> int:
        return len(self.cases)


class TestCaseStatistics(CompatBaseModel):
    total_cases: int = Field(default=0, description="总用例数")
    total_modules: int = Field(default=0, description="总模块数")
    module_counts: Dict[str, int] = Field(default_factory=dict, description="模块用例数")
    priority_counts: Dict[str, int] = Field(default_factory=dict, description="优先级统计")
    tag_counts: Dict[str, int] = Field(default_factory=dict, description="标签统计")
    automation_candidate_count: int = Field(default=0, description="可自动化候选数")
    average_quality_score: float = Field(default=0.0, description="平均质量分")


class DesignResult(CompatBaseModel):
    summary: str = Field(default="", description="设计摘要")
    modules: List[TestCaseModule] = Field(default_factory=list, description="按模块组织的测试用例")
    statistics: TestCaseStatistics = Field(default_factory=TestCaseStatistics, description="统计")

    def all_cases(self) -> List[TestCase]:
        result: List[TestCase] = []
        for module in self.modules:
            result.extend(module.cases)
        return result


class CoverageSummary(CompatBaseModel):
    total_points: int = Field(default=0, description="总测试点数")
    total_cases: int = Field(default=0, description="总用例数")
    covered_points: int = Field(default=0, description="已覆盖测试点数")
    uncovered_points: int = Field(default=0, description="未覆盖测试点数")
    coverage_rate: float = Field(default=0.0, description="覆盖率")
    uncovered_point_ids: List[str] = Field(default_factory=list, description="未生成用例的测试点ID")
    covered_point_ids: List[str] = Field(default_factory=list, description="已覆盖测试点ID")
    point_to_case_count: Dict[str, int] = Field(default_factory=dict, description="测试点对应生成用例数")
    module_case_per_point: Dict[str, float] = Field(default_factory=dict, description="模块平均每点用例数")


# =========================================================
# 审核与优化模型
# =========================================================

class ReviewIssue(CompatBaseModel):
    issue_id: str = Field(default="", description="问题ID")
    issue_type: ReviewIssueType = Field(default="字段缺失", description="问题类型")
    severity: ReviewSeverityType = Field(default="中", description="严重级别")
    module: str = Field(default="", description="所属模块")
    case_id: str = Field(default="", description="关联用例ID")
    point_id: str = Field(default="", description="关联测试点ID")
    title: str = Field(default="", description="问题标题")
    description: str = Field(default="", description="问题描述")
    suggestion: str = Field(default="", description="修复建议")


class ReviewCaseResult(CompatBaseModel):
    case_id: str = Field(default="", description="用例ID")
    point_id: str = Field(default="", description="测试点ID")
    module: str = Field(default="", description="模块")
    quality_score: float = Field(default=0.0, description="质量分")
    is_pass: bool = Field(default=True, description="是否通过")
    reject_reasons: List[str] = Field(default_factory=list, description="拒绝/问题原因")
    fix_instructions: List[str] = Field(default_factory=list, description="修复指令")


class ReviewResult(CompatBaseModel):
    summary: str = Field(default="", description="审核摘要")
    decision: ReviewDecisionType = Field(default="通过", description="审核结论")
    issues: List[ReviewIssue] = Field(default_factory=list, description="问题列表")
    case_results: List[ReviewCaseResult] = Field(default_factory=list, description="逐用例审核结果")
    coverage_gaps: List[str] = Field(default_factory=list, description="覆盖缺口")
    duplicated_case_ids: List[str] = Field(default_factory=list, description="重复用例ID")
    invalid_case_ids: List[str] = Field(default_factory=list, description="无效用例ID")
    score_summary: Dict[str, Any] = Field(default_factory=dict, description="评分摘要")

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def needs_refine(self) -> bool:
        return self.decision == "需优化"


class RefineResult(CompatBaseModel):
    summary: str = Field(default="", description="优化摘要")
    modules: List[TestCaseModule] = Field(default_factory=list, description="优化后的测试用例")
    statistics: TestCaseStatistics = Field(default_factory=TestCaseStatistics, description="统计")
    coverage_summary: CoverageSummary = Field(default_factory=CoverageSummary, description="覆盖情况")

    def all_cases(self) -> List[TestCase]:
        result: List[TestCase] = []
        for module in self.modules:
            result.extend(module.cases)
        return result


# =========================================================
# 导出与最终结果模型
# =========================================================

class ExportArtifact(CompatBaseModel):
    ready: bool = Field(default=False, description="是否已导出完成")
    file_id: str = Field(default="", description="文件ID")
    filename: str = Field(default="", description="文件名")
    excel_path: str = Field(default="", description="Excel路径")
    json_path: str = Field(default="", description="JSON路径")
    download_url: str = Field(default="", description="下载地址")
    error: str = Field(default="", description="导出错误")


class FinalSummary(CompatBaseModel):
    requirement_id: str = Field(default="", description="需求ID")
    total_points: int = Field(default=0, description="测试点总数")
    total_cases: int = Field(default=0, description="测试用例总数")
    draft_cases: int = Field(default=0, description="草稿用例数")
    review_issue_count: int = Field(default=0, description="审核问题数")

    covered_points: int = Field(default=0, description="已覆盖测试点数")
    uncovered_points: int = Field(default=0, description="未覆盖测试点数")
    coverage_rate: float = Field(default=0.0, description="覆盖率")

    total_duration_ms: int = Field(default=0, description="总耗时")
    stage_costs_ms: Dict[str, int] = Field(default_factory=dict, description="阶段耗时")


class PipelineResult(CompatBaseModel):
    """
    流水线最终唯一结果模型
    """

    requirement_id: str = Field(default="", description="需求ID")
    owner: str = Field(default="", description="责任人")

    prepared_requirement: Optional[PreparedRequirementSummary] = Field(
        default=None,
        description="需求预处理结果",
    )
    requirement_analysis_result: Optional[RequirementAnalysisResult] = Field(
        default=None,
        description="需求分析结果",
    )

    analysis_result: Optional[AnalysisResult] = Field(default=None, description="测试点分析结果")
    design_result: Optional[DesignResult] = Field(default=None, description="测试用例设计结果")
    review_result: Optional[ReviewResult] = Field(default=None, description="审核结果")
    refine_result: Optional[RefineResult] = Field(default=None, description="优化结果")

    stage_snapshots: List[StageSnapshot] = Field(default_factory=list, description="阶段快照")
    stage_metrics: List[StageMetric] = Field(default_factory=list, description="阶段指标")
    runtime_snapshot: Optional[PipelineRuntimeSnapshot] = Field(default=None, description="运行态汇总")

    artifact: ExportArtifact = Field(default_factory=ExportArtifact, description="导出产物")
    final_summary: FinalSummary = Field(default_factory=FinalSummary, description="最终汇总")

    def final_cases(self) -> List[TestCase]:
        if self.refine_result and self.refine_result.modules:
            return self.refine_result.all_cases()
        if self.design_result and self.design_result.modules:
            return self.design_result.all_cases()
        return []

    def final_case_modules(self) -> List[TestCaseModule]:
        if self.refine_result and self.refine_result.modules:
            return self.refine_result.modules
        if self.design_result and self.design_result.modules:
            return self.design_result.modules
        return []

    def draft_cases(self) -> List[TestCase]:
        if self.design_result and self.design_result.modules:
            return self.design_result.all_cases()
        return []

    def all_points(self) -> List[TestPoint]:
        if self.analysis_result:
            return self.analysis_result.all_points()
        return []


# =========================================================
# 统计构建函数
# =========================================================

def build_test_point_statistics(modules: List[TestPointModule]) -> TestPointStatistics:
    stats = TestPointStatistics()
    stats.total_modules = len(modules)

    for module in modules:
        all_points = module.all_points()
        stats.module_counts[module.module] = len(all_points)

        for point in all_points:
            point.normalize()
            stats.total_points += 1
            stats.priority_counts[point.priority_hint] = stats.priority_counts.get(point.priority_hint, 0) + 1

            if point.scenario_type == "normal":
                stats.normal_count += 1
            elif point.scenario_type == "exception":
                stats.exception_count += 1
            elif point.scenario_type == "boundary":
                stats.boundary_count += 1

    return stats


def build_test_case_statistics(modules: List[TestCaseModule]) -> TestCaseStatistics:
    stats = TestCaseStatistics()
    stats.total_modules = len(modules)

    score_total = 0.0
    score_count = 0

    for module in modules:
        stats.module_counts[module.module] = len(module.cases)

        for case in module.cases:
            case.normalize()
            stats.total_cases += 1
            stats.priority_counts[case.priority] = stats.priority_counts.get(case.priority, 0) + 1
            stats.tag_counts[case.tag] = stats.tag_counts.get(case.tag, 0) + 1
            if case.automation_candidate:
                stats.automation_candidate_count += 1
            if case.quality_score > 0:
                score_total += case.quality_score
                score_count += 1

    if score_count > 0:
        stats.average_quality_score = round(score_total / score_count, 2)

    return stats


def build_coverage_summary(
    test_points: List[TestPoint],
    test_cases: List[TestCase],
) -> CoverageSummary:
    point_to_case_count: Dict[str, int] = {}
    module_point_count: Dict[str, int] = {}
    module_case_count: Dict[str, int] = {}

    for point in test_points:
        point.normalize()
        point_id = _safe_text(point.point_id)
        if point_id:
            point_to_case_count.setdefault(point_id, 0)
        module_name = _safe_text(point.module)
        if module_name:
            module_point_count[module_name] = module_point_count.get(module_name, 0) + 1

    for case in test_cases:
        case.normalize()
        point_id = _safe_text(case.point_id)
        if point_id:
            point_to_case_count[point_id] = point_to_case_count.get(point_id, 0) + 1
        module_name = _safe_text(case.module)
        if module_name:
            module_case_count[module_name] = module_case_count.get(module_name, 0) + 1

    uncovered_point_ids = [pid for pid, count in point_to_case_count.items() if count <= 0]
    covered_point_ids = [pid for pid, count in point_to_case_count.items() if count > 0]

    module_case_per_point: Dict[str, float] = {}
    for module_name, point_count in module_point_count.items():
        case_count = module_case_count.get(module_name, 0)
        module_case_per_point[module_name] = round(case_count / point_count, 2) if point_count > 0 else 0.0

    total_points = len(test_points)
    covered_points = len(covered_point_ids)
    uncovered_points = len(uncovered_point_ids)
    coverage_rate = round(covered_points / total_points, 4) if total_points > 0 else 0.0

    return CoverageSummary(
        total_points=total_points,
        total_cases=len(test_cases),
        covered_points=covered_points,
        uncovered_points=uncovered_points,
        coverage_rate=coverage_rate,
        uncovered_point_ids=uncovered_point_ids,
        covered_point_ids=covered_point_ids,
        point_to_case_count=point_to_case_count,
        module_case_per_point=module_case_per_point,
    )


# =========================================================
# 分组与展平辅助函数
# =========================================================

def flatten_test_point_modules(modules: List[TestPointModule]) -> List[TestPoint]:
    result: List[TestPoint] = []
    for module in modules:
        result.extend(module.all_points())
    return result


def flatten_test_case_modules(modules: List[TestCaseModule]) -> List[TestCase]:
    result: List[TestCase] = []
    for module in modules:
        result.extend(module.cases)
    return result


def group_cases_by_module(cases: List[TestCase]) -> List[TestCaseModule]:
    bucket: Dict[str, List[TestCase]] = {}
    for case in cases:
        case.normalize()
        module_name = _safe_text(case.module) or "未分组模块"
        bucket.setdefault(module_name, []).append(case)

    result: List[TestCaseModule] = []
    for module_name, module_cases in bucket.items():
        result.append(TestCaseModule(module=module_name, cases=module_cases))
    return result


def group_points_by_module(points: List[TestPoint]) -> List[TestPointModule]:
    bucket: Dict[str, Dict[str, List[TestPoint]]] = {}

    for point in points:
        point.normalize()
        module_name = _safe_text(point.module) or "未分组模块"
        if module_name not in bucket:
            bucket[module_name] = {
                "normal": [],
                "exception": [],
                "boundary": [],
            }
        bucket[module_name][point.scenario_type].append(point)

    result: List[TestPointModule] = []
    for module_name, grouped in bucket.items():
        result.append(
            TestPointModule(
                module=module_name,
                normal_points=grouped["normal"],
                exception_points=grouped["exception"],
                boundary_points=grouped["boundary"],
            )
        )
    return result


# =========================================================
# Runtime 构建函数
# =========================================================

def build_runtime_snapshot(result: PipelineResult) -> PipelineRuntimeSnapshot:
    stages = [snapshot.normalize() for snapshot in result.stage_snapshots]

    final_cases = result.final_cases()
    draft_cases = result.draft_cases()
    final_points = result.all_points()

    coverage_summary = build_coverage_summary(final_points, final_cases)

    totals = {
        "test_points_total": len(final_points),
        "draft_testcases_total": len(draft_cases),
        "final_testcases_total": len(final_cases),
        "review_issues_total": result.review_result.issue_count if result.review_result else 0,
        "covered_points": coverage_summary.covered_points,
        "uncovered_points": coverage_summary.uncovered_points,
        "coverage_rate": coverage_summary.coverage_rate,
        "modules_total": len({p.module for p in final_points if _safe_text(p.module)}),
        "stage_durations": {s.key: s.duration_ms for s in stages},
        "total_duration_ms": sum(max(0, s.duration_ms) for s in stages),
    }

    artifacts = {
        "ready": result.artifact.ready,
        "filename": result.artifact.filename,
        "download_url": result.artifact.download_url,
        "excel_path": result.artifact.excel_path,
        "json_path": result.artifact.json_path,
    }

    current_stage: PipelineStageType = "READ_REQUIREMENT"
    for snapshot in stages:
        if snapshot.status == "running":
            current_stage = snapshot.key
            break
        if snapshot.status in ("completed", "skipped"):
            current_stage = snapshot.key

    final_message = "流程已完成" if result.artifact.ready or current_stage == "FINISHED" else ""

    return PipelineRuntimeSnapshot(
        current_stage=current_stage,
        stages=stages,
        totals=totals,
        artifacts=artifacts,
        current_content=None,
        final_message=final_message,
    )


# =========================================================
# 最终一致性收敛函数
# =========================================================

def ensure_pipeline_result_consistency(result: PipelineResult) -> PipelineResult:
    if result.analysis_result:
        result.analysis_result.statistics = build_test_point_statistics(result.analysis_result.modules)

    if result.design_result:
        result.design_result.statistics = build_test_case_statistics(result.design_result.modules)

    if result.refine_result:
        result.refine_result.statistics = build_test_case_statistics(result.refine_result.modules)

    final_cases = result.final_cases()
    draft_cases = result.draft_cases()
    final_points = result.all_points()
    coverage_summary = build_coverage_summary(final_points, final_cases)

    if result.refine_result:
        result.refine_result.coverage_summary = coverage_summary

    stage_costs_ms: Dict[str, int] = {}
    total_duration_ms = 0
    for snapshot in result.stage_snapshots:
        snapshot.normalize()
        stage_costs_ms[snapshot.key] = snapshot.duration_ms
        total_duration_ms += snapshot.duration_ms

    result.final_summary = FinalSummary(
        requirement_id=result.requirement_id,
        total_points=len(final_points),
        total_cases=len(final_cases),
        draft_cases=len(draft_cases),
        review_issue_count=result.review_result.issue_count if result.review_result else 0,
        covered_points=coverage_summary.covered_points,
        uncovered_points=coverage_summary.uncovered_points,
        coverage_rate=coverage_summary.coverage_rate,
        total_duration_ms=total_duration_ms,
        stage_costs_ms=stage_costs_ms,
    )

    result.runtime_snapshot = build_runtime_snapshot(result)
    return result