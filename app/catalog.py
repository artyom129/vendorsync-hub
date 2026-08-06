from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any


TRACKED_FIELDS = ("name", "description", "currency", "price", "stock", "category")


def compare(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    old = {str(item["sku"]).casefold(): item for item in previous}
    new = {str(item["sku"]).casefold(): item for item in current}
    summary = {
        "new_items": 0,
        "changed_items": 0,
        "unchanged_items": 0,
        "removed_items": 0,
    }
    changes: list[dict[str, Any]] = []

    for key, item in new.items():
        sku = str(item["sku"])
        old_item = old.get(key)
        if old_item is None:
            summary["new_items"] += 1
            changes.append(
                {
                    "sku": sku,
                    "change_type": "new",
                    "field_name": None,
                    "old_value": None,
                    "new_value": item["name"],
                }
            )
            continue

        changed = False
        for field in TRACKED_FIELDS:
            old_value = str(old_item.get(field, ""))
            new_value = str(item.get(field, ""))
            if old_value != new_value:
                changed = True
                changes.append(
                    {
                        "sku": sku,
                        "change_type": "changed",
                        "field_name": field,
                        "old_value": old_value,
                        "new_value": new_value,
                    }
                )
        summary["changed_items" if changed else "unchanged_items"] += 1

    for key, item in old.items():
        if key not in new:
            summary["removed_items"] += 1
            changes.append(
                {
                    "sku": item["sku"],
                    "change_type": "removed",
                    "field_name": None,
                    "old_value": item["name"],
                    "new_value": None,
                }
            )

    return summary, changes


def unified(
    rows: list[dict[str, Any]],
    threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["sku"]).casefold()].append(row)

    catalog: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for offers in grouped.values():
        offers.sort(
            key=lambda item: (
                int(item.get("supplier_priority", 9999)),
                Decimal(str(item["price"])),
                str(item.get("supplier_name", "")),
            )
        )
        winner = dict(offers[0])
        winner["offer_count"] = len(offers)
        winner["has_conflict"] = len(offers) > 1
        winner["alternative_suppliers"] = ", ".join(
            str(item["supplier_name"]) for item in offers[1:]
        )
        catalog.append(winner)

        if len(offers) > 1:
            prices = [Decimal(str(item["price"])) for item in offers]
            currencies = {str(item["currency"]).upper() for item in offers}
            minimum = min(prices)
            maximum = max(prices)
            if minimum == 0:
                difference_percent = 0.0 if maximum == 0 else 100.0
            else:
                difference_percent = float((maximum - minimum) / minimum * 100)
            currency_mismatch = len(currencies) > 1
            conflicts.append(
                {
                    "sku": winner["sku"],
                    "offer_count": len(offers),
                    "winner_supplier": winner["supplier_name"],
                    "winner_price": str(winner["price"]),
                    "min_price": str(minimum),
                    "max_price": str(maximum),
                    "price_difference_percent": round(difference_percent, 2),
                    "currency_mismatch": currency_mismatch,
                    "large_price_difference": (
                        currency_mismatch or difference_percent >= threshold
                    ),
                    "offers": offers,
                }
            )

    catalog.sort(key=lambda item: str(item["sku"]).casefold())
    conflicts.sort(
        key=lambda item: (
            not item["large_price_difference"],
            -item["price_difference_percent"],
            str(item["sku"]).casefold(),
        )
    )
    return catalog, conflicts
