"""Pruebas del descuento temporal de stock. Es la parte critica del motor.

    python -m scripts.test_stock_ledger

La invariante que se defiende aqui es una sola y no admite excepciones:

    para todo par (SKU, tienda), la suma de unidades reasignadas
    NUNCA supera el stock que BigQuery reporto al inicio.

Cada caso construye un escenario donde un motor mal hecho sobre-asignaria, y
comprueba ademas el orden: quien se queda con la unidad y quien queda sin ella.
"""

from __future__ import annotations

import sys
from collections import defaultdict

import pandas as pd

from config import settings
from core import engine
from core.priority import PriorityConfig
from core.validation import resolve_columns

HEADERS = [
    settings.COL_ORDER,
    settings.COL_SHGROUP,
    settings.COL_SITE,
    settings.COL_STATUS,
    settings.COL_STORE_NAME,
    settings.COL_STORE_CODE,
    settings.COL_BRAND,
    settings.COL_SKU,
    settings.COL_UNITS,
]

CASES: list[tuple[str, object]] = []


def case(name: str):
    def decorator(fn):
        CASES.append((name, fn))
        return fn

    return decorator


def config_con(tiendas: dict[str, str], **params) -> PriorityConfig:
    """`tiendas` = {codigo: nombre}, en orden de prioridad."""
    config = PriorityConfig(params={**settings.DEFAULT_PARAMS, **params})
    config.stores = {
        code: {"cod_tienda": code, "nom_tienda": name, "activo": True, "stock_seguridad": 0}
        for code, name in tiendas.items()
    }
    config.rules = [
        {
            "sitio": "*", "marca": "*", "cod_tienda": code, "nom_tienda": name,
            "prioridad": position, "activo": True, "stock_seguridad": 0, "max_unidades": 0,
        }
        for position, (code, name) in enumerate(tiendas.items(), start=1)
    ]
    return config


def pedidos(*filas: tuple[str, str, int]) -> pd.DataFrame:
    """Cada fila es `(pedido, sku, unidades)`."""
    rows = [
        {
            settings.COL_ORDER: order_id,
            settings.COL_SHGROUP: "",
            settings.COL_SITE: "columbiaperu",
            settings.COL_STATUS: "SIN_STOCK",
            settings.COL_STORE_NAME: "",
            settings.COL_STORE_CODE: "",
            settings.COL_BRAND: "Columbia",
            settings.COL_SKU: sku,
            settings.COL_UNITS: units,
        }
        for order_id, sku, units in filas
    ]
    return pd.DataFrame(rows, columns=HEADERS, dtype=object)


def correr(orders: pd.DataFrame, stock: dict, config: PriorityConfig):
    return engine.reassign(
        df=orders, headers=HEADERS, resolved=resolve_columns(HEADERS), config=config,
        stock_index=stock, stock_cutoff="2026-08-20", include_trace=True,
    )


def verificar_invariante(result, stock: dict) -> None:
    """Suma lo reasignado por (SKU, tienda) y lo compara con el stock inicial."""
    tomado: dict[tuple[str, str], int] = defaultdict(int)
    for _, row in result.detail.iterrows():
        code = str(row["Cod tienda reasignada"] or "")
        if code:
            tomado[(row["SKU"], code)] += int(row["Unidades"])
    for (sku, code), usado in tomado.items():
        inicial = stock.get((sku, code), 0)
        assert usado <= inicial, (
            f"SOBRE-ASIGNACION en SKU {sku} tienda {code}: "
            f"se repartieron {usado} unidades y solo habia {inicial}"
        )


# ---------------------------------------------------------------------------
@case("Dos ordenes, mismo SKU, una sola unidad: la segunda queda SIN STOCK")
def test_dos_ordenes_una_unidad():
    config = config_con({"10": "TIENDA A"})
    stock = {("SKU1", "10"): 1}
    result = correr(pedidos(("P1", "SKU1", 1), ("P2", "SKU1", 1)), stock, config)
    verificar_invariante(result, stock)

    asignadas = list(result.detail["Tienda reasignada"])
    resultados = list(result.detail["Resultado"])
    assert asignadas == ["TIENDA A", ""], asignadas
    assert resultados[1] == settings.RESULT_NO_OPTION, resultados
    assert result.kpis.unidades_reasignadas == 1, result.kpis.unidades_reasignadas


@case("Dos ordenes, mismo SKU: la segunda pasa a la siguiente tienda por prioridad")
def test_segunda_orden_baja_de_tienda():
    config = config_con({"10": "TIENDA A", "20": "TIENDA B"}, reserva_por_tienda="0")
    stock = {("SKU1", "10"): 1, ("SKU1", "20"): 1}
    result = correr(pedidos(("P1", "SKU1", 1), ("P2", "SKU1", 1)), stock, config)
    verificar_invariante(result, stock)
    assert list(result.detail["Tienda reasignada"]) == ["TIENDA A", "TIENDA B"]


@case("Cinco ordenes contra 3 unidades repartidas: 3 salen, 2 quedan sin stock")
def test_cinco_ordenes_tres_unidades():
    config = config_con({"10": "TIENDA A", "20": "TIENDA B"}, reserva_por_tienda="0")
    stock = {("SKU1", "10"): 2, ("SKU1", "20"): 1}
    filas = tuple((f"P{i}", "SKU1", 1) for i in range(1, 6))
    result = correr(pedidos(*filas), stock, config)
    verificar_invariante(result, stock)

    assert result.kpis.unidades_reasignadas == 3, result.kpis.unidades_reasignadas
    assert result.kpis.sin_stock == 2, result.kpis.sin_stock
    assert list(result.detail["Tienda reasignada"]) == [
        "TIENDA A", "TIENDA A", "TIENDA B", "", "",
    ], list(result.detail["Tienda reasignada"])


@case("Una orden de varias unidades consume el stock de golpe")
def test_orden_multiunidad():
    config = config_con({"10": "TIENDA A", "20": "TIENDA B"}, reserva_por_tienda="0")
    stock = {("SKU1", "10"): 3, ("SKU1", "20"): 5}
    # P1 pide 3 y vacia A; P2 pide 1 y debe irse a B.
    result = correr(pedidos(("P1", "SKU1", 3), ("P2", "SKU1", 1)), stock, config)
    verificar_invariante(result, stock)
    assert list(result.detail["Tienda reasignada"]) == ["TIENDA A", "TIENDA B"]
    assert result.kpis.unidades_reasignadas == 4


@case("Una orden que pide mas de lo que hay en total queda sin opcion")
def test_pide_mas_que_el_total():
    config = config_con({"10": "TIENDA A", "20": "TIENDA B"})
    stock = {("SKU1", "10"): 2, ("SKU1", "20"): 2}
    # 4 unidades existen, pero repartidas: ninguna tienda sola las cubre.
    result = correr(pedidos(("P1", "SKU1", 4),), stock, config)
    verificar_invariante(result, stock)
    assert result.detail.loc[0, "Resultado"] == settings.RESULT_NO_OPTION
    assert result.kpis.unidades_reasignadas == 0


@case("SKUs distintos no se roban stock entre si")
def test_skus_independientes():
    config = config_con({"10": "TIENDA A"}, reserva_por_tienda="0")
    stock = {("SKU1", "10"): 1, ("SKU2", "10"): 1}
    result = correr(pedidos(("P1", "SKU1", 1), ("P2", "SKU2", 1)), stock, config)
    verificar_invariante(result, stock)
    assert list(result.detail["Tienda reasignada"]) == ["TIENDA A", "TIENDA A"]
    assert result.kpis.unidades_reasignadas == 2


@case("Una tienda que llega a cero desaparece para los pedidos siguientes")
def test_tienda_en_cero_desaparece():
    config = config_con({"10": "TIENDA A", "20": "TIENDA B", "30": "TIENDA C"},
                        reserva_por_tienda="0")
    stock = {("SKU1", "10"): 1, ("SKU1", "20"): 1, ("SKU1", "30"): 1}
    filas = tuple((f"P{i}", "SKU1", 1) for i in range(1, 5))
    result = correr(pedidos(*filas), stock, config)
    verificar_invariante(result, stock)
    assert list(result.detail["Tienda reasignada"]) == [
        "TIENDA A", "TIENDA B", "TIENDA C", "",
    ], list(result.detail["Tienda reasignada"])
    # El stock restante que se reporta debe ser 0 en las tres.
    restantes = [r for r in result.detail["Stock restante"] if r != ""]
    assert restantes == [0, 0, 0], restantes


@case("El stock restante informado coincide con el descuento real")
def test_stock_restante_coherente():
    config = config_con({"10": "TIENDA A"}, reserva_por_tienda="0")
    stock = {("SKU1", "10"): 5}
    filas = tuple((f"P{i}", "SKU1", 1) for i in range(1, 6))
    result = correr(pedidos(*filas), stock, config)
    verificar_invariante(result, stock)
    assert list(result.detail["Stock disponible"]) == [5, 4, 3, 2, 1]
    assert list(result.detail["Stock restante"]) == [4, 3, 2, 1, 0]


@case("Carga alta: 300 ordenes del mismo SKU contra 40 unidades")
def test_carga_alta():
    tiendas = {str(10 + i): f"TIENDA {i:02d}" for i in range(20)}
    config = config_con(tiendas, reserva_por_tienda="0")
    stock = {("SKU1", code): 2 for code in tiendas}  # 20 tiendas x 2 = 40
    filas = tuple((f"P{i}", "SKU1", 1) for i in range(300))
    result = correr(pedidos(*filas), stock, config)
    verificar_invariante(result, stock)
    assert result.kpis.unidades_reasignadas == 40, result.kpis.unidades_reasignadas
    assert result.kpis.sin_stock == 260, result.kpis.sin_stock
    # Ninguna tienda puede haber cedido mas de 2.
    assert result.store_summary["unidades_reasignadas"].max() == 2


@case("Mezcla real: varios SKU, varias tiendas, unidades mixtas")
def test_mezcla_realista():
    tiendas = {"10": "TIENDA A", "20": "TIENDA B", "30": "TIENDA C"}
    config = config_con(tiendas, reserva_por_tienda="0")
    stock = {
        ("SKU1", "10"): 2, ("SKU1", "20"): 1,
        ("SKU2", "20"): 3,
        ("SKU3", "30"): 1,
    }
    result = correr(
        pedidos(
            ("P1", "SKU1", 1), ("P2", "SKU1", 2), ("P3", "SKU1", 1),
            ("P4", "SKU2", 2), ("P5", "SKU2", 2),
            ("P6", "SKU3", 1), ("P7", "SKU3", 1),
            ("P8", "SKU9", 1),
        ),
        stock, config,
    )
    verificar_invariante(result, stock)
    esperado = {
        "P1": "TIENDA A",   # A: 2 -> 1
        "P2": "",           # pide 2: A tiene 1, B tiene 1, ninguna sola alcanza
        "P3": "TIENDA A",   # A: 1 -> 0
        "P4": "TIENDA B",   # B(SKU2): 3 -> 1
        "P5": "",           # pide 2, quedaba 1
        "P6": "TIENDA C",   # C: 1 -> 0
        "P7": "",           # ya no queda
        "P8": "",           # SKU inexistente
    }
    real = dict(zip(result.detail["Pedido"], result.detail["Tienda reasignada"]))
    assert real == esperado, real
    # P1(1) + P3(1) de SKU1, P4(2) de SKU2, P6(1) de SKU3 = 5 unidades.
    assert result.kpis.unidades_reasignadas == 5, result.kpis.unidades_reasignadas


@case("El stock de BigQuery nunca se modifica: el indice de entrada queda intacto")
def test_no_muta_el_stock_de_entrada():
    config = config_con({"10": "TIENDA A"}, reserva_por_tienda="0")
    stock = {("SKU1", "10"): 3}
    copia = dict(stock)
    correr(pedidos(("P1", "SKU1", 1), ("P2", "SKU1", 1)), stock, config)
    assert stock == copia, f"el motor modifico el stock de entrada: {stock} != {copia}"


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
