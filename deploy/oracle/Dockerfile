FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY ingestion ./ingestion
COPY scripts ./scripts
COPY dashboard ./dashboard
RUN pip install --no-cache-dir .
ENTRYPOINT ["python", "-m", "ingestion.main"]
