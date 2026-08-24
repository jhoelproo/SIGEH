"""Admisión V15.

Importar este paquete no crea QApplication, no construye ventanas y no inicia
ningún ciclo de eventos. El entrypoint autónomo permanece en
``facturacion_tabs_pyside6.py``.
"""

__all__ = ("AdmissionContext", "AdmissionWidget", "AdmissionStandaloneWindow")


def __getattr__(name):
    if name == "AdmissionContext":
        from .admission_context import AdmissionContext

        return AdmissionContext
    if name in ("AdmissionWidget", "AdmissionStandaloneWindow"):
        from .admission_widget import AdmissionStandaloneWindow, AdmissionWidget

        return {
            "AdmissionWidget": AdmissionWidget,
            "AdmissionStandaloneWindow": AdmissionStandaloneWindow,
        }[name]
    raise AttributeError(name)
