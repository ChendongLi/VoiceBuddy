FROM python:3.11-slim

# Non-root user for security
RUN groupadd --system app && useradd --system --gid app app

WORKDIR /app

# Install dependencies first (better layer caching)
COPY server/requirements.txt ./server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

# Copy application code
COPY server/ ./server/
COPY static/ ./static/

# Switch to non-root user
USER app

EXPOSE 8080

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
