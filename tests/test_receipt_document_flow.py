import unittest
from pathlib import Path

import CALCULOS_QT as app
from pdf_engine import ReceiptPDFRenderer


class ReceiptDocumentFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = ReceiptPDFRenderer()
        cls.base_data = {
            "numero": 100,
            "fecha": "2026-07-17",
            "paciente": "Paciente prueba",
            "diagnostico": "Diagnóstico prueba",
            "ars": "ARS prueba",
            "categorias": [],
            "total_general": 100,
            "logo_path": str(Path("assets/logo.jpg").resolve()),
        }

    def test_preliminary_pdf_is_unmistakable(self):
        html = self.renderer.render_html({
            **self.base_data,
            "estado_documento": app.DOCUMENT_PRELIMINARY,
            "numero_autorizacion": "",
        })
        self.assertIn("VERSIÓN PRELIMINAR", html)
        self.assertIn("preliminary-watermark", html)
        self.assertIn("Autorización: <b>PENDIENTE</b>", html)

    def test_ready_pdf_shows_authorization_without_preliminary_watermark(self):
        html = self.renderer.render_html({
            **self.base_data,
            "estado_documento": app.DOCUMENT_READY,
            "numero_autorizacion": "AUT-001",
        })
        self.assertIn("Autorización: <b>AUT-001</b>", html)
        self.assertNotIn('<div class="preliminary-watermark"', html)
        self.assertNotIn("document-state-banner ready-banner", html)

    def test_history_palette_distinguishes_preliminary_and_ready(self):
        preliminary = app.receipt_history_palette(
            app.BILLING_PENDING, app.DOCUMENT_PRELIMINARY, False
        )
        ready = app.receipt_history_palette(
            app.BILLING_PENDING, app.DOCUMENT_READY, False
        )
        self.assertNotEqual(preliminary, ready)


if __name__ == "__main__":
    unittest.main()
