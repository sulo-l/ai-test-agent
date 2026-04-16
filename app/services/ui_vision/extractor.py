#! /usr/bin/python3
# coding=utf-8
# @Author: sulo

"""
UI Vision Extractor (Protocol-Level)

职责：
- 输入：PDFPageImage 列表
- 输出：UI Schema（结构化 UI 描述）
- 仅做 UI 结构抽取，不涉及测试语义

当前实现：
- OCR 文本识别
- 按行聚合 OCR 结果
- 简单 UI 类型识别
- BoundingBox 解析
- 元素去重

后续可扩展：
- PaddleOCR Layout
- YOLO UI Detection
- LLM Vision
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Literal, Dict, Any
import uuid
import re

import pytesseract

from app.services.pdf_images import PDFPageImage


# =====================================================
# 协议枚举
# =====================================================

UIElementType = Literal[
    "button",
    "input",
    "select",
    "checkbox",
    "radio",
    "text",
    "image",
    "table",
    "link",
    "icon",
    "chart",
    "tab",
    "dialog",
    "sheet",
    "unknown",
]


# =====================================================
# 协议对象
# =====================================================

@dataclass
class BoundingBox:
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass
class UIElement:
    element_id: str
    type: UIElementType

    text: Optional[str] = None
    label: Optional[str] = None

    bbox: Optional[BoundingBox] = None
    confidence: float = 0.0


@dataclass
class UIPageSchema:
    page: int
    elements: List[UIElement] = field(default_factory=list)


@dataclass
class UISchema:
    pages: List[UIPageSchema] = field(default_factory=list)


# =====================================================
# 规则配置
# =====================================================

BUTTON_HINT = [
    "提交", "确认", "保存", "登录", "注册",
    "下一步", "确定", "完成", "发送", "应用",
    "取消", "关闭", "新增", "删除", "编辑",
]

INPUT_HINT = [
    "用户名", "账号", "密码", "手机号",
    "邮箱", "验证码", "金额", "输入",
    "搜索", "请输入",
]

SELECT_HINT = [
    "选择", "下拉", "类型", "分类",
    "样式", "图表样式", "k线样式", "筛选",
]

TABLE_HINT = [
    "列表", "记录", "订单", "流水",
    "时间", "状态", "金额", "数据",
    "名称", "价格", "数量",
]

LINK_HINT = [
    "查看", "详情", "跳转", "进入",
    "更多", "去设置", "去查看",
]

TAB_HINT = [
    "1m", "5m", "15m", "30m",
    "1h", "4h", "1d", "1w",
    "分时", "日线", "周线", "月线",
    "tab", "切换",
]

CHART_HINT = [
    "图表", "k线", "美国线", "折线图",
    "面积图", "平均k线", "heikin ashi",
    "走势图", "蜡烛图", "均线",
]

DIALOG_HINT = [
    "弹窗", "确认弹窗", "提示弹窗",
    "dialog", "对话框",
]

SHEET_HINT = [
    "bottom sheet", "sheet", "底部弹层",
    "底部菜单", "底部面板", "选择样式",
]

ICON_HINT = [
    "齿轮", "设置", "图标", "眼睛",
    "关闭", "返回",
]


# =====================================================
# 工具函数
# =====================================================

def normalize_text(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def detect_element_type(text: str) -> UIElementType:
    t = normalize_text(text).lower()
    if not t:
        return "unknown"

    for k in SHEET_HINT:
        if k.lower() in t:
            return "sheet"

    for k in DIALOG_HINT:
        if k.lower() in t:
            return "dialog"

    for k in CHART_HINT:
        if k.lower() in t:
            return "chart"

    for k in TAB_HINT:
        if k.lower() in t:
            return "tab"

    for k in BUTTON_HINT:
        if k.lower() in t:
            return "button"

    for k in INPUT_HINT:
        if k.lower() in t:
            return "input"

    for k in SELECT_HINT:
        if k.lower() in t:
            return "select"

    for k in TABLE_HINT:
        if k.lower() in t:
            return "table"

    for k in LINK_HINT:
        if k.lower() in t:
            return "link"

    for k in ICON_HINT:
        if k.lower() in t:
            return "icon"

    return "text"


def create_element_id(page: int) -> str:
    return f"ui_{page}_{uuid.uuid4().hex[:8]}"


def merge_bbox(items: List[Dict[str, Any]]) -> Optional[BoundingBox]:
    if not items:
        return None

    try:
        x1 = min(int(it["left"]) for it in items)
        y1 = min(int(it["top"]) for it in items)
        x2 = max(int(it["left"]) + int(it["width"]) for it in items)
        y2 = max(int(it["top"]) + int(it["height"]) for it in items)
        return BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)
    except Exception:
        return None


def avg_confidence(items: List[Dict[str, Any]]) -> float:
    scores: List[float] = []

    for it in items:
        try:
            conf = float(it.get("conf", -1))
            if conf >= 0:
                scores.append(conf / 100.0)
        except Exception:
            continue

    if not scores:
        return 0.5

    return round(sum(scores) / len(scores), 4)


def should_keep_text_line(text: str) -> bool:
    t = normalize_text(text)
    if not t:
        return False

    if len(t) == 1 and t not in {"+", "-", "%"}:
        return False

    return True


def dedupe_elements(elements: List[UIElement]) -> List[UIElement]:
    seen = set()
    results: List[UIElement] = []

    for el in elements:
        text = normalize_text(el.text or "")
        label = normalize_text(el.label or "")
        key = (el.type, text, label)

        if key in seen:
            continue

        seen.add(key)
        results.append(el)

    return results


# =====================================================
# OCR 抽取
# =====================================================

def extract_ocr_elements(image, *, page_no: int) -> List[UIElement]:
    """
    通过 pytesseract.image_to_data 提取 OCR，并按“行”聚合成更稳定的 UIElement
    """
    elements: List[UIElement] = []

    try:
        data = pytesseract.image_to_data(
            image,
            lang="chi_sim+eng",
            output_type=pytesseract.Output.DICT,
            config="--psm 6",
        )

        total = len(data.get("text", []))
        if total <= 0:
            return elements

        grouped: Dict[str, List[Dict[str, Any]]] = {}

        for i in range(total):
            text = normalize_text(data["text"][i])
            if not should_keep_text_line(text):
                continue

            block_num = data.get("block_num", [0] * total)[i]
            par_num = data.get("par_num", [0] * total)[i]
            line_num = data.get("line_num", [0] * total)[i]

            key = f"{block_num}-{par_num}-{line_num}"
            grouped.setdefault(key, [])
            grouped[key].append({
                "text": text,
                "left": data["left"][i],
                "top": data["top"][i],
                "width": data["width"][i],
                "height": data["height"][i],
                "conf": data.get("conf", [-1] * total)[i],
            })

        for _, items in grouped.items():
            line_text = normalize_text(" ".join(it["text"] for it in items if it["text"]))
            if not should_keep_text_line(line_text):
                continue

            bbox = merge_bbox(items)
            element_type = detect_element_type(line_text)

            elements.append(
                UIElement(
                    element_id=create_element_id(page_no),
                    type=element_type,
                    text=line_text,
                    label=line_text,
                    bbox=bbox,
                    confidence=avg_confidence(items),
                )
            )

    except Exception:
        return []

    return dedupe_elements(elements)


# =====================================================
# 主 Extractor
# =====================================================

class UIVisionExtractor:
    """
    UI Vision 抽取器
    """

    def extract(self, pages: List[PDFPageImage]) -> UISchema:
        schema = UISchema()

        for page_img in pages or []:
            page_schema = UIPageSchema(page=page_img.page)

            try:
                elements = extract_ocr_elements(
                    page_img.image,
                    page_no=page_img.page,
                )
                page_schema.elements = elements
            except Exception:
                page_schema.elements = []

            schema.pages.append(page_schema)

        return schema