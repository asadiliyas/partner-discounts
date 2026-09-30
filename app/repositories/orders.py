"""All database access for the ``orders`` collection."""

from pymongo import ReturnDocument

from app.db import ORDERS, get_db


def _to_order_dict(doc: dict) -> dict:
    doc = dict(doc)
    doc["order_id"] = doc.pop("_id")
    return doc


async def get(order_id: str) -> dict | None:
    doc = await get_db()[ORDERS].find_one({"_id": order_id})
    return _to_order_dict(doc) if doc else None


async def list_recent(limit: int = 50) -> list[dict]:
    cursor = get_db()[ORDERS].find({}).sort("created_at", -1).limit(limit)
    return [_to_order_dict(doc) async for doc in cursor]


async def set_discount_if_none(
    order_id: str, expected_subtotal_paise: int, discount: dict, total_paise: int
) -> dict | None:
    """Attach a discount only if the order is pending and has none yet.

    One conditional update, so two codes racing for the same order cannot
    both win. Returns the updated order, or None if the order was not
    eligible (missing, not pending, already discounted, or its subtotal
    changed since it was read).
    """
    doc = await get_db()[ORDERS].find_one_and_update(
        {
            "_id": order_id,
            "status": "pending",
            "discount": None,
            "subtotal_paise": expected_subtotal_paise,
        },
        {"$set": {"discount": discount, "total_paise": total_paise}},
        return_document=ReturnDocument.AFTER,
    )
    return _to_order_dict(doc) if doc else None


async def update_if_pending(order_id: str, expected_total_paise: int, fields: dict) -> bool:
    """Set ``fields`` only if the order is still pending at the total we saw.

    Returns False if something changed in between (another delivery paid it,
    or a discount re-priced it); the caller re-reads and decides again.
    """
    result = await get_db()[ORDERS].update_one(
        {"_id": order_id, "status": "pending", "total_paise": expected_total_paise},
        {"$set": fields},
    )
    return result.modified_count == 1
