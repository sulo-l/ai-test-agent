#! /usr/bin/python3
# coding=utf-8
# @Author: sulo
# @Desc: Workflow Router (基础工作流创建/上传/状态更新)

from fastapi import (
    APIRouter,
    HTTPException,
    UploadFile,
    File,
    Form,
)
from pydantic import BaseModel
import os
import shutil
import logging
import time
import asyncio
from typing import Any, Dict

# ===============================
# Workflow State（内存态）
# ===============================
from app.workflow.state import (
    create_workflow,
    get_workflow,
    update_workflow_stage,
    update_workflow_data,
    reset_workflow,
)
from app.workflow.models import WorkflowStage
from app.settings import TMP_DIR

# ===============================
# PDF 解析（只在 upload 阶段）
# ===============================
from app.services.requirement_preparer import prepare_requirement_from_pdf

# ✅ 改成统一 requirement store
from app.services.requirement_store import upsert_requirement

# =====================================================
# Router
# =====================================================
router = APIRouter(prefix="/workflow", tags=["Workflow"])
os.makedirs(TMP_DIR, exist_ok=True)
logger = logging.getLogger(__name__)

DEFAULT_REQUIREMENT_ID = os.getenv("TC_DEFAULT_REQUIREMENT_ID", "default-requirement-id")


# =====================================================
# Models
# =====================================================
class WorkflowIdRequest(BaseModel):
    workflow_id: str


class PrepareGenerateRequest(BaseModel):
    workflow_id: str
    focus_requirements: str | None = None


# =====================================================
# 工具函数
# =====================================================
def _safe_text(v: Any) -> str:
    return str(v or "").strip()


def _prepared_to_dict(prepared: Any) -> Dict[str, Any]:
    """
    把 PreparedRequirement / 任意 prepared 对象转成可序列化 dict
    """
    if prepared is None:
        return {}

    if isinstance(prepared, dict):
        return dict(prepared)

    result: Dict[str, Any] = {}

    for attr in (
        "requirement_id",
        "final_text",
        "total_pages",
        "usable_for_ai",
        "pages",
        "requirement_blocks",
        "source_file_name",
        "title",
    ):
        try:
            value = getattr(prepared, attr, None)
            if value is not None:
                result[attr] = value
        except Exception:
            continue

    return result


# =====================================================
# 1️⃣ 创建 workflow
# =====================================================
@router.post("/create")
def create_new_workflow():
    task = create_workflow()
    return {
        "success": True,
        "data": {
            "workflow_id": task.workflow_id,
            "stage": task.stage.value,
        },
    }


# =====================================================
# ♻️ 重置 workflow
# =====================================================
@router.post("/reset")
def reset_workflow_status(req: WorkflowIdRequest):
    task = get_workflow(req.workflow_id)
    if not task:
        task = create_workflow(workflow_id=req.workflow_id)

    reset_workflow(req.workflow_id)

    return {
        "success": True,
        "data": {
            "workflow_id": req.workflow_id,
            "stage": WorkflowStage.IDLE.value,
        },
    }


# =====================================================
# 2️⃣ 上传 PDF（唯一允许构造 PreparedRequirement 的地方）
# =====================================================
@router.post("/upload-pdf")
async def upload_pdf(
    workflow_id: str = Form(...),
    file: UploadFile = File(...),
    requirement_id: str | None = Form(None),
):
    """
    上传 PDF 并解析出需求文本：
    - 写入 workflow 内存对象（供 API 同进程读）
    - ✅ 同时统一持久化到 requirement_store（供 ARQ worker 读）
    """
    task = get_workflow(workflow_id)
    if not task:
        logger.warning("[upload_pdf] workflow_id=%s not found, auto-creating", workflow_id)
        task = create_workflow(workflow_id=workflow_id)

    rid = (requirement_id or "").strip() or DEFAULT_REQUIREMENT_ID

    # 1) 保存上传 PDF 到临时目录
    file_path = os.path.join(TMP_DIR, f"{workflow_id}_{file.filename}")
    try:
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    except Exception as e:
        logger.exception("Save uploaded PDF failed: %s", e)
        raise HTTPException(status_code=500, detail="Save uploaded PDF failed")

    # 2) 解析 PDF（在线程池中运行，避免阻塞 asyncio 事件循环）
    try:
        def _parse_pdf():
            try:
                return prepare_requirement_from_pdf(
                    pdf_path=file_path,
                    requirement_id=rid,
                )
            except TypeError:
                try:
                    return prepare_requirement_from_pdf(
                        file_path,
                        requirement_id=rid,
                    )
                except TypeError:
                    return prepare_requirement_from_pdf(file_path)

        parsed = await asyncio.to_thread(_parse_pdf)

        pdf_text = _safe_text(getattr(parsed, "final_text", ""))
        total_pages = int(getattr(parsed, "total_pages", 0) or 0)
        usable_for_ai = bool(getattr(parsed, "usable_for_ai", bool(pdf_text)))
        source_file_name = _safe_text(getattr(parsed, "source_file_name", "")) or _safe_text(file.filename)
    except Exception as e:
        logger.exception("PDF parse failed: %s", e)
        raise HTTPException(status_code=500, detail="PDF parse failed")

    if not pdf_text:
        raise HTTPException(status_code=400, detail="Parsed PDF text is empty")

    prepared_dict = _prepared_to_dict(parsed)

    # 3) 写入 workflow 内存数据（保留 + 补强）
    update_workflow_data(
        workflow_id=workflow_id,
        pdf_path=file_path,
        pdf_text=pdf_text,
        requirement_id=rid,
        prepared_requirement=prepared_dict,
        source_file_name=source_file_name,
        updated_at=int(time.time()),
    )

    # 4) ✅ 统一持久化到 requirement_store（worker 可直接读）
    try:
        await upsert_requirement(
            workflow_id=workflow_id,
            requirement_id=rid,
            requirement_text=pdf_text,
            pdf_path=file_path,
            source_file_name=source_file_name,
            extra={
                "source": "workflow.upload_pdf",
                "file_name": _safe_text(file.filename),
                "total_pages": total_pages,
                "usable_for_ai": usable_for_ai,
            },
        )
    except Exception as e:
        logger.exception("Persist requirement failed: %s", e)
        raise HTTPException(status_code=500, detail="Persist requirement failed")

    # 5) 修改状态为 PDF 已就绪
    update_workflow_stage(workflow_id, WorkflowStage.FILE_READY)

    logger.info(
        "[workflow.upload_pdf] success | workflow_id=%s | requirement_id=%s | text_len=%s | pdf_path=%s | source_file_name=%s | total_pages=%s | usable_for_ai=%s",
        workflow_id,
        rid,
        len(pdf_text),
        file_path,
        source_file_name,
        total_pages,
        usable_for_ai,
    )

    return {
        "success": True,
        "data": {
            "workflow_id": workflow_id,
            "stage": WorkflowStage.FILE_READY.value,
            "requirement_id": rid,
            "text_length": len(pdf_text),
            "pdf_path": file_path,
            "source_file_name": source_file_name,
            "total_pages": total_pages,
            "usable_for_ai": usable_for_ai,
        },
    }


# =====================================================
# 3️⃣ 保存补充生成要求
# =====================================================
@router.post("/prepare-generate")
def prepare_generate(req: PrepareGenerateRequest):
    task = get_workflow(req.workflow_id)
    if not task:
        raise HTTPException(status_code=404, detail="Workflow not found")

    update_workflow_data(
        workflow_id=req.workflow_id,
        focus_requirements=req.focus_requirements,
        updated_at=int(time.time()),
    )

    return {"success": True}


# =====================================================
# ❗ 以下接口已弃用
# =====================================================
# 不再提供 /workflow/analyze/stream
# 不再提供 /workflow/generate/stream



