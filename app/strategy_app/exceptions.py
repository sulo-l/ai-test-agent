#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/exceptions.py


class StrategyPipelineException(Exception):
    """
    策略 Pipeline 基础异常
    所有策略相关异常建议继承此类，便于统一捕获和处理
    """
    pass


class StrategyPipelineCancelled(StrategyPipelineException):
    """
    策略任务被取消
    用于在 pipeline / agent 中主动中断执行
    """
    pass


class StrategyTimeoutException(StrategyPipelineException):
    """
    策略执行超时
    """
    pass


class StrategyInvalidInputException(StrategyPipelineException):
    """
    输入不合法（需求为空 / 参数错误等）
    """
    pass


class StrategyInternalException(StrategyPipelineException):
    """
    策略内部异常（LLM、解析、规则等）
    """
    pass