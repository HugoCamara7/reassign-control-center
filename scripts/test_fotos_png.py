# -*- coding: utf-8 -*-
"""Pruebas del Mantenedor Fotos PNG y de que el motor normal no cambió.

La regla que estas pruebas protegen: **la carga normal busca SOLO .jpg**.
Buscar las dos extensiones duplicaría las peticiones HEAD por producto y
alargaría cada carga del catálogo completo. El PNG es una herramienta manual,
aparte, para los casos en que la foto solo existe en ese formato
(Hush Puppies).

Ejecutar:  python scripts/test_fotos_png.py
"""
import inspect
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _identity_decorator(*args, **kwargs):
    if args and callable(args[0]):
        return args[0]

    def _decorator(func):
        return func

    return _decorator


class _Secrets(dict):
    def get(self, key, default=None):
        return super().get(key, default if default is not None else {})


class _StreamlitStub(types.ModuleType):
    session_state = {}
    secrets = _Secrets()
    cache_data = staticmethod(_identity_decorator)
    cache_resource = staticmethod(_identity_decorator)

    def __getattr__(self, name):
        def _noop(*args, **kwargs):
            return None

        return _noop


if "streamlit" not in sys.modules:
    stub = _StreamlitStub("streamlit")
    comp = types.ModuleType("streamlit.components")
    comp_v1 = types.ModuleType("streamlit.components.v1")
    stub.__path__ = []
    comp.__path__ = []
    comp.v1 = comp_v1
    stub.components = comp
    sys.modules["streamlit"] = stub
    sys.modules["streamlit.components"] = comp
    sys.modules["streamlit.components.v1"] = comp_v1

import app_matrixify as app  # noqa: E402
import generate_columbia_matrixify as g  # noqa: E402

CONFIG = g.get_brand_config("hush_puppies")
CODIGO = "HP1234567-001"
BASE = CONFIG["image_base_url"]


class TestElMotorNormalNoCambio(unittest.TestCase):
    """La carga normal sigue siendo JPG y nada más."""

    def test_image_candidates_solo_jpg(self):
        urls = g.image_candidates(CODIGO, CONFIG)
        self.assertEqual(len(urls), g.MAX_IMAGES_PER_PRODUCT)
        self.assertTrue(all(url.endswith(".jpg") for url in urls))
        self.assertNotIn("png", " ".join(urls).lower())

    def test_el_generador_no_sabe_de_png(self):
        """Ninguna función de fotos del motor menciona PNG."""
        for funcion in (g.image_candidates, g.build_image_lookup, g.url_is_image):
            with self.subTest(funcion=funcion.__name__):
                self.assertNotIn("png", inspect.getsource(funcion).lower())

    def test_la_carga_parcial_de_fotos_tampoco(self):
        """`build_matrixify_updates` arma las fotos sin tocar el camino PNG."""
        self.assertNotIn("png", inspect.getsource(app.build_matrixify_updates).lower())

    def test_el_mantenedor_no_reutiliza_la_funcion_del_motor(self):
        """Son caminos distintos a propósito: uno no puede arrastrar al otro.

        `png_image_candidates` sí vale; lo que no puede aparecer es una llamada
        a `image_candidates`, la del motor rápido.
        """
        fuente = inspect.getsource(app.png_probe_views)
        self.assertIsNone(re.search(r"\bimage_candidates\(", fuente))
        self.assertIn("png_image_candidates(", fuente)


class TestCandidatasPng(unittest.TestCase):
    """Diez vistas, en orden, con el mismo nombre que el motor JPG."""

    def test_diez_vistas_en_orden(self):
        urls = app.png_image_candidates(CODIGO, CONFIG)
        self.assertEqual(len(urls), app.PNG_MAX_VISTAS)
        self.assertEqual(urls[0], f"{BASE}/HP1234567_001_1.png")
        self.assertEqual(urls[-1], f"{BASE}/HP1234567_001_10.png")
        self.assertTrue(all(url.endswith(".png") for url in urls))

    def test_mismo_nombre_que_el_jpg(self):
        jpg = g.image_candidates(CODIGO, CONFIG)
        png = app.png_image_candidates(CODIGO, CONFIG)
        self.assertEqual(
            [url.rsplit(".", 1)[0] for url in jpg],
            [url.rsplit(".", 1)[0] for url in png],
        )

    def test_un_codigo_sin_color_no_da_nada(self):
        self.assertEqual(app.png_image_candidates("HP1234567", CONFIG), [])
        self.assertEqual(app.png_image_candidates("", CONFIG), [])


class TestNoRepetirFotos(unittest.TestCase):
    """Una foto que el producto ya tiene no se vuelve a subir."""

    def test_reconoce_la_misma_foto(self):
        self.assertTrue(app.png_already_uploaded("hp1234567_001_1", {"hp1234567_001_1"}))

    def test_acepta_el_sufijo_que_agrega_shopify(self):
        self.assertTrue(app.png_already_uploaded("hp1234567_001_1", {"hp1234567_001_1_a1b2c3"}))

    def test_la_vista_1_no_se_confunde_con_la_10(self):
        """El error clásico de comparar con startswith."""
        self.assertFalse(app.png_already_uploaded("hp1234567_001_1", {"hp1234567_001_10"}))

    def test_la_extension_no_importa_para_comparar(self):
        stem = app.png_file_stem(f"{BASE}/HP1234567_001_1.png")
        existente = app.png_file_stem("https://cdn.shopify.com/s/files/1/HP1234567_001_1.jpg?v=17")
        self.assertEqual(stem, existente)


class TestEstadoDeCadaVista(unittest.TestCase):
    """Encontrada / Ya existente / No existe / Sin confirmar, vista por vista.

    Se sustituye `png_comprobar_url`, que es la unica puerta a la red. Devuelve
    True (existe), False (404) o None (no se pudo comprobar).
    """

    def setUp(self):
        self.original = app.png_comprobar_url
        # Solo existen en PNG las vistas 1, 2 y 3; el resto da 404.
        app.png_comprobar_url = lambda url, brand_config=None, timeout=4: (
            (True, "") if any(url.endswith(f"_{numero}.png") for numero in (1, 2, 3))
            else (False, "404 en el bucket")
        )

    def tearDown(self):
        app.png_comprobar_url = self.original

    def test_marca_las_que_existen(self):
        filas = app.png_probe_views(CODIGO, CONFIG)
        estados = [fila["Estado"] for fila in filas]
        self.assertEqual(estados[:3], ["Encontrada"] * 3)
        self.assertEqual(set(estados[3:]), {"No existe"})

    def test_marca_las_que_el_producto_ya_tiene(self):
        actuales = ["https://cdn.shopify.com/s/files/1/HP1234567_001_2.png?v=1"]
        filas = app.png_probe_views(CODIGO, CONFIG, actuales)
        por_vista = {fila["Vista"]: fila["Estado"] for fila in filas}
        self.assertEqual(por_vista[1], "Encontrada")
        self.assertEqual(por_vista[2], "Ya existente")
        self.assertEqual(por_vista[3], "Encontrada")

    def test_solo_se_suben_las_encontradas_y_en_orden(self):
        actuales = ["https://cdn.shopify.com/s/files/1/HP1234567_001_2.png"]
        subir = app.png_views_to_upload(app.png_probe_views(CODIGO, CONFIG, actuales))
        self.assertEqual(subir, [f"{BASE}/HP1234567_001_1.png", f"{BASE}/HP1234567_001_3.png"])

    def test_nunca_mas_de_diez(self):
        app.png_comprobar_url = lambda url, brand_config=None, timeout=4: (True, "")
        filas = app.png_probe_views(CODIGO, CONFIG)
        self.assertEqual(len(filas), app.PNG_MAX_VISTAS)
        self.assertLessEqual(len(app.png_views_to_upload(filas)), 10)

    def test_lo_que_no_se_pudo_comprobar_no_se_da_por_perdido(self):
        """Regresion: el bucket contesta 403 a las consultas anonimas.

        Tratar ese 403 como "no existe" dejaba 310 vistas en "Sin PNG" y cero
        encontradas. No es un no: es un no se sabe, y quien baja la imagen de
        verdad es Shopify.
        """
        app.png_comprobar_url = lambda url, brand_config=None, timeout=4: (None, "HEAD 403")
        filas = app.png_probe_views(CODIGO, CONFIG)
        self.assertEqual({fila["Estado"] for fila in filas}, {"Sin confirmar"})
        self.assertEqual(len(app.png_views_to_upload(filas)), app.PNG_MAX_VISTAS)

    def test_un_404_si_es_un_no(self):
        app.png_comprobar_url = lambda url, brand_config=None, timeout=4: (False, "404 en el bucket")
        filas = app.png_probe_views(CODIGO, CONFIG)
        self.assertEqual({fila["Estado"] for fila in filas}, {"No existe"})
        self.assertEqual(app.png_views_to_upload(filas), [])


class _Respuesta:
    """Respuesta minima con la forma que espera el codigo."""

    def __init__(self, status=200, content_type="image/png", datos=b"png"):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self._datos = datos

    def read(self):
        return self._datos

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestComprobarUrl(unittest.TestCase):
    """La unica puerta a la red del mantenedor PNG.

    Tres respuestas y no dos: existe, no existe (404) y no se pudo comprobar.
    El bucket contesta 403 a las consultas anonimas, y tratarlo como "no
    existe" era lo que dejaba 310 vistas en "Sin PNG" y cero encontradas.
    """

    def setUp(self):
        self.urlopen_original = app.urlopen
        self.descarga_original = app._download_image_bytes

    def tearDown(self):
        app.urlopen = self.urlopen_original
        app._download_image_bytes = self.descarga_original

    def _sin_descarga(self):
        def _revienta(url):
            raise app.ShopifyApiError("403")

        app._download_image_bytes = _revienta

    def test_una_imagen_que_responde_es_encontrada(self):
        app.urlopen = lambda peticion, timeout=None: _Respuesta()
        existe, _ = app.png_comprobar_url(f"{BASE}/HP1234567_001_1.png", CONFIG)
        self.assertIs(existe, True)

    def test_un_404_es_un_no_rotundo(self):
        def _cuatrocientos_cuatro(peticion, timeout=None):
            raise app.HTTPError(peticion.full_url, 404, "Not Found", {}, None)

        app.urlopen = _cuatrocientos_cuatro
        existe, detalle = app.png_comprobar_url(f"{BASE}/HP1234567_001_9.png", CONFIG)
        self.assertIs(existe, False)
        self.assertIn("404", detalle)

    def test_un_403_no_se_da_por_perdido_y_se_confirma_descargando(self):
        """Regresion: el 403 del bucket se leia como foto inexistente."""
        def _prohibido(peticion, timeout=None):
            raise app.HTTPError(peticion.full_url, 403, "Forbidden", {}, None)

        app.urlopen = _prohibido
        app._download_image_bytes = lambda url: (b"png", "image/png", "f.png", url)
        existe, detalle = app.png_comprobar_url(f"{BASE}/HP1234567_001_1.png", CONFIG)
        self.assertIs(existe, True)
        self.assertIn("descargando", detalle)

    def test_si_tampoco_se_puede_descargar_es_que_no_esta(self):
        def _prohibido(peticion, timeout=None):
            raise app.HTTPError(peticion.full_url, 403, "Forbidden", {}, None)

        app.urlopen = _prohibido
        self._sin_descarga()
        existe, _ = app.png_comprobar_url(f"{BASE}/HP1234567_001_1.png", CONFIG)
        self.assertIs(existe, False)

    def test_sin_confirmar_cuando_se_pide_no_descargar(self):
        def _prohibido(peticion, timeout=None):
            raise app.HTTPError(peticion.full_url, 403, "Forbidden", {}, None)

        app.urlopen = _prohibido
        existe, _ = app.png_comprobar_url(
            f"{BASE}/HP1234567_001_1.png", CONFIG, confirmar_descargando=False
        )
        self.assertIsNone(existe)

    def test_un_html_de_error_no_cuenta_como_imagen(self):
        app.urlopen = lambda peticion, timeout=None: _Respuesta(content_type="text/html")
        self._sin_descarga()
        existe, _ = app.png_comprobar_url(f"{BASE}/HP1234567_001_1.png", CONFIG)
        self.assertIs(existe, False)


class TestUrlsQueSeConsultan(unittest.TestCase):
    """Se comprueban las MISMAS URLs que usa la carga al subir la foto."""

    def test_incluye_las_candidatas_de_la_carga(self):
        url = f"{BASE}/HP1234567_001_1.png"
        aprobar = app.png_urls_a_probar(url, CONFIG)
        for candidata in app._image_url_candidates(url):
            self.assertIn(candidata, aprobar)

    def test_agrega_el_host_alterno_del_bucket(self):
        url = f"{BASE}/HP1234567_001_1.png"
        aprobar = app.png_urls_a_probar(url, CONFIG)
        self.assertIn(g.validation_url(url, CONFIG), aprobar)

    def test_no_repite_ninguna(self):
        aprobar = app.png_urls_a_probar(f"{BASE}/HP1234567_001_1.png", CONFIG)
        self.assertEqual(len(aprobar), len(set(aprobar)))


class TestBuscarElProducto(unittest.TestCase):
    def test_encuentra_por_codigo_modelo_color(self):
        productos = [{"Mod-Col": "HP0000000-999"}, {"Mod-Col": CODIGO, "Handle": "zapato"}]
        self.assertEqual(app.png_find_product(productos, CODIGO)["Handle"], "zapato")

    def test_no_distingue_mayusculas(self):
        productos = [{"Mod-Col": CODIGO}]
        self.assertIsNotNone(app.png_find_product(productos, CODIGO.lower()))

    def test_devuelve_none_si_no_esta(self):
        self.assertIsNone(app.png_find_product([{"Mod-Col": "OTRO-001"}], CODIGO))
        self.assertIsNone(app.png_find_product([], ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
