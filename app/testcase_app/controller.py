# app/testcase_app/controller.py
# -*- coding: utf-8 -*-

import os
import time
import json
import uuid
import logging
from typing import Optional, Dict, Any

from fastapi import BackgroundTasks

from app.testcase_app import stream_store
from app.infra.arq_pool import get_arq_pool
from app.infra.redis_client import get_redis
from app.testcase_app.tasks import job_key

logger = logging.getLogger(__name__)

TC_QUEUE_NAME = os.getenv("TC_QUEUE_NAME", "tc_queue")
ARQ_TASK_GENERATE_TESTCASE = os.getenv(
    "TC_ARQ_TASK_GENERATE_TESTCASE",
    "app.testcase_app.tasks.generate_testcase",
)

JOB_TTL_SEC = int(os.getenv("TC_JOB_TTL_SEC", "3600"))


def _ts():
    return int(time.time())


def _ts_ms():
    return int(time.time() * 1000)


async def _emit_stage_event(stream_id: str, stage: str, status: str, title: str, message: str = "", extra=None):
    await stream_store.emit(
        stream_id,
        {
            "type": "stage_event",
            "data": {
                "stage": stage,
                "status": status,
                "title": title,
                "message": message,
                "extra": extra or {},
            },
            "ts": _ts_ms(),
        },
    )


async def _emit_error(stream_id: str, message: str):
    await stream_store.emit(
        stream_id,
        {
            "type": "error",
            "data": message,
            "ts": _ts_ms(),
        },
    )


async def _set_job_status(stream_id: str, status: str, extra: Optional[Dict[str, Any]] = None):
    r = get_redis()

    payload = {
        "stream_id": stream_id,
        "status": status,
        "updated_at": str(_ts()),
    }

    if extra:
        payload.update({k: str(v) for k, v in extra.items()})

    await r.hset(job_key(stream_id), mapping=payload)
    await r.expire(job_key(stream_id), JOB_TTL_SEC)


# =========================================================
# 核心入口（已改）
# =========================================================

async def start_testcase_generation(
    stream_id: str,
    workflow_id: str,
    requirement_id: str,
    extra_requirement: Optional[str],
    background_tasks: BackgroundTasks,
    owner: Optional[str] = None,
) -> Dict[str, Any]:

    if not stream_id:
        raise ValueError("stream_id is empty")

    try:
        # ========================
        # INIT 阶段（前端开始加载）
        # ========================
        await _emit_stage_event(
            stream_id,
            stage="init",
            status="running",
            title="开始生成测试用例",
            message="正在初始化任务",
        )

        pool = await get_arq_pool()
        job_id = uuid.uuid4().hex

        # ========================
        # QUEUED
        # ========================
        await _emit_stage_event(
            stream_id,
            stage="queued",
            status="running",
            title="任务排队中",
            message="正在等待执行",
        )

        await _set_job_status(
            stream_id,
            "QUEUED",
            {
                "workflow_id": workflow_id,
                "requirement_id": requirement_id,
                "job_id": job_id,
            },
        )

        await pool.enqueue_job(
            ARQ_TASK_GENERATE_TESTCASE,
            stream_id,
            workflow_id,
            requirement_id,
            extra_requirement,
            owner,
            _queue_name=TC_QUEUE_NAME,
            _job_id=job_id,
        )

        logger.info("enqueue success | job_id=%s", job_id)

        # ========================
        # RUNNING（关键）
        # ========================
        await _emit_stage_event(
            stream_id,
            stage="running",
            status="running",
            title="生成中",
            message="正在分析需求并生成测试用例...",
        )

        return {
            "success": True,
            "stream_id": stream_id,
            "job_id": job_id,
            "status": "RUNNING",
        }

    except Exception as e:
        logger.exception("enqueue failed")

        await _emit_error(stream_id, str(e))

        await _emit_stage_event(
            stream_id,
            stage="error",
            status="error",
            title="任务失败",
            message=str(e),
        )

        await _set_job_status(stream_id, "ERROR")

        raise