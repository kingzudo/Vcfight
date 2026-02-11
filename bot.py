import os
import asyncio
import re
import json
import logging
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import (
    SessionPasswordNeeded, PhoneCodeInvalid,
    PasswordHashInvalid, FloodWait, UserAlreadyParticipant,
    InviteHashExpired
)

from pytgcalls import PyTgCalls, StreamType
from pytgcalls.types.input_stream import AudioPiped
from pytgcalls.exceptions import AlreadyJoinedError
from pytgcalls.types import Update
from pytgcalls.types.stream import StreamAudioEnded  # ✅ stream end event

import yt_dlp


# =========================
# Configuration
# =========================
OWNER_ID = 7661825494
BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN"
API_ID = 33628258
API_HASH = "PASTE_YOUR_API_HASH"


# =========================
# Directories
# =========================
Path("/tmp/downloads").mkdir(exist_ok=True, parents=True)
Path("/app/sessions").mkdir(exist_ok=True, parents=True)
Path("/app/data").mkdir(exist_ok=True, parents=True)
Path("/app/cookies").mkdir(exist_ok=True, parents=True)


# =========================
# Logging
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("vc_bot")


# =========================
# Bot client
# =========================
bot = Client(
    "vc_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir="/app/sessions"
)


# =========================
# Storage
# =========================
user_states = {}
default_account = None
user_accounts = {}
default_calls = None
user_calls = {}

# active_streams: key -> {"chat_id": int, "audio_path": str}
# key is user_id for custom, "default" for default mode
active_streams = {}

# auto leave tasks: key -> asyncio.Task
auto_leave_tasks = {}

sudo_users = set()
chat_id_cache = {}


# =========================
# Files
# =========================
SUDO_FILE = "/app/data/sudo_users.json"
CACHE_FILE = "/app/data/chat_cache.json"
COOKIES_FILE = "/app/cookies/youtube_cookies.txt"


# =========================
# Helpers: sudo + cache
# =========================
def load_sudo_users():
    global sudo_users
    try:
        if os.path.exists(SUDO_FILE):
            with open(SUDO_FILE, "r") as f:
                sudo_users = set(json.load(f))
        else:
            sudo_users = set()
    except Exception as e:
        logger.error(f"Error loading sudo users: {e}")
        sudo_users = set()


def save_sudo_users():
    try:
        with open(SUDO_FILE, "w") as f:
            json.dump(list(sudo_users), f)
    except Exception as e:
        logger.error(f"Error saving sudo users: {e}")


def load_chat_cache():
    global chat_id_cache
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r") as f:
                chat_id_cache = json.load(f)
        else:
            chat_id_cache = {}
    except Exception as e:
        logger.error(f"Error loading chat cache: {e}")
        chat_id_cache = {}


def save_chat_cache():
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(chat_id_cache, f)
    except Exception as e:
        logger.error(f"Error saving chat cache: {e}")


load_sudo_users()
load_chat_cache()


# =========================
# Authorization
# =========================
def is_authorized(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in sudo_users


# =========================
# State
# =========================
class UserState:
    def __init__(self):
        self.step = None
        self.data = {}


def get_user_state(user_id: int) -> UserState:
    if user_id not in user_states:
        user_states[user_id] = UserState()
    return user_states[user_id]


# =========================
# Parse group link / username
# =========================
def extract_chat_info(text: str):
    text = text.strip()

    invite_patterns = [
        r"(https?://)?t\.me/\+([a-zA-Z0-9_-]+)",
        r"(https?://)?t\.me/joinchat/([a-zA-Z0-9_-]+)",
    ]
    for pattern in invite_patterns:
        m = re.search(pattern, text)
        if m:
            return {"type": "invite", "value": text, "hash": m.group(2)}

    username_patterns = [
        r"(https?://)?t\.me/([a-zA-Z0-9_]+)",
        r"(https?://)?telegram\.me/([a-zA-Z0-9_]+)",
        r"@([a-zA-Z0-9_]+)",
    ]
    for pattern in username_patterns:
        m = re.search(pattern, text)
        if m:
            username = m.group(2) if ("t.me" in pattern or "telegram" in pattern) else m.group(1)
            return {"type": "username", "value": username}

    if text and not text.startswith("http"):
        return {"type": "username", "value": text.replace("@", "")}

    return None


async def find_chat_in_dialogs_advanced(client, invite_hash=None, username=None):
    try:
        async for dialog in client.get_dialogs():
            chat = dialog.chat

            if invite_hash:
                try:
                    full_chat = await client.get_chat(chat.id)
                    if hasattr(full_chat, "invite_link") and full_chat.invite_link:
                        if invite_hash in full_chat.invite_link:
                            return chat.id, chat.title
                except:
                    pass

                try:
                    if hasattr(chat, "invite_link") and chat.invite_link:
                        if invite_hash in chat.invite_link:
                            return chat.id, chat.title
                except:
                    pass

            if username:
                try:
                    if hasattr(chat, "username") and chat.username:
                        if chat.username.lower() == username.lower():
                            return chat.id, chat.title
                except:
                    pass

        return None, None
    except Exception as e:
        logger.error(f"find_chat_in_dialogs_advanced error: {e}")
        return None, None


async def get_chat_id_smart(client, chat_info, user_key):
    """
    Returns: (success, chat_id, chat_title, error_msg, needs_chat_id)
    """
    try:
        if chat_info["type"] == "username":
            username = chat_info["value"]
            cache_key = f"{user_key}_user_{username}"

            if cache_key in chat_id_cache:
                cid, ctitle = chat_id_cache[cache_key]
                return True, cid, ctitle, None, False

            try:
                chat = await client.get_chat(username)
                chat_id_cache[cache_key] = (chat.id, chat.title)
                save_chat_cache()
                return True, chat.id, chat.title, None, False
            except:
                pass

            try:
                await client.join_chat(username)
            except UserAlreadyParticipant:
                pass
            except:
                pass

            try:
                chat = await client.get_chat(username)
                chat_id_cache[cache_key] = (chat.id, chat.title)
                save_chat_cache()
                return True, chat.id, chat.title, None, False
            except:
                pass

            cid, ctitle = await find_chat_in_dialogs_advanced(client, username=username)
            if cid:
                chat_id_cache[cache_key] = (cid, ctitle)
                save_chat_cache()
                return True, cid, ctitle, None, False

            return False, None, None, f"❌ Cannot find group @{username}.", False

        # invite
        invite_hash = chat_info.get("hash", "")
        cache_key = f"{user_key}_inv_{invite_hash}"

        if cache_key in chat_id_cache:
            cid, ctitle = chat_id_cache[cache_key]
            return True, cid, ctitle, None, False

        try:
            chat = await client.join_chat(chat_info["value"])
            chat_id_cache[cache_key] = (chat.id, chat.title)
            save_chat_cache()
            return True, chat.id, chat.title, None, False

        except UserAlreadyParticipant:
            cid, ctitle = await find_chat_in_dialogs_advanced(client, invite_hash=invite_hash)
            if cid:
                chat_id_cache[cache_key] = (cid, ctitle)
                save_chat_cache()
                return True, cid, ctitle, None, False

            error_msg = (
                "⚠️ **Cannot find the private group automatically.**\n\n"
                "**Please send the Chat ID** like `-100123456789`\n\n"
                "Get it by forwarding a group msg to @username_to_id_bot."
            )
            return False, None, None, error_msg, True

        except InviteHashExpired:
            return False, None, None, "❌ Invite link expired!", False

        except Exception as e:
            return False, None, None, f"❌ Error: {str(e)}", False

    except Exception as e:
        logger.error(f"get_chat_id_smart error: {e}")
        return False, None, None, f"❌ Unexpected error: {str(e)}", False


# =========================
# YouTube download
# =========================
async def download_youtube_audio(url: str):
    try:
        output_path = f"/tmp/downloads/{int(asyncio.get_event_loop().time())}"

        cookies_content = """# Netscape HTTP Cookie File
.youtube.com\tTRUE\t/\tTRUE\t0\tCONSENT\tYES+
.youtube.com\tTRUE\t/\tFALSE\t0\tPREF\ttz=Asia.Kolkata
"""
        with open(COOKIES_FILE, "w") as f:
            f.write(cookies_content)

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": f"{output_path}.%(ext)s",
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "cookiefile": COOKIES_FILE,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "extractor_args": {"youtube": {"skip": ["hls", "dash"]}},
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }

        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))

        return f"{output_path}.mp3"
    except Exception as e:
        logger.error(f"YouTube download error: {e}")
        return None


# =========================
# Owner error notify
# =========================
async def send_error_to_owner(error_msg: str):
    try:
        await bot.send_message(OWNER_ID, f"⚠️ **Bot Error:**\n\n`{error_msg}`")
    except:
        pass


# =========================
# Session auto-load
# =========================
async def check_and_load_session(user_id: int):
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

            calls = PyTgCalls(user_client)
            await calls.start()
            user_calls[user_id] = calls

            register_pytgcalls_handlers(calls, user_id)  # ✅ important
            return True
        except Exception as e:
            logger.error(f"Auto-load session error: {e}")
            return False
    return False


# =========================
# ✅ AUTO LEAVE LOGIC
# =========================
def cancel_auto_leave(stream_key):
    task = auto_leave_tasks.get(stream_key)
    if task and not task.done():
        task.cancel()
    auto_leave_tasks.pop(stream_key, None)


async def schedule_auto_leave(stream_key, calls_obj, chat_id: int):
    """
    Stream end hone ke baad 3 sec wait then leave.
    """
    try:
        await asyncio.sleep(3)
        # Still same active stream?
        info = active_streams.get(stream_key)
        if not info or info.get("chat_id") != chat_id:
            return

        await calls_obj.leave_group_call(chat_id)
        logger.info(f"✅ Auto-left VC after stream end | key={stream_key} chat={chat_id}")

    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.error(f"Auto leave error: {e}")
    finally:
        # cleanup map if still same
        info = active_streams.get(stream_key)
        if info and info.get("chat_id") == chat_id:
            active_streams.pop(stream_key, None)


def register_pytgcalls_handlers(calls_obj: PyTgCalls, stream_key):
    """
    Har account ke PyTgCalls instance par event handler register.
    Jab audio end ho -> schedule_auto_leave
    """

    @calls_obj.on_update()
    async def _updates_handler(_, update: Update):
        try:
            # We only care audio ended
            if isinstance(update, StreamAudioEnded):
                chat_id = update.chat_id

                # Make sure this ended stream belongs to stream_key
                info = active_streams.get(stream_key)
                if not info or info.get("chat_id") != chat_id:
                    return

                logger.info(f"🎵 Stream ended detected | key={stream_key} chat={chat_id}")

                cancel_auto_leave(stream_key)
                auto_leave_tasks[stream_key] = asyncio.create_task(
                    schedule_auto_leave(stream_key, calls_obj, chat_id)
                )
        except Exception as e:
            logger.error(f"Update handler error: {e}")


# =========================
# Cleanup downloaded files
# =========================
async def cleanup_file(file_path: str):
    try:
        await asyncio.sleep(300)
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up: {file_path}")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")


# =========================
# Commands
# =========================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message):
    try:
        user_id = message.from_user.id

        if not is_authorized(user_id):
            await message.reply_text(
                "❌ **Access Denied!**\n\nThis bot is only for authorized users."
            )
            return

        session_loaded = await check_and_load_session(user_id)

        if user_id in user_accounts or session_loaded:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎵 Play Audio", callback_data="play_audio")],
                [InlineKeyboardButton("🚪 Logout", callback_data="logout_account")]
            ])
            await message.reply_text(
                "**🎵 Welcome Back!**\n\nYou're already logged in ✅",
                reply_markup=keyboard
            )
        else:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔵 Default Account", callback_data="use_default")],
                [InlineKeyboardButton("🟢 Login My Account", callback_data="use_custom")]
            ])
            await message.reply_text(
                "**🎵 VC Bot**\n\n"
                "• Default Account (owner set)\n"
                "• Login My Account\n\n"
                "Commands:\n"
                "• /logout\n"
                "• /stop",
                reply_markup=keyboard
            )
    except Exception as e:
        logger.error(f"Start command error: {e}")
        await send_error_to_owner(f"Start command error: {str(e)}")


@bot.on_message(filters.command("stop") & filters.private)
async def stop_command(client, message: Message):
    try:
        user_id = message.from_user.id
        if not is_authorized(user_id):
            await message.reply_text("❌ No permission!")
            return

        # which key?
        stream_key = user_id if user_id in active_streams else ("default" if user_id == OWNER_ID and "default" in active_streams else None)
        if not stream_key:
            await message.reply_text("❌ **No active audio playing!**")
            return

        info = active_streams.get(stream_key)
        chat_id = info["chat_id"]

        calls_obj = None
        if stream_key == "default":
            calls_obj = default_calls
        else:
            calls_obj = user_calls.get(stream_key)

        cancel_auto_leave(stream_key)

        try:
            await calls_obj.leave_group_call(chat_id)
        except Exception as e:
            logger.error(f"Stop leave error: {e}")

        active_streams.pop(stream_key, None)
        await message.reply_text("✅ Stopped and left VC!")

    except Exception as e:
        logger.error(f"Stop error: {e}")
        await message.reply_text(f"❌ Error: {str(e)}")


@bot.on_message(filters.command("logout") & filters.private)
async def logout_command(client, message: Message):
    try:
        user_id = message.from_user.id
        if not is_authorized(user_id):
            await message.reply_text("❌ No permission!")
            return

        if user_id not in user_accounts:
            await message.reply_text("❌ No active session found!")
            return

        # stop stream if running
        if user_id in active_streams:
            try:
                cancel_auto_leave(user_id)
                await user_calls[user_id].leave_group_call(active_streams[user_id]["chat_id"])
            except:
                pass
            active_streams.pop(user_id, None)

        try:
            await user_accounts[user_id].stop()
        except:
            pass

        try:
            session_file = f"/app/sessions/user_{user_id}.session"
            if os.path.exists(session_file):
                os.remove(session_file)
        except:
            pass

        user_calls.pop(user_id, None)
        user_accounts.pop(user_id, None)
        await message.reply_text("✅ Logged out successfully!")

    except Exception as e:
        logger.error(f"Logout error: {e}")
        await message.reply_text(f"❌ Error: {str(e)}")


# =========================
# Callbacks
# =========================
@bot.on_callback_query()
async def callback_handler(client, callback_query):
    try:
        user_id = callback_query.from_user.id
        data = callback_query.data
        state = get_user_state(user_id)

        if not is_authorized(user_id):
            await callback_query.answer("❌ No permission!", show_alert=True)
            return

        if data == "use_default":
            if default_account is None or default_calls is None:
                await callback_query.answer("❌ Default account not configured!", show_alert=True)
                if user_id == OWNER_ID:
                    await callback_query.message.reply_text("Use /setdefault to configure default account.")
                return

            state.step = "default_group"
            state.data = {"mode": "default"}
            await callback_query.message.reply_text(
                "📎 Send group username/invite link"
            )

        elif data in ("use_custom", "play_audio"):
            session_loaded = await check_and_load_session(user_id)
            if user_id not in user_accounts and not session_loaded:
                state.step = "custom_phone"
                state.data = {"mode": "custom"}
                await callback_query.message.reply_text("📱 Send phone with country code:")
            else:
                state.step = "custom_group"
                state.data = {"mode": "custom"}
                await callback_query.message.reply_text("📎 Send group username/invite link")

        elif data == "logout_account":
            if user_id in user_accounts:
                fake_msg = callback_query.message
                fake_msg.from_user.id = user_id
                await logout_command(client, fake_msg)
            else:
                await callback_query.answer("❌ No active session!", show_alert=True)

        await callback_query.answer()

    except Exception as e:
        logger.error(f"Callback error: {e}")
        await send_error_to_owner(f"Callback error: {str(e)}")


# =========================
# Owner: setdefault + sudo
# =========================
@bot.on_message(filters.command("sudo") & filters.private & filters.user(OWNER_ID))
async def add_sudo(client, message: Message):
    try:
        if len(message.command) < 2:
            await message.reply_text("Usage: /sudo <username/userid>")
            return

        user_input = message.command[1]
        if user_input.startswith("@"):
            u = await client.get_users(user_input[1:])
        else:
            u = await client.get_users(int(user_input))

        sudo_users.add(u.id)
        save_sudo_users()
        await message.reply_text(f"✅ Added sudo: {u.first_name} (`{u.id}`)")
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


@bot.on_message(filters.command("rmsudo") & filters.private & filters.user(OWNER_ID))
async def rm_sudo(client, message: Message):
    try:
        if len(message.command) < 2:
            await message.reply_text("Usage: /rmsudo <username/userid>")
            return

        user_input = message.command[1]
        if user_input.startswith("@"):
            u = await client.get_users(user_input[1:])
        else:
            u = await client.get_users(int(user_input))

        sudo_users.discard(u.id)
        save_sudo_users()
        await message.reply_text(f"✅ Removed sudo: {u.first_name} (`{u.id}`)")
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


@bot.on_message(filters.command("sudolist") & filters.private & filters.user(OWNER_ID))
async def sudo_list(client, message: Message):
    if not sudo_users:
        await message.reply_text("No sudo users.")
        return
    txt = "**Sudo users:**\n\n"
    for uid in sudo_users:
        try:
            u = await client.get_users(uid)
            txt += f"• {u.first_name} (`{uid}`)\n"
        except:
            txt += f"• `{uid}`\n"
    await message.reply_text(txt)


@bot.on_message(filters.command("setdefault") & filters.private & filters.user(OWNER_ID))
async def set_default_account(client, message: Message):
    state = get_user_state(message.from_user.id)
    state.step = "default_phone"
    state.data = {}
    await message.reply_text("📱 Send default account phone number (+countrycode):")


# =========================
# Main text handler (state machine)
# =========================
@bot.on_message(filters.private & filters.text & ~filters.command([
    "start", "setdefault", "logout", "stop", "sudo", "rmsudo", "sudolist"
]))
async def message_handler(client, message: Message):
    global default_account, default_calls

    user_id = message.from_user.id
    if not is_authorized(user_id):
        return

    state = get_user_state(user_id)
    text = message.text.strip()

    if not state.step:
        return

    try:
        # -------- default login --------
        if state.step == "default_phone":
            phone = text.replace(" ", "")
            state.data["phone"] = phone
            processing = await message.reply_text("⏳ Sending OTP...")

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
                await processing.edit_text("📨 OTP sent. Send OTP code:")
            except FloodWait as e:
                await processing.edit_text(f"⏳ FloodWait {e.value}s")
                state.step = None
            except Exception as e:
                await processing.edit_text(f"❌ Error: {str(e)}")
                state.step = None

        elif state.step == "default_otp":
            otp = text.replace(" ", "").replace("-", "")
            processing = await message.reply_text("⏳ Verifying OTP...")

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

                register_pytgcalls_handlers(default_calls, "default")  # ✅ important

                await processing.edit_text("✅ Default account configured!")
                state.step = None

            except SessionPasswordNeeded:
                state.step = "default_2fa"
                await processing.edit_text("🔐 2FA enabled. Send password:")
            except PhoneCodeInvalid:
                await processing.edit_text("❌ Invalid OTP. Use /setdefault again.")
                state.step = None
            except Exception as e:
                await processing.edit_text(f"❌ Error: {str(e)}")
                state.step = None

        elif state.step == "default_2fa":
            pw = text
            processing = await message.reply_text("⏳ Checking password...")
            try:
                user_client = state.data["client"]
                await user_client.check_password(pw)

                default_account = user_client
                default_calls = PyTgCalls(default_account)
                await default_calls.start()

                register_pytgcalls_handlers(default_calls, "default")  # ✅

                await processing.edit_text("✅ Default account configured!")
                state.step = None
            except PasswordHashInvalid:
                await processing.edit_text("❌ Wrong password. Use /setdefault again.")
                state.step = None
            except Exception as e:
                await processing.edit_text(f"❌ Error: {str(e)}")
                state.step = None

        # -------- custom login --------
        elif state.step == "custom_phone":
            phone = text.replace(" ", "")
            state.data["phone"] = phone
            processing = await message.reply_text("⏳ Sending OTP...")

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
                await processing.edit_text("📨 OTP sent. Send OTP code:")
            except FloodWait as e:
                await processing.edit_text(f"⏳ FloodWait {e.value}s")
                state.step = None
            except Exception as e:
                await processing.edit_text(f"❌ Error: {str(e)}")
                state.step = None

        elif state.step == "custom_otp":
            otp = text.replace(" ", "").replace("-", "")
            processing = await message.reply_text("⏳ Verifying OTP...")

            try:
                user_client = state.data["client"]
                await user_client.sign_in(
                    state.data["phone"],
                    state.data["phone_code_hash"],
                    otp
                )

                user_accounts[user_id] = user_client
                calls = PyTgCalls(user_client)
                await calls.start()
                user_calls[user_id] = calls

                register_pytgcalls_handlers(calls, user_id)  # ✅

                state.step = "custom_group"
                await processing.edit_text("✅ Logged in! Now send group username/invite link:")
            except SessionPasswordNeeded:
                state.step = "custom_2fa"
                await processing.edit_text("🔐 2FA enabled. Send password:")
            except PhoneCodeInvalid:
                await processing.edit_text("❌ Invalid OTP. /start again.")
                state.step = None
            except Exception as e:
                await processing.edit_text(f"❌ Error: {str(e)}")
                state.step = None

        elif state.step == "custom_2fa":
            pw = text
            processing = await message.reply_text("⏳ Checking password...")

            try:
                user_client = state.data["client"]
                await user_client.check_password(pw)

                user_accounts[user_id] = user_client
                calls = PyTgCalls(user_client)
                await calls.start()
                user_calls[user_id] = calls

                register_pytgcalls_handlers(calls, user_id)  # ✅

                state.step = "custom_group"
                await processing.edit_text("✅ Logged in! Now send group username/invite link:")
            except PasswordHashInvalid:
                await processing.edit_text("❌ Wrong password. /start again.")
                state.step = None
            except Exception as e:
                await processing.edit_text(f"❌ Error: {str(e)}")
                state.step = None

        # -------- waiting chat id for private groups --------
        elif state.step == "waiting_chat_id":
            try:
                actual_chat_id = int(text)
                state.data["actual_chat_id"] = actual_chat_id
                state.step = "audio_input"
                await message.reply_text("✅ Chat ID saved. Now send YouTube URL or audio file.")
            except ValueError:
                await message.reply_text("❌ Invalid chat id. Example: `-100123456789`")
            return

        # -------- group selection --------
        elif state.step in ("default_group", "custom_group"):
            chat_info = extract_chat_info(text)
            if not chat_info:
                await message.reply_text("❌ Invalid group. Send @username or invite link.")
                return

            state.data["chat_info"] = chat_info
            mode = state.data.get("mode")

            if chat_info["type"] == "invite":
                # Try resolve via join/dialogs
                if mode == "default":
                    client_to_use = default_account
                    stream_key = "default"
                else:
                    client_to_use = user_accounts.get(user_id)
                    stream_key = user_id

                if not client_to_use:
                    await message.reply_text("❌ Session expired. /start again.")
                    state.step = None
                    return

                processing = await message.reply_text("⏳ Checking group access...")
                ok, cid, title, err, needs = await get_chat_id_smart(client_to_use, chat_info, stream_key)

                if ok and cid:
                    state.data["actual_chat_id"] = cid
                    state.data["chat_title"] = title
                    state.step = "audio_input"
                    await processing.edit_text(f"✅ Group Found: {title}\n\nNow send YouTube URL or audio file.")
                elif needs:
                    state.step = "waiting_chat_id"
                    await processing.edit_text(err)
                else:
                    await processing.edit_text(err)
                    state.step = None
            else:
                state.step = "audio_input"
                await message.reply_text("✅ Now send YouTube URL or audio file.")

        # -------- audio input (YouTube URL only in text handler) --------
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
                await message.reply_text("❌ Session expired. /start again.")
                state.step = None
                return

            if not (text.startswith("http://") or text.startswith("https://")):
                await message.reply_text("❌ Send a valid YouTube URL, or send audio file.")
                return

            processing = await message.reply_text("⏳ Getting group info...")

            if "actual_chat_id" in state.data:
                actual_chat_id = state.data["actual_chat_id"]
                chat_title = state.data.get("chat_title", "Group")
            else:
                ok, actual_chat_id, chat_title, err, needs = await get_chat_id_smart(client_to_use, chat_info, stream_key)
                if not ok or actual_chat_id is None:
                    await processing.edit_text(err)
                    state.step = None
                    return

            await processing.edit_text("⏳ Downloading audio...")
            audio_path = await download_youtube_audio(text)
            if not audio_path or not os.path.exists(audio_path):
                await processing.edit_text("❌ Download failed.")
                state.step = None
                return

            # if already playing, force stop first
            if stream_key in active_streams:
                try:
                    cancel_auto_leave(stream_key)
                    await calls_to_use.leave_group_call(active_streams[stream_key]["chat_id"])
                except:
                    pass
                active_streams.pop(stream_key, None)

            try:
                cancel_auto_leave(stream_key)

                await processing.edit_text("⏳ Joining VC and playing...")
                await calls_to_use.join_group_call(
                    actual_chat_id,
                    AudioPiped(audio_path),
                    stream_type=StreamType().pulse_stream
                )

                active_streams[stream_key] = {"chat_id": actual_chat_id, "audio_path": audio_path}

                await processing.edit_text(
                    f"✅ **Now Playing!**\n\n"
                    f"📻 Group: {chat_title}\n"
                    f"⏹ Stop: /stop\n\n"
                    f"🔁 Audio end hone ke 3 sec baad bot auto leave karega."
                )
                state.step = None

            except AlreadyJoinedError:
                await processing.edit_text("❌ Already joined. Use /stop first.")
                state.step = None
            except Exception as e:
                await processing.edit_text(f"❌ Error: {str(e)}")
                await send_error_to_owner(f"Play error: {str(e)}")
                state.step = None
            finally:
                asyncio.create_task(cleanup_file(audio_path))

    except Exception as e:
        logger.error(f"Message handler error: {e}")
        await message.reply_text(f"❌ Error: {str(e)}")
        await send_error_to_owner(f"Message handler error: {str(e)}")
        state.step = None


# =========================
# Audio/voice file handler
# =========================
@bot.on_message(filters.private & (filters.audio | filters.voice))
async def audio_file_handler(client, message: Message):
    user_id = message.from_user.id
    if not is_authorized(user_id):
        return

    state = get_user_state(user_id)
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
            await message.reply_text("❌ Session expired. /start again.")
            state.step = None
            return

        processing = await message.reply_text("⏳ Getting group info...")

        if "actual_chat_id" in state.data:
            actual_chat_id = state.data["actual_chat_id"]
            chat_title = state.data.get("chat_title", "Group")
        else:
            ok, actual_chat_id, chat_title, err, needs = await get_chat_id_smart(client_to_use, chat_info, stream_key)
            if not ok or actual_chat_id is None:
                await processing.edit_text(err)
                state.step = None
                return

        await processing.edit_text("⏳ Downloading audio file...")
        audio_path = await message.download(file_name=f"/tmp/downloads/{message.id}.mp3")

        # if already playing, stop first
        if stream_key in active_streams:
            try:
                cancel_auto_leave(stream_key)
                await calls_to_use.leave_group_call(active_streams[stream_key]["chat_id"])
            except:
                pass
            active_streams.pop(stream_key, None)

        try:
            cancel_auto_leave(stream_key)

            await processing.edit_text("⏳ Joining VC and playing...")
            await calls_to_use.join_group_call(
                actual_chat_id,
                AudioPiped(audio_path),
                stream_type=StreamType().pulse_stream
            )

            active_streams[stream_key] = {"chat_id": actual_chat_id, "audio_path": audio_path}

            await processing.edit_text(
                f"✅ **Now Playing!**\n\n"
                f"📻 Group: {chat_title}\n"
                f"⏹ Stop: /stop\n\n"
                f"🔁 Audio end hone ke 3 sec baad bot auto leave karega."
            )
            state.step = None

        except Exception as e:
            await processing.edit_text(f"❌ Error: {str(e)}")
            await send_error_to_owner(f"Audio play error: {str(e)}")
            state.step = None
        finally:
            asyncio.create_task(cleanup_file(audio_path))

    except Exception as e:
        logger.error(f"Audio handler error: {e}")
        await message.reply_text(f"❌ Error: {str(e)}")
        await send_error_to_owner(f"Audio handler error: {str(e)}")
        state.step = None


# =========================
# Run
# =========================
if __name__ == "__main__":
    logger.info("🚀 Starting VC Bot...")
    logger.info("✅ Auto-leave after stream end: enabled (3s)")
    bot.run()
