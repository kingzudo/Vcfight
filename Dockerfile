FROM python:3.10-slim-bullseye

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ✅ Install system dependencies (WITHOUT Node.js setup issues)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    gcc \
    g++ \
    libc-dev \
    libffi-dev \
    make \
    git \
    curl \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# ✅ Install Python dependencies (updated versions)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy bot script
COPY bot.py .

# Create necessary directories with proper permissions
RUN mkdir -p /app/sessions /app/data /app/cookies /tmp/downloads && \
    chmod -R 777 /app/sessions /app/data /app/cookies /tmp/downloads

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD pgrep -f "python" > /dev/null || exit 1

# Run the bot
CMD ["python", "-u", "bot.py"]
