FROM python:3.11-slim

RUN groupadd --system app && useradd --system --gid app app

WORKDIR /app

# Install dependencies
COPY pyproject.toml poetry.lock ./
RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi --only main

# Copy source
COPY src/ ./src/

USER app

EXPOSE 8765

CMD ["python", "src/server.py"]
