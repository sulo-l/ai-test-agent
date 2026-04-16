# app/services/requirement_preparer.py
# -*- coding: utf-8 -*-
"""
Requirement Preparation Service
================================
PDF → AI 可用需求输入的【唯一共享准备层】

供：
- /workflow/analyze/stream
- /workflow/generate/stream

统一使用

职责：
1. 提取 PDF 标准文本
2. 对文本不足页面做 OCR 兜底
3. 接入页面图片资源（供后续 UI Vision 使用）
4. 归一化需求块 / 需求句
5. 输出统一 PreparedRequirement
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple

import pdfplumber
import pytesseract


logger = logging.getLogger(__name__)


# =====================================================
# 配置
# =====================================================

MIN_AI_TEXT_LENGTH = 300
MIN_PAGE_TEXT_LENGTH = 80
MIN_PAGE_OCR_LENGTH = 50
DEFAULT_RENDER_DPI = 300
ENABLE_OCR = True
OCR_PSM = 6

NON_REQUIREMENT_PATTERNS = [
    r"版权声明",
    r"免责声明",
    r"本文件仅供",
    r"未经许可",
]

REQUIREMENT_HINT_PATTERNS = [
    r"功能",
    r"需求",
    r"支持",
    r"应当",
    r"需要",
    r"必须",
    r"校验",
    r"规则",
    r"流程",
    r"字段",
    r"接口",
    r"展示",
    r"按钮",
    r"页面",
    r"弹窗",
    r"列表",
    r"状态",
    r"上传",
    r"下载",
    r"跳转",
    r"限制",
    r"前置条件",
    r"成功",
    r"失败",
    r"异常",
    r"提示",
    r"风控",
    r"KYC",
    r"登录",
    r"默认值",
    r"交互",
    r"逻辑",
    r"计算",
    r"公式",
    r"颜色",
    r"样式",
    r"图标",
    r"埋点",
    r"配置",
]

IMAGE_PAGE_HINT_PATTERNS = [
    r"原型",
    r"示意图",
    r"流程图",
    r"页面示例",
    r"交互图",
    r"设计稿",
    r"UI",
    r"线框图",
]


# =====================================================
# 协议对象
# =====================================================

@dataclass
class PreparedRequirement:
    """
    AI 输入准备完成后的统一结构
    """
    final_text: str
    clean_sentences: List[str]
    requirement_blocks: List[str]
    pages: List[dict]
    confirmed_text: str
    ocr_text: Optional[str]
    page_images: List[dict] = field(default_factory=list)
    usable_for_ai: bool = False
    confidence: str = "LOW"
    requirement_id: Optional[str] = None
    total_pages: int = 0
    text_pages: int = 0
    ocr_pages: int = 0
    image_like_pages: List[int] = field(default_factory=list)


# =====================================================
# 工具函数
# =====================================================

def clean_text(text: str) -> str:
    """
    基础清洗：
    - 保留换行
    - 仅压缩行内空白
    - 清理连续空行
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    text = text.replace("\u3000", " ")

    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_ocr_text(text: str) -> str:
    """
    OCR 文本比标准文本噪声更高，额外做一些清洗
    """
    text = clean_text(text)
    if not text:
        return ""

    text = re.sub(r"[|¦‖]+", " ", text)
    text = re.sub(r"[_]{2,}", " ", text)
    text = re.sub(r"[·•]{2,}", " ", text)

    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def looks_like_requirement(text: str) -> bool:
    """
    粗粒度判断一段文本是否像需求内容
    放宽规则，避免漏掉有效需求
    """
    text = clean_text(text)
    if not text or len(text) < 6:
        return False

    for pat in NON_REQUIREMENT_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return False

    for pat in REQUIREMENT_HINT_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return True

    # 放宽：带编号 / 公式 / 配置项 / 样式项 / 英文变量名，也认为可能是需求
    if re.search(r"^\s*[\d一二三四五六七八九十]+[.、）)]", text):
        return True
    if re.search(r"[A-Za-z_]{2,}\s*[:=]", text):
        return True
    if re.search(r"(红涨绿跌|绿涨红跌|美国线|折线图|面积图|平均K线图|Heikin Ashi|HA_Open|HA_Close)", text, flags=re.IGNORECASE):
        return True

    return len(text) >= 20


def looks_like_image_page(text: str) -> bool:
    text = clean_text(text)
    if not text:
        return True

    for pat in IMAGE_PAGE_HINT_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return True

    return False


def split_requirement_sentences(text: str) -> List[str]:
    """
    按标点和换行拆句
    """
    if not text:
        return []

    parts = re.split(r"[。！？；;\n]+", text)
    results: List[str] = []

    for item in parts:
        s = clean_text(item)
        if len(s) >= 8 and looks_like_requirement(s):
            results.append(s)

    return results


def split_requirement_blocks(text: str) -> List[str]:
    """
    保留段落级需求块
    """
    if not text:
        return []

    raw_blocks = re.split(r"\n{2,}", text)
    blocks: List[str] = []

    for block in raw_blocks:
        b = clean_text(block)
        if len(b) >= 10 and looks_like_requirement(b):
            blocks.append(b)

    return blocks


def dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    results = []

    for item in items:
        norm = clean_text(item)
        if not norm:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        results.append(norm)

    return results


def dedupe_ints_keep_order(items: List[int]) -> List[int]:
    seen = set()
    results: List[int] = []

    for item in items:
        try:
            value = int(item)
        except Exception:
            continue
        if value in seen:
            continue
        seen.add(value)
        results.append(value)

    return results


def extract_page_text(page: Any) -> str:
    """
    提取页面标准文本
    """
    try:
        text = page.extract_text(
            x_tolerance=2,
            y_tolerance=2,
            layout=False,
        ) or ""
        return clean_text(text)
    except Exception:
        return ""


def extract_page_char_text(page: Any) -> str:
    """
    字符级兜底
    """
    try:
        chars = page.chars or []
        text = "".join(c.get("text", "") for c in chars if c.get("text"))
        return clean_text(text)
    except Exception:
        return ""


def should_run_ocr(page_text: str, char_text: str) -> bool:
    best_len = max(len(page_text or ""), len(char_text or ""))
    return best_len < MIN_PAGE_TEXT_LENGTH


def extract_ocr_text_from_page_image(page_image: Any) -> str:
    if page_image is None:
        return ""

    try:
        text = pytesseract.image_to_string(
            page_image,
            lang="chi_sim+eng",
            config=f"--psm {OCR_PSM}",
        )
        return normalize_ocr_text(text)
    except Exception:
        return ""


def merge_page_confirmed_text(page_text: str, char_text: str) -> Tuple[str, str]:
    if page_text and len(page_text) >= MIN_PAGE_TEXT_LENGTH:
        return page_text, "HIGH"

    if char_text and len(char_text) >= MIN_PAGE_TEXT_LENGTH:
        return char_text, "MEDIUM"

    if len(page_text) >= len(char_text):
        return page_text, "LOW"
    return char_text, "LOW"


def build_page_image_map(pdf_path: str) -> Dict[int, Dict[str, Any]]:
    # Deprecated: kept for compatibility but no longer used in main pipeline.
    # Use _render_page_image() for lazy per-page rendering instead.
    return {}


def _render_page_image(page: Any, page_no: int) -> Dict[str, Any]:
    """
    按需渲染单页图片，避免一次性把整个 PDF 所有页加载到内存。
    """
    try:
        rendered = page.to_image(resolution=DEFAULT_RENDER_DPI)
        pil_image = getattr(rendered, "original", None)
        if pil_image is None:
            return {}
        size = getattr(pil_image, "size", None)
        if not size or len(size) != 2:
            return {}
        width, height = int(size[0]), int(size[1])
        return {
            "page": page_no,
            "image": pil_image,
            "dpi": DEFAULT_RENDER_DPI,
            "width": width,
            "height": height,
            "image_path": None,
        }
    except Exception:
        logger.warning("render page image failed: page=%s", page_no)
        return {}


def calc_overall_confidence(
    usable_for_ai: bool,
    text_pages: int,
    ocr_pages: int,
    total_pages: int,
) -> str:
    if total_pages <= 0:
        return "LOW"

    if usable_for_ai and text_pages / total_pages >= 0.6:
        return "HIGH"

    if usable_for_ai or (text_pages + ocr_pages) / total_pages >= 0.6:
        return "MEDIUM"

    return "LOW"


def build_final_ai_text(
    requirement_blocks: List[str],
    confirmed_text: str,
    ocr_text: Optional[str],
) -> str:
    """
    新策略：
    1. 原始确认文本为主
    2. OCR 做补充
    3. 需求锚定块作为结构化补充
    """
    final_parts: List[str] = []

    if confirmed_text:
        final_parts.append("【原始确认文本】")
        final_parts.append(confirmed_text)

    if ocr_text:
        final_parts.append("\n【OCR补充文本】")
        final_parts.append(ocr_text)

    if requirement_blocks:
        final_parts.append("\n【需求锚定块】")
        final_parts.append("\n".join(requirement_blocks))

    final_text = "\n".join(p for p in final_parts if p).strip()

    # 如果上面因为某些特殊情况没拿到，用 requirement_blocks 再兜底
    if not final_text and requirement_blocks:
        final_text = "\n".join(requirement_blocks).strip()

    return final_text


# =====================================================
# 核心实现：PDF → PreparedRequirement
# =====================================================

def prepare_requirement_from_pdf(
    pdf_path: str,
    requirement_id: Optional[str] = None,
) -> PreparedRequirement:
    if not pdf_path:
        raise ValueError("pdf_path is required")

    pages_result: List[dict] = []
    confirmed_all: List[str] = []
    requirement_blocks: List[str] = []
    clean_requirement_sentences: List[str] = []
    ocr_all: List[str] = []
    page_images_result: List[dict] = []
    image_like_pages: List[int] = []

    text_pages = 0
    ocr_pages = 0
    total_pages = 0

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)

            for page_index, page in enumerate(pdf.pages):
                page_no = page_index + 1

                page_text = extract_page_text(page)
                char_text = extract_page_char_text(page)
                page_confirmed, page_confidence = merge_page_confirmed_text(page_text, char_text)

                page_ocr = ""
                page_source = "none"
                page_image_info: Dict[str, Any] = {}

                has_confirmed = bool(page_confirmed)

                if has_confirmed:
                    text_pages += 1
                    page_source = "text"

                if ENABLE_OCR and should_run_ocr(page_text, char_text):
                    # 仅在需要 OCR 时才渲染当前页为图片（懒加载，避免大 PDF OOM）
                    page_image_info = _render_page_image(page, page_no)
                    page_image = page_image_info.get("image")
                    page_ocr = extract_ocr_text_from_page_image(page_image)
                    # 渲染图片用完即释放，不在内存中保留
                    if "image" in page_image_info:
                        del page_image_info["image"]

                    if page_ocr and len(page_ocr) >= MIN_PAGE_OCR_LENGTH:
                        ocr_pages += 1
                        page_source = "text+ocr" if has_confirmed else "ocr"

                merged_for_requirement = page_confirmed or page_ocr or ""

                is_image_like = looks_like_image_page(merged_for_requirement)
                if is_image_like:
                    image_like_pages.append(page_no)

                page_record = {
                    "page": page_no,
                    "confirmed_text": page_confirmed,
                    "ocr_text": page_ocr,
                    "confidence": page_confidence,
                    "source": page_source,
                    "width": page_image_info.get("width"),
                    "height": page_image_info.get("height"),
                    "dpi": page_image_info.get("dpi"),
                    "image_path": page_image_info.get("image_path"),
                    "has_image": bool(page_image_info),
                    "image_like": is_image_like,
                }
                pages_result.append(page_record)

                if page_confirmed:
                    confirmed_all.append(f"【第 {page_no} 页】\n{page_confirmed}")

                if page_ocr:
                    ocr_all.append(f"【第 {page_no} 页 OCR】\n{page_ocr}")

                if merged_for_requirement:
                    page_blocks = split_requirement_blocks(merged_for_requirement)
                    for block in page_blocks:
                        requirement_blocks.append(f"【第 {page_no} 页】{block}")

                    page_sentences = split_requirement_sentences(merged_for_requirement)
                    clean_requirement_sentences.extend(page_sentences)

    except Exception as e:
        logger.exception("prepare_requirement_from_pdf failed: pdf=%s", pdf_path)
        raise RuntimeError(f"prepare_requirement_from_pdf failed: {e}") from e

    confirmed_text = "\n\n".join(confirmed_all).strip()
    ocr_text = "\n\n".join(ocr_all).strip() if ocr_all else None

    requirement_blocks = dedupe_keep_order(requirement_blocks)
    clean_requirement_sentences = dedupe_keep_order(clean_requirement_sentences)
    image_like_pages = dedupe_ints_keep_order(image_like_pages)

    final_text = build_final_ai_text(
        requirement_blocks=requirement_blocks,
        confirmed_text=confirmed_text,
        ocr_text=ocr_text,
    )

    usable_for_ai = len(final_text) >= MIN_AI_TEXT_LENGTH

    confidence = calc_overall_confidence(
        usable_for_ai=usable_for_ai,
        text_pages=text_pages,
        ocr_pages=ocr_pages,
        total_pages=total_pages,
    )

    logger.info(
        "prepare_requirement_from_pdf done: pages=%s text_pages=%s ocr_pages=%s image_pages=%s usable=%s confidence=%s final_len=%s confirmed_len=%s ocr_len=%s blocks=%s",
        total_pages,
        text_pages,
        ocr_pages,
        len(page_images_result),
        usable_for_ai,
        confidence,
        len(final_text),
        len(confirmed_text),
        len(ocr_text or ""),
        len(requirement_blocks),
    )

    return PreparedRequirement(
        final_text=final_text,
        clean_sentences=clean_requirement_sentences,
        requirement_blocks=requirement_blocks,
        pages=pages_result,
        confirmed_text=confirmed_text,
        ocr_text=ocr_text,
        page_images=page_images_result,
        usable_for_ai=usable_for_ai,
        confidence=confidence,
        requirement_id=requirement_id,
        total_pages=total_pages,
        text_pages=text_pages,
        ocr_pages=ocr_pages,
        image_like_pages=image_like_pages,
    )


# =====================================================
# 系统级统一入口
# =====================================================

def prepare_requirement(
    pdf_path: str,
    requirement_id: Optional[str] = None,
) -> PreparedRequirement:
    if not pdf_path:
        raise ValueError("pdf_path is required")

    return prepare_requirement_from_pdf(
        pdf_path=pdf_path,
        requirement_id=requirement_id,
    )