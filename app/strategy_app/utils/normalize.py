#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/utils/normalize.py

from __future__ import annotations

from typing import Any, Dict, List, Optional


# =====================================================
# 基础工具
# =====================================================

def ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def pick_first_str(*values: Any, default: str = "") -> str:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


def pick_first_non_empty(*values: Any, default: Any = None) -> Any:
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


def dedupe_str_list(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for x in items or []:
        s = str(x or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        result.append(s)
    return result


def normalize_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"true", "1", "yes", "y", "on"}:
            return True
        if s in {"false", "0", "no", "n", "off"}:
            return False
    return default


# =====================================================
# 枚举标准化
# =====================================================

def normalize_priority(value: Any, default: str = "P2") -> str:
    s = str(value or "").strip().upper()
    if s in {"P0", "P1", "P2", "P3"}:
        return s
    if s in {"BLOCKER", "CRITICAL"}:
        return "P0"
    if s in {"HIGH", "高", "严重"}:
        return "P1"
    if s in {"MEDIUM", "中"}:
        return "P2"
    if s in {"LOW", "低"}:
        return "P3"
    return default


def priority_rank(value: Any) -> int:
    p = normalize_priority(value, default="P2")
    mapping = {
        "P0": 0,
        "P1": 1,
        "P2": 2,
        "P3": 3,
    }
    return mapping.get(p, 99)


def normalize_risk_level(value: Any, default: str = "P2") -> str:
    return normalize_priority(value, default=default)


def risk_rank(value: Any) -> int:
    return priority_rank(value)


def normalize_overall_risk(value: Any, default: str = "中") -> str:
    s = str(value or "").strip()
    if s in {"高", "中", "低"}:
        return s

    s2 = s.upper()
    if s2 in {"P0", "P1", "HIGH", "BLOCKER", "CRITICAL"}:
        return "高"
    if s2 in {"P2", "MEDIUM"}:
        return "中"
    if s2 in {"P3", "LOW"}:
        return "低"

    return default


def normalize_change_scope(value: Any, default: str = "中") -> str:
    s = str(value or "").strip()
    if s in {"大", "中", "小"}:
        return s

    s2 = s.upper()
    if s2 in {"LARGE", "HIGH"}:
        return "大"
    if s2 in {"MEDIUM", "MID"}:
        return "中"
    if s2 in {"SMALL", "LOW"}:
        return "小"

    return default


def normalize_gate_decision(value: Any, default: str = "conditional_pass") -> str:
    s = str(value or "").strip().lower()
    if s in {"pass", "conditional_pass", "fail"}:
        return s
    if s in {"通过", "ok"}:
        return "pass"
    if s in {"有条件通过", "conditional"}:
        return "conditional_pass"
    if s in {"失败", "不通过", "reject"}:
        return "fail"
    return default


def normalize_business_domain(value: Any, requirement_text: str = "") -> str:
    s = str(value or "").strip()
    allowed = {
        "登录注册", "用户中心", "现货", "合约", "充值", "提现", "划转",
        "P2P", "跟单", "撮合", "风控", "KYC", "资产", "通用"
    }
    if s in allowed:
        return s

    text = f"{s} {(requirement_text or '').strip()}".lower()

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
        ("资产", ["资产", "余额", "冻结", "流水", "账变", "asset", "balance"]),
    ]

    for domain, keywords in rules:
        if any(k.lower() in text for k in keywords):
            return domain

    return "通用"


def normalize_test_type_name(value: Any) -> Optional[str]:
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

        "integration": "联调测试",
        "联调": "联调测试",
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


# =====================================================
# 各类 item 归一化
# =====================================================

def normalize_impact_module_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    name = pick_first_str(item.get("name"), item.get("title"))
    if not name:
        return None

    return {
        "name": name,
        "reason": pick_first_str(item.get("reason"), default=""),
        "level": pick_first_str(item.get("level"), default="中") or "中",
        "direct": normalize_bool(item.get("direct"), True),
        "upstream": normalize_bool(item.get("upstream"), False),
        "downstream": normalize_bool(item.get("downstream"), False),
    }


def normalize_impact_role_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    name = pick_first_str(item.get("name"), item.get("title"))
    if not name:
        return None

    return {
        "name": name,
        "reason": pick_first_str(item.get("reason"), default=""),
        "permissions": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("permissions")) if str(x).strip()]
        ),
    }


def normalize_affected_flow_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    name = pick_first_str(item.get("name"), item.get("title"))
    if not name:
        return None

    return {
        "name": name,
        "steps": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("steps")) if str(x).strip()]
        ),
        "reason": pick_first_str(item.get("reason"), default=""),
        "level": pick_first_str(item.get("level"), default="中") or "中",
        "is_core": normalize_bool(item.get("is_core"), False),
    }


def normalize_risk_item(item: Dict[str, Any], idx: int = 1, requirement_text: str = "") -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    category = pick_first_str(item.get("category"), default="一般风险")
    reason = pick_first_str(item.get("reason"), default="")

    test_types = dedupe_str_list(
        [
            normalize_test_type_name(x) or ""
            for x in ensure_list(item.get("test_types"))
            if str(x).strip()
        ]
    )
    test_types = [x for x in test_types if x]

    return {
        "risk_id": pick_first_str(item.get("risk_id"), default=f"RISK-{idx:03d}"),
        "title": title,
        "level": normalize_risk_level(item.get("level")),
        "category": category,
        "reason": reason,
        "trigger_condition": pick_first_str(item.get("trigger_condition"), default=""),
        "impact": pick_first_str(item.get("impact"), default=""),
        "suggestion": pick_first_str(item.get("suggestion"), default=""),
        "related_modules": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("related_modules")) if str(x).strip()]
        ),
        "related_flows": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("related_flows")) if str(x).strip()]
        ),
        "test_types": test_types,
        "automation_candidate": normalize_bool(item.get("automation_candidate"), False),
        "affects_release_gate": normalize_bool(
            item.get("affects_release_gate"),
            normalize_risk_level(item.get("level")) in {"P0", "P1"},
        ),
    }


def normalize_scope_item(item: Dict[str, Any], requirement_text: str = "") -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    test_types = dedupe_str_list(
        [
            normalize_test_type_name(x) or ""
            for x in ensure_list(item.get("test_types"))
            if str(x).strip()
        ]
    )
    test_types = [x for x in test_types if x]

    return {
        "title": title,
        "reason": pick_first_str(item.get("reason"), default=""),
        "priority": normalize_priority(item.get("priority"), default="P2"),
        "related_modules": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("related_modules")) if str(x).strip()]
        ),
        "related_flows": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("related_flows")) if str(x).strip()]
        ),
        "test_types": test_types,
        "owner": pick_first_str(item.get("owner"), default="测试"),
    }


def normalize_layer_advice_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    return {
        "title": title,
        "reason": pick_first_str(item.get("reason"), default=""),
        "related_scope": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("related_scope")) if str(x).strip()]
        ),
        "related_risks": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("related_risks")) if str(x).strip()]
        ),
        "priority": normalize_priority(item.get("priority"), default="P1"),
    }


def normalize_test_type_matrix_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    type_name = normalize_test_type_name(
        pick_first_str(item.get("type_name"), item.get("name"))
    )
    if not type_name:
        return None

    return {
        "type_name": type_name,
        "necessary": normalize_bool(item.get("necessary"), True),
        "priority": normalize_priority(item.get("priority"), default="P1"),
        "scope": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("scope")) if str(x).strip()]
        ),
        "reason": pick_first_str(item.get("reason"), default=""),
        "automation_candidate": normalize_bool(item.get("automation_candidate"), False),
        "related_risks": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("related_risks")) if str(x).strip()]
        ),
    }


def normalize_environment_strategy_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    env_name = pick_first_str(item.get("env_name"), item.get("name"))
    if not env_name:
        return None

    return {
        "env_name": env_name,
        "purpose": pick_first_str(item.get("purpose"), default=""),
        "required": normalize_bool(item.get("required"), True),
        "notes": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("notes")) if str(x).strip()]
        ),
    }


def normalize_test_data_strategy_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    return {
        "title": title,
        "data_type": pick_first_str(item.get("data_type"), default=""),
        "purpose": pick_first_str(item.get("purpose"), default=""),
        "required": normalize_bool(item.get("required"), True),
        "notes": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("notes")) if str(x).strip()]
        ),
    }


def normalize_automation_strategy_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    return {
        "title": title,
        "scope": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("scope")) if str(x).strip()]
        ),
        "priority": normalize_priority(item.get("priority"), default="P1"),
        "reason": pick_first_str(item.get("reason"), default=""),
        "framework_hint": pick_first_str(item.get("framework_hint"), default=""),
    }


def normalize_regression_strategy_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    return {
        "title": title,
        "scope": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("scope")) if str(x).strip()]
        ),
        "reason": pick_first_str(item.get("reason"), default=""),
        "priority": normalize_priority(item.get("priority"), default="P1"),
    }


def normalize_release_strategy_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    return {
        "title": title,
        "reason": pick_first_str(item.get("reason"), default=""),
        "required": normalize_bool(item.get("required"), False),
        "notes": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("notes")) if str(x).strip()]
        ),
    }


def normalize_rollback_strategy_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    return {
        "title": title,
        "trigger": pick_first_str(item.get("trigger"), default=""),
        "action": pick_first_str(item.get("action"), default=""),
        "notes": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("notes")) if str(x).strip()]
        ),
    }


def normalize_entry_exit_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    return {
        "title": title,
        "required": normalize_bool(item.get("required"), True),
        "reason": pick_first_str(item.get("reason"), default=""),
        "owner": pick_first_str(item.get("owner"), default=""),
    }


def normalize_resource_plan_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    return {
        "title": title,
        "scope": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("scope")) if str(x).strip()]
        ),
        "focus": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("focus")) if str(x).strip()]
        ),
        "note": pick_first_str(item.get("note"), default=""),
    }


def normalize_execution_order_item(item: Dict[str, Any], idx: int = 1) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    try:
        order = int(item.get("order"))
    except Exception:
        order = idx

    return {
        "order": order,
        "title": title,
        "reason": pick_first_str(item.get("reason"), default=""),
        "related_scope": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("related_scope")) if str(x).strip()]
        ),
        "related_risks": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("related_risks")) if str(x).strip()]
        ),
        "blocking": normalize_bool(item.get("blocking"), False),
    }


def normalize_blocker_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    return {
        "title": title,
        "reason": pick_first_str(item.get("reason"), default=""),
        "owner": pick_first_str(item.get("owner"), default=""),
        "suggestion": pick_first_str(item.get("suggestion"), default=""),
        "severity": pick_first_str(item.get("severity"), default=""),
    }


def normalize_pending_confirmation_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    return {
        "title": title,
        "reason": pick_first_str(item.get("reason"), default=""),
        "owner": pick_first_str(item.get("owner"), default=""),
        "impact": pick_first_str(item.get("impact"), default=""),
        "blocking": normalize_bool(item.get("blocking"), False),
    }


def normalize_release_checklist_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    title = pick_first_str(item.get("title"), item.get("name"))
    if not title:
        return None

    return {
        "title": title,
        "reason": pick_first_str(item.get("reason"), default=""),
        "required": normalize_bool(item.get("required"), True),
        "owner": pick_first_str(item.get("owner"), default=""),
        "related_risks": dedupe_str_list(
            [str(x).strip() for x in ensure_list(item.get("related_risks")) if str(x).strip()]
        ),
    }


# =====================================================
# 去重函数
# =====================================================

def dedupe_by_key(items: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    uniq: Dict[str, Dict[str, Any]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        k = pick_first_str(item.get(key), default="")
        if not k:
            continue
        if k not in uniq:
            uniq[k] = item
        else:
            old = uniq[k]
            # 通用合并逻辑
            for list_key in [
                "related_modules",
                "related_flows",
                "test_types",
                "permissions",
                "scope",
                "focus",
                "related_scope",
                "related_risks",
                "notes",
            ]:
                if list_key in old or list_key in item:
                    old[list_key] = dedupe_str_list(
                        ensure_list(old.get(list_key)) + ensure_list(item.get(list_key))
                    )

            if "priority" in old or "priority" in item:
                if priority_rank(item.get("priority")) < priority_rank(old.get("priority")):
                    old["priority"] = normalize_priority(item.get("priority"), default=old.get("priority", "P2"))

            if "level" in old or "level" in item:
                # 风险 level 用 P0-P3；impact level 用 高中低，后者不动
                old_level = str(old.get("level") or "")
                new_level = str(item.get("level") or "")
                if old_level in {"P0", "P1", "P2", "P3"} or new_level in {"P0", "P1", "P2", "P3"}:
                    if risk_rank(item.get("level")) < risk_rank(old.get("level")):
                        old["level"] = normalize_risk_level(item.get("level"))

            for bool_key in [
                "direct", "upstream", "downstream",
                "is_core", "blocking",
                "necessary", "required",
                "automation_candidate", "affects_release_gate",
            ]:
                if bool_key in old or bool_key in item:
                    old[bool_key] = normalize_bool(old.get(bool_key), False) or normalize_bool(item.get(bool_key), False)

            for str_key in [
                "reason", "impact", "suggestion", "trigger_condition",
                "framework_hint", "owner", "severity", "action",
            ]:
                if not pick_first_str(old.get(str_key)):
                    old[str_key] = pick_first_str(item.get(str_key), default=pick_first_str(old.get(str_key), default=""))

    return list(uniq.values())


# =====================================================
# 批量 normalize
# =====================================================

def normalize_impact_modules(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_impact_module_item(item)
        if x:
            arr.append(x)
    return dedupe_by_key(arr, "name")


def normalize_impact_roles(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_impact_role_item(item)
        if x:
            arr.append(x)
    return dedupe_by_key(arr, "name")


def normalize_affected_flows(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_affected_flow_item(item)
        if x:
            arr.append(x)
    return dedupe_by_key(arr, "name")


def normalize_risk_items(items: Any, requirement_text: str = "") -> List[Dict[str, Any]]:
    arr = []
    for idx, item in enumerate(ensure_list(items), start=1):
        x = normalize_risk_item(item, idx=idx, requirement_text=requirement_text)
        if x:
            arr.append(x)
    arr = dedupe_by_key(arr, "title")
    arr.sort(key=lambda x: risk_rank(x.get("level")))
    for idx, item in enumerate(arr, start=1):
        item["risk_id"] = f"RISK-{idx:03d}"
    return arr


def normalize_scope_items(items: Any, requirement_text: str = "") -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_scope_item(item, requirement_text=requirement_text)
        if x:
            arr.append(x)
    arr = dedupe_by_key(arr, "title")
    arr.sort(key=lambda x: priority_rank(x.get("priority")))
    return arr


def normalize_layer_advice_items(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_layer_advice_item(item)
        if x:
            arr.append(x)
    arr = dedupe_by_key(arr, "title")
    arr.sort(key=lambda x: priority_rank(x.get("priority")))
    return arr


def normalize_test_type_matrix(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_test_type_matrix_item(item)
        if x:
            arr.append(x)
    arr = dedupe_by_key(arr, "type_name")
    arr.sort(key=lambda x: priority_rank(x.get("priority")))
    return arr


def normalize_environment_strategy(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_environment_strategy_item(item)
        if x:
            arr.append(x)
    return dedupe_by_key(arr, "env_name")


def normalize_test_data_strategy(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_test_data_strategy_item(item)
        if x:
            arr.append(x)
    return dedupe_by_key(arr, "title")


def normalize_automation_strategy(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_automation_strategy_item(item)
        if x:
            arr.append(x)
    arr = dedupe_by_key(arr, "title")
    arr.sort(key=lambda x: priority_rank(x.get("priority")))
    return arr


def normalize_regression_strategy(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_regression_strategy_item(item)
        if x:
            arr.append(x)
    arr = dedupe_by_key(arr, "title")
    arr.sort(key=lambda x: priority_rank(x.get("priority")))
    return arr


def normalize_release_strategy(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_release_strategy_item(item)
        if x:
            arr.append(x)
    return dedupe_by_key(arr, "title")


def normalize_rollback_strategy(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_rollback_strategy_item(item)
        if x:
            arr.append(x)
    return dedupe_by_key(arr, "title")


def normalize_entry_criteria(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_entry_exit_item(item)
        if x:
            arr.append(x)
    return dedupe_by_key(arr, "title")


def normalize_exit_criteria(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_entry_exit_item(item)
        if x:
            arr.append(x)
    return dedupe_by_key(arr, "title")


def normalize_resource_plan_items(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_resource_plan_item(item)
        if x:
            arr.append(x)
    return dedupe_by_key(arr, "title")


def normalize_execution_order(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for idx, item in enumerate(ensure_list(items), start=1):
        x = normalize_execution_order_item(item, idx=idx)
        if x:
            arr.append(x)
    arr = dedupe_by_key(arr, "title")
    arr.sort(key=lambda x: int(x.get("order") or 0))
    return arr


def normalize_blockers(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_blocker_item(item)
        if x:
            arr.append(x)
    return dedupe_by_key(arr, "title")


def normalize_pending_confirmations(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_pending_confirmation_item(item)
        if x:
            arr.append(x)
    return dedupe_by_key(arr, "title")


def normalize_release_checklist(items: Any) -> List[Dict[str, Any]]:
    arr = []
    for item in ensure_list(items):
        x = normalize_release_checklist_item(item)
        if x:
            arr.append(x)
    return dedupe_by_key(arr, "title")


def normalize_quality_gate(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {
            "decision": "conditional_pass",
            "reasons": [],
            "blocker_risks": [],
            "required_actions": [],
        }

    return {
        "decision": normalize_gate_decision(data.get("decision"), default="conditional_pass"),
        "reasons": dedupe_str_list(
            [str(x).strip() for x in ensure_list(data.get("reasons")) if str(x).strip()]
        ),
        "blocker_risks": dedupe_str_list(
            [str(x).strip() for x in ensure_list(data.get("blocker_risks")) if str(x).strip()]
        ),
        "required_actions": dedupe_str_list(
            [str(x).strip() for x in ensure_list(data.get("required_actions")) if str(x).strip()]
        ),
    }


# =====================================================
# 汇总 normalize（给 pipeline / controller 用）
# =====================================================

def normalize_strategy_payload(payload: Dict[str, Any], requirement_text: str = "") -> Dict[str, Any]:
    """
    把 strategy agent / pipeline 输出做统一清洗
    """
    if not isinstance(payload, dict):
        return {}

    result: Dict[str, Any] = {
        "business_domain": normalize_business_domain(
            pick_first_non_empty(payload.get("business_domain"), default="通用"),
            requirement_text=requirement_text,
        ),
        "change_scope": normalize_change_scope(payload.get("change_scope"), default="中"),
        "overall_risk": normalize_overall_risk(payload.get("overall_risk"), default="中"),

        "core_reason": dedupe_str_list(
            [str(x).strip() for x in ensure_list(payload.get("core_reason")) if str(x).strip()]
        ),
        "test_objectives": dedupe_str_list(
            [str(x).strip() for x in ensure_list(payload.get("test_objectives")) if str(x).strip()]
        ),

        "impact_modules": normalize_impact_modules(payload.get("impact_modules")),
        "impact_roles": normalize_impact_roles(payload.get("impact_roles")),
        "affected_flows": normalize_affected_flows(payload.get("affected_flows")),

        "risk_items": normalize_risk_items(payload.get("risk_items"), requirement_text=requirement_text),

        "must_test": normalize_scope_items(payload.get("must_test"), requirement_text=requirement_text),
        "should_test": normalize_scope_items(payload.get("should_test"), requirement_text=requirement_text),
        "defer_test": normalize_scope_items(payload.get("defer_test"), requirement_text=requirement_text),
        "out_of_scope": normalize_scope_items(payload.get("out_of_scope"), requirement_text=requirement_text),
        "smoke_scope": normalize_scope_items(payload.get("smoke_scope"), requirement_text=requirement_text),
        "regression_scope": normalize_scope_items(payload.get("regression_scope"), requirement_text=requirement_text),

        "test_layer_advice": {
            "ui": normalize_layer_advice_items((payload.get("test_layer_advice") or {}).get("ui")),
            "api": normalize_layer_advice_items((payload.get("test_layer_advice") or {}).get("api")),
            "service": normalize_layer_advice_items((payload.get("test_layer_advice") or {}).get("service")),
            "db": normalize_layer_advice_items((payload.get("test_layer_advice") or {}).get("db")),
            "e2e": normalize_layer_advice_items((payload.get("test_layer_advice") or {}).get("e2e")),
            "manual": normalize_layer_advice_items((payload.get("test_layer_advice") or {}).get("manual")),
            "automation_candidate": normalize_layer_advice_items((payload.get("test_layer_advice") or {}).get("automation_candidate")),
        },

        "test_type_matrix": normalize_test_type_matrix(payload.get("test_type_matrix")),
        "environment_strategy": normalize_environment_strategy(payload.get("environment_strategy")),
        "test_data_strategy": normalize_test_data_strategy(payload.get("test_data_strategy")),
        "automation_strategy": normalize_automation_strategy(payload.get("automation_strategy")),
        "regression_strategy": normalize_regression_strategy(payload.get("regression_strategy")),
        "release_strategy": normalize_release_strategy(payload.get("release_strategy")),
        "rollback_strategy": normalize_rollback_strategy(payload.get("rollback_strategy")),

        "entry_criteria": normalize_entry_criteria(payload.get("entry_criteria")),
        "exit_criteria": normalize_exit_criteria(payload.get("exit_criteria")),

        "resource_plan": {
            "one_day": normalize_resource_plan_items((payload.get("resource_plan") or {}).get("one_day")),
            "two_days": normalize_resource_plan_items((payload.get("resource_plan") or {}).get("two_days")),
            "three_days": normalize_resource_plan_items((payload.get("resource_plan") or {}).get("three_days")),
            "five_days": normalize_resource_plan_items((payload.get("resource_plan") or {}).get("five_days")),
        },

        "execution_order": normalize_execution_order(payload.get("execution_order")),
        "blockers": normalize_blockers(payload.get("blockers")),
        "pending_confirmations": normalize_pending_confirmations(payload.get("pending_confirmations")),
        "release_checklist": normalize_release_checklist(payload.get("release_checklist")),
        "quality_gate": normalize_quality_gate(payload.get("quality_gate")),

        "assumptions": dedupe_str_list(
            [str(x).strip() for x in ensure_list(payload.get("assumptions")) if str(x).strip()]
        ),
        "notes": dedupe_str_list(
            [str(x).strip() for x in ensure_list(payload.get("notes")) if str(x).strip()]
        ),
    }

    return result


# =====================================================
# 统计辅助
# =====================================================

def build_strategy_metrics(payload: Dict[str, Any]) -> Dict[str, int]:
    if not isinstance(payload, dict):
        return {
            "impact_module_count": 0,
            "impact_flow_count": 0,
            "risk_count": 0,
            "must_test_count": 0,
            "regression_scope_count": 0,
            "blocker_count": 0,
            "pending_confirmation_count": 0,
        }

    return {
        "impact_module_count": len(payload.get("impact_modules") or []),
        "impact_flow_count": len(payload.get("affected_flows") or []),
        "risk_count": len(payload.get("risk_items") or []),
        "must_test_count": len(payload.get("must_test") or []),
        "regression_scope_count": len(payload.get("regression_scope") or []),
        "blocker_count": len(payload.get("blockers") or []),
        "pending_confirmation_count": len(payload.get("pending_confirmations") or []),
    }