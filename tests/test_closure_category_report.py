import pytest

from billing_closure_categories import closure_category_counts
from report_engine.html_renderer import ReportHTMLRenderer


def report_context(version=2):
    classes = [
        "AUTORIZADA",
        "PENDIENTE DE AUTORIZACIÓN",
        "HEREDADA AUTORIZADA",
        "HEREDADA PENDIENTE",
        "HISTÓRICA AUTORIZADA",
        "HISTÓRICA PENDIENTE",
    ]
    data = closure_category_counts(classes)
    data.update(
        classification_version=version,
        closure=dict(
            started_at="2026-09-18 08:00:00",
            closed_at="2026-09-19 08:00:00",
            operational_date="2026-09-18",
            closed_by="OPERADOR DE PRUEBA",
        ),
        by_ars=[dict(ars="HUMANO", label="HUMANO", worked=6, authorized=3, pending=3)],
        productivity=[],
        details=[
            dict(
                attention_id=index + 1,
                patient_name=f"PACIENTE DE PRUEBA {index + 1}",
                ars="HUMANO",
                original_turn_id=101,
                receipt_number=100 + index,
                authorization_number="1234" if "AUTORIZADA" in status else None,
                classification=status,
                responsible_username="PRUEBA",
                source_updated_at="2026-09-18 16:00:00",
            )
            for index, status in enumerate(classes)
        ],
    )
    return dict(
        mode="shift_closure",
        title="Cierre de turno - prueba",
        subtitle="Validación de categorías",
        generated_by="PRUEBA",
        generated_at="2026-09-19 08:00:00",
        data=data,
    )


@pytest.mark.parametrize("version", [1, 2])
def test_report_preserves_legacy_rules_and_displays_new_categories(version):
    html = ReportHTMLRenderer().render_html(report_context(version))
    assert ("Históricas pendientes al cierre</span><strong>1" in html) == (version == 2)
    assert ("al menos cuatro dígitos" in html) == (version == 2)
    assert ("Cierre conservado con las reglas" in html) == (version == 1)
    assert "Pendientes finales</span><strong>3" in html
