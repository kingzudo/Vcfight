FROM python:3.9-slim-bullseye

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    gcc \
    g++ \
    libc-dev \
    libffi-dev \
    make \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy bot script
COPY bot.py .

# Create necessary directories
RUN mkdir -p /app/sessions /tmp/downloads

# Set permissions
RUN chmod -R 777 /app/sessions /tmp/downloads

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD pgrep -f "python" > /dev/null || exit 1

# Run the bot
CMD ["python", "-u", "bot.py"]
