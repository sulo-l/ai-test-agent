# app/analysis_app/agents/analysis_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import List, Dict, Any
import json
import re

from app.analysis_app.agents.base_agent import BaseAgent


class RequirementAnalysisAgent(BaseAgent):
    """
    企业级需求分析 Agent（标准分类统一版）

    目标：
    1. 统一输出标准分类
    2. 过滤系统噪音 / 模型噪音 / 背景类伪问题
    3. 只保留真实、可执行、可测试的需求问题
    4. 增强 JSON 容错
    5. 为后续 issue_aggregator / pipeline / score / risk 提供稳定输入
    """

    name = "analysis"

    SYSTEM_PROMPT = (
        "你是一名资深软件需求评审专家。\n"
        "你必须只输出 JSON，不要输出 markdown，不要输出解释。\n"
        "输出格式必须是 JSON 数组。\n"
        "如果未识别到明确问题，请输出 []。\n"
        "不要输出与模型、提示词、解析、JSON、系统错误相关的内容。\n"
        "只输出与需求文本本身直接相关的问题。\n"
        "不要把项目背景、建设目标、业务价值、收益说明、方案意义、设计动机、现状介绍当作需求问题输出。\n"
        "只有当文本描述了系统应该做什么、怎么做、什么条件下做、失败时如何处理、字段/状态/权限/规则/边界如何定义时，才可以识别为真实需求问题。\n"
    )

    # =====================================================
    # 标准分类（统一）
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

    # LLM 常见分类别名 -> 标准分类
    CATEGORY_ALIAS = {
        "安全": "权限安全",
        "安全性": "权限安全",
        "权限": "权限安全",
        "权限控制": "权限安全",
        "访问控制": "权限安全",
        "鉴权": "权限安全",

        "状态机": "状态流转",
        "状态": "状态流转",
        "状态变化": "状态流转",

        "数据": "数据定义",
        "数据约束": "数据定义",
        "字段定义": "数据定义",
        "数据口径": "数据定义",

        "依赖": "依赖约束",
        "外部依赖": "依赖约束",

        "接口": "接口契约",
        "接口定义": "接口契约",
        "接口设计": "接口契约",
        "API": "接口契约",
        "api": "接口契约",

        "流程": "流程逻辑",
        "流程流转": "流程逻辑",

        "规则": "业务规则",
        "规则定义": "业务规则",

        "异常": "异常处理",
        "错误处理": "异常处理",

        "边界": "边界场景",
        "边界值": "边界场景",

        "可测试": "可测试性",
        "测试性": "可测试性",

        "可追踪": "可追踪性",
        "追踪性": "可追踪性",

        "维护性": "可维护性",
        "扩展性": "可扩展性",
        "观测性": "可观测性",
    }

    ALLOWED_LEVELS = {"high", "medium", "low"}

    ALLOWED_SEVERITIES = {
        "blocker",
        "critical",
        "major",
        "minor",
        "suggestion",
    }

    ALLOWED_IMPACTS = {"high", "medium", "low"}

    BLOCKED_KEYWORDS = [
        "模型返回格式异常",
        "输出异常",
        "解析失败",
        "json",
        "markdown",
        "提示词",
        "llm",
        "api key",
        "base url",
        "模型配置",
        "系统错误",
        "analysis failed",
        "openai",
        "langchain",
        "client",
    ]

    BACKGROUND_KEYWORDS = [
        "项目背景",
        "需求背景",
        "建设背景",
        "业务背景",
        "背景说明",
        "方案背景",
        "现状分析",
        "当前现状",
        "痛点分析",
        "建设目标",
        "项目目标",
        "需求目标",
        "优化目标",
        "目标是",
        "目的在于",
        "旨在",
        "为了提升",
        "为了优化",
        "为提升",
        "为优化",
        "业务价值",
        "项目价值",
        "方案价值",
        "预期收益",
        "收益分析",
        "建设意义",
        "实施意义",
        "价值说明",
        "有助于",
        "提升体验",
        "优化效率",
        "提升效率",
        "增强能力",
        "支撑业务",
        "支撑发展",
    ]

    REQUIREMENT_HINTS = [
        "支持",
        "新增",
        "修改",
        "删除",
        "展示",
        "显示",
        "跳转",
        "进入",
        "提交",
        "保存",
        "查询",
        "校验",
        "限制",
        "必填",
        "非必填",
        "默认值",
        "字段",
        "参数",
        "接口",
        "返回",
        "页面",
        "按钮",
        "弹窗",
        "列表",
        "详情",
        "权限",
        "角色",
        "状态",
        "流程",
        "节点",
        "异常",
        "失败",
        "成功",
        "提示",
        "规则",
        "条件",
        "触发",
        "禁止",
        "允许",
        "不可",
        "不能",
        "必须",
        "应当",
        "如果",
        "当",
        "则",
    ]

    MAX_ISSUES = 12

    # =====================================================
    # Prompt
    # =====================================================

    def _build_prompt(self, requirement_text: str) -> str:
        return f"""
请从专业需求评审角度分析以下需求文本，并识别其中存在的真实业务问题。

只分析：

- 需求完整性
- 描述清晰度
- 一致性
- 业务规则
- 流程逻辑
- 异常流程
- 边界场景
- 状态流转
- 数据定义
- 接口契约
- 依赖约束
- 权限安全
- 合规性
- 可测试性
- 可追踪性
- 可维护性
- 可扩展性
- 性能
- 可观测性
- 需求质量

严格要求：

- 只输出 JSON 数组
- 不输出解释
- 不输出 markdown
- 不输出代码块
- 不输出系统或模型问题
- 如果没有明确问题输出 []
- 只分析“可执行、可验证、可测试”的真实需求问题
- 不要把“项目背景、建设目标、业务价值、收益说明、方案意义、设计动机、现状介绍、为什么要做”当成问题输出
- 如果一段内容只是在说“为什么做”，而没有说“系统要做什么 / 怎么做 / 什么条件下做 / 出错怎么处理”，则忽略
- 重点关注功能行为、字段规则、状态流转、权限控制、异常处理、边界场景、依赖关系、性能与合规约束

输出 JSON 数组，例如：

[
  {{
    "level": "high",
    "category": "业务规则",
    "title": "缺少触发条件定义",
    "message": "需求描述了某能力，但未明确触发条件和适用范围。",
    "suggestion": "补充触发条件、适用对象和执行时机。",
    "severity": "major",
    "impact": "high",
    "solution": "在需求中明确规则条件和例外场景。"
  }}
]

需求文本：

\"\"\"
{requirement_text}
\"\"\"
"""

    # =====================================================
    # LLM
    # =====================================================

    def _call_llm(self, prompt: str) -> str:
        result = self.llm.call(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            force_json_object=False,
            timeout=120,
        )
        return (result or "").strip()

    # =====================================================
    # JSON解析
    # =====================================================

    def _strip_fence(self, text: str) -> str:
        return re.sub(r"```json|```", "", text or "", flags=re.IGNORECASE).strip()

    def _extract_json_array(self, text: str) -> str:
        clean = self._strip_fence(text)
        start = clean.find("[")
        end = clean.rfind("]")
        if start >= 0 and end > start:
            return clean[start:end + 1]
        return clean

    def _safe_load_json(self, text: str):
        if not text:
            return []

        try:
            return json.loads(text)
        except Exception:
            pass

        try:
            repaired = re.sub(r",\s*([}\]])", r"\1", text)
            return json.loads(repaired)
        except Exception:
            pass

        try:
            repaired = text.replace("\n", " ").replace("\t", " ")
            repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
            return json.loads(repaired)
        except Exception:
            pass

        try:
            repaired = text.replace("'", '"')
            repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
            return json.loads(repaired)
        except Exception:
            return []

    # =====================================================
    # 主入口
    # =====================================================

    def run(self, requirement_text: str) -> List[Dict[str, Any]]:
        prompt = self._build_prompt(requirement_text)

        try:
            raw = self._call_llm(prompt)
        except Exception:
            return []

        if not raw:
            return []

        json_text = self._extract_json_array(raw)
        parsed = self._safe_load_json(json_text)

        if not isinstance(parsed, list):
            return []

        issues = self._normalize_issues(parsed)
        issues = self._filter_blocked_issues(issues)
        issues = self._filter_background_issues(issues)
        issues = self._filter_fake_requirement_issues(issues)
        issues = self._sort_issues(issues)

        return issues[: self.MAX_ISSUES]

    # =====================================================
    # 标准化
    # =====================================================

    def _normalize_issues(self, issues):
        results = []

        for item in issues:
            if not isinstance(item, dict):
                continue

            level = self._normalize_level(item.get("level"))
            category = self._normalize_category(item.get("category"))

            title = str(item.get("title") or "").strip()
            message = str(item.get("message") or "").strip()
            suggestion = str(item.get("suggestion") or "").strip()
            solution = str(item.get("solution") or "").strip()

            severity = self._normalize_severity(item.get("severity"), fallback_level=level)
            impact = self._normalize_impact(item.get("impact"), fallback_level=level)

            if not message:
                continue

            if not title:
                title = self._build_short_title(message)

            if not suggestion:
                suggestion = self._default_suggestion(category)

            if not solution:
                solution = self._default_solution(category, suggestion)

            results.append(
                {
                    "level": self._normalize_level_from_severity(level, severity),
                    "category": category,
                    "title": title,
                    "message": message,
                    "suggestion": suggestion,
                    "severity": severity,
                    "impact": impact,
                    "solution": solution,
                    "source_agent": self.name,
                    "dimension": "general",
                }
            )

        return self._deduplicate(results)

    # =====================================================
    # 字段规范
    # =====================================================

    def _normalize_level(self, value):
        v = str(value or "medium").lower().strip()
        if v not in self.ALLOWED_LEVELS:
            v = "medium"
        return v

    def _normalize_category(self, value):
        v = str(value or "需求质量").strip()
        v = self.CATEGORY_ALIAS.get(v, v)
        if v not in self.ALLOWED_CATEGORIES:
            return "需求质量"
        return v

    def _normalize_severity(self, value, fallback_level: str = "medium"):
        v = str(value or "").lower().strip()

        alias = {
            "high": "critical",
            "medium": "major",
            "low": "minor",
        }
        v = alias.get(v, v)

        if v in self.ALLOWED_SEVERITIES:
            return v

        if fallback_level == "high":
            return "critical"
        if fallback_level == "low":
            return "minor"
        return "major"

    def _normalize_impact(self, value, fallback_level: str = "medium"):
        v = str(value or "").lower().strip()
        if v in self.ALLOWED_IMPACTS:
            return v

        if fallback_level == "high":
            return "high"
        if fallback_level == "low":
            return "low"
        return "medium"

    def _normalize_level_from_severity(self, level: str, severity: str) -> str:
        severity = str(severity or "").lower().strip()
        if severity in {"blocker", "critical"}:
            return "high"
        if severity in {"minor", "suggestion"}:
            return "low"
        return self._normalize_level(level)

    # =====================================================
    # 过滤：系统噪音
    # =====================================================

    def _filter_blocked_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        for item in issues:
            category = str(item.get("category") or "").strip()
            title = str(item.get("title") or "").strip()
            message = str(item.get("message") or "").strip()
            suggestion = str(item.get("suggestion") or "").strip()
            solution = str(item.get("solution") or "").strip()

            text = " ".join([category, title, message, suggestion, solution]).lower()

            if any(k.lower() in text for k in self.BLOCKED_KEYWORDS):
                continue

            results.append(item)

        return results

    # =====================================================
    # 过滤：背景类问题
    # =====================================================

    def _filter_background_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        for item in issues:
            title = str(item.get("title") or "").strip()
            message = str(item.get("message") or "").strip()
            suggestion = str(item.get("suggestion") or "").strip()
            solution = str(item.get("solution") or "").strip()

            if self._is_background_issue_text(
                title=title,
                message=message,
                suggestion=suggestion,
                solution=solution,
            ):
                continue

            results.append(item)

        return results

    def _is_background_issue_text(
        self,
        title: str,
        message: str,
        suggestion: str,
        solution: str,
    ) -> bool:
        text = " ".join([title, message, suggestion, solution]).strip().lower()
        if not text:
            return False

        bg_score = self._keyword_score(text, self.BACKGROUND_KEYWORDS)
        req_score = self._keyword_score(text, self.REQUIREMENT_HINTS)

        if self._match_background_pattern(text) and req_score == 0:
            return True

        if bg_score >= 2 and req_score == 0:
            return True

        if bg_score >= 3 and bg_score >= req_score * 2:
            return True

        return False

    def _match_background_pattern(self, text: str) -> bool:
        patterns = [
            r"为了[^\n。；]{2,30}(提升|优化|增强|支撑)",
            r"(目标|目的)(是|在于)",
            r"旨在[^\n。；]{2,50}",
            r"有助于[^\n。；]{2,50}",
            r"提升[^\n。；]{1,20}(体验|效率|满意度|能力)",
            r"优化[^\n。；]{1,20}(流程|体验|效率|能力)",
            r"支撑[^\n。；]{1,30}(业务|发展|增长|扩展)",
        ]
        return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)

    def _keyword_score(self, text: str, keywords: List[str]) -> int:
        score = 0
        for kw in keywords:
            kw_norm = str(kw or "").strip().lower()
            if not kw_norm:
                continue
            if kw_norm in text:
                score += len(kw_norm) * min(text.count(kw_norm), 3)
        return score

    # =====================================================
    # 过滤：伪需求问题
    # =====================================================

    def _filter_fake_requirement_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        blocked_patterns = [
            r"缺少背景",
            r"缺少目标",
            r"缺少价值",
            r"收益不明确",
            r"建设意义不清",
            r"业务价值不清",
            r"建议增加背景",
            r"建议补充目标",
        ]

        for item in issues:
            title = str(item.get("title") or "").strip()
            message = str(item.get("message") or "").strip()
            text = f"{title} {message}".lower()

            if any(re.search(p, text, flags=re.IGNORECASE) for p in blocked_patterns):
                continue

            if len(message) < 6:
                continue

            results.append(item)

        return results

    # =====================================================
    # 默认建议
    # =====================================================

    def _default_suggestion(self, category: str) -> str:
        category = str(category or "").strip()

        mapping = {
            "完整性": "补充缺失的功能说明、规则定义与验收条件。",
            "清晰性": "补充明确判定标准、适用范围和示例说明。",
            "一致性": "统一术语、规则和口径，避免冲突描述。",
            "业务规则": "补充规则条件、优先级、触发时机和例外处理。",
            "流程逻辑": "补充主流程、分支流程和前后置条件。",
            "异常处理": "补充失败场景、错误处理、回滚与兜底策略。",
            "边界场景": "补充边界值、极端场景和特殊输入处理。",
            "状态流转": "补充状态定义、状态迁移条件和终态规则。",
            "数据定义": "补充字段定义、取值范围、格式约束和数据口径。",
            "接口契约": "补充接口定义、字段结构、返回码和失败处理。",
            "依赖约束": "补充上下游依赖、调用约束、时序和失败处理。",
            "权限安全": "补充角色权限、访问控制和敏感数据保护要求。",
            "合规性": "补充合规约束、用户告知、风险提示和留痕要求。",
            "可测试性": "补充验收标准、量化口径、测试数据和校验方式。",
            "可追踪性": "补充需求、规则、测试点之间的映射关系。",
            "可维护性": "补充模块边界、统一定义和后续维护约束。",
            "可扩展性": "补充扩展场景、兼容策略和未来演进约束。",
            "性能": "补充性能指标、容量边界和响应时效要求。",
            "可观测性": "补充日志、监控、告警与审计要求。",
        }

        return mapping.get(category, "补充明确规则、约束和验收标准。")

    def _default_solution(self, category: str, suggestion: str) -> str:
        suggestion = str(suggestion or "").strip()
        if suggestion:
            return suggestion
        return self._default_suggestion(category)

    def _build_short_title(self, message: str, max_len: int = 18) -> str:
        text = re.sub(r"\s+", " ", str(message or "")).strip()
        if not text:
            return "未命名问题"
        return text if len(text) <= max_len else text[:max_len] + "..."

    # =====================================================
    # 排序
    # =====================================================

    def _sort_issues(self, issues):
        level_order = {"high": 0, "medium": 1, "low": 2}

        severity_order = {
            "blocker": 0,
            "critical": 1,
            "major": 2,
            "minor": 3,
            "suggestion": 4,
        }

        return sorted(
            issues,
            key=lambda x: (
                level_order.get(str(x.get("level") or "medium"), 1),
                severity_order.get(str(x.get("severity") or "major"), 2),
                str(x.get("category") or ""),
                str(x.get("title") or ""),
            ),
        )

    # =====================================================
    # 去重
    # =====================================================

    def _deduplicate(self, issues):
        seen = set()
        results = []

        for item in issues:
            key = (
                str(item.get("category") or "").strip(),
                str(item.get("title") or "").strip(),
                self._normalize_text_for_dedup(str(item.get("message") or "").strip()),
            )

            if key in seen:
                continue

            seen.add(key)
            results.append(item)

        return results

    def _normalize_text_for_dedup(self, text: str) -> str:
        text = str(text or "").strip().lower()
        text = re.sub(r"\s+", "", text)
        text = text.replace("，", ",").replace("。", ".").replace("：", ":")
        return text