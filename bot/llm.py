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
- If the answer is not clearly in the CATALOG, or the customer wants a human, \
a complaint, a refund, or anything sensitive, reply with EXACTLY the token \
[ESCALATE] and nothing else.
"""


@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI
    return OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)


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
