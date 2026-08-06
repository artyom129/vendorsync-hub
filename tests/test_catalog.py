from app.catalog import compare, unified


def test_compare_versions_case_insensitive():
    old = [
        {"sku": "A", "name": "One", "description": "", "currency": "USD", "price": "10", "stock": 2, "category": "X"},
        {"sku": "B", "name": "Two", "description": "", "currency": "USD", "price": "20", "stock": 1, "category": "X"},
    ]
    new = [
        {"sku": "a", "name": "One", "description": "", "currency": "USD", "price": "12", "stock": 2, "category": "X"},
        {"sku": "C", "name": "Three", "description": "", "currency": "USD", "price": "30", "stock": 1, "category": "Y"},
    ]
    summary, changes = compare(old, new)
    assert summary == {"new_items": 1, "changed_items": 1, "unchanged_items": 0, "removed_items": 1}
    assert {"new", "changed", "removed"} <= {item["change_type"] for item in changes}


def test_unified_priority_and_conflict():
    rows = [
        {"sku": "A", "name": "One", "supplier_name": "Alpha", "supplier_priority": 10, "currency": "USD", "price": "20", "stock": 1},
        {"sku": "a", "name": "One", "supplier_name": "North", "supplier_priority": 20, "currency": "USD", "price": "10", "stock": 2},
    ]
    catalog, conflicts = unified(rows, 20)
    assert catalog[0]["supplier_name"] == "Alpha"
    assert catalog[0]["offer_count"] == 2
    assert conflicts[0]["large_price_difference"] is True


def test_zero_price_conflict_is_not_hidden():
    rows = [
        {"sku": "A", "name": "One", "supplier_name": "Alpha", "supplier_priority": 10, "currency": "USD", "price": "0", "stock": 1},
        {"sku": "A", "name": "One", "supplier_name": "North", "supplier_priority": 20, "currency": "USD", "price": "10", "stock": 2},
    ]
    _, conflicts = unified(rows, 20)
    assert conflicts[0]["price_difference_percent"] == 100.0
    assert conflicts[0]["large_price_difference"] is True


def test_currency_mismatch_is_large_conflict():
    rows = [
        {"sku": "A", "name": "One", "supplier_name": "Alpha", "supplier_priority": 10, "currency": "USD", "price": "10", "stock": 1},
        {"sku": "A", "name": "One", "supplier_name": "North", "supplier_priority": 20, "currency": "EUR", "price": "10", "stock": 2},
    ]
    _, conflicts = unified(rows, 20)
    assert conflicts[0]["currency_mismatch"] is True
    assert conflicts[0]["large_price_difference"] is True
