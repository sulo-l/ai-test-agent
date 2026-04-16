# -*- coding: utf-8 -*-
"""
File Store Module
========================
提供文件存储相关的操作，如文件的保存、删除和检查。

✅ 改动要点：
- 保持原有 save_file(file_id, content) -> file_path 兼容（默认 ext=xlsx）
- 新增 save_bytes(content, file_id=None, ext="xlsx") -> file_id（导出场景）
- 新增 save_path(path, file_id=None, ext="xlsx") -> file_id
- get_file_path_by_id 支持 ext 参数（默认 xlsx）
- 规范化 file_id / ext，避免路径注入
"""

import os
import re
import uuid
from typing import Optional, List

from app.settings import TMP_DIR

# 允许的 file_id 字符（其余全部替换为 _）
_FILE_ID_SAFE_RE = re.compile(r"[^a-zA-Z0-9_\-]")
# 允许的扩展名（只允许字母数字）
_EXT_SAFE_RE = re.compile(r"[^a-zA-Z0-9]")


def _normalize_file_id(file_id: Optional[str]) -> str:
    fid = (file_id or "").strip()
    if not fid:
        return uuid.uuid4().hex
    fid = _FILE_ID_SAFE_RE.sub("_", fid)
    # 防止过长
    if len(fid) > 128:
        fid = fid[:128]
    # 防止全是下划线之类的空值
    if not fid.strip("_-"):
        fid = uuid.uuid4().hex
    return fid


def _normalize_ext(ext: Optional[str]) -> str:
    """
    ext 白名单化，防止 ../ 等路径注入
    """
    e = (ext or "xlsx").strip().lstrip(".")
    e = _EXT_SAFE_RE.sub("", e)  # 只保留字母数字
    if not e:
        e = "xlsx"
    # 你项目主要是 xlsx，这里不做强制白名单，但确保无路径字符
    if len(e) > 16:
        e = e[:16]
    return e.lower()


def _ensure_tmp_dir() -> None:
    os.makedirs(TMP_DIR, exist_ok=True)


def get_file_path_by_id(file_id: str, ext: str = "xlsx") -> str:
    """
    根据文件 ID 获取文件路径，默认 ext=xlsx
    文件位于 TMP_DIR 下，文件名为 {file_id}.{ext}
    """
    _ensure_tmp_dir()
    fid = _normalize_file_id(file_id)
    e = _normalize_ext(ext)
    return os.path.join(TMP_DIR, f"{fid}.{e}")


def save_file(file_id: str, content: bytes, ext: str = "xlsx") -> str:
    """
    ✅ 兼容保留：根据给定的 file_id 保存文件内容
    返回保存的文件路径（旧逻辑）

    - 旧调用方：save_file(file_id, content) 仍然可用（默认 ext=xlsx）
    - 新调用方：可显式传 ext
    """
    if content is None:
        raise RuntimeError("Failed to save file: content is None")

    fid = _normalize_file_id(file_id)
    file_path = get_file_path_by_id(fid, ext=ext)

    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    try:
        with open(file_path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise RuntimeError(f"Failed to save file: {str(e)}")

    return file_path


def save_bytes(content: bytes, file_id: Optional[str] = None, ext: str = "xlsx") -> str:
    """
    ✅ 推荐新接口：保存 bytes，并返回 file_id（用于下载/引用）
    """
    if content is None:
        raise RuntimeError("Failed to save file: content is None")

    fid = _normalize_file_id(file_id)
    file_path = get_file_path_by_id(fid, ext=ext)

    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    try:
        with open(file_path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise RuntimeError(f"Failed to save file: {str(e)}")

    return fid


def save_path(path: str, file_id: Optional[str] = None, ext: str = "xlsx") -> str:
    """
    ✅ 推荐新接口：把一个已存在的本地文件保存到 store，并返回 file_id
    """
    if not path:
        raise RuntimeError("Failed to save file: path is empty")
    if not os.path.exists(path):
        raise RuntimeError(f"Failed to save file: path not exists: {path}")

    fid = _normalize_file_id(file_id)
    dst = get_file_path_by_id(fid, ext=ext)

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        with open(path, "rb") as src_f:
            data = src_f.read()
        with open(dst, "wb") as dst_f:
            dst_f.write(data)
    except Exception as e:
        raise RuntimeError(f"Failed to save file: {str(e)}")

    return fid


def read_file(file_id: str, ext: str = "xlsx") -> bytes:
    """
    读取文件内容（bytes）
    """
    fid = _normalize_file_id(file_id)
    file_path = get_file_path_by_id(fid, ext=ext)
    if not os.path.exists(file_path):
        raise RuntimeError("File not found")
    with open(file_path, "rb") as f:
        return f.read()


def delete_file(file_id: str, ext: str = "xlsx") -> bool:
    """
    删除指定 file_id 的文件
    """
    fid = _normalize_file_id(file_id)
    file_path = get_file_path_by_id(fid, ext=ext)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            return True
        except Exception as e:
            raise RuntimeError(f"Failed to delete file: {str(e)}")
    return False


def file_exists(file_id: str, ext: str = "xlsx") -> bool:
    """
    检查指定 file_id 对应的文件是否存在
    """
    fid = _normalize_file_id(file_id)
    file_path = get_file_path_by_id(fid, ext=ext)
    return os.path.exists(file_path)


def list_files(ext: str = "xlsx") -> List[str]:
    """
    列出存储目录下所有指定扩展名文件，返回 file_id 列表（去掉扩展名）
    """
    _ensure_tmp_dir()
    e = _normalize_ext(ext)
    out: List[str] = []
    try:
        for f in os.listdir(TMP_DIR):
            full = os.path.join(TMP_DIR, f)
            if not os.path.isfile(full):
                continue
            if not f.lower().endswith("." + e):
                continue
            out.append(os.path.splitext(f)[0])
        return out
    except Exception as ex:
        raise RuntimeError(f"Failed to list files: {str(ex)}")