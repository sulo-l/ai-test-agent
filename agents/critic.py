#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/13 20:07
# @Author: sulo
class Critic:
    """校验测试用例是否合理"""
    @staticmethod
    def review(test_cases: list[dict]) -> dict:
        # 简单校验：每个 test_case 都有 description
        issues = [tc["id"] for tc in test_cases if not tc.get("description")]
        return {"ok": len(issues) == 0, "issues": issues}
