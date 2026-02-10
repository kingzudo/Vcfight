#!/bin/bash

echo "🚀 Deploying Telegram VC Fighting Bot..."
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed!"
    echo "Please install Docker first: https://docs.docker.com/get-docker/"
    exit 1
fi

echo "✅ Docker found"
echo ""

# Stop and remove existing container if exists
if [ "$(docker ps -aq -f name=vc-fighting-bot)" ]; then
    echo "🛑 Stopping existing container..."
    docker stop vc-fighting-bot 2>/dev/null
    echo "🗑️  Removing existing container..."
    docker rm vc-fighting-bot 2>/dev/null
fi

# Build new image
echo "🔨 Building Docker image..."
docker build -t vc-bot . --no-cache

if [ $? -ne 0 ]; then
    echo "❌ Build failed!"
    exit 1
fi

echo "✅ Build successful!"
echo ""

# Run container
echo "🚀 Starting bot..."
docker run -d \
  --name vc-fighting-bot \
  --restart unless-stopped \
  vc-bot

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Bot deployed successfully!"
    echo ""
    echo "📋 Useful commands:"
    echo "  View logs:      docker logs -f vc-fighting-bot"
    echo "  Stop bot:       docker stop vc-fighting-bot"
    echo "  Restart bot:    docker restart vc-fighting-bot"
    echo "  Remove bot:     docker rm -f vc-fighting-bot"
    echo ""
    echo "🎵 Bot is now running!"
    echo ""
    
    # Show logs
    sleep 2
    echo "📜 Bot logs (Press Ctrl+C to exit):"
    echo "---"
    docker logs -f vc-fighting-bot
else
    echo "❌ Failed to start bot!"
    exit 1
fi
