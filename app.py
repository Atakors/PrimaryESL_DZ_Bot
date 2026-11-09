"""
app.py
======
Primary ESL DZ Bot — Resource Upload & Search Engine for Algerian
Primary-School ESL teachers (3PS, 4PS, 5PS).

This single module contains:

* the Flask server (``GET /`` health-check for UptimeRobot / cron-job.org,
  ``POST /webhook`` for Telegram updates) served by gunicorn on Render;
* the complete pyTelegramBotAPI bot logic:
    - multi-step upload wizard with admin approval workflow,
    - keyword search with pagination,
    - guided browse (Level -> Unit -> Category),
    - inline mode (``@BotUsername query`` in any chat),
    - admin tools (/stats, /pending, /backup, /restore_db, /delete);
* automatic database backup to / restore from the private archive channel
  (Render's free tier has an ephemeral filesystem).

Run locally:   python app.py            (long polling when WEBHOOK_URL is unset)
Run on Render: gunicorn app:app ...     (webhook mode, see render.yaml)
"""

import atexit
import hashlib
import html
import logging
import math
import os
import re
import secrets
import sys
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import telebot
from flask import Flask, abort, jsonify, request
from telebot import types, util
from telebot.apihelper import ApiTelegramException

import curriculum as cur
from database import Database, normalize_tags

# =============================================================================
# LOGGING
# =============================================================================
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("esl.app")
telebot.logger.setLevel(logging.WARNING)


# =============================================================================
# CONFIGURATION (environment variables)
# =============================================================================
def _env_int(name: str, default: int = 0) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.error("Environment variable %s=%r is not an integer; using %s", name, raw, default)
        return default


BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is missing. "
        "Create a bot with @BotFather and set BOT_TOKEN before starting the app."
    )

ADMIN_ID = _env_int("ADMIN_ID")
ARCHIVE_CHANNEL_ID = _env_int("ARCHIVE_CHANNEL_ID")
def _resolve_webhook_url() -> str:
    """
    Pick the public webhook base URL.
    Render automatically sets RENDER_EXTERNAL_URL to the actual service URL
    (e.g. https://primaryesl-dz-bot.onrender.com). If WEBHOOK_URL was typed
    with the old template default 'primary-esl-dz-bot.onrender.com' while
    RENDER_EXTERNAL_URL points to the real service, prefer RENDER_EXTERNAL_URL.
    """
    render_url = (os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    manual_url = (os.environ.get("WEBHOOK_URL") or "").strip().rstrip("/")
    if render_url and (not manual_url or "primary-esl-dz-bot.onrender.com" in manual_url):
        return render_url
    return manual_url or render_url


WEBHOOK_URL = _resolve_webhook_url()
PORT = _env_int("PORT", 10000)
DB_PATH = os.environ.get("DB_PATH", "esl_library.db")
BACKUP_INTERVAL_MINUTES = _env_int("BACKUP_INTERVAL_MINUTES", 360)
# Secret token checked on every webhook call (deterministic so restarts agree).
WEBHOOK_SECRET = (os.environ.get("WEBHOOK_SECRET") or hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:48]).strip()

PAGE_SIZE = 10
INLINE_PAGE_SIZE = 20
SESSION_TTL_SECONDS = 30 * 60
MAX_TITLE_LEN = 120
MAX_RESTORE_BYTES = 20 * 1024 * 1024  # Bot API download limit
ALLOWED_UPDATES = ["message", "callback_query", "inline_query", "chosen_inline_result"]
START_TIME = time.time()

BOT_USERNAME = ""
RUN_MODE = "starting"


# =============================================================================
# CORE OBJECTS
# =============================================================================
class _LogExceptions(telebot.ExceptionHandler):
    """Never let a handler error kill a worker thread — log it instead."""

    def handle(self, exception: Exception) -> bool:  # noqa: D401
        log.error("Unhandled error inside a bot handler: %s", exception, exc_info=exception)
        return True


bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="HTML",
    threaded=True,
    num_threads=4,
    exception_handler=_LogExceptions(),
)
db = Database(DB_PATH)
app = Flask(__name__)


# =============================================================================
# IN-MEMORY STORES (single gunicorn worker => process-local state is safe)
# =============================================================================
class SessionStore:
    """Upload-wizard state per user, with automatic expiry."""

    def __init__(self, ttl: int):
        self._ttl = ttl
        self._data: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            session = self._data.get(user_id)
            if session and time.time() - session["_ts"] > self._ttl:
                del self._data[user_id]
                return None
            return session

    def start(self, user_id: int, **fields: Any) -> Dict[str, Any]:
        with self._lock:
            session = {"_ts": time.time(), **fields}
            self._data[user_id] = session
            return session

    def update(self, user_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
        session = self.get(user_id)
        if session is None:
            return None
        with self._lock:
            session.update(fields)
            session["_ts"] = time.time()
        return session

    def clear(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._data.pop(user_id, None)

    def step(self, user_id: int) -> Optional[str]:
        session = self.get(user_id)
        return session.get("step") if session else None


class SearchCache:
    """Maps short random keys to search queries (callback_data is limited to 64 bytes)."""

    def __init__(self, max_size: int = 3000):
        self._max = max_size
        self._data: "OrderedDict[str, str]" = OrderedDict()
        self._lock = threading.Lock()

    def put(self, query: str) -> str:
        key = secrets.token_urlsafe(6)
        with self._lock:
            self._data[key] = query
            while len(self._data) > self._max:
                self._data.popitem(last=False)
        return key

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            query = self._data.get(key)
            if query is not None:
                self._data.move_to_end(key)
            return query


sessions = SessionStore(SESSION_TTL_SECONDS)
search_cache = SearchCache()


# =============================================================================
# GENERIC HELPERS
# =============================================================================
def h(text: Any) -> str:
    """HTML-escape any user supplied value."""
    return html.escape(str(text if text is not None else ""), quote=False)


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def is_admin(user_id: Optional[int]) -> bool:
    return bool(ADMIN_ID) and user_id == ADMIN_ID


def user_full_name(user: types.User) -> str:
    name = " ".join(p for p in (user.first_name, user.last_name) if p).strip()
    return name or (f"@{user.username}" if user.username else f"Teacher {user.id}")


def user_display(user: types.User) -> str:
    name = user_full_name(user)
    return f"{name} (@{user.username})" if user.username else name


def register_user(user: Optional[types.User]) -> None:
    if user is None or user.is_bot:
        return
    try:
        db.touch_user(user.id, user_full_name(user), user.username or "")
    except Exception:  # noqa: BLE001
        log.exception("Could not register user %s", user.id)


def safe_answer(call: types.CallbackQuery, text: Optional[str] = None, alert: bool = False) -> None:
    try:
        bot.answer_callback_query(call.id, text=text, show_alert=alert)
    except ApiTelegramException as exc:  # query too old etc.
        log.debug("answer_callback_query failed: %s", exc)


def safe_edit_text(chat_id: int, message_id: int, text: str,
                   markup: Optional[types.InlineKeyboardMarkup] = None) -> bool:
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup,
                              disable_web_page_preview=True)
        return True
    except ApiTelegramException as exc:
        if "message is not modified" in str(exc):
            return True
        log.warning("edit_message_text failed: %s", exc)
        return False


def safe_edit_caption(chat_id: int, message_id: int, caption: str,
                      markup: Optional[types.InlineKeyboardMarkup] = None) -> bool:
    try:
        bot.edit_message_caption(caption, chat_id, message_id,
                                 reply_markup=markup or types.InlineKeyboardMarkup())
        return True
    except ApiTelegramException as exc:
        if "message is not modified" in str(exc):
            return True
        log.warning("edit_message_caption failed: %s", exc)
        return False


def remove_buttons(chat_id: int, message_id: Optional[int]) -> None:
    if not message_id:
        return
    try:
        bot.edit_message_reply_markup(chat_id, message_id, reply_markup=types.InlineKeyboardMarkup())
    except ApiTelegramException:
        pass


def call_chat_id(call: types.CallbackQuery) -> int:
    return call.message.chat.id if call.message else call.from_user.id


def btn(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text, callback_data=data)


def backup_async(reason: str) -> None:
    """Back up the DB to the archive channel without blocking the handler."""
    if not ARCHIVE_CHANNEL_ID:
        return
    threading.Thread(
        target=db.backup_to_channel,
        args=(bot, ARCHIVE_CHANNEL_ID, reason),
        name="db-backup",
        daemon=True,
    ).start()


def bot_link(payload: str = "") -> str:
    if not BOT_USERNAME:
        return ""
    return f"https://t.me/{BOT_USERNAME}" + (f"?start={payload}" if payload else "")


# =============================================================================
# CAPTIONS
# =============================================================================
def _hashtag(word: str) -> str:
    clean = re.sub(r"[^\w]", "_", word).strip("_")
    return f"#{clean}" if clean else ""


def resource_caption(r: Dict[str, Any], downloads: Optional[int] = None) -> str:
    """Caption shown to teachers when they download a resource."""
    lines = [
        f"📚 <b>{h(r['title'])}</b>",
        "",
        f"🎓 <b>Level:</b> {h(cur.level_label(r['level']))}",
        f"📘 <b>Unit:</b> {h(r['unit'])}",
        f"🗂️ <b>Category:</b> {h(r['category'])}",
        f"👤 <b>Shared by:</b> {h(r.get('uploader_name') or 'A fellow teacher')}",
        f"📥 <b>Total Downloads:</b> {downloads if downloads is not None else r.get('downloads', 0)}",
    ]
    if BOT_USERNAME:
        lines += ["", f"🤖 @{h(BOT_USERNAME)} — Primary ESL DZ Library"]
    return "\n".join(lines)


def archive_caption(r: Dict[str, Any]) -> str:
    """Caption used for the copy stored in the private archive channel."""
    tags = " ".join(filter(None, (_hashtag(t) for t in (r.get("tags") or "").split()[:20])))
    lines = [
        f"📚 <b>{h(r['title'])}</b>",
        "",
        f"🎓 <b>Level:</b> {h(cur.level_label(r['level']))} {_hashtag(r['level'])}",
        f"📘 <b>Unit:</b> {h(r['unit'])}",
        f"🗂️ <b>Category:</b> {h(r['category'])}",
        f"📎 <b>Type:</b> {h(cur.file_type_label(r['file_type']))}",
        f"🏷️ <b>Tags:</b> {h(tags) or '—'}",
        f"👤 <b>Shared by:</b> {h(r.get('uploader_name') or '—')} (<code>{r['uploader_id']}</code>)",
        f"🆔 <b>Resource ID:</b> <code>{r['id']}</code>",
    ]
    link = bot_link(f"get_{r['id']}")
    if link:
        lines.append(f'🔗 <a href="{link}">Get it from the bot</a>')
    return "\n".join(lines)


def review_caption(r: Dict[str, Any]) -> str:
    """Caption of the copy sent to the admin for approval."""
    uploader = h(r.get("uploader_name") or "—")
    if r.get("uploader_username"):
        uploader += f" (@{h(r['uploader_username'])})"
    lines = [
        "🆕 <b>New resource awaiting review</b>",
        "",
        f"📚 <b>Title:</b> {h(r['title'])}",
        f"🎓 <b>Level:</b> {h(cur.level_label(r['level']))}",
        f"📘 <b>Unit:</b> {h(r['unit'])}",
        f"🗂️ <b>Category:</b> {h(r['category'])}",
        f"📎 <b>Type:</b> {h(cur.file_type_label(r['file_type']))}"
        + (f" — {h(truncate(r['file_name'], 60))}" if r.get("file_name") else ""),
        f"🏷️ <b>Keywords:</b> {h(r.get('tags') or '—')}",
        f"👤 <b>Contributor:</b> {uploader} — <code>{r['uploader_id']}</code>",
        f"🆔 <b>Resource ID:</b> <code>{r['id']}</code>",
        f"🕒 <b>Submitted:</b> {h(r.get('created_at'))} UTC",
    ]
    return "\n".join(lines)


# =============================================================================
# FILE SENDING
# =============================================================================
def send_file(chat_id: int, r: Dict[str, Any], caption: str,
              markup: Optional[types.InlineKeyboardMarkup] = None) -> types.Message:
    """Send a stored resource using the correct method for its file type."""
    file_type, file_id = r["file_type"], r["file_id"]
    kwargs = {"caption": caption, "reply_markup": markup}
    if file_type == "photo":
        return bot.send_photo(chat_id, file_id, **kwargs)
    if file_type == "video":
        return bot.send_video(chat_id, file_id, **kwargs)
    if file_type == "audio":
        return bot.send_audio(chat_id, file_id, **kwargs)
    return bot.send_document(chat_id, file_id, **kwargs)


def publish_to_archive(r: Dict[str, Any]) -> Optional[int]:
    """Copy an approved resource to the archive channel. Returns the channel message id."""
    if not ARCHIVE_CHANNEL_ID:
        log.warning("ARCHIVE_CHANNEL_ID not set — resource #%s not archived.", r["id"])
        return None
    try:
        msg = send_file(ARCHIVE_CHANNEL_ID, r, archive_caption(r))
        db.set_archive_msg(r["id"], msg.message_id)
        return msg.message_id
    except Exception:  # noqa: BLE001
        log.exception("Could not post resource #%s to the archive channel", r["id"])
        return None


def send_for_review(r: Dict[str, Any]) -> Optional[int]:
    """Send a pending resource to the admin with Approve / Reject buttons."""
    if not ADMIN_ID:
        log.warning("ADMIN_ID not set — cannot send resource #%s for review.", r["id"])
        return None
    kb = types.InlineKeyboardMarkup()
    kb.row(btn("✅ Approve & Publish", f"adm_ok:{r['id']}"), btn("❌ Reject", f"adm_no:{r['id']}"))
    try:
        msg = send_file(ADMIN_ID, r, review_caption(r), kb)
        db.set_admin_msg(r["id"], msg.message_id)
        return msg.message_id
    except Exception:  # noqa: BLE001
        log.exception("Could not send resource #%s to the admin (has the admin started the bot?)", r["id"])
        return None


def deliver_resource(chat_id: int, user: types.User, resource_id: int) -> bool:
    """Send a resource to a teacher, increment its download counter."""
    r = db.get_resource(resource_id)
    if not r or (r["status"] != "approved" and not is_admin(user.id)):
        bot.send_message(chat_id, "⚠️ Sorry, this resource is no longer available.")
        return False
    register_user(user)
    downloads = db.increment_downloads(resource_id) if r["status"] == "approved" else r["downloads"]
    try:
        send_file(chat_id, r, resource_caption(r, downloads))
        return True
    except ApiTelegramException as exc:
        log.error("Sending resource #%s failed: %s", resource_id, exc)
        bot.send_message(chat_id, "⚠️ I couldn't send this file right now. Please try again later.")
        return False


# =============================================================================
# KEYBOARDS
# =============================================================================
BTN_UPLOAD = "📤 Upload Resource"
BTN_BROWSE = "📂 Browse by Level"
BTN_TOP = "🔥 Top Downloads"
BTN_LATEST = "🆕 Latest"
BTN_HELP = "ℹ️ Help"
BTN_STATS = "📊 Stats"
BTN_PENDING = "⏳ Pending"
MENU_BUTTONS = {BTN_UPLOAD, BTN_BROWSE, BTN_TOP, BTN_LATEST, BTN_HELP, BTN_STATS, BTN_PENDING}


def main_menu(user_id: int) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Type keywords… e.g. 3ps family flashcards",
    )
    kb.row(types.KeyboardButton(BTN_UPLOAD), types.KeyboardButton(BTN_BROWSE))
    kb.row(types.KeyboardButton(BTN_TOP), types.KeyboardButton(BTN_LATEST), types.KeyboardButton(BTN_HELP))
    if is_admin(user_id):
        kb.row(types.KeyboardButton(BTN_STATS), types.KeyboardButton(BTN_PENDING))
    return kb


def result_button(r: Dict[str, Any]) -> types.InlineKeyboardButton:
    label = f"📥 [{r['level']}] {truncate(r['title'], 38)} ({cur.short_category(r['category'])})"
    return btn(label, f"get_{r['id']}")


def results_markup(rows: List[Dict[str, Any]], page: int, total: int, nav_prefix: str,
                   extra_rows: Optional[List[List[types.InlineKeyboardButton]]] = None
                   ) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        kb.row(result_button(r))
    pages = max(1, math.ceil(total / PAGE_SIZE))
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(btn("⬅️ Previous", f"{nav_prefix}:{page - 1}"))
        nav.append(btn(f"📄 {page + 1}/{pages}", "noop"))
        if page < pages - 1:
            nav.append(btn("Next ➡️", f"{nav_prefix}:{page + 1}"))
        kb.row(*nav)
    for row in extra_rows or []:
        kb.row(*row)
    return kb


# ---- upload wizard keyboards ------------------------------------------------
CANCEL_BTN = ("❌ Cancel", "up_cancel")


def kb_cancel_only() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.row(btn(*CANCEL_BTN))
    return kb


def kb_upload_levels() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for level in cur.get_level_keys():
        kb.row(btn(cur.level_button(level), f"up_lv:{level}"))
    kb.row(btn(*CANCEL_BTN))
    return kb


def kb_upload_units(level: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for idx, unit in enumerate(cur.get_units(level)):
        kb.row(btn(unit, f"up_un:{idx}"))
    kb.row(btn("⬅️ Back", "up_back:lv"), btn(*CANCEL_BTN))
    return kb


def kb_upload_categories() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(*[btn(cat, f"up_ct:{idx}") for idx, cat in enumerate(cur.CATEGORIES)])
    kb.row(btn("⬅️ Back", "up_back:un"), btn(*CANCEL_BTN))
    return kb


def kb_upload_tags() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.row(btn("⏭️ Skip keywords", "up_skip"))
    kb.row(btn("⬅️ Back", "up_back:ct"), btn(*CANCEL_BTN))
    return kb


def kb_upload_confirm() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.row(btn("✅ Confirm & Submit", "up_ok"), btn(*CANCEL_BTN))
    return kb


# ---- browse keyboards -------------------------------------------------------
def kb_browse_levels() -> types.InlineKeyboardMarkup:
    counts = db.facet_counts()["levels"]
    kb = types.InlineKeyboardMarkup()
    for level in cur.get_level_keys():
        kb.row(btn(f"{cur.level_button(level)} ({counts.get(level, 0)})", f"br_lv:{level}"))
    return kb


def kb_browse_units(level: str) -> types.InlineKeyboardMarkup:
    counts = db.facet_counts(level)["units"]
    kb = types.InlineKeyboardMarkup()
    for idx, unit in enumerate(cur.get_units(level)):
        kb.row(btn(f"{unit} ({counts.get(unit, 0)})", f"br_un:{level}:{idx}"))
    kb.row(btn(f"📚 All {level} units ({sum(counts.values())})", f"br_un:{level}:a"))
    kb.row(btn("⬅️ Back to levels", "br_home"))
    return kb


def kb_browse_categories(level: str, unit_key: str) -> types.InlineKeyboardMarkup:
    unit = None if unit_key == "a" else cur.get_unit(level, int(unit_key))
    counts = db.facet_counts(level, unit)["categories"]
    kb = types.InlineKeyboardMarkup()
    for idx, cat in enumerate(cur.CATEGORIES):
        kb.row(btn(f"{cat} ({counts.get(cat, 0)})", f"br_ct:{level}:{unit_key}:{idx}:0"))
    kb.row(btn(f"🗂️ All categories ({sum(counts.values())})", f"br_ct:{level}:{unit_key}:a:0"))
    kb.row(btn("⬅️ Back to units", f"br_lv:{level}"))
    return kb


# =============================================================================
# TEXTS
# =============================================================================
def welcome_text(user: types.User) -> str:
    inline_hint = (
        f"💬 <b>Inside any group:</b> type <code>@{h(BOT_USERNAME)} family</code> to share a file directly.\n"
        if BOT_USERNAME else ""
    )
    return (
        f"👋 Welcome, <b>{h(user.first_name or 'teacher')}</b>!\n\n"
        "I'm the <b>Primary ESL DZ Library</b> 🇩🇿 — a shared resource bank for "
        "Algerian primary-school English teachers (<b>3PS • 4PS • 5PS</b>).\n\n"
        "🔎 <b>Search:</b> just type keywords, e.g.\n"
        "   • <code>3ps family flashcards</code>\n"
        "   • <code>lesson plan jobs</code>\n"
        "   • <code>4ps food worksheet</code>\n"
        "📂 <b>Browse:</b> tap <i>Browse by Level</i> → Level → Unit → Category\n"
        "📤 <b>Share:</b> tap <i>Upload Resource</i> (or simply send me a file). "
        "Every contribution is reviewed before publishing.\n"
        f"{inline_hint}\n"
        "Together we build the best ESL library in Algeria! 💪"
    )


def help_text(user_id: int) -> str:
    levels = "\n".join(f"   {cur.level_button(lv)}" for lv in cur.get_level_keys())
    text = (
        "ℹ️ <b>How to use the Primary ESL DZ Library</b>\n\n"
        "<b>1) Keyword search</b> — send any words (English, level codes like 3PS/3AP, unit names, "
        "categories). Examples:\n"
        "   <code>3ps pets song</code> · <code>5ps directions worksheet</code> · <code>phonics</code>\n\n"
        "<b>2) Browse</b> — /browse or <i>📂 Browse by Level</i>.\n\n"
        "<b>3) Inline mode</b> — in any chat type "
        f"<code>@{h(BOT_USERNAME or 'BotUsername')} query</code> and pick a file.\n\n"
        "<b>4) Share a resource</b> — /upload or send a file (PDF, Word, PowerPoint, image, "
        "video, audio). You'll choose the title, level, unit, category and keywords.\n\n"
        f"<b>Levels</b>\n{levels}\n\n"
        "<b>Commands</b>\n"
        "/search <code>words</code> — search (works in groups too)\n"
        "/browse — browse by level\n"
        "/upload — share a resource\n"
        "/top — most downloaded · /latest — newest\n"
        "/cancel — cancel the current upload\n"
        "/id — show your Telegram ID"
    )
    if is_admin(user_id):
        text += (
            "\n\n🛡️ <b>Admin</b>\n"
            "/stats — library statistics\n"
            "/pending — re-send pending submissions\n"
            "/backup — back up the database now\n"
            "/restore_db — reply to a <code>.db</code> file to restore it\n"
            "/delete <code>ID</code> — delete a resource"
        )
    return text


def upload_summary(s: Dict[str, Any], user_id: int) -> str:
    f = s["file"]
    file_line = cur.file_type_label(f["file_type"])
    if f.get("file_name"):
        file_line += f" — {truncate(f['file_name'], 60)}"
    footer = (
        "⚡ As admin, this will be <b>published immediately</b>."
        if is_admin(user_id)
        else "🛡️ It will be sent to the admin for review before publishing."
    )
    return (
        "📋 <b>Please confirm your upload</b>\n\n"
        f"📎 <b>File:</b> {h(file_line)}\n"
        f"📚 <b>Title:</b> {h(s['title'])}\n"
        f"🎓 <b>Level:</b> {h(cur.level_label(s['level']))}\n"
        f"📘 <b>Unit:</b> {h(s['unit'])}\n"
        f"🗂️ <b>Category:</b> {h(s['category'])}\n"
        f"🏷️ <b>Keywords:</b> {h(s.get('tags') or '—')}\n\n"
        f"{footer}"
    )


# =============================================================================
# UPLOAD WIZARD
# =============================================================================
def extract_file(message: types.Message) -> Optional[Dict[str, Any]]:
    ct = message.content_type
    if ct == "document" and message.document:
        d = message.document
        return {"file_type": "document", "file_id": d.file_id, "file_unique_id": d.file_unique_id,
                "file_name": d.file_name, "file_size": d.file_size, "mime_type": d.mime_type}
    if ct == "photo" and message.photo:
        p = message.photo[-1]  # largest size
        return {"file_type": "photo", "file_id": p.file_id, "file_unique_id": p.file_unique_id,
                "file_name": None, "file_size": p.file_size, "mime_type": "image/jpeg"}
    if ct == "video" and message.video:
        v = message.video
        return {"file_type": "video", "file_id": v.file_id, "file_unique_id": v.file_unique_id,
                "file_name": getattr(v, "file_name", None), "file_size": v.file_size,
                "mime_type": getattr(v, "mime_type", None)}
    if ct == "audio" and message.audio:
        a = message.audio
        name = a.file_name or " - ".join(p for p in (a.performer, a.title) if p) or None
        return {"file_type": "audio", "file_id": a.file_id, "file_unique_id": a.file_unique_id,
                "file_name": name, "file_size": a.file_size, "mime_type": a.mime_type}
    return None


def suggest_title(message: types.Message, info: Dict[str, Any]) -> str:
    source = (message.caption or "").strip()
    if not source and info.get("file_name"):
        source = os.path.splitext(info["file_name"])[0]
    source = re.sub(r"[_\-.]+", " ", source)
    source = re.sub(r"\s+", " ", source).strip()
    return source[:MAX_TITLE_LEN] if len(source) >= 3 else ""


def prompt_title(chat_id: int, user_id: int) -> None:
    s = sessions.get(user_id)
    if not s:
        return
    kb = types.InlineKeyboardMarkup()
    if s.get("suggested"):
        kb.row(btn(f"✅ Use: {truncate(s['suggested'], 40)}", "up_fn"))
    kb.row(btn(*CANCEL_BTN))
    msg = bot.send_message(
        chat_id,
        "✏️ <b>Step 2/6 — Title</b>\n\n"
        "Type a clear, descriptive title for this resource.\n"
        "<i>Example: Flashcards My Family</i>",
        reply_markup=kb,
    )
    sessions.update(user_id, prompt_msg=msg.message_id)


def start_upload(message: types.Message) -> None:
    user = message.from_user
    if message.chat.type != "private":
        kb = types.InlineKeyboardMarkup()
        link = bot_link("upload")
        if link:
            kb.row(types.InlineKeyboardButton("📤 Upload in private chat", url=link))
        bot.reply_to(message, "📤 Uploads are done in a private chat with me.",
                     reply_markup=kb if kb.keyboard else None)
        return
    register_user(user)
    sessions.clear(user.id)
    msg = bot.send_message(
        message.chat.id,
        "📤 <b>Upload a resource — Step 1/6</b>\n\n"
        "Send me the file you want to share with fellow teachers:\n"
        "📎 document (PDF, Word, PowerPoint…) · 🖼️ photo · 🎬 video · 🎵 audio\n\n"
        "<i>Tip: send one file at a time.</i>",
        reply_markup=kb_cancel_only(),
    )
    sessions.start(user.id, step="await_file", chat_id=message.chat.id, prompt_msg=msg.message_id)


def go_to_levels(chat_id: int, user_id: int, edit_message_id: Optional[int] = None) -> None:
    sessions.update(user_id, step="await_level")
    text = "🎓 <b>Step 3/6 — Level</b>\n\nWhich level is this resource for?"
    if edit_message_id:
        safe_edit_text(chat_id, edit_message_id, text, kb_upload_levels())
        sessions.update(user_id, prompt_msg=edit_message_id)
    else:
        msg = bot.send_message(chat_id, text, reply_markup=kb_upload_levels())
        sessions.update(user_id, prompt_msg=msg.message_id)


def go_to_units(chat_id: int, user_id: int, message_id: int) -> None:
    s = sessions.update(user_id, step="await_unit")
    level = s["level"]
    safe_edit_text(
        chat_id, message_id,
        f"📘 <b>Step 4/6 — Unit / Sequence</b>\n\n🎓 Level: <b>{h(cur.level_label(level))}</b>\n\n"
        "Choose the unit or sequence:",
        kb_upload_units(level),
    )


def go_to_categories(chat_id: int, user_id: int, message_id: int) -> None:
    s = sessions.update(user_id, step="await_category")
    safe_edit_text(
        chat_id, message_id,
        f"🗂️ <b>Step 5/6 — Category</b>\n\n🎓 {h(s['level'])} · 📘 {h(s['unit'])}\n\n"
        "What kind of resource is it?",
        kb_upload_categories(),
    )


def go_to_tags(chat_id: int, user_id: int, message_id: int) -> None:
    sessions.update(user_id, step="await_tags", prompt_msg=message_id)
    safe_edit_text(
        chat_id, message_id,
        "🏷️ <b>Step 6/6 — Keywords</b>\n\n"
        "Type a few search keywords separated by spaces, so teachers can find it easily.\n"
        "<i>Example: phonics cvc family father mother</i>",
        kb_upload_tags(),
    )


def show_confirmation(chat_id: int, user_id: int, edit_message_id: Optional[int] = None) -> None:
    s = sessions.update(user_id, step="await_confirm")
    if not s:
        return
    if edit_message_id:
        safe_edit_text(chat_id, edit_message_id, upload_summary(s, user_id), kb_upload_confirm())
        sessions.update(user_id, prompt_msg=edit_message_id)
    else:
        msg = bot.send_message(chat_id, upload_summary(s, user_id), reply_markup=kb_upload_confirm())
        sessions.update(user_id, prompt_msg=msg.message_id)


def finalize_upload(call: types.CallbackQuery, s: Dict[str, Any]) -> None:
    user = call.from_user
    chat_id = call_chat_id(call)
    f = s["file"]
    status = "approved" if is_admin(user.id) else "pending"

    resource_id = db.add_resource(
        file_id=f["file_id"],
        file_unique_id=f.get("file_unique_id"),
        file_type=f["file_type"],
        file_name=f.get("file_name"),
        file_size=f.get("file_size"),
        mime_type=f.get("mime_type"),
        title=s["title"],
        level=s["level"],
        unit=s["unit"],
        category=s["category"],
        tags=s.get("tags", ""),
        uploader_id=user.id,
        uploader_name=user_full_name(user),
        uploader_username=user.username,
        status=status,
    )
    sessions.clear(user.id)
    r = db.get_resource(resource_id)

    if status == "approved":
        archived = publish_to_archive(r)
        backup_async(f"Admin upload #{resource_id}")
        text = (
            f"✅ <b>Published!</b>\n\n📚 {h(r['title'])}\n🆔 Resource ID: <code>{resource_id}</code>\n"
            + ("📦 Archived to the storage channel.\n" if archived else
               "⚠️ Saved, but it could not be posted to the archive channel — check "
               "ARCHIVE_CHANNEL_ID and the bot's admin rights.\n")
            + "💾 A database backup is on its way to the channel."
        )
    else:
        sent = send_for_review(r)
        backup_async(f"New pending submission #{resource_id}")
        text = (
            "⏳ <b>Thank you! Your resource was submitted for review.</b>\n\n"
            f"📚 {h(r['title'])}\n🆔 Reference: <code>{resource_id}</code>\n\n"
            "You'll get a notification as soon as the admin publishes it. 🙏"
        )
        if not sent:
            log.warning("Resource #%s is pending but the admin could not be notified.", resource_id)

    if call.message:
        safe_edit_text(chat_id, call.message.message_id, text)
    else:
        bot.send_message(chat_id, text)


# =============================================================================
# SEARCH / BROWSE / LISTS
# =============================================================================
def run_search(chat_id: int, query: str, page: int = 0, key: Optional[str] = None,
               edit_message_id: Optional[int] = None, prefix: str = "") -> None:
    query = query.strip()[:100]
    rows, total = db.search(query, PAGE_SIZE, page * PAGE_SIZE)

    if total == 0:
        kb = types.InlineKeyboardMarkup()
        kb.row(btn(BTN_BROWSE, "br_home"))
        text = (
            f"{prefix}🔍 No resources found for “<b>{h(query)}</b>”.\n\n"
            "💡 Tips:\n• use fewer or simpler words (e.g. <code>family</code>)\n"
            "• add a level: <code>3ps</code>, <code>4ps</code>, <code>5ps</code>\n"
            "• or browse the library by level 👇\n\n"
            "Have a resource on this topic? Share it with /upload 🙌"
        )
        if edit_message_id:
            safe_edit_text(chat_id, edit_message_id, text, kb)
        else:
            bot.send_message(chat_id, text, reply_markup=kb)
        return

    key = key or search_cache.put(query)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    text = (
        f"{prefix}🔎 <b>{total}</b> resource{'s' if total != 1 else ''} found for “<b>{h(query)}</b>”"
        + (f" — page {page + 1}/{pages}" if pages > 1 else "")
        + "\n\nTap a resource to download it 👇"
    )
    kb = results_markup(rows, page, total, f"sp:{key}")
    if edit_message_id:
        safe_edit_text(chat_id, edit_message_id, text, kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb)


def browse_results(level: str, unit_key: str, cat_key: str, page: int) -> Tuple[str, types.InlineKeyboardMarkup]:
    unit = None if unit_key == "a" else cur.get_unit(level, int(unit_key))
    category = None if cat_key == "a" else cur.get_category(int(cat_key))
    rows, total = db.browse(level, unit, category, PAGE_SIZE, page * PAGE_SIZE)
    header = (
        f"📂 <b>{h(cur.level_label(level))}</b>\n"
        f"📘 {h(unit or 'All units')}\n"
        f"🗂️ {h(category or 'All categories')}\n\n"
    )
    back = [[btn("⬅️ Back to categories", f"br_un:{level}:{unit_key}"), btn("🏠 Levels", "br_home")]]
    if total == 0:
        text = header + "📭 No resources here yet.\nBe the first to share one with /upload! 🙌"
        kb = types.InlineKeyboardMarkup()
        for row in back:
            kb.row(*row)
        return text, kb
    pages = max(1, math.ceil(total / PAGE_SIZE))
    text = header + f"✅ <b>{total}</b> resource{'s' if total != 1 else ''}" + (
        f" — page {page + 1}/{pages}" if pages > 1 else "") + ". Tap one to download 👇"
    kb = results_markup(rows, page, total, f"br_ct:{level}:{unit_key}:{cat_key}", back)
    return text, kb


def latest_view(page: int) -> Tuple[str, Optional[types.InlineKeyboardMarkup]]:
    total = db.count_resources("approved")
    rows = db.latest(PAGE_SIZE, page * PAGE_SIZE)
    if not rows:
        return "📭 The library is empty for now. Be the first to /upload a resource! 🙌", None
    pages = max(1, math.ceil(total / PAGE_SIZE))
    text = f"🆕 <b>Latest resources</b> — page {page + 1}/{pages}\n\nTap a resource to download it 👇"
    return text, results_markup(rows, page, total, "lt")


def send_top(chat_id: int) -> None:
    rows = db.top_downloads(10)
    if not rows:
        bot.send_message(chat_id, "📭 The library is empty for now. Be the first to /upload a resource! 🙌")
        return
    medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
    lines = ["🔥 <b>Top downloaded resources</b>\n"]
    for medal, r in zip(medals, rows):
        lines.append(f"{medal} [{h(r['level'])}] {h(truncate(r['title'], 50))} — <b>{r['downloads']}</b> 📥")
    lines.append("\nTap a resource to download it 👇")
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        kb.row(result_button(r))
    bot.send_message(chat_id, "\n".join(lines), reply_markup=kb)


def send_browse_home(chat_id: int) -> None:
    bot.send_message(chat_id, "📂 <b>Browse the library</b>\n\nChoose a level:", reply_markup=kb_browse_levels())


def stats_text() -> str:
    s = db.stats()
    by_level = " • ".join(f"{lv}: {s['by_level'].get(lv, 0)}" for lv in cur.get_level_keys())
    lines = [
        "📊 <b>Library statistics</b>\n",
        f"📚 Published: <b>{s['approved']}</b>",
        f"⏳ Pending approval: <b>{s['pending']}</b>",
        f"❌ Rejected: <b>{s['rejected']}</b>",
        f"📥 Total downloads: <b>{s['downloads']}</b>",
        f"👥 Users: <b>{s['users']}</b> · ✍️ Contributors: <b>{s['contributors']}</b>",
        f"🎓 By level: {h(by_level)}",
        f"💾 Last backup: {h(s['last_backup_at'] or 'never')}"
        + (" · ⚠️ unsaved changes" if db.has_unsaved_changes() else " · ✅ up to date"),
        f"⚙️ Mode: {h(RUN_MODE)} · ⏱ Uptime: {int((time.time() - START_TIME) // 60)} min",
    ]
    top = db.top_downloads(10)
    if top:
        lines.append("\n🔥 <b>Top downloaded</b>")
        for i, r in enumerate(top, 1):
            lines.append(
                f"{i}. [{h(r['level'])}] {h(truncate(r['title'], 45))} — <b>{r['downloads']}</b> 📥 "
                f"(<code>{r['id']}</code>)"
            )
    return "\n".join(lines)


# =============================================================================
# COMMAND HANDLERS
# =============================================================================
@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message) -> None:
    user = message.from_user
    register_user(user)
    payload = util.extract_arguments(message.text or "").strip()

    if message.chat.type != "private":
        bot.reply_to(message, f"👋 Hi! Use /search <code>words</code> here, or type "
                              f"<code>@{h(BOT_USERNAME)} words</code> in any chat.")
        return

    sessions.clear(user.id)
    if payload.startswith("get_") and payload[4:].isdigit():
        bot.send_message(message.chat.id, "📥 Here is your resource:", reply_markup=main_menu(user.id))
        deliver_resource(message.chat.id, user, int(payload[4:]))
        return
    if payload == "upload":
        bot.send_message(message.chat.id, "👋 Welcome!", reply_markup=main_menu(user.id))
        start_upload(message)
        return
    bot.send_message(message.chat.id, welcome_text(user), reply_markup=main_menu(user.id),
                     disable_web_page_preview=True)


@bot.message_handler(commands=["help"])
def cmd_help(message: types.Message) -> None:
    markup = main_menu(message.from_user.id) if message.chat.type == "private" else None
    bot.send_message(message.chat.id, help_text(message.from_user.id), reply_markup=markup,
                     disable_web_page_preview=True)


@bot.message_handler(commands=["upload"])
def cmd_upload(message: types.Message) -> None:
    start_upload(message)


@bot.message_handler(commands=["cancel"])
def cmd_cancel(message: types.Message) -> None:
    s = sessions.clear(message.from_user.id)
    if s:
        remove_buttons(message.chat.id, s.get("prompt_msg"))
        text = "❌ Upload cancelled."
    else:
        text = "Nothing to cancel. 🙂"
    markup = main_menu(message.from_user.id) if message.chat.type == "private" else None
    bot.send_message(message.chat.id, text, reply_markup=markup)


@bot.message_handler(commands=["browse"])
def cmd_browse(message: types.Message) -> None:
    register_user(message.from_user)
    send_browse_home(message.chat.id)


@bot.message_handler(commands=["top"])
def cmd_top(message: types.Message) -> None:
    send_top(message.chat.id)


@bot.message_handler(commands=["latest"])
def cmd_latest(message: types.Message) -> None:
    text, kb = latest_view(0)
    bot.send_message(message.chat.id, text, reply_markup=kb)


@bot.message_handler(commands=["search"])
def cmd_search(message: types.Message) -> None:
    query = util.extract_arguments(message.text or "").strip()
    if not query:
        bot.reply_to(message, "🔎 Usage: <code>/search 3ps family flashcards</code>")
        return
    register_user(message.from_user)
    run_search(message.chat.id, query)


@bot.message_handler(commands=["id"])
def cmd_id(message: types.Message) -> None:
    bot.reply_to(
        message,
        f"🪪 Your Telegram user ID: <code>{message.from_user.id}</code>\n"
        f"💬 This chat ID: <code>{message.chat.id}</code>",
    )


# ---- admin commands ----------------------------------------------------------
def _admin_only(message: types.Message) -> bool:
    if is_admin(message.from_user.id):
        return True
    bot.reply_to(message, "⛔ This command is reserved for the administrator.")
    return False


@bot.message_handler(commands=["stats"])
def cmd_stats(message: types.Message) -> None:
    if _admin_only(message):
        bot.send_message(message.chat.id, stats_text())


@bot.message_handler(commands=["pending"])
def cmd_pending(message: types.Message) -> None:
    if not _admin_only(message):
        return
    pending = db.list_pending(10)
    if not pending:
        bot.send_message(message.chat.id, "✅ No pending submissions. All caught up!")
        return
    total = db.count_resources("pending")
    bot.send_message(message.chat.id, f"⏳ <b>{total}</b> pending submission(s). Showing {len(pending)}:")
    for r in pending:
        send_for_review(r)


@bot.message_handler(commands=["backup"])
def cmd_backup(message: types.Message) -> None:
    if not _admin_only(message):
        return
    if not ARCHIVE_CHANNEL_ID:
        bot.reply_to(message, "⚠️ ARCHIVE_CHANNEL_ID is not configured.")
        return
    status = bot.reply_to(message, "💾 Backing up the database…")
    msg = db.backup_to_channel(bot, ARCHIVE_CHANNEL_ID, "Manual /backup by admin")
    safe_edit_text(message.chat.id, status.message_id,
                   "✅ Backup sent and pinned in the archive channel." if msg
                   else "❌ Backup failed. Check the logs and the bot's channel permissions.")


def _restore_from_document(message: types.Message, doc: types.Document) -> None:
    name = (doc.file_name or "").lower()
    if not name.endswith((".db", ".sqlite", ".sqlite3")):
        bot.reply_to(message, "⚠️ That file is not a <code>.db</code> database backup.")
        return
    if doc.file_size and doc.file_size > MAX_RESTORE_BYTES:
        bot.reply_to(message, "⚠️ The file is larger than 20 MB (Telegram's bot download limit).")
        return

    sessions.clear(message.from_user.id)
    status = bot.reply_to(message, "♻️ Restoring the database… please wait.")
    try:
        if db.count_resources() > 0 and ARCHIVE_CHANNEL_ID:
            db.backup_to_channel(bot, ARCHIVE_CHANNEL_ID, "Safety snapshot before /restore_db")
        file_info = bot.get_file(doc.file_id)
        data = bot.download_file(file_info.file_path)
        stats = db.restore_from_bytes(data)
    except ValueError as exc:
        safe_edit_text(message.chat.id, status.message_id, f"❌ Restore refused: {h(exc)}")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("Restore failed")
        safe_edit_text(message.chat.id, status.message_id, f"❌ Restore failed: {h(exc)}")
        return

    # Pin the restored state as the newest backup so the next boot restores it too.
    backup_async("Restored via /restore_db")
    safe_edit_text(
        message.chat.id, status.message_id,
        "✅ <b>Database restored successfully!</b>\n\n"
        f"📚 Published: <b>{stats['approved']}</b>\n"
        f"⏳ Pending: <b>{stats['pending']}</b>\n"
        f"📥 Downloads: <b>{stats['downloads']}</b>\n"
        f"👥 Users: <b>{stats['users']}</b>\n\n"
        "💾 The restored database is being re-pinned in the archive channel.",
    )


@bot.message_handler(commands=["restore_db"])
def cmd_restore_db(message: types.Message) -> None:
    if not _admin_only(message):
        return
    reply = message.reply_to_message
    if not reply or not reply.document:
        bot.reply_to(
            message,
            "♻️ <b>How to restore</b>\n\n"
            "1. Open the archive channel and forward the latest <code>#DB_BACKUP</code> file to me.\n"
            "2. <b>Reply</b> to that file with /restore_db.",
        )
        return
    _restore_from_document(message, reply.document)


@bot.message_handler(
    content_types=["document"],
    func=lambda m: is_admin(m.from_user.id) and (m.caption or "").strip().startswith("/restore_db"),
)
def cmd_restore_db_caption(message: types.Message) -> None:
    """Also accept a .db file sent with '/restore_db' as its caption."""
    _restore_from_document(message, message.document)


@bot.message_handler(commands=["delete"])
def cmd_delete(message: types.Message) -> None:
    if not _admin_only(message):
        return
    arg = util.extract_arguments(message.text or "").strip().lstrip("#")
    if not arg.isdigit():
        bot.reply_to(message, "🗑 Usage: <code>/delete 12</code> (the resource ID)")
        return
    r = db.delete_resource(int(arg))
    if not r:
        bot.reply_to(message, f"⚠️ Resource <code>{h(arg)}</code> not found.")
        return
    if r.get("archive_msg_id") and ARCHIVE_CHANNEL_ID:
        try:
            bot.delete_message(ARCHIVE_CHANNEL_ID, r["archive_msg_id"])
        except ApiTelegramException as exc:
            log.info("Could not delete archive message for #%s: %s", r["id"], exc)
    backup_async(f"Deleted resource #{r['id']}")
    bot.reply_to(message, f"🗑 Deleted <b>{h(r['title'])}</b> (<code>{r['id']}</code>).")


# =============================================================================
# MENU BUTTONS (private chats)
# =============================================================================
@bot.message_handler(content_types=["text"],
                     func=lambda m: m.chat.type == "private" and (m.text or "") in MENU_BUTTONS)
def on_menu_button(message: types.Message) -> None:
    user_id = message.from_user.id
    text = message.text

    if text == BTN_UPLOAD:
        start_upload(message)
        return

    # Any other menu action cancels an unfinished upload.
    s = sessions.clear(user_id)
    if s:
        remove_buttons(message.chat.id, s.get("prompt_msg"))
        bot.send_message(message.chat.id, "ℹ️ Your unfinished upload was cancelled.")

    register_user(message.from_user)
    if text == BTN_BROWSE:
        send_browse_home(message.chat.id)
    elif text == BTN_TOP:
        send_top(message.chat.id)
    elif text == BTN_LATEST:
        body, kb = latest_view(0)
        bot.send_message(message.chat.id, body, reply_markup=kb)
    elif text == BTN_HELP:
        bot.send_message(message.chat.id, help_text(user_id), disable_web_page_preview=True)
    elif text == BTN_STATS:
        if _admin_only(message):
            bot.send_message(message.chat.id, stats_text())
    elif text == BTN_PENDING:
        cmd_pending(message)


# =============================================================================
# FILE RECEPTION (Step 1 of the wizard)
# =============================================================================
@bot.message_handler(content_types=["document", "photo", "video", "audio"],
                     func=lambda m: m.chat.type == "private")
def on_file(message: types.Message) -> None:
    user = message.from_user
    s = sessions.get(user.id)

    # Albums arrive as several messages: silently ignore the extra items.
    if s and message.media_group_id and s.get("media_group_id") == message.media_group_id:
        return
    if s and s.get("step") != "await_file":
        bot.reply_to(message,
                     "⚠️ You're in the middle of an upload. Finish the current steps or tap ❌ Cancel.",
                     reply_markup=kb_cancel_only())
        return

    # Admin forwarding a DB backup: guide to /restore_db instead of starting the wizard.
    if (is_admin(user.id) and message.document
            and (message.document.file_name or "").lower().endswith((".db", ".sqlite", ".sqlite3"))):
        sessions.clear(user.id)
        bot.reply_to(message, "🗄 Database file detected.\nReply to this file with /restore_db to restore it.")
        return

    info = extract_file(message)
    if not info:
        bot.reply_to(message, "⚠️ Unsupported file. Send a document, photo, video or audio file.")
        return

    duplicate = db.find_duplicate(info["file_unique_id"])
    if duplicate:
        sessions.clear(user.id)
        kb = types.InlineKeyboardMarkup()
        if duplicate["status"] == "approved":
            kb.row(result_button(duplicate))
            state = "is already published"
        else:
            state = "is already waiting for review"
        bot.reply_to(message, f"♻️ This file {state} in the library as "
                              f"<b>{h(duplicate['title'])}</b> (ID <code>{duplicate['id']}</code>).",
                     reply_markup=kb if kb.keyboard else None)
        return

    old_prompt = s.get("prompt_msg") if s else None
    remove_buttons(message.chat.id, old_prompt)
    register_user(user)
    sessions.start(user.id, step="await_title", chat_id=message.chat.id, file=info,
                   suggested=suggest_title(message, info), media_group_id=message.media_group_id)
    if message.media_group_id:
        bot.reply_to(message, "ℹ️ I received an album — only this first file will be uploaded. "
                              "Send the others one by one afterwards.")
    prompt_title(message.chat.id, user.id)


@bot.message_handler(content_types=["voice", "video_note", "animation", "sticker"],
                     func=lambda m: m.chat.type == "private")
def on_unsupported_media(message: types.Message) -> None:
    bot.reply_to(
        message,
        "⚠️ This type of message isn't supported.\n"
        "Please send resources as a <b>document</b>, <b>photo</b>, <b>video</b> or <b>audio</b> file "
        "(tip: use 📎 → File to keep the original quality).",
    )


# =============================================================================
# WIZARD TEXT STEPS (title / keywords)
# =============================================================================
@bot.message_handler(
    content_types=["text"],
    func=lambda m: m.chat.type == "private"
    and not (m.text or "").startswith("/")
    and sessions.get(m.from_user.id) is not None,
)
def on_wizard_text(message: types.Message) -> None:
    user_id = message.from_user.id
    s = sessions.get(user_id)
    step = s.get("step") if s else None
    text = re.sub(r"\s+", " ", message.text or "").strip()

    if step == "await_title":
        if len(text) < 3:
            bot.reply_to(message, "✏️ The title is too short — please use at least 3 characters.")
            return
        if len(text) > MAX_TITLE_LEN:
            bot.reply_to(message, f"✏️ The title is too long — maximum {MAX_TITLE_LEN} characters.")
            return
        remove_buttons(message.chat.id, s.get("prompt_msg"))
        sessions.update(user_id, title=text)
        go_to_levels(message.chat.id, user_id)

    elif step == "await_tags":
        remove_buttons(message.chat.id, s.get("prompt_msg"))
        sessions.update(user_id, tags=normalize_tags(text))
        show_confirmation(message.chat.id, user_id)

    elif step == "await_file":
        # The teacher changed their mind and typed a search instead of sending a file.
        remove_buttons(message.chat.id, s.get("prompt_msg"))
        sessions.clear(user_id)
        register_user(message.from_user)
        run_search(message.chat.id, text, prefix="ℹ️ <i>Upload cancelled — searching instead.</i>\n\n")

    else:
        bot.reply_to(message, "👆 Please use the buttons above to continue, or tap ❌ Cancel.",
                     reply_markup=kb_cancel_only())


# =============================================================================
# DEFAULT HANDLER: DIRECT KEYWORD SEARCH (private chats)
# =============================================================================
@bot.message_handler(content_types=["text"],
                     func=lambda m: m.chat.type == "private" and (m.text or "").startswith("/"))
def on_unknown_command(message: types.Message) -> None:
    bot.reply_to(message, "🤔 Unknown command. Send /help to see what I can do.")


@bot.message_handler(content_types=["text"], func=lambda m: m.chat.type == "private")
def on_search_text(message: types.Message) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        bot.reply_to(message, "🔎 Please type at least 2 characters to search.")
        return
    register_user(message.from_user)
    run_search(message.chat.id, query)


# =============================================================================
# CALLBACK HANDLERS
# =============================================================================
@bot.callback_query_handler(func=lambda c: c.data == "noop")
def cb_noop(call: types.CallbackQuery) -> None:
    safe_answer(call)


@bot.callback_query_handler(func=lambda c: (c.data or "").startswith("get_"))
def cb_get(call: types.CallbackQuery) -> None:
    raw = call.data[4:]
    if not raw.isdigit():
        safe_answer(call, "Invalid resource.", alert=True)
        return
    safe_answer(call, "📥 Sending your file…")
    deliver_resource(call_chat_id(call), call.from_user, int(raw))


# ---- upload wizard callbacks -------------------------------------------------
@bot.callback_query_handler(func=lambda c: (c.data or "").startswith("up_"))
def cb_upload(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    chat_id = call_chat_id(call)
    message_id = call.message.message_id if call.message else None
    data = call.data
    s = sessions.get(user_id)

    if data == "up_cancel":
        sessions.clear(user_id)
        safe_answer(call, "Upload cancelled")
        if message_id:
            safe_edit_text(chat_id, message_id, "❌ Upload cancelled. Tap 📤 Upload Resource to start again.")
        return

    def expired() -> None:
        safe_answer(call, "⌛ This step has expired. Use /upload to start again.", alert=True)
        remove_buttons(chat_id, message_id)

    if not s or not message_id:
        expired()
        return
    step = s.get("step")

    if data == "up_fn":
        if step != "await_title" or not s.get("suggested"):
            expired()
            return
        sessions.update(user_id, title=s["suggested"])
        safe_answer(call)
        safe_edit_text(chat_id, message_id, f"✏️ <b>Title:</b> {h(s['suggested'])}")
        go_to_levels(chat_id, user_id)

    elif data.startswith("up_lv:"):
        level = data.split(":", 1)[1]
        if step != "await_level" or not cur.is_valid_level(level):
            expired()
            return
        sessions.update(user_id, level=level)
        safe_answer(call)
        go_to_units(chat_id, user_id, message_id)

    elif data.startswith("up_un:"):
        raw = data.split(":", 1)[1]
        unit = cur.get_unit(s.get("level", ""), int(raw)) if raw.isdigit() else None
        if step != "await_unit" or not unit:
            expired()
            return
        sessions.update(user_id, unit=unit)
        safe_answer(call)
        go_to_categories(chat_id, user_id, message_id)

    elif data.startswith("up_ct:"):
        raw = data.split(":", 1)[1]
        category = cur.get_category(int(raw)) if raw.isdigit() else None
        if step != "await_category" or not category:
            expired()
            return
        sessions.update(user_id, category=category)
        safe_answer(call)
        go_to_tags(chat_id, user_id, message_id)

    elif data.startswith("up_back:"):
        target = data.split(":", 1)[1]
        if target == "lv" and step == "await_unit":
            safe_answer(call)
            go_to_levels(chat_id, user_id, edit_message_id=message_id)
        elif target == "un" and step == "await_category":
            safe_answer(call)
            go_to_units(chat_id, user_id, message_id)
        elif target == "ct" and step == "await_tags":
            safe_answer(call)
            go_to_categories(chat_id, user_id, message_id)
        else:
            expired()

    elif data == "up_skip":
        if step != "await_tags":
            expired()
            return
        sessions.update(user_id, tags="")
        safe_answer(call)
        show_confirmation(chat_id, user_id, edit_message_id=message_id)

    elif data == "up_ok":
        if step != "await_confirm":
            expired()
            return
        sessions.update(user_id, step="submitting")  # guards against double taps
        safe_answer(call, "Submitting…")
        finalize_upload(call, s)

    else:
        safe_answer(call)


# ---- admin approval callbacks ------------------------------------------------
@bot.callback_query_handler(func=lambda c: (c.data or "").startswith("adm_"))
def cb_admin_decision(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        safe_answer(call, "⛔ Only the administrator can do this.", alert=True)
        return
    action, _, raw_id = call.data.partition(":")
    if not raw_id.isdigit():
        safe_answer(call, "Invalid request.", alert=True)
        return
    resource_id = int(raw_id)
    chat_id = call_chat_id(call)
    message_id = call.message.message_id if call.message else None

    r = db.get_resource(resource_id)
    if not r:
        safe_answer(call, "⚠️ Resource not found (deleted, or the DB was restored).", alert=True)
        remove_buttons(chat_id, message_id)
        return

    if action == "adm_ok":
        if not db.claim_pending(resource_id, "approved"):
            safe_answer(call, f"Already {r['status']}.", alert=True)
            remove_buttons(chat_id, message_id)
            return
        r = db.get_resource(resource_id)
        archived = publish_to_archive(r)
        backup_async(f"Approved resource #{resource_id}")
        safe_answer(call, "✅ Approved & published!")
        if message_id:
            safe_edit_caption(
                chat_id, message_id,
                review_caption(r) + "\n\n✅ <b>APPROVED & PUBLISHED</b>"
                + ("" if archived else "\n⚠️ Not posted to the archive channel (check configuration)."),
            )
        if r["uploader_id"] != ADMIN_ID:
            link = bot_link(f"get_{resource_id}")
            try:
                bot.send_message(
                    r["uploader_id"],
                    "🎉 <b>Great news!</b> Your resource is now live in the library:\n\n"
                    f"📚 <b>{h(r['title'])}</b>\n🎓 {h(r['level'])} · 📘 {h(r['unit'])}\n"
                    f"🆔 ID: <code>{resource_id}</code>\n"
                    + (f"🔗 Share link: {link}\n" if link else "")
                    + "\nThank you for supporting fellow teachers! 💙",
                    disable_web_page_preview=True,
                )
            except ApiTelegramException as exc:
                log.info("Could not notify contributor %s: %s", r["uploader_id"], exc)

    elif action == "adm_no":
        if not db.claim_pending(resource_id, "rejected"):
            safe_answer(call, f"Already {r['status']}.", alert=True)
            remove_buttons(chat_id, message_id)
            return
        safe_answer(call, "❌ Rejected.")
        if message_id:
            safe_edit_caption(chat_id, message_id, review_caption(r) + "\n\n❌ <b>REJECTED</b>")
        if r["uploader_id"] != ADMIN_ID:
            try:
                bot.send_message(
                    r["uploader_id"],
                    f"ℹ️ Your submission <b>{h(r['title'])}</b> (ID <code>{resource_id}</code>) was not "
                    "published this time — it may be a duplicate, off-curriculum, or need a clearer "
                    "title/category.\n\nThank you for contributing — feel free to /upload again! 🙏",
                )
            except ApiTelegramException as exc:
                log.info("Could not notify contributor %s: %s", r["uploader_id"], exc)
    else:
        safe_answer(call)


# ---- browse callbacks ----------------------------------------------------------
@bot.callback_query_handler(func=lambda c: (c.data or "").startswith("br_"))
def cb_browse(call: types.CallbackQuery) -> None:
    chat_id = call_chat_id(call)
    if not call.message:
        safe_answer(call)
        return
    message_id = call.message.message_id
    parts = call.data.split(":")
    action = parts[0]
    try:
        if action == "br_home":
            safe_edit_text(chat_id, message_id, "📂 <b>Browse the library</b>\n\nChoose a level:",
                           kb_browse_levels())

        elif action == "br_lv":
            level = parts[1]
            if not cur.is_valid_level(level):
                raise ValueError(level)
            safe_edit_text(chat_id, message_id,
                           f"📂 <b>{h(cur.level_label(level))}</b>\n\nChoose a unit / sequence:",
                           kb_browse_units(level))

        elif action == "br_un":
            level, unit_key = parts[1], parts[2]
            if not cur.is_valid_level(level) or not (unit_key == "a" or cur.get_unit(level, int(unit_key))):
                raise ValueError(unit_key)
            unit_name = "All units" if unit_key == "a" else cur.get_unit(level, int(unit_key))
            safe_edit_text(chat_id, message_id,
                           f"📂 <b>{h(cur.level_label(level))}</b>\n📘 {h(unit_name)}\n\nChoose a category:",
                           kb_browse_categories(level, unit_key))

        elif action == "br_ct":
            level, unit_key, cat_key, page = parts[1], parts[2], parts[3], int(parts[4])
            if not cur.is_valid_level(level):
                raise ValueError(level)
            if unit_key != "a" and not cur.get_unit(level, int(unit_key)):
                raise ValueError(unit_key)
            if cat_key != "a" and not cur.get_category(int(cat_key)):
                raise ValueError(cat_key)
            text, kb = browse_results(level, unit_key, cat_key, max(page, 0))
            safe_edit_text(chat_id, message_id, text, kb)
        safe_answer(call)
    except (ValueError, IndexError):
        safe_answer(call, "⚠️ This menu is outdated. Send /browse to start again.", alert=True)


# ---- pagination callbacks --------------------------------------------------------
@bot.callback_query_handler(func=lambda c: (c.data or "").startswith("sp:"))
def cb_search_page(call: types.CallbackQuery) -> None:
    try:
        _, key, raw_page = call.data.split(":")
        page = int(raw_page)
    except ValueError:
        safe_answer(call, "Invalid page.", alert=True)
        return
    query = search_cache.get(key)
    if query is None or not call.message:
        safe_answer(call, "⌛ This search has expired — please send your keywords again.", alert=True)
        return
    safe_answer(call)
    run_search(call_chat_id(call), query, page=page, key=key, edit_message_id=call.message.message_id)


@bot.callback_query_handler(func=lambda c: (c.data or "").startswith("lt:"))
def cb_latest_page(call: types.CallbackQuery) -> None:
    if not call.message:
        safe_answer(call)
        return
    try:
        page = max(0, int(call.data.split(":")[1]))
    except (ValueError, IndexError):
        safe_answer(call, "Invalid page.", alert=True)
        return
    text, kb = latest_view(page)
    safe_edit_text(call_chat_id(call), call.message.message_id, text, kb)
    safe_answer(call)


@bot.callback_query_handler(func=lambda c: True)
def cb_fallback(call: types.CallbackQuery) -> None:
    safe_answer(call, "⌛ This button is no longer active.")


# =============================================================================
# INLINE MODE  (@BotUsername query — works in any chat)
# =============================================================================
def build_inline_result(r: Dict[str, Any]) -> Optional[types.InlineQueryResultBase]:
    rid = str(r["id"])
    title = truncate(f"[{r['level']}] {r['title']}", 64)
    description = truncate(f"{r['unit']} • {cur.short_category(r['category'])} • 📥 {r['downloads']}", 120)
    caption = resource_caption(r)
    ft, fid = r["file_type"], r["file_id"]
    if ft == "photo":
        return types.InlineQueryResultCachedPhoto(id=rid, photo_file_id=fid, title=title,
                                                  description=description, caption=caption,
                                                  parse_mode="HTML")
    if ft == "video":
        return types.InlineQueryResultCachedVideo(id=rid, video_file_id=fid, title=title,
                                                  description=description, caption=caption,
                                                  parse_mode="HTML")
    if ft == "audio":
        return types.InlineQueryResultCachedAudio(id=rid, audio_file_id=fid, caption=caption,
                                                  parse_mode="HTML")
    if ft == "document":
        return types.InlineQueryResultCachedDocument(id=rid, document_file_id=fid, title=title,
                                                     description=description, caption=caption,
                                                     parse_mode="HTML")
    return None


@bot.inline_handler(func=lambda q: True)
def on_inline_query(query: types.InlineQuery) -> None:
    text = (query.query or "").strip()[:100]
    offset = int(query.offset) if (query.offset or "").isdigit() else 0
    try:
        if text:
            rows, total = db.search(text, INLINE_PAGE_SIZE, offset)
        else:
            rows = db.top_downloads(INLINE_PAGE_SIZE) if offset == 0 else []
            total = len(rows)
        results = [res for res in (build_inline_result(r) for r in rows) if res is not None]
        next_offset = str(offset + INLINE_PAGE_SIZE) if text and offset + INLINE_PAGE_SIZE < total else ""
        bot.answer_inline_query(
            query.id,
            results,
            cache_time=15,
            is_personal=False,
            next_offset=next_offset,
            button=types.InlineQueryResultsButton(
                text="📚 Open the Primary ESL Library" if results else "🔍 Nothing found — open the bot",
                start_parameter="inline",
            ),
        )
    except ApiTelegramException as exc:
        log.warning("answer_inline_query failed: %s", exc)


@bot.chosen_inline_handler(func=lambda r: True)
def on_chosen_inline(result: types.ChosenInlineResult) -> None:
    """Counts inline shares as downloads (requires /setinlinefeedback in @BotFather)."""
    if (result.result_id or "").isdigit():
        db.increment_downloads(int(result.result_id))
        register_user(result.from_user)


# =============================================================================
# FLASK ROUTES  (health check + Telegram webhook)
# =============================================================================
@app.route("/", methods=["GET", "HEAD"])
def index():
    return jsonify(
        status="ok",
        message="Primary ESL Bot is running!",
        bot=f"@{BOT_USERNAME}" if BOT_USERNAME else None,
        mode=RUN_MODE,
        webhook_url=f"{WEBHOOK_URL}/webhook" if WEBHOOK_URL else None,
        uptime_seconds=int(time.time() - START_TIME),
        resources=db.count_resources("approved"),
    )


@app.route("/health", methods=["GET", "HEAD"])
def health():
    return jsonify(status="ok")


@app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)
    try:
        update = types.Update.de_json(request.get_data(as_text=True))
        if update is not None:
            bot.process_new_updates([update])
    except Exception:  # noqa: BLE001 — always 200 so Telegram doesn't retry forever
        log.exception("Failed to process webhook update")
    return "", 200


# =============================================================================
# STARTUP / BACKGROUND TASKS
# =============================================================================
def _retry(fn, attempts: int = 3, delay: float = 2.0, what: str = "operation"):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("%s failed (attempt %s/%s): %s", what, attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(delay * attempt)
    return None


def setup_bot_commands() -> None:
    public = [
        types.BotCommand("start", "Main menu"),
        types.BotCommand("search", "Search resources, e.g. /search 3ps family"),
        types.BotCommand("browse", "Browse by level, unit & category"),
        types.BotCommand("upload", "Share a resource with teachers"),
        types.BotCommand("top", "Most downloaded resources"),
        types.BotCommand("latest", "Newest resources"),
        types.BotCommand("cancel", "Cancel the current upload"),
        types.BotCommand("help", "How to use the bot"),
        types.BotCommand("id", "Show your Telegram ID"),
    ]
    admin = public + [
        types.BotCommand("stats", "Library statistics"),
        types.BotCommand("pending", "Review pending submissions"),
        types.BotCommand("backup", "Back up the database now"),
        types.BotCommand("restore_db", "Reply to a .db file to restore it"),
        types.BotCommand("delete", "Delete a resource: /delete ID"),
    ]
    try:
        bot.set_my_commands(public)
        if ADMIN_ID:
            bot.set_my_commands(admin, scope=types.BotCommandScopeChat(ADMIN_ID))
    except ApiTelegramException as exc:
        log.info("Could not set bot commands (admin may not have started the bot yet): %s", exc)


def _backup_scheduler() -> None:
    """Periodically back up download counters / new users if anything changed."""
    interval = max(15, BACKUP_INTERVAL_MINUTES) * 60
    while True:
        time.sleep(interval)
        try:
            if db.has_unsaved_changes():
                db.backup_to_channel(bot, ARCHIVE_CHANNEL_ID, "Scheduled backup (downloads & activity)")
        except Exception:  # noqa: BLE001
            log.exception("Scheduled backup failed")


def _final_backup() -> None:
    """Best-effort backup when the process stops (Render redeploy / spin-down)."""
    try:
        if ARCHIVE_CHANNEL_ID and db.has_unsaved_changes():
            log.info("Process exiting with unsaved changes — sending final backup…")
            db.backup_to_channel(bot, ARCHIVE_CHANNEL_ID, "Shutdown with unsaved changes")
    except Exception:  # noqa: BLE001
        log.exception("Final backup failed")


_bootstrap_lock = threading.Lock()
_bootstrapped = False


def bootstrap() -> None:
    """One-time startup: identity, auto-restore, commands, scheduler, webhook."""
    global BOT_USERNAME, RUN_MODE, _bootstrapped
    with _bootstrap_lock:
        if _bootstrapped:
            return
        _bootstrapped = True

    me = _retry(bot.get_me, what="getMe")
    if me:
        BOT_USERNAME = me.username or ""
        log.info("Bot authorised as @%s (id %s)", BOT_USERNAME, me.id)
    else:
        log.error("Could not reach Telegram (check BOT_TOKEN / network).")

    if not ADMIN_ID:
        log.warning("ADMIN_ID is not set — uploads cannot be approved and admin commands are disabled.")
    if not ARCHIVE_CHANNEL_ID:
        log.warning("ARCHIVE_CHANNEL_ID is not set — files and DB backups will NOT be archived.")

    # Ephemeral disk (Render free tier): restore the latest pinned backup if the DB is empty.
    if ARCHIVE_CHANNEL_ID and db.count_resources() == 0:
        log.info("Local database is empty — trying to restore the latest backup from the channel…")
        db.restore_from_channel(bot, ARCHIVE_CHANNEL_ID)

    setup_bot_commands()

    if ARCHIVE_CHANNEL_ID and BACKUP_INTERVAL_MINUTES > 0:
        threading.Thread(target=_backup_scheduler, name="backup-scheduler", daemon=True).start()
    atexit.register(_final_backup)

    if WEBHOOK_URL:
        def _set_webhook() -> bool:
            bot.remove_webhook()
            time.sleep(0.5)
            return bot.set_webhook(
                url=f"{WEBHOOK_URL}/webhook",
                secret_token=WEBHOOK_SECRET,
                allowed_updates=ALLOWED_UPDATES,
                max_connections=40,
            )

        if _retry(_set_webhook, what="setWebhook"):
            RUN_MODE = "webhook"
            log.info("Webhook set to %s/webhook", WEBHOOK_URL)
        else:
            RUN_MODE = "webhook (failed to register)"
            log.error("Webhook registration failed — check WEBHOOK_URL.")
    else:
        RUN_MODE = "polling"


def run_polling() -> None:
    log.info("WEBHOOK_URL not set — starting long polling (local mode).")
    try:
        bot.remove_webhook()
    except ApiTelegramException as exc:
        log.warning("remove_webhook failed: %s", exc)
    bot.infinity_polling(timeout=20, long_polling_timeout=20, allowed_updates=ALLOWED_UPDATES)


if __name__ == "__main__":
    # Local execution:  python app.py
    bootstrap()
    if WEBHOOK_URL:
        app.run(host="0.0.0.0", port=PORT)
    else:
        run_polling()
else:
    # Imported by gunicorn (Render). Keep exactly ONE worker (see render.yaml).
    bootstrap()
    if not WEBHOOK_URL:
        threading.Thread(target=run_polling, name="polling", daemon=True).start()
