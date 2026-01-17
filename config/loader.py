from pathlib import Path
import os
import yaml
import re

# 支持 ${ENV_VAR} 形式
_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env(value):
    """
    解析 ${ENV_VAR} 占位符
    """
    if not isinstance(value, str):
        return value

    value = value.strip()
    match = _ENV_PATTERN.fullmatch(value)
    if not match:
        return value

    env_key = match.group(1)
    env_val = os.getenv(env_key)

    if not env_val:
        raise ValueError(f"Environment variable '{env_key}' is not set")

    return env_val


def load_config() -> dict:
    """
    加载配置（支持 Docker / VPS）

    优先级：
    1️⃣ config.yaml（支持 ${ENV_VAR}）
    2️⃣ 环境变量真实注入
    """

    # loader.py 在 ai-test-agent/config/loader.py
    # parent = config/
    # parent.parent = 项目根目录
    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"Missing config.yaml at: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw_cfg = yaml.safe_load(f) or {}

    cfg = {}
    for k in ("base_url", "api_key", "model"):
        if k not in raw_cfg:
            raise ValueError(f"config.yaml missing required field: {k}")

        cfg[k] = _resolve_env(raw_cfg.get(k))

        if not cfg[k]:
            raise ValueError(
                f"Config value '{k}' is empty. "
                f"Check config.yaml or environment variables."
            )

    return cfg
