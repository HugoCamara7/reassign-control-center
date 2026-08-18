"""Componentes visuales reutilizables (hero, KPIs, notas, rail de pasos)."""

from __future__ import annotations

from html import escape

import streamlit as st

from config import settings
from core.engine import KPIs
from core.validation import LEVEL_ERROR, LEVEL_INFO, LEVEL_WARNING, ValidationReport
from ui.theme import logo_data_uri

STEPS = [
    ("Subir archivo", "Carga el Excel de pedidos"),
    ("Validar", "Revisa columnas, estados y riesgos"),
    ("Consultar stock", "Lectura de BigQuery"),
    ("Reasignar", "Aplica la lista de prioridad"),
    ("Revisar", "Vista previa y KPIs"),
    ("Descargar", "Excel listo para cargar"),
]


# Marca secundaria del intro: la fuente de datos (BigQuery). Es un glifo
# generico de base de datos, no el logotipo de Google.
DATA_MARK_SVG = """
<svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <defs>
    <linearGradient id="rccData" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#4285F4"/>
      <stop offset="100%" stop-color="#1757EF"/>
    </linearGradient>
  </defs>
  <ellipse cx="16" cy="7.4" rx="10.5" ry="4.1" fill="url(#rccData)"/>
  <path d="M5.5 7.4v7.2c0 2.27 4.7 4.1 10.5 4.1s10.5-1.83 10.5-4.1V7.4"
        fill="none" stroke="url(#rccData)" stroke-width="2.7" stroke-linecap="round"/>
  <path d="M5.5 14.6v7.2c0 2.27 4.7 4.1 10.5 4.1s10.5-1.83 10.5-4.1v-7.2"
        fill="none" stroke="url(#rccData)" stroke-width="2.7" stroke-linecap="round"
        opacity=".62"/>
</svg>
"""


def html(markup: str, sidebar: bool = False) -> None:
    target = st.sidebar if sidebar else st
    target.markdown(markup, unsafe_allow_html=True)


def login_header() -> None:
    """Cabecera azul del intro: logos, nombre de la app y bajada."""
    logo = logo_data_uri()
    forus = (
        f'<img src="{logo}" alt="Forus">'
        if logo
        else '<div class="login-forus-fallback">FORUS<small>CONSUMER FANATIC</small></div>'
    )
    html(
        f"""
        <div class="login-head">
            <div class="login-logo-row">
                <div class="login-forus-logo">{forus}</div>
                <div class="login-divider"></div>
                <div class="login-side-logo">{DATA_MARK_SVG}</div>
            </div>
            <h1>{escape(settings.APP_NAME)}</h1>
            <p>{escape(settings.APP_TAGLINE)}</p>
        </div>
        """
    )


def login_note() -> None:
    """Pie dentro de la tarjeta blanca."""
    html('<div class="login-note">Sistema exclusivo para personal autorizado</div>')


def login_footer() -> None:
    """Pie fuera de la tarjeta, sobre el fondo oscuro."""
    html(
        """
        <div class="login-foot">
            <strong>Reasignacion de pedidos para multiples marcas</strong>
            <span>Columbia &bull; Hush Puppies &bull; Vans &bull; Rockford &bull; Parfois &bull; Mas</span>
        </div>
        """
    )


def number(value: object) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# Barra lateral
# ---------------------------------------------------------------------------
def sidebar_brand() -> None:
    logo = logo_data_uri()
    mark = (
        f'<img src="{logo}" alt="Forus">'
        if logo
        else '<div class="rcc-brand-fallback">FORUS<small>CONSUMER FANATIC</small></div>'
    )
    html(
        f"""
        <div class="rcc-brand">
            {mark}
            <div class="rcc-brand-app">{escape(settings.APP_NAME)}
                <span>{escape(settings.APP_TAGLINE)}</span>
            </div>
        </div>
        """,
        sidebar=True,
    )


def sidebar_steps(current: int) -> None:
    items = []
    for index, (title, _) in enumerate(STEPS, start=1):
        if index < current:
            state, glyph = "done", "&#10003;"
        elif index == current:
            state, glyph = "active", str(index)
        else:
            state, glyph = "", str(index)
        items.append(f'<div class="rcc-step {state}"><i>{glyph}</i><span>{escape(title)}</span></div>')
    html('<p class="rcc-side-label">Flujo</p>', sidebar=True)
    html(f'<div class="rcc-steps">{"".join(items)}</div>', sidebar=True)


def sidebar_card(label: str, lines: list[str]) -> None:
    body = "".join(f"<p>{line}</p>" for line in lines)
    html(f'<p class="rcc-side-label">{escape(label)}</p><div class="rcc-side-card">{body}</div>', sidebar=True)


# ---------------------------------------------------------------------------
# Cuerpo
# ---------------------------------------------------------------------------
def hero(title: str, subtitle: str, chips: list[tuple[str, str]] | None = None) -> None:
    chip_html = "".join(
        f'<span class="rcc-chip {escape(tone)}">{escape(text)}</span>' for text, tone in (chips or [])
    )
    html(
        f"""
        <div class="rcc-hero">
            <p class="rcc-hero-eyebrow">Forus Peru &bull; Operaciones ecommerce</p>
            <h1>{escape(title)}</h1>
            <p>{escape(subtitle)}</p>
            <div class="rcc-hero-chips">{chip_html}</div>
        </div>
        """
    )


def section(eyebrow: str, title: str, description: str = "") -> None:
    extra = f"<small>{escape(description)}</small>" if description else ""
    html(
        f'<div class="rcc-section"><p>{escape(eyebrow)}</p><h3>{escape(title)}</h3>{extra}</div>'
    )


def note(tone: str, title: str, body: str = "") -> None:
    glyphs = {"info": "&#8505;", "ok": "&#10003;", "warn": "&#9888;", "bad": "&#10005;"}
    text = f"<b>{escape(title)}</b>" + (f"<br>{escape(body)}" if body else "")
    html(f'<div class="rcc-note {tone}"><i>{glyphs.get(tone, "&#8505;")}</i><div>{text}</div></div>')


def kpi_grid(cards: list[tuple[str, object, str, str]]) -> None:
    """`cards` = lista de (etiqueta, valor, tono, pie)."""
    blocks = []
    for label, value, tone, footer in cards:
        foot = f"<em>{escape(footer)}</em>" if footer else ""
        blocks.append(
            f'<div class="rcc-kpi {escape(tone)}"><span>{escape(label)}</span>'
            f"<strong>{number(value)}</strong>{foot}</div>"
        )
    html(f'<div class="rcc-kpi-grid">{"".join(blocks)}</div>')


def kpi_from_result(kpis: KPIs) -> None:
    reassigned_note = (
        f"{kpis.reasignados_parciales} parciales" if kpis.reasignados_parciales else "Cobertura total"
    )
    kpi_grid(
        [
            ("Pedidos recibidos", kpis.pedidos_recibidos, "neutral", f"{number(kpis.ordenes_unicas)} ordenes unicas"),
            ("Pedidos a reasignar", kpis.pedidos_a_reasignar, "", f"{number(kpis.unidades_solicitadas)} unidades"),
            ("Reasignados", kpis.reasignados + kpis.reasignados_parciales, "ok", reassigned_note),
            ("Sin stock disponible", kpis.sin_stock, "warn", f"Tasa de exito {kpis.tasa_exito:.0f}%"),
            ("Errores", kpis.errores, "bad", "Filas sin SKU o ilegibles"),
        ]
    )


def validation_block(report: ValidationReport) -> None:
    tones = {LEVEL_ERROR: "bad", LEVEL_WARNING: "warn", LEVEL_INFO: "info"}
    for finding in report.errors + report.warnings + report.infos:
        suffix = f" ({number(finding.count)} filas)" if finding.count else ""
        note(tones.get(finding.level, "info"), finding.title + suffix, finding.detail)
