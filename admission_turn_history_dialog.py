"""Search persisted Admission shifts without loading their patient documents."""

from PySide6.QtCore import QDate, Qt
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from admission_statistical_reports import coerce_hospital_datetime
from admission_turn_history import PAGE_SIZE, turn_cursor


class AdmissionTurnHistoryDialog(QDialog):
    def __init__(self, controller, selected, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.selected = selected
        self.cursor = None
        self.next_cursor = None
        self.page_cursors = [None]
        self.page_index = 0
        self.rows = []
        self.loading = False
        self.setWindowTitle("Historial de turnos de Admisión")
        self.resize(940, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        title = QLabel("Historial de turnos de Admisión")
        title.setObjectName("TurnHistoryTitle")
        title.setStyleSheet("font-size: 18pt; font-weight: 800;")
        intro = QLabel(
            "Busca el turno por fecha o por la persona que realizó las admisiones. "
            "Selecciona una fila para usar sus pacientes en el reporte estadístico."
        )
        intro.setWordWrap(True)
        intro.setObjectName("TurnHistoryIntro")
        intro.setStyleSheet("padding: 10px; border-radius: 7px;")
        layout.addWidget(title)
        layout.addWidget(intro)
        filters = QGroupBox("1. Buscar turnos")
        controls = QGridLayout(filters)
        self.start = QDateEdit(QDate.currentDate().addMonths(-1))
        self.end = QDateEdit(QDate.currentDate())
        for field in (self.start, self.end):
            field.setCalendarPopup(True)
            field.setDisplayFormat("dd/MM/yyyy")
        self.user = QLineEdit()
        self.user.setPlaceholderText("Usuario o nombre del personal de Admisión")
        self.search = QPushButton("Buscar turnos")
        self.clear = QPushButton("Restablecer")
        controls.addWidget(QLabel("Desde"), 0, 0)
        controls.addWidget(self.start, 1, 0)
        controls.addWidget(QLabel("Hasta"), 0, 1)
        controls.addWidget(self.end, 1, 1)
        controls.addWidget(QLabel("Usuario de Admisión (opcional)"), 0, 2)
        controls.addWidget(self.user, 1, 2)
        controls.addWidget(self.search, 1, 3)
        controls.addWidget(self.clear, 1, 4)
        controls.setColumnStretch(2, 1)
        layout.addWidget(filters)
        results = QGroupBox("2. Seleccionar un turno")
        results_layout = QVBoxLayout(results)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["N.º turno", "Inicio real", "Fin real", "Usuario de Admisión", "Pacientes"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        results_layout.addWidget(self.table)
        self.status = QLabel("Busca un turno por fecha y usuario.")
        self.status.setWordWrap(True)
        self.selection = QLabel("Ningún turno seleccionado.")
        self.selection.setWordWrap(True)
        self.selection.setStyleSheet("font-weight: 700; padding: 8px;")
        results_layout.addWidget(self.status)
        results_layout.addWidget(self.selection)
        layout.addWidget(results, 1)
        actions = QHBoxLayout()
        self.previous = QPushButton("Anterior")
        self.next = QPushButton("Siguiente")
        self.use = QPushButton("Usar turno seleccionado")
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.use.setEnabled(False)
        close = QPushButton("Cerrar")
        actions.addWidget(self.previous)
        for button in (self.next, self.use):
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.search.clicked.connect(self.restart)
        self.clear.clicked.connect(self.reset_filters)
        self.user.returnPressed.connect(self.restart)
        self.previous.clicked.connect(self.previous_page)
        self.next.clicked.connect(self.next_page)
        self.use.clicked.connect(self.choose)
        self.table.itemDoubleClicked.connect(self.choose)
        self.table.itemSelectionChanged.connect(self.update_selection)
        close.clicked.connect(self.reject)
        self.user.textChanged.connect(self.invalidate_page)
        self.start.dateChanged.connect(self.invalidate_page)
        self.end.dateChanged.connect(self.invalidate_page)
        self.setAttribute(Qt.WA_DeleteOnClose)

    def invalidate_page(self, *_):
        self.cursor = self.next_cursor = None
        self.page_cursors = [None]
        self.page_index = 0
        self.rows = []
        self.table.setRowCount(0)
        self.next.setEnabled(False)
        self.previous.setEnabled(False)
        self.use.setEnabled(False)
        self.selection.setText("Ningún turno seleccionado.")

    def set_loading(self, loading):
        self.loading = loading
        for widget in (self.search, self.clear, self.user, self.start, self.end):
            widget.setEnabled(not loading)

    def restart(self):
        if self.loading:
            return
        self.page_cursors = [None]
        self.page_index = 0
        self.cursor = None
        self.load_page()

    def reset_filters(self):
        if self.loading:
            return
        self.start.setDate(QDate.currentDate().addMonths(-1))
        self.end.setDate(QDate.currentDate())
        self.user.clear()
        self.status.setText("Filtros restablecidos. Pulsa Buscar turnos.")

    def next_page(self):
        if self.loading or self.next_cursor is None:
            return
        self.page_cursors = self.page_cursors[: self.page_index + 1]
        self.page_cursors.append(self.next_cursor)
        self.page_index += 1
        self.cursor = self.page_cursors[self.page_index]
        self.load_page()

    def previous_page(self):
        if self.loading or self.page_index == 0:
            return
        self.page_index -= 1
        self.cursor = self.page_cursors[self.page_index]
        self.load_page()

    def load_page(self):
        first, last = self.start.date().toPython(), self.end.date().toPython()
        username, cursor = self.user.text().strip(), self.cursor
        self.set_loading(True)
        self.rows = []
        self.table.setRowCount(0)
        self.next.setEnabled(False)
        self.previous.setEnabled(False)
        self.use.setEnabled(False)
        self.status.setText("Buscando turnos…")
        self.controller._ejecutar_en_segundo_plano(
            "Buscando turnos…",
            lambda: self.controller.db.search_admission_turns(
                first, last, username, cursor
            ),
            self.loaded,
            self.failed,
        )

    def loaded(self, rows):
        if not isValid(self):
            return
        self.rows = rows[:PAGE_SIZE]
        self.next_cursor = turn_cursor(self.rows[-1]) if len(rows) > PAGE_SIZE else None
        self.table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            stamps = [
                coerce_hospital_datetime(row.get(key))
                for key in ("started_at", "ends_at")
            ]
            values = [
                row["turn_id"],
                *(
                    stamp.strftime("%d/%m/%Y %H:%M") if stamp else "—"
                    for stamp in stamps
                ),
                row["display_name"],
                row["patient_count"],
            ]
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()
        self.set_loading(False)
        self.next.setEnabled(self.next_cursor is not None)
        self.previous.setEnabled(self.page_index > 0)
        self.use.setEnabled(bool(self.rows))
        self.status.setText(
            f"Página {self.page_index + 1} · {len(self.rows)} turno(s). "
            "El conteo incluye únicamente pacientes admitidos por el usuario indicado."
        )
        if self.rows:
            self.table.selectRow(0)

    def failed(self, error):
        if not isValid(self):
            return
        self.set_loading(False)
        self.previous.setEnabled(self.page_index > 0)
        self.status.setText(f"No se pudo consultar el historial: {error}")

    def update_selection(self):
        index = self.table.currentRow()
        if not (0 <= index < len(self.rows)):
            self.selection.setText("Ningún turno seleccionado.")
            self.use.setEnabled(False)
            return
        row = self.rows[index]
        self.selection.setText(
            f"Seleccionado: turno #{row['turn_id']} · {row['display_name']} · "
            f"{row['patient_count']} paciente(s)."
        )
        self.use.setEnabled(not self.loading)

    def choose(self, *_):
        if self.loading:
            return
        index = self.table.currentRow()
        if 0 <= index < len(self.rows):
            self.selected(dict(self.rows[index]))
            self.accept()
