"""Offline/online evaluation harness for the RAG pipeline.

Not part of the application package and not collected by pytest (it hits live
OpenAI and Qdrant and therefore costs money). Run from the repo root with
``python -m evals.run_eval``. Only ``evals.metrics`` is pure and unit-tested.
"""
