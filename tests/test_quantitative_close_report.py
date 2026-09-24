from pathlib import Path

from PyPDF2 import PdfReader

from billing_close_report import report_data
from report_engine.html_renderer import ReportHTMLRenderer
from tests.test_billing_close_model import admission, receipt, snapshot


def sample_context():
    rows = [admission(index) for index in range(5000)]
    receipts = [receipt(index, str(index), "1234", total="6.00") for index in range(50)]
    data = report_data(
        snapshot(rows, receipts),
        {
            "started_at": "25/09/2026 08:00",
            "closed_at": "26/09/2026 08:00",
            "operational_date": "2026-09-25",
            "closed_by": "OPERADOR DE PRUEBA",
        },
    )
    data["details"] = [{"patient_name": "PRIVATE DETAIL MUST NOT PRINT"}] * 5000
    return {
        "mode": "shift_closure",
        "title": "Cierre de turno",
        "subtitle": "Resumen de recibos y pendientes",
        "generated_by": "PRUEBA",
        "generated_at": "26/09/2026 08:00",
        "data": data,
    }


def test_quantitative_report_has_no_patient_list():
    html = ReportHTMLRenderer().render_html(sample_context())
    assert "PRIVATE DETAIL MUST NOT PRINT" not in html
    assert "Detalle inmutable" not in html
    assert "Guardar un recibo válido" in html
    assert "4,950" in html or "4950" in html
    assert "300.00" in html


def test_five_thousand_pending_do_not_create_massive_pdf(tmp_path):
    path = tmp_path / "quantitative-close.pdf"
    ReportHTMLRenderer().render_pdf(sample_context(), str(path))
    document = PdfReader(path)
    assert 1 <= len(document.pages) <= 3
    text = "\n".join(page.extract_text() for page in document.pages)
    assert "PRIVATE DETAIL" not in text
    assert "4950" in text or "4,950" in text
    assert "300.00" in text
    assert Path(path).stat().st_size > 1000
