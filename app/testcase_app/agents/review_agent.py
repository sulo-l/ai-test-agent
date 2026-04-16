# app/testcase_app/agents/review_agent.py
# -*- coding: utf-8 -*-
"""
ReviewAgent — LLM 驱动的测试用例评审智能体

职责：
- 模拟 10 年以上资深测试主管对用例进行评审
- 按模块分批调用 LLM，逐项打分（0-100）并给出可操作建议
- 每个模块评审完成后立即通过 on_module_done 回调推给前端
- 同时保留关键规则校验（结构性问题无需 LLM 就能判断）
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.llm.client import LLM
from app.testcase_app.models import (
    AnalysisResult,
    DesignResult,
    ReviewIssue,
    ReviewResult,
    TestCase,
    TestPoint,
    flatten_test_case_modules,
    flatten_test_point_modules,
    group_cases_by_module,
)

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
# 常量
# ────────────────────────────────────────────────────────────────

_REVIEW_BATCH_SIZE = 8          # 每次 LLM 调用评审多少条用例
_SCORE_THRESHOLD_GOOD = 85      # 分数 >= 85 认为质量可接受（提高标准）
_SCORE_THRESHOLD_REWRITE = 60   # 分数 < 60 认为需要重写（提高标准）

# 预期结果中不可接受的空泛表达
_VAGUE_EXPECTED_PATTERNS = [
    "操作成功", "功能正常", "系统正常", "符合预期", "结果正确", "处理正确",
    "处理成功", "正常显示", "展示正确", "无异常", "页面正常", "流程正确",
    "验证通过", "执行成功", "响应正确", "处理完成", "结果正确",
    "按钮响应正确", "输入校验正确", "提交成功", "结果正确", "页面成功加载",
]

# 回调类型
OnModuleDone = Callable[[str, List[ReviewIssue], int, int], Awaitable[None]]

# ────────────────────────────────────────────────────────────────
# System Prompt
# ────────────────────────────────────────────────────────────────

_REVIEW_SYSTEM_PROMPT = """\
你是拥有 10 年以上经验的资深测试主管，负责对测试用例进行质量评审。

评审维度（满分 100 分）：
1. 步骤可执行性（25分）：步骤是否具体，是否描述了真实用户操作（具体页面/按钮/数据值）
2. 预期可验证性（25分）：预期结果是否明确、可量化，是否引用了具体字段/状态/提示文案
3. 场景覆盖合理性（20分）：用例是否真实覆盖了测试点要求的场景
4. 前置条件合理性（15分）：前置条件是否具体可操作
5. 标题专业性（15分）：标题是否清晰体现被测功能和预期结论

评审结论：
- 分数 >= 85：质量可接受（PASS）
- 分数 60-84：需要优化（IMPROVE）
- 分数 < 60：需要重写（REWRITE）

问题上报规则（严格遵守）：
- PASS 用例（分数 >= 85）：issues 必须为空数组 []，不输出任何问题
- IMPROVE/REWRITE 用例：只输出 1 个最关键的问题，severity 必须是"高"或"中"
- 禁止凑问题：没有真正严重问题就输出空 issues []

严重度标准：
- 高：导致用例无法执行或验证结果无意义（如预期全是"操作成功"、步骤无法操作）
- 中：明显影响用例质量但仍可执行
- 不要输出"低"严重度问题

输出纯 JSON，格式：
{
  "reviews": [
    {
      "case_id": "...",
      "score": 85,
      "verdict": "PASS|IMPROVE|REWRITE",
      "issues": [
        {
          "dimension": "步骤可执行性|预期可验证性|场景覆盖合理性|前置条件合理性|标题专业性",
          "severity": "高|中",
          "description": "具体问题描述",
          "suggestion": "具体改进建议（包含示例）"
        }
      ],
      "highlight": "该用例最大的问题一句话总结，PASS 用例写空字符串"
    }
  ]
}
"""


# ────────────────────────────────────────────────────────────────
# 规则校验（不需要 LLM 就能判断的结构性问题）
# ────────────────────────────────────────────────────────────────

def _rule_check_case(case: TestCase) -> List[Dict[str, Any]]:
    """快速规则校验，返回问题列表"""
    issues = []

    if not case.title or len(case.title) < 6:
        issues.append({
            "dimension": "标题专业性",
            "severity": "高",
            "description": f"标题过短或为空（当前：{case.title!r}）",
            "suggestion": "标题格式：【场景类型】主体-操作条件-验证结论，不少于10字",
        })

    if not case.steps or len(case.steps) < 3:
        issues.append({
            "dimension": "步骤可执行性",
            "severity": "高",
            "description": f"步骤数量不足（当前：{len(case.steps or [])} 步，要求 >= 4 步）",
            "suggestion": "需要拆分为至少 4 个原子操作步骤，每步描述具体的页面元素和操作数据",
        })

    if not case.expected_results:
        issues.append({
            "dimension": "预期可验证性",
            "severity": "高",
            "description": "预期结果为空",
            "suggestion": "每个步骤对应一个明确预期，引用具体字段名/状态值/提示文案",
        })
    else:
        vague_count = sum(
            1 for e in case.expected_results
            if any(v in e for v in _VAGUE_EXPECTED_PATTERNS)
        )
        if vague_count >= len(case.expected_results):
            issues.append({
                "dimension": "预期可验证性",
                "severity": "高",
                "description": "所有预期结果均为空泛表达（如'操作成功'、'功能正常'）",
                "suggestion": "预期必须具体：如'显示提示文案xxx，按钮变为灰色不可点击状态'",
            })

    return issues


def _rule_score_case(case: TestCase) -> int:
    """快速规则评分（0-100），用于判断是否需要 LLM 评审"""
    score = 100
    rule_issues = _rule_check_case(case)
    high = sum(1 for i in rule_issues if i["severity"] == "高")
    mid = sum(1 for i in rule_issues if i["severity"] == "中")
    score -= high * 25 + mid * 10
    return max(0, score)


def _is_structurally_bad(case: TestCase) -> bool:
    """结构性垃圾用例（直接判定，不浪费 LLM token）"""
    if not case.title or len(case.title) < 6:
        return True
    if not case.steps or len(case.steps) < 3:
        return True
    if not case.expected_results:
        return True
    all_vague = all(
        any(v in e for v in _VAGUE_EXPECTED_PATTERNS)
        for e in case.expected_results
    )
    return all_vague


# ──────────────────────────────────���─────────────────────────────
# Prompt 构建
# ────────────────────────────────────────────────────────────────

def _build_review_prompt(
    cases: List[TestCase],
    point_map: Dict[str, TestPoint],
    requirement_summary: str,
) -> str:
    cases_text_parts = []
    for case in cases:
        tp = point_map.get(case.point_id or "")
        tp_title = tp.title if tp else ""
        tp_type = tp.point_type if tp else ""

        parts = [
            f"【用例 case_id={case.case_id}】",
            f"  所属测试点：{tp_title}（类型：{tp_type}）",
            f"  标题：{case.title}",
            f"  优先级：{case.priority}",
        ]
        if case.preconditions:
            parts.append(f"  前置条件：{'; '.join(case.preconditions[:3])}")
        if case.steps:
            for i, s in enumerate(case.steps[:8], 1):
                parts.append(f"  步骤{i}：{s}")
        if case.expected_results:
            for i, e in enumerate(case.expected_results[:8], 1):
                parts.append(f"  预期{i}：{e}")
        cases_text_parts.append("\n".join(parts))

    cases_text = "\n\n".join(cases_text_parts)

    return f"""\
请对以下 {len(cases)} 条测试用例进行质量评审。

需求背景（供参考）：
{requirement_summary[:1000] if requirement_summary else "无"}

────────────────────────────────
待评审用例：

{cases_text}
───────��────────────────────────

请严格按评审标准打分并给出具体可操作的改进建议。
输出纯 JSON，不要输出 markdown，不要任何解释。
"""


# ────────────────────────────────────────────────────────────────
# 解析 LLM 评审结果
# ────────────────────────────────────────────────────────────────

def _parse_review_result(raw: str, cases: List[TestCase]) -> Dict[str, Dict]:
    """返回 case_id -> review_item 的映射"""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < 0:
        return {}

    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        fixed = re.sub(r",\s*([}\]])", r"\1", raw[start:end + 1])
        try:
            data = json.loads(fixed)
        except Exception:
            return {}

    reviews = data.get("reviews", [])
    if not isinstance(reviews, list):
        return {}

    result = {}
    for item in reviews:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("case_id") or "")
        if cid:
            result[cid] = item

    return result


_DIMENSION_TO_ISSUE_TYPE: Dict[str, str] = {
    "步骤可执行性": "步骤不清",
    "预期可验证性": "预期空泛",
    "场景覆盖合理性": "覆盖缺失",
    "前置条件合理性": "前置条件不规范",
    "标题专业性": "标题不规范",
}

_VALID_ISSUE_TYPES = {
    "覆盖缺失", "重复用例", "步骤不清", "预期空泛", "与需求不符",
    "字段缺失", "结构错误", "脏内容", "优先级不合理", "标题不规范", "前置条件不规范",
}


def _map_issue_type(raw: str) -> str:
    """将 LLM 返回的 dimension 映射到合法的 ReviewIssueType，无法识别时降级为"结构错误"。"""
    if raw in _VALID_ISSUE_TYPES:
        return raw
    return _DIMENSION_TO_ISSUE_TYPE.get(raw, "结构错误")


def _build_review_issue(
    case: TestCase,
    issue_data: Dict,
    issue_seq: int,
) -> ReviewIssue:
    return ReviewIssue(
        issue_id=f"RV_{case.case_id}_{issue_seq:03d}",
        issue_type=_map_issue_type(str(issue_data.get("dimension") or "")),
        severity=str(issue_data.get("severity") or "中"),
        module=case.module or "",
        case_id=case.case_id or "",
        point_id=case.point_id or "",
        title=str(issue_data.get("description") or "")[:80],
        description=str(issue_data.get("description") or ""),
        suggestion=str(issue_data.get("suggestion") or ""),
    )


# ────────────────────────────────────────────────────────────────
# ReviewAgent
# ────────────────────────────────────────────────────────────────

class ReviewAgent:
    """
    LLM 驱动的测试用例评审智能体。

    每个模块评审完成立即回调：
        async def cb(module: str, issues: List[ReviewIssue], module_idx: int, total_modules: int)
    """

    def __init__(
        self,
        llm: Optional[LLM] = None,
        timeout: int = 180,
        batch_size: int = _REVIEW_BATCH_SIZE,
        max_concurrency: int = 6,
    ):
        self.llm = llm or LLM()
        self.timeout = timeout
        self.batch_size = batch_size
        # 限制并发 LLM 调用数，防止线程池排队超时
        self._sem = asyncio.Semaphore(max_concurrency)

    async def run(
        self,
        *,
        analysis_result: AnalysisResult,
        design_result: DesignResult,
        requirement_summary: str = "",
        on_module_done: Optional[OnModuleDone] = None,
    ) -> ReviewResult:
        points = flatten_test_point_modules(analysis_result.modules)
        cases = flatten_test_case_modules(design_result.modules)

        if not cases:
            return ReviewResult(
                summary="未生成有效测试用例",
                decision="驳回",
                issues=[],
                coverage_gaps=["未生成测试用例"],
                duplicated_case_ids=[],
                invalid_case_ids=[],
            )

        point_map: Dict[str, TestPoint] = {
            tp.point_id: tp for tp in points if tp.point_id
        }

        # 按模块分组
        module_cases: Dict[str, List[TestCase]] = {}
        for c in cases:
            m = c.module or "未知模块"
            module_cases.setdefault(m, []).append(c)

        all_issues: List[ReviewIssue] = []
        invalid_case_ids: List[str] = []
        all_scores: Dict[str, int] = {}
        modules_list = list(module_cases.items())
        total_modules = len(modules_list)
        _lock = asyncio.Lock()

        async def _review_and_notify(module_name, mod_cases, mod_idx):
            issues = await self._review_module(
                module_name=module_name,
                cases=mod_cases,
                point_map=point_map,
                requirement_summary=requirement_summary,
                scores=all_scores,
                invalid_case_ids=invalid_case_ids,
            )
            async with _lock:
                all_issues.extend(issues)
            # 每个模块完成立即回调，不等其他模块
            if on_module_done:
                try:
                    await on_module_done(module_name, issues, mod_idx, total_modules)
                except Exception as e:
                    logger.warning("[ReviewAgent] on_module_done 回调异常: %s", e)
            return issues

        module_tasks = [
            _review_and_notify(module_name, mod_cases, mod_idx)
            for mod_idx, (module_name, mod_cases) in enumerate(modules_list)
        ]
        module_results = await asyncio.gather(*module_tasks, return_exceptions=True)

        for mod_idx, (module_name, _) in enumerate(modules_list):
            result = module_results[mod_idx]
            if isinstance(result, Exception):
                logger.warning("[ReviewAgent] module=%s gather 异常: %s", module_name, result)

        # 决策
        total = len(cases)
        bad_count = len(invalid_case_ids)
        bad_ratio = bad_count / total if total else 1

        if bad_ratio > 0.5:
            decision = "驳回"
            summary = f"大量用例质量不合格（{bad_count}/{total}），建议重新生成"
        elif len(all_issues) > total * 0.3:
            decision = "需优化"
            summary = f"发现 {len(all_issues)} 个质量问题，建议优化后使用"
        else:
            decision = "通过"
            summary = f"用例质量可接受，共 {total} 条，发现 {len(all_issues)} 个轻微问题"

        logger.info(
            "[ReviewAgent] 评审完成 | total=%d bad=%d issues=%d decision=%s",
            total, bad_count, len(all_issues), decision,
        )

        return ReviewResult(
            summary=summary,
            decision=decision,
            issues=all_issues,
            coverage_gaps=[],
            duplicated_case_ids=[],
            invalid_case_ids=invalid_case_ids,
            score_summary=all_scores,
        )

    async def _review_module(
        self,
        *,
        module_name: str,
        cases: List[TestCase],
        point_map: Dict[str, TestPoint],
        requirement_summary: str,
        scores: Dict[str, int],
        invalid_case_ids: List[str],
    ) -> List[ReviewIssue]:
        """评审一个模块的所有用例"""
        all_issues: List[ReviewIssue] = []

        # ★ 新增:重复用例检测
        duplicate_pairs = _detect_duplicate_cases(cases)
        for case_id, similar_case_id, similarity in duplicate_pairs:
            all_issues.append(ReviewIssue(
                issue_id=f"RV_DUP_{case_id}",
                issue_type="重复用例",
                severity="高",
                module=module_name,
                case_id=case_id,
                point_id="",
                title=f"与用例 {similar_case_id} 重复",
                description=f"该用例与 {similar_case_id} 的相似度达到 {similarity:.1%}，属于重复用例",
                suggestion=f"建议删除或与 {similar_case_id} 合并",
            ))
            invalid_case_ids.append(case_id)
            scores[case_id] = 0

        # 先做规则校验，结构性问题直接标记
        structurally_bad = [c for c in cases if _is_structurally_bad(c)]
        structurally_ok = [c for c in cases if not _is_structurally_bad(c)]

        for c in structurally_bad:
            invalid_case_ids.append(c.case_id or "")
            scores[c.case_id or ""] = 0
            rule_issues = _rule_check_case(c)
            if rule_issues:  # 每条用例最多 1 个问题
                all_issues.append(_build_review_issue(c, rule_issues[0], 0))

        # 对结构 OK 的用例批量 LLM 评审（并发执行所有批次）
        batches = [
            structurally_ok[i:i + self.batch_size]
            for i in range(0, len(structurally_ok), self.batch_size)
        ]

        batch_tasks = [
            self._llm_review_batch(
                cases=batch,
                point_map=point_map,
                requirement_summary=requirement_summary,
                scores=scores,
                invalid_case_ids=invalid_case_ids,
            )
            for batch in batches
        ]
        batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

        for batch_idx, batch_issues in enumerate(batch_results):
            if isinstance(batch_issues, Exception):
                logger.warning("[ReviewAgent] batch=%d gather 异常: %s，使用规则 fallback", batch_idx, batch_issues)
                batch_issues = self._rule_fallback_review(batches[batch_idx], scores, invalid_case_ids)
            all_issues.extend(batch_issues)

        return all_issues

    async def _llm_review_batch(
        self,
        *,
        cases: List[TestCase],
        point_map: Dict[str, TestPoint],
        requirement_summary: str,
        scores: Dict[str, int],
        invalid_case_ids: List[str],
    ) -> List[ReviewIssue]:
        if not cases:
            return []

        prompt = _build_review_prompt(cases, point_map, requirement_summary)
        trace_id = uuid.uuid4().hex[:8]

        async with self._sem:
            try:
                raw = await asyncio.to_thread(
                    self.llm.call,
                    prompt,
                    self.timeout,
                    _REVIEW_SYSTEM_PROMPT,
                    False,
                    None,
                    4096,
                    None,
                    "review",
                    trace_id,
                )
            except asyncio.CancelledError:
                logger.warning("[ReviewAgent] LLM 调用被取消（CancelledError），使用规则 fallback")
                return self._rule_fallback_review(cases, scores, invalid_case_ids)
            except Exception as e:
                logger.warning("[ReviewAgent] LLM 调用失败: %s，使用规则 fallback", e)
                return self._rule_fallback_review(cases, scores, invalid_case_ids)

        if not raw:
            return self._rule_fallback_review(cases, scores, invalid_case_ids)

        review_map = _parse_review_result(raw, cases)
        issues: List[ReviewIssue] = []

        for case in cases:
            cid = case.case_id or ""
            review_item = review_map.get(cid)

            if not review_item:
                # LLM 未返回该用例的评审，用规则补充
                rule_issues = _rule_check_case(case)
                rule_score = _rule_score_case(case)
                scores[cid] = rule_score
                if rule_score < _SCORE_THRESHOLD_REWRITE:
                    invalid_case_ids.append(cid)
                for idx, rd in enumerate(rule_issues):
                    issues.append(_build_review_issue(case, rd, idx))
                continue

            score = int(review_item.get("score") or _rule_score_case(case))
            score = max(0, min(100, score))
            scores[cid] = score

            if score < _SCORE_THRESHOLD_REWRITE:
                invalid_case_ids.append(cid)

            # PASS 用例不输出任何问题（分数 >= 75 的用例噪音无意义）
            if score >= _SCORE_THRESHOLD_GOOD:
                continue

            # 每条用例最多上报 1 个最高严重度问题
            raw_issues = review_item.get("issues") or []
            for idx, issue_data in enumerate(raw_issues):
                if not isinstance(issue_data, dict):
                    continue
                if str(issue_data.get("severity") or "").strip() == "低":
                    continue  # 低严重度跳过
                issues.append(_build_review_issue(case, issue_data, idx))
                break  # 只取第一个（最关键）问题

        return issues

    def _rule_fallback_review(
        self,
        cases: List[TestCase],
        scores: Dict[str, int],
        invalid_case_ids: List[str],
    ) -> List[ReviewIssue]:
        issues = []
        for case in cases:
            cid = case.case_id or ""
            rule_issues = _rule_check_case(case)
            score = _rule_score_case(case)
            scores[cid] = score
            if score < _SCORE_THRESHOLD_REWRITE:
                invalid_case_ids.append(cid)
            if rule_issues:  # 每条用例最多 1 个问题
                issues.append(_build_review_issue(case, rule_issues[0], 0))
        return issues


def _tokenize(text: str) -> set:
    """中文 bi-gram（滑动2字窗口）+ 英文单词 + 数字"""
    import re
    chars = re.findall(r'[\u4e00-\u9fa5]', text)
    bigrams = {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}
    english = set(re.findall(r'[a-zA-Z]+', text.lower()))
    numbers = set(re.findall(r'\d+', text))
    return bigrams | english | numbers


def _normalize_title(title: str) -> str:
    """移除【正常】【异常】等场景前缀，使标题比较更精准"""
    import re
    return re.sub(r'【[^】]*】', '', title).strip()


def _jaccard_similarity(set1: set, set2: set) -> float:
    """Jaccard相似度"""
    if not set1 and not set2:
        return 1.0
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def _sequence_similarity(seq1: List[str], seq2: List[str]) -> float:
    """序列相似度:基于Jaccard的加权平均"""
    if not seq1 and not seq2:
        return 1.0
    if not seq1 or not seq2:
        return 0.0

    # 对每个元素计算相似度,然后取平均
    max_len = max(len(seq1), len(seq2))
    total_sim = 0.0

    for i in range(max_len):
        s1 = seq1[i] if i < len(seq1) else ""
        s2 = seq2[i] if i < len(seq2) else ""
        total_sim += _jaccard_similarity(_tokenize(s1), _tokenize(s2))

    return total_sim / max_len if max_len > 0 else 0.0


def _detect_duplicate_cases(cases: List[TestCase]) -> List[tuple]:
    """
    检测重复用例（使用词袋模型+标题归一化，降低阈值以捕获更多实质重复）
    返回: [(case_id, similar_case_id, similarity), ...]
    """
    from typing import Tuple
    duplicates: List[Tuple[str, str, float]] = []

    for i, case1 in enumerate(cases):
        for case2 in cases[i+1:]:
            title_sim = _jaccard_similarity(
                _tokenize(_normalize_title(case1.title)),
                _tokenize(_normalize_title(case2.title))
            )
            # 词袋模型：合并所有步骤后比较
            steps_sim = _jaccard_similarity(
                _tokenize(' '.join(case1.steps or [])),
                _tokenize(' '.join(case2.steps or []))
            )
            expected_sim = _jaccard_similarity(
                _tokenize(' '.join(case1.expected_results or [])),
                _tokenize(' '.join(case2.expected_results or []))
            )

            # 降低阈值：0.65/0.65 or 0.75/0.75
            if (title_sim > 0.65 and steps_sim > 0.65) or \
               (title_sim > 0.65 and expected_sim > 0.65) or \
               (steps_sim > 0.75 and expected_sim > 0.75):
                overall_sim = (title_sim + steps_sim + expected_sim) / 3
                duplicates.append((
                    case1.case_id or "",
                    case2.case_id or "",
                    overall_sim
                ))

    return duplicates
