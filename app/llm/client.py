# app/llm/client.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import re
import time
import json
import queue
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Generator, Optional, Dict, Any, List

from openai import OpenAI

from app.settings import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
)

logger = logging.getLogger(__name__)

# =========================================================
# 环境配置
# =========================================================

def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def _get_stream_enabled() -> bool:
    return _env_bool("TC_LLM_STREAM_ENABLED", True)


def _get_stream_auto_fallback() -> bool:
    return _env_bool("TC_LLM_STREAM_AUTO_FALLBACK", True)


def _get_first_token_timeout() -> int:
    return _env_int("TC_LLM_STREAM_FIRST_TOKEN_TIMEOUT", 6)


def _get_inactivity_timeout() -> int:
    return _env_int("TC_LLM_STREAM_INACTIVITY_TIMEOUT", 20)


def _get_max_stream_tokens() -> int:
    return _env_int("TC_LLM_MAX_STREAM_TOKENS", 6000)


def _get_max_workers() -> int:
    return _env_int("TC_LLM_STREAM_MAX_WORKERS", 8)


def _get_stream_queue_size() -> int:
    return _env_int("TC_LLM_STREAM_QUEUE_SIZE", 2000)


def _get_default_temperature() -> float:
    return _env_float("TC_LLM_TEMPERATURE", 0.15)


def _get_default_max_tokens() -> int:
    return _env_int("TC_LLM_MAX_TOKENS", 4096)


def _get_agent_temperature(agent_type: Optional[str], default: Optional[float] = None) -> float:
    if default is not None:
        return default

    agent = (agent_type or "").strip().lower()
    mapping = {
        "analysis": _env_float("TC_LLM_TEMPERATURE_ANALYSIS", 0.10),
        "planner": _env_float("TC_LLM_TEMPERATURE_PLANNER", 0.10),
        "review": _env_float("TC_LLM_TEMPERATURE_REVIEW", 0.10),
        "refine": _env_float("TC_LLM_TEMPERATURE_REFINE", 0.10),
        "coverage": _env_float("TC_LLM_TEMPERATURE_COVERAGE", 0.10),
        "design": _env_float("TC_LLM_TEMPERATURE_DESIGN", 0.20),
        "default": _get_default_temperature(),
    }
    return mapping.get(agent, mapping["default"])


# =========================================================
# 线程池（防止线程爆炸）
# =========================================================

_STREAM_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, _get_max_workers()))

# =========================================================
# 单例 client
# =========================================================

_openai_client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
)


class LLM:
    """
    LLM client
    - call(): 非流式，一次性返回字符串
    - call_json(): 非流式，返回 dict
    - stream(): 流式，逐块 yield 字符串
    - stream_json(): 流式文本 + 最终 JSON 收敛
    """

    _DEFAULT_SYSTEM_PROMPT_CALL = (
        "You are a senior QA architect and senior test engineer with 10+ years of experience.\n"
        "Strict rules:\n"
        "1. Follow the user requirement text strictly.\n"
        "2. Do not invent business context, modules, fields, rules, or domain assumptions not grounded in the input.\n"
        "3. Avoid generic filler such as '功能正常', '流程正常', '符合预期', '系统正常'.\n"
        "4. Output must be specific, testable, and implementation-usable.\n"
        "5. When asked for structured output, keep field semantics stable and avoid unnecessary wording drift.\n"
        "6. No explanation outside the requested result."
    )

    _DEFAULT_SYSTEM_PROMPT_STREAM = (
        "You are a senior QA architect and senior test engineer with 10+ years of experience.\n"
        "Follow the requirement text strictly.\n"
        "Do not invent business context.\n"
        "Avoid generic filler.\n"
        "Output only the requested result.\n"
        "No markdown."
    )

    _STRICT_JSON_OBJECT_PROMPT = (
        "You are a senior QA architect and senior test engineer with 10+ years of experience.\n"
        "Output ONLY one valid JSON object.\n"
        "Strict rules:\n"
        "1. Follow the user requirement text strictly.\n"
        "2. Do not invent business context, modules, fields, rules, or assumptions not grounded in the input.\n"
        "3. Do not output markdown.\n"
        "4. Do not output explanation.\n"
        "5. Do not output code fences.\n"
        "6. Do not output any text before or after the JSON object.\n"
        "7. Avoid generic filler values.\n"
    )

    _JSON_REPAIR_SYSTEM_PROMPT = (
        "You are a JSON repair assistant.\n"
        "Your task is to repair malformed content into one valid JSON object only.\n"
        "Rules:\n"
        "1. Output ONLY one valid JSON object.\n"
        "2. No markdown.\n"
        "3. No explanation.\n"
        "4. Preserve original semantics as much as possible.\n"
        "5. If some fields are malformed, fix syntax without inventing unrelated content.\n"
    )

    def __init__(self):
        self.client = _openai_client

    # =====================================================
    # 非流式调用
    # =====================================================

    def call(
        self,
        prompt: str,
        timeout: int = 120,
        system_prompt: Optional[str] = None,
        force_json_object: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        agent_type: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> str:
        request_id = trace_id or uuid.uuid4().hex[:8]
        start = time.time()

        sys_prompt = (
            self._STRICT_JSON_OBJECT_PROMPT
            if force_json_object
            else (system_prompt or self._DEFAULT_SYSTEM_PROMPT_CALL)
        )

        final_temperature = _get_agent_temperature(agent_type=agent_type, default=temperature)

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt},
        ]

        kwargs: Dict[str, Any] = {
            "model": model or OPENAI_MODEL,
            "timeout": timeout,
            "messages": messages,
            "temperature": final_temperature,
        }

        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = _get_default_max_tokens()

        if force_json_object:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            rr = self.client.chat.completions.with_raw_response.create(**kwargs)
            status = getattr(rr.http_response, "status_code", None)

            if not status or status >= 300:
                logger.error(
                    "[LLM.call] request=%s http_status=%s model=%s agent=%s",
                    request_id,
                    status,
                    kwargs["model"],
                    agent_type,
                )
                return ""

            resp = rr.parse()
            content = self._extract_message_content(resp)
            content = self._sanitize(content, force_json_object=force_json_object)

            latency = round(time.time() - start, 3)
            logger.info(
                "[LLM.call] request=%s model=%s agent=%s latency=%ss len=%s force_json=%s",
                request_id,
                kwargs["model"],
                agent_type,
                latency,
                len(content),
                force_json_object,
            )
            return content

        except Exception as e:
            # 某些 provider 不支持 response_format=json_object，自动降级一次
            if force_json_object and "response_format" in kwargs:
                logger.warning(
                    "[LLM.call] request=%s response_format failed, retry without response_format | "
                    "model=%s agent=%s err=%s",
                    request_id,
                    kwargs["model"],
                    agent_type,
                    str(e),
                )
                try:
                    kwargs.pop("response_format", None)
                    rr = self.client.chat.completions.with_raw_response.create(**kwargs)
                    status = getattr(rr.http_response, "status_code", None)

                    if not status or status >= 300:
                        logger.error(
                            "[LLM.call] request=%s retry http_status=%s model=%s agent=%s",
                            request_id,
                            status,
                            kwargs["model"],
                            agent_type,
                        )
                        return ""

                    resp = rr.parse()
                    content = self._extract_message_content(resp)
                    content = self._sanitize(content, force_json_object=True)

                    latency = round(time.time() - start, 3)
                    logger.info(
                        "[LLM.call] request=%s retry-success model=%s agent=%s latency=%ss len=%s force_json=%s",
                        request_id,
                        kwargs["model"],
                        agent_type,
                        latency,
                        len(content),
                        True,
                    )
                    return content

                except Exception as retry_e:
                    logger.error(
                        "[LLM.call] request=%s retry failed model=%s agent=%s error=%s",
                        request_id,
                        kwargs["model"],
                        agent_type,
                        str(retry_e),
                        exc_info=True,
                    )
                    return ""

            logger.error(
                "[LLM.call] request=%s model=%s agent=%s error=%s",
                request_id,
                kwargs["model"],
                agent_type,
                str(e),
                exc_info=True,
            )
            return ""

    # =====================================================
    # 强 JSON 调用
    # =====================================================

    def call_json(
        self,
        prompt: str,
        timeout: int = 120,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        agent_type: Optional[str] = None,
        trace_id: Optional[str] = None,
        auto_repair: bool = True,
    ) -> Dict[str, Any]:
        request_id = trace_id or uuid.uuid4().hex[:8]

        raw = self.call(
            prompt=prompt,
            timeout=timeout,
            system_prompt=system_prompt,
            force_json_object=True,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            agent_type=agent_type,
            trace_id=request_id,
        )

        if not raw:
            return {}

        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass

        if not auto_repair:
            logger.warning("[LLM.call_json] request=%s parse failed, auto_repair disabled", request_id)
            return {}

        repair_prompt = (
            "Repair the following content into ONE valid JSON object only.\n"
            "Return JSON only.\n\n"
            f"{raw}"
        )

        repaired = self.call(
            prompt=repair_prompt,
            timeout=timeout,
            system_prompt=self._JSON_REPAIR_SYSTEM_PROMPT,
            force_json_object=True,
            temperature=0.0,
            max_tokens=max_tokens,
            model=model,
            agent_type=agent_type,
            trace_id=request_id,
        )

        if not repaired:
            logger.warning("[LLM.call_json] request=%s repair returned empty", request_id)
            return {}

        try:
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                logger.info("[LLM.call_json] request=%s repair success", request_id)
                return parsed
        except Exception:
            logger.warning("[LLM.call_json] request=%s repair parse failed", request_id)

        return {}

    # =====================================================
    # 流式调用
    # =====================================================

    def stream(
        self,
        prompt: str,
        timeout: int = 120,
        first_token_timeout: Optional[int] = None,
        inactivity_timeout: Optional[int] = None,
        max_stream_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        force_json_object: bool = False,
        auto_fallback_call: bool = True,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        agent_type: Optional[str] = None,
        trace_id: Optional[str] = None,
        emit_warmup_chunk: bool = True,
    ) -> Generator[str, None, None]:
        """
        逐块 yield 文本内容。

        注意：
        - force_json_object=True 时，默认直接 fallback 到 call()
        - 已经开始流式输出后，不再 fallback 到 call()，避免重复内容污染
        """
        request_id = trace_id or uuid.uuid4().hex[:8]
        start_time = time.time()

        env_stream_enabled = _get_stream_enabled()
        env_auto_fallback = _get_stream_auto_fallback()

        first_token_timeout = max(1, int(first_token_timeout or _get_first_token_timeout()))
        inactivity_timeout = max(1, int(inactivity_timeout or _get_inactivity_timeout()))
        max_stream_tokens = max(1, int(max_stream_tokens or _get_max_stream_tokens()))
        auto_fallback_call = bool(auto_fallback_call and env_auto_fallback)
        final_temperature = _get_agent_temperature(agent_type=agent_type, default=temperature)

        # JSON 模式直接退化为 call，避免切碎 JSON
        if force_json_object:
            logger.info(
                "[LLM.stream] force_json_object -> fallback to call | request=%s model=%s agent=%s",
                request_id,
                model or OPENAI_MODEL,
                agent_type,
            )
            raw = self.call(
                prompt=prompt,
                timeout=timeout,
                system_prompt=system_prompt,
                force_json_object=True,
                temperature=final_temperature,
                max_tokens=max_tokens,
                model=model,
                agent_type=agent_type,
                trace_id=request_id,
            )
            if raw:
                yield raw
            return

        if not env_stream_enabled:
            logger.info(
                "[LLM.stream] disabled by env, fallback to call | request=%s model=%s agent=%s",
                request_id,
                model or OPENAI_MODEL,
                agent_type,
            )
            raw = self.call(
                prompt=prompt,
                timeout=timeout,
                system_prompt=system_prompt,
                force_json_object=False,
                temperature=final_temperature,
                max_tokens=max_tokens,
                model=model,
                agent_type=agent_type,
                trace_id=request_id,
            )
            if raw:
                yield raw
            return

        sys_prompt = system_prompt or self._DEFAULT_SYSTEM_PROMPT_STREAM

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt},
        ]

        kwargs: Dict[str, Any] = {
            "model": model or OPENAI_MODEL,
            "stream": True,
            "timeout": timeout,
            "messages": messages,
            "temperature": final_temperature,
        }

        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = _get_default_max_tokens()

        q: "queue.Queue[Any]" = queue.Queue(maxsize=max(100, _get_stream_queue_size()))
        sentinel = object()

        stream_started = False
        fallback_triggered = False
        emitted_chunks = 0
        token_count = 0
        last_token_time = start_time
        first_chunk_time: Optional[float] = None

        def _producer() -> None:
            try:
                stream_obj = self.client.chat.completions.create(**kwargs)
                for chunk in stream_obj:
                    try:
                        content = self._extract_stream_chunk_content(chunk)
                        if content:
                            q.put(content)
                    except Exception:
                        continue
            except Exception as e:
                q.put(("__ERR__", str(e)))
            finally:
                q.put(sentinel)

        _STREAM_EXECUTOR.submit(_producer)

        if emit_warmup_chunk:
            # 用空串占位，方便前端尽快进入“已收到流”的状态
            yield ""

        while True:
            try:
                item = q.get(timeout=0.2)

            except queue.Empty:
                now = time.time()

                # 首 token 超时：允许 fallback
                if not stream_started and (now - start_time) > first_token_timeout:
                    if auto_fallback_call and not fallback_triggered:
                        fallback_triggered = True
                        logger.warning(
                            "[LLM.stream] first token timeout, fallback request=%s model=%s agent=%s",
                            request_id,
                            kwargs["model"],
                            agent_type,
                        )
                        raw = self.call(
                            prompt=prompt,
                            timeout=timeout,
                            system_prompt=sys_prompt,
                            force_json_object=False,
                            temperature=final_temperature,
                            max_tokens=max_tokens,
                            model=model,
                            agent_type=agent_type,
                            trace_id=request_id,
                        )
                        if raw:
                            yield raw
                        return

                    logger.warning(
                        "[LLM.stream] first token timeout, stop request=%s model=%s agent=%s",
                        request_id,
                        kwargs["model"],
                        agent_type,
                    )
                    return

                # 已开始输出后，不再 fallback，避免重复文本污染
                if stream_started and (now - last_token_time) > inactivity_timeout:
                    logger.warning(
                        "[LLM.stream] inactivity timeout, stop request=%s model=%s agent=%s emitted_chunks=%s",
                        request_id,
                        kwargs["model"],
                        agent_type,
                        emitted_chunks,
                    )
                    return

                continue

            if item is sentinel:
                break

            if isinstance(item, tuple) and len(item) >= 2 and item[0] == "__ERR__":
                logger.error(
                    "[LLM.stream] request=%s model=%s agent=%s error=%s",
                    request_id,
                    kwargs["model"],
                    agent_type,
                    item[1],
                )

                if not stream_started and auto_fallback_call and not fallback_triggered:
                    fallback_triggered = True
                    raw = self.call(
                        prompt=prompt,
                        timeout=timeout,
                        system_prompt=sys_prompt,
                        force_json_object=False,
                        temperature=final_temperature,
                        max_tokens=max_tokens,
                        model=model,
                        agent_type=agent_type,
                        trace_id=request_id,
                    )
                    if raw:
                        yield raw
                return

            chunk_text = str(item)
            if not chunk_text:
                continue

            if not stream_started:
                first_chunk_time = time.time()

            stream_started = True
            emitted_chunks += 1
            token_count += len(chunk_text)
            last_token_time = time.time()

            if token_count > max_stream_tokens:
                logger.warning(
                    "[LLM.stream] token limit request=%s model=%s agent=%s max_stream_tokens=%s",
                    request_id,
                    kwargs["model"],
                    agent_type,
                    max_stream_tokens,
                )
                return

            yield chunk_text

        latency = round(time.time() - start_time, 3)
        first_token_latency = (
            round(first_chunk_time - start_time, 3) if first_chunk_time is not None else None
        )
        logger.info(
            "[LLM.stream] done request=%s model=%s agent=%s latency=%ss first_token=%ss emitted_chunks=%s total_chars=%s",
            request_id,
            kwargs["model"],
            agent_type,
            latency,
            first_token_latency,
            emitted_chunks,
            token_count,
        )

    # =====================================================
    # 流式 JSON
    # =====================================================

    def stream_json(
        self,
        prompt: str,
        timeout: int = 120,
        first_token_timeout: Optional[int] = None,
        inactivity_timeout: Optional[int] = None,
        system_prompt: Optional[str] = None,
        auto_fallback_call: bool = True,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        agent_type: Optional[str] = None,
        trace_id: Optional[str] = None,
        emit_warmup_chunk: bool = False,
        auto_repair: bool = True,
    ) -> Generator[str, None, None]:
        """
        先正常流式输出文本；结束后再尝试收敛 JSON。
        约定：
        - 正常文本 chunk：直接 yield
        - 收敛成功：yield "\\n__JSON_END__" + json字符串
        - 收敛失败：yield "\\n__JSON_ERROR__"
        """
        request_id = trace_id or uuid.uuid4().hex[:8]
        collected: List[str] = []

        for chunk in self.stream(
            prompt=prompt,
            timeout=timeout,
            first_token_timeout=first_token_timeout,
            inactivity_timeout=inactivity_timeout,
            system_prompt=system_prompt,
            force_json_object=False,
            auto_fallback_call=auto_fallback_call,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            agent_type=agent_type,
            trace_id=request_id,
            emit_warmup_chunk=emit_warmup_chunk,
        ):
            collected.append(chunk)
            yield chunk

        full_text = "".join(collected).strip()
        if not full_text:
            yield "\n__JSON_ERROR__"
            return

        json_text = self._sanitize(full_text, force_json_object=True)

        if not json_text and auto_repair:
            repaired = self.call(
                prompt=f"Repair the following content into ONE valid JSON object only:\n\n{full_text}",
                timeout=timeout,
                system_prompt=self._JSON_REPAIR_SYSTEM_PROMPT,
                force_json_object=True,
                temperature=0.0,
                max_tokens=max_tokens,
                model=model,
                agent_type=agent_type,
                trace_id=request_id,
            )
            json_text = self._sanitize(repaired, force_json_object=True)

        if not json_text:
            yield "\n__JSON_ERROR__"
            return

        try:
            parsed = json.loads(json_text)
            if not isinstance(parsed, dict):
                yield "\n__JSON_ERROR__"
                return
            yield "\n__JSON_END__"
            yield json.dumps(parsed, ensure_ascii=False)
        except Exception:
            yield "\n__JSON_ERROR__"

    # =====================================================
    # 内容提取
    # =====================================================

    def _extract_message_content(self, resp: Any) -> str:
        try:
            choices = getattr(resp, "choices", None) or []
            if not choices:
                return ""

            message = getattr(choices[0], "message", None)
            if message is None:
                return ""

            content = getattr(message, "content", None)

            if isinstance(content, str):
                return content

            if isinstance(content, list):
                return self._flatten_content_parts(content)

            return ""
        except Exception:
            return ""

    def _extract_stream_chunk_content(self, chunk: Any) -> str:
        """
        兼容常见流式 chunk 结构：
        - choices[0].delta.content
        - 部分 provider 返回 content list / text parts
        """
        try:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                return ""

            delta = getattr(choices[0], "delta", None)
            if delta is None:
                return ""

            content = getattr(delta, "content", None)

            if isinstance(content, str):
                return content

            if isinstance(content, list):
                return self._flatten_content_parts(content)

            # 少数 SDK/provider 可能直接给文本字段
            text = getattr(delta, "text", None)
            if isinstance(text, str):
                return text

            return ""
        except Exception:
            return ""

    def _flatten_content_parts(self, parts: List[Any]) -> str:
        out: List[str] = []

        for part in parts or []:
            if isinstance(part, str):
                out.append(part)
                continue

            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text:
                    out.append(text)
                elif part.get("type") == "text":
                    value = part.get("value")
                    if isinstance(value, str) and value:
                        out.append(value)
                continue

            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                out.append(text)
                continue

            value = getattr(part, "value", None)
            if isinstance(value, str) and value:
                out.append(value)

        return "".join(out)

    # =====================================================
    # 清洗
    # =====================================================

    def _sanitize(self, content: str, force_json_object: bool = False) -> str:
        content = (content or "").strip()
        if not content:
            return ""

        content = self._strip_code_fence(content)

        if force_json_object:
            obj = self._extract_first_json_object(content)
            if not obj:
                return ""

            try:
                parsed = json.loads(obj)
            except Exception:
                return ""

            if not isinstance(parsed, dict):
                return ""

            return json.dumps(parsed, ensure_ascii=False)

        return content

    def _strip_code_fence(self, text: str) -> str:
        s = (text or "").strip()
        if not s:
            return ""

        s = re.sub(r"^\s*```(?:json|JSON)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
        return s.strip()

    # =====================================================
    # JSON object 提取
    # =====================================================

    def _extract_first_json_object(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""

        start = text.find("{")
        if start < 0:
            return ""

        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(text)):
            ch = text[i]

            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue

            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

        return ""


# =========================================================
# 单例
# =========================================================

_llm_singleton = LLM()


def get_llm() -> LLM:
    return _llm_singleton