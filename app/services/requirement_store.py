#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/2/22 00:10
# @Author: sulo
# -*- coding: utf-8 -*-
# app/services/requirement_store.py

import os
import json
import time
import logging
from typing import Optional, Dict, Any

from app.infra.redis_client import get_redis

logger = logging.getLogger(__name__)

REQ_PREFIX = os.getenv("TC_REQ_PREFIX", "tc:req:")
REQ_TTL_SEC = int(os.getenv("TC_REQ_TTL_SEC", os.getenv("TC_STREAM_TTL_SEC", "3600")))


def _req_key(workflow_id: str, requirement_id: str) -> str:
    return f"{REQ_PREFIX}{workflow_id}:{requirement_id}"


def _safe_text(v: Any) -> str:
    return str(v or "").strip()


def _now_ts() -> int:
    return int(time.time())


def _normalize_payload(
    workflow_id: str,
    requirement_id: str,
    requirement_text: str = "",
    pdf_path: str = "",
    source_file_name: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "workflow_id": _safe_text(workflow_id),
        "requirement_id": _safe_text(requirement_id),
        "requirement_text": _safe_text(requirement_text),
        "pdf_path": _safe_text(pdf_path),
        "source_file_name": _safe_text(source_file_name),
        "extra": extra if isinstance(extra, dict) else {},
        "updated_at": _now_ts(),
    }


def _json_dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _json_loads(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None

    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")

    if isinstance(raw, dict):
        return raw

    text = _safe_text(raw)
    if not text:
        return None

    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


async def save_requirement(
    workflow_id: str,
    requirement_id: str,
    requirement_text: str = "",
    pdf_path: str = "",
    source_file_name: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    新版统一保存入口。
    """
    r = get_redis()
    payload = _normalize_payload(
        workflow_id=workflow_id,
        requirement_id=requirement_id,
        requirement_text=requirement_text,
        pdf_path=pdf_path,
        source_file_name=source_file_name,
        extra=extra,
    )
    await r.set(_req_key(workflow_id, requirement_id), _json_dumps(payload), ex=REQ_TTL_SEC)

    logger.info(
        "[requirement_store.save_requirement] saved | workflow_id=%s | requirement_id=%s | text_len=%s | has_pdf_path=%s | pdf_path=%s",
        workflow_id,
        requirement_id,
        len(payload["requirement_text"]),
        bool(payload["pdf_path"]),
        payload["pdf_path"],
    )
    return payload


async def get_requirement(workflow_id: str, requirement_id: str) -> Optional[Dict[str, Any]]:
    """
    统一读取入口。
    兼容老数据：
    - 如果 redis 里存的是纯文本，则自动包装成新结构返回
    """
    r = get_redis()
    raw = await r.get(_req_key(workflow_id, requirement_id))
    if raw is None:
        return None

    parsed = _json_loads(raw)
    if isinstance(parsed, dict):
        payload = _normalize_payload(
            workflow_id=parsed.get("workflow_id") or workflow_id,
            requirement_id=parsed.get("requirement_id") or requirement_id,
            requirement_text=parsed.get("requirement_text", ""),
            pdf_path=parsed.get("pdf_path", ""),
            source_file_name=parsed.get("source_file_name", ""),
            extra=parsed.get("extra") if isinstance(parsed.get("extra"), dict) else {},
        )
        if "updated_at" in parsed:
            payload["updated_at"] = parsed["updated_at"]
        return payload

    # 兼容老版本纯文本
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")
    text = _safe_text(raw)
    if not text:
        return None

    return _normalize_payload(
        workflow_id=workflow_id,
        requirement_id=requirement_id,
        requirement_text=text,
        pdf_path="",
        source_file_name="",
        extra={},
    )


async def save_requirement_text(workflow_id: str, requirement_id: str, text: str) -> None:
    """
    兼容旧调用：只保存文本
    """
    await save_requirement(
        workflow_id=workflow_id,
        requirement_id=requirement_id,
        requirement_text=text or "",
    )


async def load_requirement_text(workflow_id: str, requirement_id: str) -> Optional[str]:
    """
    兼容旧调用：只读取文本
    """
    payload = await get_requirement(workflow_id, requirement_id)
    if not payload:
        return None
    text = _safe_text(payload.get("requirement_text"))
    return text or None


async def save_requirement_pdf_path(
    workflow_id: str,
    requirement_id: str,
    pdf_path: str,
    source_file_name: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    仅更新 pdf_path，但会保留已有 requirement_text。
    """
    existing = await get_requirement(workflow_id, requirement_id) or {}
    return await save_requirement(
        workflow_id=workflow_id,
        requirement_id=requirement_id,
        requirement_text=_safe_text(existing.get("requirement_text")),
        pdf_path=pdf_path,
        source_file_name=source_file_name or _safe_text(existing.get("source_file_name")),
        extra=extra or existing.get("extra") or {},
    )


async def load_requirement_pdf_path(workflow_id: str, requirement_id: str) -> Optional[str]:
    payload = await get_requirement(workflow_id, requirement_id)
    if not payload:
        return None
    pdf_path = _safe_text(payload.get("pdf_path"))
    return pdf_path or None


async def upsert_requirement(
    workflow_id: str,
    requirement_id: str,
    requirement_text: Optional[str] = None,
    pdf_path: Optional[str] = None,
    source_file_name: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    增量更新：
    - 传了什么字段就更新什么
    - 没传的字段保留旧值
    """
    existing = await get_requirement(workflow_id, requirement_id) or {}

    return await save_requirement(
        workflow_id=workflow_id,
        requirement_id=requirement_id,
        requirement_text=(
            _safe_text(requirement_text)
            if requirement_text is not None
            else _safe_text(existing.get("requirement_text"))
        ),
        pdf_path=(
            _safe_text(pdf_path)
            if pdf_path is not None
            else _safe_text(existing.get("pdf_path"))
        ),
        source_file_name=(
            _safe_text(source_file_name)
            if source_file_name is not None
            else _safe_text(existing.get("source_file_name"))
        ),
        extra=extra if extra is not None else existing.get("extra") or {},
    )


async def delete_requirement(workflow_id: str, requirement_id: str) -> None:
    r = get_redis()
    await r.delete(_req_key(workflow_id, requirement_id))
    logger.info(
        "[requirement_store.delete_requirement] deleted | workflow_id=%s | requirement_id=%s",
        workflow_id,
        requirement_id,
    )