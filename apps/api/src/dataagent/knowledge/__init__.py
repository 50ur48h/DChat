"""Org knowledge: documents in, retrievable passages out (architecture 5.5).

The half of the agent's understanding that discovery cannot produce. The catalog
can find that `orders.total_amount` is numeric; only a document says that net
revenue excludes cancelled orders — and 5.5's division of labour rests on the
distinction: **RAG answers "what does this term mean here", the database answers
"what is the value"**.

Nothing in this package decides anything. It extracts, chunks, embeds, and finds
— and everything it returns is framed as reference material, because a customer's
document is text somebody else wrote and 7.4's threat model assumes it may be
hostile.
"""
