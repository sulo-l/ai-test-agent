#! /usr/bin/python3
# coding=utf-8
# app/testcase_app/agents/planner_agent.py

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Set

from app.llm.client import LLM

logger = logging.getLogger(__name__)

PlanJSON = Dict[str, Any]

_DEFAULT_COVERAGE_TARGETS = ["Happy", "Negative", "UI", "Input", "NFR", "Security", "Compat"]
_DEFAULT_TEST_METHODS = [
    "Scenario",
    "StateTransition",
    "DecisionTable",
    "ECP",
    "BVA",
    "OATS",
    "ErrorGuessing",
    "Exploratory",
]

_REQUIREMENT_ID_PATTERNS = [
    re.compile(r"^[A-Za-z]{1,10}-\d{2,}$"),
    re.compile(r"^[A-Za-z]+_\d{2,}$"),
    re.compile(r"^\d{5,}$"),
    re.compile(r"^(REQ|PRD|MRD|BRD)[-_ ]?\d+$", re.IGNORECASE),
]

_COMMON_NOISE_NAMES = {
    "需求", "功能", "模块", "页面", "流程", "校验", "规则", "提示",
    "文案", "结果", "详情", "列表", "字段", "系统", "接口", "按钮",
    "配置", "场景", "逻辑", "说明", "处理", "测试", "原型",
    "需求文档", "用户补充测试要求", "原始需求文档", "核心功能",
    "原文", "文本", "内容", "需求片段", "页面交互", "模块名",
    "功能点", "测试点", "业务", "对象", "状态", "条件", "参数",
}

_PRIORITY_P0_HINTS = [
    "登录", "注册", "提交", "保存", "删除", "确认", "支付", "下单",
    "权限", "越权", "风控", "金额", "数量", "价格", "余额", "可用额度",
    "订单", "状态流转", "一致性", "计算", "公式", "审核", "资产",
]

_PRIORITY_P1_HINTS = [
    "异常", "失败", "边界", "提示", "文案", "切换", "状态",
    "精度", "展示", "刷新", "缓存", "兼容", "交互",
]

_RISK_HIGH_HINTS = [
    "权限", "越权", "资金", "余额", "支付", "金额", "价格", "数量",
    "订单", "删除", "重复提交", "幂等", "一致性", "计算", "公式",
    "审核", "资产", "状态流转",
]

_RISK_MEDIUM_HINTS = [
    "异常", "失败", "边界", "文案", "提示", "状态",
    "刷新", "展示", "切换", "兼容", "交互",
]


def _strip_code_fence(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()


def _extract_first_json_object_by_brace(raw: str) -> Optional[Dict[str, Any]]:
    raw = _strip_code_fence(raw or "")
    if not raw:
        return None

    started = False
    depth = 0
    in_str = False
    esc = False
    obj_start = -1

    def try_load(s: str) -> Optional[Dict[str, Any]]:
        s = _strip_code_fence((s or "").strip())
        if not s:
            return None
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except Exception:
            s2 = re.sub(r",\s*}", "}", s)
            s2 = re.sub(r",\s*]", "]", s2)
            try:
                obj = json.loads(s2)
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None

    for i, ch in enumerate(raw):
        if not started:
            if ch == "{":
                started = True
                depth = 1
                in_str = False
                esc = False
                obj_start = i
            continue

        if in_str:
            if esc:
                esc = False
            else:
                if ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and obj_start >= 0:
                    chunk = raw[obj_start:i + 1]
                    return try_load(chunk)

    return None


def _clean_chunk_text(text: str) -> str:
    t = str(text or "").strip()
    if not t:
        return ""
    t = t.replace("\u3000", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _safe_slice(s: str, max_len: int) -> str:
    s = str(s or "").strip()
    return s[:max_len] if len(s) > max_len else s


def _smart_split_text(text: str, max_chunks: int, min_chars: int, max_chars: int) -> List[str]:
    t = _clean_chunk_text(text)
    if not t:
        return []

    max_chunks = max(1, int(max_chunks))
    min_chars = max(100, int(min_chars))
    max_chars = max(min_chars, int(max_chars))

    rough_parts = re.split(r"\n\s*\n", t)
    rough_parts = [p.strip() for p in rough_parts if p and p.strip()]

    if len(rough_parts) <= 1:
        rough_parts = re.split(
            r"(?=\n?(?:#+\s+|[一二三四五六七八九十]+[、\.]\s*|[0-9]+[、\.]\s*))",
            t,
        )
        rough_parts = [p.strip() for p in rough_parts if p and p.strip()]

    chunks: List[str] = []
    buf = ""

    for p in rough_parts:
        if not buf:
            buf = p
            continue

        if len(buf) + 2 + len(p) <= max_chars:
            buf = f"{buf}\n\n{p}"
        else:
            chunks.append(buf.strip())
            buf = p

        if len(chunks) >= max_chunks:
            break

    if buf and len(chunks) < max_chunks:
        chunks.append(buf.strip())

    final_chunks: List[str] = []
    for c in chunks:
        c = _clean_chunk_text(c)
        if not c:
            continue

        if len(c) <= max_chars:
            final_chunks.append(c)
            continue

        start = 0
        while start < len(c) and len(final_chunks) < max_chunks:
            seg = c[start:start + max_chars].strip()
            if seg:
                final_chunks.append(seg)
            start += max_chars

    merged: List[str] = []
    for c in final_chunks:
        if merged and len(c) < min_chars and len(merged[-1]) + 2 + len(c) <= max_chars:
            merged[-1] = f"{merged[-1]}\n\n{c}".strip()
        else:
            merged.append(c)

    return merged[:max_chunks]


def _normalize_coverage_targets(value: Any) -> List[str]:
    if not isinstance(value, list):
        return list(_DEFAULT_COVERAGE_TARGETS)

    out: List[str] = []
    seen = set()
    for item in value:
        s = str(item or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out or list(_DEFAULT_COVERAGE_TARGETS)


def _normalize_methods(value: Any) -> List[str]:
    if not isinstance(value, list):
        return list(_DEFAULT_TEST_METHODS)

    out: List[str] = []
    seen = set()
    for item in value:
        s = str(item or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out or list(_DEFAULT_TEST_METHODS)


class PlannerAgent:
    """
    AI-first 测试规划 Agent（重构版）

    设计原则：
    1. Planner 只负责“规划蓝图”，不负责写模板化测试点
    2. 先让 AI 从需求中抽取：
       - modules / objects / rules / states / inputs / risks
       - coverage strategy / scenario matrix / priorities
    3. 代码只负责：
       - JSON 提取
       - 去噪与归一化
       - 去重与弱修复
       - 最小兜底
    4. 不再用大量业务域硬编码主导模块规划
    """

    def __init__(
        self,
        llm: Optional[LLM] = None,
        *,
        timeout: int = 240,
        max_chunks: int = 18,
        chunk_min_chars: int = 500,
        chunk_max_chars: int = 2200,
    ):
        self.llm = llm or LLM()
        self.timeout = timeout
        self.max_chunks = max(1, int(max_chunks))
        self.chunk_min_chars = max(100, int(chunk_min_chars))
        self.chunk_max_chars = max(self.chunk_min_chars, int(chunk_max_chars))

    # =================================================
    # Public
    # =================================================
    def build_prompt(
        self,
        requirement_text: str,
        confirmed_hint: str = "",
        extra_requirement: str = "",
    ) -> str:
        return f"""
你是拥有 10 年以上经验的资深测试架构师和需求分析专家。

任务：
基于需求原文，生成“结构化测试规划 JSON”，供后续测试点生成和测试用例设计使用。

核心原则：
1. 【用户补充测试要求】优先级高于【原始需求文档】
2. 必须严格基于需求原文，不允许凭空脑补业务模块
3. 不要把“页面、功能、规则、提示、结果、字段、配置、需求ID”这类泛词或编号当成模块
4. 规划结果必须服务于后续高质量用例生成，而不是模板凑数
5. scenario_matrix.must_cover 要尽量写成“可直接转成测试点/用例”的覆盖目标
6. priorities 要体现真实的 P0/P1/P2 关注点，而不是空话

输出要求：
- 只输出【单个 JSON 对象】
- 不要 markdown，不要解释，不要代码块
- 允许模块为空字符串，但不能填泛词凑数

JSON schema：
{{
  "modules": [
    {{
      "name": "模块名",
      "desc": "一句话描述",
      "priority": "P0|P1|P2",
      "risk": "高|中|低"
    }}
  ],
  "objects": [
    {{
      "name": "业务对象/页面对象/数据对象",
      "module": "所属模块(可空)",
      "desc": "对象说明"
    }}
  ],
  "rules": [
    {{
      "rule": "业务规则/校验规则/结果规则",
      "module": "所属模块(可空)",
      "risk": "高|中|低",
      "priority": "P0|P1|P2"
    }}
  ],
  "rule_groups": [
    {{
      "name": "规则组名称",
      "module": "所属模块(可空)",
      "rules": ["规则1", "规则2"]
    }}
  ],
  "states": [
    {{
      "module": "所属模块(可空)",
      "state": "状态",
      "transitions": [
        {{"event":"触发事件","to":"目标状态"}}
      ]
    }}
  ],
  "inputs": [
    {{
      "field": "输入域/关键字段",
      "module": "所属模块(可空)",
      "type": "string|number|enum|bool|date|unknown",
      "constraints": ["约束1","约束2"],
      "priority": "P0|P1|P2"
    }}
  ],
  "risks": [
    {{
      "risk": "风险点",
      "impact": "影响",
      "module": "所属模块(可空)",
      "priority": "P0|P1|P2"
    }}
  ],
  "coverage_targets": ["Happy","Negative","UI","Input","NFR","Security","Compat"],
  "test_methods": ["Scenario","StateTransition","DecisionTable","ECP","BVA","OATS","ErrorGuessing","Exploratory"],
  "test_strategy": {{
    "focus": ["重点方向1", "重点方向2"],
    "priority_rule": "优先级说明",
    "design_principles": ["原则1", "原则2"],
    "case_granularity": "用例粒度要求"
  }},
  "scenario_matrix": [
    {{
      "module": "模块名",
      "scenario_types": ["正常流程","异常流程","边界条件","状态流转","权限校验","数据一致性"],
      "must_cover": ["必须覆盖点1","必须覆盖点2"],
      "recommended_methods": ["Scenario","DecisionTable"]
    }}
  ],
  "field_matrix": [
    {{
      "field": "字段名",
      "module": "模块(可空)",
      "must_cover": ["合法值","非法值","空值","边界值"],
      "recommended_methods": ["ECP","BVA"]
    }}
  ],
  "state_matrix": [
    {{
      "module": "模块(可空)",
      "state": "状态名",
      "must_cover": ["进入条件","退出条件","异常切换","刷新保持"],
      "recommended_methods": ["StateTransition","Scenario"]
    }}
  ],
  "risk_matrix": [
    {{
      "module": "模块(可空)",
      "risk": "风险点",
      "must_cover": ["校验点1","校验点2"],
      "recommended_methods": ["Scenario","ErrorGuessing"]
    }}
  ],
  "priorities": [
    {{
      "module": "模块名",
      "p0_focus": ["P0重点1","P0重点2"],
      "p1_focus": ["P1重点1"],
      "p2_focus": ["P2重点1"]
    }}
  ],
  "chunks": [
    {{
      "id":"C1",
      "title":"块标题",
      "module":"所属模块(可空)",
      "text":"尽量贴近原文的需求片段"
    }}
  ]
}}

强约束：
1. modules 要尽量真实、具体，不要泛化
2. rules 要写“真实规则/真实检查目标”，不要写“功能正常”
3. must_cover 要写成资深测试会关注的检查目标
4. 如果涉及提交、保存、删除、确认等关键动作，应体现：
   - 重复提交 / 幂等
   - 状态变化
   - 结果一致性
5. 如果涉及金额、数量、价格、精度、位数、余额、可用额度，应体现：
   - 边界值
   - 精度处理
   - 数据一致性
   - 展示结果校验
6. 如果涉及状态、刷新、重进、切换、恢复，应体现：
   - 进入条件
   - 退出条件
   - 异常流转
   - 刷新/重进保持
7. 禁止把类似 "www-99999"、"REQ-1001"、"123456" 识别成模块名
8. chunks 数量尽量 6~{self.max_chunks}，text 必须贴近原文，不要只写摘要

原始需求全文：
{requirement_text}

用户补充测试要求（最高优先级）：
{extra_requirement or "无"}

高置信提示：
{confirmed_hint}
""".strip()

    def plan(
        self,
        requirement_text: str,
        confirmed_hint: str = "",
        extra_requirement: str = "",
    ) -> PlanJSON:
        prompt = self.build_prompt(
            requirement_text=requirement_text,
            confirmed_hint=confirmed_hint,
            extra_requirement=extra_requirement,
        )

        try:
            raw = self.llm.call(prompt, timeout=self.timeout, force_json_object=True)
            obj = _extract_first_json_object_by_brace(raw)
            if isinstance(obj, dict) and obj:
                return self._sanitize_plan(
                    obj=obj,
                    requirement_text=requirement_text,
                    extra_requirement=extra_requirement,
                )
        except Exception as e:
            logger.error("PlannerAgent llm.call failed: %s", str(e), exc_info=True)

        return self._fallback_plan(
            requirement_text=requirement_text,
            confirmed_hint=confirmed_hint,
            extra_requirement=extra_requirement,
        )

    # =================================================
    # Sanitize
    # =================================================
    def _sanitize_plan(
        self,
        obj: Dict[str, Any],
        requirement_text: str,
        extra_requirement: str = "",
    ) -> PlanJSON:
        plan = dict(obj)

        list_keys = [
            "modules",
            "objects",
            "rules",
            "rule_groups",
            "states",
            "inputs",
            "risks",
            "chunks",
            "scenario_matrix",
            "field_matrix",
            "state_matrix",
            "risk_matrix",
            "priorities",
        ]
        for k in list_keys:
            if not isinstance(plan.get(k), list):
                plan[k] = []

        if not isinstance(plan.get("test_strategy"), dict):
            plan["test_strategy"] = {}

        plan["coverage_targets"] = _normalize_coverage_targets(plan.get("coverage_targets"))
        plan["test_methods"] = _normalize_methods(plan.get("test_methods"))

        plan["modules"] = self._sanitize_modules(plan.get("modules"), requirement_text=requirement_text)
        plan["objects"] = self._sanitize_objects(
            plan.get("objects"),
            requirement_text=requirement_text,
            modules=plan["modules"],
        )
        plan["rules"] = self._sanitize_rules(
            plan.get("rules"),
            requirement_text=requirement_text,
            modules=plan["modules"],
        )
        plan["rule_groups"] = self._sanitize_rule_groups(plan.get("rule_groups"))
        plan["states"] = self._sanitize_states(plan.get("states"), modules=plan["modules"])
        plan["inputs"] = self._sanitize_inputs(
            plan.get("inputs"),
            requirement_text=requirement_text,
            modules=plan["modules"],
        )
        plan["risks"] = self._sanitize_risks(
            plan.get("risks"),
            requirement_text=requirement_text,
            modules=plan["modules"],
        )
        plan["scenario_matrix"] = self._sanitize_scenario_matrix(
            plan.get("scenario_matrix"),
            requirement_text=requirement_text,
        )
        plan["field_matrix"] = self._sanitize_field_matrix(plan.get("field_matrix"))
        plan["state_matrix"] = self._sanitize_state_matrix(plan.get("state_matrix"))
        plan["risk_matrix"] = self._sanitize_risk_matrix(plan.get("risk_matrix"))
        plan["priorities"] = self._sanitize_priorities(
            plan.get("priorities"),
            requirement_text=requirement_text,
        )
        plan["test_strategy"] = self._sanitize_test_strategy(
            plan.get("test_strategy"),
            extra_requirement=extra_requirement,
            requirement_text=requirement_text,
        )

        chunks = plan.get("chunks") or []
        if not chunks:
            fallback_chunks = _smart_split_text(
                requirement_text,
                self.max_chunks,
                self.chunk_min_chars,
                self.chunk_max_chars,
            )
            plan["chunks"] = [
                {
                    "id": f"C{i + 1}",
                    "title": self._make_chunk_title(c, i + 1),
                    "module": self._guess_chunk_module(c, plan["modules"]),
                    "text": c,
                }
                for i, c in enumerate(fallback_chunks)
            ]
        else:
            plan["chunks"] = self._sanitize_chunks(chunks, requirement_text, modules=plan["modules"])

        # 本地最小兜底：只补结构，不主导语义
        local_plan = self._fallback_plan(
            requirement_text=requirement_text,
            confirmed_hint="",
            extra_requirement=extra_requirement,
        )

        plan["modules"] = self._merge_list_by_keys(plan["modules"], local_plan["modules"], keys=("name",))
        plan["objects"] = self._merge_list_by_keys(plan["objects"], local_plan["objects"], keys=("name", "module"))
        plan["rules"] = self._merge_list_by_keys(plan["rules"], local_plan["rules"], keys=("rule", "module"))
        plan["rule_groups"] = self._merge_list_by_keys(plan["rule_groups"], local_plan["rule_groups"], keys=("name", "module"))
        plan["states"] = self._merge_list_by_keys(plan["states"], local_plan["states"], keys=("module", "state"))
        plan["inputs"] = self._merge_list_by_keys(plan["inputs"], local_plan["inputs"], keys=("module", "field"))
        plan["risks"] = self._merge_list_by_keys(plan["risks"], local_plan["risks"], keys=("module", "risk"))
        plan["scenario_matrix"] = self._merge_list_by_keys(plan["scenario_matrix"], local_plan["scenario_matrix"], keys=("module",))
        plan["field_matrix"] = self._merge_list_by_keys(plan["field_matrix"], local_plan["field_matrix"], keys=("module", "field"))
        plan["state_matrix"] = self._merge_list_by_keys(plan["state_matrix"], local_plan["state_matrix"], keys=("module", "state"))
        plan["risk_matrix"] = self._merge_list_by_keys(plan["risk_matrix"], local_plan["risk_matrix"], keys=("module", "risk"))
        plan["priorities"] = self._merge_list_by_keys(plan["priorities"], local_plan["priorities"], keys=("module",))

        plan["coverage_targets"] = self._merge_unique_str_list(plan["coverage_targets"], local_plan["coverage_targets"])
        plan["test_methods"] = self._merge_unique_str_list(plan["test_methods"], local_plan["test_methods"])
        plan["test_strategy"] = self._merge_test_strategy(plan["test_strategy"], local_plan["test_strategy"])

        plan["modules"] = self._sanitize_modules(plan["modules"], requirement_text=requirement_text)
        plan["objects"] = self._sanitize_objects(plan["objects"], requirement_text=requirement_text, modules=plan["modules"])
        plan["scenario_matrix"] = self._repair_scenario_matrix(plan["scenario_matrix"], plan["modules"], requirement_text)
        plan["priorities"] = self._repair_priorities(plan["priorities"], plan["modules"], requirement_text)
        plan["chunks"] = self._sanitize_chunks(plan["chunks"], requirement_text, modules=plan["modules"])

        return plan

    def _sanitize_modules(self, modules: Any, requirement_text: str = "") -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        candidates: List[Dict[str, Any]] = []

        for item in modules or []:
            if not isinstance(item, dict):
                continue
            name = self._normalize_module_name(item.get("name"), requirement_text=requirement_text)
            if not name:
                continue
            candidates.append({
                "name": name,
                "desc": str(item.get("desc") or f"{name}相关功能、规则与交互场景").strip(),
                "priority": self._normalize_priority(item.get("priority") or self._infer_priority(name)),
                "risk": self._normalize_risk(item.get("risk") or self._infer_risk(name)),
            })

        inferred = self._infer_modules(requirement_text or "")
        candidates.extend(inferred)

        for item in candidates:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append({
                "name": name,
                "desc": str(item.get("desc") or f"{name}相关功能、规则与交互场景").strip(),
                "priority": self._normalize_priority(item.get("priority")),
                "risk": self._normalize_risk(item.get("risk")),
            })

        if not out:
            out = [{"name": "核心功能", "desc": "核心功能相关业务场景", "priority": "P1", "risk": "中"}]

        return out[:20]

    def _sanitize_objects(
        self,
        objects: Any,
        requirement_text: str = "",
        modules: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        module_names = [str(x.get("name") or "").strip() for x in (modules or []) if str(x.get("name") or "").strip()]

        for item in objects or []:
            if not isinstance(item, dict):
                continue
            name = self._clean_object_name(item.get("name"))
            if not name:
                continue
            module = self._normalize_module_name(item.get("module"), requirement_text=requirement_text)
            if not module:
                module = self._best_match_module(name, module_names)
            key = (module, name)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "name": name,
                "module": module,
                "desc": str(item.get("desc") or f"{name}对象").strip(),
            })

        inferred = self._infer_objects(requirement_text, module_names)
        for item in inferred:
            key = (str(item.get("module") or "").strip(), str(item.get("name") or "").strip())
            if key in seen:
                continue
            seen.add(key)
            out.append(item)

        return out[:80]

    def _sanitize_rules(
        self,
        rules: Any,
        requirement_text: str = "",
        modules: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        module_names = [str(x.get("name") or "").strip() for x in (modules or []) if str(x.get("name") or "").strip()]

        for item in rules or []:
            if not isinstance(item, dict):
                continue
            rule = self._clean_rule_text(item.get("rule"))
            if not rule:
                continue
            module = self._normalize_module_name(item.get("module"), requirement_text=requirement_text)
            if not module:
                module = self._best_match_module(rule, module_names)

            clean_rule = re.sub(r"\s+", "", rule)
            key = f"{clean_rule}||{module}"
            if key in seen:
                continue
            seen.add(key)

            out.append({
                "rule": _safe_slice(rule, 180),
                "module": module,
                "risk": self._normalize_risk(item.get("risk") or self._infer_risk(rule)),
                "priority": self._normalize_priority(item.get("priority") or self._infer_priority(rule)),
            })

        inferred = self._infer_rules(requirement_text, modules or [])
        for item in inferred:
            rule = str(item.get("rule") or "")
            rule = re.sub(r"\s+", "", rule)

            module = str(item.get("module") or "").strip()

            key = f"{rule}||{module}"



            if not key.strip("|") or key in seen:
                continue
            seen.add(key)
            out.append(item)

        return out[:100]

    def _sanitize_rule_groups(self, groups: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        for item in groups or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            module = str(item.get("module") or "").strip()
            rules = self._normalize_string_list(item.get("rules"))
            if not rules:
                continue
            key = f"{name}||{module}"
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "name": name,
                "module": module,
                "rules": rules[:20],
            })
        return out[:40]

    def _sanitize_states(self, states: Any, modules: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        module_names = [str(x.get("name") or "").strip() for x in (modules or []) if str(x.get("name") or "").strip()]

        for item in states or []:
            if not isinstance(item, dict):
                continue
            state = str(item.get("state") or "").strip()
            if not state:
                continue

            transitions: List[Dict[str, str]] = []
            for tr in item.get("transitions") or []:
                if not isinstance(tr, dict):
                    continue
                event = str(tr.get("event") or "").strip()
                to_state = str(tr.get("to") or "").strip()
                if not event and not to_state:
                    continue
                transitions.append({"event": event, "to": to_state})

            module = str(item.get("module") or "").strip()
            if not module:
                module = self._best_match_module(state, module_names)

            key = (module, state)
            if key in seen:
                continue
            seen.add(key)

            out.append({
                "module": module,
                "state": state,
                "transitions": transitions,
            })

        return out[:60]

    def _sanitize_inputs(
        self,
        inputs: Any,
        requirement_text: str = "",
        modules: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        module_names = [str(x.get("name") or "").strip() for x in (modules or []) if str(x.get("name") or "").strip()]

        for item in inputs or []:
            if not isinstance(item, dict):
                continue
            field = self._clean_field_name(item.get("field"))
            if not field:
                continue
            module = self._normalize_module_name(item.get("module"), requirement_text=requirement_text)
            if not module:
                module = self._best_match_module(field, module_names)

            key = (module, field)
            if key in seen:
                continue
            seen.add(key)

            out.append({
                "field": field,
                "module": module,
                "type": str(item.get("type") or "").strip() or self._infer_field_type(field),
                "constraints": self._normalize_string_list(item.get("constraints")),
                "priority": self._normalize_priority(item.get("priority") or self._infer_priority(field)),
            })

        inferred = self._infer_inputs(requirement_text, modules or [])
        for item in inferred:
            key = (str(item.get("module") or "").strip(), str(item.get("field") or "").strip())
            if key in seen:
                continue
            seen.add(key)
            out.append(item)

        return out[:100]

    def _sanitize_risks(
        self,
        risks: Any,
        requirement_text: str = "",
        modules: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        module_names = [str(x.get("name") or "").strip() for x in (modules or []) if str(x.get("name") or "").strip()]

        for item in risks or []:
            if not isinstance(item, dict):
                continue
            risk = self._clean_risk_text(item.get("risk"))
            if not risk:
                continue
            module = self._normalize_module_name(item.get("module"), requirement_text=requirement_text)
            if not module:
                module = self._best_match_module(risk, module_names)

            key = (module, risk)
            if key in seen:
                continue
            seen.add(key)

            out.append({
                "risk": risk,
                "impact": str(item.get("impact") or "").strip(),
                "module": module,
                "priority": self._normalize_priority(item.get("priority") or self._infer_priority(risk)),
            })

        inferred = self._infer_risks(requirement_text, modules or [])
        for item in inferred:
            key = (str(item.get("module") or "").strip(), str(item.get("risk") or "").strip())
            if key in seen:
                continue
            seen.add(key)
            out.append(item)

        return out[:80]

    def _sanitize_scenario_matrix(self, value: Any, requirement_text: str = "") -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        for item in value or []:
            if not isinstance(item, dict):
                continue
            module = self._normalize_module_name(item.get("module"), requirement_text=requirement_text)
            if not module:
                continue
            key = module
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "module": module,
                "scenario_types": self._normalize_string_list(item.get("scenario_types")),
                "must_cover": self._normalize_string_list(item.get("must_cover")),
                "recommended_methods": self._normalize_string_list(item.get("recommended_methods")) or ["Scenario"],
            })
        return out[:40]

    def _sanitize_field_matrix(self, value: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        for item in value or []:
            if not isinstance(item, dict):
                continue
            field = self._clean_field_name(item.get("field"))
            if not field:
                continue
            module = str(item.get("module") or "").strip()
            key = (module, field)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "field": field,
                "module": module,
                "must_cover": self._normalize_string_list(item.get("must_cover")),
                "recommended_methods": self._normalize_string_list(item.get("recommended_methods")) or ["ECP", "BVA"],
            })
        return out[:100]

    def _sanitize_state_matrix(self, value: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        for item in value or []:
            if not isinstance(item, dict):
                continue
            state = str(item.get("state") or "").strip()
            if not state:
                continue
            module = str(item.get("module") or "").strip()
            key = (module, state)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "module": module,
                "state": state,
                "must_cover": self._normalize_string_list(item.get("must_cover")),
                "recommended_methods": self._normalize_string_list(item.get("recommended_methods")) or ["StateTransition", "Scenario"],
            })
        return out[:80]

    def _sanitize_risk_matrix(self, value: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        for item in value or []:
            if not isinstance(item, dict):
                continue
            risk = self._clean_risk_text(item.get("risk"))
            if not risk:
                continue
            module = str(item.get("module") or "").strip()
            key = (module, risk)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "module": module,
                "risk": risk,
                "must_cover": self._normalize_string_list(item.get("must_cover")),
                "recommended_methods": self._normalize_string_list(item.get("recommended_methods")) or ["Scenario", "ErrorGuessing"],
            })
        return out[:80]

    def _sanitize_priorities(self, value: Any, requirement_text: str = "") -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        for item in value or []:
            if not isinstance(item, dict):
                continue
            module = self._normalize_module_name(item.get("module"), requirement_text=requirement_text)
            if not module or module in seen:
                continue
            seen.add(module)
            out.append({
                "module": module,
                "p0_focus": self._normalize_string_list(item.get("p0_focus")),
                "p1_focus": self._normalize_string_list(item.get("p1_focus")),
                "p2_focus": self._normalize_string_list(item.get("p2_focus")),
            })
        return out[:40]

    def _sanitize_test_strategy(
        self,
        value: Any,
        extra_requirement: str = "",
        requirement_text: str = "",
    ) -> Dict[str, Any]:
        if not isinstance(value, dict):
            value = {}

        focus = self._normalize_string_list(value.get("focus"))
        if extra_requirement:
            focus = self._merge_unique_str_list(focus, self._extract_focus_items(extra_requirement))

        return {
            "focus": focus or [
                "优先覆盖核心主流程",
                "优先覆盖高风险规则与异常链路",
                "优先覆盖状态变化与结果一致性",
            ],
            "priority_rule": str(
                value.get("priority_rule")
                or "优先覆盖用户补充测试要求、高风险业务规则、关键状态流转、关键结果一致性。"
            ).strip(),
            "design_principles": self._normalize_string_list(value.get("design_principles")) or [
                "优先覆盖用户补充测试要求",
                "同一规则拆分正常/异常/边界场景",
                "核心流程优先输出 P0 高价值场景",
                "字段、规则、状态、权限、数据一致性分别建模",
                "关键提交动作必须覆盖重复提交、防重或幂等",
                "关键金额与数量字段必须覆盖边界值和精度处理",
            ],
            "case_granularity": str(
                value.get("case_granularity")
                or "测试用例需细化到字段、规则、状态、异常、结果反馈和数据一致性层级，避免笼统大用例。"
            ).strip(),
        }

    def _sanitize_chunks(
        self,
        chunks: List[Dict[str, Any]],
        requirement_text: str,
        modules: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        norm_chunks: List[Dict[str, Any]] = []
        modules = modules or []

        for idx, c in enumerate(chunks[: self.max_chunks]):
            if not isinstance(c, dict):
                continue

            raw_text = _clean_chunk_text(c.get("text") or "")
            if not raw_text:
                continue

            chunk_id = str(c.get("id") or f"C{idx + 1}").strip() or f"C{idx + 1}"
            title = str(c.get("title") or "").strip() or self._make_chunk_title(raw_text, idx + 1)
            module = self._normalize_module_name(c.get("module"), requirement_text=raw_text)
            if not module:
                module = self._guess_chunk_module(raw_text, modules)

            if len(raw_text) > self.chunk_max_chars:
                sub_chunks = _smart_split_text(
                    raw_text,
                    max_chunks=4,
                    min_chars=self.chunk_min_chars,
                    max_chars=self.chunk_max_chars,
                )
                for sub_idx, sub_text in enumerate(sub_chunks, 1):
                    sub_text = _clean_chunk_text(sub_text)
                    if not sub_text:
                        continue
                    norm_chunks.append({
                        "id": f"{chunk_id}-{sub_idx}",
                        "title": f"{title} - {sub_idx}",
                        "module": self._guess_chunk_module(sub_text, modules) or module,
                        "text": sub_text,
                    })
            else:
                norm_chunks.append({
                    "id": chunk_id,
                    "title": title,
                    "module": module,
                    "text": raw_text,
                })

            if len(norm_chunks) >= self.max_chunks:
                break

        if len(norm_chunks) < 6:
            extra_chunks = _smart_split_text(
                requirement_text,
                max_chunks=max(6, self.max_chunks),
                min_chars=self.chunk_min_chars,
                max_chars=self.chunk_max_chars,
            )

            existing_texts = {str(x.get("text") or "").strip() for x in norm_chunks if isinstance(x, dict)}
            for c in extra_chunks:
                c = _clean_chunk_text(c)
                if not c or c in existing_texts:
                    continue
                norm_chunks.append({
                    "id": f"C{len(norm_chunks) + 1}",
                    "title": self._make_chunk_title(c, len(norm_chunks) + 1),
                    "module": self._guess_chunk_module(c, modules),
                    "text": c,
                })
                existing_texts.add(c)
                if len(norm_chunks) >= max(6, min(self.max_chunks, len(extra_chunks))):
                    break

        final_chunks: List[Dict[str, Any]] = []
        for idx, item in enumerate(norm_chunks[: self.max_chunks], 1):
            if not isinstance(item, dict):
                continue

            text = _clean_chunk_text(item.get("text") or "")
            if not text:
                continue

            module = self._normalize_module_name(item.get("module"), requirement_text=text)
            if not module:
                module = self._guess_chunk_module(text, modules)

            final_chunks.append({
                "id": f"C{idx}",
                "title": str(item.get("title") or self._make_chunk_title(text, idx)).strip() or self._make_chunk_title(text, idx),
                "module": module,
                "text": text,
            })

        if not final_chunks:
            fallback_chunks = _smart_split_text(
                requirement_text,
                self.max_chunks,
                self.chunk_min_chars,
                self.chunk_max_chars,
            )
            final_chunks = [
                {
                    "id": f"C{i + 1}",
                    "title": self._make_chunk_title(c, i + 1),
                    "module": self._guess_chunk_module(c, modules),
                    "text": c,
                }
                for i, c in enumerate(fallback_chunks)
            ]

        return final_chunks[: self.max_chunks]

    # =================================================
    # Fallback Plan
    # =================================================
    def _fallback_plan(
        self,
        requirement_text: str,
        confirmed_hint: str = "",
        extra_requirement: str = "",
    ) -> PlanJSON:
        merged_text = self._merge_requirement_text(requirement_text, extra_requirement)

        modules = self._infer_modules(merged_text)
        module_names = [str(x.get("name") or "").strip() for x in modules]

        objects = self._infer_objects(merged_text, module_names)
        rules = self._infer_rules(merged_text, modules)
        rule_groups = self._build_rule_groups(rules)
        states = self._infer_states(merged_text, modules)
        inputs = self._infer_inputs(merged_text, modules)
        risks = self._infer_risks(merged_text, modules)
        coverage_targets = self._infer_coverage_targets(extra_requirement, merged_text)
        test_methods = self._infer_test_methods(merged_text, extra_requirement)
        test_strategy = self._build_test_strategy(extra_requirement, merged_text)
        scenario_matrix = self._build_scenario_matrix(modules, coverage_targets, extra_requirement, merged_text)
        field_matrix = self._build_field_matrix(inputs)
        state_matrix = self._build_state_matrix(states)
        risk_matrix = self._build_risk_matrix(risks)
        priorities = self._build_priorities(modules, rules, risks, extra_requirement, merged_text)

        chunks = _smart_split_text(
            requirement_text,
            self.max_chunks,
            self.chunk_min_chars,
            self.chunk_max_chars,
        )

        return {
            "modules": modules,
            "objects": objects,
            "rules": rules,
            "rule_groups": rule_groups,
            "states": states,
            "inputs": inputs,
            "risks": risks,
            "coverage_targets": coverage_targets,
            "test_methods": test_methods,
            "test_strategy": test_strategy,
            "scenario_matrix": scenario_matrix,
            "field_matrix": field_matrix,
            "state_matrix": state_matrix,
            "risk_matrix": risk_matrix,
            "priorities": priorities,
            "chunks": [
                {
                    "id": f"C{i + 1}",
                    "title": self._make_chunk_title(c, i + 1),
                    "module": self._guess_chunk_module(c, modules),
                    "text": c,
                }
                for i, c in enumerate(chunks)
            ],
        }

    # =================================================
    # Infer helpers
    # =================================================
    def _merge_requirement_text(self, requirement_text: str, extra_requirement: str) -> str:
        requirement_text = _clean_chunk_text(requirement_text)
        extra_requirement = _clean_chunk_text(extra_requirement)
        if not extra_requirement:
            return requirement_text
        return (
            "【用户补充测试要求（最高优先级）】\n"
            f"{extra_requirement}\n\n"
            "【原始需求文档】\n"
            f"{requirement_text}"
        ).strip()

    def _normalize_module_name(self, module_name: Any, requirement_text: str = "") -> str:
        s = str(module_name or "").strip()
        if not s:
            return ""

        s = s.replace("【", "").replace("】", "").replace("[", "").replace("]", "").strip()
        s = re.sub(r"\s+", "", s)

        if self._looks_like_requirement_id(s):
            return ""
        if s in _COMMON_NOISE_NAMES:
            return ""
        if len(s) <= 1:
            return ""
        if len(s) > 30:
            return ""
        if re.fullmatch(r"[A-Za-z0-9_\-]+", s) and not re.search(r"[A-Za-z]{2,}[a-zA-Z]*", s):
            return ""

        return s

    def _infer_modules(self, text: str) -> List[Dict[str, Any]]:
        text = _clean_chunk_text(text)
        found: List[Dict[str, Any]] = []
        seen = set()

        explicit = self._extract_candidate_modules_from_text(text)
        for name in explicit:
            name = self._normalize_module_name(name, requirement_text=text)
            if not name or name in seen:
                continue
            seen.add(name)
            merged = f"{name} {text}"
            found.append({
                "name": name,
                "desc": f"{name}相关功能、规则与交互场景",
                "priority": self._infer_priority(merged),
                "risk": self._infer_risk(merged),
            })

        # 从高频对象里再补一层
        noun_candidates = self._extract_candidate_objects_from_text(text)
        for name in noun_candidates:
            name = self._normalize_module_name(name, requirement_text=text)
            if not name or name in seen:
                continue
            if name in _COMMON_NOISE_NAMES:
                continue
            if len(name) < 2 or len(name) > 12:
                continue
            seen.add(name)
            found.append({
                "name": name,
                "desc": f"{name}相关功能、规则与交互场景",
                "priority": self._infer_priority(name),
                "risk": self._infer_risk(name),
            })
            if len(found) >= 16:
                break

        if not found:
            found = [{"name": "核心功能", "desc": "核心功能相关业务场景", "priority": "P1", "risk": "中"}]

        return found[:20]

    def _infer_objects(self, text: str, module_names: List[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()

        for name in self._extract_candidate_objects_from_text(text):
            clean_name = self._clean_object_name(name)
            if not clean_name:
                continue
            module = self._best_match_module(clean_name, module_names)
            key = (module, clean_name)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "name": clean_name,
                "module": module,
                "desc": f"{clean_name}对象",
            })
            if len(out) >= 60:
                break

        return out

    def _infer_rules(self, text: str, modules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        parts = re.split(r"[。\n；;!?！？]", text or "")
        out: List[Dict[str, Any]] = []
        seen = set()

        rule_keywords = [
            "必须", "应", "需要", "支持", "不支持", "不能", "禁止",
            "校验", "限制", "最小", "最大", "不少于", "不大于",
            "精度", "位数", "唯一", "仅限", "至少", "最多",
            "不能为空", "非空", "格式", "范围", "阈值", "拦截",
            "幂等", "重复提交", "重复点击", "防重", "一致", "同步",
            "切换", "保持", "刷新后", "重新进入", "恢复", "状态",
        ]

        module_names = [str(x.get("name") or "").strip() for x in modules]

        for p in parts:
            s = p.strip()
            if len(s) < 6:
                continue
            if not any(k in s for k in rule_keywords):
                continue
            rule = self._clean_rule_text(s)
            if not rule:
                continue
            key = re.sub(r"\s+", "", rule)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "rule": _safe_slice(rule, 180),
                "module": self._best_match_module(s, module_names),
                "risk": self._infer_risk(s),
                "priority": self._infer_priority(s),
            })
            if len(out) >= 80:
                break

        # 弱语义兜底：只补通用高价值规则
        fallback_rules = self._infer_critical_generic_rules(text, module_names)
        for item in fallback_rules:
            key = re.sub(r"\s+", "", str(item.get("rule") or ""))
            if key and key not in seen:
                seen.add(key)
                out.append(item)

        return out[:100]

    def _infer_critical_generic_rules(self, text: str, module_names: List[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []

        if any(k in text for k in ["提交", "保存", "确认", "删除", "创建", "新增", "修改"]):
            out.append({
                "rule": "关键提交类操作需校验重复提交、防重处理或幂等结果。",
                "module": self._best_match_module(text, module_names),
                "risk": "高",
                "priority": "P0",
            })

        if any(k in text for k in ["金额", "数量", "价格", "精度", "位数", "余额", "可用额度", "手续费"]):
            out.append({
                "rule": "关键数值字段需校验边界值、精度处理、展示结果与实际处理结果一致性。",
                "module": self._best_match_module(text, module_names),
                "risk": "高",
                "priority": "P0",
            })

        if any(k in text for k in ["状态", "切换", "流转", "刷新后", "重新进入", "恢复"]):
            out.append({
                "rule": "状态类需求需校验进入条件、退出条件、异常切换及刷新/重进后的状态保持行为。",
                "module": self._best_match_module(text, module_names),
                "risk": "高",
                "priority": "P0",
            })

        if any(k in text for k in ["权限", "角色", "越权", "登录", "认证"]):
            out.append({
                "rule": "权限相关功能需校验未登录、角色不匹配、越权访问时的拦截与提示结果。",
                "module": self._best_match_module(text, module_names),
                "risk": "高",
                "priority": "P0",
            })

        if any(k in text for k in ["列表", "详情", "统计", "结果", "展示", "资产"]):
            out.append({
                "rule": "关键结果需校验列表页、详情页、统计数据与实际业务状态保持一致。",
                "module": self._best_match_module(text, module_names),
                "risk": "高",
                "priority": "P0",
            })

        return out

    def _build_rule_groups(self, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        groups: Dict[Tuple[str, str], List[str]] = {}
        for item in rules or []:
            rule = str(item.get("rule") or "").strip()
            module = str(item.get("module") or "").strip()
            if not rule:
                continue

            if any(k in rule for k in ["价格", "数量", "金额", "精度", "位数", "最小", "最大", "边界"]):
                group_name = "输入与边界规则"
            elif any(k in rule for k in ["权限", "角色", "越权", "登录", "认证"]):
                group_name = "权限与安全规则"
            elif any(k in rule for k in ["状态", "流转", "提交", "撤销", "关闭", "生效", "幂等", "重复提交", "防重", "保持"]):
                group_name = "状态与提交流程规则"
            elif any(k in rule for k in ["提示", "文案", "展示", "弹窗", "按钮", "页面", "交互"]):
                group_name = "页面与交互规则"
            elif any(k in rule for k in ["一致", "同步", "列表", "详情", "统计", "结果", "计算", "公式"]):
                group_name = "结果一致性规则"
            else:
                group_name = "业务规则"

            groups.setdefault((group_name, module), []).append(rule)

        out = []
        for (group_name, module), rules_in_group in groups.items():
            out.append({
                "name": group_name,
                "module": module,
                "rules": rules_in_group[:15],
            })
        return out

    def _infer_states(self, text: str, modules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        state_words = [
            "待提交", "处理中", "成功", "失败", "已完成", "已关闭",
            "待支付", "已支付", "待审核", "审核中", "审核通过", "审核拒绝",
            "待处理", "已取消", "已删除", "已保存", "已提交", "已生效",
            "启用", "禁用", "待确认", "已确认", "选中", "未选中",
        ]
        module_names = [str(x.get("name") or "").strip() for x in modules]
        found_states: List[Dict[str, Any]] = []
        seen = set()

        for word in state_words:
            if word not in text:
                continue
            module = self._best_match_module(text, module_names)
            key = (module, word)
            if key in seen:
                continue
            seen.add(key)
            found_states.append({
                "module": module,
                "state": word,
                "transitions": self._infer_transitions_for_state(word),
            })

        return found_states[:50]

    def _infer_transitions_for_state(self, state: str) -> List[Dict[str, str]]:
        mapping = {
            "待提交": [{"event": "提交", "to": "处理中"}],
            "处理中": [{"event": "成功返回", "to": "成功"}, {"event": "失败返回", "to": "失败"}],
            "待支付": [{"event": "支付成功", "to": "已支付"}, {"event": "取消支付", "to": "已取消"}],
            "待审核": [{"event": "提交审核", "to": "审核中"}],
            "审核中": [{"event": "审核通过", "to": "审核通过"}, {"event": "审核拒绝", "to": "审核拒绝"}],
            "启用": [{"event": "禁用", "to": "禁用"}],
            "禁用": [{"event": "启用", "to": "启用"}],
            "待确认": [{"event": "确认", "to": "已确认"}, {"event": "取消", "to": "已取消"}],
            "未选中": [{"event": "选择", "to": "选中"}],
            "选中": [{"event": "取消选择", "to": "未选中"}],
        }
        return mapping.get(state, [])

    def _infer_inputs(self, text: str, modules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        module_names = [str(x.get("name") or "").strip() for x in modules]
        out: List[Dict[str, Any]] = []
        seen = set()

        candidates = self._extract_candidate_fields_from_text(text)
        for field in candidates:
            clean_field = self._clean_field_name(field)
            if not clean_field:
                continue
            module = self._best_match_module(clean_field + " " + text, module_names)
            key = (module, clean_field)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "field": clean_field,
                "module": module,
                "type": self._infer_field_type(clean_field),
                "constraints": self._infer_constraints(clean_field, text),
                "priority": self._infer_priority(clean_field + " " + text),
            })
            if len(out) >= 80:
                break

        return out[:100]

    def _infer_risks(self, text: str, modules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        module_names = [str(x.get("name") or "").strip() for x in modules]
        out: List[Dict[str, Any]] = []
        seen = set()

        risk_templates = [
            ("重复提交导致重复处理或结果异常", "可能引起重复执行、状态错乱或数据重复写入"),
            ("异常输入绕过校验", "可能导致脏数据、错误结果或页面异常"),
            ("状态流转异常", "可能导致页面展示与实际结果不一致"),
            ("权限控制缺失或越权访问", "可能导致敏感操作被非授权用户执行"),
            ("金额/数量/精度计算错误", "可能导致业务结果错误或资产风险"),
            ("弱网/超时/重试处理不当", "可能导致提交结果不明确或前后状态不一致"),
            ("提示文案不准确", "可能导致用户误解当前业务状态"),
            ("前后数据不一致", "可能导致列表、详情、统计结果不一致"),
            ("刷新后状态或结果丢失", "可能导致用户看到的结果与实际处理状态不一致"),
        ]

        for risk, impact in risk_templates:
            if self._risk_relevant(risk, text):
                module = self._best_match_module(risk + " " + text, module_names)
                key = f"{module}||{risk}"
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "risk": risk,
                    "impact": impact,
                    "module": module,
                    "priority": self._infer_priority(risk + " " + text),
                })

        if not out:
            out.append({
                "risk": "核心流程处理异常",
                "impact": "可能导致主要业务不可用或结果错误",
                "module": self._best_match_module(text, module_names),
                "priority": "P0",
            })

        return out[:80]

    def _infer_coverage_targets(self, extra_requirement: str, text: str) -> List[str]:
        result: List[str] = []
        mapping = [
            ("Happy", ["主流程", "正常流程", "核心流程", "成功流程"]),
            ("Negative", ["异常", "异常流程", "失败场景", "错误场景", "拦截"]),
            ("UI", ["页面", "ui", "交互", "弹窗", "文案", "按钮", "列表", "详情", "空态", "加载态"]),
            ("Input", ["边界", "边界值", "输入", "校验", "格式", "精度", "参数", "位数"]),
            ("NFR", ["性能", "并发", "稳定性", "超时", "弱网", "重试"]),
            ("Security", ["权限", "角色", "越权", "安全", "风控", "认证"]),
            ("Compat", ["兼容", "浏览器", "机型", "分辨率", "深色模式", "浅色模式"]),
        ]

        merged = f"{extra_requirement}\n{text}".lower()
        for target, keys in mapping:
            if any(k in merged for k in keys):
                result.append(target)

        if not result:
            return list(_DEFAULT_COVERAGE_TARGETS)

        return self._merge_unique_str_list(result, _DEFAULT_COVERAGE_TARGETS)

    def _infer_test_methods(self, text: str, extra_requirement: str) -> List[str]:
        merged = f"{extra_requirement}\n{text}".lower()
        methods = ["Scenario"]

        if any(k in merged for k in ["状态", "流转", "切换", "撤销", "关闭", "恢复", "保持"]):
            methods.append("StateTransition")
        if any(k in merged for k in ["规则", "条件", "组合", "矩阵", "权限", "额度", "风控", "幂等", "一致性", "公式", "计算"]):
            methods.append("DecisionTable")
        if any(k in merged for k in ["输入", "格式", "必填", "非法字符", "参数", "选项"]):
            methods.append("ECP")
        if any(k in merged for k in ["边界", "最小", "最大", "精度", "位数", "超长"]):
            methods.append("BVA")
        if any(k in merged for k in ["性能", "并发", "超时", "弱网", "重试", "稳定性"]):
            methods.append("OATS")
        if any(k in merged for k in ["异常", "失败", "错误", "提示", "重复提交", "幂等", "防重"]):
            methods.append("ErrorGuessing")
        if any(k in merged for k in ["页面", "按钮", "展示", "弹窗", "兼容", "交互"]):
            methods.append("Exploratory")

        return self._merge_unique_str_list(methods, _DEFAULT_TEST_METHODS)

    def _build_test_strategy(self, extra_requirement: str, text: str) -> Dict[str, Any]:
        focus = self._extract_focus_items(extra_requirement)
        if not focus:
            focus = [
                "优先覆盖核心主流程",
                "优先覆盖异常流程与边界条件",
                "优先覆盖关键规则与状态流转",
            ]

        principles = [
            "优先覆盖用户补充测试要求",
            "核心模块优先设计 P0 场景",
            "同一规则拆分正常/异常/边界三类场景",
            "字段校验、状态切换、权限控制、数据一致性分别建模",
            "关键提交类动作必须覆盖重复提交、防重或幂等处理",
            "关键页面结果必须覆盖列表、详情、统计和刷新后结果保持",
        ]

        if any(k in text for k in ["金额", "数量", "精度", "余额", "价格", "资产"]):
            principles.append("金额、数量、精度、余额等高风险字段单独拆分测试")

        return {
            "focus": focus,
            "priority_rule": "用户补充测试要求 > 核心业务流程 > 高风险规则 > 状态流转 > UI/提示文案。",
            "design_principles": self._merge_unique_str_list(principles, []),
            "case_granularity": "测试用例需细化到字段、规则、状态、异常、结果反馈和数据一致性层级，避免笼统大用例。",
        }

    def _build_scenario_matrix(
        self,
        modules: List[Dict[str, Any]],
        coverage_targets: List[str],
        extra_requirement: str,
        text: str,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []

        base_types = []
        if "Happy" in coverage_targets:
            base_types.append("正常流程")
        if "Negative" in coverage_targets:
            base_types.append("异常流程")
        if "Input" in coverage_targets:
            base_types.append("边界条件")
        base_types.extend(["状态流转", "数据一致性"])
        if "Security" in coverage_targets:
            base_types.append("权限校验")
        if "UI" in coverage_targets:
            base_types.append("页面交互")
        if "NFR" in coverage_targets:
            base_types.append("弱网/超时/重试")
        if "Compat" in coverage_targets:
            base_types.append("兼容性")

        base_types = self._merge_unique_str_list(base_types, [])

        for module in modules or []:
            name = str(module.get("name") or "").strip()
            if not name:
                continue

            must_cover = self._build_module_must_cover(name, text)
            if extra_requirement:
                must_cover.extend(self._extract_focus_items(extra_requirement)[:4])

            out.append({
                "module": name,
                "scenario_types": base_types,
                "must_cover": self._merge_unique_str_list(must_cover, []),
                "recommended_methods": self._infer_recommended_methods_for_module(name, text),
            })

        return out[:40]

    def _build_field_matrix(self, inputs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in inputs or []:
            field = str(item.get("field") or "").strip()
            if not field:
                continue

            must_cover = ["合法值", "非法值", "空值", "边界值"]
            if any(k in field for k in ["价格", "数量", "金额", "精度", "位数", "手续费", "保证金", "余额", "可用额度"]):
                must_cover.extend(["最小值", "最大值", "精度位数", "超范围值", "展示结果校验"])
            if any(k in field for k in ["验证码", "手机号", "邮箱", "密码"]):
                must_cover.extend(["格式校验", "长度校验", "特殊字符校验"])
            if any(k in field for k in ["状态", "类型", "模式", "方向", "选项"]):
                must_cover.extend(["默认值", "切换后结果", "非法组合"])

            out.append({
                "field": field,
                "module": str(item.get("module") or "").strip(),
                "must_cover": self._merge_unique_str_list(must_cover, []),
                "recommended_methods": self._infer_recommended_methods_for_field(field),
            })
        return out[:100]

    def _build_state_matrix(self, states: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in states or []:
            state = str(item.get("state") or "").strip()
            if not state:
                continue
            out.append({
                "module": str(item.get("module") or "").strip(),
                "state": state,
                "must_cover": [
                    "进入条件正确",
                    "退出条件正确",
                    "异常切换处理正确",
                    "刷新后状态保持正确",
                ],
                "recommended_methods": ["StateTransition", "Scenario"],
            })
        return out[:80]

    def _build_risk_matrix(self, risks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in risks or []:
            risk = str(item.get("risk") or "").strip()
            if not risk:
                continue

            must_cover = ["风险触发条件", "拦截机制", "提示反馈", "结果一致性"]
            if any(k in risk for k in ["权限", "越权", "安全"]):
                must_cover.extend(["未登录访问", "角色不匹配访问", "敏感操作校验"])
            if any(k in risk for k in ["重复提交", "弱网", "超时", "重试"]):
                must_cover.extend(["重复点击", "慢返回", "重试后幂等"])
            if any(k in risk for k in ["数据不一致", "状态流转异常", "刷新后状态或结果丢失"]):
                must_cover.extend(["刷新后结果保持", "列表详情统计一致"])

            out.append({
                "module": str(item.get("module") or "").strip(),
                "risk": risk,
                "must_cover": self._merge_unique_str_list(must_cover, []),
                "recommended_methods": ["Scenario", "ErrorGuessing"],
            })
        return out[:80]

    def _build_priorities(
        self,
        modules: List[Dict[str, Any]],
        rules: List[Dict[str, Any]],
        risks: List[Dict[str, Any]],
        extra_requirement: str,
        text: str,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for module in modules or []:
            name = str(module.get("name") or "").strip()
            if not name:
                continue

            p0_focus = self._build_priority_focus(name, text, level="P0")
            p1_focus = self._build_priority_focus(name, text, level="P1")
            p2_focus = self._build_priority_focus(name, text, level="P2")

            for r in rules:
                if str(r.get("module") or "").strip() == name and str(r.get("priority") or "") == "P0":
                    p0_focus.append(str(r.get("rule") or "")[:60])

            for rk in risks:
                if str(rk.get("module") or "").strip() == name:
                    p0_focus.append(str(rk.get("risk") or "")[:60])

            if extra_requirement:
                focus_items = self._extract_focus_items(extra_requirement)
                p0_focus.extend(focus_items[:2])
                p1_focus.extend(focus_items[2:4])

            out.append({
                "module": name,
                "p0_focus": self._merge_unique_str_list(p0_focus, []),
                "p1_focus": self._merge_unique_str_list(p1_focus, []),
                "p2_focus": self._merge_unique_str_list(p2_focus, []),
            })
        return out[:40]

    # =================================================
    # Normalize & Merge utils
    # =================================================
    def _normalize_priority(self, value: Any) -> str:
        s = str(value or "").strip().upper()
        if s in {"P0", "P1", "P2"}:
            return s
        if "0" in s or "高" in s:
            return "P0"
        if "2" in s or "低" in s:
            return "P2"
        return "P1"

    def _normalize_risk(self, value: Any) -> str:
        s = str(value or "").strip()
        if s in {"高", "中", "低"}:
            return s
        lower = s.lower()
        if "high" in lower:
            return "高"
        if "low" in lower:
            return "低"
        return "中"

    def _normalize_string_list(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        out = []
        seen = set()
        for item in value:
            s = str(item or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    def _merge_unique_str_list(self, a: List[str], b: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for source in (a or [], b or []):
            for item in source:
                s = str(item or "").strip()
                if not s or s in seen:
                    continue
                seen.add(s)
                out.append(s)
        return out

    def _merge_list_by_keys(
        self,
        primary: List[Dict[str, Any]],
        secondary: List[Dict[str, Any]],
        keys: Tuple[str, ...],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()

        def make_key(item: Dict[str, Any]) -> str:
            vals = [str(item.get(k) or "").strip() for k in keys]
            return "||".join(vals)

        for source in (primary or [], secondary or []):
            for item in source:
                if not isinstance(item, dict):
                    continue
                k = make_key(item)
                if not k.strip("|") or k in seen:
                    continue
                seen.add(k)
                out.append(item)
        return out

    def _merge_test_strategy(self, primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "focus": self._merge_unique_str_list(
                self._normalize_string_list(primary.get("focus")),
                self._normalize_string_list(secondary.get("focus")),
            ),
            "priority_rule": str(primary.get("priority_rule") or secondary.get("priority_rule") or "").strip(),
            "design_principles": self._merge_unique_str_list(
                self._normalize_string_list(primary.get("design_principles")),
                self._normalize_string_list(secondary.get("design_principles")),
            ),
            "case_granularity": str(primary.get("case_granularity") or secondary.get("case_granularity") or "").strip(),
        }

    # =================================================
    # Semantic helpers
    # =================================================
    def _best_match_module(self, text: str, module_names: List[str]) -> str:
        text = text or ""
        if not module_names:
            return ""

        for name in sorted(module_names, key=lambda x: len(x), reverse=True):
            if name and name in text:
                return name

        return module_names[0] if module_names else ""

    def _infer_priority(self, text: str) -> str:
        s = (text or "").lower()
        if any(k in s for k in _PRIORITY_P0_HINTS):
            return "P0"
        if any(k in s for k in _PRIORITY_P1_HINTS):
            return "P1"
        return "P2"

    def _infer_risk(self, text: str) -> str:
        s = (text or "").lower()
        if any(k in s for k in _RISK_HIGH_HINTS):
            return "高"
        if any(k in s for k in _RISK_MEDIUM_HINTS):
            return "中"
        return "低"

    def _infer_field_type(self, field: str) -> str:
        if any(k in field for k in ["价格", "数量", "金额", "余额", "手续费", "倍数", "保证金", "额度", "比例", "阈值"]):
            return "number"
        if any(k in field for k in ["验证码", "手机号", "邮箱", "密码", "昵称", "姓名", "证件号", "地址"]):
            return "string"
        if any(k in field for k in ["类型", "模式", "方向", "币种", "账户类型", "订单类型", "样式", "选项", "状态"]):
            return "enum"
        if any(k in field for k in ["时间", "日期"]):
            return "date"
        if any(k in field for k in ["开关", "是否"]):
            return "bool"
        return "unknown"

    def _infer_constraints(self, field: str, text: str) -> List[str]:
        constraints = []
        merged = f"{field} {text}".lower()

        if any(k in merged for k in ["必填", "不能为空", "非空"]):
            constraints.append("非空")
        if any(k in merged for k in ["格式", "合法格式"]):
            constraints.append("格式校验")
        if any(k in merged for k in ["最小", "最少", "下限"]):
            constraints.append("最小值限制")
        if any(k in merged for k in ["最大", "最多", "上限"]):
            constraints.append("最大值限制")
        if any(k in merged for k in ["精度", "位数", "小数"]):
            constraints.append("精度/位数限制")
        if any(k in merged for k in ["唯一"]):
            constraints.append("唯一性")
        if any(k in merged for k in ["范围", "区间", "阈值"]):
            constraints.append("范围限制")
        if any(k in merged for k in ["幂等", "重复提交", "防重"]):
            constraints.append("重复提交/幂等限制")
        if any(k in merged for k in ["刷新后", "重新进入", "保持", "恢复"]):
            constraints.append("状态保持/持久化限制")

        if any(k in field for k in ["价格", "数量", "金额", "手续费", "余额", "可用额度"]):
            constraints.extend(["边界值", "精度校验", "结果展示校验"])
        if any(k in field for k in ["手机号", "邮箱", "验证码", "密码"]):
            constraints.extend(["格式校验", "长度校验"])
        if any(k in field for k in ["状态", "类型", "模式", "方向"]):
            constraints.extend(["默认值", "切换结果校验"])

        return self._merge_unique_str_list(constraints, [])

    def _risk_relevant(self, risk: str, text: str) -> bool:
        mapping = {
            "重复提交": ["提交", "保存", "下单", "确认", "创建", "修改"],
            "异常输入": ["输入", "参数", "校验", "格式"],
            "状态流转": ["状态", "切换", "流转", "撤销", "关闭", "保持", "刷新"],
            "权限": ["权限", "角色", "越权", "登录", "认证"],
            "金额": ["金额", "数量", "价格", "精度", "余额", "手续费"],
            "弱网": ["超时", "重试", "弱网", "网络", "刷新"],
            "提示文案": ["提示", "文案", "反馈", "弹窗"],
            "数据不一致": ["列表", "详情", "状态", "统计", "结果", "余额", "资产", "展示"],
        }
        for key, words in mapping.items():
            if key in risk and any(w in text for w in words):
                return True
        return True

    def _extract_focus_items(self, extra_requirement: str) -> List[str]:
        items = re.split(r"[,\n，。；;、:：]+", extra_requirement or "")
        out = []
        seen = set()
        for item in items:
            s = item.strip()
            if len(s) < 2 or s in seen:
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= 16:
                break
        return out

    def _infer_recommended_methods_for_module(self, module_name: str, text: str) -> List[str]:
        methods = ["Scenario"]
        merged = f"{module_name} {text}"

        if any(k in merged for k in ["状态", "切换", "撤销", "关闭", "流转", "保持"]):
            methods.append("StateTransition")
        if any(k in merged for k in ["规则", "风控", "权限", "额度", "一致性", "幂等", "公式", "计算"]):
            methods.append("DecisionTable")
        if any(k in merged for k in ["输入", "格式", "参数", "选项"]):
            methods.append("ECP")
        if any(k in merged for k in ["边界", "精度", "位数", "最大", "最小"]):
            methods.append("BVA")
        if any(k in merged for k in ["异常", "失败", "重复提交", "幂等", "防重"]):
            methods.append("ErrorGuessing")
        if any(k in merged for k in ["弱网", "超时", "并发", "重试"]):
            methods.append("OATS")
        if any(k in merged for k in ["页面", "展示", "弹窗", "按钮", "交互"]):
            methods.append("Exploratory")

        return self._merge_unique_str_list(methods, [])

    def _infer_recommended_methods_for_field(self, field: str) -> List[str]:
        methods = ["ECP", "BVA"]
        if any(k in field for k in ["价格", "数量", "金额", "精度", "位数", "手续费", "保证金"]):
            methods.append("DecisionTable")
        if any(k in field for k in ["验证码", "邮箱", "手机号", "密码"]):
            methods.append("ErrorGuessing")
        if any(k in field for k in ["状态", "类型", "模式", "方向", "选项"]):
            methods.append("Scenario")
        return self._merge_unique_str_list(methods, [])

    def _guess_chunk_module(self, chunk_text: str, modules: List[Dict[str, Any]]) -> str:
        module_names = [str(x.get("name") or "").strip() for x in modules if str(x.get("name") or "").strip()]
        return self._best_match_module(chunk_text, module_names)

    # =================================================
    # Repair / Extra helpers
    # =================================================
    def _looks_like_requirement_id(self, s: str) -> bool:
        if not s:
            return False
        ss = str(s).strip()
        return any(p.match(ss) for p in _REQUIREMENT_ID_PATTERNS)

    def _clean_rule_text(self, value: Any) -> str:
        s = str(value or "").strip()
        if not s:
            return ""
        s = re.sub(r"\s+", " ", s)
        if self._looks_like_requirement_id(s):
            return ""
        if len(s) < 4:
            return ""
        if s in _COMMON_NOISE_NAMES:
            return ""
        return s

    def _clean_field_name(self, value: Any) -> str:
        s = str(value or "").strip()
        if not s:
            return ""
        s = s.replace("【", "").replace("】", "").replace("[", "").replace("]", "").strip()
        s = re.sub(r"\s+", "", s)
        if self._looks_like_requirement_id(s):
            return ""
        if s in _COMMON_NOISE_NAMES:
            return ""
        if len(s) > 30:
            return ""
        return s

    def _clean_object_name(self, value: Any) -> str:
        s = str(value or "").strip()
        if not s:
            return ""
        s = s.replace("【", "").replace("】", "").replace("[", "").replace("]", "").strip()
        s = re.sub(r"\s+", "", s)
        if self._looks_like_requirement_id(s):
            return ""
        if s in _COMMON_NOISE_NAMES:
            return ""
        if len(s) > 30:
            return ""
        return s

    def _clean_risk_text(self, value: Any) -> str:
        s = str(value or "").strip()
        if not s:
            return ""
        s = re.sub(r"\s+", " ", s)
        if self._looks_like_requirement_id(s):
            return ""
        if len(s) < 4:
            return ""
        return s

    def _extract_candidate_modules_from_text(self, text: str) -> List[str]:
        results: List[str] = []
        seen = set()

        patterns = [
            r"(?:模块|功能|场景|页面|菜单|入口)[：:\s]*([^\n，。；;]{2,24})",
            r"《([^》]{2,24})》",
            r"【([^】]{2,24})】",
            r"(?m)^(?:\d+[\.\、]\s*|[一二三四五六七八九十]+[、\.]\s*)([^\n]{2,24})$",
        ]

        for p in patterns:
            for m in re.finditer(p, text):
                val = self._normalize_module_name(m.group(1), requirement_text=text)
                if not val or val in seen:
                    continue
                seen.add(val)
                results.append(val)
                if len(results) >= 16:
                    return results

        return results

    def _extract_candidate_objects_from_text(self, text: str) -> List[str]:
        results: List[str] = []
        seen = set()

        patterns = [
            r"[\"“”'‘’《》\[\]【】]([^\"“”'‘’《》\[\]【】]{2,24})[\"“”'‘’《》\[\]【】]",
            r"([^\s，。；;:：]{2,20}(?:按钮|弹窗|列表|下拉框|输入框|复选框|单选框|标签页|详情页|筛选器|搜索框))",
            r"([^\s，。；;:：]{2,20}(?:状态|结果|记录|信息|配置|选项|参数))",
        ]

        for p in patterns:
            for m in re.finditer(p, text):
                val = self._clean_object_name(m.group(1))
                if not val or val in seen:
                    continue
                seen.add(val)
                results.append(val)
                if len(results) >= 80:
                    return results

        # 通用中文短语兜底
        for token in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9_\-]{2,20}", text or ""):
            val = self._clean_object_name(token)
            if not val or val in seen:
                continue
            if val in _COMMON_NOISE_NAMES:
                continue
            seen.add(val)
            results.append(val)
            if len(results) >= 80:
                break

        return results

    def _extract_candidate_fields_from_text(self, text: str) -> List[str]:
        results: List[str] = []
        seen = set()

        builtin = [
            "价格", "数量", "金额", "余额", "可用余额", "可用额度", "手续费",
            "验证码", "手机号", "邮箱", "密码", "昵称", "姓名", "证件号",
            "地址", "账户类型", "订单类型", "方向", "模式", "状态", "类型",
            "搜索词", "筛选条件", "保证金", "仓位", "限价", "市价", "时间", "日期",
        ]

        for field in builtin:
            if field in text and field not in seen:
                seen.add(field)
                results.append(field)

        patterns = [
            r"([^\s，。；;、:：]{2,30})字段",
            r"([^\s，。；;、:：]{2,30})参数",
            r"([^\s，。；;、:：]{2,30})输入",
            r"([^\s，。；;、:：]{2,30})选项",
            r"([^\s，。；;、:：]{2,30})值",
        ]
        for p in patterns:
            for m in re.finditer(p, text):
                field = self._clean_field_name(m.group(1))
                if not field or field in seen:
                    continue
                seen.add(field)
                results.append(field)
                if len(results) >= 80:
                    return results

        return results

    def _build_module_must_cover(self, module_name: str, text: str) -> List[str]:
        must_cover = [
            f"{module_name}核心主链路正确",
            f"{module_name}异常输入与非法操作拦截正确",
            f"{module_name}状态变化与结果数据一致",
        ]

        if any(k in text for k in ["提交", "保存", "确认", "删除", "创建", "新增", "修改"]):
            must_cover.append(f"{module_name}重复提交、防重或幂等处理正确")
        if any(k in text for k in ["金额", "数量", "价格", "精度", "余额", "可用额度"]):
            must_cover.extend([
                f"{module_name}关键数值字段边界值与精度规则正确",
                f"{module_name}展示结果与实际处理结果一致",
            ])
        if any(k in text for k in ["权限", "角色", "越权", "登录", "认证"]):
            must_cover.append(f"{module_name}权限拦截与提示反馈正确")
        if any(k in text for k in ["刷新后", "重新进入", "恢复", "状态", "切换"]):
            must_cover.append(f"{module_name}刷新或重进后的状态保持行为正确")

        return self._merge_unique_str_list(must_cover, [])

    def _build_priority_focus(self, module_name: str, text: str, level: str) -> List[str]:
        if level == "P0":
            result = [
                f"{module_name}核心主流程",
                f"{module_name}高风险规则校验",
                f"{module_name}状态流转正确性",
            ]
            if any(k in text for k in ["金额", "数量", "精度", "余额", "价格", "资产"]):
                result.extend([
                    f"{module_name}关键数值处理",
                    f"{module_name}结果一致性",
                ])
        elif level == "P1":
            result = [
                f"{module_name}异常流程与边界值",
                f"{module_name}提示文案与交互反馈",
            ]
            if any(k in text for k in ["刷新后", "重新进入", "恢复", "切换"]):
                result.append(f"{module_name}刷新或重进保持")
        else:
            result = [f"{module_name}兼容性与次要展示细节"]

        return self._merge_unique_str_list(result, [])

    def _make_chunk_title(self, text: str, idx: int) -> str:
        text = _clean_chunk_text(text)
        if not text:
            return f"Chunk {idx}"

        if any(k in text for k in ["状态", "流转", "切换", "刷新后", "重新进入"]):
            return f"状态与切换片段 {idx}"
        if any(k in text for k in ["规则", "校验", "限制", "公式", "计算"]):
            return f"规则与校验片段 {idx}"
        if any(k in text for k in ["字段", "参数", "输入", "选项"]):
            return f"字段与输入片段 {idx}"
        if any(k in text for k in ["页面", "弹窗", "按钮", "展示", "列表", "详情"]):
            return f"页面与展示片段 {idx}"
        return f"需求片段 {idx}"

    def _repair_scenario_matrix(
        self,
        scenario_matrix: List[Dict[str, Any]],
        modules: List[Dict[str, Any]],
        requirement_text: str,
    ) -> List[Dict[str, Any]]:
        existing = {str(x.get("module") or "").strip(): x for x in scenario_matrix if isinstance(x, dict)}
        for mod in modules:
            name = str(mod.get("name") or "").strip()
            if not name:
                continue
            if name not in existing:
                existing[name] = {
                    "module": name,
                    "scenario_types": ["正常流程", "异常流程", "边界条件", "状态流转", "数据一致性"],
                    "must_cover": self._build_module_must_cover(name, requirement_text),
                    "recommended_methods": self._infer_recommended_methods_for_module(name, requirement_text),
                }
            else:
                existing[name]["must_cover"] = self._merge_unique_str_list(
                    self._normalize_string_list(existing[name].get("must_cover")),
                    self._build_module_must_cover(name, requirement_text),
                )
                existing[name]["recommended_methods"] = self._merge_unique_str_list(
                    self._normalize_string_list(existing[name].get("recommended_methods")),
                    self._infer_recommended_methods_for_module(name, requirement_text),
                )
                existing[name]["scenario_types"] = self._merge_unique_str_list(
                    self._normalize_string_list(existing[name].get("scenario_types")),
                    ["正常流程", "异常流程", "边界条件", "状态流转", "数据一致性"],
                )
        return list(existing.values())[:40]

    def _repair_priorities(
        self,
        priorities: List[Dict[str, Any]],
        modules: List[Dict[str, Any]],
        requirement_text: str,
    ) -> List[Dict[str, Any]]:
        existing = {str(x.get("module") or "").strip(): x for x in priorities if isinstance(x, dict)}
        for mod in modules:
            name = str(mod.get("name") or "").strip()
            if not name:
                continue
            default_item = {
                "module": name,
                "p0_focus": self._build_priority_focus(name, requirement_text, "P0"),
                "p1_focus": self._build_priority_focus(name, requirement_text, "P1"),
                "p2_focus": self._build_priority_focus(name, requirement_text, "P2"),
            }
            if name not in existing:
                existing[name] = default_item
            else:
                existing[name]["p0_focus"] = self._merge_unique_str_list(
                    self._normalize_string_list(existing[name].get("p0_focus")),
                    default_item["p0_focus"],
                )
                existing[name]["p1_focus"] = self._merge_unique_str_list(
                    self._normalize_string_list(existing[name].get("p1_focus")),
                    default_item["p1_focus"],
                )
                existing[name]["p2_focus"] = self._merge_unique_str_list(
                    self._normalize_string_list(existing[name].get("p2_focus")),
                    default_item["p2_focus"],
                )
        return list(existing.values())[:40]