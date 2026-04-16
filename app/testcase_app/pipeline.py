# app/testcase_app/pipeline.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.llm.client import get_llm
from app.services.excel_exporter import export_cases_to_excel
from app.services.requirement_preparer import (
    PreparedRequirement,
    prepare_requirement_from_pdf,
)

from app.testcase_app.agents.analysis_agent import AnalysisAgent
from app.testcase_app.agents.design_agent import DesignAgent
from app.testcase_app.agents.refine_agent import RefineAgent
from app.testcase_app.agents.review_agent import ReviewAgent

from app.testcase_app.constants import (
    DEFAULT_REQUIREMENT_ID,
    EVENT_ANALYSIS_RESULT,
    EVENT_CASE_BATCH,
    EVENT_DESIGN_RESULT,
    EVENT_DOWNLOAD,
    EVENT_ERROR,
    EVENT_FINAL_RESULT,
    EVENT_FINAL_SUMMARY,
    EVENT_METRIC,
    EVENT_REFINE_RESULT,
    EVENT_REFINE_BATCH,
    EVENT_REVIEW_RESULT,
    EVENT_REVIEW_BATCH,
    EVENT_RUNTIME_SNAPSHOT,
    EVENT_STAGE_CONTENT,
    EVENT_STAGE_EVENT,
    EVENT_STAGE_METRIC,
    EVENT_STAGE_SNAPSHOT,
    EVENT_TEST_POINT_BATCH,
    FINAL_STAGE_PROGRESS,
    STAGE_ANALYZE_REQUIREMENT,
    STAGE_ANALYZE_TEST_POINTS,
    STAGE_DESIGN_TESTCASES,
    STAGE_EXPORT_TESTCASES,
    STAGE_FINISHED,
    STAGE_READ_REQUIREMENT,
    STAGE_REFINE_TESTCASES,
    STAGE_REVIEW_TESTCASES,
    STAGE_STATUS_COMPLETED,
    STAGE_STATUS_ERROR,
    STAGE_STATUS_RUNNING,
    STAGE_SUBTITLES,
    STAGE_TITLES,
)
from app.testcase_app.models import (
    AnalysisResult,
    DesignResult,
    ExportArtifact,
    PipelineResult,
    PipelineRuntimeSnapshot,
    PreparedRequirementSummary,
    RefineResult,
    RequirementAnalysisModule,
    RequirementAnalysisResult,
    RequirementPage,
    ReviewResult,
    StageContent,
    StageMetric,
    StageSnapshot,
    TestCase,
    TestPoint,
    TestPointModule,
    build_coverage_summary,
    build_runtime_snapshot,
    ensure_pipeline_result_consistency,
    flatten_test_case_modules,
)

logger = logging.getLogger(__name__)

LLM_TIMEOUT_ANALYSIS = int(os.getenv("TC_LLM_TIMEOUT_ANALYSIS", "600"))
LLM_TIMEOUT_DESIGN = int(os.getenv("TC_LLM_TIMEOUT_DESIGN", "240"))
LLM_TIMEOUT_REVIEW = int(os.getenv("TC_LLM_TIMEOUT_REVIEW", "300"))
LLM_TIMEOUT_REFINE = int(os.getenv("TC_LLM_TIMEOUT_REFINE", "240"))

MAX_TEST_POINTS = int(os.getenv("TC_MAX_TEST_POINTS", "80"))
MAX_TESTCASES = int(os.getenv("TC_MAX_TESTCASES", "200"))

TC_POINTS_PER_CHUNK = int(os.getenv("TC_POINTS_PER_CHUNK", "12"))
TC_MAX_CHUNKS = int(os.getenv("TC_MAX_CHUNKS", "24"))
TC_CHUNK_MIN_CHARS = int(os.getenv("TC_CHUNK_MIN_CHARS", "400"))
TC_CHUNK_MAX_CHARS = int(os.getenv("TC_CHUNK_MAX_CHARS", "2000"))

REQUIREMENT_MIN_CHARS = int(os.getenv("TC_REQUIREMENT_MIN_CHARS", "50"))

PIPELINE_HEARTBEAT_INTERVAL_SEC = float(os.getenv("TC_PIPELINE_HEARTBEAT_INTERVAL_SEC", "1.2"))
PIPELINE_HEARTBEAT_MAX_PREVIEW = int(os.getenv("TC_PIPELINE_HEARTBEAT_MAX_PREVIEW", "8"))
PIPELINE_RUNTIME_PUSH_INTERVAL_SEC = float(os.getenv("TC_PIPELINE_RUNTIME_PUSH_INTERVAL_SEC", "1.5"))


def _ts_ms() -> int:
    return int(time.time() * 1000)


def _ms_since(t0: float) -> int:
    return int((time.time() - t0) * 1000)


def _safe_str(v: Any) -> str:
    return str(v or "").strip()


def _normalize_owner(owner: Optional[str]) -> Optional[str]:
    value = (owner or "").strip()
    return value or None


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _clip_progress(value: int) -> int:
    return max(0, min(100, int(value)))


def _ellipsis(text: str, limit: int = 180) -> str:
    s = _safe_str(text)
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def _prepared_to_summary(prepared: PreparedRequirement) -> PreparedRequirementSummary:
    pages: List[RequirementPage] = []
    for item in getattr(prepared, "pages", []) or []:
        if not isinstance(item, dict):
            continue
        page_text = str(
            item.get("confirmed_text")
            or item.get("text")
            or item.get("ocr_text")
            or ""
        ).strip()
        pages.append(
            RequirementPage(
                page_no=int(item.get("page") or 0),
                text=page_text,
                text_length=len(page_text),
                source=str(item.get("source") or "").strip(),
                image_like=bool(item.get("image_like", False)),
            )
        )

    clean_blocks: List[str] = []
    for x in getattr(prepared, "requirement_blocks", []) or []:
        s = str(x or "").strip()
        if s:
            clean_blocks.append(s)

    final_text = str(getattr(prepared, "final_text", "") or "").strip()
    if not final_text and clean_blocks:
        final_text = "\n\n".join(clean_blocks).strip()
    if not final_text and pages:
        final_text = "\n\n".join([p.text for p in pages if p.text]).strip()

    return PreparedRequirementSummary(
        requirement_id=str(getattr(prepared, "requirement_id", "") or "").strip(),
        title="",
        source_file_name="",
        final_text=final_text,
        clean_blocks=clean_blocks,
        pages=pages,
        total_pages=int(getattr(prepared, "total_pages", 0) or 0),
        usable_for_ai=bool(getattr(prepared, "usable_for_ai", bool(final_text))),
    )


def _summary_from_text(text: str, requirement_id: str) -> PreparedRequirementSummary:
    clean = (text or "").strip()
    return PreparedRequirementSummary(
        requirement_id=requirement_id,
        title="",
        source_file_name="",
        final_text=clean,
        clean_blocks=[clean] if clean else [],
        pages=[],
        total_pages=0,
        usable_for_ai=bool(clean),
    )


def _merge_extra_requirement(
    summary: PreparedRequirementSummary,
    extra_requirement: Optional[str],
) -> PreparedRequirementSummary:
    extra = _safe_str(extra_requirement)
    if not extra:
        return summary

    base_text = _safe_str(summary.final_text)
    merged_parts = [x for x in [base_text, f"【补充测试要求】\n{extra}"] if x]
    merged_text = "\n\n".join(merged_parts).strip()

    merged_blocks = list(summary.clean_blocks or [])
    merged_blocks.append(f"【补充测试要求】\n{extra}")

    return PreparedRequirementSummary(
        requirement_id=summary.requirement_id,
        title=summary.title,
        source_file_name=summary.source_file_name,
        final_text=merged_text,
        clean_blocks=merged_blocks,
        pages=list(summary.pages or []),
        total_pages=summary.total_pages,
        usable_for_ai=bool(merged_text),
    )


def _extract_text_from_loader_result(result: Any) -> str:
    if result is None:
        return ""

    if isinstance(result, str):
        return result.strip()

    if isinstance(result, PreparedRequirementSummary):
        return _safe_str(result.final_text)

    if isinstance(result, PreparedRequirement):
        return _safe_str(_prepared_to_summary(result).final_text)

    if isinstance(result, dict):
        candidates = [
            result.get("final_text"),
            result.get("requirement_text"),
            result.get("text"),
            result.get("content"),
            result.get("clean_text"),
            result.get("body"),
            result.get("raw_text"),
        ]
        for item in candidates:
            text = _safe_str(item)
            if text:
                return text

    for attr in (
        "final_text",
        "requirement_text",
        "text",
        "content",
        "clean_text",
        "body",
        "raw_text",
    ):
        try:
            text = _safe_str(getattr(result, attr, ""))
            if text:
                return text
        except Exception:
            continue

    return ""


async def _load_requirement_text(workflow_id: str, rid: str) -> str:
    candidates = [
        ("app.testcase_app.tasks", "load_requirement_text"),
        ("app.testcase_app.tasks", "get_requirement_text"),
        ("app.testcase_app.tasks", "get_requirement_content"),
        ("app.testcase_app.tasks", "load_requirement_content"),
        ("app.testcase_app.router", "load_requirement_text"),
        ("app.testcase_app.router", "get_requirement_text"),
        ("app.testcase_app.router", "get_requirement_content"),
        ("app.testcase_app.router", "load_requirement_content"),
        ("app.testcase_app.controller", "load_requirement_text"),
        ("app.testcase_app.controller", "get_requirement_text"),
        ("app.testcase_app.controller", "get_requirement_content"),
        ("app.testcase_app.controller", "load_requirement_content"),
        ("app.services.requirement_loader", "load_requirement_text"),
        ("app.services.requirement_loader", "get_requirement_text"),
        ("app.services.requirement_loader", "get_requirement_content"),
        ("app.services.requirement_loader", "load_requirement_content"),
    ]

    for module_name, func_name in candidates:
        try:
            module = __import__(module_name, fromlist=[func_name])
            func = getattr(module, func_name, None)
            if func is None:
                continue

            if inspect.iscoroutinefunction(func):
                raw = await func(workflow_id, rid)
            else:
                raw = await asyncio.to_thread(func, workflow_id, rid)

            text = _extract_text_from_loader_result(raw)
            if text:
                return text
        except Exception:
            logger.warning(
                "[_load_requirement_text] loader failed | module=%s | func=%s",
                module_name,
                func_name,
                exc_info=True,
            )
            continue

    return ""


async def _try_load_prepared_requirement_from_project(
    workflow_id: str,
    requirement_id: str,
) -> Optional[PreparedRequirementSummary]:
    candidates = [
        ("app.testcase_app.tasks", "load_prepared_requirement"),
        ("app.testcase_app.router", "load_prepared_requirement"),
        ("app.testcase_app.controller", "load_prepared_requirement"),
        ("app.services.requirement_loader", "load_prepared_requirement"),
        ("app.services.requirement_loader", "get_prepared_requirement"),
    ]

    for module_name, func_name in candidates:
        try:
            module = __import__(module_name, fromlist=[func_name])
            func = getattr(module, func_name, None)
            if func is None:
                continue

            if inspect.iscoroutinefunction(func):
                raw = await func(workflow_id, requirement_id)
            else:
                raw = await asyncio.to_thread(func, workflow_id, requirement_id)

            if isinstance(raw, PreparedRequirementSummary):
                return raw

            if isinstance(raw, PreparedRequirement):
                return _prepared_to_summary(raw)

            if isinstance(raw, dict):
                text = _extract_text_from_loader_result(raw)
                if text:
                    return _summary_from_text(text, requirement_id)

        except Exception:
            logger.warning(
                "[_try_load_prepared_requirement_from_project] loader failed | module=%s | func=%s",
                module_name,
                func_name,
                exc_info=True,
            )
            continue

    return None


async def _load_prepared_requirement(
    workflow_id: str,
    requirement_id: str,
    *,
    pdf_path: Optional[str] = None,
    requirement_text: Optional[str] = None,
) -> PreparedRequirementSummary:
    # ① 优先使用上传阶段已解析好的文本，避免在 worker 里重复解析大 PDF
    if requirement_text and requirement_text.strip():
        return _summary_from_text(requirement_text.strip(), requirement_id)

    # ② 没有预解析文本，才从 PDF 重新解析
    if pdf_path and os.path.exists(pdf_path):
        def _prepare() -> PreparedRequirement:
            try:
                return prepare_requirement_from_pdf(
                    pdf_path=pdf_path,
                    requirement_id=requirement_id,
                )
            except TypeError:
                try:
                    return prepare_requirement_from_pdf(
                        pdf_path,
                        requirement_id=requirement_id,
                    )
                except TypeError:
                    return prepare_requirement_from_pdf(pdf_path)

        prepared = await asyncio.to_thread(_prepare)
        summary = _prepared_to_summary(prepared)
        if not summary.requirement_id:
            summary.requirement_id = requirement_id
        return summary

    prepared_from_project = await _try_load_prepared_requirement_from_project(
        workflow_id=workflow_id,
        requirement_id=requirement_id,
    )
    if prepared_from_project:
        return prepared_from_project

    loaded_text = await _load_requirement_text(workflow_id, requirement_id)
    return _summary_from_text(loaded_text, requirement_id)


def _validate_prepared_requirement(
    prepared_requirement: PreparedRequirementSummary,
    *,
    workflow_id: str,
    requirement_id: str,
    has_pdf_path: bool,
    has_requirement_text: bool,
) -> Optional[str]:
    final_text = _safe_str(prepared_requirement.final_text)
    final_text_len = len(final_text)

    if final_text_len >= REQUIREMENT_MIN_CHARS:
        return None

    return (
        "requirement text is too short or empty | "
        f"workflow_id={workflow_id} | "
        f"requirement_id={requirement_id} | "
        f"has_pdf_path={has_pdf_path} | "
        f"has_requirement_text={has_requirement_text} | "
        f"final_text_len={final_text_len} | "
        f"required_min_chars={REQUIREMENT_MIN_CHARS} | "
        f"usable_for_ai={prepared_requirement.usable_for_ai} | "
        f"total_pages={prepared_requirement.total_pages}"
    )


def _guess_module_name(block: str, index: int) -> str:
    text = _safe_str(block)
    if not text:
        return f"模块{index}"

    first_line = text.splitlines()[0].strip()
    first_line = first_line[:48].strip("：: -")
    if not first_line:
        return f"模块{index}"
    return first_line


def _post_add_integration_points(
    analysis_result: AnalysisResult,
    requirement_text: str,
) -> AnalysisResult:
    """
    在 AnalysisAgent 完成后，扫描现有测试点，
    补充 LLM 可能遗漏的两类关键测试点：
    1. 跨模块一致性（"全局生效"类功能：在模块A操作后切换到模块B验证同步）
    2. 持久化回显（"保存/生效"类功能：操作后重新进入页面验证数据持久化）
    """
    import uuid

    _GLOBAL_KEYWORDS = ["全局", "同步", "影响全部", "所有模块", "全部生效", "跨模块", "统一"]
    _PERSIST_KEYWORDS = ["保存", "全局生效", "提交", "设置", "配置更新", "生效"]
    _PERSIST_TITLE_KW = ["持久化", "重新进入", "刷新后", "重进", "回显"]

    all_points = analysis_result.all_points()
    all_titles = {p.title for p in all_points}

    def _pid() -> str:
        return f"TP_INT_{uuid.uuid4().hex[:6].upper()}"

    new_points: List[TestPoint] = []

    # 1. 跨模块一致性：如需求含"全局"关键词，且已有跨模块测试点不足
    has_global = any(kw in requirement_text for kw in _GLOBAL_KEYWORDS)
    has_cross_module_point = any(
        any(kw in p.title for kw in ["全局同步", "跨模块", "切换.*模块", "模块.*一致"])
        for p in all_points
    )
    if has_global and not has_cross_module_point:
        # 从已有模块中找含"全局生效"的测试点，推断 subject
        global_points = [p for p in all_points if any(kw in p.title for kw in _PERSIST_KEYWORDS)]
        if global_points:
            ref_point = global_points[0]
            obj = ref_point.module or "配置"
            new_points.append(TestPoint(
                point_id=_pid(),
                module=ref_point.module,
                point_type="normal",
                scenario_type="normal",
                title=f"{obj}全局生效后切换到其他模块验证同步",
                objective=f"验证在{ref_point.module}执行全局生效操作后，切换到其他功能模块，配置/状态已同步生效，无需重复操作。",
                preconditions=[f"已登录系统，在{ref_point.module}完成配置并执行全局生效"],
                inputs=["切换到其他模块或页面"],
                check_items=["跨模块同步", "全局一致性"],
                expected_results=[f"切换后其他模块的{obj}配置与全局生效时设置的值一致，无延迟也无需重新触发"],
                priority="P0",
                tags=["功能测试"],
            ))

    # 2. 持久化回显：如有"保存/生效"测试点，但缺少"重进入页面验证"测试点
    persist_points = [p for p in all_points if any(kw in p.title for kw in _PERSIST_KEYWORDS)]
    has_persist_check = any(any(kw in p.title for kw in _PERSIST_TITLE_KW) for p in all_points)
    if persist_points and not has_persist_check:
        ref_point = persist_points[0]
        new_points.append(TestPoint(
            point_id=_pid(),
            module=ref_point.module,
            point_type="normal",
            scenario_type="normal",
            title=f"{ref_point.module}配置生效后刷新页面验证持久化回显",
            objective="验证用户完成配置保存/全局生效后，刷新页面或重新进入功能，配置状态正确持久化，不会回退到默认值。",
            preconditions=["已完成配置修改并触发保存/全局生效操作", "操作返回成功"],
            inputs=["刷新当前页面，或退出后重新进入功能页"],
            check_items=["持久化验证", "配置回显", "状态一致性"],
            expected_results=["重新进入后配置值与操作时设置的一致，未回退；localStorage/服务端数据与UI显示一致"],
            priority="P1",
            tags=["功能测试"],
        ))

    if not new_points:
        return analysis_result

    # 将新测试点合并到对应模块
    modules_map: Dict[str, TestPointModule] = {m.module: m for m in analysis_result.modules}
    for pt in new_points:
        target_module = pt.module or (analysis_result.modules[0].module if analysis_result.modules else "集成测试")
        if target_module in modules_map:
            existing = list(modules_map[target_module].normal_points or [])
            existing.append(pt)
            modules_map[target_module].normal_points = existing
        else:
            modules_map[target_module] = TestPointModule(
                module=target_module,
                normal_points=[pt],
                exception_points=[],
                boundary_points=[],
            )

    logger.info("[Pipeline] 补充集成测试点 %d 条", len(new_points))
    analysis_result.modules = list(modules_map.values())
    return analysis_result


def _build_requirement_analysis_result(
    prepared_requirement: PreparedRequirementSummary,
) -> RequirementAnalysisResult:
    blocks = [b.strip() for b in (prepared_requirement.clean_blocks or []) if b and str(b).strip()]
    final_text = _safe_str(prepared_requirement.final_text)

    if not blocks and final_text:
        blocks = [x.strip() for x in final_text.split("\n\n") if x.strip()]

    modules: List[RequirementAnalysisModule] = []
    rules: List[str] = []
    constraints: List[str] = []
    risks: List[str] = []

    for idx, block in enumerate(blocks[:20], start=1):
        module_name = _guess_module_name(block, idx)
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        summary = "；".join(lines[:3])[:240]

        module_rules: List[str] = []
        module_constraints: List[str] = []
        module_risks: List[str] = []

        for line in lines[:20]:
            lower = line.lower()
            if any(key in line for key in ["必须", "需要", "应当", "不可", "不能", "仅", "限制"]) or any(
                key in lower for key in ["must", "should", "only", "cannot", "limit"]
            ):
                module_constraints.append(line)
            elif any(key in line for key in ["异常", "失败", "错误", "拦截", "边界", "重复", "超出", "校验"]) or any(
                key in lower for key in ["error", "invalid", "fail", "exception", "boundary", "duplicate"]
            ):
                module_risks.append(line)
            else:
                module_rules.append(line)

        modules.append(
            RequirementAnalysisModule(
                module=module_name,
                summary=summary,
                rules=module_rules[:8],
                constraints=module_constraints[:8],
                risks=module_risks[:8],
            )
        )

        rules.extend(module_rules[:3])
        constraints.extend(module_constraints[:3])
        risks.extend(module_risks[:3])

    overall_summary = (
        f"需求分析完成，识别 {len(modules)} 个候选业务模块，"
        f"抽取规则 {len(rules)} 条、约束 {len(constraints)} 条、风险点 {len(risks)} 条。"
    )

    return RequirementAnalysisResult(
        requirement_id=_safe_str(prepared_requirement.requirement_id) or DEFAULT_REQUIREMENT_ID,
        summary=overall_summary,
        business_goal="基于需求内容提炼业务模块、规则、约束和风险，为测试点生成提供结构化上下文。",
        modules=modules,
        rules=rules[:20],
        constraints=constraints[:20],
        risks=risks[:20],
        assumptions=[],
    )


async def _export_testcases_to_excel(
    requirement_id: str,
    final_cases: List[TestCase],
    owner: Optional[str] = None,
) -> Tuple[Optional[str], str, Optional[str], Optional[str]]:
    file_name = f"测试用例_{requirement_id}_{int(time.time())}.xlsx"
    export_rows: List[Dict[str, Any]] = []

    safe_owner = _normalize_owner(owner)
    for case in final_cases or []:
        if not isinstance(case, TestCase):
            continue
        row = case.to_export_dict()
        if safe_owner and not str(row.get("owner") or "").strip():
            row["owner"] = safe_owner
        export_rows.append(row)

    try:
        result = await asyncio.to_thread(
            export_cases_to_excel,
            export_rows,
            requirement_id,
            filename=file_name,
        )

        if not isinstance(result, dict):
            return (None, file_name, None, "excel_exporter returned non-dict")

        file_id = result.get("file_id")
        download_url = result.get("download_url")
        export_error = result.get("error")
        final_name = result.get("file_name") or file_name

        if not file_id:
            return (None, final_name, None, export_error or "file_id is empty")

        return (str(file_id), final_name, download_url, export_error)
    except Exception as e:
        logger.exception("export_cases_to_excel failed")
        return (None, file_name, None, repr(e))


def _make_export_artifact(
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
        ready=ready,
        file_id=file_id,
        filename=filename,
        excel_path=excel_path,
        json_path=json_path,
        download_url=download_url,
        error=error,
    )


def _analysis_done_message(result: AnalysisResult) -> str:
    total_points = int(result.statistics.total_points or 0)
    total_modules = int(result.statistics.total_modules or 0)
    return f"测试点分析完成，共生成 {total_points} 个测试点，覆盖 {total_modules} 个模块。"


def _design_done_message(result: DesignResult) -> str:
    total_cases = int(result.statistics.total_cases or 0)
    total_modules = int(result.statistics.total_modules or 0)
    return f"测试用例设计完成，共生成 {total_cases} 条测试用例，覆盖 {total_modules} 个模块。"


def _review_done_message(result: ReviewResult) -> str:
    decision = _safe_str(result.decision) or "已完成"
    issue_count = int(result.issue_count or 0)
    gap_count = len(result.coverage_gaps or [])
    return f"评审完成，结论：{decision}，发现 {issue_count} 个问题，覆盖缺口 {gap_count} 项。"


def _refine_done_message(result: RefineResult) -> str:
    total_cases = int(result.statistics.total_cases or 0)
    total_modules = int(result.statistics.total_modules or 0)
    uncovered = int(result.coverage_summary.uncovered_points or 0)
    return f"优化完成，共输出 {total_cases} 条测试用例，覆盖 {total_modules} 个模块，未覆盖测试点 {uncovered} 个。"


def _export_done_message(artifact: ExportArtifact) -> str:
    if artifact.ready:
        return "导出文件已生成，可下载。"
    return artifact.error or "导出失败。"


def _extract_stage_preview_lines(result: Any, stage: str) -> List[str]:
    try:
        if stage == STAGE_ANALYZE_REQUIREMENT and isinstance(result, RequirementAnalysisResult):
            return [
                result.summary,
                *[m.module for m in result.modules[:PIPELINE_HEARTBEAT_MAX_PREVIEW]],
            ]

        if stage == STAGE_ANALYZE_TEST_POINTS and isinstance(result, AnalysisResult):
            lines = [_analysis_done_message(result)]
            for module in result.modules[:PIPELINE_HEARTBEAT_MAX_PREVIEW]:
                lines.append(f"{module.module}：{module.total}个测试点")
            return lines

        if stage == STAGE_DESIGN_TESTCASES and isinstance(result, DesignResult):
            lines = [_design_done_message(result)]
            for module in result.modules[:PIPELINE_HEARTBEAT_MAX_PREVIEW]:
                lines.append(f"{module.module}：{module.total}条用例")
            return lines

        if stage == STAGE_REVIEW_TESTCASES and isinstance(result, ReviewResult):
            lines = [_review_done_message(result)]
            for issue in result.issues[:PIPELINE_HEARTBEAT_MAX_PREVIEW]:
                title = _safe_str(getattr(issue, "title", "") or getattr(issue, "description", ""))
                issue_type = _safe_str(getattr(issue, "issue_type", "问题"))
                lines.append(f"{issue_type}：{_ellipsis(title, 80)}")
            return lines

        if stage == STAGE_REFINE_TESTCASES and isinstance(result, RefineResult):
            lines = [_refine_done_message(result)]
            for module in result.modules[:PIPELINE_HEARTBEAT_MAX_PREVIEW]:
                lines.append(f"{module.module}：{module.total}条最终用例")
            return lines
    except Exception:
        logger.warning("[_extract_stage_preview_lines] failed", exc_info=True)

    return []


def _extract_stage_content_payload(result: Any, stage: str) -> Tuple[str, Dict[str, Any], str]:
    try:
        if stage == STAGE_ANALYZE_REQUIREMENT and isinstance(result, RequirementAnalysisResult):
            return (
                result.modules[0].module if result.modules else "",
                {
                    "business_goal": result.business_goal,
                    "module_count": len(result.modules),
                    "rules_count": len(result.rules),
                    "constraints_count": len(result.constraints),
                    "risks_count": len(result.risks),
                },
                "需求分析结果",
            )

        if stage == STAGE_ANALYZE_TEST_POINTS and isinstance(result, AnalysisResult):
            return (
                result.modules[0].module if result.modules else "",
                {
                    "total_points": result.statistics.total_points,
                    "total_modules": result.statistics.total_modules,
                    "normal_count": result.statistics.normal_count,
                    "exception_count": result.statistics.exception_count,
                    "boundary_count": result.statistics.boundary_count,
                },
                "测试点分析结果",
            )

        if stage == STAGE_DESIGN_TESTCASES and isinstance(result, DesignResult):
            return (
                result.modules[0].module if result.modules else "",
                {
                    "total_cases": result.statistics.total_cases,
                    "total_modules": result.statistics.total_modules,
                    "priority_counts": result.statistics.priority_counts,
                },
                "测试用例草稿",
            )

        if stage == STAGE_REVIEW_TESTCASES and isinstance(result, ReviewResult):
            return (
                "",
                {
                    "decision": result.decision,
                    "issue_count": result.issue_count,
                    "coverage_gap_count": len(result.coverage_gaps),
                    "invalid_case_count": len(result.invalid_case_ids),
                    "duplicate_case_count": len(result.duplicated_case_ids),
                },
                "用例评审结果",
            )

        if stage == STAGE_REFINE_TESTCASES and isinstance(result, RefineResult):
            return (
                result.modules[0].module if result.modules else "",
                {
                    "total_cases": result.statistics.total_cases,
                    "total_modules": result.statistics.total_modules,
                    "covered_points": result.coverage_summary.covered_points,
                    "uncovered_points": result.coverage_summary.uncovered_points,
                    "coverage_rate": result.coverage_summary.coverage_rate,
                },
                "优化后测试用例",
            )
    except Exception:
        logger.warning("[_extract_stage_content_payload] failed", exc_info=True)

    return ("", {}, STAGE_TITLES.get(stage, stage))


def _extract_output_count(result: Any, stage: str) -> int:
    try:
        if stage == STAGE_ANALYZE_REQUIREMENT and isinstance(result, RequirementAnalysisResult):
            return len(result.modules)
        if stage == STAGE_ANALYZE_TEST_POINTS and isinstance(result, AnalysisResult):
            return int(result.statistics.total_points or 0)
        if stage == STAGE_DESIGN_TESTCASES and isinstance(result, DesignResult):
            return int(result.statistics.total_cases or 0)
        if stage == STAGE_REVIEW_TESTCASES and isinstance(result, ReviewResult):
            return int(result.issue_count or 0)
        if stage == STAGE_REFINE_TESTCASES and isinstance(result, RefineResult):
            return int(result.statistics.total_cases or 0)
    except Exception:
        logger.warning("[_extract_output_count] failed", exc_info=True)
    return 0


def _extract_stage_done_message(result: Any, stage: str) -> str:
    if stage == STAGE_ANALYZE_REQUIREMENT and isinstance(result, RequirementAnalysisResult):
        return result.summary
    if stage == STAGE_ANALYZE_TEST_POINTS and isinstance(result, AnalysisResult):
        return _analysis_done_message(result)
    if stage == STAGE_DESIGN_TESTCASES and isinstance(result, DesignResult):
        return _design_done_message(result)
    if stage == STAGE_REVIEW_TESTCASES and isinstance(result, ReviewResult):
        return _review_done_message(result)
    if stage == STAGE_REFINE_TESTCASES and isinstance(result, RefineResult):
        return _refine_done_message(result)
    return "阶段执行完成。"


def _stage_heartbeat_templates(stage: str) -> List[str]:
    mapping = {
        STAGE_ANALYZE_REQUIREMENT: [
            "正在拆解需求结构与业务目标…",
            "正在提取关键规则、约束与风险点…",
            "正在整理后续测试点生成所需上下文…",
        ],
        STAGE_ANALYZE_TEST_POINTS: [
            "正在从需求中抽取核心测试场景…",
            "正在补充异常、边界与状态流转测试点…",
            "正在整理模块级测试点覆盖结果…",
        ],
        STAGE_DESIGN_TESTCASES: [
            "正在将测试点展开为可执行测试用例…",
            "正在细化步骤、预期与优先级…",
            "正在按模块整理测试用例草稿…",
        ],
        STAGE_REVIEW_TESTCASES: [
            "正在检查重复、缺失与无效用例…",
            "正在核对覆盖缺口与评审问题…",
            "正在形成评审结论与修改建议…",
        ],
        STAGE_REFINE_TESTCASES: [
            "正在根据评审结果优化测试用例…",
            "正在去重、补齐覆盖并统一格式…",
            "正在生成最终可导出测试用例结果…",
        ],
        STAGE_EXPORT_TESTCASES: [
            "正在导出测试用例文件…",
            "正在写入 Excel 内容并生成下载地址…",
        ],
    }
    return mapping.get(stage, ["正在处理，请稍候…"])


class _Emitter:
    def __init__(self, emit: Optional[Callable[[Dict[str, Any]], Awaitable[None]]]):
        self.emit = emit

    async def send(self, payload: Dict[str, Any]) -> None:
        if self.emit:
            await self.emit(payload)

    async def stage_snapshot(self, snapshot: StageSnapshot) -> None:
        await self.send({
            "type": EVENT_STAGE_SNAPSHOT,
            "data": snapshot.to_dict(),
            "ts": _ts_ms(),
        })
        await self.send({
            "type": EVENT_STAGE_EVENT,
            "data": {
                "stage": snapshot.key,
                "status": snapshot.status,
                "title": snapshot.title,
                "message": snapshot.summary,
                "progress": snapshot.progress,
                "duration_ms": snapshot.duration_ms,
                "started_at": snapshot.started_at,
                "ended_at": snapshot.finished_at,
                "extra": snapshot.extra,
            },
            "ts": _ts_ms(),
        })

    async def stage_metric(self, metric: StageMetric) -> None:
        await self.send({
            "type": EVENT_STAGE_METRIC,
            "data": metric.to_dict(),
            "ts": _ts_ms(),
        })
        await self.send({
            "type": EVENT_METRIC,
            "data": metric.to_dict(),
            "ts": _ts_ms(),
        })

    async def stage_content(self, content: StageContent) -> None:
        await self.send({
            "type": EVENT_STAGE_CONTENT,
            "data": content.to_dict(),
            "ts": _ts_ms(),
        })

    async def runtime_snapshot(self, runtime: PipelineRuntimeSnapshot) -> None:
        await self.send({
            "type": EVENT_RUNTIME_SNAPSHOT,
            "data": runtime.to_dict(),
            "ts": _ts_ms(),
        })


class TestcasePipeline:
    def __init__(self):
        self.llm = get_llm()

        self.analysis_agent = AnalysisAgent(
            llm=self.llm,
            max_points=MAX_TEST_POINTS,
            points_per_chunk=max(1, TC_POINTS_PER_CHUNK),
            max_chunks=max(1, TC_MAX_CHUNKS),
            chunk_min_chars=max(100, TC_CHUNK_MIN_CHARS),
            chunk_max_chars=max(TC_CHUNK_MIN_CHARS, TC_CHUNK_MAX_CHARS),
            timeout=LLM_TIMEOUT_ANALYSIS,
        )

        self.design_agent = DesignAgent(
            llm=self.llm,
            max_cases=MAX_TESTCASES,
            chunk_size=max(1, TC_POINTS_PER_CHUNK),
            timeout=LLM_TIMEOUT_DESIGN,
        )

        self.review_agent = ReviewAgent(
            llm=self.llm,
            timeout=LLM_TIMEOUT_REVIEW,
        )

        self.refine_agent = RefineAgent(
            llm=self.llm,
            timeout=LLM_TIMEOUT_REFINE,
        )

    async def _run_stage_in_thread(
        self,
        fn: Callable[..., Any],
        timeout: int,
        **kwargs: Any,
    ) -> Any:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, **kwargs),
            timeout=timeout + 30,
        )

    async def run_once(
        self,
        *,
        prepared_requirement: PreparedRequirementSummary,
        owner: Optional[str] = None,
        emit: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        cancel_checker: Optional[Callable[[], Awaitable[bool]]] = None,
        initial_stage_snapshots: Optional[List[StageSnapshot]] = None,
        initial_stage_metrics: Optional[List[StageMetric]] = None,
    ) -> PipelineResult:
        owner = _normalize_owner(owner)
        requirement_id = _safe_str(prepared_requirement.requirement_id) or DEFAULT_REQUIREMENT_ID

        result = PipelineResult(
            requirement_id=requirement_id,
            owner=owner or "",
            prepared_requirement=prepared_requirement,
        )

        stage_snapshots: List[StageSnapshot] = list(initial_stage_snapshots or [])
        stage_metrics: List[StageMetric] = list(initial_stage_metrics or [])
        emitter = _Emitter(emit)

        async def _check_cancel() -> None:
            if cancel_checker and await cancel_checker():
                raise asyncio.CancelledError("pipeline cancelled")

        async def _push_runtime(force: bool = False, last_push: List[float] = [0.0]) -> None:
            now = time.time()
            if not force and (now - last_push[0]) < PIPELINE_RUNTIME_PUSH_INTERVAL_SEC:
                return

            result.stage_snapshots = stage_snapshots
            result.stage_metrics = stage_metrics
            ensure_pipeline_result_consistency(result)

            if result.runtime_snapshot:
                await emitter.runtime_snapshot(result.runtime_snapshot)

            last_push[0] = now

        async def _start_stage(
            stage: str,
            progress: int,
            summary: str,
            extra: Optional[Dict[str, Any]] = None,
        ) -> StageSnapshot:
            snapshot = StageSnapshot(
                key=stage,  # type: ignore[arg-type]
                title=STAGE_TITLES.get(stage, stage),
                status=STAGE_STATUS_RUNNING,  # type: ignore[arg-type]
                summary=summary,
                progress=progress,
                started_at=_ts_ms(),
                finished_at=None,
                duration_ms=0,
                extra=extra or {},
            ).normalize()
            stage_snapshots.append(snapshot)
            await emitter.stage_snapshot(snapshot)
            await _push_runtime(force=True)
            return snapshot

        async def _complete_stage(
            snapshot: StageSnapshot,
            *,
            summary: str,
            duration_ms: int,
            progress: int,
            extra: Optional[Dict[str, Any]] = None,
            input_count: int = 0,
            output_count: int = 0,
        ) -> None:
            snapshot.status = STAGE_STATUS_COMPLETED  # type: ignore[assignment]
            snapshot.summary = summary
            snapshot.message = summary
            snapshot.duration_ms = duration_ms
            snapshot.progress = progress
            snapshot.finished_at = _ts_ms()
            snapshot.ended_at = snapshot.finished_at
            snapshot.extra = extra or snapshot.extra
            snapshot.normalize()

            metric = StageMetric(
                stage=snapshot.key,  # type: ignore[arg-type]
                duration_ms=duration_ms,
                input_count=input_count,
                output_count=output_count,
                extra=extra or {},
            ).normalize()
            stage_metrics.append(metric)

            await emitter.stage_metric(metric)
            await emitter.stage_snapshot(snapshot)
            await _push_runtime(force=True)

        async def _fail_stage(
            snapshot: StageSnapshot,
            *,
            summary: str,
            extra: Optional[Dict[str, Any]] = None,
        ) -> None:
            snapshot.status = STAGE_STATUS_ERROR  # type: ignore[assignment]
            snapshot.summary = summary
            snapshot.message = summary
            snapshot.finished_at = _ts_ms()
            snapshot.ended_at = snapshot.finished_at
            snapshot.extra = extra or snapshot.extra
            snapshot.duration_ms = max(
                0,
                (snapshot.finished_at or 0) - (snapshot.started_at or snapshot.finished_at or 0),
            )
            snapshot.normalize()
            await emitter.stage_snapshot(snapshot)
            await _push_runtime(force=True)

        async def _emit_stage_chat_tick(
            *,
            stage: str,
            snapshot: StageSnapshot,
            base_progress: int,
            max_progress: int,
            step_no: int,
        ) -> None:
            templates = _stage_heartbeat_templates(stage)
            message = templates[step_no % max(1, len(templates))]
            span = max(0, max_progress - base_progress)
            # 前期涨得快，后期趋缓，避免直接顶满
            dynamic = min(span, max(1, step_no * max(1, span // 8 or 1)))
            progress = _clip_progress(base_progress + dynamic)

            snapshot.summary = message
            snapshot.message = message
            snapshot.progress = progress
            snapshot.duration_ms = max(0, _ts_ms() - (snapshot.started_at or _ts_ms()))
            snapshot.extra = {
                **(snapshot.extra or {}),
                "heartbeat_step": step_no,
            }
            snapshot.normalize()

            await emitter.stage_snapshot(snapshot)
            await emitter.stage_content(
                StageContent(
                    stage=stage,
                    title=STAGE_TITLES.get(stage, stage),
                    module="",
                    content={
                        "kind": "heartbeat",
                        "message": message,
                        "step": step_no,
                        "progress": progress,
                    },
                    preview_lines=[message],
                    updated_at=_ts_ms(),
                )
            )
            await _push_runtime()

        async def _run_callable_stage(
            *,
            stage: str,
            stage_snapshot: StageSnapshot,
            start_progress: int,
            running_max_progress: int,
            finish_progress: int,
            timeout: int,
            fn: Callable[..., Any],
            input_count: int,
            content_title: str,
            result_event_type: Optional[str],
            event_data_builder: Optional[Callable[[Any], Dict[str, Any]]] = None,
            **kwargs: Any,
        ) -> Any:
            await _check_cancel()
            t0 = time.time()
            task = asyncio.create_task(self._run_stage_in_thread(fn, timeout, **kwargs))
            heartbeat_step = 0

            while not task.done():
                await asyncio.sleep(PIPELINE_HEARTBEAT_INTERVAL_SEC)
                heartbeat_step += 1
                await _check_cancel()
                await _emit_stage_chat_tick(
                    stage=stage,
                    snapshot=stage_snapshot,
                    base_progress=start_progress,
                    max_progress=running_max_progress,
                    step_no=heartbeat_step,
                )

            result_obj = await task
            duration_ms = _ms_since(t0)

            if result_event_type:
                try:
                    payload_data = (
                        event_data_builder(result_obj)
                        if event_data_builder
                        else result_obj.to_dict()
                    )
                    await emitter.send({
                        "type": result_event_type,
                        "stage": stage,
                        "data": payload_data,
                        "ts": _ts_ms(),
                    })
                except Exception:
                    logger.warning("[_run_callable_stage] emit result event failed | stage=%s", stage, exc_info=True)

            module_name, content_payload, resolved_title = _extract_stage_content_payload(result_obj, stage)
            preview_lines = _extract_stage_preview_lines(result_obj, stage)

            await emitter.stage_content(
                StageContent(
                    stage=stage,
                    title=content_title or resolved_title,
                    module=module_name,
                    content=content_payload,
                    preview_lines=preview_lines,
                    updated_at=_ts_ms(),
                )
            )

            done_summary = _extract_stage_done_message(result_obj, stage)
            output_count = _extract_output_count(result_obj, stage)

            await _complete_stage(
                stage_snapshot,
                summary=done_summary,
                duration_ms=duration_ms,
                progress=finish_progress,
                extra={**content_payload},
                input_count=input_count,
                output_count=output_count,
            )
            return result_obj

        # 1) 需求分析（轻量预分析）
        await _check_cancel()
        stage_snapshot = await _start_stage(
            STAGE_ANALYZE_REQUIREMENT,
            24,
            STAGE_SUBTITLES[STAGE_ANALYZE_REQUIREMENT],
        )

        try:
            requirement_analysis_result: RequirementAnalysisResult = await _run_callable_stage(
                stage=STAGE_ANALYZE_REQUIREMENT,
                stage_snapshot=stage_snapshot,
                start_progress=24,
                running_max_progress=31,
                finish_progress=31,
                timeout=max(30, min(LLM_TIMEOUT_ANALYSIS, 120)),
                fn=_build_requirement_analysis_result,
                input_count=1,
                content_title="需求分析结果",
                result_event_type=EVENT_ANALYSIS_RESULT,
                prepared_requirement=prepared_requirement,
            )
            result.requirement_analysis_result = requirement_analysis_result
        except Exception as e:
            await _fail_stage(
                stage_snapshot,
                summary=f"需求分析失败：{repr(e)}",
                extra={"error": repr(e)},
            )
            raise

        # 2) 测试点分析（完成后按模块流式推送）
        await _check_cancel()
        stage_snapshot = await _start_stage(
            STAGE_ANALYZE_TEST_POINTS,
            40,
            STAGE_SUBTITLES[STAGE_ANALYZE_TEST_POINTS],
        )

        try:
            t0_analysis = time.time()
            # analysis_agent.run() 是同步的，在线程池跑
            # 用 run_coroutine_threadsafe 桥接：让 sync emit 能投递到当前 event loop
            _loop = asyncio.get_event_loop()

            def _sync_emit(event: Dict[str, Any]) -> None:
                if emitter.emit:
                    asyncio.run_coroutine_threadsafe(emitter.send(event), _loop)

            analysis_result: AnalysisResult = await asyncio.wait_for(
                asyncio.to_thread(
                    self.analysis_agent.run,
                    prepared_requirement=prepared_requirement,
                    emit=_sync_emit,
                ),
                timeout=LLM_TIMEOUT_ANALYSIS + 30,
            )
            duration_ms_analysis = _ms_since(t0_analysis)

            result.analysis_result = analysis_result
            ensure_pipeline_result_consistency(result)

            # 补充跨模块/持久化集成测试点（AnalysisAgent 分块分析时可能遗漏）
            _req_text_for_integration = (prepared_requirement.final_text or "")[:8000]
            analysis_result = _post_add_integration_points(analysis_result, _req_text_for_integration)
            result.analysis_result = analysis_result

            # 按模块流式推送测试点（完整结果出来后逐模块推）
            modules_list = analysis_result.modules or []
            for mod_idx, mod in enumerate(modules_list):
                await emitter.send({
                    "type": EVENT_TEST_POINT_BATCH,
                    "stage": STAGE_ANALYZE_TEST_POINTS,
                    "data": {
                        "module": mod.module,
                        "points": [p.to_dict() if hasattr(p, "to_dict") else {} for p in mod.all_points()],
                        "module_idx": mod_idx,
                        "total_modules": len(modules_list),
                        "cumulative_points": sum(
                            len(m.all_points()) for m in modules_list[:mod_idx + 1]
                        ),
                    },
                    "ts": _ts_ms(),
                })
                # 更新进度
                done_ratio = (mod_idx + 1) / max(1, len(modules_list))
                progress = _clip_progress(40 + int(15 * done_ratio))
                stage_snapshot.progress = progress
                stage_snapshot.summary = (
                    f"已推送 {mod.module} 的 {len(mod.all_points())} 个测试点"
                    f"（{mod_idx + 1}/{len(modules_list)} 模块）"
                )
                stage_snapshot.normalize()
                await emitter.stage_snapshot(stage_snapshot)

            # 推送完整分析结果
            await emitter.send({
                "type": EVENT_ANALYSIS_RESULT,
                "stage": STAGE_ANALYZE_TEST_POINTS,
                "data": analysis_result.to_dict() if hasattr(analysis_result, "to_dict") else {},
                "ts": _ts_ms(),
            })
            await _complete_stage(
                stage_snapshot,
                summary=_extract_stage_done_message(analysis_result, STAGE_ANALYZE_TEST_POINTS),
                duration_ms=duration_ms_analysis,
                progress=55,
                input_count=1,
                output_count=int(analysis_result.statistics.total_points or 0),
            )

        except Exception as e:
            await _fail_stage(
                stage_snapshot,
                summary=f"测试点分析失败：{repr(e)}",
                extra={"error": repr(e)},
            )
            raise

        # 3) 生成测试用例（流式：每批完成立即推给前端）
        await _check_cancel()
        stage_snapshot = await _start_stage(
            STAGE_DESIGN_TESTCASES,
            62,
            STAGE_SUBTITLES[STAGE_DESIGN_TESTCASES],
        )

        try:
            # 获取需求摘要，供 LLM prompt 参考
            _req_summary = ""
            if result.requirement_analysis_result:
                _req_summary = result.requirement_analysis_result.summary or ""
            if not _req_summary and prepared_requirement:
                _req_summary = (prepared_requirement.final_text or "")[:2000]

            # 流式回调：每批 case 生成后立即推给前端
            _design_batch_count: List[int] = [0]

            async def _on_design_batch_done(
                batch_cases: List[TestCase],
                batch_idx: int,
                total_batches: int,
            ) -> None:
                _design_batch_count[0] += len(batch_cases)
                await emitter.send({
                    "type": EVENT_CASE_BATCH,
                    "stage": STAGE_DESIGN_TESTCASES,
                    "data": {
                        "cases": [c.to_export_dict() for c in batch_cases],
                        "batch_idx": batch_idx,
                        "total_batches": total_batches,
                        "cumulative_count": _design_batch_count[0],
                    },
                    "ts": _ts_ms(),
                })
                # 同步更新进度
                span = max(0, 73 - 62)
                done_ratio = (batch_idx + 1) / max(1, total_batches)
                progress = _clip_progress(62 + int(span * done_ratio * 0.9))
                stage_snapshot.progress = progress
                stage_snapshot.summary = (
                    f"已生成 {_design_batch_count[0]} 条用例"
                    f"（第 {batch_idx + 1}/{total_batches} 批）"
                )
                stage_snapshot.normalize()
                await emitter.stage_snapshot(stage_snapshot)

            # 直接 await（design_agent.run 已是 async）
            t0_design = time.time()
            design_result: DesignResult = await asyncio.wait_for(
                self.design_agent.run(
                    analysis_result=analysis_result,
                    requirement_id=requirement_id,
                    requirement_summary=_req_summary,
                    on_batch_done=_on_design_batch_done,
                ),
                timeout=LLM_TIMEOUT_DESIGN + 60,
            )
            duration_ms_design = _ms_since(t0_design)

            result.design_result = design_result
            ensure_pipeline_result_consistency(result)

            # 推送完整结果
            await emitter.send({
                "type": EVENT_DESIGN_RESULT,
                "stage": STAGE_DESIGN_TESTCASES,
                "data": design_result.to_dict() if hasattr(design_result, "to_dict") else {},
                "ts": _ts_ms(),
            })
            await _complete_stage(
                stage_snapshot,
                summary=_extract_stage_done_message(design_result, STAGE_DESIGN_TESTCASES),
                duration_ms=duration_ms_design,
                progress=73,
                input_count=int(analysis_result.statistics.total_points or 0) if analysis_result else 0,
                output_count=int(design_result.statistics.total_cases or 0),
            )

        except Exception as e:
            await _fail_stage(
                stage_snapshot,
                summary=f"测试用例生成失败：{repr(e)}",
                extra={"error": repr(e)},
            )
            raise

        # 4) 用例评审（流式：每个模块评审完立即推给前端）
        await _check_cancel()
        stage_snapshot = await _start_stage(
            STAGE_REVIEW_TESTCASES,
            78,
            STAGE_SUBTITLES[STAGE_REVIEW_TESTCASES],
        )

        try:
            _req_summary_review = ""
            if result.requirement_analysis_result:
                _req_summary_review = result.requirement_analysis_result.summary or ""
            if not _req_summary_review and prepared_requirement:
                _req_summary_review = (prepared_requirement.final_text or "")[:2000]

            _review_issue_count: List[int] = [0]

            async def _on_review_module_done(
                module: str,
                issues: List,
                module_idx: int,
                total_modules: int,
            ) -> None:
                _review_issue_count[0] += len(issues)
                await emitter.send({
                    "type": EVENT_REVIEW_BATCH,
                    "stage": STAGE_REVIEW_TESTCASES,
                    "data": {
                        "module": module,
                        "issues": [i.to_dict() if hasattr(i, "to_dict") else {} for i in issues],
                        "module_idx": module_idx,
                        "total_modules": total_modules,
                        "cumulative_issues": _review_issue_count[0],
                    },
                    "ts": _ts_ms(),
                })
                span = max(0, 86 - 78)
                done_ratio = (module_idx + 1) / max(1, total_modules)
                progress = _clip_progress(78 + int(span * done_ratio * 0.9))
                stage_snapshot.progress = progress
                stage_snapshot.summary = (
                    f"已评审 {module}，发现 {len(issues)} 个问题"
                    f"（{module_idx + 1}/{total_modules} 模块）"
                )
                stage_snapshot.normalize()
                await emitter.stage_snapshot(stage_snapshot)

            t0_review = time.time()
            review_result: ReviewResult = await asyncio.wait_for(
                self.review_agent.run(
                    analysis_result=analysis_result,
                    design_result=design_result,
                    requirement_summary=_req_summary_review,
                    on_module_done=_on_review_module_done,
                ),
                timeout=LLM_TIMEOUT_REVIEW + 60,
            )
            duration_ms_review = _ms_since(t0_review)

            result.review_result = review_result
            ensure_pipeline_result_consistency(result)

            await emitter.send({
                "type": EVENT_REVIEW_RESULT,
                "stage": STAGE_REVIEW_TESTCASES,
                "data": review_result.to_dict() if hasattr(review_result, "to_dict") else {},
                "ts": _ts_ms(),
            })
            await _complete_stage(
                stage_snapshot,
                summary=_extract_stage_done_message(review_result, STAGE_REVIEW_TESTCASES),
                duration_ms=duration_ms_review,
                progress=86,
                input_count=int(design_result.statistics.total_cases or 0) if design_result else 0,
                output_count=int(review_result.issue_count or 0),
            )

        except Exception as e:
            await _fail_stage(
                stage_snapshot,
                summary=f"测试用例评审失败：{repr(e)}",
                extra={"error": repr(e)},
            )
            raise

        # 5) 优化测试用例（流式：每批精炼完立即推给前端）
        await _check_cancel()
        stage_snapshot = await _start_stage(
            STAGE_REFINE_TESTCASES,
            90,
            STAGE_SUBTITLES[STAGE_REFINE_TESTCASES],
        )

        try:
            _req_summary_refine = ""
            if result.requirement_analysis_result:
                _req_summary_refine = result.requirement_analysis_result.summary or ""
            if not _req_summary_refine and prepared_requirement:
                _req_summary_refine = (prepared_requirement.final_text or "")[:2000]

            _refine_case_count: List[int] = [0]

            async def _on_refine_batch_done(
                batch_cases: List[TestCase],
                batch_idx: int,
                total_batches: int,
            ) -> None:
                _refine_case_count[0] += len(batch_cases)
                await emitter.send({
                    "type": EVENT_REFINE_BATCH,
                    "stage": STAGE_REFINE_TESTCASES,
                    "data": {
                        "cases": [c.to_export_dict() for c in batch_cases],
                        "batch_idx": batch_idx,
                        "total_batches": total_batches,
                        "cumulative_count": _refine_case_count[0],
                    },
                    "ts": _ts_ms(),
                })
                span = max(0, 95 - 90)
                done_ratio = (batch_idx + 1) / max(1, total_batches)
                progress = _clip_progress(90 + int(span * done_ratio * 0.9))
                stage_snapshot.progress = progress
                stage_snapshot.summary = (
                    f"已优化 {_refine_case_count[0]} 条用例"
                    f"（第 {batch_idx + 1}/{total_batches} 批）"
                )
                stage_snapshot.normalize()
                await emitter.stage_snapshot(stage_snapshot)

            t0_refine = time.time()
            refine_result: RefineResult = await asyncio.wait_for(
                self.refine_agent.run(
                    analysis_result=analysis_result,
                    design_result=design_result,
                    review_result=review_result,
                    requirement_summary=_req_summary_refine,
                    on_batch_done=_on_refine_batch_done,
                ),
                timeout=LLM_TIMEOUT_REFINE + 60,
            )
            duration_ms_refine = _ms_since(t0_refine)

            result.refine_result = refine_result
            ensure_pipeline_result_consistency(result)

            await emitter.send({
                "type": EVENT_REFINE_RESULT,
                "stage": STAGE_REFINE_TESTCASES,
                "data": refine_result.to_dict() if hasattr(refine_result, "to_dict") else {},
                "ts": _ts_ms(),
            })
            await _complete_stage(
                stage_snapshot,
                summary=_extract_stage_done_message(refine_result, STAGE_REFINE_TESTCASES),
                duration_ms=duration_ms_refine,
                progress=95,
                input_count=int(design_result.statistics.total_cases or 0) if design_result else 0,
                output_count=int(refine_result.statistics.total_cases or 0),
            )

        except Exception as e:
            await _fail_stage(
                stage_snapshot,
                summary=f"优化测试用例失败：{repr(e)}",
                extra={"error": repr(e)},
            )
            raise

        # 6) 下载测试用例
        await _check_cancel()
        stage_snapshot = await _start_stage(
            STAGE_EXPORT_TESTCASES,
            97,
            STAGE_SUBTITLES[STAGE_EXPORT_TESTCASES],
        )

        try:
            final_cases = flatten_test_case_modules(refine_result.modules)

            export_task = asyncio.create_task(
                _export_testcases_to_excel(
                    requirement_id=requirement_id,
                    final_cases=final_cases,
                    owner=owner,
                )
            )

            export_heartbeat_step = 0
            export_t0 = time.time()

            while not export_task.done():
                await asyncio.sleep(PIPELINE_HEARTBEAT_INTERVAL_SEC)
                export_heartbeat_step += 1
                await _check_cancel()
                await _emit_stage_chat_tick(
                    stage=STAGE_EXPORT_TESTCASES,
                    snapshot=stage_snapshot,
                    base_progress=97,
                    max_progress=99,
                    step_no=export_heartbeat_step,
                )

            file_id, filename, download_url, export_error = await export_task
            duration_ms = _ms_since(export_t0)

            artifact = _make_export_artifact(
                ready=bool(file_id),
                file_id=file_id or "",
                filename=filename or "",
                excel_path="",
                json_path="",
                download_url=download_url or "",
                error=export_error or "",
            )
            result.artifact = artifact
            ensure_pipeline_result_consistency(result)

            await emitter.send({
                "type": EVENT_DOWNLOAD,
                "stage": STAGE_EXPORT_TESTCASES,
                "data": artifact.to_dict(),
                "ts": _ts_ms(),
            })
            await emitter.stage_content(
                StageContent(
                    stage=STAGE_EXPORT_TESTCASES,
                    title="导出结果",
                    module="",
                    content={
                        "ready": artifact.ready,
                        "filename": artifact.filename,
                        "download_url": artifact.download_url,
                        "error": artifact.error,
                    },
                    preview_lines=[
                        _export_done_message(artifact),
                        artifact.filename or "",
                    ],
                    updated_at=_ts_ms(),
                )
            )
            await _complete_stage(
                stage_snapshot,
                summary=_export_done_message(artifact),
                duration_ms=duration_ms,
                progress=99,
                extra={
                    "ready": artifact.ready,
                    "file_id": artifact.file_id,
                    "filename": artifact.filename,
                    "download_url": artifact.download_url,
                    "error": artifact.error,
                },
                input_count=len(final_cases),
                output_count=1 if artifact.ready else 0,
            )
        except Exception as e:
            await _fail_stage(
                stage_snapshot,
                summary=f"导出测试用例失败：{repr(e)}",
                extra={"error": repr(e)},
            )
            raise

        # 7) 完成
        finish_snapshot = StageSnapshot(
            key=STAGE_FINISHED,
            title=STAGE_TITLES[STAGE_FINISHED],
            status=STAGE_STATUS_COMPLETED,  # type: ignore[arg-type]
            summary="测试用例生成流程已完成。",
            progress=FINAL_STAGE_PROGRESS,
            started_at=_ts_ms(),
            finished_at=_ts_ms(),
            duration_ms=0,
            extra={
                "artifact_ready": bool(result.artifact.ready),
            },
        ).normalize()
        stage_snapshots.append(finish_snapshot)

        result.stage_snapshots = stage_snapshots
        result.stage_metrics = stage_metrics
        ensure_pipeline_result_consistency(result)

        if result.runtime_snapshot:
            result.runtime_snapshot.current_stage = STAGE_FINISHED  # type: ignore[assignment]
            result.runtime_snapshot.final_message = "测试用例已生成完成。"
            await emitter.runtime_snapshot(result.runtime_snapshot)

        await emitter.stage_snapshot(finish_snapshot)
        await emitter.send({
            "type": EVENT_FINAL_RESULT,
            "stage": STAGE_FINISHED,
            "data": result.to_dict(),
            "ts": _ts_ms(),
        })
        await emitter.send({
            "type": EVENT_FINAL_SUMMARY,
            "data": {
                "requirement_id": result.requirement_id,
                "total_points": result.final_summary.total_points,
                "draft_cases": result.final_summary.draft_cases,
                "total_cases": result.final_summary.total_cases,
                "review_issue_count": result.final_summary.review_issue_count,
                "covered_points": result.final_summary.covered_points,
                "uncovered_points": result.final_summary.uncovered_points,
                "coverage_rate": result.final_summary.coverage_rate,
                "total_duration_ms": result.final_summary.total_duration_ms,
                "stage_costs_ms": result.final_summary.stage_costs_ms,
                "artifact": result.artifact.to_dict(),
            },
            "ts": _ts_ms(),
        })
        return result


async def run_pipeline(
    stream_id: str,
    workflow_id: str,
    requirement_id: str,
    extra_requirement: Optional[str],
    emit: Callable[[str, Dict[str, Any]], Awaitable[None]],
    cancel_checker: Callable[[], Awaitable[bool]],
    owner: Optional[str] = None,
    *,
    pdf_path: Optional[str] = None,
    requirement_text: Optional[str] = None,
) -> None:
    pipe = TestcasePipeline()
    safe_requirement_id = _safe_str(requirement_id) or DEFAULT_REQUIREMENT_ID
    extra_requirement = extra_requirement or ""
    owner = _normalize_owner(owner)

    async def _emit_payload(payload: Dict[str, Any]) -> None:
        await emit(stream_id, payload)

    emitter = _Emitter(_emit_payload)

    try:
        logger.info(
            "[run_pipeline] start | stream_id=%s | workflow_id=%s | requirement_id=%s | extra_requirement_len=%s | has_pdf_path=%s | has_requirement_text=%s | owner=%s",
            stream_id,
            workflow_id,
            safe_requirement_id,
            len(extra_requirement or ""),
            bool(pdf_path),
            bool(requirement_text and requirement_text.strip()),
            owner or "",
        )

        read_started_at = _ts_ms()
        read_running = StageSnapshot(
            key=STAGE_READ_REQUIREMENT,
            title=STAGE_TITLES[STAGE_READ_REQUIREMENT],
            status=STAGE_STATUS_RUNNING,  # type: ignore[arg-type]
            summary=STAGE_SUBTITLES[STAGE_READ_REQUIREMENT],
            progress=16,
            started_at=read_started_at,
            finished_at=None,
            duration_ms=0,
            extra={},
        ).normalize()
        await emitter.stage_snapshot(read_running)
        await emitter.stage_content(
            StageContent(
                stage=STAGE_READ_REQUIREMENT,
                title="需求读取中",
                module="",
                content={
                    "kind": "heartbeat",
                    "message": STAGE_SUBTITLES[STAGE_READ_REQUIREMENT],
                    "progress": 16,
                },
                preview_lines=[STAGE_SUBTITLES[STAGE_READ_REQUIREMENT]],
                updated_at=_ts_ms(),
            )
        )

        t0 = time.time()
        prepared_requirement = await _load_prepared_requirement(
            workflow_id=workflow_id,
            requirement_id=safe_requirement_id,
            pdf_path=pdf_path,
            requirement_text=requirement_text,
        )
        prepared_requirement = _merge_extra_requirement(
            prepared_requirement,
            extra_requirement=extra_requirement,
        )

        validation_error = _validate_prepared_requirement(
            prepared_requirement,
            workflow_id=workflow_id,
            requirement_id=safe_requirement_id,
            has_pdf_path=bool(pdf_path),
            has_requirement_text=bool(requirement_text and requirement_text.strip()),
        )
        if validation_error:
            raise ValueError(validation_error)

        read_duration_ms = _ms_since(t0)
        read_running.status = STAGE_STATUS_COMPLETED  # type: ignore[assignment]
        read_running.summary = "需求读取完成。"
        read_running.message = "需求读取完成。"
        read_running.finished_at = _ts_ms()
        read_running.ended_at = read_running.finished_at
        read_running.duration_ms = read_duration_ms
        read_running.extra = {
            "requirement_id": safe_requirement_id,
            "text_length": len(prepared_requirement.final_text or ""),
            "usable_for_ai": prepared_requirement.usable_for_ai,
            "total_pages": prepared_requirement.total_pages,
            "extra_requirement_len": len(extra_requirement or ""),
        }
        read_running.normalize()

        read_metric = StageMetric(
            stage=STAGE_READ_REQUIREMENT,
            duration_ms=read_duration_ms,
            input_count=1,
            output_count=len(prepared_requirement.final_text or ""),
            extra=read_running.extra,
        ).normalize()

        await emitter.stage_metric(read_metric)
        await emitter.stage_snapshot(read_running)
        await emitter.stage_content(
            StageContent(
                stage=STAGE_READ_REQUIREMENT,
                title="需求读取结果",
                module="",
                content={
                    "text_length": len(prepared_requirement.final_text or ""),
                    "usable_for_ai": prepared_requirement.usable_for_ai,
                    "total_pages": prepared_requirement.total_pages,
                },
                preview_lines=[
                    "需求读取完成。",
                    f"正文长度：{len(prepared_requirement.final_text or '')}",
                    f"页数：{prepared_requirement.total_pages}",
                ],
                updated_at=_ts_ms(),
            )
        )

        result = await pipe.run_once(
            prepared_requirement=prepared_requirement,
            owner=owner,
            emit=_emit_payload,
            cancel_checker=cancel_checker,
            initial_stage_snapshots=[read_running],
            initial_stage_metrics=[read_metric],
        )

        ensure_pipeline_result_consistency(result)

        coverage_summary = build_coverage_summary(
            result.all_points(),
            result.final_cases(),
        )

        runtime = build_runtime_snapshot(result)
        await emitter.runtime_snapshot(runtime)
        await _emit_payload({
            "type": EVENT_FINAL_SUMMARY,
            "data": {
                "requirement_id": result.requirement_id,
                "total_points": result.final_summary.total_points,
                "draft_cases": result.final_summary.draft_cases,
                "total_cases": result.final_summary.total_cases,
                "review_issue_count": result.final_summary.review_issue_count,
                "covered_points": coverage_summary.covered_points,
                "uncovered_points": coverage_summary.uncovered_points,
                "coverage_rate": coverage_summary.coverage_rate,
                "total_duration_ms": result.final_summary.total_duration_ms,
                "stage_costs_ms": result.final_summary.stage_costs_ms,
                "artifact": result.artifact.to_dict(),
                "coverage_summary": coverage_summary.to_dict(),
            },
            "ts": _ts_ms(),
        })

        logger.info(
            "[run_pipeline] done | stream_id=%s | workflow_id=%s | requirement_id=%s | total_points=%s | total_cases=%s | review_issue_count=%s | artifact_ready=%s",
            stream_id,
            workflow_id,
            safe_requirement_id,
            result.final_summary.total_points,
            result.final_summary.total_cases,
            result.final_summary.review_issue_count,
            bool(result.artifact and result.artifact.ready),
        )

    except asyncio.CancelledError:
        logger.warning(
            "[run_pipeline] cancelled | stream_id=%s | workflow_id=%s | requirement_id=%s",
            stream_id,
            workflow_id,
            safe_requirement_id,
        )
        cancelled_snapshot = StageSnapshot(
            key=STAGE_FINISHED,
            title=STAGE_TITLES[STAGE_FINISHED],
            status=STAGE_STATUS_ERROR,  # type: ignore[arg-type]
            summary="任务已取消。",
            progress=FINAL_STAGE_PROGRESS,
            started_at=_ts_ms(),
            finished_at=_ts_ms(),
            duration_ms=0,
            extra={},
        ).normalize()
        await emitter.stage_snapshot(cancelled_snapshot)
        raise

    except Exception as e:
        logger.exception(
            "[run_pipeline] failed | stream_id=%s | workflow_id=%s | requirement_id=%s | err=%s",
            stream_id,
            workflow_id,
            safe_requirement_id,
            repr(e),
        )
        error_snapshot = StageSnapshot(
            key=STAGE_FINISHED,
            title=STAGE_TITLES[STAGE_FINISHED],
            status=STAGE_STATUS_ERROR,  # type: ignore[arg-type]
            summary=f"测试用例流水线执行失败：{repr(e)}",
            progress=FINAL_STAGE_PROGRESS,
            started_at=_ts_ms(),
            finished_at=_ts_ms(),
            duration_ms=0,
            extra={"error": repr(e)},
        ).normalize()
        await emitter.stage_snapshot(error_snapshot)
        await _emit_payload({
            "type": EVENT_ERROR,
            "stage": STAGE_FINISHED,
            "data": {
                "stage": STAGE_FINISHED,
                "message": f"测试用例流水线执行失败：{repr(e)}",
            },
            "ts": _ts_ms(),
        })
        raise
