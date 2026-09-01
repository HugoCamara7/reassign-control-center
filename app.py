"""Reassign Control Center - Forus Peru.

Automatiza la reasignacion de pedidos que no pudieron despacharse:

    Subir archivo -> Validar -> Consultar BigQuery -> Reasignar -> Revisar -> Descargar

BigQuery se usa **solo para leer stock**. El descuento de unidades ocurre en
memoria durante la corrida, para no comprometer dos veces el mismo stock.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from config import settings
from core import engine, excel_io
from core.priority import load_priority, priority_to_frame
from core.stock_source import (
    ManualStockSource,
    build_stock_index,
    is_bigquery_configured,
    resolve_stock_table,
    secrets_to_source,
    stock_cutoff,
)
from core.excel_io import normalize_status
from core.validation import resolve_columns, validate
from scripts.build_priority_template import build_bytes as build_priority_bytes
from ui import components as ui
from ui.theme import apply_login_theme, apply_theme

st.set_page_config(
    page_title=settings.APP_NAME,
    page_icon="RC",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Secretos y sesion
# ---------------------------------------------------------------------------
def secrets_section(name: str) -> dict[str, Any]:
    try:
        if name in st.secrets:
            return dict(st.secrets[name])
    except Exception:
        pass
    return {}


def bigquery_secrets() -> dict[str, Any]:
    config = secrets_section("bigquery")
    account = secrets_section("gcp_service_account")
    if account:
        config["service_account_info"] = account
    return config


def require_login() -> bool:
    """Pantalla de acceso. Solo se exige si existe la seccion [app_auth].

    Sin esa seccion en `secrets.toml` la app queda abierta, para que un
    despliegue nuevo no se bloquee a si mismo antes de configurarse.
    """
    auth = secrets_section("app_auth")
    if not auth:
        return True
    if st.session_state.get("authenticated"):
        return True

    users = {str(key).strip().casefold(): str(value) for key, value in dict(auth.get("users", {})).items()}
    if not users and auth.get("username"):
        users[str(auth["username"]).strip().casefold()] = str(auth.get("password", ""))

    apply_login_theme()
    with st.container(key="login_card"):
        ui.login_header()
        with st.container(key="login_form_area"):
            with st.form("login_form"):
                email = st.text_input("Correo electronico", placeholder="nombre.apellido@forus.pe")
                password = st.text_input("Contrasena", type="password", placeholder="********")
                submitted = st.form_submit_button("Ingresar", type="primary", width="stretch")
        ui.login_note()
    ui.login_footer()

    if submitted:
        expected = users.get(email.strip().casefold())
        if expected and hmac.compare_digest(str(password), expected):
            st.session_state["authenticated"] = True
            st.session_state["auth_user"] = email.strip().casefold()
            st.rerun()
        st.error("Usuario o contrasena incorrectos.")
    return False


def reset_run(keep_config: bool = True) -> None:
    for key in ("payload", "report", "stock", "result", "stock_error"):
        st.session_state.pop(key, None)
    if not keep_config:
        st.session_state.pop("priority_bytes", None)


def current_step() -> int:
    if "result" in st.session_state:
        return 5
    if "stock" in st.session_state:
        return 4
    if "report" in st.session_state and not st.session_state["report"].has_errors:
        return 3
    if "payload" in st.session_state:
        return 2
    return 1


# ---------------------------------------------------------------------------
# Barra lateral
# ---------------------------------------------------------------------------
def render_sidebar(config, bq_ready: bool) -> str:
    ui.sidebar_brand()
    ui.sidebar_steps(current_step())

    # --- estado del sistema, en una sola tarjeta ---------------------------
    # La prioridad viaja versionada en el repositorio: si esta cargada no hay
    # nada que pedirle al usuario, asi que no se muestra ningun cargador.
    table = resolve_stock_table(bigquery_secrets())
    ui.sidebar_status(
        [
            ("Prioridad", f"{config.store_count} tiendas", bool(config.rules)),
            ("Stock", table.split(".")[-1] if bq_ready else "sin conectar", bq_ready),
            ("Estados", ", ".join(config.target_statuses), True),
        ]
    )

    mode = "BigQuery (solo lectura)" if bq_ready else "Archivo de stock"
    if not bq_ready:
        stock_file = st.sidebar.file_uploader(
            "Archivo de stock (.xlsx / .csv)",
            type=["xlsx", "xls", "csv"],
            key="stock_upload",
            help=(
                "Columnas minimas: sku, cod_tienda, stock. Si trae 'stock_reservado', "
                "esas unidades se descuentan del disponible. Solo se pide porque "
                "BigQuery no esta configurado."
            ),
        )
        if stock_file is not None:
            st.session_state["stock_file"] = (stock_file.getvalue(), stock_file.name)

    with st.sidebar.expander("Cambiar prioridad de tiendas", expanded=False):
        st.caption(f"Activa: {config.source}")
        uploaded = st.file_uploader(
            "Subir otra (.xlsx)",
            type=["xlsx"],
            key="priority_upload",
            label_visibility="collapsed",
        )
        if uploaded is not None:
            st.session_state["priority_bytes"] = uploaded.getvalue()
        st.download_button(
            "Descargar plantilla",
            data=build_priority_bytes(),
            file_name="prioridad_tiendas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )

    st.sidebar.divider()
    if st.sidebar.button("Reiniciar proceso", width="stretch"):
        reset_run()
        st.rerun()
    if st.session_state.get("authenticated"):
        if st.sidebar.button("Cerrar sesion", width="stretch"):
            st.session_state.clear()
            st.rerun()

    usuario = st.session_state.get("auth_user", "")
    ui.sidebar_footer(usuario, f"v{settings.APP_VERSION}")
    return mode


# ---------------------------------------------------------------------------
# Pasos
# ---------------------------------------------------------------------------
def step_upload() -> None:
    ui.section("Paso 1", "Subir archivo de pedidos", "Formato de carga de reasignacion (.xls o .xlsx).")
    uploaded = st.file_uploader(
        "Arrastra el archivo o selecciona uno",
        type=["xls", "xlsx"],
        key="orders_upload",
        label_visibility="collapsed",
    )
    if uploaded is None:
        return

    signature = (uploaded.name, uploaded.size)
    if st.session_state.get("orders_signature") == signature and "payload" in st.session_state:
        return

    try:
        payload = excel_io.read_orders(uploaded.getvalue(), uploaded.name)
    except Exception as exc:
        ui.note("bad", "No se pudo leer el archivo", str(exc))
        return

    st.session_state["payload"] = payload
    st.session_state["orders_signature"] = signature
    for key in ("report", "stock", "result", "stock_error"):
        st.session_state.pop(key, None)
    st.rerun()


def available_statuses(payload) -> list[str]:
    """Estados presentes en el archivo, ordenados por frecuencia."""
    resolved = resolve_columns(payload.headers)
    column = resolved.get(settings.COL_STATUS)
    if not column:
        return []
    counts = payload.df[column].map(normalize_status).value_counts()
    return [status for status in counts.index.tolist() if status]


def ensure_status_selection(payload, config) -> None:
    """Fija la seleccion inicial de estados antes de dibujar nada.

    Tiene que correr antes que la barra lateral y la validacion: si se hiciera
    dentro del propio selector, la primera pasada validaria con los estados
    viejos y mostraria un error que ya no corresponde.
    """
    if "estados_objetivo_sel" in st.session_state:
        return
    found = available_statuses(payload)
    if not found:
        return
    coincidencias = [status for status in found if status in config.target_statuses]
    # Si el archivo no trae ninguno de los estados configurados, se preselecciona
    # el mas frecuente: es lo que el usuario venia a reasignar.
    st.session_state["estados_objetivo_sel"] = coincidencias or found[:1]


def render_status_picker(payload) -> None:
    """Selector de estados a reasignar, con lo que trae el archivo.

    Sin esto habria que editar la hoja 'Parametros' del Excel de configuracion
    cada vez que el sistema de origen cambia el nombre de un estado.
    """
    found = available_statuses(payload)
    if not found:
        return

    ui.section(
        "Alcance",
        "Elegir que pedidos reasignar",
        "Marca los estados que la app debe intentar reasignar. El resto de las filas "
        "se conservan intactas en el archivo final.",
    )

    counts = payload.df[resolve_columns(payload.headers)[settings.COL_STATUS]].map(normalize_status).value_counts()
    st.multiselect(
        "Estados a reasignar",
        options=found,
        key="estados_objetivo_sel",
        format_func=lambda status: f"{status}  ({counts.get(status, 0)} filas)",
        help="Tambien puede fijarse en la hoja 'Parametros' de la configuracion de prioridad.",
    )


def step_validate(config) -> None:
    payload = st.session_state["payload"]
    ui.section(
        "Paso 2",
        "Validar estructura y reglas",
        f"{payload.source_name} • hoja '{payload.sheet_name}' • "
        f"{payload.n_rows:,} filas • {len(payload.headers)} columnas.".replace(",", " "),
    )

    report = validate(payload.df, payload.headers, config)
    st.session_state["report"] = report

    ui.kpi_grid(
        [
            ("Ordenes recibidas", report.total_rows, "neutral", f"{len(report.columns)} columnas"),
            ("Ordenes validas", report.target_rows, "ok" if report.target_rows else "warn",
             ", ".join(config.target_statuses)),
            ("Alertas", len(report.warnings), "warn" if report.warnings else "neutral", ""),
            ("Errores", len(report.errors), "bad" if report.errors else "neutral", ""),
        ]
    )

    # Los avisos de lectura y de configuracion solo aparecen si hay algo real
    # que decir; en una corrida normal esta zona queda vacia.
    for issue in config.issues:
        ui.note("warn", "Configuracion de prioridad", issue)

    ui.validation_block(report)

    with st.expander("Detalle tecnico", expanded=False):
        left, right = st.columns(2)
        left.caption("Estados encontrados")
        left.dataframe(
            pd.DataFrame(
                [
                    {
                        "Estado": status or "(vacio)",
                        "Filas": count,
                        "Se reasigna": "SI" if status in config.target_statuses else "NO",
                    }
                    for status, count in sorted(report.statuses.items(), key=lambda item: -item[1])
                ]
            ),
            width="stretch", hide_index=True,
        )
        right.caption("Columnas reconocidas")
        right.dataframe(
            pd.DataFrame(
                [{"Campo": key, "Columna del archivo": value} for key, value in report.resolved.items()]
            ),
            width="stretch", hide_index=True,
        )
        if payload.notes:
            st.caption("Lectura del archivo")
            for note_text in payload.notes:
                st.markdown(f"- {note_text}")
        st.caption(f"Prioridad activa · {config.source}")
        st.dataframe(priority_to_frame(config), width="stretch", hide_index=True, height=220)


def step_stock(config, mode: str) -> None:
    payload = st.session_state["payload"]
    report = st.session_state["report"]
    skus = engine.target_skus(payload.df, report.resolved, config)

    ui.section(
        "Paso 3",
        "Consultar stock disponible",
        f"{len(skus):,} SKU unicos a consultar. La consulta es de solo lectura: "
        "BigQuery nunca se modifica.".replace(",", " "),
    )

    if st.session_state.get("stock_error"):
        ui.note("bad", "Fallo la consulta de stock", st.session_state["stock_error"])

    columns = st.columns([1, 1.6, 1.4])
    launch = columns[0].button("Consultar stock", type="primary", width="stretch")
    # Se decide antes de consultar, no despues: en BigQuery el descuento se
    # aplica en la propia consulta.
    descontar_reserva = columns[1].checkbox(
        "Descontar unidades reservadas",
        value=config.flag("descontar_stock_reservado"),
        key="descontar_stock_reservado",
        help=(
            "Lo reservado ya tiene dueno y no se puede volver a prometer. Con esto "
            "activado, una tienda con 3 unidades y 3 reservadas queda en 0 disponible "
            "y no recibe reasignaciones."
        ),
    )
    config.params["descontar_stock_reservado"] = "SI" if descontar_reserva else "NO"

    if not launch:
        return

    st.session_state.pop("stock_error", None)
    include_central = config.flag("incluir_stock_bodega_central")

    try:
        with st.spinner("Consultando stock..."):
            if mode.startswith("BigQuery"):
                source = secrets_to_source(bigquery_secrets(), include_central, descontar_reserva)
                stock = source.fetch(skus)
            else:
                cached = st.session_state.get("stock_file")
                if not cached:
                    raise ValueError("Sube un archivo de stock en la barra lateral.")
                frame = excel_io.read_stock_file(cached[0], cached[1])
                stock = ManualStockSource(frame, include_central, descontar_reserva).fetch(skus)
    except Exception as exc:
        st.session_state["stock_error"] = str(exc)
        st.rerun()
        return

    st.session_state["stock"] = stock
    st.session_state.pop("result", None)
    st.rerun()


def step_reassign(config) -> None:
    payload = st.session_state["payload"]
    report = st.session_state["report"]
    stock = st.session_state["stock"]
    cutoff = stock_cutoff(stock)

    covered = stock["sku"].nunique() if not stock.empty else 0
    requested = len(engine.target_skus(payload.df, report.resolved, config))
    disponible = int(stock["stock"].sum()) if not stock.empty else 0
    reservado = (
        int(stock["stock_reservado"].sum())
        if not stock.empty and "stock_reservado" in stock.columns
        else 0
    )

    ui.section(
        "Paso 4",
        "Reasignar",
        "Recorre la lista de prioridad y descuenta el stock en memoria a medida que asigna.",
    )
    ui.kpi_grid(
        [
            ("SKU consultados", requested, "neutral", "Estados objetivo"),
            ("SKU con stock", covered, "ok" if covered else "warn", f"{requested - covered} sin stock en ninguna tienda"),
            ("Combinaciones SKU/tienda", len(stock), "", "Filas devueltas"),
            ("Unidades reservadas", reservado, "warn" if reservado else "", "Descontadas del disponible"),
            ("Unidades disponibles", disponible, "", "Ya neto de reservas"),
            ("Fecha de corte", cutoff or "-", "neutral", "Ultimo cierre de stock"),
        ]
    )

    # La reserva no se puede descontar si no llego en la fuente. Callarselo
    # devolveria el problema original: tiendas con todo reservado tratadas
    # como disponibles.
    if config.flag("descontar_stock_reservado") and not stock.empty:
        columnas_reserva = list(stock.attrs.get("reserva_columnas") or [])
        if columnas_reserva:
            ui.note(
                "ok",
                f"Reserva descontada: {reservado:,} unidades".replace(",", " "),
                "El disponible ya es neto. Columnas de reserva usadas: "
                f"{', '.join(columnas_reserva)}.",
            )
        else:
            ui.note(
                "warn",
                "No se encontro columna de reserva en la fuente de stock",
                "El disponible que se muestra es el stock bruto: puede incluir unidades "
                "ya reservadas. Declara la columna en los secrets con "
                "'stock_reserved_columns' (BigQuery) o agregala al archivo de stock "
                "como 'stock_reservado'.",
            )

    # El stock es una foto: si la fuente trajo historico, se aviso cuantas
    # filas de cortes anteriores se dejaron fuera. Sumarlas inventaria stock.
    descartadas = int(stock.attrs.get("filas_descartadas_por_fecha", 0))
    if descartadas:
        ui.note(
            "info",
            f"Se ignoraron {descartadas:,} filas de cortes anteriores".replace(",", " "),
            f"La fuente trajo historico. Solo se usa la foto del {cutoff}, porque el stock "
            "no se acumula entre fechas.",
        )

    ajustes = st.columns([1.1, 1.1, 1.6])
    reserva = ajustes[0].number_input(
        "Unidades a dejar en la tienda",
        min_value=0,
        max_value=20,
        value=config.number("reserva_por_tienda", 1),
        step=1,
        help=(
            "Cuantas unidades deberia conservar la tienda despues de ceder. Con 1, "
            "se evita vaciarla; si ninguna tienda de la lista puede conservarlas, "
            "recien ahi se acepta la ultima unidad. 0 desactiva la regla."
        ),
    )
    config.params["reserva_por_tienda"] = str(reserva)

    ordenar = ajustes[1].checkbox(
        "Dentro de la misma prioridad, preferir la tienda con mas stock",
        value=config.flag("ordenar_por_stock"),
        help=(
            "La lista de prioridad viene en bandas con empates. Con esto activado, "
            "dentro de una banda gana la tienda mas surtida."
        ),
    )
    config.params["ordenar_por_stock"] = "SI" if ordenar else "NO"

    include_trace = ajustes[2].checkbox(
        "Incluir columnas de trazabilidad en el Excel final",
        value=False,
        help=(
            "Agrega Reasig_Resultado, Reasig_Stock_Disponible, etc. "
            "Desactivalo si la plataforma destino solo acepta las columnas originales."
        ),
    )

    if st.button("Ejecutar reasignacion", type="primary", width="content"):
        with st.spinner("Reasignando..."):
            result = engine.reassign(
                df=payload.df,
                headers=payload.headers,
                resolved=report.resolved,
                config=config,
                stock_index=build_stock_index(stock),
                stock_cutoff=cutoff,
                include_trace=include_trace,
            )
        st.session_state["result"] = result
        st.rerun()


def step_review(config) -> None:
    payload = st.session_state["payload"]
    result: engine.ReassignmentResult = st.session_state["result"]
    kpis = result.kpis

    ui.section("Paso 5", "Revisar resultado", "KPIs de la corrida y detalle pedido por pedido.")
    ui.kpi_from_result(kpis)

    if kpis.sin_stock:
        ui.note(
            "warn",
            f"{kpis.sin_stock} pedidos quedaron como '{settings.RESULT_NO_OPTION}'",
            "Revisa la pestana 'Sin opcion' para ver el motivo exacto de cada uno.",
        )
    if kpis.errores:
        ui.note("bad", f"{kpis.errores} filas con error", "Filas sin SKU legible: no se pudo consultar stock.")
    if not kpis.sin_stock and not kpis.errores and kpis.pedidos_a_reasignar:
        ui.note("ok", "Todos los pedidos fueron reasignados", "El archivo esta listo para descargar.")

    preview = result.preview
    tabs = st.tabs(["Vista previa", "Reasignados", "Sin opcion", "Por tienda", "Validacion"])

    with tabs[0]:
        search = st.text_input("Buscar por pedido, SKU o tienda", key="preview_search")
        view = preview
        if search.strip():
            needle = search.strip().lower()
            mask = view.apply(
                lambda row: needle in " ".join(str(value).lower() for value in row.values), axis=1
            )
            view = view[mask]
        st.dataframe(view, width="stretch", hide_index=True, height=430)
        st.caption(f"{len(view):,} de {len(preview):,} filas.".replace(",", " "))

    with tabs[1]:
        done = result.detail[result.detail["Resultado"].isin([settings.RESULT_REASSIGNED, engine.RESULT_PARTIAL])]
        st.dataframe(done, width="stretch", hide_index=True, height=430)

    with tabs[2]:
        missing = result.detail[result.detail["Resultado"] != settings.RESULT_REASSIGNED]
        missing = missing[missing["Resultado"] != engine.RESULT_PARTIAL]
        st.dataframe(missing, width="stretch", hide_index=True, height=430)

    with tabs[3]:
        if result.store_summary.empty:
            st.info("Ninguna tienda recibio unidades en esta corrida.")
        else:
            st.dataframe(result.store_summary, width="stretch", hide_index=True, height=430)
            st.bar_chart(
                result.store_summary.set_index("nom_tienda")["unidades_reasignadas"],
                width="stretch",
            )

    with tabs[4]:
        st.dataframe(st.session_state["report"].to_frame(), width="stretch", hide_index=True)

    # --- descarga ----------------------------------------------------------
    ui.section(
        "Paso 6",
        "Descargar Excel final",
        f"Mantiene las {len(payload.headers)} columnas originales y escribe la tienda en "
        f"'{result.output_column}'.",
    )

    # Auditoria independiente del resultado: recuenta el stock consumido desde
    # el detalle, sin confiar en el motor. Si algo no cuadra, se ve antes de
    # que el archivo salga hacia la plataforma.
    problemas = engine.verify_result(
        result, build_stock_index(st.session_state["stock"]), config, payload.headers
    )
    if problemas:
        for problema in problemas:
            ui.note("bad", "Revision del resultado", problema)
        st.stop()
    ui.note(
        "ok",
        "Resultado verificado",
        "Stock consumido dentro de lo disponible, prioridad respetada, columnas "
        "originales intactas y sin opcion correctamente marcados.",
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    with st.spinner("Generando archivos..."):
        final_bytes = excel_io.write_orders(
            result.output_df, result.output_headers, sheet_name=payload.sheet_name
        )
        report_bytes = excel_io.write_report(
            {
                "KPIs": kpis.to_frame(),
                "Detalle": result.detail,
                "Por tienda": result.store_summary,
                "Validacion": st.session_state["report"].to_frame(),
                "Prioridad usada": priority_to_frame(config),
            }
        )

    left, right = st.columns(2)
    left.download_button(
        "Descargar Excel de carga",
        data=final_bytes,
        file_name=f"reasignacion_{stamp}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        width="stretch",
    )
    right.download_button(
        "Descargar reporte operativo",
        data=report_bytes,
        file_name=f"reporte_reasignacion_{stamp}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )

    ui.note(
        "info",
        "Que contiene cada archivo",
        "El Excel de carga conserva el formato de origen y es el que se sube a la plataforma. "
        "El reporte operativo es interno: KPIs, detalle por pedido, uso por tienda y validaciones.",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    # El intro pinta su propio tema (fondo oscuro, sin sidebar); el tema
    # principal solo se aplica una vez que hay sesion.
    if not require_login():
        return
    apply_theme()

    config = load_priority(st.session_state.get("priority_bytes"))
    payload = st.session_state.get("payload")
    if payload is not None:
        ensure_status_selection(payload, config)
    seleccion = st.session_state.get("estados_objetivo_sel")
    if seleccion:
        config.params["estados_objetivo"] = ",".join(seleccion)
    bq_secrets = bigquery_secrets()
    bq_ready = is_bigquery_configured(bq_secrets)
    mode = render_sidebar(config, bq_ready)

    step = current_step()
    chips: list[tuple[str, str]] = [
        (f"Paso {step} de 6", ""),
        ("BigQuery conectado" if bq_ready else "BigQuery sin configurar", "ok" if bq_ready else "warn"),
        (f"{config.store_count} tiendas en prioridad" if config.rules else "Sin prioridad cargada",
         "ok" if config.rules else "warn"),
        ("Solo lectura de stock", "ok"),
    ]
    ui.hero(
        "Reasignacion de pedidos sin stock",
        "Sube el archivo de pedidos, la app identifica los estados "
        f"{' y '.join(config.target_statuses)}, consulta el stock disponible y reasigna "
        "cada pedido a la tienda de mayor prioridad que pueda despacharlo.",
        chips,
    )

    step_upload()
    if "payload" not in st.session_state:
        ui.note(
            "info",
            "Esperando el archivo de pedidos",
            "Se aceptan .xls y .xlsx. Se lee la primera hoja del libro y se conservan todas las columnas.",
        )
        return

    render_status_picker(st.session_state["payload"])
    if not st.session_state.get("estados_objetivo_sel"):
        ui.note(
            "warn",
            "Selecciona al menos un estado",
            "Sin estados marcados no hay pedidos que reasignar.",
        )
        return
    step_validate(config)
    report = st.session_state["report"]
    if report.has_errors:
        ui.note("bad", "Corrige los errores para continuar", "El proceso se detiene en el paso de validacion.")
        return

    step_stock(config, mode)
    if "stock" not in st.session_state:
        return

    step_reassign(config)
    if "result" not in st.session_state:
        return

    step_review(config)


if __name__ == "__main__":
    main()
