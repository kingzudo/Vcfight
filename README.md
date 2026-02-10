# Telegram VC Fighting Bot

## Features
- 🔵 **Default Account Mode**: Owner sets up a default account that anyone can use
- 🟢 **Custom Account Mode**: Users can login with their own Telegram account
- 🎵 Play audio files or YouTube URLs in voice chats
- 🔐 Supports 2FA authentication
- 🚀 Easy deployment with Docker

## Setup

### Owner Commands
- `/setdefault` - Configure default account (Owner only)
- `/logout` - Logout from your custom account

### User Commands
- `/start` - Start the bot and choose account type

## Deployment

### Using Docker

1. Build the Docker image:
```bash
docker build -t vc-fighting-bot .
```

2. Run the container:
```bash
docker run -d --name vc-bot vc-fighting-bot
```

### Manual Deployment

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Install FFmpeg:
```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg
```

3. Run the bot:
```bash
python bot.py
```

## Usage Flow

### Default Account Mode
1. Owner uses `/setdefault` to configure default account
2. User clicks "Default Account" button
3. Sends group link
4. Sends audio file or YouTube URL
5. Bot plays audio in the group VC

### Custom Account Mode
1. User clicks "Login My Account" button
2. Sends their phone number
3. Enters OTP (and 2FA if enabled)
4. Sends group link
5. Sends audio file or YouTube URL
6. Bot plays audio using user's account

## Configuration

All credentials are hardcoded in `bot.py`:
- Owner ID: 7661825494
- Bot Token: Already configured
- API ID & Hash: Already configured

## Error Handling

- All errors are sent to the owner via DM
- User-friendly error messages for common issues
- Automatic session cleanup

## Requirements

- Python 3.10+
- FFmpeg
- Active internet connection

## Note

Make sure the bot is added to the target group and the voice chat is active before trying to play audio.
