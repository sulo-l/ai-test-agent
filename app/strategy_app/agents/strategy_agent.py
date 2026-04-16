#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/agents/strategy_agent.py

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


def _normalize_text_key(text: Any) -> str:
    s = str(text or "").strip().lower()
    s = re.sub(r"[：:，,。.\-_/\\()\[\]{}]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


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
    mapping = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return mapping.get(p, 99)


def _normalize_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"true", "1", "yes", "y", "on"}:
            return True
        if s in {"false", "0", "no", "n", "off"}:
            return False
    return default


def _text_contains_any(text: str, keywords: List[str]) -> bool:
    text_lower = (text or "").lower()
    return any(str(k).lower() in text_lower for k in keywords if str(k).strip())


def _extract_titles(items: Any, key: str = "title", limit: Optional[int] = None) -> List[str]:
    result = []
    for item in _ensure_list(items):
        if not isinstance(item, dict):
            continue
        title = _pick_first_str(
            item.get(key),
            item.get("name"),
            item.get("summary"),
            item.get("title"),
            default="",
        )
        if title:
            result.append(title)
        if limit and len(result) >= limit:
            break
    return _dedupe_str_list(result)


def _extract_scope_titles(scope_data: Optional[Dict[str, Any]], key: str, limit: Optional[int] = None) -> List[str]:
    if not scope_data:
        return []
    return _extract_titles(scope_data.get(key), "title", limit)


def _extract_risk_titles(risk_data: Optional[Dict[str, Any]], limit: Optional[int] = None) -> List[str]:
    if not risk_data:
        return []
    return _extract_titles(risk_data.get("risk_items"), "title", limit)


def _extract_impact_modules(impact_data: Optional[Dict[str, Any]], limit: Optional[int] = None) -> List[str]:
    if not impact_data:
        return []
    return _extract_titles(impact_data.get("impact_modules"), "name", limit)


def _extract_impact_flows(impact_data: Optional[Dict[str, Any]], limit: Optional[int] = None) -> List[str]:
    if not impact_data:
        return []
    return _extract_titles(impact_data.get("affected_flows"), "name", limit)


def _extract_impact_roles(impact_data: Optional[Dict[str, Any]], limit: Optional[int] = None) -> List[str]:
    if not impact_data:
        return []
    return _extract_titles(impact_data.get("impact_roles"), "name", limit)


# =====================================================
# 业务域规则
# =====================================================

_ALLOWED_DOMAINS = {
    "登录注册", "用户中心", "现货", "合约", "充值", "提现", "划转",
    "P2P", "跟单", "撮合", "风控", "KYC", "资产", "通用"
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
    "充值": ["合约开仓", "合约平仓", "撮合", "跟单"],
    "提现": ["合约开仓", "合约平仓", "撮合", "跟单"],
    "划转": ["合约开仓", "合约平仓", "撮合"],
    "登录注册": ["合约开仓", "合约平仓", "撮合", "充值地址", "提现地址"],
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


def _normalize_test_type_name(value: Any) -> Optional[str]:
    s = str(value or "").strip()
    if not s:
        return None

    mapping = {
        "functional": "功能测试",
        "功能": "功能测试",
        "功能测试": "功能测试",
        "api": "接口测试",
        "接口": "接口测试",
        "接口测试": "接口测试",
        "联调": "联调测试",
        "integration": "联调测试",
        "联调测试": "联调测试",
        "smoke": "冒烟测试",
        "冒烟": "冒烟测试",
        "冒烟测试": "冒烟测试",
        "regression": "回归测试",
        "回归": "回归测试",
        "回归测试": "回归测试",
        "异常流": "异常流测试",
        "异常流测试": "异常流测试",
        "boundary": "边界值测试",
        "边界": "边界值测试",
        "边界值": "边界值测试",
        "边界值测试": "边界值测试",
        "权限": "权限测试",
        "权限测试": "权限测试",
        "风控": "风控测试",
        "风控测试": "风控测试",
        "concurrency": "并发测试",
        "并发": "并发测试",
        "并发测试": "并发测试",
        "idempotent": "幂等测试",
        "幂等": "幂等测试",
        "幂等测试": "幂等测试",
        "performance": "性能测试",
        "性能": "性能测试",
        "性能测试": "性能测试",
        "compatibility": "兼容性测试",
        "兼容": "兼容性测试",
        "兼容性测试": "兼容性测试",
        "security": "安全测试",
        "安全": "安全测试",
        "安全测试": "安全测试",
        "consistency": "数据一致性测试",
        "数据一致性": "数据一致性测试",
        "数据一致性测试": "数据一致性测试",
        "observability": "可观测性验证",
        "可观测性": "可观测性验证",
        "可观测性验证": "可观测性验证",
    }

    return mapping.get(s.lower(), s)


def _normalize_gate_decision(value: Any) -> str:
    s = str(value or "").strip().lower()
    if s in {"pass", "conditional_pass", "fail"}:
        return s
    if s in {"通过", "ok"}:
        return "pass"
    if s in {"有条件通过", "conditional"}:
        return "conditional_pass"
    if s in {"失败", "不通过", "reject"}:
        return "fail"
    return "conditional_pass"


def _clean_related_risks(
    risk_titles: List[str],
    business_domain: str,
    requirement_text: str,
    limit: int = 10,
) -> List[str]:
    result = []
    seen = set()
    for title in risk_titles or []:
        s = str(title or "").strip()
        if not s:
            continue
        if not _is_text_relevant_to_domain(s, business_domain, requirement_text=requirement_text):
            continue
        if _is_asset_yield_scene(requirement_text, business_domain) and _text_contains_any(s.lower(), _TRADING_ONLY_KEYWORDS):
            continue
        key = _normalize_text_key(s)
        if key in seen:
            continue
        seen.add(key)
        result.append(s)
        if len(result) >= limit:
            break
    return result


# =====================================================
# Agent
# =====================================================

class StrategyAgent:
    """
    测试策略智能体 - 策略汇总 Agent（企业级增强版）
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
        scope_data: Optional[Dict[str, Any]] = None,
        context_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        requirement_text = (requirement_text or "").strip()
        if not requirement_text:
            raise ValueError("requirement_text 不能为空")

        prompt = self._build_prompt(
            requirement_text=requirement_text,
            analysis_result=analysis_result,
            testcase_result=testcase_result,
            impact_data=impact_data,
            risk_data=risk_data,
            scope_data=scope_data,
            context_meta=context_meta,
        )

        raw = await self._call_llm_json(prompt)
        if raw:
            normalized = self._normalize_output(
                raw=raw,
                requirement_text=requirement_text,
                analysis_result=analysis_result,
                testcase_result=testcase_result,
                impact_data=impact_data,
                risk_data=risk_data,
                scope_data=scope_data,
                context_meta=context_meta,
            )
            if normalized:
                return normalized

        return self._fallback(
            requirement_text=requirement_text,
            analysis_result=analysis_result,
            testcase_result=testcase_result,
            impact_data=impact_data,
            risk_data=risk_data,
            scope_data=scope_data,
            context_meta=context_meta,
        )

    def _build_prompt(
        self,
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]],
        testcase_result: Optional[Dict[str, Any]],
        impact_data: Optional[Dict[str, Any]],
        risk_data: Optional[Dict[str, Any]],
        scope_data: Optional[Dict[str, Any]],
        context_meta: Optional[Dict[str, Any]],
    ) -> str:
        return f"""
你是企业级测试负责人 / 测试策略负责人。
请基于需求内容、影响分析、风险分析、范围裁剪、已有需求分析和已有测试用例结果，
输出一份“可执行、可落地、偏企业级”的最终测试策略 JSON。

你的任务不是重复总结，而是做“测试执行决策”和“上线质量决策”。

你必须输出这些内容：
1. business_domain：业务域识别结果
2. title：策略标题
3. objective：测试目标摘要
4. core_reason：本次策略核心判断依据
5. test_objectives：测试目标列表
6. out_of_scope：明确不测范围
7. test_layer_advice：测试层级建议（ui/api/manual/automation_candidate）
8. test_type_matrix：测试类型矩阵
9. environment_strategy：环境策略
10. test_data_strategy：测试数据策略
11. automation_strategy：自动化策略
12. regression_strategy：回归策略
13. release_strategy：发布策略
14. rollback_strategy：回滚策略
15. entry_criteria：测试准入条件
16. exit_criteria：测试准出条件
17. resource_plan：资源规划（one_day/two_days/three_days/five_days）
18. execution_order：执行顺序
19. blockers：阻塞项
20. pending_confirmations：待确认项
21. release_checklist：发布前检查项
22. quality_gate：质量门禁
23. assumptions：假设项
24. notes：补充说明

输出要求：
1. 只能输出 JSON
2. 不要输出 markdown
3. 必须结合主链路、高风险、冒烟、回归、上线风险、阻塞项做决策
4. 不要泛泛而谈，要偏企业级测试落地
5. 当资源有限时，优先强调“先测什么、后测什么、什么可延后”
6. 若涉及资金、交易、提现、充值、风控、状态流转、权限、并发、幂等、资产一致性，请优先体现这些风险
7. 如果当前是资产/理财/APR/收益展示类需求，禁止输出合约开平仓、撮合、下单等无关内容

上下文完整度：
{json.dumps(context_meta or {}, ensure_ascii=False)}

影响分析：
{json.dumps(impact_data or {}, ensure_ascii=False)}

风险分析：
{json.dumps(risk_data or {}, ensure_ascii=False)}

范围裁剪：
{json.dumps(scope_data or {}, ensure_ascii=False)}

已有需求分析结果：
{json.dumps(analysis_result or {}, ensure_ascii=False)}

已有测试用例结果：
{json.dumps(testcase_result or {}, ensure_ascii=False)}

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
            logger.warning("[strategy.strategy_agent] llm call failed", exc_info=True)
            return None

    def _normalize_output(
        self,
        raw: Dict[str, Any],
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]],
        testcase_result: Optional[Dict[str, Any]],
        impact_data: Optional[Dict[str, Any]],
        risk_data: Optional[Dict[str, Any]],
        scope_data: Optional[Dict[str, Any]],
        context_meta: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None

        business_domain = _normalize_business_domain(
            _pick_first_non_empty(
                raw.get("business_domain"),
                (context_meta or {}).get("business_domain_hint"),
                (analysis_result or {}).get("business_domain"),
                (impact_data or {}).get("business_domain"),
                default="通用",
            ),
            requirement_text=requirement_text,
        )

        result = {
            "business_domain": business_domain,
            "title": _pick_first_str(
                raw.get("title"),
                raw.get("summary_title"),
                default=f"{business_domain}测试策略分析结果",
            ) or f"{business_domain}测试策略分析结果",
            "objective": _pick_first_str(
                raw.get("objective"),
                raw.get("summary_objective"),
                default="识别高风险链路并给出可执行的测试策略建议",
            ) or "识别高风险链路并给出可执行的测试策略建议",
            "core_reason": self._clean_str_list(raw.get("core_reason"), business_domain, requirement_text=requirement_text),
            "test_objectives": self._clean_str_list(raw.get("test_objectives"), business_domain, requirement_text=requirement_text, allow_generic=True),
            "out_of_scope": self._normalize_scope_items(raw.get("out_of_scope"), business_domain, requirement_text),
            "test_layer_advice": self._normalize_test_layer_advice(raw.get("test_layer_advice"), business_domain, requirement_text),
            "test_type_matrix": self._normalize_test_type_matrix(raw.get("test_type_matrix"), business_domain, requirement_text),
            "environment_strategy": self._normalize_environment_strategy(raw.get("environment_strategy"), business_domain),
            "test_data_strategy": self._normalize_test_data_strategy(raw.get("test_data_strategy"), business_domain, requirement_text),
            "automation_strategy": self._normalize_automation_strategy(raw.get("automation_strategy"), business_domain, requirement_text),
            "regression_strategy": self._normalize_regression_strategy(raw.get("regression_strategy"), business_domain, requirement_text),
            "release_strategy": self._normalize_release_strategy(raw.get("release_strategy")),
            "rollback_strategy": self._normalize_rollback_strategy(raw.get("rollback_strategy")),
            "entry_criteria": self._normalize_entry_criteria(raw.get("entry_criteria")),
            "exit_criteria": self._normalize_exit_criteria(raw.get("exit_criteria")),
            "resource_plan": self._normalize_resource_plan(raw.get("resource_plan"), business_domain),
            "execution_order": self._normalize_execution_order(raw.get("execution_order"), business_domain, requirement_text),
            "blockers": self._normalize_blockers(raw.get("blockers")),
            "pending_confirmations": self._normalize_pending_confirmations(raw.get("pending_confirmations")),
            "release_checklist": self._normalize_release_checklist(raw.get("release_checklist")),
            "quality_gate": self._normalize_quality_gate(raw.get("quality_gate")),
            "assumptions": _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(raw.get("assumptions")) if str(x).strip()]
            ),
            "notes": _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(raw.get("notes")) if str(x).strip()]
            ),
        }

        self._apply_normalize_fallbacks(
            result=result,
            requirement_text=requirement_text,
            analysis_result=analysis_result,
            testcase_result=testcase_result,
            impact_data=impact_data,
            risk_data=risk_data,
            scope_data=scope_data,
            context_meta=context_meta,
        )
        self._post_clean(result, requirement_text=requirement_text)
        return result

    def _clean_str_list(self, items: Any, business_domain: str, requirement_text: str = "", allow_generic: bool = False) -> List[str]:
        result = []
        seen = set()
        for x in _ensure_list(items):
            s = str(x or "").strip()
            if not s:
                continue
            if not allow_generic and not _is_text_relevant_to_domain(s, business_domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, business_domain) and _text_contains_any(s.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            key = _normalize_text_key(s)
            if key in seen:
                continue
            seen.add(key)
            result.append(s)
        return result[:10]

    def _normalize_scope_items(self, items: Any, business_domain: str, requirement_text: str) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            reason = _pick_first_str(item.get("reason"), default="")
            if not title:
                continue
            full_text = f"{title} {reason}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, business_domain) and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            priority = _pick_first_str(item.get("priority"), default="")
            result.append({
                "title": title,
                "reason": reason,
                "priority": _normalize_priority(priority, default="P2") if priority else "P2",
                "related_modules": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("related_modules")) if str(x).strip()]
                ),
                "related_flows": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("related_flows")) if str(x).strip()]
                ),
                "test_types": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("test_types")) if str(x).strip()]
                ),
                "owner": _pick_first_str(item.get("owner"), default=""),
            })
        result.sort(key=lambda x: (_priority_rank(x["priority"]), x["title"]))
        return result[:10]

    def _normalize_layer_items(self, items: Any, business_domain: str, requirement_text: str) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            reason = _pick_first_str(item.get("reason"), default="")
            if not title:
                continue
            full_text = f"{title} {reason}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, business_domain) and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            priority = _pick_first_str(item.get("priority"), default="")
            result.append({
                "title": title,
                "reason": reason,
                "related_scope": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("related_scope")) if str(x).strip()]
                ),
                "related_risks": _clean_related_risks(
                    [str(x).strip() for x in _ensure_list(item.get("related_risks")) if str(x).strip()],
                    business_domain,
                    requirement_text=requirement_text,
                ),
                "priority": _normalize_priority(priority, default="P1") if priority else "P1",
            })
        result.sort(key=lambda x: (_priority_rank(x["priority"]), x["title"]))
        return result[:10]

    def _normalize_test_layer_advice(self, data: Any, business_domain: str, requirement_text: str) -> Dict[str, List[Dict[str, Any]]]:
        if not isinstance(data, dict):
            return {"ui": [], "api": [], "manual": [], "automation_candidate": []}
        return {
            "ui": self._normalize_layer_items(data.get("ui"), business_domain, requirement_text),
            "api": self._normalize_layer_items(data.get("api"), business_domain, requirement_text),
            "manual": self._normalize_layer_items(data.get("manual"), business_domain, requirement_text),
            "automation_candidate": self._normalize_layer_items(data.get("automation_candidate"), business_domain, requirement_text),
        }

    def _normalize_test_type_matrix(self, items: Any, business_domain: str, requirement_text: str) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            type_name = _normalize_test_type_name(_pick_first_str(item.get("type_name"), item.get("name")))
            if not type_name:
                continue
            scope = _dedupe_str_list([str(x).strip() for x in _ensure_list(item.get("scope")) if str(x).strip()])
            reason = _pick_first_str(item.get("reason"), default="")
            full_text = f"{type_name} {reason} {' '.join(scope)}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=requirement_text):
                if type_name not in {"功能测试", "接口测试", "回归测试", "冒烟测试", "异常流测试", "边界值测试", "数据一致性测试", "权限测试", "并发测试", "幂等测试"}:
                    continue
            if _is_asset_yield_scene(requirement_text, business_domain) and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            key = _normalize_text_key(type_name)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "type_name": type_name,
                "necessary": _normalize_bool(item.get("necessary"), True),
                "priority": _normalize_priority(item.get("priority"), default="P1"),
                "scope": scope,
                "reason": reason,
                "automation_candidate": _normalize_bool(item.get("automation_candidate"), False),
                "related_risks": _clean_related_risks(
                    [str(x).strip() for x in _ensure_list(item.get("related_risks")) if str(x).strip()],
                    business_domain,
                    requirement_text=requirement_text,
                ),
            })
        result.sort(key=lambda x: (_priority_rank(x["priority"]), x["type_name"]))
        return result[:12]

    def _normalize_environment_strategy(self, items: Any, business_domain: str) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            env_name = _pick_first_str(item.get("env_name"), item.get("name"))
            if not env_name:
                continue
            key = _normalize_text_key(env_name)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "env_name": env_name,
                "purpose": _pick_first_str(item.get("purpose"), default=""),
                "required": _normalize_bool(item.get("required"), True),
                "notes": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("notes")) if str(x).strip()]
                ),
            })
        return result[:8]

    def _normalize_test_data_strategy(self, items: Any, business_domain: str, requirement_text: str) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            purpose = _pick_first_str(item.get("purpose"), default="")
            if not title:
                continue
            full_text = f"{title} {purpose}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=requirement_text):
                if not _text_contains_any(full_text, ["账号", "角色", "权限", "订单", "状态", "资产", "账变", "流水", "收益"]):
                    continue
            if _is_asset_yield_scene(requirement_text, business_domain) and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "title": title,
                "data_type": _pick_first_str(item.get("data_type"), default=""),
                "purpose": purpose,
                "required": _normalize_bool(item.get("required"), True),
                "notes": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("notes")) if str(x).strip()]
                ),
            })
        return result[:10]

    def _normalize_automation_strategy(self, items: Any, business_domain: str, requirement_text: str) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            reason = _pick_first_str(item.get("reason"), default="")
            if not title:
                continue
            full_text = f"{title} {reason}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, business_domain) and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "title": title,
                "scope": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("scope")) if str(x).strip()]
                ),
                "priority": _normalize_priority(item.get("priority"), default="P1"),
                "reason": reason,
                "framework_hint": _pick_first_str(item.get("framework_hint"), default=""),
            })
        result.sort(key=lambda x: (_priority_rank(x["priority"]), x["title"]))
        return result[:10]

    def _normalize_regression_strategy(self, items: Any, business_domain: str, requirement_text: str) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            reason = _pick_first_str(item.get("reason"), default="")
            if not title:
                continue
            full_text = f"{title} {reason}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, business_domain) and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "title": title,
                "scope": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("scope")) if str(x).strip()]
                ),
                "reason": reason,
                "priority": _normalize_priority(item.get("priority"), default="P1"),
            })
        result.sort(key=lambda x: (_priority_rank(x["priority"]), x["title"]))
        return result[:10]

    def _normalize_release_strategy(self, items: Any) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            if not title:
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "title": title,
                "reason": _pick_first_str(item.get("reason"), default=""),
                "required": _normalize_bool(item.get("required"), False),
                "notes": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("notes")) if str(x).strip()]
                ),
            })
        return result[:8]

    def _normalize_rollback_strategy(self, items: Any) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            if not title:
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "title": title,
                "trigger": _pick_first_str(item.get("trigger"), default=""),
                "action": _pick_first_str(item.get("action"), default=""),
                "notes": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("notes")) if str(x).strip()]
                ),
            })
        return result[:8]

    def _normalize_entry_criteria(self, items: Any) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            if not title:
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "title": title,
                "required": _normalize_bool(item.get("required"), True),
                "reason": _pick_first_str(item.get("reason"), default=""),
                "owner": _pick_first_str(item.get("owner"), default=""),
            })
        return result[:10]

    def _normalize_exit_criteria(self, items: Any) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            if not title:
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "title": title,
                "required": _normalize_bool(item.get("required"), True),
                "reason": _pick_first_str(item.get("reason"), default=""),
                "owner": _pick_first_str(item.get("owner"), default=""),
            })
        return result[:10]

    def _normalize_resource_plan_items(self, items: Any, business_domain: str) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            if not title:
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "title": title,
                "scope": _dedupe_str_list([str(x).strip() for x in _ensure_list(item.get("scope")) if str(x).strip()]),
                "focus": _dedupe_str_list([str(x).strip() for x in _ensure_list(item.get("focus")) if str(x).strip()]),
                "note": _pick_first_str(item.get("note"), default=""),
            })
        return result[:8]

    def _normalize_resource_plan(self, data: Any, business_domain: str) -> Dict[str, List[Dict[str, Any]]]:
        if not isinstance(data, dict):
            return {"one_day": [], "two_days": [], "three_days": [], "five_days": []}
        return {
            "one_day": self._normalize_resource_plan_items(data.get("one_day"), business_domain),
            "two_days": self._normalize_resource_plan_items(data.get("two_days"), business_domain),
            "three_days": self._normalize_resource_plan_items(data.get("three_days"), business_domain),
            "five_days": self._normalize_resource_plan_items(data.get("five_days"), business_domain),
        }

    def _normalize_execution_order(self, items: Any, business_domain: str, requirement_text: str) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for idx, item in enumerate(_ensure_list(items), start=1):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            reason = _pick_first_str(item.get("reason"), default="")
            if not title:
                continue
            full_text = f"{title} {reason}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=requirement_text):
                if not _text_contains_any(full_text, ["高风险", "主链路", "回归", "异常", "发布"]):
                    continue
            if _is_asset_yield_scene(requirement_text, business_domain) and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            try:
                order = int(item.get("order"))
            except Exception:
                order = idx
            result.append({
                "order": order,
                "title": title,
                "reason": reason,
                "related_scope": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("related_scope")) if str(x).strip()]
                ),
                "related_risks": _clean_related_risks(
                    [str(x).strip() for x in _ensure_list(item.get("related_risks")) if str(x).strip()],
                    business_domain,
                    requirement_text=requirement_text,
                ),
                "blocking": _normalize_bool(item.get("blocking"), False),
            })
        result.sort(key=lambda x: x["order"])
        return result[:10]

    def _normalize_blockers(self, items: Any) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            if not title:
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "title": title,
                "reason": _pick_first_str(item.get("reason"), default=""),
                "owner": _pick_first_str(item.get("owner"), default=""),
                "suggestion": _pick_first_str(item.get("suggestion"), default=""),
                "severity": _pick_first_str(item.get("severity"), default=""),
            })
        return result[:10]

    def _normalize_pending_confirmations(self, items: Any) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            if not title:
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "title": title,
                "reason": _pick_first_str(item.get("reason"), default=""),
                "owner": _pick_first_str(item.get("owner"), default=""),
                "impact": _pick_first_str(item.get("impact"), default=""),
                "blocking": _normalize_bool(item.get("blocking"), False),
            })
        return result[:10]

    def _normalize_release_checklist(self, items: Any) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), item.get("name"))
            if not title:
                continue
            key = _normalize_text_key(title)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "title": title,
                "reason": _pick_first_str(item.get("reason"), default=""),
                "required": _normalize_bool(item.get("required"), True),
                "owner": _pick_first_str(item.get("owner"), default=""),
                "related_risks": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("related_risks")) if str(x).strip()]
                ),
            })
        return result[:12]

    def _normalize_quality_gate(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {
                "decision": "conditional_pass",
                "reasons": [],
                "blocker_risks": [],
                "required_actions": [],
            }

        return {
            "decision": _normalize_gate_decision(data.get("decision")),
            "reasons": _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(data.get("reasons")) if str(x).strip()]
            ),
            "blocker_risks": _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(data.get("blocker_risks")) if str(x).strip()]
            ),
            "required_actions": _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(data.get("required_actions")) if str(x).strip()]
            ),
        }

    def _apply_normalize_fallbacks(
        self,
        result: Dict[str, Any],
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]],
        testcase_result: Optional[Dict[str, Any]],
        impact_data: Optional[Dict[str, Any]],
        risk_data: Optional[Dict[str, Any]],
        scope_data: Optional[Dict[str, Any]],
        context_meta: Optional[Dict[str, Any]],
    ) -> None:
        must_titles = _extract_scope_titles(scope_data, "must_test", 8)
        should_titles = _extract_scope_titles(scope_data, "should_test", 8)
        defer_titles = _extract_scope_titles(scope_data, "defer_test", 6)
        smoke_titles = _extract_scope_titles(scope_data, "smoke_scope", 6)
        regression_titles = _extract_scope_titles(scope_data, "regression_scope", 10)
        risk_titles = _clean_related_risks(
            _extract_risk_titles(risk_data, 10),
            result["business_domain"],
            requirement_text=requirement_text,
            limit=10,
        )
        impact_modules = _extract_impact_modules(impact_data, 10)
        impact_flows = _extract_impact_flows(impact_data, 10)
        impact_roles = _extract_impact_roles(impact_data, 6)

        asset_yield_scene = _is_asset_yield_scene(requirement_text, result["business_domain"])

        if not result["core_reason"]:
            core_reason = []
            if risk_titles:
                core_reason.append("本次策略优先围绕高风险项进行测试投入和发布决策。")
            if smoke_titles or must_titles:
                core_reason.append("核心主链路和冒烟范围是本次测试执行的第一优先级。")
            if regression_titles:
                core_reason.append("存在回归范围，需对受影响链路做定向回归。")
            if asset_yield_scene:
                core_reason.append("当前需求主要聚焦收益展示、收益口径与数据生成时序，不应引入交易链路验证。")
            result["core_reason"] = _dedupe_str_list(core_reason)

        if not result["test_objectives"]:
            result["test_objectives"] = [
                "识别本次变更影响范围与高风险链路",
                "确保核心主流程、关键异常流和高风险联动场景得到覆盖",
                "为回归、自动化与上线决策提供可执行策略依据",
            ]

        if not result["out_of_scope"] and defer_titles:
            result["out_of_scope"] = [
                {
                    "title": x,
                    "reason": "当前资源优先投入核心主链路和高风险范围，该项可延后执行。",
                    "priority": "P2",
                    "related_modules": [],
                    "related_flows": [],
                    "test_types": ["回归测试"],
                    "owner": "",
                }
                for x in defer_titles[:5]
            ]

        if not result["test_layer_advice"]["ui"]:
            ui_reason = "主链路最能直接反映用户操作与页面结果，适合作为首轮验证重点。"
            if asset_yield_scene:
                ui_reason = "收益概览、收益详情、周期切换和说明文案等页面表现需优先验证展示正确性与一致性。"
            result["test_layer_advice"]["ui"] = [
                {
                    "title": "优先覆盖核心业务主链路 UI 验证",
                    "reason": ui_reason,
                    "related_scope": smoke_titles or must_titles[:4],
                    "related_risks": risk_titles[:4],
                    "priority": "P0",
                }
            ]

        if not result["test_layer_advice"]["api"]:
            api_reason = "高风险业务规则、状态判断和资格逻辑更适合在 API 层快速验证。"
            if asset_yield_scene:
                api_reason = "收益口径、汇总规则、数据生成接口与概览/详情接口一致性更适合在 API 层快速验证。"
            result["test_layer_advice"]["api"] = [
                {
                    "title": "优先覆盖高风险规则与状态流转校验",
                    "reason": api_reason,
                    "related_scope": must_titles[:6],
                    "related_risks": risk_titles[:6],
                    "priority": "P1",
                }
            ]

        if not result["test_layer_advice"]["manual"]:
            manual_reason = "复杂提示、边界表现、联动异常和交互细节更适合人工探索发现问题。"
            if asset_yield_scene:
                manual_reason = "收益展示边界、说明文案、空数据、跨周期和刷新时序类问题更适合人工探索验证。"
            result["test_layer_advice"]["manual"] = [
                {
                    "title": "补充异常交互与探索性验证",
                    "reason": manual_reason,
                    "related_scope": regression_titles[:6] or should_titles[:6],
                    "related_risks": risk_titles[:4],
                    "priority": "P1",
                }
            ]

        if not result["test_layer_advice"]["automation_candidate"]:
            auto_reason = "重复执行频率高、路径稳定的主链路适合后续沉淀自动化资产。"
            if asset_yield_scene:
                auto_reason = "收益概览/详情接口校验、周期切换和固定口径校验适合沉淀自动化回归资产。"
            result["test_layer_advice"]["automation_candidate"] = [
                {
                    "title": "将稳定高频主链路纳入自动化候选",
                    "reason": auto_reason,
                    "related_scope": smoke_titles[:4] or must_titles[:4],
                    "related_risks": [],
                    "priority": "P1",
                }
            ]

        if not result["test_type_matrix"]:
            matrix = [
                {
                    "type_name": "功能测试",
                    "necessary": True,
                    "priority": "P0",
                    "scope": smoke_titles[:4] or must_titles[:4],
                    "reason": "核心主流程必须首先验证。",
                    "automation_candidate": False,
                    "related_risks": risk_titles[:4],
                },
                {
                    "type_name": "接口测试",
                    "necessary": True,
                    "priority": "P1",
                    "scope": must_titles[:6],
                    "reason": "规则判断、状态流转和关键业务逻辑更适合在接口层快速验证。",
                    "automation_candidate": True,
                    "related_risks": risk_titles[:6],
                },
                {
                    "type_name": "冒烟测试",
                    "necessary": True,
                    "priority": "P0",
                    "scope": smoke_titles[:6] or must_titles[:4],
                    "reason": "发布前最低保障范围。",
                    "automation_candidate": True,
                    "related_risks": risk_titles[:4],
                },
                {
                    "type_name": "回归测试",
                    "necessary": True,
                    "priority": "P1",
                    "scope": regression_titles[:8] or should_titles[:6],
                    "reason": "受影响范围需要定向回归。",
                    "automation_candidate": True,
                    "related_risks": risk_titles[:5],
                },
                {
                    "type_name": "异常流测试",
                    "necessary": True,
                    "priority": "P1",
                    "scope": must_titles[:6],
                    "reason": "高价值异常分支容易造成线上风险。",
                    "automation_candidate": False,
                    "related_risks": risk_titles[:6],
                },
                {
                    "type_name": "边界值测试",
                    "necessary": True,
                    "priority": "P1",
                    "scope": should_titles[:6] or must_titles[:4],
                    "reason": "边界条件通常是规则缺陷高发区。",
                    "automation_candidate": False,
                    "related_risks": risk_titles[:4],
                },
            ]

            if _text_contains_any(requirement_text, ["资金", "提现", "充值", "交易", "订单", "划转", "账变", "余额", "收益", "apr", "年化"]):
                matrix.append({
                    "type_name": "数据一致性测试",
                    "necessary": True,
                    "priority": "P0",
                    "scope": must_titles[:6] or impact_flows[:6],
                    "reason": "涉及资产、订单或账变时，必须确认前后状态与数据一致。",
                    "automation_candidate": False,
                    "related_risks": risk_titles[:6],
                })

            if _text_contains_any(requirement_text, ["风控", "权限", "审核", "资格"]):
                matrix.append({
                    "type_name": "权限测试",
                    "necessary": True,
                    "priority": "P1",
                    "scope": must_titles[:5],
                    "reason": "权限、资格和风控条件容易出现越权或误拦截问题。",
                    "automation_candidate": False,
                    "related_risks": risk_titles[:5],
                })

            if _text_contains_any(requirement_text, ["并发", "重复提交", "幂等", "回调", "异步"]):
                matrix.append({
                    "type_name": "并发测试",
                    "necessary": True,
                    "priority": "P1",
                    "scope": must_titles[:4],
                    "reason": "涉及并发或异步处理时，需要验证竞态和重复执行风险。",
                    "automation_candidate": False,
                    "related_risks": risk_titles[:4],
                })
                matrix.append({
                    "type_name": "幂等测试",
                    "necessary": True,
                    "priority": "P1",
                    "scope": must_titles[:4],
                    "reason": "需要验证重复请求或重试场景不会导致结果重复生效。",
                    "automation_candidate": False,
                    "related_risks": risk_titles[:4],
                })

            result["test_type_matrix"] = matrix

        if not result["environment_strategy"]:
            envs = [
                {
                    "env_name": "测试环境",
                    "purpose": "完成功能、异常流、主链路和联动验证",
                    "required": True,
                    "notes": [],
                }
            ]
            if _text_contains_any(requirement_text, ["联调", "第三方", "回调", "异步", "外部系统", "t+1", "定时任务", "数据生成"]):
                envs.append({
                    "env_name": "联调环境",
                    "purpose": "验证第三方依赖、异步链路、定时任务和回调处理",
                    "required": True,
                    "notes": ["若联调环境不可用，需明确 Mock 或替代验证方案。"],
                })
            result["environment_strategy"] = envs

        if not result["test_data_strategy"]:
            data_items = [
                {
                    "title": "基础账号与权限数据准备",
                    "data_type": "账号/角色/权限",
                    "purpose": "覆盖核心角色、权限边界和主链路验证",
                    "required": True,
                    "notes": [],
                }
            ]
            if _text_contains_any(requirement_text, ["订单", "交易", "下单", "撤单", "撮合"]) and not asset_yield_scene:
                data_items.append({
                    "title": "订单与状态流转数据准备",
                    "data_type": "订单/状态",
                    "purpose": "覆盖主流程、异常流和状态切换验证",
                    "required": True,
                    "notes": ["需覆盖正常、异常、取消、失败、部分完成等状态。"],
                })
            if _text_contains_any(requirement_text, ["资金", "提现", "充值", "划转", "账变", "余额", "收益", "apr", "年化"]):
                data_items.append({
                    "title": "资产与账变数据准备",
                    "data_type": "资产/账变/流水",
                    "purpose": "验证金额变化、冻结解冻、一致性和回滚场景",
                    "required": True,
                    "notes": ["建议准备不同余额、限额和风控状态的账号。"],
                })
            if asset_yield_scene:
                data_items.append({
                    "title": "收益口径与周期数据准备",
                    "data_type": "收益/APR/周期",
                    "purpose": "验证收益概览、详情、7/30/90 天周期、加息券与 T+1 数据生成场景",
                    "required": True,
                    "notes": ["建议准备有券/无券、空数据/有数据、刚生成/未生成、跨周期账号。"],
                })
            result["test_data_strategy"] = data_items

        if not result["automation_strategy"]:
            auto_scope = smoke_titles[:4] or must_titles[:4]
            auto_reason = "主链路重复验证频率高，适合沉淀自动化资产降低回归成本。"
            auto_hint = "优先接口自动化，其次冒烟级 UI 自动化"
            if asset_yield_scene:
                auto_reason = "收益概览、收益详情、周期切换与接口口径校验重复执行频率高，适合沉淀自动化回归资产。"
                auto_hint = "优先接口自动化与页面口径校验自动化"
            result["automation_strategy"] = [
                {
                    "title": "将稳定高频主链路纳入自动化回归",
                    "scope": auto_scope,
                    "priority": "P1",
                    "reason": auto_reason,
                    "framework_hint": auto_hint,
                }
            ]

        if not result["regression_strategy"]:
            reg_scope = regression_titles[:8] or (must_titles[:6] + should_titles[:4])[:8]
            reg_reason = "基于影响面和高风险项做收敛式回归，避免范围失焦。"
            if asset_yield_scene:
                reg_reason = "基于收益展示影响面做定向回归，重点覆盖概览、详情、周期切换、收益生成和历史收益场景。"
            result["regression_strategy"] = [
                {
                    "title": "受影响模块定向回归",
                    "scope": reg_scope,
                    "reason": reg_reason,
                    "priority": "P1",
                }
            ]

        if not result["release_strategy"]:
            release_items = [
                {
                    "title": "核心链路通过后再考虑发布",
                    "reason": "主链路是发布最低保障。",
                    "required": True,
                    "notes": [],
                }
            ]
            if _text_contains_any(requirement_text, ["灰度", "开关", "AB", "A/B"]):
                release_items.append({
                    "title": "建议采用灰度或开关控制发布",
                    "reason": "降低大范围直接放量带来的未知风险。",
                    "required": False,
                    "notes": ["需确认命中规则、关闭策略和回滚路径。"],
                })
            result["release_strategy"] = release_items

        if not result["rollback_strategy"]:
            rollback_notes = ["若涉及数据写入，需同时确认数据恢复或补偿方案。"]
            if asset_yield_scene:
                rollback_notes = ["若涉及收益数据写入或缓存刷新，需同步确认数据恢复、重刷或补偿方案。"]
            result["rollback_strategy"] = [
                {
                    "title": "预留快速回滚方案",
                    "trigger": "核心主链路失败、高风险缺陷复现或监控异常",
                    "action": "关闭开关、回退版本或停用新链路",
                    "notes": rollback_notes,
                }
            ]

        if not result["entry_criteria"]:
            result["entry_criteria"] = [
                {
                    "title": "需求说明已明确且可供测试理解",
                    "required": True,
                    "reason": "避免测试目标和预期判断失真",
                    "owner": "产品",
                },
                {
                    "title": "测试环境与关键依赖可用",
                    "required": True,
                    "reason": "保证核心链路能够真实执行",
                    "owner": "研发/测试",
                },
                {
                    "title": "测试账号和测试数据已准备完成",
                    "required": True,
                    "reason": "确保主链路、异常流和边界值场景可覆盖",
                    "owner": "测试",
                },
            ]

        if not result["exit_criteria"]:
            result["exit_criteria"] = [
                {
                    "title": "核心主流程验证通过",
                    "required": True,
                    "reason": "主链路必须作为最低上线保障",
                    "owner": "测试",
                },
                {
                    "title": "阻塞级缺陷为 0",
                    "required": True,
                    "reason": "阻塞问题不应带入线上",
                    "owner": "测试/研发",
                },
                {
                    "title": "高风险场景已完成验证并有明确结论",
                    "required": True,
                    "reason": "确保关键上线风险可控",
                    "owner": "测试",
                },
            ]

        if not result["resource_plan"]["one_day"]:
            result["resource_plan"] = {
                "one_day": [
                    {
                        "title": "1 人天方案：只保主链路与高风险冒烟",
                        "scope": smoke_titles[:5] or must_titles[:5],
                        "focus": risk_titles[:4] or impact_flows[:4],
                        "note": "时间有限时，优先确保发布阻断风险可被尽早发现。",
                    }
                ],
                "two_days": [
                    {
                        "title": "2 人天方案：主链路 + 关键异常 + 状态流转",
                        "scope": (must_titles[:6] + should_titles[:3])[:8],
                        "focus": risk_titles[:5] or impact_modules[:5],
                        "note": "建议补充高风险异常分支与权限/状态切换场景。",
                    }
                ],
                "three_days": [
                    {
                        "title": "3 人天方案：重点回归 + 联动范围验证",
                        "scope": regression_titles[:10] or (must_titles[:6] + should_titles[:6])[:10],
                        "focus": risk_titles[:6] or impact_modules[:6],
                        "note": "在核心链路稳定的基础上，扩大到受影响模块和历史能力回归。",
                    }
                ],
                "five_days": [
                    {
                        "title": "5 人天方案：系统化覆盖主链路、异常流、回归与专项验证",
                        "scope": (must_titles[:8] + should_titles[:8] + regression_titles[:8])[:15],
                        "focus": risk_titles[:8] or impact_modules[:8],
                        "note": "适合在完整回归窗口内补齐专项验证与自动化候选梳理。",
                    }
                ],
            }
            if asset_yield_scene:
                result["resource_plan"]["two_days"][0]["note"] = "建议补充收益口径、概览与详情一致性、多周期切换和 T+1 数据生成场景。"
                result["resource_plan"]["three_days"][0]["note"] = "在核心展示稳定基础上，扩大到历史收益、加息券纳入、跨端展示和缓存刷新回归。"

        if not result["execution_order"]:
            step2_reason = "主链路通过后，应优先覆盖高价值异常场景。"
            if asset_yield_scene:
                step2_reason = "主链路通过后，应优先覆盖收益口径、空数据、周期切换、T+1 生成与刷新时序等异常场景。"
            result["execution_order"] = [
                {
                    "order": 1,
                    "title": "优先执行高风险主链路验证",
                    "reason": "先暴露会阻断发布或影响核心业务的严重问题。",
                    "related_scope": smoke_titles[:4] or must_titles[:4],
                    "related_risks": risk_titles[:4],
                    "blocking": True,
                },
                {
                    "order": 2,
                    "title": "执行关键异常分支与状态/权限验证",
                    "reason": step2_reason,
                    "related_scope": must_titles[2:7] or should_titles[:5],
                    "related_risks": risk_titles[:5],
                    "blocking": False,
                },
                {
                    "order": 3,
                    "title": "执行受影响范围定向回归",
                    "reason": "用于确认改动没有破坏历史能力和受影响链路。",
                    "related_scope": regression_titles[:8] or should_titles[:6],
                    "related_risks": risk_titles[:4],
                    "blocking": False,
                },
            ]

        if not result["blockers"] and _text_contains_any(requirement_text, ["第三方", "回调", "联调", "T+1", "定时任务", "数据生成"]):
            result["blockers"] = [
                {
                    "title": "关键依赖或数据触发条件未就绪",
                    "reason": "若关键依赖不可用，将直接影响核心链路验证结论。",
                    "owner": "研发/后端",
                    "suggestion": "提供可触发、可回放或可 Mock 的替代方案。",
                    "severity": "high",
                }
            ]

        if not result["pending_confirmations"]:
            pending = []
            if _text_contains_any(requirement_text, ["规则", "口径", "兼容", "历史数据", "加息券", "灰度"]):
                pending.append({
                    "title": "部分规则口径或兼容性边界待确认",
                    "reason": "未确认的规则边界会直接影响测试结论和上线风险评估。",
                    "owner": "产品/业务",
                    "impact": "可能导致验证范围返工或发布决策变化。",
                    "blocking": False,
                })
            result["pending_confirmations"] = pending

        if not result["release_checklist"]:
            result["release_checklist"] = [
                {
                    "title": "核心链路验证结果已确认",
                    "reason": "避免关键功能带病上线",
                    "required": True,
                    "owner": "测试",
                    "related_risks": risk_titles[:3],
                },
                {
                    "title": "高风险项已有测试结论或豁免说明",
                    "reason": "确保上线风险可控且可追溯",
                    "required": True,
                    "owner": "测试/产品",
                    "related_risks": risk_titles[:5],
                },
            ]

        if not result["quality_gate"] or (
            not result["quality_gate"].get("reasons") and not result["quality_gate"].get("required_actions")
        ):
            blockers = result.get("blockers") or []
            raw_risk_items = _ensure_list((risk_data or {}).get("risk_items"))
            filtered_risk_items = []
            for x in raw_risk_items:
                if not isinstance(x, dict):
                    continue
                title = _pick_first_str(x.get("title"), default="")
                text = f"{title} {_pick_first_str(x.get('category'))} {_pick_first_str(x.get('reason'))}"
                if not _is_text_relevant_to_domain(text, result["business_domain"], requirement_text=requirement_text):
                    continue
                if asset_yield_scene and _text_contains_any(text.lower(), _TRADING_ONLY_KEYWORDS):
                    continue
                filtered_risk_items.append(x)

            p0_titles = [
                _pick_first_str(x.get("title"), default="")
                for x in filtered_risk_items
                if _normalize_priority(x.get("level"), default="P2") == "P0"
            ]
            p0_titles = [x for x in p0_titles if x]

            p1_titles = [
                _pick_first_str(x.get("title"), default="")
                for x in filtered_risk_items
                if _normalize_priority(x.get("level"), default="P2") == "P1"
            ]
            p1_titles = [x for x in p1_titles if x]

            if blockers or p0_titles:
                result["quality_gate"] = {
                    "decision": "fail",
                    "reasons": ["存在阻塞项或阻塞级风险，当前不建议直接放行。"],
                    "blocker_risks": _dedupe_str_list(
                        p0_titles + [x.get("title", "") for x in blockers if isinstance(x, dict)]
                    ),
                    "required_actions": ["阻塞项清零后重新评估上线结论。"],
                }
            elif p1_titles:
                result["quality_gate"] = {
                    "decision": "conditional_pass",
                    "reasons": ["存在高风险项，建议补充验证并确认回归结论后放行。"],
                    "blocker_risks": p1_titles[:10],
                    "required_actions": ["完成高风险项验证", "确认回归范围已覆盖"],
                }
            else:
                result["quality_gate"] = {
                    "decision": "pass",
                    "reasons": ["当前未识别阻塞级风险，核心策略可执行。"],
                    "blocker_risks": [],
                    "required_actions": [],
                }

        if not result["assumptions"]:
            assumptions = []
            has_analysis = bool((context_meta or {}).get("has_analysis_result"))
            has_testcase = bool((context_meta or {}).get("has_testcase_result"))
            if not has_analysis:
                assumptions.append("当前未复用完整需求分析结果，部分策略依据需求原文和局部结构化结果推断。")
            if not has_testcase:
                assumptions.append("当前未复用完整测试用例结果，自动化和回归建议以风险和影响范围为主。")
            result["assumptions"] = _dedupe_str_list(assumptions)

        if not result["notes"]:
            notes = []
            if impact_modules or impact_flows:
                notes.append("建议优先围绕受影响模块与流程执行，避免回归范围失焦。")
            if impact_roles:
                notes.append("涉及角色差异时，建议明确不同角色、权限和状态下的验证结论。")
            if testcase_result:
                notes.append("若已有稳定测试资产，建议优先复用并在此基础上补齐高风险缺口。")
            if asset_yield_scene:
                notes.append("当前需求聚焦收益展示与收益口径，测试策略不应引入交易主链路范围。")
            result["notes"] = _dedupe_str_list(notes)

    def _post_clean(self, result: Dict[str, Any], requirement_text: str) -> None:
        business_domain = _normalize_business_domain(result.get("business_domain"), requirement_text=requirement_text)
        asset_yield_scene = _is_asset_yield_scene(requirement_text, business_domain)

        result["business_domain"] = business_domain

        def _clean_text_items(items: List[str], allow_generic: bool = False, limit: int = 10) -> List[str]:
            cleaned = []
            seen = set()
            for x in _ensure_list(items):
                s = str(x or "").strip()
                if not s:
                    continue
                if not allow_generic and not _is_text_relevant_to_domain(s, business_domain, requirement_text=requirement_text):
                    continue
                if asset_yield_scene and _text_contains_any(s.lower(), _TRADING_ONLY_KEYWORDS):
                    continue
                key = _normalize_text_key(s)
                if key in seen:
                    continue
                seen.add(key)
                cleaned.append(s)
                if len(cleaned) >= limit:
                    break
            return cleaned

        result["core_reason"] = _clean_text_items(result.get("core_reason"), allow_generic=True, limit=8)
        result["test_objectives"] = _clean_text_items(result.get("test_objectives"), allow_generic=True, limit=8)
        result["assumptions"] = _clean_text_items(result.get("assumptions"), allow_generic=True, limit=8)
        result["notes"] = _clean_text_items(result.get("notes"), allow_generic=True, limit=8)

        result["out_of_scope"] = self._normalize_scope_items(result.get("out_of_scope"), business_domain, requirement_text)
        result["test_layer_advice"] = self._normalize_test_layer_advice(result.get("test_layer_advice"), business_domain, requirement_text)
        result["test_type_matrix"] = self._normalize_test_type_matrix(result.get("test_type_matrix"), business_domain, requirement_text)
        result["test_data_strategy"] = self._normalize_test_data_strategy(result.get("test_data_strategy"), business_domain, requirement_text)
        result["automation_strategy"] = self._normalize_automation_strategy(result.get("automation_strategy"), business_domain, requirement_text)
        result["regression_strategy"] = self._normalize_regression_strategy(result.get("regression_strategy"), business_domain, requirement_text)
        result["execution_order"] = self._normalize_execution_order(result.get("execution_order"), business_domain, requirement_text)

        # release_strategy 去重
        release_seen = set()
        release_items = []
        for item in self._normalize_release_strategy(result.get("release_strategy")):
            title = _pick_first_str(item.get("title"), default="")
            key = _normalize_text_key(title)
            if key in release_seen:
                continue
            release_seen.add(key)
            release_items.append(item)
        result["release_strategy"] = release_items[:8]

        # exit_criteria 去重
        exit_seen = set()
        exit_items = []
        for item in self._normalize_exit_criteria(result.get("exit_criteria")):
            title = _pick_first_str(item.get("title"), default="")
            key = _normalize_text_key(title)
            if key in exit_seen:
                continue
            exit_seen.add(key)
            exit_items.append(item)
        result["exit_criteria"] = exit_items[:10]

        # blockers / pending / checklist 标准化
        result["blockers"] = self._normalize_blockers(result.get("blockers"))
        result["pending_confirmations"] = self._normalize_pending_confirmations(result.get("pending_confirmations"))
        result["release_checklist"] = self._normalize_release_checklist(result.get("release_checklist"))

        # quality gate 修正
        gate = self._normalize_quality_gate(result.get("quality_gate"))
        blocker_titles = _dedupe_str_list(
            [x.get("title", "") for x in _ensure_list(result.get("blockers")) if isinstance(x, dict)]
        )

        if blocker_titles and gate["decision"] != "fail":
            gate["decision"] = "fail"

        if blocker_titles and not gate["blocker_risks"]:
            gate["blocker_risks"] = blocker_titles[:10]

        if blocker_titles and not gate["reasons"]:
            gate["reasons"] = ["存在阻塞项"]

        result["quality_gate"] = gate

    def _fallback(
        self,
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]],
        testcase_result: Optional[Dict[str, Any]],
        impact_data: Optional[Dict[str, Any]],
        risk_data: Optional[Dict[str, Any]],
        scope_data: Optional[Dict[str, Any]],
        context_meta: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        business_domain = _normalize_business_domain(
            _pick_first_non_empty(
                (context_meta or {}).get("business_domain_hint"),
                (analysis_result or {}).get("business_domain"),
                (impact_data or {}).get("business_domain"),
                default="通用",
            ),
            requirement_text=requirement_text,
        )

        must_titles = _extract_scope_titles(scope_data, "must_test", 8)
        should_titles = _extract_scope_titles(scope_data, "should_test", 8)
        defer_titles = _extract_scope_titles(scope_data, "defer_test", 6)
        smoke_titles = _extract_scope_titles(scope_data, "smoke_scope", 6)
        regression_titles = _extract_scope_titles(scope_data, "regression_scope", 10)
        risk_titles = _clean_related_risks(
            _extract_risk_titles(risk_data, 10),
            business_domain,
            requirement_text=requirement_text,
            limit=10,
        )
        impact_modules = _extract_impact_modules(impact_data, 10)
        impact_flows = _extract_impact_flows(impact_data, 10)
        impact_roles = _extract_impact_roles(impact_data, 6)

        result: Dict[str, Any] = {
            "business_domain": business_domain,
            "title": f"{business_domain}测试策略分析结果",
            "objective": "识别高风险链路并给出可执行的测试策略建议",
            "core_reason": [],
            "test_objectives": [],
            "out_of_scope": [],
            "test_layer_advice": {"ui": [], "api": [], "manual": [], "automation_candidate": []},
            "test_type_matrix": [],
            "environment_strategy": [],
            "test_data_strategy": [],
            "automation_strategy": [],
            "regression_strategy": [],
            "release_strategy": [],
            "rollback_strategy": [],
            "entry_criteria": [],
            "exit_criteria": [],
            "resource_plan": {"one_day": [], "two_days": [], "three_days": [], "five_days": []},
            "execution_order": [],
            "blockers": [],
            "pending_confirmations": [],
            "release_checklist": [],
            "quality_gate": {"decision": "conditional_pass", "reasons": [], "blocker_risks": [], "required_actions": []},
            "assumptions": [],
            "notes": [],
        }

        self._apply_normalize_fallbacks(
            result=result,
            requirement_text=requirement_text,
            analysis_result=analysis_result,
            testcase_result=testcase_result,
            impact_data=impact_data,
            risk_data=risk_data,
            scope_data=scope_data,
            context_meta=context_meta,
        )
        self._post_clean(result, requirement_text=requirement_text)

        # 兜底保证
        if not result["core_reason"]:
            result["core_reason"] = [
                f"本次需求主要落在「{business_domain}」业务域。",
                "当前策略基于影响范围、风险项和测试范围裁剪结果进行收敛。",
            ]

        if not result["test_objectives"]:
            result["test_objectives"] = [
                "优先保障核心主链路正确性",
                "优先验证高风险项与关键异常流",
                "为回归和上线决策提供可执行依据",
            ]

        if not result["release_checklist"]:
            result["release_checklist"] = [
                {
                    "title": "核心链路验证结果已确认",
                    "reason": "避免关键功能带病上线",
                    "required": True,
                    "owner": "测试",
                    "related_risks": risk_titles[:3],
                }
            ]

        return result