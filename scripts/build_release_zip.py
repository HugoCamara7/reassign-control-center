"""Empaqueta el proyecto en un .zip listo para subir a GitHub.

    python -m scripts.build_release_zip

Deja fuera, a proposito:

* `.streamlit/secrets.toml` — credenciales reales;
* `config/prioridad_tiendas.xlsx` — configuracion operativa (viaja el ejemplo);
* `outputs/`, `__pycache__/`, entornos virtuales y archivos de datos.

Al terminar verifica que ningun secreto se haya colado y falla si encuentra uno.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from config import settings

ARCHIVE_NAME = "reassign-control-center"

EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "env",
    ".claude",
    ".pytest_cache",
    ".ruff_cache",
    "outputs",
}
EXCLUDE_NAMES = {"secrets.toml", f"{ARCHIVE_NAME}.zip"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".xls", ".xlsx", ".csv", ".zip"}

# Excepciones: archivos que si deben viajar aunque su extension este excluida.
KEEP_ANYWAY = {"config/prioridad_tiendas.ejemplo.xlsx"}

# Si alguno de estos aparece dentro del zip, el empaquetado se considera fallido.
FORBIDDEN = ("secrets.toml",)


def collect(root: Path) -> tuple[list[tuple[Path, str]], list[str]]:
    included: list[tuple[Path, str]] = []
    skipped: list[str] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()

        if any(part in EXCLUDE_DIRS for part in path.relative_to(root).parts):
            continue
        if rel in KEEP_ANYWAY:
            included.append((path, rel))
            continue
        if path.name in EXCLUDE_NAMES or path.suffix.lower() in EXCLUDE_SUFFIXES:
            skipped.append(rel)
            continue
        included.append((path, rel))

    return included, skipped


def main() -> int:
    root = settings.BASE_DIR
    target = root / f"{ARCHIVE_NAME}.zip"
    included, skipped = collect(root)

    if not any(rel == "app.py" for _, rel in included):
        print("No se encontro app.py: se aborta el empaquetado.")
        return 1

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, rel in included:
            archive.write(path, f"{ARCHIVE_NAME}/{rel}")

    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
    leaks = [name for name in names if any(bad in name for bad in FORBIDDEN) and "example" not in name]

    size_kb = target.stat().st_size / 1024
    print(f"{target.name}  ({size_kb:.1f} KB, {len(included)} archivos)\n")
    for _, rel in included:
        print(f"  {rel}")
    if skipped:
        print("\nExcluidos a proposito:")
        for rel in skipped:
            print(f"  {rel}")

    if leaks:
        print(f"\nFALLO: el zip contiene archivos sensibles: {leaks}")
        target.unlink(missing_ok=True)
        return 1

    print("\nSin secretos en el paquete. Listo para subir a GitHub.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
