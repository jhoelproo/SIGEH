from types import MethodType
from unittest.mock import Mock

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from ADMISION_PYSIDE6_V15 import facturacion_tabs_pyside6 as v15


def test_patient_search_receives_typing_after_dialog_activation():
    application = QApplication.instance() or QApplication([])
    owner = next(
        value
        for value in vars(v15).values()
        if isinstance(value, type) and "_abrir_edicion_paciente" in vars(value)
    )
    root = v15.tk.Tk()
    controller = Mock(root=root, edicion_paciente_win=None)
    controller._ventana_activa.return_value = False
    controller._paleta_visual_actual.return_value = {"root": "#ffffff"}
    controller._crear_toplevel_estable = MethodType(
        owner._crear_toplevel_estable, controller
    )
    owner._abrir_edicion_paciente(controller)
    window = controller.edicion_paciente_win
    search = window.findChildren(QLineEdit)[0]
    QTest.qWait(120)
    assert application.focusWidget() is search
    QTest.keyClicks(application.focusWidget(), "PRUEBA")
    assert search.text() == "PRUEBA"
    window.close()
    root.close()
