# app/analysis_app/agents/analysis_agent.py
# -*- coding: utf-8 -*-

from typing import List

from app.analysis_app.models import RequirementIssue


class RequirementAnalysisAgent:
    """
    需求问题分析 Agent（A 分支核心）

    职责：
    - 找出需求中的【不完整】
    - 找出需求中的【歧义】
    - 找出需求中的【实现 / 测试风险】
    - 给出【改进建议】

    ❌ 不生成测试点
    ❌ 不生成测试用例
    """

    def run(self, requirement_text: str) -> List[RequirementIssue]:
        issues: List[RequirementIssue] = []

        text = requirement_text.strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        # =================================================
        # 1️⃣ 不完整（Missing）
        # =================================================
        if not self._has_any(lines, ["应", "需要", "必须", "支持"]):
            issues.append(
                RequirementIssue(
                    level="missing",
                    message="需求中缺少明确的功能性描述（未出现“应/需要/必须/支持”等关键词）"
                )
            )

        if not self._has_any(lines, ["异常", "失败", "错误", "不允许"]):
            issues.append(
                RequirementIssue(
                    level="missing",
                    message="需求中未描述异常或失败场景的处理方式"
                )
            )

        if not self._has_any(lines, ["最大", "最小", "长度", "范围", "上限", "下限"]):
            issues.append(
                RequirementIssue(
                    level="missing",
                    message="需求中未说明关键字段的边界条件（如最大/最小值、长度范围）"
                )
            )

        # =================================================
        # 2️⃣ 歧义（Ambiguous）
        # =================================================
        for line in lines:
            if self._contains_ambiguous_word(line):
                issues.append(
                    RequirementIssue(
                        level="ambiguous",
                        message=f"需求描述存在歧义词汇，可能导致理解不一致：『{line}』"
                    )
                )

        # =================================================
        # 3️⃣ 风险（Risk）
        # =================================================
        if self._has_any(lines, ["性能", "并发", "高频", "大量"]):
            issues.append(
                RequirementIssue(
                    level="risk",
                    message="需求涉及性能或并发场景，但未给出明确指标，存在实现和测试风险"
                )
            )

        if self._has_any(lines, ["第三方", "外部接口", "依赖"]):
            issues.append(
                RequirementIssue(
                    level="risk",
                    message="需求依赖第三方系统或外部接口，但未说明异常、超时或降级策略"
                )
            )

        # =================================================
        # 4️⃣ 改进建议（Suggestion）
        # =================================================
        if issues:
            issues.append(
                RequirementIssue(
                    level="suggestion",
                    message="建议补充：完整流程说明、异常处理规则、边界条件及非功能性要求，以提高需求质量"
                )
            )

        return issues

    # =====================================================
    # 内部工具方法
    # =====================================================

    @staticmethod
    def _has_any(lines: List[str], keywords: List[str]) -> bool:
        """
        判断文本中是否包含任意关键词
        """
        for line in lines:
            for k in keywords:
                if k in line:
                    return True
        return False

    @staticmethod
    def _contains_ambiguous_word(text: str) -> bool:
        """
        判断是否包含歧义词
        """
        ambiguous_words = [
            "适当",
            "相关",
            "必要时",
            "尽量",
            "合理",
            "视情况",
            "等",
            "一般",
            "可能",
        ]
        return any(word in text for word in ambiguous_words)
