import os
import asyncio
import re
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeExpired, PhoneCodeInvalid, PasswordHashInvalid
from pytgcalls import PyTgCalls
from pytgcalls.types import AudioPiped, Update
from pytgcalls.exceptions import NoActiveGroupCall, AlreadyJoinedError
import yt_dlp
import logging

# Configuration
OWNER_ID = 7661825494
BOT_TOKEN = "7845373810:AAH5jWEJhLoObAwFXxjK6KFpwGZ2Y1N2fE0"
API_ID = 33628258
API_HASH = "0850762925b9c1715b9b122f7b753128"

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Bot
bot = Client(
    "vc_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
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
        r'telegram\.me/([a-zA-Z0-9_]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, link)
        if match:
            return match.group(1)
    return None

# Download YouTube audio
async def download_youtube_audio(url):
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': '/tmp/%(id)s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            return filename
    except Exception as e:
        logger.error(f"YouTube download error: {e}")
        return None

# Start command
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔵 Default Account", callback_data="use_default")],
        [InlineKeyboardButton("🟢 Login My Account", callback_data="use_custom")]
    ])
    await message.reply_text(
        "**Welcome to VC Fighting Bot!**\n\n"
        "Choose an option:\n"
        "• **Default Account**: Use pre-configured account\n"
        "• **Login My Account**: Use your own account\n\n"
        "Owner commands: /setdefault, /logout",
        reply_markup=keyboard
    )

# Set default account (Owner only)
@bot.on_message(filters.command("setdefault") & filters.private & filters.user(OWNER_ID))
async def set_default_account(client, message: Message):
    state = get_user_state(message.from_user.id)
    state.step = "default_phone"
    state.data = {}
    await message.reply_text("📱 Send the phone number for default account (with country code, e.g., +910000000000):")

# Logout command
@bot.on_message(filters.command("logout") & filters.private)
async def logout_command(client, message: Message):
    user_id = message.from_user.id
    if user_id in user_accounts:
        try:
            await user_accounts[user_id].stop()
            del user_accounts[user_id]
            if user_id in user_calls:
                del user_calls[user_id]
            await message.reply_text("✅ Logged out successfully!")
        except Exception as e:
            await message.reply_text(f"❌ Logout failed: {str(e)}")
    else:
        await message.reply_text("❌ No active session found!")

# Callback query handler
@bot.on_callback_query()
async def callback_handler(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    state = get_user_state(user_id)

    if data == "use_default":
        if default_account is None:
            await callback_query.answer("❌ Default account not configured!", show_alert=True)
            if user_id == OWNER_ID:
                await callback_query.message.reply_text("Please use /setdefault to configure default account first.")
            return
        state.step = "default_group"
        state.data = {"mode": "default"}
        await callback_query.message.reply_text("📎 Send the group/channel link where you want to play audio:")

    elif data == "use_custom":
        state.step = "custom_phone"
        state.data = {"mode": "custom"}
        await callback_query.message.reply_text("📱 Send your phone number (with country code, e.g., +910000000000):")

    await callback_query.answer()

# Message handler for steps
@bot.on_message(filters.private & filters.text)
async def message_handler(client, message: Message):
    user_id = message.from_user.id
    state = get_user_state(user_id)
    text = message.text

    if not state.step:
        return

    try:
        # Default account setup
        if state.step == "default_phone":
            phone = text.strip()
            state.data["phone"] = phone
            
            try:
                user_client = Client(
                    f"default_session",
                    api_id=API_ID,
                    api_hash=API_HASH
                )
                await user_client.connect()
                sent_code = await user_client.send_code(phone)
                state.data["phone_code_hash"] = sent_code.phone_code_hash
                state.data["client"] = user_client
                state.step = "default_otp"
                await message.reply_text("📨 OTP sent! Please send the OTP code:")
            except Exception as e:
                await message.reply_text(f"❌ Error: {str(e)}")
                await bot.send_message(OWNER_ID, f"⚠️ Error in default setup: {str(e)}")
                state.step = None

        elif state.step == "default_otp":
            otp = text.strip()
            try:
                user_client = state.data["client"]
                await user_client.sign_in(
                    state.data["phone"],
                    state.data["phone_code_hash"],
                    otp
                )
                global default_account, default_calls
                default_account = user_client
                default_calls = PyTgCalls(default_account)
                await default_calls.start()
                await message.reply_text("✅ Default account configured successfully!")
                state.step = None
            except SessionPasswordNeeded:
                state.step = "default_2fa"
                await message.reply_text("🔐 2FA enabled. Please send your 2FA password:")
            except Exception as e:
                await message.reply_text(f"❌ Error: {str(e)}")
                await bot.send_message(OWNER_ID, f"⚠️ Error in OTP verification: {str(e)}")
                state.step = None

        elif state.step == "default_2fa":
            password = text.strip()
            try:
                user_client = state.data["client"]
                await user_client.check_password(password)
                global default_account, default_calls
                default_account = user_client
                default_calls = PyTgCalls(default_account)
                await default_calls.start()
                await message.reply_text("✅ Default account configured successfully!")
                state.step = None
            except Exception as e:
                await message.reply_text(f"❌ Error: {str(e)}")
                await bot.send_message(OWNER_ID, f"⚠️ Error in 2FA: {str(e)}")
                state.step = None

        # Custom account login
        elif state.step == "custom_phone":
            phone = text.strip()
            state.data["phone"] = phone
            
            try:
                user_client = Client(
                    f"user_{user_id}",
                    api_id=API_ID,
                    api_hash=API_HASH
                )
                await user_client.connect()
                sent_code = await user_client.send_code(phone)
                state.data["phone_code_hash"] = sent_code.phone_code_hash
                state.data["client"] = user_client
                state.step = "custom_otp"
                await message.reply_text("📨 OTP sent! Please send the OTP code:")
            except Exception as e:
                await message.reply_text(f"❌ Error: {str(e)}")
                state.step = None

        elif state.step == "custom_otp":
            otp = text.strip()
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
                await message.reply_text("✅ Logged in successfully!\n\n📎 Now send the group/channel link:")
            except SessionPasswordNeeded:
                state.step = "custom_2fa"
                await message.reply_text("🔐 2FA enabled. Please send your 2FA password:")
            except Exception as e:
                await message.reply_text(f"❌ Error: {str(e)}")
                state.step = None

        elif state.step == "custom_2fa":
            password = text.strip()
            try:
                user_client = state.data["client"]
                await user_client.check_password(password)
                user_accounts[user_id] = user_client
                user_calls[user_id] = PyTgCalls(user_client)
                await user_calls[user_id].start()
                state.step = "custom_group"
                await message.reply_text("✅ Logged in successfully!\n\n📎 Now send the group/channel link:")
            except Exception as e:
                await message.reply_text(f"❌ Error: {str(e)}")
                state.step = None

        # Group link handling
        elif state.step in ["default_group", "custom_group"]:
            chat_username = extract_chat_id(text)
            if not chat_username:
                await message.reply_text("❌ Invalid link! Please send a valid Telegram group/channel link.")
                return
            
            state.data["chat_id"] = chat_username
            state.step = "audio_input"
            await message.reply_text("🎵 Send audio file or YouTube URL:")

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
                await message.reply_text("❌ Session expired! Please start again.")
                state.step = None
                return

            # Join group first
            try:
                await client_to_use.join_chat(chat_id)
                await asyncio.sleep(2)
            except Exception as e:
                logger.info(f"Join chat info: {e}")

            # Get chat
            try:
                chat = await client_to_use.get_chat(chat_id)
                actual_chat_id = chat.id
            except Exception as e:
                await message.reply_text(f"❌ Cannot access group: {str(e)}")
                state.step = None
                return

            # Handle audio
            audio_path = None
            if text.startswith("http"):
                await message.reply_text("⏳ Downloading from YouTube...")
                audio_path = await download_youtube_audio(text)
            else:
                await message.reply_text("❌ Please send a valid YouTube URL or audio file!")
                return

            if not audio_path or not os.path.exists(audio_path):
                await message.reply_text("❌ Failed to get audio!")
                state.step = None
                return

            # Play audio
            try:
                await calls_to_use.play(
                    actual_chat_id,
                    AudioPiped(audio_path)
                )
                await message.reply_text(f"✅ Playing audio in {chat.title}!")
            except NoActiveGroupCall:
                await message.reply_text("❌ No active voice chat in the group! Please start VC first.")
            except AlreadyJoinedError:
                await message.reply_text("❌ Already playing in this group!")
            except Exception as e:
                await message.reply_text(f"❌ Error: {str(e)}")
                await bot.send_message(OWNER_ID, f"⚠️ Play error: {str(e)}")
            finally:
                state.step = None
                # Cleanup
                try:
                    if os.path.exists(audio_path):
                        os.remove(audio_path)
                except:
                    pass

    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")
        await bot.send_message(OWNER_ID, f"⚠️ Critical error: {str(e)}")
        state.step = None

# Handle audio files
@bot.on_message(filters.private & filters.audio | filters.voice)
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
            await message.reply_text("❌ Session expired! Please start again.")
            state.step = None
            return

        # Download audio
        await message.reply_text("⏳ Downloading audio...")
        audio_path = await message.download()

        # Join group
        try:
            await client_to_use.join_chat(chat_id)
            await asyncio.sleep(2)
        except Exception as e:
            logger.info(f"Join chat info: {e}")

        # Get chat
        try:
            chat = await client_to_use.get_chat(chat_id)
            actual_chat_id = chat.id
        except Exception as e:
            await message.reply_text(f"❌ Cannot access group: {str(e)}")
            state.step = None
            return

        # Play audio
        try:
            await calls_to_use.play(
                actual_chat_id,
                AudioPiped(audio_path)
            )
            await message.reply_text(f"✅ Playing audio in {chat.title}!")
        except NoActiveGroupCall:
            await message.reply_text("❌ No active voice chat in the group! Please start VC first.")
        except AlreadyJoinedError:
            await message.reply_text("❌ Already playing in this group!")
        except Exception as e:
            await message.reply_text(f"❌ Error: {str(e)}")
            await bot.send_message(OWNER_ID, f"⚠️ Play error: {str(e)}")
        finally:
            state.step = None
            # Cleanup
            try:
                if os.path.exists(audio_path):
                    os.remove(audio_path)
            except:
                pass

    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")
        await bot.send_message(OWNER_ID, f"⚠️ Critical error: {str(e)}")
        state.step = None

# Run bot
if __name__ == "__main__":
    logger.info("🚀 Starting VC Fighting Bot...")
    bot.run()
