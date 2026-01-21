#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/14 22:46
# @Author: sulo

# ===============================
# 原有测试覆盖维度（保留）
# ===============================

COVERAGE = ["正常流程", "异常输入", "边界条件", "状态变化", "安全/风控"]


def calc_coverage(tp_id, cases):
    """
    计算单个测试点在不同覆盖维度下的情况
    """
    result = {c: False for c in COVERAGE}

    for case in cases:
        if case.get("test_point_id") == tp_id:
            coverage_type = (
                case.get("coverage")
                or case.get("coverage_type")
            )
            if coverage_type in result:
                result[coverage_type] = True

    return result


# ===============================
# 🔥 Mandatory / Focus Coverage 校验（升级版）
# ===============================

def check_mandatory_coverage(mandatory_items, test_points):
    """
    校验用户指定的 mandatory / focus coverage 是否被覆盖

    :param mandatory_items: list[str] | None
        - RequirementAgent 输出的 mandatory_coverage
        - 或用户 focus_requirements 拆解后的项
    :param test_points: list[dict]
        TestPointAgent 生成的测试点
    :return: dict[str, bool]
        {
          "市价单": True,
          "限价单": False
        }
    """
    result = {}

    if not mandatory_items:
        return result

    for item in mandatory_items:
        covered = False

        for tp in test_points:
            # =========================
            # 方式 1：新体系（最高优先）
            # =========================
            if tp.get("origin") == "mandatory":
                # 如果 source_requirement 明确匹配
                if tp.get("source_requirement") == item:
                    covered = True
                    break

                # 兜底：名称语义匹配
                if item and item in (tp.get("name") or ""):
                    covered = True
                    break

            # =========================
            # 方式 2：旧体系兼容
            # =========================
            if tp.get("source_requirement") == item:
                covered = True
                break

        result[item] = covered

    return result


# ===============================
# 🔥 整体完成状态计算（增强版）
# ===============================

def calc_overall_status(mandatory_coverage_result):
    """
    根据 mandatory / focus coverage 结果计算整体状态

    :return:
        - "Completed"
        - "Partially Covered"
    """
    if not mandatory_coverage_result:
        return "Completed"

    if all(mandatory_coverage_result.values()):
        return "Completed"

    return "Partially Covered"


# ===============================
# ⭐ 可选：Focus 命中统计（不影响现有逻辑）
# ===============================

def calc_focus_hit_cases(cases):
    """
    统计重点（mandatory / focus）测试用例命中数量

    :param cases: list[dict]
        TestCaseAgent / Orchestrator 输出的用例
    :return: dict
        {
          "focus_cases": int,
          "total_cases": int,
          "focus_ratio": float
        }
    """
    if not cases:
        return {
            "focus_cases": 0,
            "total_cases": 0,
            "focus_ratio": 0.0,
        }

    total = len(cases)
    focus_cases = sum(
        1 for c in cases
        if c.get("origin") == "mandatory"
        or c.get("coverage_item")
    )

    return {
        "focus_cases": focus_cases,
        "total_cases": total,
        "focus_ratio": round(focus_cases / total, 3) if total else 0.0,
    }
