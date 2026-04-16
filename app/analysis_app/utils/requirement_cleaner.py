#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/3/8 17:03
# @Author: sulo
# app/analysis_app/utils/requirement_cleaner.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from typing import List, Dict, Any, Optional


class RequirementCleaner:
    """
    需求文本清洗工具

    作用：
    1. 清洗 PDF / 富文本抽取后的噪音
    2. 统一换行、空白、标点
    3. 删除明显无价值的页码/页眉页脚
    4. 提供基础输入校验
    """

    DEFAULT_MAX_TEXT_LENGTH = 120000

    def __init__(self, max_text_length: int = DEFAULT_MAX_TEXT_LENGTH):
        self.max_text_length = int(max_text_length)

    # =====================================================
    # 主入口
    # =====================================================

    def clean(
        self,
        text: str,
        keep_line_breaks: bool = True,
    ) -> str:
        """
        主清洗入口
        """
        if not isinstance(text, str):
            return ""

        s = text

        s = self._normalize_newlines(s)
        s = self._remove_zero_width_chars(s)
        s = self._normalize_spaces(s)
        s = self._normalize_punctuation(s)
        s = self._remove_common_noise_lines(s)
        s = self._collapse_blank_lines(s)
        s = self._trim_text(s)

        if not keep_line_breaks:
            s = self._flatten_text(s)

        s = s.strip()

        if self.max_text_length > 0 and len(s) > self.max_text_length:
            s = s[: self.max_text_length].rstrip()

        return s

    def validate(self, text: str) -> None:
        """
        输入校验，不通过则抛异常
        """
        if not isinstance(text, str):
            raise ValueError("requirement_text must be a string")

        if not text.strip():
            raise ValueError("requirement_text cannot be empty")

    def clean_and_validate(
        self,
        text: str,
        keep_line_breaks: bool = True,
    ) -> str:
        cleaned = self.clean(text=text, keep_line_breaks=keep_line_breaks)
        self.validate(cleaned)
        return cleaned

    # =====================================================
    # 扩展入口：生成清洗元信息
    # =====================================================

    def clean_with_meta(
        self,
        text: str,
        keep_line_breaks: bool = True,
    ) -> Dict[str, Any]:
        original = text if isinstance(text, str) else ""
        cleaned = self.clean(text=original, keep_line_breaks=keep_line_breaks)

        return {
            "original_length": len(original),
            "cleaned_length": len(cleaned),
            "removed_chars": max(0, len(original) - len(cleaned)),
            "line_count": len(cleaned.splitlines()) if cleaned else 0,
            "text": cleaned,
        }

    # =====================================================
    # 基础清洗
    # =====================================================

    def _normalize_newlines(self, text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def _remove_zero_width_chars(self, text: str) -> str:
        """
        删除零宽字符、BOM 等
        """
        return re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)

    def _normalize_spaces(self, text: str) -> str:
        """
        统一空白：
        - 制表符转空格
        - 连续空格压缩
        - 行尾空格去掉
        """
        s = text.replace("\t", " ")
        s = re.sub(r"[ \u3000]{2,}", " ", s)
        s = re.sub(r"[ ]+\n", "\n", s)
        return s

    def _normalize_punctuation(self, text: str) -> str:
        """
        轻量标点统一：
        只做非常保守的替换，避免误伤内容。
        """
        s = text

        replace_map = {
            "：": "：",
            "，": "，",
            "。": "。",
            "；": "；",
            "（": "（",
            "）": "）",
            "【": "【",
            "】": "】",
        }

        for old, new in replace_map.items():
            s = s.replace(old, new)

        return s

    def _collapse_blank_lines(self, text: str) -> str:
        """
        连续空行最多保留 1 行
        """
        return re.sub(r"\n{3,}", "\n\n", text)

    def _flatten_text(self, text: str) -> str:
        """
        压平为单段文本
        """
        s = re.sub(r"\s*\n\s*", " ", text)
        s = re.sub(r"\s{2,}", " ", s)
        return s.strip()

    def _trim_text(self, text: str) -> str:
        return text.strip()

    # =====================================================
    # 噪音行清洗
    # =====================================================

    def _remove_common_noise_lines(self, text: str) -> str:
        """
        删除常见无意义页眉页脚/页码/导出提示
        这里只做“弱清洗”，宁可少删，也不误删正文。
        """
        lines = text.split("\n")
        kept: List[str] = []

        for line in lines:
            raw = line
            s = raw.strip()

            if not s:
                kept.append("")
                continue

            if self._is_page_number_line(s):
                continue

            if self._is_common_header_footer_line(s):
                continue

            kept.append(raw)

        return "\n".join(kept)

    def _is_page_number_line(self, s: str) -> bool:
        """
        识别常见页码
        示例：
        - 1
        - 12 / 30
        - 第1页
        - Page 2 of 10
        """
        patterns = [
            r"^\d+$",
            r"^\d+\s*/\s*\d+$",
            r"^第\s*\d+\s*页$",
            r"^page\s+\d+(\s+of\s+\d+)?$",
        ]

        lower = s.lower()
        return any(re.match(p, lower, flags=re.IGNORECASE) for p in patterns)

    def _is_common_header_footer_line(self, s: str) -> bool:
        """
        识别明显页眉页脚噪音
        """
        lower = s.lower()

        exact_blacklist = {
            "机密",
            "内部资料",
            "confidential",
            "for internal use only",
            "版权所有",
        }

        if lower in {x.lower() for x in exact_blacklist}:
            return True

        # 非常常见的导出提示 / 文档系统残留
        contains_blacklist = [
            "导出时间",
            "打印时间",
            "generated by",
            "created with",
            "仅供内部使用",
        ]

        return any(x in lower for x in [w.lower() for w in contains_blacklist])

    # =====================================================
    # 附加工具
    # =====================================================

    def split_sections(self, text: str) -> List[str]:
        """
        按空行做粗粒度分段
        """
        cleaned = self.clean(text, keep_line_breaks=True)
        if not cleaned:
            return []

        parts = re.split(r"\n\s*\n", cleaned)
        return [x.strip() for x in parts if x.strip()]

    def normalize_for_llm(self, text: str) -> str:
        """
        给 LLM 用的标准文本：
        - 保留换行
        - 去噪
        - 长度控制
        """
        return self.clean(text=text, keep_line_breaks=True)


# 单例
requirement_cleaner = RequirementCleaner()