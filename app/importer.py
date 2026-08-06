from __future__ import annotations

import hashlib
import json
import re
import shutil
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import database as db
from .adapters import load_feed, nested_get
from .catalog import compare
from .config import Settings, Supplier


class PermanentImportError(ValueError):
    """A feed error that should not be retried automatically."""


class Importer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def supplier(self, supplier_id: str) -> Supplier:
        for supplier in self.settings.suppliers:
            if supplier.id == supplier_id:
                return supplier
        raise PermanentImportError(f"Unknown supplier: {supplier_id}")

    def detect(self, name: str) -> Supplier | None:
        for supplier in self.settings.suppliers:
            if re.match(supplier.filename_pattern, name):
                return supplier
        return None

    @staticmethod
    def fingerprint(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _unique_destination(folder: Path, filename: str) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / filename
        if target.exists():
            target = folder / f"{target.stem}_{uuid4().hex[:8]}{target.suffix}"
        return target

    def move(self, path: Path, folder: Path) -> Path:
        target = self._unique_destination(folder, path.name)
        shutil.move(str(path), str(target))
        return target

    def normalize(
        self,
        records: list[dict[str, Any]],
        supplier: Supplier,
        filename: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        products: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        seen: set[str] = set()

        for row_number, record in enumerate(records, start=2):
            values = {
                target: nested_get(record, source)
                for target, source in supplier.mapping.items()
            }
            sku = str(values.get("sku") or "").strip().upper()
            name = str(values.get("name") or "").strip()
            description = str(values.get("description") or "").strip()
            category = str(values.get("category") or "").strip()
            currency = str(values.get("currency") or supplier.currency).strip().upper()
            row_issues: list[dict[str, Any]] = []

            if not sku:
                row_issues.append(
                    {
                        "row_number": row_number,
                        "field_name": "sku",
                        "code": "required",
                        "message": "SKU is required",
                    }
                )
            if not name:
                row_issues.append(
                    {
                        "row_number": row_number,
                        "field_name": "name",
                        "code": "required",
                        "message": "Name is required",
                    }
                )

            price_text = str(values.get("price") or "").strip().replace(",", ".")
            try:
                price = Decimal(price_text)
                if not price.is_finite() or price < 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                price = Decimal("0")
                row_issues.append(
                    {
                        "row_number": row_number,
                        "field_name": "price",
                        "code": "invalid_price",
                        "message": "Price must be a non-negative decimal",
                        "raw_value": str(values.get("price")),
                    }
                )

            stock_text = str(values.get("stock") or "").strip()
            try:
                stock_decimal = Decimal(stock_text)
                if (
                    not stock_decimal.is_finite()
                    or stock_decimal < 0
                    or stock_decimal != stock_decimal.to_integral_value()
                ):
                    raise InvalidOperation
                stock = int(stock_decimal)
            except (InvalidOperation, ValueError, OverflowError):
                stock = 0
                row_issues.append(
                    {
                        "row_number": row_number,
                        "field_name": "stock",
                        "code": "invalid_stock",
                        "message": "Stock must be a non-negative integer",
                        "raw_value": str(values.get("stock")),
                    }
                )

            if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
                row_issues.append(
                    {
                        "row_number": row_number,
                        "field_name": "currency",
                        "code": "invalid_currency",
                        "message": "Currency must be a three-letter code",
                        "raw_value": currency,
                    }
                )

            sku_key = sku.casefold()
            if sku and sku_key in seen:
                row_issues.append(
                    {
                        "row_number": row_number,
                        "field_name": "sku",
                        "code": "duplicate_sku",
                        "message": f"Duplicate SKU: {sku}",
                        "raw_value": sku,
                    }
                )

            if row_issues:
                issues.extend(row_issues)
                continue

            seen.add(sku_key)
            canonical = {
                "supplier_id": supplier.id,
                "sku": sku,
                "name": name,
                "description": description,
                "currency": currency,
                "price": format(price, "f"),
                "stock": stock,
                "category": category,
                "source_file": filename,
                "source_row": row_number,
            }
            hash_payload = {
                key: canonical[key]
                for key in (
                    "sku",
                    "name",
                    "description",
                    "currency",
                    "price",
                    "stock",
                    "category",
                )
            }
            canonical["record_hash"] = hashlib.sha256(
                json.dumps(
                    hash_payload,
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            products.append(canonical)

        return products, issues

    def _reject(
        self,
        *,
        path: Path,
        job_id: int,
        import_id: int,
        code: str,
        message: str,
        total_rows: int = 0,
        valid_rows: int = 0,
        invalid_rows: int = 0,
        issues: list[dict[str, Any]] | None = None,
    ) -> None:
        target = self.move(path, self.settings.rejected_dir)
        db.update_job_payload(
            self.settings.database_path,
            job_id,
            json.dumps({"path": str(target)}),
        )
        db.add_issues(
            self.settings.database_path,
            import_id,
            issues
            or [
                {
                    "row_number": None,
                    "field_name": None,
                    "code": code,
                    "message": message,
                    "raw_value": None,
                }
            ],
        )
        db.finish_import(
            self.settings.database_path,
            import_id,
            "rejected",
            total_rows,
            valid_rows,
            invalid_rows,
            str(target),
            message,
        )
        db.audit(
            self.settings.database_path,
            "feed_rejected",
            "error",
            f"{path.name}: {message}",
        )
        raise PermanentImportError(message)

    def process(
        self,
        job_id: int,
        path: Path,
        supplier_id: str | None,
    ) -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Feed file no longer exists: {path}")

        supplier = self.supplier(supplier_id) if supplier_id else self.detect(path.name)
        fingerprint = self.fingerprint(path)
        import_id = db.create_import(
            self.settings.database_path,
            job_id,
            supplier.id if supplier else None,
            path.name,
            fingerprint,
        )

        if path.stat().st_size > self.settings.max_file_size_mb * 1024 * 1024:
            self._reject(
                path=path,
                job_id=job_id,
                import_id=import_id,
                code="file_too_large",
                message=f"File exceeds the {self.settings.max_file_size_mb} MB limit",
            )

        if supplier is None:
            self._reject(
                path=path,
                job_id=job_id,
                import_id=import_id,
                code="unknown_supplier",
                message="No supplier matches this filename",
            )

        if db.fingerprint_exists(self.settings.database_path, fingerprint):
            target = self.move(path, self.settings.rejected_dir)
            db.update_job_payload(
                self.settings.database_path,
                job_id,
                json.dumps({"path": str(target)}),
            )
            message = "Exact duplicate feed already processed"
            db.add_issues(
                self.settings.database_path,
                import_id,
                [
                    {
                        "row_number": None,
                        "field_name": None,
                        "code": "duplicate_file",
                        "message": message,
                        "raw_value": fingerprint,
                    }
                ],
            )
            db.finish_import(
                self.settings.database_path,
                import_id,
                "duplicate",
                0,
                0,
                0,
                str(target),
                message,
            )
            db.audit(
                self.settings.database_path,
                "feed_duplicate",
                "warning",
                f"{path.name} rejected as duplicate",
            )
            return {"status": "duplicate", "version_id": None, "message": message}

        try:
            records = load_feed(path, supplier.format)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            self._reject(
                path=path,
                job_id=job_id,
                import_id=import_id,
                code="parse_error",
                message=f"Feed parsing failed: {exc}",
            )
            raise AssertionError("unreachable")

        if not records:
            self._reject(
                path=path,
                job_id=job_id,
                import_id=import_id,
                code="empty_feed",
                message="Feed contains no product rows",
            )

        products, issues = self.normalize(records, supplier, path.name)
        if issues:
            invalid_rows = len(
                {
                    issue.get("row_number")
                    for issue in issues
                    if issue.get("row_number") is not None
                }
            )
            self._reject(
                path=path,
                job_id=job_id,
                import_id=import_id,
                code="validation_error",
                message=f"{len(issues)} validation issue(s)",
                total_rows=len(records),
                valid_rows=len(products),
                invalid_rows=invalid_rows,
                issues=issues,
            )

        latest = db.latest_version(self.settings.database_path, supplier.id)
        previous = (
            db.version_rows(self.settings.database_path, latest["id"])
            if latest
            else []
        )
        summary, changes = compare(previous, products)
        number = db.next_version(self.settings.database_path, supplier.id)
        target = self._unique_destination(self.settings.archive_dir, path.name)
        shutil.copy2(path, target)
        try:
            version_id = db.save_catalog_version(
                self.settings.database_path,
                supplier_id=supplier.id,
                import_id=import_id,
                number=number,
                products=products,
                summary=summary,
                changes=changes,
            )
        except Exception:
            target.unlink(missing_ok=True)
            raise
        path.unlink()
        message = f"Catalog version {number} created"
        db.finish_import(
            self.settings.database_path,
            import_id,
            "accepted",
            len(records),
            len(products),
            0,
            str(target),
            message,
        )
        db.audit(
            self.settings.database_path,
            "feed_accepted",
            "info",
            f"{supplier.name} version {number}: {len(products)} products",
        )
        return {
            "status": "accepted",
            "version_id": version_id,
            "summary": summary,
            "message": message,
        }
