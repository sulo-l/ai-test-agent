#! /usr/bin/python3
# coding=utf-8
# app/analysis_app/utils/issue_normalizer.py

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.analysis_app.models import RequirementIssue
from app.analysis_app.utils.category_classifier import category_classifier


class IssueNormalizer:
    """
    需求问题归一化工具

    功能：
    1. 统一 issue 结构
    2. 自动补齐缺失字段
    3. 保留上游 agent 传入的重要字段
    4. 统一 category / severity / dimension 口径
    """

    ISSUE_LEVEL_MAP = {
        "fatal": "high",
        "blocker": "high",
        "critical": "high",
        "high": "high",
        "medium": "medium",
        "mid": "medium",
        "normal": "medium",
        "major": "medium",
        "low": "low",
        "minor": "low",
        "info": "low",
        "suggestion": "low",
    }

    DEFAULT_CATEGORY = "需求质量"
    DEFAULT_DIMENSION = "analysis"

    ALLOWED_LEVELS = {"high", "medium", "low"}
    ALLOWED_SEVERITIES = {"blocker", "critical", "major", "minor"}
    ALLOWED_IMPACTS = {"high", "medium", "low"}

    CATEGORY_MAP = {
        "安全": "权限安全",
        "权限": "权限安全",
        "权限控制": "权限安全",
        "角色权限": "权限安全",
        "访问控制": "权限安全",
        "安全要求": "权限安全",

        "接口": "接口契约",
        "依赖": "接口契约",
        "依赖接口": "接口契约",
        "依赖约束": "接口契约",

        "数据": "数据定义",
        "数据约束": "数据定义",

        "状态": "状态流转",
        "状态机": "状态流转",

        "合规": "合规性",
        "测试": "可测试性",
        "追踪": "可追踪性",
    }

    DIMENSION_MAP = {
        "general": "analysis",
        "analysis": "analysis",
        "rule": "analysis",
        "structure": "analysis",
        "testability": "analysis",

        "consistency": "consistency",
        "coverage": "coverage",
        "review": "review",
        "security": "security",
        "compliance": "compliance",
        "traceability": "traceability",
    }

    def normalize_issues(
        self,
        raw_issues: Any,
    ) -> List[Dict[str, Any]]:
        if not raw_issues:
            return []

        normalized: List[Dict[str, Any]] = []

        if isinstance(raw_issues, list):
            for idx, item in enumerate(raw_issues, start=1):
                row = self.normalize_single_issue(item, idx)
                if row:
                    normalized.append(row)
        else:
            row = self.normalize_single_issue(raw_issues, 1)
            if row:
                normalized.append(row)

        return normalized

    # =====================================================
    # 转模型
    # =====================================================

    def to_issue_models(
        self,
        issues: List[Dict[str, Any]],
    ) -> List[RequirementIssue]:
        results: List[RequirementIssue] = []

        for item in issues or []:
            try:
                results.append(RequirementIssue(**item))
            except Exception:
                continue

        return results

    # =====================================================
    # 单条归一
    # =====================================================

    def normalize_single_issue(
        self,
        item: Any,
        index: int,
    ) -> Optional[Dict[str, Any]]:
        if item is None:
            return None

        # -------------------------------------------------
        # string
        # -------------------------------------------------
        if isinstance(item, str):
            message = item.strip()
            if not message:
                return None

            category = self._canonical_category(self._infer_category("", message))
            level = "medium"
            severity = self._map_severity(level)
            impact = self._map_impact(level)

            return {
                "id": f"ISSUE-{index:03d}",
                "level": level,
                "category": category,
                "title": self._build_issue_title(message),
                "message": message,
                "suggestion": self._build_suggestion(category, message),
                "severity": severity,
                "impact": impact,
                "solution": self._build_solution(category, message),
                "dimension": self.DEFAULT_DIMENSION,
                "reason": "",
                "status": "open",
                "confidence": "medium",
                "tags": [],
                "evidence": [],
                "requirement_refs": [],
                "duplicate_keys": [],
                "source_agent": "analysis",
            }

        # -------------------------------------------------
        # object
        # -------------------------------------------------
        if not isinstance(item, dict):
            message = str(item).strip()
            if not message:
                return None

            category = self.DEFAULT_CATEGORY
            level = "medium"
            severity = self._map_severity(level)
            impact = self._map_impact(level)

            return {
                "id": f"ISSUE-{index:03d}",
                "level": level,
                "category": category,
                "title": self._build_issue_title(message),
                "message": message,
                "suggestion": self._build_suggestion(category, message),
                "severity": severity,
                "impact": impact,
                "solution": self._build_solution(category, message),
                "dimension": self.DEFAULT_DIMENSION,
                "reason": "",
                "status": "open",
                "confidence": "medium",
                "tags": [],
                "evidence": [],
                "requirement_refs": [],
                "duplicate_keys": [],
                "source_agent": "analysis",
            }

        # -------------------------------------------------
        # dict
        # -------------------------------------------------
        raw_level = (
            item.get("level")
            or item.get("priority")
            or item.get("severity")
            or "medium"
        )
        level = self._normalize_level(str(raw_level))

        title = str(item.get("title") or "").strip()

        message = (
            item.get("message")
            or item.get("desc")
            or item.get("description")
            or item.get("content")
            or item.get("detail")
            or ""
        )
        message = str(message).strip()

        if not message:
            if title:
                message = title
            else:
                return None

        raw_category = str(
            item.get("category")
            or item.get("type")
            or ""
        ).strip()
        category = self._canonical_category(
            raw_category or self._infer_category(title, message)
        )

        raw_dimension = str(
            item.get("dimension")
            or item.get("source_agent")
            or self.DEFAULT_DIMENSION
        ).strip()
        dimension = self._canonical_dimension(raw_dimension)

        if not title:
            title = self._build_issue_title(message)

        suggestion = str(item.get("suggestion") or "").strip()
        if not suggestion:
            suggestion = self._build_suggestion(category, message)

        severity = self._canonical_severity(
            str(item.get("severity") or "").strip().lower(),
            level=level,
        )
        impact = self._canonical_impact(
            str(item.get("impact") or "").strip().lower(),
            level=level,
        )

        solution = str(item.get("solution") or "").strip()
        if not solution:
            solution = self._build_solution(category, message)

        issue_id = str(item.get("id") or "").strip()
        if not issue_id:
            issue_id = f"ISSUE-{index:03d}"

        reason = str(item.get("reason") or "").strip()
        status = str(item.get("status") or "open").strip() or "open"
        confidence = str(item.get("confidence") or "medium").strip() or "medium"
        source_agent = str(item.get("source_agent") or "").strip() or dimension

        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        requirement_refs = item.get("requirement_refs") if isinstance(item.get("requirement_refs"), list) else []
        duplicate_keys = item.get("duplicate_keys") if isinstance(item.get("duplicate_keys"), list) else []

        return {
            "id": issue_id,
            "level": level,
            "category": category,
            "title": title,
            "message": message,
            "suggestion": suggestion,
            "severity": severity,
            "impact": impact,
            "solution": solution,
            "dimension": dimension,
            "reason": reason,
            "status": status,
            "confidence": confidence,
            "tags": tags,
            "evidence": evidence,
            "requirement_refs": requirement_refs,
            "duplicate_keys": duplicate_keys,
            "source_agent": source_agent,
        }

    # =====================================================
    # 分类
    # =====================================================

    def _infer_category(
        self,
        title: str,
        message: str,
    ) -> str:
        return category_classifier.classify(
            title=title,
            message=message,
            default=self.DEFAULT_CATEGORY,
        )

    def _canonical_category(self, category: str) -> str:
        value = str(category or "").strip()
        if not value:
            return self.DEFAULT_CATEGORY
        return self.CATEGORY_MAP.get(value, value)

    # =====================================================
    # level / severity / impact / dimension
    # =====================================================

    def _normalize_level(self, raw_level: str) -> str:
        raw = str(raw_level or "").strip().lower()
        return self.ISSUE_LEVEL_MAP.get(raw, "medium")

    def _canonical_dimension(self, raw_dimension: str) -> str:
        value = str(raw_dimension or "").strip().lower()
        return self.DIMENSION_MAP.get(value, self.DEFAULT_DIMENSION)

    def _canonical_severity(self, raw_severity: str, level: str) -> str:
        value = str(raw_severity or "").strip().lower()

        if value in {"suggestion", "info"}:
            value = "minor"

        if value in self.ALLOWED_SEVERITIES:
            return value

        return self._map_severity(level)

    def _canonical_impact(self, raw_impact: str, level: str) -> str:
        value = str(raw_impact or "").strip().lower()
        if value in self.ALLOWED_IMPACTS:
            return value
        return self._map_impact(level)

    def _map_severity(self, level: str) -> str:
        if level == "high":
            return "critical"
        if level == "medium":
            return "major"
        return "minor"

    def _map_impact(self, level: str) -> str:
        if level == "high":
            return "high"
        if level == "medium":
            return "medium"
        return "low"

    # =====================================================
    # title
    # =====================================================

    def _build_issue_title(self, message: str) -> str:
        msg = re.sub(r"\s+", " ", str(message or "")).strip()

        if not msg:
            return "需求问题"

        if len(msg) <= 18:
            return msg

        return msg[:18] + "..."

    # =====================================================
    # suggestion
    # =====================================================

    def _build_suggestion(self, category: str, message: str) -> str:
        category = str(category or "").strip()

        mapping = {
            "权限安全": "建议补充权限控制、身份校验与安全审计要求。",
            "异常处理": "建议补充异常处理策略和失败兜底逻辑。",
            "性能": "建议补充性能指标、容量预估和并发限制。",
            "业务规则": "建议补充明确的业务规则及判定条件。",
            "状态流转": "建议补充完整状态定义和流转条件。",
            "清晰性": "建议将描述改为可执行、可验证、无歧义表述。",
            "完整性": "建议补充缺失流程、字段或约束条件。",
            "数据定义": "建议补充字段定义和数据校验规则。",
            "接口契约": "建议补充接口字段、入参出参与异常码。",
            "边界场景": "建议补充边界值和极端输入场景。",
            "合规性": "建议补充监管与合规要求。",
            "可测试性": "建议补充可验证场景、验收标准与测试口径。",
            "可追踪性": "建议补充需求、规则、测试点之间的映射关系。",
            "可维护性": "建议补充统一定义和分层规则，降低后续维护成本。",
            "需求质量": "建议补充更明确的业务说明和验收口径。",
        }

        return mapping.get(category, "建议补充更明确的业务说明和验收口径。")

    # =====================================================
    # solution
    # =====================================================

    def _build_solution(self, category: str, message: str) -> str:
        category = str(category or "").strip()

        if category == "权限安全":
            return "在需求文档中明确权限矩阵、安全控制规则和审计要求。"

        if category == "异常处理":
            return "补充失败场景、错误码、回滚与重试策略。"

        if category == "性能":
            return "明确响应时间、并发容量及性能指标。"

        if category == "业务规则":
            return "补充业务规则、判定逻辑和示例。"

        if category == "状态流转":
            return "补充完整状态流转图、触发条件和状态定义。"

        if category == "数据定义":
            return "补充字段字典、数据口径和校验规则。"

        if category == "接口契约":
            return "补充接口文档、字段定义、异常码和降级策略。"

        if category == "完整性":
            return "补充缺失流程、输入输出、约束条件与验收标准。"

        if category == "清晰性":
            return "将模糊表述替换为明确术语，并补充示例和验收口径。"

        if category == "边界场景":
            return "补充边界值、极端输入、空数据和冲突场景处理规则。"

        if category == "合规性":
            return "补充合规披露、风险提示和监管约束要求。"

        if category == "可测试性":
            return "补充测试数据、验收标准与可验证规则。"

        if category == "可追踪性":
            return "建立需求、规则、测试点与问题之间的追踪关系。"

        short_msg = str(message or "").strip()
        if len(short_msg) > 20:
            short_msg = short_msg[:20]

        return f"建议围绕“{short_msg}”补充明确规则与验收标准。"


# 单例
issue_normalizer = IssueNormalizer()