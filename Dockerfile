FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    gcc \
    g++ \
    make \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies with specific versions
RUN pip install --no-cache-dir \
    telethon==1.36.0 \
    py-tgcalls==1.0.0 \
    tgcrypto==1.2.5 \
    yt-dlp==2025.01.26

# Copy application files
COPY . /app/

# Environment variables
ENV DATA_DIR=/data
ENV PYTHONUNBUFFERED=1

# Create data directory
RUN mkdir -p /data

# Volume for persistent data
VOLUME ["/data"]

# Run the bot
CMD ["python", "fight.py"]
