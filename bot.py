import os
import asyncio
import re
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeExpired, PhoneCodeInvalid, PasswordHashInvalid, FloodWait
from pytgcalls import PyTgCalls
from pytgcalls.types import AudioPiped, Update
from pytgcalls.types.input_stream import AudioParameters, InputAudioStream
from pytgcalls.types.input_stream.quality import HighQualityAudio
import yt_dlp
import logging
from pathlib import Path

# Configuration
OWNER_ID = 7661825494
BOT_TOKEN = "7845373810:AAH5jWEJhLoObAwFXxjK6KFpwGZ2Y1N2fE0"
API_ID = 33628258
API_HASH = "0850762925b9c1715b9b122f7b753128"

# Setup directories
Path("/tmp/downloads").mkdir(exist_ok=True, parents=True)
Path("/app/sessions").mkdir(exist_ok=True, parents=True)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Bot
bot = Client(
    "vc_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir="/app/sessions"
)

# Storage for user sessions and states
user_states = {}
default_account = None
user_accounts = {}
default_calls = None
user_calls = {}

# User state management
class UserState:
    def __init__(self):
        self.step = None
        self.data = {}

def get_user_state(user_id):
    if user_id not in user_states:
        user_states[user_id] = UserState()
    return user_states[user_id]

# Extract group chat ID from link
def extract_chat_id(link):
    patterns = [
        r't\.me/([a-zA-Z0-9_]+)',
        r't\.me/joinchat/([a-zA-Z0-9_-]+)',
        r'telegram\.me/([a-zA-Z0-9_]+)',
        r't\.me/\+([a-zA-Z0-9_-]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, link)
        if match:
            return match.group(1)
    return link.strip()

# Download YouTube audio
async def download_youtube_audio(url):
    try:
        output_path = f'/tmp/downloads/{int(asyncio.get_event_loop().time())}'
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'{output_path}.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }
        
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            return f"{output_path}.mp3"
    except Exception as e:
        logger.error(f"YouTube download error: {e}")
        return None

async def send_error_to_owner(error_msg):
    try:
        await bot.send_message(OWNER_ID, f"⚠️ **Bot Error:**\n\n`{error_msg}`")
    except:
        pass

# Start command
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message):
    try:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔵 Default Account", callback_data="use_default")],
            [InlineKeyboardButton("🟢 Login My Account", callback_data="use_custom")]
        ])
        await message.reply_text(
            "**🎵 Welcome to VC Fighting Bot!**\n\n"
            "Choose an option:\n"
            "• **Default Account**: Use pre-configured account\n"
            "• **Login My Account**: Use your own account\n\n"
            "**Owner Commands:**\n"
            "• /setdefault - Setup default account\n"
            "• /logout - Logout from your account",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Start command error: {e}")
        await send_error_to_owner(f"Start command error: {str(e)}")

# Set default account (Owner only)
@bot.on_message(filters.command("setdefault") & filters.private & filters.user(OWNER_ID))
async def set_default_account(client, message: Message):
    try:
        state = get_user_state(message.from_user.id)
        state.step = "default_phone"
        state.data = {}
        await message.reply_text("📱 **Setup Default Account**\n\nSend the phone number (with country code):\n\nExample: `+919876543210`")
    except Exception as e:
        logger.error(f"Setdefault error: {e}")
        await send_error_to_owner(f"Setdefault error: {str(e)}")

# Logout command
@bot.on_message(filters.command("logout") & filters.private)
async def logout_command(client, message: Message):
    try:
        user_id = message.from_user.id
        if user_id in user_accounts:
            try:
                if user_id in user_calls:
                    try:
                        await user_calls[user_id].stop()
                    except:
                        pass
                    del user_calls[user_id]
                
                await user_accounts[user_id].stop()
                
                # Delete session file
                session_file = f"/app/sessions/user_{user_id}.session"
                if os.path.exists(session_file):
                    os.remove(session_file)
                
                del user_accounts[user_id]
                await message.reply_text("✅ Logged out successfully!")
            except Exception as e:
                await message.reply_text(f"❌ Logout failed: {str(e)}")
                await send_error_to_owner(f"Logout error: {str(e)}")
        else:
            await message.reply_text("❌ No active session found!")
    except Exception as e:
        logger.error(f"Logout error: {e}")
        await send_error_to_owner(f"Logout command error: {str(e)}")

# Callback query handler
@bot.on_callback_query()
async def callback_handler(client, callback_query):
    try:
        user_id = callback_query.from_user.id
        data = callback_query.data
        state = get_user_state(user_id)

        if data == "use_default":
            if default_account is None:
                await callback_query.answer("❌ Default account not configured!", show_alert=True)
                if user_id == OWNER_ID:
                    await callback_query.message.reply_text("⚠️ Please use /setdefault to configure default account first.")
                return
            
            state.step = "default_group"
            state.data = {"mode": "default"}
            await callback_query.message.reply_text("📎 **Send Group Link**\n\nSend the Telegram group/channel link where you want to play audio.\n\nExample: `https://t.me/groupname`")

        elif data == "use_custom":
            state.step = "custom_phone"
            state.data = {"mode": "custom"}
            await callback_query.message.reply_text("📱 **Login to Your Account**\n\nSend your phone number (with country code):\n\nExample: `+919876543210`")

        await callback_query.answer()
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await send_error_to_owner(f"Callback error: {str(e)}")

# Message handler for steps
@bot.on_message(filters.private & filters.text & ~filters.command(["start", "setdefault", "logout"]))
async def message_handler(client, message: Message):
    global default_account, default_calls
    
    user_id = message.from_user.id
    state = get_user_state(user_id)
    text = message.text

    if not state.step:
        return

    try:
        # Default account setup
        if state.step == "default_phone":
            phone = text.strip().replace(" ", "")
            state.data["phone"] = phone
            
            processing_msg = await message.reply_text("⏳ Sending OTP...")
            
            try:
                user_client = Client(
                    "default_session",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    workdir="/app/sessions"
                )
                await user_client.connect()
                sent_code = await user_client.send_code(phone)
                state.data["phone_code_hash"] = sent_code.phone_code_hash
                state.data["client"] = user_client
                state.step = "default_otp"
                await processing_msg.edit_text("📨 **OTP Sent!**\n\nPlease send the OTP code you received:")
            except FloodWait as e:
                await processing_msg.edit_text(f"⏳ Too many requests! Wait {e.value} seconds and try again.")
                state.step = None
            except Exception as e:
                await processing_msg.edit_text(f"❌ Error: {str(e)}")
                await send_error_to_owner(f"Default phone error: {str(e)}")
                state.step = None

        elif state.step == "default_otp":
            otp = text.strip().replace(" ", "").replace("-", "")
            processing_msg = await message.reply_text("⏳ Verifying OTP...")
            
            try:
                user_client = state.data["client"]
                await user_client.sign_in(
                    state.data["phone"],
                    state.data["phone_code_hash"],
                    otp
                )
                
                default_account = user_client
                default_calls = PyTgCalls(default_account)
                await default_calls.start()
                
                await processing_msg.edit_text("✅ **Default account configured successfully!**\n\nUsers can now use the bot with default account.")
                state.step = None
            except SessionPasswordNeeded:
                state.step = "default_2fa"
                await processing_msg.edit_text("🔐 **2FA Enabled**\n\nPlease send your 2FA password:")
            except PhoneCodeInvalid:
                await processing_msg.edit_text("❌ Invalid OTP! Please use /setdefault to try again.")
                state.step = None
            except Exception as e:
                await processing_msg.edit_text(f"❌ Error: {str(e)}")
                await send_error_to_owner(f"Default OTP error: {str(e)}")
                state.step = None

        elif state.step == "default_2fa":
            password = text.strip()
            processing_msg = await message.reply_text("⏳ Verifying 2FA password...")
            
            try:
                user_client = state.data["client"]
                await user_client.check_password(password)
                
                default_account = user_client
                default_calls = PyTgCalls(default_account)
                await default_calls.start()
                
                await processing_msg.edit_text("✅ **Default account configured successfully!**\n\nUsers can now use the bot with default account.")
                state.step = None
            except PasswordHashInvalid:
                await processing_msg.edit_text("❌ Invalid 2FA password! Please use /setdefault to try again.")
                state.step = None
            except Exception as e:
                await processing_msg.edit_text(f"❌ Error: {str(e)}")
                await send_error_to_owner(f"Default 2FA error: {str(e)}")
                state.step = None

        # Custom account login
        elif state.step == "custom_phone":
            phone = text.strip().replace(" ", "")
            state.data["phone"] = phone
            
            processing_msg = await message.reply_text("⏳ Sending OTP...")
            
            try:
                user_client = Client(
                    f"user_{user_id}",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    workdir="/app/sessions"
                )
                await user_client.connect()
                sent_code = await user_client.send_code(phone)
                state.data["phone_code_hash"] = sent_code.phone_code_hash
                state.data["client"] = user_client
                state.step = "custom_otp"
                await processing_msg.edit_text("📨 **OTP Sent!**\n\nPlease send the OTP code you received:")
            except FloodWait as e:
                await processing_msg.edit_text(f"⏳ Too many requests! Wait {e.value} seconds and try again.")
                state.step = None
            except Exception as e:
                await processing_msg.edit_text(f"❌ Error: {str(e)}")
                await send_error_to_owner(f"Custom phone error: {str(e)}")
                state.step = None

        elif state.step == "custom_otp":
            otp = text.strip().replace(" ", "").replace("-", "")
            processing_msg = await message.reply_text("⏳ Verifying OTP...")
            
            try:
                user_client = state.data["client"]
                await user_client.sign_in(
                    state.data["phone"],
                    state.data["phone_code_hash"],
                    otp
                )
                
                user_accounts[user_id] = user_client
                user_calls[user_id] = PyTgCalls(user_client)
                await user_calls[user_id].start()
                
                state.step = "custom_group"
                await processing_msg.edit_text("✅ **Logged in successfully!**\n\n📎 Now send the group/channel link:\n\nExample: `https://t.me/groupname`")
            except SessionPasswordNeeded:
                state.step = "custom_2fa"
                await processing_msg.edit_text("🔐 **2FA Enabled**\n\nPlease send your 2FA password:")
            except PhoneCodeInvalid:
                await processing_msg.edit_text("❌ Invalid OTP! Please start again with /start")
                state.step = None
            except Exception as e:
                await processing_msg.edit_text(f"❌ Error: {str(e)}")
                await send_error_to_owner(f"Custom OTP error: {str(e)}")
                state.step = None

        elif state.step == "custom_2fa":
            password = text.strip()
            processing_msg = await message.reply_text("⏳ Verifying 2FA password...")
            
            try:
                user_client = state.data["client"]
                await user_client.check_password(password)
                
                user_accounts[user_id] = user_client
                user_calls[user_id] = PyTgCalls(user_client)
                await user_calls[user_id].start()
                
                state.step = "custom_group"
                await processing_msg.edit_text("✅ **Logged in successfully!**\n\n📎 Now send the group/channel link:\n\nExample: `https://t.me/groupname`")
            except PasswordHashInvalid:
                await processing_msg.edit_text("❌ Invalid 2FA password! Please start again with /start")
                state.step = None
            except Exception as e:
                await processing_msg.edit_text(f"❌ Error: {str(e)}")
                await send_error_to_owner(f"Custom 2FA error: {str(e)}")
                state.step = None

        # Group link handling
        elif state.step in ["default_group", "custom_group"]:
            chat_username = extract_chat_id(text)
            if not chat_username:
                await message.reply_text("❌ Invalid link! Please send a valid Telegram group/channel link.\n\nExample: `https://t.me/groupname`")
                return
            
            state.data["chat_id"] = chat_username
            state.step = "audio_input"
            await message.reply_text("🎵 **Send Audio**\n\nYou can send:\n• Audio file\n• Voice message\n• YouTube URL\n\nExample: `https://youtube.com/watch?v=xxxxx`")

        # Audio/URL handling
        elif state.step == "audio_input":
            mode = state.data.get("mode")
            chat_id = state.data.get("chat_id")
            
            if mode == "default":
                client_to_use = default_account
                calls_to_use = default_calls
            else:
                client_to_use = user_accounts.get(user_id)
                calls_to_use = user_calls.get(user_id)

            if not client_to_use or not calls_to_use:
                await message.reply_text("❌ Session expired! Please start again with /start")
                state.step = None
                return

            # Check if it's a URL
            if not (text.startswith("http://") or text.startswith("https://")):
                await message.reply_text("❌ Please send a valid YouTube URL or send an audio file!")
                return

            processing_msg = await message.reply_text("⏳ Processing...")

            # Join group first
            try:
                await processing_msg.edit_text("⏳ Joining group...")
                await client_to_use.join_chat(chat_id)
                await asyncio.sleep(2)
            except Exception as e:
                logger.info(f"Join chat: {e}")

            # Get chat
            try:
                chat = await client_to_use.get_chat(chat_id)
                actual_chat_id = chat.id
                await processing_msg.edit_text(f"⏳ Downloading audio from YouTube...")
            except Exception as e:
                await processing_msg.edit_text(f"❌ Cannot access group: {str(e)}")
                await send_error_to_owner(f"Chat access error: {str(e)}")
                state.step = None
                return

            # Download audio
            audio_path = await download_youtube_audio(text)
            
            if not audio_path or not os.path.exists(audio_path):
                await processing_msg.edit_text("❌ Failed to download audio! Please check the URL.")
                state.step = None
                return

            # Play audio
            try:
                await processing_msg.edit_text("⏳ Joining voice chat...")
                
                await calls_to_use.join_group_call(
                    actual_chat_id,
                    AudioPiped(audio_path),
                    stream_type=StreamType().pulse_stream
                )
                
                await processing_msg.edit_text(f"✅ **Now Playing!**\n\n📻 Group: {chat.title}\n🎵 Audio is playing in voice chat!")
                state.step = None
                
            except Exception as e:
                # Try alternative method
                try:
                    await calls_to_use.play(
                        actual_chat_id,
                        audio_path
                    )
                    await processing_msg.edit_text(f"✅ **Now Playing!**\n\n📻 Group: {chat.title}\n🎵 Audio is playing in voice chat!")
                    state.step = None
                except Exception as e2:
                    error_msg = str(e2)
                    if "No active group call" in error_msg or "GROUP_CALL_INVALID" in error_msg:
                        await processing_msg.edit_text("❌ **No Active Voice Chat!**\n\nPlease start a voice chat in the group first, then try again.")
                    elif "Already joined" in error_msg:
                        await processing_msg.edit_text("❌ Already playing in this group! Please wait for current audio to finish.")
                    else:
                        await processing_msg.edit_text(f"❌ Error playing audio: {error_msg}")
                        await send_error_to_owner(f"Play error: {error_msg}\nChat: {actual_chat_id}\nFile: {audio_path}")
                    state.step = None
            finally:
                # Cleanup after delay
                asyncio.create_task(cleanup_file(audio_path))

    except Exception as e:
        logger.error(f"Message handler error: {e}")
        await message.reply_text(f"❌ An error occurred: {str(e)}")
        await send_error_to_owner(f"Message handler error: {str(e)}")
        state.step = None

# Handle audio files
@bot.on_message(filters.private & (filters.audio | filters.voice))
async def audio_file_handler(client, message: Message):
    user_id = message.from_user.id
    state = get_user_state(user_id)
    
    if state.step != "audio_input":
        return

    try:
        mode = state.data.get("mode")
        chat_id = state.data.get("chat_id")
        
        if mode == "default":
            client_to_use = default_account
            calls_to_use = default_calls
        else:
            client_to_use = user_accounts.get(user_id)
            calls_to_use = user_calls.get(user_id)

        if not client_to_use or not calls_to_use:
            await message.reply_text("❌ Session expired! Please start again with /start")
            state.step = None
            return

        processing_msg = await message.reply_text("⏳ Processing audio...")

        # Join group
        try:
            await processing_msg.edit_text("⏳ Joining group...")
            await client_to_use.join_chat(chat_id)
            await asyncio.sleep(2)
        except Exception as e:
            logger.info(f"Join chat: {e}")

        # Get chat
        try:
            chat = await client_to_use.get_chat(chat_id)
            actual_chat_id = chat.id
        except Exception as e:
            await processing_msg.edit_text(f"❌ Cannot access group: {str(e)}")
            await send_error_to_owner(f"Chat access error: {str(e)}")
            state.step = None
            return

        # Download audio
        await processing_msg.edit_text("⏳ Downloading audio...")
        audio_path = await message.download(file_name=f"/tmp/downloads/{message.id}.mp3")

        # Play audio
        try:
            await processing_msg.edit_text("⏳ Joining voice chat...")
            
            await calls_to_use.join_group_call(
                actual_chat_id,
                AudioPiped(audio_path),
                stream_type=StreamType().pulse_stream
            )
            
            await processing_msg.edit_text(f"✅ **Now Playing!**\n\n📻 Group: {chat.title}\n🎵 Audio is playing in voice chat!")
            state.step = None
            
        except Exception as e:
            # Try alternative method
            try:
                await calls_to_use.play(
                    actual_chat_id,
                    audio_path
                )
                await processing_msg.edit_text(f"✅ **Now Playing!**\n\n📻 Group: {chat.title}\n🎵 Audio is playing in voice chat!")
                state.step = None
            except Exception as e2:
                error_msg = str(e2)
                if "No active group call" in error_msg or "GROUP_CALL_INVALID" in error_msg:
                    await processing_msg.edit_text("❌ **No Active Voice Chat!**\n\nPlease start a voice chat in the group first, then try again.")
                elif "Already joined" in error_msg:
                    await processing_msg.edit_text("❌ Already playing in this group! Please wait for current audio to finish.")
                else:
                    await processing_msg.edit_text(f"❌ Error playing audio: {error_msg}")
                    await send_error_to_owner(f"Play error: {error_msg}\nChat: {actual_chat_id}\nFile: {audio_path}")
                state.step = None
        finally:
            # Cleanup after delay
            asyncio.create_task(cleanup_file(audio_path))

    except Exception as e:
        logger.error(f"Audio file handler error: {e}")
        await message.reply_text(f"❌ An error occurred: {str(e)}")
        await send_error_to_owner(f"Audio handler error: {str(e)}")
        state.step = None

async def cleanup_file(file_path):
    """Cleanup downloaded file after some delay"""
    try:
        await asyncio.sleep(300)  # Wait 5 minutes
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up: {file_path}")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

# Import StreamType for compatibility
try:
    from pytgcalls.types.stream import StreamType
except ImportError:
    class StreamType:
        def pulse_stream(self):
            return 0

# Run bot
if __name__ == "__main__":
    try:
        logger.info("🚀 Starting VC Fighting Bot...")
        logger.info(f"Owner ID: {OWNER_ID}")
        logger.info(f"API ID: {API_ID}")
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
