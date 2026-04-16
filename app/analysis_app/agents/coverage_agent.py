# app/analysis_app/agents/coverage_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Dict, Any, List, Optional
import json
import re
import logging

from app.analysis_app.agents.base_agent import BaseAgent


logger = logging.getLogger(__name__)


class CoverageAgent(BaseAgent):
    """
    企业级 V4 覆盖率分析 Agent

    目标：
    - 评估需求在关键维度上的覆盖完整性
    - 输出 covered / missing / weak / gaps / recommendations / coverage_score
    - 与 RequirementCoverageResult 对齐
    """

    name = "coverage"

    SYSTEM_PROMPT = (
        "你是一名资深需求评审专家与测试架构师。"
        "你的任务是评估需求的覆盖完整性。"
        "必须只输出 JSON，不允许解释，不允许 markdown。"
    )

    DIMENSIONS = [
        "正常流程",
        "异常流程",
        "边界场景",
        "业务规则",
        "状态流转",
        "角色权限",
        "数据约束",
        "依赖接口",
        "安全要求",
        "性能要求",
        "可测试性",
        "合规要求",
    ]

    # =====================================================
    # 空结果
    # =====================================================

    def _empty_result(self) -> Dict[str, Any]:
        return {
            "covered_dimensions": [],
            "missing_dimensions": [],
            "weak_dimensions": [],
            "coverage_gaps": [],
            "recommendations": [],
            "coverage_score": 0,
        }

    # =====================================================
    # Prompt
    # =====================================================

    def _build_prompt(
        self,
        requirement_text: str,
        structure: Optional[Dict[str, Any]] = None,
        rules: Optional[Dict[str, Any]] = None,
        issues: Optional[List[Dict[str, Any]]] = None,
        testability: Optional[Dict[str, Any]] = None,
        consistency: Optional[Dict[str, Any]] = None,
    ) -> str:
        context = {
            "structure": structure or {},
            "rules": rules or {},
            "issues": self._simplify_issues(issues or []),
            "testability": testability or {},
            "consistency": consistency or {},
        }

        return f"""
请评估以下需求的覆盖情况。

覆盖维度列表（dimension 只能从这里选择）：
{json.dumps(self.DIMENSIONS, ensure_ascii=False)}

请严格输出 JSON 对象，格式如下：

{{
  "covered_dimensions": [
    {{
      "dimension": "正常流程",
      "reason": "需求中已描述主流程步骤和结果"
    }}
  ],
  "missing_dimensions": [
    {{
      "dimension": "异常流程",
      "reason": "未明确失败场景、错误处理或兜底策略"
    }}
  ],
  "weak_dimensions": [
    {{
      "dimension": "边界场景",
      "reason": "仅有少量边界说明，仍不足以支撑完整实现和测试"
    }}
  ],
  "coverage_gaps": [
    "当前需求缺少完整异常流程说明"
  ],
  "recommendations": [
    "补充异常处理、边界条件和验收标准"
  ],
  "coverage_score": 0
}}

要求：
1. 必须输出 JSON
2. 不允许输出解释
3. dimension 必须来自维度列表
4. coverage_score 必须为 0~100 整数
5. 不要臆造不存在的内容
6. 同一个 dimension 只能归入 covered / missing / weak 其中一种
7. 优先依据需求文本，其次结合上下文判断
8. recommendations 应聚焦如何补齐缺失维度和薄弱维度

需求文本：

\"\"\"
{requirement_text}
\"\"\"

上下文：

{json.dumps(context, ensure_ascii=False, indent=2)}
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
    # JSON 提取
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
    # 输入裁剪
    # =====================================================

    def _simplify_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []

        for item in issues or []:
            if not isinstance(item, dict):
                continue

            result.append(
                {
                    "id": str(item.get("id") or "").strip(),
                    "level": str(item.get("level") or "").strip(),
                    "category": str(item.get("category") or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "message": str(item.get("message") or "").strip(),
                }
            )

        return result[:30]

    # =====================================================
    # 归一化
    # =====================================================

    def _normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return self._empty_result()

        try:
            score = int(data.get("coverage_score", 0))
        except Exception:
            score = 0

        score = max(0, min(100, score))

        covered = self._normalize_dim(data.get("covered_dimensions"))
        missing = self._normalize_dim(data.get("missing_dimensions"))
        weak = self._normalize_dim(data.get("weak_dimensions"))

        covered, missing, weak = self._dedup_dimension_groups(covered, missing, weak)

        return {
            "covered_dimensions": covered,
            "missing_dimensions": missing,
            "weak_dimensions": weak,
            "coverage_gaps": self._normalize_list(data.get("coverage_gaps")),
            "recommendations": self._normalize_list(data.get("recommendations")),
            "coverage_score": score,
        }

    def _normalize_dim(self, value: Any) -> List[Dict[str, Any]]:
        items = value if isinstance(value, list) else []
        result: List[Dict[str, Any]] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            dimension = str(item.get("dimension") or "").strip()
            reason = str(item.get("reason") or "").strip()

            if dimension not in self.DIMENSIONS:
                continue
            if not reason:
                continue

            result.append(
                {
                    "dimension": dimension,
                    "reason": reason,
                }
            )

        return self._unique_dim_items(result)

    def _normalize_list(self, value: Any) -> List[str]:
        items = value if isinstance(value, list) else []
        result: List[str] = []

        for item in items:
            s = str(item).strip()
            if not s:
                continue
            if s not in result:
                result.append(s)

        return result

    def _unique_dim_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        result: List[Dict[str, Any]] = []

        for item in items:
            key = (
                str(item.get("dimension") or "").strip(),
                str(item.get("reason") or "").strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(item)

        return result

    def _dedup_dimension_groups(
        self,
        covered: List[Dict[str, Any]],
        missing: List[Dict[str, Any]],
        weak: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        同一个维度只能出现在一个分组里。
        优先级：missing > weak > covered
        """
        missing_dims = {str(x.get("dimension") or "").strip() for x in missing}
        weak_dims = {str(x.get("dimension") or "").strip() for x in weak}

        weak = [x for x in weak if str(x.get("dimension") or "").strip() not in missing_dims]
        covered = [
            x for x in covered
            if str(x.get("dimension") or "").strip() not in missing_dims
            and str(x.get("dimension") or "").strip() not in weak_dims
        ]

        return covered, missing, weak

    # =====================================================
    # 主入口
    # =====================================================

    def run(
        self,
        requirement_text: str,
        structure: Optional[Dict[str, Any]] = None,
        rules: Optional[Dict[str, Any]] = None,
        issues: Optional[List[Dict[str, Any]]] = None,
        testability: Optional[Dict[str, Any]] = None,
        consistency: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not requirement_text or len(requirement_text.strip()) < 5:
            return self._empty_result()

        prompt = self._build_prompt(
            requirement_text=requirement_text,
            structure=structure,
            rules=rules,
            issues=issues,
            testability=testability,
            consistency=consistency,
        )

        try:
            raw = self._call_llm(prompt)
            json_text = self._extract_json(raw)
            data = self._safe_json(json_text)
            result = self._normalize(data)

            if not (
                result["covered_dimensions"]
                or result["missing_dimensions"]
                or result["weak_dimensions"]
            ):
                return self._empty_result()

            return result

        except Exception as e:
            logger.exception("CoverageAgent llm call failed: %s", e)
            return self._empty_result()


coverage_agent = CoverageAgent()