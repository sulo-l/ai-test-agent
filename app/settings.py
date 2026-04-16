import os
from dotenv import load_dotenv
from app.config.loader import load_config

# =====================================================
# ① 显式加载 .env（⚠️ 非常关键）
# =====================================================
# 加载 .env 文件到环境变量中
load_dotenv()

# =====================================================
# ② 全局配置缓存（避免重复 IO / YAML 解析）
# =====================================================
_CONFIG = None


def get_settings() -> dict:
    """
    获取全局配置（惰性加载）
    优先级：
    1️⃣ 环境变量（.env / docker -e / export）
    2️⃣ config.yaml
    """
    global _CONFIG
    if _CONFIG is None:
        try:
            # 从 config.yaml 或其他配置源加载配置
            _CONFIG = load_config() or {}
        except Exception:
            # ⚠️ 允许 config.yaml 缺失（Docker / 本地 mock）
            _CONFIG = {}
    return _CONFIG


# =====================================================
# ③ 统一读取配置（唯一可信入口）
# =====================================================
def _get_env_or_config(key: str, default=None):
    """
    获取配置项的值，优先级：
    ENV > config.yaml > default
    """
    return os.getenv(key, get_settings().get(key, default))


# =====================================================
# ④ 运行模式识别（Docker / 本地）
# =====================================================
RUN_MODE = os.getenv("RUN_MODE", "local").lower()

# =====================================================
# ⑤ 稳定对外配置（只从 settings import）
# =====================================================

# ========= 临时目录（🔥 你之前炸点的根源） =========
if RUN_MODE == "docker":
    # Docker / VPS / 模拟 Docker
    TMP_DIR = _get_env_or_config("TMP_DIR", "/data/tmp")
else:
    # 本地开发永远安全
    TMP_DIR = _get_env_or_config("TMP_DIR", "/tmp/ai-test-agent")

# ⚠️ 目录创建必须在这里统一处理
try:
    os.makedirs(TMP_DIR, exist_ok=True)
except PermissionError:
    # 防止宿主机 / CI 直接炸
    raise RuntimeError(
        f"TMP_DIR '{TMP_DIR}' is not writable. "
        f"Please set TMP_DIR to a writable path."
    )


# ========= 并发控制 =========
MAX_CONCURRENT_TASKS = int(
    _get_env_or_config("MAX_CONCURRENT_TASKS", 3)
)

# ========= CORS / 前端 =========
FRONTEND_ORIGIN = _get_env_or_config("FRONTEND_ORIGIN", "*")

# ========= LLM / OpenAI =========
OPENAI_API_KEY = _get_env_or_config("OPENAI_API_KEY")
OPENAI_BASE_URL = _get_env_or_config("OPENAI_BASE_URL")
OPENAI_MODEL = _get_env_or_config("OPENAI_MODEL")

# ========= 测试用例生成配置 =========
# 最大生成用例数（测试环境可设为5，生产环境设为200）
MAX_TESTCASES = int(_get_env_or_config("MAX_TESTCASES", 200))
# 每个测试点最多生成几条用例（测试环境可设为1，生产环境设为3）
MAX_CASES_PER_POINT = int(_get_env_or_config("MAX_CASES_PER_POINT", 3))

# ========= 基础校验（早失败，别拖到 runtime） =========
if not OPENAI_API_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY is missing. "
        "Set it via environment variable or .env file."
    )

if not OPENAI_MODEL:
    raise RuntimeError(
        "OPENAI_MODEL is missing. "
        "Set it via environment variable or config.yaml."
    )
