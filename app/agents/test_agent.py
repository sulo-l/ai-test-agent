import json
import re
from typing import Any, Dict, List
from openai import OpenAI
from app.settings import config


def _extract_json_array(text: str) -> List[Dict[str, Any]]:
    """
    Extract a JSON array from LLM output robustly.
    """
    text = (text or "").strip()

    # remove code fences
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    # direct load
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except Exception:
        pass

    # find first [...] block
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except Exception:
            pass

    return []


class TestAgent:
    def __init__(self):
        self.client = OpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
        )
        self.model = config["model"]

    def generate_cases_cn(self, user_requirement: str, pdf_text: str) -> List[Dict[str, Any]]:
        """
        Team-level prompt: module/points + testcases (Chinese JSON).
        """
        prompt = f"""
        你是一个【资深测试负责人 / 测试专家】。

        ⚠️ 重要背景说明（必须严格遵守）：
        - PDF 文档来自 OCR，可能存在严重错字、丢行、乱码
        - 只有【确定识别内容】可以当作“事实”
        - 其余内容只能用于“合理推断”，禁止当作确定需求

        你的目标是：在【不胡编系统功能】的前提下，生成专业测试用例。

        -------------------------
        【一、确定识别内容（CONFIRMED，绝对可信）】
        以下内容是从文档中规则识别得到的，确定真实存在：
        {confirmed_items}

        -------------------------
        【二、用户输入需求（最高优先级，可信）】
        {user_requirement}

        -------------------------
        【三、PDF OCR 原始文本（仅作背景参考，不得直接当事实）】
        {pdf_text}

        -------------------------
        请按以下规则工作（一次完成）：

        1）基于【确定识别内容 + 用户需求】，识别模块（module）
           - 这些模块标记为 CONFIRMED

        2）在测试专业常识范围内，基于已确认模块，补充【合理推断的测试点】
           - 必须是“测试必然存在但文档未明确写出”的内容
           - 这些标记为 INFERRED

        3）生成测试用例时：
           - CONFIRMED 测试点：必须全部覆盖
           - INFERRED 测试点：允许补充，但必须可解释
           - 禁止凭空创造业务功能

        -------------------------
        输出要求（非常重要）：
        - 全部中文
        - 只输出 JSON（不要解释，不要 markdown，不要代码块）
        - 输出结构必须是一个 JSON 对象，字段如下：

        {{
          "modules": [
            {{
              "module": "模块名",
              "source": "CONFIRMED | INFERRED",
              "test_points": ["测试点1","测试点2"]
            }}
          ],
          "testcases": [
            {{
              "用例名称": "",
              "所属模块": "",
              "标签": ["功能测试","边界值"],
              "前置条件": "",
              "步骤描述": "1...\\n2...",
              "预期结果": "1...\\n2...",
              "来源": "CONFIRMED | INFERRED"
            }}
          ]
        }}
        """.strip()
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )

        content = resp.choices[0].message.content or ""
        # 解析顶层 JSON 对象
        try:
            data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE))
        except Exception:
            # 尝试提取 object
            m = re.search(r"\{[\s\S]*\}", content)
            if not m:
                return []
            data = json.loads(m.group(0))

        testcases = data.get("testcases", [])
        if not isinstance(testcases, list):
            return []

        # 清洗字段，保证 Excel 不乱
        cleaned = []
        seen = set()
        for i, tc in enumerate(testcases, start=1):
            if not isinstance(tc, dict):
                continue
            item = {
                "用例名称": (tc.get("用例名称") or "").strip() or f"用例_{i:04d}",
                "所属模块": (tc.get("所属模块") or "").strip() or "通用模块",
                "标签": tc.get("标签") if isinstance(tc.get("标签"), list) else [str(tc.get("标签", "")).strip()],
                "前置条件": (tc.get("前置条件") or "").strip(),
                "步骤描述": (tc.get("步骤描述") or "").strip(),
                "预期结果": (tc.get("预期结果") or "").strip(),
            }
            key = (item["所属模块"], item["步骤描述"], item["预期结果"])
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(item)

        return cleaned
