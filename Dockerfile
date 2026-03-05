FROM python:3.11-slim

RUN groupadd --system app && useradd --system --gid app app

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements-server.txt

COPY src/ ./src/

# Create logs dir owned by app user so LatencyLogger can write to it
RUN mkdir -p /app/logs && chown app:app /app/logs

# Pre-warm Silero VAD model so first connection isn't cold
RUN python -c "from silero_vad import load_silero_vad; load_silero_vad(onnx=True); print('VAD model pre-loaded')"

USER app
EXPOSE 8765
CMD ["python", "src/server.py"]
