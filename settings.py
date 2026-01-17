import os
from dotenv import load_dotenv
from config.loader import load_config

# =====================================================
# ① 显式加载 .env（⚠️ 非常关键）
# =====================================================
# 会从当前目录向上查找 .env
load_dotenv()

# =====================================================
# ② 全局配置缓存（避免重复加载）
# =====================================================
_CONFIG = None


def get_settings() -> dict:
    """
    获取全局配置（惰性加载）
    优先级：
    1. 环境变量（.env / docker -e）
    2. config.yaml
    """
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config() or {}
    return _CONFIG


# =====================================================
# ③ 统一读取入口
# =====================================================
def _get_env_or_config(key: str, default=None):
    """
    环境变量 > config.yaml > default
    """
    return os.getenv(key, get_settings().get(key, default))


# =====================================================
# ④ 对外暴露的“稳定配置项”（只从这里 import）
# =====================================================

# 🔥 main.py 正在使用
TMP_DIR = _get_env_or_config("TMP_DIR", "/tmp")

# 并发控制（v1.1 会真正用到）
MAX_CONCURRENT_TASKS = int(_get_env_or_config("MAX_CONCURRENT_TASKS", 3))

# 前端跨域（后面 nginx / docker 用）
FRONTEND_ORIGIN = _get_env_or_config("FRONTEND_ORIGIN", "*")

# LLM 相关（给 llm/client.py 用）
OPENAI_API_KEY = _get_env_or_config("OPENAI_API_KEY")
OPENAI_BASE_URL = _get_env_or_config("OPENAI_BASE_URL")
OPENAI_MODEL = _get_env_or_config("OPENAI_MODEL")
