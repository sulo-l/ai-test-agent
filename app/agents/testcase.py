#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/14 22:45
# @Author: sulo
from app.agents.base import BaseAgent

class TestCaseAgent(BaseAgent):
    system_prompt = """
你是测试工程师。
请针对指定测试点和覆盖维度生成测试用例。

输出 JSON：
{
  "case_name": "",
  "module": "",
  "test_point_id": "",
  "coverage": "",
  "precondition": "",
  "steps": "",
  "expected": ""
}
"""
