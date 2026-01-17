# services/pdf_parser.py
# -*- coding: utf-8 -*-

import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from PIL import Image
import os
import tempfile


def parse_pdf(pdf_path: str) -> dict:
    """
    三通道 PDF 解析：
    1. pdfplumber extract_text（标准文本）
    2. pdfplumber chars（字体异常兜底）
    3. OCR（整页 + 图片）

    返回结构：
    {
        "confirmed_text": str,   # 高/中置信文本（AI 主输入）
        "ocr_text": str,         # OCR 补充文本
        "confidence": "HIGH" | "MEDIUM" | "LOW",
        "pages": [
            {
                "page": 1,
                "confirmed_text": "...",
                "ocr_text": "...",
                "confidence": "HIGH"
            }
        ]
    }
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

            # =========================
            # 1️⃣ 通道一：标准文本
            # =========================
            try:
                text = page.extract_text(
                    x_tolerance=2,
                    y_tolerance=2,
                    layout=True
                )
            except Exception:
                text = ""

            if text:
                text = text.strip()

            # =========================
            # 2️⃣ 通道二：字符级兜底（关键）
            # =========================
            char_text = ""
            try:
                chars = page.chars or []
                char_text = "".join(
                    c.get("text", "") for c in chars if c.get("text")
                ).strip()
            except Exception:
                char_text = ""

            # =========================
            # 判定高 / 中置信文本
            # =========================
            if text and len(text) >= 50:
                page_confirmed = text
                page_confidence = "HIGH"
            elif char_text and len(char_text) >= 50:
                page_confirmed = char_text
                page_confidence = "MEDIUM"

            # =========================
            # 3️⃣ 通道三：OCR（整页）
            # =========================
            try:
                page_image = page.to_image(resolution=300).original
                ocr_text = pytesseract.image_to_string(
                    page_image,
                    lang="chi_sim+eng",
                    config="--psm 6"
                ).strip()
            except Exception:
                ocr_text = ""

            if ocr_text and len(ocr_text) >= 30:
                page_ocr += ocr_text + "\n"

            # =========================
            # 4️⃣ 图片 OCR（PDF 内嵌图片）
            # =========================
            try:
                for img in page.images or []:
                    bbox = (
                        img.get("x0", 0),
                        img.get("top", 0),
                        img.get("x1", 0),
                        img.get("bottom", 0)
                    )

                    cropped = page.crop(bbox).to_image(resolution=300).original
                    img_text = pytesseract.image_to_string(
                        cropped,
                        lang="chi_sim+eng",
                        config="--psm 6"
                    ).strip()

                    if img_text and len(img_text) >= 10:
                        page_ocr += "\n" + img_text
            except Exception:
                pass

            # =========================
            # 汇总分页结果
            # =========================
            pages_result.append({
                "page": page_no,
                "confirmed_text": page_confirmed,
                "ocr_text": page_ocr.strip(),
                "confidence": page_confidence
            })

            if page_confirmed:
                confirmed_all.append(f"\n【第 {page_no} 页】\n{page_confirmed}")

            if page_ocr:
                ocr_all.append(f"\n【第 {page_no} 页 OCR】\n{page_ocr}")

    # =========================
    # 全局置信度判定
    # =========================
    if confirmed_all:
        final_confidence = "HIGH"
    elif ocr_all:
        final_confidence = "LOW"
    else:
        final_confidence = "LOW"

    return {
        "confirmed_text": "\n".join(confirmed_all).strip(),
        "ocr_text": "\n".join(ocr_all).strip(),
        "confidence": final_confidence,
        "pages": pages_result
    }
