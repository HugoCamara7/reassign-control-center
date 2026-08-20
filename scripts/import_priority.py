"""Convierte la lista simple del area comercial en la configuracion completa.

    python -m scripts.import_priority "C:\\ruta\\Priorizacion Tiendas.xlsx"

El area comercial manda un archivo de una hoja con `ID Tienda | nombre |
Prioridad`. La app lo lee tal cual, pero asi no trae la hoja `Parametros` y las
reglas del motor quedan en sus valores por defecto.

Este script arma el archivo de 3 hojas conservando **sus** codigos, nombres y
prioridades, y agrega `Tiendas` y `Parametros`. El resultado se guarda como
`config/prioridad_tiendas.xlsx` (el operativo, no versionado).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd

from config import settings
from core.excel_io import as_text, normalize_store_code, to_int
from core.priority import _detect_priority_sheet, _lower_columns, _pick


def read_simple_list(path: Path) -> pd.DataFrame:
    book = pd.ExcelFile(path)
    sheet = _detect_priority_sheet(book)
    if not sheet:
        raise SystemExit(
            f"'{path.name}' no tiene una hoja con columnas de tienda y prioridad."
        )
    df = _lower_columns(book.parse(sheet, dtype=object).dropna(how="all"))

    rows = []
    for line, (_, row) in enumerate(df.iterrows(), start=2):
        code = normalize_store_code(
            _pick(row, "cod_tienda", "codigo_tienda", "id_tienda", "id", "bodega", "numbodega")
        )
        name = as_text(_pick(row, "nom_tienda", "nombre_tienda", "nombre", "tienda", "nombrebodega"))
        priority = to_int(_pick(row, "prioridad", "orden", "priority"), -1)
        if not code and not name:
            continue
        if priority < 0:
            print(f"  aviso: fila {line} ('{name or code}') sin prioridad numerica, va al final.")
            priority = 9999
        rows.append({"cod_tienda": code, "nom_tienda": name, "prioridad": priority})

    if not rows:
        raise SystemExit(f"'{path.name}' no tiene filas utilizables.")
    return pd.DataFrame(rows)


def build(simple: pd.DataFrame) -> dict[str, pd.DataFrame]:
    priority = simple.assign(
        sitio="*",
        marca="*",
        activo="SI",
        stock_seguridad=0,
        max_unidades=0,
    )[
        ["sitio", "marca", "cod_tienda", "nom_tienda", "prioridad",
         "activo", "stock_seguridad", "max_unidades"]
    ].sort_values(["prioridad", "nom_tienda"])

    stores = simple[["cod_tienda", "nom_tienda"]].drop_duplicates("cod_tienda").assign(
        activo="SI", stock_seguridad=0
    )

    params = pd.DataFrame(
        [
            {"parametro": key, "valor": value, "descripcion": settings.PARAM_HELP.get(key, "")}
            for key, value in settings.DEFAULT_PARAMS.items()
        ]
    )
    return {
        settings.SHEET_PRIORITY: priority,
        settings.SHEET_STORES: stores,
        settings.SHEET_PARAMS: params,
    }


def write(frames: dict[str, pd.DataFrame], target: Path) -> None:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in frames.items():
            frame.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            sheet.freeze_panes = "A2"
            for index, column in enumerate(frame.columns, start=1):
                width = 72 if column == "descripcion" else max(len(str(column)) + 2, 16)
                sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(buffer.getvalue())


def main(source: Path, target: Path | None = None) -> int:
    destino = target or settings.PRIORITY_FILE
    print(f"Origen : {source}")
    simple = read_simple_list(source)
    frames = build(simple)
    write(frames, destino)

    bandas = simple.groupby("prioridad").size().sort_index()
    print(f"Destino: {destino}\n")
    print(f"{len(simple)} tiendas, {len(bandas)} bandas de prioridad:")
    for banda, cuantas in bandas.items():
        print(f"  prioridad {banda:>4}: {cuantas} tienda(s)")
    print("\nHojas generadas:", ", ".join(frames))
    print("Revisa la hoja 'Parametros' para ajustar reserva_por_tienda y demas reglas.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    salida = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    sys.exit(main(Path(sys.argv[1]), salida))
