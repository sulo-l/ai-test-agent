#! /usr/bin/python3
# coding=utf-8
# app/testcase_app/protocols.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Callable, Dict, List, Literal, Optional, Protocol, Union, runtime_checkable

from app.testcase_app.constants import (
    EVENT_ANALYSIS_RESULT,
    EVENT_DESIGN_RESULT,
    EVENT_DOWNLOAD,
    EVENT_ERROR,
    EVENT_FINAL_RESULT,
    EVENT_FINAL_SUMMARY,
    EVENT_HEARTBEAT,
    EVENT_LOG,
    EVENT_METRIC,
    EVENT_PROGRESS,
    EVENT_REFINE_RESULT,
    EVENT_REVIEW_RESULT,
    EVENT_RUNTIME_SNAPSHOT,
    EVENT_STAGE,
    EVENT_STAGE_CONTENT,
    EVENT_STAGE_EVENT,
    EVENT_STAGE_METRIC,
    EVENT_STAGE_SNAPSHOT,
    FINAL_STAGE_PROGRESS,
    PIPELINE_STAGES,
    PIPELINE_UI_ORDER,
    STAGE_STATUSES,
    STAGE_STATUS_COMPLETED,
    STAGE_STATUS_ERROR,
    STAGE_STATUS_PENDING,
    STAGE_STATUS_RUNNING,
    STAGE_SUBTITLES,
    STAGE_TITLES,
    TERMINAL_EVENT_TYPES,
    TERMINAL_STAGE_NAMES,
)
from app.testcase_app.models import (
    AnalysisResult,
    CompatBaseModel,
    CoverageSummary,
    DesignResult,
    ExportArtifact,
    FinalSummary,
    PipelineResult,
    PipelineRuntimeSnapshot,
    PipelineStageType,
    PreparedRequirementSummary,
    RefineResult,
    RequirementAnalysisResult,
    ReviewResult,
    SSEEvent,
    StageContent,
    StageMetric,
    StageSnapshot,
    TestCase,
    TestCaseModule,
    TestCaseStatistics,
    TestPoint,
    TestPointModule,
    TestPointStatistics,
    build_coverage_summary,
    build_runtime_snapshot,
    build_test_case_statistics,
    build_test_point_statistics,
    ensure_pipeline_result_consistency,
)


# =========================================================
# 事件类型
# =========================================================

class EventType(str, Enum):
    CONNECTED = "connected"
    HEARTBEAT = EVENT_HEARTBEAT

    STAGE = EVENT_STAGE
    STAGE_EVENT = EVENT_STAGE_EVENT
    STAGE_SNAPSHOT = EVENT_STAGE_SNAPSHOT
    STAGE_CONTENT = EVENT_STAGE_CONTENT
    STAGE_METRIC = EVENT_STAGE_METRIC
    RUNTIME_SNAPSHOT = EVENT_RUNTIME_SNAPSHOT

    LOG = EVENT_LOG
    PROGRESS = EVENT_PROGRESS
    METRIC = EVENT_METRIC

    ANALYSIS_RESULT = EVENT_ANALYSIS_RESULT
    DESIGN_RESULT = EVENT_DESIGN_RESULT
    REVIEW_RESULT = EVENT_REVIEW_RESULT
    REFINE_RESULT = EVENT_REFINE_RESULT

    DOWNLOAD = EVENT_DOWNLOAD
    FINAL_RESULT = EVENT_FINAL_RESULT
    FINAL_SUMMARY = EVENT_FINAL_SUMMARY
    ERROR = EVENT_ERROR


# =========================================================
# pydantic 输出兼容
# =========================================================

def _model_to_dict(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=True)  # type: ignore[attr-defined]
    if hasattr(model, "dict"):
        return model.dict(exclude_none=True)  # type: ignore[attr-defined]
    if isinstance(model, dict):
        return model
    raise TypeError(f"Unsupported model type: {type(model)}")


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _safe_stage(stage: Union[PipelineStageType, str, None]) -> str:
    text = _safe_text(stage)
    return text or PIPELINE_STAGES[0]


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


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_status(status: str) -> str:
    status_value = _safe_text(status, STAGE_STATUS_PENDING)
    if status_value not in STAGE_STATUSES:
        return STAGE_STATUS_PENDING
    return status_value


# =========================================================
# 协议模型
# =========================================================

class BaseProtocolEvent(CompatBaseModel):
    type: str
    stage: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    message: str = ""
    seq: int = 0
    ts: int = 0


class StageEventData(CompatBaseModel):
    stage: str
    title: str
    subtitle: str = ""
    status: Literal["pending", "running", "completed", "error", "skipped"] = "pending"
    progress: int = 0
    duration_ms: int = 0
    started_at: Optional[int] = None
    ended_at: Optional[int] = None
    extra: Dict[str, Any] = {}


class ProgressEventData(CompatBaseModel):
    current: int = 0
    total: int = 0
    percent: int = 0
    stage: str = ""
    message: str = ""
    extra: Dict[str, Any] = {}


class MetricEventData(CompatBaseModel):
    stage: str
    duration_ms: int = 0
    input_count: int = 0
    output_count: int = 0
    extra: Dict[str, Any] = {}


class LogEventData(CompatBaseModel):
    level: Literal["INFO", "WARNING", "ERROR"] = "INFO"
    text: str = ""
    extra: Dict[str, Any] = {}


class ErrorEventData(CompatBaseModel):
    stage: str = ""
    error_type: str = ""
    message: str = ""
    detail: str = ""
    extra: Dict[str, Any] = {}


class DownloadEventData(CompatBaseModel):
    ready: bool = False
    file_id: str = ""
    filename: str = ""
    excel_path: str = ""
    json_path: str = ""
    download_url: str = ""
    error: str = ""


class AnalysisResultEventData(CompatBaseModel):
    summary: str = ""
    statistics: TestPointStatistics
    modules: List[TestPointModule]
    total_points: int = 0


class RequirementAnalysisResultEventData(CompatBaseModel):
    summary: str = ""
    module_count: int = 0
    rule_count: int = 0
    constraint_count: int = 0
    risk_count: int = 0
    raw: Dict[str, Any] = {}


class DesignResultEventData(CompatBaseModel):
    summary: str = ""
    statistics: TestCaseStatistics
    modules: List[TestCaseModule]
    total_cases: int = 0


class ReviewResultEventData(CompatBaseModel):
    summary: str = ""
    decision: str = ""
    issue_count: int = 0
    coverage_gaps: List[str] = []
    duplicated_case_ids: List[str] = []
    invalid_case_ids: List[str] = []
    issues: List[Dict[str, Any]] = []
    score_summary: Dict[str, Any] = {}


class RefineResultEventData(CompatBaseModel):
    summary: str = ""
    statistics: TestCaseStatistics
    coverage_summary: CoverageSummary
    modules: List[TestCaseModule]
    total_cases: int = 0


class FinalResultEventData(CompatBaseModel):
    requirement_id: str = ""
    owner: str = ""

    total_points: int = 0
    draft_cases: int = 0
    total_cases: int = 0
    review_issue_count: int = 0

    covered_points: int = 0
    uncovered_points: int = 0
    coverage_rate: float = 0.0

    total_duration_ms: int = 0
    stage_costs_ms: Dict[str, int] = {}

    artifact: ExportArtifact
    runtime_snapshot: Optional[Dict[str, Any]] = None

    requirement_analysis_result: Optional[Dict[str, Any]] = None
    analysis_result: Optional[Dict[str, Any]] = None
    design_result: Optional[Dict[str, Any]] = None
    review_result: Optional[Dict[str, Any]] = None
    refine_result: Optional[Dict[str, Any]] = None


class FinalSummaryEventData(CompatBaseModel):
    requirement_id: str = ""
    total_points: int = 0
    draft_cases: int = 0
    total_cases: int = 0
    review_issue_count: int = 0

    covered_points: int = 0
    uncovered_points: int = 0
    coverage_rate: float = 0.0

    total_duration_ms: int = 0
    stage_costs_ms: Dict[str, int] = {}
    artifact: Dict[str, Any] = {}
    coverage_summary: Dict[str, Any] = {}


# =========================================================
# Agent Protocol
# =========================================================

@runtime_checkable
class AnalysisAgentProtocol(Protocol):
    def run(
        self,
        *,
        prepared_requirement: PreparedRequirementSummary,
        emit: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> AnalysisResult:
        ...


@runtime_checkable
class DesignAgentProtocol(Protocol):
    def run(
        self,
        *,
        analysis_result: AnalysisResult,
        emit: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> DesignResult:
        ...


@runtime_checkable
class ReviewAgentProtocol(Protocol):
    def run(
        self,
        *,
        analysis_result: AnalysisResult,
        design_result: DesignResult,
        emit: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> ReviewResult:
        ...


@runtime_checkable
class RefineAgentProtocol(Protocol):
    def run(
        self,
        *,
        analysis_result: AnalysisResult,
        design_result: DesignResult,
        review_result: ReviewResult,
        emit: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> RefineResult:
        ...


# =========================================================
# 标准化辅助
# =========================================================

def make_stage_snapshot(
    *,
    stage: Union[PipelineStageType, str],
    status: Literal["pending", "running", "completed", "error", "skipped"],
    progress: int = 0,
    message: str = "",
    duration_ms: int = 0,
    started_at: Optional[int] = None,
    ended_at: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> StageSnapshot:
    stage_value = _safe_stage(stage)
    summary = _safe_text(message)
    return StageSnapshot(
        key=stage_value,  # type: ignore[arg-type]
        stage=stage_value,  # type: ignore[arg-type]
        title=STAGE_TITLES.get(stage_value, stage_value),
        status=_safe_status(status),  # type: ignore[arg-type]
        summary=summary,
        message=summary,
        progress=max(0, min(100, _safe_int(progress))),
        duration_ms=max(0, _safe_int(duration_ms)),
        started_at=started_at,
        finished_at=ended_at,
        ended_at=ended_at,
        extra=extra or {},
    ).normalize()


def make_stage_metric(
    *,
    stage: Union[PipelineStageType, str],
    duration_ms: int = 0,
    input_count: int = 0,
    output_count: int = 0,
    extra: Optional[Dict[str, Any]] = None,
) -> StageMetric:
    return StageMetric(
        stage=_safe_stage(stage),  # type: ignore[arg-type]
        duration_ms=max(0, _safe_int(duration_ms)),
        input_count=max(0, _safe_int(input_count)),
        output_count=max(0, _safe_int(output_count)),
        extra=extra or {},
    ).normalize()


def make_stage_content(
    *,
    stage: Union[PipelineStageType, str],
    title: str,
    module: str = "",
    content: Optional[Dict[str, Any]] = None,
    preview_lines: Optional[List[str]] = None,
    updated_at: Optional[int] = None,
) -> StageContent:
    return StageContent(
        stage=_safe_stage(stage),  # type: ignore[arg-type]
        title=_safe_text(title),
        module=_safe_text(module),
        content=content or {},
        preview_lines=[_safe_text(x) for x in (preview_lines or []) if _safe_text(x)],
        updated_at=updated_at or _now_ms(),
    )


# =========================================================
# 基础事件构造器
# =========================================================

def make_event(
    *,
    event_type: Union[EventType, str],
    stage: Optional[Union[PipelineStageType, str]] = None,
    data: Optional[Dict[str, Any]] = None,
    message: str = "",
    seq: int = 0,
    ts: Optional[int] = None,
) -> Dict[str, Any]:
    event_type_value = event_type.value if isinstance(event_type, EventType) else _safe_text(event_type)
    stage_value = _safe_stage(stage) if stage else None

    event = BaseProtocolEvent(
        type=event_type_value,
        stage=stage_value,
        data=data or {},
        message=_safe_text(message),
        seq=max(0, _safe_int(seq)),
        ts=ts or _now_ms(),
    )
    return _model_to_dict(event)


def make_heartbeat_event(seq: int = 0) -> Dict[str, Any]:
    return make_event(
        event_type=EventType.HEARTBEAT,
        data={"alive": True},
        message="heartbeat",
        seq=seq,
    )


def make_log_event(
    text: str,
    *,
    stage: Optional[Union[PipelineStageType, str]] = None,
    level: Literal["INFO", "WARNING", "ERROR"] = "INFO",
    extra: Optional[Dict[str, Any]] = None,
    seq: int = 0,
) -> Dict[str, Any]:
    payload = LogEventData(
        level=level,
        text=_safe_text(text),
        extra=extra or {},
    )
    return make_event(
        event_type=EventType.LOG,
        stage=stage,
        data=_model_to_dict(payload),
        message=_safe_text(text),
        seq=seq,
    )


def make_error_event(
    message: str,
    *,
    stage: Optional[Union[PipelineStageType, str]] = None,
    error_type: str = "PIPELINE_ERROR",
    detail: str = "",
    extra: Optional[Dict[str, Any]] = None,
    seq: int = 0,
) -> Dict[str, Any]:
    payload = ErrorEventData(
        stage=_safe_stage(stage) if stage else "",
        error_type=_safe_text(error_type),
        message=_safe_text(message),
        detail=_safe_text(detail),
        extra=extra or {},
    )
    return make_event(
        event_type=EventType.ERROR,
        stage=stage,
        data=_model_to_dict(payload),
        message=_safe_text(message),
        seq=seq,
    )


def make_progress_event(
    *,
    current: int,
    total: int,
    stage: Union[PipelineStageType, str],
    message: str = "",
    extra: Optional[Dict[str, Any]] = None,
    seq: int = 0,
) -> Dict[str, Any]:
    current_value = max(0, _safe_int(current))
    total_value = max(0, _safe_int(total))
    percent = int((current_value / total_value) * 100) if total_value > 0 else 0
    percent = max(0, min(100, percent))

    payload = ProgressEventData(
        current=current_value,
        total=total_value,
        percent=percent,
        stage=_safe_stage(stage),
        message=_safe_text(message),
        extra=extra or {},
    )
    return make_event(
        event_type=EventType.PROGRESS,
        stage=stage,
        data=_model_to_dict(payload),
        message=_safe_text(message),
        seq=seq,
    )


def make_stage_event(
    *,
    snapshot: StageSnapshot,
    seq: int = 0,
) -> Dict[str, Any]:
    snapshot = snapshot.normalize()
    payload = StageEventData(
        stage=snapshot.key,
        title=snapshot.title,
        subtitle=STAGE_SUBTITLES.get(snapshot.key, ""),
        status=snapshot.status,  # type: ignore[arg-type]
        progress=snapshot.progress,
        duration_ms=snapshot.duration_ms,
        started_at=snapshot.started_at,
        ended_at=snapshot.finished_at,
        extra=snapshot.extra,
    )
    return make_event(
        event_type=EventType.STAGE_EVENT,
        stage=snapshot.key,
        data=_model_to_dict(payload),
        message=snapshot.summary,
        seq=seq,
    )


def make_stage_snapshot_event(
    *,
    snapshot: StageSnapshot,
    seq: int = 0,
) -> Dict[str, Any]:
    snapshot = snapshot.normalize()
    return make_event(
        event_type=EventType.STAGE_SNAPSHOT,
        stage=snapshot.key,
        data=snapshot.to_dict(),
        message=snapshot.summary,
        seq=seq,
    )


def make_stage_content_event(
    *,
    content: StageContent,
    seq: int = 0,
) -> Dict[str, Any]:
    return make_event(
        event_type=EventType.STAGE_CONTENT,
        stage=content.stage,
        data=content.to_dict(),
        message=content.title,
        seq=seq,
    )


def make_metric_event(
    *,
    metric: StageMetric,
    seq: int = 0,
) -> Dict[str, Any]:
    metric = metric.normalize()
    payload = MetricEventData(
        stage=metric.stage,
        duration_ms=metric.duration_ms,
        input_count=metric.input_count,
        output_count=metric.output_count,
        extra=metric.extra,
    )
    return make_event(
        event_type=EventType.METRIC,
        stage=metric.stage,
        data=_model_to_dict(payload),
        message="metric",
        seq=seq,
    )


def make_stage_metric_event(
    *,
    metric: StageMetric,
    seq: int = 0,
) -> Dict[str, Any]:
    metric = metric.normalize()
    return make_event(
        event_type=EventType.STAGE_METRIC,
        stage=metric.stage,
        data=metric.to_dict(),
        message="stage metric",
        seq=seq,
    )


def make_runtime_snapshot_event(
    *,
    runtime: PipelineRuntimeSnapshot,
    seq: int = 0,
) -> Dict[str, Any]:
    return make_event(
        event_type=EventType.RUNTIME_SNAPSHOT,
        stage=runtime.current_stage,
        data=runtime.to_dict(),
        message=runtime.final_message or runtime.current_stage,
        seq=seq,
    )


# =========================================================
# 结果事件构造器
# =========================================================

def make_requirement_analysis_result_event(
    *,
    result: RequirementAnalysisResult,
    stage: Union[PipelineStageType, str] = "ANALYZE_REQUIREMENT",
    seq: int = 0,
) -> Dict[str, Any]:
    payload = RequirementAnalysisResultEventData(
        summary=result.summary,
        module_count=len(result.modules),
        rule_count=len(result.rules),
        constraint_count=len(result.constraints),
        risk_count=len(result.risks),
        raw=result.to_dict(),
    )
    return make_event(
        event_type=EventType.ANALYSIS_RESULT,
        stage=stage,
        data=_model_to_dict(payload),
        message=result.summary or "requirement analysis completed",
        seq=seq,
    )


def make_analysis_result_event(
    *,
    result: AnalysisResult,
    stage: Union[PipelineStageType, str] = "ANALYZE_TEST_POINTS",
    seq: int = 0,
) -> Dict[str, Any]:
    statistics = result.statistics or build_test_point_statistics(result.modules)
    payload = AnalysisResultEventData(
        summary=result.summary,
        statistics=statistics,
        modules=result.modules,
        total_points=statistics.total_points,
    )
    return make_event(
        event_type=EventType.ANALYSIS_RESULT,
        stage=stage,
        data=_model_to_dict(payload),
        message=result.summary or "analysis completed",
        seq=seq,
    )


def make_design_result_event(
    *,
    result: DesignResult,
    stage: Union[PipelineStageType, str] = "DESIGN_TESTCASES",
    seq: int = 0,
) -> Dict[str, Any]:
    statistics = result.statistics or build_test_case_statistics(result.modules)
    payload = DesignResultEventData(
        summary=result.summary,
        statistics=statistics,
        modules=result.modules,
        total_cases=statistics.total_cases,
    )
    return make_event(
        event_type=EventType.DESIGN_RESULT,
        stage=stage,
        data=_model_to_dict(payload),
        message=result.summary or "design completed",
        seq=seq,
    )


def make_review_result_event(
    *,
    result: ReviewResult,
    stage: Union[PipelineStageType, str] = "REVIEW_TESTCASES",
    seq: int = 0,
) -> Dict[str, Any]:
    payload = ReviewResultEventData(
        summary=result.summary,
        decision=result.decision,
        issue_count=result.issue_count,
        coverage_gaps=result.coverage_gaps,
        duplicated_case_ids=result.duplicated_case_ids,
        invalid_case_ids=result.invalid_case_ids,
        issues=[_model_to_dict(issue) for issue in result.issues],
        score_summary=result.score_summary or {},
    )
    return make_event(
        event_type=EventType.REVIEW_RESULT,
        stage=stage,
        data=_model_to_dict(payload),
        message=result.summary or "review completed",
        seq=seq,
    )


def make_refine_result_event(
    *,
    result: RefineResult,
    stage: Union[PipelineStageType, str] = "REFINE_TESTCASES",
    seq: int = 0,
) -> Dict[str, Any]:
    statistics = result.statistics or build_test_case_statistics(result.modules)
    coverage_summary = result.coverage_summary or CoverageSummary()
    payload = RefineResultEventData(
        summary=result.summary,
        statistics=statistics,
        coverage_summary=coverage_summary,
        modules=result.modules,
        total_cases=statistics.total_cases,
    )
    return make_event(
        event_type=EventType.REFINE_RESULT,
        stage=stage,
        data=_model_to_dict(payload),
        message=result.summary or "refine completed",
        seq=seq,
    )


def make_download_event(
    *,
    artifact: ExportArtifact,
    stage: Union[PipelineStageType, str] = "EXPORT_TESTCASES",
    seq: int = 0,
) -> Dict[str, Any]:
    payload = DownloadEventData(
        ready=artifact.ready,
        file_id=artifact.file_id,
        filename=artifact.filename,
        excel_path=artifact.excel_path,
        json_path=artifact.json_path,
        download_url=artifact.download_url,
        error=artifact.error,
    )
    return make_event(
        event_type=EventType.DOWNLOAD,
        stage=stage,
        data=_model_to_dict(payload),
        message="export completed" if artifact.ready else artifact.error,
        seq=seq,
    )


def make_final_summary_event(
    *,
    result: PipelineResult,
    seq: int = 0,
) -> Dict[str, Any]:
    final_result = ensure_pipeline_result_consistency(result)
    coverage_summary = build_coverage_summary(
        final_result.all_points(),
        final_result.final_cases(),
    )

    payload = FinalSummaryEventData(
        requirement_id=final_result.requirement_id,
        total_points=final_result.final_summary.total_points,
        draft_cases=final_result.final_summary.draft_cases,
        total_cases=final_result.final_summary.total_cases,
        review_issue_count=final_result.final_summary.review_issue_count,
        covered_points=coverage_summary.covered_points,
        uncovered_points=coverage_summary.uncovered_points,
        coverage_rate=coverage_summary.coverage_rate,
        total_duration_ms=final_result.final_summary.total_duration_ms,
        stage_costs_ms=final_result.final_summary.stage_costs_ms,
        artifact=final_result.artifact.to_dict(),
        coverage_summary=coverage_summary.to_dict(),
    )

    return make_event(
        event_type=EventType.FINAL_SUMMARY,
        stage="FINISHED",
        data=_model_to_dict(payload),
        message="pipeline summary",
        seq=seq,
    )


def make_final_result_event(
    *,
    result: PipelineResult,
    seq: int = 0,
) -> Dict[str, Any]:
    final_result = ensure_pipeline_result_consistency(result)
    coverage_summary = build_coverage_summary(
        final_result.all_points(),
        final_result.final_cases(),
    )
    runtime_snapshot = final_result.runtime_snapshot or build_runtime_snapshot(final_result)

    payload = FinalResultEventData(
        requirement_id=final_result.requirement_id,
        owner=final_result.owner,
        total_points=final_result.final_summary.total_points,
        draft_cases=final_result.final_summary.draft_cases,
        total_cases=final_result.final_summary.total_cases,
        review_issue_count=final_result.final_summary.review_issue_count,
        covered_points=coverage_summary.covered_points,
        uncovered_points=coverage_summary.uncovered_points,
        coverage_rate=coverage_summary.coverage_rate,
        total_duration_ms=final_result.final_summary.total_duration_ms,
        stage_costs_ms=final_result.final_summary.stage_costs_ms,
        artifact=final_result.artifact,
        runtime_snapshot=runtime_snapshot.to_dict(),
        requirement_analysis_result=_model_to_dict(final_result.requirement_analysis_result)
        if final_result.requirement_analysis_result else None,
        analysis_result=_model_to_dict(final_result.analysis_result)
        if final_result.analysis_result else None,
        design_result=_model_to_dict(final_result.design_result)
        if final_result.design_result else None,
        review_result=_model_to_dict(final_result.review_result)
        if final_result.review_result else None,
        refine_result=_model_to_dict(final_result.refine_result)
        if final_result.refine_result else None,
    )
    return make_event(
        event_type=EventType.FINAL_RESULT,
        stage="FINISHED",
        data=_model_to_dict(payload),
        message="pipeline finished",
        seq=seq,
    )


# =========================================================
# 管道内常用转换函数
# =========================================================

def build_stage_start_event(
    *,
    stage: Union[PipelineStageType, str],
    message: str = "",
    seq: int = 0,
    started_at: Optional[int] = None,
) -> Dict[str, Any]:
    snapshot = make_stage_snapshot(
        stage=stage,
        status="running",
        progress=0,
        message=message or STAGE_SUBTITLES.get(_safe_stage(stage), STAGE_TITLES.get(_safe_stage(stage), "")),
        started_at=started_at or _now_ms(),
    )
    return make_stage_event(snapshot=snapshot, seq=seq)


def build_stage_completed_event(
    *,
    stage: Union[PipelineStageType, str],
    message: str = "",
    progress: int = 100,
    duration_ms: int = 0,
    started_at: Optional[int] = None,
    ended_at: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
    seq: int = 0,
) -> Dict[str, Any]:
    snapshot = make_stage_snapshot(
        stage=stage,
        status="completed",
        progress=progress,
        message=message or f"{STAGE_TITLES.get(_safe_stage(stage), _safe_stage(stage))}完成",
        duration_ms=duration_ms,
        started_at=started_at,
        ended_at=ended_at or _now_ms(),
        extra=extra,
    )
    return make_stage_event(snapshot=snapshot, seq=seq)


def build_stage_error_event(
    *,
    stage: Union[PipelineStageType, str],
    message: str,
    duration_ms: int = 0,
    started_at: Optional[int] = None,
    ended_at: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
    seq: int = 0,
) -> Dict[str, Any]:
    snapshot = make_stage_snapshot(
        stage=stage,
        status="error",
        progress=0,
        message=message,
        duration_ms=duration_ms,
        started_at=started_at,
        ended_at=ended_at or _now_ms(),
        extra=extra,
    )
    return make_stage_event(snapshot=snapshot, seq=seq)


def build_stage_metric_event(
    *,
    stage: Union[PipelineStageType, str],
    duration_ms: int,
    input_count: int = 0,
    output_count: int = 0,
    extra: Optional[Dict[str, Any]] = None,
    seq: int = 0,
) -> Dict[str, Any]:
    metric = make_stage_metric(
        stage=stage,
        duration_ms=duration_ms,
        input_count=input_count,
        output_count=output_count,
        extra=extra,
    )
    return make_metric_event(metric=metric, seq=seq)


# =========================================================
# Final / 导出标准化辅助
# =========================================================

def normalize_pipeline_result_for_final_event(result: PipelineResult) -> PipelineResult:
    return ensure_pipeline_result_consistency(result)


def build_export_artifact(
    *,
    ready: bool,
    file_id: str = "",
    filename: str = "",
    excel_path: str = "",
    json_path: str = "",
    download_url: str = "",
    error: str = "",
) -> ExportArtifact:
    return ExportArtifact(
        ready=bool(ready),
        file_id=_safe_text(file_id),
        filename=_safe_text(filename),
        excel_path=_safe_text(excel_path),
        json_path=_safe_text(json_path),
        download_url=_safe_text(download_url),
        error=_safe_text(error),
    )


# =========================================================
# 终态判断
# =========================================================

def is_terminal_stage(stage: Union[PipelineStageType, str, None]) -> bool:
    if not stage:
        return False
    return _safe_stage(stage) in TERMINAL_STAGE_NAMES


def is_terminal_event(event: Dict[str, Any]) -> bool:
    if not isinstance(event, dict):
        return False
    event_type = _safe_text(event.get("type"))
    stage = _safe_text(event.get("stage"))
    return event_type in TERMINAL_EVENT_TYPES or stage in TERMINAL_STAGE_NAMES


# =========================================================
# 通用 emit 辅助
# =========================================================

def emit_if_possible(
    emit: Optional[Callable[[Dict[str, Any]], None]],
    event: Dict[str, Any],
) -> None:
    if emit is None:
        return
    emit(event)


def emit_stage_start(
    emit: Optional[Callable[[Dict[str, Any]], None]],
    *,
    stage: Union[PipelineStageType, str],
    message: str = "",
    seq: int = 0,
    started_at: Optional[int] = None,
) -> None:
    emit_if_possible(
        emit,
        build_stage_start_event(
            stage=stage,
            message=message,
            seq=seq,
            started_at=started_at,
        ),
    )


def emit_stage_completed(
    emit: Optional[Callable[[Dict[str, Any]], None]],
    *,
    stage: Union[PipelineStageType, str],
    message: str = "",
    progress: int = 100,
    duration_ms: int = 0,
    started_at: Optional[int] = None,
    ended_at: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
    seq: int = 0,
) -> None:
    emit_if_possible(
        emit,
        build_stage_completed_event(
            stage=stage,
            message=message,
            progress=progress,
            duration_ms=duration_ms,
            started_at=started_at,
            ended_at=ended_at,
            extra=extra,
            seq=seq,
        ),
    )


def emit_stage_error(
    emit: Optional[Callable[[Dict[str, Any]], None]],
    *,
    stage: Union[PipelineStageType, str],
    message: str,
    duration_ms: int = 0,
    started_at: Optional[int] = None,
    ended_at: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
    seq: int = 0,
) -> None:
    emit_if_possible(
        emit,
        build_stage_error_event(
            stage=stage,
            message=message,
            duration_ms=duration_ms,
            started_at=started_at,
            ended_at=ended_at,
            extra=extra,
            seq=seq,
        ),
    )


def emit_progress(
    emit: Optional[Callable[[Dict[str, Any]], None]],
    *,
    current: int,
    total: int,
    stage: Union[PipelineStageType, str],
    message: str = "",
    extra: Optional[Dict[str, Any]] = None,
    seq: int = 0,
) -> None:
    emit_if_possible(
        emit,
        make_progress_event(
            current=current,
            total=total,
            stage=stage,
            message=message,
            extra=extra,
            seq=seq,
        ),
    )


def emit_metric(
    emit: Optional[Callable[[Dict[str, Any]], None]],
    *,
    stage: Union[PipelineStageType, str],
    duration_ms: int,
    input_count: int = 0,
    output_count: int = 0,
    extra: Optional[Dict[str, Any]] = None,
    seq: int = 0,
) -> None:
    emit_if_possible(
        emit,
        build_stage_metric_event(
            stage=stage,
            duration_ms=duration_ms,
            input_count=input_count,
            output_count=output_count,
            extra=extra,
            seq=seq,
        ),
    )


def emit_analysis_result(
    emit: Optional[Callable[[Dict[str, Any]], None]],
    *,
    result: AnalysisResult,
    seq: int = 0,
) -> None:
    emit_if_possible(
        emit,
        make_analysis_result_event(result=result, seq=seq),
    )


def emit_design_result(
    emit: Optional[Callable[[Dict[str, Any]], None]],
    *,
    result: DesignResult,
    seq: int = 0,
) -> None:
    emit_if_possible(
        emit,
        make_design_result_event(result=result, seq=seq),
    )


def emit_review_result(
    emit: Optional[Callable[[Dict[str, Any]], None]],
    *,
    result: ReviewResult,
    seq: int = 0,
) -> None:
    emit_if_possible(
        emit,
        make_review_result_event(result=result, seq=seq),
    )


def emit_refine_result(
    emit: Optional[Callable[[Dict[str, Any]], None]],
    *,
    result: RefineResult,
    seq: int = 0,
) -> None:
    emit_if_possible(
        emit,
        make_refine_result_event(result=result, seq=seq),
    )


def emit_download(
    emit: Optional[Callable[[Dict[str, Any]], None]],
    *,
    artifact: ExportArtifact,
    seq: int = 0,
) -> None:
    emit_if_possible(
        emit,
        make_download_event(artifact=artifact, seq=seq),
    )


def emit_final_result(
    emit: Optional[Callable[[Dict[str, Any]], None]],
    *,
    result: PipelineResult,
    seq: int = 0,
) -> None:
    emit_if_possible(
        emit,
        make_final_result_event(result=result, seq=seq),
    )


# =========================================================
# 统一结构校验入口
# =========================================================

def normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(event, dict):
        return make_error_event("event must be dict")

    event_type = _safe_text(event.get("type"))
    stage = event.get("stage")
    data = event.get("data")
    message = _safe_text(event.get("message"))
    seq = _safe_int(event.get("seq"), 0)
    ts = _safe_int(event.get("ts"), _now_ms())

    if not event_type:
        return make_error_event("event.type is required")

    normalized = make_event(
        event_type=event_type,
        stage=stage,
        data=data if isinstance(data, dict) else {},
        message=message,
        seq=seq,
        ts=ts,
    )
    return normalized