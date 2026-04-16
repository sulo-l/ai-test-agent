#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/agents/risk_agent.py

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.llm.client import LLM

logger = logging.getLogger(__name__)


# =====================================================
# 工具函数
# =====================================================

def _safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    if not text or not isinstance(text, str):
        return None

    text = text.strip()
    if not text:
        return None

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        try:
            data = json.loads(fenced.group(1))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return None


def _ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _pick_first_str(*values: Any, default: str = "") -> str:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


def _pick_first_non_empty(*values: Any, default: Any = None) -> Any:
    for v in values:
        if v is None:
            continue
        if isinstance(v, str):
            if v.strip():
                return v.strip()
            continue
        if isinstance(v, (list, dict, tuple, set)):
            if len(v) > 0:
                return v
            continue
        return v
    return default


def _dedupe_str_list(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for x in items or []:
        s = str(x or "").strip()
        if not s:
            continue
        key = re.sub(r"\s+", " ", s).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(s)
    return result


def _normalize_risk_level(level: Any, default: str = "P2") -> str:
    s = str(level or "").strip().upper()

    if s in {"P0", "P1", "P2", "P3"}:
        return s

    if s in {"CRITICAL", "SEVERE", "BLOCKER"}:
        return "P0"
    if s in {"HIGH", "严重", "高"}:
        return "P1"
    if s in {"MEDIUM", "中"}:
        return "P2"
    if s in {"LOW", "低"}:
        return "P3"

    return default


def _normalize_overall_risk(level: Any) -> str:
    s = str(level or "").strip()
    if s in {"高", "中", "低"}:
        return s

    s2 = s.upper()
    if s2 in {"P0", "P1", "HIGH", "CRITICAL", "BLOCKER"}:
        return "高"
    if s2 in {"P2", "MEDIUM"}:
        return "中"
    if s2 in {"P3", "LOW"}:
        return "低"

    return "中"


def _risk_rank(level: Any) -> int:
    lv = _normalize_risk_level(level)
    mapping = {
        "P0": 0,
        "P1": 1,
        "P2": 2,
        "P3": 3,
    }
    return mapping.get(lv, 99)


def _normalize_text_for_key(text: Any) -> str:
    s = str(text or "").strip().lower()
    s = re.sub(r"[：:，,。.\-_/\\()\[\]{}]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _contains_any(text: str, keywords: List[str]) -> bool:
    lower_text = (text or "").lower()
    return any(str(k).lower() in lower_text for k in keywords if str(k).strip())


def _text_contains_any(text: str, keywords: List[str]) -> bool:
    return _contains_any(text, keywords)


def _bool_or_default(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return default


# =====================================================
# 业务域定义
# =====================================================

_ALLOWED_DOMAINS = {
    "登录注册", "用户中心", "现货", "合约", "充值", "提现", "划转",
    "P2P", "跟单", "撮合", "风控", "KYC", "资产", "通用",
}

_DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "登录注册": ["登录", "注册", "验证码", "找回密码", "二次验证", "设备校验", "auth", "login", "register"],
    "用户中心": ["用户中心", "个人中心", "资料", "profile", "avatar"],
    "现货": ["现货", "币币", "买入", "卖出", "下单", "撤单", "委托", "成交", "spot"],
    "合约": ["合约", "永续", "杠杆", "保证金", "爆仓", "强平", "资金费率", "开仓", "平仓", "perp", "future", "contract"],
    "充值": ["充值", "充币", "入金", "deposit", "address"],
    "提现": ["提现", "提币", "出金", "withdraw", "whitelist"],
    "划转": ["划转", "transfer", "账户互转"],
    "P2P": ["p2p", "法币", "广告单", "申诉"],
    "跟单": ["跟单", "copy trade", "copytrading", "带单", "跟随"],
    "撮合": ["撮合", "订单簿", "match", "成交回报"],
    "风控": ["风控", "限额", "黑名单", "白名单", "频控", "拦截", "risk"],
    "KYC": ["kyc", "实名认证", "身份认证"],
    "资产": ["资产", "余额", "冻结", "流水", "账变", "asset", "balance", "收益", "年化", "apr", "earn", "理财", "加息券", "派息"],
}

_DOMAIN_EXCLUSION_KEYWORDS: Dict[str, List[str]] = {
    "资产": [
        "合约开仓", "合约平仓", "爆仓", "强平", "撮合", "订单簿",
        "下单", "撤单", "仓位", "保证金", "成交回报", "委托", "现货交易",
        "永续", "杠杆", "开仓", "平仓",
    ],
    "充值": ["合约开仓", "合约平仓", "撮合", "跟单", "仓位", "保证金"],
    "提现": ["合约开仓", "合约平仓", "撮合", "跟单", "仓位", "保证金"],
    "划转": ["合约开仓", "合约平仓", "撮合", "仓位", "保证金"],
    "登录注册": ["合约开仓", "合约平仓", "撮合", "充值地址", "提现地址", "仓位", "保证金"],
}

_NOISE_PATTERNS = [
    "通过",
    "待观察",
    "通用兜底",
    "默认策略",
    "建议关注",
    "建议补充验证",
    "规则引擎识别出的",
    "规则引擎建议覆盖",
    "规则引擎建议需要",
    "规则引擎识别需要",
]

_ASSET_YIELD_KEYWORDS = [
    "apr", "年化", "收益", "理财", "earn", "收益概览", "历史收益", "加息券", "派息",
    "t+1", "折线图", "收益曲线", "固定apr", "基准apr", "新老用户", "周期切换",
]

_TRADING_ONLY_KEYWORDS = [
    "下单", "撤单", "撮合", "仓位", "保证金", "爆仓", "强平", "订单簿",
    "现货交易", "合约开仓", "合约平仓", "成交回报", "永续", "杠杆", "开仓", "平仓"
]

_RISK_CANONICAL_RULES: List[Tuple[List[str], str]] = [
    (["apr", "年化", "精度", "单位", "收益金额", "展示精度", "展示口径"], "收益展示口径"),
    (["收益概览", "详情", "明细", "一致性", "数据不一致"], "收益概览详情一致性"),
    (["t+1", "时序", "数据生成", "补跑", "生成延迟"], "收益数据生成时序"),
    (["多周期", "7天", "30天", "90天", "周期切换"], "多周期收益切换"),
    (["加息券", "收益纳入", "收益合并", "收益拆分"], "加息券收益口径"),
    (["空数据", "未参与", "无收益", "占位提示"], "空数据展示"),
    (["交互", "折线图", "悬停", "点击节点", "双指标"], "图表交互展示"),
    (["新老用户", "固定apr", "基准apr", "分层"], "用户分层APR规则"),
]


def _is_asset_yield_scene(requirement_text: str, business_domain: str) -> bool:
    text = (requirement_text or "").lower()
    if business_domain != "资产":
        return False
    return _contains_any(text, _ASSET_YIELD_KEYWORDS)


def _normalize_business_domain(value: Any, requirement_text: str = "") -> str:
    s = str(value or "").strip()
    if s in _ALLOWED_DOMAINS:
        return s

    text = f"{s} {(requirement_text or '').strip()}".lower()

    if _contains_any(text, _ASSET_YIELD_KEYWORDS):
        return "资产"

    rules = [
        ("合约", ["合约", "永续", "杠杆", "保证金", "爆仓", "强平", "资金费率", "开仓", "平仓", "contract", "perp", "future"]),
        ("现货", ["现货", "币币", "限价", "市价", "买入", "卖出", "撤单", "spot"]),
        ("提现", ["提现", "提币", "withdraw"]),
        ("充值", ["充值", "充币", "deposit"]),
        ("划转", ["划转", "transfer"]),
        ("P2P", ["p2p", "法币", "广告单"]),
        ("跟单", ["跟单", "copy trade", "copytrading", "跟随"]),
        ("撮合", ["撮合", "match", "订单簿", "成交回报"]),
        ("登录注册", ["登录", "注册", "验证码", "找回密码", "二次验证", "auth", "login", "register"]),
        ("风控", ["风控", "限额", "黑名单", "白名单", "频控", "拦截", "risk"]),
        ("KYC", ["kyc", "实名认证", "身份认证"]),
        ("资产", ["资产", "余额", "冻结", "流水", "账变", "asset", "balance", "收益", "年化", "apr", "earn", "理财"]),
    ]

    for domain, keywords in rules:
        if any(k.lower() in text for k in keywords):
            return domain

    return "通用"


def _is_noise_text(text: str) -> bool:
    s = _normalize_text_for_key(text)
    if not s:
        return True
    if len(s) <= 1:
        return True
    if any(p.lower() in s for p in [x.lower() for x in _NOISE_PATTERNS]):
        return True
    return False


def _is_text_relevant_to_domain(text: str, domain: str, requirement_text: str = "") -> bool:
    text = str(text or "").strip()
    if not text:
        return False

    if domain == "通用":
        return True

    lower_text = text.lower()

    if _is_asset_yield_scene(requirement_text, domain):
        if _contains_any(lower_text, _TRADING_ONLY_KEYWORDS):
            return False
        if _contains_any(lower_text, _ASSET_YIELD_KEYWORDS):
            return True

    domain_keywords = _DOMAIN_KEYWORDS.get(domain, [])
    exclusion_keywords = _DOMAIN_EXCLUSION_KEYWORDS.get(domain, [])

    if exclusion_keywords and _contains_any(lower_text, exclusion_keywords):
        return False

    if domain_keywords and _contains_any(lower_text, domain_keywords):
        return True

    matched_other_domain = False
    for other_domain, keywords in _DOMAIN_KEYWORDS.items():
        if other_domain in {domain, "通用"}:
            continue
        if keywords and _contains_any(lower_text, keywords):
            matched_other_domain = True
            break

    if matched_other_domain:
        return False

    generic_allow_keywords = [
        "主流程", "异常流", "边界", "数据一致性", "接口", "发布", "回滚", "灰度",
        "测试环境", "测试数据", "准入", "准出", "自动化", "回归", "冒烟", "质量门禁",
        "展示准确性", "汇总", "详情", "口径", "精度", "图表", "折线图", "周期", "空数据",
        "时序", "任务", "补跑", "缓存", "多端", "多语言"
    ]
    if _contains_any(lower_text, generic_allow_keywords):
        return True

    return False


def _guess_test_types(
    title: str,
    category: str,
    reason: str,
    requirement_text: str,
) -> List[str]:
    text = f"{title} {category} {reason} {requirement_text}".lower()
    result: List[str] = ["功能测试"]

    if any(k in text for k in ["接口", "状态", "规则", "校验", "风控", "权限", "资格", "幂等", "回调", "异步", "订单", "账变"]):
        result.append("接口测试")
    if any(k in text for k in ["异常", "失败", "错误", "拒绝", "拦截", "非法", "回退", "空数据", "未生成"]):
        result.append("异常流测试")
    if any(k in text for k in ["边界", "最大", "最小", "限制", "长度", "范围", "限额", "精度", "四舍五入", "周期"]):
        result.append("边界值测试")
    if any(k in text for k in ["权限", "角色", "资格"]):
        result.append("权限测试")
    if any(k in text for k in ["风控", "黑名单", "白名单", "频控", "限额"]):
        result.append("风控测试")
    if any(k in text for k in ["并发", "重复提交", "异步", "竞态"]):
        result.append("并发测试")
    if any(k in text for k in ["幂等", "重复请求", "重试"]):
        result.append("幂等测试")
    if any(k in text for k in ["余额", "账变", "流水", "金额", "资产", "订单", "成交", "冻结", "解冻", "收益", "apr", "年化", "汇总", "详情"]):
        result.append("数据一致性测试")
    if any(k in text for k in ["图表", "折线图", "悬停", "点击", "交互", "前端"]):
        result.append("交互测试")
    if any(k in text for k in ["多语言", "国际化", "文案", "兼容", "浏览器", "app", "h5", "web"]):
        result.append("兼容性测试")

    return _dedupe_str_list(result)


def _risk_semantic_key(title: str, reason: str, category: str = "") -> str:
    text = f"{title} {reason} {category}".lower()
    text = _normalize_text_for_key(text)

    for keywords, canonical in _RISK_CANONICAL_RULES:
        hit_count = sum(1 for kw in keywords if kw.lower() in text)
        if hit_count >= 2:
            return canonical

    if ("概览" in text and "详情" in text) or ("概览" in text and "明细" in text):
        return "收益概览详情一致性"
    if ("t 1" in text or "t+1" in text) and ("生成" in text or "时序" in text):
        return "收益数据生成时序"
    if ("apr" in text or "年化" in text) and ("精度" in text or "单位" in text or "口径" in text):
        return "收益展示口径"
    if ("固定 apr" in text or "基准 apr" in text or "新老用户" in text or "分层" in text):
        return "用户分层APR规则"

    return _normalize_text_for_key(title)


def _default_gate_level(level: str, affects_release_gate: bool) -> str:
    lv = _normalize_risk_level(level)
    if lv == "P0":
        return "blocker"
    if lv == "P1" and affects_release_gate:
        return "critical"
    if lv == "P1":
        return "high"
    if lv == "P2":
        return "medium"
    return "low"


def _extract_data_dependencies_from_text(text: str, title: str = "") -> List[str]:
    merged = f"{text} {title}".lower()
    deps: List[str] = []

    rules = [
        ("APR历史数据", ["apr", "年化", "历史年化", "历史 apr"]),
        ("收益汇总数据", ["收益", "收益金额", "收益汇总"]),
        ("加息券收益数据", ["加息券", "派息"]),
        ("T+1定时产物", ["t+1", "次日", "定时", "补跑"]),
        ("分层APR配置", ["固定apr", "基准apr", "新老用户", "分层"]),
        ("周期维度数据", ["7天", "30天", "90天", "周期"]),
        ("资产账户聚合结果", ["资产聚合", "账户聚合", "现货账户", "合约账户", "资金账户"]),
        ("空数据占位规则", ["空数据", "未参与", "无收益", "未生成"]),
    ]

    for name, keywords in rules:
        if any(k.lower() in merged for k in keywords):
            deps.append(name)

    return _dedupe_str_list(deps)


def _extract_api_dependencies_from_text(text: str, title: str = "") -> List[str]:
    merged = f"{text} {title}".lower()
    deps: List[str] = []

    if any(k in merged for k in ["查询", "接口", "展示", "概览", "详情", "折线图", "前端"]):
        deps.append("收益概览查询接口")
    if any(k in merged for k in ["详情", "明细"]):
        deps.append("收益详情查询接口")
    if any(k in merged for k in ["加息券"]):
        deps.append("加息券收益查询接口")
    if any(k in merged for k in ["账户聚合", "余额", "资产"]):
        deps.append("资产聚合查询接口")
    if any(k in merged for k in ["配置", "灰度", "开关"]):
        deps.append("配置中心接口")

    return _dedupe_str_list(deps)


def _extract_job_dependencies_from_text(text: str, title: str = "") -> List[str]:
    merged = f"{text} {title}".lower()
    deps: List[str] = []

    if any(k in merged for k in ["t+1", "定时", "补跑", "生成时序", "次日"]):
        deps.append("T+1收益生成任务")
    if any(k in merged for k in ["加息券", "派息"]):
        deps.append("加息券收益计算任务")
    if any(k in merged for k in ["聚合", "资产账户"]):
        deps.append("资产聚合刷新任务")

    return _dedupe_str_list(deps)


def _guess_verify_points(title: str, category: str, reason: str, requirement_text: str) -> List[str]:
    text = f"{title} {category} {reason} {requirement_text}".lower()
    points: List[str] = []

    if any(k in text for k in ["apr", "年化", "收益", "口径", "精度", "单位"]):
        points.extend([
            "校验展示APR是否取自需求定义口径，而非错误倒算",
            "校验收益金额、APR、单位、精度、四舍五入规则是否一致",
        ])

    if any(k in text for k in ["概览", "详情", "明细", "一致性"]):
        points.extend([
            "校验概览、详情、列表、Tooltip之间数据口径是否一致",
            "校验前端展示值与接口返回值是否一致",
        ])

    if any(k in text for k in ["t+1", "时序", "生成", "补跑", "次日"]):
        points.extend([
            "校验数据未生成、刚生成、补生成、重复生成场景的展示结果",
            "校验跨自然日后默认周期与历史数据刷新结果是否正确",
        ])

    if any(k in text for k in ["加息券", "派息"]):
        points.extend([
            "校验有券、无券、券过期、券生效中场景下收益汇总是否正确",
            "校验基础收益与加息收益合并/拆分口径是否符合需求",
        ])

    if any(k in text for k in ["7天", "30天", "90天", "周期", "切换"]):
        points.extend([
            "校验7/30/90天各周期默认值、切换后数据与趋势是否正确",
            "校验周期切换后无缓存复用、无旧值残留",
        ])

    if any(k in text for k in ["图表", "折线图", "悬停", "点击节点", "交互"]):
        points.extend([
            "校验折线图节点悬停/点击时APR与收益值是否同步展示",
            "校验交互态切换不会导致图表轴线或显示值错位",
        ])

    if any(k in text for k in ["新老用户", "固定apr", "基准apr", "分层"]):
        points.extend([
            "校验新用户/老用户、固定APR/基准APR场景分层命中是否正确",
            "校验分层规则切换时展示字段映射是否正确",
        ])

    if any(k in text for k in ["空数据", "未参与", "无收益", "未生成"]):
        points.extend([
            "校验未参与理财、无收益、当日未生成数据等空态是否正确提示",
            "校验不同空场景不会互相误判或误展示为正常收益",
        ])

    if any(k in text for k in ["灰度", "开关", "配置"]):
        points.extend([
            "校验配置开/关两态及灰度命中/未命中表现是否正确",
            "校验配置切换后页面与接口结果是否一致",
        ])

    if any(k in text for k in ["权限", "角色", "资格"]):
        points.extend([
            "校验不同角色、资格、登录态进入相同流程的结果是否符合预期",
        ])

    return _dedupe_str_list(points)[:6]


def _guess_monitor_points(title: str, category: str, reason: str, requirement_text: str) -> List[str]:
    text = f"{title} {category} {reason} {requirement_text}".lower()
    points: List[str] = []

    if any(k in text for k in ["apr", "年化", "收益", "口径"]):
        points.append("线上APR展示值与后端计算值偏差监控")
    if any(k in text for k in ["t+1", "生成", "补跑"]):
        points.append("T+1任务成功率与延迟监控")
    if any(k in text for k in ["概览", "详情", "一致性"]):
        points.append("概览/详情接口返回一致性抽样监控")
    if any(k in text for k in ["加息券"]):
        points.append("加息券收益纳入比例与异常分布监控")
    if any(k in text for k in ["图表", "折线图", "交互"]):
        points.append("前端图表渲染异常与接口错误率监控")
    if any(k in text for k in ["空数据", "未参与", "无收益"]):
        points.append("空数据占位触发比例异常监控")

    return _dedupe_str_list(points)


def _guess_change_scope(impact_modules: List[str], impact_flows: List[str], risk_items: List[Dict[str, Any]]) -> str:
    if len(impact_modules) >= 5 or len(impact_flows) >= 3:
        return "大"
    if len(risk_items) >= 6:
        return "大"
    if len(impact_modules) >= 3 or len(impact_flows) >= 2:
        return "中"
    return "小"


# =====================================================
# 上下文提取
# =====================================================

def _extract_issue_titles_from_analysis_result(
    analysis_result: Optional[Dict[str, Any]],
) -> List[str]:
    if not analysis_result:
        return []

    titles: List[str] = []
    issues = analysis_result.get("issues")
    if isinstance(issues, list):
        for item in issues:
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(
                item.get("title"),
                item.get("summary"),
                item.get("issue"),
                item.get("name"),
            )
            if title:
                titles.append(title)

    return _dedupe_str_list(titles)


def _extract_issue_items_from_analysis_result(
    analysis_result: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not analysis_result:
        return []

    issues = analysis_result.get("issues")
    if not isinstance(issues, list):
        return []

    result = []
    for item in issues:
        if isinstance(item, dict):
            result.append(item)
    return result


def _extract_titles_from_testcase_result(
    testcase_result: Optional[Dict[str, Any]],
) -> List[str]:
    if not testcase_result:
        return []

    titles: List[str] = []

    possible_lists = []
    for key in ("cases", "testcases", "items", "result", "data", "final_cases"):
        value = testcase_result.get(key)
        if isinstance(value, list):
            possible_lists.append(value)

    for arr in possible_lists:
        for item in arr:
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(
                item.get("title"),
                item.get("name"),
                item.get("case_title"),
                item.get("testcase_title"),
            )
            if title:
                titles.append(title)

    return _dedupe_str_list(titles)


def _extract_module_names_from_impact_data(
    impact_data: Optional[Dict[str, Any]],
) -> List[str]:
    if not impact_data:
        return []

    result = []
    for item in _ensure_list(impact_data.get("impact_modules")):
        if not isinstance(item, dict):
            continue
        name = _pick_first_str(item.get("name"), item.get("title"))
        if name:
            result.append(name)
    return _dedupe_str_list(result)


def _extract_flow_names_from_impact_data(
    impact_data: Optional[Dict[str, Any]],
) -> List[str]:
    if not impact_data:
        return []

    result = []
    for item in _ensure_list(impact_data.get("affected_flows")):
        if not isinstance(item, dict):
            continue
        name = _pick_first_str(item.get("name"), item.get("title"))
        if name:
            result.append(name)
    return _dedupe_str_list(result)


# =====================================================
# 风险规则
# =====================================================

def _keyword_risk_rules(requirement_text: str, business_domain: str) -> List[Dict[str, Any]]:
    asset_yield_scene = _is_asset_yield_scene(requirement_text, business_domain)

    rules: List[Dict[str, Any]] = [
        {
            "keywords": ["提现", "充值", "资金", "资产", "余额", "账变", "流水", "冻结", "解冻", "收益", "年化", "apr", "理财", "加息券", "派息"],
            "category": "资金",
            "level": "P1",
            "title_tpl": "资金相关风险：{kw}",
            "reason_tpl": "需求中涉及“{kw}”相关逻辑，资金类功能通常对正确性、权限控制和异常处理要求较高。",
            "impact": "若实现异常，可能直接影响资产正确性、收益展示可信度或核心用户信任。",
            "suggestion": "建议重点覆盖主链路、异常处理、重复提交、幂等、状态一致性及权限边界。",
            "trigger_condition": "发生资金扣减、增加、冻结、解冻、收益展示、收益汇总或失败补偿时",
            "automation_candidate": False,
            "affects_release_gate": True,
        },
        {
            "keywords": ["权限", "角色", "鉴权", "登录态", "访问控制", "资格"],
            "category": "权限",
            "level": "P1",
            "title_tpl": "权限相关风险：{kw}",
            "reason_tpl": "需求中涉及“{kw}”控制，权限判断错误容易导致越权或功能不可用。",
            "impact": "可能造成越权访问、功能误拦截或角色行为不一致。",
            "suggestion": "建议覆盖多角色、多登录态、越权访问、未登录访问、权限切换等场景。",
            "trigger_condition": "不同角色、状态或登录态访问相同能力时",
            "automation_candidate": True,
            "affects_release_gate": True,
        },
        {
            "keywords": ["实名", "KYC", "kyc", "合规", "认证"],
            "category": "合规",
            "level": "P1",
            "title_tpl": "合规相关风险：{kw}",
            "reason_tpl": "需求中涉及“{kw}”相关逻辑，通常与资格判断和流程限制强相关。",
            "impact": "若处理错误，可能导致不符合条件用户进入受限流程或合规拦截失效。",
            "suggestion": "建议验证资格边界、不同认证状态、被拒绝状态及流程阻断逻辑。",
            "trigger_condition": "不同认证/审核状态进入受限流程时",
            "automation_candidate": False,
            "affects_release_gate": True,
        },
        {
            "keywords": ["风控", "冻结", "拦截", "黑名单", "白名单", "频控", "限额"],
            "category": "风控",
            "level": "P1",
            "title_tpl": "风控相关风险：{kw}",
            "reason_tpl": "需求中涉及“{kw}”相关逻辑，通常包含复杂判定和异常分支。",
            "impact": "若策略异常，可能误拦截正常用户或放过高风险操作。",
            "suggestion": "建议覆盖命中/未命中、误判、边界输入、状态回退和提示一致性。",
            "trigger_condition": "风控命中、风控未命中、边界值或状态切换时",
            "automation_candidate": True,
            "affects_release_gate": True,
        },
        {
            "keywords": ["审核", "驳回", "状态", "状态流转", "申诉"],
            "category": "状态流转",
            "level": "P1",
            "title_tpl": "状态流转风险：{kw}",
            "reason_tpl": "需求中涉及“{kw}”相关逻辑，状态流转类功能通常存在分支多、条件复杂的问题。",
            "impact": "若状态处理错误，可能导致流程卡死、重复操作、展示不一致或业务结果错误。",
            "suggestion": "建议覆盖正常流转、逆向操作、重复提交、非法状态切换及状态展示一致性。",
            "trigger_condition": "状态切换、重复操作、撤回或逆向流转时",
            "automation_candidate": True,
            "affects_release_gate": True,
        },
        {
            "keywords": ["并发", "重复提交", "重试", "幂等", "回调", "异步"],
            "category": "并发/幂等",
            "level": "P1",
            "title_tpl": "并发与幂等风险：{kw}",
            "reason_tpl": "需求中涉及“{kw}”相关逻辑，容易出现竞态、重复生效和状态错乱。",
            "impact": "可能导致重复创建、重复扣款、重复通知或状态异常。",
            "suggestion": "建议覆盖并发操作、重复请求、超时重试、异步回调乱序和幂等校验。",
            "trigger_condition": "用户重复点击、请求重放、异步回调延迟或乱序时",
            "automation_candidate": False,
            "affects_release_gate": True,
        },
        {
            "keywords": ["导出", "导入", "下载", "上传", "文件"],
            "category": "数据/文件",
            "level": "P2",
            "title_tpl": "数据交互风险：{kw}",
            "reason_tpl": "需求中涉及“{kw}”相关能力，通常存在格式、大小、权限、超时等问题。",
            "impact": "可能导致文件不可用、数据丢失、格式错误或体验异常。",
            "suggestion": "建议覆盖文件边界、格式校验、失败重试、异常提示和权限限制。",
            "trigger_condition": "文件大小、格式、权限或网络异常时",
            "automation_candidate": False,
            "affects_release_gate": False,
        },
        {
            "keywords": ["搜索", "筛选", "分页", "排序", "列表"],
            "category": "列表查询",
            "level": "P2",
            "title_tpl": "列表查询风险：{kw}",
            "reason_tpl": "需求中涉及“{kw}”逻辑，列表类功能容易出现条件组合遗漏和结果不一致。",
            "impact": "可能导致查询结果错误、展示不完整或用户判断失真。",
            "suggestion": "建议覆盖空结果、多条件组合、边界条件、分页切换和排序一致性。",
            "trigger_condition": "多条件组合查询、翻页、排序切换时",
            "automation_candidate": True,
            "affects_release_gate": False,
        },
        {
            "keywords": ["多语言", "国际化", "文案"],
            "category": "兼容性",
            "level": "P3",
            "title_tpl": "兼容性风险：{kw}",
            "reason_tpl": "需求中涉及“{kw}”内容，常见问题包括文案遗漏、布局异常和语言切换不一致。",
            "impact": "可能影响用户体验和局部功能可用性。",
            "suggestion": "建议在主链路稳定后补充文案与兼容性回归。",
            "trigger_condition": "语言切换、布局变化或文案替换时",
            "automation_candidate": False,
            "affects_release_gate": False,
        },
        {
            "keywords": ["灰度", "开关", "配置", "AB", "A/B"],
            "category": "发布/配置",
            "level": "P2",
            "title_tpl": "配置与发布风险：{kw}",
            "reason_tpl": "需求涉及“{kw}”控制，配置类逻辑常导致不同环境、不同用户表现不一致。",
            "impact": "可能导致灰度范围异常、功能误开放或场景不稳定复现。",
            "suggestion": "建议验证开/关两态、灰度命中与未命中、配置刷新和回滚表现。",
            "trigger_condition": "配置切换、灰度命中、环境差异或回滚时",
            "automation_candidate": True,
            "affects_release_gate": True,
        },
    ]

    if not asset_yield_scene:
        rules.append(
            {
                "keywords": ["下单", "订单", "支付", "交易", "成交", "撤单", "撮合"],
                "category": "交易",
                "level": "P1",
                "title_tpl": "交易相关风险：{kw}",
                "reason_tpl": "需求中涉及“{kw}”主链路，交易类逻辑通常是核心业务路径。",
                "impact": "若处理异常，可能直接影响下单成功率、结果准确性和核心业务稳定性。",
                "suggestion": "建议验证主链路、失败回滚、重复点击、结果一致性和提示反馈。",
                "trigger_condition": "订单创建、成交、取消、失败、重试或重复提交时",
                "automation_candidate": True,
                "affects_release_gate": True,
            }
        )

    return rules


# =====================================================
# Agent
# =====================================================

class RiskAgent:
    """
    测试策略智能体 - 风险识别 Agent（企业级增强版）
    """

    def __init__(self) -> None:
        self.llm = LLM()

    async def analyze(
        self,
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]] = None,
        testcase_result: Optional[Dict[str, Any]] = None,
        impact_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        requirement_text = (requirement_text or "").strip()
        if not requirement_text:
            raise ValueError("requirement_text 不能为空")

        issue_titles = _extract_issue_titles_from_analysis_result(analysis_result)
        testcase_titles = _extract_titles_from_testcase_result(testcase_result)
        impact_modules = _extract_module_names_from_impact_data(impact_data)
        impact_flows = _extract_flow_names_from_impact_data(impact_data)
        business_domain = _normalize_business_domain(
            _pick_first_non_empty(
                (analysis_result or {}).get("business_domain"),
                (impact_data or {}).get("business_domain"),
                default="",
            ),
            requirement_text=requirement_text,
        )

        rule_risks = self._rule_based_risk_scan(
            requirement_text=requirement_text,
            business_domain=business_domain,
            impact_modules=impact_modules,
            impact_flows=impact_flows,
        )

        prompt = self._build_prompt(
            requirement_text=requirement_text,
            issue_titles=issue_titles,
            testcase_titles=testcase_titles,
            impact_modules=impact_modules,
            impact_flows=impact_flows,
            rule_risks=rule_risks,
            business_domain=business_domain,
        )

        raw = await self._call_llm_json(prompt)
        if raw:
            normalized = self._normalize_output(
                raw=raw,
                requirement_text=requirement_text,
                business_domain=business_domain,
            )
            if normalized:
                merged = self._merge_risk_items(
                    normalized.get("risk_items") or [],
                    rule_risks,
                    business_domain=business_domain,
                    requirement_text=requirement_text,
                )
                normalized["risk_items"] = merged
                normalized["overall_risk"] = self._calculate_overall_risk(
                    normalized.get("overall_risk"),
                    merged,
                )
                normalized["change_scope"] = _guess_change_scope(impact_modules, impact_flows, merged)
                normalized["core_reason"] = self._build_core_reason(
                    requirement_text=requirement_text,
                    business_domain=normalized["business_domain"],
                    overall_risk=normalized["overall_risk"],
                    risk_items=merged,
                    impact_modules=impact_modules,
                    impact_flows=impact_flows,
                )
                normalized["context_completeness"] = {
                    "has_requirement": bool(requirement_text),
                    "has_analysis_result": bool(analysis_result),
                    "has_testcase_result": bool(testcase_result),
                }
                return normalized

        return self._fallback(
            requirement_text=requirement_text,
            analysis_result=analysis_result,
            testcase_result=testcase_result,
            impact_data=impact_data,
            rule_risks=rule_risks,
            business_domain=business_domain,
        )

    def _build_prompt(
        self,
        requirement_text: str,
        issue_titles: List[str],
        testcase_titles: List[str],
        impact_modules: List[str],
        impact_flows: List[str],
        rule_risks: List[Dict[str, Any]],
        business_domain: str,
    ) -> str:
        return f"""
你是企业级测试风险评估专家，请基于下面需求做“风险识别与分级”。

你的任务：
1. 输出 business_domain
2. 识别本次需求的关键风险项
3. 给每个风险项打级别：P0/P1/P2/P3
4. 给出整体风险等级：高/中/低
5. 每个风险要说明：
   - title
   - category
   - reason
   - trigger_condition
   - impact
   - suggestion
   - related_modules
   - related_flows
   - test_types
   - automation_candidate
   - affects_release_gate
   - verify_points
   - gate_level
   - data_dependencies
   - api_dependencies
   - job_dependencies
   - monitor_points

输出要求：
1. 只能输出 JSON
2. 不要输出 markdown
3. 风险项必须与当前需求强相关，禁止输出无关业务域的通用兜底内容
4. 若需求偏“资产/理财/APR/收益展示”，禁止输出合约开平仓、撮合、下单等无关风险
5. 禁止把同一类风险拆成多个语义相近标题，尤其：
   - 收益展示口径 / APR精度 / 单位显示
   - 收益概览 / 详情 / 明细不一致
   - T+1时序 / 数据生成时序
   - 新老用户APR / 固定APR / 基准APR分层
   这些要合并为单一风险项
6. JSON 结构必须严格如下：
{{
  "business_domain": "登录注册/现货/合约/充值/提现/划转/P2P/跟单/撮合/风控/KYC/资产/通用",
  "overall_risk": "高/中/低",
  "risk_items": [
    {{
      "risk_id": "RISK-001",
      "title": "风险标题",
      "level": "P0/P1/P2/P3",
      "category": "风险分类",
      "reason": "风险原因",
      "trigger_condition": "触发条件",
      "impact": "风险影响",
      "suggestion": "测试建议",
      "related_modules": ["模块A"],
      "related_flows": ["流程A"],
      "test_types": ["接口测试"],
      "automation_candidate": false,
      "affects_release_gate": true,
      "verify_points": ["验证点1"],
      "gate_level": "blocker/critical/high/medium/low",
      "data_dependencies": ["依赖数据1"],
      "api_dependencies": ["依赖接口1"],
      "job_dependencies": ["依赖任务1"],
      "monitor_points": ["监控点1"]
    }}
  ]
}}

补充上下文：
- 业务域提示：{business_domain}
- 已有需求分析问题标题：{json.dumps(issue_titles, ensure_ascii=False)}
- 已有测试用例标题：{json.dumps(testcase_titles, ensure_ascii=False)}
- 已识别受影响模块：{json.dumps(impact_modules, ensure_ascii=False)}
- 已识别受影响流程：{json.dumps(impact_flows, ensure_ascii=False)}
- 本地规则已识别风险：{json.dumps(rule_risks, ensure_ascii=False)}

需求内容：
{requirement_text}
""".strip()

    async def _call_llm_json(self, prompt: str) -> Optional[Dict[str, Any]]:
        try:
            ret = self.llm.call(prompt)
            if asyncio.iscoroutine(ret):
                ret = await ret

            if isinstance(ret, dict):
                return ret

            if isinstance(ret, str):
                return _safe_json_loads(ret)

            content = None
            if hasattr(ret, "content"):
                content = getattr(ret, "content")
            elif hasattr(ret, "text"):
                content = getattr(ret, "text")

            if isinstance(content, str):
                return _safe_json_loads(content)

            model_dump = getattr(ret, "model_dump", None)
            if callable(model_dump):
                try:
                    data = model_dump()
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass

            dict_fn = getattr(ret, "dict", None)
            if callable(dict_fn):
                try:
                    data = dict_fn()
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass

            return None

        except Exception:
            logger.warning("[strategy.risk_agent] llm call failed", exc_info=True)
            return None

    def _normalize_output(
        self,
        raw: Dict[str, Any],
        requirement_text: str,
        business_domain: str,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None

        risk_items = []
        for idx, item in enumerate(_ensure_list(raw.get("risk_items")), start=1):
            if not isinstance(item, dict):
                continue

            title = _pick_first_str(item.get("title"), item.get("name"))
            if not title:
                continue

            category = _pick_first_str(item.get("category"), default="一般风险")
            reason = _pick_first_str(item.get("reason"), default="")
            impact = _pick_first_str(item.get("impact"), default="")
            suggestion = _pick_first_str(item.get("suggestion"), default="")
            trigger_condition = _pick_first_str(item.get("trigger_condition"), default="")

            text = f"{title} {category} {reason} {impact} {suggestion} {trigger_condition}"
            if not _is_text_relevant_to_domain(text, business_domain, requirement_text=requirement_text):
                continue
            if _is_noise_text(text):
                continue

            level = _normalize_risk_level(item.get("level"))
            affects_release_gate = (
                bool(item.get("affects_release_gate"))
                if isinstance(item.get("affects_release_gate"), bool)
                else level in {"P0", "P1"}
            )

            test_types = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("test_types")) if str(x).strip()]
            )
            if not test_types:
                test_types = _guess_test_types(
                    title=title,
                    category=category,
                    reason=reason,
                    requirement_text=requirement_text,
                )

            risk_items.append({
                "risk_id": _pick_first_str(item.get("risk_id"), default=f"RISK-{idx:03d}"),
                "title": title,
                "level": level,
                "category": category,
                "reason": reason,
                "trigger_condition": trigger_condition,
                "impact": impact,
                "suggestion": suggestion,
                "related_modules": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("related_modules")) if str(x).strip()]
                ),
                "related_flows": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("related_flows")) if str(x).strip()]
                ),
                "test_types": test_types,
                "automation_candidate": _bool_or_default(item.get("automation_candidate"), default=False),
                "affects_release_gate": affects_release_gate,
                "verify_points": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("verify_points")) if str(x).strip()]
                ) or _guess_verify_points(title, category, reason, requirement_text),
                "gate_level": _pick_first_str(
                    item.get("gate_level"),
                    default=_default_gate_level(level, affects_release_gate),
                ),
                "data_dependencies": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("data_dependencies")) if str(x).strip()]
                ) or _extract_data_dependencies_from_text(text, title),
                "api_dependencies": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("api_dependencies")) if str(x).strip()]
                ) or _extract_api_dependencies_from_text(text, title),
                "job_dependencies": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("job_dependencies")) if str(x).strip()]
                ) or _extract_job_dependencies_from_text(text, title),
                "monitor_points": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("monitor_points")) if str(x).strip()]
                ) or _guess_monitor_points(title, category, reason, requirement_text),
            })

        if not risk_items:
            return None

        risk_items = self._dedupe_and_reindex_risk_items(
            risk_items,
            business_domain,
            requirement_text=requirement_text,
        )
        risk_items.sort(key=lambda x: _risk_rank(x.get("level")))

        return {
            "business_domain": _normalize_business_domain(
                _pick_first_non_empty(raw.get("business_domain"), business_domain, default="通用"),
                requirement_text=requirement_text,
            ),
            "overall_risk": _normalize_overall_risk(raw.get("overall_risk")),
            "risk_items": risk_items,
        }

    def _rule_based_risk_scan(
        self,
        requirement_text: str,
        business_domain: str,
        impact_modules: List[str],
        impact_flows: List[str],
    ) -> List[Dict[str, Any]]:
        text = requirement_text or ""
        text_lower = text.lower()

        found_items: List[Dict[str, Any]] = []

        for rule in _keyword_risk_rules(requirement_text, business_domain):
            keywords = rule.get("keywords") or []
            for kw in keywords:
                if str(kw).lower() in text_lower:
                    title = str(rule.get("title_tpl", "{kw}风险")).format(kw=kw)
                    category = _pick_first_str(rule.get("category"), default="一般风险")
                    reason = str(rule.get("reason_tpl", "")).format(kw=kw)

                    full_text = f"{title} {category} {reason}"
                    if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=requirement_text):
                        continue

                    level = _normalize_risk_level(rule.get("level"))
                    affects_release_gate = bool(rule.get("affects_release_gate"))

                    found_items.append({
                        "risk_id": "",
                        "title": title,
                        "level": level,
                        "category": category,
                        "reason": reason,
                        "trigger_condition": _pick_first_str(rule.get("trigger_condition"), default=""),
                        "impact": _pick_first_str(rule.get("impact"), default=""),
                        "suggestion": _pick_first_str(rule.get("suggestion"), default=""),
                        "related_modules": _dedupe_str_list([kw] + impact_modules[:3]),
                        "related_flows": impact_flows[:3],
                        "test_types": _guess_test_types(
                            title=title,
                            category=category,
                            reason=reason,
                            requirement_text=requirement_text,
                        ),
                        "automation_candidate": bool(rule.get("automation_candidate")),
                        "affects_release_gate": affects_release_gate,
                        "verify_points": _guess_verify_points(title, category, reason, requirement_text),
                        "gate_level": _default_gate_level(level, affects_release_gate),
                        "data_dependencies": _extract_data_dependencies_from_text(full_text, title),
                        "api_dependencies": _extract_api_dependencies_from_text(full_text, title),
                        "job_dependencies": _extract_job_dependencies_from_text(full_text, title),
                        "monitor_points": _guess_monitor_points(title, category, reason, requirement_text),
                    })
                    break

        domain_items: List[Dict[str, Any]] = []

        if business_domain == "提现":
            domain_items.extend([
                {
                    "risk_id": "",
                    "title": "提现链路资金一致性风险",
                    "level": "P0",
                    "category": "资金",
                    "reason": "提现流程涉及资产冻结、提交流程、审核和状态变化，任何一步异常都可能导致资产不一致。",
                    "trigger_condition": "提现提交、审核、失败回退、取消或异常重试时",
                    "impact": "可能导致余额、冻结金额、流水和提现状态不一致。",
                    "suggestion": "建议验证提交成功/失败、审核通过/驳回、回退、重复提交和状态一致性。",
                    "related_modules": _dedupe_str_list(["提现", "资产"] + impact_modules[:4]),
                    "related_flows": impact_flows[:4],
                    "test_types": ["功能测试", "接口测试", "数据一致性测试", "异常流测试"],
                    "automation_candidate": False,
                    "affects_release_gate": True,
                },
                {
                    "risk_id": "",
                    "title": "提现风控与限额误判风险",
                    "level": "P1",
                    "category": "风控",
                    "reason": "提现通常受风控、白名单、黑名单、限额和资格条件影响，边界处理容易出错。",
                    "trigger_condition": "不同限额、资格、风控状态和边界值提现时",
                    "impact": "可能导致正常用户被拦截或风险用户被放行。",
                    "suggestion": "建议覆盖多资格状态、限额边界、风控命中/未命中和错误提示一致性。",
                    "related_modules": _dedupe_str_list(["提现", "风控"] + impact_modules[:4]),
                    "related_flows": impact_flows[:4],
                    "test_types": ["接口测试", "风控测试", "权限测试", "异常流测试"],
                    "automation_candidate": True,
                    "affects_release_gate": True,
                },
            ])

        elif business_domain == "充值":
            domain_items.append({
                "risk_id": "",
                "title": "充值到账与资产入账一致性风险",
                "level": "P0",
                "category": "资金",
                "reason": "充值到账涉及到账确认、状态更新、资产入账和流水生成，链路较长且易出现状态不一致。",
                "trigger_condition": "到账确认、重复通知、失败回退或状态切换时",
                "impact": "可能导致到账状态、资产余额和流水记录不一致。",
                "suggestion": "建议验证到账成功、重复通知、异常状态和资产/流水一致性。",
                "related_modules": _dedupe_str_list(["充值", "资产"] + impact_modules[:4]),
                "related_flows": impact_flows[:4],
                "test_types": ["功能测试", "接口测试", "数据一致性测试"],
                "automation_candidate": False,
                "affects_release_gate": True,
            })

        elif business_domain == "划转":
            domain_items.append({
                "risk_id": "",
                "title": "划转前后账户资产一致性风险",
                "level": "P0",
                "category": "资金",
                "reason": "划转涉及多个账户余额变化，若事务处理不完整容易出现一增一减不一致。",
                "trigger_condition": "划转成功、失败、重复提交或并发操作时",
                "impact": "可能导致划出与划入账户资产不一致。",
                "suggestion": "建议验证成功/失败/重试/并发场景下的资产与流水一致性。",
                "related_modules": _dedupe_str_list(["划转", "资产"] + impact_modules[:4]),
                "related_flows": impact_flows[:4],
                "test_types": ["接口测试", "数据一致性测试", "并发测试", "幂等测试"],
                "automation_candidate": False,
                "affects_release_gate": True,
            })

        elif business_domain == "现货":
            domain_items.append({
                "risk_id": "",
                "title": "现货下单与订单状态流转风险",
                "level": "P0",
                "category": "交易",
                "reason": "现货下单是核心主链路，涉及订单创建、余额处理、状态变更和结果展示。",
                "trigger_condition": "下单、撤单、失败、部分成交或状态切换时",
                "impact": "可能导致订单状态错误、余额处理异常或结果展示不一致。",
                "suggestion": "建议验证下单主链路、异常回退、余额处理、订单状态和结果一致性。",
                "related_modules": _dedupe_str_list(["现货", "订单"] + impact_modules[:4]),
                "related_flows": impact_flows[:4],
                "test_types": ["功能测试", "接口测试", "数据一致性测试", "异常流测试"],
                "automation_candidate": True,
                "affects_release_gate": True,
            })

        elif business_domain == "合约":
            domain_items.append({
                "risk_id": "",
                "title": "合约开平仓与仓位资产联动风险",
                "level": "P0",
                "category": "交易",
                "reason": "合约业务涉及仓位、保证金、订单和资产联动，逻辑复杂且风险高。",
                "trigger_condition": "开仓、平仓、加减仓、爆仓或强平时",
                "impact": "可能导致仓位、保证金、订单状态和资产结果不一致。",
                "suggestion": "建议重点验证开平仓主链路、仓位变化、保证金变化和异常场景。",
                "related_modules": _dedupe_str_list(["合约", "订单", "资产"] + impact_modules[:4]),
                "related_flows": impact_flows[:4],
                "test_types": ["功能测试", "接口测试", "数据一致性测试", "异常流测试"],
                "automation_candidate": False,
                "affects_release_gate": True,
            })

        elif business_domain == "登录注册":
            domain_items.append({
                "risk_id": "",
                "title": "登录鉴权与状态校验风险",
                "level": "P1",
                "category": "权限",
                "reason": "登录注册相关需求涉及认证、验证码、登录态与角色校验，容易出现越权或不可用。",
                "trigger_condition": "不同登录态、验证码状态、角色状态或未登录访问时",
                "impact": "可能导致用户无法正常登录、被误拦截或越权访问。",
                "suggestion": "建议覆盖正常登录、失败提示、验证码校验、过期状态和角色差异。",
                "related_modules": _dedupe_str_list(["登录注册", "鉴权"] + impact_modules[:4]),
                "related_flows": impact_flows[:4],
                "test_types": ["功能测试", "接口测试", "权限测试", "异常流测试"],
                "automation_candidate": True,
                "affects_release_gate": True,
            })

        elif business_domain == "撮合":
            domain_items.append({
                "risk_id": "",
                "title": "撮合结果与订单状态一致性风险",
                "level": "P0",
                "category": "交易",
                "reason": "撮合流程直接决定订单结果和成交状态，错误会影响核心交易正确性。",
                "trigger_condition": "成交、部分成交、撤单、失败回退或异步通知时",
                "impact": "可能导致订单状态、成交结果和资产变化不一致。",
                "suggestion": "建议验证撮合结果、状态回写、异步通知、重复回调和资产一致性。",
                "related_modules": _dedupe_str_list(["撮合", "订单"] + impact_modules[:4]),
                "related_flows": impact_flows[:4],
                "test_types": ["接口测试", "数据一致性测试", "幂等测试", "异常流测试"],
                "automation_candidate": False,
                "affects_release_gate": True,
            })

        elif business_domain == "资产":
            if _is_asset_yield_scene(requirement_text, business_domain):
                domain_items.extend([
                    {
                        "risk_id": "",
                        "title": "收益展示口径错误风险",
                        "level": "P0",
                        "category": "展示准确性",
                        "reason": "需求涉及APR/年化/收益展示，且存在固定APR、基准APR、新老用户分层、加息券收益统一口径等复杂规则，极易出现口径混用。",
                        "trigger_condition": "不同页面、不同币种、不同用户层级、不同持仓状态展示APR或收益时",
                        "impact": "可能直接导致收益展示错误、用户误判收益预期并影响上线结论。",
                        "suggestion": "必须优先验证APR来源、收益口径、精度、单位、汇总与明细一致性，并作为发布门禁项。",
                        "related_modules": _dedupe_str_list(["资产", "理财"] + impact_modules[:4]),
                        "related_flows": impact_flows[:4],
                        "test_types": ["功能测试", "边界值测试", "数据一致性测试", "接口测试"],
                        "automation_candidate": True,
                        "affects_release_gate": True,
                    },
                    {
                        "risk_id": "",
                        "title": "收益概览与详情不一致风险",
                        "level": "P1",
                        "category": "数据一致性",
                        "reason": "收益概览、详情、列表、tooltip可能来自不同接口或缓存，容易出现汇总值与详情值不一致。",
                        "trigger_condition": "概览页、详情页、列表页切换或数据刷新时",
                        "impact": "可能导致页面间数据对不上，降低结果可信度。",
                        "suggestion": "建议校验概览、详情、列表、接口源数据在不同状态下的一致性。",
                        "related_modules": _dedupe_str_list(["资产", "理财"] + impact_modules[:4]),
                        "related_flows": impact_flows[:4],
                        "test_types": ["功能测试", "接口测试", "数据一致性测试"],
                        "automation_candidate": True,
                        "affects_release_gate": True,
                    },
                    {
                        "risk_id": "",
                        "title": "收益数据生成时序风险",
                        "level": "P1",
                        "category": "时序/数据生成",
                        "reason": "收益展示依赖后台定时生成或异步入库，若T+1任务时序不稳定，容易导致页面展示延迟、缺失或错位。",
                        "trigger_condition": "T+1 数据生成、定时任务延迟、补跑或数据刷新时",
                        "impact": "可能导致用户在不同时间点看到的收益结果不一致。",
                        "suggestion": "建议覆盖数据未生成、刚生成、补生成、重复生成及跨周期刷新场景。",
                        "related_modules": _dedupe_str_list(["资产", "理财"] + impact_modules[:4]),
                        "related_flows": impact_flows[:4],
                        "test_types": ["功能测试", "接口测试", "数据一致性测试", "异常流测试"],
                        "automation_candidate": False,
                        "affects_release_gate": True,
                    },
                    {
                        "risk_id": "",
                        "title": "多周期切换收益展示错误风险",
                        "level": "P1",
                        "category": "展示准确性",
                        "reason": "7/30/90天等多周期切换场景容易出现口径切换、缓存复用或字段映射错误。",
                        "trigger_condition": "切换不同收益周期、页面刷新、跨端查看时",
                        "impact": "可能导致不同周期收益值、趋势或说明展示错误。",
                        "suggestion": "建议重点覆盖周期切换、默认周期、空数据周期及不同端展示一致性。",
                        "related_modules": _dedupe_str_list(["资产", "理财"] + impact_modules[:4]),
                        "related_flows": impact_flows[:4],
                        "test_types": ["功能测试", "边界值测试", "数据一致性测试"],
                        "automation_candidate": True,
                        "affects_release_gate": True,
                    },
                    {
                        "risk_id": "",
                        "title": "加息券收益纳入口径风险",
                        "level": "P1",
                        "category": "规则口径",
                        "reason": "加息券收益若纳入概览展示，容易出现基础收益与加息收益合并口径不一致。",
                        "trigger_condition": "有券/无券、券过期、券生效中、叠加展示时",
                        "impact": "可能导致收益汇总与明细拆分口径不一致。",
                        "suggestion": "建议覆盖不同加息券状态下的收益汇总、明细与说明文案一致性。",
                        "related_modules": _dedupe_str_list(["资产", "理财"] + impact_modules[:4]),
                        "related_flows": impact_flows[:4],
                        "test_types": ["功能测试", "边界值测试", "数据一致性测试"],
                        "automation_candidate": False,
                        "affects_release_gate": True,
                    },
                    {
                        "risk_id": "",
                        "title": "用户分层APR规则命中风险",
                        "level": "P1",
                        "category": "规则判断",
                        "reason": "需求存在新老用户、固定APR、基准APR等分层逻辑，若规则命中顺序或字段映射错误，极易出现展示错配。",
                        "trigger_condition": "新用户/老用户切换、固定APR/基准APR切换、不同产品规则组合时",
                        "impact": "可能导致用户看到不属于自己的APR与收益展示结果。",
                        "suggestion": "建议构造不同用户层级、APR规则、产品配置组合验证规则命中。",
                        "related_modules": _dedupe_str_list(["资产", "理财", "APR规则"] + impact_modules[:4]),
                        "related_flows": impact_flows[:4],
                        "test_types": ["功能测试", "接口测试", "边界值测试", "数据一致性测试"],
                        "automation_candidate": True,
                        "affects_release_gate": True,
                    },
                    {
                        "risk_id": "",
                        "title": "收益折线图交互与双指标同步展示风险",
                        "level": "P2",
                        "category": "交互/前端展示",
                        "reason": "需求要求在悬停或点击某一日期节点时同时展示APR与收益值，且支持双轴或切换展示，前端实现复杂。",
                        "trigger_condition": "鼠标悬停、点击节点、APR/收益切换、弱网或性能受限场景",
                        "impact": "信息展示不完整或错位，降低模块可用性与专业感。",
                        "suggestion": "重点验证交互触发条件下APR与收益值是否同步、轴线切换是否影响数据准确性。",
                        "related_modules": _dedupe_str_list(["资产", "理财", "前端展示"] + impact_modules[:4]),
                        "related_flows": _dedupe_str_list(["收益趋势交互查看"] + impact_flows[:4]),
                        "test_types": ["功能测试", "交互测试", "兼容性测试"],
                        "automation_candidate": False,
                        "affects_release_gate": False,
                    },
                    {
                        "risk_id": "",
                        "title": "无收益与未参与理财场景提示风险",
                        "level": "P2",
                        "category": "异常/空数据处理",
                        "reason": "需求定义了多种无数据场景（未参与、周期内无可计息资产、当日未生成），若判断条件不清晰，易误展示空态。",
                        "trigger_condition": "未参与理财、无收益、周期内无数据、T+1未生成时",
                        "impact": "可能误导用户理解当前收益状态，影响可用性与专业性。",
                        "suggestion": "建议区分不同空场景的占位提示、说明文案与入口引导。",
                        "related_modules": _dedupe_str_list(["资产", "理财", "空态展示"] + impact_modules[:4]),
                        "related_flows": impact_flows[:4],
                        "test_types": ["功能测试", "异常流测试", "兼容性测试"],
                        "automation_candidate": True,
                        "affects_release_gate": False,
                    },
                ])
            else:
                domain_items.append({
                    "risk_id": "",
                    "title": "资产展示与账变一致性风险",
                    "level": "P1",
                    "category": "数据一致性",
                    "reason": "资产域需求通常涉及余额、明细、流水或账变联动，若同步异常会导致展示不一致。",
                    "trigger_condition": "资产更新、页面刷新、明细切换或账变写入时",
                    "impact": "可能导致资产概览与明细展示不一致。",
                    "suggestion": "建议验证资产概览、明细、流水及接口源数据的一致性。",
                    "related_modules": _dedupe_str_list(["资产"] + impact_modules[:4]),
                    "related_flows": impact_flows[:4],
                    "test_types": ["功能测试", "接口测试", "数据一致性测试"],
                    "automation_candidate": True,
                    "affects_release_gate": True,
                })

        enriched_domain_items = []
        for item in domain_items:
            title = _pick_first_str(item.get("title"))
            category = _pick_first_str(item.get("category"))
            reason = _pick_first_str(item.get("reason"))
            level = _normalize_risk_level(item.get("level"))
            affects_release_gate = bool(item.get("affects_release_gate"))
            text_for_dep = f"{title} {category} {reason} {requirement_text}"

            x = dict(item)
            x["verify_points"] = _guess_verify_points(title, category, reason, requirement_text)
            x["gate_level"] = _default_gate_level(level, affects_release_gate)
            x["data_dependencies"] = _extract_data_dependencies_from_text(text_for_dep, title)
            x["api_dependencies"] = _extract_api_dependencies_from_text(text_for_dep, title)
            x["job_dependencies"] = _extract_job_dependencies_from_text(text_for_dep, title)
            x["monitor_points"] = _guess_monitor_points(title, category, reason, requirement_text)
            enriched_domain_items.append(x)

        found_items.extend(enriched_domain_items)

        result = self._dedupe_and_reindex_risk_items(
            found_items,
            business_domain,
            requirement_text=requirement_text,
        )
        result.sort(key=lambda x: _risk_rank(x.get("level")))
        return result

    def _merge_risk_items(
        self,
        primary: List[Dict[str, Any]],
        secondary: List[Dict[str, Any]],
        business_domain: str,
        requirement_text: str,
    ) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}

        for arr in (primary or [], secondary or []):
            for item in arr:
                if not isinstance(item, dict):
                    continue

                title = _pick_first_str(item.get("title"), default="风险项")
                category = _pick_first_str(item.get("category"), default="一般风险")
                reason = _pick_first_str(item.get("reason"), default="")
                full_text = " ".join([
                    title,
                    category,
                    reason,
                    _pick_first_str(item.get("impact")),
                    _pick_first_str(item.get("suggestion")),
                ])
                if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=requirement_text):
                    continue
                if _is_noise_text(full_text):
                    continue
                if _is_asset_yield_scene(requirement_text, business_domain) and _contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                    continue

                level = _normalize_risk_level(item.get("level"))
                affects_release_gate = (
                    bool(item.get("affects_release_gate"))
                    if isinstance(item.get("affects_release_gate"), bool)
                    else level in {"P0", "P1"}
                )
                dedupe_key = _risk_semantic_key(title, reason, category)

                if dedupe_key not in merged:
                    merged[dedupe_key] = {
                        "risk_id": _pick_first_str(item.get("risk_id"), default=""),
                        "title": title,
                        "level": level,
                        "category": category,
                        "reason": reason,
                        "trigger_condition": _pick_first_str(item.get("trigger_condition"), default=""),
                        "impact": _pick_first_str(item.get("impact"), default=""),
                        "suggestion": _pick_first_str(item.get("suggestion"), default=""),
                        "related_modules": _dedupe_str_list(
                            [str(x).strip() for x in _ensure_list(item.get("related_modules")) if str(x).strip()]
                        ),
                        "related_flows": _dedupe_str_list(
                            [str(x).strip() for x in _ensure_list(item.get("related_flows")) if str(x).strip()]
                        ),
                        "test_types": _dedupe_str_list(
                            [str(x).strip() for x in _ensure_list(item.get("test_types")) if str(x).strip()]
                        ) or _guess_test_types(
                            title=title,
                            category=category,
                            reason=reason,
                            requirement_text=requirement_text,
                        ),
                        "automation_candidate": _bool_or_default(item.get("automation_candidate"), default=False),
                        "affects_release_gate": affects_release_gate,
                        "verify_points": _dedupe_str_list(
                            [str(x).strip() for x in _ensure_list(item.get("verify_points")) if str(x).strip()]
                        ) or _guess_verify_points(title, category, reason, requirement_text),
                        "gate_level": _pick_first_str(
                            item.get("gate_level"),
                            default=_default_gate_level(level, affects_release_gate),
                        ),
                        "data_dependencies": _dedupe_str_list(
                            [str(x).strip() for x in _ensure_list(item.get("data_dependencies")) if str(x).strip()]
                        ) or _extract_data_dependencies_from_text(full_text, title),
                        "api_dependencies": _dedupe_str_list(
                            [str(x).strip() for x in _ensure_list(item.get("api_dependencies")) if str(x).strip()]
                        ) or _extract_api_dependencies_from_text(full_text, title),
                        "job_dependencies": _dedupe_str_list(
                            [str(x).strip() for x in _ensure_list(item.get("job_dependencies")) if str(x).strip()]
                        ) or _extract_job_dependencies_from_text(full_text, title),
                        "monitor_points": _dedupe_str_list(
                            [str(x).strip() for x in _ensure_list(item.get("monitor_points")) if str(x).strip()]
                        ) or _guess_monitor_points(title, category, reason, requirement_text),
                    }
                else:
                    old = merged[dedupe_key]

                    if _risk_rank(item.get("level")) < _risk_rank(old.get("level")):
                        old["level"] = _normalize_risk_level(item.get("level"))

                    new_title = title.strip()
                    old_title = _pick_first_str(old.get("title"), default="").strip()
                    if new_title and (not old_title or len(new_title) < len(old_title)):
                        old["title"] = new_title

                    if not old.get("reason"):
                        old["reason"] = reason
                    if not old.get("trigger_condition"):
                        old["trigger_condition"] = _pick_first_str(item.get("trigger_condition"), default="")
                    if not old.get("impact"):
                        old["impact"] = _pick_first_str(item.get("impact"), default="")
                    if not old.get("suggestion"):
                        old["suggestion"] = _pick_first_str(item.get("suggestion"), default="")

                    old["related_modules"] = _dedupe_str_list(
                        old.get("related_modules", []) +
                        [str(x).strip() for x in _ensure_list(item.get("related_modules")) if str(x).strip()]
                    )
                    old["related_flows"] = _dedupe_str_list(
                        old.get("related_flows", []) +
                        [str(x).strip() for x in _ensure_list(item.get("related_flows")) if str(x).strip()]
                    )
                    old["test_types"] = _dedupe_str_list(
                        old.get("test_types", []) +
                        [str(x).strip() for x in _ensure_list(item.get("test_types")) if str(x).strip()]
                    )
                    old["verify_points"] = _dedupe_str_list(
                        old.get("verify_points", []) +
                        [str(x).strip() for x in _ensure_list(item.get("verify_points")) if str(x).strip()]
                    )
                    old["data_dependencies"] = _dedupe_str_list(
                        old.get("data_dependencies", []) +
                        [str(x).strip() for x in _ensure_list(item.get("data_dependencies")) if str(x).strip()]
                    )
                    old["api_dependencies"] = _dedupe_str_list(
                        old.get("api_dependencies", []) +
                        [str(x).strip() for x in _ensure_list(item.get("api_dependencies")) if str(x).strip()]
                    )
                    old["job_dependencies"] = _dedupe_str_list(
                        old.get("job_dependencies", []) +
                        [str(x).strip() for x in _ensure_list(item.get("job_dependencies")) if str(x).strip()]
                    )
                    old["monitor_points"] = _dedupe_str_list(
                        old.get("monitor_points", []) +
                        [str(x).strip() for x in _ensure_list(item.get("monitor_points")) if str(x).strip()]
                    )

                    if not old.get("automation_candidate") and isinstance(item.get("automation_candidate"), bool):
                        old["automation_candidate"] = item.get("automation_candidate")
                    if not old.get("affects_release_gate") and isinstance(item.get("affects_release_gate"), bool):
                        old["affects_release_gate"] = item.get("affects_release_gate")

                    old["gate_level"] = _default_gate_level(
                        old.get("level"),
                        bool(old.get("affects_release_gate")),
                    )

        result = self._dedupe_and_reindex_risk_items(
            list(merged.values()),
            business_domain,
            requirement_text=requirement_text,
        )
        result.sort(key=lambda x: _risk_rank(x.get("level")))
        return result

    def _dedupe_and_reindex_risk_items(
        self,
        items: List[Dict[str, Any]],
        business_domain: str,
        requirement_text: str,
    ) -> List[Dict[str, Any]]:
        uniq: Dict[str, Dict[str, Any]] = {}

        for item in items or []:
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), default="")
            category = _pick_first_str(item.get("category"), default="")
            reason = _pick_first_str(item.get("reason"), default="")
            if not title:
                continue

            full_text = " ".join([
                title,
                category,
                reason,
                _pick_first_str(item.get("impact")),
                _pick_first_str(item.get("suggestion")),
            ])
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=requirement_text):
                continue
            if _is_noise_text(full_text):
                continue
            if _is_asset_yield_scene(requirement_text, business_domain) and _contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue

            key = _risk_semantic_key(title, reason, category)

            if key not in uniq:
                uniq[key] = dict(item)
            else:
                old = uniq[key]
                if _risk_rank(item.get("level")) < _risk_rank(old.get("level")):
                    old["level"] = _normalize_risk_level(item.get("level"))

                new_title = title.strip()
                old_title = _pick_first_str(old.get("title"), default="").strip()
                if new_title and (not old_title or len(new_title) < len(old_title)):
                    old["title"] = new_title

                old["related_modules"] = _dedupe_str_list(
                    _ensure_list(old.get("related_modules")) + _ensure_list(item.get("related_modules"))
                )
                old["related_flows"] = _dedupe_str_list(
                    _ensure_list(old.get("related_flows")) + _ensure_list(item.get("related_flows"))
                )
                old["test_types"] = _dedupe_str_list(
                    _ensure_list(old.get("test_types")) + _ensure_list(item.get("test_types"))
                )
                old["verify_points"] = _dedupe_str_list(
                    _ensure_list(old.get("verify_points")) + _ensure_list(item.get("verify_points"))
                )
                old["data_dependencies"] = _dedupe_str_list(
                    _ensure_list(old.get("data_dependencies")) + _ensure_list(item.get("data_dependencies"))
                )
                old["api_dependencies"] = _dedupe_str_list(
                    _ensure_list(old.get("api_dependencies")) + _ensure_list(item.get("api_dependencies"))
                )
                old["job_dependencies"] = _dedupe_str_list(
                    _ensure_list(old.get("job_dependencies")) + _ensure_list(item.get("job_dependencies"))
                )
                old["monitor_points"] = _dedupe_str_list(
                    _ensure_list(old.get("monitor_points")) + _ensure_list(item.get("monitor_points"))
                )

                if not old.get("impact"):
                    old["impact"] = _pick_first_str(item.get("impact"), default="")
                if not old.get("suggestion"):
                    old["suggestion"] = _pick_first_str(item.get("suggestion"), default="")
                if not old.get("trigger_condition"):
                    old["trigger_condition"] = _pick_first_str(item.get("trigger_condition"), default="")
                if not old.get("reason"):
                    old["reason"] = _pick_first_str(item.get("reason"), default="")
                if not old.get("automation_candidate") and isinstance(item.get("automation_candidate"), bool):
                    old["automation_candidate"] = item.get("automation_candidate")
                if not old.get("affects_release_gate") and isinstance(item.get("affects_release_gate"), bool):
                    old["affects_release_gate"] = item.get("affects_release_gate")

        result = list(uniq.values())
        result.sort(key=lambda x: (_risk_rank(x.get("level")), x.get("title", "")))

        for idx, item in enumerate(result, start=1):
            level = _normalize_risk_level(item.get("level"))
            affects_release_gate = (
                bool(item.get("affects_release_gate"))
                if isinstance(item.get("affects_release_gate"), bool)
                else level in {"P0", "P1"}
            )

            item["risk_id"] = f"RISK-{idx:03d}"
            item["level"] = level
            item["automation_candidate"] = _bool_or_default(item.get("automation_candidate"), default=False)
            item["affects_release_gate"] = affects_release_gate
            item["gate_level"] = _pick_first_str(
                item.get("gate_level"),
                default=_default_gate_level(level, affects_release_gate),
            )

            if "test_types" not in item or not item.get("test_types"):
                item["test_types"] = _guess_test_types(
                    title=_pick_first_str(item.get("title")),
                    category=_pick_first_str(item.get("category")),
                    reason=_pick_first_str(item.get("reason")),
                    requirement_text=requirement_text,
                )

            if "verify_points" not in item or not item.get("verify_points"):
                item["verify_points"] = _guess_verify_points(
                    title=_pick_first_str(item.get("title")),
                    category=_pick_first_str(item.get("category")),
                    reason=_pick_first_str(item.get("reason")),
                    requirement_text=requirement_text,
                )

            if "data_dependencies" not in item or not item.get("data_dependencies"):
                item["data_dependencies"] = _extract_data_dependencies_from_text(
                    f"{_pick_first_str(item.get('title'))} {_pick_first_str(item.get('reason'))}",
                    _pick_first_str(item.get("title")),
                )
            if "api_dependencies" not in item or not item.get("api_dependencies"):
                item["api_dependencies"] = _extract_api_dependencies_from_text(
                    f"{_pick_first_str(item.get('title'))} {_pick_first_str(item.get('reason'))}",
                    _pick_first_str(item.get("title")),
                )
            if "job_dependencies" not in item or not item.get("job_dependencies"):
                item["job_dependencies"] = _extract_job_dependencies_from_text(
                    f"{_pick_first_str(item.get('title'))} {_pick_first_str(item.get('reason'))}",
                    _pick_first_str(item.get("title")),
                )
            if "monitor_points" not in item or not item.get("monitor_points"):
                item["monitor_points"] = _guess_monitor_points(
                    title=_pick_first_str(item.get("title")),
                    category=_pick_first_str(item.get("category")),
                    reason=_pick_first_str(item.get("reason")),
                    requirement_text=requirement_text,
                )

            item["related_modules"] = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("related_modules")) if str(x).strip()]
            )
            item["related_flows"] = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("related_flows")) if str(x).strip()]
            )
            item["test_types"] = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("test_types")) if str(x).strip()]
            )
            item["verify_points"] = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("verify_points")) if str(x).strip()]
            )[:6]
            item["data_dependencies"] = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("data_dependencies")) if str(x).strip()]
            )
            item["api_dependencies"] = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("api_dependencies")) if str(x).strip()]
            )
            item["job_dependencies"] = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("job_dependencies")) if str(x).strip()]
            )
            item["monitor_points"] = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("monitor_points")) if str(x).strip()]
            )[:5]

        return result[:20]

    def _calculate_overall_risk(
        self,
        llm_overall_risk: Any,
        risk_items: List[Dict[str, Any]],
    ) -> str:
        normalized_llm = _normalize_overall_risk(llm_overall_risk)

        if any(_normalize_risk_level(x.get("level")) == "P0" for x in risk_items):
            return "高"
        if any(_normalize_risk_level(x.get("level")) == "P1" and bool(x.get("affects_release_gate")) for x in risk_items):
            return "高"

        p1_count = sum(1 for x in risk_items if _normalize_risk_level(x.get("level")) == "P1")
        p2_count = sum(1 for x in risk_items if _normalize_risk_level(x.get("level")) == "P2")

        if p1_count >= 2:
            return "高"
        if p1_count >= 1 or p2_count >= 4:
            return "中"
        if len(risk_items) <= 1:
            return "低" if normalized_llm == "低" else "中"

        return normalized_llm

    def _build_core_reason(
        self,
        requirement_text: str,
        business_domain: str,
        overall_risk: str,
        risk_items: List[Dict[str, Any]],
        impact_modules: List[str],
        impact_flows: List[str],
    ) -> List[str]:
        reasons = [
            f"当前需求识别为「{business_domain}」业务域",
            f"共识别 {len(risk_items)} 个与当前需求强相关的风险项",
            f"整体风险等级收敛为「{overall_risk}」",
        ]

        if _is_asset_yield_scene(requirement_text, business_domain):
            reasons.append("需求涉及APR规则、收益展示、T+1数据生成、加息券兼容或多周期切换，属于资产收益核心链路")
        if len(impact_modules) >= 4:
            reasons.append("受影响模块较多，存在前后端、数据生成与展示链路联动")
        if len(impact_flows) >= 3:
            reasons.append("受影响流程较多，需同时关注主流程、分支流程与异常流程")

        blocker_titles = [
            _pick_first_str(x.get("title"))
            for x in risk_items
            if _pick_first_str(x.get("gate_level")) == "blocker"
        ]
        if blocker_titles:
            reasons.append(f"存在 blocker 级风险：{ '、'.join(blocker_titles[:3]) }")

        return _dedupe_str_list(reasons)

    def _fallback(
        self,
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]],
        testcase_result: Optional[Dict[str, Any]],
        impact_data: Optional[Dict[str, Any]],
        rule_risks: List[Dict[str, Any]],
        business_domain: str,
    ) -> Dict[str, Any]:
        risk_items: List[Dict[str, Any]] = list(rule_risks)

        impact_modules = _extract_module_names_from_impact_data(impact_data)
        impact_flows = _extract_flow_names_from_impact_data(impact_data)

        if len(impact_modules) >= 5:
            risk_items.append({
                "risk_id": "",
                "title": "影响模块较多导致回归范围扩大",
                "level": "P2",
                "category": "影响范围",
                "reason": "本次变更识别到较多受影响模块，说明联动面较大。",
                "trigger_condition": "多个受影响模块需要同时验证时",
                "impact": "可能增加遗漏场景和联动缺陷风险。",
                "suggestion": "建议先聚焦高风险主链路，再扩展回归验证。",
                "related_modules": impact_modules[:8],
                "related_flows": [],
                "test_types": ["回归测试"],
                "automation_candidate": False,
                "affects_release_gate": False,
                "verify_points": ["校验新增模块与关联模块之间的联动是否被覆盖"],
                "gate_level": "medium",
                "data_dependencies": [],
                "api_dependencies": [],
                "job_dependencies": [],
                "monitor_points": ["回归缺陷分布监控"],
            })

        if len(impact_flows) >= 3:
            risk_items.append({
                "risk_id": "",
                "title": "流程分支较多导致遗漏风险提升",
                "level": "P2",
                "category": "流程复杂度",
                "reason": "本次需求涉及多条主流程/分支流程，流程复杂度上升。",
                "trigger_condition": "主流程、分支流程、逆向流程同时存在时",
                "impact": "可能出现状态遗漏、分支未覆盖或回归不充分。",
                "suggestion": "建议优先覆盖主流程、关键分支和非法路径。",
                "related_modules": [],
                "related_flows": impact_flows[:6],
                "test_types": ["功能测试", "异常流测试", "回归测试"],
                "automation_candidate": False,
                "affects_release_gate": False,
                "verify_points": ["梳理并验证主流程、关键分支、异常分支"],
                "gate_level": "medium",
                "data_dependencies": [],
                "api_dependencies": [],
                "job_dependencies": [],
                "monitor_points": ["关键路径失败率监控"],
            })

        analysis_issues = _extract_issue_items_from_analysis_result(analysis_result)
        for item in analysis_issues[:8]:
            title = _pick_first_str(
                item.get("title"),
                item.get("summary"),
                item.get("issue"),
                default="需求问题",
            )
            severity = _pick_first_str(item.get("severity"), item.get("level"), default="中")

            full_text = f"{title} {_pick_first_str(item.get('category'), default='')}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=requirement_text):
                continue

            level = "P2"
            sev_upper = str(severity).strip().upper()
            if sev_upper in {"CRITICAL", "HIGH", "严重", "高"}:
                level = "P1"

            affects_release_gate = level in {"P0", "P1"}

            risk_items.append({
                "risk_id": "",
                "title": f"需求质量风险：{title}",
                "level": level,
                "category": "需求质量",
                "reason": "需求分析结果中已识别出该问题，若未澄清可能影响实现正确性与测试判断。",
                "trigger_condition": "需求边界不清、规则未确认或异常流程未定义时",
                "impact": "可能导致测试范围误判、开发实现偏差或上线遗漏。",
                "suggestion": "建议测试前优先确认该问题，并提高相关场景优先级。",
                "related_modules": [],
                "related_flows": [],
                "test_types": ["功能测试", "异常流测试"],
                "automation_candidate": False,
                "affects_release_gate": affects_release_gate,
                "verify_points": ["在测试执行前完成需求澄清并同步测试边界"],
                "gate_level": _default_gate_level(level, affects_release_gate),
                "data_dependencies": [],
                "api_dependencies": [],
                "job_dependencies": [],
                "monitor_points": [],
            })

        testcase_titles = _extract_titles_from_testcase_result(testcase_result)
        if testcase_result is not None and len(testcase_titles) <= 1:
            risk_items.append({
                "risk_id": "",
                "title": "测试场景覆盖可能不足",
                "level": "P2",
                "category": "测试覆盖",
                "reason": "当前可复用的测试用例标题较少，可能说明场景拆解仍不充分。",
                "trigger_condition": "当前可复用测试资产较少时",
                "impact": "可能导致异常分支、边界条件或联动场景遗漏。",
                "suggestion": "建议补充关键异常、边界、状态流转和角色差异场景。",
                "related_modules": impact_modules[:5],
                "related_flows": impact_flows[:5],
                "test_types": ["异常流测试", "边界值测试", "回归测试"],
                "automation_candidate": False,
                "affects_release_gate": False,
                "verify_points": ["补充关键高风险场景、异常场景与边界场景用例"],
                "gate_level": "medium",
                "data_dependencies": [],
                "api_dependencies": [],
                "job_dependencies": [],
                "monitor_points": [],
            })

        text = requirement_text or ""
        if _text_contains_any(text, ["多角色", "多状态", "多端", "兼容", "历史数据", "灰度", "配置开关"]):
            risk_items.append({
                "risk_id": "",
                "title": "复杂变更特征带来的联动风险",
                "level": "P2",
                "category": "复杂度",
                "reason": "需求包含多角色、多状态、多端或配置控制等复杂因素。",
                "trigger_condition": "不同角色、终端、配置或状态组合出现时",
                "impact": "可能导致不同条件下表现不一致或特定组合场景遗漏。",
                "suggestion": "建议做条件组合梳理，并优先验证关键交叉场景。",
                "related_modules": impact_modules[:6],
                "related_flows": impact_flows[:6],
                "test_types": ["功能测试", "回归测试", "异常流测试"],
                "automation_candidate": False,
                "affects_release_gate": False,
                "verify_points": ["建立条件组合矩阵，优先覆盖关键交叉场景"],
                "gate_level": "medium",
                "data_dependencies": [],
                "api_dependencies": [],
                "job_dependencies": [],
                "monitor_points": [],
            })

        if business_domain in {"提现", "充值", "划转", "现货", "合约", "撮合"}:
            risk_items.append({
                "risk_id": "",
                "title": "核心交易/资金链路结果一致性风险",
                "level": "P0",
                "category": "资金/交易",
                "reason": "该业务域属于核心交易或资金链路，结果正确性与状态一致性风险高。",
                "trigger_condition": "主链路成功、失败、重复提交、回退或异步处理时",
                "impact": "可能导致核心业务错误、资产异常或结果不一致。",
                "suggestion": "建议优先验证主链路、异常流、幂等、状态一致性和结果回写。",
                "related_modules": impact_modules[:6],
                "related_flows": impact_flows[:6],
                "test_types": ["功能测试", "接口测试", "数据一致性测试", "异常流测试"],
                "automation_candidate": False,
                "affects_release_gate": True,
                "verify_points": ["验证成功/失败/重试/回滚下的状态与资产结果一致性"],
                "gate_level": "blocker",
                "data_dependencies": [],
                "api_dependencies": [],
                "job_dependencies": [],
                "monitor_points": ["核心链路错误率监控"],
            })

        if business_domain == "资产" and _is_asset_yield_scene(text, business_domain):
            risk_items.extend([
                {
                    "risk_id": "",
                    "title": "收益展示口径错误风险",
                    "level": "P0",
                    "category": "展示准确性",
                    "reason": "需求涉及APR/年化/收益展示，若计算口径、精度、单位或四舍五入规则不一致，容易造成展示错误。",
                    "trigger_condition": "不同页面、不同币种、不同持仓状态展示收益时",
                    "impact": "可能导致用户对收益预期产生误解，引发投诉或信任下降。",
                    "suggestion": "建议验证收益口径、精度、单位、汇总与详情一致性，以及边界场景展示。",
                    "related_modules": impact_modules[:6],
                    "related_flows": impact_flows[:6],
                    "test_types": ["功能测试", "边界值测试", "数据一致性测试", "接口测试"],
                    "automation_candidate": True,
                    "affects_release_gate": True,
                    "verify_points": [
                        "校验APR来源字段、收益金额来源字段与需求定义一致",
                        "校验概览、详情、图表、tooltip口径一致",
                    ],
                    "gate_level": "blocker",
                    "data_dependencies": ["APR历史数据", "收益汇总数据"],
                    "api_dependencies": ["收益概览查询接口", "收益详情查询接口"],
                    "job_dependencies": [],
                    "monitor_points": ["线上APR展示值与后端计算值偏差监控"],
                },
                {
                    "risk_id": "",
                    "title": "收益数据生成时序风险",
                    "level": "P1",
                    "category": "时序/数据生成",
                    "reason": "收益展示依赖后台定时生成或异步入库，若时序不稳定，容易导致页面展示延迟、缺失或错位。",
                    "trigger_condition": "T+1 数据生成、定时任务延迟、补跑或数据刷新时",
                    "impact": "可能导致用户在不同时间点看到的收益结果不一致。",
                    "suggestion": "建议覆盖数据未生成、刚生成、补生成、重复生成及跨周期刷新场景。",
                    "related_modules": impact_modules[:6],
                    "related_flows": impact_flows[:6],
                    "test_types": ["功能测试", "接口测试", "数据一致性测试", "异常流测试"],
                    "automation_candidate": False,
                    "affects_release_gate": True,
                    "verify_points": [
                        "校验T+1未生成、已生成、补生成、重复生成场景下展示是否正确",
                    ],
                    "gate_level": "critical",
                    "data_dependencies": ["T+1定时产物", "周期维度数据"],
                    "api_dependencies": ["收益概览查询接口"],
                    "job_dependencies": ["T+1收益生成任务"],
                    "monitor_points": ["T+1任务成功率与延迟监控"],
                },
            ])

        merged = self._dedupe_and_reindex_risk_items(
            risk_items,
            business_domain,
            requirement_text=requirement_text,
        )
        overall_risk = self._calculate_overall_risk(None, merged)

        return {
            "business_domain": business_domain,
            "change_scope": _guess_change_scope(impact_modules, impact_flows, merged),
            "overall_risk": overall_risk,
            "risk_items": merged,
            "core_reason": self._build_core_reason(
                requirement_text=requirement_text,
                business_domain=business_domain,
                overall_risk=overall_risk,
                risk_items=merged,
                impact_modules=impact_modules,
                impact_flows=impact_flows,
            ),
            "context_completeness": {
                "has_requirement": bool(requirement_text),
                "has_analysis_result": bool(analysis_result),
                "has_testcase_result": bool(testcase_result),
            },
        }