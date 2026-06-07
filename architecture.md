# SupBot — Architecture & Production Guide

> Keep this file up to date whenever the system changes. It is the source of truth for how the system works and how to deploy it.

---

## Table of Contents
1. [System Overview](#1-system-overview)
2. [Component Map](#2-component-map)
3. [Data Flow](#3-data-flow)
4. [Technology Stack & Cost](#4-technology-stack--cost)
5. [Multi-tenancy Design](#5-multi-tenancy-design)
6. [RAG Pipeline](#6-rag-pipeline)
7. [Escalation Logic](#7-escalation-logic)
8. [Script Normalization](#8-script-normalization)
9. [Production Deployment](#9-production-deployment)
10. [Monitoring & Operations](#10-monitoring--operations)
11. [Scaling Path](#11-scaling-path)
12. [Monetization](#12-monetization)
13. [Known Gaps & Next Steps](#13-known-gaps--next-steps)

---

## 1. System Overview

SupBot is a **multi-tenant B2B Telegram support bot** for small Uzbek/Russian shops. One server process runs all shops' bots simultaneously. Each shop gets its own Telegram bot (created via BotFather), branded as their own, while the backend is shared.

**Core value proposition:** answers routine questions (price, size, availability, delivery) in mixed Uzbek/Russian, routes real questions to the shop manager, and saves the cost of a dedicated support employee (~200,000–500,000 UZS/month subscription).

---

## 2. Component Map

```
SupBot/
├── main.py                  # entry point (delegates to bot/main.py)
├── bot/
│   ├── main.py              # starts all bots in one asyncio process
│   ├── config.py            # env vars; shop registry now in DB
│   ├── handlers.py          # aiogram message handlers (customer + manager flows)
│   ├── rag.py               # retrieval-augmented generation orchestrator
│   ├── store.py             # SQLite + sqlite-vec: vectors, escalation, history, shops
│   ├── embeddings.py        # local multilingual embeddings (sentence-transformers)
│   ├── llm.py               # OpenAI-compat LLM call (DeepSeek default)
│   ├── translit.py          # Latin↔Cyrillic Uzbek normalizer
│   └── ingest.py            # CLI: load a shop's CSV catalog into the DB
├── admin/
│   ├── app.py               # FastAPI admin UI (port 8080, cookie-based session auth)
│   └── i18n.py              # UI translations: English, Russian, Uzbek (Latin)
├── data/
│   ├── bot.db               # SQLite DB (created on first run)
│   └── sample_catalog.csv   # example product/FAQ CSV
├── .env                     # secrets (not committed — copy from .example)
└── requirements.txt
```

---

## 3. Data Flow

### Customer message
```
Customer types → Telegram → aiogram handler (handlers.py)
                                │
                     is_escalated? ──yes──▶ (silent, human owns thread)
                                │no
                     wants_human(text)?  (keyword check via translit.fold)
                         │yes                │no
                         ▼                   ▼
                    _handoff()          rag.answer(shop_id, text)
                         │               │
                         │         embed query (embeddings.py)
                         │               │
                         │         sqlite-vec KNN search (store.py)
                         │               │
                         │         distance > threshold? ──yes──▶ _handoff()
                         │               │no
                         │         llm.generate(text, context)  (llm.py)
                         │               │
                         │         [ESCALATE] in reply? ──yes──▶ _handoff()
                         │               │no
                         ▼               ▼
                    manager notified    reply sent to customer
```

### Manager flow
```
Manager's Telegram ──▶ /reply <user_id> <text>  →  bot relays to customer
                    ──▶ /resume <user_id>         →  clears escalation, bot resumes
```

### Catalog ingestion (one-time / on update)
```
CSV file → ingest.py → embed_passages() → sqlite-vec INSERT (per shop_id partition)
```

---

## 4. Technology Stack & Cost

### Admin UI (new)
The admin UI is a FastAPI app at `admin/app.py`, served separately from the bot on port 8080.

**Start:**
```bash
uvicorn admin.app:app --host 0.0.0.0 --port 8080 --reload   # dev
uvicorn admin.app:app --host 0.0.0.0 --port 8080             # prod
```

**Features:**
- Shop list with catalog chunk count and escalation status
- Add shop form (name, BotFather token, manager chat ID)
- Upload a catalog CSV with two modes:
  - **Replace catalog** — wipes the shop's existing catalog and ingests the new file from scratch.
  - **Update catalog** — deduplicates by the first CSV column: rows whose first-column value matches an existing chunk are replaced; everything else in the existing catalog is preserved.
- Delete shop (removes catalog, history, escalations)
- Download sample CSV template

**Auth:** Cookie-based session auth backed by the SQLite DB.
- Admin accounts stored in `admin_users` table (PBKDF2-SHA256 hashed passwords, 260k iterations, no extra dependencies).
- Sessions stored in `admin_sessions` table (random 64-hex token, 30-day TTL).
- `GET /register` is open only when no admin accounts exist (first-run bootstrap); after that it requires an existing admin session.
- `GET /login`, `POST /login`, `POST /logout` — standard form-based login/logout.
- All other routes redirect to `/login` if no valid session cookie is present.
- `ADMIN_USER` / `ADMIN_PASSWORD` in `.env` are no longer used by the admin UI (kept in config for backwards compatibility only).

**Internationalisation:** The admin UI supports **English, Russian, and Uzbek** (Latin script).
- Language is auto-detected from the browser's `Accept-Language` header on every request.
- Users can override with the `EN / RU / UZ` switcher in the navbar; the choice is saved in an `sb_lang` cookie (1-year expiry).
- All translations live in `admin/i18n.py` under the `T` dict; flash messages are translated at redirect time.

**Shop registry:** Shops are now stored in the `shops` table in `bot.db` (not `shops.json`). On first bot startup, any existing `shops.json` is automatically migrated to the DB.



| Layer | Choice | Cost |
|---|---|---|
| Bot framework | aiogram 3.x (async Python) | free |
| Embeddings | `intfloat/multilingual-e5-small` local CPU | free |
| Vector store | sqlite-vec (embedded in SQLite) | free |
| Database | SQLite (`data/bot.db`) | free |
| LLM | DeepSeek Chat V3 (default) | ~$0.0003/msg → ~$3/shop/mo |
| LLM (quality) | Gemini 2.5 Flash | ~$0.0009/msg → ~$8/shop/mo |
| Hosting | Oracle Cloud Always-Free (4 cores, 24 GB) | **$0/mo** |
| Hosting (alt) | Hetzner CAX11 (2 cores, 4 GB ARM) | ~€4/mo |

**Revenue per shop:** 200,000–500,000 UZS/mo ≈ $16–40/mo  
**Variable cost per shop:** $2–8/mo LLM  
**Gross margin:** ~80–95%

**Fixed infra cost: $0–6/month total**, regardless of shop count (up to dozens).

---

## 5. Multi-tenancy Design

- Each shop registers a **separate Telegram bot** via BotFather and provides the token in `shops.json`.
- `bot/main.py` starts one `Bot` instance per token, all sharing one `Dispatcher`.
- Handlers receive the active `Bot` object and look up the shop config by `bot.token` — this is the tenancy boundary.
- Catalog vectors in sqlite-vec use `shop_id` as a **partition key**: queries are automatically scoped to one tenant with no cross-contamination.
- Escalation state in the `escalations` table is keyed `(shop_id, user_id)`.
- Adding a new shop = use the admin UI (or add one entry to `shops.json`) + ingest their catalog + restart the process.

**SQLite tables (bot.db):**
| Table | Purpose |
|---|---|
| `catalog` | sqlite-vec virtual table — catalog embeddings, partitioned by `shop_id` |
| `shops` | Shop registry (shop_id, name, bot_token, manager_chat_id) |
| `escalations` | Active escalations (shop_id, user_id) |
| `contacts` | Customer name/username cache (shop_id, user_id) |
| `messages` | Full conversation history (shop_id, user_id, role, text, ts) |
| `admin_users` | Admin accounts (username, PBKDF2-SHA256 password_hash) |
| `admin_sessions` | Active web sessions (token, username, created_at; 30-day TTL) |

`shops.json` schema:
```json
[
  {
    "shop_id": "acme_shoes",
    "name": "Acme Shoes",
    "bot_token": "123456:ABC...",
    "manager_chat_id": 987654321
  }
]
```

---

## 6. RAG Pipeline

**Ingest (offline, per catalog update):**
1. Parse CSV — each row becomes one text chunk: `"name: X; price: Y; status: Z"`
2. Append a script-folded copy of the chunk for cross-script retrieval boost.
3. Encode with `multilingual-e5-small` (prefix `"passage: "`).
4. Store in sqlite-vec under the shop's partition key.

**Query (per customer message):**
1. Encode query with prefix `"query: "`.
2. KNN search in sqlite-vec (`k=4` by default, cosine distance).
3. If no results or top result distance > `ESCALATE_DISTANCE` (0.6), escalate immediately.
4. Concatenate top-k content as `CATALOG` context.
5. Call LLM with system prompt + context + customer message.
6. If LLM returns `[ESCALATE]`, escalate; otherwise send the reply.

**Tuning knobs** (in `.env`):
- `TOP_K` — number of catalog chunks to retrieve (default 4)
- `ESCALATE_DISTANCE` — cosine distance threshold (0.0 = perfect match, 1.0 = unrelated); lower = more escalations

---

## 7. Escalation Logic

A conversation escalates to the human manager when **any** of:
- Customer message contains an escalation keyword: `operator`, `оператор`, `menejer`, `менеджер`, `shikoyat`, `жалоба`, `vozvrat`, `возврат`, `refund`, etc. (see `config.ESCALATE_KEYWORDS`).
- RAG retrieval finds nothing relevant (empty results or distance > threshold).
- LLM explicitly returns `[ESCALATE]` (instructed to do so for complaints, refunds, sensitive topics).

On escalation:
1. `store.set_escalated(shop_id, user_id)` — marks thread as human-owned.
2. Customer receives a holding message ("manager will respond shortly").
3. Manager's Telegram receives a notification with the customer's name, username, Telegram ID, and the original message, plus the `/reply` command pre-filled.
4. Bot stays silent for that user until manager runs `/resume <user_id>`.

---

## 8. Script Normalization

Uzbek is written in both Latin and Cyrillic scripts interchangeably, and customers mix Russian terms into Uzbek sentences. `bot/translit.py` handles this:

- `fold(text)` → lowercase + Cyrillic-to-Latin + strip apostrophe variants → canonical key.
- `"o'lcham"`, `"olcham"`, `"ўлчам"`, `"Ўлчам"` all fold to `"olcham"`.
- `"dostavka"`, `"доставка"` both fold to `"dostavka"`.
- Used in: keyword escalation matching (`handlers.py`) and ingest chunk augmentation (`ingest.py`).
- The LLM handles the generation side: system prompt instructs it to mirror the customer's exact language and script.

---

## 9. Production Deployment

### Prerequisites
- A Linux server (Oracle Cloud Always-Free ARM or Hetzner)
- Python 3.11+
- Your Gemini API key (get from [aistudio.google.com](https://aistudio.google.com))
- A Telegram bot token per shop (create via [@BotFather](https://t.me/BotFather))
- Manager chat IDs (get from [@userinfobot](https://t.me/userinfobot))

---

### Step 1 — Provision a server

**Oracle Cloud (free):**
1. Sign up at cloud.oracle.com (requires a credit card for verification, but the Always-Free tier is genuinely free).
2. Create a VM: **Ampere A1 Flex**, 4 OCPUs, 24 GB RAM, Ubuntu 22.04.
3. Open ports in the Security List: allow inbound TCP 22 (SSH). No other ports needed — the bot uses outbound polling.
4. SSH in: `ssh ubuntu@<your-ip>`

**Hetzner (simpler, €4/mo):**
```bash
# In Hetzner Cloud Console: create CAX11 (ARM, 2 core, 4 GB), Ubuntu 24.04
ssh root@<your-ip>
```

---

### Step 2 — Server setup

```bash
# Update and install Python + git
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip git

# Create a non-root user (skip on Oracle Cloud, ubuntu user already exists)
# sudo useradd -m -s /bin/bash supbot && su - supbot

# Clone the repo
git clone https://github.com/YOUR_USERNAME/SupBot.git
cd SupBot

# Create and activate venv
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

> **First run note:** `sentence-transformers` will download `multilingual-e5-small` (~120 MB) on first import. This only happens once and is cached in `~/.cache/huggingface/`.

---

### Step 3 — Configure

```bash
# Copy and edit environment config
cp .env.example .env
nano .env
# Set LLM_API_KEY to your Gemini key

# Copy and edit shop registry
cp shops.example.json shops.json
nano shops.json
# Add your shop(s): shop_id, name, bot_token, manager_chat_id
```

`shops.json` example:
```json
[
  {
    "shop_id": "acme_shoes",
    "name": "Acme Shoes",
    "bot_token": "123456789:AAF...",
    "manager_chat_id": 987654321
  }
]
```

Create the data directory:
```bash
mkdir -p data
```

---

### Step 4 — Ingest catalog

```bash
# Use the sample or replace with your real catalog CSV
python -m bot.ingest --shop acme_shoes --file data/sample_catalog.csv --replace

# For a real shop, create your CSV with columns like:
# type,name,details,price,status
# Then ingest it:
python -m bot.ingest --shop acme_shoes --file data/my_catalog.csv --replace
```

Catalog CSV format (any columns work — each row becomes one searchable chunk):
```csv
type,name,details,price,status
product,Nike Air Max,o'lcham 36-44; yengil,450000 so'm,bor
faq,Yetkazib berish,Toshkent bo'yicha 1 kun,30000 so'm,
faq,Qaytarish,7 kun ichida qaytarish mumkin,,
```

---

### Step 5 — Test run

```bash
# Activate venv if not active
source .venv/bin/activate

# Run the bot
python main.py
```

Send your bot a message on Telegram. Try:
- `narx qancha?` — should answer with pricing
- `размер есть?` — should answer about sizes  
- `operator bilan gaplashmoqchiman` — should escalate to manager

---

### Step 6 — Run as a systemd service (keeps running after reboot)

Create the service file:
```bash
sudo nano /etc/systemd/system/supbot.service
```

Paste (adjust paths if your user is not `ubuntu`):
```ini
[Unit]
Description=SupBot Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/SupBot
ExecStart=/home/ubuntu/SupBot/.venv/bin/python main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable supbot
sudo systemctl start supbot

# Verify it's running
sudo systemctl status supbot

# Watch live logs
sudo journalctl -u supbot -f
```

---

### Step 7 — Updating the bot

```bash
cd /home/ubuntu/SupBot
git pull
source .venv/bin/activate
pip install -r requirements.txt          # if requirements changed
sudo systemctl restart supbot
sudo systemctl status supbot
```

To update a catalog without restarting the bot:
```bash
python -m bot.ingest --shop acme_shoes --file data/new_catalog.csv --replace
# No restart needed — store reads on every query
```

---

### Adding a new shop (after launch)

1. Client creates bot via [@BotFather](https://t.me/BotFather), sends you the token.
2. Client sends you their manager's Telegram ID (from [@userinfobot](https://t.me/userinfobot)).
3. Add entry to `shops.json` on the server.
4. Ingest their catalog CSV.
5. `sudo systemctl restart supbot`

---

## 10. Monitoring & Operations

### View logs
```bash
# Live
sudo journalctl -u supbot -f

# Last 100 lines
sudo journalctl -u supbot -n 100

# Errors only
sudo journalctl -u supbot -p err
```

### Check the database
```bash
source .venv/bin/activate
python3 -c "
import sqlite3, sqlite_vec
db = sqlite3.connect('data/bot.db')
db.enable_load_extension(True); sqlite_vec.load(db); db.enable_load_extension(False)
print('shops in catalog:', db.execute('SELECT shop_id, count(*) FROM catalog GROUP BY shop_id').fetchall())
print('active escalations:', db.execute('SELECT * FROM escalations').fetchall())
"
```

### Disk usage
The DB will grow very slowly — catalog chunks are small text. For dozens of shops it stays under 100 MB. SQLite handles it fine.

### Cost tracking
Log LLM calls if you want precise per-shop cost attribution. Currently all shops share one Gemini API key; you can track usage in [Google AI Studio](https://aistudio.google.com) under "Usage".

---

## 11. Scaling Path

The current architecture handles **dozens of shops on one process** without issue. Future scaling levers (in order of when you'd need them):

1. **More shops, same server:** Just add to `shops.json`. aiogram's polling is lightweight; 50 shops with moderate traffic is fine on the Always-Free instance.

2. **Conversation history:** Currently each message is stateless. Add a `conversations` table to store the last N turns per `(shop_id, user_id)` and pass them as extra context to the LLM.

3. **Web admin UI:** A simple FastAPI app (same server) where shops can upload CSVs themselves. Calls `store.clear_shop()` + `store.add_items()` via the same store module.

4. **Webhook mode instead of polling:** Replace `dp.start_polling()` with `dp.start_webhook()` + a reverse proxy (nginx or Caddy). Required if Telegram ever throttles your polling, and slightly more efficient.

5. **Multiple processes / workers:** When shops number in the hundreds, split `shops.json` into shards and run one process per shard under systemd.

6. **Managed Postgres:** Only needed if SQLite WAL mode becomes a bottleneck under concurrent writes. Very unlikely at this scale.

---

## 12. Monetization

**Subscription tiers (suggested):**
| Tier | Price (UZS/mo) | Price (USD) | Suitable for |
|---|---|---|---|
| Starter | 200,000 | ~$16 | 1 shop, up to 500 messages/day |
| Standard | 350,000 | ~$28 | 1 shop, up to 2,000 messages/day |
| Pro | 500,000 | ~$40 | 1 shop, unlimited |

**Payment rails:** Payme and Click both have merchant APIs for recurring billing. For the first few customers, skip the integration — invoice manually or accept a bank transfer. Build billing automation only once you have 3+ paying shops.

**Onboarding funnel:**
1. Find a friendly first shop (ideally yours or a contact).
2. Run for 2–4 weeks, gather real customer messages, grade quality.
3. Once answers are demonstrably good, approach 5–10 more shops directly.
4. Only then invest in self-serve onboarding and payment automation.

---

## 13. Known Gaps & Next Steps

| Gap | Priority | Notes |
|---|---|---|
| **Answer quality evaluation** | Critical | Build a test set of 50–100 real messy customer messages; grade outputs. This is the real quality gate. |
| Conversation history | High | Without it, follow-up questions ("and what about size 38?") lose context. |
| Web catalog upload | Medium | Currently only CLI ingest. A simple upload endpoint removes the need to SSH for catalog updates. |
| Payme/Click billing | Medium | Do manually first; automate at 5+ shops. |
| Per-shop LLM cost tracking | Medium | Add a `messages` table with `(shop_id, timestamp, tokens_in, tokens_out)`. |
| Webhook mode | Low | Polling works fine; switch if you hit Telegram rate limits. |
| Rate limiting per user | Low | Prevent a single customer from spamming hundreds of LLM calls. |
| `/stats` manager command | Low | "You had 47 questions today, 8 escalated." Useful for retention. |
