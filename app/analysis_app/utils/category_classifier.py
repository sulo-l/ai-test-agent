# app/analysis_app/utils/category_classifier.py
from __future__ import annotations

import re
import json
from typing import Dict, List, Optional, Any

from app.llm.client import LLM


class CategoryClassifier:
    """
    问题分类器
    目标：
    1. 优先用规则快速分类
    2. 对“项目背景 / 建设目标 / 业务价值 / 收益说明 / 动机描述”进行前置识别
    3. 无法规则命中时，使用 LLM 兜底
    """

    BACKGROUND_CATEGORY = "非需求背景"

    CATEGORIES: List[str] = [
        "完整性",
        "清晰性",
        "业务规则",
        "异常处理",
        "安全",
        "性能",
        "依赖",
        "数据",
        "状态机",
        "边界场景",
        "可维护性",
        "可扩展性",
        "合规性",
        "需求质量",
        "非需求背景",
    ]

    KEYWORD_RULES: Dict[str, List[str]] = {
        "安全": ["安全", "权限", "越权", "鉴权", "认证", "授权", "敏感信息", "脱敏"],
        "异常处理": ["异常", "失败", "报错", "错误", "重试", "超时", "兜底", "回滚", "补偿"],
        "性能": ["性能", "并发", "吞吐", "延迟", "响应时间", "容量", "qps", "tps", "耗时"],
        "业务规则": ["规则", "条件", "限制", "资格", "判定", "场景", "满足", "触发", "允许", "禁止"],
        "状态机": ["状态", "流转", "生命周期", "节点", "阶段", "迁移", "变更"],
        "数据": ["字段", "格式", "类型", "取值", "必填", "非必填", "默认值", "枚举", "入参", "出参"],
        "依赖": ["依赖", "接口", "第三方", "回调", "服务", "上游", "下游", "外部系统"],
        "边界场景": ["边界", "极端", "空数据", "空值", "重复", "最大值", "最小值", "临界值"],
        "清晰性": ["不明确", "歧义", "不清晰", "未说明", "不一致", "模糊", "待确认"],
        "完整性": ["缺失", "遗漏", "缺少", "未提供", "不完整", "没有说明", "未覆盖"],
        "可维护性": ["可维护", "维护成本", "耦合", "复用", "冗余"],
        "可扩展性": ["扩展", "兼容", "扩容", "预留", "可配置"],
        "合规性": ["合规", "审计", "监管", "隐私", "留痕", "日志审计"],
        "需求质量": ["需求", "说明", "描述", "文档", "评审", "质量"],
    }

    # 背景/目标/价值类词
    BACKGROUND_KEYWORDS: List[str] = [
        "项目背景",
        "需求背景",
        "建设背景",
        "业务背景",
        "背景说明",
        "方案背景",
        "现状分析",
        "当前现状",
        "痛点分析",
        "建设目标",
        "项目目标",
        "需求目标",
        "优化目标",
        "目标是",
        "目的在于",
        "旨在",
        "为了",
        "为提升",
        "为优化",
        "业务价值",
        "项目价值",
        "方案价值",
        "预期收益",
        "收益分析",
        "建设意义",
        "实施意义",
        "价值说明",
        "有助于",
        "提升体验",
        "优化效率",
        "提升效率",
        "增强能力",
        "支撑业务",
        "支撑发展",
    ]

    # 真实需求/可测试需求常见提示词
    REQUIREMENT_HINTS: List[str] = [
        "支持",
        "新增",
        "修改",
        "删除",
        "展示",
        "显示",
        "跳转",
        "进入",
        "提交",
        "保存",
        "查询",
        "校验",
        "限制",
        "必填",
        "非必填",
        "默认",
        "字段",
        "参数",
        "接口",
        "返回",
        "页面",
        "按钮",
        "弹窗",
        "列表",
        "详情",
        "权限",
        "角色",
        "状态",
        "流程",
        "节点",
        "异常",
        "失败",
        "成功",
        "提示",
        "规则",
        "条件",
        "触发",
        "不可",
        "不能",
        "必须",
        "应当",
    ]

    SYSTEM_PROMPT = (
        "你是一名资深软件需求评审专家。"
        "请根据问题描述判断其最合适的问题分类。"
        "如果内容主要是在描述项目背景、建设目标、业务价值、收益、动机，而不是具体功能规则、字段约束、状态流转、异常处理、权限逻辑，则分类为“非需求背景”。"
        "必须只输出 JSON。"
    )

    def __init__(self, enable_llm_fallback: bool = True):
        self.enable_llm_fallback = enable_llm_fallback
        self.llm = LLM()

    # =====================================================
    # 主入口
    # =====================================================

    def classify(
        self,
        message: str,
        title: Optional[str] = None,
        default: str = "需求质量",
    ) -> str:
        text = self._build_text(title=title, message=message)

        # 0) 先判定是否为背景类非需求内容
        background_category = self._classify_background(text)
        if background_category:
            return background_category

        # 1) 规则分类
        category = self._classify_by_rules(text)
        if category:
            return category

        # 2) LLM 兜底
        if self.enable_llm_fallback:
            try:
                category = self._classify_by_llm(text)
                if category:
                    return category
            except Exception:
                pass

        return default

    # =====================================================
    # 背景识别
    # =====================================================

    def _classify_background(self, text: str) -> Optional[str]:
        """
        优先识别“非需求背景”：
        - 命中明显背景/目标/价值词
        - 且缺乏具体功能/规则/字段/流程等真实需求信号
        """
        s = self._normalize_text(text)
        if not s:
            return None

        bg_score = self._keyword_score(s, self.BACKGROUND_KEYWORDS)
        req_score = self._keyword_score(s, self.REQUIREMENT_HINTS)

        # 标题型强命中
        if self._contains_section_title(s):
            return self.BACKGROUND_CATEGORY

        # 明显是背景腔，且没有什么真实需求信号
        if bg_score >= 2 and req_score == 0:
            return self.BACKGROUND_CATEGORY

        # 背景信号明显强于需求信号
        if bg_score >= 3 and bg_score >= req_score * 2:
            return self.BACKGROUND_CATEGORY

        # 常见背景句式，但没有规则描述
        if self._match_background_pattern(s) and req_score == 0:
            return self.BACKGROUND_CATEGORY

        return None

    def _contains_section_title(self, text: str) -> bool:
        titles = [
            "项目背景",
            "需求背景",
            "业务背景",
            "方案背景",
            "背景说明",
            "建设目标",
            "项目目标",
            "业务价值",
            "预期收益",
            "收益分析",
            "建设意义",
            "现状分析",
            "痛点分析",
        ]
        for t in titles:
            if t.lower() in text:
                return True
        return False

    def _match_background_pattern(self, text: str) -> bool:
        patterns = [
            r"为了[^\n。；]{2,30}(提升|优化|增强|支撑)",
            r"(目标|目的)(是|在于)",
            r"旨在[^\n。；]{2,50}",
            r"有助于[^\n。；]{2,50}",
            r"提升[^\n。；]{1,20}(体验|效率|满意度|能力)",
            r"优化[^\n。；]{1,20}(流程|体验|效率|能力)",
            r"支撑[^\n。；]{1,30}(业务|发展|增长|扩展)",
        ]
        return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)

    # =====================================================
    # 规则分类
    # =====================================================

    def _classify_by_rules(self, text: str) -> Optional[str]:
        s = self._normalize_text(text)
        if not s:
            return None

        hit_scores: Dict[str, int] = {}

        for category, keywords in self.KEYWORD_RULES.items():
            score = self._keyword_score(s, keywords)
            if score > 0:
                hit_scores[category] = score

        if not hit_scores:
            return None

        best = sorted(hit_scores.items(), key=lambda x: -x[1])[0][0]
        return best

    def _keyword_score(self, text: str, keywords: List[str]) -> int:
        score = 0
        for kw in keywords:
            kw_norm = kw.lower().strip()
            if not kw_norm:
                continue

            if kw_norm in text:
                # 关键词越长，权重越高；重复出现适度加分
                count = text.count(kw_norm)
                score += len(kw_norm) * min(count, 3)
        return score

    # =====================================================
    # LLM 分类
    # =====================================================

    def _classify_by_llm(self, text: str) -> Optional[str]:
        prompt = self._build_llm_prompt(text)

        raw = self.llm.call(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            force_json_object=True,
            temperature=0.1,
            max_tokens=120,
            timeout=60,
        )

        json_text = self._extract_json(raw)
        obj = self._safe_json(json_text)

        category = str(obj.get("category") or "").strip()

        if category in self.CATEGORIES:
            return category

        return None

    # =====================================================
    # Prompt
    # =====================================================

    def _build_llm_prompt(self, text: str) -> str:
        categories = " / ".join(self.CATEGORIES)

        return f"""
请判断下面内容属于哪个分类。

分类只能是：
{categories}

判定规则：
1. 如果内容主要描述项目背景、建设目标、业务价值、收益、现状、建设意义、做这件事的原因，
   而不是描述系统具体要做什么、怎么做、什么条件下做、失败时怎么处理，
   则必须分类为：非需求背景
2. 如果内容涉及具体规则、字段、页面行为、接口、状态流转、权限、异常、边界条件，
   则按最贴近的需求问题分类
3. 只能输出一个分类
4. 必须只输出 JSON

输出格式：
{{
  "category": "某一个分类"
}}

待分类内容：
{text}
"""

    # =====================================================
    # JSON 工具
    # =====================================================

    def _extract_json(self, text: str) -> str:
        text = re.sub(r"```json|```", "", text or "").strip()

        start = text.find("{")
        if start < 0:
            return text

        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

        return text

    def _safe_json(self, raw: str) -> Dict[str, Any]:
        if not raw:
            return {}

        try:
            return json.loads(raw)
        except Exception:
            pass

        try:
            raw = raw.replace("\n", " ").replace("\t", " ")
            return json.loads(raw)
        except Exception:
            return {}

    # =====================================================
    # 文本处理
    # =====================================================

    def _build_text(self, title: Optional[str], message: str) -> str:
        title = str(title or "").strip()
        message = str(message or "").strip()

        if title and message:
            return f"{title} {message}"
        return title or message

    def _normalize_text(self, text: str) -> str:
        s = str(text or "").lower()
        s = re.sub(r"\s+", " ", s)
        s = s.replace("：", ":").replace("（", "(").replace("）", ")")
        return s.strip()


category_classifier = CategoryClassifier()