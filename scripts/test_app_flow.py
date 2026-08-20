"""Pruebas de la app con el framework oficial de Streamlit (AppTest).

    python -m scripts.test_app_flow

Ejercita la aplicacion real, sin navegador: pantalla de acceso, credenciales
correctas e incorrectas, y el arranque del flujo con la sesion iniciada.
"""

from __future__ import annotations

import sys

from streamlit.testing.v1 import AppTest

from config import settings

APP = str(settings.BASE_DIR / "app.py")
SECRETS = {"app_auth": {"users": {"hugo.camara@forus.pe": "clave-de-prueba"}}}

CASES: list[tuple[str, object]] = []


def case(name: str):
    def decorator(fn):
        CASES.append((name, fn))
        return fn

    return decorator


def start(secrets: dict | None = None) -> AppTest:
    app = AppTest.from_file(APP, default_timeout=60)
    if secrets:
        for section, values in secrets.items():
            app.secrets[section] = values
    return app.run()


@case("Sin [app_auth] la app arranca abierta, sin pantalla de acceso")
def test_open_app():
    # AppTest hereda el secrets.toml real del proyecto, asi que la seccion se
    # vacia a proposito para reproducir un despliegue sin login configurado.
    app = start({"app_auth": {}})
    body = " ".join(element.value for element in app.markdown)
    assert "Sistema exclusivo" not in body, "no deberia pedir login"
    assert "authenticated" not in app.session_state
    assert not app.exception, app.exception


@case("Con [app_auth] se muestra la pantalla de acceso")
def test_login_shown():
    app = start(SECRETS)
    body = " ".join(element.value for element in app.markdown)
    assert "Reassign Control Center" in body, "falta el nombre de la app"
    assert "Sistema exclusivo para personal autorizado" in body
    assert len(app.text_input) == 2, f"se esperaban 2 campos, hay {len(app.text_input)}"
    assert not app.exception, app.exception


@case("Credenciales correctas: entra y queda la sesion iniciada")
def test_login_ok():
    app = start(SECRETS)
    app.text_input[0].set_value("hugo.camara@forus.pe")
    app.text_input[1].set_value("clave-de-prueba")
    app.button[0].click().run()
    assert app.session_state["authenticated"] is True, "no autentico"
    assert app.session_state["auth_user"] == "hugo.camara@forus.pe"
    assert not app.exception, app.exception


@case("El correo se normaliza: mayusculas y espacios no bloquean el acceso")
def test_login_normalizes_email():
    app = start(SECRETS)
    app.text_input[0].set_value("  Hugo.Camara@Forus.pe  ")
    app.text_input[1].set_value("clave-de-prueba")
    app.button[0].click().run()
    assert app.session_state["authenticated"] is True


@case("Credenciales incorrectas: no entra y avisa")
def test_login_fail():
    app = start(SECRETS)
    app.text_input[0].set_value("hugo.camara@forus.pe")
    app.text_input[1].set_value("clave-equivocada")
    app.button[0].click().run()
    assert "authenticated" not in app.session_state, "no debio autenticar"
    assert app.error, "faltaba el mensaje de error"
    assert "incorrect" in app.error[0].value.lower()


@case("Usuario inexistente: no entra")
def test_unknown_user():
    app = start(SECRETS)
    app.text_input[0].set_value("otra.persona@forus.pe")
    app.text_input[1].set_value("clave-de-prueba")
    app.button[0].click().run()
    assert "authenticated" not in app.session_state
    assert app.error


@case("Con sesion iniciada aparece el flujo, los pasos y el cargador de archivo")
def test_flow_after_login():
    app = start(SECRETS)
    app.session_state["authenticated"] = True
    app.session_state["auth_user"] = "hugo.camara@forus.pe"
    app.run()
    body = " ".join(element.value for element in app.markdown)
    assert "Reasignacion de pedidos sin stock" in body, "falta la cabecera principal"
    assert "Subir archivo de pedidos" in body, "falta el paso 1"
    assert "Subir archivo" in body and "Descargar" in body, "falta el rail de pasos"
    assert app.file_uploader, "falta el cargador del archivo de pedidos"
    assert not app.exception, app.exception


@case("La prioridad versionada se carga sola y se ve en la barra lateral")
def test_priority_visible():
    from core.priority import load_priority

    app = start(SECRETS)
    app.session_state["authenticated"] = True
    app.run()
    sidebar_text = " ".join(element.value for element in app.sidebar.markdown)
    assert "SIN_STOCK, SIN_DESPACHO" in sidebar_text, "no se ven los estados objetivo"

    # La barra reporta el numero real de tiendas del archivo del repositorio.
    tiendas = load_priority().store_count
    assert tiendas > 0, "la prioridad versionada no se cargo"
    assert f"{tiendas} tiendas" in sidebar_text, f"no aparece '{tiendas} tiendas'"
    assert not app.exception, app.exception


@case("Sin avisos de configuracion cuando la prioridad ya esta cargada")
def test_no_config_noise():
    app = start(SECRETS)
    app.session_state["authenticated"] = True
    app.run()
    cuerpo = " ".join(element.value for element in app.markdown)
    for ruido in ("No se encontro", "sube tu propia version", "prioridad_tiendas.ejemplo"):
        assert ruido not in cuerpo, f"la app sigue pidiendo configuracion: '{ruido}'"


@case("Cerrar sesion devuelve a la pantalla de acceso")
def test_logout():
    app = start(SECRETS)
    app.session_state["authenticated"] = True
    app.session_state["auth_user"] = "hugo.camara@forus.pe"
    app.run()
    logout = [button for button in app.sidebar.button if "Cerrar" in button.label]
    assert logout, "no hay boton de cerrar sesion"
    logout[0].click().run()
    assert "authenticated" not in app.session_state, "la sesion sigue abierta"


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
