#! /usr/bin/python3
# coding=utf-8

from __future__ import annotations

import os
import json
from typing import Optional, Any, Dict

import requests


BASE_URL = "http://127.0.0.1:8000"

PDF_PATH = "/Users/liushengtao/Downloads/理财页面新增“年化收益概览”+“理财参与流程”.pdf"
REQUIREMENT_ID = "www-999888888"

HTTP_TIMEOUT = 60
UPLOAD_TIMEOUT = 180
STREAM_TIMEOUT = 1800


# =====================================================
# HTTP helpers
# =====================================================

def _post_json(url: str, payload: dict, timeout: int = HTTP_TIMEOUT) -> dict:
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _get_json(url: str, params: Optional[dict] = None, timeout: int = HTTP_TIMEOUT) -> dict:
    r = requests.get(url, params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _post_multipart(url: str, files: dict, data: dict, timeout: int = UPLOAD_TIMEOUT) -> dict:
    r = requests.post(url, files=files, data=data, timeout=timeout)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


# =====================================================
# Utils
# =====================================================

def _pretty(title: str, payload: Any) -> None:
    print(f"\n=== {title} ===")
    try:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    except Exception:
        print(payload)


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


# =====================================================
# Workflow
# =====================================================

def create_workflow() -> str:

    url = f"{BASE_URL}/workflow/create"

    r = requests.post(url, timeout=30)
    r.raise_for_status()

    payload = r.json()

    _pretty("create workflow", payload)

    workflow_id = (
        payload.get("workflow_id")
        or _safe_dict(payload.get("data")).get("workflow_id")
    )

    if not workflow_id:
        raise RuntimeError(f"workflow_id missing: {payload}")

    workflow_id = str(workflow_id).strip()

    print("workflow_id =", workflow_id)

    return workflow_id


def upload_pdf(workflow_id: str) -> dict:

    url = f"{BASE_URL}/workflow/upload-pdf"

    with open(PDF_PATH, "rb") as f:

        files = {
            "file": (os.path.basename(PDF_PATH), f, "application/pdf")
        }

        data = {
            "workflow_id": workflow_id,
            "requirement_id": REQUIREMENT_ID,
        }

        payload = _post_multipart(url, files, data)

    _pretty("upload pdf", payload)

    return payload


# =====================================================
# Analysis
# =====================================================

def start_analysis(workflow_id: str) -> str:

    url = f"{BASE_URL}/analysis/run"

    payload = {
        "workflow_id": workflow_id,
        "requirement_id": REQUIREMENT_ID,
    }

    resp = _post_json(url, payload)

    _pretty("analysis run", resp)

    stream_id = (
        resp.get("stream_id")
        or _safe_dict(resp.get("data")).get("stream_id")
    )

    if not stream_id:
        raise RuntimeError(f"stream_id missing: {resp}")

    stream_id = str(stream_id).strip()

    print("stream_id =", stream_id)

    return stream_id


def get_stream_status(stream_id: str) -> dict:

    url = f"{BASE_URL}/analysis/stream/status"

    payload = _get_json(url, params={"stream_id": stream_id})

    _pretty("stream status", payload)

    return payload


# =====================================================
# SSE
# =====================================================

def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


def _print_stage(payload: dict) -> None:

    stage = payload.get("stage")
    message = payload.get("message")

    print(f"[STAGE] {stage} | {message}")


def _print_issue(payload: dict) -> None:

    print(
        "[ISSUE]",
        payload.get("level"),
        payload.get("category"),
        payload.get("title"),
    )


def _print_score(payload: dict) -> None:

    print(
        "[SCORE]",
        payload.get("score"),
        payload.get("qualityLevel"),
    )


def listen_sse(stream_id: str) -> bool:

    url = f"{BASE_URL}/analysis/stream"

    print("\nConnecting SSE:", stream_id)

    status_payload = get_stream_status(stream_id)

    if not status_payload.get("ok"):
        raise RuntimeError(f"invalid stream_id: {status_payload}")

    with requests.get(
        url,
        params={"stream_id": stream_id},
        stream=True,
        timeout=STREAM_TIMEOUT,
    ) as r:

        r.raise_for_status()

        event = None
        data_lines = []

        for raw in r.iter_lines(decode_unicode=True):

            if raw is None:
                continue

            line = raw.strip()

            if line == "":

                if data_lines:

                    text = "\n".join(data_lines)

                    payload = _parse_json(text)

                    ev = event or payload.get("type")

                    if ev == "stage":
                        _print_stage(payload)

                    elif ev == "issue":
                        _print_issue(payload)

                    elif ev == "score":
                        _print_score(payload)

                    elif ev == "done":

                        print("\n=== STREAM DONE ===")

                        return True

                    elif ev == "error":

                        print("\n=== STREAM ERROR ===")

                        _pretty("error", payload)

                        return False

                event = None
                data_lines = []

                continue

            if line.startswith("event:"):
                event = line[6:].strip()
                continue

            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
                continue

    print("SSE ended")

    return False


# =====================================================
# Result
# =====================================================

def get_result(workflow_id: str) -> dict:

    url = f"{BASE_URL}/analysis/result"

    params = {
        "workflow_id": workflow_id,
        "requirement_id": REQUIREMENT_ID,
    }

    payload = _get_json(url, params)

    _pretty("analysis result", payload)

    return payload


# =====================================================
# Summary（完全对齐 router）
# =====================================================

def print_summary(payload: dict) -> None:

    result = _safe_dict(payload.get("result"))

    overview = _safe_dict(result.get("overview"))
    statistics = _safe_dict(result.get("statistics"))
    panels = _safe_dict(result.get("panels"))

    risk = _safe_dict(panels.get("risk"))
    review = _safe_dict(panels.get("review"))
    coverage = _safe_dict(panels.get("coverage"))

    overall = _safe_dict(review.get("overallReview"))

    print("\n====== SUMMARY ======")

    print("score:", overview.get("score"))
    print("qualityLevel:", overview.get("qualityLevel"))
    print("decision:", overview.get("decision"))
    print("passed:", overview.get("passed"))

    print("issues:", statistics.get("totalIssues"))
    print("highCount:", statistics.get("highCount"))
    print("mediumCount:", statistics.get("mediumCount"))
    print("lowCount:", statistics.get("lowCount"))

    print("durationMs:", overview.get("durationMs"))

    print("risk summary:", risk.get("riskSummary"))

    print("review quality:", overall.get("quality"))
    print("should_refine:", overall.get("shouldRefine"))

    print("coverage_score:", coverage.get("coverage_score"))

    print("summary:", overview.get("summary"))


# =====================================================
# Main
# =====================================================

def main() -> None:

    if not os.path.exists(PDF_PATH):
        raise FileNotFoundError(PDF_PATH)

    workflow_id = create_workflow()

    upload_pdf(workflow_id)

    stream_id = start_analysis(workflow_id)

    stream_ok = listen_sse(stream_id)

    result = get_result(workflow_id)

    print_summary(result)

    if not stream_ok:
        raise RuntimeError("analysis stream failed")


if __name__ == "__main__":
    main()