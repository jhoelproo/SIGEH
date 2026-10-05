"""Integration checks of the printed NSS geometry in the real Chromium renderer."""

import re

import pytest

from pdf_engine.renderer import ReceiptPDFRenderer


@pytest.fixture(scope="module")
def renderer():
    engine = ReceiptPDFRenderer(persistent=True)
    engine.start()
    yield engine
    engine.close()


def relative_luminance(color):
    components = [int(value) / 255 for value in re.findall(r"\d+", color)[:3]]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in components
    ]
    return sum(
        value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722))
    )


@pytest.mark.parametrize(
    "name,nss",
    [
        ("PACIENTE SINTÉTICO", "001234567"),
        ("PACIENTE SINTÉTICO", "000000000000000000000001"),
        ("PACIENTE SINTÉTICO", ""),
        ("PACIENTE CON NOMBRE EXTENSO PARA COMPROBAR EL AJUSTE DEL TEXTO", "0" * 40),
    ],
)
def test_printed_nss_has_left_aligned_space_and_visible_color(renderer, name, nss):
    page = renderer._page
    page.set_content(renderer.render_html({"nombre": name, "nss": nss}))
    page.emulate_media(media="print")
    geometry = page.evaluate(
        """() => {
            const copy = document.querySelector('.patient-left .field-copy');
            const name = copy.querySelector('.field-value');
            const badge = copy.querySelector('.patient-identifier');
            const rect = element => {
                const box = element.getBoundingClientRect();
                return {left: box.left, right: box.right, top: box.top, bottom: box.bottom};
            };
            const style = getComputedStyle(badge);
            return {copy: rect(copy), name: rect(name), badge: rect(badge),
                scrollWidth: badge.scrollWidth, width: badge.clientWidth,
                text: badge.textContent, color: style.color,
                background: style.backgroundColor, weight: style.fontWeight};
        }"""
    )
    assert abs(geometry["badge"]["left"] - geometry["copy"]["left"]) < 1
    assert geometry["badge"]["right"] <= geometry["copy"]["right"] + 1
    assert geometry["badge"]["top"] > geometry["name"]["bottom"]
    assert geometry["scrollWidth"] <= geometry["width"] + 1
    assert (nss or "NO REGISTRADO") in geometry["text"]
    assert geometry["background"] not in {"transparent", "rgba(0, 0, 0, 0)"}
    light, dark = sorted(
        (
            relative_luminance(geometry["background"]),
            relative_luminance(geometry["color"]),
        ),
        reverse=True,
    )
    assert (light + 0.05) / (dark + 0.05) >= 4.5
    assert int(geometry["weight"]) >= 700
