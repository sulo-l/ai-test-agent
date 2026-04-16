#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/2/22 17:24
# @Author: sulo
# app/services/coverage.py
# -*- coding: utf-8 -*-

"""
覆盖统计 & 缺口计算（通用服务）

你现在的 pipeline / CoverageAgent / Coverage loop 都需要“统一口径”的：
- 维度归一化 normalize_dimension()
- 覆盖统计 calc_coverage()
- 缺口计算 calc_missing_dimensions()

✅ 设计目标：
- 纯函数（无 LLM、无 IO）
- 口径稳定：同一套 normalize 规则，避免“Happy/positive/正常”混乱
- 可扩展：允许 targets 自定义、min_per_dim 自定义
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Iterable, Set, Tuple
import os
import re

TestPoint = Dict[str, Any]

# 默认覆盖维度（你 pipeline 里一直用的那套）
DEFAULT_COVERAGE_TARGETS: List[str] = ["Happy", "Negative", "UI", "Input", "NFR", "Security", "Compat"]

# 默认缺口阈值（可被 pipeline 参数覆盖；这里只做服务兜底）
DEFAULT_MIN_PER_DIM = int(os.getenv("TC_COVERAGE_MIN_PER_DIM", "3"))


# =========================
# Dimension normalize
# =========================
_DIM_ALIASES: List[Tuple[str, str]] = [
    # Negative
    (r"^(neg|negative|异常|失败|错误|err|error|异常流程)$", "Negative"),
    # Happy (positive/normal)
    (r"^(happy|pos|positive|正常|正常流程|主流程|成功|success)$", "Happy"),
    # UI
    (r"^(ui|ux|界面|交互|前端|展示|样式)$", "UI"),
    # Input
    (r"^(input|输入|表单|校验|validate|validation|参数|param)$", "Input"),
    # NFR (performance/reliability)
    (r"^(nfr|non[-\s]?functional|性能|perf|performance|稳定|reliab|reliability|容量|压测|sla)$", "NFR"),
    # Security
    (r"^(sec|security|安全|鉴权|认证|auth|authorization|权限|permission|越权|注入|xss|csrf)$", "Security"),
    # Compat
    (r"^(comp|compat|兼容|browser|os|device|适配|分辨率|多端|多浏览器)$", "Compat"),
]


def normalize_dimension(dim: str) -> str:
    """
    维度归一化：把各种写法归一到 DEFAULT_COVERAGE_TARGETS 的一员。
    """
    d = (dim or "").strip()
    if not d:
        return "Happy"

    low = d.lower().strip()

    # 先做一些常见清洗
    low = re.sub(r"[\s_/]+", "-", low)  # "non functional" / "non_functional" -> "non-functional"
    low = low.replace("nonfunctional", "non-functional")

    # 命中别名规则
    for pat, canonical in _DIM_ALIASES:
        if re.match(pat, low, flags=re.IGNORECASE):
            return canonical

    # 最后兜底：首字母大写，但如果不在目标集，仍归到 Happy（保证口径）
    cap = d[:1].upper() + d[1:]
    if cap in DEFAULT_COVERAGE_TARGETS:
        return cap

    # unknown -> Happy（避免统计出现一堆新维度导致缺口判断失真）
    return "Happy"


def normalize_targets(targets: Optional[Iterable[str]]) -> List[str]:
    """
    把 targets 归一化，并去重保持顺序。
    """
    if not targets:
        return list(DEFAULT_COVERAGE_TARGETS)

    seen: Set[str] = set()
    out: List[str] = []
    for t in targets:
        nt = normalize_dimension(str(t))
        if nt not in seen:
            seen.add(nt)
            out.append(nt)

    return out or list(DEFAULT_COVERAGE_TARGETS)


# =========================
# Coverage calc
# =========================
def calc_coverage(points: List[TestPoint], targets: Optional[Iterable[str]] = None) -> Dict[str, int]:
    """
    计算覆盖统计：每个维度对应多少条测试点。

    返回示例：
    {"Happy": 12, "Negative": 6, "UI": 4, ...}
    """
    tgs = normalize_targets(targets)
    cnt: Dict[str, int] = {t: 0 for t in tgs}

    for tp in points or []:
        dim = normalize_dimension(str((tp or {}).get("dimension") or "Happy"))
        # 允许统计出现“额外维度”，但仍归一后落到既定维度
        if dim not in cnt:
            cnt[dim] = 0
        cnt[dim] += 1

    # 确保所有 targets 都存在键
    for t in tgs:
        cnt.setdefault(t, 0)

    return cnt


def calc_missing_dimensions(
    points: List[TestPoint],
    targets: Optional[Iterable[str]] = None,
    min_per_dim: Optional[int] = None,
) -> List[str]:
    """
    计算缺口维度：统计低于阈值的维度列表（按 targets 顺序输出）。

    - min_per_dim 默认读取 DEFAULT_MIN_PER_DIM
    """
    threshold = DEFAULT_MIN_PER_DIM if min_per_dim is None else int(min_per_dim)
    tgs = normalize_targets(targets)
    stats = calc_coverage(points, tgs)

    missing: List[str] = []
    for t in tgs:
        if int(stats.get(t, 0)) < threshold:
            missing.append(t)
    return missing


# =========================
# Optional helpers (pipeline/agent 可选复用)
# =========================
def brief_points(
    points: List[TestPoint],
    limit: int = 80,
    title_max: int = 120,
    source_max: int = 120,
) -> List[Dict[str, str]]:
    """
    把测试点压缩成“给 LLM 看”的轻量摘要（可用于 coverage prompt 里避免塞爆 token）。
    """
    out: List[Dict[str, str]] = []
    for tp in (points or [])[: max(0, limit)]:
        out.append(
            {
                "dimension": normalize_dimension(str(tp.get("dimension") or "")),
                "title": str(tp.get("title") or "")[:title_max],
                "source": str(tp.get("source") or "")[:source_max],
            }
        )
    return out


def tp_fingerprint(tp: TestPoint) -> str:
    """
    统一的测试点去重指纹（维度 + title + source）。
    你 pipeline 里如果要跨模块复用 dedup，可以直接用这个。
    """
    dim = normalize_dimension(str(tp.get("dimension") or ""))
    title = (tp.get("title") or "").strip()
    source = (tp.get("source") or "").strip()
    return f"{dim}||{title}||{source}"