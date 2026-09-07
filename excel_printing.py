"""Submit the patient worksheet through a private Excel instance on Windows."""

from pathlib import Path


def print_patient_workbook(path, copies=1):
    import pythoncom
    from win32com.client import DispatchEx

    pythoncom.CoInitialize()
    application = None
    workbook = None
    try:
        application = DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        workbook = application.Workbooks.Open(
            str(Path(path).resolve()), UpdateLinks=0, ReadOnly=True
        )
        workbook.Worksheets(1).PrintOut(Copies=max(1, int(copies)), Collate=True)
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
