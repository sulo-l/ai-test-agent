#! /usr/bin/python3
# coding=utf-8
# app/analysis_app/agents/compliance_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Dict, Any, List
import json
import re
import logging

from app.analysis_app.agents.base_agent import BaseAgent


logger = logging.getLogger(__name__)


class ComplianceAgent(BaseAgent):
    """
    企业级 V4 合规分析 Agent

    作用：
        从需求文本中识别合规相关要求与缺口，包括：
        - privacy_requirements：隐私要求
        - audit_requirements：审计要求
        - regulatory_requirements：监管要求
        - data_retention_requirements：数据保留要求
        - access_control_requirements：访问控制要求
        - user_notification_requirements：用户告知要求
        - cross_border_requirements：跨境数据要求
        - compliance_gaps：合规缺口
        - recommendations：合规改进建议
    """

    name = "compliance"

    SYSTEM_PROMPT = (
        "你是一名资深合规评审专家，熟悉互联网产品数据合规、隐私保护、审计留痕、"
        "监管要求与数据生命周期管理。"
        "请基于需求文本识别合规相关要求与缺口。"
        "必须只输出 JSON，不允许解释，不允许 markdown。"
    )

    COMPLIANCE_TRIGGERS = [
        "隐私",
        "个人信息",
        "实名",
        "手机号",
        "身份证",
        "审计",
        "日志",
        "监管",
        "合规",
        "风控",
        "实名制",
        "数据删除",
        "数据导出",
        "数据保留",
        "访问控制",
        "权限",
        "敏感数据",
        "隐私政策",
        "告知",
        "用户同意",
        "授权书",
        "跨境",
        "境外",
        "出境",
    ]

    BLOCKED_KEYWORDS = [
        "json",
        "markdown",
        "llm",
        "prompt",
        "模型",
        "解析",
        "api key",
        "base url",
        "openai",
        "langchain",
        "client",
        "系统错误",
    ]

    # =====================================================
    # 空结果
    # =====================================================

    def _empty_result(self) -> Dict[str, List[str]]:
        return {
            "privacy_requirements": [],
            "audit_requirements": [],
            "regulatory_requirements": [],
            "data_retention_requirements": [],
            "access_control_requirements": [],
            "user_notification_requirements": [],
            "cross_border_requirements": [],
            "compliance_gaps": [],
            "recommendations": [],
        }

    # =====================================================
    # Prompt
    # =====================================================

    def _build_prompt(self, requirement_text: str) -> str:
        return f"""
请识别以下需求中的合规要求与合规缺口，并严格输出 JSON 对象。

输出格式必须如下：

{{
  "privacy_requirements": [],
  "audit_requirements": [],
  "regulatory_requirements": [],
  "data_retention_requirements": [],
  "access_control_requirements": [],
  "user_notification_requirements": [],
  "cross_border_requirements": [],
  "compliance_gaps": [],
  "recommendations": []
}}

要求：
1. 必须输出 JSON
2. 不允许输出解释
3. 不允许输出 markdown
4. 若不存在对应内容，返回空数组
5. 不要臆造具体法律条文编号
6. 仅基于需求文本中已有触发点输出相关要求或缺口
7. 重点关注：
   - 隐私保护 / 个人信息处理
   - 审计留痕 / 日志要求
   - 监管与实名制要求
   - 数据保留 / 删除 / 导出 / 生命周期管理
   - 访问控制 / 权限边界
   - 用户告知 / 用户同意 / 提示义务
   - 跨境数据 / 境外传输
8. 不要输出与模型、JSON、提示词、系统错误相关的内容

需求文本：

\"\"\"
{requirement_text}
\"\"\"
"""

    # =====================================================
    # LLM调用
    # =====================================================

    def _call_llm(self, prompt: str) -> str:
        result = self.llm.call(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            force_json_object=True,
            temperature=0.2,
            max_tokens=2200,
            timeout=120,
        )
        return (result or "").strip()

    # =====================================================
    # JSON提取
    # =====================================================

    def _strip_fence(self, text: str) -> str:
        return re.sub(r"```json|```", "", text or "", flags=re.IGNORECASE).strip()

    def _extract_json(self, text: str) -> str:
        text = self._strip_fence(text)

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

    # =====================================================
    # JSON解析
    # =====================================================

    def _safe_json(self, raw: str) -> Dict[str, Any]:
        if not raw:
            return {}

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
            return {}

    # =====================================================
    # normalize
    # =====================================================

    def _normalize(self, data: Dict[str, Any]) -> Dict[str, List[str]]:
        if not isinstance(data, dict):
            return self._empty_result()

        return {
            "privacy_requirements": self._normalize_list(data.get("privacy_requirements")),
            "audit_requirements": self._normalize_list(data.get("audit_requirements")),
            "regulatory_requirements": self._normalize_list(data.get("regulatory_requirements")),
            "data_retention_requirements": self._normalize_list(data.get("data_retention_requirements")),
            "access_control_requirements": self._normalize_list(data.get("access_control_requirements")),
            "user_notification_requirements": self._normalize_list(data.get("user_notification_requirements")),
            "cross_border_requirements": self._normalize_list(data.get("cross_border_requirements")),
            "compliance_gaps": self._normalize_list(data.get("compliance_gaps")),
            "recommendations": self._normalize_list(data.get("recommendations")),
        }

    def _normalize_list(self, value: Any) -> List[str]:
        items = value if isinstance(value, list) else []
        result: List[str] = []

        for item in items:
            s = str(item).strip()
            if not s:
                continue
            if self._is_blocked_text(s):
                continue
            if s not in result:
                result.append(s)

        return result

    def _is_blocked_text(self, text: str) -> bool:
        text = str(text or "").lower()
        return any(k in text for k in self.BLOCKED_KEYWORDS)

    # =====================================================
    # trigger检测
    # =====================================================

    def _has_trigger(self, requirement_text: str) -> bool:
        text = (requirement_text or "").lower()
        return any(word.lower() in text for word in self.COMPLIANCE_TRIGGERS)

    # =====================================================
    # 本地fallback
    # =====================================================

    def _local_fallback(self, requirement_text: str) -> Dict[str, List[str]]:
        result = self._empty_result()
        text = (requirement_text or "").lower()

        if "隐私" in text or "个人信息" in text or "敏感数据" in text:
            result["privacy_requirements"].append(
                "涉及个人信息或敏感数据时，应补充采集、存储、展示和传输保护要求。"
            )

        if "日志" in text or "审计" in text:
            result["audit_requirements"].append(
                "关键操作应保留审计日志，满足留痕与追踪要求。"
            )

        if "实名" in text or "实名制" in text or "监管" in text or "合规" in text:
            result["regulatory_requirements"].append(
                "应明确与实名制或监管要求相关的业务约束。"
            )

        if "删除" in text or "保留" in text or "导出" in text:
            result["data_retention_requirements"].append(
                "应明确数据保留周期、删除策略和导出边界。"
            )

        if "权限" in text or "访问控制" in text:
            result["access_control_requirements"].append(
                "敏感数据或关键功能应限制访问范围并明确权限边界。"
            )

        if "告知" in text or "同意" in text or "隐私政策" in text:
            result["user_notification_requirements"].append(
                "涉及用户数据处理时，应明确用户告知、提示或同意义务。"
            )

        if "跨境" in text or "境外" in text or "出境" in text:
            result["cross_border_requirements"].append(
                "涉及数据跨境或境外传输时，应明确相应约束与处理要求。"
            )

        # 缺口判断
        if "个人信息" in text or "隐私" in text:
            result["compliance_gaps"].append(
                "若未明确个人信息处理边界、展示保护或用户告知要求，可能存在隐私合规缺口。"
            )

        if "日志" in text or "审计" in text:
            result["compliance_gaps"].append(
                "若未明确关键操作审计日志范围和保留要求，可能影响合规追溯。"
            )

        if any(result[k] for k in result if k != "recommendations"):
            result["recommendations"].append(
                "建议补充隐私保护、审计留痕、数据生命周期、用户告知和权限控制相关合规约束。"
            )

        for k, v in result.items():
            result[k] = self._unique_keep_order(v)

        return result

    # =====================================================
    # 工具
    # =====================================================

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

    # =====================================================
    # 主入口
    # =====================================================

    def run(self, requirement_text: str) -> Dict[str, List[str]]:
        if not requirement_text or len(requirement_text.strip()) < 5:
            return self._empty_result()

        if not self._has_trigger(requirement_text):
            return self._empty_result()

        prompt = self._build_prompt(requirement_text)

        try:
            raw = self._call_llm(prompt)
        except Exception as e:
            logger.exception("ComplianceAgent llm call failed: %s", e)
            return self._local_fallback(requirement_text)

        json_text = self._extract_json(raw)
        data = self._safe_json(json_text)
        result = self._normalize(data)

        if not any(result.values()):
            return self._local_fallback(requirement_text)

        return result


# 单例
compliance_agent = ComplianceAgent()