#! /usr/bin/python3
# coding=utf-8
# @Author: sulo
"""
UI Vision → TestPoint Converter (Protocol-Level)

职责：
- 将 UISchema 转换为【标准 TestPoint】
- 不调用 LLM
- 不生成测试用例
- 不做最终风险/优先级裁决

设计目标：
- 尽量从 UI 结构中推导“候选测试点”
- 偏保守：宁可多产出候选点，也不遗漏明显 UI 测试点
"""

from __future__ import annotations

from typing import List, Set, Tuple
import uuid

from app.services.ui_vision.extractor import (
    UISchema,
    UIElement,
)
from app.workflow.models import TestPoint


# =====================================================
# 主入口
# =====================================================

def ui_schema_to_test_points(ui_schema: UISchema) -> List[TestPoint]:
    """
    将 UI Schema 转换为 TestPoint 列表（候选测试点）
    """
    if not ui_schema or not ui_schema.pages:
        return []

    test_points: List[TestPoint] = []
    dedupe_keys: Set[Tuple[str, str, str]] = set()

    for page in ui_schema.pages:
        for element in page.elements:
            points = _element_to_test_points(
                element=element,
                page_no=page.page,
            )

            for point in points:
                title = getattr(point, "title", "") or ""
                category = getattr(point, "category", "") or ""
                source_ref = getattr(point, "source_ref", "") or ""

                dedupe_key = (title.strip(), category.strip(), source_ref.strip())
                if dedupe_key in dedupe_keys:
                    continue

                dedupe_keys.add(dedupe_key)
                test_points.append(point)

    return test_points


# =====================================================
# 单元素 → TestPoint
# =====================================================

def _element_to_test_points(
    *,
    element: UIElement,
    page_no: int,
) -> List[TestPoint]:
    """
    一个 UIElement 可能生成 0~N 个测试点
    """
    points: List[TestPoint] = []

    element_label = _get_element_label(element)
    source_ref = f"UI_PAGE_{page_no}:{element.type}:{element_label}"

    # ---------------------------
    # 1) button
    # ---------------------------
    if element.type == "button":
        points.extend([
            _make_point(
                title=f"按钮展示-{element_label}",
                description=f"验证按钮【{element_label}】在页面中正常展示，位置、文案、样式与设计一致。",
                category="normal",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"按钮交互-{element_label}",
                description=f"验证点击按钮【{element_label}】后的跳转、提交、弹窗或其他交互行为正确。",
                category="normal",
                source_ref=source_ref,
            ),
        ])

        if _looks_like_submit_action(element_label):
            points.append(
                _make_point(
                    title=f"按钮提交结果-{element_label}",
                    description=f"验证按钮【{element_label}】触发提交、确认或保存动作后，成功与失败结果反馈正确。",
                    category="abnormal",
                    source_ref=source_ref,
                )
            )

    # ---------------------------
    # 2) input
    # ---------------------------
    elif element.type == "input":
        points.extend([
            _make_point(
                title=f"输入框展示-{element_label}",
                description=f"验证输入框【{element_label}】在页面中正常展示，标签、占位提示或关联文案正确。",
                category="normal",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"输入校验-{element_label}",
                description=f"验证输入框【{element_label}】的必填、格式、长度、非法字符等校验逻辑正确。",
                category="boundary",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"输入异常提示-{element_label}",
                description=f"验证输入框【{element_label}】在输入非法值、空值或超限值时提示信息正确。",
                category="abnormal",
                source_ref=source_ref,
            ),
        ])

    # ---------------------------
    # 3) select
    # ---------------------------
    elif element.type == "select":
        points.extend([
            _make_point(
                title=f"选择器展示-{element_label}",
                description=f"验证选择器【{element_label}】正常展示，默认值、候选项和当前选中状态正确。",
                category="normal",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"选择器切换-{element_label}",
                description=f"验证选择器【{element_label}】切换选项后页面展示、联动数据、提交值或图表样式更新正确。",
                category="normal",
                source_ref=source_ref,
            ),
        ])

        if _looks_like_style_select(element_label):
            points.append(
                _make_point(
                    title=f"样式切换生效-{element_label}",
                    description=f"验证样式选择组件【{element_label}】切换后样式立即生效，重新进入页面时记忆状态正确。",
                    category="abnormal",
                    source_ref=source_ref,
                )
            )

    # ---------------------------
    # 4) checkbox
    # ---------------------------
    elif element.type == "checkbox":
        points.extend([
            _make_point(
                title=f"勾选框状态-{element_label}",
                description=f"验证勾选框【{element_label}】默认状态、勾选与取消勾选状态切换正确。",
                category="normal",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"勾选联动-{element_label}",
                description=f"验证勾选框【{element_label}】切换后关联字段、按钮可用性或页面逻辑正确。",
                category="abnormal",
                source_ref=source_ref,
            ),
        ])

    # ---------------------------
    # 5) radio
    # ---------------------------
    elif element.type == "radio":
        points.extend([
            _make_point(
                title=f"单选项切换-{element_label}",
                description=f"验证单选项【{element_label}】切换时选中状态、互斥逻辑和默认值正确。",
                category="normal",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"单选联动-{element_label}",
                description=f"验证单选项【{element_label}】切换后相关内容展示、字段启用状态或提交结果正确。",
                category="abnormal",
                source_ref=source_ref,
            ),
        ])

    # ---------------------------
    # 6) table
    # ---------------------------
    elif element.type == "table":
        points.extend([
            _make_point(
                title=f"表格展示-{element_label}",
                description=f"验证表格或列表【{element_label}】的列头、数据内容、布局展示正确。",
                category="normal",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"表格空态异常态-{element_label}",
                description=f"验证表格或列表【{element_label}】在空数据、加载失败或异常情况下页面提示正确。",
                category="abnormal",
                source_ref=source_ref,
            ),
        ])

        if _looks_like_list_table(element_label):
            points.append(
                _make_point(
                    title=f"列表字段正确性-{element_label}",
                    description=f"验证列表或表格【{element_label}】中的关键字段展示、排序、状态值映射或刷新结果正确。",
                    category="normal",
                    source_ref=source_ref,
                )
            )

    # ---------------------------
    # 7) link
    # ---------------------------
    elif element.type == "link":
        points.extend([
            _make_point(
                title=f"链接展示-{element_label}",
                description=f"验证链接【{element_label}】在页面中正常展示且文案正确。",
                category="normal",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"链接跳转-{element_label}",
                description=f"验证点击链接【{element_label}】后的跳转目标、参数携带或打开方式正确。",
                category="normal",
                source_ref=source_ref,
            ),
        ])

    # ---------------------------
    # 8) icon
    # ---------------------------
    elif element.type == "icon":
        points.extend([
            _make_point(
                title=f"图标展示-{element_label}",
                description=f"验证图标【{element_label}】在页面中的展示位置、样式或状态标识正确。",
                category="normal",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"图标状态联动-{element_label}",
                description=f"验证图标【{element_label}】在不同状态下的展示、隐藏或交互联动正确。",
                category="abnormal",
                source_ref=source_ref,
            ),
        ])

    # ---------------------------
    # 9) image
    # ---------------------------
    elif element.type == "image":
        points.extend([
            _make_point(
                title=f"图片展示-{element_label}",
                description=f"验证图片区域【{element_label}】加载正常、展示完整且与页面设计一致。",
                category="normal",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"图片异常处理-{element_label}",
                description=f"验证图片区域【{element_label}】在加载失败、空图或异常资源情况下展示与兜底逻辑正确。",
                category="abnormal",
                source_ref=source_ref,
            ),
        ])

    # ---------------------------
    # 10) chart
    # ---------------------------
    elif element.type == "chart":
        points.extend([
            _make_point(
                title=f"图表展示-{element_label}",
                description=f"验证图表【{element_label}】正常展示，数据绘制、样式渲染和布局正确。",
                category="normal",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"图表数据映射-{element_label}",
                description=f"验证图表【{element_label}】的数据映射、涨跌颜色、特殊边界值或重绘逻辑正确。",
                category="abnormal",
                source_ref=source_ref,
            ),
        ])

        if _looks_like_kline_chart(element_label):
            points.extend([
                _make_point(
                    title=f"K线样式切换-{element_label}",
                    description=f"验证 K 线相关图表【{element_label}】在不同样式切换后展示正确，切换结果立即生效。",
                    category="normal",
                    source_ref=source_ref,
                ),
                _make_point(
                    title=f"K线极值展示-{element_label}",
                    description=f"验证 K 线相关图表【{element_label}】在极值、一字线、首根特殊值等边界场景下展示正确。",
                    category="boundary",
                    source_ref=source_ref,
                ),
            ])

    # ---------------------------
    # 11) tab
    # ---------------------------
    elif element.type == "tab":
        points.extend([
            _make_point(
                title=f"Tab展示-{element_label}",
                description=f"验证 Tab【{element_label}】正常展示，默认选中状态和可点击状态正确。",
                category="normal",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"Tab切换-{element_label}",
                description=f"验证 Tab【{element_label}】切换后页面内容、样式高亮、数据刷新或图表重绘正确。",
                category="normal",
                source_ref=source_ref,
            ),
        ])

    # ---------------------------
    # 12) dialog
    # ---------------------------
    elif element.type == "dialog":
        points.extend([
            _make_point(
                title=f"弹窗展示-{element_label}",
                description=f"验证弹窗【{element_label}】拉起、关闭、遮罩层和文案展示正确。",
                category="normal",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"弹窗交互-{element_label}",
                description=f"验证弹窗【{element_label}】中的确认、取消、关闭、二次进入等交互逻辑正确。",
                category="abnormal",
                source_ref=source_ref,
            ),
        ])

    # ---------------------------
    # 13) sheet
    # ---------------------------
    elif element.type == "sheet":
        points.extend([
            _make_point(
                title=f"底部弹层展示-{element_label}",
                description=f"验证底部弹层【{element_label}】正常拉起，选项内容、当前选中态和关闭逻辑正确。",
                category="normal",
                source_ref=source_ref,
            ),
            _make_point(
                title=f"底部弹层选择生效-{element_label}",
                description=f"验证底部弹层【{element_label}】选择不同选项后页面、图表或设置项立即生效，状态记忆正确。",
                category="abnormal",
                source_ref=source_ref,
            ),
        ])

    # ---------------------------
    # 14) text
    # ---------------------------
    elif element.type == "text":
        points.extend([
            _make_point(
                title=f"文案展示-{_short_label(element_label)}",
                description=f"验证页面文案【{element_label}】与需求、设计稿或业务规则一致。",
                category="normal",
                source_ref=source_ref,
            ),
        ])

        if _looks_like_status_text(element_label):
            points.append(
                _make_point(
                    title=f"状态文案正确性-{_short_label(element_label)}",
                    description=f"验证状态文案【{element_label}】在不同业务状态下展示正确，不出现错文案、漏文案或状态映射错误。",
                    category="abnormal",
                    source_ref=source_ref,
                )
            )

        if _looks_like_hint_text(element_label):
            points.append(
                _make_point(
                    title=f"提示文案一致性-{_short_label(element_label)}",
                    description=f"验证提示或说明文案【{element_label}】与页面规则、字段要求和交互逻辑保持一致。",
                    category="normal",
                    source_ref=source_ref,
                )
            )

        if _looks_like_event_text(element_label):
            points.append(
                _make_point(
                    title=f"埋点文案识别-{_short_label(element_label)}",
                    description=f"验证与事件、曝光、点击相关的文案【{element_label}】对应的埋点触发场景和参数映射正确。",
                    category="abnormal",
                    source_ref=source_ref,
                )
            )

    # ---------------------------
    # 15) unknown
    # ---------------------------
    else:
        if element_label and element_label != "unknown":
            points.append(
                _make_point(
                    title=f"页面元素展示-{_short_label(element_label)}",
                    description=f"验证页面元素【{element_label}】展示正常，位置、文案或基础交互符合设计。",
                    category="normal",
                    source_ref=source_ref,
                )
            )

    return points


# =====================================================
# TestPoint 构造
# =====================================================

def _make_point(
    *,
    title: str,
    description: str,
    category: str,
    source_ref: str,
) -> TestPoint:
    """
    构造标准 TestPoint（UI 来源，隐性测试点）
    """
    return TestPoint(
        id=f"TP_UI_{uuid.uuid4().hex[:8]}",
        title=title,
        category=category,          # normal / abnormal / boundary
        description=description,
        source_ref=source_ref,
        implicit=True,              # UI 推导出的，默认标记为隐性
    )


# =====================================================
# 工具函数
# =====================================================

def _get_element_label(element: UIElement) -> str:
    label = (element.label or element.text or element.type or "unknown").strip()
    return label if label else "unknown"


def _short_label(label: str, max_len: int = 12) -> str:
    label = (label or "").strip()
    if len(label) <= max_len:
        return label
    return label[:max_len]


def _looks_like_submit_action(text: str) -> bool:
    text = text or ""
    keywords = [
        "提交", "确认", "保存", "登录", "注册", "发送",
        "立即", "下一步", "完成", "支付", "申请",
    ]
    return any(k in text for k in keywords)


def _looks_like_status_text(text: str) -> bool:
    text = text or ""
    keywords = [
        "成功", "失败", "处理中", "已完成", "未完成",
        "已开启", "已关闭", "启用", "禁用", "异常",
        "状态", "审核中", "已驳回",
    ]
    return any(k in text for k in keywords)


def _looks_like_hint_text(text: str) -> bool:
    text = text or ""
    keywords = [
        "提示", "说明", "注意", "请输入", "请选择",
        "最多", "最少", "不能为空", "格式", "规则",
    ]
    return any(k in text for k in keywords)


def _looks_like_list_table(text: str) -> bool:
    text = text or ""
    keywords = [
        "列表", "记录", "订单", "流水", "明细",
        "时间", "状态", "金额", "名称", "类型",
    ]
    return any(k in text for k in keywords)


def _looks_like_style_select(text: str) -> bool:
    text = text or ""
    keywords = [
        "样式", "图表样式", "K线样式", "切换样式", "选择样式",
    ]
    return any(k in text for k in keywords)


def _looks_like_kline_chart(text: str) -> bool:
    text = text or ""
    keywords = [
        "K线", "美国线", "折线图", "面积图", "平均K线", "Heikin Ashi", "图表",
    ]
    return any(k.lower() in text.lower() for k in keywords)


def _looks_like_event_text(text: str) -> bool:
    text = text or ""
    keywords = [
        "曝光", "点击", "埋点", "event", "click", "exposure",
    ]
    return any(k.lower() in text.lower() for k in keywords)