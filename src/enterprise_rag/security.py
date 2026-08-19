"""Small, authentication-free safety checks used at the public API boundary."""

from fastapi import HTTPException


def reject_prompt_injection(question: str) -> None:
    """Reject a few obvious attempts to override the document-answering task.

    This is deliberately a narrow, explainable first layer. It is not a
    substitute for the more important safeguard in ``rag.py``: the model only
    receives retrieved document excerpts and is instructed to treat their
    contents as data rather than executable instructions.
    """

    suspicious_phrases = ("ignore previous instructions", "reveal secrets", "system prompt")
    if any(phrase in question.lower() for phrase in suspicious_phrases):
        raise HTTPException(status_code=400, detail="The question contains an unsupported instruction override.")
