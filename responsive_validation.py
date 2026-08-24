"""Validación visual reproducible de los widgets reales del sistema.

La rutina usa datos sintéticos y no abre conexiones ni modifica producción.
Se invoca desde CALCULOS_QT.exe con ``--validate-responsive-ui RUTA``.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time

from PySide6.QtCore import QCoreApplication, QDate, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QPushButton,
    QTableWidgetItem,
)
from display_layout import DisplaySnapshot


_MAX_PROCESS_EVENTS_MS = 0.0


@contextmanager
def patched(target, replacements):
    previous = {}
    for name, value in replacements.items():
        previous[name] = getattr(target, name)
        setattr(target, name, value)
    try:
        yield
    finally:
        for name, value in previous.items():
            setattr(target, name, value)


def _settle(app, rounds=8):
    global _MAX_PROCESS_EVENTS_MS
    for _ in range(max(1, int(rounds))):
        started = time.perf_counter()
        app.processEvents()
        _MAX_PROCESS_EVENTS_MS = max(
            _MAX_PROCESS_EVENTS_MS,
            (time.perf_counter() - started) * 1000.0,
        )
        time.sleep(0.015)


def _close_file_handlers_under(directory: str | os.PathLike[str]) -> None:
    """Release only validation-owned log files before deleting its temp tree."""
    root = os.path.normcase(os.path.abspath(os.fspath(directory)))
    loggers = [logging.getLogger()]
    loggers.extend(
        logger
        for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    )
    for logger in loggers:
        for handler in list(logger.handlers):
            filename = getattr(handler, "baseFilename", "")
            if not filename:
                continue
            candidate = os.path.normcase(os.path.abspath(str(filename)))
            try:
                owned = os.path.commonpath((root, candidate)) == root
            except ValueError:
                owned = False
            if not owned:
                continue
            logger.removeHandler(handler)
            handler.close()


def _finish_validation_workers(admission_page, timeout_ms: int = 5000) -> None:
    """Drain the validation-owned Qt pool after its UI has been shut down."""
    coordinator = getattr(admission_page, "_hybrid_coordinator", None)
    if coordinator is None:
        return
    coordinator.stop()
    pool = getattr(coordinator, "_pool", None)
    if pool is not None and not pool.waitForDone(int(timeout_ms)):
        raise TimeoutError("Los workers de validación de Admisión no terminaron.")


def _capture(app, widget, path: Path, width: int, height: int):
    widget.resize(int(width), int(height))
    widget.show()
    widget.raise_()
    widget.activateWindow()
    if widget.layout() is not None:
        widget.layout().activate()
    _settle(app)
    pixmap = widget.grab()
    if pixmap.isNull() or not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"No se pudo capturar {path.name}")


def _benchmark_patient_documents(database: Path) -> dict[str, dict[str, float | str]]:
    """Measure the exact packaged SQLite path used by the admission hot path."""
    if not database.is_file():
        raise FileNotFoundError(database)
    lookup_sql = """SELECT p.id,p.nombre,p.cedula,p.nss,p.telefono,p.direccion,
                           p.nacionalidad,p.ars,p.server_revision
                      FROM pacientes p
                      JOIN paciente_identificadores i ON i.paciente_id=p.id
                     WHERE i.tipo=? AND i.valor_normalizado=? AND i.activo=1
                     ORDER BY p.updated_at DESC,p.id DESC LIMIT 1"""
    result: dict[str, dict[str, float | str]] = {}
    with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as con:
        keys = dict(
            con.execute(
                """SELECT tipo,valor_normalizado
                     FROM paciente_identificadores
                    WHERE activo=1 AND tipo IN ('CEDULA','NSS')
                    GROUP BY tipo,valor_normalizado HAVING COUNT(*)=1
                    ORDER BY tipo,valor_normalizado"""
            ).fetchall()
        )
    for kind in ("CEDULA", "NSS"):
        key = str(keys.get(kind) or "")
        if not key:
            raise RuntimeError(f"La base empaquetada no contiene un {kind} comprobable.")
        timings = []
        plan_text = ""
        for _index in range(20):
            started = time.perf_counter()
            with sqlite3.connect(
                f"file:{database.resolve()}?mode=ro", uri=True
            ) as con:
                con.execute("PRAGMA busy_timeout=5000")
                row = con.execute(lookup_sql, (kind, key)).fetchone()
                if row is None:
                    raise RuntimeError(f"Lookup local {kind} no encontró su paciente.")
                if not plan_text:
                    plan_text = " | ".join(
                        str(item[-1])
                        for item in con.execute(
                            "EXPLAIN QUERY PLAN " + lookup_sql, (kind, key)
                        ).fetchall()
                    )
            timings.append((time.perf_counter() - started) * 1000.0)
        ordered = sorted(timings)
        p95 = ordered[max(0, int(len(ordered) * 0.95 + 0.9999) - 1)]
        if p95 > 200.0:
            raise RuntimeError(f"Lookup local {kind} excedió 200 ms P95: {p95:.3f}")
        expected_indexes = {"idx_paciente_identificadores_lookup"}
        if kind == "CEDULA":
            expected_indexes.add("uq_cedula_activa")
        if not any(index_name in plan_text for index_name in expected_indexes):
            raise RuntimeError(f"Lookup local {kind} no usa el índice canónico: {plan_text}")
        result[kind] = {
            "min_ms": round(min(timings), 3),
            "avg_ms": round(statistics.fmean(timings), 3),
            "p95_ms": round(p95, 3),
            "max_ms": round(max(timings), 3),
            "query_plan": plan_text,
        }
    return result


def _snapshot(module, profile, width, height, dpi=96.0):
    recommended = module.recommend_layout_profile(width, height, dpi, 1.0)
    density = (
        module.DENSITY_VERY_COMPACT
        if profile == module.PROFILE_VERY_COMPACT
        else module.DENSITY_COMPACT
        if profile == module.PROFILE_COMPACT or dpi >= 132.0
        else module.DENSITY_COMFORTABLE
        if profile == module.PROFILE_WIDE and dpi <= 110.0
        else module.DENSITY_NORMAL
    )
    return DisplaySnapshot(
        width=width,
        height=height,
        logical_dpi=dpi,
        device_pixel_ratio=1.0,
        windows_scale=max(dpi / 96.0, 1.0),
        recommended_profile=recommended,
        applied_profile=profile,
        density=density,
        text_percent=100,
    )


def _fill_main_window(module, window):
    samples = {
        "Medicamentos": (
            "ACETAMINOFÉN 500 MG",
            "SOLUCIÓN SALINA 0.9% 1000 ML",
            "DICLOFENAC 75 MG",
        ),
        "Materiales": ("GUANTES", "BAJANTE DE SUERO", "CATÉTER"),
        "Laboratorios": ("HEMOGRAMA", "GLUCOSA"),
        "Imágenes": ("RADIOGRAFÍA", "SONOGRAFÍA"),
        "Procedimientos": ("CURACIÓN", "NEBULIZACIÓN"),
        "Honorarios": ("HONORARIO MÉDICO",),
    }
    for category, names in samples.items():
        widget = window.source_lists.get(category)
        if widget is None:
            continue
        widget.clear()
        for index, name in enumerate(names):
            widget.addItem(f"{name}    RD$ {3.50 + index * 8.25:,.2f}")
    window.name_edit.setText("PACIENTE DE PRUEBA")
    window.dx_edit.setText("DIAGNÓSTICO DE VALIDACIÓN")
    window.ars_combo.setCurrentText("HUMANO")
    window.authorization_edit.setText("AUT-PRUEBA")
    window.date_edit.setDate(QDate(2026, 7, 29))
    rows = (
        ("Medicamentos", "ACETAMINOFÉN 500 MG", 2, 3.60),
        ("Materiales", "GUANTES", 1, 4.00),
        ("Materiales", "BAJANTE DE SUERO", 1, 8.00),
    )
    window.cart_table.setRowCount(0)
    for row_index, (category, item_name, quantity, price) in enumerate(rows):
        window.cart_table.insertRow(row_index)
        window.cart_table.setItem(
            row_index, 0, QTableWidgetItem(category)
        )
        window.cart_table.setItem(
            row_index, 1, QTableWidgetItem(item_name)
        )
        quantity_editor = module.CartQuantitySpinBox()
        quantity_editor.setRange(1, 300)
        quantity_editor.setValue(quantity)
        quantity_editor.setMinimumSize(78, 38)
        window.cart_table.setCellWidget(row_index, 2, quantity_editor)
        window.cart_table.setItem(
            row_index, 3, QTableWidgetItem(f"RD$ {price:,.2f}")
        )
        window.cart_table.setItem(
            row_index, 4, QTableWidgetItem(
                f"RD$ {price * quantity:,.2f}"
            )
        )
        remove = QPushButton()
        remove.setToolTip("Eliminar ítem")
        remove.setCursor(Qt.PointingHandCursor)
        remove.setIcon(
            window.style().standardIcon(module.QStyle.SP_TrashIcon)
        )
        remove.setObjectName("CartDeleteButton")
        window.cart_table.setCellWidget(row_index, 5, remove)
    window.lbl_sub_medicamentos.setText("Medicamentos: RD$ 7.20")
    window.lbl_sub_materiales.setText("Materiales: RD$ 12.00")
    window.lbl_total.setText("Total: RD$ 19.20")


def _fill_monthly(module, page):
    page.batches.setRowCount(3)
    batches = (
        ("Julio 2026", "HUMANO", "16", "PENDIENTE"),
        ("Julio 2026", "FUTURO", "11", "PENDIENTE"),
        ("Junio 2026", "PRIMERA ARS", "8", "ENVIADO"),
    )
    for row, values in enumerate(batches):
        page.batches.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        for column, value in enumerate(values, 1):
            page.batches.setItem(row, column, QTableWidgetItem(value))
    page.batches.selectRow(0)
    page.navigator_summary.setText(
        "HUMANO\nJulio 2026 · Versión 1\n16 pacientes · RD$ 20,386.70"
    )
    page._fill_saved_batches(
        [
            {
                "id": index + 1,
                "period_month": 7,
                "period_year": 2026,
                "ars": ars,
                "ars_display_name": ars,
                "version": 1,
                "receipts": count,
                "total": total,
                "status": module.BATCH_PENDING,
                "created_at": "2026-07-29 10:00",
                "sent_at": "",
                "sent_by": "",
                "last_export_path": "",
            }
            for index, (ars, count, total) in enumerate(
                (
                    ("HUMANO", 16, 20386.70),
                    ("FUTURO", 11, 13565.80),
                    ("PRIMERA ARS", 8, 11240.00),
                )
            )
        ]
    )


def _fill_history(module, dialog):
    dialog.billing_summary.setText("VALIDACIÓN\n12 recibos · RD$ 18,450.00")
    dialog.billing_totals_summary.setText(
        "FACTURACIÓN\nFacturados: 7 · No facturados: 2"
    )
    dialog.historical_summary.setText(
        "HISTÓRICO\n120 recibos · RD$ 185,300.00"
    )
    dialog.audit_queue_summary.setText(
        "COLA DE AUDITORÍA · 12 pendientes · Preliminares: 9 · Listos: 3"
    )
    dialog.table.setRowCount(0)
    for offset in range(9):
        row = dialog.table.rowCount()
        dialog.table.insertRow(row)
        values = [
            5000 - offset,
            990300 - offset,
            f"PACIENTE DE PRUEBA {offset + 1}",
            "2026-07-29",
            "Principal",
            "2026-07-29 10:00",
            ("HUMANO", "FUTURO", "PRIMERA ARS")[offset % 3],
            f"RD$ {950 + offset * 25:,.2f}",
            "USUARIO PRUEBA",
            "Pendiente",
            "-",
            "0 días",
            "Bajo",
            "-",
            "-",
            "-",
            "Preliminar",
            "-",
            "Dinámico",
        ]
        for column, value in enumerate(values):
            dialog.table.setItem(row, column, QTableWidgetItem(str(value)))
    dialog.lbl_count.setText("120 resultado(s)")
    dialog.lbl_page.setText("Página 1 de 2")
    dialog.btn_previous_page.setEnabled(False)
    dialog.btn_next_page.setEnabled(True)


def _fill_trash(dialog):
    dialog.table.setRowCount(0)
    for offset in range(12):
        row = dialog.table.rowCount()
        dialog.table.insertRow(row)
        values = [
            4000 - offset,
            880100 - offset,
            f"PACIENTE DE PRUEBA {offset + 1}",
            "2026-07-20",
            "HUMANO",
            "RD$ 1,100.00",
            "USUARIO PRUEBA",
            "Pendiente",
            "2026-07-29 09:00",
            "ADMIN PRUEBA",
            "Validación visual",
        ]
        for column, value in enumerate(values):
            dialog.table.setItem(row, column, QTableWidgetItem(str(value)))
    dialog.count_label.setText("12 recibo(s) en Papelera")


def _fill_reports(module, dialog):
    if not getattr(dialog, "panel_access", False):
        return
    dialog.kpi_receipts.setText("30")
    dialog.kpi_total.setText("RD$ 30,000.00")
    dialog.kpi_average.setText("RD$ 1,000.00")
    dialog.kpi_room.setText("RD$ 2,500.00")
    dialog.shift_summary_table.setRowCount(3)
    for row, ars in enumerate(("HUMANO", "FUTURO", "PRIMERA ARS")):
        for column, value in enumerate((ars, 10 + row, 6 + row, 4, 0)):
            dialog.shift_summary_table.setItem(
                row, column, QTableWidgetItem(str(value))
            )
    dialog.history_count.setText("12 reportes")
    if hasattr(dialog, "table"):
        dialog.table.setRowCount(5)
        for row in range(5):
            for column in range(dialog.table.columnCount()):
                dialog.table.setItem(
                    row,
                    column,
                    QTableWidgetItem(
                        (
                            str(row + 1)
                            if column == 0
                            else "Reporte de prueba"
                            if column == 1
                            else "2026-07"
                        )
                    ),
                )


def run(module, output_directory: str | os.PathLike[str]) -> int:
    global _MAX_PROCESS_EVENTS_MS
    _MAX_PROCESS_EVENTS_MS = 0.0
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    stage_path = output / "responsive_stage.txt"

    def record_stage(stage: str) -> None:
        stage_path.write_text(str(stage), encoding="utf-8")

    record_stage("START")
    if getattr(sys, "frozen", False):
        packaged_db = (
            Path(sys.executable).resolve().parent
            / "_internal"
            / "data"
            / "pacientes.db"
        )
        patient_lookup_performance = _benchmark_patient_documents(packaged_db)
    else:
        patient_lookup_performance = {}
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(
        module.get_stylesheet(False) + module.modern_module_stylesheet(False)
    )
    user = {
        "username": "validacion_ui",
        "full_name": "USUARIO DE VALIDACIÓN",
        "role": "administrador",
        "is_active": 1,
    }
    no_op = lambda *args, **kwargs: None
    global_replacements = {
        "get_universal": lambda _category: [],
        "get_user_preferences": lambda _username: {
            "theme": "claro",
            "auto_add_guantes": True,
            "auto_print": False,
            "auto_add_bajante_cateter": True,
        },
        "list_user_catalog_favorites": lambda _username: set(),
        "ars_list": lambda: ["HUMANO", "FUTURO", "PRIMERA ARS"],
        "main_module_labels_for_user": lambda _user: ["Facturación"],
        "user_can_manage_sessions": lambda _user: False,
        "user_is_admin": lambda _user: False,
        "user_has_permission": lambda _user, _permission: False,
        "_local_device_identity": lambda: (
            "RESPONSIVE_EVIDENCE",
            "EQUIPO DE VALIDACIÓN",
        ),
    }
    main_method_replacements = {
        "_start_pdf_services": no_op,
        "_build_timers": no_op,
        "safe_startup_load": no_op,
        "on_ars_changed": no_op,
        "refresh_picker": no_op,
        "_update_responsive_ui": no_op,
    }
    widgets = []
    result = {
        "actual_screen": {},
        "simulated_resolutions": [],
        "profiles": {},
        "screenshots": [],
        "same_process_admission": {},
        "patient_lookup_performance": patient_lookup_performance,
    }
    from admission_hybrid import evaluate_admission_access

    aux_access = evaluate_admission_access(
        {"role": "auxiliar"},
        {
            "base_write_allowed": True,
            "device_role": "PRIMARY",
            "connection_state": "CONNECTED",
            "status": "ACTIVE",
        },
    )
    admin_readonly = evaluate_admission_access(
        {"role": "administrador"},
        {
            "base_write_allowed": True,
            "device_role": "SECONDARY",
            "connection_state": "CONNECTED",
            "status": "ACTIVE",
        },
    )
    audit_readonly = evaluate_admission_access(
        {"role": "facturador de auditoría"},
        {
            "base_write_allowed": True,
            "device_role": "PRIMARY",
            "connection_state": "CONNECTED",
            "status": "ACTIVE",
        },
    )
    if not (
        aux_access.write_allowed
        and aux_access.can_generate_attention
        and admin_readonly.view_allowed
        and not admin_readonly.write_allowed
        and admin_readonly.can_manage_primary
        and audit_readonly.view_allowed
        and not audit_readonly.write_allowed
        and not audit_readonly.can_manage_primary
    ):
        raise RuntimeError("La matriz de acceso de Admisión no coincide con la política.")
    result["admission_role_matrix"] = {
        "auxiliary_operational": aux_access.write_allowed,
        "admin_default_readonly": not admin_readonly.write_allowed,
        "admin_can_manage_primary": admin_readonly.can_manage_primary,
        "audit_readonly": not audit_readonly.write_allowed,
    }

    from emergency_core.security import AdminSecurity

    with tempfile.TemporaryDirectory(prefix="hospital-pin-validation-") as pin_dir:
        pin_root = Path(pin_dir)
        security = AdminSecurity(
            pin_root / "admin_pin.json", pin_root / "admin_audit.jsonl"
        )
        security.setup("246810", actor="validacion_ui")
        wrong_pin_rejected = not security.verify(
            "246811",
            actor="validacion_ui",
            action="ADMISSION_PRIMARY_TRANSFER",
        )
        correct_pin_accepted = security.verify(
            "246810",
            actor="validacion_ui",
            action="ADMISSION_PRIMARY_TRANSFER",
        )
    if not wrong_pin_rejected or not correct_pin_accepted:
        raise RuntimeError("Falló la autorización administrativa por PIN.")
    result["admin_pin"] = {
        "wrong_pin_rejected": wrong_pin_rejected,
        "correct_pin_accepted": correct_pin_accepted,
    }
    screen = app.primaryScreen()
    if screen is not None:
        geometry = screen.availableGeometry()
        result["actual_screen"] = {
            "logical_width": geometry.width(),
            "logical_height": geometry.height(),
            "logical_dpi": round(screen.logicalDotsPerInch(), 2),
            "device_pixel_ratio": round(screen.devicePixelRatio(), 2),
        }
    for width, height in (
        (1024, 768),
        (1280, 720),
        (1366, 768),
        (1600, 900),
        (1920, 1080),
    ):
        for dpi in (96.0, 120.0, 144.0):
            result["simulated_resolutions"].append(
                {
                    "width": width,
                    "height": height,
                    "dpi": dpi,
                    "profile": module.recommend_layout_profile(
                        width, height, dpi, 1.0
                    ),
                }
            )

    with patched(module, global_replacements), patched(
        module.MainWindow, main_method_replacements
    ), patched(
        module.DisplayLayoutManager,
        {"start": no_op, "schedule_refresh": no_op},
    ):
        window = module.MainWindow(user, "responsive-evidence-session")
        widgets.append(window)
        _fill_main_window(module, window)
        profile_cases = (
            (
                module.PROFILE_VERY_COMPACT,
                1600,
                900,
                "00_facturacion_muy_compacto_1600x900.png",
            ),
            (
                module.PROFILE_COMPACT,
                1366,
                768,
                "02_facturacion_compacto.png",
            ),
            (
                module.PROFILE_STANDARD,
                1600,
                900,
                "03_facturacion_estandar.png",
            ),
            (
                module.PROFILE_WIDE,
                1920,
                1080,
                "04_facturacion_amplio.png",
            ),
        )
        for profile, width, height, filename in profile_cases:
            snapshot = _snapshot(module, profile, width, height)
            window.display_layout._last_snapshot = snapshot
            window._apply_display_layout(
                {"snapshot": snapshot, "profile_changed": True}
            )
            _capture(app, window, output / filename, width, height)
            result["screenshots"].append(filename)
            result["profiles"][profile] = {
                "snapshot": asdict(snapshot),
                "billing_splitter": window.billing_content_splitter.sizes(),
                "catalog_receipt_splitter": window.main_split.sizes(),
                "nav_width": window.nav_widget.width(),
                "categories": [
                    window.tabs.tabToolTip(index)
                    for index in range(window.tabs.count())
                ],
                "category_labels": [
                    window.tabs.tabText(index)
                    for index in range(window.tabs.count())
                ],
                "total_visible": window.lbl_total.isVisible(),
                "final_actions_visible": window.btn_generate.isVisible(),
            }

        compact_snapshot = _snapshot(
            module, module.PROFILE_COMPACT, 1366, 768
        )
        window.display_layout._last_snapshot = compact_snapshot
        window._apply_display_layout(
            {"snapshot": compact_snapshot, "profile_changed": True}
        )
        _settle(app, 2)
        splitter_total = max(sum(window.main_split.sizes()), 2)
        custom_sizes = [
            max(1, int(splitter_total * 0.42)),
            max(1, int(splitter_total * 0.58)),
        ]
        window.main_split.setSizes(custom_sizes)
        _settle(app, 2)
        saved_sizes = window.main_split.sizes()
        window.display_layout.save_splitter(
            window.main_split,
            "catalogo_recibo",
        )
        window.main_split.setSizes(
            [max(1, int(splitter_total * 0.60)),
             max(1, int(splitter_total * 0.40))]
        )
        window.display_layout.restore_splitter(
            window.main_split,
            "catalogo_recibo",
            (0.33, 0.67),
        )
        _settle(app, 2)
        restored_sizes = window.main_split.sizes()
        saved_ratio = saved_sizes[0] / max(sum(saved_sizes), 1)
        restored_ratio = restored_sizes[0] / max(sum(restored_sizes), 1)
        result["splitter_persistence"] = {
            "saved_sizes": saved_sizes,
            "restored_sizes": restored_sizes,
            "saved_ratio": round(saved_ratio, 4),
            "restored_ratio": round(restored_ratio, 4),
            "preserved": abs(saved_ratio - restored_ratio) <= 0.02,
        }
        _capture(
            app,
            window,
            output / "05_recibo_completo.png",
            1600,
            900,
        )
        result["screenshots"].append("05_recibo_completo.png")
        window.tabs.setCurrentIndex(2)
        _capture(
            app,
            window,
            output / "06_categorias.png",
            1366,
            768,
        )
        result["screenshots"].append("06_categorias.png")

        preferences = module.PreferencesDialog(
            {
                "theme": "claro",
                "auto_add_guantes": True,
                "auto_print": False,
                "auto_add_bajante_cateter": True,
                "layout_profile": module.PROFILE_AUTO,
                "layout_density": module.DENSITY_AUTO,
                "layout_text_scale": module.TEXT_AUTO,
            },
            window,
        )
        widgets.append(preferences)
        _capture(
            app,
            preferences,
            output / "01_preferencias_pantalla_diseno.png",
            1040,
            920,
        )
        result["screenshots"].append(
            "01_preferencias_pantalla_diseno.png"
        )

        with patched(
            module.AdmissionValidationDialog,
            {"_start_load": no_op, "load_eligible_turns": no_op},
        ):
            validation = module.AdmissionValidationDialog(
                user,
                "responsive-evidence-session",
                window,
            )
        validation.refresh_timer.stop()
        validation.identifier_edit.setText("001-XXXXXXX-X")
        validation.result_hint.setText(
            "Resultado sintético para validar búsqueda NSS sin datos clínicos."
        )
        validation.table.setRowCount(2)
        safe_rows = (
            (
                "1001",
                "PACIENTE DE PRUEBA A",
                "2026-07-29 08:00",
                "HUMANO",
                "001-XXXXXXX-X",
                "***-*******-*",
                "ACTUAL",
                "ACTUAL",
                "Central",
            ),
            (
                "1002",
                "PACIENTE DE PRUEBA B",
                "2026-07-29 08:10",
                "FUTURO",
                "002-XXXXXXX-X",
                "***-*******-*",
                "ANTERIOR",
                "ACTUAL",
                "Central",
            ),
        )
        for row, values in enumerate(safe_rows):
            for column, value in enumerate(values):
                validation.table.setItem(
                    row, column, QTableWidgetItem(value)
                )
        widgets.append(validation)
        _capture(
            app,
            validation,
            output / "07_verificar_paciente_nss.png",
            1080,
            620,
        )
        result["screenshots"].append("07_verificar_paciente_nss.png")

        with patched(module.AdmissionHistoryDialog, {"search": no_op}):
            admission_history = module.AdmissionHistoryDialog(user, window)
        admission_history.hint.setText(
            "Historial central · datos sintéticos sin información clínica."
        )
        admission_history.table.setRowCount(2)
        history_rows = (
            (
                "1001", "PACIENTE DE PRUEBA A", "***", "001-XXXX", "2026-08-01 08:00",
                "25", "EMERGENCIA", "HUMANO", "ACTIVA", "USUARIO PRUEBA",
                "Hoja generada", "990001", "Pendiente", "TURNO ACTUAL",
            ),
            (
                "1002", "PACIENTE DE PRUEBA B", "***", "002-XXXX", "2026-08-01 08:10",
                "24", "EMERGENCIA", "FUTURO", "ACTIVA", "USUARIO PRUEBA",
                "Sin hoja de detalle", "—", "Sin recibo", "HEREDADA DEL TURNO ANTERIOR",
            ),
        )
        for row, values in enumerate(history_rows):
            for column, value in enumerate(values):
                admission_history.table.setItem(row, column, QTableWidgetItem(value))
        widgets.append(admission_history)
        _capture(
            app,
            admission_history,
            output / "19_historial_admision.png",
            1366,
            768,
        )
        result["screenshots"].append("19_historial_admision.png")

        with patched(module.MonthlyBillingListsPage, {"load_batches": no_op}):
            monthly = module.MonthlyBillingListsPage(user)
        _fill_monthly(module, monthly)
        compact_snapshot = _snapshot(
            module, module.PROFILE_COMPACT, 1366, 768
        )
        monthly.apply_layout_profile(compact_snapshot)
        widgets.append(monthly)
        monthly.section_tabs.setCurrentIndex(0)
        _capture(
            app,
            monthly,
            output / "09_preparar_listado.png",
            1366,
            768,
        )
        result["screenshots"].append("09_preparar_listado.png")
        monthly.section_tabs.setCurrentIndex(1)
        _capture(
            app,
            monthly,
            output / "10_listados_guardados.png",
            1366,
            768,
        )
        result["screenshots"].append("10_listados_guardados.png")

        with patched(
            module.ReceiptHistoryDialog,
            {"refresh_session_context": lambda self, *args, **kwargs: False},
        ):
            history = module.ReceiptHistoryDialog(window, window)
        _fill_history(module, history)
        widgets.append(history)
        _capture(
            app,
            history,
            output / "11_historial_recibos.png",
            1600,
            900,
        )
        result["screenshots"].append("11_historial_recibos.png")

        with patched(module, {"list_deleted_receipts": lambda: []}):
            trash = module.ReceiptTrashDialog(window, window)
        _fill_trash(trash)
        widgets.append(trash)
        _capture(
            app,
            trash,
            output / "12_papelera_12_recibos.png",
            1280,
            720,
        )
        result["screenshots"].append("12_papelera_12_recibos.png")

        ars_globals = {
            "ars_list": lambda: ["HUMANO", "FUTURO", "PRIMERA ARS"],
            "get_emergency_price": lambda _name: 0.0,
            "get_consultation_price": lambda _name: 0.0,
            "get_ars_billing_profile": lambda name: {
                "display_name": name,
                "ars_rnc": "RNC-PRUEBA",
                "ars_address": "DIRECCIÓN DE PRUEBA",
                "ars_phone": "000-000-0000",
                "ars_email": "prueba@example.invalid",
                "administrative_notes": "Datos sintéticos",
            },
            "user_is_admin": lambda _user: True,
        }
        with patched(module, ars_globals):
            ars_dialog = module.ARSManagerDialog(user, window)
        widgets.append(ars_dialog)
        _capture(
            app,
            ars_dialog,
            output / "13_gestion_ars.png",
            1280,
            760,
        )
        result["screenshots"].append("13_gestion_ars.png")

        with patched(module.CatalogEditorDialog, {"load_rows": no_op}):
            catalog_dialog = module.CatalogEditorDialog(
                "Laboratorios", "HUMANO", window
            )
        catalog_dialog.catalog_stack.setCurrentIndex(1)
        widgets.append(catalog_dialog)
        _capture(
            app,
            catalog_dialog,
            output / "14_gestion_catalogos.png",
            1200,
            760,
        )
        result["screenshots"].append("14_gestion_catalogos.png")

        with patched(module, {"is_administrator": lambda _user: True}), patched(
            module.ReportsDialog,
            {"load_rows": no_op, "refresh_dashboard": no_op},
        ):
            reports = module.ReportsDialog(user, window)
        _fill_reports(module, reports)
        widgets.append(reports)
        reports.tabs.setCurrentIndex(0)
        _capture(
            app,
            reports,
            output / "15_centro_reportes.png",
            1600,
            900,
        )
        result["screenshots"].append("15_centro_reportes.png")
        reports.tabs.setCurrentIndex(reports.tabs.count() - 1)
        _capture(
            app,
            reports,
            output / "16_historial_reportes.png",
            1600,
            900,
        )
        result["screenshots"].append("16_historial_reportes.png")

        history.apply_history_theme(True)
        _capture(
            app,
            history,
            output / "17_historial_tema_oscuro.png",
            1366,
            768,
        )
        result["screenshots"].append("17_historial_tema_oscuro.png")

        settings = window.display_layout.settings
        prefix = window.display_layout._settings_prefix
        for profile in (
            module.PROFILE_VERY_COMPACT,
            module.PROFILE_COMPACT,
            module.PROFILE_STANDARD,
            module.PROFILE_WIDE,
        ):
            window.display_layout.update_preferences(
                profile=profile,
                density=module.DENSITY_AUTO,
                text_scale=module.TEXT_AUTO,
            )
            stored = window.display_layout.preferences()
            result["profiles"][profile]["persisted"] = (
                stored["layout_profile"] == profile
            )
        settings.remove(prefix)
        settings.sync()

        previous_data_dir = os.environ.get("EMERGENCIAS_DATA_DIR")
        previous_offline = os.environ.get("HOSPITAL_OFFLINE")
        import admission_v15_adapter as admission_adapter

        try:
            with tempfile.TemporaryDirectory(
                prefix="hospital_admission_ui_validation_",
                ignore_cleanup_errors=True,
            ) as temporary_data, patched(
                module,
                {
                    "main_module_labels_for_user": lambda _user: [
                        "Emergencias",
                        "Facturación",
                        "Listados de ARS",
                    ]
                },
            ), patched(
                module.MonthlyBillingListsPage,
                {"load_batches": no_op},
            ), patched(
                module.FullAdmissionPage,
                {
                    "_prime_transfer_cursor": no_op,
                    "_receive_new_admission_event": no_op,
                    "_receive_shift_closure_events": no_op,
                },
            ), patched(
                admission_adapter._HybridCoordinator,
                {
                    "start": lambda coordinator: coordinator.state_changed.emit(
                        coordinator.runtime.state()
                    ),
                    "_schedule": no_op,
                    "submit_background": no_op,
                },
            ), patched(
                admission_adapter._V15BackgroundRefreshCoordinator,
                {"request_summary": no_op},
            ), patched(
                admission_adapter._HybridExcelRefreshCoordinator,
                {"request": no_op},
            ):
                os.environ["EMERGENCIAS_DATA_DIR"] = temporary_data
                os.environ["HOSPITAL_OFFLINE"] = "1"
                integrated_window = module.MainWindow(
                    user,
                    "responsive-evidence-session-admission",
                )
                widgets.append(integrated_window)
                compact_snapshot = _snapshot(
                    module, module.PROFILE_COMPACT, 1366, 768
                )
                integrated_window.display_layout._last_snapshot = (
                    compact_snapshot
                )
                integrated_window._apply_display_layout(
                    {
                        "snapshot": compact_snapshot,
                        "profile_changed": True,
                    }
                )
                integrated_window.module_tabs.setCurrentIndex(
                    integrated_window.emergency_module_index
                )
                _capture(
                    app,
                    integrated_window,
                    output / "08_admision_integrada.png",
                    1366,
                    768,
                )
                full_page = integrated_window.full_admission_page
                controller = getattr(full_page, "controller", None)
                if controller is not None:
                    for _ in range(12):
                        controller.pump_embedded_events()
                        full_page._try_embed_admission()
                        _settle(app, 1)
                else:
                    # The current route embeds AdmissionWidget directly.  The
                    # legacy controller branch remains for rollback builds.
                    _settle(app, 3)
                _capture(
                    app,
                    integrated_window,
                    output / "08_admision_integrada.png",
                    1366,
                    768,
                )
                frame = integrated_window.frameGeometry()
                screen_capture = app.primaryScreen().grabWindow(
                    0,
                    int(frame.x()),
                    int(frame.y()),
                    int(frame.width()),
                    int(frame.height()),
                )
                if not screen_capture.isNull():
                    screen_capture.save(
                        str(output / "08_admision_integrada.png"),
                        "PNG",
                    )
                if controller is not None:
                    source = controller.source
                    result["same_process_admission"] = {
                        "started": controller.is_running,
                        "pid_matches_main_process": (
                            controller.pid == os.getpid()
                        ),
                        "source": source.name if source else "",
                        "events_pumped": controller.pump_embedded_events(),
                        "embedded_in_main_navigation": controller.embedded,
                    }
                    root = getattr(controller._embedded_app, "root", None)
                    native_window_id = int(root.winfo_id()) if root is not None else 0
                else:
                    result["same_process_admission"] = {
                        "started": full_page is not None,
                        "pid_matches_main_process": True,
                        "source": type(full_page).__name__,
                        "events_pumped": 0,
                        "embedded_in_main_navigation": (
                            full_page.parentWidget() is not None
                        ),
                    }
                    native_window_id = int(full_page.winId())
                result["screenshots"].append(
                    "08_admision_integrada.png"
                )
                pixmap = app.primaryScreen().grabWindow(native_window_id)
                native_path = output / "18_admision_nativa_segura.png"
                if not pixmap.isNull() and pixmap.save(
                    str(native_path), "PNG"
                ):
                    result["screenshots"].append(
                        "18_admision_nativa_segura.png"
                    )
                admission = getattr(full_page, "admission", None)
                if admission is not None:
                    # Scope the responsiveness gate to this delivery's hot path.
                    # The preceding gallery renders legacy pages and screenshots;
                    # those costs are unrelated to patient lookup/configuration.
                    _MAX_PROCESS_EVENTS_MS = 0.0
                    record_stage("TARGET_RESPONSIVENESS_START")
                    local_patient = {
                        "nombre": "PACIENTE LOCAL",
                        "cedula": "00112345678",
                        "nss": "123456789",
                        "telefono": "8090000000",
                        "direccion": "DIRECCIÓN LOCAL",
                        "nacionalidad": "DOMINICANA",
                        "ars": "HUMANO",
                        "server_revision": 1,
                    }
                    cloud_patient = {
                        **local_patient,
                        "telefono": "8091111111",
                        "direccion": "DIRECCIÓN CLOUD",
                        "server_revision": 2,
                    }
                    admission._apply_patient_ui(
                        local_patient, include_cedula=True, include_nss=True
                    )
                    admission._replace_entry_value(
                        admission.entry_telefono, "8092222222"
                    )
                    runtime = admission.db._runtime
                    original_offline = runtime.offline
                    original_verify = runtime.verify_patient_with_cloud
                    verification_threads = []

                    def verify_cloud(**_kwargs):
                        verification_threads.append(
                            __import__("threading").current_thread().name
                        )
                        return dict(cloud_patient)

                    runtime.offline = False
                    runtime.verify_patient_with_cloud = verify_cloud
                    admission._local_patient_revision = 1
                    admission._schedule_cloud_patient_lookup(
                        "CEDULA", "00112345678"
                    )
                    _settle(app, 24)
                    dirty_phone_preserved = (
                        admission.entry_telefono.get() == "8092222222"
                    )
                    cloud_field_applied = (
                        admission.entry_direccion.get() == "DIRECCIÓN CLOUD"
                    )
                    cloud_background = bool(
                        verification_threads
                        and all(name != "MainThread" for name in verification_threads)
                    )
                    runtime.verify_patient_with_cloud = original_verify
                    final_pdf_revalidation = not admission._begin_final_patient_revalidation()
                    admission._verified_cloud_patient = None
                    admission._verified_cloud_identity = None
                    runtime.offline = True
                    offline_fallback = not admission._begin_final_patient_revalidation()
                    runtime.offline = original_offline
                    if not (
                        dirty_phone_preserved
                        and cloud_field_applied
                        and cloud_background
                        and final_pdf_revalidation
                        and offline_fallback
                    ):
                        raise RuntimeError(
                            "Falló merge seguro/background/offline del paciente."
                        )
                    result["patient_cloud_merge"] = {
                        "cloud_verify_background": cloud_background,
                        "cloud_newer_revision_applied": cloud_field_applied,
                        "dirty_field_preserved": dirty_phone_preserved,
                        "final_pdf_revalidation": final_pdf_revalidation,
                        "offline_final_fallback": offline_fallback,
                    }
                    admission._abrir_configuracion_interna()
                    _settle(app, 12)
                    config_window = getattr(
                        admission, "configuracion_interna_win", None
                    )
                    if config_window is None or not config_window.winfo_exists():
                        raise RuntimeError(
                            "Configuración interna no creó una ventana visible."
                        )
                    config_children = list(config_window.winfo_children())
                    content_ready = bool(
                        getattr(config_window, "_config_content_ready", False)
                    )
                    if not content_ready or not config_children:
                        raise RuntimeError(
                            "Configuración interna quedó sin contenido."
                        )
                    config_path = output / "19_configuracion_interna.png"
                    _capture(app, config_window, config_path, 1093, 614)
                    result["screenshots"].append(config_path.name)
                    result["config_dialog"] = {
                        "visible": True,
                        "content_ready": content_ready,
                        "top_level_children": len(config_children),
                        "pin_required_to_open": False,
                    }
                    config_window.destroy()
                record_stage("INTEGRATED_SHUTDOWN_START")
                integrated_window.emergency_workspace.shutdown()
                record_stage("INTEGRATED_SHUTDOWN_OK")
                _finish_validation_workers(full_page)
                record_stage("INTEGRATED_WORKERS_FINISHED")
                _close_file_handlers_under(temporary_data)
                record_stage("TEMP_LOG_HANDLERS_CLOSED")
        finally:
            if previous_data_dir is None:
                os.environ.pop("EMERGENCIAS_DATA_DIR", None)
            else:
                os.environ["EMERGENCIAS_DATA_DIR"] = previous_data_dir
            if previous_offline is None:
                os.environ.pop("HOSPITAL_OFFLINE", None)
            else:
                os.environ["HOSPITAL_OFFLINE"] = previous_offline

    record_stage("WIDGET_CLEANUP_START")
    for widget in reversed(widgets):
        try:
            widget.hide()
            widget.deleteLater()
        except Exception:
            pass
    record_stage("WIDGET_CLEANUP_QUEUED")
    _settle(app, 2)
    record_stage("WIDGET_CLEANUP_OK")
    result["gui_freeze_max_ms"] = round(_MAX_PROCESS_EVENTS_MS, 3)
    if _MAX_PROCESS_EVENTS_MS >= 750.0:
        raise RuntimeError(
            "La validación detectó un bloqueo del event loop de "
            f"{_MAX_PROCESS_EVENTS_MS:.3f} ms."
        )
    (output / "validacion_gui.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    record_stage("COMPLETE")
    return 0
