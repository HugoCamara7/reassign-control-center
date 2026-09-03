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
# se toma unicamente el ultimo `fecha_corte` disponible.
#
# Todo se compara sobre el texto normalizado a proposito. En el datalake
# `id_producto` puede llegar como STRING, INT64 o FLOAT64, y `CAST(x AS STRING)`
# de un FLOAT64 devuelve `5438957.0`: comparado contra el SKU del pedido
# (`5438957`) no coincide **ninguna fila** y la app parece quedarse sin stock
# sin que la consulta falle. `_SQL_SKU` deja los dos lados en el mismo formato.
_SQL_SKU = r"REGEXP_REPLACE(CAST({column} AS STRING), r'\.0+$', '')"

# El corte se compara por DIA, no por instante. Si `fecha_corte` es TIMESTAMP y
# el ETL sella cada lote con su propia hora, `MAX(fecha_corte)` es un instante y
# el JOIN por igualdad se queda con una rebanada minima de la foto (o con nada).
_SQL_DIA = (
    "COALESCE("
    "SAFE_CAST(CAST({column} AS STRING) AS DATE), "
    "SAFE_CAST(LEFT(CAST({column} AS STRING), 10) AS DATE)"
    ")"
)

# Las unidades pasan por FLOAT64 antes de INT64: `SAFE_CAST('3.0' AS INT64)` es
# NULL en BigQuery, y esa fila se contaria como cero.
_SQL_UNIDADES = "CAST(COALESCE(SAFE_CAST(CAST({column} AS STRING) AS FLOAT64), 0) AS INT64)"

STOCK_QUERY = f"""
WITH normalizado AS (
  SELECT
    {_SQL_SKU.format(column='s.id_producto')}        AS sku,
    {_SQL_SKU.format(column='s.codigo_tienda')}      AS cod_tienda,
    {_SQL_UNIDADES.format(column='s.stock_tiendas')} AS stock_tiendas,
    {_SQL_UNIDADES.format(column='s.stock_bodega')}  AS stock_bodega,
    s.fecha_corte                                    AS fecha_corte,
    {_SQL_DIA.format(column='s.fecha_corte')}        AS dia_corte
  FROM `{{table}}` AS s
),
ultimo_corte AS (
  SELECT MAX(dia_corte) AS dia_corte
  FROM normalizado
)
SELECT
  n.sku                                AS sku,
  n.cod_tienda                         AS cod_tienda,
  SUM(n.stock_tiendas)                 AS stock_tiendas,
  SUM(n.stock_bodega)                  AS stock_bodega,
  CAST(MAX(n.fecha_corte) AS STRING)   AS fecha_corte
FROM normalizado AS n
JOIN ultimo_corte AS u
  ON n.dia_corte = u.dia_corte
WHERE n.sku IN UNNEST(@skus)
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


def _parse_cutoffs(values: pd.Series) -> pd.Series:
    """Parsea `fecha_corte` a datetime sin lanzar nunca.

    `pd.to_datetime(..., format="mixed")` **si lanza** con `errors="coerce"`
    cuando los textos traen offsets UTC distintos ("Mixed timezones detected"),
    y eso tumbaba la consulta de stock entera. Por eso se reintenta con
    `utc=True` y, si tampoco, se devuelve todo NaT para que el llamador caiga
    a la comparacion textual.
    """
    textos = values.map(as_text)
    for utc in (False, True):
        try:
            return pd.to_datetime(
                textos, errors="coerce", dayfirst=True, format="mixed", utc=utc
            )
        except (ValueError, TypeError):
            continue
    return pd.Series(pd.NaT, index=values.index)


def cutoff_days(values: pd.Series) -> pd.Series:
    """Dia del corte de cada fila (medianoche), o NaT si no se pudo leer."""
    fechas = _parse_cutoffs(values)
    if fechas.isna().all():
        return fechas
    return fechas.dt.normalize()


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
    fechas = _parse_cutoffs(textos)
    if fechas.notna().any():
        return textos[fechas == fechas.max()].iloc[0]
    return textos.max()


def keep_latest_cutoff(df: pd.DataFrame) -> tuple[pd.DataFrame, int, str]:
    """Deja solo las filas del ultimo corte. Devuelve `(df, descartadas, corte)`.

    El stock es una foto, no un acumulado: si el origen trae historico, sumar
    todos los cortes multiplica las unidades disponibles e inventa stock que
    no existe.

    El corte se decide **por dia, no por instante**. Si `fecha_corte` es una
    marca de tiempo y el origen sella cada lote con su propia hora, quedarse
    solo con el instante maximo borra casi toda la foto: la app se queda sin
    stock aunque BigQuery lo tenga. Elegir la foto vigente entre varias del
    mismo dia es trabajo de `_collapse_snapshots`, que lo hace por SKU/tienda
    y por eso no descarta filas de otras tiendas.
    """
    if df.empty or "fecha_corte" not in df.columns:
        return df, 0, ""
    textos = df["fecha_corte"].map(as_text)
    if not bool((textos != "").any()):
        return df, 0, ""

    dias = cutoff_days(df["fecha_corte"])
    if dias.notna().any():
        vigentes = dias == dias.max()
    else:
        vigentes = textos == textos.max()

    descartadas = int((~vigentes).sum())
    vigente_df = df[vigentes].reset_index(drop=True)
    corte = latest_cutoff_value(vigente_df["fecha_corte"]) if not vigente_df.empty else None
    return vigente_df, descartadas, as_text(corte)


def _collapse_snapshots(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Una sola fila por `(sku, cod_tienda)`. Devuelve `(df, reemplazadas)`.

    Dentro del corte vigente pueden convivir dos cosas distintas:

    * el mismo SKU/tienda con **varias marcas de tiempo** del mismo dia: es una
      foto que reemplaza a la anterior, asi que gana la mas reciente;
    * el mismo SKU/tienda repetido con la **misma** marca de tiempo: es un solo
      corte desglosado en varias filas, y ahi si se suma.

    Distinguirlo por SKU/tienda —y no para toda la tabla— es lo que evita que
    una tienda sellada una hora antes desaparezca del stock disponible.
    """
    if df.empty:
        return df, 0

    trabajo = df.copy()
    fechas = _parse_cutoffs(trabajo["fecha_corte"])
    reemplazadas = 0
    if fechas.notna().any():
        trabajo["_ts"] = fechas
        maximos = trabajo.groupby(["sku", "cod_tienda"])["_ts"].transform("max")
        # Un grupo entero sin fecha legible se conserva completo.
        vigentes = trabajo["_ts"].eq(maximos) | (trabajo["_ts"].isna() & maximos.isna())
        reemplazadas = int((~vigentes).sum())
        trabajo = trabajo[vigentes].drop(columns="_ts")

    consolidado = (
        trabajo.groupby(["sku", "cod_tienda"], as_index=False)
        .agg(
            stock_tiendas=("stock_tiendas", "sum"),
            stock_bodega=("stock_bodega", "sum"),
            stock=("stock", "sum"),
            fecha_corte=("fecha_corte", "max"),
        )[STOCK_COLUMNS]
    )
    return consolidado, reemplazadas


def _finalize(df: pd.DataFrame, include_central_warehouse: bool) -> pd.DataFrame:
    """Normaliza tipos y calcula la columna `stock` efectiva."""
    # Se guarda cuantas filas entraron: en pantalla, "cero unidades" se ve
    # igual si la fuente no devolvio nada o si el saneamiento se lo llevo todo.
    filas_crudas = int(len(df))
    if df.empty:
        vacio = empty_stock_frame()
        vacio.attrs["filas_crudas"] = filas_crudas
        return vacio

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
    df = df[STOCK_COLUMNS].reset_index(drop=True)

    # Garantia unica para las dos fuentes: nunca se mezclan cortes. Si el
    # origen trae historico (o una consulta propia sin filtro de fecha), aqui
    # se queda solo la foto mas reciente.
    df, descartadas, corte = keep_latest_cutoff(df)

    # Recien con un solo corte encima se consolida el SKU/tienda repetido.
    # Antes de filtrar por fecha, sumar mezclaria cortes distintos e inventaria
    # unidades que no existen.
    df, reemplazadas = _collapse_snapshots(df)
    df.attrs["filas_crudas"] = filas_crudas
    df.attrs["filas_descartadas_por_fecha"] = descartadas + reemplazadas
    df.attrs["filas_de_cortes_anteriores"] = descartadas
    df.attrs["filas_reemplazadas_en_el_dia"] = reemplazadas
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
        result.attrs["skus_solicitados"] = len(unique)
        result.attrs["tabla"] = self.table
        return result

    def diagnose(self, skus: Iterable[str]) -> dict[str, Any]:
        """Por que la consulta no trae stock. Solo lectura, sin traer datos.

        Cuando la app muestra cero unidades hay varias causas posibles que se
        ven identicas en pantalla: la tabla vacia, un corte nuevo sin filas,
        SKU que no existen, o —la mas traicionera— un `id_producto` numerico
        cuyo texto (`5438957.0`) no coincide con el SKU del pedido
        (`5438957`). Esto las separa con contadores concretos.
        """
        from google.cloud import bigquery

        unique = sorted({normalize_sku(sku) for sku in skus if normalize_sku(sku)})
        muestra = unique[:200]
        sql = f"""
        WITH normalizado AS (
          SELECT
            {_SQL_SKU.format(column='s.id_producto')} AS sku,
            CAST(s.id_producto AS STRING)             AS sku_crudo,
            {_SQL_DIA.format(column='s.fecha_corte')} AS dia_corte
          FROM `{self.table}` AS s
        ),
        corte AS (SELECT MAX(dia_corte) AS dia FROM normalizado)
        SELECT
          (SELECT COUNT(*) FROM normalizado)                        AS filas_tabla,
          (SELECT CAST(dia AS STRING) FROM corte)                   AS ultimo_corte,
          (SELECT COUNT(*) FROM normalizado n, corte c
             WHERE n.dia_corte = c.dia)                             AS filas_ultimo_corte,
          (SELECT COUNT(DISTINCT n.sku) FROM normalizado n
             WHERE n.sku IN UNNEST(@skus))                          AS skus_en_tabla,
          (SELECT COUNT(DISTINCT n.sku) FROM normalizado n, corte c
             WHERE n.dia_corte = c.dia AND n.sku IN UNNEST(@skus))  AS skus_en_ultimo_corte,
          (SELECT COUNT(DISTINCT n.sku_crudo) FROM normalizado n
             WHERE n.sku_crudo IN UNNEST(@skus))                    AS skus_sin_normalizar
        """
        job_config = bigquery.QueryJobConfig(
            use_legacy_sql=False,
            query_parameters=[bigquery.ArrayQueryParameter("skus", "STRING", muestra)],
        )
        client = self._client()
        job = client.query(sql, job_config=job_config, location=self.location or None)
        fila = dict(next(iter(job.result())).items())
        fila["skus_consultados"] = len(muestra)
        fila["tabla"] = self.table
        fila["consulta_propia"] = bool(self.custom_query)
        return fila


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

        # `_finalize` deja solo el ultimo corte y ya consolida el SKU/tienda
        # repetido dentro de ese corte, igual que para BigQuery: una sola fila
        # por combinacion, sin mezclar fechas.
        return _finalize(data, self.include_central_warehouse)


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
