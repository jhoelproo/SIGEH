from pathlib import Path


def test_history_pagination_buttons_have_visible_fixed_geometry_and_states():
    source = (
        Path(__file__).resolve().parents[1] / "CALCULOS_QT.py"
    ).read_text(encoding="utf-8")
    assert 'setObjectName("HistoryPaginationButton")' in source
    assert "setMinimumSize(104, 34)" in source
    assert "QPushButton#HistoryPaginationButton:hover:enabled" in source
    assert "QPushButton#HistoryPaginationButton:pressed:enabled" in source
    assert "QPushButton#HistoryPaginationButton:disabled" in source
    assert 'QPushButton("Anterior")' in source
    assert 'QPushButton("Siguiente")' in source
    assert "QStyle.SP_ArrowLeft" in source
    assert "QStyle.SP_ArrowRight" in source
