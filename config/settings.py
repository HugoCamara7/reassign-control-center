"""Constantes y parametros por defecto de Reassign Control Center.

Nada de reglas de negocio "duras" vive aqui: la prioridad de tiendas se lee
siempre desde `config/prioridad_tiendas.xlsx` (ver `core/priority.py`).
Este modulo solo declara nombres de columnas, rutas y valores de arranque.
"""

from __future__ import annotations

from pathlib import Path

APP_NAME = "Reassign Control Center"
APP_TAGLINE = "Reasignacion automatica de pedidos sin stock"
APP_VERSION = "1.0.0"

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"

# Unica fuente de prioridad. Viaja versionada en el repositorio para que la
# app desplegada la tenga sin que nadie suba nada a mano. No hay archivo de
# ejemplo aparte: duplicar la configuracion solo genera dudas sobre cual manda.
PRIORITY_FILE = CONFIG_DIR / "prioridad_tiendas.xlsx"

# --- Hojas de la configuracion editable -------------------------------------
SHEET_PRIORITY = "Prioridad"
SHEET_STORES = "Tiendas"
SHEET_PARAMS = "Parametros"

# --- Columnas del archivo de pedidos ----------------------------------------
# Detectadas sobre "Formato de Carga Reasignacion.xls" (58 columnas).
COL_ORDER = "Order"
COL_SHGROUP = "ShGroup"
COL_SITE = "Sitio"
COL_STATUS = "Estado"
COL_STORE_NAME = "Nom_Tienda_Asig"
COL_STORE_CODE = "Cod_Tienda_Asig"
COL_REASSIGNED = "Nom Tda Reasignada"
COL_BRAND = "Marca"
COL_SKU = "SKU"
COL_BARCODE = "Barra"
COL_PRODUCT = "Nombre_Prod"
COL_MODEL = "Cod_Modelo"
COL_SIZE = "Talla"
COL_UNITS = "Unidades"
COL_SHIPPING_METHOD = "Metodo_de_Despacho"

# Minimo indispensable para poder correr el motor.
REQUIRED_COLUMNS = [COL_ORDER, COL_STATUS, COL_SKU, COL_UNITS]

# Recomendadas: si faltan, la app avisa pero deja continuar.
RECOMMENDED_COLUMNS = [
    COL_SHGROUP,
    COL_SITE,
    COL_STORE_NAME,
    COL_STORE_CODE,
    COL_BRAND,
    COL_SIZE,
    COL_SHIPPING_METHOD,
]

# Alias tolerantes: el mismo dato puede llegar con otro encabezado.
COLUMN_ALIASES = {
    COL_ORDER: ["order", "pedido", "numero de pedido", "n_pedido", "order_number"],
    COL_SHGROUP: ["shgroup", "sh_group", "shipping_group", "grupo_despacho"],
    COL_SITE: ["sitio", "site", "tienda_online", "ecommerce"],
    COL_STATUS: ["estado", "status", "estado_pedido", "order_status"],
    COL_STORE_NAME: [
        "nom_tienda_asig",
        "nombre_tienda_asignada",
        "tienda_asignada",
        "tienda origen",
        "tienda_origen",
    ],
    COL_STORE_CODE: ["cod_tienda_asig", "codigo_tienda_asignada", "cod_tienda", "codigo_tienda"],
    COL_REASSIGNED: [
        "nom tda reasignada",
        "nom_tda_reasignada",
        "tienda_reasignada",
        "nombre_tienda_reasignada",
    ],
    COL_BRAND: ["marca", "brand", "vendor"],
    COL_SKU: ["sku", "cod_sku", "codigo_sku", "id_producto", "codint_ma"],
    COL_UNITS: ["unidades", "cantidad", "qty", "quantity", "units"],
    COL_SIZE: ["talla", "size"],
    COL_MODEL: ["cod_modelo", "codigo_modelo", "model", "modelo"],
    COL_SHIPPING_METHOD: ["metodo_de_despacho", "metodo_despacho", "shipping_method"],
}

# --- Estados ----------------------------------------------------------------
# El archivo real trae los estados con guion bajo (SIN_STOCK / SIN_DESPACHO),
# pero se normaliza para aceptar tambien "SIN STOCK", "sin-stock", etc.
DEFAULT_TARGET_STATUSES = ["SIN_STOCK", "SIN_DESPACHO"]

RESULT_REASSIGNED = "REASIGNADO"
RESULT_NO_OPTION = "SIN OPCION DE REASIGNACION"
RESULT_NOT_APPLICABLE = "NO APLICA"
RESULT_ERROR = "ERROR"

# --- Columnas de trazabilidad que agrega la app -----------------------------
# Se pueden excluir del archivo final con un switch en la UI, para que el
# Excel quede identico al original + "Nom Tda Reasignada".
TRACE_COLUMNS = [
    "Reasig_Resultado",
    "Reasig_Cod_Tienda",
    "Reasig_Stock_Disponible",
    "Reasig_Stock_Restante",
    "Reasig_Prioridad",
    "Reasig_Detalle",
    "Reasig_Fecha_Corte",
]

# --- BigQuery (solo lectura) ------------------------------------------------
DEFAULT_STOCK_TABLE = "forus-analitica-prod-datalake.bronze.stg_pe_central_stock_bi"

# Bodega central: es la unica donde el stock de bodega suma al disponible.
CENTRAL_WAREHOUSE_CODE = "320"

# --- Parametros de negocio por defecto (editables en la hoja Parametros) -----
DEFAULT_PARAMS = {
    "estados_objetivo": ",".join(DEFAULT_TARGET_STATUSES),
    "excluir_tienda_origen": "SI",
    "permitir_reasignacion_parcial": "NO",
    "agrupar_por_shgroup": "NO",
    "fallback_linea_si_grupo_falla": "SI",
    "incluir_stock_bodega_central": "SI",
    "descontar_stock_reservado": "SI",
    "stock_seguridad_global": "0",
    "reserva_por_tienda": "1",
    "ordenar_por_stock": "SI",
    "max_unidades_por_tienda": "0",
    "columna_salida": COL_REASSIGNED,
}

PARAM_HELP = {
    "estados_objetivo": "Estados que se intentan reasignar. Separados por coma.",
    "excluir_tienda_origen": "SI = nunca reasignar a la misma tienda que ya tenia el pedido.",
    "permitir_reasignacion_parcial": "SI = permite cubrir solo parte de las unidades con una tienda.",
    "agrupar_por_shgroup": "SI = todas las lineas de un mismo ShGroup van a la misma tienda.",
    "fallback_linea_si_grupo_falla": (
        "Solo aplica con agrupar_por_shgroup=SI. SI = si ninguna tienda cubre el grupo "
        "completo, se resuelve linea por linea. NO = el grupo entero queda sin opcion."
    ),
    "incluir_stock_bodega_central": "SI = en la bodega 320 se suma stock_bodega al disponible.",
    "descontar_stock_reservado": (
        "SI = al stock de tienda y bodega se le restan las unidades reservadas. "
        "Lo reservado ya tiene dueno: si una tienda tiene 3 unidades y 3 reservadas, "
        "su disponible es 0 y no recibe reasignaciones. NO = se usa el stock bruto."
    ),
    "stock_seguridad_global": "Unidades que nunca se tocan en ninguna tienda.",
    "reserva_por_tienda": (
        "Unidades que deberia conservar la tienda despues de ceder. Con 1, se evita "
        "dejarla en cero; si ninguna tienda puede conservarlas, recien ahi se acepta "
        "la ultima unidad. 0 desactiva la regla."
    ),
    "ordenar_por_stock": (
        "SI = dentro de la misma banda de prioridad gana la tienda con mas stock. "
        "NO = gana la primera de la lista."
    ),
    "max_unidades_por_tienda": "Tope de unidades reasignadas por tienda en una corrida. 0 = sin tope.",
    "columna_salida": "Nombre exacto de la columna donde se escribe la tienda reasignada.",
}
