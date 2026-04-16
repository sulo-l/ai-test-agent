# -*- coding: utf-8 -*-
import logging
from typing import Optional, Dict, Any

from fastapi import BackgroundTasks

from app.workflow.state import get_workflow
from app.services.workflow_store import load_workflow_object
from app.services.requirement_store import upsert_requirement

logger = logging.getLogger(__name__)


# =========================================================
# 工具函数
# =========================================================

def _safe_text(v: Any) -> str:
    return str(v or "").strip()


def _extract_text_from_prepared(prepared: Any) -> str:
    if prepared is None:
        return ""

    if isinstance(prepared, dict):
        for item in (
            prepared.get("final_text"),
            prepared.get("requirement_text"),
            prepared.get("text"),
            prepared.get("content"),
            prepared.get("pdf_text"),
        ):
            text = _safe_text(item)
            if text:
                return text

    for attr in ("final_text", "requirement_text", "text", "content", "pdf_text"):
        try:
            text = _safe_text(getattr(prepared, attr, ""))
            if text:
                return text
        except Exception:
            continue

    return ""


def _extract_pdf_path_from_prepared(prepared: Any) -> str:
    if prepared is None:
        return ""

    if isinstance(prepared, dict):
        for item in (
            prepared.get("pdf_path"),
            prepared.get("file_path"),
            prepared.get("path"),
            prepared.get("local_path"),
        ):
            path = _safe_text(item)
            if path:
                return path

    for attr in ("pdf_path", "file_path", "path", "local_path"):
        try:
            path = _safe_text(getattr(prepared, attr, ""))
            if path:
                return path
        except Exception:
            continue

    return ""


def _extract_source_file_name_from_prepared(prepared: Any) -> str:
    if prepared is None:
        return ""

    if isinstance(prepared, dict):
        for item in (
            prepared.get("source_file_name"),
            prepared.get("file_name"),
            prepared.get("filename"),
            prepared.get("name"),
        ):
            name = _safe_text(item)
            if name:
                return name

    for attr in ("source_file_name", "file_name", "filename", "name"):
        try:
            name = _safe_text(getattr(prepared, attr, ""))
            if name:
                return name
        except Exception:
            continue

    return ""


def _load_requirement_from_memory(workflow_id: str) -> Dict[str, Any]:
    """
    从内存 workflow 中提取需求信息
    """
    requirement_text = ""
    pdf_path = ""
    source_file_name = ""

    task = get_workflow(workflow_id)
    if not task:
        return {
            "requirement_text": "",
            "pdf_path": "",
            "source_file_name": "",
            "source": "",
        }

    prepared = getattr(task, "prepared_requirement", None)

    if prepared:
        requirement_text = _extract_text_from_prepared(prepared)
        pdf_path = _extract_pdf_path_from_prepared(prepared)
        source_file_name = _extract_source_file_name_from_prepared(prepared)

    if not requirement_text:
        requirement_text = _safe_text(getattr(task, "pdf_text", ""))

    if not pdf_path:
        pdf_path = _safe_text(getattr(task, "pdf_path", ""))

    if requirement_text or pdf_path:
        return {
            "requirement_text": requirement_text,
            "pdf_path": pdf_path,
            "source_file_name": source_file_name,
            "source": "workflow.state",
        }

    return {
        "requirement_text": "",
        "pdf_path": "",
        "source_file_name": "",
        "source": "",
    }


def _load_requirement_from_store(workflow_id: str) -> Dict[str, Any]:
    """
    从 workflow_store 中提取需求信息
    """
    requirement_text = ""
    pdf_path = ""
    source_file_name = ""

    stored = load_workflow_object(workflow_id)
    if not stored:
        return {
            "requirement_text": "",
            "pdf_path": "",
            "source_file_name": "",
            "source": "",
        }

    prepared = stored.get("prepared_requirement")
    if prepared:
        requirement_text = _extract_text_from_prepared(prepared)
        pdf_path = _extract_pdf_path_from_prepared(prepared)
        source_file_name = _extract_source_file_name_from_prepared(prepared)

    if not requirement_text:
        requirement_text = _safe_text(stored.get("pdf_text"))

    if not pdf_path:
        pdf_path = _safe_text(stored.get("pdf_path"))

    if requirement_text or pdf_path:
        return {
            "requirement_text": requirement_text,
            "pdf_path": pdf_path,
            "source_file_name": source_file_name,
            "source": "workflow_store",
        }

    return {
        "requirement_text": "",
        "pdf_path": "",
        "source_file_name": "",
        "source": "",
    }


def _load_requirement_data(workflow_id: str) -> Dict[str, Any]:
    """
    统一需求加载逻辑：
    1. workflow.state
    2. workflow_store
    """
    memory_data = _load_requirement_from_memory(workflow_id)
    if memory_data.get("requirement_text") or memory_data.get("pdf_path"):
        return memory_data

    store_data = _load_requirement_from_store(workflow_id)
    if store_data.get("requirement_text") or store_data.get("pdf_path"):
        return store_data

    return {
        "requirement_text": "",
        "pdf_path": "",
        "source_file_name": "",
        "source": "",
    }


async def _persist_requirement_to_store(
    workflow_id: str,
    requirement_id: str,
) -> Dict[str, Any]:
    """
    从 workflow 链路取出需求，并落到 requirement_store
    """
    loaded = _load_requirement_data(workflow_id)

    requirement_text = _safe_text(loaded.get("requirement_text"))
    pdf_path = _safe_text(loaded.get("pdf_path"))
    source_file_name = _safe_text(loaded.get("source_file_name"))
    source = _safe_text(loaded.get("source"))

    await upsert_requirement(
        workflow_id=workflow_id,
        requirement_id=requirement_id,
        requirement_text=requirement_text if requirement_text else None,
        pdf_path=pdf_path if pdf_path else None,
        source_file_name=source_file_name if source_file_name else None,
        extra={"source": source} if source else {},
    )

    logger.info(
        "[workflow.controller] persisted requirement | workflow_id=%s | requirement_id=%s | text_len=%s | has_pdf_path=%s | pdf_path=%s | source=%s",
        workflow_id,
        requirement_id,
        len(requirement_text or ""),
        bool(pdf_path),
        pdf_path,
        source,
    )

    return {
        "workflow_id": workflow_id,
        "requirement_id": requirement_id,
        "requirement_text": requirement_text,
        "final_text": requirement_text,
        "pdf_path": pdf_path,
        "source_file_name": source_file_name,
        "source": source,
    }


# =========================================================
# 对 testcase_app 提供的统一 loader
# =========================================================

async def load_prepared_requirement(workflow_id: str, requirement_id: str) -> Dict[str, Any]:
    return await _persist_requirement_to_store(workflow_id, requirement_id)


async def get_prepared_requirement(workflow_id: str, requirement_id: str) -> Dict[str, Any]:
    return await _persist_requirement_to_store(workflow_id, requirement_id)


async def load_requirement_text(workflow_id: str, requirement_id: str) -> str:
    data = await _persist_requirement_to_store(workflow_id, requirement_id)
    return _safe_text(data.get("requirement_text"))


async def get_requirement_text(workflow_id: str, requirement_id: str) -> str:
    return await load_requirement_text(workflow_id, requirement_id)


async def get_requirement_content(workflow_id: str, requirement_id: str) -> str:
    return await load_requirement_text(workflow_id, requirement_id)


async def load_requirement_content(workflow_id: str, requirement_id: str) -> str:
    return await load_requirement_text(workflow_id, requirement_id)


async def load_requirement_file(workflow_id: str, requirement_id: str) -> Dict[str, Any]:
    data = await _persist_requirement_to_store(workflow_id, requirement_id)
    return {
        "workflow_id": workflow_id,
        "requirement_id": requirement_id,
        "pdf_path": _safe_text(data.get("pdf_path")),
        "file_path": _safe_text(data.get("pdf_path")),
        "source_file_name": _safe_text(data.get("source_file_name")),
        "source": _safe_text(data.get("source")),
    }


async def get_requirement_file(workflow_id: str, requirement_id: str) -> Dict[str, Any]:
    return await load_requirement_file(workflow_id, requirement_id)


# =========================================================
# Controller 入口
# =========================================================

async def start_testcase_generation(
    stream_id: str,
    workflow_id: str,
    requirement_id: str,
    extra_requirement: Optional[str],
    background_tasks: BackgroundTasks,
):
    """
    新版职责：
    只负责把 workflow 链路中的需求信息落到 requirement_store，
    真正的 testcase 生成由 testcase_app 新架构处理。
    """
    background_tasks.add_task(
        _prepare_requirement_for_testcase,
        stream_id,
        workflow_id,
        requirement_id,
        extra_requirement,
    )


# =========================================================
# Worker（轻量准备，不再直接跑 testcase pipeline）
# =========================================================

async def _prepare_requirement_for_testcase(
    stream_id: str,
    workflow_id: str,
    requirement_id: str,
    extra_requirement: Optional[str],
):
    logger.info(
        "[workflow.controller] prepare testcase requirement start | stream_id=%s | workflow_id=%s | requirement_id=%s | extra_requirement_len=%s",
        stream_id,
        workflow_id,
        requirement_id,
        len(_safe_text(extra_requirement)),
    )

    try:
        data = await _persist_requirement_to_store(workflow_id, requirement_id)

        if not _safe_text(data.get("requirement_text")) and not _safe_text(data.get("pdf_path")):
            logger.warning(
                "[workflow.controller] no requirement data found | workflow_id=%s | requirement_id=%s",
                workflow_id,
                requirement_id,
            )
            return

        logger.info(
            "[workflow.controller] prepare testcase requirement done | stream_id=%s | workflow_id=%s | requirement_id=%s | text_len=%s | has_pdf_path=%s",
            stream_id,
            workflow_id,
            requirement_id,
            len(_safe_text(data.get("requirement_text"))),
            bool(_safe_text(data.get("pdf_path"))),
        )

    except Exception as e:
        logger.exception(
            "[workflow.controller] prepare testcase requirement failed | stream_id=%s | workflow_id=%s | requirement_id=%s | err=%s",
            stream_id,
            workflow_id,
            requirement_id,
            repr(e),
        )