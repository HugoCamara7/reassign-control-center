"""Genera `config/prioridad_tiendas.xlsx`, la configuracion editable.

Se puede ejecutar desde consola:

    python -m scripts.build_priority_template

o desde la barra lateral de la app ("Descargar plantilla").

El maestro de tiendas parte del listado de bodegas ecommerce de Forus, y la
prioridad inicial replica las bodegas habilitadas por sitio. **Es un punto de
partida**: el area comercial debe reordenar la columna `prioridad` segun su
criterio (cercania, rotacion, capacidad de despacho, etc.).
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from config import settings

# (codigo, nombre, activo, stock_seguridad)
STORE_MASTER: list[tuple[int, str, int, int]] = [
    (2, "RKF JOCKEY", 1, 0),
    (7, "HP CHACARILLA", 1, 0),
    (8, "HP JOCKEY", 1, 0),
    (12, "HP CHICLAYO", 1, 0),
    (16, "HP LARCOMAR", 1, 1),
    (18, "HPK JOCKEY", 1, 0),
    (19, "HP MEGAPLAZA", 1, 0),
    (20, "SE HUALLAGA", 0, 1),
    (22, "HP TRUJILLO", 1, 0),
    (23, "HP CHORRILLOS", 1, 0),
    (26, "RKF CUSCO", 1, 0),
    (29, "RKF TRUJILLO", 1, 0),
    (30, "HP PLAZA ANGAMOS", 1, 0),
    (31, "RKF AREQUIPA", 1, 0),
    (37, "HP PIURA 2", 1, 0),
    (38, "HP SANTA ANITA", 0, 0),
    (39, "RKF SAN BORJA", 0, 0),
    (43, "HP SAN MIGUEL 2", 1, 0),
    (44, "HP PLAZA NORTE", 1, 0),
    (46, "HPK PLAZA NORTE", 1, 0),
    (52, "RKF LARCOMAR 2", 1, 0),
    (55, "HP AREQUIPA CENTER", 1, 0),
    (58, "CLB CAJAMARCA", 1, 2),
    (59, "CLB JOCKEY PLAZA", 1, 0),
    (60, "RKF CUSCO 2", 1, 0),
    (61, "HP CUSCO", 1, 0),
    (64, "BBG TRUJILLO", 0, 0),
    (65, "BBG MEGAPLAZA", 0, 0),
    (70, "BBG CHICLAYO", 0, 0),
    (72, "BBG SAN MIGUEL", 0, 0),
    (83, "RKF SALAVERRY", 1, 0),
    (84, "CLB SALAVERRY", 1, 0),
    (86, "BBG SALAVERRY", 0, 3),
    (88, "DH AEROPUERTO 2", 1, 0),
    (92, "BBG OUTLET AEROPUERTO 2", 0, 1),
    (95, "FB OVALO PAPAL TRUJILLO", 1, 1),
    (96, "FB CAMACHO", 1, 1),
    (97, "HP SALAVERRY 2", 1, 0),
    (107, "BBG JOCKEY 2", 0, 3),
    (109, "BBG PIURA", 0, 0),
    (111, "DH LURIN", 1, 0),
    (112, "CLB LARCOMAR", 1, 0),
    (113, "CLB PLAZA NORTE", 1, 0),
    (114, "CLB SAN MIGUEL", 1, 0),
    (115, "CLB CAYMA", 1, 0),
    (116, "HP CAYMA", 1, 0),
    (117, "PAT CUSCO", 1, 0),
    (118, "CLB CUSCO", 1, 0),
    (119, "PAT SALAVERRY", 0, 0),
    (120, "PAT JOCKEY", 0, 0),
    (121, "CLB TRUJILLO", 0, 0),
    (122, "PAT LARCOMAR", 1, 0),
    (123, "FB LAMBRAMANI", 0, 1),
    (126, "CLB PIURA", 1, 0),
    (127, "JANSPORT JOCKEY PLAZA", 0, 0),
    (128, "CLB CUSCO PLAZA", 1, 0),
    (129, "HP LA MOLINA", 1, 0),
    (130, "RKF LA MOLINA", 1, 0),
    (131, "CLB CHICLAYO", 1, 0),
    (132, "CLB ICA", 1, 2),
    (133, "RKF ASIA 2", 0, 1),
    (134, "BSOUL ASIA", 0, 1),
    (135, "BBG ASIA 2", 0, 1),
    (136, "CLB ASIA", 0, 1),
    (137, "BSOUL JOCKEY PLAZA", 1, 0),
    (138, "BSOUL LA MOLINA", 1, 0),
    (139, "CLB LA MOLINA", 1, 0),
    (140, "CLB HUANCAYO", 1, 2),
    (141, "CLB PORONGOCHE", 1, 0),
    (142, "CLB MALL DEL SUR", 1, 0),
    (143, "CLB PARQUE LA MOLINA", 1, 0),
    (144, "PARFOIS JOCKEY PLAZA", 1, 1),
    (145, "CLB ANGAMOS", 1, 0),
    (147, "RKF CAYMA", 1, 0),
    (148, "RKF ICA", 1, 1),
    (149, "VANS PLAZA NORTE", 1, 0),
    (150, "VANS JOCKEY PLAZA", 1, 0),
    (151, "VANS SALAVERRY", 1, 0),
    (152, "VANS PARQUE LA MOLINA", 1, 0),
    (155, "VANS TRUJILLO", 1, 0),
    (156, "VANS PIURA", 1, 0),
    (157, "VANS CHICLAYO", 1, 0),
    (158, "PARFOIS SAN MIGUEL", 1, 1),
    (320, "BODEGA FORUS 320", 1, 0),
    (350, "CDP VENTAS CORPORATIVAS", 0, 1),
    (370, "BODEGA NORSEG", 0, 0),
    (400, "VIRTUAL", 1, 0),
]

STORE_NAMES = {code: name for code, name, _, _ in STORE_MASTER}
ACTIVE_STORES = [code for code, _, active, _ in STORE_MASTER if active]

# Orden inicial por sitio. El primero de la lista es prioridad 1.
# Punto de partida = bodegas ecommerce habilitadas hoy para cada sitio.
SITE_PRIORITY: dict[str, list[int]] = {
    "columbiaperu": [320, 59, 84, 145, 143, 142, 139, 114, 113, 112, 130, 111, 96, 88, 83, 52, 46, 19, 18, 2],
    "hushpuppiesperu": [320, 8, 97, 129, 44, 43, 30, 23, 19, 18, 16, 7, 111, 96, 88, 46],
    "rockfordperu": [320, 2, 83, 52, 130, 145, 143, 142, 139, 129, 122, 114, 113, 112, 111, 97, 96, 88, 84, 59, 44, 43, 30, 23, 19, 16, 8, 7],
    "vansperu": [320, 150, 151, 152, 149, 155, 156, 157],
    "parfoispe": [144, 158],
    "bsoulperu": [137, 138],
    "kedspe": [320, 8, 97, 44, 43],
    # supermallpe y shopstarpe son multimarca: heredan la lista general "*".
}

SITE_NOTES = {
    "columbiaperu": "Columbia.pe",
    "hushpuppiesperu": "Hushpuppies.pe",
    "rockfordperu": "Rockford.pe",
    "vansperu": "Vans.pe",
    "parfoispe": "Parfois.pe",
    "bsoulperu": "Bsoul.pe",
    "kedspe": "Keds.pe",
}


def build_frames() -> dict[str, pd.DataFrame]:
    priority_rows: list[dict] = []

    for site, codes in SITE_PRIORITY.items():
        for position, code in enumerate(codes, start=1):
            priority_rows.append(
                {
                    "sitio": site,
                    "marca": "*",
                    "cod_tienda": code,
                    "nom_tienda": STORE_NAMES.get(code, ""),
                    "prioridad": position,
                    "activo": "SI",
                    "stock_seguridad": 0,
                    "max_unidades": 0,
                }
            )

    # Lista general: cubre cualquier sitio sin bloque propio (supermallpe,
    # shopstarpe, sitios nuevos). La bodega central va primero.
    fallback = [320] + [code for code in ACTIVE_STORES if code not in (320, 400)]
    for position, code in enumerate(fallback, start=1):
        priority_rows.append(
            {
                "sitio": "*",
                "marca": "*",
                "cod_tienda": code,
                "nom_tienda": STORE_NAMES.get(code, ""),
                "prioridad": position,
                "activo": "SI",
                "stock_seguridad": 0,
                "max_unidades": 0,
            }
        )

    stores = pd.DataFrame(
        [
            {
                "cod_tienda": code,
                "nom_tienda": name,
                "activo": "SI" if active else "NO",
                "stock_seguridad": safety,
            }
            for code, name, active, safety in STORE_MASTER
        ]
    )

    params = pd.DataFrame(
        [
            {
                "parametro": key,
                "valor": value,
                "descripcion": settings.PARAM_HELP.get(key, ""),
            }
            for key, value in settings.DEFAULT_PARAMS.items()
        ]
    )

    instructions = pd.DataFrame(
        [
            {"Tema": "Prioridad", "Como se usa": "Una fila por tienda candidata. 'prioridad' 1 es la primera opcion."},
            {"Tema": "Comodines", "Como se usa": "sitio='*' o marca='*' aplica a todos. Una fila con el sitio exacto gana sobre '*'."},
            {"Tema": "Apagar una tienda", "Como se usa": "activo='NO' en la hoja Tiendas la desactiva en todos los sitios de una vez."},
            {"Tema": "Stock de seguridad", "Como se usa": "Unidades que nunca se comprometen. Gana el valor mas alto entre Prioridad, Tiendas y el parametro global."},
            {"Tema": "Tope por tienda", "Como se usa": "'max_unidades' limita cuantas unidades recibe esa tienda en una corrida. 0 = sin tope."},
            {"Tema": "Parametros", "Como se usa": "Reglas del motor. Usar SI / NO. 'estados_objetivo' acepta varios separados por coma."},
            {"Tema": "Importante", "Como se usa": "Este archivo es el punto de partida. Reordena 'prioridad' segun el criterio real de despacho."},
        ]
    )

    return {
        settings.SHEET_PRIORITY: pd.DataFrame(priority_rows),
        settings.SHEET_STORES: stores,
        settings.SHEET_PARAMS: params,
        "Instrucciones": instructions,
    }


def build_bytes() -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in build_frames().items():
            frame.to_excel(writer, sheet_name=name, index=False)
        for name, frame in build_frames().items():
            sheet = writer.sheets[name]
            sheet.freeze_panes = "A2"
            for column_index, column in enumerate(frame.columns, start=1):
                width = max(len(str(column)) + 2, 14)
                if column in ("descripcion", "Como se usa"):
                    width = 72
                elif column in ("nom_tienda", "parametro", "Tema"):
                    width = 26
                sheet.column_dimensions[
                    sheet.cell(row=1, column=column_index).column_letter
                ].width = width
    return buffer.getvalue()


def write_template(path: Path | None = None) -> Path:
    target = Path(path) if path else settings.PRIORITY_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build_bytes())
    return target


if __name__ == "__main__":
    written = write_template()
    print(f"Plantilla generada en: {written}")
