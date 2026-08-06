from io import BytesIO
from zipfile import ZipFile

from app.reporting import to_csv, to_xlsx


def test_csv_neutralizes_spreadsheet_formulas():
    rows = [{"sku": "=1+1", "name": "@danger", "price": "10"}]
    content = to_csv(rows).decode("utf-8-sig")
    assert "'=1+1" in content
    assert "'@danger" in content


def test_xlsx_disables_formula_interpretation():
    catalog = [{
        "sku": "=1+1",
        "name": "Product",
        "description": "",
        "category": "",
        "currency": "USD",
        "price": "10",
        "stock": 1,
        "supplier_name": "Alpha",
        "offer_count": 1,
        "alternative_suppliers": "",
        "has_conflict": False,
    }]
    content = to_xlsx(catalog, [], [], [], [])
    with ZipFile(BytesIO(content)) as archive:
        workbook_xml = archive.read("xl/workbook.xml")
        assert b"Unified Catalog" in workbook_xml
        worksheet_xml = archive.read("xl/worksheets/sheet1.xml")
        assert b"<f>" not in worksheet_xml
