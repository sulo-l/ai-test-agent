#! /usr/bin/python3
# coding=utf-8
# app/testcase_app/agents/coverage_agent.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.llm.client import LLM

logger = logging.getLogger(__name__)

TestPoint = Dict[str, Any]

# =========================
# 维度规范化
# =========================
_DEFAULT_TARGETS = ["Happy", "Negative", "UI", "Input", "NFR", "Security", "Compat"]


def normalize_dimension(dim: str) -> str:
    d = (dim or "").strip().lower()
    if not d:
        return "Happy"
    if d.startswith("neg") or "异常" in d or "反向" in d:
        return "Negative"
    if d.startswith("ui") or "交互" in d:
        return "UI"
    if d.startswith("input") or "输入" in d or "参数" in d:
        return "Input"
    if d.startswith("nfr") or d.startswith("perf") or d.startswith("non") or "性能" in d or "非功能" in d:
        return "NFR"
    if d.startswith("sec") or "安全" in d or "权限" in d:
        return "Security"
    if d.startswith("comp") or "兼容" in d:
        return "Compat"
    if d.startswith("happy") or d.startswith("pos") or "正常" in d:
        return "Happy"
    return dim[:1].upper() + dim[1:]


def _normalize_priority(value: Any) -> str:
    s = str(value or "").strip().upper()
    if s in {"P0", "P1", "P2"}:
        return s
    if s in {"0", "1", "2"}:
        return f"P{s}"
    if "0" in s or "高" in s:
        return "P0"
    if "2" in s or "低" in s:
        return "P2"
    return "P1"


def _normalize_risk(value: Any) -> str:
    s = str(value or "").strip()
    if s in {"高", "中", "低"}:
        return s
    s_lower = s.lower()
    if "high" in s_lower:
        return "高"
    if "low" in s_lower:
        return "低"
    return "中"


def _normalize_flow_type(tp_type: Any, group: Any) -> Tuple[str, str]:
    tt = str(tp_type or "").strip().lower()
    gg = str(group or "").strip()

    if tt in {"normal", "exception", "boundary"}:
        if not gg:
            gg = {
                "normal": "正常流程",
                "exception": "异常流程",
                "boundary": "边界条件",
            }[tt]
        return tt, gg

    if "异常" in gg:
        return "exception", gg
    if "边界" in gg:
        return "boundary", gg
    return "normal", gg or "正常流程"


def _tag_from_priority(priority: Any) -> str:
    return "冒烟测试" if _normalize_priority(priority) == "P0" else "功能测试"


def calc_coverage(
    test_points: List[TestPoint],
    targets: Optional[List[str]] = None,
) -> Dict[str, int]:
    targets = targets or _DEFAULT_TARGETS
    cnt: Dict[str, int] = {t: 0 for t in targets}

    for tp in test_points or []:
        dim = normalize_dimension(str(tp.get("dimension") or "Happy"))
        if dim not in cnt:
            cnt[dim] = 0
        cnt[dim] += 1

    return cnt


def calc_missing_dimensions(
    test_points: List[TestPoint],
    targets: Optional[List[str]] = None,
    min_per_dim: int = 3,
) -> List[str]:
    targets = targets or _DEFAULT_TARGETS
    cnt = calc_coverage(test_points, targets)
    missing = [t for t in targets if cnt.get(t, 0) < min_per_dim]
    return missing


def calc_module_coverage(
    test_points: List[TestPoint],
    targets: Optional[List[str]] = None,
) -> Dict[str, Dict[str, int]]:
    targets = targets or _DEFAULT_TARGETS
    result: Dict[str, Dict[str, int]] = {}

    for tp in test_points or []:
        module = str(tp.get("module") or "整体功能").strip() or "整体功能"
        dim = normalize_dimension(str(tp.get("dimension") or "Happy"))

        if module not in result:
            result[module] = {t: 0 for t in targets}
        if dim not in result[module]:
            result[module][dim] = 0

        result[module][dim] += 1

    return result


def calc_missing_dimensions_by_module(
    test_points: List[TestPoint],
    targets: Optional[List[str]] = None,
    min_per_dim: int = 1,
) -> Dict[str, List[str]]:
    targets = targets or _DEFAULT_TARGETS
    module_cov = calc_module_coverage(test_points, targets=targets)

    result: Dict[str, List[str]] = {}
    for module, cov in module_cov.items():
        result[module] = [t for t in targets if cov.get(t, 0) < min_per_dim]
    return result


def calc_priority_coverage(
    test_points: List[TestPoint],
) -> Dict[str, int]:
    cnt = {"P0": 0, "P1": 0, "P2": 0}
    for tp in test_points or []:
        p = _normalize_priority(tp.get("priority") or "P1")
        cnt[p] = cnt.get(p, 0) + 1
    return cnt


def calc_smoke_coverage(
    test_points: List[TestPoint],
) -> Dict[str, int]:
    smoke = 0
    non_smoke = 0
    for tp in test_points or []:
        if _normalize_priority(tp.get("priority") or "P1") == "P0":
            smoke += 1
        else:
            non_smoke += 1
    return {"smoke": smoke, "non_smoke": non_smoke}


# =========================
# JSON 行解析
# =========================
def _strip_code_fence(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()


def parse_json_lines(raw: str) -> List[Dict[str, Any]]:
    raw = _strip_code_fence(raw or "")
    out: List[Dict[str, Any]] = []

    for line in raw.splitlines():
        s = line.strip().rstrip(",")
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue

    if out:
        return out

    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for k in ("items", "data", "test_points", "points", "result"):
                v = data.get(k)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
    except Exception:
        pass

    for m in re.finditer(r"\{[\s\S]*?\}", raw, re.S):
        s = m.group(0).strip().rstrip(",")
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


# =========================
# 内部工具
# =========================
_GENERIC_TITLE_PATTERNS = [
    "功能验证",
    "流程验证",
    "页面验证",
    "功能正常",
    "流程正常",
    "页面正常",
    "结果正确",
    "逻辑正确",
    "业务正确",
    "场景正确",
    "验证功能正常",
    "验证页面展示",
    "通用异常处理",
    "基础功能验证",
    "常规流程验证",
    "异常场景反馈校验",
    "页面反馈与交互展示校验",
    "输入边界与格式校验",
    "非功能表现校验",
    "权限与安全控制校验",
    "兼容性展示校验",
]

_COMMON_MODULE_PATTERNS = [
    r"(登录|注册|找回密码|验证码|安全验证)",
    r"(充值|提币|提现|充币|提币地址|链类型|到账)",
    r"(划转|转账|资金划转|账户划转)",
    r"(现货|币币|交易对|买入|卖出|下单|撤单|撮合)",
    r"(合约|杠杆|开仓|平仓|止盈|止损|强平|爆仓)",
    r"(跟单|带单|复制交易)",
    r"(理财|申购|赎回|收益)",
    r"(订单|委托|历史|成交记录)",
    r"(个人中心|账户设置|身份认证|KYC|风控)",
    r"(活动|奖励|邀请|返佣|任务中心)",
    r"(行情|K线|图表|深度图|盘口)",
    r"(申诉|工单|客服|消息中心|通知)",
    r"(弹窗|表单|列表|筛选|搜索|详情页)",
]

_FIELD_HINT_PATTERNS = [
    r"(价格|数量|金额|余额|可用余额|可用额度|手续费|止盈价|止损价|验证码|手机号|邮箱|密码|昵称|身份信息|姓名|证件号|地址|链类型|币种|账户类型|订单类型|杠杆倍数|方向|模式|数量精度|价格精度)",
    r"([A-Za-z0-9_\-/]{2,40})(?:字段|参数)",
]

_ACTION_HINT_PATTERNS = [
    "新增", "创建", "提交", "保存", "确认", "删除", "编辑", "修改",
    "开通", "关闭", "开启", "禁用", "启用", "切换", "查询", "搜索",
    "筛选", "导出", "导入", "下单", "撤单", "买入", "卖出", "充值",
    "提币", "划转", "申购", "赎回", "登录", "注册", "绑定", "解绑",
]

_STATE_HINT_PATTERNS = [
    "状态", "切换", "流转", "刷新后", "再次进入", "提交后", "关闭后",
    "撤销后", "成功后", "失败后", "待审核", "审核中", "已完成", "已取消",
]

_ROLE_HINT_PATTERNS = [
    "角色", "权限", "管理员", "普通用户", "未登录", "登录态", "越权",
    "认证用户", "未认证", "风控", "白名单", "黑名单",
]

_OFF_TOPIC_DOMAIN_RULES = {
    "埋点": ["埋点", "事件", "日志", "曝光", "上报"],
    "性能": ["性能", "耗时", "并发", "吞吐", "延迟", "超时", "弱网", "稳定性", "重试"],
    "兼容": ["兼容", "浏览器", "机型", "系统版本", "分辨率", "深色模式", "浅色模式"],
    "权限": ["权限", "角色", "越权", "未登录", "认证", "风控"],
    "一致性": ["一致性", "数据一致", "列表", "详情", "统计", "汇总", "同步"],
    "安全": ["安全", "token", "xss", "sql", "注入", "csrf", "敏感"],
}

_LOW_VALUE_WORDS = {
    "功能", "流程", "系统", "页面", "规则", "场景", "处理", "校验", "验证",
    "正确", "成功", "失败", "操作", "结果", "反馈", "展示", "业务", "逻辑",
    "支持", "需要", "可以", "应当", "应该", "进行", "完成", "实现", "功能点",
}


def _truncate_text(text: Any, max_len: int = 160) -> str:
    s = str(text or "").strip()
    if len(s) <= max_len:
        return s
    return s[:max_len]


def _brief_existing_points(existing_points: List[TestPoint], limit: int = 60) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for tp in (existing_points or [])[:limit]:
        out.append({
            "module": str(tp.get("module") or "整体功能")[:80],
            "dimension": normalize_dimension(str(tp.get("dimension") or "")),
            "title": _truncate_text(tp.get("title") or "", 120),
            "source": _truncate_text(tp.get("source") or "", 120),
            "priority": _normalize_priority(tp.get("priority") or "P1"),
        })
    return out


def _infer_group_type_from_dimension(dimension: str) -> Tuple[str, str]:
    dim = normalize_dimension(dimension)
    if dim in {"Negative", "Security"}:
        return "exception", "异常流程"
    if dim in {"Input"}:
        return "boundary", "边界条件"
    return "normal", "正常流程"


def _infer_methods_by_dimension(dimension: str) -> List[str]:
    dim = normalize_dimension(dimension)
    if dim == "Negative":
        return ["Scenario", "ErrorGuessing"]
    if dim == "Input":
        return ["ECP", "BVA"]
    if dim == "Security":
        return ["Scenario", "DecisionTable"]
    if dim == "UI":
        return ["Scenario", "Exploratory"]
    if dim == "Compat":
        return ["Scenario", "Exploratory"]
    if dim == "NFR":
        return ["Scenario", "OATS"]
    return ["Scenario"]


def _make_fingerprint(tp: TestPoint) -> str:
    module = str(tp.get("module") or "").strip()
    dim = normalize_dimension(str(tp.get("dimension") or ""))
    title = str(tp.get("title") or "").strip()
    source = str(tp.get("source") or "").strip()
    return f"{module}||{dim}||{title}||{source}"


def _extract_modules(text: str) -> List[str]:
    modules: List[str] = []

    for p in _COMMON_MODULE_PATTERNS:
        for m in re.finditer(p, text or "", re.IGNORECASE):
            val = m.group(1).strip()
            if val:
                modules.append(val)

    explicit = re.findall(
        r"(?:模块|功能|页面|场景)[:：]\s*([^\n，。,；;]{2,20})",
        text or "",
        re.IGNORECASE,
    )
    modules.extend([x.strip() for x in explicit if str(x).strip()])
    return _uniq_clean_items(modules, max_count=12, max_len=20)


def _extract_candidate_fields(text: str) -> List[str]:
    fields: List[str] = []
    for pattern in _FIELD_HINT_PATTERNS:
        for m in re.finditer(pattern, text or "", re.IGNORECASE):
            val = (m.group(1) if m.groups() else m.group(0)).strip()
            val = re.sub(r"[：:，。；;、,\s]+$", "", val)
            if 1 < len(val) <= 20:
                fields.append(val)
    return _uniq_clean_items(fields, max_count=10, max_len=20)


def _extract_candidate_actions(text: str) -> List[str]:
    actions: List[str] = []
    for action in _ACTION_HINT_PATTERNS:
        if action in (text or ""):
            actions.append(action)
    return _uniq_clean_items(actions, max_count=10, max_len=10)


def _extract_state_terms(text: str) -> List[str]:
    out: List[str] = []
    for kw in _STATE_HINT_PATTERNS:
        if kw in (text or ""):
            out.append(kw)

    for m in re.finditer(r"([^\s，。,；;:：]{1,8}状态)", text or ""):
        val = m.group(1).strip()
        if 2 <= len(val) <= 12:
            out.append(val)

    return _uniq_clean_items(out, max_count=10, max_len=12)


def _extract_role_terms(text: str) -> List[str]:
    out: List[str] = []
    for kw in _ROLE_HINT_PATTERNS:
        if kw in (text or ""):
            out.append(kw)
    return _uniq_clean_items(out, max_count=10, max_len=12)


def _core_tokens(text: str) -> List[str]:
    raw = re.findall(r"[A-Za-z0-9_\-/]+|[\u4e00-\u9fa5]{2,}", text or "")
    out: List[str] = []
    for x in raw:
        s = x.strip().lower()
        if not s:
            continue
        if s in _LOW_VALUE_WORDS:
            continue
        if len(s) <= 1:
            continue
        out.append(s)
    return out[:12]


def _uniq_clean_items(
    items: List[str],
    *,
    max_count: int = 20,
    max_len: int = 30,
) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for item in items or []:
        s = str(item or "").strip()
        s = re.sub(r"\s+", "", s)
        s = re.sub(r"^(字段|参数|输入框|按钮|图表)", "", s)
        s = re.sub(r"(字段|参数|输入框|按钮|图表)$", "", s)
        if not s:
            continue
        if len(s) > max_len:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= max_count:
            break
    return out


def _extract_requirement_anchors(
    requirement_text: str,
    extra_requirement: str = "",
    default_module: str = "整体功能",
) -> Dict[str, Any]:
    merged = f"{requirement_text}\n{extra_requirement}".strip()

    modules = _extract_modules(merged)
    fields = _extract_candidate_fields(merged)
    actions = _extract_candidate_actions(merged)
    states = _extract_state_terms(merged)
    roles = _extract_role_terms(merged)

    keywords = []
    keywords.extend(modules[:10])
    keywords.extend(fields[:12])
    keywords.extend(actions[:10])
    keywords.extend(states[:10])
    keywords.extend(roles[:10])
    keywords = _uniq_clean_items(keywords, max_count=40, max_len=20)

    return {
        "default_module": default_module,
        "modules": modules or ([default_module] if default_module else []),
        "fields": fields,
        "actions": actions,
        "states": states,
        "roles": roles,
        "keywords": keywords,
    }


def _is_generic_title(title: str) -> bool:
    s = str(title or "").strip()
    if not s:
        return True
    if len(s) <= 5:
        return True
    if any(x == s for x in _GENERIC_TITLE_PATTERNS):
        return True
    return len(_core_tokens(s)) <= 1


def _contains_anchor_signal(
    point: TestPoint,
    anchors: Dict[str, Any],
) -> bool:
    combined = " ".join(
        [
            str(point.get("title") or ""),
            str(point.get("detail") or ""),
            str(point.get("source") or ""),
            str(point.get("module") or ""),
        ]
    ).lower()

    keywords = [
        *(anchors.get("modules") or []),
        *(anchors.get("fields") or []),
        *(anchors.get("actions") or []),
        *(anchors.get("states") or []),
        *(anchors.get("roles") or []),
        *(anchors.get("keywords") or []),
    ]
    keywords = [str(x).strip().lower() for x in keywords if str(x).strip()]

    for kw in keywords:
        if kw and kw in combined:
            return True
    return False


def _relevance_score(
    point: TestPoint,
    requirement_text: str,
    extra_requirement: str,
    anchors: Dict[str, Any],
) -> int:
    title = str(point.get("title") or "")
    detail = str(point.get("detail") or "")
    source = str(point.get("source") or "")
    module = str(point.get("module") or "")

    combined = " ".join([title, detail, source]).strip()
    tokens = _core_tokens(combined)
    if not tokens:
        return 0

    req_text = (requirement_text or "").lower()
    user_text = (extra_requirement or "").lower()
    score = 0

    for tk in _core_tokens(title)[:6]:
        if tk and tk in req_text:
            score += 2

    for tk in _core_tokens(detail)[:6]:
        if tk and tk in req_text:
            score += 1

    for tk in _core_tokens(source)[:4]:
        if tk and tk in req_text:
            score += 1

    if module and module.lower() in req_text:
        score += 1

    anchor_sets = []
    for k in ("modules", "fields", "actions", "states", "roles", "keywords"):
        anchor_sets.extend(anchors.get(k) or [])

    combined_l = combined.lower()
    anchor_hit_count = 0
    for kw in anchor_sets:
        kw = str(kw or "").strip().lower()
        if kw and kw in combined_l:
            anchor_hit_count += 1
    score += min(4, anchor_hit_count)

    if user_text:
        for tk in _core_tokens(title)[:4]:
            if tk and tk in user_text:
                score += 1

    return score


def _looks_like_off_topic(
    point: TestPoint,
    requirement_text: str,
    extra_requirement: str,
) -> bool:
    title = str(point.get("title") or "")
    detail = str(point.get("detail") or "")
    combined_title = f"{title} {detail}".lower()
    combined_req = f"{requirement_text}\n{extra_requirement}".lower()

    for topic, signals in _OFF_TOPIC_DOMAIN_RULES.items():
        if topic in title or topic in detail:
            if not any(sig in combined_req for sig in signals):
                return True
    return False


def _accept_generated_point(
    point: TestPoint,
    requirement_text: str,
    extra_requirement: str,
    anchors: Dict[str, Any],
    missing_dimensions: List[str],
) -> bool:
    title = str(point.get("title") or "")
    detail = str(point.get("detail") or "")
    dim = normalize_dimension(str(point.get("dimension") or ""))
    missing_set = {normalize_dimension(x) for x in (missing_dimensions or [])}

    if not title or len(title.strip()) < 4:
        return False
    if _is_generic_title(title):
        return False
    if missing_set and dim not in missing_set:
        return False
    if _looks_like_off_topic(point, requirement_text, extra_requirement):
        return False
    if not _contains_anchor_signal(point, anchors):
        return False
    if _relevance_score(point, requirement_text, extra_requirement, anchors) < 4:
        return False
    if len(_core_tokens(f"{title} {detail}")) <= 1:
        return False
    return True


# =========================
# CoverageAgent：补漏生成
# =========================
class CoverageAgent:
    """
    CoverageAgent 只做一件事：
    - 输入：missing_dimensions + requirement_text + 已有测试点摘要
    - 输出：高相关、补缺口、少而精的测试点

    强约束：
    - 只围绕需求锚点补漏
    - 只补缺失维度，不自由扩写
    - fallback 也必须带业务对象，不允许泛化通用点
    """

    def __init__(
        self,
        llm: Optional[LLM] = None,
        *,
        timeout: int = 180,
        max_points: int = 12,
        default_module: str = "整体功能",
    ):
        self.llm = llm or LLM()
        self.timeout = max(30, int(timeout))
        self.max_points = max(1, int(max_points))
        self.default_module = str(default_module or "整体功能").strip() or "整体功能"

    def build_prompt(
        self,
        requirement_text: str,
        missing_dimensions: List[str],
        *,
        requirement_id: str = "",
        confirmed_hint: str = "",
        coverage_stats: Optional[Dict[str, int]] = None,
        module_coverage_stats: Optional[Dict[str, Dict[str, int]]] = None,
        existing_points_brief: Optional[List[Dict[str, str]]] = None,
        extra_requirement: str = "",
        anchors: Optional[Dict[str, Any]] = None,
    ) -> str:
        missing = [normalize_dimension(x) for x in (missing_dimensions or [])]
        if not missing:
            missing = ["Negative", "Input", "Security"]

        brief = (existing_points_brief or [])[:40]
        req_module_tip = requirement_id or self.default_module
        anchors = anchors or {}

        anchor_modules = ", ".join(anchors.get("modules") or []) or "无"
        anchor_fields = ", ".join(anchors.get("fields") or []) or "无"
        anchor_actions = ", ".join(anchors.get("actions") or []) or "无"
        anchor_states = ", ".join(anchors.get("states") or []) or "无"
        anchor_roles = ", ".join(anchors.get("roles") or []) or "无"

        return f"""
Role: 资深测试专家（Coverage 补漏专员）

Task:
当前测试点覆盖存在缺口，请【只针对缺失维度】补充测试点（不是测试用例）。

硬性协议（必须严格遵守）：
1. 只围绕需求锚点生成测试点
2. 只补缺失维度，不允许自由发散
3. 每条标题必须包含具体对象/字段/动作/状态/规则
4. 不允许输出“功能验证 / 流程验证 / 页面验证 / 通用异常处理”这类泛化标题
5. 不允许输出与需求无关的性能、兼容、埋点、安全等场景，除非需求原文或补充要求明确提到
6. 每行一个【完整 JSON 对象】，禁止数组、禁止 markdown、禁止解释
7. 只输出 4~{self.max_points} 条，宁少勿滥

缺失维度（必须命中其一）：
{", ".join(missing)}

需求锚点（只能围绕这些对象补漏）：
- 模块锚点：{anchor_modules}
- 字段锚点：{anchor_fields}
- 动作锚点：{anchor_actions}
- 状态锚点：{anchor_states}
- 角色/权限锚点：{anchor_roles}

当前覆盖统计（全局）：
{json.dumps(coverage_stats or {}, ensure_ascii=False)}

当前覆盖统计（按模块）：
{json.dumps(module_coverage_stats or {}, ensure_ascii=False)}

每条 JSON 格式固定为：
{{"id":"TP-xxx",
"module":"{req_module_tip}",
"group":"正常流程|异常流程|边界条件",
"type":"normal|exception|boundary",
"dimension":"Happy|Negative|UI|Input|NFR|Security|Compat",
"methods":["Scenario"],
"priority":"P0|P1|P2",
"risk":"高|中|低",
"title":"具体可执行的测试点标题（必须带对象）",
"detail":"补充说明，强调输入/状态/规则/异常等",
"description":"同 detail，可相同",
"source":"引用需求原文短句"}}

方法要求（methods 字段，可多选，尽量多样）：
Scenario, StateTransition, DecisionTable, ECP, BVA, OATS, ErrorGuessing, Exploratory

已有测试点 brief（仅供避重复）：
{json.dumps(brief, ensure_ascii=False)}

补充测试要求（如有，优先满足）：
{extra_requirement or "无"}

需求全文（可引用片段作为 source）：
{requirement_text}

高置信提示（必须参考）：
{confirmed_hint or "无"}
""".strip()

    def generate_missing_points_call(
        self,
        requirement_text: str,
        missing_dimensions: List[str],
        *,
        requirement_id: str = "",
        confirmed_hint: str = "",
        coverage_stats: Optional[Dict[str, int]] = None,
        module_coverage_stats: Optional[Dict[str, Dict[str, int]]] = None,
        existing_points: Optional[List[TestPoint]] = None,
        extra_requirement: str = "",
    ) -> List[TestPoint]:
        existing_points = existing_points or []
        existing_brief = _brief_existing_points(existing_points, limit=80)
        default_module = requirement_id or self.default_module

        anchors = _extract_requirement_anchors(
            requirement_text=requirement_text,
            extra_requirement=extra_requirement,
            default_module=default_module,
        )

        prompt = self.build_prompt(
            requirement_text=requirement_text,
            missing_dimensions=missing_dimensions,
            requirement_id=requirement_id,
            confirmed_hint=confirmed_hint,
            coverage_stats=coverage_stats,
            module_coverage_stats=module_coverage_stats,
            existing_points_brief=existing_brief,
            extra_requirement=extra_requirement,
            anchors=anchors,
        )

        try:
            raw = self.llm.call(prompt, timeout=self.timeout, force_json_object=False)
        except Exception as e:
            logger.error("coverage_agent llm.call failed: %s", str(e), exc_info=True)
            raw = ""

        items = parse_json_lines(raw)
        normalized = self._post_process_generated_points(
            items=items,
            missing_dimensions=missing_dimensions,
            requirement_id=requirement_id,
            existing_points=existing_points,
            requirement_text=requirement_text,
            extra_requirement=extra_requirement,
            anchors=anchors,
        )

        if normalized:
            return normalized[: self.max_points]

        return self._fallback_missing_points(
            requirement_text=requirement_text,
            missing_dimensions=missing_dimensions,
            requirement_id=requirement_id,
            existing_points=existing_points,
            extra_requirement=extra_requirement,
            anchors=anchors,
        )[: self.max_points]

    def analyze_coverage(
        self,
        test_points: List[TestPoint],
        *,
        targets: Optional[List[str]] = None,
        min_per_dim: int = 3,
        module_min_per_dim: int = 1,
    ) -> Dict[str, Any]:
        targets = targets or _DEFAULT_TARGETS

        coverage_stats = calc_coverage(test_points, targets=targets)
        missing_dimensions = calc_missing_dimensions(
            test_points,
            targets=targets,
            min_per_dim=min_per_dim,
        )

        module_coverage_stats = calc_module_coverage(test_points, targets=targets)
        missing_dimensions_by_module = calc_missing_dimensions_by_module(
            test_points,
            targets=targets,
            min_per_dim=module_min_per_dim,
        )

        priority_coverage = calc_priority_coverage(test_points)
        smoke_coverage = calc_smoke_coverage(test_points)

        return {
            "targets": list(targets),
            "coverage_stats": coverage_stats,
            "missing_dimensions": missing_dimensions,
            "module_coverage_stats": module_coverage_stats,
            "missing_dimensions_by_module": missing_dimensions_by_module,
            "priority_coverage": priority_coverage,
            "smoke_coverage": smoke_coverage,
            "total_points": len(test_points or []),
        }

    def _post_process_generated_points(
        self,
        *,
        items: List[Dict[str, Any]],
        missing_dimensions: List[str],
        requirement_id: str,
        existing_points: List[TestPoint],
        requirement_text: str,
        extra_requirement: str,
        anchors: Dict[str, Any],
    ) -> List[TestPoint]:
        missing_set: Set[str] = {normalize_dimension(x) for x in (missing_dimensions or [])}
        existing_fp: Set[str] = {_make_fingerprint(tp) for tp in (existing_points or [])}
        out: List[TestPoint] = []
        seen: Set[str] = set()

        default_module = requirement_id or self.default_module

        for idx, obj in enumerate(items, 1):
            if not isinstance(obj, dict):
                continue

            dim = normalize_dimension(str(obj.get("dimension") or ""))
            if missing_set and dim not in missing_set:
                continue

            tp_type, group = _normalize_flow_type(
                obj.get("type") or "",
                obj.get("group") or "",
            )

            if not str(obj.get("group") or "").strip() and not str(obj.get("type") or "").strip():
                tp_type, group = _infer_group_type_from_dimension(dim)

            methods = obj.get("methods")
            if isinstance(methods, list):
                methods_list = [str(x).strip() for x in methods if str(x).strip()]
            elif isinstance(methods, str) and methods.strip():
                methods_list = [methods.strip()]
            else:
                methods_list = _infer_methods_by_dimension(dim)

            title = str(obj.get("title") or "").strip() or "未命名测试点"
            detail = str(obj.get("detail") or obj.get("description") or "").strip() or title
            source = str(obj.get("source") or "需求整体描述").strip() or "需求整体描述"
            priority = _normalize_priority(obj.get("priority") or "P1")
            risk = _normalize_risk(obj.get("risk") or "中")

            module = str(obj.get("module") or default_module).strip() or default_module
            if requirement_id:
                module = requirement_id

            tp: TestPoint = {
                "id": "",
                "module": module,
                "group": group,
                "type": tp_type,
                "dimension": dim,
                "methods": methods_list or ["Scenario"],
                "priority": priority,
                "risk": risk,
                "title": title,
                "detail": detail,
                "description": str(obj.get("description") or detail).strip() or detail,
                "source": source,
                "tags": [_tag_from_priority(priority)],
                "smoke_flag": priority == "P0",
                "test_type": _tag_from_priority(priority),
            }

            if not _accept_generated_point(
                tp,
                requirement_text=requirement_text,
                extra_requirement=extra_requirement,
                anchors=anchors,
                missing_dimensions=missing_dimensions,
            ):
                continue

            fp = _make_fingerprint(tp)
            if fp in existing_fp or fp in seen:
                continue

            seen.add(fp)
            tp["id"] = f"TP-COV-{idx:03d}"
            out.append(tp)

            if len(out) >= self.max_points:
                break

        return out

    def _fallback_missing_points(
        self,
        *,
        requirement_text: str,
        missing_dimensions: List[str],
        requirement_id: str,
        existing_points: List[TestPoint],
        extra_requirement: str,
        anchors: Dict[str, Any],
    ) -> List[TestPoint]:
        """
        当 LLM 失败时，基于需求锚点保守生成 fallback 点
        不允许再产出泛化兜底点
        """
        existing_fp: Set[str] = {_make_fingerprint(tp) for tp in (existing_points or [])}
        out: List[TestPoint] = []
        seen: Set[str] = set()

        module = requirement_id or self.default_module
        source = self._extract_short_source(requirement_text)

        fields = anchors.get("fields") or []
        actions = anchors.get("actions") or []
        states = anchors.get("states") or []
        roles = anchors.get("roles") or []
        dims = [normalize_dimension(x) for x in (missing_dimensions or [])]

        def try_add(tp: TestPoint) -> None:
            fp = _make_fingerprint(tp)
            if fp in existing_fp or fp in seen:
                return
            if not _accept_generated_point(
                tp,
                requirement_text=requirement_text,
                extra_requirement=extra_requirement,
                anchors=anchors,
                missing_dimensions=missing_dimensions,
            ):
                return
            seen.add(fp)
            tp["id"] = f"TP-COV-{len(out) + 1:03d}"
            out.append(tp)

        # Negative
        if "Negative" in dims:
            if fields:
                field = fields[0]
                try_add({
                    "id": "",
                    "module": module,
                    "group": "异常流程",
                    "type": "exception",
                    "dimension": "Negative",
                    "methods": ["Scenario", "ErrorGuessing"],
                    "priority": "P1",
                    "risk": "中",
                    "title": f"{field}字段非法输入时应被正确拦截",
                    "detail": f"验证{field}字段为空、格式非法或非法值场景下系统应正确拦截并提示。",
                    "description": f"验证{field}字段为空、格式非法或非法值场景下系统应正确拦截并提示。",
                    "source": source,
                    "tags": ["功能测试"],
                    "smoke_flag": False,
                    "test_type": "功能测试",
                })
            elif actions:
                action = actions[0]
                try_add({
                    "id": "",
                    "module": module,
                    "group": "异常流程",
                    "type": "exception",
                    "dimension": "Negative",
                    "methods": ["Scenario", "ErrorGuessing"],
                    "priority": "P1",
                    "risk": "中",
                    "title": f"{action}操作失败时页面反馈正确",
                    "detail": f"验证{action}操作失败或条件不满足时，系统反馈、提示信息和页面状态处理正确。",
                    "description": f"验证{action}操作失败或条件不满足时，系统反馈、提示信息和页面状态处理正确。",
                    "source": source,
                    "tags": ["功能测试"],
                    "smoke_flag": False,
                    "test_type": "功能测试",
                })

        # Input
        if "Input" in dims and fields:
            field = fields[0]
            try_add({
                "id": "",
                "module": module,
                "group": "边界条件",
                "type": "boundary",
                "dimension": "Input",
                "methods": ["ECP", "BVA"],
                "priority": "P1",
                "risk": "中",
                "title": f"{field}字段边界值处理正确",
                "detail": f"验证{field}字段最小值、最大值、精度位数或长度边界场景处理是否正确。",
                "description": f"验证{field}字段最小值、最大值、精度位数或长度边界场景处理是否正确。",
                "source": source,
                "tags": ["功能测试"],
                "smoke_flag": False,
                "test_type": "功能测试",
            })

        # Security
        if "Security" in dims and roles:
            role = roles[0]
            try_add({
                "id": "",
                "module": module,
                "group": "异常流程",
                "type": "exception",
                "dimension": "Security",
                "methods": ["Scenario", "DecisionTable"],
                "priority": "P0",
                "risk": "高",
                "title": f"{role}场景下权限限制处理正确",
                "detail": f"验证{role}场景下执行受限操作时，系统应正确拦截并提示，不应产生越权结果。",
                "description": f"验证{role}场景下执行受限操作时，系统应正确拦截并提示，不应产生越权结果。",
                "source": source,
                "tags": ["冒烟测试"],
                "smoke_flag": True,
                "test_type": "冒烟测试",
            })

        # UI
        if "UI" in dims and actions:
            action = actions[0]
            try_add({
                "id": "",
                "module": module,
                "group": "正常流程",
                "type": "normal",
                "dimension": "UI",
                "methods": ["Scenario", "Exploratory"],
                "priority": "P1",
                "risk": "中",
                "title": f"{action}操作后页面提示与反馈展示正确",
                "detail": f"验证执行{action}操作后，页面提示、按钮状态、文案和结果反馈展示正确。",
                "description": f"验证执行{action}操作后，页面提示、按钮状态、文案和结果反馈展示正确。",
                "source": source,
                "tags": ["功能测试"],
                "smoke_flag": False,
                "test_type": "功能测试",
            })

        # NFR
        if "NFR" in dims and actions:
            action = actions[0]
            try_add({
                "id": "",
                "module": module,
                "group": "正常流程",
                "type": "normal",
                "dimension": "NFR",
                "methods": ["Scenario", "OATS"],
                "priority": "P2",
                "risk": "低",
                "title": f"{action}操作在重复触发下系统表现稳定",
                "detail": f"验证执行{action}操作时，在连续触发、刷新或重新进入场景下，系统反馈和状态保持稳定。",
                "description": f"验证执行{action}操作时，在连续触发、刷新或重新进入场景下，系统反馈和状态保持稳定。",
                "source": source,
                "tags": ["功能测试"],
                "smoke_flag": False,
                "test_type": "功能测试",
            })

        # Compat
        if "Compat" in dims and anchors.get("modules"):
            m = anchors["modules"][0]
            try_add({
                "id": "",
                "module": module,
                "group": "正常流程",
                "type": "normal",
                "dimension": "Compat",
                "methods": ["Scenario", "Exploratory"],
                "priority": "P2",
                "risk": "低",
                "title": f"{m}页面在不同终端展示与交互一致",
                "detail": f"验证{m}页面在不同终端或环境下，关键展示内容、按钮状态和交互行为保持一致。",
                "description": f"验证{m}页面在不同终端或环境下，关键展示内容、按钮状态和交互行为保持一致。",
                "source": source,
                "tags": ["功能测试"],
                "smoke_flag": False,
                "test_type": "功能测试",
            })

        if out:
            return out[: self.max_points]

        return []

    def _extract_short_source(self, requirement_text: str) -> str:
        text = str(requirement_text or "").strip()
        if not text:
            return "需求整体描述"

        for line in text.splitlines():
            s = line.strip()
            if len(s) >= 8:
                return _truncate_text(s, 120)

        sentences = re.split(r"[。；;！!？?\n]", text)
        for s in sentences:
            s = s.strip()
            if len(s) >= 8:
                return _truncate_text(s, 120)

        return _truncate_text(text, 120)

    def generate_missing_points_stream(
        self,
        requirement_text: str,
        missing_dimensions: List[str],
        stream_json_objects_fn,
        *,
        requirement_id: str = "",
        confirmed_hint: str = "",
        coverage_stats: Optional[Dict[str, int]] = None,
        module_coverage_stats: Optional[Dict[str, Dict[str, int]]] = None,
        existing_points: Optional[List[TestPoint]] = None,
        extra_requirement: str = "",
    ):
        raise NotImplementedError(
            "如果你要真流式补漏：把 pipeline.py 里的 _stream_json_objects 作为 stream_json_objects_fn 传进来，"
            "然后在这里用 async for 逐条 yield。当前先使用 generate_missing_points_call。"
        )