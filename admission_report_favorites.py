"""Per-user report filter views stored in the existing local Qt preferences."""

import hashlib
import json

from PySide6.QtCore import QSettings

from admission_report_tools import normalize_view


MAX_SAVED_VIEWS = 40


class ReportFavoriteStore:
    def __init__(self, username, settings=None):
        username = str(username or "").strip()
        if not username:
            raise ValueError("La sesión no tiene un usuario para guardar vistas.")
        self.settings = (
            settings if settings is not None else QSettings("SIGEH", "ReportesAdmision")
        )
        self.key = hashlib.sha256(username.encode("utf8")).hexdigest() + "/views"

    def list(self):
        raw = self.settings.value(self.key, "[]")
        try:
            values = json.loads(str(raw))
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "No se pudieron leer las vistas guardadas de este usuario."
            ) from exc
        if not isinstance(values, list):
            raise ValueError("El formato de las vistas guardadas no es válido.")
        return sorted(
            (normalize_view(value) for value in values),
            key=lambda value: value["name"].casefold(),
        )

    def save(self, values):
        view = normalize_view(values)
        views = {item["name"]: item for item in self.list()}
        views[view["name"]] = view
        if len(views) > MAX_SAVED_VIEWS:
            raise ValueError(
                "Puedes guardar hasta 40 vistas. Elimina una para añadir otra."
            )
        self._write(list(views.values()))
        return view

    def remove(self, name):
        views = [view for view in self.list() if view["name"] != name]
        self._write(views)

    def _write(self, views):
        self.settings.setValue(self.key, json.dumps(views, ensure_ascii=False))
        self.settings.sync()
        if self.settings.status() != QSettings.NoError:
            raise OSError("No se pudieron guardar las vistas en esta computadora.")
