"""Retrieval + answer-quality evaluation for the RAG pipeline.

Runs a golden set through the *real* retrieval components against an isolated
Qdrant collection, and reports:

  * Retrieval: recall@k and MRR, both pre-rerank (the vector-search ceiling) and
    post-rerank (what the LLM reranker keeps).
  * Answer quality: LLM-judged faithfulness and answer-relevance (1-5).
  * Robustness: abstention rate on out-of-corpus questions (does it correctly
    refuse instead of hallucinating?).

It mirrors ``RAGService`` step by step rather than calling it, so both the pre-
and post-rerank rankings can be captured from one retrieval. Run from repo root:

    python -m evals.build_corpus            # once, to generate the fixture PDF
    python -m evals.run_eval --reindex      # index the corpus, then evaluate
    python -m evals.run_eval --retrieval-only   # cheap: skip generation + judge

Costs money: each answerable question makes embedding + rerank (+ generation +
judge) OpenAI calls, and it reads/writes a live Qdrant collection.
"""

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from enterprise_rag.config import Settings, get_settings
from enterprise_rag.services.chunking import chunk_pages
from enterprise_rag.services.document_loader import extract_pages
from enterprise_rag.services.embeddings import OpenAIEmbeddingService
from enterprise_rag.services.rag import RAGService
from enterprise_rag.services.vector_store import QdrantStore, RetrievedChunk
from evals.metrics import hit_at_k, mean, recall_at_k, reciprocal_rank

EVAL_DIR = Path(__file__).resolve().parent
CORPUS_DIR = EVAL_DIR / "corpus"
GOLDEN_PATH = EVAL_DIR / "golden_set.jsonl"
REPORTS_DIR = EVAL_DIR / "reports"
DEFAULT_COLLECTION = "enterprise_documents_eval"
DEFAULT_KS = (1, 3, 5, 8, 10)
ABSTENTION_MARKER = "could not find"

_JUDGE_SYSTEM = """You are a strict evaluator of a retrieval-augmented answer.
Given a QUESTION, the ANSWER, and the CONTEXT excerpts the answer was meant to use, score:
- faithfulness (1-5): is every factual claim in the ANSWER supported by the CONTEXT? 5 = fully grounded, 1 = fabricated.
- answer_relevance (1-5): does the ANSWER actually address the QUESTION?
Return ONLY JSON: {"faithfulness": <1-5>, "answer_relevance": <1-5>, "reasoning": "<one short sentence>"}."""


def load_golden() -> list[dict]:
    """Read the golden set, skipping blank and ``#`` comment lines."""

    items: list[dict] = []
    for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            items.append(json.loads(stripped))
    return items


def reindex(settings: Settings) -> None:
    """(Re)index every PDF in the corpus into the eval collection, idempotently."""

    store = QdrantStore(settings)
    embeddings = OpenAIEmbeddingService(settings)
    pdfs = sorted(CORPUS_DIR.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No corpus PDFs in {CORPUS_DIR}. Run: python -m evals.build_corpus")
    for pdf in pdfs:
        content = pdf.read_bytes()
        chunks = chunk_pages(extract_pages(pdf.name, content))
        vectors = embeddings.embed([chunk.text for chunk in chunks])
        # Deterministic ID keyed on the filename, so reindexing overwrites rather
        # than duplicating.
        document_id = str(uuid5(NAMESPACE_URL, f"eval:{pdf.name}"))
        store.delete_by_document(document_id)
        store.upsert(document_id, pdf.name, chunks, vectors)
        print(f"  indexed {pdf.name}: {len(chunks)} chunks")


def _pages(chunks: list[RetrievedChunk]) -> list[tuple[str, int]]:
    """Rank-ordered (filename, page) pairs, dropping chunks without a page."""

    return [(c.filename, c.page_number) for c in chunks if c.page_number is not None]


def judge(rag: RAGService, question: str, answer: str, context: str) -> tuple[float | None, float | None, str]:
    """Score faithfulness and relevance with an LLM judge. None on parse failure."""

    response = rag.llm.chat.completions.create(
        model=rag.settings.chat_model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": f"QUESTION:\n{question}\n\nANSWER:\n{answer}\n\nCONTEXT:\n{context}"},
        ],
    )
    try:
        data = json.loads(response.choices[0].message.content or "{}")
        return float(data["faithfulness"]), float(data["answer_relevance"]), str(data.get("reasoning", ""))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None, None, "judge parse error"


def evaluate(settings: Settings, ks: tuple[int, ...], retrieval_only: bool) -> dict:
    """Run the golden set and return an aggregate report dict."""

    embeddings = OpenAIEmbeddingService(settings)
    store = QdrantStore(settings)
    rag = RAGService(settings)
    golden = load_golden()

    rows: list[dict] = []
    for item in golden:
        question = item["question"]
        answerable = item.get("answerable", True)
        relevant = {(item["doc"], page) for page in item.get("pages", [])} if answerable else set()

        vector = embeddings.embed([question])[0]
        # Pre-rerank: raw vector search, no score threshold -> the retriever's ceiling.
        pre = store.search(vector, settings.top_k, None, 0.0)
        pre_ranked = _pages(pre)
        # Post-rerank mirrors production: keep what clears the threshold, then rerank.
        above = [c for c in pre if c.score >= settings.score_threshold]
        selected = rag._rerank(question, above) if above else []
        post_ranked = _pages(selected)

        row: dict = {"id": item["id"], "answerable": answerable}
        if answerable:
            row["pre"] = {f"recall@{k}": recall_at_k(pre_ranked, relevant, k) for k in ks}
            row["pre"]["rr"] = reciprocal_rank(pre_ranked, relevant)
            row["post"] = {f"recall@{k}": recall_at_k(post_ranked, relevant, k) for k in ks}
            row["post"]["rr"] = reciprocal_rank(post_ranked, relevant)
            row["hit@1_post"] = hit_at_k(post_ranked, relevant, 1)

        if not retrieval_only:
            # Generate the answer the same way RAGService.answer does, reusing its
            # prompt and context builder, from the chunks we already reranked.
            if selected:
                context = rag._build_context(selected)
                completion = rag.llm.chat.completions.create(
                    model=settings.chat_model,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": rag._system_prompt()},
                        {"role": "user", "content": f"Question: {question}\n\nDocument excerpts:\n{context}"},
                    ],
                )
                answer = completion.choices[0].message.content or ""
            else:
                context = ""
                answer = "I could not find supporting information in the indexed documents."

            row["abstained"] = ABSTENTION_MARKER in answer.lower()
            if answerable:
                fact = next((f for f in item.get("must_include", [])), None)
                row["fact_present"] = bool(fact) and fact.lower() in answer.lower()
                faithfulness, relevance, reasoning = judge(rag, question, answer, context)
                row["faithfulness"] = faithfulness
                row["answer_relevance"] = relevance
                row["judge_note"] = reasoning
        rows.append(row)

    return _aggregate(rows, ks, retrieval_only)


def _aggregate(rows: list[dict], ks: tuple[int, ...], retrieval_only: bool) -> dict:
    """Collapse per-question rows into headline numbers."""

    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    summary: dict = {
        "questions": len(rows),
        "answerable": len(answerable),
        "retrieval": {
            "pre": {f"recall@{k}": mean(r["pre"][f"recall@{k}"] for r in answerable) for k in ks},
            "post": {f"recall@{k}": mean(r["post"][f"recall@{k}"] for r in answerable) for k in ks},
            "mrr_pre": mean(r["pre"]["rr"] for r in answerable),
            "mrr_post": mean(r["post"]["rr"] for r in answerable),
        },
    }
    if not retrieval_only:
        summary["answer_quality"] = {
            "faithfulness_mean": mean(r.get("faithfulness") for r in answerable),
            "answer_relevance_mean": mean(r.get("answer_relevance") for r in answerable),
            "fact_present_rate": mean(1.0 if r.get("fact_present") else 0.0 for r in answerable),
        }
        if unanswerable:
            summary["robustness"] = {
                "abstention_rate": mean(1.0 if r.get("abstained") else 0.0 for r in unanswerable),
                "unanswerable": len(unanswerable),
            }
    return {"summary": summary, "rows": rows}


def _print_report(report: dict, ks: tuple[int, ...], retrieval_only: bool) -> None:
    s = report["summary"]
    print("\n=== Retrieval ===")
    print("  recall@k     " + "  ".join(f"@{k}" for k in ks))
    print("  pre-rerank   " + "  ".join(f"{s['retrieval']['pre'][f'recall@{k}']:.2f}" for k in ks))
    print("  post-rerank  " + "  ".join(f"{s['retrieval']['post'][f'recall@{k}']:.2f}" for k in ks))
    print(f"  MRR   pre={s['retrieval']['mrr_pre']:.3f}  post={s['retrieval']['mrr_post']:.3f}")
    if not retrieval_only:
        aq = s["answer_quality"]
        print("\n=== Answer quality (LLM judge, 1-5) ===")
        print(f"  faithfulness   {aq['faithfulness_mean']:.2f}")
        print(f"  relevance      {aq['answer_relevance_mean']:.2f}")
        print(f"  key-fact present rate  {aq['fact_present_rate']:.2f}")
        if "robustness" in s:
            print(f"\n=== Robustness ===\n  abstention on {s['robustness']['unanswerable']} out-of-corpus Qs: {s['robustness']['abstention_rate']:.2f}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the RAG retrieval pipeline.")
    parser.add_argument("--reindex", action="store_true", help="(re)index the corpus before evaluating")
    parser.add_argument("--retrieval-only", action="store_true", help="skip generation and the LLM judge")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Qdrant collection to use")
    parser.add_argument("--k", default=",".join(map(str, DEFAULT_KS)), help="comma-separated k values")
    parser.add_argument("--report", default=None, help="path to write the JSON report (default: evals/reports/<ts>.json)")
    args = parser.parse_args()

    ks = tuple(int(k) for k in args.k.split(","))
    # Isolate from prod/local by using a dedicated collection.
    settings = get_settings().model_copy(update={"qdrant_collection": args.collection})

    if args.reindex:
        print(f"Reindexing corpus into '{args.collection}'...")
        reindex(settings)

    print(f"Evaluating {'(retrieval only)' if args.retrieval_only else '(full)'} against '{args.collection}'...")
    report = evaluate(settings, ks, args.retrieval_only)
    _print_report(report, ks, args.retrieval_only)

    REPORTS_DIR.mkdir(exist_ok=True)
    out = Path(args.report) if args.report else REPORTS_DIR / f"eval-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report written to {out}")


if __name__ == "__main__":
    main()
