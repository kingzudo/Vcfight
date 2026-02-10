import os
import asyncio
import re
import json
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import (
    SessionPasswordNeeded, PhoneCodeExpired, PhoneCodeInvalid, 
    PasswordHashInvalid, FloodWait, UserAlreadyParticipant, InviteHashExpired
)
from pytgcalls import PyTgCalls, StreamType
from pytgcalls.types.input_stream import AudioPiped
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
Path("/app/data").mkdir(exist_ok=True, parents=True)
Path("/app/cookies").mkdir(exist_ok=True, parents=True)

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

# Storage
user_states = {}
default_account = None
user_accounts = {}
default_calls = None
user_calls = {}
active_streams = {}
sudo_users = set()

# Files
SUDO_FILE = "/app/data/sudo_users.json"
COOKIES_FILE = "/app/cookies/youtube_cookies.txt"

def load_sudo_users():
    global sudo_users
    try:
        if os.path.exists(SUDO_FILE):
            with open(SUDO_FILE, 'r') as f:
                sudo_users = set(json.load(f))
        else:
            sudo_users = set()
    except Exception as e:
        logger.error(f"Error loading sudo users: {e}")
        sudo_users = set()

def save_sudo_users():
    try:
        with open(SUDO_FILE, 'w') as f:
            json.dump(list(sudo_users), f)
    except Exception as e:
        logger.error(f"Error saving sudo users: {e}")

load_sudo_users()

class UserState:
    def __init__(self):
        self.step = None
        self.data = {}

def get_user_state(user_id):
    if user_id not in user_states:
        user_states[user_id] = UserState()
    return user_states[user_id]

def is_authorized(user_id):
    return user_id == OWNER_ID or user_id in sudo_users

def extract_chat_info(text):
    """Extract chat username or invite link"""
    text = text.strip()
    
    # Check if it's an invite link (private group)
    invite_patterns = [
        r't\.me/\+([a-zA-Z0-9_-]+)',
        r't\.me/joinchat/([a-zA-Z0-9_-]+)',
    ]
    
    for pattern in invite_patterns:
        match = re.search(pattern, text)
        if match:
            return {"type": "invite", "value": match.group(0)}
    
    # Check if it's a username (public group/channel)
    username_patterns = [
        r't\.me/([a-zA-Z0-9_]+)',
        r'telegram\.me/([a-zA-Z0-9_]+)',
        r'@([a-zA-Z0-9_]+)',
    ]
    
    for pattern in username_patterns:
        match = re.search(pattern, text)
        if match:
            username = match.group(1)
            return {"type": "username", "value": username}
    
    # If no pattern matches, assume it's a username without @
    if text and not text.startswith('http'):
        return {"type": "username", "value": text.replace('@', '')}
    
    return None

async def join_chat_safely(client, chat_info):
    """
    Safely join a chat, handling already joined scenarios
    Returns: (success: bool, chat_id, error_msg)
    """
    try:
        if chat_info["type"] == "username":
            # For public groups/channels
            try:
                # Try to join first
                await client.join_chat(chat_info["value"])
                logger.info(f"Successfully joined: {chat_info['value']}")
            except UserAlreadyParticipant:
                # Already in the group, this is fine
                logger.info(f"Already participant in: {chat_info['value']}")
                pass
            except Exception as join_error:
                # Log but don't fail - we'll try to get chat anyway
                logger.info(f"Join attempt: {join_error}")
            
            # Get chat details
            chat = await client.get_chat(chat_info["value"])
            return True, chat.id, None
            
        else:  # invite link
            try:
                # Try to join via invite link
                chat = await client.join_chat(chat_info["value"])
                logger.info(f"Successfully joined via invite: {chat_info['value']}")
                return True, chat.id, None
            except UserAlreadyParticipant:
                # Already in group, but we need chat_id
                # For private groups, we can't get chat without being member
                # So this shouldn't happen, but log it
                logger.info(f"Already participant via invite: {chat_info['value']}")
                # Try to extract chat_id from error or return None
                return False, None, "Already in group but cannot get chat ID from invite link. Please use public username or send group message to bot."
            except InviteHashExpired:
                return False, None, "❌ Invite link expired! Please get a new invite link."
            except Exception as e:
                error_msg = str(e)
                if "USER_ALREADY_PARTICIPANT" in error_msg:
                    return False, None, "Already in group but cannot access via invite link. Please use group username if available."
                return False, None, f"Cannot join via invite: {error_msg}"
    
    except Exception as e:
        logger.error(f"Join chat error: {e}")
        return False, None, str(e)

async def download_youtube_audio(url):
    try:
        output_path = f'/tmp/downloads/{int(asyncio.get_event_loop().time())}'
        
        # Create cookies file for YouTube login
        cookies_content = """# Netscape HTTP Cookie File
# This file is generated by yt-dlp.  Do not edit.

.youtube.com	TRUE	/	TRUE	0	CONSENT	YES+
.youtube.com	TRUE	/	FALSE	0	PREF	tz=Asia.Kolkata
"""
        
        with open(COOKIES_FILE, 'w') as f:
            f.write(cookies_content)
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'{output_path}.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'nocheckcertificate': True,
            'cookiefile': COOKIES_FILE,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'extractor_args': {'youtube': {'skip': ['hls', 'dash']}},
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

async def check_and_load_session(user_id):
    """Check if user has saved session and auto-load it"""
    session_file = f"/app/sessions/user_{user_id}.session"
    if os.path.exists(session_file):
        try:
            user_client = Client(
                f"user_{user_id}",
                api_id=API_ID,
                api_hash=API_HASH,
                workdir="/app/sessions"
            )
            await user_client.start()
            user_accounts[user_id] = user_client
            user_calls[user_id] = PyTgCalls(user_client)
            await user_calls[user_id].start()
            return True
        except Exception as e:
            logger.error(f"Auto-load session error: {e}")
            return False
    return False

@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message):
    try:
        user_id = message.from_user.id
        
        if not is_authorized(user_id):
            await message.reply_text(
                "❌ **Access Denied!**\n\n"
                "This bot is only available for authorized users.\n"
                "Contact the owner for access."
            )
            return
        
        # Auto-load session if exists
        session_loaded = await check_and_load_session(user_id)
        
        if user_id in user_accounts or session_loaded:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎵 Play Audio", callback_data="play_audio")],
                [InlineKeyboardButton("🚪 Logout", callback_data="logout_account")]
            ])
            await message.reply_text(
                "**🎵 Welcome Back!**\n\n"
                "You're already logged in! ✅\n\n"
                "Choose an option:\n\n"
                "**Powered by** @zudo_userbot",
                reply_markup=keyboard
            )
        else:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔵 Default Account", callback_data="use_default")],
                [InlineKeyboardButton("🟢 Login My Account", callback_data="use_custom")]
            ])
            
            commands_text = ""
            if user_id == OWNER_ID:
                commands_text = "\n\n**Owner Commands:**\n• /setdefault - Setup default account\n• /sudo <username/userid> - Add sudo user\n• /rmsudo <username/userid> - Remove sudo user\n• /sudolist - List all sudo users"
            
            await message.reply_text(
                "**🎵 Welcome to VC Fighting Bot!**\n\n"
                "Choose an option:\n"
                "• **Default Account**: Use pre-configured account\n"
                "• **Login My Account**: Use your own account\n\n"
                "**Commands:**\n"
                "• /logout - Logout from your account\n"
                "• /stop - Stop playing audio"
                f"{commands_text}\n\n"
                "**Powered by** @zudo_userbot",
                reply_markup=keyboard
            )
    except Exception as e:
        logger.error(f"Start command error: {e}")
        await send_error_to_owner(f"Start command error: {str(e)}")

@bot.on_message(filters.command("sudo") & filters.private & filters.user(OWNER_ID))
async def add_sudo(client, message: Message):
    try:
        if len(message.command) < 2:
            await message.reply_text("❌ **Usage:** `/sudo <username or userid>`\n\n**Example:** `/sudo @username` or `/sudo 123456789`")
            return
        
        user_input = message.command[1]
        
        try:
            if user_input.startswith('@'):
                user = await client.get_users(user_input[1:])
            else:
                user = await client.get_users(int(user_input))
            
            if user.id in sudo_users:
                await message.reply_text(f"ℹ️ **{user.first_name}** is already a sudo user!")
            else:
                sudo_users.add(user.id)
                save_sudo_users()
                await message.reply_text(f"✅ **{user.first_name}** (`{user.id}`) added as sudo user!")
        except Exception as e:
            await message.reply_text(f"❌ User not found: {str(e)}")
    except Exception as e:
        logger.error(f"Add sudo error: {e}")
        await message.reply_text(f"❌ Error: {str(e)}")

@bot.on_message(filters.command("rmsudo") & filters.private & filters.user(OWNER_ID))
async def remove_sudo(client, message: Message):
    try:
        if len(message.command) < 2:
            await message.reply_text("❌ **Usage:** `/rmsudo <username or userid>`\n\n**Example:** `/rmsudo @username` or `/rmsudo 123456789`")
            return
        
        user_input = message.command[1]
        
        try:
            if user_input.startswith('@'):
                user = await client.get_users(user_input[1:])
            else:
                user = await client.get_users(int(user_input))
            
            if user.id not in sudo_users:
                await message.reply_text(f"ℹ️ **{user.first_name}** is not a sudo user!")
            else:
                sudo_users.remove(user.id)
                save_sudo_users()
                await message.reply_text(f"✅ **{user.first_name}** (`{user.id}`) removed from sudo users!")
        except Exception as e:
            await message.reply_text(f"❌ User not found: {str(e)}")
    except Exception as e:
        logger.error(f"Remove sudo error: {e}")
        await message.reply_text(f"❌ Error: {str(e)}")

@bot.on_message(filters.command("sudolist") & filters.private & filters.user(OWNER_ID))
async def list_sudo(client, message: Message):
    try:
        if not sudo_users:
            await message.reply_text("ℹ️ **No sudo users added yet!**")
            return
        
        text = "**👥 Sudo Users List:**\n\n"
        for user_id in sudo_users:
            try:
                user = await client.get_users(user_id)
                text += f"• {user.first_name} (@{user.username or 'no_username'}) - `{user.id}`\n"
            except:
                text += f"• Unknown User - `{user_id}`\n"
        
        text += f"\n**Total:** {len(sudo_users)} users"
        await message.reply_text(text)
    except Exception as e:
        logger.error(f"List sudo error: {e}")
        await message.reply_text(f"❌ Error: {str(e)}")

@bot.on_message(filters.command("setdefault") & filters.private & filters.user(OWNER_ID))
async def set_default_account(client, message: Message):
    try:
        state = get_user_state(message.from_user.id)
        state.step = "default_phone"
        state.data = {}
        await message.reply_text("📱 **Setup Default Account**\n\nSend the phone number with country code:")
    except Exception as e:
        logger.error(f"Setdefault error: {e}")
        await send_error_to_owner(f"Setdefault error: {str(e)}")

@bot.on_message(filters.command("stop") & filters.private)
async def stop_command(client, message: Message):
    try:
        user_id = message.from_user.id
        
        if not is_authorized(user_id):
            await message.reply_text("❌ You don't have permission to use this bot!")
            return
        
        stopped = False
        
        if user_id in user_calls and user_id in active_streams:
            try:
                chat_id = active_streams[user_id]
                await user_calls[user_id].leave_group_call(chat_id)
                del active_streams[user_id]
                stopped = True
            except:
                pass
        
        if user_id == OWNER_ID and default_calls and "default" in active_streams:
            try:
                chat_id = active_streams["default"]
                await default_calls.leave_group_call(chat_id)
                del active_streams["default"]
                stopped = True
            except:
                pass
        
        if stopped:
            await message.reply_text("✅ Stopped playing audio!")
        else:
            await message.reply_text("❌ No active audio playing!")
    except Exception as e:
        logger.error(f"Stop error: {e}")
        await message.reply_text(f"❌ Error: {str(e)}")

@bot.on_message(filters.command("logout") & filters.private)
async def logout_command(client, message: Message):
    try:
        user_id = message.from_user.id
        
        if not is_authorized(user_id):
            await message.reply_text("❌ You don't have permission to use this bot!")
            return
        
        if user_id in user_accounts:
            try:
                if user_id in user_calls:
                    try:
                        if user_id in active_streams:
                            await user_calls[user_id].leave_group_call(active_streams[user_id])
                            del active_streams[user_id]
                    except:
                        pass
                    del user_calls[user_id]
                
                await user_accounts[user_id].stop()
                
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

@bot.on_callback_query()
async def callback_handler(client, callback_query):
    try:
        user_id = callback_query.from_user.id
        data = callback_query.data
        state = get_user_state(user_id)
        
        if not is_authorized(user_id):
            await callback_query.answer("❌ You don't have permission!", show_alert=True)
            return

        if data == "use_default":
            if default_account is None:
                await callback_query.answer("❌ Default account not configured!", show_alert=True)
                if user_id == OWNER_ID:
                    await callback_query.message.reply_text("⚠️ Please use /setdefault to configure default account first.")
                return
            
            state.step = "default_group"
            state.data = {"mode": "default"}
            await callback_query.message.reply_text(
                "📎 **Send Group Info**\n\n"
                "**For Public Groups:**\n"
                "Send username: `@groupusername`\n\n"
                "**For Private Groups:**\n"
                "Send invite link: `https://t.me/+xxxxx`"
            )

        elif data == "use_custom":
            session_loaded = await check_and_load_session(user_id)
            
            if user_id in user_accounts or session_loaded:
                state.step = "custom_group"
                state.data = {"mode": "custom"}
                await callback_query.message.reply_text(
                    "✅ **Already Logged In!**\n\n"
                    "📎 **Send Group Info**\n\n"
                    "**For Public Groups:**\n"
                    "Send username: `@groupusername`\n\n"
                    "**For Private Groups:**\n"
                    "Send invite link: `https://t.me/+xxxxx`"
                )
            else:
                state.step = "custom_phone"
                state.data = {"mode": "custom"}
                await callback_query.message.reply_text(
                    "📱 **Login to Your Account**\n\n"
                    "Send your phone number with country code:"
                )
        
        elif data == "play_audio":
            if user_id not in user_accounts:
                session_loaded = await check_and_load_session(user_id)
                if not session_loaded:
                    await callback_query.answer("❌ Please login first!", show_alert=True)
                    return
            
            state.step = "custom_group"
            state.data = {"mode": "custom"}
            await callback_query.message.reply_text(
                "📎 **Send Group Info**\n\n"
                "**For Public Groups:**\n"
                "Send username: `@groupusername`\n\n"
                "**For Private Groups:**\n"
                "Send invite link: `https://t.me/+xxxxx`"
            )
        
        elif data == "logout_account":
            if user_id in user_accounts:
                try:
                    if user_id in user_calls:
                        try:
                            if user_id in active_streams:
                                await user_calls[user_id].leave_group_call(active_streams[user_id])
                                del active_streams[user_id]
                        except:
                            pass
                        del user_calls[user_id]
                    
                    await user_accounts[user_id].stop()
                    
                    session_file = f"/app/sessions/user_{user_id}.session"
                    if os.path.exists(session_file):
                        os.remove(session_file)
                    
                    del user_accounts[user_id]
                    await callback_query.message.reply_text("✅ Logged out successfully! Use /start to login again.")
                except Exception as e:
                    await callback_query.message.reply_text(f"❌ Logout failed: {str(e)}")
            else:
                await callback_query.answer("❌ No active session!", show_alert=True)

        await callback_query.answer()
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await send_error_to_owner(f"Callback error: {str(e)}")

@bot.on_message(filters.private & filters.text & ~filters.command(["start", "setdefault", "logout", "stop", "sudo", "rmsudo", "sudolist"]))
async def message_handler(client, message: Message):
    global default_account, default_calls
    
    user_id = message.from_user.id
    
    if not is_authorized(user_id):
        return
    
    state = get_user_state(user_id)
    text = message.text

    if not state.step:
        return

    try:
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
                await processing_msg.edit_text("📨 **OTP Sent!**\n\nPlease send the OTP code:")
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
                await processing_msg.edit_text("📨 **OTP Sent!**\n\nPlease send the OTP code:")
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
                await processing_msg.edit_text(
                    "✅ **Logged in successfully!**\n\n"
                    "📎 **Send Group Info**\n\n"
                    "**For Public Groups:**\n"
                    "Send username: `@groupusername`\n\n"
                    "**For Private Groups:**\n"
                    "Send invite link: `https://t.me/+xxxxx`"
                )
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
                await processing_msg.edit_text(
                    "✅ **Logged in successfully!**\n\n"
                    "📎 **Send Group Info**\n\n"
                    "**For Public Groups:**\n"
                    "Send username: `@groupusername`\n\n"
                    "**For Private Groups:**\n"
                    "Send invite link: `https://t.me/+xxxxx`"
                )
            except PasswordHashInvalid:
                await processing_msg.edit_text("❌ Invalid 2FA password! Please start again with /start")
                state.step = None
            except Exception as e:
                await processing_msg.edit_text(f"❌ Error: {str(e)}")
                await send_error_to_owner(f"Custom 2FA error: {str(e)}")
                state.step = None

        elif state.step in ["default_group", "custom_group"]:
            chat_info = extract_chat_info(text)
            
            if not chat_info:
                await message.reply_text(
                    "❌ Invalid input!\n\n"
                    "**For Public Groups:**\n"
                    "Send: `@groupusername`\n\n"
                    "**For Private Groups:**\n"
                    "Send: `https://t.me/+xxxxx`"
                )
                return
            
            state.data["chat_info"] = chat_info
            state.step = "audio_input"
            await message.reply_text(
                "🎵 **Send Audio**\n\n"
                "You can send:\n"
                "• Audio file 🎵\n"
                "• Voice message 🎤\n"
                "• YouTube URL 📺"
            )

        elif state.step == "audio_input":
            mode = state.data.get("mode")
            chat_info = state.data.get("chat_info")
            
            if mode == "default":
                client_to_use = default_account
                calls_to_use = default_calls
                stream_key = "default"
            else:
                client_to_use = user_accounts.get(user_id)
                calls_to_use = user_calls.get(user_id)
                stream_key = user_id

            if not client_to_use or not calls_to_use:
                await message.reply_text("❌ Session expired! Please start again with /start")
                state.step = None
                return

            if not (text.startswith("http://") or text.startswith("https://")):
                await message.reply_text("❌ Please send a valid YouTube URL or send an audio file!")
                return

            processing_msg = await message.reply_text("⏳ Processing...")

            # Join group safely
            await processing_msg.edit_text("⏳ Joining group...")
            success, actual_chat_id, error_msg = await join_chat_safely(client_to_use, chat_info)
            
            if not success or actual_chat_id is None:
                await processing_msg.edit_text(f"❌ {error_msg}")
                state.step = None
                return
            
            # Get chat details for display
            try:
                chat = await client_to_use.get_chat(actual_chat_id)
                chat_title = chat.title
            except:
                chat_title = "Unknown"

            await processing_msg.edit_text(f"⏳ Downloading audio from YouTube...")
            audio_path = await download_youtube_audio(text)
            
            if not audio_path or not os.path.exists(audio_path):
                await processing_msg.edit_text("❌ Failed to download audio! Please check the URL.")
                state.step = None
                return

            try:
                await processing_msg.edit_text("⏳ Joining voice chat...")
                
                await calls_to_use.join_group_call(
                    actual_chat_id,
                    AudioPiped(audio_path),
                    stream_type=StreamType().pulse_stream
                )
                
                active_streams[stream_key] = actual_chat_id
                
                await processing_msg.edit_text(
                    f"✅ **Now Playing!**\n\n"
                    f"📻 Group: {chat_title}\n"
                    f"🎵 Audio is playing in voice chat!\n\n"
                    f"Use /stop to stop playing."
                )
                state.step = None
                
            except Exception as e:
                error_msg = str(e)
                if "No active group call" in error_msg or "GROUP_CALL_INVALID" in error_msg or "not found" in error_msg.lower():
                    await processing_msg.edit_text(
                        "❌ **No Active Voice Chat!**\n\n"
                        "Please:\n"
                        "1. Start a voice chat in the group\n"
                        "2. Make sure the account is admin or can join voice chat\n"
                        "3. Try again"
                    )
                elif "Already joined" in error_msg or "GROUPCALL_ALREADY_STARTED" in error_msg:
                    await processing_msg.edit_text("❌ Already playing in this group! Please use /stop first.")
                else:
                    await processing_msg.edit_text(
                        f"❌ Error: {error_msg}\n\n"
                        f"Make sure:\n"
                        f"• Voice chat is active\n"
                        f"• Account has permission to join"
                    )
                    await send_error_to_owner(f"Play error: {error_msg}\nChat: {actual_chat_id}\nFile: {audio_path}")
                state.step = None
            finally:
                asyncio.create_task(cleanup_file(audio_path))

    except Exception as e:
        logger.error(f"Message handler error: {e}")
        await message.reply_text(f"❌ An error occurred: {str(e)}")
        await send_error_to_owner(f"Message handler error: {str(e)}")
        state.step = None

@bot.on_message(filters.private & (filters.audio | filters.voice))
async def audio_file_handler(client, message: Message):
    user_id = message.from_user.id
    state = get_user_state(user_id)
    
    if not is_authorized(user_id):
        return
    
    if state.step != "audio_input":
        return

    try:
        mode = state.data.get("mode")
        chat_info = state.data.get("chat_info")
        
        if mode == "default":
            client_to_use = default_account
            calls_to_use = default_calls
            stream_key = "default"
        else:
            client_to_use = user_accounts.get(user_id)
            calls_to_use = user_calls.get(user_id)
            stream_key = user_id

        if not client_to_use or not calls_to_use:
            await message.reply_text("❌ Session expired! Please start again with /start")
            state.step = None
            return

        processing_msg = await message.reply_text("⏳ Processing audio...")

        # Join group safely
        await processing_msg.edit_text("⏳ Joining group...")
        success, actual_chat_id, error_msg = await join_chat_safely(client_to_use, chat_info)
        
        if not success or actual_chat_id is None:
            await processing_msg.edit_text(f"❌ {error_msg}")
            state.step = None
            return
        
        # Get chat details for display
        try:
            chat = await client_to_use.get_chat(actual_chat_id)
            chat_title = chat.title
        except:
            chat_title = "Unknown"

        await processing_msg.edit_text("⏳ Downloading audio...")
        audio_path = await message.download(file_name=f"/tmp/downloads/{message.id}.mp3")

        try:
            await processing_msg.edit_text("⏳ Joining voice chat...")
            
            await calls_to_use.join_group_call(
                actual_chat_id,
                AudioPiped(audio_path),
                stream_type=StreamType().pulse_stream
            )
            
            active_streams[stream_key] = actual_chat_id
            
            await processing_msg.edit_text(
                f"✅ **Now Playing!**\n\n"
                f"📻 Group: {chat_title}\n"
                f"🎵 Audio is playing in voice chat!\n\n"
                f"Use /stop to stop playing."
            )
            state.step = None
            
        except Exception as e:
            error_msg = str(e)
            if "No active group call" in error_msg or "GROUP_CALL_INVALID" in error_msg or "not found" in error_msg.lower():
                await processing_msg.edit_text(
                    "❌ **No Active Voice Chat!**\n\n"
                    "Please:\n"
                    "1. Start a voice chat in the group\n"
                    "2. Make sure the account is admin or can join voice chat\n"
                    "3. Try again"
                )
            elif "Already joined" in error_msg or "GROUPCALL_ALREADY_STARTED" in error_msg:
                await processing_msg.edit_text("❌ Already playing in this group! Please use /stop first.")
            else:
                await processing_msg.edit_text(
                    f"❌ Error: {error_msg}\n\n"
                    f"Make sure:\n"
                    f"• Voice chat is active\n"
                    f"• Account has permission to join"
                )
                await send_error_to_owner(f"Play error: {error_msg}\nChat: {actual_chat_id}\nFile: {audio_path}")
            state.step = None
        finally:
            asyncio.create_task(cleanup_file(audio_path))

    except Exception as e:
        logger.error(f"Audio file handler error: {e}")
        await message.reply_text(f"❌ An error occurred: {str(e)}")
        await send_error_to_owner(f"Audio handler error: {str(e)}")
        state.step = None

async def cleanup_file(file_path):
    try:
        await asyncio.sleep(300)
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up: {file_path}")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

if __name__ == "__main__":
    try:
        logger.info("🚀 Starting VC Fighting Bot...")
        logger.info(f"Owner ID: {OWNER_ID}")
        logger.info(f"API ID: {API_ID}")
        logger.info("Powered by @zudo_userbot")
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
