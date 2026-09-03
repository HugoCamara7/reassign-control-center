"""Fuentes de stock. BigQuery es **solo lectura**: nunca se escribe alli.

Se ofrecen dos proveedores con la misma interfaz:

* `BigQueryStockSource` : consulta `stg_pe_central_stock_bi` (produccion).
* `ManualStockSource`   : lee un Excel/CSV de stock. Sirve para probar la
  app sin credenciales y para simulaciones puntuales del equipo.

Ambos devuelven un DataFrame con el mismo contrato:

    sku | cod_tienda | stock_tiendas | stock_bodega | stock | fecha_corte
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol

import pandas as pd

from config import settings
from core.excel_io import as_text, normalize_sku, normalize_store_code, to_int

STOCK_COLUMNS = ["sku", "cod_tienda", "stock_tiendas", "stock_bodega", "stock", "fecha_corte"]

# --- Canonizacion del SKU dentro de BigQuery --------------------------------
# `SKU_LIMPIO_SQL` y `SKU_CANONICO_SQL` replican paso a paso lo que hace
# `core.excel_io.normalize_sku` del lado del archivo de pedidos: recortar el
# `.0` que deja un campo numerico, pasar a mayusculas, y quitar los ceros a la
# izquierda **solo** cuando el codigo es todo digitos.
#
# Sin esto el filtro `IN UNNEST(@skus)` compara la forma cruda de la tabla
# (`5438957.0` si `id_producto` es FLOAT, `0005438957` si es texto) contra el
# SKU ya normalizado del Excel: no devuelve ninguna fila y la app termina
# informando "sin stock" para productos que si lo tienen.
SKU_LIMPIO_SQL = "REGEXP_REPLACE(UPPER(TRIM(CAST({column} AS STRING))), r'[.]0+$', '')"
SKU_CANONICO_SQL = (
    "IF(REGEXP_CONTAINS({column}, r'^[0-9]+$'), "
    "IFNULL(REGEXP_EXTRACT({column}, r'^0*([0-9]+?)$'), {column}), "
    "{column})"
)

# Consulta parametrizada. Se filtra por SKU para no traer la tabla completa y
# se toma unicamente la ultima `fecha_corte` disponible.
STOCK_QUERY = """
WITH ultimo_corte AS (
  SELECT MAX(fecha_corte) AS fecha_corte
  FROM `{table}`
),
corte_vigente AS (
  SELECT
    {sku_limpio}                                AS sku_limpio,
    CAST(s.codigo_tienda AS STRING)             AS cod_tienda,
    COALESCE(CAST(s.stock_tiendas AS INT64), 0) AS stock_tiendas,
    COALESCE(CAST(s.stock_bodega  AS INT64), 0) AS stock_bodega,
    CAST(s.fecha_corte AS STRING)               AS fecha_corte
  FROM `{table}` AS s
  JOIN ultimo_corte AS u
    ON s.fecha_corte = u.fecha_corte
),
stock_vigente AS (
  SELECT
    {sku_canonico} AS sku,
    cod_tienda,
    stock_tiendas,
    stock_bodega,
    fecha_corte
  FROM corte_vigente
)
SELECT
  sku,
  cod_tienda,
  SUM(stock_tiendas) AS stock_tiendas,
  SUM(stock_bodega)  AS stock_bodega,
  MAX(fecha_corte)   AS fecha_corte
FROM stock_vigente
WHERE sku IN UNNEST(@skus)
GROUP BY sku, cod_tienda
"""


def build_stock_query(table: str) -> str:
    """Consulta por defecto, ya formateada para una tabla concreta."""
    return STOCK_QUERY.format(
        table=table,
        sku_limpio=SKU_LIMPIO_SQL.format(column="s.id_producto"),
        sku_canonico=SKU_CANONICO_SQL.format(column="sku_limpio"),
    )


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
    "fecha_corte": ("fecha_corte", "fecha"),
}


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


def central_warehouse_codes(codes: Iterable[str] | None = None) -> set[str]:
    """Bodegas donde `stock_bodega` si es despachable. Por defecto, la 320."""
    if codes is None:
        codes = settings.CENTRAL_WAREHOUSE_CODES
    if isinstance(codes, str):
        codes = codes.replace(";", ",").split(",")
    resolved = {normalize_store_code(code) for code in codes}
    return {code for code in resolved if code}


def consolidate(df: pd.DataFrame) -> pd.DataFrame:
    """Suma las filas repetidas de un mismo par (SKU, tienda).

    Una consulta propia puede devolver el stock abierto por talla, almacen o
    linea. Sin consolidar, `build_stock_index` se queda con la ultima fila de
    cada par y **pierde** el resto de las unidades. Se hace despues de filtrar
    por fecha de corte: sumar entre cortes distintos inventaria stock.
    """
    if df.empty:
        return df
    atributos = dict(df.attrs)
    consolidado = (
        df.groupby(["sku", "cod_tienda"], as_index=False, sort=False)
        .agg(
            stock_tiendas=("stock_tiendas", "sum"),
            stock_bodega=("stock_bodega", "sum"),
            stock=("stock", "sum"),
            fecha_corte=("fecha_corte", "max"),
        )[STOCK_COLUMNS]
    )
    consolidado.attrs.update(atributos)
    return consolidado


def _finalize(
    df: pd.DataFrame,
    include_central_warehouse: bool,
    central_codes: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Normaliza tipos y calcula la columna `stock` efectiva."""
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
    for column in ("stock_tiendas", "stock_bodega"):
        if column not in df.columns:
            df[column] = 0
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype(int)
    if "fecha_corte" not in df.columns:
        df["fecha_corte"] = ""
    df["fecha_corte"] = df["fecha_corte"].map(as_text)

    # El stock de bodega solo suma en las bodegas centrales (por defecto, la
    # 320). En una tienda fisica el `stock_bodega` corresponde a otro almacen y
    # no es despachable desde ahi, asi que se ignora.
    central = df["cod_tienda"].isin(central_warehouse_codes(central_codes))
    if include_central_warehouse:
        df["stock"] = df["stock_tiendas"] + df["stock_bodega"].where(central, 0)
    else:
        df["stock"] = df["stock_tiendas"]
    df["stock"] = df["stock"].astype(int)

    df = df[df["sku"] != ""]
    df = df[STOCK_COLUMNS].reset_index(drop=True)

    # Garantia unica para las dos fuentes: nunca se mezclan cortes. Si el
    # origen trae historico (o una consulta propia sin filtro de fecha), aqui
    # se queda solo la foto mas reciente.
    df, descartadas, corte = keep_latest_cutoff(df)

    # Recien con un solo corte sobre la mesa se suman las filas repetidas del
    # mismo par (SKU, tienda). Antes de filtrar por fecha, sumarlas mezclaria
    # fotos de dias distintos.
    df = consolidate(df)

    # El piso en cero va **al final**: una tienda con +5 en una fila y -3 en
    # otra tiene 2 unidades, no 5. Recortar antes de sumar inflaria el stock.
    if not df.empty:
        df["stock"] = df["stock"].clip(lower=0).astype(int)
    df.attrs["filas_descartadas_por_fecha"] = descartadas
    df.attrs["fecha_corte"] = corte
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
    central_codes: tuple[str, ...] = ()
    custom_query: str = ""
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

    def fetch(self, skus: Iterable[str]) -> pd.DataFrame:
        from google.cloud import bigquery

        unique = sorted({normalize_sku(sku) for sku in skus if normalize_sku(sku)})
        if not unique:
            return empty_stock_frame()

        client = self._client()
        query = self.custom_query.strip()
        if query:
            if "{table}" in query:
                query = query.format(table=self.table)
        else:
            query = build_stock_query(self.table)

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
        result = _finalize(combined, self.include_central_warehouse, self.central_codes or None)
        if not filters_by_sku and not result.empty:
            result = result[result["sku"].isin(unique)].reset_index(drop=True)
        return result


@dataclass
class ManualStockSource:
    """Stock desde un archivo subido por el usuario (modo sin BigQuery)."""

    frame: pd.DataFrame
    include_central_warehouse: bool = True
    central_codes: tuple[str, ...] = ()
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

        data = pd.DataFrame(
            {
                "sku": self.frame[resolved["sku"]],
                "cod_tienda": self.frame[resolved["cod_tienda"]],
                "stock_tiendas": self.frame[resolved["stock_tiendas"]],
                "stock_bodega": (
                    self.frame[resolved["stock_bodega"]] if "stock_bodega" in resolved else 0
                ),
                "fecha_corte": (
                    self.frame[resolved["fecha_corte"]] if "fecha_corte" in resolved else ""
                ),
            }
        )

        wanted = {normalize_sku(sku) for sku in skus if normalize_sku(sku)}
        data["sku"] = data["sku"].map(normalize_sku)
        if wanted:
            data = data[data["sku"].isin(wanted)]

        # `_finalize` deja solo el ultimo corte y recien ahi suma las filas
        # repetidas del mismo par (SKU, tienda).
        return _finalize(data, self.include_central_warehouse, self.central_codes or None)


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
    """Fecha de corte del stock cargado.

    Compara como fecha, no como texto: `31/12/2025` es mayor que `20/08/2026`
    en orden alfabetico, y esa fecha es la que se estampa en el reporte y en
    `Reasig_Fecha_Corte`.
    """
    if stock is None or stock.empty or "fecha_corte" not in stock.columns:
        return ""
    return as_text(latest_cutoff_value(stock["fecha_corte"]))


def stock_coverage(stock: pd.DataFrame, skus: Iterable[str]) -> pd.DataFrame:
    """Que paso con cada SKU consultado. Sirve para explicar un "no jala".

    Devuelve `sku | tiendas | unidades | situacion`, donde `situacion` separa
    los dos motivos que la app confundia en un solo "sin stock":

    * `SIN RESPUESTA`  : la fuente no devolvio ninguna fila para ese SKU
      (SKU inexistente en la tabla, o un cruce que no esta encontrando).
    * `EN CERO`        : la fuente si lo conoce, pero no hay unidades.
    * `CON STOCK`      : hay unidades disponibles.
    """
    pedidos = sorted({normalize_sku(sku) for sku in skus if normalize_sku(sku)})
    if stock is None or stock.empty:
        resumen: dict[str, tuple[int, int]] = {}
    else:
        agrupado = stock[stock["stock"] > 0].groupby("sku")["stock"]
        unidades = agrupado.sum().to_dict()
        tiendas = agrupado.count().to_dict()
        presentes = set(stock["sku"])
        resumen = {
            sku: (int(tiendas.get(sku, 0)), int(unidades.get(sku, 0))) for sku in presentes
        }

    filas = []
    for sku in pedidos:
        if sku not in resumen:
            situacion = "SIN RESPUESTA"
            conteo, total = 0, 0
        else:
            conteo, total = resumen[sku]
            situacion = "CON STOCK" if total > 0 else "EN CERO"
        filas.append(
            {"sku": sku, "tiendas": conteo, "unidades": total, "situacion": situacion}
        )
    return pd.DataFrame(filas, columns=["sku", "tiendas", "unidades", "situacion"])


def resolve_stock_table(secrets: dict[str, Any]) -> str:
    """Tabla de stock a consultar.

    **No se usa `table` como respaldo a proposito.** En los secrets de Catalogo
    Control Center `table` apunta a la tabla ARTI (maestro de productos), no al
    stock; ese proyecto resuelve el stock con una consulta fija contra
    `stg_pe_central_stock_bi`. Reutilizar `table` aqui haria que la app buscara
    stock en la tabla equivocada al copiar los mismos secrets.
    """
    return as_text((secrets or {}).get("stock_table")) or settings.DEFAULT_STOCK_TABLE


def secrets_to_source(
    secrets: dict[str, Any],
    include_central_warehouse: bool,
    central_codes: Iterable[str] | None = None,
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
        central_codes=tuple(sorted(central_warehouse_codes(central_codes))),
        custom_query=as_text(config.get("stock_query")),
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
