#! /usr/bin/python3
# coding=utf-8
# @Author: sulo
# app/analysis_app/ocr/pdf_ocr.py

"""
PDF OCR Service

职责：
- 对 PDF 页面图片执行 OCR
- 输入：PDFPageImage 列表
- 输出：合并后的 OCR 文本 / 分页 OCR 文本
- 不做业务判断
- 不依赖 pipeline
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
import logging
import re

import pytesseract

from app.services.pdf_images import PDFPageImage


logger = logging.getLogger(__name__)


# =====================================================
# 配置
# =====================================================

DEFAULT_OCR_LANG = "chi_sim+eng"
DEFAULT_OCR_PSM = 6
MIN_OCR_TEXT_LEN = 3


# =====================================================
# 协议对象
# =====================================================

@dataclass
class OCRPageResult:
    """
    单页 OCR 结果
    """
    page: int
    text: str
    success: bool = True
    error: Optional[str] = None


@dataclass
class OCRResult:
    """
    OCR 总结果
    """
    full_text: str
    pages: List[OCRPageResult] = field(default_factory=list)
    total_pages: int = 0
    success_pages: int = 0
    failed_pages: int = 0


# =====================================================
# 工具函数
# =====================================================

def clean_ocr_text(text: str) -> str:
    """
    OCR 文本清洗：
    - 去首尾空白
    - 合并连续空格
    - 压缩连续空行
    """
    text = (text or "").strip()
    if not text:
        return ""

    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[|¦‖]+", " ", text)
    text = re.sub(r"[_]{2,}", " ", text)
    text = re.sub(r"[·•]{2,}", " ", text)
    return text.strip()


def extract_text_from_image(
    image: object,
    *,
    lang: str = DEFAULT_OCR_LANG,
    psm: int = DEFAULT_OCR_PSM,
) -> str:
    """
    对单张图片执行 OCR
    """
    if image is None:
        return ""

    try:
        text = pytesseract.image_to_string(
            image,
            lang=lang,
            config=f"--psm {psm}",
        )
        return clean_ocr_text(text)
    except Exception as e:
        logger.exception("extract_text_from_image failed")
        raise RuntimeError(str(e)) from e


# =====================================================
# 主函数
# =====================================================

def ocr_pdf_images(
    images: List[PDFPageImage],
    *,
    lang: str = DEFAULT_OCR_LANG,
    psm: int = DEFAULT_OCR_PSM,
    min_text_len: int = MIN_OCR_TEXT_LEN,
    with_page_tag: bool = True,
) -> OCRResult:
    """
    对 PDF 页面图片列表执行 OCR

    :param images: PDFPageImage 列表
    :param lang: OCR 语言，默认 chi_sim+eng
    :param psm: tesseract PSM 模式
    :param min_text_len: 最小有效文本长度
    :param with_page_tag: full_text 是否带页码标签
    :return: OCRResult
    """
    if not images:
        return OCRResult(
            full_text="",
            pages=[],
            total_pages=0,
            success_pages=0,
            failed_pages=0,
        )

    page_results: List[OCRPageResult] = []
    full_parts: List[str] = []

    for item in images:
        page_no = int(getattr(item, "page", 0) or 0)

        try:
            text = extract_text_from_image(
                getattr(item, "image", None),
                lang=lang,
                psm=psm,
            )

            if len(text) < min_text_len:
                text = ""

            page_result = OCRPageResult(
                page=page_no,
                text=text,
                success=True,
                error=None,
            )
            page_results.append(page_result)

            if text:
                if with_page_tag:
                    full_parts.append(f"【第 {page_no} 页 OCR】\n{text}")
                else:
                    full_parts.append(text)

        except Exception as e:
            logger.exception("ocr_pdf_images failed on page=%s", page_no)
            page_results.append(
                OCRPageResult(
                    page=page_no,
                    text="",
                    success=False,
                    error=str(e),
                )
            )

    success_pages = sum(1 for x in page_results if x.success)
    failed_pages = sum(1 for x in page_results if not x.success)

    return OCRResult(
        full_text="\n\n".join([x for x in full_parts if x]).strip(),
        pages=page_results,
        total_pages=len(images),
        success_pages=success_pages,
        failed_pages=failed_pages,
    )


def ocr_pdf_images_to_text(
    images: List[PDFPageImage],
    *,
    lang: str = DEFAULT_OCR_LANG,
    psm: int = DEFAULT_OCR_PSM,
    min_text_len: int = MIN_OCR_TEXT_LEN,
    with_page_tag: bool = True,
) -> str:
    """
    兼容接口：只返回 OCR 全文本
    """
    result = ocr_pdf_images(
        images=images,
        lang=lang,
        psm=psm,
        min_text_len=min_text_len,
        with_page_tag=with_page_tag,
    )
    return result.full_text


def ocr_single_pdf_page_image(
    image: PDFPageImage,
    *,
    lang: str = DEFAULT_OCR_LANG,
    psm: int = DEFAULT_OCR_PSM,
) -> OCRPageResult:
    """
    单页 OCR，便于局部调试
    """
    try:
        text = extract_text_from_image(
            getattr(image, "image", None),
            lang=lang,
            psm=psm,
        )
        return OCRPageResult(
            page=int(getattr(image, "page", 0) or 0),
            text=text,
            success=True,
            error=None,
        )
    except Exception as e:
        return OCRPageResult(
            page=int(getattr(image, "page", 0) or 0),
            text="",
            success=False,
            error=str(e),
        )