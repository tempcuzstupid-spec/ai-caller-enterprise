FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y ffmpeg gcc libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    python -c "import websockets, uvicorn; print(f'verified: websockets={websockets.__version__} uvicorn={uvicorn.__version__}')"
COPY . .
# ARG forces a fresh layer; helps invalidate Fly's image cache between deploys
ARG BUILD_TIME=unknown
LABEL build_time=$BUILD_TIME
CMD ["sh", "-c", "uvicorn ai_caller.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --proxy-headers --log-level info"]
