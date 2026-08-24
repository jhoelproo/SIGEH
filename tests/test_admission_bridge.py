import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app

from admission_bridge import (
    AdmissionBridgeError,
    AdmissionReadOnlyRepository,
    is_uninsured,
    normalize_identifier,
    normalize_service_date,
)
from admission_contract import (
    COVERAGE_INCOMPLETE,
    COVERAGE_INSURED_VERIFIED,
    READINESS_READY,
)


def build_admission_db(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE atenciones(
            id INTEGER PRIMARY KEY,
            paciente_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            fecha TEXT,
            hora TEXT,
            nss TEXT,
            nss_clean TEXT,
            cedula TEXT,
            cedula_clean TEXT,
            ars TEXT,
            tipo_atencion TEXT,
            estado TEXT,
            identidad_estado TEXT,
            requiere_revision INTEGER,
            created_at TEXT,
            updated_at TEXT
            ,turno_id INTEGER
        );
        CREATE TABLE turnos(
            id INTEGER PRIMARY KEY,
            fecha_inicio TEXT,
            fecha_inicio_real TEXT,
            estado TEXT
        );
        CREATE TABLE paciente_identificadores(
            id INTEGER PRIMARY KEY,
            paciente_id INTEGER NOT NULL,
            tipo TEXT,
            valor_normalizado TEXT,
            activo INTEGER,
            conflicto INTEGER
        );
        CREATE TABLE integracion_eventos(
            id INTEGER PRIMARY KEY,
            event_uuid TEXT UNIQUE,
            source_instance_id TEXT,
            atencion_id INTEGER,
            tipo TEXT,
            estado_flujo TEXT,
            campos_faltantes_json TEXT,
            actor TEXT,
            actor_rol TEXT,
            session_id TEXT,
            created_at TEXT
        );
        CREATE TABLE turno_cierre_eventos(
            id INTEGER PRIMARY KEY,event_uuid TEXT,source_instance_id TEXT,
            turno_id INTEGER,dia_operativo_id INTEGER,fecha_base TEXT,
            fecha_inicio TEXT,fecha_fin_programada TEXT,fecha_cierre_real TEXT,
            representante TEXT,tipo_turno TEXT,actor TEXT,actor_rol TEXT,
            session_id TEXT,created_at TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO turnos VALUES(1,'2026-07-17 08:00:00','2026-07-17 08:00:00','ABIERTO')"
    )
    rows = [
        (1, 10, "PACIENTE ASEGURADO", "2026-07-17", "09:00", "001-999", "001999",
         "001-0000001-1", "00100000011", "SENASA CONTRIBUTIVO", "EMERGENCIA",
         "ACTIVA", "VALIDADA", 0, "2026-07-17 09:00:00", None),
        (2, 11, "PACIENTE URGENCIA", "2026-07-17", "09:10", "002-99", "00299",
         "001-0000002-2", "00100000022", "SENASA CONTRIBUTIVO", "URGENCIA",
         "ACTIVA", "VALIDADA", 0, "2026-07-17 09:10:00", None),
        (3, 12, "PACIENTE ANULADO", "2026-07-17", "09:20", "003-99", "00399",
         "001-0000003-3", "00100000033", "HUMANO", "EMERGENCIA",
         "ANULADA", "VALIDADA", 0, "2026-07-17 09:20:00", None),
        (4, 13, "PACIENTE SIN SEGURO", "2026-07-17", "09:30", "", "",
         "001-0000004-4", "00100000044", "SIN SEGURO", "EMERGENCIA",
         "ACTIVA", "VALIDADA", 0, "2026-07-17 09:30:00", None),
        (5, 14, "PACIENTE EN REVISION", "2026-07-17", "09:40", "005-99", "00599",
         "001-0000005-5", "00100000055", "HUMANO", "EMERGENCIA",
         "ACTIVA", "NSS_EN_REVISION", 1, "2026-07-17 09:40:00", None),
    ]
    connection.executemany(
        "INSERT INTO atenciones VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)", rows
    )
    connection.executemany(
        """INSERT INTO paciente_identificadores(
               id,paciente_id,tipo,valor_normalizado,activo,conflicto
           ) VALUES(?,?,?,?,1,0)""",
        [
            (1, 10, "CEDULA", "00100000011"),
            (2, 11, "CEDULA", "00100000022"),
            (3, 12, "CEDULA", "00100000033"),
            (4, 13, "CEDULA", "00100000044"),
            (5, 14, "CEDULA", "00100000055"),
        ],
    )
    connection.commit()
    connection.close()


class AdmissionBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_identifier_normalization_and_uninsured_aliases(self):
        self.assertEqual(normalize_identifier("001-0000001-1"), "00100000011")
        self.assertEqual(normalize_service_date("17/07/2026"), "2026-07-17")
        self.assertEqual(normalize_service_date("2026-07-17"), "2026-07-17")
        self.assertTrue(is_uninsured("SIN SEGURO", ""))
        self.assertFalse(is_uninsured("", ""))
        self.assertFalse(is_uninsured("HUMANO", ""))
        self.assertFalse(is_uninsured("SENASA CONTRIBUTIVO", "12345"))

    def test_reads_shift_closure_and_all_turn_attentions(self):
        path = self.root / "pacientes.db"
        build_admission_db(path)
        connection = sqlite3.connect(path)
        connection.execute(
            """INSERT INTO turno_cierre_eventos VALUES(
               1,'evt-1','source-1',1,9,'2026-07-17','2026-07-17 08:00:00',
               '2026-07-18 08:00:00','2026-07-18 08:02:00','aux.01','8AM_8AM',
               'aux.01','auxiliar','session-1','2026-07-18 08:02:00')"""
        )
        connection.commit()
        connection.close()
        repository = AdmissionReadOnlyRepository(path)

        closures = repository.list_shift_closure_events()
        attentions = repository.list_turn_attentions(1)

        self.assertEqual(len(closures), 1)
        self.assertEqual(closures[0].turn_id, 1)
        self.assertEqual(closures[0].representative, "aux.01")
        self.assertEqual([row.attention_id for row in attentions], [1, 4, 5])

    def test_search_only_returns_active_valid_emergency(self):
        path = self.root / "pacientes.db"
        build_admission_db(path)
        repository = AdmissionReadOnlyRepository(path)

        insured = repository.find_eligible_by_identifier("001-0000001-1")
        self.assertEqual([row.attention_id for row in insured], [1])
        self.assertEqual(insured[0].name, "PACIENTE ASEGURADO")
        self.assertFalse(insured[0].uninsured)
        self.assertEqual(
            insured[0].coverage_status,
            COVERAGE_INSURED_VERIFIED,
        )
        self.assertEqual(insured[0].billing_readiness, READINESS_READY)
        self.assertEqual(insured[0].service_date, "2026-07-17")
        self.assertTrue(insured[0].source_instance_id)
        self.assertTrue(insured[0].snapshot_hash)

        self.assertEqual(
            repository.find_eligible_by_identifier("001-0000002-2"), []
        )
        self.assertEqual(
            repository.find_eligible_by_identifier("001-0000003-3"), []
        )
        self.assertEqual(
            repository.find_eligible_by_identifier("001-0000005-5"), []
        )

    def test_current_turn_billable_list_is_ready_emergency_and_newest_first(self):
        path = self.root / "pacientes.db"
        build_admission_db(path)
        rows = AdmissionReadOnlyRepository(path).list_current_billable_attentions()

        self.assertEqual([row.attention_id for row in rows], [4, 1])
        self.assertTrue(
            all(row.attention_type == "EMERGENCIA" for row in rows)
        )
        self.assertTrue(
            all(row.billing_readiness == READINESS_READY for row in rows)
        )

    def test_validation_dialog_loads_turn_searches_and_clear_restores_all(self):
        path = self.root / "pacientes.db"
        build_admission_db(path)
        qt_app = QApplication.instance() or QApplication([])
        all_rows = AdmissionReadOnlyRepository(path).list_current_billable_attentions()

        def central_loader(_repository=None, identifier="", **_kwargs):
            term = "".join(ch for ch in str(identifier or "") if ch.isdigit())
            if not term:
                return list(all_rows)
            return [
                row for row in all_rows
                if term in row.nss_clean or term in row.cedula_clean
            ]

        loader_patch = patch.object(
            app,
            "load_admission_validation_attentions",
            side_effect=central_loader,
        )
        loader_patch.start()
        dialog = app.AdmissionValidationDialog()

        def wait_for(expected_ids):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                qt_app.processEvents()
                if dialog._load_worker is None and [
                    row.attention_id for row in dialog.attentions
                ] == expected_ids:
                    return
                time.sleep(0.005)
            self.fail("La lista de Admisión no terminó de cargar")

        try:
            with patch.object(app, "sync_admission_projection", return_value=0):
                dialog.show()
                wait_for([4, 1])
                self.assertEqual(dialog.table.rowCount(), 2)
                self.assertFalse(dialog.confirm_button.isEnabled())

                dialog.identifier_edit.setText("00100000011")
                dialog.identifier_edit.setFocus()
                QTest.keyClick(dialog.identifier_edit, Qt.Key_Return)
                wait_for([1])
                self.assertEqual(dialog.table.rowCount(), 1)

                dialog.identifier_edit.clear()
                wait_for([4, 1])
                self.assertEqual(dialog.table.rowCount(), 2)
                self.assertEqual(
                    len({row.attention_id for row in dialog.attentions}), 2
                )
                self.assertFalse(dialog.confirm_button.isEnabled())
        finally:
            dialog.close()
            loader_patch.stop()

    def test_identifier_search_excludes_same_patient_from_old_shift(self):
        path = self.root / "pacientes.db"
        build_admission_db(path)
        connection = sqlite3.connect(path)
        connection.execute(
            "INSERT INTO turnos VALUES(0,'2026-07-16 08:00:00','2026-07-16 08:00:00','CERRADO')"
        )
        connection.execute(
            """INSERT INTO atenciones VALUES(
                   8,10,'PACIENTE ASEGURADO','2026-07-16','09:00','001-999','001999',
                   '001-0000001-1','00100000011','SENASA CONTRIBUTIVO','EMERGENCIA',
                   'ACTIVA','VALIDADA',0,'2026-07-16 09:00:00',NULL,0
               )"""
        )
        connection.commit()
        connection.close()

        rows = AdmissionReadOnlyRepository(path).find_eligible_by_identifier(
            "001-0000001-1"
        )

        self.assertEqual([row.attention_id for row in rows], [1])

    def test_uninsured_attention_is_detected_and_source_remains_unchanged(self):
        path = self.root / "pacientes.db"
        build_admission_db(path)
        repository = AdmissionReadOnlyRepository(path)
        before = path.read_bytes()

        rows = repository.find_eligible_by_identifier("00100000044")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].attention_id, 4)
        self.assertTrue(rows[0].uninsured)
        self.assertEqual(path.read_bytes(), before)

    def test_admission_ars_catalog_exposes_only_canonical_names(self):
        path = self.root / "pacientes.db"
        build_admission_db(path)
        connection = sqlite3.connect(path)
        connection.execute(
            "UPDATE atenciones SET ars='HUMNAO' WHERE id=5"
        )
        connection.commit()
        connection.close()

        names = AdmissionReadOnlyRepository(path).list_canonical_ars()

        self.assertEqual(names, ["HUMANO", "SENASA CONTRIBUTIVO"])

    def test_missing_nss_is_incomplete_not_uninsured(self):
        path = self.root / "pacientes.db"
        build_admission_db(path)
        connection = sqlite3.connect(path)
        connection.execute(
            """INSERT INTO atenciones VALUES(
                   6,15,'PACIENTE COBERTURA PENDIENTE','2026-07-17','10:00',
                   '','', '001-0000006-6','00100000066','HUMANO',
                   'EMERGENCIA','ACTIVA','VALIDADA',0,
                   '2026-07-17 10:00:00',NULL,1
               )"""
        )
        connection.commit()
        connection.close()

        attention = AdmissionReadOnlyRepository(path).get_eligible_attention(6)

        self.assertIsNotNone(attention)
        self.assertFalse(attention.uninsured)
        self.assertEqual(attention.coverage_status, COVERAGE_INCOMPLETE)
        self.assertIsNone(
            AdmissionReadOnlyRepository(path).get_billable_attention(6)
        )

    def test_only_exact_emergency_type_is_exposed(self):
        path = self.root / "pacientes.db"
        build_admission_db(path)
        connection = sqlite3.connect(path)
        connection.execute(
            """INSERT INTO atenciones VALUES(
                   7,16,'PACIENTE CONSULTA','2026-07-17','10:10',
                   '777777','777777','001-0000007-7','00100000077','HUMANO',
                   'CONSULTA','ACTIVA','VALIDADA',0,
                   '2026-07-17 10:10:00',NULL,1
               )"""
        )
        connection.commit()
        connection.close()

        repository = AdmissionReadOnlyRepository(path)
        self.assertIsNone(repository.get_eligible_attention(7))
        self.assertNotIn(
            7,
            [
                row.attention_id
                for row in repository.list_eligible(
                    "2026-07-17",
                    "2026-07-17",
                )
            ],
        )

    def test_list_eligible_supports_date_range_and_name_search(self):
        path = self.root / "pacientes.db"
        build_admission_db(path)
        connection = sqlite3.connect(path)
        connection.execute(
            "UPDATE atenciones SET fecha='17/07/2026' WHERE id=1"
        )
        connection.commit()
        connection.close()
        repository = AdmissionReadOnlyRepository(path)

        rows = repository.list_eligible(
            "2026-07-17",
            "2026-07-17",
            search="asegurado",
        )

        self.assertEqual([row.attention_id for row in rows], [1])
        self.assertEqual(rows[0].service_date, "2026-07-17")
        self.assertEqual(repository.latest_eligible_date(), "2026-07-17")

    def test_rejects_legacy_or_wrong_database(self):
        path = self.root / "legacy.db"
        sqlite3.connect(path).close()
        repository = AdmissionReadOnlyRepository(path)

        with self.assertRaises(AdmissionBridgeError):
            repository.find_eligible_by_identifier("12345")

    def test_transfer_events_are_ordered_and_source_remains_read_only(self):
        path = self.root / "pacientes.db"
        build_admission_db(path)
        connection = sqlite3.connect(path)
        connection.executemany(
            """INSERT INTO integracion_eventos VALUES(
                   ?,?,?,?,?,?,?,?,?,?,?
               )""",
            [
                (1, "event-1", "source-1", 1, "ATENCION_LISTA_FACTURACION",
                 "LISTO_PARA_FACTURAR", "[]", "aux", "auxiliar", "s1",
                 "2026-07-17 09:01:00"),
                (2, "event-2", "source-1", 4, "ATENCION_REQUIERE_CORRECCION",
                 "INCOMPLETO", '["cédula"]', "aux", "auxiliar", "s1",
                 "2026-07-17 09:31:00"),
            ],
        )
        connection.commit()
        connection.close()
        before = path.read_bytes()

        repository = AdmissionReadOnlyRepository(path)
        events = repository.list_transfer_events(after_event_id=0)

        self.assertEqual([event.event_id for event in events], [1, 2])
        self.assertEqual(events[0].attention.attention_id, 1)
        self.assertEqual(events[1].missing_fields, ("cédula",))
        self.assertEqual(repository.latest_transfer_event_id(), 2)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
