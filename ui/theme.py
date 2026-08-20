"""Tema visual Forus: mismo lenguaje que Catalogo / Revenue Control Center.

Puntos no negociables del diseno:

* Barra lateral **permanente**: sin boton de colapso, sin handle de resize,
  ancho fijo. Streamlit intenta colapsarla en varios puntos (boton propio,
  atajo de teclado, `aria-expanded=false`), asi que se neutralizan todos.
* Paleta azul Forus sobre fondo claro, tipografia de peso alto y tarjetas
  con borde suave, igual que en el resto de aplicaciones internas.
"""

from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

from config import settings

SIDEBAR_WIDTH = 340

PALETTE = {
    "ink": "#0B1B46",
    "ink_soft": "#3F5578",
    "primary": "#2367FF",
    "primary_dark": "#1757EF",
    "primary_deep": "#14306B",
    "navy": "#152238",
    "surface": "#FFFFFF",
    "canvas": "#F5F8FD",
    "sidebar": "#F3F6FB",
    "border": "#DDE6F2",
    "success": "#16A34A",
    "success_bg": "#ECFDF5",
    "warning": "#D97706",
    "warning_bg": "#FFFBEB",
    "danger": "#DC2626",
    "danger_bg": "#FEF2F2",
    "info": "#0284C7",
    "info_bg": "#EFF6FF",
}


def logo_data_uri(name: str = "forus.png") -> str:
    """Devuelve el logo como data URI, o cadena vacia si no esta el archivo."""
    for candidate in (name, "forus.png", "forus.svg", "logo.png"):
        path = settings.ASSETS_DIR / candidate
        if path.exists():
            mime = "image/svg+xml" if path.suffix.lower() == ".svg" else "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{encoded}"
    return ""


def _css() -> str:
    p = PALETTE
    return f"""
    <style>
    :root {{
        --rcc-ink: {p['ink']};
        --rcc-ink-soft: {p['ink_soft']};
        --rcc-primary: {p['primary']};
        --rcc-primary-dark: {p['primary_dark']};
        --rcc-border: {p['border']};
        --rcc-surface: {p['surface']};
        --rcc-canvas: {p['canvas']};
        --rcc-radius: 18px;
        --rcc-shadow: 0 12px 28px rgba(15,23,42,0.07);
        --rcc-sidebar-w: {SIDEBAR_WIDTH}px;
    }}

    /* ---------- lienzo general ---------- */
    [data-testid="stAppViewContainer"] {{ background: var(--rcc-canvas); }}
    [data-testid="stHeader"] {{ background: transparent; }}
    [data-testid="stToolbar"], .stDeployButton {{ display: none !important; }}
    #MainMenu, footer {{ visibility: hidden; }}

    /* Sin `max-width` fijo: sumado al ancho de la barra lateral desbordaba a
       lo ancho y recortaba las tarjetas de KPI en pantallas medianas. */
    .main .block-container,
    [data-testid="stMainBlockContainer"] {{
        padding-top: 1.4rem;
        padding-bottom: 4rem;
        max-width: 100% !important;
    }}
    html, body, [class*="css"] {{
        font-family: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
        color: var(--rcc-ink);
    }}

    /* ---------- barra lateral permanente ----------
       Se apagan TODOS los mecanismos de colapso de Streamlit. */
    [data-testid="stSidebarCollapseButton"],
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="collapsedControl"],
    [data-testid="stSidebarResizeHandle"],
    [data-testid="stSidebarHeader"] button,
    button[kind="headerNoPadding"] {{
        display: none !important;
        visibility: hidden !important;
        pointer-events: none !important;
        width: 0 !important;
    }}
    /* Streamlit colapsa la barra con `transform: translateX(-Xpx)`, asi que ese
       es el unico override que hace falta para dejarla fija. NO se tocan
       `position`, `left`, `margin-left` ni `flex`: Streamlit los usa para
       calcular donde empieza el area principal, y forzarlos hacia que el
       contenido se montara encima de la barra. */
    section[data-testid="stSidebar"],
    section[data-testid="stSidebar"][aria-expanded="false"],
    section[data-testid="stSidebar"][aria-expanded="true"] {{
        visibility: visible !important;
        opacity: 1 !important;
        transform: none !important;
        transition: none !important;
        width: var(--rcc-sidebar-w) !important;
        min-width: var(--rcc-sidebar-w) !important;
        max-width: var(--rcc-sidebar-w) !important;
        background: {p['sidebar']} !important;
        border-right: 1px solid var(--rcc-border) !important;
    }}
    /* Los contenedores internos ocupan el 100% del ancho ya reservado por la
       barra. Fijarles los mismos 340px que al `section` los hacia desbordar
       por el padding del padre, y el contenido salia cortado a la derecha. */
    section[data-testid="stSidebar"] > div,
    div[data-testid="stSidebarContent"],
    div[data-testid="stSidebarUserContent"] {{
        width: 100% !important;
        min-width: 0 !important;
        max-width: 100% !important;
        overflow-x: hidden !important;
    }}
    section[data-testid="stSidebar"] > div {{ padding: 18px 14px 26px !important; }}
    /* Nada dentro de la barra puede ser mas ancho que ella. */
    section[data-testid="stSidebar"] * {{ max-width: 100% !important; }}
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] li {{ color: #172554 !important; }}

    /* ---------- marca en la barra lateral ---------- */
    .rcc-brand {{
        display: block;
        margin: 0 0 18px;
        padding: 18px 16px;
        border: 1px solid var(--rcc-border);
        border-radius: 20px;
        background: linear-gradient(160deg, #FFFFFF 0%, #F7FAFF 100%);
        box-shadow: var(--rcc-shadow);
        text-align: center;
    }}
    .rcc-brand img {{ max-width: 150px; max-height: 52px; object-fit: contain; }}
    .rcc-brand-fallback {{
        color: #17269A; font-size: 30px; font-weight: 950; letter-spacing: -0.05em; line-height: 1;
    }}
    .rcc-brand-fallback small {{
        display: block; margin-top: 4px; font-size: 8px;
        letter-spacing: 0.22em; font-weight: 900; color: #17269A;
    }}
    .rcc-brand-app {{
        margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--rcc-border);
        font-size: 13px; font-weight: 900; color: var(--rcc-ink); line-height: 1.25;
    }}
    .rcc-brand-app span {{
        display: block; margin-top: 3px; font-size: 11px;
        font-weight: 750; color: #64748B; letter-spacing: 0.02em;
    }}

    .rcc-side-label {{
        display: block; margin: 18px 0 8px;
        font-size: 12px; font-weight: 950; letter-spacing: 0.06em;
        text-transform: uppercase; color: #0B1B46 !important;
    }}
    .rcc-side-card {{
        border: 1px solid var(--rcc-border); border-radius: 16px;
        background: var(--rcc-surface); padding: 14px 15px; margin-bottom: 12px;
        box-shadow: 0 8px 18px rgba(15,23,42,0.05);
    }}

    /* ---------- estado del sistema (una sola tarjeta) ---------- */
    .rcc-status {{
        border: 1px solid var(--rcc-border); border-radius: 16px;
        background: var(--rcc-surface); padding: 6px 14px; margin: 4px 0 14px;
        box-shadow: 0 8px 18px rgba(15,23,42,0.05);
    }}
    .rcc-status-row {{
        display: grid; grid-template-columns: 8px 1fr auto;
        align-items: center; gap: 9px; padding: 9px 0;
        border-bottom: 1px solid #EDF2F9; font-size: 12.5px;
    }}
    .rcc-status-row:last-child {{ border-bottom: 0; }}
    .rcc-status-row i {{
        width: 8px; height: 8px; border-radius: 50%; display: block;
        background: {p['success']}; box-shadow: 0 0 0 3px rgba(22,163,74,.14);
    }}
    .rcc-status-row i.warn {{
        background: {p['warning']}; box-shadow: 0 0 0 3px rgba(217,119,6,.16);
    }}
    .rcc-status-row span {{ color: #64748B !important; font-weight: 800; }}
    .rcc-status-row b {{
        color: var(--rcc-ink); font-weight: 900; max-width: 150px;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: right;
    }}

    .rcc-side-foot {{
        display: flex; justify-content: space-between; align-items: center; gap: 8px;
        margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--rcc-border);
        font-size: 11.5px; font-weight: 750; color: #7C8AA3 !important;
    }}
    .rcc-side-foot span {{ color: #7C8AA3 !important; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .rcc-side-card p {{ margin: 0 0 6px; font-size: 13px; font-weight: 800; line-height: 1.4; }}
    .rcc-side-card p:last-child {{ margin-bottom: 0; }}
    .rcc-side-card b {{ color: var(--rcc-ink); }}

    /* ---------- rail de pasos ---------- */
    .rcc-steps {{ display: flex; flex-direction: column; gap: 6px; margin-bottom: 6px; }}
    .rcc-step {{
        display: grid; grid-template-columns: 30px 1fr; align-items: center; gap: 11px;
        padding: 10px 12px; border-radius: 13px;
        border: 1px solid transparent; background: transparent;
        font-size: 13.5px; font-weight: 850; color: #55637A;
    }}
    .rcc-step i {{
        display: grid; place-items: center; width: 26px; height: 26px; border-radius: 9px;
        font-style: normal; font-size: 12.5px; font-weight: 950;
        background: #E7EDF7; color: #64748B;
    }}
    .rcc-step.done {{ color: #15803D; }}
    .rcc-step.done i {{ background: {p['success_bg']}; color: {p['success']}; }}
    .rcc-step.active {{
        background: {p['info_bg']}; border-color: #BFDBFE; color: var(--rcc-ink);
        box-shadow: 0 8px 18px rgba(37,99,235,0.10);
    }}
    .rcc-step.active i {{ background: var(--rcc-primary); color: #FFFFFF; }}

    /* ---------- cabecera principal ---------- */
    .rcc-hero {{
        position: relative; overflow: hidden;
        border-radius: 24px; padding: 26px 30px;
        background: linear-gradient(125deg, {p['primary_deep']} 0%, {p['primary_dark']} 55%, #3B82F6 100%);
        color: #FFFFFF; margin-bottom: 20px;
        box-shadow: 0 22px 46px rgba(19,48,107,0.26);
    }}
    .rcc-hero::after {{
        content: ""; position: absolute; right: -70px; top: -90px;
        width: 260px; height: 260px; border-radius: 50%;
        background: rgba(255,255,255,0.10);
    }}
    .rcc-hero::before {{
        content: ""; position: absolute; right: 90px; bottom: -120px;
        width: 200px; height: 200px; border-radius: 50%;
        background: rgba(255,255,255,0.07);
    }}
    .rcc-hero-eyebrow {{
        margin: 0 0 8px; font-size: 11.5px; font-weight: 900;
        letter-spacing: 0.18em; text-transform: uppercase; color: #BFDBFE;
    }}
    .rcc-hero h1 {{ margin: 0; font-size: 31px; line-height: 1.1; font-weight: 950; }}
    .rcc-hero p {{ margin: 10px 0 0; max-width: 720px; font-size: 15px; font-weight: 700; color: #E4EEFF; }}
    .rcc-hero-chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; position: relative; z-index: 1; }}
    .rcc-chip {{
        padding: 6px 13px; border-radius: 999px; font-size: 12px; font-weight: 850;
        background: rgba(255,255,255,0.16); color: #FFFFFF;
        border: 1px solid rgba(255,255,255,0.26); backdrop-filter: blur(3px);
    }}
    .rcc-chip.ok {{ background: rgba(22,163,74,0.28); border-color: rgba(134,239,172,0.55); }}
    .rcc-chip.warn {{ background: rgba(217,119,6,0.30); border-color: rgba(253,224,71,0.55); }}

    /* ---------- KPIs ---------- */
    .rcc-kpi-grid {{
        display: grid; grid-template-columns: repeat(5, minmax(0,1fr));
        gap: 13px; margin: 4px 0 22px;
    }}
    .rcc-kpi {{
        position: relative; overflow: hidden;
        background: var(--rcc-surface); border: 1px solid var(--rcc-border);
        border-radius: var(--rcc-radius); padding: 16px 17px 15px;
        box-shadow: var(--rcc-shadow);
    }}
    .rcc-kpi::before {{
        content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px;
        background: var(--rcc-primary);
    }}
    .rcc-kpi.ok::before {{ background: {p['success']}; }}
    .rcc-kpi.warn::before {{ background: {p['warning']}; }}
    .rcc-kpi.bad::before {{ background: {p['danger']}; }}
    .rcc-kpi.neutral::before {{ background: #94A3B8; }}
    .rcc-kpi span {{
        display: block; font-size: 11.5px; font-weight: 900; line-height: 1.25;
        letter-spacing: 0.05em; text-transform: uppercase; color: #64748B;
    }}
    .rcc-kpi strong {{
        display: block; margin-top: 9px; font-size: 30px; line-height: 1;
        font-weight: 950; color: var(--rcc-ink); font-variant-numeric: tabular-nums;
    }}
    .rcc-kpi em {{ display: block; margin-top: 7px; font-size: 12px; font-style: normal; font-weight: 750; color: #7C8AA3; }}
    @media (max-width: 1280px) {{ .rcc-kpi-grid {{ grid-template-columns: repeat(3, minmax(0,1fr)); }} }}
    @media (max-width: 820px)  {{ .rcc-kpi-grid {{ grid-template-columns: repeat(2, minmax(0,1fr)); }} }}

    /* ---------- secciones y tarjetas ---------- */
    .rcc-section {{ margin: 26px 0 12px; }}
    .rcc-section p {{
        margin: 0 0 4px; font-size: 11.5px; font-weight: 900;
        letter-spacing: 0.14em; text-transform: uppercase; color: var(--rcc-primary);
    }}
    .rcc-section h3 {{ margin: 0; font-size: 21px; font-weight: 950; color: var(--rcc-ink); }}
    .rcc-section small {{ display: block; margin-top: 5px; font-size: 13.5px; font-weight: 700; color: var(--rcc-ink-soft); }}

    .rcc-panel {{
        background: var(--rcc-surface); border: 1px solid var(--rcc-border);
        border-radius: var(--rcc-radius); padding: 18px 20px; box-shadow: var(--rcc-shadow);
        margin-bottom: 14px;
    }}

    .rcc-note {{
        display: flex; gap: 11px; align-items: flex-start;
        border-radius: 14px; padding: 13px 15px; margin-bottom: 10px;
        font-size: 13.5px; font-weight: 750; line-height: 1.55;
    }}
    .rcc-note b {{ font-weight: 950; }}
    .rcc-note i {{ font-style: normal; font-size: 15px; line-height: 1.3; }}
    .rcc-note.info {{ background: {p['info_bg']}; border: 1px solid #BFDBFE; color: #0B3E82; }}
    .rcc-note.ok {{ background: {p['success_bg']}; border: 1px solid #BBF7D0; color: #06603F; }}
    .rcc-note.warn {{ background: {p['warning_bg']}; border: 1px solid #FDE68A; color: #92400E; }}
    .rcc-note.bad {{ background: {p['danger_bg']}; border: 1px solid #FECACA; color: #991B1B; }}

    /* ---------- controles ---------- */
    .stButton button, .stDownloadButton button {{
        border-radius: 12px !important; min-height: 44px !important;
        font-weight: 900 !important; letter-spacing: 0.01em;
        border: 1px solid var(--rcc-border) !important;
        white-space: nowrap !important;
        transition: transform .12s ease, box-shadow .12s ease;
    }}
    .stButton button:hover, .stDownloadButton button:hover {{
        transform: translateY(-1px);
        box-shadow: 0 10px 20px rgba(35,103,255,0.16) !important;
    }}
    .stButton button[kind="primary"], .stDownloadButton button[kind="primary"] {{
        background: var(--rcc-primary) !important; border-color: var(--rcc-primary) !important;
        color: #FFFFFF !important;
    }}
    [data-testid="stFileUploaderDropzone"] {{
        border-radius: var(--rcc-radius) !important;
        border: 1.5px dashed #A9C2EA !important;
        background: #FBFDFF !important;
    }}
    [data-testid="stMetricValue"] {{ font-weight: 950; color: var(--rcc-ink); }}
    [data-testid="stDataFrame"] {{
        border: 1px solid var(--rcc-border); border-radius: 14px; overflow: hidden;
    }}
    .stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid var(--rcc-border); }}
    .stTabs [data-baseweb="tab"] {{
        border-radius: 11px 11px 0 0; font-weight: 900; font-size: 14px; padding: 9px 16px;
    }}
    .stTabs [aria-selected="true"] {{ background: {p['info_bg']}; color: var(--rcc-primary) !important; }}
    [data-testid="stExpander"] {{
        border: 1px solid var(--rcc-border) !important; border-radius: 14px !important;
        background: var(--rcc-surface); box-shadow: 0 6px 14px rgba(15,23,42,0.04);
    }}
    [data-testid="stProgressBar"] > div > div {{ background: var(--rcc-primary); }}
    </style>
    """


def apply_theme() -> None:
    """Inyecta el tema. Llamar una sola vez por rerun, antes de dibujar."""
    st.markdown(_css(), unsafe_allow_html=True)


def _login_css() -> str:
    p = PALETTE
    return f"""
    <style>
    /* La barra lateral no existe en el intro: aun no hay sesion.
       El selector repite `section` para ganarle en especificidad a las reglas
       de sidebar permanente de `apply_theme()`, por si ambas se inyectan. */
    section[data-testid="stSidebar"],
    [data-testid="stSidebar"] {{ display: none !important; }}
    [data-testid="stToolbar"], .stDeployButton {{ display: none !important; }}
    #MainMenu, footer {{ visibility: hidden; }}

    [data-testid="stAppViewContainer"] {{ background: {p['navy']} !important; }}
    [data-testid="stHeader"] {{ background: transparent; }}
    .main .block-container {{ padding-top: 40px; padding-bottom: 48px; max-width: 640px; }}

    .st-key-login_card {{
        width: min(448px, calc(100vw - 32px));
        margin: 0 auto;
        overflow: hidden;
        border-radius: 16px;
        background: #FFFFFF;
        box-shadow: 0 28px 80px rgba(0,0,0,.32);
        color-scheme: light;
    }}

    .login-head {{
        padding: 32px 32px 34px;
        text-align: center;
        background: linear-gradient(180deg, {p['primary']} 0%, {p['primary_dark']} 100%);
        color: #FFFFFF;
    }}
    .login-logo-row {{
        display: flex; align-items: center; justify-content: center;
        gap: 22px; margin-bottom: 24px;
    }}
    .login-forus-logo {{
        min-width: 178px; height: 64px; border-radius: 10px; background: #FFFFFF;
        display: grid; place-items: center; padding: 8px 14px; box-sizing: border-box;
    }}
    .login-forus-logo img {{ max-width: 100%; max-height: 48px; object-fit: contain; }}
    .login-forus-fallback {{
        color: {p['primary_deep']}; font-size: 34px; line-height: 1;
        font-weight: 950; letter-spacing: .01em;
    }}
    .login-forus-fallback small {{
        display: block; margin-top: 3px; color: {p['primary_deep']};
        font-size: 8px; letter-spacing: .22em; font-weight: 900;
    }}
    .login-divider {{ width: 1px; height: 48px; background: rgba(255,255,255,.62); }}
    .login-side-logo {{
        width: 52px; height: 52px; border-radius: 10px; background: #FFFFFF;
        display: grid; place-items: center; box-shadow: 0 10px 22px rgba(15,23,42,.14);
    }}
    .login-side-logo svg {{ width: 30px; height: 30px; display: block; }}

    .login-head h1 {{ margin: 0; font-size: 30px; line-height: 1.12; font-weight: 950; }}
    .login-head p {{ margin: 10px 0 0; color: #EAF2FF; font-size: 16px; font-weight: 750; }}

    .st-key-login_form_area {{ padding: 24px 32px 8px; background: #FFFFFF; color-scheme: light; }}
    .st-key-login_form_area label {{ color: #1E293B !important; font-weight: 850 !important; }}
    .st-key-login_form_area .stTextInput input {{
        border-radius: 12px; min-height: 48px; font-size: 15px;
        background: #F8FAFC !important; border: 1px solid #CBD5E1 !important;
        color: #0F172A !important; caret-color: #0F172A !important;
        -webkit-text-fill-color: #0F172A !important; opacity: 1 !important;
        color-scheme: light !important;
    }}
    .st-key-login_form_area .stTextInput input::placeholder {{
        color: #64748B !important; -webkit-text-fill-color: #64748B !important; opacity: 1 !important;
    }}
    .st-key-login_form_area div[data-baseweb="input"],
    .st-key-login_form_area div[data-baseweb="base-input"] {{
        background: #F8FAFC !important; color: #0F172A !important; color-scheme: light !important;
    }}
    /* El autocompletado de Chrome pinta el input de amarillo y borra el texto. */
    .st-key-login_form_area .stTextInput input:-webkit-autofill,
    .st-key-login_form_area .stTextInput input:-webkit-autofill:hover,
    .st-key-login_form_area .stTextInput input:-webkit-autofill:focus {{
        -webkit-text-fill-color: #0F172A !important;
        -webkit-box-shadow: 0 0 0 1000px #F8FAFC inset !important;
        transition: background-color 9999s ease-out 0s;
    }}
    .st-key-login_form_area .stTextInput button,
    .st-key-login_form_area .stTextInput svg {{ color: #475569 !important; fill: currentColor !important; }}
    /* Streamlit encoge el contenedor del boton al ancho del texto: hay que
       estirar el wrapper, no solo el <button>. */
    .st-key-login_form_area [data-testid="stFormSubmitButton"],
    .st-key-login_form_area .stButton {{ width: 100% !important; }}
    .st-key-login_form_area .stButton button,
    .st-key-login_form_area [data-testid="stFormSubmitButton"] button {{
        width: 100% !important; min-height: 48px; border-radius: 12px;
        background: {p['primary']} !important; border-color: {p['primary']} !important;
        color: #FFFFFF !important; font-weight: 950 !important;
        white-space: nowrap !important; line-height: 1.12 !important;
    }}

    .login-note {{
        padding: 4px 32px 30px; text-align: center;
        color: #64748B; font-size: 13px; font-weight: 750; background: #FFFFFF;
    }}
    .login-foot {{
        margin: 26px auto 0; width: min(448px, calc(100vw - 32px)); text-align: center;
        color: #FFFFFF; font-size: 14px; line-height: 1.7; font-weight: 750;
    }}
    .login-foot strong {{ display: block; margin-bottom: 6px; font-weight: 850; }}
    .login-foot span {{ color: #93C5FD; }}

    @media (max-width: 560px) {{
        .main .block-container {{ padding-top: 20px; }}
        .login-head {{ padding: 26px 20px 28px; }}
        .login-head h1 {{ font-size: 25px; }}
        .st-key-login_form_area {{ padding: 22px 22px 6px; }}
        .login-forus-logo {{ min-width: 152px; }}
    }}
    </style>
    """


def apply_login_theme() -> None:
    """Tema exclusivo de la pantalla de acceso (fondo oscuro, sin sidebar)."""
    st.markdown(_login_css(), unsafe_allow_html=True)
