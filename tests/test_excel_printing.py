import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from excel_printing import print_patient_workbook


@pytest.mark.parametrize("failure", [None, "open", "print", "close", "quit"])
def test_prints_patient_sheet_and_releases_private_excel(
    monkeypatch, tmp_path, failure
):
    com = Mock()
    application = Mock()
    workbook = application.Workbooks.Open.return_value
    targets = {
        "open": application.Workbooks.Open,
        "print": workbook.Worksheets.return_value.PrintOut,
        "close": workbook.Close,
        "quit": application.Quit,
    }
    if failure:
        targets[failure].side_effect = RuntimeError("SYNTHETIC PRINT FAILURE")
    dispatch = Mock(return_value=application)
    monkeypatch.setitem(sys.modules, "pythoncom", com)
    monkeypatch.setitem(
        sys.modules, "win32com.client", SimpleNamespace(DispatchEx=dispatch)
    )
    if failure:
        with pytest.raises(RuntimeError):
            print_patient_workbook(tmp_path / "list.xlsx", copies=2)
    else:
        print_patient_workbook(tmp_path / "list.xlsx", copies=2)
        workbook.Worksheets.assert_called_once_with(1)
        workbook.Worksheets.return_value.PrintOut.assert_called_once_with(
            Copies=2, Collate=True
        )
    dispatch.assert_called_once_with("Excel.Application")
    application.Quit.assert_called_once()
    com.CoUninitialize.assert_called_once()


def test_failed_excel_start_still_releases_com(monkeypatch, tmp_path):
    com = Mock()
    monkeypatch.setitem(sys.modules, "pythoncom", com)
    monkeypatch.setitem(
        sys.modules,
        "win32com.client",
        SimpleNamespace(DispatchEx=Mock(side_effect=RuntimeError("NO EXCEL"))),
    )
    with pytest.raises(RuntimeError):
        print_patient_workbook(tmp_path / "list.xlsx")
    com.CoUninitialize.assert_called_once()
