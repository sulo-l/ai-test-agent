#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/13 20:07
# @Author: sulo


class Planner:
    """
    把需求拆解成子任务（支持强制覆盖 mandatory_coverage）
    """

    @staticmethod
    def make_plan(requirement):
        """
        :param requirement:
            - str（兼容老逻辑）
            - dict（RequirementAgent 输出）
        :return: list[dict]
        """

        plans = []

        # ===== 1️⃣ 兼容老逻辑（requirement 是字符串）=====
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
            return plans

        # ===== 2️⃣ 新逻辑：RequirementAgent 输出 =====
        modules = requirement.get("modules", [])
        mandatory_coverage = requirement.get("mandatory_coverage", [])

        # —— 2.1 按模块拆解（原有能力）——
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

        # —— 2.2 🔥 强制补充用户指定覆盖点（核心）——
        for item in mandatory_coverage:
            plans.append({
                "type": "mandatory",
                "module": "Mandatory Coverage",
                "coverage_item": item,
                "instruction": f"""
必须生成测试点以覆盖以下用户指定内容：
【{item}】

要求：
- 不允许只写一句话
- 必须包含：正常流程、异常情况、边界条件
"""
            })

        return plans
