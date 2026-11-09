# 🇩🇿 Primary ESL DZ Bot — Resource Upload & Search Engine

A production-ready Telegram Bot built for **Algerian Primary School ESL Teachers (3PS, 4PS, 5PS)**. Teachers can search, browse, share, and download lesson plans, flashcards, worksheets, exams, songs, videos, and classroom games—all organized around the official Algerian Primary ESL curriculum.

Architected specifically for **GitHub** + **Render.com (Free Web Service Tier)** with an automatic **Private Telegram Channel Archive & Database Backup/Restore** system so zero data is lost when Render's ephemeral disk restarts.

---

## ✨ Features

### 📤 Interface A — Multi-Step Upload Wizard (`/upload` or `📤 Upload Resource`)
1. **File Reception:** Accepts `document`, `photo`, `video`, or `audio` (with automatic duplicate detection via `file_unique_id`).
2. **Title:** Prompts for a clear resource title (or 1-click use of the cleaned filename/caption).
3. **Level Selection:** Inline buttons for `3PS`, `4PS`, `5PS`, and `General`.
4. **Unit/Sequence Selection:** Dynamically displays the exact units/sequences for the selected level from [`curriculum.py`](file:///d:/Projects/PrimaryESL_DZ_Bot/curriculum.py).
5. **Category Selection:** `📄 Lesson Plan`, `🖼️ Flashcards / Posters`, `📝 Worksheet / Exam`, `🎵 Song / Audio`, `🎬 Video`, `🎮 Game / Activity`.
6. **Keywords/Tags:** Free-text tags for rich searchability (or skip).
7. **Confirmation & Approval Workflow:**
   - **Admin Upload (`user.id == ADMIN_ID`):** Immediately published (`status = 'approved'`), copied to `ARCHIVE_CHANNEL_ID`, and triggers an automatic `#DB_BACKUP` upload to the archive channel.
   - **Teacher Contributor Upload (`user.id != ADMIN_ID`):** Saved as `status = 'pending'` and forwarded to the Admin with `[ ✅ Approve & Publish ]` and `[ ❌ Reject ]` buttons. Once approved, the bot archives the resource, notifies the contributor teacher with a direct share link, and posts an updated `#DB_BACKUP` to the archive channel.

### 🔎 Interface B — Teacher Search Engine (3 Search Modes)
1. **Direct Keyword Search:** Send any plain text (e.g., `3ps family flashcards`, `lesson plan jobs`, `4ap food exam`). Tokenized, synonym-aware (`3ap` → `3ps`, `fiche` → `lesson plan`), case-insensitive SQL `LIKE %token%` search across `title`, `level`, `unit`, `category`, `tags`, and `file_name` with 10 results per page and `⬅️ Previous` / `Next ➡️` pagination.
2. **Guided Filter Browse (`📂 Browse by Level`):** Interactive drill-down menu (`Level` → `Unit / Sequence` → `Category` → downloadable file list) showing live resource counts at every step.
3. **Inline Query Mode (`@YourBotUsername query`):** Share resources directly inside any Telegram group or chat using cached inline results (`InlineQueryResultCachedDocument`, `Photo`, `Video`, `Audio`).

### 💾 Ephemeral-Safe Persistence (`#DB_BACKUP` & `/restore_db`)
Render's Free Tier wipes the local filesystem on every deploy or spin-down. This bot solves that in three ways:
1. **Automatic Backup on Every Change:** Every approved upload, deletion, or periodic download-counter update creates a consistent SQLite online backup and sends `esl_library_<timestamp>.db` to `ARCHIVE_CHANNEL_ID` tagged `#DB_BACKUP` and pins it.
2. **Automatic Boot Restore:** On startup, if the local `esl_library.db` is empty, the bot checks the pinned message in `ARCHIVE_CHANNEL_ID` and automatically downloads & restores the latest `#DB_BACKUP`.
3. **Manual Admin Restore (`/restore_db`):** Reply to any `.db` file in Telegram with `/restore_db` (or send a `.db` file with `/restore_db` as its caption) to validate its SQLite integrity and hot-restore the live database.

---

## 📁 Project Structure

```text
PrimaryESL_DZ_Bot/
├── app.py             # Flask webhook/health-check server + pyTelegramBotAPI handlers
├── database.py        # Thread-safe SQLite3 manager, search engine & channel backup/restore
├── curriculum.py      # Algerian Primary ESL curriculum (3PS, 4PS, 5PS, General) & categories
├── requirements.txt   # Pinned production dependencies
├── render.yaml        # Render.com Blueprint for 1-click deployment
├── .gitignore         # Standard Python + SQLite gitignore
└── README.md          # Setup, deployment & operations guide
```

---

## 🛠️ Step 1: Create the Bot & Private Archive Channel

### 1.1 Create Your Telegram Bot (`BOT_TOKEN`)
1. Open Telegram and message **[@BotFather](https://t.me/BotFather)**.
2. Send `/newbot`, choose a display name (e.g., `Primary ESL DZ Library`) and a username ending in `bot` (e.g., `PrimaryESL_DZ_Bot`).
3. Copy the **HTTP API Token** — this is your `BOT_TOKEN`.
4. **Enable Inline Mode** (required for `@BotUsername query` in groups):
   - Send `/setinline` to `@BotFather`, select your bot, and type a placeholder like `Search 3PS, 4PS, 5PS resources...`.
   - Send `/setinlinefeedback` to `@BotFather`, select your bot, and choose `Enabled` (so inline shares increment the download counter).

### 1.2 Get Your Admin Telegram ID (`ADMIN_ID`)
1. Message **[@userinfobot](https://t.me/userinfobot)** on Telegram (or start your own bot once it's running and send `/id`).
2. Copy your numeric **Id** (e.g., `123456789`) — this is your `ADMIN_ID`.

### 1.3 Set Up the Private Archive Channel (`ARCHIVE_CHANNEL_ID`)
1. In Telegram, create a **New Channel** → name it e.g. `ESL DZ Bot Archive & Backups` → set it to **Private**.
2. Open **Channel Info → Administrators → Add Admin** and add your bot (`@YourBotUsername`).
3. Make sure the following permissions are **enabled** for the bot:
   - ✅ **Post Messages** (required to archive files and upload `#DB_BACKUP` snapshots)
   - ✅ **Edit Messages of Others** & **Pin Messages** (required so the bot can automatically pin the latest `#DB_BACKUP` for auto-restore on boot)
   - ✅ **Delete Messages of Others** (optional, used when `/delete <id>` removes a resource)
4. Get the Channel ID:
   - Post any test message in the channel and forward it to **[@userinfobot](https://t.me/userinfobot)** (or **[@JsonDumpBot](https://t.me/JsonDumpBot)**).
   - Copy the channel ID—it **must start with `-100`** (for example `-1001987654321`). This is your `ARCHIVE_CHANNEL_ID`.

---

## 💻 Step 2: Local Testing (Optional)

When `WEBHOOK_URL` is not set, [`app.py`](file:///d:/Projects/PrimaryESL_DZ_Bot/app.py) automatically falls back to `bot.infinity_polling()`.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variables (PowerShell example)
$env:BOT_TOKEN="123456789:AAH..."
$env:ADMIN_ID="123456789"
$env:ARCHIVE_CHANNEL_ID="-1001987654321"

# 4. Run the bot in polling mode
python app.py
```

---

## 🚀 Step 3: Push to GitHub

```bash
git init
git add app.py database.py curriculum.py requirements.txt render.yaml .gitignore README.md
git commit -m "Initial release: Primary ESL DZ Bot"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo-name>.git
git push -u origin main
```

---

## ☁️ Step 4: Deploy on Render.com (Free Web Service Tier)

### Option A — 1-Click Blueprint Deploy (`render.yaml`)
1. Log in to **[Render.com](https://dashboard.render.com/)**.
2. Click **New +** → **Blueprint**.
3. Connect your GitHub repository containing this project.
4. Render will detect [`render.yaml`](file:///d:/Projects/PrimaryESL_DZ_Bot/render.yaml) and prompt you for the secret environment variables:
   - `BOT_TOKEN`: Your BotFather token
   - `ADMIN_ID`: Your numeric Telegram user ID
   - `ARCHIVE_CHANNEL_ID`: Your private channel ID (`-100...`)
   - `WEBHOOK_URL`: Your Render service URL, e.g. `https://primary-esl-dz-bot.onrender.com` *(no trailing slash; Note: Render also sets `RENDER_EXTERNAL_URL` automatically, which `app.py` uses as a built-in fallback!)*
5. Click **Apply** and wait ~2 minutes for the build to go live.

### Option B — Manual Web Service Setup
1. Click **New +** → **Web Service** → connect your GitHub repo.
2. Set:
   - **Runtime:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
   - **Instance Type:** `Free`
3. Under **Environment Variables**, add `BOT_TOKEN`, `ADMIN_ID`, `ARCHIVE_CHANNEL_ID`, and `WEBHOOK_URL`.
4. Deploy the service. Verify it is live by visiting `https://<your-app-name>.onrender.com/` — you will see:
   ```json
   {
     "bot": "@YourBotUsername",
     "message": "Primary ESL Bot is running!",
     "mode": "webhook",
     "resources": 0,
     "status": "ok",
     "uptime_seconds": 42
   }
   ```

---

## ⏰ Step 5: Keep-Alive Setup (Prevent Render Free Tier from Sleeping)

Render's free tier spins down after 15 minutes of inactivity. Use a free pinger to hit the `GET /` health-check endpoint every 5–10 minutes:

1. Create a free account on **[UptimeRobot](https://uptimerobot.com/)** or **[cron-job.org](https://cron-job.org/)**.
2. Add a new **HTTP(s) Monitor**:
   - **URL:** `https://<your-app-name>.onrender.com/`
   - **Interval:** Every **5 minutes** (or 10 minutes).
3. Save. Your bot will now respond instantaneously 24/7.

---

## 🛡️ Admin Commands Reference

| Command | Description |
| :--- | :--- |
| `/stats` | Show total published files, pending approvals, downloads, level breakdown, and top 10 resources |
| `/pending` | Re-send all pending teacher submissions with `[ ✅ Approve & Publish ]` / `[ ❌ Reject ]` buttons |
| `/backup` | Immediately create and pin a fresh `#DB_BACKUP` snapshot in the archive channel |
| `/restore_db` | Reply to any `.db` backup file with `/restore_db` to replace the local `esl_library.db` |
| `/delete <ID>` | Delete a resource by ID from SQLite and remove its archived message from the channel |
| `/id` | Show your Telegram User ID and the current Chat ID |
