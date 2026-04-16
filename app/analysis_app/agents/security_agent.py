#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/3/8 16:40
# @Author: sulo
# app/analysis_app/agents/security_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Dict, Any, List
import json
import re
import logging

from app.analysis_app.agents.base_agent import BaseAgent


logger = logging.getLogger(__name__)


class SecurityAgent(BaseAgent):
    """
    企业级 V4 安全分析 Agent

    作用：
        从需求文本中识别安全相关风险与约束，包括：
        - authentication_requirements：认证要求
        - authorization_requirements：授权/权限要求
        - sensitive_data_requirements：敏感数据保护要求
        - input_validation_requirements：输入校验要求
        - operation_security_requirements：关键操作安全要求
        - audit_security_requirements：审计安全要求
        - abuse_prevention_requirements：滥用防控要求
        - security_gaps：当前需求中的安全缺口
        - recommendations：安全改进建议
    """

    name = "security"

    SYSTEM_PROMPT = (
        "你是一名资深安全评审专家，熟悉互联网产品安全设计、认证鉴权、敏感数据保护、输入校验、"
        "越权防护、关键操作安全、审计要求与滥用防控。"
        "请基于需求文本识别安全相关要求与缺口。"
        "必须只输出 JSON，不允许输出解释，不允许输出 markdown。"
    )

    SECURITY_TRIGGERS = [
        "登录", "鉴权", "认证", "权限", "角色", "越权",
        "用户", "账号", "token", "session",
        "敏感", "隐私", "脱敏", "加密", "手机号", "身份证",
        "输入", "参数", "校验", "非法字符",
        "上传", "文件", "回调", "接口", "api", "第三方",
        "日志", "审计", "风控", "限制", "频率", "幂等",
        "支付", "提现", "转账", "申诉", "审核", "审批",
    ]

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

    # =====================================================
    # 空结果
    # =====================================================

    def _empty_result(self) -> Dict[str, List[str]]:
        return {
            "authentication_requirements": [],
            "authorization_requirements": [],
            "sensitive_data_requirements": [],
            "input_validation_requirements": [],
            "operation_security_requirements": [],
            "audit_security_requirements": [],
            "abuse_prevention_requirements": [],
            "security_gaps": [],
            "recommendations": [],
        }

    # =====================================================
    # Prompt
    # =====================================================

    def _build_prompt(self, requirement_text: str) -> str:
        return f"""
请从以下需求文本中识别安全相关内容，并严格输出 JSON 对象。

输出格式必须如下：

{{
  "authentication_requirements": [
    "用户执行关键操作前需要完成身份校验"
  ],
  "authorization_requirements": [
    "仅具备对应权限的角色可访问该功能"
  ],
  "sensitive_data_requirements": [
    "敏感信息展示时需要脱敏处理"
  ],
  "input_validation_requirements": [
    "用户输入内容需要做格式校验与非法字符过滤"
  ],
  "operation_security_requirements": [
    "关键操作需要二次确认并记录操作日志"
  ],
  "audit_security_requirements": [
    "关键安全操作需要保留审计日志"
  ],
  "abuse_prevention_requirements": [
    "需要限制重复提交和高频调用"
  ],
  "security_gaps": [
    "未说明用户权限校验规则，存在越权访问风险"
  ],
  "recommendations": [
    "补充认证、鉴权、越权防护、敏感数据保护和关键操作审计要求"
  ]
}}

要求：
1. 必须输出 JSON 对象
2. 不允许输出 markdown，不允许输出解释
3. 所有内容使用中文
4. 若某类信息不存在，返回空数组
5. 不要臆造具体漏洞编号
6. 只有当需求文本中存在明确触发点时，才输出对应安全要求或安全缺口
7. 重点关注：
   - 认证 / 登录 / 身份校验
   - 授权 / 权限 / 越权防护
   - 敏感数据 / 脱敏 / 加密
   - 输入校验 / 参数校验 / 非法输入
   - 关键操作保护 / 二次确认 / 审计日志
   - 文件上传 / 回调接口 / 第三方接口安全
   - 滥用防控 / 频率限制 / 幂等 / 重复提交
8. 不要输出任何与模型、解析、JSON、提示词、系统错误相关的内容

需求文本：

\"\"\"
{requirement_text}
\"\"\"
"""

    # =====================================================
    # LLM 调用
    # =====================================================

    def _call_llm(self, prompt: str) -> str:
        result = self.llm.call(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            force_json_object=True,
            temperature=0.2,
            max_tokens=2400,
            timeout=120,
        )
        return (result or "").strip()

    # =====================================================
    # JSON 提取 / 修复
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
    # 归一化
    # =====================================================

    def _normalize(self, data: Dict[str, Any]) -> Dict[str, List[str]]:
        if not isinstance(data, dict):
            return self._empty_result()

        return {
            "authentication_requirements": self._normalize_str_list(data.get("authentication_requirements")),
            "authorization_requirements": self._normalize_str_list(data.get("authorization_requirements")),
            "sensitive_data_requirements": self._normalize_str_list(data.get("sensitive_data_requirements")),
            "input_validation_requirements": self._normalize_str_list(data.get("input_validation_requirements")),
            "operation_security_requirements": self._normalize_str_list(data.get("operation_security_requirements")),
            "audit_security_requirements": self._normalize_str_list(data.get("audit_security_requirements")),
            "abuse_prevention_requirements": self._normalize_str_list(data.get("abuse_prevention_requirements")),
            "security_gaps": self._normalize_str_list(data.get("security_gaps")),
            "recommendations": self._normalize_str_list(data.get("recommendations")),
        }

    def _normalize_str_list(self, value: Any) -> List[str]:
        items = self._ensure_list(value)
        result: List[str] = []

        for item in items:
            s = str(item or "").strip()
            if not s:
                continue
            if self._is_blocked_text(s):
                continue
            if s in result:
                continue
            result.append(s)

        return result

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

    def _is_blocked_text(self, *parts: Any) -> bool:
        text = " ".join(str(p or "") for p in parts).lower()
        return any(k.lower() in text for k in self.BLOCKED_KEYWORDS)

    # =====================================================
    # 触发检测 / 本地兜底
    # =====================================================

    def _has_security_trigger(self, requirement_text: str) -> bool:
        text = (requirement_text or "").lower()
        return any(word.lower() in text for word in self.SECURITY_TRIGGERS)

    def _local_fallback(self, requirement_text: str) -> Dict[str, List[str]]:
        text = (requirement_text or "").lower()
        result = self._empty_result()

        def contains_any(words: List[str]) -> bool:
            return any(w.lower() in text for w in words)

        if contains_any(["登录", "认证", "鉴权", "token", "session", "账号"]):
            result["authentication_requirements"].append("关键功能应明确登录状态或身份校验要求。")

        if contains_any(["权限", "角色", "越权", "审批", "审核", "管理"]):
            result["authorization_requirements"].append("应明确不同角色的访问范围和操作权限边界。")
            result["security_gaps"].append("未充分说明角色权限边界时，存在越权访问风险。")

        if contains_any(["敏感", "隐私", "手机号", "身份证", "实名", "脱敏", "加密"]):
            result["sensitive_data_requirements"].append("敏感信息在展示、存储和传输时应有保护要求。")

        if contains_any(["输入", "参数", "校验", "上传", "文件", "回调", "接口", "api"]):
            result["input_validation_requirements"].append("输入参数、上传内容和接口入参应具备格式与合法性校验。")

        if contains_any(["提交", "审批", "审核", "支付", "提现", "申诉", "删除", "修改"]):
            result["operation_security_requirements"].append("关键操作应具备确认、幂等控制或防重复提交约束。")
            result["audit_security_requirements"].append("关键操作应记录必要的审计日志，便于追踪。")

        if contains_any(["频率", "限制", "重复提交", "幂等", "刷", "风控"]):
            result["abuse_prevention_requirements"].append("应补充频率限制、幂等约束或滥用防控要求。")

        if any(result[k] for k in result if k != "recommendations"):
            result["recommendations"].append("建议补充认证、授权、输入校验、审计日志和滥用防控要求。")

        for k, v in result.items():
            result[k] = self._unique_keep_order(v)

        return result

    # =====================================================
    # 主入口
    # =====================================================

    def run(self, requirement_text: str) -> Dict[str, List[str]]:
        if not requirement_text or len(requirement_text.strip()) < 5:
            return self._empty_result()

        # 没有明显安全触发点时，直接返回空结构，避免模型乱报
        if not self._has_security_trigger(requirement_text):
            return self._empty_result()

        prompt = self._build_prompt(requirement_text)

        try:
            raw = self._call_llm(prompt)
        except Exception as e:
            logger.exception("SecurityAgent llm call failed: %s", e)
            return self._local_fallback(requirement_text)

        json_text = self._extract_json(raw)
        data = self._safe_json(json_text)
        normalized = self._normalize(data)

        # 模型返回过空时走本地兜底
        if not any(normalized.values()):
            return self._local_fallback(requirement_text)

        return normalized


# 单例
security_agent = SecurityAgent()