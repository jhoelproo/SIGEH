"""Local receipt paths and rendering without contacting the central service."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Any
import uuid

from local_receipts import LocalReceiptStore


def local_receipt_root() -> Path:
    return (
        Path(os.environ.get("LOCALAPPDATA") or Path.home())
        / "HospitalProvincial"
        / "FacturacionMedica"
        / "local_receipts"
    )


@lru_cache(maxsize=4)
def _store_at(path: str) -> LocalReceiptStore:
    return LocalReceiptStore(path)


def local_receipt_store() -> LocalReceiptStore:
    return _store_at(str(local_receipt_root() / "receipts.sqlite3"))


def remember_startup_catalog(data: dict[str, Any], username: str) -> None:
    store = local_receipt_store()
    store.cache("universal", data.get("universal") or {})
    store.cache("preferences:" + username, data.get("preferences") or {})
    store.cache("favorites:" + username, list(data.get("catalog_favorites") or ()))


def remember_tariff(name: str, data: dict[str, Any]) -> None:
    store = local_receipt_store()
    store.cache("tariff:" + name, data)
    names = set(store.cached("tariff_names", []))
    names.add(name)
    store.cache("tariff_names", sorted(names))


def local_catalog_startup(username: str) -> dict[str, Any]:
    store = local_receipt_store()
    names = store.cached("tariff_names", [])
    return {
        "universal": store.cached("universal", {}),
        "preferences": store.cached("preferences:" + username, {"theme": "claro"}),
        "catalog_favorites": store.cached("favorites:" + username, []),
        "local_tariffs": {name: store.cached("tariff:" + name, {}) for name in names},
        "local_medication_markup": store.cached("medication_markup", None),
    }


def local_receipt_pdf_data(job: dict[str, Any], request_id: str) -> dict[str, Any]:
    identity = str(uuid.UUID(request_id))
    categories = []
    for category, rows in job["grouped"]:
        categories.append(
            {
                "nombre": category,
                "items": [
                    {
                        "descripcion": name,
                        "precio": price,
                        "cantidad": quantity,
                        "total": subtotal,
                    }
                    for name, price, quantity, subtotal, _ars in rows
                ],
            }
        )
    attention = dict(job.get("admission_attention") or {})
    return {
        "numero": "LOCAL-" + identity[:8].upper(),
        "local_request_id": identity,
        "document_title": "RECIBO LOCAL · PENDIENTE DE SINCRONIZACIÓN",
        "estado_documento": "PRELIMINAR",
        "fecha": job["date_str"],
        "paciente": job["patient"],
        "dx": job["dx_raw"],
        "ars": job["ars_name"],
        "sala": job["sala"],
        "categorias": categories,
        "total_general": job["total_general"],
        "nss": attention.get("nss_clean") or attention.get("nss") or "",
        "numero_autorizacion": job.get("authorization_number") or "",
        "usuario": job["current_user"].get("full_name")
        or job["current_user"]["username"],
    }


def render_local_receipt(job: dict[str, Any], request_id: str, renderer) -> str:
    identity = str(uuid.UUID(request_id))
    destination = local_receipt_root() / "pdfs" / f"local_{identity}.pdf"
    destination.parent.mkdir(parents=True, exist_ok=True)
    return renderer.render_pdf(local_receipt_pdf_data(job, identity), destination)
