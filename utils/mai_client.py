"""
Posit mAI Gateway client  (see "Posit mAI Gateway – Developer Guide").

This replaces every LLM call of the original app (LangChain → Anthropic / OpenAI / Gemini / DeepL).
Only TWO environment variables are required:

    MAI_BASE_URL   gateway URL, ends with "/"         (…/posit-mai/conversationalai/)
    MAI_ENDPOINT   the `mai-endpoint` for the provider (…/openai/responses |
                   …/bedrock/model/us.anthropic.<model>/converse | …/gemini/interactions)

`MAI_MODEL` is optional (OpenAI / Gemini only; Claude's model is part of the endpoint).
No API key / token is used and **no Authorization header is ever sent** – the gateway
authenticates Posit Connect apps by the `X-Posit-App-Id` header, whose value is
`CONNECT_CONTENT_GUID` (set automatically by Posit Connect – never hard-coded).

The provider is detected from MAI_ENDPOINT:
    /bedrock/ → Claude via Bedrock Converse (plain HTTP, `requests`)
    /openai/  → GPT via the OpenAI SDK  (`client.responses.create`)
    /gemini/  → Gemini via google-genai (`client.interactions.create`)

The gateway offers neither streaming nor embeddings, so the app polls background jobs
(utils/jobs.py) and retrieves with BM25 (utils/rag.py).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

import constants as C

log = logging.getLogger(__name__)


class MaiError(RuntimeError):
    """Raised for any gateway problem.  `str(exc)` is safe to show to the user."""


# ── configuration ────────────────────────────────────────────────────────────
def provider() -> str:
    ep = (C.MAI_ENDPOINT or "").lower()
    if "/bedrock/" in ep or ep.endswith("/converse"):
        return "claude"
    if "/gemini/" in ep:
        return "gemini"
    if "/openai/" in ep or ep.endswith("/responses"):
        return "openai"
    return "claude"


def is_configured() -> bool:
    return C.MAI_MOCK or bool(C.MAI_BASE_URL and C.MAI_ENDPOINT)


def model_name() -> str:
    prov = provider()
    if prov == "claude":
        # "…/bedrock/model/us.anthropic.claude-haiku-5-5/converse" → "claude-haiku-5-5"
        try:
            part = C.MAI_ENDPOINT.split("/model/")[1].split("/")[0]
            return part.replace("us.anthropic.", "")
        except IndexError:
            return "claude"
    return C.MAI_MODEL or C.MAI_DEFAULT_MODEL[prov]


def describe() -> str:
    return f"{provider()}:{model_name()}" + (" (mock)" if C.MAI_MOCK else "")


def _app_id() -> str:
    guid = os.environ.get("CONNECT_CONTENT_GUID", "").strip()
    if not guid:
        raise MaiError(
            "The mAI gateway only accepts requests from apps running on Posit Connect "
            "(CONNECT_CONTENT_GUID is not set)."
        )
    return guid


def _check_config() -> None:
    if not C.MAI_BASE_URL or not C.MAI_ENDPOINT:
        raise MaiError("mAI is not configured. Set the MAI_BASE_URL and MAI_ENDPOINT environment variables.")
    prov = provider()
    if prov != "claude":
        allowed = C.MAI_ALLOWED_MODELS[prov]
        if model_name() not in allowed:
            raise MaiError(f"MAI_MODEL '{model_name()}' is not allowed by the gateway. Use one of: {', '.join(allowed)}.")


def _base_url() -> str:
    return C.MAI_BASE_URL if C.MAI_BASE_URL.endswith("/") else C.MAI_BASE_URL + "/"


def _headers() -> dict[str, str]:
    # NB: deliberately no Authorization / API-key header – the gateway authenticates the app by its id.
    return {"X-Posit-App-Id": _app_id(), "Content-Type": "application/json", "x-source-type": C.MAI_SOURCE_TYPE}


def _http_error(status: int, text: str) -> MaiError:
    detail = (text or "").strip().replace("\n", " ")[:300]
    hints = {
        400: "Check that MAI_ENDPOINT is set and that the app runs on Posit Connect (X-Posit-App-Id).",
        403: "The gateway refused the request (model not allowed, or the app is not running on Posit Connect).",
        404: "Resource not found – check MAI_BASE_URL (it must end with '/').",
        429: "The gateway is rate-limiting this app – try again shortly.",
    }
    return MaiError(f"mAI gateway error {status}. {hints.get(status, '')} {detail}".strip())


# ── message helpers ──────────────────────────────────────────────────────────
def _normalise(messages: list[dict]) -> list[dict]:
    """user/assistant turns with non-empty text, alternating, starting with a user turn."""
    out: list[dict] = []
    for m in messages:
        role = "assistant" if m.get("role") == "assistant" else "user"
        text = str(m.get("content") or "").strip()
        if not text:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _flatten(messages: list[dict]) -> str:
    if len(messages) == 1:
        return messages[0]["content"]
    lines = []
    for m in messages[:-1]:
        lines.append(("User: " if m["role"] == "user" else "Assistant: ") + m["content"])
    return "Conversation so far:\n" + "\n\n".join(lines) + "\n\nLatest user message:\n" + messages[-1]["content"]


# ── providers ────────────────────────────────────────────────────────────────
def _claude(system: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
    body: dict[str, Any] = {
        "messages": [{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages],
        "inferenceConfig": {"maxTokens": int(max_tokens), "temperature": float(temperature)},
    }
    if system:
        body["system"] = [{"text": system}]
    resp = requests.post(
        _base_url() + "converse", params={"mai-endpoint": C.MAI_ENDPOINT}, headers=_headers(), json=body,
        timeout=C.MAI_TIMEOUT_SECONDS,
    )
    if resp.status_code >= 400:
        raise _http_error(resp.status_code, resp.text)
    data = resp.json()
    blocks = (data.get("output", {}).get("message", {}) or {}).get("content", [])
    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
    stop = data.get("stopReason")
    if stop == "max_tokens":
        log.warning("mAI Claude response truncated (maxTokens=%s)", max_tokens)
    return text


def _openai(system: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
    from openai import OpenAI  # lazy: only needed for GPT endpoints

    client = OpenAI(
        base_url=_base_url(), api_key="not-used",   # gateway handles authentication
        default_headers={"X-Posit-App-Id": _app_id(), "x-source-type": C.MAI_SOURCE_TYPE},
        default_query={"mai-endpoint": C.MAI_ENDPOINT}, timeout=C.MAI_TIMEOUT_SECONDS, max_retries=0,
    )
    kwargs: dict[str, Any] = {"model": model_name(), "input": [{"role": m["role"], "content": m["content"]}
                                                               for m in messages],
                              "max_output_tokens": int(max_tokens)}
    if system:
        kwargs["instructions"] = system
    try:
        resp = client.responses.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        status = getattr(exc, "status_code", None)
        if status:
            raise _http_error(int(status), str(getattr(exc, "message", exc))) from exc
        raise
    return resp.output_text or ""


def _gemini(system: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
    from google import genai  # lazy: only needed for Gemini endpoints
    from google.genai import types

    client = genai.Client(
        api_key="not-used",                         # gateway handles authentication
        http_options=types.HttpOptions(
            base_url=_base_url().rstrip("/"), api_version="",
            headers={"X-Posit-App-Id": _app_id(), "x-source-type": C.MAI_SOURCE_TYPE},
            client_args={"params": {"mai-endpoint": C.MAI_ENDPOINT}},
            timeout=C.MAI_TIMEOUT_SECONDS * 1000,
        ),
    )
    try:
        resp = client.interactions.create(model=model_name(), system_instruction=system or None,
                                          input=_flatten(messages))
    except Exception as exc:  # noqa: BLE001
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if isinstance(status, int):
            raise _http_error(status, str(exc)) from exc
        raise
    return resp.output_text or ""


# ── public API ───────────────────────────────────────────────────────────────
def generate(system: str, messages: list[dict] | str, max_tokens: int | None = None, temperature: float = 0.2,
             retries: int = 3) -> str:
    """One blocking completion.  `messages` = list of {role, content} (or a plain user string)."""
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    msgs = _normalise(messages)
    if not msgs:
        raise MaiError("Nothing to send to the model.")
    max_tokens = max_tokens or C.CHAT_MAX_TOKENS

    if C.MAI_MOCK:
        return _mock(system, msgs)
    _check_config()

    fn = {"claude": _claude, "openai": _openai, "gemini": _gemini}[provider()]
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return fn(system, msgs, max_tokens, temperature).strip()
        except MaiError as exc:
            # 4xx (except 429) will not get better by retrying
            last = exc
            if "error 429" not in str(exc) and "error 5" not in str(exc):
                raise
        except (requests.Timeout, requests.ConnectionError, TimeoutError, ConnectionError) as exc:
            last = exc
        except Exception as exc:  # noqa: BLE001
            log.exception("mAI call failed")
            raise MaiError("The mAI gateway call failed. Please try again.") from exc
        if attempt < retries - 1:
            time.sleep(min(10, 2 ** attempt))
    if isinstance(last, MaiError):
        raise last
    raise MaiError("The mAI gateway did not respond in time. Please try again.") from last


# ── mock (local UI preview; the real gateway only accepts calls from Posit Connect) ──
def _mock(system: str, messages: list[dict]) -> str:
    last = messages[-1]["content"]
    if "Translate the user's text" in system:
        return "[mock translation] " + last[:200]
    if "rephrase the follow-up" in system:
        return last
    if "Public Investment Management Context" in system or "Transparency International" in system:
        return (
            "## Overview\n\n_Mock briefing generated locally (PIM_MAI_MOCK=1). On Posit Connect this text is produced "
            "by the mAI gateway from the retrieved policy documents._\n\n"
            "| Institution / Entity | PIM Role | Key Instrument |\n|---|---|---|\n"
            "| Ministry of Finance | Central PIM coordination | PFM Act |\n\n"
            "## Key gaps & reform priorities\n\n- Example gap one\n- Example gap two\n"
        )
    excerpt = ""
    if "[1]" in system:
        excerpt = system.split("[1]", 1)[1][:300].strip().replace("\n", " ")
    return (f"**Mock answer** (PIM_MAI_MOCK=1) to: _{last[:160]}_\n\n"
            + (f"Grounded in excerpt [1]: “{excerpt}…”" if excerpt else "No documents were retrieved."))
