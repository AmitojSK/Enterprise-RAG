# Evaluation guide

RAG quality is measurable. Keep a versioned set of questions with expected evidence, then run it after every retrieval, prompt, or model change.

Measure:

- Retrieval recall: does the expected source appear in the top results?
- Citation accuracy: do citations actually support the answer?
- Faithfulness: is every claim supported by the retrieved context?
- Safety: are injection, cross-tenant, and unsupported-file tests rejected?

Start with at least 30 real user questions per important document collection. Review failures manually before changing the pipeline; changing models alone does not fix poor source data or chunking.
