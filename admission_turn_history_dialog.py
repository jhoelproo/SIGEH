"""Search persisted Admission shifts without loading their patient documents."""

from PySide6.QtCore import QDate, Qt
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDialog,
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
        self.rows = []
        self.loading = False
        self.setWindowTitle("Historial de turnos de Admisión")
        self.resize(940, 560)
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.start = QDateEdit(QDate.currentDate().addMonths(-1))
        self.end = QDateEdit(QDate.currentDate())
        for field in (self.start, self.end):
            field.setCalendarPopup(True)
            field.setDisplayFormat("dd/MM/yyyy")
        self.user = QLineEdit()
        self.user.setPlaceholderText("Usuario o nombre del personal de Admisión")
        self.search = QPushButton("Buscar")
        for widget in (
            QLabel("Desde"),
            self.start,
            QLabel("Hasta"),
            self.end,
            self.user,
            self.search,
        ):
            controls.addWidget(widget)
        layout.addLayout(controls)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Turno", "Inicio", "Fin", "Usuario de admisión", "Pacientes"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.status = QLabel("Busca un turno por fecha y usuario.")
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.next = QPushButton("Siguiente")
        self.use = QPushButton("Usar turno en reporte")
        self.next.setEnabled(False)
        self.use.setEnabled(False)
        close = QPushButton("Cerrar")
        for button in (self.next, self.use, close):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.search.clicked.connect(self.restart)
        self.user.returnPressed.connect(self.restart)
        self.next.clicked.connect(self.next_page)
        self.use.clicked.connect(self.choose)
        self.table.itemDoubleClicked.connect(self.choose)
        close.clicked.connect(self.reject)
        self.user.textChanged.connect(self.invalidate_page)
        self.start.dateChanged.connect(self.invalidate_page)
        self.end.dateChanged.connect(self.invalidate_page)
        self.setAttribute(Qt.WA_DeleteOnClose)

    def invalidate_page(self, *_):
        self.cursor = self.next_cursor = None
        self.rows = []
        self.table.setRowCount(0)
        self.next.setEnabled(False)
        self.use.setEnabled(False)

    def set_loading(self, loading):
        self.loading = loading
        for widget in (self.search, self.user, self.start, self.end):
            widget.setEnabled(not loading)

    def restart(self):
        if self.loading:
            return
        self.cursor = None
        self.load_page()

    def next_page(self):
        if self.loading or self.next_cursor is None:
            return
        self.cursor = self.next_cursor
        self.load_page()

    def load_page(self):
        first, last = self.start.date().toPython(), self.end.date().toPython()
        username, cursor = self.user.text().strip(), self.cursor
        self.set_loading(True)
        self.rows = []
        self.table.setRowCount(0)
        self.next.setEnabled(False)
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
        self.use.setEnabled(bool(self.rows))
        self.status.setText(
            f"{len(self.rows)} turnos cargados. Solo se incluyen pacientes admitidos por el usuario indicado."
        )
        if self.rows:
            self.table.selectRow(0)

    def failed(self, error):
        if not isValid(self):
            return
        self.set_loading(False)
        self.status.setText(f"No se pudo consultar el historial: {error}")

    def choose(self, *_):
        if self.loading:
            return
        index = self.table.currentRow()
        if 0 <= index < len(self.rows):
            self.selected(dict(self.rows[index]))
            self.accept()
