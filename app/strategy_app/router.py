#! /usr/bin/python3
# coding=utf-8
# app/strategy_app/router.py

from __future__ import annotations

import inspect
from typing import Optional, Any, Dict

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.strategy_app.controller import (
    start_strategy_run,
    get_strategy_result,
    get_strategy_context,
    get_strategy_status,
    cancel_strategy_run,
)
from app.strategy_app.ws import strategy_sse_manager
from app.strategy_app.stream_store import strategy_stream_store
from app.strategy_app.models import StrategyResult
from app.workflow.state import get_workflow


# =====================================================
# Router
# =====================================================

router = APIRouter(
    prefix="/strategy",
    tags=["strategy"],
)


# =====================================================
# 请求模型
# =====================================================

class StrategyRunRequest(BaseModel):
    workflow_id: str = Field(..., description="工作流 ID")
    requirement_id: Optional[str] = Field(default=None, description="需求 ID")
    force_refresh: bool = Field(default=False, description="是否强制刷新")
    use_analysis_result: bool = Field(default=True, description="是否复用需求分析结果")
    use_testcase_result: bool = Field(default=True, description="是否复用测试用例结果")


class StrategyRunResponse(BaseModel):
    ok: bool = True
    job_id: str
    stream_id: str
    workflow_id: str
    requirement_id: Optional[str]
    status: str
    message: str


class StrategyContextResponse(BaseModel):
    ok: bool = True
    workflow_id: str
    requirement_id: Optional[str]
    has_requirement: bool
    has_analysis_result: bool
    has_testcase_result: bool
    strategy_status: Optional[str]
    strategy_stream_id: Optional[str]
    strategy_job_id: Optional[str]
    strategy_error: Optional[str]
    message: str


class StrategyStreamStatusResponse(BaseModel):
    ok: bool = True
    stream_id: str
    message: str = "stream exists"


class StrategyStatusResponse(BaseModel):
    ok: bool = True
    workflow_id: str
    job_id: Optional[str] = None
    stream_id: Optional[str] = None
    status: str = "idle"
    progress: Optional[int] = None
    result_ready: Optional[bool] = None
    last_stage: Optional[str] = None
    last_stage_status: Optional[str] = None
    last_stage_title: Optional[str] = None
    last_stage_message: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[int] = None
    finished_at: Optional[int] = None
    duration_ms: Optional[int] = None
    result: Optional[Dict[str, Any]] = None


class StrategyCancelRequest(BaseModel):
    workflow_id: str = Field(..., description="工作流 ID")
    job_id: Optional[str] = Field(default=None, description="任务 ID")
    stream_id: Optional[str] = Field(default=None, description="流 ID")


# =====================================================
# 工具
# =====================================================

def _workflow_get(workflow: Any, key: str, default: Any = None) -> Any:
    if workflow is None:
        return default
    if isinstance(workflow, dict):
        return workflow.get(key, default)
    return getattr(workflow, key, default)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _normalize_strategy_result(raw: Any) -> Optional[StrategyResult]:
    if raw is None:
        return None

    if isinstance(raw, StrategyResult):
        return raw

    if isinstance(raw, dict):
        try:
            return StrategyResult(**raw)
        except Exception:
            return None

    model_dump = getattr(raw, "model_dump", None)
    if callable(model_dump):
        try:
            return StrategyResult(**model_dump())
        except Exception:
            return None

    dict_fn = getattr(raw, "dict", None)
    if callable(dict_fn):
        try:
            return StrategyResult(**dict_fn())
        except Exception:
            return None

    return None


async def _stream_exists(stream_id: str) -> bool:
    # 先查 manager
    try:
        exists = strategy_sse_manager.exists(stream_id)
        exists = await _maybe_await(exists)
        if exists:
            return True
    except Exception:
        pass

    # 再查 store
    try:
        data = await strategy_stream_store.get_stream(stream_id)
        if data and data.get("status") != "not_found":
            return True
    except Exception:
        pass

    return False


async def _stream_event_generator(stream_id: str):
    """
    优先走 strategy_sse_manager 实时订阅。
    若 manager 中不存在，则走 stream_store fallback 轮询。
    """
    # 优先 manager
    try:
        exists = strategy_sse_manager.exists(stream_id)
        exists = await _maybe_await(exists)
        if exists:
            stream_iter = strategy_sse_manager.stream(stream_id)
            if inspect.isawaitable(stream_iter):
                stream_iter = await stream_iter

            async for chunk in stream_iter:
                yield chunk
            return
    except Exception:
        pass

    # fallback: stream_store 增量轮询
    last_offset = 0
    while True:
        data = await strategy_stream_store.get_stream(stream_id)
        if not data or data.get("status") == "not_found":
            yield "event: error\ndata: {\"type\":\"error\",\"message\":\"stream not found\"}\n\n"
            return

        events = await strategy_stream_store.get_stream_events(stream_id, last_offset=last_offset)
        if events:
            for event in events:
                import json
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            last_offset += len(events)

        status = str(data.get("status") or "").lower()
        if status in {"done", "error", "cancelled"}:
            return

        yield ": keepalive\n\n"

        import asyncio
        await asyncio.sleep(1)


# =====================================================
# 1. 启动策略
# =====================================================

@router.post("/run", response_model=StrategyRunResponse)
async def run_strategy(req: StrategyRunRequest) -> StrategyRunResponse:
    workflow = get_workflow(req.workflow_id)
    workflow = await _maybe_await(workflow)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow 不存在")

    requirement_id = req.requirement_id or _workflow_get(workflow, "requirement_id", None)

    ctx = await get_strategy_context(req.workflow_id)
    if not ctx.get("has_requirement"):
        raise HTTPException(status_code=400, detail="缺少需求内容，无法生成策略")

    result = await start_strategy_run(
        workflow_id=req.workflow_id,
        requirement_id=requirement_id,
        force_refresh=req.force_refresh,
        use_analysis_result=req.use_analysis_result,
        use_testcase_result=req.use_testcase_result,
    )

    return StrategyRunResponse(
        job_id=result["job_id"],
        stream_id=result["stream_id"],
        workflow_id=req.workflow_id,
        requirement_id=requirement_id,
        status=result.get("status", "queued"),
        message=result.get("message", "strategy started"),
    )


# =====================================================
# 2. SSE 流
# =====================================================

@router.get("/stream")
async def stream_strategy(stream_id: str = Query(..., description="流 ID")):
    exists = await _stream_exists(stream_id)
    if not exists:
        raise HTTPException(status_code=404, detail="stream 不存在")

    return StreamingResponse(
        _stream_event_generator(stream_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =====================================================
# 3. stream 状态检查
# =====================================================

@router.get("/stream/status", response_model=StrategyStreamStatusResponse)
async def strategy_stream_status(stream_id: str = Query(..., description="流 ID")):
    exists = await _stream_exists(stream_id)
    if not exists:
        raise HTTPException(status_code=404, detail="stream 不存在")

    return StrategyStreamStatusResponse(stream_id=stream_id)


# =====================================================
# 4. 获取任务状态
# =====================================================

@router.get("/status", response_model=StrategyStatusResponse)
async def fetch_strategy_status(workflow_id: str = Query(..., description="工作流 ID")):
    workflow = get_workflow(workflow_id)
    workflow = await _maybe_await(workflow)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow 不存在")

    status = await get_strategy_status(workflow_id)

    return StrategyStatusResponse(
        workflow_id=workflow_id,
        job_id=status.get("job_id"),
        stream_id=status.get("stream_id"),
        status=status.get("status", "idle"),
        progress=status.get("progress"),
        result_ready=status.get("result_ready"),
        last_stage=status.get("last_stage"),
        last_stage_status=status.get("last_stage_status"),
        last_stage_title=status.get("last_stage_title"),
        last_stage_message=status.get("last_stage_message"),
        error_type=status.get("error_type"),
        error_message=status.get("error_message"),
        started_at=status.get("started_at"),
        finished_at=status.get("finished_at"),
        duration_ms=status.get("duration_ms"),
        result=status.get("result"),
    )


# =====================================================
# 5. 获取结果（企业级修复版）
# =====================================================

def _unwrap_result(data: Any) -> Optional[Dict[str, Any]]:
    """
    统一结果结构：
    - 兼容 result.result
    - 兼容 pydantic
    """
    if not data:
        return None

    # dict
    if isinstance(data, dict):
        # 👉 关键：拆 result.result
        if "result" in data and isinstance(data["result"], dict):
            inner = data["result"]
            # 防止无限嵌套
            if "summary" in inner or "risks" in inner:
                return inner
        return data

    # pydantic v2
    if hasattr(data, "model_dump"):
        return data.model_dump()

    # pydantic v1
    if hasattr(data, "dict"):
        return data.dict()

    return None


def _normalize_output(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    企业级统一输出结构（前端稳定渲染依赖）
    """
    return {
        "summary": result.get("summary", ""),
        "scope": result.get("scope", {}),
        "risks": result.get("risks", []),
        "coverage": result.get("coverage", {}),
        "plan": result.get("plan", []),
        "metrics": result.get("metrics", {}),
        "quality_gate": result.get("quality_gate", {}),
    }


@router.get("/result")
async def fetch_strategy_result(
    workflow_id: Optional[str] = Query(default=None),
    stream_id: Optional[str] = Query(default=None),
) -> Dict[str, Any]:

    if not workflow_id and not stream_id:
        raise HTTPException(status_code=400, detail="workflow_id 和 stream_id 不能同时为空")

    # =====================================================
    # 1️⃣ 优先 stream_id
    # =====================================================
    if stream_id:

        # --- 从 stream_store 读 ---
        store_data = await strategy_stream_store.get_stream(stream_id)

        if store_data:
            raw = store_data.get("result")
            result = _unwrap_result(raw)

            if result:
                return {
                    "ok": True,
                    "stream_id": stream_id,
                    "status": store_data.get("status", "done"),
                    "result": _normalize_output(result),
                    "message": "from stream_store",
                }

        # --- fallback ---
        raw = await strategy_stream_store.get_result(stream_id)
        result = _unwrap_result(raw)

        if result:
            return {
                "ok": True,
                "stream_id": stream_id,
                "status": "done",
                "result": _normalize_output(result),
                "message": "from stream_result",
            }

        return {
            "ok": True,
            "stream_id": stream_id,
            "status": "running",
            "result": None,
        }

    # =====================================================
    # 2️⃣ workflow fallback
    # =====================================================
    workflow = get_workflow(workflow_id)
    workflow = await _maybe_await(workflow)

    if not workflow:
        raise HTTPException(status_code=404, detail="workflow 不存在")

    wf_stream_id = _workflow_get(workflow, "strategy_stream_id", None)

    if wf_stream_id:
        store_data = await strategy_stream_store.get_stream(wf_stream_id)

        if store_data:
            raw = store_data.get("result")
            result = _unwrap_result(raw)

            if result:
                return {
                    "ok": True,
                    "workflow_id": workflow_id,
                    "stream_id": wf_stream_id,
                    "status": store_data.get("status", "done"),
                    "result": _normalize_output(result),
                    "message": "from workflow_stream",
                }

    # --- 最后 fallback ---
    raw = await get_strategy_result(workflow_id)
    result = _unwrap_result(raw)

    if not result:
        return {
            "ok": True,
            "workflow_id": workflow_id,
            "status": "running",
            "result": None,
        }

    return {
        "ok": True,
        "workflow_id": workflow_id,
        "status": "done",
        "result": _normalize_output(result),
    }


# =====================================================
# 6. 上下文
# =====================================================

@router.get("/context", response_model=StrategyContextResponse)
async def fetch_strategy_context(workflow_id: str = Query(..., description="工作流 ID")):
    workflow = get_workflow(workflow_id)
    workflow = await _maybe_await(workflow)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow 不存在")

    ctx = await get_strategy_context(workflow_id)

    return StrategyContextResponse(
        workflow_id=workflow_id,
        requirement_id=ctx.get("requirement_id"),
        has_requirement=bool(ctx.get("has_requirement")),
        has_analysis_result=bool(ctx.get("has_analysis_result")),
        has_testcase_result=bool(ctx.get("has_testcase_result")),
        strategy_status=ctx.get("strategy_status"),
        strategy_stream_id=ctx.get("strategy_stream_id"),
        strategy_job_id=ctx.get("strategy_job_id"),
        strategy_error=ctx.get("strategy_error"),
        message=ctx.get("message", "context loaded"),
    )


# =====================================================
# 7. 取消任务
# =====================================================

@router.post("/cancel")
async def cancel_strategy(req: StrategyCancelRequest) -> Dict[str, Any]:
    workflow = get_workflow(req.workflow_id)
    workflow = await _maybe_await(workflow)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow 不存在")

    result = await cancel_strategy_run(
        workflow_id=req.workflow_id,
        job_id=req.job_id,
        stream_id=req.stream_id,
    )
    return result


# =====================================================
# 8. Debug
# =====================================================

@router.get("/debug/stream")
async def debug_stream(stream_id: str = Query(..., description="流 ID")):
    data = await strategy_stream_store.get_stream(stream_id)
    return {
        "ok": True,
        "data": data,
    }


# =====================================================
# 9. 健康检查
# =====================================================

@router.get("/ping")
async def ping():
    return {"ok": True, "message": "strategy router ok"}