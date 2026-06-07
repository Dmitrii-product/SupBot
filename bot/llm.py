"""LLM generation via the OpenAI-compatible Chat Completions API.

Defaults to Gemini. To swap providers, change only LLM_BASE_URL / LLM_MODEL in
.env (e.g. DeepSeek's https://api.deepseek.com + deepseek-chat) — no code change.
"""
from functools import lru_cache

from bot import config

SYSTEM_PROMPT = """You are a customer-support assistant for an online shop.

Rules:
- Answer ONLY using the CATALOG provided below. Never invent prices, sizes, \
availability, or policies.
- Reply in the SAME language and script the customer used. Customers mix Uzbek \
(Latin and Cyrillic) and Russian freely — mirror them naturally.
- Keep replies short, friendly, and concrete.
- If the question is clearly unrelated to shopping, products, or commerce \
(e.g. weather, math, general trivia, politics), reply with EXACTLY the token \
[OFFTOPIC] and nothing else.
- If the answer is not clearly in the CATALOG, or the customer wants a human, \
a complaint, a refund, or anything sensitive, reply with EXACTLY the token \
[ESCALATE] and nothing else.
"""

_CLASSIFY_PROMPT = """You classify messages sent to a shop's customer-support bot.

Reply with exactly one word — no punctuation, no explanation:
- OFFTOPIC  if the message is clearly unrelated to any shop (e.g. weather, maths, \
politics, general knowledge, greetings to an AI, etc.)
- SHOP      if the message could be about products, prices, orders, delivery, \
availability, returns, or anything commerce-related — even vaguely

When in doubt, choose SHOP."""


@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI
    return OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)


def classify_offtopic(user_text: str) -> bool:
    """Return True if the message is clearly unrelated to any shop."""
    resp = _client().chat.completions.create(
        model=config.LLM_MODEL,
        temperature=0,
        max_tokens=10,
        messages=[
            {"role": "system", "content": _CLASSIFY_PROMPT},
            {"role": "user", "content": user_text},
        ],
    )
    result = (resp.choices[0].message.content or "").strip().upper()
    return result.startswith("OFFTOPIC")


def generate(user_text: str, context: str) -> str:
    resp = _client().chat.completions.create(
        model=config.LLM_MODEL,
        temperature=0.3,
        max_tokens=400,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"CATALOG:\n{context}\n\nCUSTOMER: {user_text}"},
        ],
    )
    return (resp.choices[0].message.content or "").strip()
