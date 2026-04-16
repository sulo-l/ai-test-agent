#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/3/9
# @Author: sulo

import asyncio
import logging
from typing import AsyncGenerator

from app.llm.client import LLM
from .stream_store import stream_store


logger = logging.getLogger(__name__)


# =====================================================
# System Prompt
# =====================================================

SYSTEM_PROMPT = """
你是一名资深系统分析师，请根据用户输入生成详细的需求分析文档。

输出内容需要包括：

1. 项目背景
2. 用户角色
3. 核心需求
4. 功能模块
5. 业务流程
6. 非功能需求
7. 风险与限制

要求：
- 结构清晰
- 使用 Markdown
- 内容专业
"""


# =====================================================
# LLM 生成
# =====================================================

async def generate_analysis(stream_id: str, user_input: str):
    """
    异步生成需求分析，并流式写入 Redis stream_store
    """

    prompt = f"""
{SYSTEM_PROMPT}

用户需求：
{user_input}
"""

    try:

        await stream_store.init_stream(stream_id)

        await stream_store.set_status(
            stream_id,
            status="running",
            stage="LLM_ANALYSIS",
            progress=10,
        )

        full_text = ""

        async for chunk in LLM.stream(prompt):

            if not chunk:
                continue

            full_text += chunk

            await stream_store.append_event(
                stream_id,
                "chunk",
                {"text": chunk},
            )

        result = {
            "analysis_markdown": full_text,
        }

        await stream_store.mark_done(
            stream_id,
            result,
        )

    except Exception as e:

        logger.exception("generate_analysis error")

        await stream_store.mark_error(
            stream_id,
            str(e),
        )


# =====================================================
# 启动任务
# =====================================================

async def start_analysis_task(stream_id: str, user_input: str):
    """
    启动后台任务
    """

    asyncio.create_task(
        generate_analysis(
            stream_id,
            user_input,
        )
    )


# =====================================================
# SSE Stream
# =====================================================

async def stream_analysis(stream_id: str) -> AsyncGenerator[str, None]:
    """
    SSE 流式返回
    """

    index = 0

    try:

        while True:

            events = await stream_store.get_events(stream_id, index)

            if events:

                for e in events:

                    yield f"data: {e}\n\n"

                index += len(events)

            status = await stream_store.get_status(stream_id)

            if status and status.get("status") in ("done", "error"):
                break

            await asyncio.sleep(0.1)

    except asyncio.CancelledError:

        logger.info("SSE client disconnected stream_id=%s", stream_id)

    except Exception:

        logger.exception("stream_analysis error")

        yield "data: {\"type\":\"error\",\"message\":\"stream error\"}\n\n"

    finally:

        yield "data: {\"type\":\"done\"}\n\n"