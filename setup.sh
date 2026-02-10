#!/bin/bash

echo "🚀 VC Fighting Bot - Auto Setup Script"
echo "======================================"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker not found!${NC}"
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    echo -e "${GREEN}✅ Docker installed!${NC}"
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo -e "${YELLOW}⚠️ Docker Compose not found!${NC}"
    echo "Installing Docker Compose..."
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
    echo -e "${GREEN}✅ Docker Compose installed!${NC}"
fi

# Create directories
mkdir -p sessions downloads

# Build and run
echo -e "${YELLOW}🔨 Building Docker image...${NC}"
docker-compose build

echo -e "${YELLOW}🚀 Starting bot...${NC}"
docker-compose up -d

echo ""
echo -e "${GREEN}✅ Bot is running!${NC}"
echo ""
echo "📋 Useful commands:"
echo "  docker-compose logs -f          # View logs"
echo "  docker-compose restart          # Restart bot"
echo "  docker-compose stop             # Stop bot"
echo "  docker-compose down             # Stop and remove"
echo ""
echo -e "${GREEN}🎉 Setup complete! Bot is ready to use.${NC}"
