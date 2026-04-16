# app/testcase_app/agents/refine_agent.py
# -*- coding: utf-8 -*-
"""
RefineAgent — LLM 驱动的测试用例精炼智能体

职责：
- 对 Review 阶段评分 < 75 的用例，调用 LLM 进行重写或优化
- 评分 >= 75 的用例保留原样（不浪费 token）
- 为覆盖缺口补充新用例
- 每批精炼完成后立即通过 on_batch_done 回调推给前端
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.llm.client import LLM
from app.testcase_app.models import (
    AnalysisResult,
    CoverageSummary,
    DesignResult,
    RefineResult,
    ReviewResult,
    TestCase,
    TestPoint,
    build_coverage_summary,
    build_test_case_statistics,
    flatten_test_case_modules,
    flatten_test_point_modules,
    group_cases_by_module,
)

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
# 常量
# ────────────────────────────────────────────────────────────────

_REFINE_BATCH_SIZE = 6
_SCORE_KEEP = 75          # >= 此分保留原用例
_SCORE_IMPROVE = 50       # 50-74 分：LLM 优化
# < 50 分：LLM 完全重写

OnBatchDone = Callable[[List[TestCase], int, int], Awaitable[None]]

# ────────────────────────────────────────────────────────────────
# System Prompt
# ────────────────────────────────────────────────────────────────

_REFINE_SYSTEM_PROMPT = """\
你是拥有 10 年以上经验的资深测试工程师，负责将低质量测试用例重写为高标准用例。

重写/优化标准：
1. 步骤必须是"真人操作"风格：具体页面名、具体按钮名、具体数据值，不写模板套话
2. 预期结果与步骤强绑定：每步对应一条可验证预期，引用具体字段名/状态值/提示文案
3. 前置条件必须可执行：不能写"系统正常"，要写"已登录账号 test@example.com，余额 > 100"
4. 标题格式：【场景类型】主体-操作条件-验证结论，清晰简洁
5. 异常用例：必须给出具体错误码/提示文案/降级行为
6. 边界用例：必须给出具体���界数值和处理结果

绝对禁止的表达（每次出现扣 20 分）：
- 操作成功、功能正常、符合预期、系统正确处理、结果正确
- 执行操作、查看结果、进行操作、进行测试
- 按钮响应正确、输入校验正确、提交成功（无具体说明）
- 系统正常运行、处于正常状态

输出纯 JSON 数组，每个元素是一条完整用例：
[
  {
    "case_id": "原 case_id",
    "用例名称": "【场景类型】...",
    "前置条件": "具体可执行的前置条件；第二个条件",
    "步骤描述": "1. 具体操作步骤\n2. ...",
    "预期结果": "1. 具体可验证的预期\n2. ...",
    "用例等级": "P0|P1|P2|P3",
    "标签": "功能测试|边界测试|异常测试|接口测试|冒烟测试",
    "refine_note": "本次改动简述"
  }
]
"""

_REFINE_FEW_SHOT = """\
【改写示例】

原用例（差）：
  标题：验证用户提交功能
  步骤：1. 进入页面 2. 执行操作 3. 查看结果
  预期：1. 页面正常 2. 操作成功 3. 结果正确

改写后（好）：
  标题：【正常】用户提交有效金额订单-余额扣减且订单状态变更为待确认
  步骤：
    1. 以账号 buyer@test.com（余额 500 元）登录，进入「我的订单」→ 点击「创建订单」
    2. 商品数量填写 2，单价 100 元，点击「计算总价」，确认总价显示为 200 元
    3. 点击「提交订单」按钮
    4. 等待页面跳转，查看「我的订单」列表和账户余额
  预期：
    1. 登录成功，进入创建订单页面，商品列表正常加载
    2. 总价计算结果为 200 元，显示在页面右上角汇总区域
    3. 提交按钮进入 loading 状态（禁止重复点击），发起创建订单接口请求
    4. 跳转到订单详情页；「我的订单」列表出现新订单，状态为「待确认」；账户余额从 500 变为 300 元
"""


# ────────────────────────────────────────────────────────────────
# Prompt 构建
# ────────────────────────────────────────────────────────────────

def _build_refine_prompt(
    cases_with_context: List[Tuple[TestCase, Optional[TestPoint], str, int]],
    requirement_summary: str,
) -> str:
    """
    cases_with_context: [(case, test_point, action, score), ...]
    action: "rewrite" | "improve"
    """
    parts = []
    for case, tp, action, score in cases_with_context:
        action_label = "完全重写（分数极低）" if action == "rewrite" else "优化改进"
        parts.append(f"""
[case_id={case.case_id}] 操作={action_label} 当前评分={score}
所属测试点：{tp.title if tp else "未知"}（类型：{tp.point_type if tp else "?"}，优先级：{tp.priority if tp else "?"}）
测试点目标：{tp.objective if tp else ""}
测试点前置线索：{"; ".join(tp.preconditions[:3]) if tp and tp.preconditions else ""}
测试点检查项：{"; ".join(tp.check_items[:3]) if tp and tp.check_items else ""}

当前用例：
  标题：{case.title}
  前置条件：{"; ".join(case.preconditions[:3]) if case.preconditions else "无"}
  步骤：
{chr(10).join(f"    {s}" for s in (case.steps or [])[:8])}
  预期：
{chr(10).join(f"    {e}" for e in (case.expected_results or [])[:8])}
""".strip())

    cases_text = "\n\n".join(parts)

    return f"""\
{_REFINE_FEW_SHOT}

────────────────────────────────
需求背景：
{requirement_summary[:800] if requirement_summary else "无"}

────────────────────────────────
待精炼用例（共 {len(cases_with_context)} 条）：

{cases_text}

────────────────────────────────
请按要求 rewrite（完全重写）或 improve（优化改进）每条用例。
保持 case_id 不变。输出纯 JSON 数组，每条用例一个对象。
不要输出 markdown，不要任何解释。
"""


# ────────────────────────────────────────────────────────────────
# 解析 LLM 输出
# ────────────────────────────────────────────────────────────────

def _parse_refined_cases(
    raw: str,
    original_cases: List[TestCase],
) -> Dict[str, TestCase]:
    """返回 case_id -> 精炼后的 TestCase"""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0 or end < 0:
        return {}

    try:
        items = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        fixed = re.sub(r",\s*([}\]])", r"\1", raw[start:end + 1])
        try:
            items = json.loads(fixed)
        except Exception:
            return {}

    if not isinstance(items, list):
        return {}

    orig_map = {c.case_id: c for c in original_cases if c.case_id}
    result: Dict[str, TestCase] = {}

    for item in items:
        if not isinstance(item, dict):
            continue

        cid = str(item.get("case_id") or "")
        orig = orig_map.get(cid)
        if not orig:
            continue

        title = str(item.get("用例名称") or item.get("title") or orig.title or "").strip()
        preconditions = _norm_list(
            item.get("前置条件") or item.get("preconditions")
        ) or orig.preconditions
        steps = _norm_list(item.get("步骤描述") or item.get("steps"))
        expected_results = _norm_list(item.get("预期结果") or item.get("expected_results"))
        priority = str(item.get("用例等级") or item.get("priority") or orig.priority or "P1")
        tag = str(item.get("标签") or item.get("tag") or orig.tag or "功能测试")
        refine_note = str(item.get("refine_note") or "")

        # 质量门禁：如果精炼结果更差，不采用
        if not _is_better_than_original(
            title=title, steps=steps, expected_results=expected_results, orig=orig
        ):
            result[cid] = orig
            continue

        new_case = copy.deepcopy(orig)
        new_case.title = title
        new_case.preconditions = preconditions
        new_case.steps = steps or orig.steps
        new_case.expected_results = expected_results or orig.expected_results
        new_case.priority = priority if priority in ("P0", "P1", "P2", "P3") else orig.priority
        new_case.tag = tag
        new_case.remarks = refine_note[:100] if refine_note else orig.remarks

        if hasattr(new_case, "normalize"):
            new_case = new_case.normalize()

        result[cid] = new_case

    return result


def _norm_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


_VAGUE = [
    "操作成功", "功能正常", "系统正常", "符合预期", "结果正确", "处理正确",
    "按钮响应正确", "输入校验正确", "提交成功", "结果正确", "页面成功加载",
    "执行操作", "查看结果", "进行操作",
]


def _is_better_than_original(
    title: str,
    steps: List[str],
    expected_results: List[str],
    orig: TestCase,
) -> bool:
    """判断精炼结果是否比原用例好（不能变更差）"""
    if not title or len(title) < 6:
        return False
    if not steps or len(steps) < 3:
        return False
    if not expected_results or len(expected_results) < 2:
        return False

    # 预期结果不能全是空泛的
    vague_count = sum(1 for e in expected_results if any(v in e for v in _VAGUE))
    if expected_results and vague_count >= len(expected_results):
        return False

    return True


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


def _global_dedup_with_merge(cases: List[TestCase]) -> List[TestCase]:
    """
    全局去重+智能合并:
    - 使用词袋模型（不依赖步骤顺序）
    - 标准化标题（移除【场景】前缀）
    - 降低阈值（0.65/0.65）以捕获更多换汤不换药的重复
    - 保留质量更高的用例
    """
    from typing import Set
    result: List[TestCase] = []
    merged_ids: Set[str] = set()

    for i, case1 in enumerate(cases):
        if case1.case_id in merged_ids:
            continue

        c1_title = _tokenize(_normalize_title(case1.title))
        c1_steps = _tokenize(' '.join(case1.steps or []))

        similar_cases = []
        for case2 in cases[i+1:]:
            if case2.case_id in merged_ids:
                continue

            c2_title = _tokenize(_normalize_title(case2.title))
            c2_steps = _tokenize(' '.join(case2.steps or []))

            title_sim = _jaccard_similarity(c1_title, c2_title)
            steps_sim = _jaccard_similarity(c1_steps, c2_steps)

            if title_sim > 0.65 and steps_sim > 0.60:
                similar_cases.append((case2, title_sim, steps_sim))

        if not similar_cases:
            result.append(case1)
        else:
            best_case = case1
            best_score = _calculate_case_quality(case1)

            for similar_case, _, _ in similar_cases:
                similar_score = _calculate_case_quality(similar_case)
                if similar_score > best_score:
                    best_case = similar_case
                    best_score = similar_score
                merged_ids.add(similar_case.case_id or "")

            result.append(best_case)

    return result


def _calculate_case_quality(case: TestCase) -> float:
    """
    计算用例质量分数:
    - 步骤数量(最少4步)
    - 预期结果数量(与步骤一一对应)
    - 具体性(是否包含具体页面/控件/数据)
    - 可验证性(预期是否包含具体字段/状态/提示)
    """
    score = 0.0

    # 步骤数量(4-8步为佳)
    steps_count = len(case.steps or [])
    if steps_count >= 4:
        score += min(25, steps_count * 5)

    # 预期结果数量(应与步骤数量匹配)
    expected_count = len(case.expected_results or [])
    if expected_count >= steps_count - 1:
        score += 25

    # 具体性检查
    concrete_keywords = ["页面", "按钮", "输入框", "字段", "弹窗", "列表", "详情"]
    steps_text = " ".join(case.steps or [])
    if any(k in steps_text for k in concrete_keywords):
        score += 20

    # 可验证性检查
    verifiable_keywords = ["HTTP", "返回", "状态码", "显示", "变为", "跳转", "提示"]
    expected_text = " ".join(case.expected_results or [])
    if any(k in expected_text for k in verifiable_keywords):
        score += 20

    # 禁止空泛表达
    vague_keywords = ["操作成功", "功能正常", "符合预期", "结果正确"]
    if any(v in expected_text for v in vague_keywords):
        score -= 20

    return max(0, min(100, score))


# ────────────────────────────────────────────────────────────────
# RefineAgent
# ────────────────────────────────────────────────────────────────

class RefineAgent:
    """
    LLM 驱动的测试用例精炼智能体。

    - 评分 >= 75：保留原样
    - 评分 50-74：LLM 优化
    - 评分 < 50：LLM 完全重写
    - 每批完成立即回调 on_batch_done
    """

    def __init__(
        self,
        llm: Optional[LLM] = None,
        timeout: int = 240,
        batch_size: int = _REFINE_BATCH_SIZE,
    ):
        self.llm = llm or LLM()
        self.timeout = timeout
        self.batch_size = batch_size

    async def run(
        self,
        *,
        analysis_result: AnalysisResult,
        design_result: DesignResult,
        review_result: ReviewResult,
        requirement_summary: str = "",
        on_batch_done: Optional[OnBatchDone] = None,
    ) -> RefineResult:
        points = flatten_test_point_modules(analysis_result.modules)
        cases = flatten_test_case_modules(design_result.modules)

        point_map: Dict[str, TestPoint] = {
            tp.point_id: tp for tp in points if tp.point_id
        }

        # 从 review_result 获取评分
        case_scores: Dict[str, int] = {}
        if hasattr(review_result, "score_summary") and review_result.score_summary:
            case_scores = {k: int(v) for k, v in review_result.score_summary.items() if isinstance(v, (int, float))}

        # 分类
        keep_cases: List[TestCase] = []
        need_llm_cases: List[Tuple[TestCase, str]] = []  # (case, "improve"|"rewrite")

        for case in cases:
            score = case_scores.get(case.case_id or "", 80)
            if score >= _SCORE_KEEP:
                keep_cases.append(case)
            elif score >= _SCORE_IMPROVE:
                need_llm_cases.append((case, "improve"))
            else:
                need_llm_cases.append((case, "rewrite"))

        logger.info(
            "[RefineAgent] 分类完成 | keep=%d improve+rewrite=%d",
            len(keep_cases), len(need_llm_cases),
        )

        # 分批精炼
        batches = [
            need_llm_cases[i:i + self.batch_size]
            for i in range(0, len(need_llm_cases), self.batch_size)
        ]
        total_batches = len(batches)

        refined_map: Dict[str, TestCase] = {}

        for batch_idx, batch in enumerate(batches):
            batch_results = await self._refine_batch(
                batch=batch,
                point_map=point_map,
                requirement_summary=requirement_summary,
                batch_idx=batch_idx,
                total_batches=total_batches,
            )
            refined_map.update(batch_results)

            # 实时回调
            batch_cases = list(batch_results.values())
            if on_batch_done and batch_cases:
                try:
                    await on_batch_done(batch_cases, batch_idx, total_batches)
                except Exception as e:
                    logger.warning("[RefineAgent] on_batch_done 回调异常: %s", e)

        # 组装最终用例（保持原始顺序）
        all_refined: List[TestCase] = []
        for case in cases:
            cid = case.case_id or ""
            if cid in refined_map:
                all_refined.append(refined_map[cid])
            else:
                all_refined.append(case)

        # ★ 新增:全局去重（最后一道防线）
        logger.info("[RefineAgent] 全局去重前: %d 条用例", len(all_refined))
        all_refined = _global_dedup_with_merge(all_refined)
        logger.info("[RefineAgent] 全局去重后: %d 条用例", len(all_refined))

        # 覆盖度统计
        modules = group_cases_by_module(all_refined)
        stats = build_test_case_statistics(modules)
        coverage = build_coverage_summary(points, all_refined)

        total = stats.total_cases
        rewritten = sum(
            1 for c, action in need_llm_cases
            if action == "rewrite" and (c.case_id or "") in refined_map
        )
        improved = len(need_llm_cases) - rewritten

        summary = (
            f"精炼完成：共 {total} 条用例，"
            f"保留 {len(keep_cases)} 条，"
            f"优化 {improved} 条，"
            f"重写 {rewritten} 条"
        )

        logger.info("[RefineAgent] %s", summary)

        return RefineResult(
            summary=summary,
            modules=modules,
            statistics=stats,
            coverage_summary=coverage,
        )

    async def _refine_batch(
        self,
        batch: List[Tuple[TestCase, str]],
        point_map: Dict[str, TestPoint],
        requirement_summary: str,
        batch_idx: int,
        total_batches: int,
    ) -> Dict[str, TestCase]:
        if not batch:
            return {}

        cases_with_context = [
            (case, point_map.get(case.point_id or ""), action, 40 if action == "rewrite" else 60)
            for case, action in batch
        ]

        prompt = _build_refine_prompt(cases_with_context, requirement_summary)
        trace_id = uuid.uuid4().hex[:8]
        start = time.time()

        try:
            raw = await asyncio.to_thread(
                self.llm.call,
                prompt,
                self.timeout,
                _REFINE_SYSTEM_PROMPT,
                False,
                None,
                8192,
                None,
                "refine",
                trace_id,
            )
        except Exception as e:
            logger.warning(
                "[RefineAgent] batch=%d/%d LLM 调用失败: %s，保留原用例",
                batch_idx + 1, total_batches, e,
            )
            return {case.case_id or "": case for case, _ in batch}

        elapsed = round(time.time() - start, 2)

        if not raw:
            logger.warning(
                "[RefineAgent] batch=%d/%d LLM 返回空（%.1fs），保留原用例",
                batch_idx + 1, total_batches, elapsed,
            )
            return {case.case_id or "": case for case, _ in batch}

        orig_cases = [case for case, _ in batch]
        refined_map = _parse_refined_cases(raw, orig_cases)

        # 对 LLM 没有返回的用例，保留原样
        for case in orig_cases:
            cid = case.case_id or ""
            if cid not in refined_map:
                refined_map[cid] = case

        logger.info(
            "[RefineAgent] batch=%d/%d 完成 | 精炼 %d/%d 条 | 耗时 %.1fs",
            batch_idx + 1, total_batches, len(refined_map), len(batch), elapsed,
        )

        return refined_map
