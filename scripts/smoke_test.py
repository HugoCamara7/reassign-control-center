"""Prueba end-to-end sin Streamlit ni BigQuery.

Lee un Excel real de pedidos, genera un stock sintetico determinista y
ejecuta el motor completo, verificando las invariantes del negocio:

* nunca se reasigna a la tienda de origen;
* la suma de unidades tomadas por (SKU, tienda) jamas supera el stock;
* el archivo final conserva todas las columnas originales.

Uso:
    python -m scripts.smoke_test "ruta\\al\\archivo.xls" [ESTADO,ESTADO...]

El segundo argumento fuerza los estados a reasignar, para probar archivos que
usan otra nomenclatura (por ejemplo PENDIENTE_ASIGNACION).
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

from config import settings
from core import engine, excel_io
from core.priority import load_priority
from core.stock_source import ManualStockSource, build_stock_index, stock_cutoff
from core.validation import validate

DEFAULT_SOURCE = Path(
    r"C:\Users\hcamara\OneDrive - Peru Forus S.A\Documentos\Formato de Carga Reasignacion.xls"
)


def synthetic_stock(skus: list[str], stores: list[str], units: int = 2) -> pd.DataFrame:
    """Stock reproducible: cada SKU aparece en un subconjunto de tiendas."""
    rows = []
    for position, sku in enumerate(skus):
        for offset in range(3):
            store = stores[(position + offset * 7) % len(stores)]
            rows.append(
                {
                    "sku": sku,
                    "cod_tienda": store,
                    "stock_tiendas": units,
                    "stock_bodega": 0,
                    "fecha_corte": "2026-08-17",
                }
            )
    return pd.DataFrame(rows)


def main(source: Path, estados: list[str] | None = None) -> int:
    print(f"Archivo: {source.name}")
    payload = excel_io.read_orders(source.read_bytes(), source.name)
    print(f"  hoja='{payload.sheet_name}' filas={payload.n_rows} columnas={len(payload.headers)}")
    for note in payload.notes:
        print(f"  nota: {note}")

    config = load_priority()
    if estados:
        config.params["estados_objetivo"] = ",".join(estados)
    print(f"\nPrioridad: {config.source} | tiendas={config.store_count} sitios={config.site_count}")
    print(f"Estados objetivo: {', '.join(config.target_statuses)}")
    for issue in config.issues:
        print(f"  aviso: {issue}")

    report = validate(payload.df, payload.headers, config)
    print(f"\nValidacion: objetivo={report.target_rows} errores={len(report.errors)} alertas={len(report.warnings)}")
    for finding in report.findings:
        print(f"  [{finding.level:6}] {finding.title}: {finding.detail[:110]}")
    if report.has_errors:
        print("\nFALLO: la validacion reporta errores.")
        return 1

    skus = engine.target_skus(payload.df, report.resolved, config)
    stores = sorted({rule["cod_tienda"] for rule in config.rules if rule["cod_tienda"]})
    stock = ManualStockSource(synthetic_stock(skus, stores), True).fetch(skus)
    index = build_stock_index(stock)
    print(f"\nStock sintetico: {len(stock)} combinaciones, {int(stock['stock'].sum())} unidades")

    result = engine.reassign(
        df=payload.df,
        headers=payload.headers,
        resolved=report.resolved,
        config=config,
        stock_index=index,
        stock_cutoff=stock_cutoff(stock),
        include_trace=True,
    )
    kpis = result.kpis
    print("\nKPIs")
    for _, row in kpis.to_frame().iterrows():
        print(f"  {row['Indicador']:<24} {row['Valor']}")

    # --- invariantes -------------------------------------------------------
    failures: list[str] = []

    # La misma auditoria que corre la app antes de dejar descargar el Excel.
    failures.extend(engine.verify_result(result, index, config, payload.headers))

    taken: dict[tuple[str, str], int] = defaultdict(int)
    for _, row in result.detail.iterrows():
        code = excel_io.as_text(row["Cod tienda reasignada"])
        if not code:
            continue
        taken[(row["SKU"], code)] += int(row["Unidades"])
    for (sku, code), used in taken.items():
        available = index.get((sku, code), 0)
        if used > available:
            failures.append(f"Sobre-asignacion en SKU {sku} tienda {code}: {used} > {available}")

    origin_col = report.resolved.get(settings.COL_STORE_NAME)
    if origin_col:
        for _, row in result.detail.iterrows():
            origin = excel_io.normalize_store_name(row["Tienda origen"])
            target = excel_io.normalize_store_name(row["Tienda reasignada"])
            if origin and target and origin == target:
                failures.append(f"Pedido {row['Pedido']} reasignado a su propia tienda origen ({origin})")

    missing_columns = [header for header in payload.headers if header not in result.output_df.columns]
    if missing_columns:
        failures.append(f"Columnas originales perdidas: {missing_columns}")

    if config.output_column not in result.output_headers:
        failures.append(f"Falta la columna de salida '{config.output_column}'")

    counted = kpis.reasignados + kpis.reasignados_parciales + kpis.sin_stock + kpis.errores
    if counted != kpis.pedidos_a_reasignar:
        failures.append(f"KPIs descuadrados: {counted} != {kpis.pedidos_a_reasignar}")

    # --- escritura ---------------------------------------------------------
    out_dir = settings.BASE_DIR / "outputs"
    out_dir.mkdir(exist_ok=True)
    final = out_dir / "smoke_reasignacion.xlsx"
    final.write_bytes(
        excel_io.write_orders(result.output_df, result.output_headers, payload.sheet_name)
    )
    print(f"\nArchivo generado: {final}")

    reread = excel_io.read_orders(final.read_bytes(), final.name)
    if len(reread.df) != len(result.output_df):
        failures.append("El archivo escrito no se relee con el mismo numero de filas.")
    for header in payload.headers:
        if header not in reread.headers:
            failures.append(f"Encabezado perdido al escribir: '{header}'")

    print("\nMuestra de la vista previa:")
    print(result.preview.head(8).to_string(index=False))

    if failures:
        print("\nFALLOS DETECTADOS:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nOK: todas las invariantes se cumplen.")
    return 0


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    estados = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    sys.exit(main(path, estados))
