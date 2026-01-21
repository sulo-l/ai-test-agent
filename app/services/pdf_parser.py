# -*- coding: utf-8 -*-

import pdfplumber
import pytesseract
from PIL import Image
import os
import re


MIN_AI_TEXT_LENGTH = 300   # ⭐ AI 分析最小文本长度（工程经验值）


def clean_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def parse_pdf(pdf_path: str) -> dict:
    """
    工程级 PDF 解析（AI 友好 · 语义闭环版）
    """

    pages_result = []
    confirmed_all = []
    ocr_all = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            page_no = page_index + 1

            page_confirmed = ""
            page_ocr = ""
            page_confidence = "LOW"

            # ========= 1️⃣ 标准文本 =========
            try:
                text = page.extract_text(
                    x_tolerance=2,
                    y_tolerance=2,
                    layout=True
                ) or ""
            except Exception:
                text = ""

            text = text.strip()

            # ========= 2️⃣ 字符兜底 =========
            char_text = ""
            try:
                chars = page.chars or []
                char_text = "".join(
                    c.get("text", "") for c in chars if c.get("text")
                ).strip()
            except Exception:
                pass

            if text and len(text) >= 80:
                page_confirmed = clean_text(text)
                page_confidence = "HIGH"
            elif char_text and len(char_text) >= 80:
                page_confirmed = clean_text(char_text)
                page_confidence = "MEDIUM"

            # ========= 3️⃣ OCR =========
            try:
                page_image = page.to_image(resolution=300).original
                ocr_text = pytesseract.image_to_string(
                    page_image,
                    lang="chi_sim+eng",
                    config="--psm 6"
                ).strip()
            except Exception:
                ocr_text = ""

            if ocr_text and len(ocr_text) >= 50:
                page_ocr = clean_text(ocr_text)

            pages_result.append({
                "page": page_no,
                "confirmed_text": page_confirmed,
                "ocr_text": page_ocr,
                "confidence": page_confidence
            })

            if page_confirmed:
                confirmed_all.append(f"\n【第 {page_no} 页】\n{page_confirmed}")

            if page_ocr:
                ocr_all.append(f"\n【第 {page_no} 页 OCR】\n{page_ocr}")

    confirmed_text = "\n".join(confirmed_all).strip()
    ocr_text = "\n".join(ocr_all).strip()

    # ========= ⭐ 是否可用于 AI =========
    usable_for_ai = len(confirmed_text) >= MIN_AI_TEXT_LENGTH

    # ========= ⭐ 最终给 AI 的文本（关键） =========
    final_text = confirmed_text if usable_for_ai else ""

    final_confidence = "HIGH" if usable_for_ai else "LOW"

    return {
        "confirmed_text": confirmed_text,
        "ocr_text": ocr_text,
        "final_text": final_text,          # ⭐ 新增：唯一 AI 输入
        "confidence": final_confidence,
        "usable_for_ai": usable_for_ai,    # ⭐ 明确结论
        "pages": pages_result
    }
