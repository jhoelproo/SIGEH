"""Shared GUI orchestration for an administrative PRIMARY transfer."""

from __future__ import annotations

from typing import Any, Callable


def request_primary_transfer(
    app: Any,
    *,
    parent: Any,
    messagebox: Any,
    simpledialog: Any,
    logger: Any,
    is_admin: bool,
) -> None:
    """Run the target-based transfer flow without changing operational identity."""
    _PrimaryTransferCoordinator(
        app=app,
        parent=parent,
        messagebox=messagebox,
        simpledialog=simpledialog,
        logger=logger,
        is_admin=is_admin,
    ).start()


class _PrimaryTransferCoordinator:
    def __init__(
        self,
        *,
        app: Any,
        parent: Any,
        messagebox: Any,
        simpledialog: Any,
        logger: Any,
        is_admin: bool,
    ) -> None:
        self.app = app
        self.parent = parent
        self.messagebox = messagebox
        self.simpledialog = simpledialog
        self.logger = logger
        self.is_admin = is_admin
        self.runtime = getattr(app.db, "_runtime", None)
        self.before: dict[str, Any] = {}
        self.expected_revision = 0
        self.target: dict[str, Any] = {}
        self.reason = ""

    def start(self) -> None:
        if not self.is_admin:
            self._warn(
                "Transferir acceso principal",
                "Solo un Administrador puede realizar esta operación.",
            )
            return
        if self.runtime is None or bool(getattr(self.runtime, "offline", False)):
            self._warn(
                "Transferir acceso principal",
                "Se requiere conexión central para transferir PRIMARY.",
            )
            return
        if self.app._primary_transfer_in_progress:
            return
        self.before = dict(self.runtime.state() or {})
        self.expected_revision = int(
            self.before.get("operational_revision") or 0
        )
        if (
            not self.before.get("operational_session_id")
            or self.expected_revision <= 0
        ):
            self._warn(
                "Transferir acceso principal",
                "El estado operacional aún no está confirmado. Actualice y reintente.",
            )
            return
        self.app._primary_transfer_in_progress = True
        self.app._refresh_actions_menu_state()
        self.app._ejecutar_en_segundo_plano(
            "Consultando estaciones conectadas...",
            self._load_candidates,
            al_terminar=self._choose_target,
            al_error=self._failed,
        )

    def _release_progress(self) -> None:
        self.app._primary_transfer_in_progress = False
        self.app._refresh_actions_menu_state()

    def _load_candidates(self) -> list[dict[str, Any]]:
        return self.runtime.list_primary_transfer_candidates()

    def _choose_target(self, candidates: list[dict[str, Any]]) -> None:
        primary_device = str(self.before.get("primary_device_id") or "")
        healthy_devices = {
            str(item.get("device_id") or "") for item in candidates
        }
        if primary_device not in healthy_devices:
            self._release_progress()
            self._warn(
                "Transferir acceso principal",
                "La estación PRIMARY actual debe permanecer conectada para "
                "realizar esta transferencia.",
            )
            return
        targets = [
            item
            for item in candidates
            if str(item.get("device_id") or "") != primary_device
            and str(item.get("station_role") or "").upper() == "SECONDARY"
        ]
        if not targets:
            self._release_progress()
            self.messagebox.showinfo(
                "Transferir acceso principal",
                "No existe otra estación SECONDARY conectada y saludable.",
                parent=self.parent,
            )
            return
        selected = _select_target(
            targets,
            parent=self.parent,
            simpledialog=self.simpledialog,
            release_progress=self._release_progress,
        )
        if selected is None:
            return
        reason = self._request_reason()
        if not reason:
            return
        if not _confirm_target(
            primary_device,
            selected,
            parent=self.parent,
            messagebox=self.messagebox,
        ):
            self._release_progress()
            return
        self.target = selected
        self.reason = reason
        self.app._ejecutar_en_segundo_plano(
            "Transfiriendo acceso PRIMARY...",
            self._transfer,
            al_terminar=self._finish,
            al_error=self._failed,
        )

    def _request_reason(self) -> str:
        reason = self.simpledialog.askstring(
            "Motivo de la transferencia",
            "Indique el motivo de la transferencia de PRIMARY:",
            parent=self.parent,
        )
        if reason is None:
            self._release_progress()
            return ""
        reason = reason.strip()
        if not reason:
            self._release_progress()
            self._warn("Motivo requerido", "Debe indicar un motivo breve.")
        return reason

    def _transfer(self) -> Any:
        return self.runtime.force_transfer_admission_primary(
            target_device_id=str(self.target.get("device_id") or ""),
            target_login_session_id=str(
                self.target.get("login_session_id") or ""
            ),
            expected_operational_revision=self.expected_revision,
            reason=self.reason,
        )

    def _finish(self, changed: Any) -> None:
        self._release_progress()
        self.app._set_turn_change_controls_enabled(True)
        local_is_primary = str(changed.primary_device_id) == str(
            getattr(self.runtime, "device_id", "") or ""
        )
        self.app.set_status(
            "Conectado · Principal · Sincronizado"
            if local_is_primary
            else "Conectado · Secundaria · Sincronizado",
            "ok",
        )
        _refresh_primary_panel(self.app)
        if not _preserves_operational_identity(self.before, changed):
            self.logger.critical(
                "PRIMARY_FORCE_TRANSFER_INVARIANT_FAILED turn_before=%s "
                "turn_after=%s generation_before=%s generation_after=%s",
                self.before.get("turn_id"),
                changed.turn_id,
                self.before.get("generation"),
                changed.generation,
            )
            self.messagebox.showerror(
                "Transferir acceso principal",
                "La transferencia no conservó el estado operativo esperado.",
                parent=self.parent,
            )
            return
        self.messagebox.showinfo(
            "Transferencia completada",
            f"La estación {changed.primary_device_id} es ahora PRIMARY. "
            "El turno, la generación, el representante y el conteo "
            "permanecen sin cambios.",
            parent=self.parent,
        )

    def _failed(self, exc: Exception) -> None:
        self._release_progress()
        self.app.set_status("No se pudo transferir PRIMARY", "error")
        _refresh_primary_panel(self.app)
        self.logger.error(
            "Falló la transferencia administrativa de PRIMARY",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        self.messagebox.showerror(
            "Transferir acceso principal", str(exc), parent=self.parent
        )

    def _warn(self, title: str, message: str) -> None:
        self.messagebox.showwarning(title, message, parent=self.parent)


def _select_target(
    targets: list[dict[str, Any]],
    *,
    parent: Any,
    simpledialog: Any,
    release_progress: Callable[[], None],
) -> dict[str, Any] | None:
    if len(targets) == 1:
        return targets[0]
    options = "\n".join(
        f"{index}. {item.get('device_name') or item.get('device_id')} · "
        f"{str(item.get('station_role') or '').upper()} · "
        f"{item.get('login_username') or 'Usuario conectado'} · "
        f"actividad {item.get('last_seen') or 'no disponible'} · "
        f"salud {item.get('health_status') or 'NO VERIFICADA'} · "
        f"sync {item.get('sync_status') or 'NO REPORTADO'}"
        for index, item in enumerate(targets, start=1)
    )
    selected = simpledialog.askinteger(
        "Seleccionar estación destino",
        "Seleccione la estación que recibirá PRIMARY:\n\n" + options,
        parent=parent,
        minvalue=1,
        maxvalue=len(targets),
    )
    if selected is None:
        release_progress()
        return None
    return targets[selected - 1]


def _confirm_target(
    primary_device: str,
    target: dict[str, Any],
    *,
    parent: Any,
    messagebox: Any,
) -> bool:
    target_label = str(target.get("device_name") or target.get("device_id"))
    target_details = (
        f"Rol: {str(target.get('station_role') or '').upper()}\n"
        f"Usuario: {target.get('login_username') or 'No disponible'}\n"
        f"Última actividad: {target.get('last_seen') or 'No disponible'}\n"
        f"Estado: {target.get('health_status') or 'NO VERIFICADO'}\n"
        f"Sincronización: {target.get('sync_status') or 'NO REPORTADA'}"
    )
    return bool(
        messagebox.askyesno(
            "Transferir acceso principal",
            f"PRIMARY pasará de {primary_device} a {target_label}.\n\n"
            f"{target_details}\n\n"
            "Ambas sesiones permanecerán conectadas. El turno, la generación, "
            "el representante y el conteo NO cambiarán.\n\n¿Desea continuar?",
            parent=parent,
        )
    )


def _refresh_primary_panel(app: Any) -> None:
    refresh_panel = getattr(app, "_refresh_primary_config_panel", None)
    if callable(refresh_panel):
        refresh_panel()


def _preserves_operational_identity(before: dict[str, Any], changed: Any) -> bool:
    return (
        changed.turn_id == before.get("turn_id")
        and changed.generation == before.get("generation")
        and str(changed.active_user_id or "")
        == str(before.get("active_user_id") or "")
    )
