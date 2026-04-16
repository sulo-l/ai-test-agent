#! /usr/bin/python3
# coding=utf-8
# app/analysis_app/issue_aggregator.py

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from app.analysis_app.utils.issue_normalizer import issue_normalizer


class IssueAggregator:
    """
    统一问题聚合器

    职责：
    1 聚合所有 agent 输出
    2 统一 category / severity / dimension
    3 去重
    4 应用 review 修正
    5 输出最终问题池
    """

    DEFAULT_CATEGORY = "需求质量"
    DEFAULT_DIMENSION = "analysis"

    # =====================================================
    # 非业务问题
    # =====================================================

    NON_BUSINESS_CATEGORIES = {"系统", "解析"}

    NON_BUSINESS_KEYWORDS = [
        "json",
        "markdown",
        "llm",
        "api key",
        "base url",
        "模型配置",
        "提示词",
        "analysis failed",
        "模型返回格式异常",
        "解析失败",
    ]

    # =====================================================
    # 分类
    # =====================================================

    ALLOWED_CATEGORIES = {
        "完整性",
        "清晰性",
        "一致性",
        "业务规则",
        "流程逻辑",
        "异常处理",
        "边界场景",
        "状态流转",
        "数据定义",
        "接口契约",
        "权限安全",
        "合规性",
        "可测试性",
        "可追踪性",
        "可维护性",
        "可扩展性",
        "性能",
        "需求质量",
    }

    CATEGORY_ALIAS = {
        "安全": "权限安全",
        "权限": "权限安全",
        "权限控制": "权限安全",
        "角色权限": "权限安全",

        "依赖": "接口契约",
        "依赖接口": "接口契约",
        "接口": "接口契约",

        "数据": "数据定义",
        "数据约束": "数据定义",

        "状态": "状态流转",
        "状态机": "状态流转",

        "合规": "合规性",
        "测试": "可测试性",
        "追踪": "可追踪性",
    }

    CATEGORY_MAPPING = {
        "正常流程": "完整性",
        "异常流程": "异常处理",
        "边界场景": "边界场景",
        "业务规则": "业务规则",
        "状态流转": "状态流转",
        "角色权限": "权限安全",
        "数据约束": "数据定义",
        "依赖接口": "接口契约",
        "安全要求": "权限安全",
        "性能要求": "性能",
        "可测试性": "可测试性",
        "合规要求": "合规性",
    }

    # =====================================================
    # 等级
    # =====================================================

    SEVERITY_LEVEL_MAPPING = {
        "blocker": "high",
        "critical": "high",
        "major": "medium",
        "minor": "low",
    }

    ALLOWED_LEVELS = {"high", "medium", "low"}
    ALLOWED_SEVERITIES = {"blocker", "critical", "major", "minor"}
    ALLOWED_IMPACTS = {"high", "medium", "low"}

    # =====================================================
    # 主入口
    # =====================================================

    def aggregate(
        self,
        *,
        analysis_issues: Optional[List[Dict[str, Any]]] = None,
        rules: Optional[Dict[str, Any]] = None,
        consistency: Optional[Dict[str, Any]] = None,
        coverage: Optional[Dict[str, Any]] = None,
        security: Optional[Dict[str, Any]] = None,
        compliance: Optional[Dict[str, Any]] = None,
        traceability: Optional[Dict[str, Any]] = None,
        review_result: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:

        raw_items: List[Dict[str, Any]] = []

        raw_items.extend(self._from_analysis_issues(analysis_issues or []))
        raw_items.extend(self._from_rules(rules or {}))
        raw_items.extend(self._from_consistency(consistency or {}))
        raw_items.extend(self._from_security(security or {}))
        raw_items.extend(self._from_compliance(compliance or {}))
        raw_items.extend(self._from_coverage(coverage or {}))
        raw_items.extend(self._from_traceability(traceability or {}))
        raw_items.extend(self._from_review_missing_findings(review_result or {}))

        issues = issue_normalizer.normalize_issues(raw_items)

        issues = self._canonicalize_issues(issues)
        issues = self.filter_non_business_issues(issues)
        issues = self.dedup_issues(issues)

        issues = self.apply_review_corrections(issues, review_result or {})

        issues = self._canonicalize_issues(issues)
        issues = self.filter_non_business_issues(issues)
        issues = self.dedup_issues(issues)

        return self.sort_issues(issues)

    # =====================================================
    # 来源转换
    # =====================================================

    def _from_analysis_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for item in issues or []:
            if not isinstance(item, dict):
                continue

            cloned = deepcopy(item)
            cloned.setdefault("source_agent", "analysis")
            cloned.setdefault("dimension", self.DEFAULT_DIMENSION)
            rows.append(cloned)

        return rows

    def _from_rules(self, rules: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for text in rules.get("ambiguous_rules", []) or []:
            msg = str(text or "").strip()
            if not msg:
                continue

            rows.append({
                "level": "medium",
                "category": "清晰性",
                "dimension": "rule",
                "title": "规则表述歧义",
                "message": msg,
                "suggestion": "补充明确的规则定义、适用范围与判定标准。",
                "severity": "major",
                "impact": "medium",
                "solution": "在需求中补齐规则说明、示例与边界条件。",
                "source_agent": "rule",
            })

        for text in rules.get("unresolved_rules", []) or []:
            msg = str(text or "").strip()
            if not msg:
                continue

            rows.append({
                "level": "medium",
                "category": "业务规则",
                "dimension": "rule",
                "title": "规则未闭环",
                "message": msg,
                "suggestion": "补齐未闭环规则及对应异常分支。",
                "severity": "major",
                "impact": "medium",
                "solution": "补充完整规则链路、输入输出和例外处理。",
                "source_agent": "rule",
            })

        return rows

    def _from_consistency(self, consistency: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        consistency_mapping = {
            "rule_conflicts": ("业务规则", "high", "critical", "high"),
            "state_conflicts": ("状态流转", "high", "critical", "high"),
            "role_conflicts": ("权限安全", "medium", "major", "medium"),
            "flow_conflicts": ("清晰性", "medium", "major", "medium"),
            "term_conflicts": ("清晰性", "medium", "major", "medium"),
            "data_conflicts": ("数据定义", "high", "major", "high"),
        }

        for key, (category, level, severity, impact) in consistency_mapping.items():
            for item in consistency.get(key, []) or []:
                if not isinstance(item, dict):
                    continue

                title = str(item.get("title") or "一致性问题").strip()
                message = str(item.get("message") or "").strip()
                reason = str(item.get("reason") or "").strip()

                if not message:
                    continue

                rows.append({
                    "level": level,
                    "category": category,
                    "dimension": "consistency",
                    "title": title,
                    "message": message,
                    "reason": reason,
                    "suggestion": reason or "建议统一口径并补充明确规则。",
                    "severity": severity,
                    "impact": impact,
                    "solution": "统一术语、规则、状态和数据定义，并明确优先级与判定条件。",
                    "source_agent": "consistency",
                    "tags": list(item.get("related_terms", []) or []),
                })

        for text in consistency.get("consistency_gaps", []) or []:
            msg = str(text or "").strip()
            if not msg:
                continue

            rows.append({
                "level": "medium",
                "category": "清晰性",
                "dimension": "consistency",
                "title": "一致性缺口",
                "message": msg,
                "suggestion": "补充一致性约束与统一口径说明。",
                "severity": "major",
                "impact": "medium",
                "solution": "在需求文档中统一术语、规则和数据口径。",
                "source_agent": "consistency",
            })

        return rows

    def _from_security(self, security: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for text in security.get("security_gaps", []) or []:
            msg = str(text or "").strip()
            if not msg:
                continue

            rows.append({
                "level": "medium",
                "category": "权限安全",
                "dimension": "security",
                "title": "安全缺口",
                "message": msg,
                "suggestion": "补充认证、授权、输入校验、审计及安全防护要求。",
                "severity": "major",
                "impact": "high",
                "solution": "在需求中补齐与当前功能相关的安全控制与验收规则。",
                "source_agent": "security",
            })

        return rows

    def _from_compliance(self, compliance: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for text in compliance.get("compliance_gaps", []) or []:
            msg = str(text or "").strip()
            if not msg:
                continue

            rows.append({
                "level": "medium",
                "category": "合规性",
                "dimension": "compliance",
                "title": "合规缺口",
                "message": msg,
                "suggestion": "补充隐私、审计、监管和数据留存相关要求。",
                "severity": "major",
                "impact": "high",
                "solution": "在需求中明确合规边界、提示、留痕和数据处理策略。",
                "source_agent": "compliance",
            })

        return rows

    def _from_coverage(self, coverage: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for item in coverage.get("missing_dimensions", []) or []:
            if not isinstance(item, dict):
                continue

            dim = str(item.get("dimension") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if not reason:
                continue

            rows.append({
                "level": "medium",
                "category": self.category_from_coverage_dimension(dim),
                "dimension": "coverage",
                "title": f"{dim}覆盖缺失" if dim else "覆盖缺失",
                "message": reason,
                "suggestion": "补齐该维度需求说明、规则与验收标准。",
                "severity": "major",
                "impact": "medium",
                "solution": "围绕该维度补充需求细节、场景和验收口径。",
                "source_agent": "coverage",
            })

        for item in coverage.get("weak_dimensions", []) or []:
            if not isinstance(item, dict):
                continue

            dim = str(item.get("dimension") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if not reason:
                continue

            rows.append({
                "level": "low",
                "category": self.category_from_coverage_dimension(dim),
                "dimension": "coverage",
                "title": f"{dim}覆盖薄弱" if dim else "覆盖薄弱",
                "message": reason,
                "suggestion": "进一步补充该维度规则和示例。",
                "severity": "minor",
                "impact": "low",
                "solution": "增加该维度场景说明、边界和示例。",
                "source_agent": "coverage",
            })

        for text in coverage.get("coverage_gaps", []) or []:
            msg = str(text or "").strip()
            if not msg:
                continue

            rows.append({
                "level": "medium",
                "category": "完整性",
                "dimension": "coverage",
                "title": "覆盖率缺口",
                "message": msg,
                "suggestion": "补齐缺失覆盖维度和验收条件。",
                "severity": "major",
                "impact": "medium",
                "solution": "按主流程、异常、边界、权限、安全等维度补全需求。",
                "source_agent": "coverage",
            })

        return rows

    def _from_traceability(self, traceability: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for item in traceability.get("uncovered_requirements", []) or []:
            if not isinstance(item, dict):
                continue

            message = str(item.get("reason") or "").strip()
            if not message:
                continue

            rows.append({
                "level": "medium",
                "category": "可追踪性",
                "dimension": "traceability",
                "title": str(item.get("name") or "未覆盖需求").strip(),
                "message": message,
                "suggestion": "补齐需求到规则/测试点/风险的追踪关系。",
                "severity": "major",
                "impact": "medium",
                "solution": "建立需求编号与规则、测试点、问题之间的追踪矩阵。",
                "source_agent": "traceability",
            })

        for item in traceability.get("orphan_rules", []) or []:
            if not isinstance(item, dict):
                continue

            message = str(item.get("reason") or "").strip()
            if not message:
                continue

            rows.append({
                "level": "low",
                "category": "可追踪性",
                "dimension": "traceability",
                "title": str(item.get("name") or "孤立规则").strip(),
                "message": message,
                "suggestion": "明确规则归属的需求条目。",
                "severity": "minor",
                "impact": "low",
                "solution": "为规则补齐来源需求与追踪关系。",
                "source_agent": "traceability",
            })

        for item in traceability.get("orphan_test_points", []) or []:
            if not isinstance(item, dict):
                continue

            message = str(item.get("reason") or "").strip()
            if not message:
                continue

            rows.append({
                "level": "low",
                "category": "可追踪性",
                "dimension": "traceability",
                "title": str(item.get("name") or "孤立测试点").strip(),
                "message": message,
                "suggestion": "补齐测试点来源需求与追踪关系。",
                "severity": "minor",
                "impact": "low",
                "solution": "为测试点补充对应需求、规则或风险来源。",
                "source_agent": "traceability",
            })

        return rows

    def _from_review_missing_findings(self, review_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for item in review_result.get("missing_findings", []) or []:
            if not isinstance(item, dict):
                continue

            message = str(item.get("message") or "").strip()
            if not message:
                continue

            rows.append({
                "id": str(item.get("item_id") or "").strip() or None,
                "level": "high",
                "category": str(item.get("category") or "完整性").strip() or "完整性",
                "dimension": "general",
                "title": str(item.get("title") or "复核遗漏项").strip(),
                "message": message,
                "reason": str(item.get("reason") or "").strip(),
                "suggestion": str(item.get("suggestion") or item.get("reason") or "").strip(),
                "severity": "critical",
                "impact": "high",
                "solution": "根据复核意见补充缺失规则、口径和约束，并形成闭环。",
                "source_agent": "review",
            })

        return rows

    # =====================================================
    # review 修正
    # =====================================================

    def apply_review_corrections(
        self,
        issues: List[Dict[str, Any]],
        review_result: Dict[str, Any],
    ) -> List[Dict[str, Any]]:

        if not issues:
            return issues

        result = [deepcopy(x) for x in issues]
        by_id: Dict[str, Dict[str, Any]] = {}

        for row in result:
            issue_id = str(row.get("id") or "")
            if issue_id:
                by_id[issue_id] = row

        for item in review_result.get("category_corrections", []) or []:
            if not isinstance(item, dict):
                continue

            issue_id = str(item.get("issue_id") or item.get("item_id") or "").strip()
            to_category = self._canonical_category(str(item.get("to") or "").strip())

            if issue_id in by_id and to_category:
                by_id[issue_id]["category"] = to_category

        for item in review_result.get("severity_corrections", []) or []:
            if not isinstance(item, dict):
                continue

            issue_id = str(item.get("issue_id") or item.get("item_id") or "").strip()
            to_sev = str(item.get("to") or "").lower().strip()

            if issue_id in by_id and to_sev in self.ALLOWED_SEVERITIES:
                by_id[issue_id]["severity"] = to_sev
                by_id[issue_id]["level"] = self.level_from_severity(to_sev)

        for item in review_result.get("suggestion_improvements", []) or []:
            if not isinstance(item, dict):
                continue

            issue_id = str(item.get("issue_id") or item.get("item_id") or "").strip()
            improved = str(item.get("improved") or item.get("suggestion") or "").strip()

            if issue_id in by_id and improved:
                by_id[issue_id]["suggestion"] = improved

        return result

    # =====================================================
    # 去重
    # =====================================================

    def dedup_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        results: List[Dict[str, Any]] = []
        used_ids = set()
        auto_index = 1

        for item in issues:
            if not isinstance(item, dict):
                continue

            cloned = deepcopy(item)

            category = str(cloned.get("category") or "")
            title = self._normalize_text(cloned.get("title"))
            msg = self._normalize_text(cloned.get("message"))

            key = (category, title, msg)

            if key in seen:
                continue

            seen.add(key)

            raw_id = str(cloned.get("id") or "").strip()
            if raw_id and raw_id not in used_ids:
                issue_id = raw_id
            else:
                while True:
                    candidate = f"ISSUE-{auto_index:03d}"
                    auto_index += 1
                    if candidate not in used_ids:
                        issue_id = candidate
                        break

            cloned["id"] = issue_id
            used_ids.add(issue_id)

            results.append(cloned)

        return results

    # =====================================================
    # 过滤
    # =====================================================

    def filter_non_business_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for item in issues:
            if not isinstance(item, dict):
                continue

            category = str(item.get("category") or "").strip()
            title = str(item.get("title") or "").strip()
            message = str(item.get("message") or "").strip()

            if category in self.NON_BUSINESS_CATEGORIES:
                continue

            text = f"{title} {message}".lower()
            if any(k in text for k in self.NON_BUSINESS_KEYWORDS):
                continue

            if not message:
                continue

            rows.append(item)

        return rows

    # =====================================================
    # 排序
    # =====================================================

    def sort_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        severity_rank = {
            "blocker": 5,
            "critical": 4,
            "major": 3,
            "minor": 2,
        }

        level_rank = {
            "high": 3,
            "medium": 2,
            "low": 1,
        }

        def key_fn(x: Dict[str, Any]) -> Tuple[int, int, str]:
            sev = str(x.get("severity") or "").lower()
            lvl = str(x.get("level") or "").lower()

            return (
                -severity_rank.get(sev, 0),
                -level_rank.get(lvl, 0),
                str(x.get("id") or ""),
            )

        return sorted(issues, key=key_fn)

    # =====================================================
    # top issues / risk refs
    # =====================================================

    def build_top_issues(self, issues: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
        rows = self.sort_issues(issues)
        results: List[Dict[str, Any]] = []

        for item in rows[: max(1, limit)]:
            results.append({
                "issueId": str(item.get("id") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "category": str(item.get("category") or "").strip(),
                "level": str(item.get("level") or "").strip(),
                "severity": str(item.get("severity") or "").strip(),
            })

        return results

    def build_risk_issue_refs(self, issues: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        rows = self.sort_issues(issues)

        high_ids: List[str] = []
        medium_ids: List[str] = []
        low_ids: List[str] = []

        for item in rows:
            issue_id = str(item.get("id") or "").strip()
            if not issue_id:
                continue

            level = str(item.get("level") or "").strip().lower()
            if level == "high":
                high_ids.append(issue_id)
            elif level == "medium":
                medium_ids.append(issue_id)
            else:
                low_ids.append(issue_id)

        return {
            "highIssueIds": high_ids,
            "mediumIssueIds": medium_ids,
            "lowIssueIds": low_ids,
        }

    # =====================================================
    # 规范
    # =====================================================

    def _canonicalize_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for item in issues:
            if not isinstance(item, dict):
                continue

            cloned = deepcopy(item)

            cloned["category"] = self._canonical_category(
                str(cloned.get("category") or self.DEFAULT_CATEGORY)
            )

            cloned["severity"] = self._canonical_severity(
                str(cloned.get("severity") or "major")
            )

            cloned["level"] = self.level_from_severity(
                cloned["severity"],
                fallback=str(cloned.get("level") or "medium"),
            )

            cloned["impact"] = self._canonical_impact(
                str(cloned.get("impact") or "medium")
            )

            cloned["dimension"] = str(
                cloned.get("dimension") or self.DEFAULT_DIMENSION
            ).strip() or self.DEFAULT_DIMENSION

            if not str(cloned.get("title") or "").strip():
                msg = str(cloned.get("message") or "").strip()
                cloned["title"] = msg[:18] + ("..." if len(msg) > 18 else "") if msg else "未命名问题"

            rows.append(cloned)

        return rows

    # =====================================================
    # 工具
    # =====================================================

    def category_from_coverage_dimension(self, dimension: str) -> str:
        return self._canonical_category(
            self.CATEGORY_MAPPING.get(str(dimension or "").strip(), self.DEFAULT_CATEGORY)
        )

    def level_from_severity(self, severity: str, fallback: str = "medium") -> str:
        value = str(severity or "").strip().lower()

        if value in self.SEVERITY_LEVEL_MAPPING:
            return self.SEVERITY_LEVEL_MAPPING[value]

        fallback = str(fallback or "medium").strip().lower()
        if fallback in self.ALLOWED_LEVELS:
            return fallback

        return "medium"

    def _canonical_category(self, category: str) -> str:
        value = str(category or "").strip()
        value = self.CATEGORY_ALIAS.get(value, value)

        if value in self.ALLOWED_CATEGORIES:
            return value

        return self.DEFAULT_CATEGORY

    def _canonical_severity(self, severity: str) -> str:
        value = str(severity or "").lower().strip()

        if value == "suggestion":
            return "minor"

        if value in self.ALLOWED_SEVERITIES:
            return value

        return "major"

    def _canonical_impact(self, impact: str) -> str:
        value = str(impact or "").lower().strip()

        if value in self.ALLOWED_IMPACTS:
            return value

        return "medium"

    def _normalize_text(self, text: Any) -> str:
        value = str(text or "").lower()
        value = value.replace("“", "\"").replace("”", "\"")
        value = value.replace("（", "(").replace("）", ")")
        value = value.replace("，", ",").replace("。", ".").replace("：", ":").replace("；", ";")
        value = re.sub(r"\s+", "", value)
        return value


issue_aggregator = IssueAggregator()