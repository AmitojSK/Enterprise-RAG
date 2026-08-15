# The image contains only the API and its runtime dependencies.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
# Install after copying the package because pip needs the source tree to build it.
RUN pip install --no-cache-dir .
EXPOSE 8000
# Do not use --reload in containers: it is a development-only file watcher.
CMD ["uvicorn", "enterprise_rag.main:app", "--host", "0.0.0.0", "--port", "8000"]
