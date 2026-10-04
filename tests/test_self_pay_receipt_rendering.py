import pytest
from pdf_engine import ReceiptPDFRenderer


@pytest.mark.parametrize(
    "status,reason", [("PAGADO", ""), ("EXONERADO", "Exoneración <autorizada>")]
)
def test_direct_receipt_prints_payment_without_pending_authorization_or_audit(
    status, reason
):
    html = ReceiptPDFRenderer().render_html(
        dict(
            numero=1,
            fecha="2026-10-03",
            paciente="SINTETICO",
            ars="Extranjero",
            sala=460,
            categorias=[],
            total_general=460,
            total_letras="CUATROCIENTOS SESENTA PESOS",
            usuario="QA",
            estado_documento="LISTO_AUDITORIA",
            payment_status=status,
            exemption_reason=reason,
        )
    )
    assert "RECIBO DE COBRO" in html and status in html
    assert (
        "Listo para auditoría" not in html
        and "Autorización:" not in html
        and "PENDIENTE" not in html
    )
    if reason:
        assert "Exoneración &lt;autorizada&gt;" in html


def test_insured_receipt_keeps_authorization_and_audit_banner():
    html = ReceiptPDFRenderer().render_html(
        dict(
            numero=1,
            fecha="2026-10-03",
            paciente="SINTETICO",
            ars="SENASA CONTRIBUTIVO",
            sala=460,
            categorias=[],
            total_general=460,
            total_letras="CUATROCIENTOS SESENTA PESOS",
            usuario="QA",
            estado_documento="LISTO_AUDITORIA",
            numero_autorizacion="1234",
        )
    )
    assert "Listo para auditoría" in html and "Autorización:" in html and "1234" in html
