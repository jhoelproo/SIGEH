"""Import a synthetic price list into disposable PostgreSQL only."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import CALCULOS_QT as app
from tests.test_billing_consistency_postgres import server as server
from tests.test_receipt_optional_uuid_postgres import receipts as receipts
from tests.test_word_catalog_import import PRICES


def catalog_rows():
    with app.db_connect() as con:
        return {
            (row["categoria"], row["nombre"]): (row["id"], float(row["precio"]))
            for row in con.execute(
                "SELECT id,categoria,nombre,precio FROM universal_items"
            ).fetchall()
        }


def test_synthetic_document_persists_and_reimport_keeps_identifiers(receipts):
    target, first = app.import_word_catalog(str(PRICES), "Laboratorios", "FUTURO")
    assert target == "universal"
    assert sum(sum(counts.values()) for counts in first.values()) == 4
    before = catalog_rows()
    assert before[("Medicamentos", "PRODUCTO QA ALFA 10 MG")][1] == 12.34
    assert before[("Materiales", "INSUMO QA GUANTES")][1] == 9180
    _, second = app.import_word_catalog(str(PRICES), "Honorarios", "FUTURO")
    assert second == {
        "Medicamentos": {"updated": 2, "inserted": 0},
        "Materiales": {"updated": 2, "inserted": 0},
    }
    assert catalog_rows() == before


def test_import_rolls_back_if_a_later_item_fails(receipts, monkeypatch):
    original_connect = app.db_connect
    before = catalog_rows()

    @contextmanager
    def fail_later_item():
        with original_connect() as con:

            def execute(sql, params=()):
                if (
                    "INSERT INTO universal_items" in sql
                    and "PRODUCTO QA BETA" in params[1]
                ):
                    raise RuntimeError("Synthetic interrupted import")
                return con.execute(sql, params)

            yield SimpleNamespace(execute=execute)

    with monkeypatch.context() as context:
        context.setattr(app, "db_connect", fail_later_item)
        with pytest.raises(RuntimeError, match="Synthetic interrupted import"):
            app.import_word_to_universal_catalog(str(PRICES))
    assert catalog_rows() == before
