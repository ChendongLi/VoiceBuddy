FROM python:3.11-slim

RUN groupadd --system app && useradd --system --gid app app

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements-server.txt

COPY src/ ./src/

USER app
EXPOSE 8765
CMD ["python", "src/server.py"]
