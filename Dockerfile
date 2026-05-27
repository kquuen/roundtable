FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir setuptools>=68.0 && \
    pip install --no-cache-dir -e ".[dev]" && \
    pip install --no-cache-dir uvicorn

# Copy source
COPY roundtable/ ./roundtable/
COPY skills/ ./skills/
COPY data/ ./data/
COPY tests/ ./tests/

# Create output directories
RUN mkdir -p /app/reports

ENV PYTHONPATH=/app
EXPOSE 8000

# Default: API server
CMD uvicorn roundtable.app:app --host 0.0.0.0 --port ${PORT:-8000}
