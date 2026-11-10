"""
AI Coach question answering (port of `rag/app/generation/chains.py::rag_query` + `api/chat.py`).

condense follow-up → retrieve (BM25) → answer with mAI.  No streaming: the Coach page shows a typing
indicator and renders the whole answer when the (background) job finishes.
"""

from __future__ import annotations

import logging

from utils import constants as C
from utils import jobs, mai_client, prompts, rag

log = logging.getLogger(__name__)


def condense_question(question: str, history: list[dict]) -> str:
    """Rephrase a follow-up into a standalone question (skipped when there is no history)."""
    if not history:
        return question
    try:
        out = mai_client.generate(prompts.CONDENSE_SYSTEM_PROMPT,
                                  history + [{"role": "user", "content": question}],
                                  max_tokens=300, temperature=0.0, retries=2)
        return out.strip() or question
    except Exception:  # noqa: BLE001 - fall back to the raw question
        log.warning("condense failed", exc_info=True)
        return question


def ask(question: str, history: list[dict] | None = None, filters: dict | None = None) -> dict:
    """→ {"answer": str, "sources": [{"content": str(≤300), "metadata": {...}}]}."""
    question = (question or "").strip()
    if not question:
        raise ValueError("Question is required.")
    if not mai_client.is_configured():
        raise mai_client.MaiError("mAI is not configured. Set the MAI_BASE_URL and MAI_ENDPOINT environment variables.")
    history = [{"role": m.get("role"), "content": m.get("content")} for m in (history or [])
               if m.get("role") in ("user", "assistant") and m.get("content")]
    response_language = "ORI" if (filters or {}).get("lang_type") == "ORI" else None
    standalone = condense_question(question, history)
    docs = rag.retrieve(standalone, filters=filters, k=C.RETRIEVER_K)
    system = prompts.build_chat_system(docs, response_language)
    answer = mai_client.generate(system, history + [{"role": "user", "content": question}],
                                 max_tokens=C.CHAT_MAX_TOKENS, temperature=0.2)
    return {"answer": answer, "sources": [{"content": d["content"][:300], "metadata": d["metadata"]} for d in docs]}


def _job(job_id: str, question: str, history: list[dict], filters: dict | None) -> dict:
    return ask(question, history, filters)


def start(question: str, history: list[dict] | None, filters: dict | None) -> str:
    return jobs.submit("chat", _job, question, history or [], filters)
