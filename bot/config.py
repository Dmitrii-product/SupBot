"""Central config: reads environment variables and the shop registry."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

EMBED_MODEL = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-small")

DB_PATH = str(ROOT / os.getenv("DB_PATH", "data/bot.db"))
TOP_K = int(os.getenv("TOP_K", "4"))
ESCALATE_DISTANCE = float(os.getenv("ESCALATE_DISTANCE", "0.6"))

# Phrases that always hand the conversation to a human, in UZ/RU + common slang.
ESCALATE_KEYWORDS = [
    "operator", "оператор", "odam", "human", "menejer", "менеджер",
    "shikoyat", "жалоба", "claim", "vozvrat", "возврат", "refund",
]


ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


def load_shops(path: str | None = None) -> list[dict]:
    """Load the shop registry. Each shop: shop_id, name, bot_token, manager_chat_id."""
    p = Path(path) if path else ROOT / "shops.json"
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found. Copy shops.example.json to shops.json and fill it in."
        )
    return json.loads(p.read_text(encoding="utf-8"))
