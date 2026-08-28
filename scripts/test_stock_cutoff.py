"""Pruebas del filtro por fecha de corte del stock.

    python -m scripts.test_stock_cutoff

El stock es una **foto**, no un acumulado. Si el origen trae historico y se
suman todos los cortes, la app cree que hay mas unidades de las que existen y
reasigna pedidos que despues no se pueden despachar.

Estos casos fijan una sola garantia: pase lo que pase, solo entra el corte mas
reciente.
"""

from __future__ import annotations

import sys

import pandas as pd

from core.stock_source import (
    ManualStockSource,
    _finalize,
    keep_latest_cutoff,
    latest_cutoff_value,
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
@case("Historico de dos anios: solo entra el corte de este anio")
def test_descarta_anio_pasado():
    datos = archivo(
        [
            {"sku": "5337735", "cod_tienda": "59", "stock": 4, "fecha_corte": "2025-11-30"},
            {"sku": "5337735", "cod_tienda": "59", "stock": 3, "fecha_corte": "2026-08-20"},
        ]
    )
    out = ManualStockSource(datos, True).fetch(["5337735"])
    assert len(out) == 1, out.to_dict("records")
    # 3, no 7: no se suman los dos cortes.
    assert int(out.loc[0, "stock"]) == 3, out.to_dict("records")
    assert out.loc[0, "fecha_corte"] == "2026-08-20"


@case("Varias tiendas con historico: cada una toma solo su corte vigente")
def test_varias_tiendas():
    datos = archivo(
        [
            {"sku": "A", "cod_tienda": "10", "stock": 9, "fecha_corte": "2025-01-15"},
            {"sku": "A", "cod_tienda": "20", "stock": 9, "fecha_corte": "2025-01-15"},
            {"sku": "A", "cod_tienda": "10", "stock": 2, "fecha_corte": "2026-08-20"},
            {"sku": "A", "cod_tienda": "20", "stock": 1, "fecha_corte": "2026-08-20"},
        ]
    )
    out = ManualStockSource(datos, True).fetch(["A"])
    total = int(out["stock"].sum())
    assert total == 3, f"se esperaban 3 unidades vigentes y salieron {total}"


@case("Fechas DD/MM/YYYY: se comparan como fecha, no como texto")
def test_formato_dia_primero():
    # Como texto, '31/12/2025' > '20/08/2026'. Como fecha, no.
    datos = archivo(
        [
            {"sku": "A", "cod_tienda": "10", "stock": 99, "fecha_corte": "31/12/2025"},
            {"sku": "A", "cod_tienda": "10", "stock": 5, "fecha_corte": "20/08/2026"},
        ]
    )
    out = ManualStockSource(datos, True).fetch(["A"])
    assert int(out.loc[0, "stock"]) == 5, out.to_dict("records")
    assert out.loc[0, "fecha_corte"] == "20/08/2026"


@case("Fechas con hora: el corte mas reciente del mismo dia gana")
def test_con_hora():
    datos = archivo(
        [
            {"sku": "A", "cod_tienda": "10", "stock": 8, "fecha_corte": "2026-08-20 06:00:00"},
            {"sku": "A", "cod_tienda": "10", "stock": 2, "fecha_corte": "2026-08-20 18:00:00"},
        ]
    )
    out = ManualStockSource(datos, True).fetch(["A"])
    assert int(out.loc[0, "stock"]) == 2, out.to_dict("records")


@case("Un solo corte: no se descarta nada")
def test_un_solo_corte():
    datos = archivo(
        [
            {"sku": "A", "cod_tienda": "10", "stock": 3, "fecha_corte": "2026-08-20"},
            {"sku": "A", "cod_tienda": "20", "stock": 4, "fecha_corte": "2026-08-20"},
        ]
    )
    out = ManualStockSource(datos, True).fetch(["A"])
    assert len(out) == 2 and int(out["stock"].sum()) == 7


@case("Sin columna de fecha: se procesa igual, sin filtrar")
def test_sin_fecha():
    datos = archivo([{"sku": "A", "cod_tienda": "10", "stock": 3}])
    out = ManualStockSource(datos, True).fetch(["A"])
    assert len(out) == 1 and int(out.loc[0, "stock"]) == 3


@case("Repetidos dentro del mismo corte si se suman")
def test_suma_dentro_del_corte():
    datos = archivo(
        [
            {"sku": "A", "cod_tienda": "10", "stock": 2, "fecha_corte": "2026-08-20"},
            {"sku": "A", "cod_tienda": "10", "stock": 3, "fecha_corte": "2026-08-20"},
        ]
    )
    out = ManualStockSource(datos, True).fetch(["A"])
    assert len(out) == 1 and int(out.loc[0, "stock"]) == 5, out.to_dict("records")


@case("Una consulta BigQuery sin filtro de fecha tambien queda saneada")
def test_consulta_sin_filtro():
    # Simula el resultado crudo de un stock_query propio que trae historico.
    crudo = pd.DataFrame(
        [
            {"id_producto": 111, "codigo_tienda": 59, "stock_tiendas": 7,
             "stock_bodega": 0, "fecha_corte": "2025-06-30"},
            {"id_producto": 111, "codigo_tienda": 59, "stock_tiendas": 1,
             "stock_bodega": 0, "fecha_corte": "2026-08-20"},
        ]
    )
    out = _finalize(crudo, include_central_warehouse=True)
    assert len(out) == 1, out.to_dict("records")
    assert int(out.loc[0, "stock"]) == 1
    assert out.attrs["filas_descartadas_por_fecha"] == 1
    assert out.attrs["fecha_corte"] == "2026-08-20"


@case("Se informa cuantas filas se descartaron por fecha")
def test_reporta_descartes():
    datos = pd.DataFrame(
        [
            {"sku": "A", "cod_tienda": "10", "stock_tiendas": 1, "fecha_corte": "2024-01-01"},
            {"sku": "A", "cod_tienda": "10", "stock_tiendas": 1, "fecha_corte": "2025-01-01"},
            {"sku": "A", "cod_tienda": "10", "stock_tiendas": 1, "fecha_corte": "2026-08-20"},
        ]
    )
    vigentes, descartadas, corte = keep_latest_cutoff(datos)
    assert descartadas == 2, descartadas
    assert corte == "2026-08-20"
    assert len(vigentes) == 1


@case("latest_cutoff_value ignora vacios y basura")
def test_valor_corte():
    serie = pd.Series(["", "2026-08-20", None, "2025-01-01"])
    assert latest_cutoff_value(serie) == "2026-08-20"
    assert latest_cutoff_value(pd.Series(["", None])) is None


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
