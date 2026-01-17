from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import shutil
import os
import uuid
import json
import asyncio
import traceback
import re
from typing import Dict

# ✅ 统一从 app.settings 读取
from app.settings import TMP_DIR

# ✅ 初始化临时目录
os.makedirs(TMP_DIR, exist_ok=True)

# =====================================================
# App 初始化
# =====================================================
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TASK_EXCEL_MAP: Dict[str, str] = {}

# =====================================================
# UI 清洗函数
# =====================================================
def clean_case_name_for_ui(name: str) -> str:
    if not name:
        return name
    return re.sub(
        r"^[A-Z]+(?:-[A-Z]+)*-\d+(?:-\d+)*:\s*",
        "",
        name
    )


def clean_module_name_for_ui(module: str) -> str:
    if not module:
        return module
    return module.split(" (")[0].strip()


# =====================================================
# 健康检查
# =====================================================
@app.get("/health")
def health():
    return {"status": "ok"}


# =====================================================
# SSE 工具函数
# =====================================================
def sse_event(event_type: str, data):
    return f"data: {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False)}\n\n"


# =====================================================
# SSE 主接口
# =====================================================
@app.post("/generate-testcases/stream")
async def generate_testcases_stream(
    file: UploadFile = File(...),
    requirement: str = Form("")
):
    async def event_generator():
        task_id = str(uuid.uuid4())
        tmp_name = f"sse_{task_id}_{file.filename}"
        file_path = os.path.join(TMP_DIR, tmp_name)

        try:
            # ✅ 全部使用 app.xxx 绝对路径
            from app.services.pdf_parser import parse_pdf
            from app.agents.orchestrator import Orchestrator
            from app.services.excel_exporter import export_excel
            from app.services.coverage import (
                check_mandatory_coverage,
                calc_overall_status
            )

            orch = Orchestrator()

            # ---------- connected ----------
            yield sse_event("connected", {"task_id": task_id})

            # ---------- 保存 PDF ----------
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)

            # ---------- PDF 解析 ----------
            yield sse_event("stage", "pdf_parsing")

            pdf_data = parse_pdf(file_path) or {}
            confirmed_text = pdf_data.get("confirmed_text", "")
            ocr_text = pdf_data.get("ocr_text", "")

            raw_requirements = confirmed_text
            if ocr_text:
                raw_requirements += "\n\n【OCR 补充内容】\n" + ocr_text

            yield sse_event("pdf_parsed", {
                "confirmed_text": confirmed_text,
                "ocr_text": ocr_text,
                "confidence": pdf_data.get("confidence", "LOW"),
            })

            if not raw_requirements.strip():
                yield sse_event("error", {"message": "PDF 无有效文本"})
                return

            # ---------- agent_running ----------
            yield sse_event("stage", "agent_running")

            confirmed_items = [requirement] if requirement else []

            # ---------- Stage 1：modules ----------
            modules = orch._stage_modules(raw_requirements)

            modules_for_ui = []
            for m in modules:
                m2 = dict(m)
                m2["module"] = clean_module_name_for_ui(m2.get("module"))
                modules_for_ui.append(m2)

            yield sse_event("modules", modules_for_ui)
            await asyncio.sleep(0)

            # ---------- Stage 2：test_points ----------
            test_points = orch._stage_test_points(
                raw_requirements,
                modules,
                confirmed_items
            )

            test_points = [
                m for m in test_points
                if m.get("module") not in ("前端强制测试要求", "强制测试要求")
            ]

            test_points_for_ui = []
            for group in test_points:
                g2 = dict(group)
                g2["module"] = clean_module_name_for_ui(g2.get("module"))
                test_points_for_ui.append(g2)

            yield sse_event("test_points", test_points_for_ui)
            await asyncio.sleep(0)

            # ---------- coverage flatten ----------
            flat_test_points = []
            for group in test_points:
                for p in group.get("points", []):
                    flat_test_points.append({
                        "id": p.get("id"),
                        "name": p.get("name"),
                        "source_requirement": p.get("source_requirement")
                    })

            # ---------- Stage 3：cases ----------
            collected_cases = []
            index = 0

            for group in test_points:
                for case in orch._stage_cases_stream(
                    raw_requirements,
                    [group],
                    confirmed_items
                ):
                    index += 1
                    collected_cases.append(case)

                    case_for_ui = dict(case)
                    case_for_ui["case_name"] = clean_case_name_for_ui(
                        case_for_ui.get("case_name")
                    )
                    case_for_ui["module"] = clean_module_name_for_ui(
                        case_for_ui.get("module")
                    )

                    yield sse_event("case", {
                        "case": case_for_ui,
                        "index": index
                    })

                    await asyncio.sleep(0)

            # ---------- Coverage ----------
            coverage_result = check_mandatory_coverage(
                confirmed_items,
                flat_test_points
            )
            status = calc_overall_status(coverage_result)

            # ---------- Excel ----------
            yield sse_event("stage", "excel_export")

            excel_path = export_excel(collected_cases)
            TASK_EXCEL_MAP[task_id] = excel_path

            # ---------- done ----------
            yield sse_event("done", {
                "total": len(collected_cases),
                "task_id": task_id,
                "download_url": f"/download/{task_id}",
                "status": status,
                "coverage": coverage_result
            })

        except Exception as e:
            yield sse_event("error", {
                "message": str(e),
                "trace": traceback.format_exc()
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
        }
    )


# =====================================================
# Excel 下载
# =====================================================
@app.get("/download/{task_id}")
async def download_excel(task_id: str):
    excel_path = TASK_EXCEL_MAP.get(task_id)

    if not excel_path or not os.path.exists(excel_path):
        return JSONResponse(
            status_code=404,
            content={"message": "Excel 文件不存在或已过期"}
        )

    return FileResponse(
        excel_path,
        filename="智能体生成测试用例.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
