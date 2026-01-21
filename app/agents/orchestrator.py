from typing import Dict, Any, List, Generator
import json
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from app.llm.client import llm
from app.agents.test_point import TestPointAgent
from app.agents.planner import Planner
from app.workflow.merge import merge_generation_context


LLM_TIMEOUT_SECONDS = 1800  # ⭐ 5 分钟


class Orchestrator:
    """
    Orchestrator（工程级 · 永不沉默版）

    保障原则：
    1️⃣ run_streaming 必须 yield
    2️⃣ LLM 出问题 ≠ SSE 卡死
    3️⃣ 最差情况也要返回兜底用例
    """

    def __init__(self):
        pass

    # =====================================================
    # 🚀 需求分析 + 测试点生成
    # =====================================================
    def run(
        self,
        raw_requirements: str,
        confirmed_items: List[str] | None = None,
        mode: str = "DELIVERY",
        focus_requirements: str | None = None,  # ⭐ 新增
    ) -> Dict[str, Any]:

        confirmed_items = confirmed_items or []

        # =================================================
        # 1️⃣ 需求分析（强化 focus）
        # =================================================
        analysis_prompt = f"""
你是一名资深软件测试专家。

请对以下需求进行【需求分析】：
- 总体质量评估
- 潜在风险
- 测试建议
⚠️ 不要生成测试用例

【需求内容】
{raw_requirements}

【用户补充测试重点（必须重点考虑）】
{focus_requirements or "无"}

请返回 JSON：
{{
  "summary": {{
    "quality": 0,
    "comment": ""
  }},
  "issues": [],
  "risks": [],
  "suggestions": []
}}
"""

        analysis = llm.call(analysis_prompt)

        if not isinstance(analysis, dict):
            raise RuntimeError("需求分析阶段：LLM 返回非 JSON")

        summary = analysis.get("summary") or {
            "quality": 70,
            "comment": "AI 已完成需求分析",
        }

        # =================================================
        # 2️⃣ Planner：生成计划（🔥关键）
        # =================================================
        plans = Planner.make_plan(
            requirement=raw_requirements,
            focus_requirements=focus_requirements,
        )

        # =================================================
        # 3️⃣ 根据计划生成测试点
        # =================================================
        test_point_agent = TestPointAgent()
        test_points: List[Dict[str, Any]] = []

        for plan in plans:
            plan_type = plan.get("type", "normal")

            tp_output = test_point_agent.run({
                "instruction": plan.get("instruction"),
                "type": plan_type,
                "module": plan.get("module"),
                "coverage_item": plan.get("coverage_item"),
            })

            if isinstance(tp_output, dict):
                test_points.extend(
                    tp_output.get("test_points")
                    or tp_output.get("points")
                    or []
                )
            elif isinstance(tp_output, list):
                test_points.extend(tp_output)

        if not test_points:
            raise RuntimeError("AI 未生成任何测试点（test_points 为空）")

        return {
            "summary": summary,
            "modules": [],
            "test_points": test_points,
            "requirements": [
                tp.get("name")
                for tp in test_points
                if isinstance(tp, dict) and tp.get("name")
            ],
            "issues": analysis.get("issues") or [],
            "risks": analysis.get("risks") or [],
            "suggestions": analysis.get("suggestions") or [],
        }

    # =====================================================
    # ✅ 测试用例生成（Streaming）
    # =====================================================
    def run_streaming(
        self,
        raw_requirements: str,
        test_points: List[Dict[str, Any]],
        confirmed_items: List[str] | None = None,
        requirement_hint: str | None = None,
        analysis_result: Dict[str, Any] | None = None,
        focus_requirements: str | None = None,  # ⭐ 新增
    ) -> Generator[Dict[str, Any], None, None]:

        confirmed_items = confirmed_items or []

        if not test_points:
            raise RuntimeError("无测试点，禁止生成测试用例")

        merged = merge_generation_context(
            raw_requirements=raw_requirements,
            user_requirement=requirement_hint,
            analysis_result=analysis_result,
        )
        merged_requirements = merged["merged_requirements"]

        idx = 0
        yielded_any = False

        try:
            for raw_case in self._stage_cases_stream(
                merged_requirements,
                test_points,
                confirmed_items,
                focus_requirements,  # ⭐ 传下去
            ):
                idx += 1
                yielded_any = True
                normalized = self._normalize_case(raw_case)
                normalized["_index"] = idx
                yield normalized

        except Exception as e:
            print("❌ run_streaming error:", e)
            traceback.print_exc()

        # =================================================
        # 🛟 兜底
        # =================================================
        if not yielded_any:
            yield {
                "_index": 1,
                "case_name": "【系统兜底】未能生成测试用例",
                "module": "SYSTEM",
                "precondition": "",
                "steps": [
                    "AI 在生成测试用例时发生异常或超时",
                    "请检查 LLM 服务状态 / prompt 输出",
                ],
                "expected": "系统应提示生成失败原因",
                "test_point_id": None,
                "test_point_name": None,
            }

    # =====================================================
    # ⭐ LLM 用例生成（真正可控超时 · 5 分钟）
    # =====================================================
    def _stage_cases_stream(
        self,
        raw_requirements: str,
        test_points: List[Dict[str, Any]],
        confirmed_items: List[str],
        focus_requirements: str | None = None,  # ⭐ 新增
    ) -> Generator[Dict[str, Any], None, None]:

        # 🔥 关键修改：强制 precondition
        prompt = f"""
你是一名资深软件测试专家。

请基于以下【测试点】生成测试用例：

【生成规则（必须严格遵守）】
1. 每个测试点 ≥ 3 条（正常 / 异常 / 边界）
2. 返回 JSON 数组
3. 每条用例【必须包含以下字段】：
   - case_name
   - module
   - precondition
   - steps（数组）
   - expected

【关于 precondition 的强制说明】
- precondition 表示【执行该用例前必须满足的状态】
- 只能描述“状态 / 前提”，不能写操作步骤
- 不允许为空
- 如果无特殊前置条件，请写：“无特殊前置条件”

【用户补充测试重点（必须重点覆盖）】
{focus_requirements or "无"}

【需求内容】
{raw_requirements}

【测试点】
{json.dumps(test_points, ensure_ascii=False, indent=2)}
"""

        raw = None

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(llm.call, prompt)
            try:
                raw = future.result(timeout=LLM_TIMEOUT_SECONDS)
            except TimeoutError:
                print(f"❌ llm.call timeout (>{LLM_TIMEOUT_SECONDS}s)")
                return
            except Exception as e:
                print("❌ llm.call exception:", e)
                return

        if isinstance(raw, str):
            raw = re.sub(r"^```json|```$", "", raw.strip(), flags=re.I)
            try:
                raw = json.loads(raw)
            except Exception as e:
                print("❌ JSON parse failed:", e)
                return

        cases = self._safe_parse_cases(raw)

        for case in cases:
            yield case

    # =====================================================
    # 用例规范化
    # =====================================================
    def _normalize_case(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        steps = raw.get("steps") or []
        if isinstance(steps, str):
            steps = [steps]

        return {
            "case_name": raw.get("case_name") or "未命名用例",
            "module": raw.get("module", ""),
            "precondition": raw.get("precondition", ""),
            "steps": steps,
            "expected": raw.get("expected", ""),
            "test_point_id": raw.get("test_point_id"),
            "test_point_name": raw.get("test_point_name"),
        }

    def _safe_parse_cases(self, raw: Any) -> List[Dict[str, Any]]:
        if isinstance(raw, list):
            return [c for c in raw if isinstance(c, dict)]

        if isinstance(raw, dict):
            cases = raw.get("cases")
            if isinstance(cases, list):
                return [c for c in cases if isinstance(c, dict)]

        return []
