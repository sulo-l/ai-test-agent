#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/13 20:07
# @Author: sulo


class Planner:
    """
    把需求拆解成子任务（支持强制覆盖 mandatory_coverage / focus_requirements）
    """

    @staticmethod
    def make_plan(requirement, focus_requirements: str | None = None):
        """
        :param requirement:
            - str（兼容老逻辑）
            - dict（RequirementAgent 输出）
        :param focus_requirements:
            - 用户输入的补充测试重点（字符串）
        :return: list[dict]
        """

        plans = []

        # =====================================================
        # 0️⃣ 预处理：解析 focus_requirements
        # =====================================================
        focus_items = []
        if focus_requirements:
            # 支持：中文逗号 / 换行 / 顿号
            separators = ["\n", "，", ",", "、", ";", "；"]
            temp = focus_requirements
            for sep in separators:
                temp = temp.replace(sep, "\n")

            focus_items = [
                item.strip()
                for item in temp.split("\n")
                if item.strip()
            ]

        # =====================================================
        # 1️⃣ 兼容老逻辑（requirement 是字符串）
        # =====================================================
        if isinstance(requirement, str):
            plans.extend([
                {
                    "type": "general",
                    "instruction": f"分析需求: {requirement}"
                },
                {
                    "type": "general",
                    "instruction": "拆解前端校验"
                },
                {
                    "type": "general",
                    "instruction": "拆解后端校验"
                },
                {
                    "type": "general",
                    "instruction": "生成测试点和边界条件"
                }
            ])

            # 🔥 即使是老逻辑，也强插 mandatory coverage
            for item in focus_items:
                plans.append({
                    "type": "mandatory",
                    "module": "User Focus",
                    "coverage_item": item,
                    "instruction": f"""
必须生成测试点以覆盖以下【用户重点测试要求】：
【{item}】

要求：
- 必须拆分为多个测试点
- 覆盖正常 / 异常 / 边界情况
- 不允许只生成 happy path
"""
                })

            return plans

        # =====================================================
        # 2️⃣ 新逻辑：RequirementAgent 输出
        # =====================================================
        modules = requirement.get("modules", [])
        mandatory_coverage = requirement.get("mandatory_coverage", [])

        # —— 2.1 按模块拆解（原有能力，完全保留）——
        for module in modules:
            module_name = module.get("module", "未命名模块")
            module_reqs = module.get("requirements", [])

            plans.append({
                "type": "module",
                "module": module_name,
                "instruction": f"分析模块：{module_name}"
            })

            for req in module_reqs:
                plans.append({
                    "type": "module_requirement",
                    "module": module_name,
                    "instruction": f"为以下需求生成测试点：{req}"
                })

        # =====================================================
        # 3️⃣ 🔥 合并强制覆盖点（RequirementAgent + 用户输入）
        # =====================================================
        merged_mandatory = []

        # 3.1 来自 RequirementAgent
        for item in mandatory_coverage:
            merged_mandatory.append(item)

        # 3.2 来自用户 focus_requirements（去重）
        for item in focus_items:
            if item not in merged_mandatory:
                merged_mandatory.append(item)

        # =====================================================
        # 4️⃣ 🔥 强制补充覆盖计划（核心）
        # =====================================================
        for item in merged_mandatory:
            plans.append({
                "type": "mandatory",
                "module": "Mandatory Coverage",
                "coverage_item": item,
                "instruction": f"""
必须生成测试点以覆盖以下【强制覆盖内容】：
【{item}】

要求：
- 不允许只写一句话
- 必须包含：正常流程、异常情况、边界条件
- 若涉及网络 / 下单 / 金融计算，必须包含失败与极端场景
"""
            })

        return plans
