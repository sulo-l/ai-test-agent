# app/services/workflow_store.py
# -*- coding: utf-8 -*-

import os
import json
import logging
from dataclasses import asdict, is_dataclass
from typing import Optional, Any, Dict

from filelock import FileLock

from app.settings import TMP_DIR
from app.workflow.models import WorkflowTask

logger = logging.getLogger(__name__)

WORKFLOW_STORE_DIR = os.path.join(TMP_DIR, "workflow_store")
os.makedirs(WORKFLOW_STORE_DIR, exist_ok=True)


def _normalize_workflow_id(workflow_id: str) -> str:
    value = str(workflow_id or "").strip()
    if not value:
        raise ValueError("workflow_id is empty")
    return value


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _path(workflow_id: str) -> str:
    safe_workflow_id = _normalize_workflow_id(workflow_id)
    return os.path.join(WORKFLOW_STORE_DIR, f"{safe_workflow_id}.json")


def _lock_path(path: str) -> str:
    return f"{path}.lock"


def _read_json_locked(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None

    lock = FileLock(_lock_path(path))
    with lock:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    """
    原子写入 + 文件锁
    防止多个 worker 同时写入 json 导致文件损坏
    """
    _ensure_parent_dir(path)
    lock = FileLock(_lock_path(path))

    with lock:
        tmp_path = f"{path}.tmp"

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(tmp_path, path)


def _safe_prepared_requirement_dict(prepared: Any) -> Optional[Dict[str, Any]]:
    if prepared is None:
        return None

    if is_dataclass(prepared):
        raw = asdict(prepared)
    elif isinstance(prepared, dict):
        raw = dict(prepared)
    else:
        return None

    pages_raw = raw.get("pages", []) if isinstance(raw.get("pages"), list) else []
    page_images_raw = raw.get("page_images", []) if isinstance(raw.get("page_images"), list) else []

    safe_pages = []
    for item in pages_raw:
        if not isinstance(item, dict):
            continue

        safe_pages.append(
            {
                "page": int(item.get("page") or 0),
                "confirmed_text": str(item.get("confirmed_text") or ""),
                "ocr_text": str(item.get("ocr_text") or ""),
                "confidence": str(item.get("confidence") or "").strip(),
                "source": str(item.get("source") or "").strip(),
                "width": item.get("width"),
                "height": item.get("height"),
                "dpi": item.get("dpi"),
                "image_path": item.get("image_path"),
                "has_image": bool(item.get("has_image")),
                "image_like": bool(item.get("image_like")),
            }
        )

    safe_page_images = []
    for item in page_images_raw:
        if not isinstance(item, dict):
            continue

        safe_page_images.append(
            {
                "page": int(item.get("page") or 0),
                "dpi": int(item.get("dpi") or 0),
                "width": int(item.get("width") or 0),
                "height": int(item.get("height") or 0),
                "image_path": str(item.get("image_path") or "").strip() or None,
                "has_image": bool(item.get("image") is not None or item.get("has_image")),
            }
        )

    return {
        "final_text": str(raw.get("final_text") or "").strip(),
        "clean_sentences": raw.get("clean_sentences", []) if isinstance(raw.get("clean_sentences"), list) else [],
        "requirement_blocks": raw.get("requirement_blocks", []) if isinstance(raw.get("requirement_blocks"), list) else [],
        "pages": safe_pages,
        "confirmed_text": str(raw.get("confirmed_text") or "").strip(),
        "ocr_text": str(raw.get("ocr_text") or "").strip() or None,
        "page_images": safe_page_images,
        "usable_for_ai": bool(raw.get("usable_for_ai", False)),
        "confidence": str(raw.get("confidence") or "LOW").strip() or "LOW",
        "requirement_id": str(raw.get("requirement_id") or "").strip() or None,
        "total_pages": int(raw.get("total_pages") or 0),
        "text_pages": int(raw.get("text_pages") or 0),
        "ocr_pages": int(raw.get("ocr_pages") or 0),
        "image_like_pages": raw.get("image_like_pages", []) if isinstance(raw.get("image_like_pages"), list) else [],
    }


def _normalize_pdf_path(pdf_path: Optional[str]) -> Optional[str]:
    value = str(pdf_path or "").strip()
    return value or None


def _normalize_requirement_id(requirement_id: Optional[str]) -> Optional[str]:
    value = str(requirement_id or "").strip()
    return value or None


def _merge_obj(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base or {})
    for k, v in (patch or {}).items():
        out[k] = v
    return out


def _load_or_init_workflow_obj(workflow_id: str) -> Dict[str, Any]:
    safe_workflow_id = _normalize_workflow_id(workflow_id)
    obj = load_workflow_object(safe_workflow_id)
    if not isinstance(obj, dict):
        obj = {"workflow_id": safe_workflow_id}
    else:
        obj["workflow_id"] = safe_workflow_id
    return obj


def load_workflow_object(workflow_id: str) -> Optional[dict]:
    path = _path(workflow_id)
    try:
        obj = _read_json_locked(path)
        if obj is None:
            return None
        return obj if isinstance(obj, dict) else None
    except Exception:
        logger.exception("load_workflow_object failed | workflow_id=%s path=%s", workflow_id, path)
        return None


def save_workflow_object(task: WorkflowTask) -> bool:
    workflow_id = _normalize_workflow_id(getattr(task, "workflow_id", ""))

    data = {
        "workflow_id": workflow_id,
        "pdf_path": _normalize_pdf_path(getattr(task, "pdf_path", "") or None),
        "pdf_text": str(getattr(task, "pdf_text", "") or ""),
        "focus_requirements": getattr(task, "focus_requirements", None),
        "requirement_id": _normalize_requirement_id(getattr(task, "requirement_id", "") or None),
        "stage": getattr(getattr(task, "stage", None), "value", None),
    }

    prepared = getattr(task, "prepared_requirement", None)
    if prepared is not None:
        data["prepared_requirement"] = _safe_prepared_requirement_dict(prepared)

        # prepared 里带 requirement_id 时做兜底
        prepared_dict = data.get("prepared_requirement") or {}
        if not data.get("requirement_id") and isinstance(prepared_dict, dict):
            data["requirement_id"] = _normalize_requirement_id(prepared_dict.get("requirement_id"))

    _atomic_write_json(_path(workflow_id), data)

    logger.info(
        "save_workflow_object ok | workflow_id=%s pdf_path=%s pdf_text_len=%s requirement_id=%s has_prepared=%s",
        workflow_id,
        data.get("pdf_path"),
        len(data.get("pdf_text") or ""),
        data.get("requirement_id"),
        bool(data.get("prepared_requirement")),
    )
    return True


def write_workflow_fields(workflow_id: str, **kwargs) -> bool:
    safe_workflow_id = _normalize_workflow_id(workflow_id)
    obj = _load_or_init_workflow_obj(safe_workflow_id)

    for k, v in kwargs.items():
        if k == "prepared_requirement":
            obj[k] = _safe_prepared_requirement_dict(v)

            prepared_dict = obj.get(k) or {}
            if isinstance(prepared_dict, dict) and not obj.get("requirement_id"):
                obj["requirement_id"] = _normalize_requirement_id(prepared_dict.get("requirement_id"))

        elif k == "pdf_path":
            obj[k] = _normalize_pdf_path(v)

        elif k == "requirement_id":
            obj[k] = _normalize_requirement_id(v)

        elif k == "pdf_text":
            obj[k] = str(v or "")

        else:
            obj[k] = v

    _atomic_write_json(_path(safe_workflow_id), obj)

    logger.info(
        "write_workflow_fields ok | workflow_id=%s keys=%s pdf_path=%s requirement_id=%s has_prepared=%s",
        safe_workflow_id,
        list(kwargs.keys()),
        obj.get("pdf_path"),
        obj.get("requirement_id"),
        bool(obj.get("prepared_requirement")),
    )
    return True


def write_pdf_path(workflow_id: str, pdf_path: str) -> bool:
    safe_workflow_id = _normalize_workflow_id(workflow_id)
    obj = _load_or_init_workflow_obj(safe_workflow_id)

    obj["pdf_path"] = _normalize_pdf_path(pdf_path)

    _atomic_write_json(_path(safe_workflow_id), obj)

    logger.info(
        "write_pdf_path ok | workflow_id=%s pdf_path=%s",
        safe_workflow_id,
        obj.get("pdf_path"),
    )
    return True


def write_requirement_text(
    workflow_id: str,
    text: str,
    requirement_id: str,
    pdf_path: Optional[str] = None,
    prepared_requirement: Any = None,
) -> bool:
    safe_workflow_id = _normalize_workflow_id(workflow_id)
    obj = _load_or_init_workflow_obj(safe_workflow_id)

    obj.update(
        {
            "pdf_text": str(text or ""),
            "requirement_id": _normalize_requirement_id(requirement_id),
        }
    )

    if pdf_path is not None:
        obj["pdf_path"] = _normalize_pdf_path(pdf_path)

    if prepared_requirement is not None:
        prepared_dict = _safe_prepared_requirement_dict(prepared_requirement)
        obj["prepared_requirement"] = prepared_dict

        if isinstance(prepared_dict, dict):
            if not obj.get("requirement_id"):
                obj["requirement_id"] = _normalize_requirement_id(prepared_dict.get("requirement_id"))

            # 若外部没传 pdf_path，尽量保留原 pdf_path，不做清空
            if not obj.get("pdf_path"):
                # prepared 里通常没有 pdf_path，本处仅保留现有 obj["pdf_path"]
                pass

    _atomic_write_json(_path(safe_workflow_id), obj)

    logger.info(
        "write_requirement_text ok | workflow_id=%s pdf_text_len=%s requirement_id=%s pdf_path=%s has_prepared=%s",
        safe_workflow_id,
        len(obj.get("pdf_text") or ""),
        obj.get("requirement_id"),
        obj.get("pdf_path"),
        bool(obj.get("prepared_requirement")),
    )
    return True


def write_prepared_requirement(
    workflow_id: str,
    prepared_requirement: Any,
    *,
    pdf_path: Optional[str] = None,
    requirement_id: Optional[str] = None,
    pdf_text: Optional[str] = None,
) -> bool:
    """
    强化版：
    - 不只写 prepared_requirement
    - 可选同步补写 pdf_path / requirement_id / pdf_text
    - 避免出现“prepared_requirement 已有，但 pdf_path 缺失”
    """
    safe_workflow_id = _normalize_workflow_id(workflow_id)
    obj = _load_or_init_workflow_obj(safe_workflow_id)

    prepared_dict = _safe_prepared_requirement_dict(prepared_requirement)
    obj["prepared_requirement"] = prepared_dict

    if pdf_path is not None:
        obj["pdf_path"] = _normalize_pdf_path(pdf_path)

    if requirement_id is not None:
        obj["requirement_id"] = _normalize_requirement_id(requirement_id)
    elif not obj.get("requirement_id") and isinstance(prepared_dict, dict):
        obj["requirement_id"] = _normalize_requirement_id(prepared_dict.get("requirement_id"))

    if pdf_text is not None:
        obj["pdf_text"] = str(pdf_text or "")
    else:
        # 没有外部显式传 pdf_text 时，尽量从 prepared 里兜底生成
        if not str(obj.get("pdf_text") or "").strip() and isinstance(prepared_dict, dict):
            best_text = (
                str(prepared_dict.get("final_text") or "").strip()
                or str(prepared_dict.get("confirmed_text") or "").strip()
                or str(prepared_dict.get("ocr_text") or "").strip()
            )
            if best_text:
                obj["pdf_text"] = best_text

    _atomic_write_json(_path(safe_workflow_id), obj)

    logger.info(
        "write_prepared_requirement ok | workflow_id=%s pdf_path=%s requirement_id=%s pdf_text_len=%s has_prepared=%s",
        safe_workflow_id,
        obj.get("pdf_path"),
        obj.get("requirement_id"),
        len(str(obj.get("pdf_text") or "")),
        bool(obj.get("prepared_requirement")),
    )
    return True


def write_requirement_bundle(
    workflow_id: str,
    *,
    requirement_id: str,
    pdf_text: str,
    pdf_path: Optional[str] = None,
    prepared_requirement: Any = None,
    focus_requirements: Any = None,
    stage: Optional[str] = None,
) -> bool:
    safe_workflow_id = _normalize_workflow_id(workflow_id)
    obj = _load_or_init_workflow_obj(safe_workflow_id)

    obj["requirement_id"] = _normalize_requirement_id(requirement_id)
    obj["pdf_text"] = str(pdf_text or "")

    if pdf_path is not None:
        obj["pdf_path"] = _normalize_pdf_path(pdf_path)

    if prepared_requirement is not None:
        obj["prepared_requirement"] = _safe_prepared_requirement_dict(prepared_requirement)

    if focus_requirements is not None:
        obj["focus_requirements"] = focus_requirements

    if stage is not None:
        obj["stage"] = stage

    _atomic_write_json(_path(safe_workflow_id), obj)

    logger.info(
        "write_requirement_bundle ok | workflow_id=%s pdf_path=%s requirement_id=%s pdf_text_len=%s has_prepared=%s",
        safe_workflow_id,
        obj.get("pdf_path"),
        obj.get("requirement_id"),
        len(str(obj.get("pdf_text") or "")),
        bool(obj.get("prepared_requirement")),
    )
    return True


def get_requirement_text_by_workflow_id(workflow_id: str) -> str:
    obj = load_workflow_object(workflow_id)
    if not obj:
        return ""
    return str(obj.get("pdf_text") or "")


def get_pdf_path_by_workflow_id(workflow_id: str) -> str:
    obj = load_workflow_object(workflow_id)
    if not obj:
        return ""
    return str(obj.get("pdf_path") or "").strip()


def get_prepared_requirement_by_workflow_id(workflow_id: str) -> Optional[dict]:
    obj = load_workflow_object(workflow_id)
    if not obj:
        return None
    prepared = obj.get("prepared_requirement")
    return prepared if isinstance(prepared, dict) else None