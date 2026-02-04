import os
import re
import json
import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from telethon import TelegramClient, events, Button
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import (
    UserAlreadyParticipantError,
    InviteHashInvalidError,
    InviteHashExpiredError,
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
)

from pytgcalls import PyTgCalls
from pytgcalls.types.input_stream import AudioPiped
from pytgcalls.exceptions import GroupCallNotFoundError

import yt_dlp

# ================== ENV CONFIG ==================
BOT_TOKEN = "7845373810:AAH5jWEJhLoObAwFXxjK6KFpwGZ2Y1N2fE0"
API_ID = "33628258"
API_HASH = "0850762925b9c1715b9b122f7b753128"
OWNER_ID = "7661825494"

DATA_DIR = os.getenv("DATA_DIR", "/data")
DEFAULT_SESSION_PATH = os.path.join(DATA_DIR, "default.session")
USER_SESSION_DIR = os.path.join(DATA_DIR, "usersessions")
SUDO_FILE = os.path.join(DATA_DIR, "sudo.json")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(USER_SESSION_DIR, exist_ok=True)

if not (BOT_TOKEN and API_ID and API_HASH and OWNER_ID):
    raise RuntimeError("Missing env vars: BOT_TOKEN, API_ID, API_HASH, OWNER_ID")

# ================== SUDO STORE ==================
def load_sudos() -> List[int]:
    if not os.path.exists(SUDO_FILE):
        return []
    try:
        with open(SUDO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return sorted(list(set(int(x) for x in data.get("sudos", []))))
    except:
        return []

def save_sudos(sudos: List[int]):
    with open(SUDO_FILE, "w", encoding="utf-8") as f:
        json.dump({"sudos": sorted(list(set(sudos)))}, f, indent=2)

def is_owner(uid: int) -> bool:
    return uid == OWNER_ID

def is_allowed(uid: int) -> bool:
    if is_owner(uid):
        return True
    return uid in load_sudos()

# ================== HELPERS ==================
def user_session_path(user_id: int) -> str:
    return os.path.join(USER_SESSION_DIR, f"{user_id}.session")

def is_youtube(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be)", url, re.I))

def parse_invite_hash(link: str) -> Optional[str]:
    """
    Supports:
      - https://t.me/+HASH
      - t.me/+HASH
      - https://t.me/joinchat/HASH
      - t.me/joinchat/HASH
    """
    link = link.strip()
    m = re.search(r"t\.me/\+([A-Za-z0-9_-]+)", link)
    if m:
        return m.group(1)
    m = re.search(r"t\.me/joinchat/([A-Za-z0-9_-]+)", link)
    if m:
        return m.group(1)
    return None

async def ytdlp_get_direct_audio(url: str) -> str:
    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "extract_flat": False,
    }
    loop = asyncio.get_running_loop()

    def _extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info["url"]

    return await loop.run_in_executor(None, _extract)

async def ensure_join_by_link(user_client: TelegramClient, link: str) -> int:
    """
    Returns chat_id after ensuring membership for private invite links.
    Public @username also supported.
    """
    link = link.strip()

    invite_hash = parse_invite_hash(link)
    if invite_hash:
        try:
            res = await user_client(ImportChatInviteRequest(invite_hash))
            # res.chats[0] should be the chat
            if res.chats:
                return res.chats[0].id
        except UserAlreadyParticipantError:
            # Already joined; must resolve entity via invite? Telethon doesn't give chat id directly.
            # We'll try get_entity via full link fallback.
            pass
        except (InviteHashInvalidError, InviteHashExpiredError):
            raise ValueError("Invite link invalid/expired.")
        except Exception as e:
            # If something else happens
            raise RuntimeError(f"Invite join error: {e}")

    # Public link / @username / entity
    if link.startswith("@"):
        ent = await user_client.get_entity(link)
        return ent.id

    if "t.me/" in link:
        slug = link.split("t.me/")[-1].split("?")[0].strip("/")
        if slug.startswith("+") or slug.startswith("joinchat/"):
            # already handled above
            raise ValueError("Invite link parse failed. Recheck your link.")
        ent = await user_client.get_entity(slug)
        return ent.id

    ent = await user_client.get_entity(link)
    return ent.id

# ================== PLAYBACK STATE (QUEUE) ==================
@dataclass
class QueueItem:
    kind: str                 # "file" or "yt"
    value: str                # file path or yt url
    title: str = ""

@dataclass
class PlayerState:
    queue: List[QueueItem] = field(default_factory=list)
    current: Optional[QueueItem] = None
    volume: int = 100
    playing: bool = False

# key: (session_file, chat_id)
players: Dict[Tuple[str, int], PlayerState] = {}

# ================== BOT CLIENT ==================
bot = TelegramClient("bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# ================== SESSION CLIENT CACHES ==================
client_cache: Dict[str, TelegramClient] = {}
calls_cache: Dict[str, PyTgCalls] = {}

async def get_user_client(session_file: str) -> TelegramClient:
    if session_file in client_cache:
        return client_cache[session_file]
    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("Session not authorized. Please login.")
    client_cache[session_file] = client
    return client

async def get_calls(session_file: str) -> PyTgCalls:
    if session_file in calls_cache:
        return calls_cache[session_file]
    user_client = await get_user_client(session_file)
    calls = PyTgCalls(user_client)
    await calls.start()
    calls_cache[session_file] = calls
    return calls

async def build_stream(item: QueueItem) -> AudioPiped:
    if item.kind == "yt":
        direct = await ytdlp_get_direct_audio(item.value)
        return AudioPiped(direct)
    return AudioPiped(item.value)

async def join_and_play(session_file: str, chat_id: int, item: QueueItem):
    calls = await get_calls(session_file)
    stream = await build_stream(item)
    try:
        await calls.join_group_call(chat_id, stream)
    except GroupCallNotFoundError:
        raise RuntimeError("Group me active voice chat nahi chal raha. Pehle VC start karo.")
    except Exception:
        # already joined -> switch stream
        await calls.change_stream(chat_id, stream)

async def play_next(session_file: str, chat_id: int):
    key = (session_file, chat_id)
    st = players.get(key)
    if not st:
        st = PlayerState()
        players[key] = st

    if not st.queue:
        st.current = None
        st.playing = False
        return False

    item = st.queue.pop(0)
    st.current = item
    st.playing = True
    await join_and_play(session_file, chat_id, item)
    return True

# ================== LOGIN FLOW (OTP/2FA) ==================
async def do_phone_login(event, session_file: str, phone: str):
    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()

    sent = await client.send_code_request(phone)
    await event.respond("OTP bhej diya. Ab OTP bhejo (12345 ya 1 2 3 4 5).")

    otp_event = await bot.wait_for(events.NewMessage(from_users=event.sender_id))
    code = re.sub(r"\s+", "", otp_event.raw_text.strip())

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
    except PhoneCodeInvalidError:
        await client.disconnect()
        raise RuntimeError("OTP galat hai.")
    except SessionPasswordNeededError:
        await event.respond("2FA enabled hai. Apna 2FA password bhejo.")
        pw_event = await bot.wait_for(events.NewMessage(from_users=event.sender_id))
        password = pw_event.raw_text.strip()
        await client.sign_in(password=password)

    await client.disconnect()
    await event.respond("Login success ✅ Session save ho gaya.")

# ================== UI FLOW (/start buttons) ==================
@dataclass
class FlowState:
    mode: str     # "default" or "login"
    step: str     # "idle" | "ask_group" | "ask_source"
    group_link: Optional[str] = None

user_flow: Dict[int, FlowState] = {}

START_BTNS = [[
    Button.inline("Default", b"mode:default"),
    Button.inline("Login", b"mode:login"),
]]

# ================== GUARDS ==================
async def guard(event) -> bool:
    if not is_allowed(event.sender_id):
        await event.respond("Access denied. Ye bot sirf Owner/Sudo users use kar sakte.")
        return False
    return True

# ================== COMMANDS ==================
HELP_TEXT = (
    "**Commands**\n"
    "Auth/Admin:\n"
    "  /sudo <user_id|@username>  - add sudo (owner only)\n"
    "  /rmsudo <user_id|@username> - remove sudo (owner only)\n"
    "  /sudolist - list sudo users\n"
    "  /setdefault - default account login (owner only)\n"
    "  /login - apna account login (owner/sudo)\n"
    "  /logout - apna account logout\n\n"
    "Playback:\n"
    "  /play (reply audio) - audio queue\n"
    "  /ytplay <url> - youtube queue\n"
    "  /join <group_link> - VC join (no play)\n"
    "  /leave <group_link> - VC leave\n"
    "  /pause <group_link>\n"
    "  /resume <group_link>\n"
    "  /skip <group_link>\n"
    "  /stop <group_link> - stop and clear queue\n"
    "  /queue <group_link>\n"
    "  /now <group_link>\n"
)

@bot.on(events.NewMessage(pattern=r"^/help$"))
async def help_cmd(event):
    if not await guard(event): return
    await event.respond(HELP_TEXT)

@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_cmd(event):
    if not await guard(event): return
    user_flow[event.sender_id] = FlowState(mode="", step="idle")
    await event.respond("VC Bot Mode select karo:", buttons=START_BTNS)

@bot.on(events.CallbackQuery(pattern=b"mode:(default|login)"))
async def mode_cb(event):
    if not is_allowed(event.sender_id):
        return await event.answer("Access denied", alert=True)

    mode = event.pattern_match.group(1).decode()
    user_flow[event.sender_id] = FlowState(mode=mode, step="ask_group")
    await event.edit(f"Mode: **{mode}**\n\nAb group invite link/@username bhejo:")

async def resolve_user_id(arg: str) -> int:
    arg = arg.strip()
    if arg.isdigit():
        return int(arg)
    if arg.startswith("@"):
        ent = await bot.get_entity(arg)
        return ent.id
    # allow raw username without @
    if re.match(r"^[A-Za-z0-9_]{4,}$", arg):
        ent = await bot.get_entity(arg)
        return ent.id
    raise ValueError("Invalid user argument")

@bot.on(events.NewMessage(pattern=r"^/sudo(?:\s+(.+))?$"))
async def sudo_add(event):
    if not await guard(event): return
    if not is_owner(event.sender_id):
        return await event.respond("Sirf owner /sudo use kar sakta.")
    arg = event.pattern_match.group(1)
    if not arg:
        return await event.respond("Usage: /sudo <user_id|@username>")
    try:
        uid = await resolve_user_id(arg)
        sudos = load_sudos()
        if uid not in sudos:
            sudos.append(uid)
            save_sudos(sudos)
        await event.respond(f"Sudo added ✅ `{uid}`")
    except Exception as e:
        await event.respond(f"Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/rmsudo(?:\s+(.+))?$"))
async def sudo_rm(event):
    if not await guard(event): return
    if not is_owner(event.sender_id):
        return await event.respond("Sirf owner /rmsudo use kar sakta.")
    arg = event.pattern_match.group(1)
    if not arg:
        return await event.respond("Usage: /rmsudo <user_id|@username>")
    try:
        uid = await resolve_user_id(arg)
        sudos = load_sudos()
        sudos = [x for x in sudos if x != uid]
        save_sudos(sudos)
        await event.respond(f"Sudo removed ✅ `{uid}`")
    except Exception as e:
        await event.respond(f"Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/sudolist$"))
async def sudo_list(event):
    if not await guard(event): return
    sudos = load_sudos()
    if not sudos:
        return await event.respond("Sudo list empty.")
    txt = "**Sudo Users:**\n" + "\n".join(f"- `{x}`" for x in sudos)
    await event.respond(txt)

@bot.on(events.NewMessage(pattern=r"^/setdefault$"))
async def setdefault(event):
    if not await guard(event): return
    if not is_owner(event.sender_id):
        return await event.respond("Sirf owner /setdefault use kar sakta.")
    await event.respond("Default account set: phone number bhejo (+91xxxxxxxxxx).")
    phone_event = await bot.wait_for(events.NewMessage(from_users=event.sender_id))
    phone = phone_event.raw_text.strip()
    try:
        await do_phone_login(event, DEFAULT_SESSION_PATH, phone)
    except Exception as e:
        await event.respond(f"Login failed: {e}")

@bot.on(events.NewMessage(pattern=r"^/login$"))
async def login_cmd(event):
    if not await guard(event): return
    path = user_session_path(event.sender_id)
    await event.respond("Apna account login: phone number bhejo (+91xxxxxxxxxx).")
    phone_event = await bot.wait_for(events.NewMessage(from_users=event.sender_id))
    phone = phone_event.raw_text.strip()
    try:
        await do_phone_login(event, path, phone)
    except Exception as e:
        await event.respond(f"Login failed: {e}")

@bot.on(events.NewMessage(pattern=r"^/logout$"))
async def logout_cmd(event):
    if not await guard(event): return
    path = user_session_path(event.sender_id)
    if os.path.exists(path):
        os.remove(path)
        await event.respond("Logout ✅ session deleted.")
    else:
        await event.respond("Tum logged in nahi ho.")

# ============== MODE BUTTON FLOW (Default/Login) ==============
@bot.on(events.NewMessage())
async def start_flow_router(event):
    if event.raw_text.startswith("/"):
        return
    if not is_allowed(event.sender_id):
        return

    st = user_flow.get(event.sender_id)
    if not st:
        return

    if st.step == "ask_group":
        st.group_link = event.raw_text.strip()
        st.step = "ask_source"
        user_flow[event.sender_id] = st
        return await event.respond("Ab audio file bhejo ya YouTube URL paste karo.")

    if st.step == "ask_source":
        group_link = st.group_link or ""
        mode = st.mode

        # pick session
        if mode == "default":
            if not os.path.exists(DEFAULT_SESSION_PATH):
                return await event.respond("Default account set nahi. Owner /setdefault kare.")
            session_file = DEFAULT_SESSION_PATH
        else:
            session_file = user_session_path(event.sender_id)
            if not os.path.exists(session_file):
                return await event.respond("Pehle /login karke apna account login karo.")

        # resolve chat and enqueue
        try:
            user_client = await get_user_client(session_file)
            chat_id = await ensure_join_by_link(user_client, group_link)
        except Exception as e:
            return await event.respond(f"Group resolve/join error: {e}")

        # source
        if event.media:
            local_path = await event.download_media(file=DATA_DIR)
            item = QueueItem(kind="file", value=local_path, title=os.path.basename(local_path))
        else:
            text = event.raw_text.strip()
            if is_youtube(text):
                item = QueueItem(kind="yt", value=text, title="YouTube")
            else:
                return await event.respond("Audio file bhejo ya valid YouTube URL do.")

        key = (session_file, chat_id)
        ps = players.get(key) or PlayerState()
        ps.queue.append(item)
        players[key] = ps

        await event.respond("Queued ✅ VC start hoga/stream switch hoga.")

        # if not currently playing, start
        try:
            if not ps.playing:
                await play_next(session_file, chat_id)
                await event.respond("Playing ✅")
            else:
                await event.respond(f"Queue position: {len(ps.queue)}")
        except Exception as e:
            await event.respond(f"Play error: {e}")

        user_flow[event.sender_id] = FlowState(mode="", step="idle")
        return

# ================== PLAYBACK COMMANDS ==================
async def get_session_for_user(uid: int, use_default: bool) -> str:
    if use_default:
        if not os.path.exists(DEFAULT_SESSION_PATH):
            raise RuntimeError("Default account not set. Owner /setdefault.")
        return DEFAULT_SESSION_PATH
    path = user_session_path(uid)
    if not os.path.exists(path):
        raise RuntimeError("You are not logged in. Use /login first.")
    return path

@bot.on(events.NewMessage(pattern=r"^/join\s+(.+)$"))
async def join_cmd(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    # join with default by default (owner/sudo convenience)
    try:
        session_file = await get_session_for_user(event.sender_id, use_default=True)
        user_client = await get_user_client(session_file)
        chat_id = await ensure_join_by_link(user_client, link)
        calls = await get_calls(session_file)
        # join with silent stream? pytgcalls requires stream; we just won't join without play.
        await event.respond("Join command received. Use /ytplay or /play to start streaming.")
        return
    except Exception as e:
        await event.respond(f"Join error: {e}")

@bot.on(events.NewMessage(pattern=r"^/ytplay\s+(.+)$"))
async def ytplay_cmd(event):
    if not await guard(event): return
    url = event.pattern_match.group(1).strip()
    if not is_youtube(url):
        return await event.respond("Valid YouTube URL do.")

    await event.respond("Group link bhejo (public @username ya private invite).")
    gl = await bot.wait_for(events.NewMessage(from_users=event.sender_id))
    link = gl.raw_text.strip()

    try:
        session_file = await get_session_for_user(event.sender_id, use_default=True)
        user_client = await get_user_client(session_file)
        chat_id = await ensure_join_by_link(user_client, link)

        key = (session_file, chat_id)
        ps = players.get(key) or PlayerState()
        ps.queue.append(QueueItem(kind="yt", value=url, title="YouTube"))
        players[key] = ps

        if not ps.playing:
            await play_next(session_file, chat_id)
            await event.respond("Playing ✅")
        else:
            await event.respond(f"Queued ✅ position: {len(ps.queue)}")
    except Exception as e:
        await event.respond(f"Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/play$"))
async def play_cmd(event):
    if not await guard(event): return
    if not event.is_reply:
        return await event.respond("Audio ko reply karke /play bhejo.")
    rep = await event.get_reply_message()
    if not rep.media:
        return await event.respond("Reply me audio file hona chahiye.")
    await event.respond("Group link bhejo (public @username ya private invite).")
    gl = await bot.wait_for(events.NewMessage(from_users=event.sender_id))
    link = gl.raw_text.strip()

    try:
        session_file = await get_session_for_user(event.sender_id, use_default=True)
        user_client = await get_user_client(session_file)
        chat_id = await ensure_join_by_link(user_client, link)

        local_path = await rep.download_media(file=DATA_DIR)
        item = QueueItem(kind="file", value=local_path, title=os.path.basename(local_path))

        key = (session_file, chat_id)
        ps = players.get(key) or PlayerState()
        ps.queue.append(item)
        players[key] = ps

        if not ps.playing:
            await play_next(session_file, chat_id)
            await event.respond("Playing ✅")
        else:
            await event.respond(f"Queued ✅ position: {len(ps.queue)}")
    except Exception as e:
        await event.respond(f"Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/skip\s+(.+)$"))
async def skip_cmd(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_session_for_user(event.sender_id, use_default=True)
        user_client = await get_user_client(session_file)
        chat_id = await ensure_join_by_link(user_client, link)

        key = (session_file, chat_id)
        ps = players.get(key)
        if not ps or (not ps.playing and not ps.queue):
            return await event.respond("Nothing playing/queued.")
        ok = await play_next(session_file, chat_id)
        await event.respond("Skipped ✅" if ok else "Queue empty.")
    except Exception as e:
        await event.respond(f"Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/stop\s+(.+)$"))
async def stop_cmd(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_session_for_user(event.sender_id, use_default=True)
        user_client = await get_user_client(session_file)
        chat_id = await ensure_join_by_link(user_client, link)

        key = (session_file, chat_id)
        ps = players.get(key) or PlayerState()
        ps.queue.clear()
        ps.current = None
        ps.playing = False
        players[key] = ps

        calls = await get_calls(session_file)
        try:
            await calls.leave_group_call(chat_id)
        except Exception:
            pass

        await event.respond("Stopped ✅ left VC and cleared queue.")
    except Exception as e:
        await event.respond(f"Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/leave\s+(.+)$"))
async def leave_cmd(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_session_for_user(event.sender_id, use_default=True)
        user_client = await get_user_client(session_file)
        chat_id = await ensure_join_by_link(user_client, link)

        calls = await get_calls(session_file)
        await calls.leave_group_call(chat_id)

        key = (session_file, chat_id)
        if key in players:
            players[key].playing = False

        await event.respond("Left VC ✅")
    except Exception as e:
        await event.respond(f"Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/pause\s+(.+)$"))
async def pause_cmd(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_session_for_user(event.sender_id, use_default=True)
        user_client = await get_user_client(session_file)
        chat_id = await ensure_join_by_link(user_client, link)

        calls = await get_calls(session_file)
        await calls.pause_stream(chat_id)
        await event.respond("Paused ✅")
    except Exception as e:
        await event.respond(f"Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/resume\s+(.+)$"))
async def resume_cmd(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_session_for_user(event.sender_id, use_default=True)
        user_client = await get_user_client(session_file)
        chat_id = await ensure_join_by_link(user_client, link)

        calls = await get_calls(session_file)
        await calls.resume_stream(chat_id)
        await event.respond("Resumed ✅")
    except Exception as e:
        await event.respond(f"Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/queue\s+(.+)$"))
async def queue_cmd(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_session_for_user(event.sender_id, use_default=True)
        user_client = await get_user_client(session_file)
        chat_id = await ensure_join_by_link(user_client, link)

        key = (session_file, chat_id)
        ps = players.get(key)
        if not ps:
            return await event.respond("No queue.")

        lines = []
        if ps.current:
            lines.append(f"**Now:** {ps.current.title or ps.current.kind}")
        if ps.queue:
            lines.append("**Up Next:**")
            for i, it in enumerate(ps.queue[:20], 1):
                lines.append(f"{i}. {it.title or it.kind}")
        else:
            lines.append("Queue empty.")
        await event.respond("\n".join(lines))
    except Exception as e:
        await event.respond(f"Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/now\s+(.+)$"))
async def now_cmd(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_session_for_user(event.sender_id, use_default=True)
        user_client = await get_user_client(session_file)
        chat_id = await ensure_join_by_link(user_client, link)
        key = (session_file, chat_id)
        ps = players.get(key)
        if not ps or not ps.current:
            return await event.respond("Nothing playing.")
        await event.respond(f"Now playing: **{ps.current.title or ps.current.kind}**")
    except Exception as e:
        await event.respond(f"Error: {e}")

# ================== MAIN ==================
async def main():
    print("Bot running...")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
