import os, sys, re, json, asyncio, subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ----------------- Auto install requirements -----------------
PIP_DEPS = [
    "telethon==1.36.0",
    "tgcrypto==1.2.5",
    "yt-dlp==2025.01.26",
    "py-tgcalls==1.0.0",  # FIXED: Stable version
]

def ensure_deps():
    try:
        import telethon
        import tgcrypto
        import yt_dlp
        import pytgcalls
        return
    except Exception:
        pass

    print("[BOOT] Installing Python dependencies ...")
    for dep in PIP_DEPS:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", dep])

ensure_deps()

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
from pytgcalls.types import AudioPiped, StreamAudioEnded
from pytgcalls.exceptions import GroupCallNotFound
import yt_dlp


# ================== ENV CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

DATA_DIR = os.getenv("DATA_DIR", "./data")  # FIXED: Local fallback
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
    return is_owner(uid) or (uid in load_sudos())

async def guard(event) -> bool:
    if not is_allowed(event.sender_id):
        await event.respond("❌ Access denied. Ye bot sirf Owner/Sudo users use kar sakte.")
        return False
    return True

# ================== HELPERS ==================
def user_session_path(user_id: int) -> str:
    return os.path.join(USER_SESSION_DIR, f"{user_id}.session")

def is_youtube(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be)", url, re.I))

def parse_invite_hash(link: str) -> Optional[str]:
    link = link.strip()
    m = re.search(r"t\.me/\+([A-Za-z0-9_-]+)", link)
    if m:
        return m.group(1)
    m = re.search(r"t\.me/joinchat/([A-Za-z0-9_-]+)", link)
    if m:
        return m.group(1)
    return None

async def ytdlp_get_direct_audio(url: str) -> str:
    """Returns direct audio URL (ffmpeg can read)."""
    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    loop = asyncio.get_running_loop()

    def _extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info["url"]

    return await loop.run_in_executor(None, _extract)

async def ensure_join_and_get_chat_id(user_client: TelegramClient, link: str) -> int:
    """
    Supports:
      - public: @username / t.me/username
      - private: t.me/+hash / t.me/joinchat/hash (imports invite)
    """
    link = link.strip()

    inv = parse_invite_hash(link)
    if inv:
        try:
            res = await user_client(ImportChatInviteRequest(inv))
            if res.chats:
                return res.chats[0].id
        except UserAlreadyParticipantError:
            pass
        except (InviteHashInvalidError, InviteHashExpiredError):
            raise ValueError("❌ Invite link invalid/expired.")
        except Exception as e:
            raise RuntimeError(f"Invite join error: {e}")

    if link.startswith("@"):
        ent = await user_client.get_entity(link)
        return ent.id

    if "t.me/" in link:
        slug = link.split("t.me/")[-1].split("?")[0].strip("/")
        if slug.startswith("+") or slug.startswith("joinchat/"):
            raise ValueError("Invite link parse failed. Recheck link.")
        ent = await user_client.get_entity(slug)
        return ent.id

    ent = await user_client.get_entity(link)
    return ent.id


# ================== PLAYER STATE ==================
@dataclass
class QueueItem:
    kind: str   # "file" | "yt"
    value: str
    title: str = ""

@dataclass
class PlayerState:
    queue: List[QueueItem] = field(default_factory=list)
    current: Optional[QueueItem] = None
    playing: bool = False

players: Dict[Tuple[str, int], PlayerState] = {}

# ================== CLIENT CACHES ==================
bot = TelegramClient("bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)

client_cache: Dict[str, TelegramClient] = {}
calls_cache: Dict[str, PyTgCalls] = {}

async def get_user_client(session_file: str) -> TelegramClient:
    if session_file in client_cache:
        return client_cache[session_file]
    c = TelegramClient(session_file, API_ID, API_HASH)
    await c.connect()
    if not await c.is_user_authorized():
        await c.disconnect()
        raise RuntimeError("Session not authorized. Login required.")
    client_cache[session_file] = c
    return c

async def get_calls(session_file: str) -> PyTgCalls:
    if session_file in calls_cache:
        return calls_cache[session_file]
    uc = await get_user_client(session_file)
    calls = PyTgCalls(uc)
    await calls.start()
    
    # FIXED: Add stream ended handler
    @calls.on_stream_end()
    async def on_end(client, update):
        chat_id = update.chat_id
        key = (session_file, chat_id)
        await play_next(session_file, chat_id)
    
    calls_cache[session_file] = calls
    return calls

async def build_stream(item: QueueItem) -> AudioPiped:
    if item.kind == "yt":
        direct = await ytdlp_get_direct_audio(item.value)
        return AudioPiped(direct)
    return AudioPiped(item.value)

async def join_or_switch(session_file: str, chat_id: int, item: QueueItem):
    calls = await get_calls(session_file)
    stream = await build_stream(item)
    try:
        await calls.play(chat_id, stream)  # FIXED: Use play() instead of join_group_call()
    except GroupCallNotFound:
        raise RuntimeError("❌ Group me Voice Chat ON nahi hai. Pehle VC start karo.")
    except Exception as e:
        # already joined -> change stream
        try:
            await calls.change_stream(chat_id, stream)
        except:
            raise RuntimeError(f"Stream switch error: {e}")

async def play_next(session_file: str, chat_id: int) -> bool:
    key = (session_file, chat_id)
    st = players.get(key) or PlayerState()
    players[key] = st

    if not st.queue:
        st.current = None
        st.playing = False
        return False

    item = st.queue.pop(0)
    st.current = item
    st.playing = True
    await join_or_switch(session_file, chat_id, item)
    return True

# ================== LOGIN FLOW ==================
# FIXED: Custom wait_for implementation
pending_responses: Dict[int, asyncio.Future] = {}

@bot.on(events.NewMessage)
async def response_handler(event):
    if event.sender_id in pending_responses:
        future = pending_responses.pop(event.sender_id)
        if not future.done():
            future.set_result(event)

async def wait_for_response(user_id: int, timeout: int = 120) -> events.NewMessage.Event:
    """Custom wait_for implementation"""
    future = asyncio.Future()
    pending_responses[user_id] = future
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        pending_responses.pop(user_id, None)
        raise RuntimeError("⏱️ Timeout: Response nahi mila")

async def do_phone_login(event, session_file: str, phone: str):
    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()

    sent = await client.send_code_request(phone)
    await event.respond("📱 OTP bhej diya. Ab OTP bhejo (12345 ya 1 2 3 4 5).")

    otp_event = await wait_for_response(event.sender_id)  # FIXED
    code = re.sub(r"\s+", "", otp_event.raw_text.strip())

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
    except PhoneCodeInvalidError:
        await client.disconnect()
        raise RuntimeError("❌ OTP galat hai.")
    except SessionPasswordNeededError:
        await event.respond("🔐 2FA enabled hai. Apna 2FA password bhejo.")
        pw_event = await wait_for_response(event.sender_id)  # FIXED
        await client.sign_in(password=pw_event.raw_text.strip())

    await client.disconnect()
    await event.respond("✅ Login success! Session save ho gaya.")

# ================== /start button flow ==================
@dataclass
class FlowState:
    mode: str     # "default" | "login"
    step: str     # "idle" | "ask_group" | "ask_source"
    group_link: Optional[str] = None

flows: Dict[int, FlowState] = {}

START_BTNS = [[Button.inline("🔧 Default", b"mode:default"), Button.inline("👤 Login", b"mode:login")]]

HELP = """
🎵 **Owner/Sudo Only VC Bot**

**Auth Commands:**
/setdefault - Default account login (owner only)
/login - Your account login
/logout - Remove your session
/sudo <id|@user> - Add sudo (owner only)
/rmsudo <id|@user> - Remove sudo (owner only)
/sudolist - List all sudos

**Play Commands:**
/start - Interactive mode
/play (reply to audio) - Play audio file
/ytplay <url> - Play YouTube video
/pause <group_link> - Pause playback
/resume <group_link> - Resume playback
/skip <group_link> - Skip current
/stop <group_link> - Stop & clear queue
/leave <group_link> - Leave voice chat
/queue <group_link> - Show queue
/now <group_link> - Current playing

**Note:** Voice chat ON hona chahiye group mein!
"""

async def resolve_user_id(arg: str) -> int:
    arg = arg.strip()
    if arg.isdigit():
        return int(arg)
    if not arg.startswith("@") and re.match(r"^[A-Za-z0-9_]{4,}$", arg):
        arg = "@" + arg
    ent = await bot.get_entity(arg)
    return ent.id

def default_session_ready() -> bool:
    return os.path.exists(DEFAULT_SESSION_PATH)

# ================== HANDLERS ==================
@bot.on(events.NewMessage(pattern=r"^/help$"))
async def help_cmd(event):
    if not await guard(event): return
    await event.respond(HELP)

@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_cmd(event):
    if not await guard(event): return
    flows[event.sender_id] = FlowState(mode="", step="idle")
    await event.respond("👋 **Mode select karo:**", buttons=START_BTNS)

@bot.on(events.CallbackQuery(pattern=b"mode:(default|login)"))
async def mode_cb(event):
    if not is_allowed(event.sender_id):
        return await event.answer("❌ Access denied", alert=True)
    mode = event.pattern_match.group(1).decode()
    flows[event.sender_id] = FlowState(mode=mode, step="ask_group")
    await event.edit(f"✅ Mode: **{mode}**\n\n📍 Group link bhejo (public ya private invite):")

@bot.on(events.NewMessage(pattern=r"^/sudo\s+(.+)$"))
async def sudo_add(event):
    if not await guard(event): return
    if not is_owner(event.sender_id):
        return await event.respond("❌ Only owner can /sudo")
    try:
        uid = await resolve_user_id(event.pattern_match.group(1))
        sudos = load_sudos()
        if uid not in sudos:
            sudos.append(uid)
            save_sudos(sudos)
        await event.respond(f"✅ Sudo added: `{uid}`")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/rmsudo\s+(.+)$"))
async def sudo_rm(event):
    if not await guard(event): return
    if not is_owner(event.sender_id):
        return await event.respond("❌ Only owner can /rmsudo")
    try:
        uid = await resolve_user_id(event.pattern_match.group(1))
        sudos = [x for x in load_sudos() if x != uid]
        save_sudos(sudos)
        await event.respond(f"✅ Sudo removed: `{uid}`")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/sudolist$"))
async def sudo_list(event):
    if not await guard(event): return
    sudos = load_sudos()
    if not sudos:
        return await event.respond("📭 Sudo list empty.")
    await event.respond("👥 **Sudo users:**\n" + "\n".join(f"• `{x}`" for x in sudos))

@bot.on(events.NewMessage(pattern=r"^/setdefault$"))
async def setdefault(event):
    if not await guard(event): return
    if not is_owner(event.sender_id):
        return await event.respond("❌ Only owner can /setdefault")
    await event.respond("📱 Default account phone do (+91xxxx):")
    ph = await wait_for_response(event.sender_id)  # FIXED
    try:
        await do_phone_login(event, DEFAULT_SESSION_PATH, ph.raw_text.strip())
    except Exception as e:
        await event.respond(f"❌ Login failed: {e}")

@bot.on(events.NewMessage(pattern=r"^/login$"))
async def login(event):
    if not await guard(event): return
    path = user_session_path(event.sender_id)
    await event.respond("📱 Apna account phone do (+91xxxx):")
    ph = await wait_for_response(event.sender_id)  # FIXED
    try:
        await do_phone_login(event, path, ph.raw_text.strip())
    except Exception as e:
        await event.respond(f"❌ Login failed: {e}")

@bot.on(events.NewMessage(pattern=r"^/logout$"))
async def logout(event):
    if not await guard(event): return
    path = user_session_path(event.sender_id)
    if os.path.exists(path):
        os.remove(path)
        await event.respond("✅ Logout successful!")
    else:
        await event.respond("❌ Not logged in.")

# -------- button flow text router (group link + audio/yt) --------
@bot.on(events.NewMessage)
async def flow_router(event):
    if event.raw_text.startswith("/"):
        return
    if not is_allowed(event.sender_id):
        return
    st = flows.get(event.sender_id)
    if not st or st.step == "idle":
        return

    if st.step == "ask_group":
        st.group_link = event.raw_text.strip()
        st.step = "ask_source"
        flows[event.sender_id] = st
        return await event.respond("🎵 Ab audio file bhejo ya YouTube URL paste karo:")

    if st.step == "ask_source":
        group_link = st.group_link or ""
        mode = st.mode

        # session
        if mode == "default":
            if not default_session_ready():
                return await event.respond("❌ Default not set. Owner /setdefault kare.")
            session_file = DEFAULT_SESSION_PATH
        else:
            session_file = user_session_path(event.sender_id)
            if not os.path.exists(session_file):
                return await event.respond("❌ Pehle /login karke apna account login karo.")

        # resolve/join group
        try:
            uc = await get_user_client(session_file)
            chat_id = await ensure_join_and_get_chat_id(uc, group_link)
        except Exception as e:
            return await event.respond(f"❌ Group join/resolve error: {e}")

        # source
        if event.media:
            local_path = await event.download_media(file=DATA_DIR)
            item = QueueItem(kind="file", value=local_path, title=os.path.basename(local_path))
        else:
            text = event.raw_text.strip()
            if not is_youtube(text):
                return await event.respond("❌ Valid YouTube URL do ya audio file bhejo.")
            item = QueueItem(kind="yt", value=text, title="YouTube")

        # enqueue and play
        key = (session_file, chat_id)
        ps = players.get(key) or PlayerState()
        ps.queue.append(item)
        players[key] = ps

        try:
            if not ps.playing:
                await play_next(session_file, chat_id)
                await event.respond("▶️ Playing! (VC ON hona chahiye)")
            else:
                await event.respond(f"✅ Queued at position: {len(ps.queue)}")
        except Exception as e:
            await event.respond(f"❌ Play error: {e}")

        flows[event.sender_id] = FlowState(mode="", step="idle")
        return

# -------- command play helpers --------
async def get_default_session() -> str:
    if not default_session_ready():
        raise RuntimeError("❌ Default not set. Owner must /setdefault.")
    return DEFAULT_SESSION_PATH

async def resolve_chat_by_link(session_file: str, link: str) -> int:
    uc = await get_user_client(session_file)
    return await ensure_join_and_get_chat_id(uc, link)

@bot.on(events.NewMessage(pattern=r"^/ytplay\s+(.+)$"))
async def ytplay(event):
    if not await guard(event): return
    url = event.pattern_match.group(1).strip()
    if not is_youtube(url):
        return await event.respond("❌ Valid YouTube URL do.")
    await event.respond("📍 Group link bhejo:")
    gl = await wait_for_response(event.sender_id)  # FIXED
    link = gl.raw_text.strip()

    try:
        session_file = await get_default_session()
        chat_id = await resolve_chat_by_link(session_file, link)

        key = (session_file, chat_id)
        ps = players.get(key) or PlayerState()
        ps.queue.append(QueueItem(kind="yt", value=url, title="YouTube"))
        players[key] = ps

        if not ps.playing:
            await play_next(session_file, chat_id)
            await event.respond("▶️ Playing YouTube!")
        else:
            await event.respond(f"✅ Queued at position: {len(ps.queue)}")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/play$"))
async def play_audio(event):
    if not await guard(event): return
    if not event.is_reply:
        return await event.respond("❌ Audio ko reply karke /play bhejo.")
    rep = await event.get_reply_message()
    if not rep.media:
        return await event.respond("❌ Reply me audio file hona chahiye.")
    await event.respond("📍 Group link bhejo:")
    gl = await wait_for_response(event.sender_id)  # FIXED
    link = gl.raw_text.strip()

    try:
        session_file = await get_default_session()
        chat_id = await resolve_chat_by_link(session_file, link)
        local_path = await rep.download_media(file=DATA_DIR)

        key = (session_file, chat_id)
        ps = players.get(key) or PlayerState()
        ps.queue.append(QueueItem(kind="file", value=local_path, title=os.path.basename(local_path)))
        players[key] = ps

        if not ps.playing:
            await play_next(session_file, chat_id)
            await event.respond("▶️ Playing audio!")
        else:
            await event.respond(f"✅ Queued at position: {len(ps.queue)}")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/skip\s+(.+)$"))
async def skip(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_default_session()
        chat_id = await resolve_chat_by_link(session_file, link)
        ok = await play_next(session_file, chat_id)
        await event.respond("⏭️ Skipped!" if ok else "📭 Queue empty.")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/stop\s+(.+)$"))
async def stop(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_default_session()
        chat_id = await resolve_chat_by_link(session_file, link)

        key = (session_file, chat_id)
        ps = players.get(key) or PlayerState()
        ps.queue.clear()
        ps.current = None
        ps.playing = False
        players[key] = ps

        calls = await get_calls(session_file)
        try:
            await calls.leave_group_call(chat_id)
        except:
            pass
        await event.respond("⏹️ Stopped! (Queue cleared + left VC)")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/leave\s+(.+)$"))
async def leave(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_default_session()
        chat_id = await resolve_chat_by_link(session_file, link)
        calls = await get_calls(session_file)
        await calls.leave_group_call(chat_id)
        await event.respond("👋 Left VC!")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/pause\s+(.+)$"))
async def pause(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_default_session()
        chat_id = await resolve_chat_by_link(session_file, link)
        calls = await get_calls(session_file)
        await calls.pause_stream(chat_id)
        await event.respond("⏸️ Paused!")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/resume\s+(.+)$"))
async def resume(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_default_session()
        chat_id = await resolve_chat_by_link(session_file, link)
        calls = await get_calls(session_file)
        await calls.resume_stream(chat_id)
        await event.respond("▶️ Resumed!")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/queue\s+(.+)$"))
async def queue(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_default_session()
        chat_id = await resolve_chat_by_link(session_file, link)
        key = (session_file, chat_id)
        ps = players.get(key)
        if not ps:
            return await event.respond("📭 No queue.")
        lines = []
        if ps.current:
            lines.append(f"🎵 **Now:** {ps.current.title or ps.current.kind}")
        if ps.queue:
            lines.append("\n📜 **Next:**")
            for i, it in enumerate(ps.queue[:20], 1):
                lines.append(f"{i}. {it.title or it.kind}")
        else:
            lines.append("\n📭 Queue empty.")
        await event.respond("\n".join(lines))
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@bot.on(events.NewMessage(pattern=r"^/now\s+(.+)$"))
async def now(event):
    if not await guard(event): return
    link = event.pattern_match.group(1).strip()
    try:
        session_file = await get_default_session()
        chat_id = await resolve_chat_by_link(session_file, link)
        ps = players.get((session_file, chat_id))
        if not ps or not ps.current:
            return await event.respond("📭 Nothing playing.")
        await event.respond(f"🎵 **Now playing:** {ps.current.title or ps.current.kind}")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

# ================== MAIN ==================
async def main():
    print("[✅ OK] Bot is running...")
    print(f"[ℹ️] DATA_DIR: {DATA_DIR}")
    print(f"[ℹ️] OWNER_ID: {OWNER_ID}")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[👋] Bot stopped by user.")
