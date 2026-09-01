"""Pruebas del descuento de stock reservado.

    python -m scripts.test_stock_reservado

El caso que motiva todo esto: una tienda figura con 3 unidades y las 3 estan
reservadas. Sin descontar la reserva, la app la ve disponible y le manda un
pedido que despues no se puede despachar. El contrato que fijan estos casos es
uno solo: `stock` es el **disponible neto**, nunca el bruto.
"""

from __future__ import annotations

import sys

import pandas as pd

from core.stock_source import (
    ManualStockSource,
    _finalize,
    build_stock_query,
    detect_reserved_columns,
    reserved_columns_from_secrets,
    split_reserved_override,
)

CASES: list[tuple[str, object]] = []


def case(name: str):
    def decorator(fn):
        CASES.append((name, fn))
        return fn

    return decorator


def archivo(filas: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
@case("3 unidades con 3 reservadas: disponible 0")
def test_todo_reservado():
    datos = archivo(
        [{"sku": "5337735", "cod_tienda": "59", "stock": 3, "stock_reservado": 3}]
    )
    out = ManualStockSource(datos, True).fetch(["5337735"])
    assert int(out.loc[0, "stock"]) == 0, out.to_dict("records")
    assert int(out.loc[0, "stock_reservado"]) == 3, out.to_dict("records")


@case("Reserva parcial: 5 unidades con 2 reservadas dejan 3")
def test_reserva_parcial():
    datos = archivo([{"sku": "A", "cod_tienda": "59", "stock": 5, "reservado": 2}])
    out = ManualStockSource(datos, True).fetch(["A"])
    assert int(out.loc[0, "stock"]) == 3, out.to_dict("records")


@case("La reserva no puede dejar el disponible en negativo")
def test_nunca_negativo():
    datos = archivo([{"sku": "A", "cod_tienda": "59", "stock": 2, "stock_reservado": 9}])
    out = ManualStockSource(datos, True).fetch(["A"])
    assert int(out.loc[0, "stock"]) == 0, out.to_dict("records")


@case("Sin columna de reserva se comporta como antes")
def test_sin_columna():
    datos = archivo([{"sku": "A", "cod_tienda": "59", "stock": 4}])
    out = ManualStockSource(datos, True).fetch(["A"])
    assert int(out.loc[0, "stock"]) == 4, out.to_dict("records")
    assert int(out.loc[0, "stock_reservado"]) == 0
    assert out.attrs.get("reserva_columnas") == []


@case("Bodega central: la reserva de bodega descuenta del total")
def test_bodega_central():
    crudo = archivo(
        [
            {
                "sku": "A",
                "cod_tienda": "320",
                "stock_tiendas": 2,
                "stock_bodega": 10,
                "reservado_tiendas": 1,
                "reservado_bodega": 4,
            }
        ]
    )
    out = _finalize(crudo, include_central_warehouse=True)
    # (2 + 10) - (1 + 4) = 7
    assert int(out.loc[0, "stock"]) == 7, out.to_dict("records")
    assert int(out.loc[0, "stock_reservado"]) == 5, out.to_dict("records")


@case("Tienda fisica: ni el stock ni la reserva de bodega entran")
def test_tienda_no_central():
    crudo = archivo(
        [
            {
                "sku": "A",
                "cod_tienda": "59",
                "stock_tiendas": 6,
                "stock_bodega": 10,
                "reservado_tiendas": 2,
                "reservado_bodega": 8,
            }
        ]
    )
    out = _finalize(crudo, include_central_warehouse=True)
    # Solo sala: 6 - 2 = 4. La reserva de bodega no resta unidades que nunca
    # se contaron.
    assert int(out.loc[0, "stock"]) == 4, out.to_dict("records")
    assert int(out.loc[0, "stock_reservado"]) == 2, out.to_dict("records")


@case("Con incluir_stock_bodega_central apagado tampoco resta su reserva")
def test_sin_bodega_central():
    crudo = archivo(
        [
            {
                "sku": "A",
                "cod_tienda": "320",
                "stock_tiendas": 5,
                "stock_bodega": 10,
                "reservado_tiendas": 1,
                "reservado_bodega": 9,
            }
        ]
    )
    out = _finalize(crudo, include_central_warehouse=False)
    assert int(out.loc[0, "stock"]) == 4, out.to_dict("records")


@case("descontar_stock_reservado apagado: vuelve el stock bruto")
def test_descuento_apagado():
    datos = archivo([{"sku": "A", "cod_tienda": "59", "stock": 3, "stock_reservado": 3}])
    out = ManualStockSource(datos, True, subtract_reserved=False).fetch(["A"])
    assert int(out.loc[0, "stock"]) == 3, out.to_dict("records")
    assert int(out.loc[0, "stock_reservado"]) == 0, out.to_dict("records")


@case("Filas repetidas del mismo corte: la reserva se aplica sobre el total")
def test_consolidado():
    datos = archivo(
        [
            {"sku": "A", "cod_tienda": "59", "stock": 3, "stock_reservado": 5,
             "fecha_corte": "2026-08-20"},
            {"sku": "A", "cod_tienda": "59", "stock": 4, "stock_reservado": 0,
             "fecha_corte": "2026-08-20"},
        ]
    )
    out = ManualStockSource(datos, True).fetch(["A"])
    assert len(out) == 1, out.to_dict("records")
    # (3 + 4) - 5 = 2. Netear fila por fila daria 4 e inventaria 2 unidades.
    assert int(out.loc[0, "stock"]) == 2, out.to_dict("records")


@case("Reserva de un corte viejo no descuenta del corte vigente")
def test_reserva_corte_viejo():
    datos = archivo(
        [
            {"sku": "A", "cod_tienda": "59", "stock": 9, "stock_reservado": 9,
             "fecha_corte": "2025-06-30"},
            {"sku": "A", "cod_tienda": "59", "stock": 4, "stock_reservado": 1,
             "fecha_corte": "2026-08-20"},
        ]
    )
    out = ManualStockSource(datos, True).fetch(["A"])
    assert len(out) == 1, out.to_dict("records")
    assert int(out.loc[0, "stock"]) == 3, out.to_dict("records")
    assert out.loc[0, "fecha_corte"] == "2026-08-20"


@case("Una reserva negativa en el origen no suma disponible")
def test_reserva_negativa():
    datos = archivo([{"sku": "A", "cod_tienda": "59", "stock": 4, "stock_reservado": -3}])
    out = ManualStockSource(datos, True).fetch(["A"])
    assert int(out.loc[0, "stock"]) == 4, out.to_dict("records")


@case("Alias reconocidos de la columna de reserva")
def test_alias():
    for alias in ("stock_reservado", "reservado", "reserva", "unidades_reservadas",
                  "cantidad_reservada", "stock_comprometido", "STOCK_RESERVADO"):
        datos = archivo([{"sku": "A", "cod_tienda": "59", "stock": 3, alias: 3}])
        out = ManualStockSource(datos, True).fetch(["A"])
        assert int(out.loc[0, "stock"]) == 0, (alias, out.to_dict("records"))


@case("Deteccion en el esquema: gana la columna especifica sobre la generica")
def test_deteccion_esquema():
    encontradas = detect_reserved_columns(
        ["id_producto", "codigo_tienda", "stock_tiendas", "stock_bodega",
         "stock_reservado_tiendas", "stock_reservado_bodega", "fecha_corte"]
    )
    assert encontradas["reservado_tiendas"] == ["stock_reservado_tiendas"], encontradas
    assert encontradas["reservado_bodega"] == ["stock_reservado_bodega"], encontradas

    generica = detect_reserved_columns(["id_producto", "stock_tiendas", "stock_reservado"])
    assert generica["reservado_tiendas"] == ["stock_reservado"], generica
    assert generica["reservado_bodega"] == [], generica

    ninguna = detect_reserved_columns(["id_producto", "stock_tiendas", "stock_bodega"])
    assert ninguna == {"reservado_tiendas": [], "reservado_bodega": []}, ninguna


@case("La consulta solo nombra columnas de reserva que existen")
def test_consulta_generada():
    # Sin columnas de reserva en la tabla, la consulta selecciona 0 constante:
    # nombrar una columna inexistente reventaria la consulta entera.
    sin_reserva = build_stock_query(detect_reserved_columns(["stock_tiendas"]))
    for linea in sin_reserva.splitlines():
        if "AS reservado_" in linea:
            assert linea.strip().startswith("0"), linea
    assert "SUM(COALESCE(CAST(s.`" not in sin_reserva, sin_reserva

    con_reserva = build_stock_query({"reservado_tiendas": ["stock_reservado"], "reservado_bodega": []})
    seleccion = " ".join(con_reserva.split())
    assert (
        "SUM(COALESCE(CAST(s.`stock_reservado` AS INT64), 0)) AS reservado_tiendas" in seleccion
    ), con_reserva
    assert "0 AS reservado_bodega" in seleccion, con_reserva
    assert "{reservado_tiendas}" not in con_reserva
    assert "{reservado_bodega}" not in con_reserva
    # El marcador de tabla se resuelve despues, en el `fetch`.
    assert "{table}" in con_reserva


@case("Columnas de reserva declaradas a mano en los secrets")
def test_override_secrets():
    assert reserved_columns_from_secrets({"stock_reserved_columns": "a, b"}) == ("a", "b")
    assert reserved_columns_from_secrets({"stock_reserved_columns": ["a"]}) == ("a",)
    assert reserved_columns_from_secrets({}) == ()

    partido = split_reserved_override(["reserva_sala", "reserva_bodega_central"])
    assert partido["reservado_tiendas"] == ["reserva_sala"], partido
    assert partido["reservado_bodega"] == ["reserva_bodega_central"], partido


@case("BigQuery resuelve la reserva contra el esquema real de la tabla")
def test_resolucion_bigquery():
    from core.stock_source import BigQueryStockSource

    class _Campo:
        def __init__(self, name):
            self.name = name

    class _Tabla:
        def __init__(self, columnas):
            self.schema = [_Campo(nombre) for nombre in columnas]

    class _Cliente:
        def __init__(self, columnas):
            self._tabla = _Tabla(columnas)

        def get_table(self, _):
            return self._tabla

    class _ClienteSinPermiso:
        def get_table(self, _):
            raise RuntimeError("403")

    fuente = BigQueryStockSource()
    encontradas = fuente.resolve_reserved_columns(
        _Cliente(["id_producto", "stock_tiendas", "stock_reservado"])
    )
    assert encontradas["reservado_tiendas"] == ["stock_reservado"], encontradas

    # Sin permiso para leer el esquema no se inventa la columna: se consulta
    # sin descuento y la app lo avisa en pantalla.
    assert fuente.resolve_reserved_columns(_ClienteSinPermiso()) == {
        "reservado_tiendas": [],
        "reservado_bodega": [],
    }

    # Lo declarado a mano manda sobre la deteccion automatica.
    manual = BigQueryStockSource(reserved_columns=("mi_reserva",))
    assert manual.resolve_reserved_columns(_Cliente(["stock_reservado"])) == {
        "reservado_tiendas": ["mi_reserva"],
        "reservado_bodega": [],
    }

    # Con el descuento apagado no se busca nada.
    apagado = BigQueryStockSource(subtract_reserved=False)
    assert apagado.resolve_reserved_columns(_Cliente(["stock_reservado"])) == {
        "reservado_tiendas": [],
        "reservado_bodega": [],
    }


@case("El indice que consume el motor usa el disponible neto")
def test_indice_motor():
    from core.stock_source import build_stock_index

    datos = archivo(
        [
            {"sku": "A", "cod_tienda": "59", "stock": 3, "stock_reservado": 3},
            {"sku": "A", "cod_tienda": "60", "stock": 5, "stock_reservado": 1},
        ]
    )
    indice = build_stock_index(ManualStockSource(datos, True).fetch(["A"]))
    # La tienda con todo reservado no entra al indice: no es candidata.
    assert ("A", "59") not in indice, indice
    assert indice[("A", "60")] == 4, indice


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
