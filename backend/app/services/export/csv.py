"""Export/import prices as CSV files."""

from __future__ import annotations

import csv
import io

HEADERS = ["Territory Code", "Territory Name", "Currency", "Customer Price", "Proceeds"]


class CSVExportService:
    """Export/import prices as CSV files."""

    @staticmethod
    def export_prices(
        subscription_name: str,
        prices: list[dict],
    ) -> bytes:
        """Create a CSV file with price data.

        Args:
            subscription_name: Unused for CSV but kept for interface parity
                               with :class:`ExcelExportService`.
            prices: List of dicts with keys: territory_code, territory_name,
                    currency_code, customer_price, proceeds.

        Returns:
            The CSV content encoded as UTF-8 bytes.
        """
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(HEADERS)

        sorted_prices = sorted(prices, key=lambda p: p.get("territory_code", ""))

        for price in sorted_prices:
            writer.writerow([
                price.get("territory_code", ""),
                price.get("territory_name", ""),
                price.get("currency_code", ""),
                price.get("customer_price", 0),
                price.get("proceeds", 0),
            ])

        return buf.getvalue().encode("utf-8")

    @staticmethod
    def import_prices(file_bytes: bytes) -> list[dict]:
        """Parse a CSV file and return a list of price dicts.

        Expected format matches the export layout:
        Territory Code, Territory Name, Currency, Customer Price, Proceeds

        Returns:
            List of ``{territory_code: str, customer_price: float}``.
        """
        text = file_bytes.decode("utf-8")
        buf = io.StringIO(text)
        reader = csv.reader(buf)

        # Skip header row
        try:
            next(reader)
        except StopIteration:
            return []

        result: list[dict] = []
        for row in reader:
            if not row or not row[0].strip():
                continue
            territory_code = row[0].strip()
            try:
                customer_price = float(row[3]) if len(row) > 3 and row[3] else 0.0
            except (ValueError, TypeError):
                customer_price = 0.0

            result.append({
                "territory_code": territory_code,
                "customer_price": customer_price,
            })

        return result
