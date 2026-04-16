from typing import Dict, Any, Optional
from app.llm.client import llm
from app.services.ui_vision.extractor import extract_ui_schema


class RequirementAgent:

    def run(
        self,
        raw_requirements: str,
        pdf_path: Optional[str] = None,
    ) -> Dict[str, Any]:

        ui_schema = None
        if pdf_path:
            try:
                ui_schema = extract_ui_schema(pdf_path)
            except Exception:
                pass

        prompt = f"""
你是一名资深测试专家，请基于需求进行质量分析：
- 需求完整性
- 潜在风险
- 测试建议

⚠ 不要生成测试用例

需求原文：
{raw_requirements}

返回 JSON：
{{
  "summary": {{"quality": 0, "comment": ""}},
  "issues": [],
  "risks": [],
  "suggestions": []
}}
"""

        result = llm.call(prompt)
        if not isinstance(result, dict):
            result = {}

        result["ui_schema"] = ui_schema
        return result
