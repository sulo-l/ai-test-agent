# app/testcase_app/agents/design_agent.py
# -*- coding: utf-8 -*-
"""
DesignAgent — LLM 驱动的测试用例设计智能体

核心能力：
- 调用 LLM，基于测试点生成资深测试工程师级别的用例
- 分批并发生成，每批完成立即通过 on_batch_done 回调推给前端
- 内建 fallback：LLM 失败时退化为规则生成（保障可用性）
- 全链路质量过滤：去掉空泛步骤、空泛预期、重复用例
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
from app.settings import MAX_TESTCASES, MAX_CASES_PER_POINT
from app.testcase_app.models import (
    AnalysisResult,
    DesignResult,
    TestCase,
    TestCaseModule,
    TestCaseStatistics,
    TestPoint,
    build_test_case_statistics,
    group_cases_by_module,
)

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
# 常量
# ────────────────────────────────────────────────────────────────

_DEFAULT_BATCH_SIZE = 8      # 每次 LLM 调用处理的测试点数（增加以提升全局视角）
# 使用环境配置的最大用例数（测试环境可设为5，生产环境设为200）
_DEFAULT_MAX_CASES = MAX_TESTCASES
_DEFAULT_TIMEOUT = 180

# 预期结果里禁止出现的空泛短语
_VAGUE_EXPECTED = [
    "操作成功", "功能正常", "系统正常", "符合预期", "结果正确", "处理正确",
    "处理成功", "正常显示", "展示正确", "无异常", "页面正常", "流程正确",
    "验证通过", "执行成功", "响应正确", "处理完成",
]

# 步骤里禁止出现的空泛模式
_VAGUE_STEP_PATTERNS = [
    r"^(进行|执行|进入|操作|查看|验证|确认)(功能|流程|操作|测试)$",
    r"^查看(结果|页面|内容|系统)$",
    r"^(观察|检查)(结果|反馈|系统)$",
]

# 标题里不能有的词
_VAGUE_TITLE_WORDS = [
    "功能正常", "流程正常", "流程验证", "系统正常", "功能验证",
    "页面正常", "操作正常",
]


# ────────────────────────────────────────────────────────────────
# Prompt 构建
# ────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
你是拥有 15 年以上经验的资深测试架构师，主导过多个大型系统的测试体系建设，精通功能测试、边界值分析、等价类划分、场景测试法、异常测试、接口测试、权限测试和数据一致性测试。

你设计的测试用例满足以下专业标准：

【用例名称】
- 格式：【场景类型】主体-操作条件-验证结论
- 场景类型：正常 / 异常 / 边界 / 权限 / 并发 / 数据一致性 / 状态切换 / 容错
- 不超过 60 字，明确体现"在什么条件下做了什么，预期是什么"

【前置条件】
- 必须是可直接执行的操作前提，具体到账号/权限/数据状态
- 禁止写"系统处于正常状态"之类废话
- 示例：已用 admin@test.com 登录；当前列表有 3 条记录；账户余额为 500.00 元

【步骤描述】
- 至少 4 步，每步聚焦单一原子操作
- 必须写明：页面名称 + 控件名称/位置 + 具体操作数据
- 禁止写"输入数据"，要写"在「金额」输入框输入 9999.99"
- 禁止写"点击按钮"，要写"点击列表右上角「+ 新增」按钮"

【预期结果】
- 与步骤一一对应，每步有一个预期
- 必须引用具体：字段名、状态值、提示文案、HTTP 响应码、数量变化
- 禁止：操作成功、功能正常、符合预期、系统正确处理、结果正确
- 正确示例：「金额」字段显示 9,999.99，「保存」按钮变为不可点击状态直到请求返回
- 异常预期：接口返回 HTTP 400，响应体 code=AMOUNT_EXCEED_LIMIT，页面弹出"金额不能超过 9999 元"

【覆盖全面性】
- 正常场景：标准流程 + 典型数据
- 异常场景：非法输入 + 权限不足 + 网络/服务异常 + 重复提交
- 边界场景：最小值、最大值、恰好临界值、超过临界值
- 状态切换：前一状态 -> 操作 -> 后一状态，以及不可逆状态
- 数据一致性：操作后多处数据（列表/详情/统计/消息）是否同步
- 并发安全（关键功能）：重复点击/双开 Tab 是否幂等

【用例去重原则】★ 核心要求
- 每个测试点的用例必须有明确的差异化场景，禁止生成语义相同的重复用例
- 同一模块内的用例，标题、步骤、预期结果必须体现明显差异
- 正常/异常/边界场景必须分别独立设计，不能仅改标题而步骤相同
- 如果两条用例的核心验证点相同，必须合并为一条，在一个用例内覆盖多个检查点
- 禁止生成"换汤不换药"的用例：如仅改数值、仅改字段名但逻辑完全相同

【15年资深测试的专业要求】★ 质量标准
- 用例必须像"资深测试手工编写"的水平，不能有模板化、凑数的迹象
- 每个步骤必须描述真实的人工操作，具体到页面名、按钮名、输入框名、具体数据值
- 预期结果必须可验证、可量化，禁止"操作成功"、"功能正常"、"符合预期"等空话
- 异常用例必须包含具体错误码（如HTTP 400）、错误提示文案（如"金额不能超过9999元"）
- 边界用例必须包含具体边界数值（如输入501，边界值为500）和具体处理结果
- 权限用例必须明确角色（如只读用户readonly@test.com）和拦截提示

【输出格式】
输出纯 JSON 数组，每个元素是一条用例，不加 markdown 代码块，不加任何解释文字：
[
  {
    "用例名称": "【正常】...",
    "前置条件": "已登录系统；账号具有xx权限；xxx",
    "步骤描述": "1. 打开「xxx」页面，确认「yyy」按钮可见\n2. 点击「yyy」按钮\n3. 在「字段名」输入 xxx\n4. 点击「保存」按钮\n5. 查看页面反馈和列表数据",
    "预期结果": "1. 页面加载完成，「yyy」按钮高亮可点击\n2. 弹出「新增xxx」对话框，标题正确\n3. 字段回显输入内容，格式校验通过\n4. 按钮进入 loading 状态，发起 POST /api/xxx 请求\n5. 提示「保存成功」，对话框关闭，列表第一行新增刚创建的记录",
    "用例等级": "P0|P1|P2|P3",
    "标签": "功能测试|边界测试|异常测试|UI测试|接口测试|冒烟测试"
  }
]
"""

# Few-shot 示例（内嵌在 user prompt 中，让 LLM 知道什么是高质量）
_FEW_SHOT_EXAMPLE = """\
【反例：低质量用例（严禁模仿）】
用例名称：验证用户登录功能
步骤描述：1. 进入登录页面 2. 输入账号密码 3. 点击登录
预期结果：1. 页面加载正常 2. 登录成功 3. 功能正常
——问题：步骤无具体页面/控件/数据，预期全是空泛表达

【正例1：正常场景（达到这个水平）】
用例名称：【正常】使用有效账号和正确密码登录-跳转首页且用户信息展示正确
前置条件：账号 test@example.com 已注册且状态正常（未封禁/未锁定）；清除浏览器登录 Cookie；错误计数为 0
步骤描述：
  1. 打开登录页 /login，确认「账号」输入框、「密码」输入框、「登录」按钮均可见可点击
  2. 在「账号」输入框输入 test@example.com
  3. 在「密码」输入框输入 Test@123（输入时内容以 ●●● 掩码显示）
  4. 点击「登录」按钮，等待接口响应
  5. 观察地址栏跳转目标和页面右上角用户信息区域
预期结果：
  1. 登录页渲染完成，三个交互元素均可见、可聚焦；页面 title 为"用户登录"
  2. 「账号」输入框回显 test@example.com，无截断
  3. 「密码」输入框以 ●●● 形式展示，长度与输入字符数一致
  4. 「登录」按钮立即进入 loading/禁用状态，发起 POST /api/auth/login；请求体含 email+password 字段
  5. 接口返回 HTTP 200；浏览器跳转到 /dashboard；右上角显示用户头像和昵称"测试用户"；未读消息红点未出现（账号无未读）

【正例2：异常场景-连续错误锁定】
用例名称：【异常】密码连续错误 5 次时账号被锁定且正确密码也无法登录
前置条件：账号 test@example.com 当前密码错误次数已累计 4 次；账号状态正常（未提前锁定）
步骤描述：
  1. 打开登录页 /login
  2. 「账号」输入框输入 test@example.com，「密码」输入框输入错误密码 wrong_pass_001
  3. 点击「登录」按钮，等待响应（第 5 次错误）
  4. 查看页面错误提示和「登录」按钮状态
  5. 不刷新页面，再次在「密码」输入框输入正确密码 Test@123，点击「登录」
预期结果：
  1. 登录页正常渲染，无残留提示
  2. 输入被接受（密码掩码显示）
  3. 接口返回 HTTP 403，响应体 code=ACCOUNT_LOCKED；错误次数写入后端达到 5
  4. 页面弹出红色提示"账号已被锁定，请 24 小时后重试或点击「找回密码」"；「登录」按钮变灰且禁用
  5. 接口仍返回 HTTP 403，code=ACCOUNT_LOCKED；页面继续显示锁定提示，不发起成功登录

【正例3：边界场景-字段长度边界】
用例名称：【边界】「备注」字段输入恰好 500 字符时保存成功，输入 501 字符时被拦截
前置条件：已以 editor@test.com 登录；当前位于「工单详情」页，工单状态为"进行中"；「备注」字段为空
步骤描述：
  1. 在「备注」文本域输入恰好 500 个汉字（可使用固定测试文本 boundary_500.txt 的内容）
  2. 查看输入框下方字数统计显示
  3. 点击「保存备注」按钮
  4. 清空「备注」文本域，输入 501 个汉字（在上述 500 字文本后追加一个"字"）
  5. 点击「保存备注」按钮
预期结果：
  1. 文本域接受 500 字输入，无截断
  2. 字数统计显示"500/500"，颜色为默认灰色（未超限）
  3. 接口 POST /api/ticket/{id}/remark 返回 HTTP 200；页面提示"备注已保存"；页面刷新后备注内容完整显示 500 字
  4. 文本域接受第 501 个字符，字数统计立即变为"501/500"，颜色变红
  5. 接口返回 HTTP 422，code=REMARK_TOO_LONG；页面显示"备注不能超过 500 个字符"，数据库中备注未被修改（仍为步骤3保存的 500 字内容）

【正例4：权限场景】
用例名称：【权限】只读角色用户尝试删除记录时被系统拦截且数据不变
前置条件：已用只读账号 readonly@test.com（角色=viewer）登录；「数据列表」页存在至少 1 条数据记录
步骤描述：
  1. 进入「数据列表」页，确认列表数据可见
  2. 查看列表中「删除」按钮的展示状态
  3. 若删除按钮可见，点击第一条记录右侧「删除」按钮
  4. 若弹出确认框，点击「确认删除」
  5. 记录下列表记录总数，刷新页面再次查看
预期结果：
  1. 「数据列表」页加载完成，数据正常展示
  2. 「删除」按钮应不可见或处于禁用（灰色）状态；hover 时 tooltip 提示"您没有删除权限"
  3. 若按钮意外可点击，点击后接口 DELETE /api/records/{id} 应返回 HTTP 403，code=PERMISSION_DENIED
  4. 确认框若出现，提交后接口仍返回 403；数据不被删除
  5. 刷新后列表记录总数不变，该条记录依然存在
"""


def _build_user_prompt(
    points: List[TestPoint],
    requirement_summary: str,
    module_context: str,
    existing_cases: List[TestCase] = None,
) -> str:
    """构建发给 LLM 的 user prompt"""

    # 测试点列表
    points_text_parts: List[str] = []
    for i, tp in enumerate(points, 1):
        parts = [
            f"【测试点 {i}】",
            f"  ID: {tp.point_id}",
            f"  模块: {tp.module}",
            f"  类型: {tp.point_type}（normal=正常流程 / exception=异常 / boundary=边界）",
            f"  标题: {tp.title}",
            f"  目标: {tp.objective}",
        ]
        if tp.preconditions:
            parts.append(f"  前置条件线索: {'; '.join(tp.preconditions[:3])}")
        if tp.inputs:
            parts.append(f"  输入/操作: {'; '.join(tp.inputs[:4])}")
        if tp.check_items:
            parts.append(f"  检查项: {'; '.join(tp.check_items[:4])}")
        if tp.expected_direction:
            parts.append(f"  预期方向: {'; '.join(tp.expected_direction[:3])}")
        if tp.source_requirement_refs:
            parts.append(f"  需求依据: {'; '.join(tp.source_requirement_refs[:2])}")
        parts.append(f"  优先级: {tp.priority}")
        points_text_parts.append("\n".join(parts))

    points_text = "\n\n".join(points_text_parts)

    # 每个测试点应该生成几条用例
    count_guide_parts: List[str] = []
    for tp in points:
        n = _expected_case_count(tp)
        variants = _decide_variants(tp, n)
        count_guide_parts.append(
            f"  {tp.point_id} ({tp.title[:20]}...): 生成 {n} 条，场景变体={variants}"
        )
    count_guide = "\n".join(count_guide_parts)

    # 新增:已生成用例摘要（包含标题+核心步骤，让LLM真正理解已覆盖了什么）
    existing_summary = ""
    if existing_cases:
        existing_summary = "\n\n【已生成用例摘要（避免重复）】\n"
        # 只显示最近20条，但同时展示步骤关键词，帮助LLM判断覆盖范围
        recent_cases = existing_cases[-20:] if len(existing_cases) > 20 else existing_cases
        for case in recent_cases:
            # 提取步骤的核心动作词（第一步和最后一步最能体现场景差异）
            step_hint = ""
            if case.steps:
                first = case.steps[0][:35] if case.steps else ""
                step_hint = f" → {first}..."
            existing_summary += f"- {case.title}{step_hint}\n"
        existing_summary += "\n⚠️ 禁止生成与上述用例语义相同或高度相似的用例！注意看步骤提示，不只是标题。\n"

    return f"""\
{_FEW_SHOT_EXAMPLE}

────────────────────────────────────────
需求背景（供参考，不要脑补需求之外的内容）：
{requirement_summary[:1500] if requirement_summary else "无"}

当前模块上下文：{module_context}
────────────────────────────────────────

待设计的测试点列表：
{points_text}

────────────────────────────────────────
生成数量要求（严格遵守）：
{count_guide}

{existing_summary}

【质量强制要求】
1. 步骤描述：最少 4 步，每步必须包含：具体页面/弹窗名称 + 具体控件名称 + 具体操作数据
   - ✗ 错误："输入用户名和密码"
   - ✓ 正确："在「用户名」输入框输入 admin@test.com，在「密码」输入框输入 Admin@2024"
2. 预期结果：与步骤一一对应，引用具体字段名/状态值/提示文案/HTTP 响应码
   - ✗ 错误："操作成功"、"功能正常"、"符合预期"
   - ✓ 正确："接口返回 HTTP 200，响应体含 token 字段；页面跳转到 /dashboard"
3. 用例名称：格式【{_variant_label_hint()}】主体-操作条件-验证结论，体现具体场景
4. 异常用例：必须包含具体错误码/提示文案/系统降级行为
5. 边界用例：必须写明具体临界数值（如"输入 101，边界值为 100"）
6. 前置条件：具体到账号名/权限角色/数据状态，禁止写笼统条件
7. ���等价类覆盖：如果测试点涉及"选择/切换/配置"类操作，且需求中有多个可选值，
   必须为不同的枚举值各生成一条用例，不能所有用例都只用同一个示例值
   - ✗ 错误：3条用例全部选择"Open"样式
   - ✓ 正确：一条选"Open"、一条选"High"、一条选"默认"，或覆盖"从A切换到B"的逆向场景
8. ★逆向操作：对于"保存/生效"类用例，必须��一条验证"操作后重新进入页面配置能持久化"
9. ★去重要求：禁止生成与【已生成用例摘要】中语义相同的用例，注意看步骤内容判断是否重复

输出 JSON 数组（数组长度 = 所有测试点生成的用例总数之和）。
不要输出任何 markdown，不要输出解释，直接输出 JSON 数组。
"""


def _variant_label_hint() -> str:
    return "正常|异常|边界|状态切换|权限|数据一致性"


# ────────────────────────────────────────────────────────────────
# 用例数量 & 变体决策（保留原逻辑，供 prompt 指导 LLM）
# ────────────────────────────────────────────────────────────────

def _expected_case_count(tp: TestPoint) -> int:
    """计算每个测试点应生成多少条用例（受MAX_CASES_PER_POINT限制）"""
    text = f"{tp.title} {tp.objective}"

    # 基础逻辑计算期望数量
    expected = 2  # 默认值

    # 异常/边界测试点：正常+异常+边界 各1条 = 3条
    if tp.point_type == "exception":
        expected = 3
    elif tp.point_type == "boundary":
        expected = 3
    # 复杂功能：覆盖正常+异常+边界
    elif any(k in text for k in [
        "状态流转", "多状态", "切换", "权限", "金额", "数量",
        "数据一致", "重复提交", "幂等", "并发", "审批", "流程",
    ]):
        expected = 3
    # P0 核心用例：正常+异常 = 2条
    elif tp.priority == "P0":
        expected = 2
    # 一般功能：正常+异常 = 2条，保证基本覆盖
    else:
        expected = 2

    # ⚠️ 关键：受环境配置限制（测试环境可设为1，避免生成过多用例）
    return min(expected, MAX_CASES_PER_POINT)


def _decide_variants(tp: TestPoint, count: int) -> List[str]:
    text = f"{tp.title} {tp.objective}"

    if count >= 3:
        if tp.point_type == "exception":
            return ["main", "negative", "edge"]
        if tp.point_type == "boundary":
            return ["boundary_min", "boundary_max", "boundary_exceed"]
        if any(k in text for k in ["状态流转", "切换"]):
            return ["main", "state", "negative"]
        if any(k in text for k in ["权限", "角色", "越权"]):
            return ["main", "permission_denied", "negative"]
        return ["main", "negative", "boundary"]

    # count == 2
    if tp.point_type == "exception":
        return ["main", "negative"]
    if tp.point_type == "boundary":
        return ["boundary_min", "boundary_max"]
    if any(k in text for k in ["状态流转", "切换", "刷新"]):
        return ["main", "state"]
    if any(k in text for k in ["权限", "角色", "越权"]):
        return ["main", "permission_denied"]
    return ["main", "negative"]


# ────────────────────────────────────────────────────────────────
# 解析 & 校验
# ────────────────────────────────────────────────────────────────

def _recover_truncated_json_array(text: str) -> List[Dict]:
    """
    从截断的 JSON 数组字符串中恢复已完整的 JSON 对象。
    适用于 LLM 因 max_tokens 截断导致末尾缺少 ']' 的情况。
    """
    results = []
    depth = 0
    in_string = False
    escape_next = False
    obj_start = None

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == "\"":
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    obj = json.loads(text[obj_start:i + 1])
                    if isinstance(obj, dict):
                        results.append(obj)
                except Exception:
                    pass
                obj_start = None

    return results


def _parse_cases_from_llm(
    raw: str,
    points: List[TestPoint],
    requirement_id: str,
) -> List[TestCase]:
    """解析 LLM 返回的 JSON 数组，转换为 TestCase 列表"""

    # 提取 JSON 数组
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    # 找到第一个 [ ... ]
    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0:
        logger.warning("[DesignAgent] LLM 返回内容无 JSON 数组: %s", raw[:200])
        return []

    # end < 0 说明响应被截断（hit max_tokens），尝试从已有内容中恢复完整对象
    if end < 0:
        logger.warning("[DesignAgent] LLM 响应被截断（无结尾 ']'），尝试恢复部分 JSON")
        items = _recover_truncated_json_array(raw[start:])
        if not items:
            return []
    else:
        json_str = raw[start:end + 1]
        try:
            items = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning("[DesignAgent] JSON 解析失败: %s | raw=%s", e, json_str[:300])
            # 尝试修复常见问题：尾部多余逗号
            json_str_fixed = re.sub(r",\s*([}\]])", r"\1", json_str)
            try:
                items = json.loads(json_str_fixed)
            except Exception:
                # 最后尝试恢复截断内容
                items = _recover_truncated_json_array(json_str)
                if not items:
                    return []

    if not isinstance(items, list):
        return []

    # 建立 point_id -> TestPoint 映射，用于 fallback
    point_map: Dict[str, TestPoint] = {tp.point_id: tp for tp in points}

    # 逐条 case 分配 point_id（LLM 按顺序生成，按生成数量对应）
    point_sequence: List[str] = []
    for tp in points:
        n = _expected_case_count(tp)
        for _ in range(n):
            point_sequence.append(tp.point_id)

    cases: List[TestCase] = []
    seq_idx = 0

    for raw_case in items:
        if not isinstance(raw_case, dict):
            continue

        # 分配 point_id
        point_id = ""
        if seq_idx < len(point_sequence):
            point_id = point_sequence[seq_idx]
        seq_idx += 1

        tp = point_map.get(point_id)
        module = tp.module if tp else (points[0].module if points else "")
        source_refs = tp.source_requirement_refs if tp else []

        title = str(raw_case.get("用例名称") or raw_case.get("title") or "").strip()
        preconditions = _normalize_str_list(
            raw_case.get("前置条件") or raw_case.get("preconditions")
        )
        steps = _normalize_str_list(
            raw_case.get("步骤描述") or raw_case.get("steps")
        )
        expected_results = _normalize_str_list(
            raw_case.get("预期结果") or raw_case.get("expected_results")
        )
        priority = str(
            raw_case.get("用例等级") or raw_case.get("priority") or (tp.priority if tp else "P1")
        )
        tag = str(raw_case.get("标签") or raw_case.get("tag") or "功能测试")
        automation = bool(raw_case.get("automation_candidate", False))

        # 质量过滤
        if not _accept_case(title, steps, expected_results):
            logger.debug("[DesignAgent] 过滤低质量用例: %s", title[:40])
            continue

        case_id = _build_case_id(len(cases) + 1)

        case = TestCase(
            case_id=case_id,
            point_id=point_id,
            module=module,
            title=title,
            preconditions=preconditions,
            steps=steps,
            expected_results=expected_results,
            priority=_safe_priority(priority),
            tag=_safe_tag(tag),
            status="未开始",
            remarks="",
            source_requirement_refs=source_refs,
            source_point_title=tp.title if tp else "",
            automation_candidate=automation,
        ).normalize()

        cases.append(case)

    return cases


def _accept_case(title: str, steps: List[str], expected_results: List[str]) -> bool:
    """质量门禁"""
    if not title or len(title) < 6:
        return False
    if len(steps) < 4:
        return False
    if len(expected_results) < 3:
        return False

    # 标题不能含空泛词
    if any(w in title for w in _VAGUE_TITLE_WORDS):
        return False

    # 预期结果：超过一半是空泛表达就拒绝
    vague_count = sum(
        1 for e in expected_results
        if any(v in e for v in _VAGUE_EXPECTED)
    )
    if vague_count > len(expected_results) // 2:
        return False

    # 步骤必须至少有一步提到具体元素（页面/按钮/输入框/字段/弹窗等）
    _CONCRETE_STEP_WORDS = ["页面", "按钮", "输入框", "输入", "点击", "字段", "弹窗", "选择", "勾选", "填写"]
    steps_text = " ".join(steps)
    if not any(w in steps_text for w in _CONCRETE_STEP_WORDS):
        return False

    # 预期结果必须至少有1条包含可验证的具体信息
    _VERIFIABLE_KEYWORDS = [
        "HTTP", "接口", "返回", "提示", "显示", "跳转", "状态", "变为",
        "字段", "文案", "code=", "响应", "弹出", "变更", "跳到", "导航",
        "列表", "刷新", "更新", "消失", "出现", "400", "200", "403", "500",
    ]
    expected_text = " ".join(expected_results)
    if not any(k in expected_text for k in _VERIFIABLE_KEYWORDS):
        return False

    return True


def _normalize_str_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        items: List[str] = []
        for v in value:
            s = str(v).strip()
            if s:
                items.append(s)
        return items
    if isinstance(value, str):
        # LLM 常把多步骤写成 "1. ...\n2. ...\n3. ..." 的单字符串，按换行拆分
        lines = [line.strip() for line in value.strip().splitlines() if line.strip()]
        return lines if lines else []
    return []


def _safe_priority(p: str) -> str:
    return p if p in ("P0", "P1", "P2", "P3") else "P1"


def _safe_tag(t: str) -> str:
    valid = {"功能测试", "边界测试", "异常测试", "UI测试", "接口测试", "冒烟测试"}
    return t if t in valid else "功能测试"


def _build_case_id(seq: int) -> str:
    return f"TC_{seq:04d}"


# ────────────────────────────────────────────────────────────────
# Fallback：规则生成（LLM 失败时保障可用性）
# ────────────────────────────────────────────────────────────────

def _fallback_generate_cases(
    points: List[TestPoint],
    requirement_id: str,
) -> List[TestCase]:
    """当 LLM 调用失败时，使用规则生成保底用例"""
    result: List[TestCase] = []
    for tp in points:
        module = tp.module or "业务模块"
        count = _expected_case_count(tp)
        variants = _decide_variants(tp, count)

        for idx, variant in enumerate(variants, 1):
            steps = _rule_steps(tp, module=module, variant=variant)
            expected = _rule_expected(steps, tp=tp, variant=variant)
            title = _rule_title(tp, module=module, variant=variant)

            case_id = _build_case_id(len(result) + 1)
            case = TestCase(
                case_id=case_id,
                point_id=tp.point_id,
                module=module,
                title=title,
                preconditions=_rule_preconditions(tp, module=module),
                steps=steps,
                expected_results=expected,
                priority=tp.priority or "P1",
                tag=_rule_tag(tp),
                status="未开始",
                remarks="[fallback]",
                source_requirement_refs=tp.source_requirement_refs,
                source_point_title=tp.title,
                automation_candidate=False,
            ).normalize()
            result.append(case)

    return result


def _rule_tag(tp: TestPoint) -> str:
    if tp.point_type == "boundary":
        return "边界测试"
    if tp.point_type == "exception":
        return "异常测试"
    if tp.priority == "P0":
        return "冒烟测试"
    return "功能测试"


def _rule_title(tp: TestPoint, *, module: str, variant: str) -> str:
    base = tp.title or module
    base = re.sub(r"(验证|校验|检查|测试|功能正常|流程正常)", "", base).strip()

    label_map = {
        "main": "正常",
        "negative": "异常",
        "boundary_min": "边界-最小值",
        "boundary_max": "边界-最大值",
        "state": "状态切换",
        "permission_denied": "权限拦截",
    }
    label = label_map.get(variant, "正常")
    return f"【{label}】{base}"[:80]


def _rule_preconditions(tp: TestPoint, *, module: str) -> List[str]:
    pre = list(tp.preconditions) if tp.preconditions else []
    if not pre:
        pre = [f"用户已登录系统", f"已进入【{module}】相关页面"]
    return pre[:4]


def _rule_steps(tp: TestPoint, *, module: str, variant: str) -> List[str]:
    target = tp.title or tp.objective or module

    if variant == "negative":
        return [
            f"1. 打开【{module}】相关页面",
            "2. 构造不满足业务规则的输入数据（如超长字符、特殊符号、空值）",
            f"3. 执行【{target}】操作",
            "4. 提交表单或触发相关动作",
            "5. 查看系统提示信息和页面状态",
        ]
    if variant in ("boundary_min", "boundary_max"):
        boundary_hint = "最小边界值" if variant == "boundary_min" else "最大边界值"
        return [
            f"1. 打开【{module}】相关页面",
            f"2. 在关键字段输入{boundary_hint}（参考需求规格）",
            f"3. 执行【{target}】操作",
            "4. 提交或确认",
            "5. 查看系统处理结果和提示",
        ]
    if variant == "state":
        return [
            f"1. 打开【{module}】相关页面",
            "2. 确认当前状态为初始状态",
            "3. 执行状态变更操作",
            "4. 刷新页面或重新进入该功能",
            "5. 查看状态是否正确保持",
        ]
    if variant == "permission_denied":
        return [
            "1. 使用无操作权限的账号登录",
            f"2. 尝试访问【{module}】相关功能",
            "3. 执行受限操作",
            "4. 查看系统拦截提示",
        ]
    # main
    return [
        f"1. 打开【{module}】相关页面，确认页面加载完整",
        f"2. 准备满足前置条件的测试数据",
        f"3. 执行【{target}】操作",
        "4. 提交或确认后等待系统响应",
        "5. 查看页面反馈、数据变化和状态结果",
    ]


def _rule_expected(
    steps: List[str],
    *,
    tp: TestPoint,
    variant: str,
) -> List[str]:
    results: List[str] = []
    for step in steps:
        s = step.strip()
        if "打开" in s or "进入" in s:
            results.append("页面加载完成，核心元素可见可交互，无报错")
        elif "输入" in s or "构造" in s or "准备" in s:
            results.append("输入内容被正确接收，校验逻辑触发")
        elif "执行" in s or "点击" in s:
            if variant == "negative":
                results.append("系统拦截非法操作，返回对应错误码和提示文案")
            else:
                results.append("操作被正常受理，系统进入处理状态")
        elif "提交" in s or "确认" in s:
            if variant == "negative":
                results.append("提交被拒绝，页面显示具体错误原因，数据未写入")
            elif variant in ("boundary_min", "boundary_max"):
                results.append("边界值被正确处理，结果与规格书一致")
            else:
                results.append("提交成功，数据持久化正确，页面更新反映最新状态")
        elif "刷新" in s or "重新进入" in s:
            results.append("刷新后状态正确保持，无数据回退或丢失")
        elif "查看" in s or "观察" in s:
            if variant == "negative":
                results.append("错误提示明确，文案符合设计规范，不暴露系统内部信息")
            elif variant in ("boundary_min", "boundary_max"):
                results.append("边界临界值处理结果符合规格，精度和格式正确")
            elif variant == "permission_denied":
                results.append("页面显示「无权限」提示或跳转至 403 页面，不暴露受限数据")
            else:
                results.append("页面展示结果与数据库/后端状态一致，无延迟或缓存问题")
        else:
            results.append("系统响应符合业务规则，状态与数据保持一致")
    return results


# ────────────────────────────────────────────────────────────────
# DesignAgent
# ────────────────────────────────────────────────────────────────

# 回调类型：每批用例生成后调用，供 pipeline 实时推给前端
OnBatchDone = Callable[[List[TestCase], int, int], Awaitable[None]]


class DesignAgent:
    """
    LLM 驱动的测试用例设计智能体。

    pipeline 调用方式：
        result = await agent.run(analysis_result=..., requirement_id=..., ...)

    支持流式回调（每批 LLM 调用完成立即推给前端）：
        result = await agent.run(
            analysis_result=...,
            on_batch_done=async_callback,
        )
    """

    def __init__(
        self,
        llm: LLM,
        max_cases: int = _DEFAULT_MAX_CASES,
        chunk_size: int = _DEFAULT_BATCH_SIZE,
        timeout: int = _DEFAULT_TIMEOUT,
        max_concurrency: int = 8,
    ):
        self.llm = llm
        self.max_cases = max_cases
        self.batch_size = max(1, chunk_size)
        self.timeout = timeout
        # 限制并发 LLM 调用数，防止线程池排队超时
        self._sem = asyncio.Semaphore(max_concurrency)

    # ──────────────────────────────────────
    # 公共入口（供 pipeline 调用）
    # ──────────────────────────────────────

    async def run(
        self,
        analysis_result: AnalysisResult,
        requirement_id: str = "",
        requirement_summary: str = "",
        on_batch_done: Optional[OnBatchDone] = None,
    ) -> DesignResult:
        """
        主入口：将 AnalysisResult 中的所有 TestPoint 展开为 TestCase，
        返回 DesignResult。

        on_batch_done：每批 case 生成后的回调，签名：
            async def cb(cases: List[TestCase], batch_idx: int, total_batches: int) -> None
        """
        req_id = requirement_id or "REQ"
        all_points = analysis_result.all_points()

        if not all_points:
            return DesignResult(
                summary="无测试点，跳过设计阶段",
                modules=[],
                statistics=TestCaseStatistics(),
            )

        # 分批：按 batch_size 切分 points
        batches = _split_into_batches(all_points, self.batch_size)
        total_batches = len(batches)

        all_cases: List[TestCase] = []
        # 用锁保护 all_cases 的并发追加和 max_cases 截断
        _lock = asyncio.Lock()
        _stopped = [False]

        async def _run_batch_and_notify(batch_points, batch_idx):
            if _stopped[0]:
                return []
            batch_cases = await self._process_batch(
                points=batch_points,
                requirement_id=req_id,
                requirement_summary=requirement_summary,
                batch_idx=batch_idx,
                total_batches=total_batches,
                existing_cases=all_cases,  # 传入已生成的用例
            )
            # 增量去重：与已生成的用例比对
            deduplicated = _incremental_dedup(batch_cases, all_cases)

            async with _lock:
                if _stopped[0]:
                    return []
                all_cases.extend(deduplicated)
                if len(all_cases) >= self.max_cases:
                    _stopped[0] = True
            # 每批完成立即回调，不等其他批次
            if on_batch_done and deduplicated:
                try:
                    await on_batch_done(deduplicated, batch_idx, total_batches)
                except Exception as e:
                    logger.warning("[DesignAgent] on_batch_done 回调异常: %s", e)
            return deduplicated

        # 串行执行：逐批生成，每批都能看到之前生成的用例
        batch_results = []
        for batch_idx, batch_points in enumerate(batches):
            if _stopped[0]:
                break
            try:
                result = await _run_batch_and_notify(batch_points, batch_idx)
                batch_results.append(result)
            except Exception as e:
                logger.warning(
                    "[DesignAgent] batch=%d/%d 执行异常: %s，启用 fallback",
                    batch_idx + 1, total_batches, e,
                )
                fallback = _fallback_generate_cases(batch_points, req_id)
                all_cases.extend(fallback)
                if on_batch_done and fallback:
                    try:
                        await on_batch_done(fallback, batch_idx, total_batches)
                    except Exception as e:
                        logger.warning("[DesignAgent] fallback on_batch_done 异常: %s", e)

        # 去重
        all_cases = _dedup_cases(all_cases)[: self.max_cases]

        # 按模块组织
        modules = group_cases_by_module(all_cases)
        stats = build_test_case_statistics(modules)

        total = stats.total_cases
        module_count = stats.total_modules
        summary = f"共生成 {total} 条测试用例，覆盖 {module_count} 个模块"

        logger.info(
            "[DesignAgent] 设计完成 | total_cases=%d modules=%d batches=%d",
            total, module_count, total_batches,
        )

        return DesignResult(
            summary=summary,
            modules=modules,
            statistics=stats,
        )

    # ──────────────────────────────────────
    # 批次处理
    # ──────────────────────────────────────

    async def _process_batch(
        self,
        points: List[TestPoint],
        requirement_id: str,
        requirement_summary: str,
        batch_idx: int,
        total_batches: int,
        existing_cases: List[TestCase] = None,
    ) -> List[TestCase]:
        """处理一批测试点：先调 LLM，失败则 fallback 规则生成"""

        module_ctx = _infer_module_context(points)
        prompt = _build_user_prompt(
            points=points,
            requirement_summary=requirement_summary,
            module_context=module_ctx,
            existing_cases=existing_cases or [],
        )

        trace_id = uuid.uuid4().hex[:8]
        start = time.time()

        try:
            async with self._sem:
                raw = await asyncio.to_thread(
                    self.llm.call,
                    prompt,
                    self.timeout,        # timeout
                    _SYSTEM_PROMPT,      # system_prompt
                    False,               # force_json_object
                    None,                # temperature — 由 agent_type 决定
                    12000,               # max_tokens（增加以容纳更丰富的用例内容）
                    None,                # model
                    "design",            # agent_type
                    trace_id,            # trace_id
                )
        except asyncio.CancelledError:
            logger.warning(
                "[DesignAgent] batch=%d/%d LLM 调用被取消（CancelledError），启用 fallback",
                batch_idx + 1, total_batches,
            )
            return _fallback_generate_cases(points, requirement_id)
        except Exception as e:
            logger.warning(
                "[DesignAgent] batch=%d/%d LLM 调用异常: %s，启用 fallback",
                batch_idx + 1, total_batches, e,
            )
            return _fallback_generate_cases(points, requirement_id)

        elapsed = round(time.time() - start, 2)

        if not raw:
            logger.warning(
                "[DesignAgent] batch=%d/%d LLM 返回空内容（%.1fs），启用 fallback",
                batch_idx + 1, total_batches, elapsed,
            )
            return _fallback_generate_cases(points, requirement_id)

        cases = _parse_cases_from_llm(raw, points, requirement_id)

        if not cases:
            logger.warning(
                "[DesignAgent] batch=%d/%d 解析结果为空（%.1fs），启用 fallback",
                batch_idx + 1, total_batches, elapsed,
            )
            return _fallback_generate_cases(points, requirement_id)

        logger.info(
            "[DesignAgent] batch=%d/%d 生成 %d 条用例 | 耗时 %.1fs | 模块=%s",
            batch_idx + 1, total_batches, len(cases), elapsed, module_ctx,
        )
        return cases


# ────────────────────────────────────────────────────────────────
# 工具函数
# ────────────────────────────────────────────────────────────────

def _split_into_batches(points: List[TestPoint], batch_size: int) -> List[List[TestPoint]]:
    """按模块优先分批，相同模块的 points 尽量在一批"""
    # 先按模块分组
    by_module: Dict[str, List[TestPoint]] = {}
    for tp in points:
        m = tp.module or "__none__"
        by_module.setdefault(m, []).append(tp)

    batches: List[List[TestPoint]] = []
    current: List[TestPoint] = []

    for module_points in by_module.values():
        for tp in module_points:
            current.append(tp)
            if len(current) >= batch_size:
                batches.append(current)
                current = []

    if current:
        batches.append(current)

    return batches


def _infer_module_context(points: List[TestPoint]) -> str:
    modules = list({tp.module for tp in points if tp.module})
    return "、".join(modules[:3]) if modules else "未知模块"


def _dedup_cases(cases: List[TestCase]) -> List[TestCase]:
    """
    全局去重（基于词袋模型，不依赖步骤顺序，降低阈值以捕获更多重复）:
    1. 标准化标题后比较（去掉【场景】前缀干扰）
    2. 步骤使用词袋合并比较（不依赖位置顺序）
    3. 预期结果同上
    """
    result: List[TestCase] = []

    for candidate in cases:
        is_duplicate = False
        c_title_tokens = _tokenize(_normalize_title(candidate.title))
        c_steps_bag = _tokenize(' '.join(candidate.steps or []))
        c_expected_bag = _tokenize(' '.join(candidate.expected_results or []))

        for existing in result:
            e_title_tokens = _tokenize(_normalize_title(existing.title))
            e_steps_bag = _tokenize(' '.join(existing.steps or []))
            e_expected_bag = _tokenize(' '.join(existing.expected_results or []))

            title_sim = _jaccard_similarity(c_title_tokens, e_title_tokens)
            steps_sim = _jaccard_similarity(c_steps_bag, e_steps_bag)
            expected_sim = _jaccard_similarity(c_expected_bag, e_expected_bag)

            # 标题相似 + 步骤或预期也相似 → 重复
            # 或者步骤和预期都高度相似（即使标题改了写法）
            if (title_sim > 0.6 and steps_sim > 0.7) or \
               (title_sim > 0.6 and expected_sim > 0.7) or \
               (steps_sim > 0.8 and expected_sim > 0.8):
                logger.debug(
                    "[去重] 用例重复 | "
                    f"标题相似度={title_sim:.2f} "
                    f"步骤相似度={steps_sim:.2f} "
                    f"预期相似度={expected_sim:.2f} | "
                    f"候选={candidate.title[:40]} | "
                    f"已存在={existing.title[:40]}"
                )
                is_duplicate = True
                break

        if not is_duplicate:
            result.append(candidate)

    return result


def _tokenize(text: str) -> set:
    """
    中文 bi-gram（滑动2字窗口）+ 英文单词 + 数字。
    bi-gram 比单字更精准（能区分"登录"/"登出"），比整段匹配更宽容。
    示例: "用户登录正常" → {用户, 户登, 登录, 录正, 正常}
          "用户登录异常" → {用户, 户登, 登录, 录异, 异常}
    两者 Jaccard ≈ 0.56，不会被误判为重复。
    """
    chars = re.findall(r'[\u4e00-\u9fa5]', text)
    bigrams = {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}
    english = set(re.findall(r'[a-zA-Z]+', text.lower()))
    numbers = set(re.findall(r'\d+', text))
    return bigrams | english | numbers


def _normalize_title(title: str) -> str:
    """移除【正常】【异常】等场景前缀，使标题比较更精准"""
    return re.sub(r'【[^】]*】', '', title).strip()


def _steps_bag_similarity(steps1: List[str], steps2: List[str]) -> float:
    """基于词袋模型的步骤相似度（不依赖位置，避免因步骤顺序不同漏判重复）"""
    bag1 = _tokenize(' '.join(steps1))
    bag2 = _tokenize(' '.join(steps2))
    return _jaccard_similarity(bag1, bag2)


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


def _incremental_dedup(
    new_cases: List[TestCase],
    existing_cases: List[TestCase]
) -> List[TestCase]:
    """增量去重:新用例与已有用例比对（使用词袋模型，降低阈值）"""
    result = []
    for candidate in new_cases:
        is_duplicate = False
        c_title_tokens = _tokenize(_normalize_title(candidate.title))
        c_steps_bag = _tokenize(' '.join(candidate.steps or []))
        c_expected_bag = _tokenize(' '.join(candidate.expected_results or []))

        for existing in existing_cases:
            e_title_tokens = _tokenize(_normalize_title(existing.title))
            e_steps_bag = _tokenize(' '.join(existing.steps or []))
            e_expected_bag = _tokenize(' '.join(existing.expected_results or []))

            title_sim = _jaccard_similarity(c_title_tokens, e_title_tokens)
            steps_sim = _jaccard_similarity(c_steps_bag, e_steps_bag)
            expected_sim = _jaccard_similarity(c_expected_bag, e_expected_bag)

            if (title_sim > 0.6 and steps_sim > 0.7) or \
               (title_sim > 0.6 and expected_sim > 0.7) or \
               (steps_sim > 0.8 and expected_sim > 0.8):
                logger.debug(
                    "[增量去重] 用例重复 | "
                    f"标题相似度={title_sim:.2f} "
                    f"步骤相似度={steps_sim:.2f} "
                    f"预期相似度={expected_sim:.2f} | "
                    f"候选={candidate.title[:40]} | "
                    f"已存在={existing.title[:40]}"
                )
                is_duplicate = True
                break

        if not is_duplicate:
            result.append(candidate)
    return result
