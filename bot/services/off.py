"""Open Food Facts — открытая база продуктов по штрихкодам (бесплатно, без ключа)."""

import asyncio
from typing import Any

import httpx

API_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
TIMEOUT = 15.0


def _fetch_sync(barcode: str) -> dict[str, Any] | None:
    response = httpx.get(API_URL.format(barcode=barcode), timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != 1:
        return None
    return payload.get("product")


def _pick_name(product: dict[str, Any]) -> str:
    for key in ("product_name_ru", "product_name"):
        value = (product.get(key) or "").strip()
        if value:
            return value
    brands = (product.get("brands") or "").strip()
    return brands or "Продукт"


def _num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_product(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Извлекает название и КБЖУ на 100 г из ответа Open Food Facts."""
    if not payload:
        return None
    nutriments = payload.get("nutriments") or {}
    kcal = _num(nutriments.get("energy-kcal_100g"))
    if kcal is None:
        kj = _num(nutriments.get("energy_100g"))
        kcal = kj / 4.184 if kj is not None else 0.0
    return {
        "name": _pick_name(payload),
        "kcal": round(kcal or 0.0, 1),
        "protein": round(_num(nutriments.get("proteins_100g")) or 0.0, 1),
        "fat": round(_num(nutriments.get("fat_100g")) or 0.0, 1),
        "carbs": round(_num(nutriments.get("carbohydrates_100g")) or 0.0, 1),
        "brand": (payload.get("brands") or "").strip(),
    }


async def get_product(barcode: str) -> dict[str, Any] | None:
    """Штрихкод -> {'name', 'kcal', 'protein', 'fat', 'carbs', 'brand'} или None."""
    clean = "".join(ch for ch in barcode if ch.isdigit())
    if len(clean) < 8:
        return None
    try:
        payload = await asyncio.to_thread(_fetch_sync, clean)
    except Exception:
        return None
    return parse_product(payload)
