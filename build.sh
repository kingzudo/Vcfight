#!/bin/bash

echo "🚀 Building Telegram VC Fighting Bot..."

# Build Docker image
docker build -t vc-fighting-bot .

if [ $? -eq 0 ]; then
    echo "✅ Build successful!"
    echo ""
    echo "To run the bot, use:"
    echo "docker run -d --name vc-bot vc-fighting-bot"
    echo ""
    echo "To view logs:"
    echo "docker logs -f vc-bot"
    echo ""
    echo "To stop the bot:"
    echo "docker stop vc-bot"
    echo ""
    echo "To restart the bot:"
    echo "docker restart vc-bot"
else
    echo "❌ Build failed!"
    exit 1
fi
