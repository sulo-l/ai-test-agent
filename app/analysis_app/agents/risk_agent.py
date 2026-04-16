# app/analysis_app/agents/risk_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple

from app.analysis_app.models import RequirementIssue


class RequirementRiskAgent:
    """
    需求风险评估 Agent

    目标：
    1. 输出结构与 RequirementRiskReport / RequirementRiskItem 对齐
    2. high_risks / medium_risks / low_risks 必须是 dict 列表
    3. 不依赖 LLM，避免不稳定
    4. 兼容 pipeline 当前调用方式
    5. 分类口径与 models.py / review_agent.py / issue_aggregator 保持一致
    """

    CANONICAL_CATEGORIES = {
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
        "依赖约束",
        "权限安全",
        "合规性",
        "可测试性",
        "可追踪性",
        "可维护性",
        "可扩展性",
        "性能",
        "可观测性",
        "需求质量",
    }

    CATEGORY_ALIASES = {
        "安全": "权限安全",
        "权限": "权限安全",
        "角色权限": "权限安全",
        "访问控制": "权限安全",
        "状态机": "状态流转",
        "状态": "状态流转",
        "依赖": "依赖约束",
        "依赖接口": "依赖约束",
        "数据": "数据定义",
        "数据约束": "数据定义",
        "接口": "接口契约",
        "合规": "合规性",
        "测试": "可测试性",
        "追踪": "可追踪性",
    }

    MISSING_DIMENSION_RISK_LEVEL = {
        "角色权限": "high",
        "安全要求": "high",
        "合规要求": "high",
        "依赖接口": "high",
        "正常流程": "high",
        "异常流程": "high",
        "业务规则": "high",
        "状态流转": "high",
        "数据约束": "medium",
        "性能要求": "medium",
        "可测试性": "medium",
        "边界场景": "medium",
    }

    WEAK_DIMENSION_RISK_LEVEL = {
        "角色权限": "medium",
        "安全要求": "medium",
        "合规要求": "medium",
        "依赖接口": "medium",
        "正常流程": "medium",
        "异常流程": "medium",
        "业务规则": "medium",
        "状态流转": "medium",
        "数据约束": "low",
        "性能要求": "low",
        "可测试性": "low",
        "边界场景": "low",
    }

    SEVERITY_RANK = {
        "blocker": 5,
        "critical": 4,
        "major": 3,
        "minor": 2,
        "suggestion": 1,
    }

    LEVEL_RANK = {
        "high": 3,
        "medium": 2,
        "low": 1,
    }

    def __init__(self):
        pass

    # =====================================================
    # 主入口
    # =====================================================

    def generate_risk_assessment(
        self,
        issues: List[RequirementIssue],
        result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        result = result or {}

        risk_assessment: Dict[str, Any] = {
            "high_risks": [],
            "medium_risks": [],
            "low_risks": [],
            "risk_summary": "",
            "top_risks": [],
            "detailed_report": [],
        }

        # 1) 基于 issues 生成风险项
        for issue in issues or []:
            risk_item = self._issue_to_risk_item(issue)
            if not risk_item:
                continue

            self._append_by_level(risk_assessment, risk_item)
            risk_assessment["detailed_report"].append(risk_item)

        # 2) 基于安全 / 合规 / 覆盖缺口补充风险
        self._add_security_compliance_risks(result, risk_assessment)
        self._add_coverage_risks(result, risk_assessment)

        # 3) 去重 + 排序
        risk_assessment["high_risks"] = self._sort_risk_items(
            self._unique_risk_items(risk_assessment["high_risks"])
        )
        risk_assessment["medium_risks"] = self._sort_risk_items(
            self._unique_risk_items(risk_assessment["medium_risks"])
        )
        risk_assessment["low_risks"] = self._sort_risk_items(
            self._unique_risk_items(risk_assessment["low_risks"])
        )
        risk_assessment["detailed_report"] = self._sort_risk_items(
            self._unique_risk_items(risk_assessment["detailed_report"])
        )

        # 4) top risks
        risk_assessment["top_risks"] = self._build_top_risks(
            risk_assessment["high_risks"],
            risk_assessment["medium_risks"],
            max_items=5,
        )

        # 5) 风险总结（基于最终 dedup 后的风险项）
        risk_assessment["risk_summary"] = self._generate_risk_summary(
            risk_assessment["high_risks"],
            risk_assessment["medium_risks"],
            risk_assessment["low_risks"],
        )

        return risk_assessment

    # =====================================================
    # issue -> risk item
    # =====================================================

    def _issue_to_risk_item(self, issue: Any) -> Optional[Dict[str, Any]]:
        issue_dict = self._issue_to_dict(issue)
        if not issue_dict:
            return None

        message = str(issue_dict.get("message") or "").strip()
        if not message:
            return None

        category = self._normalize_category(issue_dict.get("category"))
        title = str(issue_dict.get("title") or "").strip() or self._default_title_from_category(category)
        suggestion = str(issue_dict.get("suggestion") or "").strip()
        solution = str(issue_dict.get("solution") or "").strip()
        level = self._normalize_level(issue_dict.get("level"))

        severity = self._normalize_severity(issue_dict.get("severity"), level)
        impact = self._normalize_impact(issue_dict.get("impact"), level)

        cause = str(issue_dict.get("reason") or "").strip() or None
        scope = self._infer_scope(category, impact)
        trigger = self._infer_trigger_from_message(message, category)

        issue_id = str(issue_dict.get("id") or "").strip()

        return {
            "id": issue_id or None,
            "title": title,
            "category": category,
            "level": level,
            "severity": severity,
            "impact": impact,
            "message": message,
            "cause": cause,
            "trigger": trigger,
            "scope": scope,
            "suggestion": suggestion or self._default_suggestion(category),
            "solution": solution or self._build_default_solution(category, suggestion, message),
            "related_issue_ids": [issue_id] if issue_id else [],
        }

    def _issue_to_dict(self, issue: Any) -> Optional[Dict[str, Any]]:
        if isinstance(issue, RequirementIssue):
            try:
                return issue.model_dump()
            except Exception:
                return {
                    "id": getattr(issue, "id", None),
                    "level": getattr(issue, "level", None),
                    "severity": getattr(issue, "severity", None),
                    "impact": getattr(issue, "impact", None),
                    "category": getattr(issue, "category", None),
                    "title": getattr(issue, "title", None),
                    "message": getattr(issue, "message", None),
                    "reason": getattr(issue, "reason", None),
                    "suggestion": getattr(issue, "suggestion", None),
                    "solution": getattr(issue, "solution", None),
                }

        if isinstance(issue, dict):
            return issue

        return None

    # =====================================================
    # 补充风险
    # =====================================================

    def _add_security_compliance_risks(
        self,
        result: Dict[str, Any],
        risk_assessment: Dict[str, Any],
    ) -> None:
        security = result.get("security", {}) if isinstance(result, dict) else {}
        compliance = result.get("compliance", {}) if isinstance(result, dict) else {}

        security_gaps = self._ensure_list(security.get("security_gaps", []))
        for gap in security_gaps:
            msg = str(gap or "").strip()
            if not msg:
                continue

            item = self._build_risk_item(
                category="权限安全",
                title="安全控制缺口",
                message=msg,
                suggestion="补充认证、授权、输入校验、审计日志和滥用防控要求。",
                level="medium",
            )
            self._append_by_level(risk_assessment, item)
            risk_assessment["detailed_report"].append(item)

        compliance_gaps = self._ensure_list(compliance.get("compliance_gaps", []))
        for gap in compliance_gaps:
            msg = str(gap or "").strip()
            if not msg:
                continue

            item = self._build_risk_item(
                category="合规性",
                title="合规约束缺口",
                message=msg,
                suggestion="补充隐私、审计、监管与数据保留相关约束。",
                level="medium",
            )
            self._append_by_level(risk_assessment, item)
            risk_assessment["detailed_report"].append(item)

    def _add_coverage_risks(
        self,
        result: Dict[str, Any],
        risk_assessment: Dict[str, Any],
    ) -> None:
        coverage = result.get("coverage", {}) if isinstance(result, dict) else {}
        weak_dimensions = self._ensure_list(coverage.get("weak_dimensions", []))
        missing_dimensions = self._ensure_list(coverage.get("missing_dimensions", []))

        for item in missing_dimensions:
            if not isinstance(item, dict):
                continue

            dim = str(item.get("dimension") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if not reason:
                continue

            level = self.MISSING_DIMENSION_RISK_LEVEL.get(dim, "medium")
            category = self._category_from_dimension(dim)

            risk_item = self._build_risk_item(
                category=category,
                title=f"{dim}覆盖缺失" if dim else "覆盖缺失风险",
                message=reason,
                suggestion="补充缺失维度对应的规则、流程和验收标准。",
                level=level,
            )
            self._append_by_level(risk_assessment, risk_item)
            risk_assessment["detailed_report"].append(risk_item)

        for item in weak_dimensions:
            if not isinstance(item, dict):
                continue

            dim = str(item.get("dimension") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if not reason:
                continue

            level = self.WEAK_DIMENSION_RISK_LEVEL.get(dim, "low")
            category = self._category_from_dimension(dim)

            risk_item = self._build_risk_item(
                category=category,
                title=f"{dim}覆盖薄弱" if dim else "覆盖薄弱风险",
                message=reason,
                suggestion="补充薄弱维度的边界、异常和验收口径说明。",
                level=level,
            )
            self._append_by_level(risk_assessment, risk_item)
            risk_assessment["detailed_report"].append(risk_item)

    # =====================================================
    # 风险项构造
    # =====================================================

    def _build_risk_item(
        self,
        category: str,
        title: str,
        message: str,
        suggestion: str,
        level: str,
        related_issue_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        level = self._normalize_level(level)
        category = self._normalize_category(category)

        return {
            "id": None,
            "title": title or self._default_title_from_category(category),
            "category": category,
            "level": level,
            "severity": self._severity_from_level(level),
            "impact": self._impact_from_level(level),
            "message": str(message or "").strip(),
            "cause": None,
            "trigger": self._infer_trigger_from_message(message, category),
            "scope": self._infer_scope(category, self._impact_from_level(level)),
            "suggestion": str(suggestion or "").strip() or self._default_suggestion(category),
            "solution": self._build_default_solution(category, suggestion, message),
            "related_issue_ids": related_issue_ids or [],
        }

    def _build_default_solution(self, category: str, suggestion: str, message: str) -> str:
        suggestion = str(suggestion or "").strip()
        if suggestion:
            return suggestion

        category = self._normalize_category(category)

        if category == "权限安全":
            return "明确认证鉴权、权限控制、输入校验和审计要求。"
        if category == "合规性":
            return "补充隐私、审计、数据保留与监管约束。"
        if category == "异常处理":
            return "补充失败场景、错误码、重试、回滚与兜底策略。"
        if category == "性能":
            return "明确性能指标、容量边界和高峰处理策略。"
        if category in {"业务规则", "状态流转", "流程逻辑"}:
            return "补充明确的业务规则、状态流转条件和例外处理逻辑。"
        if category in {"依赖约束", "接口契约"}:
            return "明确上下游接口约束、失败处理、超时与重试策略。"
        if category == "数据定义":
            return "补充字段定义、格式约束、校验规则和数据边界。"
        if category == "完整性":
            return "补充缺失主流程、异常流程、关键规则和验收条件。"
        if category == "边界场景":
            return "补充边界值、空数据、极端输入和冲突场景说明。"
        if category == "可测试性":
            return "补充可量化验收标准、测试数据与可验证口径。"
        if category == "可追踪性":
            return "建立需求、规则、测试点和问题之间的追踪关系。"
        if category == "可维护性":
            return "统一定义和分层结构，减少重复与口径漂移。"
        if category == "可观测性":
            return "补充日志、监控、埋点、告警和排障要求。"

        short_msg = str(message or "").strip()[:20]
        return f"建议围绕“{short_msg}”补充明确规则、约束和验收标准。"

    def _default_title_from_category(self, category: str) -> str:
        category = self._normalize_category(category)

        mapping = {
            "权限安全": "权限安全风险",
            "合规性": "合规风险",
            "异常处理": "异常处理风险",
            "性能": "性能风险",
            "业务规则": "规则缺失风险",
            "状态流转": "状态流转风险",
            "流程逻辑": "流程逻辑风险",
            "依赖约束": "依赖不确定风险",
            "接口契约": "接口契约风险",
            "数据定义": "数据约束风险",
            "完整性": "需求完整性风险",
            "边界场景": "边界场景风险",
            "可测试性": "可测试性风险",
            "可追踪性": "可追踪性风险",
            "可维护性": "可维护性风险",
            "可扩展性": "可扩展性风险",
            "可观测性": "可观测性风险",
            "需求质量": "需求质量风险",
            "清晰性": "理解偏差风险",
            "一致性": "口径不一致风险",
        }
        return mapping.get(category, "需求风险")

    def _default_suggestion(self, category: str) -> str:
        category = self._normalize_category(category)

        if category == "权限安全":
            return "补充安全控制要求与验收口径。"
        if category == "合规性":
            return "补充合规约束、留痕与数据处理边界。"
        if category == "异常处理":
            return "补充异常流程、失败处理与兜底策略。"
        if category == "性能":
            return "明确性能指标、容量边界和压测目标。"
        if category in {"业务规则", "状态流转", "流程逻辑"}:
            return "补充规则定义、状态条件和例外分支。"
        if category in {"依赖约束", "接口契约"}:
            return "补充依赖接口、失败处理和联调约束。"
        if category == "数据定义":
            return "补充字段约束、数据格式和校验规则。"
        if category == "完整性":
            return "补充关键流程、规则、约束与验收标准。"
        if category == "边界场景":
            return "补充边界、冲突、极端和空数据场景。"
        if category == "可测试性":
            return "补充验收条件、测试数据和量化标准。"
        if category == "可追踪性":
            return "补充需求到规则、测试点和问题的映射。"
        if category == "可观测性":
            return "补充日志、埋点、监控与告警要求。"

        return "补充明确规则、约束和验收标准。"

    def _category_from_dimension(self, dimension: str) -> str:
        mapping = {
            "正常流程": "完整性",
            "异常流程": "异常处理",
            "边界场景": "边界场景",
            "业务规则": "业务规则",
            "状态流转": "状态流转",
            "角色权限": "权限安全",
            "数据约束": "数据定义",
            "依赖接口": "依赖约束",
            "安全要求": "权限安全",
            "性能要求": "性能",
            "可测试性": "可测试性",
            "合规要求": "合规性",
        }
        return mapping.get(str(dimension or "").strip(), "需求质量")

    # =====================================================
    # 风险总结
    # =====================================================

    def _generate_risk_summary(
        self,
        high_risks: List[Dict[str, Any]],
        medium_risks: List[Dict[str, Any]],
        low_risks: List[Dict[str, Any]],
    ) -> str:
        high_count = len(high_risks or [])
        medium_count = len(medium_risks or [])
        low_count = len(low_risks or [])

        if high_count == 0 and medium_count == 0 and low_count == 0:
            return "未识别出明显风险。"

        parts: List[str] = []
        total = high_count + medium_count + low_count
        parts.append(f"共识别风险 {total} 项。")

        if high_count:
            parts.append(f"其中高风险 {high_count} 项，建议优先处理。")
        if medium_count:
            parts.append(f"中风险 {medium_count} 项，需要在设计与研发前补齐。")
        if low_count:
            parts.append(f"低风险 {low_count} 项，可在后续细化阶段完善。")

        top_msgs: List[str] = []
        for item in (high_risks or [])[:3]:
            title = str(item.get("title") or "").strip()
            msg = str(item.get("message") or "").strip()
            text = msg or title
            if text:
                top_msgs.append(text)

        if top_msgs:
            parts.append("重点风险包括：" + "；".join(top_msgs) + "。")

        return " ".join(parts)

    def _build_top_risks(
        self,
        high_risks: List[Dict[str, Any]],
        medium_risks: List[Dict[str, Any]],
        max_items: int = 5,
    ) -> List[str]:
        results: List[str] = []

        for item in (high_risks or []) + (medium_risks or []):
            title = str(item.get("title") or "").strip()
            msg = str(item.get("message") or "").strip()

            text = title or msg
            if text and text not in results:
                results.append(text)

            if len(results) >= max_items:
                break

        return results

    # =====================================================
    # 兼容旧接口
    # =====================================================

    def generate_detailed_risk_report(self, issues: List[RequirementIssue]) -> Dict[str, Any]:
        return self.generate_risk_assessment(issues=issues, result={})

    def generate_risk_suggestions(self, issues: List[RequirementIssue]) -> List[str]:
        risk_suggestions: List[str] = []

        for issue in issues or []:
            issue_dict = self._issue_to_dict(issue)
            if not issue_dict:
                continue

            if str(issue_dict.get("level") or "").strip() in {"high", "medium"}:
                suggestion = str(issue_dict.get("suggestion") or "").strip()
                if suggestion:
                    risk_suggestions.append(suggestion)

        return self._unique_keep_order(risk_suggestions)

    # =====================================================
    # 工具方法
    # =====================================================

    def _append_by_level(
        self,
        risk_assessment: Dict[str, Any],
        risk_item: Dict[str, Any],
    ) -> None:
        level = str(risk_item.get("level") or "medium").strip().lower()

        if level == "high":
            risk_assessment["high_risks"].append(risk_item)
        elif level == "medium":
            risk_assessment["medium_risks"].append(risk_item)
        else:
            risk_assessment["low_risks"].append(risk_item)

    def _normalize_category(self, category: Any) -> str:
        c = str(category or "需求质量").strip()
        if not c:
            return "需求质量"

        if c in self.CANONICAL_CATEGORIES:
            return c

        mapped = self.CATEGORY_ALIASES.get(c)
        if mapped:
            return mapped

        lower_map = {k.lower(): v for k, v in self.CATEGORY_ALIASES.items()}
        canonical_lower = {x.lower(): x for x in self.CANONICAL_CATEGORIES}

        low = c.lower()
        if low in lower_map:
            return lower_map[low]
        if low in canonical_lower:
            return canonical_lower[low]

        return "需求质量"

    def _normalize_level(self, level: Any) -> str:
        lv = str(level or "medium").strip().lower()
        return lv if lv in {"high", "medium", "low"} else "medium"

    def _normalize_severity(self, severity: Any, level: str) -> str:
        s = str(severity or "").strip().lower()
        aliases = {
            "high": "critical",
            "medium": "major",
            "low": "minor",
        }
        s = aliases.get(s, s)
        if s in {"blocker", "critical", "major", "minor", "suggestion"}:
            return s
        return self._severity_from_level(level)

    def _normalize_impact(self, impact: Any, level: str) -> str:
        s = str(impact or "").strip().lower()
        if s in {"high", "medium", "low"}:
            return s
        return self._impact_from_level(level)

    def _severity_from_level(self, level: str) -> str:
        if level == "high":
            return "critical"
        if level == "medium":
            return "major"
        return "minor"

    def _impact_from_level(self, level: str) -> str:
        if level == "high":
            return "high"
        if level == "medium":
            return "medium"
        return "low"

    def _infer_scope(self, category: str, impact: str) -> str:
        category = self._normalize_category(category)
        impact = str(impact or "").strip()

        if category in {"权限安全", "合规性"}:
            return "可能影响系统安全、数据合规与审计要求。"
        if category in {"业务规则", "状态流转", "流程逻辑", "异常处理"}:
            return "可能影响核心业务流程实现与验收口径。"
        if category in {"依赖约束", "接口契约", "数据定义", "需求质量"}:
            return "可能影响系统联调、数据一致性与需求落地质量。"
        if category in {"完整性", "边界场景", "可测试性"}:
            return "可能影响设计、开发与测试覆盖闭环。"
        if category in {"可追踪性", "可维护性", "可扩展性", "可观测性"}:
            return "可能影响后续维护、定位与持续迭代效率。"

        if impact == "high":
            return "可能影响核心功能交付与上线质量。"
        if impact == "medium":
            return "可能影响研发实现一致性和测试验证效率。"
        return "可能在后续细化阶段带来额外修正成本。"

    def _infer_trigger_from_message(self, message: str, category: str) -> Optional[str]:
        text = str(message or "").strip()
        if not text:
            return None

        category = self._normalize_category(category)

        if category in {"权限安全", "合规性"}:
            return "在涉及认证、授权、隐私、审计或数据处理场景时触发。"
        if category == "异常处理":
            return "在接口失败、参数异常、依赖超时或业务失败场景时触发。"
        if category in {"状态流转", "业务规则", "流程逻辑"}:
            return "在状态切换、规则判断或例外分支执行时触发。"
        if category in {"依赖约束", "接口契约"}:
            return "在上下游接口联调、回调、重试或降级场景时触发。"
        if category == "数据定义":
            return "在字段落库、计算口径、格式校验或数据展示场景时触发。"

        return None

    def _ensure_list(self, value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if value is None:
            return []
        return [value]

    def _unique_keep_order(self, items: List[str]) -> List[str]:
        seen = set()
        result: List[str] = []
        for item in items:
            value = str(item or "").strip()
            if not value:
                continue
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _unique_risk_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        result: List[Dict[str, Any]] = []

        for item in items or []:
            if not isinstance(item, dict):
                continue

            key = (
                str(item.get("category") or "").strip(),
                str(item.get("title") or "").strip(),
                str(item.get("message") or "").strip(),
                str(item.get("level") or "").strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(item)

        return result

    def _sort_risk_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def key_fn(item: Dict[str, Any]) -> Tuple[int, int, str, str]:
            severity = str(item.get("severity") or "").strip().lower()
            level = str(item.get("level") or "").strip().lower()
            title = str(item.get("title") or "").strip()
            message = str(item.get("message") or "").strip()
            return (
                -self.SEVERITY_RANK.get(severity, 0),
                -self.LEVEL_RANK.get(level, 0),
                title,
                message,
            )

        return sorted(items or [], key=key_fn)


# =====================================================
# 单例模式：需求风险评估 Agent
# =====================================================

risk_agent = RequirementRiskAgent()