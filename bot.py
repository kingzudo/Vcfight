import os
import asyncio
import re
import json
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import (
    SessionPasswordNeeded, PhoneCodeExpired, PhoneCodeInvalid, 
    PasswordHashInvalid, FloodWait, UserAlreadyParticipant, 
    InviteHashExpired, UserNotParticipant, ChatAdminRequired
)
from pytgcalls import PyTgCalls, StreamType
from pytgcalls.types.input_stream import AudioPiped
from pytgcalls.exceptions import NoActiveGroupCall, GroupCallNotFound, AlreadyJoinedError
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
active_streams = {}  # Format: {user_id: chat_id} - Tracks which user is playing in which chat
sudo_users = set()
chat_id_cache = {}

# Files
SUDO_FILE = "/app/data/sudo_users.json"
CACHE_FILE = "/app/data/chat_cache.json"
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

def load_chat_cache():
    global chat_id_cache
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                chat_id_cache = json.load(f)
        else:
            chat_id_cache = {}
    except Exception as e:
        logger.error(f"Error loading chat cache: {e}")
        chat_id_cache = {}

def save_chat_cache():
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(chat_id_cache, f)
    except Exception as e:
        logger.error(f"Error saving chat cache: {e}")

load_sudo_users()
load_chat_cache()

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
        r'(https?://)?t\.me/\+([a-zA-Z0-9_-]+)',
        r'(https?://)?t\.me/joinchat/([a-zA-Z0-9_-]+)',
    ]
    
    for pattern in invite_patterns:
        match = re.search(pattern, text)
        if match:
            return {"type": "invite", "value": text, "hash": match.group(2)}
    
    # Check if it's a username (public group/channel)
    username_patterns = [
        r'(https?://)?t\.me/([a-zA-Z0-9_]+)',
        r'(https?://)?telegram\.me/([a-zA-Z0-9_]+)',
        r'@([a-zA-Z0-9_]+)',
    ]
    
    for pattern in username_patterns:
        match = re.search(pattern, text)
        if match:
            username = match.group(2) if 't.me' in pattern or 'telegram' in pattern else match.group(1)
            return {"type": "username", "value": username}
    
    # If no pattern matches, assume it's a username without @
    if text and not text.startswith('http'):
        return {"type": "username", "value": text.replace('@', '')}
    
    return None

async def find_chat_in_dialogs_advanced(client, invite_hash=None, username=None):
    """Advanced method to find chat in dialogs"""
    try:
        logger.info(f"🔍 Searching in dialogs for invite_hash={invite_hash}, username={username}")
        
        async for dialog in client.get_dialogs():
            try:
                chat = dialog.chat
                
                # For invite hash (private groups)
                if invite_hash:
                    try:
                        full_chat = await client.get_chat(chat.id)
                        
                        if hasattr(full_chat, 'invite_link') and full_chat.invite_link:
                            if invite_hash in full_chat.invite_link:
                                logger.info(f"✅ Found via invite_link: {chat.id} - {chat.title}")
                                return chat.id, chat.title
                        
                        try:
                            invite_links = await client.get_chat_invite_link(chat.id)
                            if hasattr(invite_links, 'invite_link') and invite_hash in invite_links.invite_link:
                                logger.info(f"✅ Found via exported link: {chat.id} - {chat.title}")
                                return chat.id, chat.title
                        except:
                            pass
                            
                    except Exception as e:
                        if hasattr(chat, 'invite_link') and chat.invite_link:
                            if invite_hash in chat.invite_link:
                                logger.info(f"✅ Found via basic check: {chat.id} - {chat.title}")
                                return chat.id, chat.title
                
                # For username (public groups)
                if username:
                    if hasattr(chat, 'username') and chat.username:
                        if chat.username.lower() == username.lower():
                            logger.info(f"✅ Found via username: {chat.id} - {chat.title}")
                            return chat.id, chat.title
            
            except Exception as dialog_error:
                logger.debug(f"Dialog check error: {dialog_error}")
                continue
        
        logger.warning("❌ Chat not found in dialogs")
        return None, None
        
    except Exception as e:
        logger.error(f"Error in find_chat_in_dialogs_advanced: {e}")
        return None, None

async def get_chat_id_smart(client, chat_info, user_key):
    """
    ULTIMATE SMART METHOD - Gets chat_id for both public and private groups
    Now also requests chat_id for private groups if needed
    
    Returns: (success: bool, chat_id, chat_title, error_msg, needs_chat_id: bool)
    """
    
    try:
        if chat_info["type"] == "username":
            username = chat_info["value"]
            cache_key = f"{user_key}_user_{username}"
            
            # Step 1: Check cache
            if cache_key in chat_id_cache:
                cached_id, cached_title = chat_id_cache[cache_key]
                logger.info(f"✅ CACHE HIT for @{username}: {cached_id}")
                return True, cached_id, cached_title, None, False
            
            # Step 2: Try direct get_chat
            try:
                chat = await client.get_chat(username)
                chat_id_cache[cache_key] = (chat.id, chat.title)
                save_chat_cache()
                logger.info(f"✅ DIRECT GET_CHAT for @{username}: {chat.id}")
                return True, chat.id, chat.title, None, False
            except Exception as e:
                logger.debug(f"get_chat failed for @{username}: {e}")
            
            # Step 3: Try join
            try:
                await client.join_chat(username)
                logger.info(f"✅ Joined @{username}")
            except UserAlreadyParticipant:
                logger.info(f"✅ Already in @{username}")
            except Exception as join_err:
                logger.debug(f"Join error for @{username}: {join_err}")
            
            # Step 4: Try get_chat again
            try:
                chat = await client.get_chat(username)
                chat_id_cache[cache_key] = (chat.id, chat.title)
                save_chat_cache()
                logger.info(f"✅ GET_CHAT after join for @{username}: {chat.id}")
                return True, chat.id, chat.title, None, False
            except Exception as e:
                logger.debug(f"get_chat after join failed: {e}")
            
            # Step 5: Search in dialogs
            chat_id, chat_title = await find_chat_in_dialogs_advanced(client, username=username)
            if chat_id:
                chat_id_cache[cache_key] = (chat_id, chat_title)
                save_chat_cache()
                logger.info(f"✅ DIALOGS SEARCH for @{username}: {chat_id}")
                return True, chat_id, chat_title, None, False
            
            return False, None, None, f"❌ Cannot find group @{username}. Make sure the username is correct.", False
        
        else:  # invite link (private group)
            invite_hash = chat_info.get("hash", "")
            cache_key = f"{user_key}_inv_{invite_hash}"
            
            # Step 1: Check cache
            if cache_key in chat_id_cache:
                cached_id, cached_title = chat_id_cache[cache_key]
                logger.info(f"✅ CACHE HIT for invite: {cached_id}")
                return True, cached_id, cached_title, None, False
            
            # Step 2: Try join_chat
            try:
                chat = await client.join_chat(chat_info["value"])
                chat_id_cache[cache_key] = (chat.id, chat.title)
                save_chat_cache()
                logger.info(f"✅ JOINED via invite: {chat.id} - {chat.title}")
                return True, chat.id, chat.title, None, False
            
            except UserAlreadyParticipant:
                logger.info(f"✅ Already member, searching in dialogs...")
                
                # Step 3: Deep search in dialogs
                chat_id, chat_title = await find_chat_in_dialogs_advanced(client, invite_hash=invite_hash)
                
                if chat_id:
                    chat_id_cache[cache_key] = (chat_id, chat_title)
                    save_chat_cache()
                    logger.info(f"✅ DIALOGS SEARCH found: {chat_id} - {chat_title}")
                    return True, chat_id, chat_title, None, False
                
                # Step 4: Request chat_id from user
                error_msg = (
                    "⚠️ **Cannot find the private group automatically.**\n\n"
                    "**Please send the Chat ID:**\n\n"
                    "**How to get Chat ID:**\n"
                    "1. Forward any message from the group to @username_to_id_bot\n"
                    "2. The bot will reply with the Chat ID\n"
                    "3. Send me that Chat ID (it will look like `-100123456789`)\n\n"
                    "Or you can send a message in the group and try again."
                )
                return False, None, None, error_msg, True  # needs_chat_id = True
            
            except InviteHashExpired:
                return False, None, None, "❌ Invite link expired! Get a new invite link from group admin.", False
            
            except Exception as e:
                error_str = str(e).upper()
                
                if "INVITE_HASH_EXPIRED" in error_str or "EXPIRED" in error_str:
                    return False, None, None, "❌ Invite link expired! Get a new invite link.", False
                    
                elif "USER_ALREADY_PARTICIPANT" in error_str:
                    chat_id, chat_title = await find_chat_in_dialogs_advanced(client, invite_hash=invite_hash)
                    if chat_id:
                        chat_id_cache[cache_key] = (chat_id, chat_title)
                        save_chat_cache()
                        return True, chat_id, chat_title, None, False
                    
                    error_msg = (
                        "⚠️ **Cannot find the private group automatically.**\n\n"
                        "**Please send the Chat ID** (like `-100123456789`)\n\n"
                        "Get it from @username_to_id_bot by forwarding a group message."
                    )
                    return False, None, None, error_msg, True
                else:
                    return False, None, None, f"❌ Error: {str(e)}", False
    
    except Exception as e:
        logger.error(f"get_chat_id_smart error: {e}")
        return False, None, None, f"❌ Unexpected error: {str(e)}", False

async def rejoin_and_play(client, calls, chat_id, audio_path, stream_key):
    """
    Emergency function: Leave and rejoin group to fix VC issues
    Only called when normal join fails
    """
    try:
        logger.info(f"🔄 Attempting rejoin strategy for chat {chat_id}")
        
        # Step 1: Try to leave current call (if any)
        try:
            await calls.leave_group_call(chat_id)
            await asyncio.sleep(2)
            logger.info("✅ Left previous call")
        except:
            pass
        
        # Step 2: Leave the group
        try:
            await client.leave_chat(chat_id)
            await asyncio.sleep(3)
            logger.info("✅ Left group")
        except Exception as e:
            logger.error(f"Failed to leave group: {e}")
            return False, f"Cannot leave group: {str(e)}"
        
        # Step 3: Rejoin the group
        try:
            # Get the chat info to rejoin
            chat = await client.get_chat(chat_id)
            
            if hasattr(chat, 'username') and chat.username:
                await client.join_chat(chat.username)
            else:
                # For private groups, we need invite link
                return False, "Cannot rejoin private group automatically. Please send invite link again."
            
            await asyncio.sleep(2)
            logger.info("✅ Rejoined group")
        except Exception as e:
            logger.error(f"Failed to rejoin: {e}")
            return False, f"Cannot rejoin group: {str(e)}"
        
        # Step 4: Try to join VC again
        try:
            await calls.join_group_call(
                chat_id,
                AudioPiped(audio_path),
                stream_type=StreamType().pulse_stream
            )
            active_streams[stream_key] = chat_id
            logger.info("✅ Successfully joined VC after rejoin!")
            return True, None
        except Exception as e:
            logger.error(f"Failed to join VC after rejoin: {e}")
            return False, f"Still cannot join VC: {str(e)}"
    
    except Exception as e:
        logger.error(f"Rejoin strategy failed: {e}")
        return False, f"Rejoin failed: {str(e)}"

async def download_youtube_audio(url):
    try:
        output_path = f'/tmp/downloads/{int(asyncio.get_event_loop().time())}'
        
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
                "• /stop - Stop playing audio in your active group"
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
        chat_name = None
        
        # NEW: Check if this specific user has an active stream
        if user_id in active_streams:
            # This user (sudo or owner using custom account) has an active stream
            if user_id in user_calls:
                try:
                    chat_id = active_streams[user_id]
                    
                    # Get chat name for confirmation
                    try:
                        if user_id in user_accounts:
                            chat = await user_accounts[user_id].get_chat(chat_id)
                        else:
                            chat = await bot.get_chat(chat_id)
                        chat_name = chat.title
                    except:
                        chat_name = f"Chat {chat_id}"
                    
                    await user_calls[user_id].leave_group_call(chat_id)
                    del active_streams[user_id]
                    stopped = True
                    logger.info(f"✅ User {user_id} stopped playing in chat {chat_id}")
                except Exception as e:
                    logger.error(f"Error stopping for user {user_id}: {e}")
        
        # For owner: also check default account (backward compatibility)
        elif user_id == OWNER_ID and "default" in active_streams:
            if default_calls:
                try:
                    chat_id = active_streams["default"]
                    
                    # Get chat name
                    try:
                        chat = await default_account.get_chat(chat_id)
                        chat_name = chat.title
                    except:
                        chat_name = f"Chat {chat_id}"
                    
                    await default_calls.leave_group_call(chat_id)
                    del active_streams["default"]
                    stopped = True
                    logger.info(f"✅ Default account stopped playing in chat {chat_id}")
                except Exception as e:
                    logger.error(f"Error stopping default account: {e}")
        
        if stopped:
            if chat_name:
                await message.reply_text(f"✅ **Stopped playing!**\n\n📻 **Group:** {chat_name}")
            else:
                await message.reply_text("✅ **Stopped playing audio!**")
        else:
            await message.reply_text("❌ **No active audio playing!**\n\nYou don't have any active streams.")
    
    except Exception as e:
        logger.error(f"Stop error: {e}")
        await message.reply_text(f"❌ Error: {str(e)}")
        await send_error_to_owner(f"Stop command error: {str(e)}")

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
        # Phone/OTP/2FA handlers (same as before)
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

        # NEW: Handle Chat ID input (for private groups)
        elif state.step == "waiting_chat_id":
            chat_id_input = text.strip()
            
            try:
                actual_chat_id = int(chat_id_input)
                
                state.data["actual_chat_id"] = actual_chat_id
                state.step = "audio_input"
                
                await message.reply_text(
                    f"✅ **Chat ID received:** `{actual_chat_id}`\n\n"
                    "🎵 **Send Audio**\n\n"
                    "You can send:\n"
                    "• Audio file 🎵\n"
                    "• Voice message 🎤\n"
                    "• YouTube URL 📺"
                )
            except ValueError:
                await message.reply_text(
                    "❌ Invalid Chat ID format!\n\n"
                    "Please send a valid Chat ID like: `-100123456789`"
                )
            return

        # Group input handler
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
            
            # For private groups, ask for chat_id immediately
            if chat_info["type"] == "invite":
                mode = state.data.get("mode")
                
                if mode == "default":
                    client_to_use = default_account
                    stream_key = "default"
                else:
                    client_to_use = user_accounts.get(user_id)
                    stream_key = user_id
                
                if not client_to_use:
                    await message.reply_text("❌ Session expired! Please start again with /start")
                    state.step = None
                    return
                
                processing_msg = await message.reply_text("⏳ Checking group access...")
                success, actual_chat_id, chat_title, error_msg, needs_chat_id = await get_chat_id_smart(
                    client_to_use, chat_info, stream_key
                )
                
                if success and actual_chat_id:
                    state.data["actual_chat_id"] = actual_chat_id
                    state.data["chat_title"] = chat_title
                    state.step = "audio_input"
                    await processing_msg.edit_text(
                        f"✅ **Group Found:** {chat_title}\n\n"
                        "🎵 **Send Audio**\n\n"
                        "You can send:\n"
                        "• Audio file 🎵\n"
                        "• Voice message 🎤\n"
                        "• YouTube URL 📺"
                    )
                elif needs_chat_id:
                    state.step = "waiting_chat_id"
                    await processing_msg.edit_text(error_msg)
                else:
                    await processing_msg.edit_text(error_msg)
                    state.step = None
            else:
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

            # Get chat_id
            if "actual_chat_id" in state.data:
                actual_chat_id = state.data["actual_chat_id"]
                chat_title = state.data.get("chat_title", "Group")
            else:
                await processing_msg.edit_text("⏳ Getting group info...")
                success, actual_chat_id, chat_title, error_msg, needs_chat_id = await get_chat_id_smart(
                    client_to_use, chat_info, stream_key
                )
                
                if not success or actual_chat_id is None:
                    await processing_msg.edit_text(error_msg)
                    state.step = None
                    return

            await processing_msg.edit_text("⏳ Downloading audio from YouTube...")
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
                
                # NEW: Store with proper key (user_id for custom accounts, "default" for default account)
                if mode == "custom":
                    active_streams[user_id] = actual_chat_id
                else:
                    active_streams["default"] = actual_chat_id
                
                logger.info(f"✅ Stream started - Key: {stream_key}, Chat: {actual_chat_id}")
                
                await processing_msg.edit_text(
                    f"✅ **Now Playing!**\n\n"
                    f"📻 **Group:** {chat_title}\n"
                    f"🎵 **Audio is playing in voice chat!**\n\n"
                    f"Use /stop to stop playing."
                )
                state.step = None
                
            except Exception as e:
                error_msg = str(e)
                
                if any(x in error_msg for x in ["No active group call", "GROUP_CALL_INVALID", "not found", "GROUPCALL_FORBIDDEN"]):
                    await processing_msg.edit_text("⏳ Trying rejoin strategy...")
                    
                    rejoin_success, rejoin_error = await rejoin_and_play(
                        client_to_use, calls_to_use, actual_chat_id, audio_path, stream_key
                    )
                    
                    if rejoin_success:
                        # Store stream info
                        if mode == "custom":
                            active_streams[user_id] = actual_chat_id
                        else:
                            active_streams["default"] = actual_chat_id
                        
                        await processing_msg.edit_text(
                            f"✅ **Now Playing!** (via rejoin)\n\n"
                            f"📻 **Group:** {chat_title}\n"
                            f"🎵 **Audio is playing in voice chat!**\n\n"
                            f"Use /stop to stop playing."
                        )
                        state.step = None
                    else:
                        await processing_msg.edit_text(
                            f"❌ **Rejoin strategy failed:**\n{rejoin_error}\n\n"
                            f"**Please:**\n"
                            f"1. Make sure voice chat is active\n"
                            f"2. Check account permissions\n"
                            f"3. Try starting a new voice chat"
                        )
                        state.step = None
                elif "Already joined" in error_msg or "GROUPCALL_ALREADY_STARTED" in error_msg:
                    await processing_msg.edit_text("❌ Already playing in this group! Please use /stop first.")
                    state.step = None
                else:
                    await processing_msg.edit_text(
                        f"❌ **Error:** {error_msg}\n\n"
                        f"**Make sure:**\n"
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

        # Get chat_id
        if "actual_chat_id" in state.data:
            actual_chat_id = state.data["actual_chat_id"]
            chat_title = state.data.get("chat_title", "Group")
        else:
            await processing_msg.edit_text("⏳ Getting group info...")
            success, actual_chat_id, chat_title, error_msg, needs_chat_id = await get_chat_id_smart(
                client_to_use, chat_info, stream_key
            )
            
            if not success or actual_chat_id is None:
                await processing_msg.edit_text(error_msg)
                state.step = None
                return

        await processing_msg.edit_text("⏳ Downloading audio...")
        audio_path = await message.download(file_name=f"/tmp/downloads/{message.id}.mp3")

        try:
            await processing_msg.edit_text("⏳ Joining voice chat...")
            
            await calls_to_use.join_group_call(
                actual_chat_id,
                AudioPiped(audio_path),
                stream_type=StreamType().pulse_stream
            )
            
            # NEW: Store with proper key
            if mode == "custom":
                active_streams[user_id] = actual_chat_id
            else:
                active_streams["default"] = actual_chat_id
            
            logger.info(f"✅ Stream started - Key: {stream_key}, Chat: {actual_chat_id}")
            
            await processing_msg.edit_text(
                f"✅ **Now Playing!**\n\n"
                f"📻 **Group:** {chat_title}\n"
                f"🎵 **Audio is playing in voice chat!**\n\n"
                f"Use /stop to stop playing."
            )
            state.step = None
            
        except Exception as e:
            error_msg = str(e)
            
            if any(x in error_msg for x in ["No active group call", "GROUP_CALL_INVALID", "not found", "GROUPCALL_FORBIDDEN"]):
                await processing_msg.edit_text("⏳ Trying rejoin strategy...")
                
                rejoin_success, rejoin_error = await rejoin_and_play(
                    client_to_use, calls_to_use, actual_chat_id, audio_path, stream_key
                )
                
                if rejoin_success:
                    # Store stream info
                    if mode == "custom":
                        active_streams[user_id] = actual_chat_id
                    else:
                        active_streams["default"] = actual_chat_id
                    
                    await processing_msg.edit_text(
                        f"✅ **Now Playing!** (via rejoin)\n\n"
                        f"📻 **Group:** {chat_title}\n"
                        f"🎵 **Audio is playing in voice chat!**\n\n"
                        f"Use /stop to stop playing."
                    )
                    state.step = None
                else:
                    await processing_msg.edit_text(
                        f"❌ **Rejoin strategy failed:**\n{rejoin_error}\n\n"
                        f"**Please:**\n"
                        f"1. Make sure voice chat is active\n"
                        f"2. Check account permissions"
                    )
                    state.step = None
            elif "Already joined" in error_msg:
                await processing_msg.edit_text("❌ Already playing! Use /stop first.")
                state.step = None
            else:
                await processing_msg.edit_text(f"❌ **Error:** {error_msg}")
                await send_error_to_owner(f"Audio play error: {error_msg}")
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
        logger.info("✅ COMPLETE VERSION - Sudo Users /stop Fixed!")
        logger.info("🔥 Each user has independent stream control")
        logger.info("🔥 /stop only affects the user who executed it")
        logger.info("Powered by @zudo_userbot")
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
