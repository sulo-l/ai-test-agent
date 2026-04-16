#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/3/25 21:19
# @Author: sulo
#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/utils/rules.py

from __future__ import annotations

from typing import Dict, List, Any


# =====================================================
# 1. 业务域识别规则
# =====================================================

BUSINESS_DOMAIN_RULES = {
    "登录注册": ["登录", "注册", "验证码", "密码", "账号"],
    "现货交易": ["现货", "买入", "卖出", "撮合", "订单簿"],
    "合约交易": ["合约", "杠杆", "爆仓", "强平", "保证金"],
    "充提": ["充值", "提现", "地址", "链", "到账"],
    "资金划转": ["划转", "账户", "资金", "余额"],
    "P2P": ["p2p", "商户", "广告", "法币"],
    "跟单": ["跟单", "带单", "交易员"],
    "风控": ["风控", "限额", "冻结", "校验"],
    "账户": ["账户", "资产", "余额"],
}


def detect_business_domain(text: str) -> str:
    text = (text or "").lower()

    for domain, keywords in BUSINESS_DOMAIN_RULES.items():
        for kw in keywords:
            if kw in text:
                return domain

    return "通用业务"


# =====================================================
# 2. 风险识别规则（核心）
# =====================================================

RISK_RULES = [
    {
        "type": "资金安全",
        "keywords": ["提现", "充值", "资金", "余额"],
        "level": "高",
        "desc": "涉及资金安全，必须严格校验金额与账户一致性",
    },
    {
        "type": "交易风险",
        "keywords": ["撮合", "订单", "成交"],
        "level": "高",
        "desc": "涉及撮合与订单状态一致性",
    },
    {
        "type": "权限风险",
        "keywords": ["权限", "登录", "鉴权"],
        "level": "高",
        "desc": "涉及访问控制与安全风险",
    },
    {
        "type": "状态流转",
        "keywords": ["状态", "审核", "流转"],
        "level": "中",
        "desc": "涉及状态机完整性",
    },
    {
        "type": "UI/展示",
        "keywords": ["展示", "页面", "列表"],
        "level": "低",
        "desc": "主要影响用户体验",
    },
]


def detect_risks(text: str) -> List[Dict[str, Any]]:
    text = (text or "").lower()
    result = []

    for rule in RISK_RULES:
        if any(k in text for k in rule["keywords"]):
            result.append({
                "risk_type": rule["type"],
                "level": rule["level"],
                "desc": rule["desc"],
            })

    return result


# =====================================================
# 3. 测试范围规则（核心企业级能力）
# =====================================================

TEST_SCOPE_RULES = [
    {
        "dimension": "功能测试",
        "cases": ["正常流程", "异常流程", "边界值"],
    },
    {
        "dimension": "资金校验",
        "cases": ["金额精度", "余额扣减", "到账一致性"],
    },
    {
        "dimension": "状态流转",
        "cases": ["状态变更", "重复操作", "回滚"],
    },
    {
        "dimension": "权限校验",
        "cases": ["未登录", "越权访问", "接口权限"],
    },
    {
        "dimension": "接口异常",
        "cases": ["超时", "失败重试", "幂等"],
    },
    {
        "dimension": "性能",
        "cases": ["高并发", "响应时间"],
    },
]


def build_test_scope(domain: str) -> List[Dict[str, Any]]:
    """
    根据业务域生成测试范围
    """
    base = TEST_SCOPE_RULES.copy()

    # 交易类增强
    if domain in {"现货交易", "合约交易"}:
        base.append({
            "dimension": "撮合验证",
            "cases": ["价格优先", "时间优先", "成交一致性"],
        })

    # 资金类增强
    if domain in {"充提", "资金划转"}:
        base.append({
            "dimension": "资金安全",
            "cases": ["重复提交", "金额校验", "跨账户"],
        })

    return base


# =====================================================
# 4. 必测项规则（企业级重点）
# =====================================================

def build_must_test_points(domain: str, risks: List[Dict[str, Any]]) -> List[str]:
    result = []

    for r in risks:
        if r["level"] == "高":
            result.append(f"{r['risk_type']}必须重点验证")

    if domain in {"充提", "资金划转"}:
        result.append("资金一致性必须验证")

    if domain in {"合约交易"}:
        result.append("爆仓逻辑必须验证")

    if domain in {"登录注册"}:
        result.append("账号安全必须验证")

    return list(set(result))


# =====================================================
# 5. 回归范围规则
# =====================================================

REGRESSION_RULES = {
    "交易": ["下单", "撮合", "订单查询"],
    "资金": ["余额", "流水", "资产"],
    "账户": ["登录", "权限"],
}


def build_regression_scope(domain: str) -> List[str]:
    result = []

    if domain in {"现货交易", "合约交易"}:
        result.extend(REGRESSION_RULES["交易"])

    if domain in {"充提", "资金划转"}:
        result.extend(REGRESSION_RULES["资金"])

    result.extend(REGRESSION_RULES["账户"])

    return list(set(result))


# =====================================================
# 6. 执行策略规则
# =====================================================

def build_execution_strategy(risks: List[Dict[str, Any]]) -> Dict[str, Any]:
    high_risk = any(r["level"] == "高" for r in risks)

    return {
        "test_level": "严格" if high_risk else "常规",
        "need_automation": True,
        "need_regression": True,
        "need_gray_release": high_risk,
        "need_monitor": True,
    }


# =====================================================
# 7. 汇总（核心入口）
# =====================================================

def build_strategy_by_rules(requirement_text: str) -> Dict[str, Any]:
    """
    核心规则引擎入口
    """
    domain = detect_business_domain(requirement_text)
    risks = detect_risks(requirement_text)

    test_scope = build_test_scope(domain)
    must_test = build_must_test_points(domain, risks)
    regression = build_regression_scope(domain)
    execution = build_execution_strategy(risks)

    return {
        "summary": {
            "business_domain": domain,
            "risk_level": "高" if any(r["level"] == "高" for r in risks) else "中",
        },
        "risks": risks,
        "test_scope": test_scope,
        "must_test": must_test,
        "regression_scope": regression,
        "execution_strategy": execution,
    }