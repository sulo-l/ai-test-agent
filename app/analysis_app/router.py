#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/2/8 21:55
# @Author: sulo
# app/analysis_app/router.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Optional, Any, Dict

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.analysis_app.controller import start_requirement_analysis
from app.analysis_app.sse import analysis_sse_manager
from app.workflow.state import get_workflow


router = APIRouter(
    prefix="/analysis",
    tags=["analysis"],
)


# =====================================================
# 工具函数
# =====================================================

def _task_get(task: Any, key: str, default: Any = None) -> Any:
    if task is None:
        return default

    if isinstance(task, dict):
        return task.get(key, default)

    return getattr(task, key, default)


def _task_set(task: Any, key: str, value: Any) -> None:
    if task is None:
        return

    if isinstance(task, dict):
        task[key] = value
        return

    setattr(task, key, value)


def _safe_to_dict(raw: Any) -> Optional[Dict[str, Any]]:
    """
    将 workflow 中保存的分析结果安全转换为 dict
    兼容：
    - dict
    - pydantic v2: model_dump()
    - pydantic v1: dict()
    - 普通对象：读取 __dict__
    """
    if raw is None:
        return None

    if isinstance(raw, dict):
        return raw

    model_dump = getattr(raw, "model_dump", None)
    if callable(model_dump):
        try:
            data = model_dump()
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    to_dict = getattr(raw, "dict", None)
    if callable(to_dict):
        try:
            data = to_dict()
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    obj_dict = getattr(raw, "__dict__", None)
    if isinstance(obj_dict, dict):
        try:
            return dict(obj_dict)
        except Exception:
            pass

    return None


def _build_public_result(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    对外轻量结果：
    - 保留 overview 作为唯一概览
    - 保留核心面板，去掉重 payload 面板
    - 不重复挂 workflowId / requirementId / cacheHit 等顶层冗余字段
    """
    if not isinstance(result, dict):
        return None

    panels = result.get("panels", {})
    if not isinstance(panels, dict):
        panels = {}

    meta = result.get("meta", {})
    if not isinstance(meta, dict):
        meta = {}

    public_result = {
        "overview": result.get("overview", {}) if isinstance(result.get("overview"), dict) else {},
        "qualityGate": result.get("qualityGate", {}) if isinstance(result.get("qualityGate"), dict) else {},
        "topIssues": result.get("topIssues", []) if isinstance(result.get("topIssues"), list) else [],
        "issues": result.get("issues", []) if isinstance(result.get("issues"), list) else [],
        "statistics": result.get("statistics", {}) if isinstance(result.get("statistics"), dict) else {},
        "panels": {
            "analysis": panels.get("analysis", {}) if isinstance(panels.get("analysis"), dict) else {},
            "coverage": panels.get("coverage", {}) if isinstance(panels.get("coverage"), dict) else {},
            "review": panels.get("review", {}) if isinstance(panels.get("review"), dict) else {},
            "score": panels.get("score", {}) if isinstance(panels.get("score"), dict) else {},
            "risk": panels.get("risk", {}) if isinstance(panels.get("risk"), dict) else {},
        },
        "recommendations": result.get("recommendations", []) if isinstance(result.get("recommendations"), list) else [],
        "meta": {
            "durationMs": meta.get("durationMs", 0),
            "parallelEnabled": bool(meta.get("parallelEnabled", False)),
            "maxParallelAgents": meta.get("maxParallelAgents", 0),
            "agentTimeoutSec": meta.get("agentTimeoutSec", 0),
            "workflowId": meta.get("workflowId"),
            "requirementId": meta.get("requirementId"),
            "cacheHit": bool(meta.get("cacheHit", False)),
            "reusedInflight": bool(meta.get("reusedInflight", False)),
            "cacheVersion": meta.get("cacheVersion"),
        },
    }

    return public_result


def _build_detail_result(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    完整结果：
    - 返回 pipeline 保存的完整紧凑结构
    - 但剔除重复顶层字段污染（如果有）
    """
    if not isinstance(result, dict):
        return None

    cleaned = dict(result)
    cleaned.pop("workflowId", None)
    cleaned.pop("requirementId", None)
    cleaned.pop("cacheHit", None)
    cleaned.pop("reusedInflight", None)

    return cleaned


# =====================================================
# Request / Response Models
# =====================================================

class AnalysisRunRequest(BaseModel):
    workflow_id: str = Field(..., description="工作流ID")
    requirement_id: str = Field(..., description="需求ID")


class AnalysisRunResponse(BaseModel):
    ok: bool = Field(..., description="是否启动成功")
    stream_id: str = Field(..., description="SSE stream ID")
    workflow_id: str = Field(..., description="工作流ID")
    requirement_id: str = Field(..., description="需求ID")
    status: str = Field(..., description="分析状态：running")
    message: str = Field(..., description="响应说明")


class AnalysisStreamStatusResponse(BaseModel):
    ok: bool = Field(..., description="stream 是否存在")
    stream_id: str = Field(..., description="SSE stream ID")
    message: str = Field(..., description="状态说明")


class AnalysisResultResponse(BaseModel):
    ok: bool = Field(..., description="是否成功")
    workflow_id: str = Field(..., description="工作流ID")
    requirement_id: Optional[str] = Field(None, description="需求ID")
    status: str = Field(..., description="分析状态：idle / running / done / error")
    has_result: bool = Field(..., description="是否已有分析结果")
    result: Optional[Dict[str, Any]] = Field(None, description="轻量分析结果")
    error: Optional[str] = Field(None, description="错误信息")
    message: str = Field(..., description="响应说明")


class AnalysisDetailResponse(BaseModel):
    ok: bool = Field(..., description="是否成功")
    workflow_id: str = Field(..., description="工作流ID")
    requirement_id: Optional[str] = Field(None, description="需求ID")
    status: str = Field(..., description="分析状态：idle / running / done / error")
    has_result: bool = Field(..., description="是否已有分析结果")
    result: Optional[Dict[str, Any]] = Field(None, description="完整分析结果")
    error: Optional[str] = Field(None, description="错误信息")
    message: str = Field(..., description="响应说明")


# =====================================================
# 1️⃣ 启动需求分析（POST）
# =====================================================

@router.post("/run", response_model=AnalysisRunResponse)
async def run_analysis(req: AnalysisRunRequest):
    """
    启动需求分析智能体

    返回：
    - stream_id：前端通过 /analysis/stream?stream_id=xxx 建立 SSE 连接
    """
    workflow_id = (req.workflow_id or "").strip()
    requirement_id = (req.requirement_id or "").strip()

    if not workflow_id:
        raise HTTPException(status_code=400, detail="workflow_id required")

    if not requirement_id:
        raise HTTPException(status_code=400, detail="requirement_id required")

    task = get_workflow(workflow_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=f"workflow not found: {workflow_id}",
        )

    requirement_text = (_task_get(task, "pdf_text", "") or "").strip()
    if not requirement_text:
        raise HTTPException(
            status_code=400,
            detail=f"requirement text not found in workflow memory: {workflow_id}",
        )

    _task_set(task, "analysis_requirement_id", requirement_id)
    _task_set(task, "analysis_error", None)
    _task_set(task, "analysis_status", "running")

    stream_id = await analysis_sse_manager.create_stream()

    await start_requirement_analysis(
        stream_id=stream_id,
        workflow_id=workflow_id,
        requirement_id=requirement_id,
    )

    return AnalysisRunResponse(
        ok=True,
        stream_id=stream_id,
        workflow_id=workflow_id,
        requirement_id=requirement_id,
        status="running",
        message="analysis started",
    )


# =====================================================
# 2️⃣ SSE 流式输出（GET）
# =====================================================

@router.get("/stream")
async def stream_analysis(
    stream_id: str = Query(..., description="SSE stream ID"),
):
    """
    需求分析 SSE 输出接口

    前端调用方式：
        GET /analysis/stream?stream_id=xxx
    """
    stream_id = (stream_id or "").strip()

    if not stream_id:
        raise HTTPException(status_code=400, detail="stream_id required")

    exists = await analysis_sse_manager.exists(stream_id)
    if not exists:
        raise HTTPException(status_code=404, detail="invalid stream_id")

    generator = analysis_sse_manager.subscribe(stream_id)

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream; charset=utf-8",
        },
    )


# =====================================================
# 3️⃣ 查询 stream 是否存在（GET）
# =====================================================

@router.get("/stream/status", response_model=AnalysisStreamStatusResponse)
async def stream_status(
    stream_id: str = Query(..., description="SSE stream ID"),
):
    """
    查询 stream 状态，方便前端在建立 EventSource 前先做一次校验
    """
    stream_id = (stream_id or "").strip()

    if not stream_id:
        raise HTTPException(status_code=400, detail="stream_id required")

    exists = await analysis_sse_manager.exists(stream_id)

    return AnalysisStreamStatusResponse(
        ok=exists,
        stream_id=stream_id,
        message="stream exists" if exists else "stream not found",
    )


# =====================================================
# 4️⃣ 查询轻量分析结果（GET）
# =====================================================

@router.get("/result", response_model=AnalysisResultResponse)
async def get_analysis_result(
    workflow_id: str = Query(..., description="工作流ID"),
    requirement_id: Optional[str] = Query(None, description="需求ID"),
):
    """
    查询某个 workflow 当前保存的需求分析结果（轻量版）

    返回：
    - overview
    - qualityGate
    - topIssues
    - issues
    - statistics
    - panels.analysis / coverage / review / score / risk
    - recommendations
    - meta
    """
    workflow_id = (workflow_id or "").strip()
    requirement_id = (requirement_id or "").strip() or None

    if not workflow_id:
        raise HTTPException(status_code=400, detail="workflow_id required")

    task = get_workflow(workflow_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=f"workflow not found: {workflow_id}",
        )

    raw_result = (
        _task_get(task, "analysis_result", None)
        or _task_get(task, "requirement_analysis_result", None)
        or _task_get(task, "analysisResult", None)
    )

    status = str(_task_get(task, "analysis_status", "idle") or "idle")
    error = _task_get(task, "analysis_error", None)

    saved_requirement_id = (
        _task_get(task, "analysis_requirement_id", None)
        or requirement_id
    )

    normalized_result = _safe_to_dict(raw_result)
    public_result = _build_public_result(normalized_result)
    has_result = public_result is not None

    if status == "error":
        message = "analysis failed"
    elif status == "running":
        message = "analysis is running"
    elif status == "done" and has_result:
        message = "analysis result found"
    elif status == "done" and not has_result:
        message = "analysis completed but result serialization failed"
    else:
        message = "analysis result not ready"

    return AnalysisResultResponse(
        ok=True,
        workflow_id=workflow_id,
        requirement_id=saved_requirement_id,
        status=status,
        has_result=has_result,
        result=public_result,
        error=str(error) if error else None,
        message=message,
    )


# =====================================================
# 5️⃣ 查询完整分析结果（GET）
# =====================================================

@router.get("/result/detail", response_model=AnalysisDetailResponse)
async def get_analysis_result_detail(
    workflow_id: str = Query(..., description="工作流ID"),
    requirement_id: Optional[str] = Query(None, description="需求ID"),
):
    """
    查询某个 workflow 当前保存的需求分析结果（完整详情版）

    返回：
    - pipeline 完整 compact result
    - 适用于详情页、调试页、导出页
    """
    workflow_id = (workflow_id or "").strip()
    requirement_id = (requirement_id or "").strip() or None

    if not workflow_id:
        raise HTTPException(status_code=400, detail="workflow_id required")

    task = get_workflow(workflow_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=f"workflow not found: {workflow_id}",
        )

    raw_result = (
        _task_get(task, "analysis_result", None)
        or _task_get(task, "requirement_analysis_result", None)
        or _task_get(task, "analysisResult", None)
    )

    status = str(_task_get(task, "analysis_status", "idle") or "idle")
    error = _task_get(task, "analysis_error", None)

    saved_requirement_id = (
        _task_get(task, "analysis_requirement_id", None)
        or requirement_id
    )

    normalized_result = _safe_to_dict(raw_result)
    detail_result = _build_detail_result(normalized_result)
    has_result = detail_result is not None

    if status == "error":
        message = "analysis failed"
    elif status == "running":
        message = "analysis is running"
    elif status == "done" and has_result:
        message = "analysis result found"
    elif status == "done" and not has_result:
        message = "analysis completed but result serialization failed"
    else:
        message = "analysis result not ready"

    return AnalysisDetailResponse(
        ok=True,
        workflow_id=workflow_id,
        requirement_id=saved_requirement_id,
        status=status,
        has_result=has_result,
        result=detail_result,
        error=str(error) if error else None,
        message=message,
    )