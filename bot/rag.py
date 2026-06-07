"""Ties retrieval + generation together and decides when to hand off to a human."""
from bot import config, embeddings, llm, store

ESCALATE = "__ESCALATE__"
OFFTOPIC = "__OFFTOPIC__"


def answer(shop_id: str, user_text: str) -> str:
    """Return a reply string, ESCALATE if a human should take over, or OFFTOPIC
    if the message is clearly unrelated to the shop.
    """
    q_vec = embeddings.embed_query(user_text)
    hits = store.search(shop_id, q_vec, k=config.TOP_K)

    if not hits or hits[0][1] > config.ESCALATE_DISTANCE:
        if llm.classify_offtopic(user_text):
            return OFFTOPIC
        return ESCALATE

    context = "\n\n".join(content for content, _ in hits)
    reply = llm.generate(user_text, context)

    if not reply or "[ESCALATE]" in reply:
        return ESCALATE
    if "[OFFTOPIC]" in reply:
        return OFFTOPIC
    return reply
