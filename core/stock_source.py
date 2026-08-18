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


def _finalize(df: pd.DataFrame, include_central_warehouse: bool) -> pd.DataFrame:
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

    # El stock de bodega solo suma en la bodega central (320). En una tienda
    # fisica el `stock_bodega` corresponde a otro almacen y no es despachable
    # desde ahi, asi que se ignora.
    central = df["cod_tienda"] == settings.CENTRAL_WAREHOUSE_CODE
    if include_central_warehouse:
        df["stock"] = df["stock_tiendas"] + df["stock_bodega"].where(central, 0)
    else:
        df["stock"] = df["stock_tiendas"]
    df["stock"] = df["stock"].clip(lower=0).astype(int)

    df = df[df["sku"] != ""]
    return df[STOCK_COLUMNS].reset_index(drop=True)


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
        query = self.custom_query or STOCK_QUERY
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
        result = _finalize(combined, self.include_central_warehouse)
        if not filters_by_sku and not result.empty:
            result = result[result["sku"].isin(unique)].reset_index(drop=True)
        return result


@dataclass
class ManualStockSource:
    """Stock desde un archivo subido por el usuario (modo sin BigQuery)."""

    frame: pd.DataFrame
    include_central_warehouse: bool = True
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

        # Un mismo SKU/tienda puede venir repetido: se consolida.
        data = _finalize(data, self.include_central_warehouse)
        if data.empty:
            return data
        return (
            data.groupby(["sku", "cod_tienda"], as_index=False)
            .agg(
                stock_tiendas=("stock_tiendas", "sum"),
                stock_bodega=("stock_bodega", "sum"),
                stock=("stock", "sum"),
                fecha_corte=("fecha_corte", "max"),
            )[STOCK_COLUMNS]
        )


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


def secrets_to_source(secrets: dict[str, Any], include_central_warehouse: bool) -> BigQueryStockSource:
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
