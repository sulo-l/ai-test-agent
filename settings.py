from config.loader import load_config

# 缓存配置，避免重复加载
_CONFIG = None


def get_settings() -> dict:
    """
    获取全局配置（惰性加载）
    """
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG
