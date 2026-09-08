"""Submit the patient worksheet through a private Excel instance on Windows."""

from pathlib import Path
from excel_delivery_path import prepare_excel_delivery


def print_patient_workbook(path, copies=1):
    import pythoncom
    from win32com.client import DispatchEx

    prepared = prepare_excel_delivery(path)
    pythoncom.CoInitialize()
    application = None
    workbook = None
    try:
        application = DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        workbook = application.Workbooks.Open(
            str(Path(prepared).resolve()), UpdateLinks=0, ReadOnly=True
        )
        sheet = workbook.Worksheets(1)
        sheet.PageSetup.Zoom = False
        sheet.PageSetup.FitToPagesWide = 1
        sheet.PageSetup.FitToPagesTall = False
        sheet.PageSetup.PrintTitleRows = "$1:$5"
        sheet.PrintOut(Copies=max(1, int(copies)), Collate=True)
    finally:
        try:
            if workbook is not None:
                workbook.Close(SaveChanges=False)
        finally:
            try:
                if application is not None:
                    application.Quit()
            finally:
                pythoncom.CoUninitialize()
