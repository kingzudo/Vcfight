FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    telethon \
    pytgcalls \
    tgcrypto \
    yt-dlp

COPY app/ /app/

ENV DATA_DIR=/data
VOLUME ["/data"]

CMD ["python", "main.py"]
