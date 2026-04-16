#! /usr/bin/python3
# coding=utf-8
# app/analysis_app/agents/base_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

from app.llm.client import LLM
from app.analysis_app.models import (
    AgentAnalysisResult,
    AgentExecutionMeta,
    RequirementIssue,
    IssueStatistics,
)

logger = logging.getLogger(__name__)


class BaseAgent:
    """
    所有需求分析 Agent 的同步基类

    提供统一能力：
    - self.llm 初始化
    - LLM 调用
    - JSON 提取 / 修复
    - Pydantic 安全转换
    - issue 解析
    - 通用去重 / list 规范化
    - 标准 agent result 构造
    """

    name: str = "base_agent"

    def __init__(self) -> None:
        self.llm = LLM()

    # =====================================================
    # LLM 调用
    # =====================================================

    def call_llm(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        force_json_object: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 2200,
        timeout: int = 120,
    ) -> str:
        """
        统一同步调用 LLM
        """
        try:
            result = self.llm.call(
                prompt=prompt,
                system_prompt=system_prompt,
                force_json_object=force_json_object,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            if not isinstance(result, str):
                result = str(result or "")
            return result.strip()
        except Exception as e:
            logger.exception("LLM call failed in %s", self.name)
            raise RuntimeError(f"{self.name} LLM call failed: {e}") from e

    # =====================================================
    # JSON 处理
    # =====================================================

    def strip_fence(self, text: str) -> str:
        return re.sub(r"```json|```", "", text or "", flags=re.IGNORECASE).strip()

    def extract_json_object(self, text: str) -> str:
        """
        从文本中提取最外层 JSON object
        """
        text = self.strip_fence(text)

        start = text.find("{")
        if start < 0:
            return text

        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(text)):
            ch = text[i]

            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue

            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

        return text

    def extract_json_array(self, text: str) -> str:
        """
        从文本中提取最外层 JSON array
        """
        text = self.strip_fence(text)

        start = text.find("[")
        if start < 0:
            return text

        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(text)):
            ch = text[i]

            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue

            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

        return text

    def parse_json_object(self, text: str) -> Dict[str, Any]:
        """
        解析 JSON object，带容错
        """
        if not text:
            return {}

        raw = self.extract_json_object(text)

        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass

        try:
            fixed = raw.replace("\n", " ").replace("\t", " ")
            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
            parsed = json.loads(fixed)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass

        try:
            fixed = raw.replace("'", '"')
            fixed = fixed.replace("\n", " ").replace("\t", " ")
            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
            parsed = json.loads(fixed)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            logger.warning("%s parse_json_object failed", self.name)
            return {}

    def parse_json_array(self, text: str) -> List[Any]:
        """
        解析 JSON array，带容错
        """
        if not text:
            return []

        raw = self.extract_json_array(text)

        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            pass

        try:
            fixed = raw.replace("\n", " ").replace("\t", " ")
            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
            parsed = json.loads(fixed)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            pass

        try:
            fixed = raw.replace("'", '"')
            fixed = fixed.replace("\n", " ").replace("\t", " ")
            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
            parsed = json.loads(fixed)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            logger.warning("%s parse_json_array failed", self.name)
            return []

    # =====================================================
    # Pydantic 转换
    # =====================================================

    def parse_model(
        self,
        data: Dict[str, Any],
        model: Type[BaseModel],
    ) -> Optional[BaseModel]:
        try:
            model_validate = getattr(model, "model_validate", None)
            if callable(model_validate):
                return model_validate(data)

            return model(**data)
        except Exception:
            logger.exception("%s parse_model failed", self.name)
            return None

    def model_to_dict(self, value: Any) -> Dict[str, Any]:
        if value is None:
            return {}

        if isinstance(value, dict):
            return value

        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                data = model_dump()
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        to_dict = getattr(value, "dict", None)
        if callable(to_dict):
            try:
                data = to_dict()
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        return {}

    # =====================================================
    # Issues 解析
    # =====================================================

    def parse_issues(self, data: Dict[str, Any]) -> List[RequirementIssue]:
        issues: List[RequirementIssue] = []

        raw = data.get("issues") or []
        if not isinstance(raw, list):
            return issues

        for item in raw:
            if not isinstance(item, dict):
                continue

            try:
                issue = RequirementIssue.model_validate(item)
                issue.source_agent = self.name
                issues.append(issue)
            except Exception:
                continue

        return issues

    # =====================================================
    # 通用工具
    # =====================================================

    def ensure_list(self, value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if value is None:
            return []
        return [value]

    def normalize_str_list(self, value: Any, max_items: Optional[int] = None) -> List[str]:
        items = self.ensure_list(value)
        results: List[str] = []

        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            if text in results:
                continue
            results.append(text)

        if max_items is not None:
            return results[:max_items]
        return results

    def unique_keep_order(self, items: List[str]) -> List[str]:
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

    def unique_dict_items(
        self,
        items: List[Dict[str, Any]],
        keys: tuple[str, ...],
    ) -> List[Dict[str, Any]]:
        seen = set()
        results: List[Dict[str, Any]] = []

        for item in items or []:
            if not isinstance(item, dict):
                continue

            key = tuple(str(item.get(k) or "").strip() for k in keys)
            if key in seen:
                continue

            seen.add(key)
            results.append(item)

        return results

    # =====================================================
    # 执行信息
    # =====================================================

    def build_execution_meta(
        self,
        *,
        success: bool = True,
        duration_ms: Optional[int] = None,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ) -> AgentExecutionMeta:
        return AgentExecutionMeta(
            name=self.name,
            enabled=True,
            success=success,
            durationMs=duration_ms,
            message=message,
            error=error,
        )

    def build_issue_statistics(self, issues: List[Dict[str, Any]]) -> Optional[IssueStatistics]:
        if not isinstance(issues, list):
            return None

        high_count = sum(1 for x in issues if str(x.get("level") or "") == "high")
        medium_count = sum(1 for x in issues if str(x.get("level") or "") == "medium")
        low_count = sum(1 for x in issues if str(x.get("level") or "") == "low")

        blocker_count = sum(1 for x in issues if str(x.get("severity") or "") == "blocker")
        critical_count = sum(1 for x in issues if str(x.get("severity") or "") == "critical")
        major_count = sum(1 for x in issues if str(x.get("severity") or "") == "major")
        minor_count = sum(1 for x in issues if str(x.get("severity") or "") == "minor")
        suggestion_count = sum(1 for x in issues if str(x.get("severity") or "") == "suggestion")

        by_category: Dict[str, int] = {}
        by_dimension: Dict[str, int] = {}

        for item in issues:
            category = str(item.get("category") or "").strip()
            dimension = str(item.get("dimension") or "").strip()

            if category:
                by_category[category] = by_category.get(category, 0) + 1
            if dimension:
                by_dimension[dimension] = by_dimension.get(dimension, 0) + 1

        try:
            return IssueStatistics(
                totalIssues=len(issues),
                highCount=high_count,
                mediumCount=medium_count,
                lowCount=low_count,
                blockerCount=blocker_count,
                criticalCount=critical_count,
                majorCount=major_count,
                minorCount=minor_count,
                suggestionCount=suggestion_count,
                byCategory=by_category,
                byDimension=by_dimension,
            )
        except Exception:
            return None

    def build_agent_result(
        self,
        *,
        payload: Optional[Dict[str, Any]] = None,
        issues: Optional[List[Dict[str, Any]]] = None,
        summary: str = "",
        success: bool = True,
        error: Optional[str] = None,
        started_at: Optional[float] = None,
    ) -> AgentAnalysisResult:
        started_at = started_at or time.time()
        duration_ms = int((time.time() - started_at) * 1000)

        safe_payload = payload or {}
        safe_issues = issues or []

        statistics = self.build_issue_statistics(safe_issues)

        try:
            issue_models = [
                RequirementIssue.model_validate(x) if isinstance(x, dict) else x
                for x in safe_issues
                if isinstance(x, dict) or isinstance(x, RequirementIssue)
            ]
        except Exception:
            issue_models = []

        execution = self.build_execution_meta(
            success=success,
            duration_ms=duration_ms,
            message=summary or None,
            error=error,
        )

        return AgentAnalysisResult(
            agent=self.name,
            issues=issue_models,
            summary=summary or None,
            statistics=statistics,
            payload=safe_payload,
            execution=execution,
        )