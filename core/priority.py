"""Carga de la prioridad de tiendas desde configuracion editable.

La regla de oro del proyecto: **la prioridad nunca se escribe en Python**.
Vive en `config/prioridad_tiendas.xlsx` y el area comercial la edita sin
tocar codigo. Este modulo solo la lee, la valida y la ordena.

Estructura del archivo (3 hojas):

`Prioridad`
    sitio | marca | cod_tienda | nom_tienda | prioridad | activo |
    stock_seguridad | max_unidades
    `sitio` y `marca` aceptan `*` como comodin. Una fila con el sitio
    exacto siempre gana sobre una fila con `*`.

`Tiendas`
    cod_tienda | nom_tienda | activo | stock_seguridad
    Maestro de tiendas. Sirve para traducir codigo <-> nombre y para
    apagar una tienda en todos los sitios de una sola vez.

`Parametros`
    parametro | valor | descripcion
    Reglas de negocio conmutables (ver `config/settings.DEFAULT_PARAMS`).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from config import settings
from core.excel_io import (
    as_text,
    normalize_store_code,
    normalize_store_name,
    to_int,
)

WILDCARD = "*"


@dataclass(frozen=True)
class StoreRule:
    """Una tienda candidata dentro de la lista de prioridad."""

    cod_tienda: str
    nom_tienda: str
    prioridad: int
    stock_seguridad: int = 0
    max_unidades: int = 0
    sitio: str = WILDCARD
    marca: str = WILDCARD

    @property
    def key(self) -> str:
        return self.cod_tienda or normalize_store_name(self.nom_tienda)


@dataclass
class PriorityConfig:
    """Configuracion completa ya normalizada y lista para el motor."""

    rules: list[dict[str, Any]] = field(default_factory=list)
    stores: dict[str, dict[str, Any]] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    source: str = "defaults"

    # -- parametros ---------------------------------------------------------
    def param(self, name: str, default: str = "") -> str:
        return as_text(self.params.get(name, settings.DEFAULT_PARAMS.get(name, default)))

    def flag(self, name: str) -> bool:
        return self.param(name).strip().upper() in ("SI", "SÍ", "S", "YES", "Y", "1", "TRUE")

    def number(self, name: str, default: int = 0) -> int:
        return to_int(self.param(name), default)

    @property
    def target_statuses(self) -> list[str]:
        from core.excel_io import normalize_status

        raw = self.param("estados_objetivo") or ",".join(settings.DEFAULT_TARGET_STATUSES)
        values = [normalize_status(part) for part in raw.split(",")]
        return [value for value in values if value] or list(settings.DEFAULT_TARGET_STATUSES)

    @property
    def output_column(self) -> str:
        return self.param("columna_salida") or settings.COL_REASSIGNED

    # -- tiendas ------------------------------------------------------------
    def store_name(self, code: str) -> str:
        entry = self.stores.get(normalize_store_code(code))
        return entry["nom_tienda"] if entry else ""

    def store_code_by_name(self, name: str) -> str:
        target = normalize_store_name(name)
        if not target:
            return ""
        for code, entry in self.stores.items():
            if normalize_store_name(entry["nom_tienda"]) == target:
                return code
        return ""

    def is_store_active(self, code: str) -> bool:
        entry = self.stores.get(normalize_store_code(code))
        return bool(entry["activo"]) if entry else True

    # -- resolucion de prioridad -------------------------------------------
    def rules_for(self, sitio: str, marca: str) -> list[StoreRule]:
        """Lista ordenada de tiendas candidatas para un sitio y marca.

        Una fila con el `sitio` exacto desplaza por completo a las filas
        comodin; lo mismo ocurre despues con la `marca`. Asi el resultado
        es siempre predecible y facil de explicar al area comercial.
        """
        site_key = _norm_key(sitio)
        brand_key = _norm_key(marca)

        candidates = [
            rule
            for rule in self.rules
            if rule["sitio"] in (WILDCARD, site_key) and rule["marca"] in (WILDCARD, brand_key)
        ]
        if not candidates:
            return []

        if any(rule["sitio"] == site_key for rule in candidates):
            candidates = [rule for rule in candidates if rule["sitio"] == site_key]
        if any(rule["marca"] == brand_key for rule in candidates):
            candidates = [rule for rule in candidates if rule["marca"] == brand_key]

        # Una misma tienda puede aparecer varias veces: gana la prioridad menor.
        best: dict[str, dict[str, Any]] = {}
        for rule in candidates:
            key = rule["cod_tienda"] or normalize_store_name(rule["nom_tienda"])
            if not key:
                continue
            current = best.get(key)
            if current is None or rule["prioridad"] < current["prioridad"]:
                best[key] = rule

        ordered = sorted(best.values(), key=lambda item: (item["prioridad"], item["nom_tienda"]))

        global_safety = self.number("stock_seguridad_global", 0)
        global_cap = self.number("max_unidades_por_tienda", 0)

        result: list[StoreRule] = []
        for rule in ordered:
            code = rule["cod_tienda"]
            master = self.stores.get(code, {})
            if not rule["activo"] or not master.get("activo", True):
                continue
            safety = max(
                to_int(rule.get("stock_seguridad"), 0),
                to_int(master.get("stock_seguridad"), 0),
                global_safety,
            )
            cap = to_int(rule.get("max_unidades"), 0) or global_cap
            result.append(
                StoreRule(
                    cod_tienda=code,
                    nom_tienda=rule["nom_tienda"] or master.get("nom_tienda", ""),
                    prioridad=rule["prioridad"],
                    stock_seguridad=safety,
                    max_unidades=cap,
                    sitio=rule["sitio"],
                    marca=rule["marca"],
                )
            )
        return result

    @property
    def store_count(self) -> int:
        return len({rule["cod_tienda"] for rule in self.rules if rule["cod_tienda"]})

    @property
    def site_count(self) -> int:
        return len({rule["sitio"] for rule in self.rules})


def _norm_key(value: Any) -> str:
    """Clave comparable para sitio/marca: sin espacios, simbolos ni tildes."""
    text = as_text(value).lower()
    if text in ("", WILDCARD, "todos", "todas", "all"):
        return WILDCARD
    replacements = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}
    for old, new in replacements.items():
        text = text.replace(old, new)
    return "".join(char for char in text if char.isalnum())


def _pick(row: pd.Series, *names: str) -> Any:
    for name in names:
        if name in row.index:
            value = row[name]
            if as_text(value):
                return value
    return ""


def _lower_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [as_text(column).strip().lower().replace(" ", "_") for column in df.columns]
    return df


# Encabezados que identifican a la tabla de prioridad, en cualquier variante.
_PRIORITY_HEADERS = {"prioridad", "orden", "priority"}
_STORE_HEADERS = {
    "cod_tienda", "codigo_tienda", "bodega", "numbodega", "id_tienda", "id", "codigo",
    "nom_tienda", "nombre_tienda", "tienda", "nombrebodega", "nombre",
}


def _detect_priority_sheet(book: pd.ExcelFile) -> str:
    """Primera hoja que tenga una columna de prioridad y una de tienda."""
    for name in book.sheet_names:
        try:
            head = _lower_columns(book.parse(name, nrows=0, dtype=object))
        except Exception:
            continue
        columns = set(head.columns)
        if columns & _PRIORITY_HEADERS and columns & _STORE_HEADERS:
            return name
    return ""


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------
def load_priority(source: str | Path | bytes | None = None) -> PriorityConfig:
    """Carga la configuracion desde archivo, bytes subidos, o defaults."""
    if source is None:
        source = settings.PRIORITY_FILE

    config = PriorityConfig(params=dict(settings.DEFAULT_PARAMS))

    if isinstance(source, (bytes, bytearray)):
        handle: Any = io.BytesIO(source)
        config.source = "archivo subido"
    else:
        path = Path(source)
        if not path.exists() and settings.EXAMPLE_PRIORITY_FILE.exists():
            # Despliegue nuevo: el archivo operativo aun no existe, pero el
            # ejemplo viaja en el repositorio y permite trabajar de inmediato.
            path = settings.EXAMPLE_PRIORITY_FILE
            config.issues.append(
                f"No se encontro '{Path(source).name}': se esta usando "
                f"'{path.name}' del repositorio. Revisa el orden de prioridad y sube "
                "tu propia version desde la barra lateral."
            )
        if not path.exists():
            config.issues.append(
                f"No se encontro '{path.name}'. Se usan los valores por defecto; "
                "genera la plantilla desde la barra lateral."
            )
            config.source = "defaults"
            return config
        handle = path
        config.source = path.name

    try:
        book = pd.ExcelFile(handle)
    except Exception as exc:  # pragma: no cover - depende del archivo del usuario
        config.issues.append(f"No se pudo abrir la configuracion de prioridad: {exc}")
        return config

    available = {name.strip().lower(): name for name in book.sheet_names}

    # --- Parametros --------------------------------------------------------
    params_sheet = available.get(settings.SHEET_PARAMS.lower())
    if params_sheet:
        try:
            params_df = _lower_columns(book.parse(params_sheet, dtype=object).dropna(how="all"))
            for _, row in params_df.iterrows():
                key = as_text(_pick(row, "parametro", "parámetro", "clave", "key")).lower()
                if not key:
                    continue
                config.params[key] = as_text(_pick(row, "valor", "value"))
        except Exception as exc:
            config.issues.append(f"Hoja '{settings.SHEET_PARAMS}' ilegible: {exc}")

    # --- Tiendas -----------------------------------------------------------
    stores_sheet = available.get(settings.SHEET_STORES.lower())
    if stores_sheet:
        try:
            stores_df = _lower_columns(book.parse(stores_sheet, dtype=object).dropna(how="all"))
            for _, row in stores_df.iterrows():
                code = normalize_store_code(
                    _pick(row, "cod_tienda", "codigo_tienda", "numbodega", "bodega", "id_tienda", "id")
                )
                name = as_text(
                    _pick(row, "nom_tienda", "nombre_tienda", "nombrebodega", "tienda", "nombre")
                )
                if not code and not name:
                    continue
                config.stores[code] = {
                    "cod_tienda": code,
                    "nom_tienda": name,
                    "activo": _truthy(_pick(row, "activo", "estado", "on/off"), default=True),
                    "stock_seguridad": to_int(_pick(row, "stock_seguridad", "stock_seg"), 0),
                }
        except Exception as exc:
            config.issues.append(f"Hoja '{settings.SHEET_STORES}' ilegible: {exc}")
    else:
        config.issues.append(f"Falta la hoja '{settings.SHEET_STORES}' en la configuracion.")

    # --- Prioridad ---------------------------------------------------------
    priority_sheet = available.get(settings.SHEET_PRIORITY.lower())
    if not priority_sheet:
        # El area comercial suele mandar un archivo de una sola hoja con
        # `ID Tienda | nombre | Prioridad`. Se detecta por sus columnas en vez
        # de exigir que la hoja se llame "Prioridad".
        priority_sheet = _detect_priority_sheet(book)
        if priority_sheet:
            config.issues.append(
                f"No hay hoja '{settings.SHEET_PRIORITY}': se leyo la prioridad de "
                f"'{priority_sheet}'. Sin hoja 'Parametros', se usan las reglas por defecto."
            )
    if not priority_sheet:
        config.issues.append(
            f"Falta la hoja '{settings.SHEET_PRIORITY}': sin ella no se puede reasignar."
        )
        return config

    try:
        priority_df = _lower_columns(book.parse(priority_sheet, dtype=object).dropna(how="all"))
    except Exception as exc:
        config.issues.append(f"Hoja '{settings.SHEET_PRIORITY}' ilegible: {exc}")
        return config

    for line, (_, row) in enumerate(priority_df.iterrows(), start=2):
        code = normalize_store_code(
            _pick(row, "cod_tienda", "codigo_tienda", "bodega", "numbodega", "id_tienda", "id", "codigo")
        )
        name = as_text(_pick(row, "nom_tienda", "nombre_tienda", "tienda", "nombrebodega", "nombre"))
        if not code and not name:
            continue
        if not code:
            code = config.store_code_by_name(name)
        if not name:
            name = config.stores.get(code, {}).get("nom_tienda", "")
        if not name:
            config.issues.append(
                f"Fila {line} de '{settings.SHEET_PRIORITY}': tienda {code} sin nombre en el maestro."
            )

        raw_priority = _pick(row, "prioridad", "orden", "priority")
        priority_value = to_int(raw_priority, -1)
        if priority_value < 0:
            priority_value = 9999
            config.issues.append(
                f"Fila {line} de '{settings.SHEET_PRIORITY}': prioridad vacia o no numerica "
                f"para '{name or code}'. Se mando al final de la lista."
            )

        config.rules.append(
            {
                "sitio": _norm_key(_pick(row, "sitio", "site", "tienda_online")),
                "marca": _norm_key(_pick(row, "marca", "brand")),
                "cod_tienda": code,
                "nom_tienda": name,
                "prioridad": priority_value,
                "activo": _truthy(_pick(row, "activo", "estado", "on/off"), default=True),
                "stock_seguridad": to_int(_pick(row, "stock_seguridad", "stock_seg"), 0),
                "max_unidades": to_int(_pick(row, "max_unidades", "tope_unidades"), 0),
            }
        )

    if not config.rules:
        config.issues.append(f"La hoja '{settings.SHEET_PRIORITY}' no tiene filas utilizables.")

    # Cualquier tienda de prioridad que no este en el maestro se registra igual.
    for rule in config.rules:
        code = rule["cod_tienda"]
        if code and code not in config.stores:
            config.stores[code] = {
                "cod_tienda": code,
                "nom_tienda": rule["nom_tienda"],
                "activo": True,
                "stock_seguridad": 0,
            }

    return config


def _truthy(value: Any, default: bool = True) -> bool:
    text = as_text(value).strip().upper()
    if not text:
        return default
    if text in ("0", "NO", "N", "FALSE", "OFF", "INACTIVO", "APAGADO"):
        return False
    if text in ("1", "SI", "SÍ", "S", "YES", "Y", "TRUE", "ON", "ACTIVO"):
        return True
    return default


def priority_to_frame(config: PriorityConfig) -> pd.DataFrame:
    """Vista tabular de la prioridad, para mostrarla y re-exportarla."""
    if not config.rules:
        return pd.DataFrame(
            columns=[
                "sitio",
                "marca",
                "cod_tienda",
                "nom_tienda",
                "prioridad",
                "activo",
                "stock_seguridad",
                "max_unidades",
            ]
        )
    frame = pd.DataFrame(config.rules)
    frame["activo"] = frame["activo"].map(lambda flag: "SI" if flag else "NO")
    return frame.sort_values(["sitio", "marca", "prioridad"]).reset_index(drop=True)
