#! /usr/bin/python3
# coding=utf-8

from __future__ import annotations

import os
import json
import time
import signal
import sys
from typing import Optional, Any, Dict, Tuple

import requests


BASE_URL = "http://127.0.0.1:8000"

PDF_PATH = "/Users/liushengtao/Downloads/理财页面新增“年化收益概览”+“理财参与流程”.pdf"
REQUIREMENT_ID = "www-99"

HTTP_TIMEOUT = 18000
UPLOAD_TIMEOUT = 1800

# SSE 连接超时 / 重试配置
SSE_CONNECT_TIMEOUT = 10
SSE_READ_TIMEOUT = None
SSE_MAX_RETRY = 5
SSE_RETRY_SLEEP_SEC = 2

# 是否复用上游结果
USE_ANALYSIS_RESULT = True
USE_TESTCASE_RESULT = True
FORCE_REFRESH = True

_STOP = False


# =====================================================
# Signal
# =====================================================

def _handle_stop_signal(signum, frame) -> None:
    global _STOP
    _STOP = True
    print(f"\n[STOP] received signal={signum}, preparing to exit...")


signal.signal(signal.SIGINT, _handle_stop_signal)
signal.signal(signal.SIGTERM, _handle_stop_signal)


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


def _pick_first_str(*values: Any, default: str = "") -> str:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


def _now_str() -> str:
    return time.strftime("%H:%M:%S")


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


def _unwrap_strategy_result(payload: dict) -> Dict[str, Any]:
    """
    兼容：
    1. {result: {...}}
    2. {result: {result: {...}}}
    3. {...}
    """
    payload = _safe_dict(payload)

    result = _safe_dict(payload.get("result"))
    if result:
        inner = _safe_dict(result.get("result"))
        if inner:
            return inner
        return result

    return payload


def _request_timeout_for_sse() -> Tuple[int, Optional[int]]:
    # requests 支持 timeout=(connect_timeout, read_timeout)
    # SSE 长连接 read_timeout 必须 None，否则会因为长时间无业务事件而 ReadTimeout
    return (SSE_CONNECT_TIMEOUT, SSE_READ_TIMEOUT)


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
# Strategy Context / Run
# =====================================================

def get_strategy_context(workflow_id: str) -> dict:
    url = f"{BASE_URL}/strategy/context"

    payload = _get_json(
        url,
        params={"workflow_id": workflow_id},
    )

    _pretty("strategy context", payload)
    return payload


def start_strategy(workflow_id: str) -> dict:
    url = f"{BASE_URL}/strategy/run"

    payload = {
        "workflow_id": workflow_id,
        "requirement_id": REQUIREMENT_ID,
        "force_refresh": FORCE_REFRESH,
        "use_analysis_result": USE_ANALYSIS_RESULT,
        "use_testcase_result": USE_TESTCASE_RESULT,
    }

    resp = _post_json(url, payload)

    _pretty("strategy run", resp)

    stream_id = _pick_first_str(resp.get("stream_id"))
    job_id = _pick_first_str(resp.get("job_id"))

    if not stream_id:
        raise RuntimeError(f"strategy stream_id missing: {resp}")

    if not job_id:
        raise RuntimeError(f"strategy job_id missing: {resp}")

    print("strategy job_id =", job_id)
    print("strategy stream_id =", stream_id)

    return {
        "job_id": job_id,
        "stream_id": stream_id,
        "raw": resp,
    }


def get_strategy_stream_status(stream_id: str) -> dict:
    url = f"{BASE_URL}/strategy/stream/status"

    payload = _get_json(url, params={"stream_id": stream_id})

    _pretty("strategy stream status", payload)

    return payload


def get_strategy_status(workflow_id: str) -> dict:
    url = f"{BASE_URL}/strategy/status"

    payload = _get_json(url, params={"workflow_id": workflow_id})

    _pretty("strategy status", payload)

    return payload


def cancel_strategy(workflow_id: str) -> Optional[dict]:
    """
    如果后端有 cancel 接口就调用；没有就安静跳过，不影响现有脚本结构
    """
    url = f"{BASE_URL}/strategy/cancel"
    payload = {"workflow_id": workflow_id}

    try:
        resp = _post_json(url, payload, timeout=30)
        _pretty("strategy cancel", resp)
        return resp
    except Exception as e:
        print(f"[WARN] strategy cancel skipped: {e}")
        return None


# =====================================================
# SSE
# =====================================================

def _print_stage(payload: dict) -> None:
    stage = payload.get("stage")
    status = payload.get("status")
    title = payload.get("title")
    message = payload.get("message")
    progress = payload.get("progress")

    print(
        f"[{_now_str()}] [STAGE] "
        f"stage={stage} status={status} progress={progress} title={title} message={message}"
    )


def _print_metric(payload: dict) -> None:
    print(f"[{_now_str()}] [METRIC]", payload.get("name"), payload.get("value"))


def _print_result(payload: dict) -> None:
    result = _unwrap_strategy_result(payload)
    summary = _safe_dict(result.get("summary"))
    quality_gate = _safe_dict(result.get("quality_gate"))

    print(f"[{_now_str()}] [RESULT] title =", summary.get("title"))
    print(f"[{_now_str()}] [RESULT] business_domain =", summary.get("business_domain"))
    print(f"[{_now_str()}] [RESULT] change_scope =", summary.get("change_scope"))
    print(f"[{_now_str()}] [RESULT] overall_risk =", summary.get("overall_risk"))
    print(f"[{_now_str()}] [RESULT] quality_gate =", quality_gate.get("decision"))


def _consume_sse_response(r: requests.Response) -> Tuple[bool, bool]:
    """
    返回:
    - stream_ok: 本次流是否正常结束
    - should_stop: 是否已经拿到最终结果/终止信号
    """
    event = None
    data_lines = []

    for raw in r.iter_lines(decode_unicode=True):
        if _STOP:
            print(f"[{_now_str()}] [STOP] stopping SSE consume")
            return False, True

        if raw is None:
            continue

        line = raw.strip()

        # 空行：SSE 事件分隔
        if line == "":
            if data_lines:
                text = "\n".join(data_lines)
                payload = _parse_json(text)
                ev = event or payload.get("type")

                if ev == "stage":
                    _print_stage(payload)

                elif ev == "metric":
                    _print_metric(payload)

                elif ev == "result":
                    _print_result(payload)

                elif ev == "error":
                    print("\n=== STRATEGY STREAM ERROR ===")
                    _pretty("strategy error", payload)
                    return False, True

                elif ev == "connected":
                    print(f"[{_now_str()}] [CONNECTED]", payload.get("message"))

                elif ev == "ping":
                    # 心跳包，不打印，避免刷屏
                    pass

                # 收到最终阶段直接结束
                stage = _pick_first_str(payload.get("stage"))
                if stage in {"DONE", "RESULT_READY", "ERROR", "CANCELLED"}:
                    if stage == "ERROR":
                        return False, True

                    print("\n=== STRATEGY STREAM END ===")
                    return True, True

            event = None
            data_lines = []
            continue

        if line.startswith("event:"):
            event = line[6:].strip()
            continue

        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
            continue

    return True, False


def listen_strategy_sse(stream_id: str) -> bool:
    url = f"{BASE_URL}/strategy/stream"

    print("\nConnecting strategy SSE:", stream_id)

    status_payload = get_strategy_stream_status(stream_id)

    if not status_payload.get("ok"):
        raise RuntimeError(f"invalid strategy stream_id: {status_payload}")

    retry = 0
    final_ok = False

    while retry < SSE_MAX_RETRY:
        if _STOP:
            print(f"[{_now_str()}] [STOP] SSE listen aborted before connect")
            return False

        try:
            print(f"[{_now_str()}] [SSE] connect attempt={retry + 1}/{SSE_MAX_RETRY}")

            with requests.get(
                url,
                params={"stream_id": stream_id},
                stream=True,
                timeout=_request_timeout_for_sse(),
            ) as r:
                r.raise_for_status()

                stream_ok, should_stop = _consume_sse_response(r)
                if should_stop:
                    final_ok = stream_ok
                    break

                # 非预期断开，进入重试
                retry += 1
                print(f"[{_now_str()}] [WARN] strategy SSE disconnected unexpectedly, retry={retry}")

        except requests.exceptions.ReadTimeout:
            retry += 1
            print(f"[{_now_str()}] [WARN] strategy SSE read timeout, retry={retry}")

        except requests.exceptions.ConnectionError as e:
            retry += 1
            print(f"[{_now_str()}] [WARN] strategy SSE connection error, retry={retry}, err={e}")

        except Exception as e:
            retry += 1
            print(f"[{_now_str()}] [WARN] strategy SSE unexpected error, retry={retry}, err={e}")

        if retry < SSE_MAX_RETRY and not _STOP:
            time.sleep(SSE_RETRY_SLEEP_SEC)

    if retry >= SSE_MAX_RETRY and not final_ok:
        print(f"[{_now_str()}] [ERROR] strategy SSE retry exhausted")

    print("strategy SSE ended")
    return final_ok


# =====================================================
# Result
# =====================================================

def get_strategy_result(workflow_id: str, stream_id: Optional[str] = None) -> dict:
    url = f"{BASE_URL}/strategy/result"

    params = {"workflow_id": workflow_id}
    if stream_id:
        params["stream_id"] = stream_id

    payload = _get_json(url, params=params)

    _pretty("strategy result", payload)
    return payload


# =====================================================
# Summary
# =====================================================

def print_strategy_summary(payload: dict) -> None:
    result = _unwrap_strategy_result(payload)

    summary = _safe_dict(result.get("summary"))
    metrics = _safe_dict(result.get("metrics"))
    quality_gate = _safe_dict(result.get("quality_gate"))

    must_test = result.get("must_test") or []
    smoke_scope = result.get("smoke_scope") or []
    regression_scope = result.get("regression_scope") or []
    blockers = result.get("blockers") or []
    pending_confirmations = result.get("pending_confirmations") or []

    print("\n====== STRATEGY SUMMARY ======")
    print("title:", summary.get("title"))
    print("business_domain:", summary.get("business_domain"))
    print("change_scope:", summary.get("change_scope"))
    print("overall_risk:", summary.get("overall_risk"))
    print("objective:", summary.get("objective"))

    print("impact_module_count:", metrics.get("impact_module_count"))
    print("impact_flow_count:", metrics.get("impact_flow_count"))
    print("risk_count:", metrics.get("risk_count"))
    print("must_test_count:", metrics.get("must_test_count"))
    print("regression_scope_count:", metrics.get("regression_scope_count"))
    print("blocker_count:", metrics.get("blocker_count"))
    print("pending_confirmation_count:", metrics.get("pending_confirmation_count"))

    print("quality_gate.decision:", quality_gate.get("decision"))
    print("quality_gate.reasons:", quality_gate.get("reasons"))

    print("must_test titles:", [x.get("title") for x in must_test[:5] if isinstance(x, dict)])
    print("smoke_scope titles:", [x.get("title") for x in smoke_scope[:5] if isinstance(x, dict)])
    print("regression_scope titles:", [x.get("title") for x in regression_scope[:5] if isinstance(x, dict)])

    print("blockers:", [x.get("title") for x in blockers[:5] if isinstance(x, dict)])
    print("pending_confirmations:", [x.get("title") for x in pending_confirmations[:5] if isinstance(x, dict)])


# =====================================================
# Main
# =====================================================

def main() -> None:
    if not os.path.exists(PDF_PATH):
        raise FileNotFoundError(PDF_PATH)

    workflow_id = create_workflow()

    if _STOP:
        print("[STOP] aborted after create_workflow")
        return

    upload_pdf(workflow_id)

    if _STOP:
        print("[STOP] aborted after upload_pdf")
        return

    ctx = get_strategy_context(workflow_id)
    if not ctx.get("has_requirement"):
        raise RuntimeError("strategy context missing requirement")

    if _STOP:
        print("[STOP] aborted before start_strategy")
        return

    run_info = start_strategy(workflow_id)
    job_id = run_info["job_id"]
    stream_id = run_info["stream_id"]

    stream_ok = listen_strategy_sse(stream_id)

    if _STOP:
        cancel_strategy(workflow_id)
        print("[STOP] user interrupted, exit now")
        return

    status_payload = get_strategy_status(workflow_id)
    if status_payload.get("status") == "error":
        raise RuntimeError(f"strategy status error: {status_payload}")

    # 无论 SSE 是否中途抖动，最终都拉一次 result
    result = get_strategy_result(workflow_id, stream_id=stream_id)
    print_strategy_summary(result)

    if not stream_ok:
        raise RuntimeError(
            f"strategy stream failed or interrupted, "
            f"job_id={job_id}, stream_id={stream_id}"
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _STOP = True
        print("\n[STOP] KeyboardInterrupt")
        sys.exit(1)