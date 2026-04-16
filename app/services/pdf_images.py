#! /usr/bin/python3
# coding=utf-8
# @Author: sulo
"""
PDF Image Extractor (Protocol-Level)

职责：
- 将 PDF 的每一页转换为图片资源
- 仅做“资源抽取”，不涉及任何 UI / CV / 语义判断
- 作为共享输入准备层的一部分

增强点：
- 支持返回 PIL.Image
- 支持可选保存页面图片到本地目录
- 返回页面尺寸、图片路径、错误信息
- 单页失败不影响整体
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
import logging

import pdfplumber


logger = logging.getLogger(__name__)


# =====================================================
# 协议对象（共享层）
# =====================================================

@dataclass
class PDFPageImage:
    """
    单页 PDF 图片资源
    """
    page: int                          # 页码（从 1 开始）
    image: object                      # PIL.Image.Image
    dpi: int                           # 渲染分辨率
    width: int                         # 图片宽度（像素）
    height: int                        # 图片高度（像素）
    image_path: Optional[str] = None   # 落盘后的图片路径（可选）


@dataclass
class PDFPageImageError:
    """
    单页 PDF 图片提取错误
    """
    page: int
    error: str


@dataclass
class PDFImageExtractResult:
    """
    PDF 图片提取结果
    """
    pdf_path: str
    dpi: int
    images: List[PDFPageImage]
    errors: List[PDFPageImageError]
    total_pages: int
    success_pages: int
    failed_pages: int


# =====================================================
# 工具函数
# =====================================================

def _normalize_dpi(dpi: int) -> int:
    """
    规范化 DPI，避免非法值
    """
    try:
        value = int(dpi)
    except Exception:
        value = 300

    if value <= 0:
        return 300

    # 过大 DPI 容易导致内存压力，做一个工程级保护
    return min(value, 600)


def _ensure_output_dir(output_dir: Optional[str]) -> Optional[Path]:
    """
    确保输出目录存在
    """
    if not output_dir:
        return None

    path = Path(output_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _build_output_filename(
    pdf_path: str,
    page_no: int,
) -> str:
    """
    生成页面图片文件名
    """
    pdf_name = Path(pdf_path).stem
    safe_name = pdf_name.replace(" ", "_")
    return f"{safe_name}_page_{page_no:03d}.png"


def _safe_image_for_save(image: object) -> object:
    """
    少数图片对象可能不是标准可直接保存的模式，这里做轻量兜底
    """
    try:
        mode = getattr(image, "mode", "")
        if mode in {"RGBA", "RGB", "L"}:
            return image
        if hasattr(image, "convert"):
            return image.convert("RGB")
    except Exception:
        pass
    return image


def _save_page_image(
    image: object,
    output_dir: Path,
    pdf_path: str,
    page_no: int,
) -> str:
    """
    保存页面图片并返回路径
    """
    filename = _build_output_filename(pdf_path=pdf_path, page_no=page_no)
    file_path = output_dir / filename

    image_to_save = _safe_image_for_save(image)
    image_to_save.save(file_path, format="PNG")

    return str(file_path)


# =====================================================
# 主函数
# =====================================================

def extract_pdf_images(
    pdf_path: str,
    *,
    dpi: int = 300,
    output_dir: Optional[str] = None,
    save_to_disk: bool = False,
) -> PDFImageExtractResult:
    """
    从 PDF 中提取页面图片（稳定协议）

    :param pdf_path: PDF 文件路径
    :param dpi: 渲染 DPI（UI Vision 常用 200~300，默认 300）
    :param output_dir: 图片输出目录（当 save_to_disk=True 时生效）
    :param save_to_disk: 是否将页面图片保存到磁盘
    :return: PDFImageExtractResult
    """

    dpi = _normalize_dpi(dpi)

    images: List[PDFPageImage] = []
    errors: List[PDFPageImageError] = []

    if not pdf_path:
        return PDFImageExtractResult(
            pdf_path="",
            dpi=dpi,
            images=[],
            errors=[PDFPageImageError(page=0, error="pdf_path is empty")],
            total_pages=0,
            success_pages=0,
            failed_pages=0,
        )

    pdf_file = Path(pdf_path).expanduser().resolve()

    if not pdf_file.exists() or not pdf_file.is_file():
        return PDFImageExtractResult(
            pdf_path=str(pdf_file),
            dpi=dpi,
            images=[],
            errors=[PDFPageImageError(page=0, error="pdf file not found")],
            total_pages=0,
            success_pages=0,
            failed_pages=0,
        )

    output_path: Optional[Path] = None
    if save_to_disk:
        try:
            default_output_dir = str(pdf_file.parent / "tmp" / "pdf_pages")
            output_path = _ensure_output_dir(output_dir or default_output_dir)
        except Exception as e:
            return PDFImageExtractResult(
                pdf_path=str(pdf_file),
                dpi=dpi,
                images=[],
                errors=[PDFPageImageError(page=0, error=f"failed to prepare output dir: {e}")],
                total_pages=0,
                success_pages=0,
                failed_pages=0,
            )

    total_pages = 0

    try:
        with pdfplumber.open(str(pdf_file)) as pdf:
            total_pages = len(pdf.pages)

            for page_index, page in enumerate(pdf.pages):
                page_no = page_index + 1

                try:
                    # 仅做页面渲染为图片
                    rendered = page.to_image(resolution=dpi)
                    pil_image = getattr(rendered, "original", None)

                    if pil_image is None:
                        raise ValueError("rendered page image is None")

                    size = getattr(pil_image, "size", None)
                    if not size or len(size) != 2:
                        raise ValueError("invalid rendered image size")

                    width, height = int(size[0]), int(size[1])
                    image_path: Optional[str] = None

                    if save_to_disk and output_path is not None:
                        image_path = _save_page_image(
                            image=pil_image,
                            output_dir=output_path,
                            pdf_path=str(pdf_file),
                            page_no=page_no,
                        )

                    images.append(
                        PDFPageImage(
                            page=page_no,
                            image=pil_image,
                            dpi=dpi,
                            width=width,
                            height=height,
                            image_path=image_path,
                        )
                    )

                except Exception as e:
                    logger.exception(
                        "extract pdf page image failed: pdf=%s, page=%s",
                        pdf_file,
                        page_no,
                    )
                    errors.append(
                        PDFPageImageError(
                            page=page_no,
                            error=str(e),
                        )
                    )
                    continue

    except Exception as e:
        logger.exception("open pdf failed: pdf=%s", pdf_file)
        return PDFImageExtractResult(
            pdf_path=str(pdf_file),
            dpi=dpi,
            images=[],
            errors=[PDFPageImageError(page=0, error=f"open pdf failed: {e}")],
            total_pages=0,
            success_pages=0,
            failed_pages=0,
        )

    return PDFImageExtractResult(
        pdf_path=str(pdf_file),
        dpi=dpi,
        images=images,
        errors=errors,
        total_pages=total_pages,
        success_pages=len(images),
        failed_pages=len(errors),
    )


# =====================================================
# 兼容旧接口
# =====================================================

def extract_pdf_page_images(
    pdf_path: str,
    *,
    dpi: int = 300,
    output_dir: Optional[str] = None,
    save_to_disk: bool = False,
) -> List[PDFPageImage]:
    """
    兼容型接口：
    只返回 PDFPageImage 列表，方便旧代码平滑迁移
    """
    result = extract_pdf_images(
        pdf_path=pdf_path,
        dpi=dpi,
        output_dir=output_dir,
        save_to_disk=save_to_disk,
    )
    return result.images