"""The production migration retains records and revokes inherited API grants."""

from pathlib import Path

import psycopg2

from tests.test_billing_consistency_postgres import server as server


def test_migration_preserves_existing_catalog_and_blocks_api_roles(server):
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations/20261023184143_ars_specialty_medication_materials.sql"
    ).read_text(encoding="utf8")
    with psycopg2.connect(server) as con:
        with con.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
            cur.execute("CREATE TABLE ars(id INTEGER PRIMARY KEY, name TEXT)")
            cur.execute(
                "CREATE TABLE universal_items(id INTEGER PRIMARY KEY, price NUMERIC)"
            )
            cur.execute("INSERT INTO ars VALUES(7,'SYNTHETIC')")
            cur.execute("INSERT INTO universal_items VALUES(1,19.25),(2,3.50)")
            for role in ("anon", "authenticated"):
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
                if not cur.fetchone():
                    # The two fixed identifiers are not user input.
                    from psycopg2 import sql

                    cur.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
            cur.execute(migration)
            cur.execute(
                "INSERT INTO medication_material_links VALUES(1,2,3,NOW(),'QA')"
            )
            cur.execute(
                "GRANT ALL ON medication_material_links TO PUBLIC,anon,authenticated"
            )
            cur.execute(migration)
            cur.execute(
                "SELECT id,price,materials_configured FROM universal_items ORDER BY id"
            )
            assert cur.fetchall() == [(1, 19.25, False), (2, 3.50, False)]
            cur.execute("SELECT honorarium_specialties FROM ars WHERE id=7")
            assert cur.fetchone() == ({},)
            cur.execute("SELECT units FROM medication_material_links")
            assert cur.fetchone() == (3,)
            cur.execute(
                "SELECT relrowsecurity FROM pg_class WHERE oid='medication_material_links'::regclass"
            )
            assert cur.fetchone() == (True,)
            for role in ("anon", "authenticated"):
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                    cur.execute(
                        "SELECT has_table_privilege(%s,'medication_material_links',%s)",
                        (role, privilege),
                    )
                    assert cur.fetchone() == (False,)
