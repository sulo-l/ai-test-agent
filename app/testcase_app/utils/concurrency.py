#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/2/22 16:50
# @Author: sulo
# app/testcase_app/utils/concurrency.py
# -*- coding: utf-8 -*-

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Coroutine, List, Optional, Sequence, Tuple, TypeVar, Union, AsyncGenerator

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


@dataclass
class TaskResult:
    """
    统一承载并发任务产出：
    - item: 任务产生的一条结果（可多条）
    - error: 任务异常（不会抛到上层，交由上层选择处理策略）
    - meta: 附加信息（例如 task_id/chunk_id）
    """
    item: Optional[Any] = None
    error: Optional[BaseException] = None
    meta: Optional[dict] = None


class AsyncPool:
    """
    轻量 async 并发池（基于 Semaphore 限流）。
    适用场景：
    - chunk 并发分析（每个 chunk 会产出多条 item）
    - design/review/refine 并发（每个输入产出 0/1 条 item）
    """

    def __init__(self, concurrency: int):
        self.concurrency = max(1, int(concurrency))
        self._sem = asyncio.Semaphore(self.concurrency)

    async def run(self, coro_fn: Callable[[], Awaitable[None]]) -> None:
        async with self._sem:
            await coro_fn()


async def run_producers_to_queue(
    producers: Sequence[Callable[[asyncio.Queue], Awaitable[None]]],
    *,
    concurrency: int = 4,
    queue_maxsize: int = 500,
    sentinel: Any = None,
) -> AsyncGenerator[Any, None]:
    """
    并发跑一组 producer：
    - 每个 producer 接收 out_q，producer 内部可 out_q.put(item) 多次
    - 框架会在每个 producer 结束后自动塞一个 sentinel（默认 None）
    - 外部用 async for 消费该 generator 即可持续收到 item
    - 发生异常不会炸掉主流程，会记录日志，并仍然发送 sentinel

    用法示例：
    async def producer(out_q):
        async for obj in stream(...):
            await out_q.put(obj)

    async for item in run_producers_to_queue([lambda q: producer(q), ...], concurrency=4):
        if item is None: ... # sentinel
        else: ...
    """
    out_q: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _wrap(prod: Callable[[asyncio.Queue], Awaitable[None]]) -> None:
        async with sem:
            try:
                await prod(out_q)
            except Exception as e:
                logger.error("producer failed: %s", str(e), exc_info=True)
            finally:
                await out_q.put(sentinel)

    tasks = [asyncio.create_task(_wrap(p)) for p in producers]

    alive = len(tasks)
    try:
        while alive > 0:
            item = await out_q.get()
            if item is sentinel:
                alive -= 1
                continue
            yield item
    finally:
        # 避免泄露：收尾取消
        for t in tasks:
            if not t.done():
                t.cancel()
        for t in tasks:
            try:
                await t
            except Exception:
                pass


async def map_concurrent(
    items: Sequence[T],
    worker: Callable[[T], Awaitable[Optional[R]]],
    *,
    concurrency: int = 4,
    return_exceptions: bool = False,
) -> List[Union[R, BaseException]]:
    """
    并发 map：每个 item -> worker(item) -> Optional[R]
    - 返回按完成顺序的结果列表（不是输入顺序）
    - worker 返回 None 的会被过滤掉（不进入结果列表）
    - 默认吞异常（只记录日志），return_exceptions=True 时把异常也返回

    适合：
    - design/review/refine：每个输入最多输出 1 个对象
    """
    sem = asyncio.Semaphore(max(1, concurrency))
    out: List[Union[R, BaseException]] = []

    async def _one(x: T) -> Optional[Union[R, BaseException]]:
        async with sem:
            try:
                r = await worker(x)
                return r
            except Exception as e:
                logger.error("map_concurrent worker failed: %s", str(e), exc_info=True)
                return e if return_exceptions else None

    tasks = [asyncio.create_task(_one(x)) for x in items]
    for fut in asyncio.as_completed(tasks):
        r = await fut
        if r is None:
            continue
        out.append(r)

    # 确保任务都收尾
    for t in tasks:
        if not t.done():
            t.cancel()
        try:
            await t
        except Exception:
            pass

    return out


async def iter_map_concurrent(
    items: Sequence[T],
    worker: Callable[[T], Awaitable[Optional[R]]],
    *,
    concurrency: int = 4,
) -> AsyncGenerator[R, None]:
    """
    并发 map 的流式版本：完成一个 yield 一个（按完成顺序）。
    worker 返回 None 会被过滤掉。
    """
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(x: T) -> Optional[R]:
        async with sem:
            try:
                return await worker(x)
            except Exception as e:
                logger.error("iter_map_concurrent worker failed: %s", str(e), exc_info=True)
                return None

    tasks = [asyncio.create_task(_one(x)) for x in items]
    try:
        for fut in asyncio.as_completed(tasks):
            r = await fut
            if r is not None:
                yield r
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        for t in tasks:
            try:
                await t
            except Exception:
                pass


async def cancel_tasks(tasks: Sequence[asyncio.Task]) -> None:
    """
    统一取消任务并等待结束（避免 warning: Task was destroyed but it is pending）
    """
    for t in tasks:
        if not t.done():
            t.cancel()
    for t in tasks:
        try:
            await t
        except Exception:
            pass