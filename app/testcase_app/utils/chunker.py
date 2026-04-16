#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/2/22 16:50
# @Author: sulo
# app/testcase_app/utils/chunker.py
# -*- coding: utf-8 -*-

import re
from typing import List, Tuple, Optional, Dict, Any


_HEADER_RE = re.compile(
    r"^\s*("
    r"#+\s+|"                              # Markdown header
    r"[一二三四五六七八九十]+[、\.]\s*|"       # 中文序号：一、 一. 等
    r"[0-9]+[、\.]\s*|"                    # 数字序号：1、 1.
    r"第[一二三四五六七八九十0-9]+[章节部分]\s*|"  # 第X章/节/部分
    r"=+|-+"                               # 分隔线
    r")\s*"
)

# 经验：常见“标题行”特征（可扩展）
_TITLE_HINT_RE = re.compile(r"^\s*(概述|背景|范围|目标|术语|定义|流程|规则|说明|接口|字段|异常|权限|安全|性能|兼容|边界|FAQ|附录)\b")


def is_header_line(line: str) -> bool:
    s = (line or "").rstrip()
    if not s.strip():
        return False
    if _HEADER_RE.match(s):
        return True
    # 很短且像标题
    if len(s.strip()) <= 18 and _TITLE_HINT_RE.search(s.strip()):
        return True
    return False


def split_by_headers(text: str) -> List[str]:
    """
    先按“标题行/分隔线”进行粗切。
    """
    t = (text or "").strip()
    if not t:
        return []

    parts: List[str] = []
    buf: List[str] = []

    for line in t.splitlines():
        if is_header_line(line):
            if buf:
                parts.append("\n".join(buf).strip())
                buf = []
            buf.append(line.rstrip())
        else:
            # 保留空行（用于段落语义）
            if not line.strip():
                if buf:
                    buf.append("")
                continue
            buf.append(line.rstrip())

    if buf:
        parts.append("\n".join(buf).strip())

    # 过滤空块
    return [p for p in parts if p and p.strip()]


def merge_to_size(parts: List[str], min_chars: int, max_chars: int, max_chunks: int) -> List[str]:
    """
    把 parts 合并成尽量落在 [min_chars, max_chars] 的 chunk。
    """
    chunks: List[str] = []
    cur = ""

    for p in parts:
        p = (p or "").strip()
        if not p:
            continue

        if not cur:
            cur = p
            continue

        if len(cur) + 2 + len(p) <= max_chars:
            cur = (cur + "\n\n" + p).strip()
        else:
            chunks.append(cur.strip())
            cur = p
            if len(chunks) >= max_chunks:
                break

    if cur and len(chunks) < max_chunks:
        chunks.append(cur.strip())

    # 超长块：硬切
    hard: List[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            hard.append(c)
        else:
            for i in range(0, len(c), max_chars):
                if len(hard) >= max_chunks:
                    break
                seg = c[i:i + max_chars].strip()
                if seg:
                    hard.append(seg)

    # 太短块：向前合并
    merged: List[str] = []
    for c in hard:
        if merged and len(c) < min_chars and (len(merged[-1]) + 2 + len(c) <= max_chars):
            merged[-1] = (merged[-1] + "\n\n" + c).strip()
        else:
            merged.append(c)

    return merged[:max_chunks]


def smart_split_text(
    text: str,
    *,
    max_chunks: int = 18,
    min_chars: int = 500,
    max_chars: int = 2200,
) -> List[str]:
    """
    入口函数：智能切块
    1) 按标题/分隔线粗切
    2) 合并到合理大小
    3) 做超长硬切与过短合并
    """
    t = (text or "").strip()
    if not t:
        return []

    parts = split_by_headers(t)

    # 如果标题切不出来（例如纯长段），按空行做一次粗切
    if len(parts) <= 1:
        paras = [p.strip() for p in re.split(r"\n\s*\n+", t) if p.strip()]
        parts = paras if paras else [t]

    return merge_to_size(parts, min_chars=min_chars, max_chars=max_chars, max_chunks=max_chunks)


def chunk_to_objects(
    chunks: List[str],
    *,
    prefix: str = "C",
    title_prefix: str = "Chunk",
    module: str = "",
) -> List[Dict[str, Any]]:
    """
    将 chunk 文本列表转换为 Planner 所需结构：
    [{"id":"C1","title":"Chunk 1","module":"","text":"..."}]
    """
    out: List[Dict[str, Any]] = []
    for i, c in enumerate(chunks or []):
        txt = (c or "").strip()
        if not txt:
            continue
        out.append({
            "id": f"{prefix}{i+1}",
            "title": f"{title_prefix} {i+1}",
            "module": module,
            "text": txt,
        })
    return out