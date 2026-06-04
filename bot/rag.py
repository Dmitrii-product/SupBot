"""Ties retrieval + generation together and decides when to hand off to a human."""
from bot import config, embeddings, llm, store

ESCALATE = "__ESCALATE__"


def answer(shop_id: str, user_text: str) -> str:
    """Return a reply string, or the ESCALATE sentinel if a human should take over.

    Escalates when (a) nothing in the catalog is close enough, or (b) the model
    itself signals it can't/shouldn't answer.
    """
    q_vec = embeddings.embed_query(user_text)
    hits = store.search(shop_id, q_vec, k=config.TOP_K)

    if not hits or hits[0][1] > config.ESCALATE_DISTANCE:
        return ESCALATE

    context = "\n\n".join(content for content, _ in hits)
    reply = llm.generate(user_text, context)

    if not reply or "[ESCALATE]" in reply:
        return ESCALATE
    return reply
