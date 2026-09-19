# Evaluation guide

RAG quality is measurable. Keep a versioned set of questions with expected evidence, then run it after every retrieval, prompt, or model change.

Measure:

- Retrieval recall: does the expected source appear in the top results?
- Citation accuracy: do citations actually support the answer?
- Faithfulness: is every claim supported by the retrieved context?
- Safety: are injection and unsupported-file tests rejected?

Start with at least 30 real user questions per important document collection. Review failures manually before changing the pipeline; changing models alone does not fix poor source data or chunking.

## The harness (`evals/`)

An implemented version of the above lives in [`evals/`](../evals/README.md):

```bash
python -m evals.build_corpus         # generate the fixture corpus (once)
python -m evals.run_eval --reindex   # recall@k, MRR (pre/post rerank), faithfulness, abstention
python -m evals.run_eval --retrieval-only   # cheaper: retrieval metrics only
```

It runs the real pipeline against an **isolated** Qdrant collection
(`enterprise_documents_eval`), scores retrieval with pure, unit-tested metric
functions, and uses an LLM judge for faithfulness and answer-relevance. Ground
truth comes from an authored fixture PDF, so the expected pages are correct by
construction. Extend `evals/golden_set.jsonl` with questions over the real
documents to make the numbers meaningful beyond the fixture.
