"""Pruebas del cruce de stock: que el SKU del pedido encuentre su stock.

    python -m scripts.test_stock_match

Cubren los tres motivos por los que la app puede decir "sin stock" teniendo
stock:

1. **El codigo no cruza.** El Excel trae `0005438957` y BigQuery guarda
   `5438957` (o al reves, `5438957.0` si el campo es numerico). Si cada lado
   compara su forma cruda, no hay coincidencia y el resultado es cero filas.
2. **Las unidades se pierden al consolidar.** Si la fuente devuelve el mismo
   par (SKU, tienda) en varias filas, quedarse con la ultima en vez de sumarlas
   descarta unidades reales.
3. **El stock queda viejo.** El stock consultado vale solo para los SKU que se
   pidieron; con otra seleccion de estados, la foto en memoria queda corta.
"""

from __future__ import annotations

import sys

import pandas as pd

from config import settings
from core.excel_io import normalize_sku
from core.stock_source import (
    DIAG_CORTE_QUERY,
    DIAG_MUESTRA_QUERY,
    DIAG_SKU_QUERY,
    ManualStockSource,
    SKU_CANONICO_SQL,
    SKU_LIMPIO_SQL,
    STOCK_QUERY,
    build_stock_index,
    build_stock_query,
    central_warehouse_codes,
    consolidate,
    diagnose_conclusion,
    sku_query_values,
    stock_coverage,
    stock_cutoff,
)

CASES: list[tuple[str, object]] = []


def case(name: str):
    def decorator(fn):
        CASES.append((name, fn))
        return fn

    return decorator


def archivo(filas: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(filas)


# --- 1. El codigo cruza sin importar la forma en que venga ------------------
@case("SKU: el archivo trae ceros a la izquierda y la fuente no")
def test_ceros_a_la_izquierda():
    stock = ManualStockSource(
        archivo([{"sku": "5438957", "cod_tienda": "59", "stock": 4}]), True
    ).fetch(["0005438957"])
    assert len(stock) == 1, stock.to_dict("records")
    assert build_stock_index(stock) == {("5438957", "59"): 4}


@case("SKU: la fuente trae ceros a la izquierda y el archivo no")
def test_ceros_del_lado_de_la_fuente():
    stock = ManualStockSource(
        archivo([{"sku": "0005438957", "cod_tienda": "59", "stock": 4}]), True
    ).fetch(["5438957"])
    assert len(stock) == 1, stock.to_dict("records")
    assert stock.loc[0, "sku"] == "5438957"


@case("SKU: el `.0` de un campo numerico no rompe el cruce")
def test_decimal_cero():
    stock = ManualStockSource(
        archivo([{"sku": "5438957.0", "cod_tienda": "59", "stock": 4}]), True
    ).fetch([5438957.0])
    assert len(stock) == 1, stock.to_dict("records")
    assert stock.loc[0, "sku"] == "5438957"


@case("SKU: un codigo alfanumerico conserva su cero inicial")
def test_alfanumerico_conserva_ceros():
    # `0A12` no es un numero: ahi el cero puede ser parte del codigo y no se
    # toca. Si se recortara, dejaria de cruzar con la fuente.
    assert normalize_sku("0A12") == "0A12"
    stock = ManualStockSource(
        archivo([{"sku": "0A12", "cod_tienda": "59", "stock": 2}]), True
    ).fetch(["0A12"])
    assert len(stock) == 1, stock.to_dict("records")


@case("SKU: normalize_sku es idempotente")
def test_idempotente():
    for value in ["0005438957", "5438957.0", " 5438957 ", 5438957.0, "0A12", "000"]:
        una = normalize_sku(value)
        assert normalize_sku(una) == una, (value, una)


@case("BigQuery: la consulta normaliza el SKU del mismo modo que la app")
def test_consulta_normaliza_en_sql():
    query = build_stock_query(settings.DEFAULT_STOCK_TABLE)
    assert settings.DEFAULT_STOCK_TABLE in query
    assert "{" not in query, "quedaron marcadores sin reemplazar en la consulta"
    # El filtro compara la forma canonica, no `CAST(id_producto AS STRING)` a secas.
    assert "WHERE sku IN UNNEST(@skus)" in query
    for pieza in ("UPPER(TRIM(CAST(s.id_producto AS STRING)))", "[.]0+$", "^0*([0-9]+?)$"):
        assert pieza in query, pieza


@case("BigQuery: la consulta sigue siendo de solo lectura")
def test_consulta_solo_lectura():
    prohibido = ("INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "DROP", "TRUNCATE")
    texto = STOCK_QUERY.upper()
    for palabra in prohibido:
        assert palabra not in texto, palabra


# --- 2. Las unidades no se pierden ------------------------------------------
@case("Repetidos: el mismo par (SKU, tienda) suma en vez de pisarse")
def test_repetidos_suman():
    stock = ManualStockSource(
        archivo(
            [
                {"sku": "5438957", "cod_tienda": "59", "stock": 3, "fecha_corte": "2026-08-20"},
                {"sku": "5438957", "cod_tienda": "59", "stock": 4, "fecha_corte": "2026-08-20"},
            ]
        ),
        True,
    ).fetch(["5438957"])
    assert len(stock) == 1, stock.to_dict("records")
    assert build_stock_index(stock) == {("5438957", "59"): 7}


@case("Repetidos: una consulta propia con filas abiertas tambien se consolida")
def test_consolidate_directo():
    crudo = pd.DataFrame(
        [
            {"sku": "A", "cod_tienda": "59", "stock_tiendas": 2, "stock_bodega": 0,
             "stock": 2, "fecha_corte": "2026-08-20"},
            {"sku": "A", "cod_tienda": "59", "stock_tiendas": 5, "stock_bodega": 0,
             "stock": 5, "fecha_corte": "2026-08-20"},
            {"sku": "A", "cod_tienda": "88", "stock_tiendas": 1, "stock_bodega": 0,
             "stock": 1, "fecha_corte": "2026-08-20"},
        ]
    )
    out = consolidate(crudo)
    assert len(out) == 2, out.to_dict("records")
    assert build_stock_index(out) == {("A", "59"): 7, ("A", "88"): 1}


@case("Bodega central: 320 suma su stock_bodega y una tienda fisica no")
def test_bodega_central():
    datos = archivo(
        [
            {"sku": "A", "cod_tienda": "320", "stock_tiendas": 1, "stock_bodega": 9},
            {"sku": "A", "cod_tienda": "59", "stock_tiendas": 1, "stock_bodega": 9},
        ]
    )
    stock = ManualStockSource(datos, True).fetch(["A"])
    index = build_stock_index(stock)
    assert index == {("A", "320"): 10, ("A", "59"): 1}, index


@case("Bodega central: se puede declarar mas de una sin tocar codigo")
def test_bodegas_centrales_configurables():
    assert central_warehouse_codes() == {"320"}
    assert central_warehouse_codes("320, 400") == {"320", "400"}
    datos = archivo([{"sku": "A", "cod_tienda": "400", "stock_tiendas": 1, "stock_bodega": 9}])
    assert build_stock_index(ManualStockSource(datos, True).fetch(["A"])) == {("A", "400"): 1}
    con_400 = ManualStockSource(datos, True, ("320", "400")).fetch(["A"])
    assert build_stock_index(con_400) == {("A", "400"): 10}


@case("Negativos: se suman antes de aplicar el piso en cero")
def test_negativos():
    # Una tienda con +5 en una fila y -3 en otra tiene 2 unidades, no 5.
    datos = archivo(
        [
            {"sku": "A", "cod_tienda": "59", "stock_tiendas": 5},
            {"sku": "A", "cod_tienda": "59", "stock_tiendas": -3},
            {"sku": "B", "cod_tienda": "59", "stock_tiendas": -4},
        ]
    )
    stock = ManualStockSource(datos, True).fetch(["A", "B"])
    assert build_stock_index(stock) == {("A", "59"): 2}, stock.to_dict("records")


@case("Parametro @skus: viaja la forma canonica y tambien la variante `.0`")
def test_variantes_del_parametro():
    # Una `stock_query` copiada de otra app puede comparar
    # `CAST(id_producto AS STRING)` en crudo: si el campo es FLOAT, la tabla
    # dice `5438957.0`. Mandando las dos formas, esa consulta ajena cruza igual.
    valores = sku_query_values(["0005438957", "0A12"])
    assert valores == ["0A12", "5438957", "5438957.0"], valores


@case("Parametro @skus: sin SKU utilizables no se manda nada")
def test_variantes_vacias():
    assert sku_query_values(["", None, "  "]) == []


# --- 3. Diagnostico de una consulta que vuelve vacia ------------------------
@case("Diagnostico: las consultas se formatean y son de solo lectura")
def test_consultas_de_diagnostico():
    canonico = SKU_CANONICO_SQL.format(column=SKU_LIMPIO_SQL.format(column="s.id_producto"))
    consultas = [
        DIAG_CORTE_QUERY.format(table=settings.DEFAULT_STOCK_TABLE),
        DIAG_MUESTRA_QUERY.format(table=settings.DEFAULT_STOCK_TABLE),
        DIAG_SKU_QUERY.format(
            table=settings.DEFAULT_STOCK_TABLE,
            sku_canonico=canonico,
            sku_canonico_where=canonico,
        ),
    ]
    for consulta in consultas:
        assert "{" not in consulta, consulta
        for palabra in ("INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "DROP", "TRUNCATE"):
            assert palabra not in consulta.upper(), (palabra, consulta)
    # La busqueda de SKU va a proposito sin filtro de fecha: separa "el codigo
    # no cruza" de "el corte los deja fuera".
    assert "MAX(fecha_corte)" not in consultas[2]


@case("Diagnostico: una tabla sin la columna id_producto se nombra como tal")
def test_conclusion_sin_columna():
    info = {"columnas": pd.DataFrame([{"columna": "codint_ma", "tipo": "STRING"}])}
    assert "no tiene la columna 'id_producto'" in diagnose_conclusion(info)


@case("Diagnostico: una tabla sin filas visibles apunta a permisos")
def test_conclusion_sin_filas():
    info = {
        "columnas": pd.DataFrame([{"columna": "id_producto", "tipo": "INTEGER"}]),
        "filas_tabla": 0,
    }
    assert "permisos" in diagnose_conclusion(info)


@case("Diagnostico: si ningun SKU existe, el problema es el codigo")
def test_conclusion_sin_coincidencias():
    info = {
        "columnas": pd.DataFrame([{"columna": "id_producto", "tipo": "INTEGER"}]),
        "filas_tabla": 1000,
        "coincidencias": pd.DataFrame(),
    }
    assert "Ninguno de los SKU" in diagnose_conclusion(info)


@case("Diagnostico: si existen pero en un corte viejo, el problema es la fecha")
def test_conclusion_corte_viejo():
    info = {
        "columnas": pd.DataFrame([{"columna": "id_producto", "tipo": "INTEGER"}]),
        "filas_tabla": 1000,
        "ultimo_corte": "2026-09-02",
        "coincidencias": pd.DataFrame(
            [{"sku": "5438957", "fecha_corte": "2026-08-20", "filas": 3}]
        ),
    }
    conclusion = diagnose_conclusion(info)
    assert "no en el ultimo corte" in conclusion, conclusion
    assert "2026-08-20" in conclusion, conclusion


@case("Diagnostico: si estan en el ultimo corte, el problema es la consulta")
def test_conclusion_todo_bien():
    info = {
        "columnas": pd.DataFrame([{"columna": "id_producto", "tipo": "INTEGER"}]),
        "filas_tabla": 1000,
        "ultimo_corte": "2026-09-02",
        "coincidencias": pd.DataFrame(
            [{"sku": "5438957", "fecha_corte": "2026-09-02", "filas": 3}]
        ),
    }
    assert "el resto de la consulta" in diagnose_conclusion(info)


# --- 4. Que se ve cuando un SKU no trae stock -------------------------------
@case("Cobertura: se distingue 'no vino en la consulta' de 'vino en cero'")
def test_cobertura():
    stock = ManualStockSource(
        archivo(
            [
                {"sku": "A", "cod_tienda": "59", "stock": 4},
                {"sku": "B", "cod_tienda": "59", "stock": 0},
            ]
        ),
        True,
    ).fetch(["A", "B", "C"])
    cobertura = stock_coverage(stock, ["A", "B", "C"]).set_index("sku")
    assert cobertura.loc["A", "situacion"] == "CON STOCK"
    assert int(cobertura.loc["A", "unidades"]) == 4
    assert cobertura.loc["B", "situacion"] == "EN CERO"
    assert cobertura.loc["C", "situacion"] == "SIN RESPUESTA"


@case("Cobertura: compara el SKU ya normalizado, no el crudo del archivo")
def test_cobertura_normaliza():
    stock = ManualStockSource(
        archivo([{"sku": "5438957", "cod_tienda": "59", "stock": 4}]), True
    ).fetch(["0005438957"])
    cobertura = stock_coverage(stock, ["0005438957"])
    assert list(cobertura["sku"]) == ["5438957"]
    assert cobertura.loc[0, "situacion"] == "CON STOCK"


@case("Cobertura: sin ninguna fila, todos los SKU quedan como SIN RESPUESTA")
def test_cobertura_vacia():
    cobertura = stock_coverage(pd.DataFrame(), ["A", "B"])
    assert list(cobertura["situacion"]) == ["SIN RESPUESTA", "SIN RESPUESTA"]


@case("Fecha de corte: se informa como fecha, no como texto")
def test_cutoff_dd_mm_yyyy():
    # Como texto, "31/12/2025" es mayor que "20/08/2026". Como fecha, no.
    stock = ManualStockSource(
        archivo(
            [
                {"sku": "A", "cod_tienda": "59", "stock": 1, "fecha_corte": "31/12/2025"},
                {"sku": "A", "cod_tienda": "88", "stock": 1, "fecha_corte": "20/08/2026"},
            ]
        ),
        True,
    ).fetch(["A"])
    assert stock_cutoff(stock) == "20/08/2026", stock.to_dict("records")


def main() -> int:
    passed, failed = 0, []
    for name, test in CASES:
        try:
            test()
        except AssertionError as exc:
            failed.append(name)
            print(f"  FALLO  {name}\n         {exc}")
        except Exception as exc:  # pragma: no cover
            failed.append(name)
            print(f"  ERROR  {name}\n         {exc!r}")
        else:
            passed += 1
            print(f"  ok     {name}")

    print(f"\n{passed}/{len(CASES)} casos correctos.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
