#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/14 22:45
# @Author: sulo
from agents.base import BaseAgent

class ReviewAgent(BaseAgent):
    system_prompt = """
你是测试经理。
请评审测试用例是否合格。

输出 JSON：
{
  "approved": true,
  "issues": []
}
"""
