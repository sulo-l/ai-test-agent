#! /usr/bin/python3
# coding=utf-8
# app/workflow/router.py

from fastapi import (
    APIRouter,
    HTTPException,
    UploadFile,
    File,
    Form,
)
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, Generator
import uuid
import os
import shutil
import json
import time
import queue
import threading
import traceback

from app.workflow.models import WorkflowStage
from app.workflow.state import (
    create_workflow,
    get_workflow,
    update_workflow,
    update_workflow_stage,
    reset_workflow,
    get_workflow_progress,
)
from app.workflow.analyze import analyze_requirements
from app.services.pdf_parser import parse_pdf
from app.services.excel_exporter import export_cases_to_excel
from app.agents.orchestrator import Orchestrator
from app.settings import TMP_DIR

router = APIRouter(tags=["workflow"])
os.makedirs(TMP_DIR, exist_ok=True)

DONE = object()


# =====================================================
# Models
# =====================================================

class WorkflowCreateResponse(BaseModel):
    workflow_id: str
    stage: str


class WorkflowStatusResponse(BaseModel):
    workflow_id: str
    stage: str
    progress: int
    message: Optional[str] = None
    excel_path: Optional[str] = None
    total_cases: Optional[int] = None


class WorkflowAnalyzeRequest(BaseModel):
    workflow_id: str


class WorkflowAnalyzeResponse(BaseModel):
    summary: dict
    requirements: list
    issues: list
    risks: list
    suggestions: list


# =====================================================
# SSE helpers
# =====================================================

def sse_pack(event: str, data: dict) -> str:
    # ✅ ensure_ascii=False 保证中文不被转义
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_ping() -> str:
    return ": ping\n\n"


# =====================================================
# 1️⃣ 创建 workflow
# =====================================================
@router.post("/create", response_model=WorkflowCreateResponse)
def create_new_workflow():
    workflow_id = str(uuid.uuid4())
    create_workflow(workflow_id=workflow_id)
    update_workflow_stage(workflow_id, WorkflowStage.IDLE)

    return WorkflowCreateResponse(
        workflow_id=workflow_id,
        stage=WorkflowStage.IDLE.value,
    )


# =====================================================
# 2️⃣ 查询 workflow 状态
# =====================================================
@router.get("/status/{workflow_id}", response_model=WorkflowStatusResponse)
def get_workflow_status(workflow_id: str):
    task = get_workflow(workflow_id)
    if not task:
        raise HTTPException(404, "Workflow not found")

    progress = get_workflow_progress(workflow_id)
    if not progress:
        raise HTTPException(404, "Workflow progress not found")

    return WorkflowStatusResponse(
        workflow_id=workflow_id,
        stage=progress.stage,
        progress=progress.progress,
        message=progress.message,
        excel_path=task.excel_path,
        total_cases=task.total_cases,
    )


# =====================================================
# 3️⃣ 上传 PDF
# =====================================================
@router.post("/upload-pdf")
def upload_pdf(
    workflow_id: str = Form(...),
    file: UploadFile = File(...),
):
    task = get_workflow(workflow_id)
    if not task:
        raise HTTPException(404, "Workflow not found")

    file_path = os.path.join(TMP_DIR, f"{workflow_id}_{file.filename}")
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    pdf_data = parse_pdf(file_path)
    raw_text = (
        (pdf_data.get("confirmed_text") or "")
        + "\n"
        + (pdf_data.get("ocr_text") or "")
    ).strip()

    if not raw_text:
        raise HTTPException(400, "PDF 解析失败")

    update_workflow(
        workflow_id=workflow_id,
        pdf_path=file_path,
        pdf_text=raw_text,
    )
    update_workflow_stage(workflow_id, WorkflowStage.FILE_READY)

    return {
        "workflow_id": workflow_id,
        "filename": file.filename,
        "text_length": len(raw_text),
    }


# =====================================================
# 4️⃣ AI 需求分析
# =====================================================
@router.post("/analyze", response_model=WorkflowAnalyzeResponse)
def analyze_workflow(req: WorkflowAnalyzeRequest):
    task = get_workflow(req.workflow_id)
    if not task:
        raise HTTPException(404, "Workflow not found")

    if not task.pdf_text:
        raise HTTPException(400, "PDF 尚未上传")

    update_workflow_stage(req.workflow_id, WorkflowStage.ANALYZING)

    try:
        result = analyze_requirements(
            workflow_id=req.workflow_id,
            raw_requirements=task.pdf_text,
        )
    except Exception as e:
        update_workflow_stage(
            req.workflow_id,
            WorkflowStage.ERROR,
            message=str(e),
        )
        raise HTTPException(500, "AI 需求分析失败")

    update_workflow(
        workflow_id=req.workflow_id,
        analysis_result=result,
    )
    update_workflow_stage(req.workflow_id, WorkflowStage.ANALYSIS_DONE)

    return WorkflowAnalyzeResponse(**result)


# =====================================================
# 5️⃣ AI 测试用例生成（SSE · 工程级稳定版）
# =====================================================
@router.get("/generate/stream")
def generate_testcases_stream(
    workflow_id: str,
    requirement: str = "",
):
    task = get_workflow(workflow_id)
    if not task:
        raise HTTPException(404, "Workflow not found")

    if not task.pdf_text:
        raise HTTPException(400, "PDF 尚未上传")

    q: "queue.Queue" = queue.Queue()

    def worker():
        try:
            update_workflow_stage(workflow_id, WorkflowStage.GENERATING)

            q.put(("meta", {"message": "generation_started"}))

            # 没有测试点则补生成（保留你原逻辑）
            if not task.test_points:
                analyze_requirements(
                    workflow_id=workflow_id,
                    raw_requirements=task.pdf_text,
                )

            refreshed = get_workflow(workflow_id)
            if not refreshed or not refreshed.test_points:
                raise RuntimeError("未生成测试点")

            orch = Orchestrator()
            collected = []

            # ⭐ 从 workflow 里拿到 focus_requirements（即使为空也不影响）
            focus_requirements = getattr(refreshed, "focus_requirements", None)

            for case in orch.run_streaming(
                raw_requirements=refreshed.pdf_text,
                test_points=refreshed.test_points,
                confirmed_items=[],
                requirement_hint=requirement,
                analysis_result=refreshed.analysis_result,
                focus_requirements=focus_requirements,  # ⭐ 透传给生成阶段
            ):
                collected.append(case)
                q.put(("case", case))

            excel_path = export_cases_to_excel(collected, workflow_id)
            update_workflow(
                workflow_id=workflow_id,
                excel_path=excel_path,
                total_cases=len(collected),
            )
            update_workflow_stage(workflow_id, WorkflowStage.GENERATED)

            q.put((
                "done",
                {
                    "total": len(collected),
                    "download_url": f"/workflow/download/{workflow_id}",
                },
            ))

        except Exception as e:
            traceback.print_exc()
            update_workflow_stage(
                workflow_id,
                WorkflowStage.ERROR,
                message=str(e),
            )
            q.put(("error", {"message": str(e)}))
        finally:
            q.put(DONE)

    threading.Thread(target=worker, daemon=True).start()

    def event_stream() -> Generator[str, None, None]:
        # ⭐ 首包，立刻防止前端超时
        yield sse_pack("meta", {"message": "connected"})

        last_send = time.time()

        while True:
            try:
                item = q.get(timeout=0.5)
                if item is DONE:
                    break

                event, payload = item
                yield sse_pack(event, payload)
                last_send = time.time()

            except queue.Empty:
                # 心跳保活
                if time.time() - last_send > 10:
                    yield sse_ping()
                    last_send = time.time()

    return StreamingResponse(
        event_stream(),
        # ✅ 关键：明确 charset，避免 EventStream 中文乱码
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            # ✅ 双保险：某些代理/中间层会覆盖 media_type
            "Content-Type": "text/event-stream; charset=utf-8",
        },
    )


# =====================================================
# 6️⃣ 下载 Excel
# =====================================================
@router.get("/download/{workflow_id}")
def download_excel(workflow_id: str):
    task = get_workflow(workflow_id)
    if task and task.excel_path and os.path.exists(task.excel_path):
        return FileResponse(
            task.excel_path,
            filename=os.path.basename(task.excel_path),
        )
    raise HTTPException(404, "Excel 不存在")


# =====================================================
# 7️⃣ 重置 workflow
# =====================================================
@router.post("/reset/{workflow_id}")
def reset_workflow_api(workflow_id: str):
    task = reset_workflow(workflow_id)
    if not task:
        raise HTTPException(404, "Workflow not found")

    return {
        "workflow_id": task.workflow_id,
        "stage": task.stage.value,
    }
