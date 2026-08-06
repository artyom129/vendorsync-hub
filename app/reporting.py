from __future__ import annotations

import csv
from io import BytesIO, StringIO
from typing import Any

import xlsxwriter


FIELDS = [
    "sku",
    "name",
    "description",
    "category",
    "currency",
    "price",
    "stock",
    "supplier_name",
    "offer_count",
    "alternative_suppliers",
    "has_conflict",
]


def _safe_spreadsheet_value(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def to_csv(rows: list[dict[str, Any]]) -> bytes:
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {field: _safe_spreadsheet_value(row.get(field, "")) for field in FIELDS}
        )
    return output.getvalue().encode("utf-8-sig")


def to_xlsx(catalog, conflicts, versions, changes, imports):
    stream = BytesIO()
    workbook = xlsxwriter.Workbook(
        stream,
        {
            "in_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        },
    )
    header = workbook.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#172033",
            "border": 1,
        }
    )
    warning = workbook.add_format(
        {"bg_color": "#FFF3DC", "font_color": "#9A5700"}
    )
    bad = workbook.add_format(
        {"bg_color": "#FDE8E7", "font_color": "#B42318"}
    )
    wrap = workbook.add_format({"text_wrap": True, "valign": "top"})

    def add_sheet(name, rows, fields=None):
        worksheet = workbook.add_worksheet(name)
        if fields is None:
            fields = list(rows[0].keys()) if rows else []
        for column, field in enumerate(fields):
            worksheet.write(0, column, field.replace("_", " ").title(), header)
        for row_index, row in enumerate(rows, 1):
            for column, field in enumerate(fields):
                value = _safe_spreadsheet_value(row.get(field, ""))
                cell_format = wrap if field in (
                    "description",
                    "message",
                    "alternative_suppliers",
                ) else None
                worksheet.write(row_index, column, value, cell_format)
        if fields:
            worksheet.freeze_panes(1, 0)
            worksheet.autofilter(0, 0, max(len(rows), 1), len(fields) - 1)
            worksheet.set_column(0, len(fields) - 1, 18)
        return worksheet

    catalog_sheet = add_sheet("Unified Catalog", catalog, FIELDS)
    for row_index, row in enumerate(catalog, 1):
        if row.get("has_conflict"):
            catalog_sheet.set_row(row_index, None, warning)

    conflict_fields = [
        "sku",
        "offer_count",
        "winner_supplier",
        "winner_price",
        "min_price",
        "max_price",
        "price_difference_percent",
        "currency_mismatch",
        "large_price_difference",
    ]
    conflict_sheet = add_sheet("Conflicts", conflicts, conflict_fields)
    for row_index, row in enumerate(conflicts, 1):
        if row.get("large_price_difference"):
            conflict_sheet.set_row(row_index, None, bad)

    add_sheet("Versions", versions)
    add_sheet("Changes", changes)
    add_sheet("Import Audit", imports)

    summary = workbook.add_worksheet("Summary")
    summary.write(
        "A1",
        "VendorSync Hub Report",
        workbook.add_format({"bold": True, "font_size": 18}),
    )
    metrics = [
        ("Unified products", len(catalog)),
        ("Conflicts", len(conflicts)),
        ("Versions", len(versions)),
        ("Changes", len(changes)),
        ("Imports", len(imports)),
    ]
    for index, (label, value) in enumerate(metrics, 3):
        summary.write(index, 0, label, header)
        summary.write(index, 1, value)
    summary.set_column("A:A", 28)
    summary.set_column("B:B", 18)
    workbook.close()
    return stream.getvalue()
