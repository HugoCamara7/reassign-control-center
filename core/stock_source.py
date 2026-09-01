"""Fuentes de stock. BigQuery es **solo lectura**: nunca se escribe alli.

Se ofrecen dos proveedores con la misma interfaz:

* `BigQueryStockSource` : consulta `stg_pe_central_stock_bi` (produccion).
* `ManualStockSource`   : lee un Excel/CSV de stock. Sirve para probar la
  app sin credenciales y para simulaciones puntuales del equipo.

Ambos devuelven un DataFrame con el mismo contrato:

    sku | cod_tienda | stock_tiendas | stock_bodega |
    reservado_tiendas | reservado_bodega | stock_reservado | stock | fecha_corte

`stock` es el **disponible real**: unidades fisicas menos lo reservado. Una
tienda con 3 unidades y 3 reservadas queda en 0 y no recibe reasignaciones,
que es justo lo que se busca: lo reservado ya tiene dueno.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol

import pandas as pd

from config import settings
from core.excel_io import as_text, normalize_sku, normalize_store_code, to_int

STOCK_COLUMNS = [
    "sku",
    "cod_tienda",
    "stock_tiendas",
    "stock_bodega",
    "reservado_tiendas",
    "reservado_bodega",
    "stock_reservado",
    "stock",
    "fecha_corte",
]

# Consulta parametrizada. Se filtra por SKU para no traer la tabla completa y
# se toma unicamente la ultima `fecha_corte` disponible.
STOCK_QUERY = """
WITH ultimo_corte AS (
  SELECT MAX(fecha_corte) AS fecha_corte
  FROM `{table}`
)
SELECT
  CAST(s.id_producto AS STRING)              AS sku,
  CAST(s.codigo_tienda AS STRING)            AS cod_tienda,
  SUM(COALESCE(CAST(s.stock_tiendas AS INT64), 0)) AS stock_tiendas,
  SUM(COALESCE(CAST(s.stock_bodega  AS INT64), 0)) AS stock_bodega,
  {reservado_tiendas}                        AS reservado_tiendas,
  {reservado_bodega}                         AS reservado_bodega,
  CAST(MAX(s.fecha_corte) AS STRING)         AS fecha_corte
FROM `{table}` AS s
JOIN ultimo_corte AS u
  ON s.fecha_corte = u.fecha_corte
WHERE CAST(s.id_producto AS STRING) IN UNNEST(@skus)
GROUP BY sku, cod_tienda
"""

# BigQuery limita el tamano de los parametros; los SKU se mandan por lotes.
SKU_BATCH_SIZE = 5000


class StockSource(Protocol):
    name: str

    def fetch(self, skus: Iterable[str]) -> pd.DataFrame: ...


def empty_stock_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=STOCK_COLUMNS)


# Nombres que puede traer una consulta de stock segun de donde venga.
# `id_producto` / `codigo_tienda` son los de `stg_pe_central_stock_bi`, que es
# lo que devuelve el `stock_query` de Catalogo Control Center si se reutiliza.
QUERY_COLUMN_ALIASES = {
    "sku": ("sku", "id_producto", "codint_ma"),
    "cod_tienda": ("cod_tienda", "codigo_tienda", "bodega", "numbodega"),
    "stock_tiendas": ("stock_tiendas", "stock_tienda"),
    "stock_bodega": ("stock_bodega",),
    # Reserva de sala. El alias generico va al final: si la fuente distingue
    # sala de bodega, mandan las columnas especificas.
    "reservado_tiendas": (
        "reservado_tiendas",
        "reservado_tienda",
        "stock_reservado_tiendas",
        "stock_reservado_tienda",
        "reserva_tiendas",
        "reserva_tienda",
        "stock_reserva_tiendas",
        "stock_reservado",
        "stock_reserva",
        "reservado",
        "reserva",
        "reservas",
        "unidades_reservadas",
        "cantidad_reservada",
        "stock_comprometido",
        "comprometido",
    ),
    "reservado_bodega": (
        "reservado_bodega",
        "stock_reservado_bodega",
        "reserva_bodega",
        "stock_reserva_bodega",
        "reservado_almacen",
        "stock_reservado_almacen",
    ),
    "fecha_corte": ("fecha_corte", "fecha"),
}

# Columnas de la tabla de stock que representan unidades reservadas. Se buscan
# en el esquema real antes de armar la consulta: la tabla de produccion no
# tiene por que llamarlas igual que aqui, y pedir una columna inexistente
# rompe la consulta entera.
RESERVED_COLUMN_CANDIDATES = {
    "reservado_tiendas": QUERY_COLUMN_ALIASES["reservado_tiendas"],
    "reservado_bodega": QUERY_COLUMN_ALIASES["reservado_bodega"],
}


def detect_reserved_columns(column_names: Iterable[str]) -> dict[str, list[str]]:
    """Columnas de reserva presentes en un esquema, por destino.

    Devuelve `{"reservado_tiendas": [...], "reservado_bodega": [...]}` con los
    nombres **tal como vienen** en la tabla. Si no hay ninguna, ambas listas
    quedan vacias y el disponible se calcula como antes (sin descuento), pero
    la app lo avisa en pantalla en vez de callarselo.
    """
    disponibles = {as_text(name).strip().lower(): as_text(name).strip() for name in column_names}
    encontradas: dict[str, list[str]] = {"reservado_tiendas": [], "reservado_bodega": []}
    usadas: set[str] = set()
    # Primero bodega: sus nombres son mas especificos y no deben caer en el
    # alias generico de sala.
    for destino in ("reservado_bodega", "reservado_tiendas"):
        for alias in RESERVED_COLUMN_CANDIDATES[destino]:
            if alias in disponibles and alias not in usadas:
                encontradas[destino].append(disponibles[alias])
                usadas.add(alias)
                break
    return encontradas


def split_reserved_override(columns: Iterable[str]) -> dict[str, list[str]]:
    """Columnas de reserva declaradas a mano en los secrets.

    Escape hatch para cuando la tabla usa un nombre que no esta en la lista de
    alias: `stock_reserved_columns = "mi_columna_reservada"`. El nombre decide
    a que cubeta va; lo que menciona bodega descuenta del stock de bodega.
    """
    encontradas: dict[str, list[str]] = {"reservado_tiendas": [], "reservado_bodega": []}
    for column in columns:
        nombre = as_text(column).strip()
        if not nombre:
            continue
        destino = (
            "reservado_bodega"
            if any(palabra in nombre.lower() for palabra in ("bodega", "almacen"))
            else "reservado_tiendas"
        )
        encontradas[destino].append(nombre)
    return encontradas


def _sum_expression(columns: list[str]) -> str:
    """`SUM(COALESCE(...))` de una o varias columnas, o `0` si no hay ninguna."""
    if not columns:
        return "0"
    partes = [f"COALESCE(CAST(s.`{column}` AS INT64), 0)" for column in columns]
    return f"SUM({' + '.join(partes)})"


def build_stock_query(reserved: dict[str, list[str]] | None = None, query: str = "") -> str:
    """Rellena las expresiones de reserva en la consulta de stock."""
    reserved = reserved or {}
    plantilla = query or STOCK_QUERY
    for destino in ("reservado_tiendas", "reservado_bodega"):
        plantilla = plantilla.replace(
            "{" + destino + "}", _sum_expression(reserved.get(destino, []))
        )
    return plantilla


def _rename_query_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Acepta el esquema de `stg_pe_central_stock_bi` ademas del propio."""
    lookup = {str(column).strip().lower(): column for column in df.columns}
    mapping = {}
    for target, aliases in QUERY_COLUMN_ALIASES.items():
        if target in df.columns:
            continue
        for alias in aliases:
            if alias in lookup:
                mapping[lookup[alias]] = target
                break
    return df.rename(columns=mapping) if mapping else df


def latest_cutoff_value(values: pd.Series) -> Any:
    """Ultima fecha de corte, comparando como fecha y no como texto.

    Importa el detalle: si `fecha_corte` llega como texto en formato
    `DD/MM/YYYY`, comparar alfabeticamente elige mal — `31/12/2025` es mayor
    que `20/08/2026` como cadena. Por eso se parsea antes de comparar, y solo
    si eso falla se cae al maximo textual.
    """
    textos = values.map(as_text)
    textos = textos[textos != ""]
    if textos.empty:
        return None
    fechas = pd.to_datetime(textos, errors="coerce", dayfirst=True, format="mixed")
    if fechas.notna().any():
        return textos[fechas == fechas.max()].iloc[0]
    return textos.max()


def keep_latest_cutoff(df: pd.DataFrame) -> tuple[pd.DataFrame, int, str]:
    """Deja solo las filas del ultimo corte. Devuelve `(df, descartadas, corte)`.

    El stock es una foto, no un acumulado: si el origen trae historico, sumar
    todos los cortes multiplica las unidades disponibles e inventa stock que
    no existe.
    """
    if df.empty or "fecha_corte" not in df.columns:
        return df, 0, ""
    corte = latest_cutoff_value(df["fecha_corte"])
    if corte is None:
        return df, 0, ""
    vigentes = df["fecha_corte"].map(as_text) == as_text(corte)
    descartadas = int((~vigentes).sum())
    return df[vigentes].reset_index(drop=True), descartadas, as_text(corte)


def _net_stock(
    df: pd.DataFrame,
    include_central_warehouse: bool,
    subtract_reserved: bool,
) -> tuple[pd.Series, pd.Series]:
    """Devuelve `(reserva_efectiva, disponible)` fila a fila.

    El stock de bodega solo suma en la bodega central (320): en una tienda
    fisica corresponde a otro almacen y no es despachable desde ahi. La reserva
    sigue exactamente la misma regla — si esas unidades no entraron al
    disponible, su reserva tampoco puede descontarse, o se restaria dos veces.

    Y el punto de todo esto: lo reservado ya tiene dueno. Una tienda con 3
    unidades y 3 reservadas queda en 0 disponible y deja de ser candidata.
    """
    central = df["cod_tienda"] == settings.CENTRAL_WAREHOUSE_CODE
    if include_central_warehouse:
        fisico = df["stock_tiendas"] + df["stock_bodega"].where(central, 0)
        reservado = df["reservado_tiendas"] + df["reservado_bodega"].where(central, 0)
    else:
        fisico = df["stock_tiendas"]
        reservado = df["reservado_tiendas"]
    if not subtract_reserved:
        reservado = reservado * 0
    reservado = reservado.clip(lower=0).astype(int)
    disponible = (fisico - reservado).clip(lower=0).astype(int)
    return reservado, disponible


def _finalize(
    df: pd.DataFrame,
    include_central_warehouse: bool,
    subtract_reserved: bool = True,
) -> pd.DataFrame:
    """Normaliza tipos y calcula la columna `stock` efectiva (ya neta)."""
    if df.empty:
        return empty_stock_frame()

    df = _rename_query_columns(df.copy())
    missing = [name for name in ("sku", "cod_tienda") if name not in df.columns]
    if missing:
        raise ValueError(
            "La consulta de stock no devolvio las columnas necesarias "
            f"({', '.join(missing)}). Columnas recibidas: {', '.join(map(str, df.columns))}."
        )
    df["sku"] = df["sku"].map(normalize_sku)
    df["cod_tienda"] = df["cod_tienda"].map(normalize_store_code)
    reserva_en_origen = any(
        column in df.columns for column in ("reservado_tiendas", "reservado_bodega")
    )
    for column in ("stock_tiendas", "stock_bodega", "reservado_tiendas", "reservado_bodega"):
        if column not in df.columns:
            df[column] = 0
        # Una reserva negativa en el origen no puede sumar disponible.
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).clip(lower=0).astype(int)
    if "fecha_corte" not in df.columns:
        df["fecha_corte"] = ""
    df["fecha_corte"] = df["fecha_corte"].map(as_text)

    df["stock_reservado"], df["stock"] = _net_stock(
        df, include_central_warehouse, subtract_reserved
    )

    df = df[df["sku"] != ""]
    df = df[STOCK_COLUMNS].reset_index(drop=True)

    # Garantia unica para las dos fuentes: nunca se mezclan cortes. Si el
    # origen trae historico (o una consulta propia sin filtro de fecha), aqui
    # se queda solo la foto mas reciente.
    df, descartadas, corte = keep_latest_cutoff(df)
    df.attrs["filas_descartadas_por_fecha"] = descartadas
    df.attrs["fecha_corte"] = corte
    df.attrs["reserva_en_origen"] = reserva_en_origen
    df.attrs["reserva_descontada"] = int(df["stock_reservado"].sum()) if not df.empty else 0
    df.attrs["reserva_aplicada"] = bool(subtract_reserved)
    return df


@dataclass
class BigQueryStockSource:
    """Consulta de stock contra BigQuery. Estrictamente de lectura."""

    project_id: str = ""
    job_project_id: str = ""
    table: str = settings.DEFAULT_STOCK_TABLE
    location: str = ""
    service_account_info: dict[str, Any] | None = None
    include_central_warehouse: bool = True
    custom_query: str = ""
    subtract_reserved: bool = True
    reserved_columns: tuple[str, ...] = ()
    name: str = "BigQuery"

    def _client(self):
        try:
            from google.cloud import bigquery
            from google.oauth2 import service_account
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Falta instalar google-cloud-bigquery. Ejecuta: pip install -r requirements.txt"
            ) from exc

        credentials = None
        project = self.project_id
        if self.service_account_info:
            credentials = service_account.Credentials.from_service_account_info(
                dict(self.service_account_info)
            )
            project = project or credentials.project_id
        return bigquery.Client(
            project=(self.job_project_id or project) or None,
            credentials=credentials,
        )

    def test_connection(self) -> tuple[bool, str]:
        """Valida credenciales y acceso a la tabla sin traer datos."""
        try:
            client = self._client()
            table = client.get_table(self.table)
            return True, f"Conectado. {table.full_table_id} ({table.num_rows:,} filas)."
        except Exception as exc:
            return False, str(exc)

    def table_columns(self, client) -> list[str]:
        """Nombres de columna de la tabla de stock. `[]` si no se pueden leer."""
        try:
            return [field.name for field in client.get_table(self.table).schema]
        except Exception:
            return []

    def resolve_reserved_columns(self, client) -> dict[str, list[str]]:
        """Que columnas de la tabla se descuentan como reserva.

        Manda lo declarado en los secrets (`stock_reserved_columns`); si no hay
        nada, se busca en el esquema real. Nunca se adivina un nombre: pedir
        una columna que no existe reventaria la consulta completa.
        """
        if not self.subtract_reserved:
            return {"reservado_tiendas": [], "reservado_bodega": []}
        if self.reserved_columns:
            return split_reserved_override(self.reserved_columns)
        return detect_reserved_columns(self.table_columns(client))

    def fetch(self, skus: Iterable[str]) -> pd.DataFrame:
        from google.cloud import bigquery

        unique = sorted({normalize_sku(sku) for sku in skus if normalize_sku(sku)})
        if not unique:
            return empty_stock_frame()

        client = self._client()
        reserved = self.resolve_reserved_columns(client)
        query = build_stock_query(reserved, self.custom_query or STOCK_QUERY)
        if "{table}" in query:
            query = query.format(table=self.table)

        # Una consulta propia puede no filtrar por SKU (por ejemplo, la de
        # Catalogo Control Center, que trae el corte completo). En ese caso se
        # ejecuta una sola vez, sin parametros, y se filtra despues en memoria.
        filters_by_sku = "@skus" in query
        batches = (
            [unique[start : start + SKU_BATCH_SIZE] for start in range(0, len(unique), SKU_BATCH_SIZE)]
            if filters_by_sku
            else [None]
        )

        frames: list[pd.DataFrame] = []
        for batch in batches:
            job_config = bigquery.QueryJobConfig(
                use_legacy_sql=False,
                query_parameters=(
                    [bigquery.ArrayQueryParameter("skus", "STRING", batch)] if batch is not None else []
                ),
            )
            job = client.query(query, job_config=job_config, location=self.location or None)
            frames.append(job.result().to_dataframe())

        combined = pd.concat(frames, ignore_index=True) if frames else empty_stock_frame()
        result = _finalize(combined, self.include_central_warehouse, self.subtract_reserved)
        if not filters_by_sku and not result.empty:
            result = result[result["sku"].isin(unique)].reset_index(drop=True)
        # La UI necesita saber de donde salio la reserva (o que no se encontro
        # ninguna columna) para poder avisarlo en pantalla.
        result.attrs["reserva_columnas"] = sorted(
            {column for columnas in reserved.values() for column in columnas}
        )
        return result


@dataclass
class ManualStockSource:
    """Stock desde un archivo subido por el usuario (modo sin BigQuery)."""

    frame: pd.DataFrame
    include_central_warehouse: bool = True
    subtract_reserved: bool = True
    name: str = "Archivo de stock"

    COLUMN_ALIASES = {
        "sku": ["sku", "id_producto", "codint_ma", "codigo_sku", "cod_sku"],
        "cod_tienda": [
            "cod_tienda",
            "codigo_tienda",
            "bodega",
            "numbodega",
            "tienda",
            "store",
            "codigo",
        ],
        "stock_tiendas": ["stock_tiendas", "stock_tienda", "stock", "disponible", "stock_disponible"],
        "stock_bodega": ["stock_bodega", "stock_almacen"],
        "reservado_tiendas": list(QUERY_COLUMN_ALIASES["reservado_tiendas"]),
        "reservado_bodega": list(QUERY_COLUMN_ALIASES["reservado_bodega"]),
        "fecha_corte": ["fecha_corte", "fecha", "fecha_stock"],
    }

    def fetch(self, skus: Iterable[str]) -> pd.DataFrame:
        if self.frame is None or self.frame.empty:
            return empty_stock_frame()

        lookup = {as_text(column).strip().lower(): column for column in self.frame.columns}
        resolved: dict[str, str] = {}
        for target, aliases in self.COLUMN_ALIASES.items():
            for alias in aliases:
                if alias in lookup:
                    resolved[target] = lookup[alias]
                    break

        missing = [name for name in ("sku", "cod_tienda") if name not in resolved]
        if missing:
            raise ValueError(
                "El archivo de stock necesita al menos las columnas 'sku' y 'cod_tienda'. "
                f"No se encontraron: {', '.join(missing)}."
            )
        if "stock_tiendas" not in resolved:
            raise ValueError(
                "El archivo de stock necesita una columna de unidades "
                "('stock', 'stock_tiendas' o 'disponible')."
            )

        def _optional(target: str, default: Any = 0):
            return self.frame[resolved[target]] if target in resolved else default

        data = pd.DataFrame(
            {
                "sku": self.frame[resolved["sku"]],
                "cod_tienda": self.frame[resolved["cod_tienda"]],
                "stock_tiendas": self.frame[resolved["stock_tiendas"]],
                "stock_bodega": _optional("stock_bodega"),
                # Si el archivo trae una columna de reserva, se descuenta igual
                # que en BigQuery. Si no la trae, no hay nada que restar.
                "reservado_tiendas": (
                    _optional("reservado_tiendas") if self.subtract_reserved else 0
                ),
                "reservado_bodega": (
                    _optional("reservado_bodega") if self.subtract_reserved else 0
                ),
                "fecha_corte": _optional("fecha_corte", ""),
            }
        )

        wanted = {normalize_sku(sku) for sku in skus if normalize_sku(sku)}
        data["sku"] = data["sku"].map(normalize_sku)
        if wanted:
            data = data[data["sku"].isin(wanted)]

        # `_finalize` ya dejo solo el ultimo corte. Recien despues se consolida
        # un mismo SKU/tienda repetido DENTRO de ese corte; sumar entre cortes
        # distintos inflaria el stock disponible.
        data = _finalize(data, self.include_central_warehouse, self.subtract_reserved)
        if data.empty:
            return data
        atributos = dict(data.attrs)
        consolidado = (
            data.groupby(["sku", "cod_tienda"], as_index=False)
            .agg(
                stock_tiendas=("stock_tiendas", "sum"),
                stock_bodega=("stock_bodega", "sum"),
                reservado_tiendas=("reservado_tiendas", "sum"),
                reservado_bodega=("reservado_bodega", "sum"),
                stock_reservado=("stock_reservado", "sum"),
                stock=("stock", "sum"),
                fecha_corte=("fecha_corte", "max"),
            )
        )
        # El neto se recalcula sobre los totales ya consolidados. Sumar netos
        # por fila subestimaria la reserva cuando una fila queda en cero: 3
        # unidades con 5 reservadas mas 4 sin reserva son 2 disponibles, no 4.
        consolidado["stock_reservado"], consolidado["stock"] = _net_stock(
            consolidado, self.include_central_warehouse, self.subtract_reserved
        )
        consolidado = consolidado[STOCK_COLUMNS]
        atributos["reserva_columnas"] = sorted(
            resolved[target]
            for target in ("reservado_tiendas", "reservado_bodega")
            if target in resolved
        )
        consolidado.attrs.update(atributos)
        return consolidado


def build_stock_index(stock: pd.DataFrame) -> dict[tuple[str, str], int]:
    """Diccionario `(sku, cod_tienda) -> unidades` para acceso O(1)."""
    if stock is None or stock.empty:
        return {}
    return {
        (row.sku, row.cod_tienda): int(row.stock)
        for row in stock.itertuples(index=False)
        if int(row.stock) > 0
    }


def stock_cutoff(stock: pd.DataFrame) -> str:
    if stock is None or stock.empty or "fecha_corte" not in stock.columns:
        return ""
    values = [as_text(value) for value in stock["fecha_corte"] if as_text(value)]
    return max(values) if values else ""


def resolve_stock_table(secrets: dict[str, Any]) -> str:
    """Tabla de stock a consultar.

    **No se usa `table` como respaldo a proposito.** En los secrets de Catalogo
    Control Center `table` apunta a la tabla ARTI (maestro de productos), no al
    stock; ese proyecto resuelve el stock con una consulta fija contra
    `stg_pe_central_stock_bi`. Reutilizar `table` aqui haria que la app buscara
    stock en la tabla equivocada al copiar los mismos secrets.
    """
    return as_text((secrets or {}).get("stock_table")) or settings.DEFAULT_STOCK_TABLE


def reserved_columns_from_secrets(secrets: dict[str, Any]) -> tuple[str, ...]:
    """Columnas de reserva declaradas a mano en `stock_reserved_columns`.

    Acepta lista o texto separado por comas. Solo hace falta si la tabla usa
    un nombre que no esta en los alias conocidos.
    """
    raw = (secrets or {}).get("stock_reserved_columns")
    if raw is None:
        return ()
    if isinstance(raw, str):
        nombres = raw.split(",")
    else:
        nombres = list(raw)
    return tuple(as_text(nombre).strip() for nombre in nombres if as_text(nombre).strip())


def secrets_to_source(
    secrets: dict[str, Any],
    include_central_warehouse: bool,
    subtract_reserved: bool = True,
) -> BigQueryStockSource:
    """Construye el proveedor de BigQuery desde `st.secrets`.

    Acepta el mismo bloque `[bigquery]` + `[gcp_service_account]` que usan las
    demas aplicaciones internas, para poder copiar los secrets sin editarlos.
    """
    config = dict(secrets or {})
    service_account_info = config.get("service_account_info")
    if not service_account_info and config.get("service_account_json"):
        import json

        service_account_info = json.loads(config["service_account_json"])
    return BigQueryStockSource(
        project_id=as_text(config.get("project_id")),
        job_project_id=as_text(config.get("job_project_id")),
        table=resolve_stock_table(config),
        location=as_text(config.get("location")),
        service_account_info=service_account_info,
        include_central_warehouse=include_central_warehouse,
        custom_query=as_text(config.get("stock_query")),
        subtract_reserved=subtract_reserved,
        reserved_columns=reserved_columns_from_secrets(config),
    )


def is_bigquery_configured(secrets: dict[str, Any]) -> bool:
    """Hay BigQuery utilizable si esta habilitado y hay con que autenticarse.

    La tabla no se exige: si no se declara `stock_table`, se usa la de siempre
    (`settings.DEFAULT_STOCK_TABLE`), igual que en Catalogo Control Center.
    """
    if not secrets:
        return False
    if as_text(secrets.get("enabled", "true")).lower() in ("0", "false", "no", "off"):
        return False
    # Credenciales: service account explicita, o project_id + ADC del entorno.
    return bool(
        secrets.get("service_account_info")
        or secrets.get("service_account_json")
        or as_text(secrets.get("project_id"))
    )
