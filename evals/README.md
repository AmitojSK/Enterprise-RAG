# RAG evaluation harness

Measures the retrieval and answer quality the pipeline is tuned for, instead of
trusting the defaults (`TOP_K=20`, `RERANK_K=8`, `SCORE_THRESHOLD=0.25`) blind.

## What it measures

| Group | Metric | Meaning |
|---|---|---|
| Retrieval | `recall@k` (pre / post rerank) | Did the expected evidence page appear in the top *k*? Pre = vector-search ceiling; post = what the reranker kept. |
| Retrieval | `MRR` (pre / post) | Mean reciprocal rank of the first relevant page. |
| Answer | `faithfulness` (1-5) | LLM judge: is every claim in the answer supported by the retrieved context? |
| Answer | `answer_relevance` (1-5) | LLM judge: does the answer address the question? |
| Answer | `fact_present_rate` | Lexical check that the known key fact made it into the answer. |
| Robustness | `abstention_rate` | On out-of-corpus questions, does it correctly refuse instead of hallucinating? |

## Ground truth

`corpus/cloud-cost-guide.pdf` is a fixture whose content we authored, one topic
per page, so the expected page numbers in `golden_set.jsonl` are ground truth
rather than guesses. Regenerate or extend it with `python -m evals.build_corpus`.

Relevance is **page-level**, matching the granularity of the app's citations.

## Running it

From the repo root, with `OPENAI_API_KEY` and Qdrant configured (as in `.env`):

```bash
python -m evals.build_corpus          # once: generate the fixture PDF
python -m evals.run_eval --reindex    # index the corpus, then full evaluation
python -m evals.run_eval --retrieval-only   # cheaper: recall@k / MRR only
```

- Uses an **isolated** Qdrant collection (`enterprise_documents_eval`), so it
  never touches the local or production libraries.
- **Costs money**: each answerable question makes embedding + rerank (+ in full
  mode, generation + judge) OpenAI calls. The 12-item set is a few cents.
- Reports print to the console and are written to `evals/reports/` (gitignored).

## How it stays honest

The harness mirrors `RAGService` step by step — same embedding model, same
`_rerank`, same system prompt and context builder — so the numbers reflect the
real pipeline, not a re-implementation. The only deliberate difference is that it
also captures the **pre-rerank** ranking, to separate retriever quality from the
reranker's contribution.

The LLM judge is the same `gpt-4o-mini` used for answers; a stronger, independent
judge model would reduce self-preference bias and is the obvious next improvement.
