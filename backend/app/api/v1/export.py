"""Price export/import API endpoints (Excel and CSV)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.security import get_current_user
from app.services.export.csv import CSVExportService
from app.services.export.excel import ExcelExportService

logger = logging.getLogger(__name__)
router = APIRouter()


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------


class PriceExportItem(BaseModel):
    territory_code: str
    territory_name: str = ""
    currency_code: str = ""
    customer_price: float = 0.0
    proceeds: float = 0.0


class PriceExportRequest(BaseModel):
    subscription_name: str = "Prices"
    format: str = "xlsx"  # "xlsx" or "csv"
    prices: list[PriceExportItem]


class PriceImportItem(BaseModel):
    territory_code: str
    customer_price: float


class PriceImportResponse(BaseModel):
    items: list[PriceImportItem]
    count: int


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.post("/export")
async def export_prices(
    body: PriceExportRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    """Export prices as an Excel or CSV file.

    Accepts a JSON body with a prices array and returns the file as
    a downloadable attachment.
    """
    prices_dicts = [p.model_dump() for p in body.prices]
    filename_base = body.subscription_name.replace(" ", "_")

    if body.format == "csv":
        file_bytes = CSVExportService.export_prices(body.subscription_name, prices_dicts)
        return Response(
            content=file_bytes,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_base}.csv"',
            },
        )

    # Default to Excel
    file_bytes = ExcelExportService.export_prices(body.subscription_name, prices_dicts)
    return Response(
        content=file_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename_base}.xlsx"',
        },
    )


@router.post("/import", response_model=PriceImportResponse)
async def import_prices(
    file: UploadFile,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> PriceImportResponse:
    """Import prices from an uploaded Excel or CSV file.

    The file format is determined by the filename extension.
    Returns parsed price items.
    """
    if file.filename is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required to determine format",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    filename_lower = file.filename.lower()

    if filename_lower.endswith(".csv"):
        parsed = CSVExportService.import_prices(file_bytes)
    elif filename_lower.endswith((".xlsx", ".xls")):
        parsed = ExcelExportService.import_prices(file_bytes)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file format. Use .xlsx or .csv files.",
        )

    items = [
        PriceImportItem(
            territory_code=p["territory_code"],
            customer_price=p["customer_price"],
        )
        for p in parsed
    ]

    return PriceImportResponse(items=items, count=len(items))
