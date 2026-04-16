#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/agents/scope_agent.py

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

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


def _normalize_priority(value: Any, default: str = "P2") -> str:
    s = str(value or "").strip().upper()
    if s in {"P0", "P1", "P2", "P3"}:
        return s
    if s in {"BLOCKER", "CRITICAL"}:
        return "P0"
    if s in {"HIGH", "严重", "高"}:
        return "P1"
    if s in {"MEDIUM", "中"}:
        return "P2"
    if s in {"LOW", "低"}:
        return "P3"
    return default


def _priority_rank(value: Any) -> int:
    p = _normalize_priority(value, default="P2")
    mapping = {
        "P0": 0,
        "P1": 1,
        "P2": 2,
        "P3": 3,
    }
    return mapping.get(p, 99)


def _risk_to_scope_priority(level: Any) -> str:
    lv = str(level or "").strip().upper()
    if lv == "P0":
        return "P0"
    if lv == "P1":
        return "P1"
    if lv == "P2":
        return "P2"
    if lv == "P3":
        return "P3"
    if lv in {"BLOCKER", "CRITICAL"}:
        return "P0"
    if lv in {"HIGH", "严重", "高"}:
        return "P1"
    if lv in {"MEDIUM", "中"}:
        return "P2"
    if lv in {"LOW", "低"}:
        return "P3"
    return "P2"


def _normalize_text_key(text: Any) -> str:
    s = str(text or "").strip().lower()
    s = re.sub(r"[：:，,。.\-_/\\()\[\]{}]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _text_contains_any(text: str, keywords: List[str]) -> bool:
    text_lower = (text or "").lower()
    return any(str(k).lower() in text_lower for k in keywords if str(k).strip())


# =====================================================
# 业务域
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
    "资产": ["资产", "余额", "冻结", "流水", "账变", "asset", "balance", "收益", "年化", "apr", "earn", "理财", "加息券", "派息", "t+1"],
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

_ASSET_YIELD_KEYWORDS = [
    "apr", "年化", "收益", "理财", "earn", "收益概览", "历史收益", "加息券", "派息", "t+1"
]

_TRADING_ONLY_KEYWORDS = [
    "下单", "撤单", "撮合", "仓位", "保证金", "爆仓", "强平", "订单簿",
    "现货交易", "合约开仓", "合约平仓", "成交回报", "永续", "杠杆", "开仓", "平仓"
]


def _is_asset_yield_scene(requirement_text: str, domain: str) -> bool:
    if domain != "资产":
        return False
    text = (requirement_text or "").lower()
    return _text_contains_any(text, _ASSET_YIELD_KEYWORDS)


def _normalize_business_domain(value: Any, requirement_text: str = "") -> str:
    s = str(value or "").strip()
    if s in _ALLOWED_DOMAINS:
        return s

    text = f"{s} {(requirement_text or '').strip()}".lower()

    if _text_contains_any(text, _ASSET_YIELD_KEYWORDS):
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


def _is_text_relevant_to_domain(text: str, domain: str, requirement_text: str = "") -> bool:
    text = str(text or "").strip()
    if not text:
        return False

    if domain == "通用":
        return True

    lower_text = text.lower()

    if _is_asset_yield_scene(requirement_text, domain):
        if _text_contains_any(lower_text, _TRADING_ONLY_KEYWORDS):
            return False
        if _text_contains_any(lower_text, _ASSET_YIELD_KEYWORDS):
            return True

    domain_keywords = _DOMAIN_KEYWORDS.get(domain, [])
    exclusion_keywords = _DOMAIN_EXCLUSION_KEYWORDS.get(domain, [])

    if exclusion_keywords and any(k.lower() in lower_text for k in exclusion_keywords):
        return False

    if domain_keywords and any(k.lower() in lower_text for k in domain_keywords):
        return True

    matched_other_domain = False
    for other_domain, keywords in _DOMAIN_KEYWORDS.items():
        if other_domain in {domain, "通用"}:
            continue
        if keywords and any(k.lower() in lower_text for k in keywords):
            matched_other_domain = True
            break

    if matched_other_domain:
        return False

    generic_allow_keywords = [
        "主流程", "异常流", "边界", "数据一致性", "接口", "发布", "回滚", "灰度",
        "测试环境", "测试数据", "准入", "准出", "自动化", "回归", "冒烟", "质量门禁",
        "展示准确性", "汇总", "详情", "口径", "精度"
    ]
    if any(k.lower() in lower_text for k in generic_allow_keywords):
        return True

    return False


# =====================================================
# 提取上下文
# =====================================================

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


def _extract_risk_items(
    risk_data: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not risk_data:
        return []

    items = risk_data.get("risk_items")
    if not isinstance(items, list):
        return []

    return [item for item in items if isinstance(item, dict)]


# =====================================================
# 测试类型猜测
# =====================================================

def _guess_test_types(
    title: str,
    reason: str,
    requirement_text: str,
) -> List[str]:
    text = f"{title} {reason} {requirement_text}".lower()
    result: List[str] = ["功能测试"]

    if any(k in text for k in ["接口", "状态", "规则", "校验", "风控", "权限", "资格", "幂等", "回调", "异步"]):
        result.append("接口测试")

    if any(k in text for k in ["主链路", "冒烟", "登录", "提交", "确认", "审核", "支付", "提现", "充值", "划转", "收益展示", "理财"]):
        result.append("冒烟测试")

    if any(k in text for k in ["回归", "联动", "影响范围", "受影响", "历史能力", "兼容"]):
        result.append("回归测试")

    if any(k in text for k in ["异常", "失败", "错误", "拒绝", "拦截", "边界"]):
        result.append("异常流测试")

    if any(k in text for k in ["边界", "最大", "最小", "限制", "长度", "范围", "精度", "四舍五入"]):
        result.append("边界值测试")

    if any(k in text for k in ["权限", "角色", "资格"]):
        result.append("权限测试")

    if any(k in text for k in ["风控", "限额", "频控", "黑名单", "白名单"]):
        result.append("风控测试")

    if any(k in text for k in ["并发", "重复提交", "异步", "竞态"]):
        result.append("并发测试")

    if any(k in text for k in ["幂等", "重复请求", "重试"]):
        result.append("幂等测试")

    if any(k in text for k in ["余额", "账变", "流水", "金额", "资产", "收益", "apr", "年化", "汇总", "详情", "加息券", "派息"]):
        result.append("数据一致性测试")

    return _dedupe_str_list(result)


# =====================================================
# Agent
# =====================================================

class ScopeAgent:
    """
    测试策略智能体 - 范围裁剪 Agent（企业级增强版）
    """

    def __init__(self) -> None:
        self.llm = LLM()

    async def analyze(
        self,
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]] = None,
        testcase_result: Optional[Dict[str, Any]] = None,
        impact_data: Optional[Dict[str, Any]] = None,
        risk_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        requirement_text = (requirement_text or "").strip()
        if not requirement_text:
            raise ValueError("requirement_text 不能为空")

        testcase_titles = _extract_titles_from_testcase_result(testcase_result)
        issue_titles = _extract_issue_titles_from_analysis_result(analysis_result)
        impact_modules = _extract_module_names_from_impact_data(impact_data)
        impact_flows = _extract_flow_names_from_impact_data(impact_data)
        risk_items = _extract_risk_items(risk_data)

        business_domain = _normalize_business_domain(
            _pick_first_non_empty(
                (analysis_result or {}).get("business_domain"),
                (impact_data or {}).get("business_domain"),
                default="",
            ),
            requirement_text=requirement_text,
        )

        prompt = self._build_prompt(
            requirement_text=requirement_text,
            testcase_titles=testcase_titles,
            issue_titles=issue_titles,
            impact_modules=impact_modules,
            impact_flows=impact_flows,
            risk_items=risk_items,
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
                return normalized

        return self._fallback(
            requirement_text=requirement_text,
            analysis_result=analysis_result,
            testcase_result=testcase_result,
            impact_data=impact_data,
            risk_data=risk_data,
            business_domain=business_domain,
        )

    # -------------------------------------------------
    # Prompt
    # -------------------------------------------------
    def _build_prompt(
        self,
        requirement_text: str,
        testcase_titles: List[str],
        issue_titles: List[str],
        impact_modules: List[str],
        impact_flows: List[str],
        risk_items: List[Dict[str, Any]],
        business_domain: str,
    ) -> str:
        return f"""
你是企业级测试策略专家，请根据需求内容和已有分析结果做“测试范围裁剪”。

你的任务：
1. 输出 business_domain（业务域）
2. 给出 must_test（本次必须测试）
3. 给出 should_test（建议测试）
4. 给出 defer_test（可延后测试）
5. 给出 out_of_scope（明确本次不测）
6. 给出 smoke_scope（冒烟测试范围）
7. 给出 regression_scope（回归测试范围）

输出要求：
1. 只能输出 JSON
2. 不要输出 markdown
3. 风险高、主链路、资金/交易/权限/风控/状态流转相关内容优先进入 must_test
4. out_of_scope 不能包含核心主链路、P0/P1 风险链路或烟囱式兜底内容
5. 若当前需求属于资产/理财/APR/收益展示，不要把合约/撮合/下单等无关交易链路纳入范围
6. JSON 结构必须严格如下：
{{
  "business_domain": "登录注册/现货/合约/充值/提现/划转/P2P/跟单/撮合/风控/KYC/资产/通用",
  "must_test": [
    {{
      "title": "范围标题",
      "reason": "为什么必须测",
      "priority": "P0/P1/P2/P3",
      "related_modules": ["模块A"],
      "related_flows": ["流程A"],
      "test_types": ["功能测试"],
      "owner": "测试/前端/后端"
    }}
  ],
  "should_test": [],
  "defer_test": [],
  "out_of_scope": [],
  "smoke_scope": [],
  "regression_scope": []
}}

补充上下文：
- 业务域提示：{business_domain}
- 已有测试用例标题：{json.dumps(testcase_titles, ensure_ascii=False)}
- 已有需求分析问题标题：{json.dumps(issue_titles, ensure_ascii=False)}
- 受影响模块：{json.dumps(impact_modules, ensure_ascii=False)}
- 受影响流程：{json.dumps(impact_flows, ensure_ascii=False)}
- 风险项：{json.dumps(risk_items, ensure_ascii=False)}

需求内容：
{requirement_text}
""".strip()

    # -------------------------------------------------
    # LLM
    # -------------------------------------------------
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
            logger.warning("[strategy.scope_agent] llm call failed", exc_info=True)
            return None

    # -------------------------------------------------
    # Normalize
    # -------------------------------------------------
    def _normalize_output(
        self,
        raw: Dict[str, Any],
        requirement_text: str,
        business_domain: str,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None

        domain = _normalize_business_domain(
            _pick_first_non_empty(raw.get("business_domain"), business_domain, default="通用"),
            requirement_text=requirement_text,
        )

        result = {
            "business_domain": domain,
            "must_test": self._normalize_scope_items(raw.get("must_test"), requirement_text, domain, default_priority="P1"),
            "should_test": self._normalize_scope_items(raw.get("should_test"), requirement_text, domain, default_priority="P2"),
            "defer_test": self._normalize_scope_items(raw.get("defer_test"), requirement_text, domain, default_priority="P3"),
            "out_of_scope": self._normalize_scope_items(raw.get("out_of_scope"), requirement_text, domain, default_priority="P3"),
            "smoke_scope": self._normalize_scope_items(raw.get("smoke_scope"), requirement_text, domain, default_priority="P1"),
            "regression_scope": self._normalize_scope_items(raw.get("regression_scope"), requirement_text, domain, default_priority="P2"),
        }

        if not result["must_test"] and not result["smoke_scope"] and not result["regression_scope"]:
            return None

        for key in ("must_test", "should_test", "defer_test", "out_of_scope", "smoke_scope", "regression_scope"):
            result[key] = self._dedupe_scope_items(result[key])

        self._apply_normalize_fallbacks(result, requirement_text=requirement_text, business_domain=domain)
        self._remove_cross_group_duplicates(result)
        self._guard_out_of_scope(result, requirement_text=requirement_text, business_domain=domain)
        self._post_clean(result, requirement_text=requirement_text, business_domain=domain)
        return result

    def _normalize_scope_items(
        self,
        items: Any,
        requirement_text: str,
        business_domain: str,
        default_priority: str = "P2",
    ) -> List[Dict[str, Any]]:
        result = []
        asset_yield_scene = _is_asset_yield_scene(requirement_text, business_domain)

        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue

            title = _pick_first_str(item.get("title"), item.get("name"))
            if not title:
                continue

            reason = _pick_first_str(item.get("reason"), default="")
            full_text = f"{title} {reason}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=requirement_text):
                continue
            if asset_yield_scene and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue

            priority = _normalize_priority(item.get("priority"), default=default_priority)

            related_modules = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("related_modules")) if str(x).strip()]
            )
            related_flows = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("related_flows")) if str(x).strip()]
            )
            test_types = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("test_types")) if str(x).strip()]
            )
            owner = _pick_first_str(item.get("owner"), default="测试")

            if not test_types:
                test_types = _guess_test_types(
                    title=title,
                    reason=reason,
                    requirement_text=requirement_text,
                )

            result.append({
                "title": title,
                "reason": reason,
                "priority": priority,
                "related_modules": related_modules,
                "related_flows": related_flows,
                "test_types": test_types,
                "owner": owner,
            })

        result.sort(key=lambda x: (_priority_rank(x.get("priority")), x.get("title", "")))
        return result

    def _dedupe_scope_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        uniq: Dict[str, Dict[str, Any]] = {}

        for item in items or []:
            if not isinstance(item, dict):
                continue

            title = _pick_first_str(item.get("title"), default="范围项")
            key = _normalize_text_key(title)

            if key not in uniq:
                uniq[key] = item
            else:
                old = uniq[key]

                if _priority_rank(item.get("priority")) < _priority_rank(old.get("priority")):
                    old["priority"] = _normalize_priority(item.get("priority"), default="P2")

                if not old.get("reason"):
                    old["reason"] = _pick_first_str(item.get("reason"), default="")

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
                if not old.get("owner"):
                    old["owner"] = _pick_first_str(item.get("owner"), default="测试")

        result = list(uniq.values())
        result.sort(key=lambda x: (_priority_rank(x.get("priority")), x.get("title", "")))
        return result

    def _remove_cross_group_duplicates(self, result: Dict[str, Any]) -> None:
        must_keys = {_normalize_text_key(x.get("title")) for x in result.get("must_test", []) if isinstance(x, dict)}
        smoke_keys = {_normalize_text_key(x.get("title")) for x in result.get("smoke_scope", []) if isinstance(x, dict)}

        def _filter(items: List[Dict[str, Any]], blocked: set[str]) -> List[Dict[str, Any]]:
            kept = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = _normalize_text_key(item.get("title"))
                if key in blocked:
                    continue
                kept.append(item)
            return kept

        result["should_test"] = _filter(result.get("should_test", []), must_keys)
        result["defer_test"] = _filter(result.get("defer_test", []), must_keys | smoke_keys)
        result["out_of_scope"] = _filter(result.get("out_of_scope", []), must_keys | smoke_keys)
        result["regression_scope"] = _filter(result.get("regression_scope", []), set())

    def _guard_out_of_scope(self, result: Dict[str, Any], requirement_text: str, business_domain: str) -> None:
        guarded = []
        asset_yield_scene = _is_asset_yield_scene(requirement_text, business_domain)

        for item in result.get("out_of_scope", []):
            if not isinstance(item, dict):
                continue

            title = _pick_first_str(item.get("title"))
            reason = _pick_first_str(item.get("reason"))
            priority = _normalize_priority(item.get("priority"), default="P3")
            full_text = f"{title} {reason}"

            if priority in {"P0", "P1"}:
                continue

            forbidden_keywords = [
                "主链路", "登录", "提现", "充值", "划转",
                "风控", "权限", "订单", "资产", "收益", "apr", "年化"
            ]
            if any(k.lower() in full_text.lower() for k in forbidden_keywords):
                continue

            if asset_yield_scene and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue

            guarded.append(item)

        result["out_of_scope"] = guarded

    def _apply_normalize_fallbacks(
        self,
        result: Dict[str, Any],
        requirement_text: str,
        business_domain: str,
    ) -> None:
        must_test = result.get("must_test") or []
        smoke_scope = result.get("smoke_scope") or []
        regression_scope = result.get("regression_scope") or []
        should_test = result.get("should_test") or []
        asset_yield_scene = _is_asset_yield_scene(requirement_text, business_domain)

        if not must_test and smoke_scope:
            result["must_test"] = smoke_scope[:3]

        if not smoke_scope and must_test:
            smoke_candidates = []
            for item in must_test:
                if not isinstance(item, dict):
                    continue
                copied = dict(item)
                copied["test_types"] = ["冒烟测试"]
                smoke_candidates.append(copied)
            result["smoke_scope"] = smoke_candidates[:3]

        if not regression_scope:
            seen = set()
            merged = []
            for arr in (must_test, should_test):
                for item in arr:
                    if not isinstance(item, dict):
                        continue
                    key = _normalize_text_key(item.get("title"))
                    if key in seen:
                        continue
                    seen.add(key)
                    copied = dict(item)
                    copied["test_types"] = ["回归测试"]
                    merged.append(copied)
            result["regression_scope"] = merged[:6]

        if not result.get("out_of_scope"):
            out_of_scope = []
            if _text_contains_any(requirement_text, ["多语言", "国际化", "文案"]):
                out_of_scope.append({
                    "title": "多语言/文案兼容验证",
                    "reason": "当前版本优先保证核心主链路和高风险范围，多语言细节本次可不作为主验证目标。",
                    "priority": "P3",
                    "related_modules": ["多语言"],
                    "related_flows": [],
                    "test_types": ["回归测试"],
                    "owner": "测试",
                })
            if asset_yield_scene:
                out_of_scope.append({
                    "title": "无关交易链路专项验证",
                    "reason": "当前需求聚焦收益展示与收益口径，本次不纳入无关交易主链路范围。",
                    "priority": "P3",
                    "related_modules": [],
                    "related_flows": [],
                    "test_types": ["回归测试"],
                    "owner": "测试",
                })
            result["out_of_scope"] = out_of_scope

    def _post_clean(self, result: Dict[str, Any], requirement_text: str, business_domain: str) -> None:
        asset_yield_scene = _is_asset_yield_scene(requirement_text, business_domain)

        for key in ("must_test", "should_test", "defer_test", "out_of_scope", "smoke_scope", "regression_scope"):
            cleaned = []
            for item in _ensure_list(result.get(key)):
                if not isinstance(item, dict):
                    continue
                title = _pick_first_str(item.get("title"))
                reason = _pick_first_str(item.get("reason"))
                text = f"{title} {reason}"
                if not _is_text_relevant_to_domain(text, business_domain, requirement_text=requirement_text):
                    continue
                if asset_yield_scene and _text_contains_any(text.lower(), _TRADING_ONLY_KEYWORDS):
                    continue
                cleaned.append(item)
            result[key] = self._dedupe_scope_items(cleaned)

    # -------------------------------------------------
    # Fallback
    # -------------------------------------------------
    def _fallback(
        self,
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]],
        testcase_result: Optional[Dict[str, Any]],
        impact_data: Optional[Dict[str, Any]],
        risk_data: Optional[Dict[str, Any]],
        business_domain: str,
    ) -> Dict[str, Any]:
        text = requirement_text or ""
        business_domain = _normalize_business_domain(business_domain, requirement_text=text)

        impact_modules = _extract_module_names_from_impact_data(impact_data)
        impact_flows = _extract_flow_names_from_impact_data(impact_data)
        testcase_titles = _extract_titles_from_testcase_result(testcase_result)
        issue_titles = _extract_issue_titles_from_analysis_result(analysis_result)
        risk_items = _extract_risk_items(risk_data)

        must_test: List[Dict[str, Any]] = []
        should_test: List[Dict[str, Any]] = []
        defer_test: List[Dict[str, Any]] = []
        out_of_scope: List[Dict[str, Any]] = []
        smoke_scope: List[Dict[str, Any]] = []
        regression_scope: List[Dict[str, Any]] = []

        asset_yield_scene = _is_asset_yield_scene(text, business_domain)

        def add_item(
            bucket: List[Dict[str, Any]],
            title: str,
            reason: str,
            priority: str,
            related_modules: Optional[List[str]] = None,
            related_flows: Optional[List[str]] = None,
            test_types: Optional[List[str]] = None,
            owner: str = "测试",
        ) -> None:
            full_text = f"{title} {reason}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=text):
                return
            if asset_yield_scene and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                return

            bucket.append({
                "title": title,
                "reason": reason,
                "priority": _normalize_priority(priority, default="P2"),
                "related_modules": _dedupe_str_list(related_modules or []),
                "related_flows": _dedupe_str_list(related_flows or []),
                "test_types": _dedupe_str_list(test_types or _guess_test_types(title=title, reason=reason, requirement_text=text)),
                "owner": owner,
            })

        # 1) 高风险先入 must/smoke/regression
        for risk in risk_items[:12]:
            if not isinstance(risk, dict):
                continue

            title = _pick_first_str(risk.get("title"), default="高风险项")
            reason = _pick_first_str(
                risk.get("reason"),
                risk.get("impact"),
                default="高风险项应优先纳入本次测试范围。",
            )
            full_text = f"{title} {reason}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=text):
                continue
            if asset_yield_scene and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue

            level = _normalize_priority(_risk_to_scope_priority(risk.get("level")), default="P2")
            related_modules = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(risk.get("related_modules")) if str(x).strip()]
            )
            related_flows = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(risk.get("related_flows")) if str(x).strip()]
            )
            test_types = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(risk.get("test_types")) if str(x).strip()]
            )
            if not test_types:
                test_types = _guess_test_types(title=title, reason=reason, requirement_text=text)

            item = {
                "title": title,
                "reason": reason,
                "priority": level,
                "related_modules": related_modules,
                "related_flows": related_flows,
                "test_types": test_types,
                "owner": "测试",
            }

            if level in {"P0", "P1"}:
                must_test.append(dict(item))
                smoke_scope.append({**dict(item), "test_types": ["冒烟测试"]})
                regression_scope.append({**dict(item), "test_types": ["回归测试"]})
            elif level == "P2":
                should_test.append(dict(item))
                regression_scope.append({**dict(item), "test_types": ["回归测试"]})
            else:
                defer_test.append(dict(item))

        # 2) 受影响流程优先
        for flow_name in impact_flows[:8]:
            title = flow_name
            reason = "受影响流程应纳入本次核心验证范围。"
            full_text = f"{title} {reason}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=text):
                continue
            if asset_yield_scene and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue

            item = {
                "title": title,
                "reason": reason,
                "priority": "P1",
                "related_modules": [],
                "related_flows": [flow_name],
                "test_types": _guess_test_types(title=title, reason=reason, requirement_text=text),
                "owner": "测试",
            }

            regression_scope.append({**dict(item), "test_types": ["回归测试"]})

            if any(x in flow_name for x in ["主流程", "提交", "审核", "登录", "鉴权", "状态流转", "提现", "充值", "划转", "收益展示", "详情", "周期切换"]):
                must_test.append(dict(item))
                smoke_scope.append({**dict(item), "test_types": ["冒烟测试"]})
            else:
                should_test.append(dict(item))

        # 3) 受影响模块扩大回归
        if impact_modules:
            add_item(
                regression_scope,
                title="受影响模块定向回归",
                reason="本次涉及模块受影响，建议做定向回归以降低外溢风险。",
                priority="P2",
                related_modules=impact_modules[:8],
                related_flows=impact_flows[:8],
                test_types=["回归测试"],
            )

        # 4) 已有用例补充 should / regression
        for title in testcase_titles[:8]:
            full_text = f"{title} 已有测试资产"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=text):
                continue
            if asset_yield_scene and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue

            item = {
                "title": title,
                "reason": "已有测试资产对应场景，建议纳入本次测试范围评估。",
                "priority": "P2",
                "related_modules": [],
                "related_flows": [],
                "test_types": _guess_test_types(title=title, reason="已有测试资产", requirement_text=text),
                "owner": "测试",
            }
            should_test.append(item)
            regression_scope.append({**dict(item), "test_types": ["回归测试"]})

        # 5) 需求分析问题补充 should
        for title in issue_titles[:6]:
            full_text = f"{title} 需求分析问题"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=text):
                continue
            if asset_yield_scene and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue

            should_test.append({
                "title": title,
                "reason": "需求分析已识别该问题，建议纳入本次验证重点。",
                "priority": "P1",
                "related_modules": [],
                "related_flows": [],
                "test_types": _guess_test_types(title=title, reason="需求分析问题", requirement_text=text),
                "owner": "测试",
            })

        # 6) 业务域专项
        if business_domain == "登录注册":
            add_item(
                must_test,
                "登录主链路验证",
                "登录链路是用户进入系统的核心入口。",
                "P0",
                ["登录注册"],
                ["登录流程"],
                ["功能测试", "冒烟测试", "接口测试"],
            )
            add_item(
                must_test,
                "验证码/鉴权校验验证",
                "鉴权和验证码异常会直接影响登录成功率和安全性。",
                "P1",
                ["登录注册", "鉴权"],
                ["验证码校验流程"],
                ["接口测试", "异常流测试", "权限测试"],
            )

        elif business_domain == "提现":
            add_item(
                must_test,
                "提现提交流程验证",
                "提现主流程直接影响资金安全与用户可用性。",
                "P0",
                ["提现", "资产"],
                ["提现提交流程"],
                ["功能测试", "冒烟测试", "接口测试", "数据一致性测试"],
            )
            add_item(
                must_test,
                "提现风控/限额校验验证",
                "提现风控、限额、资格校验是高风险逻辑。",
                "P0",
                ["提现", "风控"],
                ["提现校验流程"],
                ["风控测试", "接口测试", "异常流测试", "权限测试"],
            )

        elif business_domain == "充值":
            add_item(
                must_test,
                "充值到账主链路验证",
                "充值到账链路影响资产入账正确性。",
                "P0",
                ["充值", "资产"],
                ["充值到账流程"],
                ["功能测试", "冒烟测试", "数据一致性测试"],
            )

        elif business_domain == "划转":
            add_item(
                must_test,
                "划转主链路验证",
                "划转涉及账户间资产变化，需优先验证。",
                "P0",
                ["划转", "资产"],
                ["划转流程"],
                ["功能测试", "接口测试", "数据一致性测试"],
            )

        elif business_domain == "现货" and not asset_yield_scene:
            add_item(
                must_test,
                "现货下单主链路验证",
                "下单是核心交易主链路。",
                "P0",
                ["现货", "订单"],
                ["现货下单流程"],
                ["功能测试", "冒烟测试", "接口测试"],
            )

        elif business_domain == "合约" and not asset_yield_scene:
            add_item(
                must_test,
                "合约开平仓主链路验证",
                "开仓/平仓是合约交易核心链路。",
                "P0",
                ["合约", "订单", "资产"],
                ["开仓流程", "平仓流程"],
                ["功能测试", "冒烟测试", "接口测试", "数据一致性测试"],
            )

        elif business_domain == "撮合" and not asset_yield_scene:
            add_item(
                must_test,
                "订单撮合与状态流转验证",
                "撮合和状态流转正确性直接影响交易结果。",
                "P0",
                ["撮合", "订单"],
                ["撮合流程", "订单状态流转"],
                ["接口测试", "功能测试", "数据一致性测试"],
            )

        elif business_domain == "风控":
            add_item(
                must_test,
                "风控规则生效验证",
                "风控规则错误可能导致误拦截或风险放行。",
                "P0",
                ["风控"],
                ["风控校验流程"],
                ["风控测试", "接口测试", "异常流测试"],
            )

        elif business_domain == "资产":
            if asset_yield_scene:
                add_item(
                    must_test,
                    "收益展示口径验证",
                    "APR/年化/收益展示类需求需优先验证口径、精度和单位正确性。",
                    "P0",
                    ["资产", "理财"],
                    impact_flows[:5],
                    ["功能测试", "数据一致性测试", "边界值测试"],
                )
                add_item(
                    must_test,
                    "收益概览与详情一致性验证",
                    "概览、详情、列表可能来自不同数据源，需验证结果一致。",
                    "P1",
                    ["资产", "理财"],
                    impact_flows[:5],
                    ["功能测试", "接口测试", "数据一致性测试"],
                )
                if _text_contains_any(text.lower(), ["7天", "30天", "90天", "周期", "切换"]):
                    add_item(
                        must_test,
                        "多周期收益切换验证",
                        "不同周期切换容易出现口径、缓存或映射错误。",
                        "P1",
                        ["资产", "理财"],
                        ["多周期收益切换流程"],
                        ["功能测试", "边界值测试", "数据一致性测试"],
                    )
                if _text_contains_any(text.lower(), ["t+1", "数据生成", "派息", "补跑"]):
                    add_item(
                        must_test,
                        "收益数据生成时序验证",
                        "收益生成与前端展示衔接时序异常会导致结果缺失或不一致。",
                        "P1",
                        ["资产", "理财"],
                        ["收益数据生成与展示衔接流程"],
                        ["功能测试", "接口测试", "数据一致性测试", "异常流测试"],
                    )
                if _text_contains_any(text.lower(), ["加息券"]):
                    should_test.append({
                        "title": "加息券收益纳入展示验证",
                        "reason": "加息券收益纳入后，汇总与详情口径需保持一致。",
                        "priority": "P1",
                        "related_modules": ["资产", "理财", "加息券"],
                        "related_flows": ["加息券收益纳入展示流程"],
                        "test_types": ["功能测试", "数据一致性测试", "边界值测试"],
                        "owner": "测试",
                    })
            else:
                add_item(
                    must_test,
                    "资产展示与结果一致性验证",
                    "资产相关需求需优先验证展示结果与底层数据一致。",
                    "P0",
                    ["资产"],
                    impact_flows[:5],
                    ["功能测试", "接口测试", "数据一致性测试"],
                )

        # 7) 通用延后/不测
        if _text_contains_any(text, ["多语言", "国际化", "文案"]):
            defer_test.append({
                "title": "多语言/文案兼容验证",
                "reason": "若本次时间有限，可在主链路稳定后补充兼容性验证。",
                "priority": "P3",
                "related_modules": ["多语言"],
                "related_flows": [],
                "test_types": ["回归测试"],
                "owner": "测试",
            })
            out_of_scope.append({
                "title": "多语言/文案细节优化验证",
                "reason": "当前版本优先保证主链路和高风险逻辑，多语言细节本次不作为主验证目标。",
                "priority": "P3",
                "related_modules": ["多语言"],
                "related_flows": [],
                "test_types": ["回归测试"],
                "owner": "测试",
            })

        if _text_contains_any(text, ["分页", "筛选", "排序", "搜索", "列表"]):
            defer_test.append({
                "title": "列表筛选/分页/排序细节验证",
                "reason": "若主链路和高风险逻辑优先级更高，列表细节可延后补充。",
                "priority": "P2",
                "related_modules": ["列表"],
                "related_flows": [],
                "test_types": ["回归测试", "功能测试"],
                "owner": "测试",
            })

        # 8) 关键词强化
        if _text_contains_any(text, ["资金", "提现", "充值", "划转", "账变", "余额", "金额", "收益", "apr", "年化"]):
            add_item(
                must_test,
                "资产变化与结果一致性验证",
                "涉及金额、收益或资产变更时，必须确认资产、流水、状态三者一致。",
                "P0",
                ["资产"],
                impact_flows[:5],
                ["数据一致性测试", "接口测试", "功能测试"],
            )

        if (not asset_yield_scene) and _text_contains_any(text, ["订单", "交易", "撮合", "成交", "撤单"]):
            add_item(
                must_test,
                "订单状态流转与结果一致性验证",
                "订单创建、流转、成交、撤销等状态必须保持正确一致。",
                "P0",
                ["订单"],
                impact_flows[:5],
                ["接口测试", "功能测试", "数据一致性测试"],
            )

        if _text_contains_any(text, ["权限", "角色", "资格", "审核", "风控"]):
            add_item(
                must_test,
                "权限/资格/风控限制验证",
                "越权、误拦截或错误放行都属于高风险问题。",
                "P1",
                ["风控"],
                impact_flows[:5],
                ["权限测试", "风控测试", "异常流测试"],
            )

        if _text_contains_any(text, ["并发", "重复提交", "幂等", "重试", "回调", "异步"]):
            add_item(
                must_test,
                "并发/幂等/重试异常验证",
                "并发、重复请求或异步回调容易引发结果重复生效或状态异常。",
                "P1",
                impact_modules[:5],
                impact_flows[:5],
                ["并发测试", "幂等测试", "接口测试"],
            )

        # 9) 兜底主链路
        if not must_test and not smoke_scope:
            base_item = {
                "title": "核心业务主链路验证",
                "reason": "在缺乏充分上下文时，至少应覆盖需求涉及的核心主链路。",
                "priority": "P1",
                "related_modules": impact_modules[:5],
                "related_flows": impact_flows[:5],
                "test_types": ["功能测试", "冒烟测试"],
                "owner": "测试",
            }
            must_test.append(dict(base_item))
            smoke_scope.append({**dict(base_item), "test_types": ["冒烟测试"]})
            regression_scope.append({**dict(base_item), "test_types": ["回归测试"]})

        # 10) 多模块 / 多流程扩大回归
        if len(impact_modules) >= 5 or len(impact_flows) >= 3:
            regression_scope.append({
                "title": "多模块联动回归验证",
                "reason": "本次受影响模块或流程较多，建议扩大回归范围覆盖联动影响。",
                "priority": "P2",
                "related_modules": impact_modules[:8],
                "related_flows": impact_flows[:8],
                "test_types": ["回归测试"],
                "owner": "测试",
            })

        # 11) 资产收益类专门补充
        if asset_yield_scene:
            regression_scope.append({
                "title": "收益展示与历史能力定向回归",
                "reason": "需重点回归收益概览、收益详情、多周期切换、历史收益、加息券与数据生成链路。",
                "priority": "P1",
                "related_modules": ["资产", "理财"] + impact_modules[:6],
                "related_flows": impact_flows[:8],
                "test_types": ["回归测试", "数据一致性测试"],
                "owner": "测试",
            })
            out_of_scope.append({
                "title": "无关交易链路专项验证",
                "reason": "当前需求聚焦收益展示与收益口径，本次不纳入无关交易主链路范围。",
                "priority": "P3",
                "related_modules": [],
                "related_flows": [],
                "test_types": ["回归测试"],
                "owner": "测试",
            })

        result = {
            "business_domain": business_domain,
            "must_test": self._dedupe_scope_items(must_test),
            "should_test": self._dedupe_scope_items(should_test),
            "defer_test": self._dedupe_scope_items(defer_test),
            "out_of_scope": self._dedupe_scope_items(out_of_scope),
            "smoke_scope": self._dedupe_scope_items(smoke_scope),
            "regression_scope": self._dedupe_scope_items(regression_scope),
        }

        self._apply_normalize_fallbacks(result, requirement_text=text, business_domain=business_domain)
        self._remove_cross_group_duplicates(result)
        self._guard_out_of_scope(result, requirement_text=text, business_domain=business_domain)
        self._post_clean(result, requirement_text=text, business_domain=business_domain)

        return result