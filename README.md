# 🎵 VC Fighting Bot - 100% Fixed Version

**Telegram Voice Chat Bot with YouTube Support**

✅ **Fixed Issues:**
- ✔️ Node.js 15.0.0+ requirement solved
- ✔️ All dependencies auto-install
- ✔️ No manual setup needed
- ✔️ Production-ready Docker setup

---

## 🚀 Quick Start (1 Command)

```bash
chmod +x setup.sh && ./setup.sh
```

That's it! Bot will automatically install everything and start.

---

## 📋 Manual Installation

### Prerequisites
- Docker & Docker Compose
- Linux/VPS

### Steps

1️⃣ **Clone/Upload Files**
```bash
# Upload all files to your server
```

2️⃣ **Build Docker Image**
```bash
docker-compose build
```

3️⃣ **Start Bot**
```bash
docker-compose up -d
```

4️⃣ **View Logs**
```bash
docker-compose logs -f
```

---

## 🛠️ Bot Commands

### For Users:
- `/start` - Start the bot
- `/stop` - Stop playing audio
- `/logout` - Logout from your account

### For Owner (ID: 7661825494):
- `/setdefault` - Setup default account for all users

---

## 📊 Bot Features

✅ Play YouTube audio in Telegram voice chat  
✅ Support for audio files & voice messages  
✅ Default account mode (no login needed)  
✅ Custom account mode (use your own)  
✅ Auto-cleanup downloaded files  
✅ Error reporting to owner  
✅ Session management  
✅ 2FA support  

---

## 🔧 Configuration

Edit these variables in `bot.py`:

```python
OWNER_ID = 7661825494  # Your Telegram user ID
BOT_TOKEN = "YOUR_BOT_TOKEN"
API_ID = 33628258
API_HASH = "YOUR_API_HASH"
```

---

## 📁 File Structure

```
vc_bot_fixed/
├── bot.py              # Main bot code
├── requirements.txt    # Python dependencies
├── Dockerfile          # Docker configuration
├── docker-compose.yml  # Docker Compose setup
├── setup.sh           # Auto-install script
├── .dockerignore      # Docker ignore file
├── sessions/          # Session storage (auto-created)
└── downloads/         # Temp audio files (auto-created)
```

---

## 🐛 Troubleshooting

### Problem: Bot not starting
**Solution:**
```bash
docker-compose logs -f
```
Check logs for errors.

### Problem: Can't join voice chat
**Solution:**
1. Make sure voice chat is active in the group
2. Account must be admin or have join permissions
3. Try restarting: `docker-compose restart`

### Problem: Node.js error
**Solution:** This is FIXED in this version! Node.js 18 is pre-installed.

---

## 🔄 Update Bot

```bash
# Stop bot
docker-compose down

# Rebuild
docker-compose build --no-cache

# Start again
docker-compose up -d
```

---

## 📱 Usage Flow

1. User sends `/start`
2. Choose **Default Account** or **Login My Account**
3. If custom: Login with phone + OTP
4. Send group link (e.g., `https://t.me/yourgroup`)
5. Send YouTube URL or audio file
6. Bot joins voice chat and plays audio
7. Use `/stop` to stop

---

## ⚡ Performance

- **RAM Usage:** ~200-500MB
- **CPU:** Minimal (<10%)
- **Storage:** ~100MB + temp audio files
- **Concurrent Users:** Supports multiple users

---

## 🔒 Security

- Sessions stored securely
- 2FA support enabled
- Auto-cleanup of sensitive data
- Owner-only commands protected

---

## 📞 Support

If you face any issues:
1. Check logs: `docker-compose logs -f`
2. Restart: `docker-compose restart`
3. Contact bot owner

---

## 📄 License

Free to use for personal projects.

---

## 🎉 Credits

Built with:
- Pyrogram (Telegram client)
- py-tgcalls (Voice chat)
- yt-dlp (YouTube downloader)
- Docker (Containerization)

---

**Made with ❤️ for the Telegram community**
