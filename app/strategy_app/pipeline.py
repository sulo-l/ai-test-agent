#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/pipeline.py

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.strategy_app.agents.impact_agent import ImpactAgent
from app.strategy_app.agents.risk_agent import RiskAgent
from app.strategy_app.agents.scope_agent import ScopeAgent
from app.strategy_app.agents.strategy_agent import StrategyAgent
from app.strategy_app.models import (
    AffectedFlow,
    AutomationStrategyItem,
    BlockerItem,
    EntryCriteriaItem,
    EnvironmentStrategyItem,
    ExecutionOrderItem,
    ExitCriteriaItem,
    ImpactModule,
    ImpactRole,
    LayerAdviceItem,
    PendingConfirmationItem,
    QualityGate,
    RegressionStrategyItem,
    ReleaseChecklistItem,
    ReleaseStrategyItem,
    RollbackStrategyItem,
    ScopeItem,
    StrategyContextMeta,
    StrategyLayerAdvice,
    StrategyMetrics,
    StrategyResourcePlan,
    StrategyResourcePlanItem,
    StrategyResult,
    StrategyRiskItem,
    StrategySummary,
    TestDataStrategyItem,
    TestTypeAdviceItem,
    refresh_strategy_metrics,
)
from app.strategy_app.utils.rules import build_strategy_by_rules
from app.strategy_app.utils.normalize import (
    normalize_strategy_payload,
    build_strategy_metrics,
)

logger = logging.getLogger(__name__)


# =====================================================
# 常量
# =====================================================

StageEmitter = Optional[Callable[[str, Dict[str, Any]], Any]]
CancelChecker = Optional[Callable[[], Any]]

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
    "风控": ["风控", "限额", "黑名单", "白名单", "频控", "拦截", "风险", "risk"],
    "KYC": ["kyc", "实名认证", "身份认证", "证件"],
    "资产": ["资产", "余额", "冻结", "账变", "流水", "账户资产", "收益", "年化", "apr", "理财", "earn", "加息券", "派息", "t+1"],
    "通用": [],
}

_DOMAIN_EXCLUSION_KEYWORDS: Dict[str, List[str]] = {
    "资产": [
        "合约开仓", "合约平仓", "爆仓", "强平", "撮合", "订单簿",
        "下单", "撤单", "仓位", "保证金", "现货交易", "永续", "杠杆", "开仓", "平仓", "成交回报",
    ],
    "充值": ["合约开仓", "合约平仓", "撮合", "跟单"],
    "提现": ["合约开仓", "合约平仓", "撮合", "跟单"],
    "划转": ["合约开仓", "合约平仓", "撮合"],
    "登录注册": ["合约开仓", "合约平仓", "撮合", "充值地址", "提现地址"],
}

_ASSET_YIELD_KEYWORDS = [
    "apr", "年化", "收益", "理财", "earn", "收益概览", "历史收益", "加息券", "派息", "t+1",
]

_TRADING_ONLY_KEYWORDS = [
    "下单", "撤单", "撮合", "仓位", "保证金", "爆仓", "强平", "订单簿",
    "现货交易", "合约开仓", "合约平仓", "成交回报", "永续", "杠杆", "开仓", "平仓",
]

_NOISE_PATTERNS = [
    "通过",
    "待观察",
    "通用兜底",
    "默认策略",
    "建议关注",
    "建议补充验证",
    "建议纳入测试策略",
    "规则引擎识别出的",
    "规则引擎建议覆盖",
    "规则引擎建议需要",
    "规则引擎识别需要",
]

_LOW_VALUE_TITLES = {
    "资金安全",
    "状态流转风险",
    "接口异常",
    "权限校验",
    "功能测试",
    "回归测试",
}

_STAGE_PROGRESS = {
    "LOAD_CONTEXT": 5,
    "RULE_BASELINE": 12,
    "IMPACT_ANALYSIS": 28,
    "RISK_ANALYSIS": 44,
    "SCOPE_ANALYSIS": 58,
    "MERGE_CLEAN": 72,
    "STRATEGY_GENERATION": 84,
    "FINAL_NORMALIZE": 94,
    "DONE": 100,
}


# =====================================================
# 异常
# =====================================================

class StrategyPipelineCancelled(Exception):
    """策略任务已取消"""


# =====================================================
# 工具函数
# =====================================================

def _ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _pick_first_str(*values: Any, default: Optional[str] = "") -> Optional[str]:
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


def _normalize_priority(value: Any, default: str = "P1") -> str:
    s = str(value or "").strip().upper()
    if s in {"P0", "P1", "P2", "P3"}:
        return s
    if s in {"HIGH", "严重", "高"}:
        return "P1"
    if s in {"MEDIUM", "中"}:
        return "P2"
    if s in {"LOW", "低"}:
        return "P3"
    if s in {"CRITICAL", "BLOCKER"}:
        return "P0"
    return default


def _normalize_risk_level(level: Any) -> str:
    return _normalize_priority(level, default="P2")


def _risk_rank(level: Any) -> int:
    lv = _normalize_risk_level(level)
    mapping = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return mapping.get(lv, 99)


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


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"true", "1", "yes", "y", "on"}:
            return True
        if s in {"false", "0", "no", "n", "off"}:
            return False
    return default


def _normalize_text_for_key(text: Any) -> str:
    s = str(text or "").strip().lower()
    s = re.sub(r"[：:，,。.\-_/\\()\[\]{}]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _contains_any(text: str, keywords: List[str]) -> bool:
    lower_text = text.lower()
    return any(str(k).lower() in lower_text for k in keywords if str(k).strip())


def _clean_sentence(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s).strip()
    for p in _NOISE_PATTERNS:
        s = s.replace(p, "")
    s = re.sub(r"\s+", " ", s).strip(" ，,。.;；")
    return s


def _is_asset_yield_scene(requirement_text: str, domain: str) -> bool:
    if domain != "资产":
        return False
    return _contains_any((requirement_text or "").lower(), _ASSET_YIELD_KEYWORDS)


def _normalize_business_domain(value: Any, requirement_text: str = "") -> str:
    s = str(value or "").strip()
    if s in _ALLOWED_DOMAINS:
        return s

    text = f"{s} {(requirement_text or '').strip()}".lower()

    if _contains_any(text, _ASSET_YIELD_KEYWORDS):
        return "资产"
    if any(k in text for k in ["login", "register", "auth"]):
        return "登录注册"
    if "user center" in text or "profile" in text:
        return "用户中心"
    if "spot" in text:
        return "现货"
    if any(k in text for k in ["contract", "future", "perp"]):
        return "合约"
    if "deposit" in text:
        return "充值"
    if "withdraw" in text:
        return "提现"
    if "transfer" in text:
        return "划转"
    if "p2p" in text:
        return "P2P"
    if any(k in text for k in ["copy", "follow"]):
        return "跟单"
    if "match" in text:
        return "撮合"
    if "risk" in text:
        return "风控"
    if "kyc" in text:
        return "KYC"
    if any(k in text for k in ["asset", "balance", "apr", "earn", "yield"]):
        return "资产"
    return "通用"


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
        "展示准确性", "汇总", "详情", "口径", "精度",
    ]
    return _contains_any(lower_text, generic_allow_keywords)


def _is_noise_text(text: str) -> bool:
    s = _normalize_text_for_key(text)
    if not s:
        return True
    if len(s) <= 1:
        return True
    if any(p.lower() in s for p in [x.lower() for x in _NOISE_PATTERNS]):
        return True
    return False


# =====================================================
# 企业级增强：策略收口
# =====================================================

def _semantic_similar(a: str, b: str) -> bool:
    a = _normalize_text_for_key(a)
    b = _normalize_text_for_key(b)

    replace_map = [
        ("展示", "显示"),
        ("概览", "汇总"),
        ("详情", "明细"),
        ("收益金额", "收益"),
        ("t+1", "时序"),
        ("数据生成", "时序"),
        ("精度", "口径"),
        ("单位", "口径"),
    ]
    for k, v in replace_map:
        a = a.replace(k, v)
        b = b.replace(k, v)

    if a == b:
        return True

    a_words = set(a.split())
    b_words = set(b.split())
    if not a_words or not b_words:
        return False

    inter = len(a_words & b_words)
    union = len(a_words | b_words)
    return union > 0 and (inter / union) >= 0.6


def _dedupe_risks_semantic(risks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []

    for r in risks or []:
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or "").strip()
        if not title:
            continue

        dup_idx = None
        for idx, existing in enumerate(result):
            if _semantic_similar(title, existing.get("title", "")):
                dup_idx = idx
                break

        if dup_idx is None:
            result.append(r)
            continue

        old = result[dup_idx]
        if _risk_rank(r.get("level")) < _risk_rank(old.get("level")):
            old["level"] = r.get("level")

        if len(title) < len(str(old.get("title") or "").strip()):
            old["title"] = title

        for key in ["reason", "impact", "suggestion", "trigger_condition"]:
            if not old.get(key) and r.get(key):
                old[key] = r.get(key)

        old["related_modules"] = _dedupe_str_list(
            _ensure_list(old.get("related_modules")) + _ensure_list(r.get("related_modules"))
        )
        old["related_flows"] = _dedupe_str_list(
            _ensure_list(old.get("related_flows")) + _ensure_list(r.get("related_flows"))
        )
        old["test_types"] = _dedupe_str_list(
            _ensure_list(old.get("test_types")) + _ensure_list(r.get("test_types"))
        )
        old["automation_candidate"] = bool(old.get("automation_candidate")) or bool(r.get("automation_candidate"))
        old["affects_release_gate"] = bool(old.get("affects_release_gate")) or bool(r.get("affects_release_gate"))

    return result


def _fix_quality_gate(payload: Dict[str, Any]) -> Dict[str, Any]:
    risks = [x for x in _ensure_list(payload.get("risk_items")) if isinstance(x, dict)]
    blockers = [x for x in _ensure_list(payload.get("blockers")) if isinstance(x, dict)]

    blocker_risks = []
    for r in risks:
        if _normalize_priority(r.get("level"), default="P2") == "P0":
            title = _pick_first_str(r.get("title"), default="")
            if title:
                blocker_risks.append(title)

    blocker_risks = _dedupe_str_list(blocker_risks)

    if blockers:
        decision = "fail"
        reasons = ["存在阻塞项"]
        required_actions = ["阻塞项清零后重新评估上线结论", "核心链路验证通过"]
    elif blocker_risks:
        decision = "fail"
        reasons = ["存在阻塞级风险"]
        required_actions = ["完成阻塞级风险验证与关闭", "核心链路验证通过"]
    else:
        high_risks = [
            r for r in risks
            if _normalize_priority(r.get("level"), default="P2") == "P1"
            and bool(r.get("affects_release_gate"))
        ]
        if high_risks:
            decision = "conditional_pass"
            reasons = ["存在高风险项，需完成重点验证后再放行"]
            required_actions = ["完成高风险验证", "核心链路验证通过", "关键回归验证完成"]
        else:
            decision = "pass"
            reasons = ["风险可控"]
            required_actions = ["核心链路验证通过"]

    return {
        "decision": decision,
        "reasons": reasons,
        "blocker_risks": blocker_risks,
        "required_actions": _dedupe_str_list(required_actions),
    }


def _fix_exit_criteria(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = [x for x in _ensure_list(items) if isinstance(x, dict)]
    titles = {_pick_first_str(x.get("title"), default="") for x in items}

    base = [
        ("核心主流程验证通过", "企业级准出要求"),
        ("阻塞级缺陷为0", "企业级准出要求"),
        ("高风险场景验证完成", "企业级准出要求"),
    ]

    for title, reason in base:
        if title not in titles:
            items.append({"title": title, "reason": reason})

    return items


def _fix_release_strategy(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = [x for x in _ensure_list(items) if isinstance(x, dict)]
    titles = {_pick_first_str(x.get("title"), default="") for x in items}

    base = [
        ("核心链路验证通过后方可发布", "降低发布风险"),
        ("建议灰度或开关控制发布", "降低发布风险"),
    ]

    for title, reason in base:
        if title not in titles:
            items.append({
                "title": title,
                "reason": reason,
                "required": title == "核心链路验证通过后方可发布",
                "notes": [],
            })

    return items


def _compress_core_reason(items: List[str]) -> List[str]:
    seen = set()
    result = []

    for x in _ensure_list(items):
        s = str(x or "").strip()
        if not s:
            continue
        key = _normalize_text_for_key(s)
        if key in seen:
            continue
        seen.add(key)
        result.append(s)

    return result[:5]


# =====================================================
# 主 Pipeline
# =====================================================

class StrategyPipeline:
    """
    测试策略智能体主执行链路（企业级增强版）
    """

    def __init__(
        self,
        workflow_id: Optional[str] = None,
        requirement_id: Optional[str] = None,
        stream_id: Optional[str] = None,
        event_emitter: StageEmitter = None,
        cancel_checker: CancelChecker = None,
    ) -> None:
        self.workflow_id = workflow_id
        self.requirement_id = requirement_id
        self.stream_id = stream_id
        self.event_emitter = event_emitter
        self.cancel_checker = cancel_checker

        self.impact_agent = ImpactAgent()
        self.risk_agent = RiskAgent()
        self.scope_agent = ScopeAgent()
        self.strategy_agent = StrategyAgent()

    async def run(
        self,
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]] = None,
        testcase_result: Optional[Dict[str, Any]] = None,
    ) -> StrategyResult:
        requirement_text = (requirement_text or "").strip()
        if not requirement_text:
            raise ValueError("requirement_text 不能为空")

        logger.info(
            "[strategy.pipeline] start run, workflow_id=%s, requirement_id=%s, len=%s",
            self.workflow_id,
            self.requirement_id,
            len(requirement_text),
        )

        await self._emit_stage("LOAD_CONTEXT", "start", "开始加载策略上下文", "正在读取需求、分析结果和测试用例上下文…")
        await self._check_cancel()

        context_meta = StrategyContextMeta(
            has_requirement=bool(requirement_text),
            has_analysis_result=bool(analysis_result),
            has_testcase_result=bool(testcase_result),
            requirement_length=len(requirement_text),
            business_domain_hint=self._guess_business_domain(
                requirement_text=requirement_text,
                analysis_result=analysis_result,
                testcase_result=testcase_result,
            ),
            source_types=self._build_source_types(
                requirement_text=requirement_text,
                analysis_result=analysis_result,
                testcase_result=testcase_result,
            ),
        )
        await self._emit_metric(
            "context",
            {
                "requirement_length": len(requirement_text),
                "has_analysis_result": bool(analysis_result),
                "has_testcase_result": bool(testcase_result),
                "business_domain_hint": context_meta.business_domain_hint,
            },
        )
        await self._emit_stage("LOAD_CONTEXT", "done", "策略上下文加载完成", f"业务域初判：{context_meta.business_domain_hint}")
        await self._emit_partial(
            "overview",
            {
                "business_domain_hint": context_meta.business_domain_hint,
                "requirement_length": len(requirement_text),
                "has_analysis_result": bool(analysis_result),
                "has_testcase_result": bool(testcase_result),
            },
        )

        await self._check_cancel()
        await self._emit_stage("RULE_BASELINE", "start", "开始规则基线分析", "正在生成规则基线，用于兜底但不会直接污染最终结果…")
        rule_strategy = build_strategy_by_rules(requirement_text)
        await self._emit_stage("RULE_BASELINE", "done", "规则基线分析完成", "已生成规则基线")

        await self._check_cancel()
        impact_data, risk_data, scope_data = await self._run_parallel_agents(
            requirement_text=requirement_text,
            analysis_result=analysis_result,
            testcase_result=testcase_result,
            context_meta=context_meta,
        )

        await self._check_cancel()
        await self._emit_stage("MERGE_CLEAN", "start", "开始融合与清洗", "正在融合规则结果与多 agent 结果，并进行企业级去重去噪…")

        impact_data = self._merge_impact_with_rules(impact_data, rule_strategy, context_meta, requirement_text)
        risk_data = self._merge_risk_with_rules(risk_data, rule_strategy, context_meta, requirement_text)
        scope_data = self._merge_scope_with_rules(scope_data, rule_strategy, context_meta, requirement_text)

        risk_data = self._merge_risk_with_impact(risk_data, impact_data)
        scope_data = self._merge_scope_with_context(scope_data, impact_data, risk_data, requirement_text, context_meta)

        impact_data, risk_data, scope_data = self._post_clean_agent_outputs(
            requirement_text=requirement_text,
            context_meta=context_meta,
            impact_data=impact_data,
            risk_data=risk_data,
            scope_data=scope_data,
        )

        await self._emit_metric(
            "clean_result",
            {
                "impact_module_count": len(_ensure_list(impact_data.get("impact_modules"))),
                "affected_flow_count": len(_ensure_list(impact_data.get("affected_flows"))),
                "risk_count": len(_ensure_list(risk_data.get("risk_items"))),
                "must_test_count": len(_ensure_list(scope_data.get("must_test"))),
                "regression_scope_count": len(_ensure_list(scope_data.get("regression_scope"))),
            },
        )
        await self._emit_partial(
            "modules",
            {"impact_modules": impact_data.get("impact_modules") or []},
        )
        await self._emit_partial(
            "risks",
            {"risk_items": risk_data.get("risk_items") or [], "overall_risk": risk_data.get("overall_risk")},
        )
        await self._emit_partial(
            "scope",
            {
                "must_test": scope_data.get("must_test") or [],
                "should_test": scope_data.get("should_test") or [],
                "regression_scope": scope_data.get("regression_scope") or [],
            },
        )
        await self._emit_stage("MERGE_CLEAN", "done", "融合与清洗完成", "已完成规则融合、去重、去噪和领域过滤")

        await self._check_cancel()
        await self._emit_stage("STRATEGY_GENERATION", "start", "开始生成测试策略", "正在基于清洗后的结构化上下文生成企业级测试策略…")
        strategy_data = await self._run_strategy_agent(
            requirement_text=requirement_text,
            analysis_result=analysis_result,
            testcase_result=testcase_result,
            impact_data=impact_data,
            risk_data=risk_data,
            scope_data=scope_data,
            context_meta=context_meta,
        )
        strategy_data = self._merge_strategy_with_rules(
            strategy_data=strategy_data,
            rule_strategy=rule_strategy,
            context_meta=context_meta,
            requirement_text=requirement_text,
        )
        strategy_data = self._clean_strategy_data(strategy_data, context_meta.business_domain_hint, requirement_text)
        await self._emit_partial(
            "strategy",
            {
                "test_objectives": strategy_data.get("test_objectives") or [],
                "test_type_matrix": strategy_data.get("test_type_matrix") or [],
                "automation_strategy": strategy_data.get("automation_strategy") or [],
                "release_strategy": strategy_data.get("release_strategy") or [],
            },
        )
        await self._emit_stage("STRATEGY_GENERATION", "done", "测试策略生成完成", "已完成高层测试策略生成")

        await self._check_cancel()
        await self._emit_stage("FINAL_NORMALIZE", "start", "开始最终结构化收敛", "正在构建最终结果并生成 markdown 输出…")

        final_domain = self._decide_final_business_domain(
            requirement_text=requirement_text,
            context_meta=context_meta,
            impact_data=impact_data,
            risk_data=risk_data,
            scope_data=scope_data,
            strategy_data=strategy_data,
            rule_strategy=rule_strategy,
        )

        normalized_payload = normalize_strategy_payload(
            {
                "business_domain": final_domain,
                "change_scope": impact_data.get("change_scope") or strategy_data.get("change_scope"),
                "overall_risk": risk_data.get("overall_risk") or strategy_data.get("overall_risk"),
                "core_reason": (
                    _ensure_list(strategy_data.get("core_reason"))
                    + _ensure_list(impact_data.get("core_reason"))
                    + _ensure_list(risk_data.get("core_reason"))
                ),
                "test_objectives": strategy_data.get("test_objectives"),
                "impact_modules": impact_data.get("impact_modules"),
                "impact_roles": impact_data.get("impact_roles"),
                "affected_flows": impact_data.get("affected_flows"),
                "risk_items": risk_data.get("risk_items"),
                "must_test": scope_data.get("must_test"),
                "should_test": scope_data.get("should_test"),
                "defer_test": scope_data.get("defer_test"),
                "out_of_scope": scope_data.get("out_of_scope") or strategy_data.get("out_of_scope"),
                "smoke_scope": scope_data.get("smoke_scope"),
                "regression_scope": scope_data.get("regression_scope"),
                "test_layer_advice": strategy_data.get("test_layer_advice"),
                "test_type_matrix": strategy_data.get("test_type_matrix"),
                "environment_strategy": strategy_data.get("environment_strategy"),
                "test_data_strategy": strategy_data.get("test_data_strategy"),
                "automation_strategy": strategy_data.get("automation_strategy"),
                "regression_strategy": strategy_data.get("regression_strategy"),
                "release_strategy": strategy_data.get("release_strategy"),
                "rollback_strategy": strategy_data.get("rollback_strategy"),
                "entry_criteria": strategy_data.get("entry_criteria"),
                "exit_criteria": strategy_data.get("exit_criteria"),
                "resource_plan": strategy_data.get("resource_plan"),
                "execution_order": strategy_data.get("execution_order"),
                "blockers": strategy_data.get("blockers"),
                "pending_confirmations": strategy_data.get("pending_confirmations"),
                "release_checklist": strategy_data.get("release_checklist"),
                "quality_gate": strategy_data.get("quality_gate"),
                "assumptions": strategy_data.get("assumptions"),
                "notes": strategy_data.get("notes"),
            },
            requirement_text=requirement_text,
        )

        normalized_payload = self._final_business_clean(
            normalized_payload=normalized_payload,
            context_meta=context_meta,
            requirement_text=requirement_text,
        )

        result = self._build_final_result(
            normalized_payload=normalized_payload,
            context_meta=context_meta,
            raw_agent_outputs={
                "rule_strategy": rule_strategy,
                "impact_data": impact_data,
                "risk_data": risk_data,
                "scope_data": scope_data,
                "strategy_data": strategy_data,
            },
        )

        result.markdown = self._build_markdown(result)
        result.metrics = StrategyMetrics(**build_strategy_metrics(normalized_payload))
        result = refresh_strategy_metrics(result)

        result.workflow_id = self.workflow_id
        result.requirement_id = self.requirement_id
        result.stream_id = self.stream_id
        result.status = "done"

        await self._emit_metric(
            "final_result",
            {
                "risk_count": len(result.risk_items or []),
                "must_test_count": len(result.must_test or []),
                "should_test_count": len(result.should_test or []),
                "regression_scope_count": len(result.regression_scope or []),
                "automation_count": len(result.automation_strategy or []),
            },
        )
        await self._emit_partial("final_summary", {"summary": getattr(result, "summary", None)})
        await self._emit_stage("FINAL_NORMALIZE", "done", "最终结构化收敛完成", "结果已标准化")
        await self._emit_stage("DONE", "done", "测试策略生成完成", "已生成企业级测试策略结果")

        logger.info(
            "[strategy.pipeline] done, workflow_id=%s, requirement_id=%s, risk_count=%s, must_test=%s",
            self.workflow_id,
            self.requirement_id,
            len(result.risk_items or []),
            len(result.must_test or []),
        )
        return result

    # =====================================================
    # emit
    # =====================================================

    async def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self.event_emitter:
            return
        try:
            maybe = self.event_emitter(event_type, payload)
            if inspect.isawaitable(maybe):
                await maybe
        except Exception as e:
            logger.warning("[strategy.pipeline] emit %s failed: %s", event_type, e)

    async def _emit_stage(
        self,
        stage: str,
        status: str,
        title: str,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = {
            "type": "stage",
            "stage": stage,
            "status": status,
            "title": title,
            "message": message,
            "workflow_id": self.workflow_id,
            "requirement_id": self.requirement_id,
            "stream_id": self.stream_id,
            "progress": _STAGE_PROGRESS.get(stage, 0),
        }
        if extra:
            payload.update(extra)
        await self._emit("stage", payload)

    async def _emit_metric(self, name: str, value: Dict[str, Any]) -> None:
        await self._emit(
            "metric",
            {
                "type": "metric",
                "name": name,
                "value": value,
                "workflow_id": self.workflow_id,
                "requirement_id": self.requirement_id,
                "stream_id": self.stream_id,
            },
        )

    async def _emit_log(self, message: str, level: str = "info", **extra: Any) -> None:
        payload = {
            "type": "log",
            "level": level,
            "message": message,
            "workflow_id": self.workflow_id,
            "requirement_id": self.requirement_id,
            "stream_id": self.stream_id,
        }
        if extra:
            payload.update(extra)
        await self._emit("log", payload)

    async def _emit_partial(self, block: str, data: Any) -> None:
        await self._emit(
            "partial",
            {
                "type": "partial",
                "block": block,
                "data": data,
                "workflow_id": self.workflow_id,
                "requirement_id": self.requirement_id,
                "stream_id": self.stream_id,
            },
        )

    async def _check_cancel(self) -> None:
        if not self.cancel_checker:
            return
        try:
            maybe = self.cancel_checker()
            cancelled = await maybe if inspect.isawaitable(maybe) else maybe
            if bool(cancelled):
                await self._emit_stage("CANCELLED", "done", "测试策略已取消", "任务已收到取消信号并终止执行")
                raise StrategyPipelineCancelled("strategy pipeline cancelled")
        except StrategyPipelineCancelled:
            raise
        except Exception as e:
            logger.warning("[strategy.pipeline] cancel check failed: %s", e)

    # =====================================================
    # agent 执行
    # =====================================================

    async def _run_parallel_agents(
        self,
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]],
        testcase_result: Optional[Dict[str, Any]],
        context_meta: StrategyContextMeta,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        await self._emit_stage("IMPACT_ANALYSIS", "start", "开始影响面分析", "正在识别受影响模块、角色和业务流程…")
        await self._emit_stage("RISK_ANALYSIS", "start", "开始风险分析", "正在识别变更引入的高风险点…")
        await self._emit_stage("SCOPE_ANALYSIS", "start", "开始范围分析", "正在识别必测范围、建议范围和回归范围…")

        impact_task = asyncio.create_task(
            self._safe_agent_call(
                "impact_agent",
                self.impact_agent.analyze(
                    requirement_text=requirement_text,
                    analysis_result=analysis_result,
                    testcase_result=testcase_result,
                ),
            )
        )

        risk_task = asyncio.create_task(
            self._safe_agent_call(
                "risk_agent",
                self.risk_agent.analyze(
                    requirement_text=requirement_text,
                    analysis_result=analysis_result,
                    testcase_result=testcase_result,
                    impact_data=None,
                ),
            )
        )

        scope_task = asyncio.create_task(
            self._safe_agent_call(
                "scope_agent",
                self.scope_agent.analyze(
                    requirement_text=requirement_text,
                    analysis_result=analysis_result,
                    testcase_result=testcase_result,
                    impact_data=None,
                    risk_data=None,
                ),
            )
        )

        impact_data, risk_data, scope_data = await asyncio.gather(
            impact_task,
            risk_task,
            scope_task,
        )

        await self._check_cancel()

        impact_data = impact_data or {}
        risk_data = risk_data or {}
        scope_data = scope_data or {}

        await self._emit_metric(
            "agent_raw_output",
            {
                "impact_keys": sorted(list(impact_data.keys())),
                "risk_keys": sorted(list(risk_data.keys())),
                "scope_keys": sorted(list(scope_data.keys())),
                "business_domain_hint": context_meta.business_domain_hint,
            },
        )

        await self._emit_stage(
            "IMPACT_ANALYSIS",
            "done",
            "影响面分析完成",
            f"模块={len(_ensure_list(impact_data.get('impact_modules')))}，流程={len(_ensure_list(impact_data.get('affected_flows')))}",
        )
        await self._emit_stage(
            "RISK_ANALYSIS",
            "done",
            "风险分析完成",
            f"风险项={len(_ensure_list(risk_data.get('risk_items')))}",
        )
        await self._emit_stage(
            "SCOPE_ANALYSIS",
            "done",
            "范围分析完成",
            f"必测={len(_ensure_list(scope_data.get('must_test')))}，回归={len(_ensure_list(scope_data.get('regression_scope')))}",
        )

        return impact_data, risk_data, scope_data

    async def _run_strategy_agent(
        self,
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]],
        testcase_result: Optional[Dict[str, Any]],
        impact_data: Dict[str, Any],
        risk_data: Dict[str, Any],
        scope_data: Dict[str, Any],
        context_meta: StrategyContextMeta,
    ) -> Dict[str, Any]:
        return await self._safe_agent_call(
            "strategy_agent",
            self.strategy_agent.analyze(
                requirement_text=requirement_text,
                analysis_result=analysis_result,
                testcase_result=testcase_result,
                impact_data=impact_data,
                risk_data=risk_data,
                scope_data=scope_data,
                context_meta={
                    "has_requirement": context_meta.has_requirement,
                    "has_analysis_result": context_meta.has_analysis_result,
                    "has_testcase_result": context_meta.has_testcase_result,
                    "requirement_length": context_meta.requirement_length,
                    "business_domain_hint": context_meta.business_domain_hint,
                    "source_types": context_meta.source_types,
                },
            ),
        )

    async def _safe_agent_call(self, name: str, awaitable: Any) -> Dict[str, Any]:
        try:
            data = await awaitable
            if isinstance(data, dict):
                return data
            logger.warning("[strategy.pipeline] %s returned non-dict: %s", name, type(data).__name__)
            return {}
        except Exception as e:
            logger.exception("[strategy.pipeline] %s failed: %s", name, e)
            await self._emit_log(f"{name} 执行失败：{e}", level="error", agent=name)
            return {
                "_agent_error": True,
                "_agent_name": name,
                "_agent_error_message": str(e),
            }

    # =====================================================
    # merge
    # =====================================================

    def _merge_impact_with_rules(
        self,
        impact_data: Dict[str, Any],
        rule_strategy: Dict[str, Any],
        context_meta: StrategyContextMeta,
        requirement_text: str,
    ) -> Dict[str, Any]:
        impact_data = dict(impact_data or {})
        domain = self._decide_domain_from_sources(
            requirement_text=requirement_text,
            context_meta=context_meta,
            candidates=[
                impact_data.get("business_domain"),
                (rule_strategy.get("summary") or {}).get("business_domain"),
                context_meta.business_domain_hint,
            ],
        )
        impact_data["business_domain"] = domain
        impact_data["core_reason"] = _dedupe_str_list(
            _ensure_list(impact_data.get("core_reason"))
            + [f"本次需求主要落在「{domain}」业务域"]
        )
        return impact_data

    def _merge_risk_with_rules(
        self,
        risk_data: Dict[str, Any],
        rule_strategy: Dict[str, Any],
        context_meta: StrategyContextMeta,
        requirement_text: str,
    ) -> Dict[str, Any]:
        risk_data = dict(risk_data or {})
        domain = context_meta.business_domain_hint
        rule_risks = []

        for idx, item in enumerate(_ensure_list(rule_strategy.get("risks")), start=1):
            if not isinstance(item, dict):
                continue
            risk_type = _pick_first_str(item.get("risk_type"), item.get("type"), default=f"规则风险-{idx}")
            level = _pick_first_str(item.get("level"), default="中")
            desc = _pick_first_str(item.get("desc"), default="规则引擎识别出的风险")
            full_text = f"{risk_type} {desc}"

            if not _is_text_relevant_to_domain(full_text, domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, domain) and _contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                continue

            mapped_level = "P1" if level == "高" else ("P2" if level == "中" else "P3")
            rule_risks.append({
                "risk_id": f"RULE-RISK-{idx:03d}",
                "title": risk_type,
                "level": mapped_level,
                "category": risk_type,
                "reason": desc,
                "trigger_condition": "",
                "impact": "",
                "suggestion": "建议优先纳入测试策略与回归验证",
                "related_modules": [],
                "related_flows": [],
                "test_types": [],
                "automation_candidate": False,
                "affects_release_gate": mapped_level in {"P0", "P1"},
            })

        risk_data["risk_items"] = _ensure_list(risk_data.get("risk_items")) + rule_risks

        current_overall = risk_data.get("overall_risk")
        if not current_overall:
            rule_risk_level = (rule_strategy.get("summary") or {}).get("risk_level")
            risk_data["overall_risk"] = rule_risk_level or "中"

        risk_data["core_reason"] = _dedupe_str_list(
            _ensure_list(risk_data.get("core_reason"))
            + [f"风险项已按「{domain}」领域相关性过滤和去重"]
        )
        return risk_data

    def _merge_scope_with_rules(
        self,
        scope_data: Dict[str, Any],
        rule_strategy: Dict[str, Any],
        context_meta: StrategyContextMeta,
        requirement_text: str,
    ) -> Dict[str, Any]:
        scope_data = dict(scope_data or {})
        domain = context_meta.business_domain_hint

        must_test = _ensure_list(scope_data.get("must_test"))
        rule_must = []
        for idx, title in enumerate(_ensure_list(rule_strategy.get("must_test")), start=1):
            title = str(title or "").strip()
            if not title:
                continue
            if not _is_text_relevant_to_domain(title, domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, domain) and _contains_any(title.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            rule_must.append({
                "title": title,
                "reason": "规则基线识别出的重点验证场景",
                "priority": "P1" if idx > 1 else "P0",
                "related_modules": [],
                "related_flows": [],
                "test_types": [],
                "owner": "测试",
            })
        scope_data["must_test"] = must_test + rule_must

        regression_scope = _ensure_list(scope_data.get("regression_scope"))
        rule_regression = []
        for title in _ensure_list(rule_strategy.get("regression_scope")):
            title = str(title or "").strip()
            if not title:
                continue
            if not _is_text_relevant_to_domain(title, domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, domain) and _contains_any(title.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            rule_regression.append({
                "title": title,
                "reason": "规则基线识别出的回归重点",
                "priority": "P1",
                "related_modules": [],
                "related_flows": [],
                "test_types": ["回归测试"],
                "owner": "测试",
            })
        scope_data["regression_scope"] = regression_scope + rule_regression

        scope_data["business_domain"] = self._decide_domain_from_sources(
            requirement_text=requirement_text,
            context_meta=context_meta,
            candidates=[
                scope_data.get("business_domain"),
                (rule_strategy.get("summary") or {}).get("business_domain"),
                context_meta.business_domain_hint,
            ],
        )
        return scope_data

    def _merge_strategy_with_rules(
        self,
        strategy_data: Dict[str, Any],
        rule_strategy: Dict[str, Any],
        context_meta: StrategyContextMeta,
        requirement_text: str,
    ) -> Dict[str, Any]:
        strategy_data = dict(strategy_data or {})
        summary = rule_strategy.get("summary") or {}
        execution_strategy = rule_strategy.get("execution_strategy") or {}
        test_scope = _ensure_list(rule_strategy.get("test_scope"))
        domain = context_meta.business_domain_hint

        strategy_data["business_domain"] = self._decide_domain_from_sources(
            requirement_text=requirement_text,
            context_meta=context_meta,
            candidates=[
                strategy_data.get("business_domain"),
                summary.get("business_domain"),
                context_meta.business_domain_hint,
            ],
        )

        if not strategy_data.get("test_objectives"):
            strategy_data["test_objectives"] = [
                "识别本次变更影响范围与高风险链路",
                "确保核心主流程、关键异常流和高风险联动场景得到覆盖",
                "为回归、自动化与上线决策提供可执行策略依据",
            ]

        if not strategy_data.get("test_type_matrix"):
            matrix = []
            for item in test_scope:
                if not isinstance(item, dict):
                    continue
                dimension = _pick_first_str(item.get("dimension"), default="")
                cases = _ensure_list(item.get("cases"))
                if not dimension:
                    continue
                full_text = f"{dimension} {' '.join(str(x) for x in cases)}"
                if not _is_text_relevant_to_domain(full_text, domain, requirement_text=requirement_text):
                    continue
                if _is_asset_yield_scene(requirement_text, domain) and _contains_any(full_text.lower(), _TRADING_ONLY_KEYWORDS):
                    continue

                mapped_type = dimension
                if dimension == "资金校验":
                    mapped_type = "数据一致性测试"
                elif dimension == "状态流转":
                    mapped_type = "异常流测试"
                elif dimension == "权限校验":
                    mapped_type = "权限测试"
                elif dimension == "接口异常":
                    mapped_type = "接口测试"
                elif dimension == "性能":
                    mapped_type = "性能测试"
                elif dimension == "撮合验证":
                    mapped_type = "接口测试"
                elif dimension == "资金安全":
                    mapped_type = "数据一致性测试"

                matrix.append({
                    "type_name": mapped_type,
                    "necessary": True,
                    "priority": "P1",
                    "scope": cases,
                    "reason": f"建议覆盖：{dimension}",
                    "automation_candidate": mapped_type in {"接口测试", "回归测试", "冒烟测试"},
                    "related_risks": [],
                })
            strategy_data["test_type_matrix"] = matrix

        if not strategy_data.get("automation_strategy") and execution_strategy.get("need_automation"):
            strategy_data["automation_strategy"] = [
                {
                    "title": "优先沉淀稳定主链路自动化",
                    "scope": ["核心主流程", "高频回归场景"],
                    "priority": "P1",
                    "reason": "本次需求存在持续回归价值，建议优先建设接口自动化与冒烟自动化",
                    "framework_hint": "优先接口自动化，再补充冒烟级 UI 自动化",
                }
            ]

        if not strategy_data.get("regression_strategy") and execution_strategy.get("need_regression"):
            filtered_scope = [
                x for x in _ensure_list(rule_strategy.get("regression_scope"))
                if _is_text_relevant_to_domain(str(x), domain, requirement_text=requirement_text)
                and not (_is_asset_yield_scene(requirement_text, domain) and _contains_any(str(x).lower(), _TRADING_ONLY_KEYWORDS))
            ]
            if filtered_scope:
                strategy_data["regression_strategy"] = [
                    {
                        "title": "定向回归受影响范围",
                        "scope": filtered_scope,
                        "reason": "围绕本次实际受影响链路执行定向回归",
                        "priority": "P1",
                    }
                ]

        if not strategy_data.get("release_strategy"):
            release_items = [
                {
                    "title": "核心链路通过后再考虑发布",
                    "reason": "需先确保本次变更核心链路稳定，避免带病上线",
                    "required": True,
                    "notes": [],
                }
            ]
            if execution_strategy.get("need_gray_release"):
                release_items.append({
                    "title": "建议灰度发布",
                    "reason": "考虑分批放量，降低发布风险",
                    "required": False,
                    "notes": ["需确认灰度命中规则、关闭开关和回滚路径"],
                })
            strategy_data["release_strategy"] = release_items

        if not strategy_data.get("quality_gate"):
            risk_level = summary.get("risk_level")
            if risk_level == "高":
                strategy_data["quality_gate"] = {
                    "decision": "conditional_pass",
                    "reasons": ["整体风险偏高，建议完成高风险验证后再放行"],
                    "blocker_risks": [],
                    "required_actions": ["完成高风险项验证", "完成关键回归验证"],
                }

        return strategy_data

    def _merge_risk_with_impact(self, risk_data: Dict[str, Any], impact_data: Dict[str, Any]) -> Dict[str, Any]:
        risk_data = dict(risk_data or {})
        module_names = [
            _pick_first_str(x.get("name"), default="")
            for x in _ensure_list(impact_data.get("impact_modules"))
            if isinstance(x, dict)
        ]
        flow_names = [
            _pick_first_str(x.get("name"), default="")
            for x in _ensure_list(impact_data.get("affected_flows"))
            if isinstance(x, dict)
        ]

        new_risks = []
        for item in _ensure_list(risk_data.get("risk_items")):
            if not isinstance(item, dict):
                continue
            x = dict(item)
            if not _ensure_list(x.get("related_modules")) and module_names:
                x["related_modules"] = module_names[:5]
            if not _ensure_list(x.get("related_flows")) and flow_names:
                x["related_flows"] = flow_names[:5]
            new_risks.append(x)

        risk_data["risk_items"] = new_risks
        return risk_data

    def _merge_scope_with_context(
        self,
        scope_data: Dict[str, Any],
        impact_data: Dict[str, Any],
        risk_data: Dict[str, Any],
        requirement_text: str,
        context_meta: StrategyContextMeta,
    ) -> Dict[str, Any]:
        scope_data = dict(scope_data or {})
        domain = context_meta.business_domain_hint

        module_names = [
            _pick_first_str(x.get("name"), default="")
            for x in _ensure_list(impact_data.get("impact_modules"))
            if isinstance(x, dict)
        ]
        flow_names = [
            _pick_first_str(x.get("name"), default="")
            for x in _ensure_list(impact_data.get("affected_flows"))
            if isinstance(x, dict)
        ]

        must_test = []
        for item in _ensure_list(scope_data.get("must_test")):
            if not isinstance(item, dict):
                continue
            x = dict(item)
            if not _ensure_list(x.get("related_modules")) and module_names:
                x["related_modules"] = module_names[:5]
            if not _ensure_list(x.get("related_flows")) and flow_names:
                x["related_flows"] = flow_names[:5]
            must_test.append(x)
        scope_data["must_test"] = must_test

        if not _ensure_list(scope_data.get("regression_scope")):
            generated = []
            for item in _ensure_list(risk_data.get("risk_items"))[:8]:
                if not isinstance(item, dict):
                    continue
                title = _pick_first_str(item.get("title"), default="")
                if not title:
                    continue
                title_text = str(title)
                if not _is_text_relevant_to_domain(title_text, domain, requirement_text=requirement_text):
                    continue
                generated.append({
                    "title": title,
                    "reason": "高风险项对应场景建议纳入回归验证",
                    "priority": _normalize_priority(item.get("level"), default="P1"),
                    "related_modules": _ensure_list(item.get("related_modules")),
                    "related_flows": _ensure_list(item.get("related_flows")),
                    "test_types": _dedupe_str_list(_ensure_list(item.get("test_types")) + ["回归测试"]),
                    "owner": "测试",
                })
            scope_data["regression_scope"] = generated

        if not _ensure_list(scope_data.get("smoke_scope")):
            smoke = []
            for item in _ensure_list(impact_data.get("affected_flows"))[:5]:
                if not isinstance(item, dict):
                    continue
                name = _pick_first_str(item.get("name"), default="")
                if not name:
                    continue
                smoke.append({
                    "title": name,
                    "reason": "核心受影响流程建议纳入冒烟范围",
                    "priority": "P1",
                    "related_modules": module_names[:5],
                    "related_flows": [name],
                    "test_types": ["冒烟测试"],
                    "owner": "测试",
                })
            scope_data["smoke_scope"] = smoke

        scope_data["business_domain"] = self._decide_domain_from_sources(
            requirement_text=requirement_text,
            context_meta=context_meta,
            candidates=[scope_data.get("business_domain"), domain],
        )
        return scope_data

    # =====================================================
    # clean
    # =====================================================

    def _post_clean_agent_outputs(
        self,
        requirement_text: str,
        context_meta: StrategyContextMeta,
        impact_data: Dict[str, Any],
        risk_data: Dict[str, Any],
        scope_data: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        domain = context_meta.business_domain_hint

        impact_data = dict(impact_data or {})
        risk_data = dict(risk_data or {})
        scope_data = dict(scope_data or {})

        impact_data["business_domain"] = self._decide_domain_from_sources(
            requirement_text=requirement_text,
            context_meta=context_meta,
            candidates=[impact_data.get("business_domain"), domain],
        )
        scope_data["business_domain"] = self._decide_domain_from_sources(
            requirement_text=requirement_text,
            context_meta=context_meta,
            candidates=[scope_data.get("business_domain"), domain],
        )

        impact_data["impact_modules"] = self._clean_impact_modules(
            impact_data.get("impact_modules"),
            domain,
            requirement_text,
        )
        impact_data["impact_roles"] = self._clean_impact_roles(
            impact_data.get("impact_roles"),
            domain,
            requirement_text,
        )
        impact_data["affected_flows"] = self._clean_affected_flows(
            impact_data.get("affected_flows"),
            domain,
            requirement_text,
        )

        risk_data["risk_items"] = self._clean_risk_items(
            risk_data.get("risk_items"),
            domain,
            requirement_text=requirement_text,
            cleaned_modules=impact_data["impact_modules"],
            cleaned_flows=impact_data["affected_flows"],
        )
        risk_data["overall_risk"] = self._recalculate_overall_risk(risk_data["risk_items"], risk_data.get("overall_risk"))

        scope_data["must_test"] = self._clean_scope_items(scope_data.get("must_test"), domain, requirement_text, default_priority="P1")
        scope_data["should_test"] = self._clean_scope_items(scope_data.get("should_test"), domain, requirement_text, default_priority="P2")
        scope_data["defer_test"] = self._clean_scope_items(scope_data.get("defer_test"), domain, requirement_text, default_priority="P3")
        scope_data["out_of_scope"] = self._clean_scope_items(scope_data.get("out_of_scope"), domain, requirement_text, default_priority="P3")
        scope_data["smoke_scope"] = self._clean_scope_items(scope_data.get("smoke_scope"), domain, requirement_text, default_priority="P1")
        scope_data["regression_scope"] = self._clean_scope_items(scope_data.get("regression_scope"), domain, requirement_text, default_priority="P1")

        scope_data["must_test"], scope_data["should_test"] = self._dedupe_scope_across_groups(
            scope_data["must_test"], scope_data["should_test"]
        )
        scope_data["must_test"], scope_data["regression_scope"] = self._dedupe_scope_across_groups(
            scope_data["must_test"], scope_data["regression_scope"]
        )
        scope_data["should_test"], scope_data["regression_scope"] = self._dedupe_scope_across_groups(
            scope_data["should_test"], scope_data["regression_scope"]
        )

        impact_data["core_reason"] = _dedupe_str_list(
            _ensure_list(impact_data.get("core_reason"))
            + [f"受影响范围围绕「{domain}」业务域收敛"]
        )
        risk_data["core_reason"] = _dedupe_str_list(
            _ensure_list(risk_data.get("core_reason"))
            + [f"风险项已按「{domain}」领域相关性过滤和去重"]
        )

        if not _ensure_list(scope_data.get("must_test")):
            fallback_must = []
            for idx, flow in enumerate(_ensure_list(impact_data.get("affected_flows"))[:5], start=1):
                if not isinstance(flow, dict):
                    continue
                name = _pick_first_str(flow.get("name"), flow.get("title"), default="")
                if not name:
                    continue
                fallback_must.append({
                    "title": name,
                    "reason": "受影响核心流程，建议纳入必测范围",
                    "priority": "P0" if idx == 1 else "P1",
                    "related_flows": [name],
                })

            if not fallback_must:
                for item in _ensure_list(risk_data.get("risk_items"))[:5]:
                    if not isinstance(item, dict):
                        continue
                    title = _pick_first_str(item.get("title"), default="")
                    if not title:
                        continue
                    fallback_must.append({
                        "title": title,
                        "reason": "高风险项对应场景建议纳入必测范围",
                        "priority": _normalize_priority(item.get("level"), default="P1"),
                        "related_modules": _ensure_list(item.get("related_modules")),
                        "related_flows": _ensure_list(item.get("related_flows")),
                    })

            scope_data["must_test"] = self._clean_scope_items(
                fallback_must, domain, requirement_text, default_priority="P1"
            )

        return impact_data, risk_data, scope_data

    def _clean_impact_modules(self, items: Any, domain: str, requirement_text: str) -> List[Dict[str, Any]]:
        result = []
        seen = set()

        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            name = _pick_first_str(item.get("name"), item.get("title"), default="")
            reason = _pick_first_str(item.get("reason"), default="")
            if not name:
                continue
            text = f"{name} {reason}"
            if not _is_text_relevant_to_domain(text, domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, domain) and _contains_any(text.lower(), _TRADING_ONLY_KEYWORDS):
                continue

            key = _normalize_text_for_key(name)
            if key in seen:
                continue
            seen.add(key)

            result.append({
                "name": name,
                "reason": _clean_sentence(reason),
                "level": _pick_first_str(item.get("level"), default=None),
                "direct": _safe_bool(item.get("direct"), True),
                "upstream": _safe_bool(item.get("upstream"), False),
                "downstream": _safe_bool(item.get("downstream"), False),
            })
        return result[:12]

    def _clean_impact_roles(self, items: Any, domain: str, requirement_text: str) -> List[Dict[str, Any]]:
        result = []
        seen = set()

        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            name = _pick_first_str(item.get("name"), item.get("title"), default="")
            reason = _pick_first_str(item.get("reason"), default="")
            if not name:
                continue
            text = f"{name} {reason}"
            if not _is_text_relevant_to_domain(text, domain, requirement_text=requirement_text):
                if _is_noise_text(text):
                    continue

            key = _normalize_text_for_key(name)
            if key in seen:
                continue
            seen.add(key)

            result.append({
                "name": name,
                "reason": _clean_sentence(reason),
                "permissions": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("permissions")) if str(x).strip()]
                ),
            })
        return result[:10]

    def _clean_affected_flows(self, items: Any, domain: str, requirement_text: str) -> List[Dict[str, Any]]:
        result = []
        seen = set()

        for item in _ensure_list(items):
            if not isinstance(item, dict):
                continue
            name = _pick_first_str(item.get("name"), item.get("title"), default="")
            reason = _pick_first_str(item.get("reason"), default="")
            steps = _dedupe_str_list([str(x).strip() for x in _ensure_list(item.get("steps")) if str(x).strip()])
            if not name:
                continue

            text = f"{name} {reason} {' '.join(steps)}"
            if not _is_text_relevant_to_domain(text, domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, domain) and _contains_any(text.lower(), _TRADING_ONLY_KEYWORDS):
                continue

            key = _normalize_text_for_key(name)
            if key in seen:
                continue
            seen.add(key)

            result.append({
                "name": name,
                "steps": steps[:10],
                "reason": _clean_sentence(reason),
                "level": _pick_first_str(item.get("level"), default=None),
                "is_core": _safe_bool(item.get("is_core"), False),
            })
        return result[:15]

    def _clean_risk_items(
        self,
        items: Any,
        domain: str,
        requirement_text: str,
        cleaned_modules: List[Dict[str, Any]],
        cleaned_flows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        result = []
        seen = set()

        module_names = [_pick_first_str(x.get("name"), default="") for x in cleaned_modules if isinstance(x, dict)]
        flow_names = [_pick_first_str(x.get("name"), default="") for x in cleaned_flows if isinstance(x, dict)]

        for idx, item in enumerate(_ensure_list(items), start=1):
            if not isinstance(item, dict):
                continue

            title = _pick_first_str(item.get("title"), default=f"风险项-{idx}") or f"风险项-{idx}"
            reason = _pick_first_str(item.get("reason"), default="")
            trigger_condition = _pick_first_str(item.get("trigger_condition"), default="")
            impact = _pick_first_str(item.get("impact"), default="")
            suggestion = _pick_first_str(item.get("suggestion"), default="")
            category = _pick_first_str(item.get("category"), default=None)

            text = f"{title} {reason} {trigger_condition} {impact} {suggestion} {category or ''}"
            if not _is_text_relevant_to_domain(text, domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, domain) and _contains_any(text.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            if self._is_noise_risk_item(title=title, reason=reason, suggestion=suggestion):
                continue

            key = self._risk_dedupe_key(title, reason)
            if key in seen:
                continue
            seen.add(key)

            related_modules = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("related_modules")) if str(x).strip()]
            )
            related_flows = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("related_flows")) if str(x).strip()]
            )
            test_types = _dedupe_str_list(
                [str(x).strip() for x in _ensure_list(item.get("test_types")) if str(x).strip()]
            )

            if not related_modules and module_names:
                related_modules = module_names[:5]
            if not related_flows and flow_names:
                related_flows = flow_names[:5]

            result.append({
                "risk_id": _pick_first_str(item.get("risk_id"), default=None),
                "title": _clean_sentence(title),
                "level": _normalize_risk_level(item.get("level")),
                "category": _clean_sentence(category) if category else None,
                "reason": _clean_sentence(reason),
                "trigger_condition": _clean_sentence(trigger_condition),
                "impact": _clean_sentence(impact),
                "suggestion": _clean_sentence(suggestion),
                "related_modules": related_modules,
                "related_flows": related_flows,
                "test_types": test_types,
                "automation_candidate": _safe_bool(item.get("automation_candidate"), False),
                "affects_release_gate": _safe_bool(item.get("affects_release_gate"), False),
            })

        result.sort(key=lambda x: _risk_rank(x.get("level")))
        result = _dedupe_risks_semantic(result)
        return result[:20]

    def _clean_scope_items(
        self,
        items: Any,
        domain: str,
        requirement_text: str,
        default_priority: str = "P1",
    ) -> List[Dict[str, Any]]:
        result = []
        seen = set()

        for idx, item in enumerate(_ensure_list(items), start=1):
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), default=f"范围项-{idx}") or f"范围项-{idx}"
            reason = _pick_first_str(item.get("reason"), default="")
            text = f"{title} {reason}"
            if not _is_text_relevant_to_domain(text, domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, domain) and _contains_any(text.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            if self._is_noise_scope_item(title=title, reason=reason):
                continue

            key = _normalize_text_for_key(title)
            if key in seen:
                continue
            seen.add(key)

            result.append({
                "title": _clean_sentence(title),
                "reason": _clean_sentence(reason),
                "priority": _normalize_priority(item.get("priority"), default=default_priority),
                "related_modules": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("related_modules")) if str(x).strip()]
                ),
                "related_flows": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("related_flows")) if str(x).strip()]
                ),
                "test_types": _dedupe_str_list(
                    [str(x).strip() for x in _ensure_list(item.get("test_types")) if str(x).strip()]
                ),
                "owner": _pick_first_str(item.get("owner"), default=None),
            })
        return result[:20]

    def _dedupe_scope_across_groups(
        self,
        primary_items: List[Dict[str, Any]],
        secondary_items: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        primary_keys = {
            _normalize_text_for_key(_pick_first_str(x.get("title"), default=""))
            for x in primary_items
            if isinstance(x, dict)
        }
        kept_secondary = []
        for item in secondary_items:
            if not isinstance(item, dict):
                continue
            title = _pick_first_str(item.get("title"), default="")
            key = _normalize_text_for_key(title)
            if key in primary_keys:
                continue
            kept_secondary.append(item)
        return primary_items, kept_secondary

    def _clean_strategy_data(self, strategy_data: Dict[str, Any], domain: str, requirement_text: str) -> Dict[str, Any]:
        data = dict(strategy_data or {})

        data["business_domain"] = self._decide_domain_from_sources(
            requirement_text=requirement_text,
            context_meta=StrategyContextMeta(
                has_requirement=True,
                has_analysis_result=False,
                has_testcase_result=False,
                requirement_length=len(requirement_text or ""),
                business_domain_hint=domain,
                source_types=["requirement"],
            ),
            candidates=[data.get("business_domain"), domain],
        )
        data["test_objectives"] = self._clean_text_list(
            data.get("test_objectives"),
            domain=domain,
            requirement_text=requirement_text,
            allow_generic=True,
            limit=8,
        )

        data["out_of_scope"] = self._clean_scope_items(data.get("out_of_scope"), domain, requirement_text, default_priority="P3")

        data["environment_strategy"] = self._clean_named_strategy_items(
            data.get("environment_strategy"),
            title_keys=["env_name", "name"],
            extra_clean_keys=["purpose", "notes"],
            domain=domain,
            requirement_text=requirement_text,
            limit=8,
            allow_generic=True,
        )
        data["test_data_strategy"] = self._clean_named_strategy_items(
            data.get("test_data_strategy"),
            title_keys=["title", "name"],
            extra_clean_keys=["data_type", "purpose", "notes"],
            domain=domain,
            requirement_text=requirement_text,
            limit=10,
            allow_generic=True,
        )
        data["automation_strategy"] = self._clean_named_strategy_items(
            data.get("automation_strategy"),
            title_keys=["title"],
            extra_clean_keys=["scope", "reason", "framework_hint"],
            domain=domain,
            requirement_text=requirement_text,
            limit=10,
            allow_generic=True,
        )
        data["regression_strategy"] = self._clean_named_strategy_items(
            data.get("regression_strategy"),
            title_keys=["title"],
            extra_clean_keys=["scope", "reason"],
            domain=domain,
            requirement_text=requirement_text,
            limit=10,
            allow_generic=True,
        )
        data["release_strategy"] = self._clean_named_strategy_items(
            data.get("release_strategy"),
            title_keys=["title"],
            extra_clean_keys=["reason", "notes"],
            domain=domain,
            requirement_text=requirement_text,
            limit=8,
            allow_generic=True,
        )
        data["rollback_strategy"] = self._clean_named_strategy_items(
            data.get("rollback_strategy"),
            title_keys=["title"],
            extra_clean_keys=["trigger", "action", "notes"],
            domain=domain,
            requirement_text=requirement_text,
            limit=8,
            allow_generic=True,
        )
        data["entry_criteria"] = self._clean_named_strategy_items(
            data.get("entry_criteria"),
            title_keys=["title"],
            extra_clean_keys=["reason", "owner"],
            domain=domain,
            requirement_text=requirement_text,
            limit=10,
            allow_generic=True,
        )
        data["exit_criteria"] = self._clean_named_strategy_items(
            data.get("exit_criteria"),
            title_keys=["title"],
            extra_clean_keys=["reason", "owner"],
            domain=domain,
            requirement_text=requirement_text,
            limit=10,
            allow_generic=True,
        )
        data["blockers"] = self._clean_named_strategy_items(
            data.get("blockers"),
            title_keys=["title"],
            extra_clean_keys=["reason", "owner", "suggestion", "severity"],
            domain=domain,
            requirement_text=requirement_text,
            limit=10,
            allow_generic=True,
        )
        data["pending_confirmations"] = self._clean_named_strategy_items(
            data.get("pending_confirmations"),
            title_keys=["title"],
            extra_clean_keys=["reason", "owner", "impact"],
            domain=domain,
            requirement_text=requirement_text,
            limit=10,
            allow_generic=True,
        )
        data["release_checklist"] = self._clean_named_strategy_items(
            data.get("release_checklist"),
            title_keys=["title"],
            extra_clean_keys=["reason", "owner", "related_risks"],
            domain=domain,
            requirement_text=requirement_text,
            limit=12,
            allow_generic=True,
        )

        if isinstance(data.get("quality_gate"), dict):
            gate = dict(data["quality_gate"])
            gate["decision"] = _normalize_gate_decision(gate.get("decision"))
            gate["reasons"] = self._clean_text_list(gate.get("reasons"), domain, requirement_text, allow_generic=True, limit=8)
            gate["blocker_risks"] = self._clean_text_list(gate.get("blocker_risks"), domain, requirement_text, allow_generic=True, limit=10)
            gate["required_actions"] = self._clean_text_list(gate.get("required_actions"), domain, requirement_text, allow_generic=True, limit=10)
            data["quality_gate"] = gate

        if isinstance(data.get("resource_plan"), dict):
            rp = dict(data["resource_plan"])
            for key in ["one_day", "two_days", "three_days", "five_days"]:
                rp[key] = self._clean_named_strategy_items(
                    rp.get(key),
                    title_keys=["title"],
                    extra_clean_keys=["scope", "focus", "note"],
                    domain=domain,
                    requirement_text=requirement_text,
                    limit=8,
                    allow_generic=True,
                )
            data["resource_plan"] = rp

        if isinstance(data.get("test_layer_advice"), dict):
            layer = dict(data["test_layer_advice"])
            for key in ["ui", "api", "service", "db", "e2e", "manual", "automation_candidate"]:
                layer[key] = self._clean_named_strategy_items(
                    layer.get(key),
                    title_keys=["title"],
                    extra_clean_keys=["reason", "related_scope", "related_risks", "priority"],
                    domain=domain,
                    requirement_text=requirement_text,
                    limit=10,
                    allow_generic=True,
                )
            data["test_layer_advice"] = layer

        if isinstance(data.get("test_type_matrix"), list):
            matrix = []
            seen = set()
            for item in data["test_type_matrix"]:
                if not isinstance(item, dict):
                    continue
                type_name = _pick_first_str(item.get("type_name"), item.get("name"), default="")
                reason = _pick_first_str(item.get("reason"), default="")
                scope = _ensure_list(item.get("scope"))
                text = f"{type_name} {reason} {' '.join(str(x) for x in scope)}"
                if not type_name:
                    continue
                if not _is_text_relevant_to_domain(text, domain, requirement_text=requirement_text) and type_name not in {
                    "功能测试", "接口测试", "回归测试", "冒烟测试", "异常流测试", "边界值测试",
                    "数据一致性测试", "权限测试", "并发测试", "幂等测试",
                }:
                    continue
                if _is_asset_yield_scene(requirement_text, domain) and _contains_any(text.lower(), _TRADING_ONLY_KEYWORDS):
                    continue
                key = _normalize_text_for_key(type_name)
                if key in seen:
                    continue
                seen.add(key)
                matrix.append({
                    "type_name": type_name,
                    "necessary": _safe_bool(item.get("necessary"), True),
                    "priority": _normalize_priority(item.get("priority"), default="P1"),
                    "scope": self._clean_text_list(scope, domain, requirement_text, allow_generic=True, limit=10),
                    "reason": _clean_sentence(reason),
                    "automation_candidate": _safe_bool(item.get("automation_candidate"), False),
                    "related_risks": self._clean_text_list(item.get("related_risks"), domain, requirement_text, allow_generic=True, limit=8),
                })
            data["test_type_matrix"] = matrix

        data["assumptions"] = self._clean_text_list(data.get("assumptions"), domain, requirement_text, allow_generic=True, limit=10)
        data["notes"] = self._clean_text_list(data.get("notes"), domain, requirement_text, allow_generic=True, limit=10)
        data["core_reason"] = self._clean_text_list(data.get("core_reason"), domain, requirement_text, allow_generic=True, limit=10)

        return data

    def _clean_named_strategy_items(
        self,
        items: Any,
        title_keys: List[str],
        extra_clean_keys: List[str],
        domain: str,
        requirement_text: str,
        limit: int = 10,
        allow_generic: bool = False,
    ) -> List[Dict[str, Any]]:
        result = []
        seen = set()

        for idx, item in enumerate(_ensure_list(items), start=1):
            if not isinstance(item, dict):
                continue
            title = ""
            for k in title_keys:
                title = _pick_first_str(item.get(k), default="") or title
                if title:
                    break
            if not title:
                title = f"策略项-{idx}"

            texts = [title]
            for k in extra_clean_keys:
                v = item.get(k)
                if isinstance(v, list):
                    texts.extend(str(x) for x in v if x is not None)
                elif v is not None:
                    texts.append(str(v))
            joined = " ".join(texts).strip()

            if not allow_generic and not _is_text_relevant_to_domain(joined, domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, domain) and _contains_any(joined.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            if _is_noise_text(joined):
                continue

            key = _normalize_text_for_key(title)
            if key in seen:
                continue
            seen.add(key)

            cleaned = dict(item)
            for k in title_keys:
                if k in cleaned and cleaned[k] is not None:
                    cleaned[k] = _clean_sentence(cleaned[k])
            for k in extra_clean_keys:
                if k not in cleaned:
                    continue
                if isinstance(cleaned[k], list):
                    cleaned[k] = self._clean_text_list(
                        cleaned[k], domain, requirement_text, allow_generic=True, limit=10
                    )
                else:
                    cleaned[k] = _clean_sentence(cleaned[k])

            result.append(cleaned)
            if len(result) >= limit:
                break

        return result

    def _clean_text_list(
        self,
        items: Any,
        domain: str,
        requirement_text: str,
        allow_generic: bool = False,
        limit: int = 10,
    ) -> List[str]:
        result = []
        seen = set()
        for raw in _ensure_list(items):
            text = _clean_sentence(raw)
            if not text:
                continue
            if not allow_generic and not _is_text_relevant_to_domain(text, domain, requirement_text=requirement_text):
                continue
            if _is_asset_yield_scene(requirement_text, domain) and _contains_any(text.lower(), _TRADING_ONLY_KEYWORDS):
                continue
            key = _normalize_text_for_key(text)
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
            if len(result) >= limit:
                break
        return result

    # =====================================================
    # final clean / domain
    # =====================================================

    def _decide_domain_from_sources(
        self,
        requirement_text: str,
        context_meta: StrategyContextMeta,
        candidates: List[Any],
    ) -> str:
        for value in candidates:
            normalized = _normalize_business_domain(value, requirement_text=requirement_text)
            if normalized in _ALLOWED_DOMAINS and normalized != "通用":
                return normalized
        return _normalize_business_domain(context_meta.business_domain_hint, requirement_text=requirement_text)

    def _guess_business_domain(
        self,
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]],
        testcase_result: Optional[Dict[str, Any]],
    ) -> str:
        candidates = [
            (analysis_result or {}).get("business_domain"),
            (analysis_result or {}).get("domain"),
            (testcase_result or {}).get("business_domain"),
            (testcase_result or {}).get("domain"),
            requirement_text[:500],
        ]
        return self._decide_domain_from_sources(
            requirement_text=requirement_text,
            context_meta=StrategyContextMeta(
                has_requirement=bool(requirement_text),
                has_analysis_result=bool(analysis_result),
                has_testcase_result=bool(testcase_result),
                requirement_length=len(requirement_text or ""),
                business_domain_hint="通用",
                source_types=[],
            ),
            candidates=candidates,
        )

    def _build_source_types(
        self,
        requirement_text: str,
        analysis_result: Optional[Dict[str, Any]],
        testcase_result: Optional[Dict[str, Any]],
    ) -> List[str]:
        result = []
        if requirement_text:
            result.append("requirement")
        if analysis_result:
            result.append("analysis")
        if testcase_result:
            result.append("testcase")
        return result

    def _decide_final_business_domain(
        self,
        requirement_text: str,
        context_meta: StrategyContextMeta,
        impact_data: Dict[str, Any],
        risk_data: Dict[str, Any],
        scope_data: Dict[str, Any],
        strategy_data: Dict[str, Any],
        rule_strategy: Dict[str, Any],
    ) -> str:
        return self._decide_domain_from_sources(
            requirement_text=requirement_text,
            context_meta=context_meta,
            candidates=[
                strategy_data.get("business_domain"),
                impact_data.get("business_domain"),
                scope_data.get("business_domain"),
                risk_data.get("business_domain"),
                (rule_strategy.get("summary") or {}).get("business_domain"),
                context_meta.business_domain_hint,
            ],
        )

    def _final_business_clean(
        self,
        normalized_payload: Dict[str, Any],
        context_meta: StrategyContextMeta,
        requirement_text: str,
    ) -> Dict[str, Any]:
        payload = dict(normalized_payload or {})
        domain = self._decide_domain_from_sources(
            requirement_text=requirement_text,
            context_meta=context_meta,
            candidates=[payload.get("business_domain"), context_meta.business_domain_hint],
        )
        payload["business_domain"] = domain
        payload["overall_risk"] = _normalize_overall_risk(payload.get("overall_risk"))
        payload["change_scope"] = _normalize_change_scope(payload.get("change_scope"))

        payload["risk_items"] = _dedupe_risks_semantic(_ensure_list(payload.get("risk_items")))
        payload["core_reason"] = _compress_core_reason(_ensure_list(payload.get("core_reason")))

        payload["quality_gate"] = _fix_quality_gate(payload)
        payload["exit_criteria"] = _fix_exit_criteria(_ensure_list(payload.get("exit_criteria")))
        payload["release_strategy"] = _fix_release_strategy(_ensure_list(payload.get("release_strategy")))

        return payload

    # =====================================================
    # builders
    # =====================================================

    def _build_final_result(
        self,
        normalized_payload: Dict[str, Any],
        context_meta: StrategyContextMeta,
        raw_agent_outputs: Dict[str, Any],
    ) -> StrategyResult:
        payload = dict(normalized_payload or {})

        summary = StrategySummary(
            business_domain=payload.get("business_domain"),
            change_scope=payload.get("change_scope"),
            overall_risk=payload.get("overall_risk"),
            core_reason=_ensure_list(payload.get("core_reason")),
            test_objectives=_ensure_list(payload.get("test_objectives")),
        )

        payload["summary"] = summary
        payload["context_meta"] = context_meta
        payload["raw_agent_outputs"] = raw_agent_outputs

        result = StrategyResult(**payload)
        return result

    def _build_markdown(self, result: StrategyResult) -> str:
        summary = getattr(result, "summary", None)
        business_domain = getattr(summary, "business_domain", "") if summary else ""
        change_scope = getattr(summary, "change_scope", "") if summary else ""
        overall_risk = getattr(summary, "overall_risk", "") if summary else ""

        lines = [
            "# 测试策略报告",
            "",
            "## 一、概览",
            f"- 业务域：{business_domain or '-'}",
            f"- 变更范围：{change_scope or '-'}",
            f"- 整体风险：{overall_risk or '-'}",
            "",
            "## 二、核心原因",
        ]

        for item in _ensure_list(getattr(summary, "core_reason", []) if summary else []):
            lines.append(f"- {item}")

        lines.extend(["", "## 三、受影响模块"])
        for item in _ensure_list(getattr(result, "impact_modules", [])):
            if isinstance(item, dict):
                lines.append(f"- {item.get('name')}: {item.get('reason') or ''}")
            else:
                lines.append(f"- {item}")

        lines.extend(["", "## 四、风险项"])
        for item in _ensure_list(getattr(result, "risk_items", [])):
            if isinstance(item, dict):
                lines.append(f"- [{item.get('level')}] {item.get('title')}：{item.get('reason') or ''}")
            else:
                lines.append(f"- {item}")

        lines.extend(["", "## 五、必测范围"])
        for item in _ensure_list(getattr(result, "must_test", [])):
            if isinstance(item, dict):
                lines.append(f"- [{item.get('priority')}] {item.get('title')}：{item.get('reason') or ''}")
            else:
                lines.append(f"- {item}")

        lines.extend(["", "## 六、回归范围"])
        for item in _ensure_list(getattr(result, "regression_scope", [])):
            if isinstance(item, dict):
                lines.append(f"- [{item.get('priority')}] {item.get('title')}：{item.get('reason') or ''}")
            else:
                lines.append(f"- {item}")

        lines.extend(["", "## 七、测试类型建议"])
        for item in _ensure_list(getattr(result, "test_type_matrix", [])):
            if isinstance(item, dict):
                lines.append(f"- {item.get('type_name')}：{item.get('reason') or ''}")
            else:
                lines.append(f"- {item}")

        lines.extend(["", "## 八、发布建议"])
        for item in _ensure_list(getattr(result, "release_strategy", [])):
            if isinstance(item, dict):
                lines.append(f"- {item.get('title')}：{item.get('reason') or ''}")
            else:
                lines.append(f"- {item}")

        gate = getattr(result, "quality_gate", None)
        if gate:
            lines.extend(["", "## 九、质量门禁"])
            if isinstance(gate, dict):
                lines.append(f"- 决策：{gate.get('decision')}")
                for x in _ensure_list(gate.get("reasons")):
                    lines.append(f"- 原因：{x}")
                for x in _ensure_list(gate.get("required_actions")):
                    lines.append(f"- 动作：{x}")
            else:
                lines.append(f"- {gate}")

        return "\n".join(lines).strip()

    # =====================================================
    # misc
    # =====================================================

    def _recalculate_overall_risk(self, risk_items: List[Dict[str, Any]], current: Any) -> str:
        items = [x for x in _ensure_list(risk_items) if isinstance(x, dict)]
        if not items:
            return _normalize_overall_risk(current)

        levels = [_normalize_priority(x.get("level"), default="P2") for x in items]
        if "P0" in levels or "P1" in levels:
            return "高"
        if "P2" in levels:
            return "中"
        return "低"

    def _risk_dedupe_key(self, title: str, reason: str) -> str:
        return _normalize_text_for_key(f"{title} {reason}")

    def _is_noise_risk_item(self, title: str, reason: str, suggestion: str) -> bool:
        title_clean = _clean_sentence(title)
        if not title_clean:
            return True
        if title_clean in _LOW_VALUE_TITLES and not _clean_sentence(reason):
            return True
        merged = f"{title} {reason} {suggestion}"
        return _is_noise_text(merged)

    def _is_noise_scope_item(self, title: str, reason: str) -> bool:
        title_clean = _clean_sentence(title)
        if not title_clean:
            return True
        if title_clean in _LOW_VALUE_TITLES and not _clean_sentence(reason):
            return True
        return _is_noise_text(f"{title} {reason}")