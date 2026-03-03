FROM python:3.11-slim

RUN groupadd --system app && useradd --system --gid app app

WORKDIR /app

# Install only server dependencies (no torch/ML deps)
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

# Copy source
COPY src/ ./src/

USER app

EXPOSE 8765

CMD ["python", "src/server.py"]
