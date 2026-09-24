from decimal import Decimal

import pytest

from billing_money import (
    install_money_storage,
    medication_base_price,
    effective_medication_price,
    line_total,
)
from tests import test_billing_consistency_postgres as pg

server = pg.server


@pytest.fixture
def money_database(server):
    with pg.Connection(server) as con:
        con.execute("""CREATE TABLE universal_items(precio REAL);
            CREATE TABLE ars_items(precio REAL);
            CREATE TABLE recibos(sala REAL,total REAL);
            CREATE TABLE recibo_items(precio_unit REAL,total REAL);
            INSERT INTO universal_items VALUES(5.99);
            INSERT INTO recibos VALUES(460, 5.99);""")
        install_money_storage(con)
    return lambda: pg.Connection(server)


def test_catalog_receipt_persistence_and_reopen_keep_cents(money_database):
    with money_database() as con:
        assert con.execute("SELECT total FROM recibos").fetchone()["total"] == Decimal(
            "5.99"
        )
        assert con.execute("SELECT precio FROM universal_items").fetchone()[
            "precio"
        ] == Decimal("5.99")
        for price in ("6.00", "5.99", "0.10", "999999.99"):
            con.execute(
                "INSERT INTO universal_items VALUES(%s)",
                (medication_base_price(price, 35),),
            )
    with money_database() as con:
        bases = con.execute("SELECT precio FROM universal_items OFFSET 1").fetchall()
        for row, expected in zip(
            bases, ("6.00", "5.99", "0.10", "999999.99"), strict=True
        ):
            effective = effective_medication_price(row["precio"], 35)
            assert effective == Decimal(expected)
            con.execute(
                "INSERT INTO recibo_items VALUES(%s,%s)",
                (effective, line_total(effective, 3)),
            )
    with money_database() as con:
        totals = con.execute("SELECT total FROM recibo_items").fetchall()
        assert [row["total"] for row in totals] == [
            Decimal(v) for v in ("18.00", "17.97", "0.30", "2999999.97")
        ]
