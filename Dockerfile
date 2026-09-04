# The image contains only the API and its runtime dependencies.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
# Install after copying the package because pip needs the source tree to build it.
RUN pip install --no-cache-dir .
EXPOSE 8000
# A shell is needed so ${PORT} expands: hosting platforms such as Render assign
# the port at runtime and expect the process to bind it. Falls back to 8000 for
# local Docker Compose, which maps the port itself. `exec` then replaces the
# shell with uvicorn so it receives SIGTERM directly and shuts down gracefully
# on redeploy, rather than being killed when the shell swallows the signal.
# Do not use --reload in containers: it is a development-only file watcher.
CMD ["sh", "-c", "exec uvicorn enterprise_rag.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
