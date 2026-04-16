#! /usr/bin/python3
# coding=utf-8
# @Author: sulo

"""
UI Vision Rules (Protocol-Level)

职责：
- 基于 OCR 文本 + 规则识别 UI 元素
- 输出统一 UIElement
- 不涉及测试语义

增强点：
- 支持 chart / tab / dialog / sheet
- 支持更丰富的 button / select / icon 识别
- 支持去重
- 支持按行解析 OCR 文本
"""

from __future__ import annotations

from typing import List, Optional
import uuid
import re

from app.services.ui_vision.extractor import (
    UIElement,
    UIElementType,
)


# =====================================================
# Rule Engine
# =====================================================

class UIVisionRuleEngine:
    """
    基于规则的 UI 识别器
    """

    def detect(self, ocr_text: str) -> List[UIElement]:
        text = _normalize_text(ocr_text)
        if not text:
            return []

        elements: List[UIElement] = []

        elements.extend(_detect_charts(text))
        elements.extend(_detect_tabs(text))
        elements.extend(_detect_dialogs(text))
        elements.extend(_detect_sheets(text))
        elements.extend(_detect_buttons(text))
        elements.extend(_detect_inputs(text))
        elements.extend(_detect_selects(text))
        elements.extend(_detect_tables(text))
        elements.extend(_detect_links(text))
        elements.extend(_detect_checkboxes(text))
        elements.extend(_detect_radios(text))
        elements.extend(_detect_icons(text))
        elements.extend(_detect_texts(text))

        return _dedupe_elements(elements)


# =====================================================
# 工具
# =====================================================

def _normalize_text(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _split_lines(text: str) -> List[str]:
    if not text:
        return []
    raw_lines = re.split(r"[\n\r]+", str(text))
    lines = []
    for line in raw_lines:
        s = _normalize_text(line)
        if s:
            lines.append(s)
    return lines


def _new_element(
    *,
    element_type: UIElementType,
    text: Optional[str] = None,
    label: Optional[str] = None,
    confidence: float = 0.6,
) -> UIElement:
    return UIElement(
        element_id=str(uuid.uuid4()),
        type=element_type,
        text=text,
        label=label,
        bbox=None,
        confidence=confidence,
    )


def _dedupe_elements(elements: List[UIElement]) -> List[UIElement]:
    seen = set()
    results: List[UIElement] = []

    for el in elements:
        key = (
            str(el.type or "").strip(),
            _normalize_text(el.text or ""),
            _normalize_text(el.label or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        results.append(el)

    return results


def _contains_any(text: str, keywords: List[str]) -> bool:
    t = _normalize_text(text).lower()
    for k in keywords:
        if _normalize_text(k).lower() in t:
            return True
    return False


# =====================================================
# Chart
# =====================================================

def _detect_charts(text: str) -> List[UIElement]:
    keywords = [
        "图表", "k线", "K线", "美国线", "折线图", "面积图",
        "平均K线", "heikin ashi", "走势图", "蜡烛图", "均线",
    ]

    elements: List[UIElement] = []

    if _contains_any(text, keywords):
        label = "chart"
        for line in _split_lines(text):
            if _contains_any(line, keywords) and len(line) <= 50:
                label = line
                break

        elements.append(
            _new_element(
                element_type="chart",
                label=label,
                text=label,
                confidence=0.85,
            )
        )

    return elements


# =====================================================
# Tab
# =====================================================

def _detect_tabs(text: str) -> List[UIElement]:
    patterns = [
        r"\b1m\b", r"\b5m\b", r"\b15m\b", r"\b30m\b",
        r"\b1h\b", r"\b4h\b", r"\b1d\b", r"\b1w\b",
        r"\b1M\b", r"\bALL\b",
    ]

    keywords = [
        "分时", "日线", "周线", "月线", "切换", "tab", "TAB",
    ]

    elements: List[UIElement] = []

    for line in _split_lines(text):
        low = line.lower()

        hit = False
        for pat in patterns:
            if re.search(pat, low, flags=re.IGNORECASE):
                hit = True
                break

        if not hit and not _contains_any(line, keywords):
            continue

        elements.append(
            _new_element(
                element_type="tab",
                text=line[:50],
                label=line[:50],
                confidence=0.75,
            )
        )

    return elements


# =====================================================
# Dialog
# =====================================================

def _detect_dialogs(text: str) -> List[UIElement]:
    keywords = [
        "弹窗", "确认弹窗", "提示弹窗", "对话框", "dialog", "Dialog",
    ]

    if not _contains_any(text, keywords):
        return []

    return [
        _new_element(
            element_type="dialog",
            label="dialog",
            text="dialog",
            confidence=0.8,
        )
    ]


# =====================================================
# Sheet
# =====================================================

def _detect_sheets(text: str) -> List[UIElement]:
    keywords = [
        "bottom sheet", "Bottom Sheet", "sheet", "底部弹层",
        "底部菜单", "底部面板", "选择样式", "图表样式", "K线样式",
    ]

    if not _contains_any(text, keywords):
        return []

    label = "sheet"
    for line in _split_lines(text):
        if _contains_any(line, keywords) and len(line) <= 50:
            label = line
            break

    return [
        _new_element(
            element_type="sheet",
            label=label,
            text=label,
            confidence=0.85,
        )
    ]


# =====================================================
# Button
# =====================================================

def _detect_buttons(text: str) -> List[UIElement]:
    keywords = [
        "提交", "确认", "保存", "登录", "注册",
        "取消", "返回", "下一步", "完成",
        "发送", "搜索", "查询", "应用",
        "删除", "新增", "编辑", "关闭",
    ]

    elements: List[UIElement] = []

    for line in _split_lines(text):
        for k in keywords:
            if k in line:
                elements.append(
                    _new_element(
                        element_type="button",
                        text=k,
                        label=line[:50],
                        confidence=0.8,
                    )
                )

    return elements


# =====================================================
# Input
# =====================================================

def _detect_inputs(text: str) -> List[UIElement]:
    patterns = [
        "用户名", "账号", "密码", "手机号", "邮箱",
        "验证码", "金额", "输入", "搜索", "请输入",
    ]

    elements: List[UIElement] = []

    for line in _split_lines(text):
        for p in patterns:
            if p in line:
                elements.append(
                    _new_element(
                        element_type="input",
                        text=line[:50],
                        label=p,
                        confidence=0.75,
                    )
                )

    return elements


# =====================================================
# Select
# =====================================================

def _detect_selects(text: str) -> List[UIElement]:
    patterns = [
        "选择", "请选择", "类型", "分类", "下拉",
        "样式", "图表样式", "K线样式", "筛选",
    ]

    elements: List[UIElement] = []

    for line in _split_lines(text):
        for p in patterns:
            if p in line:
                elements.append(
                    _new_element(
                        element_type="select",
                        text=line[:50],
                        label=p,
                        confidence=0.75,
                    )
                )

    return elements


# =====================================================
# Table
# =====================================================

def _detect_tables(text: str) -> List[UIElement]:
    keywords = [
        "列表", "记录", "订单", "流水", "时间",
        "状态", "金额", "数据", "名称", "价格", "数量",
    ]

    if _contains_any(text, keywords):
        return [
            _new_element(
                element_type="table",
                label="data_table",
                text="data_table",
                confidence=0.75,
            )
        ]

    return []


# =====================================================
# Link
# =====================================================

def _detect_links(text: str) -> List[UIElement]:
    keywords = [
        "查看", "详情", "跳转", "进入", "更多",
        "去设置", "去查看",
    ]

    elements: List[UIElement] = []

    for line in _split_lines(text):
        for k in keywords:
            if k in line:
                elements.append(
                    _new_element(
                        element_type="link",
                        text=k,
                        label=line[:50],
                        confidence=0.72,
                    )
                )

    return elements


# =====================================================
# Checkbox
# =====================================================

def _detect_checkboxes(text: str) -> List[UIElement]:
    keywords = [
        "同意", "勾选", "我已阅读", "我已同意",
    ]

    if not _contains_any(text, keywords):
        return []

    return [
        _new_element(
            element_type="checkbox",
            label="checkbox_option",
            text="checkbox_option",
            confidence=0.65,
        )
    ]


# =====================================================
# Radio
# =====================================================

def _detect_radios(text: str) -> List[UIElement]:
    keywords = [
        "单选", "选中", "默认", "当前选中",
    ]

    if not _contains_any(text, keywords):
        return []

    return [
        _new_element(
            element_type="radio",
            label="radio_option",
            text="radio_option",
            confidence=0.65,
        )
    ]


# =====================================================
# Icon
# =====================================================

def _detect_icons(text: str) -> List[UIElement]:
    keywords = [
        "隐藏", "显示", "眼睛", "可见",
        "齿轮", "设置", "返回", "关闭",
    ]

    elements: List[UIElement] = []

    for line in _split_lines(text):
        for k in keywords:
            if k in line:
                elements.append(
                    _new_element(
                        element_type="icon",
                        text=k,
                        label=line[:50],
                        confidence=0.65,
                    )
                )

    return elements


# =====================================================
# Text
# =====================================================

def _detect_texts(text: str) -> List[UIElement]:
    lines = _split_lines(text)
    elements: List[UIElement] = []

    keep_keywords = [
        "提示", "说明", "规则", "默认", "计算",
        "统计", "涨跌", "颜色", "当前选中",
        "记忆", "保存", "曝光", "点击",
        "异常", "成功", "失败",
    ]

    for line in lines:
        if not (4 <= len(line) <= 80):
            continue

        if _contains_any(line, keep_keywords):
            elements.append(
                _new_element(
                    element_type="text",
                    text=line,
                    label=line[:50],
                    confidence=0.55,
                )
            )

    return elements