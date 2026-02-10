version: '3.8'

services:
  vc-bot:
    build: .
    container_name: vc-fighting-bot
    restart: unless-stopped
    volumes:
      - ./sessions:/app/sessions
      - ./downloads:/tmp/downloads
    environment:
      - PYTHONUNBUFFERED=1
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
