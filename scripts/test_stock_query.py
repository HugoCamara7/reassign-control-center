"""Pruebas de la consulta a BigQuery, con un cliente simulado.

    python -m scripts.test_stock_query

La consulta de stock no se puede ejecutar desde las pruebas: hace falta un
proyecto real. Eso ya costo una corrida en produccion, asi que aca se simula
el cliente de BigQuery y se **emula la semantica del `WHERE`** sobre una tabla
en memoria, con los formatos reales que puede traer `id_producto`:

* `5438957`      entero (el caso que siempre funciono)
* `0005438957`   texto con ceros a la izquierda
* `5438957.0`    campo numerico guardado como texto

Lo que se fija aca es el contrato entre la app y BigQuery: que parametros se
mandan, que compara la consulta y que llega de vuelta. La sintaxis SQL en si
la valida BigQuery; por eso el cruce no depende de expresiones regulares.
"""

from __future__ import annotations

import sys

import pandas as pd

from config import settings
from core.stock_source import (
    BigQueryStockSource,
    build_stock_index,
    build_stock_query,
)

CASES: list[tuple[str, object]] = []


def case(name: str):
    def decorator(fn):
        CASES.append((name, fn))
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Cliente simulado
# ---------------------------------------------------------------------------
class _FakeJob:
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def result(self):
        return self

    def to_dataframe(self) -> pd.DataFrame:
        return self._frame


class FakeClient:
    """Emula lo justo de `bigquery.Client` para la consulta de stock."""

    def __init__(self, filas: list[dict]) -> None:
        self.filas = filas
        self.consultas: list[str] = []
        self.parametros: list[dict] = []

    def query(self, query, job_config=None, location=None):
        self.consultas.append(query)
        valores = {p.name: list(p.values) for p in (job_config.query_parameters or [])}
        self.parametros.append(valores)

        skus = set(valores.get("skus", []))
        skus_num = set(valores.get("skus_num", []))

        # Emulacion del WHERE: las dos comparaciones son alternativas.
        elegidas = []
        for fila in self.filas:
            crudo = str(fila["id_producto"])
            try:
                numero = int(float(crudo))
            except (TypeError, ValueError):
                numero = None
            if crudo in skus or (numero is not None and numero in skus_num):
                elegidas.append(fila)

        if not elegidas:
            return _FakeJob(
                pd.DataFrame(
                    columns=["sku", "cod_tienda", "stock_tiendas", "stock_bodega", "fecha_corte"]
                )
            )
        return _FakeJob(
            pd.DataFrame(
                [
                    {
                        "sku": str(fila["id_producto"]),
                        "cod_tienda": str(fila["codigo_tienda"]),
                        "stock_tiendas": fila.get("stock_tiendas", 0),
                        "stock_bodega": fila.get("stock_bodega", 0),
                        "fecha_corte": fila.get("fecha_corte", "2026-09-02"),
                    }
                    for fila in elegidas
                ]
            )
        )


def fuente(filas: list[dict], **kwargs) -> tuple[BigQueryStockSource, FakeClient]:
    cliente = FakeClient(filas)
    source = BigQueryStockSource(project_id="demo", **kwargs)
    source._client = lambda: cliente  # type: ignore[method-assign]
    return source, cliente


# ---------------------------------------------------------------------------
@case("El SKU entero cruza: es el caso que siempre funciono")
def test_entero():
    source, _ = fuente([{"id_producto": 5438957, "codigo_tienda": 59, "stock_tiendas": 4}])
    stock = source.fetch(["5438957"])
    assert build_stock_index(stock) == {("5438957", "59"): 4}, stock.to_dict("records")


@case("El SKU con ceros a la izquierda cruza por la comparacion numerica")
def test_ceros():
    source, cliente = fuente(
        [{"id_producto": "0005438957", "codigo_tienda": "59", "stock_tiendas": 4}]
    )
    stock = source.fetch(["5438957"])
    assert build_stock_index(stock) == {("5438957", "59"): 4}, stock.to_dict("records")
    assert 5438957 in cliente.parametros[0]["skus_num"]


@case("El campo numerico guardado como texto cruza por la variante `.0`")
def test_decimal():
    source, cliente = fuente(
        [{"id_producto": "5438957.0", "codigo_tienda": "59", "stock_tiendas": 4}]
    )
    stock = source.fetch(["5438957"])
    assert build_stock_index(stock) == {("5438957", "59"): 4}, stock.to_dict("records")
    assert "5438957.0" in cliente.parametros[0]["skus"]


@case("Dos formas del mismo SKU en la tabla se suman, no se pisan")
def test_dos_formas_del_mismo_sku():
    source, _ = fuente(
        [
            {"id_producto": "5438957", "codigo_tienda": "59", "stock_tiendas": 3},
            {"id_producto": "0005438957", "codigo_tienda": "59", "stock_tiendas": 4},
        ]
    )
    stock = source.fetch(["5438957"])
    assert build_stock_index(stock) == {("5438957", "59"): 7}, stock.to_dict("records")


@case("Un SKU que no esta en la tabla no inventa filas")
def test_sin_coincidencia():
    source, _ = fuente([{"id_producto": "1111111", "codigo_tienda": "59", "stock_tiendas": 4}])
    stock = source.fetch(["5438957"])
    assert stock.empty, stock.to_dict("records")


@case("Se mandan los dos parametros: texto y numerico")
def test_parametros():
    source, cliente = fuente([{"id_producto": "5438957", "codigo_tienda": "59"}])
    source.fetch(["5438957", "0A12"])
    valores = cliente.parametros[0]
    assert set(valores) == {"skus", "skus_num"}, valores
    assert sorted(valores["skus"]) == ["0A12", "5438957", "5438957.0"]
    assert valores["skus_num"] == [5438957]


@case("Una `stock_query` propia sin @skus_num no recibe ese parametro")
def test_consulta_propia_sin_parametro_numerico():
    # BigQuery no tiene por que aceptar un parametro que la consulta no usa.
    consulta = "SELECT * FROM `t` WHERE CAST(id_producto AS STRING) IN UNNEST(@skus)"
    source, cliente = fuente(
        [{"id_producto": "5438957", "codigo_tienda": "59", "stock_tiendas": 2}],
        custom_query=consulta,
    )
    source.fetch(["5438957"])
    assert list(cliente.parametros[0]) == ["skus"], cliente.parametros[0]
    assert cliente.consultas[0] == consulta


@case("La bodega central suma su stock_bodega tambien viniendo de BigQuery")
def test_bodega_central():
    source, _ = fuente(
        [
            {"id_producto": "5438957", "codigo_tienda": "320", "stock_tiendas": 1, "stock_bodega": 9},
            {"id_producto": "5438957", "codigo_tienda": "59", "stock_tiendas": 1, "stock_bodega": 9},
        ]
    )
    index = build_stock_index(source.fetch(["5438957"]))
    assert index == {("5438957", "320"): 10, ("5438957", "59"): 1}, index


@case("La corrida deja trazas: consulta usada y filas devueltas")
def test_trazas():
    source, _ = fuente([{"id_producto": "5438957", "codigo_tienda": "59", "stock_tiendas": 4}])
    stock = source.fetch(["5438957"])
    assert stock.attrs["filas_crudas"] == 1
    assert "ultimo_corte" in stock.attrs["consulta"]


@case("Sin SKU utilizables no se llama a BigQuery")
def test_sin_skus():
    source, cliente = fuente([{"id_producto": "5438957", "codigo_tienda": "59"}])
    assert source.fetch(["", None]).empty
    assert cliente.consultas == []


@case("La tabla configurada es la que se consulta")
def test_tabla():
    source, cliente = fuente(
        [{"id_producto": "5438957", "codigo_tienda": "59"}], table="proyecto.dataset.tabla"
    )
    source.fetch(["5438957"])
    assert "proyecto.dataset.tabla" in cliente.consultas[0]
    assert cliente.consultas[0] == build_stock_query("proyecto.dataset.tabla")


@case("La consulta por defecto no se rompio: sigue siendo un solo SELECT")
def test_forma_de_la_consulta():
    query = build_stock_query(settings.DEFAULT_STOCK_TABLE)
    assert query.count("SELECT") == 2, query  # la del CTE `ultimo_corte` y la principal
    assert "GROUP BY sku, cod_tienda" in query


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
