"""All database access for the ``payments`` collection.

One document per gateway ``payment_id`` (used as ``_id``), so the unique
``_id`` index is what makes duplicate webhook deliveries harmless.
"""

from datetime import datetime

from pymongo.errors import DuplicateKeyError

from app.db import PAYMENTS, get_db


async def insert_if_new(doc: dict) -> tuple[dict, bool]:
    """Insert a payment; return ``(stored_doc, created)``.

    If the payment id already exists, returns the stored document untouched
    and ``created=False``.
    """
    collection = get_db()[PAYMENTS]
    try:
        await collection.insert_one(dict(doc))
        return doc, True
    except DuplicateKeyError:
        existing = await collection.find_one({"_id": doc["_id"]})
        return existing, False


async def set_outcome(payment_id: str, outcome: str, settled_at: datetime) -> None:
    await get_db()[PAYMENTS].update_one(
        {"_id": payment_id},
        {"$set": {"outcome": outcome, "settled_at": settled_at}},
    )


async def get(payment_id: str) -> dict | None:
    return await get_db()[PAYMENTS].find_one({"_id": payment_id})
