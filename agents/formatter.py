#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/13 20:08
# @Author: sulo
class OutputFormatter:
    """把测试用例输出成结构化 JSON"""
    @staticmethod
    def format(test_cases: list[dict]) -> dict:
        return {"result": test_cases, "count": len(test_cases)}
