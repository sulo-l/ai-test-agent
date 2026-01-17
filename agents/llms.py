import requests
from settings import get_settings


class GeminiLLM:
    """
    调用 Google Gemini API（兼容旧代码 + SSE/Docker 安全）
    """

    def __init__(self):
        """
        ⚠️ 兼容旧逻辑：
        - 保留 __init__
        - 但不在这里读配置、不连网
        """
        self.endpoint = "https://gemini.googleapis.com/v1/complete"

    def complete(self, prompt: str) -> str:
        """
        完整调用流程：
        - 运行时读取配置
        - 必须 timeout
        - 失败必须抛异常（不能吞）
        """
        cfg = get_settings()

        api_key = cfg.get("api_key")
        if not api_key:
            raise RuntimeError("Gemini API key is not configured")

        payload = {
            "prompt": prompt,
            # ⚠️ 保留你原来语义，优先使用配置
            "model": cfg.get("model", "gemini-1"),
            "temperature": 0.7,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(
                self.endpoint,
                json=payload,
                headers=headers,
                timeout=300,  # ⚠️ 防止永远卡死
            )
            resp.raise_for_status()

            data = resp.json()
            return data.get("completion", "[空]")

        except requests.exceptions.Timeout as e:
            # ❌ 不能 return 字符串
            # ✅ 必须抛异常，SSE 才能结束
            raise RuntimeError("Gemini request timed out") from e

        except Exception as e:
            # ❌ 原来是 return "[Gemini 调用失败]"
            # ✅ 现在统一抛出
            raise RuntimeError(f"Gemini API call failed: {e}") from e


# =====================================================
# ✅ 向后兼容（如果你以前有单例用法）
# =====================================================
gemini_llm = GeminiLLM()
