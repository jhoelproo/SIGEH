from __future__ import annotations

import base64
import io
import mimetypes
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from jinja2 import Environment, FileSystemLoader, select_autoescape

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_FILE = BASE_DIR / "template.html"
CSS_FILE = BASE_DIR / "styles.css"
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BASE_DIR.parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
OUTPUT_DIR = APP_DIR / "recibos"

CAT_COLORS = {
    "Medicamentos": "#124a9f",
    "Materiales": "#0b8b61",
    "Laboratorios": "#6430a8",
    "Imágenes": "#e4570a",
    "Imagenes": "#e4570a",
    "Procedimientos": "#0e5eac",
    "Honorarios": "#124a9f",
}

CAT_ICONS = {
    "Medicamentos": "💊",
    "Materiales": "🩹",
    "Laboratorios": "🧪",
    "Imágenes": "🖼️",
    "Imagenes": "🖼️",
    "Procedimientos": "🩺",
    "Honorarios": "👨‍⚕️",
}

CATEGORY_ORDER = [
    "Medicamentos",
    "Materiales",
    "Laboratorios",
    "Imágenes",
    "Procedimientos",
    "Honorarios",
]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        if isinstance(value, (int, float)):
            return float(value)

        text = str(value).strip()
        if not text:
            return default

        # Permite valores como "RD$ 3,500.00", "$1,200", "1.200,50".
        cleaned = re.sub(r"[^0-9,.-]", "", text)
        if not cleaned:
            return default

        if "," in cleaned and "." in cleaned:
            # Si el punto aparece después de la coma, se asume formato 1,234.56.
            # Si la coma aparece después del punto, se asume formato 1.234,56.
            if cleaned.rfind(".") > cleaned.rfind(","):
                cleaned = cleaned.replace(",", "")
            else:
                cleaned = cleaned.replace(".", "").replace(",", ".")
        elif "," in cleaned:
            left, right = cleaned.rsplit(",", 1)
            if right.isdigit() and 1 <= len(right) <= 2:
                cleaned = left.replace(",", "") + "." + right
            else:
                cleaned = cleaned.replace(",", "")

        return float(cleaned)
    except Exception:
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def money(value: Any) -> str:
    return f"RD$ {_as_float(value):,.2f}"


def number(value: Any) -> str:
    return f"{_as_float(value):,.2f}"


def clean_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _format_date(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return datetime.now().strftime("%d-%m-%Y")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).strftime("%d-%m-%Y")
        except Exception:
            pass
    return text


def _find_logo(explicit_path: Any = None) -> str:
    candidates: List[Path] = []
    if explicit_path:
        candidates.append(Path(str(explicit_path)))
    for name in ("logo.png", "logo.jpg", "logo.jpeg"):
        candidates.append(BASE_DIR / name)
        candidates.append(BASE_DIR / "assets" / name)
        candidates.append(BASE_DIR.parent / "assets" / name)
        candidates.append(BUNDLE_DIR / "assets" / name)
        candidates.append(BASE_DIR / "static" / "img" / name)
    for path in candidates:
        try:
            if path.exists():
                raw = path.read_bytes()
                mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
                try:
                    from PIL import Image, ImageChops

                    with Image.open(io.BytesIO(raw)).convert("RGB") as image:
                        background = Image.new("RGB", image.size, (255, 255, 255))
                        difference = ImageChops.difference(image, background).convert("L")
                        bbox = difference.point(lambda value: 255 if value > 18 else 0).getbbox()
                        if bbox:
                            left, top, right, bottom = bbox
                            pad_x = max(8, int((right - left) * 0.025))
                            pad_y = max(8, int((bottom - top) * 0.06))
                            crop_box = (
                                max(0, left - pad_x), max(0, top - pad_y),
                                min(image.width, right + pad_x), min(image.height, bottom + pad_y),
                            )
                            output = io.BytesIO()
                            image.crop(crop_box).save(output, format="PNG", optimize=True)
                            raw = output.getvalue()
                            mime_type = "image/png"
                except Exception:
                    pass
                encoded = base64.b64encode(raw).decode("ascii")
                return f"data:{mime_type};base64,{encoded}"
        except Exception:
            continue
    return ""


def _configure_playwright_browsers() -> None:
    """Apunta Playwright al navegador incluido por PyInstaller, si existe."""
    bundled_browsers = BUNDLE_DIR / "playwright-browsers"
    if bundled_browsers.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers)


def render_html_to_pdf(
    html_content: str,
    output_file: str | os.PathLike[str],
    *,
    landscape: bool = False,
    display_page_numbers: bool = True,
) -> str:
    """Motor HTML/CSS genérico compartido por recibos y reportes."""
    output_path = Path(output_file).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _configure_playwright_browsers()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=20_000)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_content(html_content, wait_until="domcontentloaded", timeout=20_000)
            page.emulate_media(media="print")
            footer_template = (
                "<div style='width:100%;font-size:8px;color:#52657a;padding:0 12mm;"
                "display:flex;justify-content:space-between;font-family:Arial,sans-serif'>"
                "<span>Hospital Provincial Dr. Ángel Contreras Mejía</span>"
                "<span>Página <span class='pageNumber'></span> de <span class='totalPages'></span></span>"
                "</div>"
            )
            page.pdf(
                path=str(output_path),
                format="Letter",
                landscape=bool(landscape),
                print_background=True,
                prefer_css_page_size=False,
                display_header_footer=bool(display_page_numbers),
                header_template="<span></span>",
                footer_template=footer_template if display_page_numbers else "<span></span>",
                margin={"top": "9mm", "right": "9mm", "bottom": "15mm", "left": "9mm"},
            )
        finally:
            browser.close()
    return str(output_path)


class ReceiptPDFRenderer:
    def __init__(
        self,
        base_dir: str | os.PathLike[str] | None = None,
        persistent: bool = False,
    ):
        self.base_dir = Path(base_dir).resolve() if base_dir else BASE_DIR
        self.persistent = bool(persistent)
        self.env = Environment(
            loader=FileSystemLoader(str(self.base_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self.env.filters["money"] = money
        self.env.filters["number"] = number
        self.template = self.env.get_template("template.html")
        css_path = self.base_dir / "styles.css"
        self._inline_css = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
        self._logo_cache: Dict[str, str] = {}
        self._playwright = None
        self._browser = None
        self._page = None

    def start(self) -> None:
        """Inicia Chromium una sola vez para reutilizarlo entre recibos."""
        if self._browser is not None:
            return
        _configure_playwright_browsers()
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(
                headless=True,
                timeout=20_000,
            )
            self._page = self._browser.new_page(
                viewport={"width": 816, "height": 1056},
                device_scale_factor=1,
            )
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Libera la página, Chromium y Playwright en orden seguro."""
        if self._page is not None:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def _logo_data_url(self, explicit_path: Any = None) -> str:
        cache_key = str(explicit_path or "__default__")
        if cache_key not in self._logo_cache:
            self._logo_cache[cache_key] = _find_logo(explicit_path)
        return self._logo_cache[cache_key]

    def _normalize_categories(self, raw_categories: Any) -> List[Dict[str, Any]]:
        raw_categories = raw_categories or []
        normalized: List[Dict[str, Any]] = []

        if isinstance(raw_categories, dict):
            iterable = []
            for name, payload in raw_categories.items():
                if isinstance(payload, dict):
                    payload = {**payload, "nombre": payload.get("nombre", name)}
                else:
                    payload = {"nombre": name, "items": payload}
                iterable.append(payload)
        else:
            iterable = list(raw_categories)

        for cat in iterable:
            if not isinstance(cat, dict):
                continue
            name = clean_text(cat.get("nombre") or cat.get("categoria") or cat.get("name"))
            if not name:
                continue
            items_raw = cat.get("items") or cat.get("cargos") or []
            items = []
            subtotal = 0.0
            for item in items_raw:
                if not isinstance(item, dict):
                    continue
                qty = _as_int(item.get("cantidad") or item.get("cant") or item.get("qty"), 1)
                price = _as_float(item.get("precio") or item.get("precio_unit") or item.get("unit_price"))
                total = _as_float(item.get("total"), qty * price)
                subtotal += total
                items.append({
                    "descripcion": clean_text(item.get("descripcion") or item.get("nombre") or item.get("item")),
                    "cantidad": qty,
                    "precio": price,
                    "total": total,
                })

            subtotal = _as_float(cat.get("subtotal"), subtotal)
            normalized.append({
                "nombre": name,
                "icono": clean_text(cat.get("icono"), CAT_ICONS.get(name, "•")),
                "color": clean_text(cat.get("color"), CAT_COLORS.get(name, "#124a9f")),
                "subtotal": subtotal,
                "items": items,
            })

        order_index = {name: i for i, name in enumerate(CATEGORY_ORDER)}
        normalized.sort(key=lambda c: order_index.get(c["nombre"], 99))
        return normalized

    def _prepare_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        prepared = {
            "hospital_line_1": clean_text(data.get("hospital_line_1"), "HOSPITAL PROVINCIAL"),
            "hospital_line_2": clean_text(data.get("hospital_line_2"), "DR. ÁNGEL CONTRERAS MEJÍA"),
            "document_title": clean_text(data.get("document_title"), "DETALLE DE FACTURACIÓN DE EMERGENCIA"),
            "numero": clean_text(data.get("numero") or data.get("recibo") or data.get("recibo_number"), ""),
            "fecha": clean_text(data.get("fecha"), datetime.now().strftime("%Y-%m-%d")),
            "fecha_display": _format_date(data.get("fecha")),
            "paciente": clean_text(data.get("paciente") or data.get("nombre"), "N/A"),
            "dx": clean_text(data.get("dx") or data.get("diagnostico"), "N/A"),
            "ars": clean_text(data.get("ars"), "N/A"),
            "sala": _as_float(data.get("sala") or data.get("costo_sala")),
            "categorias": self._normalize_categories(data.get("categorias") or data.get("items_por_categoria")),
            "total_general": _as_float(data.get("total_general") or data.get("total")),
            "total_letras": clean_text(data.get("total_letras"), ""),
            "usuario": clean_text(data.get("usuario") or data.get("auxiliar"), "Administrador del sistema"),
            "generado": clean_text(data.get("generado") or data.get("generated_at"), datetime.now().strftime("%d/%m/%Y  %I:%M %p")),
            "numero_autorizacion": clean_text(data.get("numero_autorizacion"), ""),
            "estado_documento": clean_text(data.get("estado_documento"), "PRELIMINAR"),
            "logo_url": self._logo_data_url(data.get("logo_path")),
            "inline_css": self._inline_css,
        }
        return prepared

    def render_html(self, data: Dict[str, Any]) -> str:
        """Renderiza template.html e inyecta styles.css en el HTML final.

        El template nuevo usa este marcador para evitar que VS Code marque
        errores por Jinja dentro de una etiqueta <style>:

            <style id="pdf-css">
            /*__INLINE_CSS__*/
            </style>

        Por eso el CSS debe insertarse aquí, después de ejecutar Jinja y
        antes de enviar el HTML a Playwright.
        """
        prepared = self._prepare_data(data)
        html = self.template.render(**prepared)

        css = prepared.get("inline_css", "")
        if not str(css).strip():
            css_path = self.base_dir / "styles.css"
            raise RuntimeError(
                f"No se encontró contenido CSS. Verifica que exista: {css_path}"
            )

        # Forma recomendada en el template nuevo.
        if "/*__INLINE_CSS__*/" in html:
            html = html.replace("/*__INLINE_CSS__*/", css)

        # Compatibilidad con otra variante del template.
        elif '<style id="pdf-css"></style>' in html:
            html = html.replace(
                '<style id="pdf-css"></style>',
                f'<style id="pdf-css">\n{css}\n</style>'
            )

        # Compatibilidad con el template viejo, por si todavía existe.
        elif "{{ inline_css|safe }}" in html:
            html = html.replace("{{ inline_css|safe }}", css)

        # Validación para evitar generar PDFs sin diseño.
        if "/*__INLINE_CSS__*/" in html or "{{ inline_css|safe }}" in html:
            raise RuntimeError(
                "El CSS no se inyectó en el HTML. Revisa el marcador del template.html."
            )

        if "--blue" not in html and "RECIBO HOSPITALARIO" not in html:
            raise RuntimeError(
                "El HTML final no parece contener el CSS esperado. Revisa styles.css."
            )

        return html

    def render(self, data: Dict[str, Any], output_file: str | os.PathLike[str], save_html_preview: bool = True) -> str:
        """Alias de compatibilidad para scripts antiguos que llaman renderer.render(...)."""
        return self.render_pdf(data, output_file, save_html_preview=save_html_preview)

    def render_pdf(self, data: Dict[str, Any], output_file: str | os.PathLike[str], save_html_preview: bool = True) -> str:
        output_path = Path(output_file)
        if not output_path.is_absolute():
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output_path = OUTPUT_DIR / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path = output_path.resolve()

        html_content = self.render_html(data)

        if save_html_preview:
            output_path.with_suffix(".html").write_text(html_content, encoding="utf-8")

        def create_pdf(page) -> None:
            page.set_content(html_content, wait_until="domcontentloaded", timeout=15_000)
            page.emulate_media(media="print")
            page.pdf(
                path=str(output_path),
                format="Letter",
                print_background=True,
                prefer_css_page_size=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )

        if self.persistent:
            self.start()
            create_pdf(self._page)
        else:
            self.start()
            try:
                create_pdf(self._page)
            finally:
                self.close()

        return str(output_path)


PDFRenderer = ReceiptPDFRenderer
