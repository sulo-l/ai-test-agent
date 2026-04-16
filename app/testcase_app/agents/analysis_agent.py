#! /usr/bin/python3
# coding=utf-8
# app/testcase_app/agents/analysis_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Union

from app.llm.client import LLM
from app.services.requirement_preparer import PreparedRequirement
from app.testcase_app.models import (
    AnalysisResult,
    PreparedRequirementSummary,
    RequirementPage,
    TestPoint,
    build_test_point_statistics,
    group_points_by_module,
)
from app.testcase_app.protocols import (
    emit_analysis_result,
    emit_progress,
    emit_stage_completed,
    emit_stage_start,
)

try:
    from app.testcase_app.utils.chunker import smart_split_text
except Exception:  # pragma: no cover
    smart_split_text = None  # type: ignore


class AnalysisAgent:
    """
    企业级测试点分析 Agent

    核心策略：
    1. 输入需求文本 / PreparedRequirement
    2. 抽取语义锚点（模块 / 对象 / 字段 / 动作 / 状态 / 角色 / 约束 / UI词）
    3. 先做结构化规则生成（稳定、可控）
    4. 再做 LLM 补充生成（提高覆盖）
    5. 统一做去重、泛化过滤、相关性过滤、标题强化
    6. 输出唯一合法的新模型 AnalysisResult
    """

    _ALLOWED_POINT_TYPES = {"normal", "exception", "boundary"}
    _ALLOWED_PRIORITIES = {"P0", "P1", "P2", "P3"}

    _GENERIC_TITLE_PATTERNS = [
        "功能正常",
        "流程正常",
        "页面正常",
        "系统正常",
        "可正常使用",
        "正确执行",
        "正确生效",
        "处理正确",
        "展示正确",
        "反馈正确",
        "校验正确",
        "流程可完成",
        "主流程可完成",
        "关键流程正确",
        "逻辑正确",
        "业务正确",
        "结果正确",
        "场景正确",
        "功能验证",
        "流程验证",
        "页面验证",
        "测试验证",
        "基础验证",
        "通用验证",
        "通用异常处理",
        "基础功能验证",
        "常规流程验证",
        "兜底场景验证",
        "基础场景验证",
    ]

    _GENERIC_DETAIL_PATTERNS = [
        "验证系统处理是否正确",
        "验证功能是否正常",
        "验证流程是否正常",
        "验证页面展示是否正确",
        "系统应正确处理",
        "页面应正确展示",
        "结果应正确",
        "验证功能可正常使用",
        "验证业务流程可正常执行",
        "验证逻辑正确",
        "检查功能是否正常",
        "检查页面是否正常",
        "检查流程是否正常",
    ]

    _LOW_VALUE_WORDS = {
        "功能", "流程", "系统", "页面", "规则", "场景", "处理", "校验", "验证",
        "正确", "成功", "失败", "操作", "结果", "反馈", "展示", "业务", "逻辑",
        "支持", "需要", "可以", "应当", "应该", "进行", "完成", "实现", "功能点",
        "相关", "对应", "内容", "情况", "能力", "问题", "信息", "数据", "默认",
        "基础", "通用", "常规", "整体", "正常", "异常", "边界",
    }

    _TITLE_BAD_PREFIXES = ("验证", "检查", "确认", "测试")
    _TITLE_BAD_SUFFIXES = (
        "是否正确", "是否正常", "正确性", "功能验证", "流程验证", "页面验证",
        "逻辑验证", "结果验证", "场景验证",
    )

    _COMMON_STATE_WORDS = {
        "开启", "关闭", "启用", "禁用", "成功", "失败", "待处理", "处理中",
        "待审核", "审核中", "已通过", "已拒绝", "已完成", "已取消",
        "已提交", "已保存", "已删除", "已生效", "未生效", "草稿", "冻结",
        "锁定", "解锁", "可编辑", "不可编辑", "可见", "不可见",
    }

    _COMMON_ROLE_WORDS = {
        "管理员", "普通用户", "访客", "未登录", "登录用户", "审核人",
        "操作人", "提交人", "审批人", "拥有权限用户", "无权限用户",
        "创建人", "维护人", "负责人",
    }

    _COMMON_ACTION_WORDS = {
        "点击", "选择", "输入", "提交", "保存", "确认", "删除", "编辑", "修改",
        "切换", "查询", "搜索", "筛选", "导出", "导入", "上传", "下载", "创建",
        "新增", "关闭", "开启", "启用", "禁用", "查看", "进入", "打开", "刷新",
        "提交审核", "撤回", "审批", "驳回", "复制", "排序", "分页",
    }

    _P0_HINT_WORDS = {
        "资金", "余额", "资产", "权益", "下单", "成交", "扣减", "划转", "支付",
        "订单", "结算", "风控", "权限", "审批", "删除", "生效", "状态流转",
    }

    _P1_HINT_WORDS = {
        "创建", "新增", "编辑", "修改", "保存", "提交", "查询", "筛选", "导出",
        "导入", "展示", "列表", "详情", "审核", "配置", "校验",
    }

    _UI_HINT_WORDS = {
        "按钮", "弹窗", "列表", "详情", "下拉框", "输入框", "复选框", "单选框",
        "tab", "Tab", "标签页", "图表", "表格", "筛选器", "搜索框", "提示语",
        "toast", "Toast", "页面", "卡片", "开关",
    }

    def __init__(
        self,
        llm: Optional[LLM] = None,
        *,
        max_points: int = 160,
        points_per_chunk: int = 8,
        max_chunks: int = 16,
        chunk_min_chars: int = 350,
        chunk_max_chars: int = 1400,
        dedup: bool = True,
        timeout: int = 180,
        strict_relevance_filter: bool = True,
    ) -> None:
        self.llm = llm or LLM()
        self.max_points = max(1, int(max_points))
        self.points_per_chunk = max(1, int(points_per_chunk))
        self.max_chunks = max(1, int(max_chunks))
        self.chunk_min_chars = max(100, int(chunk_min_chars))
        self.chunk_max_chars = max(self.chunk_min_chars, int(chunk_max_chars))
        self.dedup = bool(dedup)
        self.timeout = max(30, int(timeout))
        self.strict_relevance_filter = bool(strict_relevance_filter)

    # =========================================================
    # Public API
    # =========================================================

    def run(
        self,
        *,
        prepared_requirement: Union[PreparedRequirement, PreparedRequirementSummary, str],
        emit: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> AnalysisResult:
        prepared = self._ensure_prepared_requirement(prepared_requirement)
        requirement_text = self._extract_requirement_text(prepared)
        if not requirement_text:
            raise ValueError("requirement_text is empty")

        emit_stage_start(
            emit,
            stage="ANALYZE_TEST_POINTS",
            message="开始分析需求并提取测试点",
        )

        user_requirement = self._extract_user_test_requirement(prepared_requirement)
        merged_requirement_text = self._merge_requirement_with_user_requirement(
            requirement_text=requirement_text,
            user_requirement=user_requirement,
        )

        default_module = self._infer_global_module(requirement_text) or "整体功能"
        anchors = self._extract_requirement_anchors(
            requirement_text=requirement_text,
            user_requirement=user_requirement,
            default_module=default_module,
        )

        emit_progress(
            emit,
            current=1,
            total=4,
            stage="ANALYZE_TEST_POINTS",
            message="已完成需求语义锚点提取",
        )

        # 1) 先做结构化生成，保证稳定质量
        structured_points = self._build_structured_points(
            requirement_text=requirement_text,
            user_requirement=user_requirement,
            default_module=default_module,
            anchors=anchors,
        )

        emit_progress(
            emit,
            current=2,
            total=4,
            stage="ANALYZE_TEST_POINTS",
            message=f"已生成结构化测试点 {len(structured_points)} 个",
        )

        # 2) 再做分块 LLM 补充，补遗漏场景
        chunks = self._chunk(merged_requirement_text)
        llm_points: List[TestPoint] = []

        for idx, chunk in enumerate(chunks, 1):
            emit_progress(
                emit,
                current=idx,
                total=len(chunks),
                stage="ANALYZE_TEST_POINTS",
                message=f"正在分析第 {idx}/{len(chunks)} 个需求块",
            )

            chunk_points = self._analyze_chunk(
                chunk_text=chunk["text"],
                chunk_title=chunk["title"],
                default_module=default_module,
                requirement_text=requirement_text,
                user_requirement=user_requirement,
                anchors=anchors,
            )
            llm_points.extend(chunk_points)

        emit_progress(
            emit,
            current=3,
            total=4,
            stage="ANALYZE_TEST_POINTS",
            message=f"已补充生成候选测试点 {len(llm_points)} 个",
        )

        # 3) 合并、过滤、去重
        all_points = structured_points + llm_points
        points = self._post_filter_points(
            points=all_points,
            requirement_text=requirement_text,
            user_requirement=user_requirement,
            default_module=default_module,
            anchors=anchors,
        )

        points = self._sort_points(points)
        points = self._renumber_points(points)
        modules = group_points_by_module(points)
        statistics = build_test_point_statistics(modules)

        result = AnalysisResult(
            summary=f"测试点分析完成，共生成 {statistics.total_points} 个测试点，覆盖 {statistics.total_modules} 个模块。",
            requirement_id=self._extract_requirement_id(prepared_requirement),
            modules=modules,
            statistics=statistics,
        )

        emit_analysis_result(emit, result=result)
        emit_stage_completed(
            emit,
            stage="ANALYZE_TEST_POINTS",
            message=result.summary,
            progress=100,
            extra={
                "total_points": statistics.total_points,
                "total_modules": statistics.total_modules,
                "normal_count": statistics.normal_count,
                "exception_count": statistics.exception_count,
                "boundary_count": statistics.boundary_count,
            },
        )
        return result

    # =========================================================
    # Requirement adapter
    # =========================================================

    def _ensure_prepared_requirement(
        self,
        requirement_input: Union[PreparedRequirement, PreparedRequirementSummary, str],
    ) -> PreparedRequirementSummary:
        if isinstance(requirement_input, PreparedRequirementSummary):
            return requirement_input

        if isinstance(requirement_input, PreparedRequirement):
            pages: List[RequirementPage] = []
            raw_pages = getattr(requirement_input, "pages", None) or []
            for item in raw_pages:
                if isinstance(item, dict):
                    page_text = str(item.get("confirmed_text") or item.get("text") or "").strip()
                    pages.append(
                        RequirementPage(
                            page_no=int(item.get("page") or 0),
                            text=page_text,
                            text_length=len(page_text),
                            source=str(item.get("source") or ""),
                            image_like=bool(item.get("image_like", False)),
                        )
                    )

            return PreparedRequirementSummary(
                requirement_id=str(getattr(requirement_input, "requirement_id", "") or ""),
                title="",
                source_file_name="",
                final_text=str(getattr(requirement_input, "final_text", "") or ""),
                clean_blocks=list(getattr(requirement_input, "requirement_blocks", None) or []),
                pages=pages,
                total_pages=int(getattr(requirement_input, "total_pages", 0) or 0),
                usable_for_ai=bool(getattr(requirement_input, "usable_for_ai", True)),
            )

        if isinstance(requirement_input, str):
            text = requirement_input.strip()
            return PreparedRequirementSummary(
                requirement_id="",
                title="",
                source_file_name="",
                final_text=text,
                clean_blocks=[text] if text else [],
                pages=[],
                total_pages=0,
                usable_for_ai=bool(text),
            )

        raise TypeError("prepared_requirement must be PreparedRequirement | PreparedRequirementSummary | str")

    def _extract_requirement_text(self, prepared: PreparedRequirementSummary) -> str:
        text = (prepared.final_text or "").strip()
        if text:
            return text

        if prepared.clean_blocks:
            merged = "\n\n".join([x for x in prepared.clean_blocks if str(x).strip()])
            return merged.strip()

        if prepared.pages:
            merged = "\n\n".join([x.text for x in prepared.pages if x.text.strip()])
            return merged.strip()

        return ""

    def _extract_requirement_id(
        self,
        prepared_requirement: Union[PreparedRequirement, PreparedRequirementSummary, str],
    ) -> str:
        if isinstance(prepared_requirement, PreparedRequirementSummary):
            return prepared_requirement.requirement_id or ""
        if isinstance(prepared_requirement, PreparedRequirement):
            return str(getattr(prepared_requirement, "requirement_id", "") or "")
        return ""

    # =========================================================
    # 用户补充测试要求
    # =========================================================

    def _extract_user_test_requirement(
        self,
        prepared_requirement: Union[PreparedRequirement, PreparedRequirementSummary, str],
    ) -> str:
        if isinstance(prepared_requirement, str):
            return ""

        candidate_keys = [
            "extra_requirement",
            "extra_requirements",
            "testing_requirement",
            "testing_requirements",
            "test_requirement",
            "test_requirements",
            "user_requirement",
            "user_requirements",
            "supplement_requirement",
            "supplement_requirements",
            "additional_requirement",
            "additional_requirements",
        ]

        for key in candidate_keys:
            value = getattr(prepared_requirement, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()

        for key in ("metadata", "extra", "context", "ext"):
            obj = getattr(prepared_requirement, key, None)
            if isinstance(obj, dict):
                for sub_key in candidate_keys:
                    value = obj.get(sub_key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()

        # 兼容把补充测试要求直接写入 final_text 的场景
        if isinstance(prepared_requirement, PreparedRequirementSummary):
            merged_text = (prepared_requirement.final_text or "").strip()
            m = re.search(r"【补充测试要求】\s*([\s\S]{1,1200})", merged_text)
            if m:
                extra = m.group(1).strip()
                # 避免把整个原文都吃进去
                extra = extra.split("【原始需求文档】")[0].strip()
                return extra

        return ""

    def _merge_requirement_with_user_requirement(
        self,
        *,
        requirement_text: str,
        user_requirement: str,
    ) -> str:
        requirement_text = (requirement_text or "").strip()
        user_requirement = (user_requirement or "").strip()
        if not user_requirement:
            return requirement_text

        return (
            "【用户补充测试要求（最高优先级，必须优先覆盖）】\n"
            f"{user_requirement}\n\n"
            "【原始需求文档】\n"
            f"{requirement_text}"
        ).strip()

    # =========================================================
    # Structured generation
    # =========================================================

    def _build_structured_points(
        self,
        *,
        requirement_text: str,
        user_requirement: str,
        default_module: str,
        anchors: Dict[str, Any],
    ) -> List[TestPoint]:
        modules = anchors.get("modules") or [default_module]
        objects = anchors.get("objects") or []
        actions = anchors.get("actions") or []
        states = anchors.get("states") or []
        roles = anchors.get("roles") or []
        fields = anchors.get("fields") or []
        constraints = anchors.get("constraints") or []
        ui_terms = anchors.get("ui_terms") or []

        chosen_modules = modules[:4] if modules else [default_module]
        chosen_objects = objects[:12]
        chosen_actions = actions[:8]
        chosen_states = states[:6]
        chosen_roles = roles[:4]
        chosen_fields = fields[:8]
        chosen_constraints = constraints[:8]
        chosen_ui_terms = ui_terms[:6]

        points: List[TestPoint] = []

        # A. 对象 + 动作 主流程
        for module in chosen_modules:
            for obj in chosen_objects[:8]:
                for act in chosen_actions[:5]:
                    if self._is_action_too_generic(act):
                        continue
                    title = self._compose_title(
                        module=module,
                        obj=obj,
                        action=act,
                        state="",
                        assert_target=self._guess_assert_target(obj, act),
                    )
                    point = self._make_structured_point(
                        module=module,
                        point_type="normal",
                        title=title,
                        objective=f"验证{obj}在执行“{act}”时流程正确、结果可校验。",
                        preconditions=self._build_preconditions(module, obj, "", ""),
                        inputs=[f"执行{act}相关操作"],
                        check_items=self._build_check_items(obj, act, chosen_fields, chosen_ui_terms),
                        expected_results=self._build_expected_results(obj, act, "", chosen_constraints),
                        refs=self._build_refs(requirement_text, user_requirement, obj, act, module),
                    )
                    points.append(point)
                    if len(points) >= self.max_points:
                        return points

        # B. 对象 + 状态（正向）
        for module in chosen_modules:
            for obj in chosen_objects[:8]:
                for state in chosen_states[:4]:
                    title = self._compose_title(
                        module=module,
                        obj=obj,
                        action="状态变化",
                        state=state,
                        assert_target="结果与状态展示",
                    )
                    point = self._make_structured_point(
                        module=module,
                        point_type="normal",
                        title=title,
                        objective=f"验证{obj}处于「{state}」状态时展示、操作能力及结果是否符合预期。",
                        preconditions=self._build_preconditions(module, obj, state, ""),
                        inputs=[f"将{obj}置于{state}状态"],
                        check_items=self._build_state_check_items(obj, state, chosen_fields),
                        expected_results=self._build_expected_results(obj, "状态变化", state, chosen_constraints),
                        refs=self._build_refs(requirement_text, user_requirement, obj, state, module),
                    )
                    points.append(point)
                    if len(points) >= self.max_points:
                        return points

        # B2. 对象 + 状态切换（逆向：相邻状态对的反向切换）
        if len(chosen_states) >= 2:
            for module in chosen_modules[:2]:
                for obj in chosen_objects[:4]:
                    for i in range(min(len(chosen_states) - 1, 3)):
                        from_state = chosen_states[i + 1]
                        to_state = chosen_states[i]
                        title = f"{obj}从{from_state}切换回{to_state}后状态正确回退"
                        point = self._make_structured_point(
                            module=module,
                            point_type="normal",
                            title=title,
                            objective=f"验证{obj}从「{from_state}」逆向切换回「{to_state}」时，状态正确回退且展示与功能符合预期。",
                            preconditions=self._build_preconditions(module, obj, from_state, ""),
                            inputs=[f"将{obj}从{from_state}切换到{to_state}"],
                            check_items=self._build_state_check_items(obj, to_state, chosen_fields),
                            expected_results=self._build_expected_results(obj, "状态切换", to_state, chosen_constraints),
                            refs=self._build_refs(requirement_text, user_requirement, obj, to_state, module),
                        )
                        points.append(point)
                        if len(points) >= self.max_points:
                            return points

        # C. 权限/角色
        for module in chosen_modules:
            for role in chosen_roles[:3]:
                for obj in chosen_objects[:5]:
                    title = self._compose_title(
                        module=module,
                        obj=obj,
                        action="访问或操作",
                        state=role,
                        assert_target="权限控制",
                    )
                    point = self._make_structured_point(
                        module=module,
                        point_type="exception",
                        title=title,
                        objective=f"验证{role}对{obj}执行访问或操作时的权限限制是否符合需求。",
                        preconditions=[f"当前用户角色为{role}"],
                        inputs=[f"尝试访问或操作{obj}"],
                        check_items=["权限控制", "拦截提示", "操作结果"],
                        expected_results=["权限符合需求定义", "无权限时被拦截并给出明确提示"],
                        refs=self._build_refs(requirement_text, user_requirement, role, obj, "权限", module),
                    )
                    points.append(point)
                    if len(points) >= self.max_points:
                        return points

        # D. 字段 / 约束 / 边界
        for module in chosen_modules:
            for field in chosen_fields[:6]:
                if self._looks_like_noise_token(field):
                    continue
                title = self._compose_title(
                    module=module,
                    obj=field,
                    action="字段校验",
                    state="",
                    assert_target="输入约束",
                )
                point = self._make_structured_point(
                    module=module,
                    point_type="boundary",
                    title=title,
                    objective=f"验证字段“{field}”在边界值、空值、非法值场景下的校验行为。",
                    preconditions=[f"进入{module}相关操作页面"],
                    inputs=[f"输入{field}的边界值/非法值/空值"],
                    check_items=["前端校验", "提交行为", "错误提示"],
                    expected_results=["不合法输入被正确拦截", "提示清晰且位置合理"],
                    refs=self._build_refs(requirement_text, user_requirement, field, "校验", module),
                )
                points.append(point)
                if len(points) >= self.max_points:
                    return points

        # E. 约束规则
        for module in chosen_modules:
            for constraint in chosen_constraints[:6]:
                if len(constraint) < 3:
                    continue
                title = self._compose_title(
                    module=module,
                    obj=module,
                    action="规则约束校验",
                    state="",
                    assert_target=constraint[:18],
                )
                point = self._make_structured_point(
                    module=module,
                    point_type="exception",
                    title=title,
                    objective=f"验证业务约束“{constraint}”在触发时是否正确生效。",
                    preconditions=[f"满足触发约束“{constraint}”的前置条件"],
                    inputs=["执行相关业务操作"],
                    check_items=["约束触发", "拦截/提示", "结果状态"],
                    expected_results=["约束按需求生效", "不满足条件时不允许继续执行或结果被正确限制"],
                    refs=self._build_refs(requirement_text, user_requirement, constraint, module),
                )
                points.append(point)
                if len(points) >= self.max_points:
                    return points

        # F. 持久化 / 重进入验证
        # 如果动作中有"保存/提交/生效/确认"类词汇，生成对应的持久化验证测试点
        _PERSIST_ACTIONS = {"保存", "提交", "生效", "确认", "设置", "配置", "更新", "修改"}
        persist_actions = [a for a in chosen_actions if any(w in a for w in _PERSIST_ACTIONS)]
        if persist_actions:
            for module in chosen_modules[:2]:
                for obj in chosen_objects[:3]:
                    act = persist_actions[0]
                    title = f"{obj}{act}后重新进入页面验证配置持久化"
                    point = self._make_structured_point(
                        module=module,
                        point_type="normal",
                        title=title,
                        objective=f"验证{obj}执行「{act}」操作后，刷新页面或重新进入，配置能正确持久化回显。",
                        preconditions=[f"已登录系统，完成{obj}的{act}操作", "页面显示操作成功"],
                        inputs=["刷新当前页面或重新进入功能页"],
                        check_items=["数据持久化", "配置回显", "状态保持"],
                        expected_results=[f"重新进入后，{obj}的配置/状态与{act}时设置的值一致，未回退到默认值"],
                        refs=self._build_refs(requirement_text, user_requirement, obj, act, module),
                    )
                    points.append(point)
                    if len(points) >= self.max_points:
                        return points

        # G. 跨模块 / 全局一致性验证
        # 如果需求文本含"全局"/"同步"/"影响"等词，生成跨模块一致性测试点
        _GLOBAL_KEYWORDS = ["全局", "同步", "影响全部", "所有模块", "全部生效", "跨模块", "统一"]
        has_global = any(w in requirement_text for w in _GLOBAL_KEYWORDS)
        if has_global and len(chosen_modules) >= 1:
            for obj in chosen_objects[:2]:
                for act in (persist_actions or chosen_actions)[:1]:
                    title = f"{obj}在模块A执行{act}后切换到其他模块验证全局同步"
                    point = self._make_structured_point(
                        module=chosen_modules[0],
                        point_type="normal",
                        title=title,
                        objective=f"验证{obj}的「{act}」操作具有全局效果，切换到其他页面/模块后变更同步生效，不需要重复操作。",
                        preconditions=[f"已登录系统，进入{chosen_modules[0]}功能页面"],
                        inputs=[f"在当前模块执行{obj}的{act}操作", "切换到其他模块或页面"],
                        check_items=["全局同步", "跨模块一致性", "无需重复操作"],
                        expected_results=[f"切换模块后，{obj}的状态/配置与{act}时一致，无需重新操作即可生效"],
                        refs=self._build_refs(requirement_text, user_requirement, obj, act, chosen_modules[0]),
                    )
                    points.append(point)
                    if len(points) >= self.max_points:
                        return points

        return points

    def _make_structured_point(
        self,
        *,
        module: str,
        point_type: str,
        title: str,
        objective: str,
        preconditions: List[str],
        inputs: List[str],
        check_items: List[str],
        expected_results: List[str],
        refs: List[str],
    ) -> TestPoint:
        priority = self._decide_priority(title=title, objective=objective, refs=refs)

        point = TestPoint(
            point_id="",
            module=self._normalize_module(module, module),
            scenario_type=point_type,  # type: ignore[arg-type]
            point_type=point_type,      # type: ignore[arg-type]
            title=self._refine_title(title),
            objective=self._refine_detail(objective),
            preconditions=self._uniq_str_list(preconditions, max_count=6),
            inputs=self._uniq_str_list(inputs, max_count=6),
            check_items=self._uniq_str_list(check_items, max_count=8),
            expected_direction=self._uniq_str_list(expected_results, max_count=8),
            expected_results=self._uniq_str_list(expected_results, max_count=8),
            priority=priority,          # type: ignore[arg-type]
            priority_hint=priority,     # type: ignore[arg-type]
            tags=["功能测试" if point_type == "normal" else ("异常测试" if point_type == "exception" else "边界测试")],
            source_requirement_refs=self._uniq_str_list(refs, max_count=3),
            requirement_evidence=self._uniq_str_list(refs, max_count=3),
            notes=[],
        ).normalize()

        return self._force_point_structure(point)

    def _compose_title(
        self,
        *,
        module: str,
        obj: str,
        action: str,
        state: str,
        assert_target: str,
    ) -> str:
        parts: List[str] = []
        if module and obj and obj not in module:
            parts.append(module)
        if obj:
            parts.append(obj)
        if state:
            parts.append(f"{state}场景")
        if action:
            parts.append(action)
        if assert_target:
            parts.append(assert_target)
        title = " ".join([x for x in parts if x]).strip()
        return title or f"{module or obj or '业务对象'} 核心逻辑校验"

    def _guess_assert_target(self, obj: str, action: str) -> str:
        merged = f"{obj}{action}"
        if any(k in merged for k in ("保存", "创建", "新增", "编辑", "修改", "提交")):
            return "结果落库与回显"
        if any(k in merged for k in ("删除", "关闭", "禁用")):
            return "结果状态与提示"
        if any(k in merged for k in ("查询", "筛选", "搜索")):
            return "结果准确性"
        if any(k in merged for k in ("导出", "下载")):
            return "结果文件与内容"
        if any(k in merged for k in ("审批", "审核", "通过", "驳回")):
            return "状态流转与权限"
        return "结果与展示"

    def _build_preconditions(self, module: str, obj: str, state: str, role: str) -> List[str]:
        preconditions = [f"进入{module}相关功能范围"]
        if obj:
            preconditions.append(f"存在与{obj}相关的可操作数据或页面入口")
        if state:
            preconditions.append(f"{obj or '对象'}处于{state}状态")
        if role:
            preconditions.append(f"当前用户角色为{role}")
        return preconditions

    def _build_check_items(
        self,
        obj: str,
        action: str,
        fields: List[str],
        ui_terms: List[str],
    ) -> List[str]:
        items = [f"{obj}执行{action}后的结果"]
        if fields:
            items.append(f"关键字段：{fields[0]}")
        if len(fields) > 1:
            items.append(f"相关字段联动：{fields[1]}")
        if ui_terms:
            items.append(f"界面反馈：{ui_terms[0]}")
        items.append("列表/详情/状态同步")
        return items

    def _build_state_check_items(self, obj: str, state: str, fields: List[str]) -> List[str]:
        items = [f"{obj}在{state}状态下的可操作性", f"{obj}在{state}状态下的展示内容"]
        if fields:
            items.append(f"{fields[0]}字段展示/计算")
        return items

    def _build_expected_results(
        self,
        obj: str,
        action: str,
        state: str,
        constraints: List[str],
    ) -> List[str]:
        results = [f"{obj}相关结果符合需求定义"]
        if state:
            results.append(f"{obj}在{state}状态下的展示与操作限制符合预期")
        if action:
            results.append(f"{action}后的状态、提示及数据结果一致")
        if constraints:
            results.append(f"相关约束“{constraints[0]}”正确生效")
        return results

    def _build_refs(
        self,
        requirement_text: str,
        user_requirement: str,
        *keywords: str,
    ) -> List[str]:
        merged = "\n".join([requirement_text or "", user_requirement or ""]).strip()
        refs: List[str] = []
        for kw in keywords:
            if not kw:
                continue
            excerpt = self._find_best_excerpt(merged, kw)
            if excerpt:
                refs.append(excerpt)
        return refs[:3]

    def _find_best_excerpt(self, text: str, keyword: str) -> str:
        s = (text or "").strip()
        kw = (keyword or "").strip()
        if not s or not kw:
            return ""

        lines = [x.strip() for x in re.split(r"[\n。；;]+", s) if x.strip()]
        for line in lines:
            if kw in line:
                return line[:120]
        return ""

    # =========================================================
    # Chunk analysis (LLM supplement)
    # =========================================================

    def _analyze_chunk(
        self,
        *,
        chunk_text: str,
        chunk_title: str,
        default_module: str,
        requirement_text: str,
        user_requirement: str,
        anchors: Dict[str, Any],
    ) -> List[TestPoint]:
        prompt = self._build_chunk_prompt(
            chunk_title=chunk_title,
            chunk_text=chunk_text,
            default_module=default_module,
            user_requirement=user_requirement,
            anchors=anchors,
        )

        parsed: List[Dict[str, Any]] = []
        try:
            data = self.llm.call_json(
                prompt=prompt,
                timeout=self.timeout,
                agent_type="analysis",
            )
            if isinstance(data, dict):
                for key in ("items", "data", "test_points", "points", "result"):
                    value = data.get(key)
                    if isinstance(value, list):
                        parsed = [x for x in value if isinstance(x, dict)]
                        break
            elif isinstance(data, list):
                parsed = [x for x in data if isinstance(x, dict)]
        except Exception:
            parsed = []

        if not parsed:
            raw = ""
            try:
                raw = self.llm.call(
                    prompt=prompt,
                    timeout=self.timeout,
                    agent_type="analysis",
                )
            except Exception:
                raw = ""
            parsed = self._parse_points(raw)

        result: List[TestPoint] = []

        for item in parsed:
            point = self._normalize_point(
                item=item,
                default_source=chunk_title,
                default_module=default_module,
            )
            point = self._force_point_structure(point)
            if not self._accept_final_point(
                point=point,
                requirement_text=requirement_text,
                user_requirement=user_requirement,
                anchors=anchors,
            ):
                continue
            result.append(point)

        return result[: self.points_per_chunk * 4]

    def _build_chunk_prompt(
        self,
        *,
        chunk_title: str,
        chunk_text: str,
        default_module: str,
        user_requirement: str,
        anchors: Dict[str, Any],
    ) -> str:
        anchor_modules = ", ".join(anchors.get("modules") or []) or "无"
        anchor_objects = ", ".join(anchors.get("objects") or []) or "无"
        anchor_fields = ", ".join(anchors.get("fields") or []) or "无"
        anchor_actions = ", ".join(anchors.get("actions") or []) or "无"
        anchor_states = ", ".join(anchors.get("states") or []) or "无"
        anchor_roles = ", ".join(anchors.get("roles") or []) or "无"
        anchor_constraints = ", ".join(anchors.get("constraints") or []) or "无"
        user_req_block = user_requirement.strip() or "无"

        return f"""
你是资深测试分析专家。请严格基于需求原文，提取"高质量、可执行、可继续展开成测试用例"的测试点。

【基础要求】
1. 严格基于原文，不要脑补需求中没有的业务，不要擅自补充现货/合约/APP/H5/iOS/Android 等未出现信息
2. 每个测试点必须具体，不允许写泛化标题
3. 标题必须尽量体现：对象 + 动作/校验点 + 条件/状态（如适用）
4. 不要输出"功能正常/流程验证/页面验证/系统处理正确"这种空泛标题
5. 不要把需求ID、文档编号、PRD编号直接写进标题或目标
6. 优先输出：主流程、异常流程、边界值、状态流转、权限、数据联动
7. 只输出 JSON 对象，不要解释，不要 markdown

【等价类与枚举值覆盖 ★ 核心要求】
8. 如果需求中包含枚举选项（如"样式有A/B/C"、"类型分为X/Y/Z"、"状态包括待审核/审核中/已完成"），
   必须为每个独立枚举值分别生成一个测试点，绝对不能只取第一个示例
9. 对于"选择X后执行操作"类功能，必须覆盖所有枚举值，如"选A后全局生效"、"选B后全局生效"均需独立测试点
10. 对于"切换/选择"类操作，必须覆盖：选择每个值的正常路径 + 从已选值切换到其他值的路径

【逆向操作与状态双向覆盖 ★】
11. 状态切换必须双向测试：若"A→B"是一个测试点，"B→A"的逆向切换也必须是独立测试点
12. 对于"开启/关闭"、"启用/禁用"、"显示/隐藏"类功能，正向和逆向各需一个测试点
13. 对于"选中后取消选中"、"配置后恢复默认"类操作，必须生成逆向操作的测试点

【持久化与重新进入验证 ★】
14. 对于任何"保存/提交/生效"操作，必须额外生成一个"操作成功后重新进入页面/刷新页面，验证配置是否持久化"的测试点
15. 对于涉及"本地存储/缓存/用户配置"的功能，必须生成"清除缓存后重新进入"和"不同账号间配置隔离"的测试点

【跨模块一致性验证 ★】
16. 如果需求描述的功能说明"全局生效"或"影响多处/多模块"，必须生成"在模块A操作后，切换到模块B验证是否同步生效"的测试点
17. 对于"用户配置/偏好设置"类功能，必须生成"当前用户配置不影响其他用户"的账号隔离测试点
18. 对于"列表+详情"类功能，必须生成"列表数据与详情数据一致性"的测试点

输出 JSON 对象，结构如下：
{{
  "items": [
    {{
      "module": "{default_module}",
      "point_type": "normal|exception|boundary",
      "title": "测试点标题",
      "objective": "该测试点的测试目标",
      "preconditions": ["前置条件1"],
      "inputs": ["输入/操作条件1"],
      "check_items": ["检查项1"],
      "expected_results": ["预期结果1"],
      "priority": "P0|P1|P2|P3",
      "tags": ["功能测试"],
      "source_requirement_refs": ["需求原文中的关键语句"],
      "notes": ["补充说明"]
    }}
  ]
}}

禁止输出：
- 功能正常、页面正常、流程正常、系统处理正确、展示正确、通用验证、基础功能验证

语义锚点：
- 模块：{anchor_modules}
- 业务对象：{anchor_objects}
- 字段：{anchor_fields}
- 动作：{anchor_actions}
- 状态：{anchor_states}
- 角色：{anchor_roles}
- 规则/约束：{anchor_constraints}

用户补充测试要求：
{user_req_block}

当前需求块标题：
{chunk_title}

当前需求块原文：
{chunk_text[:6000]}
""".strip()

    # =========================================================
    # Anchor extraction
    # =========================================================

    def _extract_requirement_anchors(
        self,
        *,
        requirement_text: str,
        user_requirement: str,
        default_module: str,
    ) -> Dict[str, Any]:
        merged = f"{requirement_text}\n{user_requirement}".strip()
        ai_anchors = self._extract_ai_anchors(merged)
        lex_anchors = self._extract_lexical_anchors(merged)

        modules = self._merge_anchor_list(
            ai_anchors.get("modules", []),
            lex_anchors.get("modules", []),
            [default_module],
            max_count=12,
        )
        objects = self._merge_anchor_list(
            ai_anchors.get("objects", []),
            lex_anchors.get("objects", []),
            max_count=20,
        )
        fields = self._merge_anchor_list(
            ai_anchors.get("fields", []),
            lex_anchors.get("fields", []),
            max_count=16,
        )
        actions = self._merge_anchor_list(
            ai_anchors.get("actions", []),
            lex_anchors.get("actions", []),
            list(self._COMMON_ACTION_WORDS),
            max_count=16,
        )
        states = self._merge_anchor_list(
            ai_anchors.get("states", []),
            lex_anchors.get("states", []),
            max_count=12,
        )
        roles = self._merge_anchor_list(
            ai_anchors.get("roles", []),
            lex_anchors.get("roles", []),
            max_count=12,
        )
        constraints = self._merge_anchor_list(
            ai_anchors.get("constraints", []),
            lex_anchors.get("constraints", []),
            max_count=16,
        )
        ui_terms = self._merge_anchor_list(
            ai_anchors.get("ui_terms", []),
            lex_anchors.get("ui_terms", []),
            max_count=16,
        )

        keywords = self._merge_anchor_list(
            modules, objects, fields, actions, states, roles, constraints, ui_terms, max_count=60
        )

        return {
            "default_module": default_module,
            "modules": modules,
            "objects": objects,
            "fields": fields,
            "actions": actions,
            "states": states,
            "roles": roles,
            "constraints": constraints,
            "ui_terms": ui_terms,
            "keywords": keywords,
        }

    def _extract_ai_anchors(self, merged_text: str) -> Dict[str, List[str]]:
        text = (merged_text or "").strip()
        if not text:
            return {
                "modules": [],
                "objects": [],
                "fields": [],
                "actions": [],
                "states": [],
                "roles": [],
                "constraints": [],
                "ui_terms": [],
            }

        prompt = f"""
你是需求分析专家。请从下面需求原文中提取“语义锚点”，用于后续测试点生成。

要求：
1. 严格基于原文，不要脑补
2. 每类最多 12 条
3. 只输出 JSON 对象，不要解释

JSON 结构：
{{
  "modules": [],
  "objects": [],
  "fields": [],
  "actions": [],
  "states": [],
  "roles": [],
  "constraints": [],
  "ui_terms": []
}}

需求原文：
{text[:6000]}
""".strip()

        result: Dict[str, List[str]] = {
            "modules": [],
            "objects": [],
            "fields": [],
            "actions": [],
            "states": [],
            "roles": [],
            "constraints": [],
            "ui_terms": [],
        }

        try:
            obj = self.llm.call_json(
                prompt=prompt,
                timeout=min(self.timeout, 90),
                agent_type="analysis",
            )
        except Exception:
            obj = {}

        if not isinstance(obj, dict) or not obj:
            try:
                raw = self.llm.call(
                    prompt=prompt,
                    timeout=min(self.timeout, 90),
                    agent_type="analysis",
                )
                obj = self._parse_first_json_object(raw)
            except Exception:
                obj = {}

        if not isinstance(obj, dict):
            return result

        for key in result.keys():
            value = obj.get(key)
            if isinstance(value, list):
                result[key] = self._uniq_clean_items([str(x).strip() for x in value if str(x).strip()], max_count=12)
            elif isinstance(value, str) and value.strip():
                result[key] = self._uniq_clean_items(
                    [x.strip() for x in re.split(r"[,\n，；;、]+", value) if x.strip()],
                    max_count=12,
                )
        return result

    def _extract_lexical_anchors(self, text: str) -> Dict[str, List[str]]:
        s = (text or "").strip()

        modules: List[str] = []
        objects: List[str] = []
        fields: List[str] = []
        actions: List[str] = []
        states: List[str] = []
        roles: List[str] = []
        constraints: List[str] = []
        ui_terms: List[str] = []

        for m in re.finditer(r"(?:模块|功能|页面|场景|流程|对象)[:：]\s*([^\n，。,；;]{2,30})", s):
            val = m.group(1).strip()
            if val:
                modules.append(val)
                objects.append(val)

        for m in re.finditer(r"[\"“”'‘’《》\[\]【】]([^\"“”'‘’《》\[\]【】]{2,24})[\"“”'‘’《》\[\]【】]", s):
            token = m.group(1).strip()
            if not token:
                continue
            if any(ch in token for ch in self._UI_HINT_WORDS):
                ui_terms.append(token)
            else:
                objects.append(token)
                fields.append(token)

        for token in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{1,31}", s):
            if self._looks_like_identifier(token):
                fields.append(token)

        for m in re.finditer(
            r"(点击|选择|输入|提交|保存|确认|删除|编辑|修改|切换|查询|搜索|筛选|导出|导入|上传|下载|创建|新增|关闭|开启|启用|禁用|查看|刷新)([^\n，。；;]{0,16})",
            s,
        ):
            head = m.group(1).strip()
            tail = m.group(2).strip()
            phrase = f"{head}{tail}".strip()
            if 1 < len(phrase) <= 24:
                actions.append(phrase)

        for word in self._COMMON_STATE_WORDS:
            if word in s:
                states.append(word)

        for word in self._COMMON_ROLE_WORDS:
            if word in s:
                roles.append(word)

        for m in re.finditer(r"(?:当|若|如果|仅当|必须|不可|不能|仅限|至少|最多|大于|小于|等于|超过|不足)([^\n。；;]{1,20})", s):
            phrase = m.group(0).strip()
            if 2 <= len(phrase) <= 28:
                constraints.append(phrase)

        for m in re.finditer(
            r"([^\s，。,；;:：]{1,18}(?:按钮|弹窗|列表|下拉框|输入框|复选框|单选框|tab|Tab|标签页|图表|表格|筛选器|搜索框|提示语|toast|Toast))",
            s,
        ):
            val = m.group(1).strip()
            if 2 <= len(val) <= 24:
                ui_terms.append(val)

        for token in self._extract_candidate_noun_phrases(s):
            objects.append(token)

        return {
            "modules": self._uniq_clean_items(modules, max_count=12),
            "objects": self._uniq_clean_items(objects, max_count=20),
            "fields": self._uniq_clean_items(fields, max_count=16),
            "actions": self._uniq_clean_items(actions, max_count=16),
            "states": self._uniq_clean_items(states, max_count=12),
            "roles": self._uniq_clean_items(roles, max_count=12),
            "constraints": self._uniq_clean_items(constraints, max_count=16, max_len=28),
            "ui_terms": self._uniq_clean_items(ui_terms, max_count=16),
        }

    # =========================================================
    # Parse / normalize
    # =========================================================

    def _parse_points(self, raw: str) -> List[Dict[str, Any]]:
        if not raw or not isinstance(raw, str):
            return []

        s = raw.strip()
        if not s:
            return []

        try:
            data = json.loads(s)
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
            if isinstance(data, dict):
                for key in ("items", "data", "test_points", "points", "result"):
                    value = data.get(key)
                    if isinstance(value, list):
                        return [x for x in value if isinstance(x, dict)]
        except Exception:
            pass

        items: List[Dict[str, Any]] = []
        for m in re.finditer(r"\{[\s\S]*?\}", s):
            chunk = m.group().strip()
            try:
                obj = json.loads(chunk)
                if isinstance(obj, dict):
                    items.append(obj)
            except Exception:
                continue
        return items

    def _parse_first_json_object(self, raw: str) -> Dict[str, Any]:
        if not raw or not isinstance(raw, str):
            return {}
        s = raw.strip()
        if not s:
            return {}
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

        for m in re.finditer(r"\{[\s\S]*?\}", s):
            chunk = m.group().strip()
            try:
                obj = json.loads(chunk)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
        return {}

    def _normalize_point(
        self,
        *,
        item: Dict[str, Any],
        default_source: str,
        default_module: str,
    ) -> TestPoint:
        module = self._normalize_module(str(item.get("module") or default_module), default_module)
        point_type = self._normalize_point_type(str(item.get("point_type") or item.get("type") or "normal"))
        title = self._refine_title(str(item.get("title") or ""))
        objective = self._refine_detail(
            str(item.get("objective") or item.get("detail") or item.get("description") or title)
        )

        preconditions = self._ensure_str_list(item.get("preconditions"))
        inputs = self._ensure_str_list(item.get("inputs"))
        check_items = self._ensure_str_list(item.get("check_items"))
        expected_results = self._ensure_str_list(item.get("expected_results"))
        tags = self._ensure_str_list(item.get("tags"))
        refs = self._ensure_str_list(item.get("source_requirement_refs"))
        notes = self._ensure_str_list(item.get("notes"))

        if not refs:
            refs = [self._refine_source(str(item.get("source") or default_source))]

        if not check_items and objective:
            check_items = [objective]

        if not expected_results and objective:
            expected_results = [objective]

        priority = self._normalize_priority(
            str(item.get("priority") or self._decide_priority(title=title, objective=objective, refs=refs))
        )

        point = TestPoint(
            point_id="",
            module=module,
            scenario_type=point_type,  # type: ignore[arg-type]
            point_type=point_type,      # type: ignore[arg-type]
            title=title or "未命名测试点",
            objective=objective or title or "未命名测试点",
            preconditions=self._uniq_str_list(preconditions, max_count=6),
            inputs=self._uniq_str_list(inputs, max_count=6),
            check_items=self._uniq_str_list(check_items, max_count=8),
            expected_direction=self._uniq_str_list(expected_results, max_count=8),
            expected_results=self._uniq_str_list(expected_results, max_count=8),
            priority=priority,          # type: ignore[arg-type]
            priority_hint=priority,     # type: ignore[arg-type]
            tags=self._uniq_str_list(tags or ["功能测试"], max_count=6),
            source_requirement_refs=self._uniq_str_list(refs, max_count=3),
            requirement_evidence=self._uniq_str_list(refs, max_count=3),
            notes=self._uniq_str_list(notes, max_count=4),
        ).normalize()

        return self._force_point_structure(point)

    # =========================================================
    # Filters
    # =========================================================

    def _post_filter_points(
        self,
        *,
        points: List[TestPoint],
        requirement_text: str,
        user_requirement: str,
        default_module: str,
        anchors: Dict[str, Any],
    ) -> List[TestPoint]:
        result: List[TestPoint] = []
        seen: Set[str] = set()

        for point in points:
            point.module = self._normalize_module(point.module, default_module)
            point.title = self._refine_title(point.title)
            point.objective = self._refine_detail(point.objective)
            point = self._force_point_structure(point)

            if not self._accept_final_point(
                point=point,
                requirement_text=requirement_text,
                user_requirement=user_requirement,
                anchors=anchors,
            ):
                continue

            fp = self._semantic_fingerprint(point)
            if self.dedup and fp in seen:
                continue
            seen.add(fp)
            result.append(point)

            if len(result) >= self.max_points:
                break

        return result

    def _accept_final_point(
        self,
        *,
        point: TestPoint,
        requirement_text: str,
        user_requirement: str,
        anchors: Dict[str, Any],
    ) -> bool:
        title = point.title or ""
        objective = point.objective or ""
        merged = f"{title} {objective}".strip()

        if not title or len(title) < 6:
            return False
        if self._is_generic_title(title):
            return False
        if self._is_too_generic_detail(objective):
            return False
        if self._contains_document_id_noise(title):
            return False
        if self._is_over_generalized(merged):
            return False
        if not self._contains_anchor_signal(point, anchors):
            return False

        if self.strict_relevance_filter:
            score = self._relevance_score(point, requirement_text, user_requirement, anchors)
            if score < 4:
                return False

        return True

    def _contains_anchor_signal(self, point: TestPoint, anchors: Dict[str, Any]) -> bool:
        combined = " ".join(
            [
                point.title or "",
                point.objective or "",
                point.module or "",
                " ".join(point.source_requirement_refs or []),
            ]
        ).lower()

        keywords = [
            *(anchors.get("modules") or []),
            *(anchors.get("objects") or []),
            *(anchors.get("fields") or []),
            *(anchors.get("actions") or []),
            *(anchors.get("states") or []),
            *(anchors.get("roles") or []),
            *(anchors.get("constraints") or []),
            *(anchors.get("ui_terms") or []),
            *(anchors.get("keywords") or []),
        ]
        keywords = [str(x).strip().lower() for x in keywords if str(x).strip()]
        if not keywords:
            return len(self._core_tokens(combined)) >= 2
        return any(kw in combined for kw in keywords)

    def _relevance_score(
        self,
        point: TestPoint,
        requirement_text: str,
        user_requirement: str,
        anchors: Dict[str, Any],
    ) -> int:
        title = point.title or ""
        objective = point.objective or ""
        refs = " ".join(point.source_requirement_refs or [])
        module = point.module or ""

        combined = " ".join([title, objective, refs]).strip()
        tokens = self._core_tokens(combined)
        if not tokens:
            return 0

        req_text = (requirement_text or "").lower()
        user_text = (user_requirement or "").lower()
        score = 0

        for tk in self._core_tokens(title)[:8]:
            if tk and tk in req_text:
                score += 2

        for tk in self._core_tokens(objective)[:8]:
            if tk and tk in req_text:
                score += 1

        for tk in self._core_tokens(refs)[:4]:
            if tk and tk in req_text:
                score += 1

        if module and module.lower() in req_text:
            score += 1

        anchor_sets: List[str] = []
        for key in ("modules", "objects", "fields", "actions", "states", "roles", "constraints", "ui_terms", "keywords"):
            anchor_sets.extend(anchors.get(key) or [])

        combined_l = combined.lower()
        anchor_hit_count = 0
        for kw in anchor_sets:
            kw = str(kw or "").strip().lower()
            if kw and kw in combined_l:
                anchor_hit_count += 1

        score += min(6, anchor_hit_count)

        if user_text:
            for tk in self._core_tokens(title)[:4]:
                if tk and tk in user_text:
                    score += 1

        if len(self._core_tokens(title)) >= 3:
            score += 1

        if point.source_requirement_refs:
            score += 1

        return score

    # =========================================================
    # Helpers
    # =========================================================

    def _chunk(self, text: str) -> List[Dict[str, str]]:
        t = (text or "").strip()
        if not t:
            return [{"id": "C1", "title": "Chunk 1", "text": ""}]

        if smart_split_text:
            try:
                parts = smart_split_text(
                    t,
                    max_chunks=self.max_chunks,
                    min_chars=self.chunk_min_chars,
                    max_chars=self.chunk_max_chars,
                )
                if parts:
                    return [{"id": f"C{i + 1}", "title": f"Chunk {i + 1}", "text": part} for i, part in enumerate(parts)]
            except Exception:
                pass

        paras = [p.strip() for p in re.split(r"\n\s*\n+", t) if p.strip()]
        merged: List[str] = []
        cur = ""

        for p in paras:
            if not cur:
                cur = p
                continue
            if len(cur) + 2 + len(p) <= self.chunk_max_chars:
                cur = cur + "\n\n" + p
            else:
                merged.append(cur)
                cur = p
                if len(merged) >= self.max_chunks:
                    break

        if cur and len(merged) < self.max_chunks:
            merged.append(cur)

        if not merged:
            merged = [t[: self.chunk_max_chars]]

        return [{"id": f"C{i + 1}", "title": f"Chunk {i + 1}", "text": chunk} for i, chunk in enumerate(merged)]

    def _infer_global_module(self, text: str) -> str:
        s = (text or "").strip()
        m = re.search(r"(?:模块|功能|页面)[:：]\s*([^\n，。,；;]{2,30})", s)
        if m:
            return m.group(1).strip()
        return ""

    def _normalize_module(self, module: str, default_module: str) -> str:
        s = str(module or "").strip()
        if not s:
            return default_module
        if s in {"模块", "功能模块", "页面模块", "功能点"}:
            return default_module
        return s[:50]

    def _normalize_point_type(self, point_type: str) -> str:
        s = (point_type or "").strip().lower()
        if s in self._ALLOWED_POINT_TYPES:
            return s
        if "异常" in s or "negative" in s:
            return "exception"
        if "边界" in s or "boundary" in s:
            return "boundary"
        return "normal"

    def _normalize_priority(self, priority: str) -> str:
        s = (priority or "").strip().upper()
        if s in self._ALLOWED_PRIORITIES:
            return s
        if "0" in s or "高" in s:
            return "P0"
        if "2" in s:
            return "P2"
        if "3" in s or "低" in s:
            return "P3"
        return "P1"

    def _decide_priority(self, *, title: str, objective: str, refs: List[str]) -> str:
        merged = " ".join([title or "", objective or "", " ".join(refs or [])])

        if any(word in merged for word in self._P0_HINT_WORDS):
            return "P0"
        if any(word in merged for word in self._P1_HINT_WORDS):
            return "P1"
        if "边界" in merged:
            return "P2"
        return "P1"

    def _ensure_str_list(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            result: List[str] = []
            for item in value:
                text = str(item).strip()
                if text:
                    result.append(text)
            return result
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _uniq_str_list(self, values: List[str], *, max_count: int = 8) -> List[str]:
        result: List[str] = []
        seen: Set[str] = set()
        for item in values or []:
            s = re.sub(r"\s+", " ", str(item or "").strip())
            s = s.strip(" -—:：，。")
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(s)
            if len(result) >= max_count:
                break
        return result

    def _refine_title(self, title: str) -> str:
        s = re.sub(r"\s+", " ", str(title or "").strip())
        for prefix in self._TITLE_BAD_PREFIXES:
            s = re.sub(rf"^{re.escape(prefix)}\s*", "", s)
        for suffix in self._TITLE_BAD_SUFFIXES:
            s = re.sub(rf"{re.escape(suffix)}$", "", s)
        s = s.replace("应被正确执行", "").replace("应正确执行", "")
        s = s.replace("处理正确", "").replace("展示正确", "")
        s = s.replace("功能点", "")
        s = s.strip(" -—:：，。")
        return s[:120] if s else "未命名测试点"

    def _refine_detail(self, detail: str) -> str:
        s = re.sub(r"\s+", " ", str(detail or "").strip())
        s = s.strip(" -—:：，。")
        return s[:300] if s else ""

    def _refine_source(self, source: str) -> str:
        s = re.sub(r"\s+", " ", str(source or "").strip())
        s = s.strip(" -—:：，。")
        return s[:120] if s else ""

    def _force_point_structure(self, point: TestPoint) -> TestPoint:
        title = self._refine_title(point.title)
        tokens = self._core_tokens(title)

        if len(tokens) < 2:
            obj = point.module or "业务对象"
            action = "逻辑校验"
            title = f"{obj} {action}"

        if not point.objective or len(self._core_tokens(point.objective)) < 2:
            point.objective = f"验证{title}相关流程、结果及展示是否符合需求。"

        if not point.check_items:
            point.check_items = [point.objective]
        if not point.expected_results and not point.expected_direction:
            point.expected_results = [point.objective]
            point.expected_direction = [point.objective]

        point.title = self._refine_title(title)
        point.preconditions = self._uniq_str_list(point.preconditions or [], max_count=6)
        point.inputs = self._uniq_str_list(point.inputs or [], max_count=6)
        point.check_items = self._uniq_str_list(point.check_items or [], max_count=8)
        point.expected_results = self._uniq_str_list(point.expected_results or [], max_count=8)
        point.expected_direction = self._uniq_str_list(point.expected_direction or point.expected_results or [], max_count=8)
        point.source_requirement_refs = self._uniq_str_list(point.source_requirement_refs or [], max_count=3)
        point.requirement_evidence = self._uniq_str_list(point.requirement_evidence or point.source_requirement_refs or [], max_count=3)

        return point.normalize()

    def _is_generic_title(self, title: str) -> bool:
        s = str(title or "").strip()
        if not s:
            return True
        if len(s) <= 4:
            return True
        if any(x in s for x in self._GENERIC_TITLE_PATTERNS):
            return True
        token_count = len(self._core_tokens(s))
        return token_count <= 1

    def _is_too_generic_detail(self, detail: str) -> bool:
        s = str(detail or "").strip()
        if not s:
            return True
        if any(x in s for x in self._GENERIC_DETAIL_PATTERNS):
            return True
        return len(self._core_tokens(s)) <= 1

    def _is_over_generalized(self, text: str) -> bool:
        tokens = self._core_tokens(text)
        if len(tokens) <= 1:
            return True
        if all(t in self._LOW_VALUE_WORDS for t in tokens):
            return True
        return False

    def _contains_document_id_noise(self, title: str) -> bool:
        s = str(title or "").strip()
        if not s:
            return False
        if re.search(r"\b(?:prd|req|rid|story|task|bug)[\-_]?\d+\b", s, re.I):
            return True
        if re.search(r"\b[A-Z]{2,}-\d+\b", s):
            return True
        if re.search(r"\b[a-z]{2,}-\d+\b", s, re.I):
            return True
        return False

    def _semantic_fingerprint(self, point: TestPoint) -> str:
        title_tokens = self._core_tokens(point.title)[:8]
        obj_tokens = self._core_tokens(point.objective)[:5]
        joined = "|".join(sorted(set(title_tokens + obj_tokens)))
        point_type = getattr(point, "point_type", "") or getattr(point, "scenario_type", "")
        return f"{point.module}||{point_type}||{joined}"

    def _core_tokens(self, text: str) -> List[str]:
        raw = re.findall(r"[A-Za-z0-9_\-/]+|[\u4e00-\u9fa5]{2,}", text or "")
        result: List[str] = []
        for item in raw:
            s = item.strip().lower()
            if not s:
                continue
            if s in self._LOW_VALUE_WORDS:
                continue
            if len(s) <= 1:
                continue
            result.append(s)
        return result[:20]

    def _merge_anchor_list(self, *parts: List[str], max_count: int = 20) -> List[str]:
        merged: List[str] = []
        seen: Set[str] = set()
        for part in parts:
            for item in part or []:
                s = str(item or "").strip()
                if not s:
                    continue
                key = re.sub(r"\s+", "", s).lower()
                if key in seen:
                    continue
                seen.add(key)
                merged.append(s)
                if len(merged) >= max_count:
                    return merged
        return merged

    def _extract_candidate_noun_phrases(self, text: str) -> List[str]:
        out: List[str] = []
        for token in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9_\-]{2,24}", text or ""):
            s = token.strip()
            if not s:
                continue
            if s in self._LOW_VALUE_WORDS:
                continue
            if self._looks_like_noise_token(s):
                continue
            out.append(s)
            if len(out) >= 120:
                break
        return out

    def _looks_like_identifier(self, token: str) -> bool:
        t = str(token or "").strip()
        if not t or len(t) < 2 or len(t) > 32:
            return False
        if re.fullmatch(r"[A-Z0-9_]+", t):
            return True
        if "_" in t or "-" in t:
            return True
        if re.search(r"[a-z][A-Z]", t):
            return True
        if re.search(r"\d", t) and re.search(r"[A-Za-z]", t):
            return True
        return False

    def _looks_like_noise_token(self, token: str) -> bool:
        s = str(token or "").strip()
        if not s:
            return True
        if len(s) == 1:
            return True
        if s.lower() in {"true", "false", "null", "json", "http", "https"}:
            return True
        if re.fullmatch(r"\d+(\.\d+)?", s):
            return True
        return False

    def _uniq_clean_items(
        self,
        items: List[str],
        *,
        max_count: int = 20,
        max_len: int = 30,
    ) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        for item in items or []:
            s = str(item or "").strip()
            s = re.sub(r"\s+", "", s)
            s = s.strip("，,、-—:：;；.。()（）[]【】")
            if not s:
                continue
            if len(s) > max_len:
                continue
            if self._looks_like_noise_token(s):
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
            if len(out) >= max_count:
                break
        return out

    def _renumber_points(self, points: List[TestPoint]) -> List[TestPoint]:
        result: List[TestPoint] = []
        for idx, point in enumerate(points, 1):
            point.point_id = f"TP_{idx:03d}"
            result.append(point)
        return result

    def _sort_points(self, points: List[TestPoint]) -> List[TestPoint]:
        priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        type_rank = {"normal": 0, "exception": 1, "boundary": 2}

        def _key(p: TestPoint) -> Any:
            pty = str(getattr(p, "priority", "") or getattr(p, "priority_hint", "") or "P3").upper()
            sty = str(getattr(p, "point_type", "") or getattr(p, "scenario_type", "") or "normal").lower()
            return (
                priority_rank.get(pty, 9),
                type_rank.get(sty, 9),
                str(getattr(p, "module", "") or ""),
                str(getattr(p, "title", "") or ""),
            )

        return sorted(points, key=_key)

    def _is_action_too_generic(self, action: str) -> bool:
        s = (action or "").strip()
        if not s:
            return True
        return s in {"操作", "处理", "执行", "提交操作", "进行操作"}