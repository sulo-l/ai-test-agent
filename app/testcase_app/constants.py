#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/4/14 12:17
# @Author: sulo
# app/testcase_app/constants.py
# -*- coding: utf-8 -*-

from __future__ import annotations


# =========================================================
# Pipeline stages
# 固定主流程：
# 读取需求 -> 需求分析 -> 测试点 -> 生成测试用例 -> 用例评审
# -> 优化测试用例 -> 下载测试用例 -> 完成
# =========================================================
STAGE_READ_REQUIREMENT = "READ_REQUIREMENT"
STAGE_ANALYZE_REQUIREMENT = "ANALYZE_REQUIREMENT"
STAGE_ANALYZE_TEST_POINTS = "ANALYZE_TEST_POINTS"
STAGE_DESIGN_TESTCASES = "DESIGN_TESTCASES"
STAGE_REVIEW_TESTCASES = "REVIEW_TESTCASES"
STAGE_REFINE_TESTCASES = "REFINE_TESTCASES"
STAGE_EXPORT_TESTCASES = "EXPORT_TESTCASES"
STAGE_FINISHED = "FINISHED"

PIPELINE_STAGES = [
    STAGE_READ_REQUIREMENT,
    STAGE_ANALYZE_REQUIREMENT,
    STAGE_ANALYZE_TEST_POINTS,
    STAGE_DESIGN_TESTCASES,
    STAGE_REVIEW_TESTCASES,
    STAGE_REFINE_TESTCASES,
    STAGE_EXPORT_TESTCASES,
    STAGE_FINISHED,
]

# 前端主流程标题，严格对应页面展示
STAGE_TITLES = {
    STAGE_READ_REQUIREMENT: "读取需求",
    STAGE_ANALYZE_REQUIREMENT: "需求分析",
    STAGE_ANALYZE_TEST_POINTS: "测试点",
    STAGE_DESIGN_TESTCASES: "生成测试用例",
    STAGE_REVIEW_TESTCASES: "用例评审",
    STAGE_REFINE_TESTCASES: "优化测试用例",
    STAGE_EXPORT_TESTCASES: "下载测试用例",
    STAGE_FINISHED: "完成",
}

# 阶段简述，可用于前端卡片副标题/默认摘要
STAGE_SUBTITLES = {
    STAGE_READ_REQUIREMENT: "读取并清洗需求内容，建立执行上下文",
    STAGE_ANALYZE_REQUIREMENT: "提炼业务模块、规则、约束与风险点",
    STAGE_ANALYZE_TEST_POINTS: "基于需求分析结果提取结构化测试点",
    STAGE_DESIGN_TESTCASES: "根据测试点生成结构化测试用例草稿",
    STAGE_REVIEW_TESTCASES: "检查覆盖、步骤、预期与结构质量",
    STAGE_REFINE_TESTCASES: "针对问题用例定向修正与提质",
    STAGE_EXPORT_TESTCASES: "导出最终测试用例结果",
    STAGE_FINISHED: "测试用例生成流程已完成",
}


# =========================================================
# Stage status
# =========================================================
STAGE_STATUS_PENDING = "pending"
STAGE_STATUS_RUNNING = "running"
STAGE_STATUS_COMPLETED = "completed"
STAGE_STATUS_ERROR = "error"
STAGE_STATUS_SKIPPED = "skipped"

STAGE_STATUSES = {
    STAGE_STATUS_PENDING,
    STAGE_STATUS_RUNNING,
    STAGE_STATUS_COMPLETED,
    STAGE_STATUS_ERROR,
    STAGE_STATUS_SKIPPED,
}


# =========================================================
# Event types
# 统一前后端事件协议，避免页面顶部/中部/底部各吃一套数据
# =========================================================
EVENT_CONNECTED = "connected"
EVENT_HEARTBEAT = "heartbeat"

# 老事件，兼容旧逻辑
EVENT_STAGE = "stage"
EVENT_STAGE_EVENT = "stage_event"
EVENT_LOG = "log"
EVENT_PROGRESS = "progress"
EVENT_METRIC = "metric"

# 新主事件，建议前端优先消费
EVENT_STAGE_SNAPSHOT = "stage_snapshot"     # 整体阶段快照
EVENT_STAGE_CONTENT = "stage_content"       # 当前阶段内容摘要
EVENT_STAGE_METRIC = "stage_metric"         # 当前阶段指标
EVENT_RUNTIME_SNAPSHOT = "runtime_snapshot" # 全链路运行态

# 各阶段结果
EVENT_REQUIREMENT_RESULT = "requirement_result"
EVENT_ANALYSIS_RESULT = "analysis_result"
EVENT_TEST_POINT_BATCH = "test_point_batch"  # 分析阶段每个模块测试点流式推送
EVENT_TEST_POINT_RESULT = "test_point_result"
EVENT_DESIGN_RESULT = "design_result"
EVENT_CASE_BATCH = "case_batch"          # 设计阶段每批用例流式推送
EVENT_REVIEW_RESULT = "review_result"
EVENT_REVIEW_BATCH = "review_batch"      # 评审阶段每个模块结果流式推送
EVENT_REFINE_RESULT = "refine_result"
EVENT_REFINE_BATCH = "refine_batch"      # 精炼阶段每批用例流式推送

# 结束态
EVENT_DOWNLOAD = "download"
EVENT_FINAL_RESULT = "final_result"
EVENT_FINAL_SUMMARY = "final_summary"
EVENT_PIPELINE_SUMMARY = "pipeline_summary"
EVENT_ERROR = "error"

EVENT_TYPES = {
    EVENT_CONNECTED,
    EVENT_HEARTBEAT,
    EVENT_STAGE,
    EVENT_STAGE_EVENT,
    EVENT_LOG,
    EVENT_PROGRESS,
    EVENT_METRIC,
    EVENT_STAGE_SNAPSHOT,
    EVENT_STAGE_CONTENT,
    EVENT_STAGE_METRIC,
    EVENT_RUNTIME_SNAPSHOT,
    EVENT_REQUIREMENT_RESULT,
    EVENT_ANALYSIS_RESULT,
    EVENT_TEST_POINT_BATCH,
    EVENT_TEST_POINT_RESULT,
    EVENT_DESIGN_RESULT,
    EVENT_CASE_BATCH,
    EVENT_REVIEW_RESULT,
    EVENT_REVIEW_BATCH,
    EVENT_REFINE_RESULT,
    EVENT_REFINE_BATCH,
    EVENT_DOWNLOAD,
    EVENT_FINAL_RESULT,
    EVENT_FINAL_SUMMARY,
    EVENT_PIPELINE_SUMMARY,
    EVENT_ERROR,
}


# =========================================================
# Job status
# =========================================================
JOB_STATUS_ENQUEUED = "ENQUEUED"
JOB_STATUS_ENQUEUE_OK = "ENQUEUE_OK"
JOB_STATUS_RUNNING = "RUNNING"
JOB_STATUS_DONE = "DONE"
JOB_STATUS_ERROR = "ERROR"
JOB_STATUS_CANCELLED = "CANCELLED"
JOB_STATUS_NOT_FOUND = "NOT_FOUND"

JOB_STATUSES = {
    JOB_STATUS_ENQUEUED,
    JOB_STATUS_ENQUEUE_OK,
    JOB_STATUS_RUNNING,
    JOB_STATUS_DONE,
    JOB_STATUS_ERROR,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_NOT_FOUND,
}


# =========================================================
# Requirement / analysis types
# =========================================================
SCENARIO_TYPE_NORMAL = "normal"
SCENARIO_TYPE_EXCEPTION = "exception"
SCENARIO_TYPE_BOUNDARY = "boundary"

SCENARIO_TYPES = {
    SCENARIO_TYPE_NORMAL,
    SCENARIO_TYPE_EXCEPTION,
    SCENARIO_TYPE_BOUNDARY,
}

POINT_TYPE_NORMAL = SCENARIO_TYPE_NORMAL
POINT_TYPE_EXCEPTION = SCENARIO_TYPE_EXCEPTION
POINT_TYPE_BOUNDARY = SCENARIO_TYPE_BOUNDARY

TEST_POINT_TYPES = {
    POINT_TYPE_NORMAL,
    POINT_TYPE_EXCEPTION,
    POINT_TYPE_BOUNDARY,
}


# =========================================================
# Priority
# =========================================================
PRIORITY_P0 = "P0"
PRIORITY_P1 = "P1"
PRIORITY_P2 = "P2"
PRIORITY_P3 = "P3"

PRIORITIES = {
    PRIORITY_P0,
    PRIORITY_P1,
    PRIORITY_P2,
    PRIORITY_P3,
}

# 可用于 planner / design 的优先级参考
PRIORITY_DESC = {
    PRIORITY_P0: "资金安全/核心主链路/高风险",
    PRIORITY_P1: "核心功能/关键校验",
    PRIORITY_P2: "重要分支/常规边界",
    PRIORITY_P3: "低风险边界/提示文案/补充验证",
}


# =========================================================
# Test tags
# =========================================================
TAG_FUNCTION = "功能测试"
TAG_BOUNDARY = "边界测试"
TAG_EXCEPTION = "异常测试"
TAG_UI = "UI测试"
TAG_API = "接口测试"
TAG_SMOKE = "冒烟测试"

TEST_TAGS = {
    TAG_FUNCTION,
    TAG_BOUNDARY,
    TAG_EXCEPTION,
    TAG_UI,
    TAG_API,
    TAG_SMOKE,
}


# =========================================================
# Case status
# =========================================================
CASE_STATUS_PENDING = "未开始"
CASE_STATUS_RUNNING = "执行中"
CASE_STATUS_DONE = "已执行"
CASE_STATUS_DEPRECATED = "已废弃"

CASE_STATUSES = {
    CASE_STATUS_PENDING,
    CASE_STATUS_RUNNING,
    CASE_STATUS_DONE,
    CASE_STATUS_DEPRECATED,
}


# =========================================================
# Review
# =========================================================
REVIEW_SEVERITY_HIGH = "高"
REVIEW_SEVERITY_MEDIUM = "中"
REVIEW_SEVERITY_LOW = "低"

REVIEW_SEVERITIES = {
    REVIEW_SEVERITY_HIGH,
    REVIEW_SEVERITY_MEDIUM,
    REVIEW_SEVERITY_LOW,
}

REVIEW_DECISION_PASS = "通过"
REVIEW_DECISION_NEEDS_REFINE = "需优化"
REVIEW_DECISION_REJECT = "驳回"

REVIEW_DECISIONS = {
    REVIEW_DECISION_PASS,
    REVIEW_DECISION_NEEDS_REFINE,
    REVIEW_DECISION_REJECT,
}

REVIEW_ISSUE_COVERAGE = "覆盖缺失"
REVIEW_ISSUE_DUPLICATE = "重复用例"
REVIEW_ISSUE_STEP = "步骤不清"
REVIEW_ISSUE_EXPECTED = "预期空泛"
REVIEW_ISSUE_MISMATCH = "与需求不符"
REVIEW_ISSUE_MISSING_FIELD = "字段缺失"
REVIEW_ISSUE_STRUCTURE = "结构错误"
REVIEW_ISSUE_DIRTY = "脏内容"
REVIEW_ISSUE_PRIORITY = "优先级不合理"
REVIEW_ISSUE_TITLE = "标题不规范"
REVIEW_ISSUE_PRECONDITION = "前置条件不规范"

REVIEW_ISSUE_TYPES = {
    REVIEW_ISSUE_COVERAGE,
    REVIEW_ISSUE_DUPLICATE,
    REVIEW_ISSUE_STEP,
    REVIEW_ISSUE_EXPECTED,
    REVIEW_ISSUE_MISMATCH,
    REVIEW_ISSUE_MISSING_FIELD,
    REVIEW_ISSUE_STRUCTURE,
    REVIEW_ISSUE_DIRTY,
    REVIEW_ISSUE_PRIORITY,
    REVIEW_ISSUE_TITLE,
    REVIEW_ISSUE_PRECONDITION,
}


# =========================================================
# Queue / worker stage aliases
# =========================================================
WORKER_STAGE_WORKER_RECEIVED = "WORKER_RECEIVED"
WORKER_STAGE_PIPELINE_START = "PIPELINE_START"
WORKER_STAGE_CANCEL_REQUESTED = "CANCEL_REQUESTED"
WORKER_STAGE_CANCEL_SIGNALLED = "CANCEL_SIGNALLED"
WORKER_STAGE_CANCELLED_BEFORE_START = "CANCELLED_BEFORE_START"
WORKER_STAGE_CANCELLED = "CANCELLED"
WORKER_STAGE_ERROR = "ERROR"
WORKER_STAGE_DONE = "DONE"

WORKER_STAGES = {
    WORKER_STAGE_WORKER_RECEIVED,
    WORKER_STAGE_PIPELINE_START,
    WORKER_STAGE_CANCEL_REQUESTED,
    WORKER_STAGE_CANCEL_SIGNALLED,
    WORKER_STAGE_CANCELLED_BEFORE_START,
    WORKER_STAGE_CANCELLED,
    WORKER_STAGE_ERROR,
    WORKER_STAGE_DONE,
}


# =========================================================
# Metrics / summary keys
# 统一统计口径，避免前端自己猜字段
# =========================================================
METRIC_TEST_POINTS_TOTAL = "test_points_total"
METRIC_DRAFT_TESTCASES_TOTAL = "draft_testcases_total"
METRIC_FINAL_TESTCASES_TOTAL = "final_testcases_total"
METRIC_REVIEW_ISSUES_TOTAL = "review_issues_total"
METRIC_COVERED_POINTS = "covered_points"
METRIC_UNCOVERED_POINTS = "uncovered_points"
METRIC_COVERAGE_RATE = "coverage_rate"
METRIC_TOTAL_DURATION_MS = "total_duration_ms"
METRIC_STAGE_DURATIONS = "stage_durations"
METRIC_MODULES_TOTAL = "modules_total"
METRIC_DOWNLOAD_URL = "download_url"
METRIC_EXPORT_PATH = "export_path"

SUMMARY_METRIC_KEYS = {
    METRIC_TEST_POINTS_TOTAL,
    METRIC_DRAFT_TESTCASES_TOTAL,
    METRIC_FINAL_TESTCASES_TOTAL,
    METRIC_REVIEW_ISSUES_TOTAL,
    METRIC_COVERED_POINTS,
    METRIC_UNCOVERED_POINTS,
    METRIC_COVERAGE_RATE,
    METRIC_TOTAL_DURATION_MS,
    METRIC_STAGE_DURATIONS,
    METRIC_MODULES_TOTAL,
    METRIC_DOWNLOAD_URL,
    METRIC_EXPORT_PATH,
}


# =========================================================
# Defaults
# =========================================================
DEFAULT_REQUIREMENT_ID = "default-requirement-id"
DEFAULT_MODULE_NAME = "默认模块"
DEFAULT_UNKNOWN_MODULE_NAME = "未分组模块"
DEFAULT_OUTPUT_FILENAME = "testcases.xlsx"

DEFAULT_STAGE_PROGRESS = 0
FINAL_STAGE_PROGRESS = 100

DEFAULT_REVIEW_PASS_SCORE = 90.0
DEFAULT_REVIEW_REFINE_SCORE = 70.0


# =========================================================
# Helper sets
# =========================================================
TERMINAL_STAGE_NAMES = {
    STAGE_FINISHED,
    WORKER_STAGE_DONE,
    WORKER_STAGE_ERROR,
    WORKER_STAGE_CANCELLED,
    WORKER_STAGE_CANCELLED_BEFORE_START,
}

TERMINAL_EVENT_TYPES = {
    EVENT_FINAL_RESULT,
    EVENT_ERROR,
}

EXPORT_LIKE_STAGE_NAMES = {
    STAGE_EXPORT_TESTCASES,
    "export",
    "done",
}

TERMINAL_STAGE_EVENT_STATUS = {
    STAGE_STATUS_COMPLETED,
    STAGE_STATUS_ERROR,
    "done",
}

ANALYSIS_LIKE_STAGES = {
    STAGE_ANALYZE_REQUIREMENT,
    STAGE_ANALYZE_TEST_POINTS,
    "ANALYSIS",
    "ANALYSIS_PIPELINE_START",
    "ANALYSIS_PIPELINE_DONE",
}


# =========================================================
# UI order
# 页面严格按这个顺序展示
# =========================================================
PIPELINE_UI_ORDER = [
    STAGE_READ_REQUIREMENT,
    STAGE_ANALYZE_REQUIREMENT,
    STAGE_ANALYZE_TEST_POINTS,
    STAGE_DESIGN_TESTCASES,
    STAGE_REVIEW_TESTCASES,
    STAGE_REFINE_TESTCASES,
    STAGE_EXPORT_TESTCASES,
    STAGE_FINISHED,
]


# =========================================================
# Stage index / quick lookup
# =========================================================
PIPELINE_STAGE_INDEX = {
    stage: index for index, stage in enumerate(PIPELINE_STAGES)
}

PIPELINE_UI_STAGE_INDEX = {
    stage: index for index, stage in enumerate(PIPELINE_UI_ORDER)
}