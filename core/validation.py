"""Validacion del archivo de pedidos antes de tocar BigQuery.

Todo lo que aqui se detecta se muestra al usuario en el paso "Validar".
Los hallazgos se clasifican en tres niveles:

* `error`   : bloquea la ejecucion (falta una columna obligatoria).
* `alerta`  : deja continuar pero el resultado puede ser incompleto.
* `info`    : contexto util, sin accion requerida.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import pandas as pd

from config import settings
from core.excel_io import (
    SAFE_INT_LIMIT,
    as_text,
    normalize_sku,
    normalize_status,
    normalize_store_code,
    normalize_store_name,
    to_units,
)
from core.priority import PriorityConfig

LEVEL_ERROR = "error"
LEVEL_WARNING = "alerta"
LEVEL_INFO = "info"


@dataclass
class Finding:
    level: str
    title: str
    detail: str
    count: int = 0


@dataclass
class ValidationReport:
    columns: list[str] = field(default_factory=list)
    resolved: dict[str, str] = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)
    missing_recommended: list[str] = field(default_factory=list)
    statuses: dict[str, int] = field(default_factory=dict)
    target_rows: int = 0
    total_rows: int = 0
    findings: list[Finding] = field(default_factory=list)
    created_output_column: bool = False

    @property
    def has_errors(self) -> bool:
        return any(finding.level == LEVEL_ERROR for finding in self.findings)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == LEVEL_ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == LEVEL_WARNING]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.level == LEVEL_INFO]

    def add(self, level: str, title: str, detail: str, count: int = 0) -> None:
        self.findings.append(Finding(level=level, title=title, detail=detail, count=count))

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "Nivel": finding.level.upper(),
                    "Hallazgo": finding.title,
                    "Detalle": finding.detail,
                    "Filas": finding.count or "",
                }
                for finding in self.findings
            ]
        )


def repair_mojibake(text: str) -> str:
    """Repara texto UTF-8 leido como latin-1 (`MÃ©todo` -> `Metodo`).

    Solo se usa para **comparar** encabezados. El nombre original se conserva
    intacto en el archivo de salida, porque la plataforma destino lo espera asi.
    """
    if "Ã" not in text and "Â" not in text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _canonical(name: str) -> str:
    """Encabezado comparable: sin tildes, sin simbolos, en minusculas.

    Tolera tres cosas que aparecen en los archivos reales: mojibake
    (`MÃ©todo_de_Despacho`), tildes (`Método_de_Despacho`) y sufijos numericos
    que agrega Excel al duplicar columnas (`Sitio_1`).
    """
    text = repair_mojibake(as_text(name))
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("¿", "").replace("?", "")
    text = "".join(char if char.isalnum() else "_" for char in text).strip("_")
    return re.sub(r"_\d+$", "", text)


def resolve_columns(headers: list[str]) -> dict[str, str]:
    """Mapea nombre canonico del proyecto -> encabezado real del archivo."""
    index = {_canonical(header): header for header in headers}
    resolved: dict[str, str] = {}
    for canonical_name, aliases in settings.COLUMN_ALIASES.items():
        candidates = [canonical_name, *aliases]
        for candidate in candidates:
            key = _canonical(candidate)
            if key in index:
                resolved[canonical_name] = index[key]
                break
    return resolved


def validate(df: pd.DataFrame, headers: list[str], config: PriorityConfig) -> ValidationReport:
    report = ValidationReport(columns=list(headers), total_rows=len(df))
    report.resolved = resolve_columns(headers)
    resolved = report.resolved

    # --- columnas ----------------------------------------------------------
    report.missing_required = [
        name for name in settings.REQUIRED_COLUMNS if name not in resolved
    ]
    report.missing_recommended = [
        name for name in settings.RECOMMENDED_COLUMNS if name not in resolved
    ]

    if report.missing_required:
        report.add(
            LEVEL_ERROR,
            "Faltan columnas obligatorias",
            "No se encontraron (ni por alias): " + ", ".join(report.missing_required),
            len(report.missing_required),
        )
    if report.missing_recommended:
        report.add(
            LEVEL_WARNING,
            "Faltan columnas recomendadas",
            "El proceso corre igual, pero con menos control: "
            + ", ".join(report.missing_recommended),
            len(report.missing_recommended),
        )

    output_column = config.output_column
    if output_column not in headers and settings.COL_REASSIGNED not in resolved:
        report.created_output_column = True
        report.add(
            LEVEL_INFO,
            f"La columna '{output_column}' la crea la app",
            "Tu archivo no necesita traerla: se agrega automaticamente al final del "
            "archivo de salida, con la tienda destino de cada pedido. No hay nada que hacer.",
        )

    if report.missing_required:
        return report

    col_status = resolved[settings.COL_STATUS]
    col_sku = resolved[settings.COL_SKU]
    col_units = resolved[settings.COL_UNITS]

    # --- estados -----------------------------------------------------------
    statuses = df[col_status].map(normalize_status)
    report.statuses = statuses.value_counts().to_dict()
    targets = set(config.target_statuses)
    is_target = statuses.isin(targets)
    report.target_rows = int(is_target.sum())

    unknown = sorted(set(report.statuses) - targets - {""})
    if unknown:
        ignored = int(statuses.isin(unknown).sum())
        report.add(
            LEVEL_INFO,
            "Estados fuera de alcance",
            "No se reasignan (quedan tal cual en el archivo final): " + ", ".join(unknown),
            ignored,
        )
    if report.target_rows == 0:
        encontrados = ", ".join(
            f"{estado} ({filas})"
            for estado, filas in sorted(report.statuses.items(), key=lambda item: -item[1])
            if estado
        )
        report.add(
            LEVEL_ERROR,
            "Ningun pedido coincide con los estados seleccionados",
            f"Estan seleccionados: {', '.join(sorted(targets))}. "
            f"Pero el archivo trae: {encontrados}. "
            "Elige los estados correctos en el selector de arriba (el cambio se aplica al momento).",
        )
        return report

    target_df = df[is_target]

    # --- SKU ---------------------------------------------------------------
    skus = target_df[col_sku].map(normalize_sku)
    empty_sku = int((skus == "").sum())
    if empty_sku:
        report.add(
            LEVEL_WARNING,
            "Pedidos sin SKU",
            "Sin SKU no se puede consultar stock: quedaran como ERROR.",
            empty_sku,
        )
    repeated = skus[skus != ""].value_counts()
    repeated = repeated[repeated > 1]
    if not repeated.empty:
        report.add(
            LEVEL_INFO,
            "SKU repetidos entre pedidos",
            f"{len(repeated)} SKU aparecen en mas de un pedido (maximo {int(repeated.max())} veces). "
            "El motor descuenta el stock a medida que reasigna para no comprometerlo dos veces.",
            int(repeated.sum()),
        )

    # --- unidades ----------------------------------------------------------
    units = target_df[col_units].map(to_units)
    zero_units = int((units <= 0).sum())
    if zero_units:
        report.add(
            LEVEL_WARNING,
            "Pedidos sin unidades validas",
            "Unidades vacias, cero o no numericas: se tratan como 1 unidad.",
            zero_units,
        )

    # --- tienda de origen --------------------------------------------------
    if config.flag("excluir_tienda_origen"):
        name_col = resolved.get(settings.COL_STORE_NAME)
        code_col = resolved.get(settings.COL_STORE_CODE)
        if name_col or code_col:
            has_origin = pd.Series(False, index=target_df.index)
            if name_col:
                has_origin |= target_df[name_col].map(normalize_store_name).ne("")
            if code_col:
                has_origin |= target_df[code_col].map(normalize_store_code).ne("")
            without_origin = int((~has_origin).sum())
            if without_origin:
                report.add(
                    LEVEL_WARNING,
                    "Pedidos sin tienda de origen",
                    f"{without_origin} de {report.target_rows} filas no traen "
                    f"'{settings.COL_STORE_NAME}' ni '{settings.COL_STORE_CODE}'. "
                    "En esas filas no se puede aplicar la regla de excluir la tienda origen.",
                    without_origin,
                )
        else:
            report.add(
                LEVEL_WARNING,
                "No se puede excluir la tienda de origen",
                "El archivo no trae columna de tienda asignada. La regla queda inactiva.",
            )

    # --- ShGroup: perdida de precision -------------------------------------
    shgroup_col = resolved.get(settings.COL_SHGROUP)
    if shgroup_col:
        def _is_lossy(value: object) -> bool:
            text = as_text(value)
            return text.isdigit() and len(text) >= 16

        lossy = int(df[shgroup_col].map(_is_lossy).sum())
        if lossy:
            report.add(
                LEVEL_WARNING,
                "ShGroup con IDs de mas de 15 digitos",
                f"Excel los guarda como numero de coma flotante, asi que los ultimos digitos "
                f"pueden no ser exactos ya en el archivo de origen (limite: {SAFE_INT_LIMIT:,}). "
                "La app los escribe como texto para no empeorarlo, pero conviene verificar "
                "contra el sistema de origen.",
                lossy,
            )

        multi = df.groupby(df[shgroup_col].map(as_text)).size()
        multi = multi[(multi.index != "") & (multi > 1)]
        if not multi.empty:
            mode = "misma tienda" if config.flag("agrupar_por_shgroup") else "linea por linea"
            report.add(
                LEVEL_INFO,
                "Despachos con varias lineas",
                f"{len(multi)} ShGroup tienen mas de una linea. "
                f"Modo actual: {mode} (parametro 'agrupar_por_shgroup').",
                int(multi.sum()),
            )

    # --- encabezados con mojibake ------------------------------------------
    broken = [header for header in headers if "Ã" in header or "Â" in header]
    if broken:
        report.add(
            LEVEL_INFO,
            "Encabezados con codificacion danada",
            "Se conservan tal cual para no romper la carga en la plataforma destino: "
            + ", ".join(broken[:4])
            + ("..." if len(broken) > 4 else ""),
            len(broken),
        )

    # --- cobertura de la configuracion de prioridad ------------------------
    site_col = resolved.get(settings.COL_SITE)
    brand_col = resolved.get(settings.COL_BRAND)
    if site_col and config.rules:
        uncovered: dict[str, int] = {}
        for site, group in target_df.groupby(target_df[site_col].map(as_text)):
            brand = as_text(group[brand_col].iloc[0]) if brand_col else ""
            if not config.rules_for(site, brand):
                uncovered[site or "(sin sitio)"] = len(group)
        if uncovered:
            detail = ", ".join(f"{site} ({count})" for site, count in sorted(uncovered.items()))
            report.add(
                LEVEL_WARNING,
                "Sitios sin lista de prioridad",
                "Estos sitios no tienen tiendas configuradas y quedaran "
                f"'{settings.RESULT_NO_OPTION}': {detail}. "
                "Agrega filas con esos sitios (o una fila con sitio '*') en la hoja 'Prioridad'.",
                sum(uncovered.values()),
            )

    if not config.rules:
        report.add(
            LEVEL_ERROR,
            "Sin configuracion de prioridad",
            "No hay tiendas cargadas en la hoja 'Prioridad'. "
            "Genera la plantilla desde la barra lateral y subela.",
        )

    return report
