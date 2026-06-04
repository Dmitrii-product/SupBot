# Telegram Shop Bot (MVP)

A multi-tenant Telegram support bot for small shops. It answers routine
questions (price, size, availability, delivery) in mixed Uzbek/Russian using
local retrieval + a cheap LLM, and hands off to a human when it isn't sure.

- **Bot:** aiogram 3.x — one process runs every shop's bot.
- **Retrieval:** sqlite-vec (embedded, no separate vector DB) + local
  multilingual embeddings.
- **Generation:** any OpenAI-compatible LLM (Gemini by default; one env change
  switches to DeepSeek).
- **Script handling:** Latin/Cyrillic Uzbek folding so `o'lcham`, `olcham`, and
  `ўлчам` all match.

## How it fits together
```
customer ─▶ Telegram ─▶ handlers ─▶ rag.answer ─▶ embeddings ─▶ sqlite-vec
                                          │
                                          └▶ llm (Gemini) ─▶ reply
                                          │
                          low confidence / "operator" / refund
                                          ▼
                                manager's Telegram  ◀── /reply, /resume
```

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # add your Gemini API key
cp shops.example.json shops.json
```

**Add a shop** to `shops.json`:
- Create a bot with **@BotFather** → copy the token into `bot_token`.
- Get the manager's numeric chat id from **@userinfobot** → `manager_chat_id`.

**Ingest a catalog** (any CSV works — columns become searchable text):
```bash
python -m bot.ingest --shop demo_shop --file data/sample_catalog.csv --replace
```

**Run:**
```bash
python main.py
```

Message the bot as a customer. Try `narx qancha?`, `размер есть?`,
`operator bilan gaplashmoqchiman`. The last one (and anything the bot can't
answer) is forwarded to the manager, who replies with `/reply <user_id> ...`
and re-enables the bot with `/resume <user_id>`.

## Cheap hosting
A single Oracle Cloud **Always-Free** ARM instance (4 cores / 24 GB) runs this
for free and comfortably handles many shops. Hetzner (~€4/mo) is a simpler
alternative. Run `python main.py` under `systemd` or `tmux` so it restarts.

## Cost notes
- Embeddings run locally → free.
- LLM is pay-per-message: roughly $2–8 per active shop/month at typical volume.
- Use a more capable model (e.g. `gemini-2.5-flash`) if Uzbek quality matters;
  drop to a Flash-Lite tier only if cost ever becomes a concern.

## Sensible next steps
- Add conversation history (currently each message is answered statelessly).
- Web admin for shops to upload catalogs themselves.
- Payme/Click billing + a subscription/trial check before answering.
- A test set of real messy customer messages to grade answer quality — this is
  the real quality gate, more than any infra choice.

> Note: this scaffold was written but not run against live Telegram/Gemini
> services. Expect to adjust model names and confirm your sqlite-vec version
> (>= 0.1.6, for `partition key` support) on first run.
