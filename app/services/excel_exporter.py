#! /usr/bin/python3
# coding=utf-8
# @Author: sulo

from datetime import datetime
import re
import os
from typing import List, Dict, Any
from openpyxl import Workbook
from openpyxl.styles import Alignment

from app.settings import TMP_DIR

# ===============================
# Excel 表头（保持不动）
# ===============================
HEADERS = [
    "用例名称", "所属模块", "标签", "前置条件", "步骤描述",
    "预期结果", "编辑模式", "备注", "用例状态", "责任人",
    "用例等级", "是否可自动化", "是否已自动化"
]

EXCEL_CELL_LIMIT = 32000


# ===============================
# 工具函数
# ===============================
def _truncate(text: str) -> str:
    if not text:
        return ""
    if len(text) > EXCEL_CELL_LIMIT:
        return text[:EXCEL_CELL_LIMIT] + "\n【内容过长，已截断】"
    return text


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return _truncate("\n".join(str(x) for x in v))
    return _truncate(str(v))


# ===============================
# 🔥 steps 归一化（保持不动）
# ===============================
def _normalize_steps(steps) -> List[str]:
    if not steps:
        return []

    if isinstance(steps, str):
        return [s.strip() for s in steps.split("\n") if s.strip()]

    normalized = []
    for s in steps:
        if isinstance(s, str):
            normalized.append(s.strip())
        elif isinstance(s, dict):
            normalized.append(
                str(
                    s.get("step")
                    or s.get("desc")
                    or s.get("content")
                    or ""
                ).strip()
            )
        else:
            normalized.append(str(s).strip())

    return [x for x in normalized if x]


# =====================================================
# 用例名 / 模块清洗（保持不动）
# =====================================================
def clean_case_name(name: str) -> str:
    if not name:
        return name

    name = name.strip()
    name = re.sub(r"^[A-Za-z0-9_-]+:\s*", "", name)
    return name.strip()


def clean_module_name(module: str) -> str:
    if not module:
        return module
    return module.split(" (")[0].strip()


# ===============================
# Case 结构处理
# ===============================
def flatten_cases(raw_cases: list) -> list:
    result = []

    for raw in raw_cases:
        if isinstance(raw, dict) and isinstance(raw.get("test_cases"), list):
            for c in raw["test_cases"]:
                merged = dict(c)
                merged["module"] = raw.get("module", "")
                merged["test_point_name"] = raw.get("test_point_name", "")
                result.append(merged)
        else:
            result.append(raw)

    return result


def infer_priority(steps, is_focus: bool = False):
    if is_focus:
        return "P0"

    text = " ".join(steps)
    if any(k in text for k in ["资金", "下单", "风控"]):
        return "P0"
    if "异常" in text:
        return "P1"
    return "P2"


def infer_automatable(steps):
    return "是" if any("接口" in s for s in steps) else "否"


def normalize_case(raw: Dict[str, Any]) -> Dict[str, Any]:
    steps = _normalize_steps(raw.get("steps"))

    expected = (
        raw.get("expected")
        or raw.get("expected_result")
        or raw.get("expected_results")
        or ""
    )
    if isinstance(expected, list):
        expected = "\n".join(str(x) for x in expected)

    precondition = (
        raw.get("precondition")
        or raw.get("preconditions")
        or ""
    )
    if isinstance(precondition, list):
        precondition = "\n".join(str(x) for x in precondition)

    case_name = (
        raw.get("title")
        or raw.get("case_name")
        or raw.get("name")
        or f"【{raw.get('type','')}】{raw.get('test_point_name','未命名用例')}"
    )

    is_focus = raw.get("origin") == "mandatory"
    coverage_item = raw.get("coverage_item")

    tags = ["功能测试"]
    if is_focus:
        tags.append("重点测试")

    remark = ""
    if coverage_item:
        remark = f"重点覆盖：{coverage_item}"

    return {
        "case_name": clean_case_name(case_name),
        "module": clean_module_name(raw.get("module", "")),
        "tags": ",".join(tags),
        "precondition": precondition,
        "steps": steps,
        "expected": expected,
        "priority": infer_priority(steps, is_focus=is_focus),
        "automatable": infer_automatable(steps),
        "remark": remark,
    }


# ===============================
# ⭐ 原始导出逻辑（增强重点可见性）
# ===============================
def export_excel(raw_cases: list, save_path: str) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = "测试用例"

    ws.append(HEADERS)
    ws.freeze_panes = "A2"

    wrap = Alignment(wrap_text=True, vertical="top")

    flat_cases = flatten_cases(raw_cases)

    for raw in flat_cases:
        c = normalize_case(raw)

        ws.append([
            _cell(c["case_name"]),
            _cell(c["module"]),
            c["tags"],
            _cell(c["precondition"]),
            _cell(c["steps"]),
            _cell(c["expected"]),
            "STEP",
            _cell(c["remark"]),
            "未开始",
            "",
            c["priority"],
            c["automatable"],
            "否"
        ])

        row = ws.max_row
        ws[f"E{row}"].alignment = wrap
        ws[f"F{row}"].alignment = wrap
        ws[f"H{row}"].alignment = wrap

    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["D"].width = 35
    ws.column_dimensions["E"].width = 70
    ws.column_dimensions["F"].width = 60
    ws.column_dimensions["H"].width = 40

    wb.save(save_path)
    return save_path


# =====================================================
# ✅ Workflow / Router 唯一依赖入口（保持不动）
# =====================================================
def export_cases_to_excel(
    cases: List[Dict[str, Any]],
    workflow_id: str,
) -> str:
    os.makedirs(TMP_DIR, exist_ok=True)
    save_path = os.path.join(TMP_DIR, f"{workflow_id}.xlsx")
    return export_excel(cases, save_path)
