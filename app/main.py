from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.workflow.router import router as workflow_router


import shutil
import os
import uuid
import json
import asyncio
import traceback
import re
from typing import Dict, Optional

# ===============================
# 项目内部依赖
# ===============================
from app.settings import TMP_DIR
from app.workflow.state import update_workflow, get_workflow
from app.workflow.models import WorkflowStage

# ✅ 正确的 merge 函数
from app.workflow.merge import merge_generation_context

# ===============================
# 初始化
# ===============================
os.makedirs(TMP_DIR, exist_ok=True)

app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ⭐ 注册 workflow 路由（关键）
from app.workflow.router import router as workflow_router
app.include_router(workflow_router, prefix="/workflow", tags=["workflow"])

TASK_EXCEL_MAP: Dict[str, str] = {}

# =====================================================
# SSE 工具函数
# =====================================================
def sse_event(event_type: str, data):
    return f"data: {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False)}\n\n"


# =====================================================
# SSE 主接口（生成阶段）
# =====================================================
@app.post("/generate-testcases/stream")
async def generate_testcases_stream(
    file: UploadFile = File(...),
    requirement: str = Form(""),
    workflow_id: Optional[str] = Form(None),
):
    async def event_generator():
        task_id = str(uuid.uuid4())
        tmp_name = f"sse_{task_id}_{file.filename}"
        file_path = os.path.join(TMP_DIR, tmp_name)

        try:
            from app.services.pdf_parser import parse_pdf
            from app.agents.orchestrator import Orchestrator
            from app.services.excel_exporter import export_excel

            orch = Orchestrator()

            # ===============================
            # workflow：开始生成
            # ===============================
            if workflow_id:
                update_workflow(
                    workflow_id,
                    stage=WorkflowStage.GENERATING,
                    progress=5,
                    message="建立生成任务",
                    task_id=task_id,
                )

            yield sse_event("connected", {"task_id": task_id})

            # ===============================
            # 保存 PDF
            # ===============================
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)

            # ===============================
            # PDF 解析
            # ===============================
            yield sse_event("stage", "pdf_parsing")
            update_workflow(workflow_id, progress=15, message="解析需求文档")

            pdf_data = parse_pdf(file_path) or {}
            raw_requirements = (
                pdf_data.get("confirmed_text", "")
                + "\n"
                + pdf_data.get("ocr_text", "")
            ).strip()

            if not raw_requirements:
                yield sse_event("error", {"message": "PDF 无有效文本"})
                return

            # =====================================================
            # ⭐ 关键：合并「分析结果 + 用户输入 + 原始需求」
            # =====================================================
            analysis_result = None
            if workflow_id:
                wf = get_workflow(workflow_id)
                analysis_result = wf.analysis_result if wf else None

            merged = merge_generation_context(
                raw_requirements=raw_requirements,
                user_requirement=requirement,
                analysis_result=analysis_result,
            )

            merged_context = merged["merged_requirements"]

            # ===============================
            # Stage 1：modules
            # ===============================
            update_workflow(workflow_id, progress=30, message="构建功能模块")
            modules = orch._stage_modules(merged_context)
            yield sse_event("modules", modules)

            # ===============================
            # Stage 2：test_points
            # ===============================
            update_workflow(workflow_id, progress=50, message="生成测试点")
            test_points = orch._stage_test_points(
                merged_context,
                modules,
                [],
            )
            yield sse_event("test_points", test_points)

            # ===============================
            # Stage 3：cases
            # ===============================
            update_workflow(workflow_id, progress=70, message="生成测试用例")
            collected_cases = []

            for group in test_points:
                for case in orch._stage_cases_stream(
                    merged_context,
                    [group],
                    [],
                ):
                    collected_cases.append(case)
                    yield sse_event("case", {"case": case})
                    await asyncio.sleep(0)

            # ===============================
            # Excel
            # ===============================
            update_workflow(workflow_id, progress=90, message="导出 Excel")
            excel_path = export_excel(collected_cases)
            TASK_EXCEL_MAP[task_id] = excel_path

            update_workflow(
                workflow_id,
                stage=WorkflowStage.GENERATED,
                progress=100,
                message="生成完成",
                excel_path=excel_path,
            )

            yield sse_event("done", {
                "task_id": task_id,
                "download_url": f"/download/{task_id}",
            })

        except Exception as e:
            yield sse_event("error", {
                "message": str(e),
                "trace": traceback.format_exc(),
            })

        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# =====================================================
# Excel 下载
# =====================================================
@app.get("/download/{task_id}")
async def download_excel(task_id: str):
    excel_path = TASK_EXCEL_MAP.get(task_id)
    if not excel_path or not os.path.exists(excel_path):
        return JSONResponse(status_code=404, content={"message": "Excel 不存在"})
    return FileResponse(
        excel_path,
        filename="智能体生成测试用例.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
