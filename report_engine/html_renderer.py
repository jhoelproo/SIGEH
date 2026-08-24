from __future__ import annotations

import base64
import html
import math
import mimetypes
import textwrap
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pdf_engine.renderer import render_html_to_pdf


ROOT = Path(__file__).resolve().parent


def _display_date(value) -> str:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%d-%m-%Y")
    except (TypeError, ValueError):
        return str(value or "")


def _signed_money(value) -> str:
    value = float(value or 0)
    sign = "+" if value > 0 else "-" if value < 0 else ""
    return f"{sign}RD$ {abs(value):,.2f}"


def _logo_data_url(path: str = "") -> str:
    logo = Path(path) if path else None
    if not logo or not logo.is_file():
        return ""
    mime = mimetypes.guess_type(logo.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(logo.read_bytes()).decode('ascii')}"


def _compact_money(value: float) -> str:
    value = float(value or 0)
    if abs(value) >= 1_000_000:
        return f"RD$ {value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"RD$ {value / 1_000:.1f}K"
    return f"RD$ {value:,.0f}"


def bar_chart_svg(entries, value_key="total", title="Comparación") -> str:
    entries = list(entries or [])[:12]
    if not entries:
        return '<div class="empty-chart">Sin datos</div>'
    ars_chart = str(title or "").strip().casefold() == "autorizaciones por ars"
    width, height = (900, 360) if ars_chart else (900, 330)
    left, top, right, bottom = 62, 46, 24, 98 if ars_chart else 62
    plot_w, plot_h = width - left - right, height - top - bottom
    maximum = max(float(row.get(value_key, 0) or 0) for row in entries) or 1
    slot = plot_w / len(entries)
    bar_w = min(52, slot * .58)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">']
    for line in range(5):
        y = top + plot_h * line / 4
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" class="grid-line"/>')
    for index, row in enumerate(entries):
        value = float(row.get(value_key, 0) or 0)
        bar_h = plot_h * value / maximum
        x = left + index * slot + (slot - bar_w) / 2
        y = top + plot_h - bar_h
        raw_label = str(row.get("label", "")).strip()
        label_lines = textwrap.wrap(raw_label, width=16, break_long_words=False)[:2]
        if not label_lines:
            label_lines = ["—"]
        if value_key in ("total", "amount"):
            shown = _compact_money(value)
        elif value_key == "percentage_value":
            shown = f"{value:.1f}%"
        else:
            shown = f"{int(value):,}"
        parts.extend([
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" rx="5" class="bar"/>',
            f'<text x="{x + bar_w / 2:.1f}" y="{max(18, y - 8):.1f}" class="chart-value" text-anchor="middle">{shown}</text>',
        ])
        label_class = "chart-label chart-label-ars" if ars_chart else "chart-label"
        label_y = top + plot_h + (28 if ars_chart else 22)
        tspans = "".join(
            f'<tspan x="{x + bar_w / 2:.1f}" dy="{0 if line_index == 0 else 22}">{html.escape(line)}</tspan>'
            for line_index, line in enumerate(label_lines)
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{label_y:.1f}" class="{label_class}" '
            f'text-anchor="middle">{tspans}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def line_chart_svg(entries, value_key="total", title="Evolución") -> str:
    entries = list(entries or [])
    if not entries:
        return '<div class="empty-chart">Sin datos</div>'
    width, height = 900, 320
    left, top, right, bottom = 62, 42, 24, 56
    plot_w, plot_h = width - left - right, height - top - bottom
    maximum = max(float(row.get(value_key, 0) or 0) for row in entries) or 1
    divisor = max(1, len(entries) - 1)
    points = []
    for index, row in enumerate(entries):
        x = left + plot_w * index / divisor
        value = float(row.get(value_key, 0) or 0)
        y = top + plot_h - plot_h * value / maximum
        points.append((x, y, value, str(row.get("label", ""))))
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">']
    for line in range(5):
        y = top + plot_h * line / 4
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" class="grid-line"/>')
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in points)
    parts.append(f'<polyline points="{polyline}" class="trend-line"/>')
    label_step = max(1, math.ceil(len(points) / 8))
    for index, (x, y, value, label) in enumerate(points):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" class="trend-dot"/>')
        if index % label_step == 0 or index == len(points) - 1:
            shown = _compact_money(value) if value_key == "total" else f"{int(value):,}"
            parts.append(f'<text x="{x:.1f}" y="{max(16, y - 10):.1f}" class="chart-value" text-anchor="middle">{shown}</text>')
            parts.append(f'<text x="{x:.1f}" y="{top + plot_h + 24:.1f}" class="chart-label" text-anchor="middle">{html.escape(label[:14])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def doughnut_chart_svg(entries, title="Distribución", currency=True) -> str:
    entries = [row for row in list(entries or []) if float(row.get("total", 0) or 0) > 0][:8]
    if not entries:
        return '<div class="empty-chart">Sin datos</div>'
    colors = ["#174A96", "#0B7A5A", "#6B35C8", "#EA6A24", "#D14B4B", "#2E91C7", "#8A6D3B", "#607D8B"]
    total = sum(float(row["total"]) for row in entries)
    radius, circumference = 78, 2 * math.pi * 78
    offset = 0.0
    parts = ['<svg viewBox="0 0 900 330" role="img" aria-label="Distribución">']
    parts.append('<circle cx="180" cy="165" r="78" class="donut-bg"/>')
    for index, row in enumerate(entries):
        fraction = float(row["total"]) / total
        dash = fraction * circumference
        parts.append(
            f'<circle cx="180" cy="165" r="78" fill="none" stroke="{colors[index]}" stroke-width="38" '
            f'stroke-dasharray="{dash:.2f} {circumference - dash:.2f}" stroke-dashoffset="{-offset:.2f}" '
            'transform="rotate(-90 180 165)"/>'
        )
        offset += dash
        y = 62 + index * 30
        percentage = fraction * 100
        parts.extend([
            f'<rect x="340" y="{y - 12}" width="15" height="15" rx="3" fill="{colors[index]}"/>',
            f'<text x="368" y="{y}" class="donut-label">{html.escape(str(row.get("label", ""))[:28])}</text>',
            f'<text x="790" y="{y}" class="donut-value" text-anchor="end">{percentage:.1f}%</text>',
        ])
    parts.extend([
        '<text x="180" y="158" class="donut-center" text-anchor="middle">TOTAL</text>',
        f'<text x="180" y="183" class="donut-total" text-anchor="middle">{_compact_money(total) if currency else f"{int(total):,}"}</text>',
        "</svg>",
    ])
    return "".join(parts)


def paired_bar_chart_svg(entries, title="Comparación") -> str:
    """Barras horizontales dobles; cada indicador conserva su propia escala."""
    entries = list(entries or [])
    if not entries:
        return '<div class="empty-chart">Sin datos</div>'
    width = 900
    row_height = 76
    height = 44 + row_height * len(entries) + 28
    left, right = 190, 125
    plot_width = width - left - right
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">']
    parts.extend([
        '<rect x="610" y="12" width="14" height="14" rx="3" class="bar-current"/>',
        '<text x="631" y="24" class="chart-label">Período actual</text>',
        '<rect x="744" y="12" width="14" height="14" rx="3" class="bar-previous"/>',
        '<text x="765" y="24" class="chart-label">Anterior</text>',
    ])
    for index, row in enumerate(entries):
        current = float(row.get("current", 0) or 0)
        previous = float(row.get("previous", 0) or 0)
        maximum = max(abs(current), abs(previous), 1)
        y = 48 + index * row_height
        current_width = plot_width * abs(current) / maximum
        previous_width = plot_width * abs(previous) / maximum
        currency = bool(row.get("currency", True))
        current_text = _compact_money(current) if currency else f"{int(current):,}"
        previous_text = _compact_money(previous) if currency else f"{int(previous):,}"
        parts.extend([
            f'<text x="{left - 12}" y="{y + 22}" class="pair-label" text-anchor="end">{html.escape(str(row.get("label", ""))[:28])}</text>',
            f'<rect x="{left}" y="{y}" width="{current_width:.1f}" height="22" rx="4" class="bar-current"/>',
            f'<rect x="{left}" y="{y + 29}" width="{previous_width:.1f}" height="22" rx="4" class="bar-previous"/>',
            f'<text x="{min(width - 5, left + current_width + 8):.1f}" y="{y + 16}" class="pair-value">{current_text}</text>',
            f'<text x="{min(width - 5, left + previous_width + 8):.1f}" y="{y + 45}" class="pair-value">{previous_text}</text>',
        ])
    parts.append("</svg>")
    return "".join(parts)


class ReportHTMLRenderer:
    def __init__(self):
        self.env = Environment(
            loader=FileSystemLoader(str(ROOT)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self.env.filters["display_date"] = _display_date
        self.env.filters["signed_money"] = _signed_money
        self.template = self.env.get_template("report_template.html")
        self.styles = (ROOT / "report_styles.css").read_text(encoding="utf-8")

    def render_html(self, context: dict) -> str:
        prepared = dict(context)
        prepared.setdefault("generated_at", datetime.now().strftime("%d-%m-%Y %H:%M"))
        prepared["styles"] = self.styles
        prepared["logo_url"] = _logo_data_url(prepared.get("logo_path", ""))
        if prepared.get("mode") in ("panel", "comparison"):
            data = dict(prepared["data"])
            view = dict(data.get("view", {}))
            view.setdefault("show_ars_comparison", bool(data.get("show_ars_comparison")))
            view.setdefault("ars_metric", "total")
            view.setdefault("ars_metric_label", "Monto facturado")
            view.setdefault("evolution_metric", "total")
            view.setdefault("evolution_label", "Monto facturado")
            data["view"] = view
            prepared["data"] = data
            def selection_text(selection, empty):
                if not isinstance(selection, dict) or not selection.get("values"):
                    return empty
                mode = "Excluir" if selection.get("mode") in ("exclude", "excluir") else "Incluir"
                return f"{mode}: {', '.join(selection['values'])}"
            filters = data.get("filters", {})
            prepared["filter_ars"] = selection_text(filters.get("ars"), "Todas")
            prepared["filter_users"] = selection_text(filters.get("user"), "Todos")
            prepared["analysis_label"] = filters.get("analysis_label", "Facturación confirmada")
            prepared["date_basis_label"] = filters.get("date_basis_label", "Fecha de validación")
        if prepared.get("mode") == "shift_closure":
            data = prepared["data"]
            prepared["status_donut_svg"] = doughnut_chart_svg([
                {"label": "Autorizadas", "total": data.get("authorized_applicable", 0)},
                {"label": "Pendientes", "total": data.get("pending_next", 0)},
            ], currency=False)
            prepared["ars_amount_svg"] = bar_chart_svg(
                data.get("by_ars", []), "authorized", "Autorizaciones por ARS"
            )
            prepared["productivity_svg"] = bar_chart_svg(
                data.get("productivity", []), "receipts", "Recibos por auxiliar"
            )
        elif prepared.get("mode") == "comparison":
            data = prepared["data"]
            summary = data.get("summary", {})
            previous = data.get("previous", {})
            prepared["comparison_rows"] = [
                {"label": filters.get("receipt_label", "Total de recibos").title(), "current": summary.get("receipts", 0), "previous": previous.get("receipts", 0), "currency": False},
                {"label": filters.get("total_label", "Total emitido").title(), "current": summary.get("total", 0), "previous": previous.get("total", 0), "currency": True},
                {"label": "Promedio por recibo", "current": summary.get("average", 0), "previous": previous.get("average", 0), "currency": True},
                {"label": "Sala de emergencia", "current": summary.get("room", 0), "previous": previous.get("room", 0), "currency": True},
            ]
            prepared["category_comparison_rows"] = list(data.get("category_comparison", []))
            prepared["indicator_comparison_svg"] = paired_bar_chart_svg(
                prepared["comparison_rows"], "Comparación de indicadores"
            )
            prepared["category_comparison_svg"] = paired_bar_chart_svg(
                [{**row, "currency": True} for row in prepared["category_comparison_rows"]],
                "Comparación por categorías",
            )
        elif prepared.get("mode") == "panel":
            data = prepared["data"]
            view = data.get("view", {})
            ars_metric = prepared.get("ars_metric", view.get("ars_metric", "total"))
            evolution_metric = prepared.get(
                "evolution_metric", view.get("evolution_metric", "total")
            )
            prepared["category_bar_svg"] = bar_chart_svg(
                data.get("categories"), "total", "Distribución por categorías"
            )
            percentage_key = "receipt_percentage" if ars_metric == "receipts" else "money_percentage"
            ars_rows = [
                {**row, "percentage_value": float(row.get(percentage_key, 0) or 0) * 100}
                for row in data.get("comparison", [])
            ]
            prepared["comparison_svg"] = bar_chart_svg(
                ars_rows, "percentage_value", "Distribución porcentual por ARS"
            )
            prepared["line_svg"] = line_chart_svg(
                data.get("trend"), evolution_metric, "Evolución diaria"
            )
            prepared["donut_svg"] = doughnut_chart_svg(
                data.get("category_distribution", data.get("categories"))
            )
            coverage_total = sum(float(row.get("receipts", 0) or 0) for row in data.get("coverage", []))
            coverage_rows = [
                {**row, "percentage_value": (float(row.get("receipts", 0) or 0) / coverage_total * 100) if coverage_total else 0}
                for row in data.get("coverage", [])
            ]
            prepared["coverage_svg"] = bar_chart_svg(
                coverage_rows, "percentage_value", "Distribución por tipo de cobertura"
            )
        return self.template.render(**prepared)

    def render_pdf(self, context: dict, output_path: str, landscape: bool = False) -> str:
        html_content = self.render_html(context)
        return render_html_to_pdf(
            html_content,
            output_path,
            landscape=landscape,
            display_page_numbers=True,
        )
