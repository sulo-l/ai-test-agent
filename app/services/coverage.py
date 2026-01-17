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
            coverage_type = case.get("coverage")
            if coverage_type in result:
                result[coverage_type] = True

    return result


# ===============================
# 🔥 新增：Mandatory Coverage 校验
# ===============================

def check_mandatory_coverage(mandatory_items, test_points):
    """
    校验用户指定的 mandatory coverage 是否被覆盖

    :param mandatory_items: list[str]
        RequirementAgent 输出的 mandatory_coverage
    :param test_points: list[dict]
        TestPointAgent 生成的测试点
    :return: dict[str, bool]
        {
          "市价单": True,
          "限价单": False
        }
    """
    result = {}

    for item in mandatory_items:
        covered = False

        for tp in test_points:
            # 方式 1：明确绑定（最可靠）
            if tp.get("source_requirement") == item:
                covered = True
                break

            # 方式 2：兜底文本匹配（防 LLM 偶尔漏字段）
            if item in (tp.get("name") or ""):
                covered = True
                break

        result[item] = covered

    return result


# ===============================
# 🔥 新增：整体完成状态计算
# ===============================

def calc_overall_status(mandatory_coverage_result):
    """
    根据 mandatory coverage 结果计算整体状态

    :return:
        - "Completed"
        - "Partially Covered"
    """
    if not mandatory_coverage_result:
        return "Completed"

    if all(mandatory_coverage_result.values()):
        return "Completed"

    return "Partially Covered"
