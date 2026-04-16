# app/analysis_app/agents/structure_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Dict, Any, List
import json
import re
import logging

from app.analysis_app.agents.base_agent import BaseAgent


logger = logging.getLogger(__name__)


class StructureAgent(BaseAgent):
    """
    企业级 V4 需求结构解析 Agent

    解析结构：
    - actors
    - modules
    - business_goals
    - scenarios
    - workflows
    - data_objects
    - interfaces
    - constraints
    - non_functional_requirements
    - missing_sections
    """

    name = "structure"

    SYSTEM_PROMPT = (
        "你是一名资深软件架构师和需求分析专家。"
        "你的任务是从需求文本中提取结构化需求信息。"
        "必须只输出 JSON，不允许输出解释，不允许输出 markdown。"
    )

    # =====================================================
    # Prompt
    # =====================================================

    def _build_prompt(self, requirement_text: str) -> str:
        return f"""
请从以下需求文本中提取结构化需求信息。

输出 JSON 结构如下：

{{
  "actors": [],
  "modules": [],
  "business_goals": [],
  "scenarios": [],
  "workflows": [
    {{
      "name": "",
      "steps": [],
      "preconditions": [],
      "postconditions": [],
      "exceptions": []
    }}
  ],
  "data_objects": [
    {{
      "name": "",
      "fields": [],
      "constraints": []
    }}
  ],
  "interfaces": [
    {{
      "name": "",
      "method": "",
      "path": "",
      "request_fields": [],
      "response_fields": [],
      "error_codes": []
    }}
  ],
  "constraints": [],
  "non_functional_requirements": [],
  "missing_sections": []
}}

要求：

1. 只输出 JSON
2. 不允许 markdown
3. 不允许解释
4. 如果没有信息返回空数组或空对象
5. 不要臆造需求中不存在的信息
6. workflow 重点抽取主流程、前置条件、后置条件、异常分支
7. data_objects 重点抽取核心业务对象、字段和字段约束
8. interfaces 重点抽取接口名、方法、路径、请求字段、响应字段、错误码
9. missing_sections 用于标识明显缺失但通常应该存在的章节，例如：异常流程、权限规则、验收标准、性能要求

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
            max_tokens=3000,
            timeout=120,
        )
        return (result or "").strip()

    # =====================================================
    # JSON 提取
    # =====================================================

    def _strip_fence(self, text: str) -> str:
        return re.sub(r"```json|```", "", text or "", flags=re.IGNORECASE).strip()

    def _extract_json(self, text: str) -> str:
        if not text:
            return ""

        text = self._strip_fence(text)

        start = text.find("{")
        end = text.rfind("}")

        if start >= 0 and end > start:
            return text[start:end + 1]

        return text

    # =====================================================
    # JSON 解析
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
    # 结构归一化
    # =====================================================

    def _normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            data = {}

        return {
            "actors": self._normalize_str_list(data.get("actors")),
            "modules": self._normalize_str_list(data.get("modules")),
            "business_goals": self._normalize_str_list(data.get("business_goals")),
            "scenarios": self._normalize_str_list(data.get("scenarios")),
            "workflows": self._normalize_workflows(data.get("workflows")),
            "data_objects": self._normalize_data_objects(data.get("data_objects")),
            "interfaces": self._normalize_interfaces(data.get("interfaces")),
            "constraints": self._normalize_str_list(data.get("constraints")),
            "non_functional_requirements": self._normalize_str_list(data.get("non_functional_requirements")),
            "missing_sections": self._normalize_str_list(data.get("missing_sections")),
        }

    # =====================================================
    # 各字段规范化
    # =====================================================

    def _normalize_str_list(self, value: Any) -> List[str]:
        if isinstance(value, list):
            items = value
        elif value is None:
            items = []
        else:
            items = [value]

        results: List[str] = []

        for item in items:
            text = str(item).strip()
            if not text:
                continue
            if text not in results:
                results.append(text)

        return results

    def _normalize_workflows(self, value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []

        results: List[Dict[str, Any]] = []

        for item in value:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name") or "").strip()
            steps = self._normalize_str_list(item.get("steps"))
            preconditions = self._normalize_str_list(item.get("preconditions"))
            postconditions = self._normalize_str_list(item.get("postconditions"))
            exceptions = self._normalize_str_list(item.get("exceptions"))

            if not name and not steps and not preconditions and not postconditions and not exceptions:
                continue

            results.append(
                {
                    "name": name,
                    "steps": steps,
                    "preconditions": preconditions,
                    "postconditions": postconditions,
                    "exceptions": exceptions,
                }
            )

        return results

    def _normalize_data_objects(self, value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []

        results: List[Dict[str, Any]] = []

        for item in value:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name") or "").strip()
            fields = self._normalize_str_list(item.get("fields"))
            constraints = self._normalize_str_list(item.get("constraints"))

            if not name and not fields and not constraints:
                continue

            results.append(
                {
                    "name": name,
                    "fields": fields,
                    "constraints": constraints,
                }
            )

        return results

    def _normalize_interfaces(self, value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []

        results: List[Dict[str, Any]] = []

        for item in value:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name") or "").strip()
            method = str(item.get("method") or "").strip().upper()
            path = str(item.get("path") or "").strip()

            request_fields = self._normalize_str_list(item.get("request_fields"))
            response_fields = self._normalize_str_list(item.get("response_fields"))
            error_codes = self._normalize_str_list(item.get("error_codes"))

            if method and method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}:
                method = ""

            if not name and not path and not request_fields and not response_fields:
                continue

            results.append(
                {
                    "name": name,
                    "method": method,
                    "path": path,
                    "request_fields": request_fields,
                    "response_fields": response_fields,
                    "error_codes": error_codes,
                }
            )

        return results

    # =====================================================
    # 空结果
    # =====================================================

    def _empty_result(self) -> Dict[str, Any]:
        return {
            "actors": [],
            "modules": [],
            "business_goals": [],
            "scenarios": [],
            "workflows": [],
            "data_objects": [],
            "interfaces": [],
            "constraints": [],
            "non_functional_requirements": [],
            "missing_sections": [],
        }

    # =====================================================
    # 主入口
    # =====================================================

    def run(self, requirement_text: str) -> Dict[str, Any]:
        if not requirement_text or len(requirement_text.strip()) < 5:
            return self._empty_result()

        prompt = self._build_prompt(requirement_text)

        try:
            raw = self._call_llm(prompt)
        except Exception as e:
            logger.exception("StructureAgent llm call failed: %s", e)
            return self._empty_result()

        json_text = self._extract_json(raw)
        data = self._safe_json(json_text)

        if not isinstance(data, dict):
            return self._empty_result()

        return self._normalize(data)