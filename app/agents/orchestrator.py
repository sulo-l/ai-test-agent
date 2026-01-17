from typing import List, Dict, Any, Callable, Generator
from app.llm.client import llm
from app.services.coverage import (
    check_mandatory_coverage,
    calc_overall_status
)
import json
import re


class Orchestrator:
    """
    Orchestrator 分两种模式：

    - DELIVERY（生产模式）
    - RESEARCH（禁用）
    """

    def __init__(self):
        # ✅ 用于 streaming 模式的最终用例缓冲
        self._final_cases: List[Dict[str, Any]] = []

    # =====================================================
    # 🚀 对外唯一入口（不动）
    # =====================================================
    def run(
        self,
        raw_requirements: str,
        confirmed_items: List[str] | None = None,
        mode: str = "DELIVERY",
    ) -> Dict[str, Any]:

        confirmed_items = confirmed_items or []

        if mode == "DELIVERY":
            return self._run_delivery(raw_requirements, confirmed_items)

        raise RuntimeError("RESEARCH 模式已禁用")

    # =====================================================
    # 🔥 DELIVERY（原逻辑，不动）
    # =====================================================
    def _run_delivery(
        self,
        raw_requirements: str,
        confirmed_items: List[str],
    ) -> Dict[str, Any]:

        prompt = f"""
你是一名资深软件测试专家。

需求：
{raw_requirements}

前端要求：
{confirmed_items}

输出 JSON：
{{ "test_points": [], "cases": [] }}
"""
        result = llm.call(prompt)

        if not isinstance(result, dict):
            raise RuntimeError("LLM 返回不是 JSON")

        result.setdefault("test_points", [])
        result.setdefault("cases", [])
        result.setdefault("status", "Completed")

        return result

    # =====================================================
    # 🆕 三阶段流式生成（结构不变，行为修正）
    # =====================================================
    def run_streaming(
        self,
        raw_requirements: str,
        confirmed_items: List[str] | None = None,
        on_stage: Callable[[str, Any], None] | None = None,
    ) -> Dict[str, Any]:

        confirmed_items = confirmed_items or []
        self._final_cases = []  # ✅ 每次 run 清空

        # ---------- 阶段 1：模块 ----------
        modules = self._stage_modules(raw_requirements)
        if on_stage:
            on_stage("modules", modules)

        # ---------- 阶段 2：测试点 ----------
        test_points = self._stage_test_points(
            raw_requirements,
            modules,
            confirmed_items,
        )
        if on_stage:
            on_stage("test_points", test_points)

        # ---------- 阶段 3：用例（关键修复点） ----------
        index = 0
        for case in self._stage_cases_stream(
            raw_requirements,
            test_points,
            confirmed_items,
        ):
            normalized = self._normalize_case(case)
            index += 1
            normalized["_index"] = index

            # ✅ 只缓存“完整用例”
            self._final_cases.append(normalized)

            # ✅ SSE 只推完整用例（不再推半成品）
            if on_stage:
                on_stage("case", normalized)

        # ✅ 一次性推 cases 完整列表（给前端 / Excel 用）
        if on_stage:
            on_stage("cases", self._final_cases)

        mandatory_coverage_result = check_mandatory_coverage(
            confirmed_items,
            self._flatten_test_points(test_points)
        )

        status = calc_overall_status(mandatory_coverage_result)

        return {
            "requirement_analysis": {"modules": modules},
            "test_points": test_points,
            "cases": self._final_cases,
            "coverage": mandatory_coverage_result,
            "status": status,
        }

    # =====================================================
    # 工具：拍平测试点（不动）
    # =====================================================
    def _flatten_test_points(self, test_points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        flat = []
        for group in test_points:
            for p in group.get("points", []):
                flat.append(p)
        return flat

    # =====================================================
    # 阶段 1：模块（不改结构，只兜底）
    # =====================================================
    def _stage_modules(self, raw_requirements: str) -> List[Dict[str, Any]]:
        prompt = """
你是一名需求分析专家。
请提取功能模块，返回 JSON 数组：
[{ "module": "模块名" }]
"""

        try:
            result = llm.call(prompt)
        except Exception:
            result = None

        if isinstance(result, list):
            valid = []
            for m in result:
                if isinstance(m, dict) and isinstance(m.get("module"), str):
                    valid.append({
                        "module": m["module"],
                        "source": m.get("source", "LLM")
                    })
            if valid:
                return valid

        return [{"module": "需求整体功能", "source": "FALLBACK"}]

    # =====================================================
    # 阶段 2：测试点（不改结构）
    # =====================================================
    def _stage_test_points(
        self,
        raw_requirements: str,
        modules: List[Dict[str, Any]],
        confirmed_items: List[str],
    ) -> List[Dict[str, Any]]:

        prompt = f"""
你是一名测试专家。

需求：
{raw_requirements}

模块：
{modules}

前端强制要求：
{confirmed_items}

返回 JSON：
[
  {{
    "module": "模块名",
    "points": [
      {{ "id": "TP-1", "name": "测试点名称", "source_requirement": null }}
    ]
  }}
]
"""

        try:
            result = llm.call(prompt)
        except Exception:
            result = None

        if isinstance(result, list):
            normalized = []
            for g in result:
                if not isinstance(g, dict):
                    continue
                if not isinstance(g.get("module"), str):
                    continue
                if not isinstance(g.get("points"), list):
                    continue

                points = []
                for p in g["points"]:
                    if isinstance(p, dict) and isinstance(p.get("name"), str):
                        points.append({
                            "id": p.get("id"),
                            "name": p["name"],
                            "source_requirement": p.get("source_requirement")
                        })

                if points:
                    normalized.append({
                        "module": g["module"],
                        "points": points
                    })

            if normalized:
                return normalized

        return [{
            "module": modules[0]["module"] if modules else "默认模块",
            "points": [{
                "id": "TP-1",
                "name": "基础功能验证（自动兜底）",
                "source_requirement": None
            }]
        }]

    # =====================================================
    # 阶段 3：用例生成（⚠️性能 + 完整性关键）
    # =====================================================
    def _stage_cases_stream(
        self,
        raw_requirements: str,
        test_points: List[Dict[str, Any]],
        confirmed_items: List[str],
    ) -> Generator[Dict[str, Any], None, None]:

        prompt = f"""
你是一名资深软件测试专家。

请【一次性】为以下测试点生成测试用例：
- 每个测试点 ≥ 3 条（正常 / 异常 / 边界）
- 返回 JSON 数组
- 每条用例必须包含：
  case_name, module, steps[], expected

需求：
{raw_requirements}

测试点：
{test_points}
"""

        try:
            raw = llm.call(prompt)
        except Exception:
            raw = None

        if isinstance(raw, str):
            raw = re.sub(r"^```json|```$", "", raw.strip(), flags=re.I)
            try:
                raw = json.loads(raw)
            except Exception:
                raw = None

        cases = self._safe_parse_cases(raw)

        # ❌ 只有在“完全失败”时才兜底
        if not cases:
            for group in test_points:
                for p in group.get("points", []):
                    for i in range(3):
                        cases.append({
                            "case_name": f"{p['name']} - 场景{i+1}",
                            "module": group.get("module", "默认模块"),
                            "steps": [f"执行 {p['name']} 场景{i+1}"],
                            "expected": "系统行为符合预期"
                        })

        for c in cases:
            yield c

    # =====================================================
    # ✅ 用例统一规范（Excel 依赖这个）
    # =====================================================
    def _normalize_case(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        steps = raw.get("steps") or []
        if isinstance(steps, str):
            steps = [steps]

        return {
            "case_name": raw.get("case_name") or raw.get("title") or "未命名用例",
            "module": raw.get("module", ""),
            "precondition": raw.get("precondition", ""),
            "steps": steps,
            "expected": raw.get("expected") or raw.get("expected_result") or "",
            "test_point_id": raw.get("test_point_id"),
            "test_point_name": raw.get("test_point_name"),
        }

    # =====================================================
    # JSON 安全解析（不动）
    # =====================================================
    def _safe_parse_cases(self, raw: Any) -> List[Dict[str, Any]]:
        if isinstance(raw, list):
            return [c for c in raw if isinstance(c, dict)]

        if isinstance(raw, dict):
            cases = raw.get("cases")
            if isinstance(cases, list):
                return [c for c in cases if isinstance(c, dict)]

        return []
