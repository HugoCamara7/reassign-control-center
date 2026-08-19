"""Pruebas de las reglas de negocio del motor, con escenarios controlados.

    python -m scripts.test_rules

Cada caso arma un mini archivo de pedidos y un stock a medida, para que el
resultado esperado sea evidente sin depender de BigQuery ni del Excel real.
"""

from __future__ import annotations

import sys

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

STORES = {
    "10": "TIENDA A",
    "20": "TIENDA B",
    "30": "TIENDA C",
}


def make_config(**params) -> PriorityConfig:
    config = PriorityConfig(params={**settings.DEFAULT_PARAMS, **params})
    config.stores = {
        code: {"cod_tienda": code, "nom_tienda": name, "activo": True, "stock_seguridad": 0}
        for code, name in STORES.items()
    }
    config.rules = [
        {
            "sitio": "*",
            "marca": "*",
            "cod_tienda": code,
            "nom_tienda": name,
            "prioridad": position,
            "activo": True,
            "stock_seguridad": 0,
            "max_unidades": 0,
        }
        for position, (code, name) in enumerate(STORES.items(), start=1)
    ]
    return config


def make_orders(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=HEADERS, dtype=object)


def run(orders: pd.DataFrame, stock: dict, config: PriorityConfig):
    return engine.reassign(
        df=orders,
        headers=HEADERS,
        resolved=resolve_columns(HEADERS),
        config=config,
        stock_index=stock,
        stock_cutoff="2026-08-17",
        include_trace=True,
    )


def order(order_id: str, sku: str, units: int, store_name: str = "", store_code: str = "", **extra):
    row = {
        settings.COL_ORDER: order_id,
        settings.COL_SHGROUP: extra.get("shgroup", ""),
        settings.COL_SITE: extra.get("site", "columbiaperu"),
        settings.COL_STATUS: extra.get("status", "SIN_STOCK"),
        settings.COL_STORE_NAME: store_name,
        settings.COL_STORE_CODE: store_code,
        settings.COL_BRAND: extra.get("brand", "Columbia"),
        settings.COL_SKU: sku,
        settings.COL_UNITS: units,
    }
    return row


CASES: list[tuple[str, callable]] = []


def case(name: str):
    def decorator(fn):
        CASES.append((name, fn))
        return fn

    return decorator


# ---------------------------------------------------------------------------
@case("Regla 5: gana la tienda de mayor prioridad, no la de mas stock")
def test_priority_wins():
    orders = make_orders([order("P1", "S1", 1)])
    stock = {("S1", "10"): 1, ("S1", "20"): 99}
    result = run(orders, stock, make_config())
    assert result.detail.loc[0, "Tienda reasignada"] == "TIENDA A", result.detail.loc[0].to_dict()


@case("Regla 6: el stock se descuenta durante la misma corrida")
def test_stock_decrement():
    orders = make_orders([order("P1", "S1", 1), order("P2", "S1", 1), order("P3", "S1", 1)])
    stock = {("S1", "10"): 2, ("S1", "20"): 1}
    result = run(orders, stock, make_config())
    assigned = list(result.detail["Tienda reasignada"])
    assert assigned == ["TIENDA A", "TIENDA A", "TIENDA B"], assigned


@case("Regla 6: no se reparte mas stock del que existe")
def test_no_oversell():
    orders = make_orders([order(f"P{i}", "S1", 1) for i in range(5)])
    stock = {("S1", "10"): 2, ("S1", "20"): 1}
    result = run(orders, stock, make_config())
    assert result.kpis.unidades_reasignadas == 3, result.kpis.unidades_reasignadas
    assert result.kpis.sin_stock == 2, result.kpis.sin_stock


@case("Regla 6: unidades multiples requieren cobertura completa")
def test_units_must_fit():
    orders = make_orders([order("P1", "S1", 3)])
    stock = {("S1", "10"): 2, ("S1", "20"): 5}
    result = run(orders, stock, make_config())
    assert result.detail.loc[0, "Tienda reasignada"] == "TIENDA B", result.detail.loc[0].to_dict()


@case("Regla 7: nunca se reasigna a la tienda de origen (por codigo)")
def test_exclude_origin_by_code():
    orders = make_orders([order("P1", "S1", 1, store_code="10")])
    stock = {("S1", "10"): 5, ("S1", "20"): 5}
    result = run(orders, stock, make_config())
    assert result.detail.loc[0, "Tienda reasignada"] == "TIENDA B", result.detail.loc[0].to_dict()


@case("Regla 7: tambien se excluye por nombre cuando no viene el codigo")
def test_exclude_origin_by_name():
    orders = make_orders([order("P1", "S1", 1, store_name="tienda a")])
    stock = {("S1", "10"): 5, ("S1", "20"): 5}
    result = run(orders, stock, make_config())
    assert result.detail.loc[0, "Tienda reasignada"] == "TIENDA B", result.detail.loc[0].to_dict()


@case("Regla 7: se puede desactivar por configuracion")
def test_origin_rule_off():
    orders = make_orders([order("P1", "S1", 1, store_code="10")])
    stock = {("S1", "10"): 5, ("S1", "20"): 5}
    result = run(orders, stock, make_config(excluir_tienda_origen="NO"))
    assert result.detail.loc[0, "Tienda reasignada"] == "TIENDA A", result.detail.loc[0].to_dict()


@case("Regla 8: sin stock en ninguna tienda -> SIN OPCION DE REASIGNACION")
def test_no_option():
    orders = make_orders([order("P1", "S9", 1)])
    result = run(orders, {}, make_config())
    assert result.detail.loc[0, "Resultado"] == settings.RESULT_NO_OPTION
    assert result.output_df.loc[0, settings.COL_REASSIGNED] == ""


@case("Regla 2: solo se tocan los estados objetivo")
def test_status_filter():
    orders = make_orders(
        [
            order("P1", "S1", 1, status="SIN_STOCK"),
            order("P2", "S1", 1, status="SIN_DESPACHO"),
            order("P3", "S1", 1, status="ERROR_OPERADOR_LOGISTICO"),
        ]
    )
    stock = {("S1", "10"): 9}
    result = run(orders, stock, make_config())
    assert result.kpis.pedidos_a_reasignar == 2, result.kpis.pedidos_a_reasignar
    assert result.output_df.loc[2, "Reasig_Resultado"] == settings.RESULT_NOT_APPLICABLE
    assert result.output_df.loc[2, settings.COL_REASSIGNED] == ""


@case("Estados: 'sin stock' con espacio se normaliza igual que SIN_STOCK")
def test_status_normalization():
    orders = make_orders([order("P1", "S1", 1, status="sin stock")])
    stock = {("S1", "10"): 1}
    result = run(orders, stock, make_config())
    assert result.kpis.reasignados == 1, result.kpis.to_frame().to_dict()


@case("Stock de seguridad: reserva unidades intocables")
def test_safety_stock():
    orders = make_orders([order("P1", "S1", 1)])
    stock = {("S1", "10"): 2, ("S1", "20"): 5}
    result = run(orders, stock, make_config(stock_seguridad_global="2"))
    assert result.detail.loc[0, "Tienda reasignada"] == "TIENDA B", result.detail.loc[0].to_dict()


@case("Tope por tienda: max_unidades_por_tienda limita la carga")
def test_store_cap():
    orders = make_orders([order("P1", "S1", 1), order("P2", "S1", 1)])
    stock = {("S1", "10"): 9, ("S1", "20"): 9}
    result = run(orders, stock, make_config(max_unidades_por_tienda="1"))
    assigned = list(result.detail["Tienda reasignada"])
    assert assigned == ["TIENDA A", "TIENDA B"], assigned


@case("Reasignacion parcial: apagada por defecto, opcional por configuracion")
def test_partial():
    orders = make_orders([order("P1", "S1", 3)])
    stock = {("S1", "10"): 2}

    strict = run(orders, stock, make_config())
    assert strict.detail.loc[0, "Resultado"] == settings.RESULT_NO_OPTION

    loose = run(orders, stock, make_config(permitir_reasignacion_parcial="SI"))
    assert loose.detail.loc[0, "Resultado"] == engine.RESULT_PARTIAL
    assert loose.kpis.unidades_reasignadas == 2, loose.kpis.unidades_reasignadas


@case("ShGroup agrupado: las lineas de un despacho van a la misma tienda")
def test_group_by_shgroup():
    orders = make_orders(
        [
            order("P1", "S1", 1, shgroup="G1"),
            order("P1", "S2", 1, shgroup="G1"),
        ]
    )
    # TIENDA A solo tiene S1; solo TIENDA B cubre el despacho completo.
    stock = {("S1", "10"): 5, ("S1", "20"): 5, ("S2", "20"): 5}
    result = run(orders, stock, make_config(agrupar_por_shgroup="SI"))
    assigned = set(result.detail["Tienda reasignada"])
    assert assigned == {"TIENDA B"}, assigned


@case("ShGroup agrupado: sin cobertura total se puede exigir todo o nada")
def test_group_all_or_nothing():
    orders = make_orders(
        [
            order("P1", "S1", 1, shgroup="G1"),
            order("P1", "S2", 1, shgroup="G1"),
        ]
    )
    stock = {("S1", "10"): 5}  # S2 no existe en ninguna tienda

    split = run(orders, stock, make_config(agrupar_por_shgroup="SI"))
    assert split.detail.loc[0, "Tienda reasignada"] == "TIENDA A", split.detail.loc[0].to_dict()

    strict = run(
        orders,
        stock,
        make_config(agrupar_por_shgroup="SI", fallback_linea_si_grupo_falla="NO"),
    )
    assert set(strict.detail["Resultado"]) == {settings.RESULT_NO_OPTION}


@case("Regla 9: la columna de salida se crea si el archivo no la trae")
def test_output_column_created():
    orders = make_orders([order("P1", "S1", 1)])
    result = run(orders, {("S1", "10"): 1}, make_config())
    assert settings.COL_REASSIGNED in result.output_headers
    assert result.output_headers[: len(HEADERS)] == HEADERS
    assert result.output_df.loc[0, settings.COL_REASSIGNED] == "TIENDA A"


@case("Prioridad: una fila con el sitio exacto desplaza a la fila comodin")
def test_site_specificity():
    config = make_config()
    config.rules.append(
        {
            "sitio": "vansperu",
            "marca": "*",
            "cod_tienda": "30",
            "nom_tienda": "TIENDA C",
            "prioridad": 1,
            "activo": True,
            "stock_seguridad": 0,
            "max_unidades": 0,
        }
    )
    names = [rule.nom_tienda for rule in config.rules_for("vansperu", "Vans")]
    assert names == ["TIENDA C"], names
    generic = [rule.nom_tienda for rule in config.rules_for("columbiaperu", "Columbia")]
    assert generic == ["TIENDA A", "TIENDA B", "TIENDA C"], generic


@case("Columnas: 'Sitio_1' se reconoce como 'Sitio' (sufijo numerico de Excel)")
def test_alias_numeric_suffix():
    headers = ["Order", "Estado", "SKU", "Unidades", "Sitio_1"]
    resolved = resolve_columns(headers)
    assert resolved.get(settings.COL_SITE) == "Sitio_1", resolved


@case("Columnas: un encabezado con mojibake se reconoce igual")
def test_alias_mojibake():
    # 'Método_de_Despacho' guardado como UTF-8 leido en latin-1.
    headers = ["Order", "Estado", "SKU", "Unidades", "MÃ©todo_de_Despacho"]
    resolved = resolve_columns(headers)
    assert resolved.get(settings.COL_SHIPPING_METHOD) == "MÃ©todo_de_Despacho", resolved


@case("Columnas: un encabezado con tilde real tambien se reconoce")
def test_alias_accent():
    headers = ["Order", "Estado", "SKU", "Unidades", "Método_de_Despacho"]
    resolved = resolve_columns(headers)
    assert resolved.get(settings.COL_SHIPPING_METHOD) == "Método_de_Despacho", resolved


@case("Estados: se pueden reasignar estados propios como PENDIENTE_ASIGNACION")
def test_custom_status():
    orders = make_orders([order("P1", "S1", 1, status="PENDIENTE_ASIGNACION")])
    stock = {("S1", "10"): 5}
    config = make_config(estados_objetivo="PENDIENTE_ASIGNACION")
    result = run(orders, stock, config)
    assert result.kpis.pedidos_a_reasignar == 1, result.kpis.pedidos_a_reasignar
    assert result.detail.loc[0, "Tienda reasignada"] == "TIENDA A"


@case("Errores: una fila sin SKU se marca como ERROR, no como sin stock")
def test_missing_sku():
    orders = make_orders([order("P1", "", 1)])
    result = run(orders, {}, make_config())
    assert result.detail.loc[0, "Resultado"] == settings.RESULT_ERROR
    assert result.kpis.errores == 1


def main() -> int:
    passed, failed = 0, []
    for name, test in CASES:
        try:
            test()
        except AssertionError as exc:
            failed.append((name, str(exc)))
            print(f"  FALLO  {name}\n         {exc}")
        except Exception as exc:  # pragma: no cover
            failed.append((name, repr(exc)))
            print(f"  ERROR  {name}\n         {exc!r}")
        else:
            passed += 1
            print(f"  ok     {name}")

    print(f"\n{passed}/{len(CASES)} casos correctos.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
