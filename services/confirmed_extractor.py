#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/15 00:21
# @Author: sulo
# services/confirmed_extractor.py

import re
from typing import List, Dict, Any, Optional


def _extract_focus_points(requirement: Optional[str]) -> List[str]:
    """
    从前端补充的 requirement 中拆解【重点测试方向】
    """
    if not requirement:
        return []

    # 常见中文分隔符
    parts = re.split(r"[，,；;、\n]", requirement)

    focus_points = []
    for p in parts:
        p = p.strip()
        if len(p) >= 2:
            focus_points.append(p)

    return focus_points


def extract_confirmed_items(
    text: str,
    requirement: Optional[str] = None
) -> Dict[str, Any]:
    """
    从 OCR / 文本中抽取【确定识别】的高置信内容
    同时解析前端指定的【重点测试方向】

    返回结构化结果，供下游 Agent 使用
    """

    # ===============================
    # 1️⃣ 原有：PDF 高置信内容抽取（完全保留）
    # ===============================
    confirmed = set()

    if text:
        patterns = [
            r"https?://[^\s]+",                 # URL
            r"/api/[a-zA-Z0-9/_\-]+",            # API 路径
            r"登录|注册|退出|新增|删除|修改|查询",
            r"用户|账号|用户名|密码|验证码|权限|角色",
            r"成功|失败|错误|异常|超时",
        ]

        for p in patterns:
            for m in re.findall(p, text):
                confirmed.add(m.strip())

    confirmed_items = sorted(list(confirmed))

    # ===============================
    # 2️⃣ 新增：前端重点测试方向解析
    # ===============================
    focus_points = _extract_focus_points(requirement)

    # ===============================
    # 3️⃣ 返回统一结构（关键）
    # ===============================
    return {
        # PDF / OCR 中“客观存在”的内容
        "confirmed_items": confirmed_items,

        # 前端显式要求的重点测试方向（最高优先级）
        "focus_points": focus_points,

        # 方便后续 prompt 直接拼接
        "has_focus": len(focus_points) > 0,
    }
