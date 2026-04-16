#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/agents/impact_agent.py

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


def _normalize_change_scope(level: Any) -> str:
    s = str(level or "").strip()
    if s in {"大", "中", "小"}:
        return s

    s2 = s.upper()
    if s2 in {"LARGE", "HIGH"}:
        return "大"
    if s2 in {"MEDIUM", "MID"}:
        return "中"
    if s2 in {"SMALL", "LOW"}:
        return "小"

    return "中"


def _level_rank(level: Any) -> int:
    s = str(level or "").strip()
    mapping = {"高": 0, "中": 1, "低": 2}
    return mapping.get(s, 9)


def _text_contains_any(text: str, keywords: List[str]) -> bool:
    text_lower = (text or "").lower()
    return any(str(k).lower() in text_lower for k in keywords if str(k).strip())


# =====================================================
# 业务域规则
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
        "主流程", "状态流转", "详情", "列表", "查询", "审核", "结果展示", "配置开关",
        "灰度", "权限", "风控", "数据一致性", "回滚", "发布", "展示准确性", "汇总", "口径", "精度"
    ]
    if any(k.lower() in lower_text for k in generic_allow_keywords):
        return True

    return False


# =====================================================
# 提取上下文
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


# =====================================================
# Agent
# =====================================================

class ImpactAgent:
    """
    测试策略智能体 - 影响分析 Agent（企业级增强版）
    """

    def __init__(self) -> None:
        self.llm = LLM()

    async def analyze(
        self,
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]] = None,
        testcase_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        requirement_text = (requirement_text or "").strip()
        if not requirement_text:
            raise ValueError("requirement_text 不能为空")

        issue_titles = _extract_issue_titles_from_analysis_result(analysis_result)
        testcase_titles = _extract_titles_from_testcase_result(testcase_result)
        business_domain = _normalize_business_domain(
            _pick_first_non_empty(
                (analysis_result or {}).get("business_domain"),
                default="",
            ),
            requirement_text=requirement_text,
        )

        prompt = self._build_prompt(
            requirement_text=requirement_text,
            issue_titles=issue_titles,
            testcase_titles=testcase_titles,
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
                self._apply_normalize_fallbacks(
                    normalized,
                    requirement_text=requirement_text,
                    analysis_result=analysis_result,
                    testcase_result=testcase_result,
                )
                self._post_clean(normalized, requirement_text=requirement_text)
                return normalized

        result = self._fallback(
            requirement_text=requirement_text,
            analysis_result=analysis_result,
            testcase_result=testcase_result,
            business_domain=business_domain,
        )
        self._post_clean(result, requirement_text=requirement_text)
        return result

    # -------------------------------------------------
    # Prompt
    # -------------------------------------------------
    def _build_prompt(
        self,
        requirement_text: str,
        issue_titles: List[str],
        testcase_titles: List[str],
        business_domain: str,
    ) -> str:
        return f"""
你是企业级测试策略专家，请对下面需求做“影响分析”。

你的任务：
1. 识别 business_domain（业务域）
2. 识别本次需求变更影响到的模块
3. 识别受影响角色
4. 识别受影响流程
5. 判断整体变更范围（大/中/小）
6. 给出核心原因

输出要求：
1. 只能输出 JSON
2. 不要输出 markdown
3. JSON 结构必须严格如下：
{{
  "business_domain": "登录注册/现货/合约/充值/提现/划转/P2P/跟单/撮合/风控/KYC/资产/通用",
  "impact_modules": [
    {{
      "name": "模块名称",
      "reason": "影响原因",
      "level": "高/中/低",
      "direct": true,
      "upstream": false,
      "downstream": false
    }}
  ],
  "impact_roles": [
    {{
      "name": "角色名称",
      "reason": "影响原因",
      "permissions": ["权限A", "权限B"]
    }}
  ],
  "affected_flows": [
    {{
      "name": "流程名称",
      "steps": ["步骤1", "步骤2"],
      "reason": "影响原因",
      "level": "高/中/低",
      "is_core": true
    }}
  ],
  "change_scope": "大/中/小",
  "core_reason": ["原因1", "原因2"]
}}

补充要求：
- 结果必须强相关于当前需求
- 如果是资产/理财/APR/收益展示类需求，禁止输出合约开平仓、撮合、下单等无关流程
- 若涉及资金、交易、订单、资产、账变、权限、风控、审核、状态流转、并发、幂等，应提高影响敏感度
- 不要只给页面名字，要尽量识别业务模块、角色、核心流程和联动范围
- 结果要偏企业级测试落地，而不是泛泛而谈

补充上下文：
- 业务域提示：{business_domain}
- 已有需求分析问题标题：{json.dumps(issue_titles, ensure_ascii=False)}
- 已有测试用例标题：{json.dumps(testcase_titles, ensure_ascii=False)}

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
            logger.warning("[strategy.impact_agent] llm call failed", exc_info=True)
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

        business_domain = _normalize_business_domain(
            _pick_first_non_empty(raw.get("business_domain"), business_domain, default="通用"),
            requirement_text=requirement_text,
        )

        impact_modules = []
        for item in _ensure_list(raw.get("impact_modules")):
            if not isinstance(item, dict):
                continue
            name = _pick_first_str(item.get("name"), item.get("title"))
            reason = _pick_first_str(item.get("reason"), default="")
            if not name:
                continue
            if not _is_text_relevant_to_domain(f"{name} {reason}", business_domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, business_domain) and _text_contains_any(f"{name} {reason}".lower(), _TRADING_ONLY_KEYWORDS):
                continue
            impact_modules.append({
                "name": name,
                "reason": reason,
                "level": self._normalize_level(item.get("level")),
                "direct": item.get("direct") if isinstance(item.get("direct"), bool) else True,
                "upstream": item.get("upstream") if isinstance(item.get("upstream"), bool) else False,
                "downstream": item.get("downstream") if isinstance(item.get("downstream"), bool) else False,
            })

        impact_roles = []
        for item in _ensure_list(raw.get("impact_roles")):
            if not isinstance(item, dict):
                continue
            name = _pick_first_str(item.get("name"), item.get("title"))
            reason = _pick_first_str(item.get("reason"), default="")
            if not name:
                continue
            if not _is_text_relevant_to_domain(f"{name} {reason}", business_domain, requirement_text=requirement_text):
                if not any(k in name for k in ["用户", "管理员", "审核员", "运营", "客服", "风控人员", "访客"]):
                    continue
            impact_roles.append({
                "name": name,
                "reason": reason,
                "permissions": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("permissions")) if str(x).strip()]
                ),
            })

        affected_flows = []
        for item in _ensure_list(raw.get("affected_flows")):
            if not isinstance(item, dict):
                continue
            name = _pick_first_str(item.get("name"), item.get("title"))
            reason = _pick_first_str(item.get("reason"), default="")
            if not name:
                continue
            full_text = f"{name} {reason} {' '.join(str(x) for x in _ensure_list(item.get('steps')))}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, business_domain) and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue

            steps = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("steps")) if str(x).strip()]
            )
            affected_flows.append({
                "name": name,
                "steps": steps,
                "reason": reason,
                "level": self._normalize_level(item.get("level")),
                "is_core": item.get("is_core") if isinstance(item.get("is_core"), bool) else False,
            })

        change_scope = _normalize_change_scope(raw.get("change_scope"))
        core_reason = _dedupe_str_list(
            [str(x).strip() for x in _ensure_list(raw.get("core_reason")) if str(x).strip()]
        )

        impact_modules = self._dedupe_impact_modules(impact_modules)
        impact_roles = self._dedupe_impact_roles(impact_roles)
        affected_flows = self._dedupe_affected_flows(affected_flows)

        if not impact_modules and not affected_flows:
            return None

        return {
            "business_domain": business_domain,
            "impact_modules": impact_modules,
            "impact_roles": impact_roles,
            "affected_flows": affected_flows,
            "change_scope": change_scope,
            "core_reason": core_reason,
        }

    def _normalize_level(self, level: Any) -> str:
        s = str(level or "").strip()
        if s in {"高", "中", "低"}:
            return s

        s2 = s.upper()
        if s2 in {"HIGH", "CRITICAL", "P0", "P1"}:
            return "高"
        if s2 in {"MEDIUM", "P2"}:
            return "中"
        if s2 in {"LOW", "P3"}:
            return "低"
        return "中"

    def _dedupe_impact_modules(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        uniq: Dict[str, Dict[str, Any]] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            name = _pick_first_str(item.get("name"), default="")
            if not name:
                continue
            key = _normalize_text_key(name)
            if key not in uniq:
                uniq[key] = item
            else:
                old = uniq[key]
                if not old.get("reason"):
                    old["reason"] = _pick_first_str(item.get("reason"), default="")
                if _level_rank(item.get("level")) < _level_rank(old.get("level")):
                    old["level"] = self._normalize_level(item.get("level"))
                old["direct"] = bool(old.get("direct")) or bool(item.get("direct"))
                old["upstream"] = bool(old.get("upstream")) or bool(item.get("upstream"))
                old["downstream"] = bool(old.get("downstream")) or bool(item.get("downstream"))
        result = list(uniq.values())
        result.sort(key=lambda x: (_level_rank(x.get("level")), x.get("name", "")))
        return result[:12]

    def _dedupe_impact_roles(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        uniq: Dict[str, Dict[str, Any]] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            name = _pick_first_str(item.get("name"), default="")
            if not name:
                continue
            key = _normalize_text_key(name)
            if key not in uniq:
                uniq[key] = item
            else:
                old = uniq[key]
                if not old.get("reason"):
                    old["reason"] = _pick_first_str(item.get("reason"), default="")
                old["permissions"] = _dedupe_str_list(
                    old.get("permissions", []) +
                    [str(x).strip() for x in _ensure_list(item.get("permissions")) if str(x).strip()]
                )
        result = list(uniq.values())
        result.sort(key=lambda x: x.get("name", ""))
        return result[:10]

    def _dedupe_affected_flows(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        uniq: Dict[str, Dict[str, Any]] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            name = _pick_first_str(item.get("name"), default="")
            if not name:
                continue
            key = _normalize_text_key(name)
            if key not in uniq:
                uniq[key] = item
            else:
                old = uniq[key]
                if not old.get("reason"):
                    old["reason"] = _pick_first_str(item.get("reason"), default="")
                old["steps"] = _dedupe_str_list(
                    old.get("steps", []) +
                    [str(x).strip() for x in _ensure_list(item.get("steps")) if str(x).strip()]
                )
                if _level_rank(item.get("level")) < _level_rank(old.get("level")):
                    old["level"] = self._normalize_level(item.get("level"))
                old["is_core"] = bool(old.get("is_core")) or bool(item.get("is_core"))
        result = list(uniq.values())
        result.sort(key=lambda x: (_level_rank(x.get("level")), not bool(x.get("is_core")), x.get("name", "")))
        return result[:15]

    def _apply_normalize_fallbacks(
        self,
        result: Dict[str, Any],
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]],
        testcase_result: Optional[Dict[str, Any]],
    ) -> None:
        impact_modules = result.get("impact_modules") or []
        impact_roles = result.get("impact_roles") or []
        affected_flows = result.get("affected_flows") or []
        core_reason = result.get("core_reason") or []
        asset_yield_scene = _is_asset_yield_scene(requirement_text, result.get("business_domain", "通用"))

        if not affected_flows:
            default_name = "核心主流程"
            default_steps = ["进入功能", "执行操作", "结果验证"]
            default_reason = "需求至少影响核心业务操作链路。"

            if asset_yield_scene:
                default_name = "收益展示主流程"
                default_steps = ["进入理财/资产页", "读取收益数据", "展示收益概览", "查看详情结果"]
                default_reason = "当前需求主要影响收益展示与查看链路。"

            affected_flows.append({
                "name": default_name,
                "steps": default_steps,
                "reason": default_reason,
                "level": "中",
                "is_core": True,
            })
            result["affected_flows"] = affected_flows

        if not core_reason:
            reasons = []
            reasons.append(f"识别到 {len(impact_modules)} 个主要受影响模块" if impact_modules else "识别到核心业务主流程受影响")
            reasons.append(f"涉及 {len(impact_roles)} 类角色" if impact_roles else "未明确识别多角色差异")
            reasons.append(f"涉及 {len(affected_flows)} 条主要流程" if affected_flows else "以核心主流程为主")

            if asset_yield_scene:
                reasons.append("当前需求主要聚焦收益展示、收益口径和数据生成时序，不应引入交易链路影响范围。")

            if _extract_issue_titles_from_analysis_result(analysis_result):
                reasons.append("已结合需求分析问题项辅助判断影响范围")
            if _extract_titles_from_testcase_result(testcase_result):
                reasons.append("已结合测试用例标题辅助判断受影响场景")

            result["core_reason"] = _dedupe_str_list(reasons)

        if not result.get("change_scope"):
            result["change_scope"] = self._fallback_scope(
                result.get("impact_modules") or [],
                result.get("impact_roles") or [],
                result.get("affected_flows") or [],
                requirement_text,
            )

    def _post_clean(self, result: Dict[str, Any], requirement_text: str) -> None:
        domain = _normalize_business_domain(result.get("business_domain"), requirement_text=requirement_text)
        asset_yield_scene = _is_asset_yield_scene(requirement_text, domain)

        result["impact_modules"] = self._dedupe_impact_modules([
            x for x in _ensure_list(result.get("impact_modules"))
            if isinstance(x, dict)
            and _is_text_relevant_to_domain(f"{x.get('name', '')} {x.get('reason', '')}", domain, requirement_text=requirement_text)
            and not (asset_yield_scene and _text_contains_any(f"{x.get('name', '')} {x.get('reason', '')}".lower(), _TRADING_ONLY_KEYWORDS))
        ])

        result["impact_roles"] = self._dedupe_impact_roles([
            x for x in _ensure_list(result.get("impact_roles"))
            if isinstance(x, dict)
        ])

        result["affected_flows"] = self._dedupe_affected_flows([
            x for x in _ensure_list(result.get("affected_flows"))
            if isinstance(x, dict)
            and _is_text_relevant_to_domain(
                f"{x.get('name', '')} {x.get('reason', '')} {' '.join(_ensure_list(x.get('steps')))}",
                domain,
                requirement_text=requirement_text,
            )
            and not (
                asset_yield_scene and
                _text_contains_any(
                    f"{x.get('name', '')} {x.get('reason', '')} {' '.join(_ensure_list(x.get('steps')))}".lower(),
                    _TRADING_ONLY_KEYWORDS,
                )
            )
        ])

        result["change_scope"] = self._fallback_scope(
            result.get("impact_modules") or [],
            result.get("impact_roles") or [],
            result.get("affected_flows") or [],
            requirement_text,
        )

        result["core_reason"] = _dedupe_str_list(
            [str(x).strip() for x in _ensure_list(result.get("core_reason")) if str(x).strip()]
        )[:8]

    # -------------------------------------------------
    # Fallback
    # -------------------------------------------------
    def _fallback(
        self,
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]],
        testcase_result: Optional[Dict[str, Any]],
        business_domain: str,
    ) -> Dict[str, Any]:
        text = requirement_text or ""
        business_domain = _normalize_business_domain(business_domain, requirement_text=text)

        impact_modules = self._fallback_modules(text, business_domain)
        impact_roles = self._fallback_roles(text, business_domain)
        affected_flows = self._fallback_flows(text, business_domain)
        change_scope = self._fallback_scope(impact_modules, impact_roles, affected_flows, text)

        core_reason = [
            f"识别到 {len(impact_modules)} 个主要受影响模块" if impact_modules else "识别到核心业务主流程受影响",
            f"涉及 {len(impact_roles)} 类角色" if impact_roles else "未明确识别多角色差异",
            f"涉及 {len(affected_flows)} 条主要流程" if affected_flows else "以核心主流程为主",
        ]

        if _is_asset_yield_scene(text, business_domain):
            core_reason.append("当前需求主要聚焦收益展示、收益口径和数据生成时序，不应引入交易链路影响范围。")

        issue_titles = _extract_issue_titles_from_analysis_result(analysis_result)
        testcase_titles = _extract_titles_from_testcase_result(testcase_result)

        if issue_titles:
            core_reason.append("已结合需求分析问题项辅助判断影响范围")
        if testcase_titles:
            core_reason.append("已结合测试用例标题辅助判断受影响场景")

        return {
            "business_domain": business_domain,
            "impact_modules": self._dedupe_impact_modules(impact_modules),
            "impact_roles": self._dedupe_impact_roles(impact_roles),
            "affected_flows": self._dedupe_affected_flows(affected_flows),
            "change_scope": change_scope,
            "core_reason": _dedupe_str_list(core_reason),
        }

    def _fallback_modules(self, text: str, business_domain: str) -> List[Dict[str, Any]]:
        modules: List[Dict[str, Any]] = []
        asset_yield_scene = _is_asset_yield_scene(text, business_domain)

        def add_module(
            name: str,
            reason: str,
            level: str = "中",
            direct: bool = True,
            upstream: bool = False,
            downstream: bool = False,
        ) -> None:
            full_text = f"{name} {reason}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=text):
                if name not in {"核心业务流程", "鉴权", "权限", "风控", "订单", "资产", "理财"}:
                    return
            if asset_yield_scene and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                return
            modules.append({
                "name": name,
                "reason": reason,
                "level": level,
                "direct": direct,
                "upstream": upstream,
                "downstream": downstream,
            })

        keyword_modules = [
            ("入口", "需求描述中涉及入口或页面进入能力。"),
            ("表单", "需求涉及输入、填写、校验或提交。"),
            ("列表", "需求涉及列表展示、记录查看或结果聚合。"),
            ("详情", "需求涉及明细查看或详情页展示。"),
            ("搜索", "需求涉及查询条件或搜索能力。"),
            ("筛选", "需求涉及筛选条件变化。"),
            ("分页", "需求涉及分页翻页和结果切换。"),
            ("提交", "需求涉及提交动作或结果回写。"),
            ("审核", "需求涉及审核流程或审核结果。"),
            ("状态展示", "需求涉及状态字段或结果展示。"),
            ("记录查询", "需求涉及历史记录或流水查询。"),
            ("消息通知", "需求涉及消息、通知或提醒反馈。"),
            ("配置开关", "需求涉及配置、开关或灰度控制。"),
            ("上传", "需求涉及上传能力。"),
            ("下载", "需求涉及下载能力。"),
            ("导出", "需求涉及导出能力。"),
            ("导入", "需求涉及导入能力。"),
        ]

        for name, reason in keyword_modules:
            if name in text:
                add_module(name=name, reason=reason, level="中")

        if business_domain == "登录注册":
            add_module("登录注册", "需求属于登录注册域，核心入口能力受影响。", level="高")
            add_module("鉴权", "登录态、验证码、权限或认证逻辑通常与鉴权能力联动。", level="高", downstream=True)

        elif business_domain == "提现":
            add_module("提现", "需求属于提现域，提现主能力受影响。", level="高")
            add_module("资产", "提现通常与余额、冻结、账变联动。", level="高", downstream=True)
            add_module("风控", "提现通常涉及限额、风控、资格或审核逻辑。", level="高", upstream=True)

        elif business_domain == "充值":
            add_module("充值", "需求属于充值域，充值到账链路受影响。", level="高")
            add_module("资产", "充值与到账、入账和流水通常联动。", level="高", downstream=True)

        elif business_domain == "划转":
            add_module("划转", "需求属于划转域，账户间资产流转受影响。", level="高")
            add_module("资产", "划转会直接影响账户资产与流水。", level="高", downstream=True)

        elif business_domain == "现货":
            add_module("现货", "需求属于现货交易域，现货核心能力受影响。", level="高")
            add_module("订单", "现货通常与下单、撤单、成交和状态流转联动。", level="高", downstream=True)
            add_module("资产", "现货交易会影响可用余额和冻结资产。", level="高", downstream=True)

        elif business_domain == "合约":
            add_module("合约", "需求属于合约交易域，合约核心能力受影响。", level="高")
            add_module("订单", "合约通常与委托、成交、仓位变化联动。", level="高", downstream=True)
            add_module("资产", "合约与保证金、资产结果联动。", level="高", downstream=True)

        elif business_domain == "撮合":
            add_module("撮合", "需求属于撮合域，撮合链路受影响。", level="高")
            add_module("订单", "撮合与订单状态回写直接相关。", level="高", downstream=True)
            add_module("资产", "撮合结果通常影响资产变化。", level="高", downstream=True)

        elif business_domain == "风控":
            add_module("风控", "需求属于风控域，风控规则和限制能力受影响。", level="高")
            add_module("权限", "风控规则常与资格/权限判断联动。", level="中", downstream=True)

        elif business_domain == "KYC":
            add_module("KYC", "需求属于 KYC/认证域，资格校验相关能力受影响。", level="高")
            add_module("权限", "认证状态通常影响访问资格和能力开放。", level="中", downstream=True)

        elif business_domain == "资产":
            add_module("资产", "需求属于资产域，余额/流水/账变能力受影响。", level="高")
            if _text_contains_any(text.lower(), ["apr", "年化", "收益", "理财", "earn"]):
                add_module("理财", "需求涉及收益、APR 或理财展示能力。", level="高", downstream=True)
                add_module("收益展示", "需求涉及收益概览、历史收益或收益详情展示能力。", level="高", downstream=True)
                add_module("收益计算", "需求涉及收益口径、收益汇总或收益明细计算能力。", level="高", downstream=True)
                if _text_contains_any(text.lower(), ["加息券"]):
                    add_module("加息券", "需求涉及加息券收益纳入展示能力。", level="中", downstream=True)
                if _text_contains_any(text.lower(), ["t+1", "数据生成", "派息"]):
                    add_module("收益生成", "需求涉及收益数据生成、派息或 T+1 时序能力。", level="高", upstream=True)

        elif business_domain == "P2P":
            add_module("P2P", "需求属于 P2P 域，广告、订单或申诉能力可能受影响。", level="高")
            add_module("订单", "P2P 订单状态和流程通常联动。", level="高", downstream=True)

        elif business_domain == "跟单":
            add_module("跟单", "需求属于跟单域，跟随链路和结果回写能力受影响。", level="高")
            add_module("订单", "跟单通常涉及订单结果联动。", level="高", downstream=True)
            add_module("资产", "跟单结果通常影响资产变化。", level="高", downstream=True)

        if _text_contains_any(text, ["权限", "角色", "鉴权", "登录态"]):
            add_module("权限", "需求涉及权限、角色或鉴权逻辑。", level="高")
        if _text_contains_any(text, ["风控", "黑名单", "白名单", "限额", "频控"]):
            add_module("风控", "需求涉及风控规则、限额或限制能力。", level="高")
        if not asset_yield_scene and _text_contains_any(text, ["订单", "下单", "撤单", "成交"]):
            add_module("订单", "需求涉及订单创建、流转、成交或撤销。", level="高")
        if _text_contains_any(text, ["余额", "账变", "流水", "冻结", "解冻", "金额", "收益", "apr", "年化"]):
            add_module("资产", "需求涉及金额、余额、账变、收益或资产状态变化。", level="高")

        if not modules:
            add_module("核心业务流程", "根据需求原文推断核心业务链路受影响。", level="中")

        return modules

    def _fallback_roles(self, text: str, business_domain: str) -> List[Dict[str, Any]]:
        roles: List[Dict[str, Any]] = []

        def add_role(name: str, reason: str, permissions: Optional[List[str]] = None) -> None:
            roles.append({
                "name": name,
                "reason": reason,
                "permissions": _dedupe_str_list(permissions or []),
            })

        role_candidates = [
            ("用户", "需求可能直接影响普通用户操作路径。"),
            ("管理员", "需求可能影响管理后台或配置操作。"),
            ("审核员", "需求可能涉及审核处理逻辑。"),
            ("运营", "需求可能影响运营配置、查看或处理能力。"),
            ("客服", "需求可能影响人工处理、查询或申诉路径。"),
            ("商户", "需求可能影响商户侧操作或结果。"),
            ("访客", "需求可能影响未登录或弱登录态用户路径。"),
            ("新用户", "需求可能影响新用户首次使用路径。"),
            ("老用户", "需求可能影响已有用户习惯路径。"),
            ("风控人员", "需求可能影响风控审核和策略使用。"),
        ]

        for role, reason in role_candidates:
            if role in text:
                add_role(role, reason)

        if business_domain == "登录注册":
            add_role("用户", "登录注册流程通常直接影响普通用户。", ["登录", "注册", "找回密码"])
            add_role("访客", "未登录访客通常是登录注册流程的起点角色。", ["访问入口"])

        elif business_domain == "提现":
            add_role("用户", "提现流程通常直接影响发起提现的用户。", ["发起提现", "查看提现状态"])
            add_role("审核员", "提现常涉及人工或系统审核。", ["审核提现", "查看审核结果"])
            add_role("风控人员", "提现通常涉及风控审核和限制判断。", ["风控审核"])

        elif business_domain == "充值":
            add_role("用户", "充值流程通常直接影响充值用户。", ["查看充值结果", "查看到账状态"])

        elif business_domain == "现货":
            add_role("用户", "现货交易流程通常直接影响下单用户。", ["下单", "撤单", "查看订单"])

        elif business_domain == "合约":
            add_role("用户", "合约交易流程通常直接影响交易用户。", ["开仓", "平仓", "查看仓位"])

        elif business_domain == "风控":
            add_role("风控人员", "风控规则和审核流程通常直接影响风控角色。", ["查看规则", "执行审核"])
            add_role("用户", "风控结果会影响普通用户操作结果。", ["提交操作", "查看拦截结果"])

        elif business_domain == "资产":
            if _is_asset_yield_scene(text, business_domain):
                add_role("用户", "收益展示类需求通常直接影响用户查看收益、概览和详情。", ["查看收益概览", "查看收益详情"])
                add_role("运营", "收益口径、加息券或活动收益展示调整可能影响运营配置与核对。", ["查看活动配置", "核对收益展示"])
            else:
                add_role("用户", "资产和收益展示类需求通常直接影响普通用户查看和操作。", ["查看资产", "查看收益"])

        if _text_contains_any(text, ["权限", "角色", "审核", "审批"]):
            add_role("管理员", "需求涉及权限或审核规则，可能影响管理角色。", ["配置权限", "审批处理"])

        return self._dedupe_impact_roles(roles)

    def _fallback_flows(self, text: str, business_domain: str) -> List[Dict[str, Any]]:
        flows: List[Dict[str, Any]] = []
        asset_yield_scene = _is_asset_yield_scene(text, business_domain)

        def add_flow(
            name: str,
            steps: List[str],
            reason: str,
            level: str = "中",
            is_core: bool = False,
        ) -> None:
            full_text = f"{name} {reason} {' '.join(steps)}"
            if not _is_text_relevant_to_domain(full_text, business_domain, requirement_text=text):
                if name not in {"核心主流程", "登录/鉴权访问流程", "记录查询与筛选展示流程"}:
                    return
            if asset_yield_scene and _text_contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                return
            flows.append({
                "name": name,
                "steps": _dedupe_str_list(steps),
                "reason": reason,
                "level": level,
                "is_core": is_core,
            })

        if "提交" in text and ("审核" in text or "状态" in text):
            add_flow(
                name="提交-审核-状态流转",
                steps=["进入功能", "填写/提交", "审核处理", "结果展示"],
                reason="需求中同时出现提交流程与审核/状态变化。",
                level="高",
                is_core=True,
            )

        if ("列表" in text or "记录" in text or "查询" in text) and ("搜索" in text or "筛选" in text or "分页" in text):
            add_flow(
                name="记录查询与筛选展示流程",
                steps=["进入列表", "查询/筛选", "展示结果", "查看详情"],
                reason="需求涉及记录查询、筛选或分页能力。",
                level="中",
                is_core=False,
            )

        if "登录" in text or "权限" in text:
            add_flow(
                name="登录/鉴权访问流程",
                steps=["进入页面", "身份校验", "权限判断", "功能访问"],
                reason="需求涉及登录态或权限控制。",
                level="高",
                is_core=True,
            )

        if (not asset_yield_scene) and ("支付" in text or "充值" in text or "提现" in text or "交易" in text or "订单" in text):
            add_flow(
                name="交易/资金主流程",
                steps=["进入功能", "输入参数", "提交操作", "结果校验"],
                reason="需求涉及资金、交易或订单类关键主链路。",
                level="高",
                is_core=True,
            )

        if business_domain == "登录注册":
            add_flow(
                name="登录注册主流程",
                steps=["进入登录/注册页", "输入信息", "校验/提交", "登录态建立"],
                reason="登录注册域核心主流程受影响。",
                level="高",
                is_core=True,
            )

        elif business_domain == "提现":
            add_flow(
                name="提现提交流程",
                steps=["进入提现页", "填写提现信息", "提交提现", "查看状态"],
                reason="提现域核心主流程受影响。",
                level="高",
                is_core=True,
            )
            add_flow(
                name="提现审核与状态回写流程",
                steps=["提交提现", "触发审核", "审核结果回写", "状态展示"],
                reason="提现通常涉及审核和状态联动。",
                level="高",
                is_core=True,
            )

        elif business_domain == "充值":
            add_flow(
                name="充值到账流程",
                steps=["进入充值页", "获取充值信息", "到账确认", "资产展示"],
                reason="充值域到账链路受影响。",
                level="高",
                is_core=True,
            )

        elif business_domain == "划转":
            add_flow(
                name="划转主流程",
                steps=["进入划转页", "选择账户", "提交划转", "结果展示"],
                reason="划转域账户间资产流转流程受影响。",
                level="高",
                is_core=True,
            )

        elif business_domain == "现货":
            add_flow(
                name="现货下单流程",
                steps=["进入交易页", "输入下单参数", "提交订单", "查看订单结果"],
                reason="现货交易主链路受影响。",
                level="高",
                is_core=True,
            )

        elif business_domain == "合约":
            add_flow(
                name="合约开平仓流程",
                steps=["进入合约页", "输入下单参数", "提交开/平仓", "查看结果"],
                reason="合约交易主链路受影响。",
                level="高",
                is_core=True,
            )

        elif business_domain == "撮合":
            add_flow(
                name="订单撮合与结果回写流程",
                steps=["创建订单", "触发撮合", "状态回写", "结果展示"],
                reason="撮合域核心流程受影响。",
                level="高",
                is_core=True,
            )

        elif business_domain == "风控":
            add_flow(
                name="风控校验流程",
                steps=["触发操作", "规则判定", "命中/放行", "结果展示"],
                reason="风控域规则校验链路受影响。",
                level="高",
                is_core=True,
            )

        elif business_domain == "KYC":
            add_flow(
                name="认证与资格校验流程",
                steps=["进入认证流程", "提交认证信息", "审核/校验", "资格生效"],
                reason="KYC 域资格链路受影响。",
                level="高",
                is_core=True,
            )

        elif business_domain == "资产":
            if _text_contains_any(text.lower(), ["apr", "年化", "收益", "理财", "earn"]):
                add_flow(
                    name="收益展示流程",
                    steps=["进入资产/理财页", "读取收益数据", "展示收益概览", "查看详情结果"],
                    reason="需求涉及收益、APR 或年化展示能力。",
                    level="高",
                    is_core=True,
                )
                add_flow(
                    name="收益概览与详情联动流程",
                    steps=["查看收益概览", "进入详情页", "核对汇总与明细", "结果展示"],
                    reason="收益概览与详情通常存在联动关系。",
                    level="高",
                    is_core=True,
                )
                if _text_contains_any(text.lower(), ["7天", "30天", "90天", "周期", "切换"]):
                    add_flow(
                        name="多周期收益切换流程",
                        steps=["进入收益页", "切换收益周期", "刷新收益展示", "核对结果"],
                        reason="需求涉及 7/30/90 天等多周期收益展示切换。",
                        level="高",
                        is_core=True,
                    )
                if _text_contains_any(text.lower(), ["t+1", "数据生成", "派息", "补跑"]):
                    add_flow(
                        name="收益数据生成与展示衔接流程",
                        steps=["后台生成收益数据", "前端读取收益结果", "展示收益概览", "查看详情结果"],
                        reason="需求涉及 T+1、派息或收益数据生成后展示衔接能力。",
                        level="高",
                        is_core=True,
                    )
                if _text_contains_any(text.lower(), ["加息券"]):
                    add_flow(
                        name="加息券收益纳入展示流程",
                        steps=["识别加息券状态", "计算收益汇总", "展示收益概览", "核对详情结果"],
                        reason="需求涉及加息券收益纳入收益展示能力。",
                        level="中",
                        is_core=False,
                    )
            else:
                add_flow(
                    name="资产展示流程",
                    steps=["进入资产页", "读取资产数据", "展示余额/明细", "查看结果"],
                    reason="资产域展示和结果链路受影响。",
                    level="高",
                    is_core=True,
                )

        if not flows:
            add_flow(
                name="核心主流程",
                steps=["进入功能", "执行操作", "结果验证"],
                reason="根据需求原文推断存在完整业务主链路。",
                level="中",
                is_core=True,
            )

        return self._dedupe_affected_flows(flows)

    def _fallback_scope(
        self,
        impact_modules: List[Dict[str, Any]],
        impact_roles: List[Dict[str, Any]],
        affected_flows: List[Dict[str, Any]],
        text: str,
    ) -> str:
        high_modules = sum(1 for x in impact_modules if str(x.get("level")) == "高")
        high_flows = sum(1 for x in affected_flows if str(x.get("level")) == "高")
        core_flows = sum(1 for x in affected_flows if bool(x.get("is_core")))

        complex_keywords = [
            "多角色", "多状态", "多端", "灰度", "配置开关",
            "风控", "权限", "实名", "交易", "支付", "提现", "充值",
            "审核", "状态流转", "兼容", "历史数据", "并发", "幂等",
            "收益", "apr", "年化", "加息券", "t+1", "数据生成", "周期切换"
        ]
        has_complexity = _text_contains_any(text, complex_keywords)

        if high_modules >= 3 or high_flows >= 2:
            return "大"

        if len(impact_modules) >= 6 or len(impact_roles) >= 3 or len(affected_flows) >= 3:
            return "大"

        if has_complexity and (len(impact_modules) >= 4 or core_flows >= 2):
            return "大"

        if len(impact_modules) <= 2 and len(affected_flows) <= 1 and len(impact_roles) <= 1:
            return "小"

        return "中"