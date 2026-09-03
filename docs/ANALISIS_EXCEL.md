# Analisis del archivo `Formato de Carga Reasignacion.xls`

Archivo analizado: `Formato de Carga Reasignacion.xls` (338 KB, formato BIFF/OLE2 real).
Hoja unica: `shipping_groups_1783611991147` — **402 filas de datos, 58 columnas**.

> El nombre de la hoja lleva un timestamp variable, por eso la app **nunca** la
> busca por nombre: siempre lee la primera hoja del libro.

---

## 1. Columnas encontradas (58)

| # | Columna | Tipo real | % vacio | Uso en la app |
|---|---------|-----------|---------|----------------|
| 0 | `Fecha_Compra` | texto con comillas literales | 0 % | se conserva |
| 1 | `IngresoSistema` | texto ISO | 5 % | se conserva |
| 2 | `Order` | texto (`vns85884-01`) | 0 % | **clave del pedido** |
| 3 | `Platform_Order` | numero | 58 % | se conserva |
| 4 | `ShGroup` | numero (hasta 18 digitos) | 0 % | agrupacion de despacho |
| 5 | `Sitio` | texto | 0 % | **selecciona la lista de prioridad** |
| 6 | `Estado` | texto | 0 % | **filtro de pedidos a reasignar** |
| 7 | `Motivo_Rechazo` | texto | 100 % | se conserva |
| 8 | `Nom_Tienda_Asig` | texto | 94 % | **tienda de origen** |
| 9 | `Nom Tda Reasignada` | texto | 100 % | **columna de salida (ya existe)** |
| 10 | `No_Boleta` | numero | 98 % | se conserva |
| 11 | `Cod_Tienda_Asig` | numero | 94 % | **tienda de origen (codigo)** |
| 12 | `Mail` | texto | 0 % | se conserva |
| 13 | `Cod_Responsable` | numero | 94 % | se conserva |
| 14 | `Marca` | texto | 1 % | prioridad por marca |
| 15 | `Barra` | numero / texto | 1 % | se conserva |
| 16 | `SKU` | numero (`5438957.0`) | 0 % | **clave de stock** |
| 17 | `Nombre_Prod` | texto | 0 % | referencia |
| 18 | `Cod_Modelo` | texto | 1 % | referencia |
| 19-21 | `Nombre_Color`, `Cod_Color`, `Talla` | mixto | 1 % | referencia |
| 22 | `Unidades` | numero | 0 % | **unidades solicitadas** |
| 23-28 | `Precio_Unitario` … `Total` | numero | 0-37 % | se conservan |
| 29 | `Medio_de_Pago` | texto | 0 % | se conserva |
| 30 | `Metodo_de_Despacho` | texto | 0 % | contexto operativo |
| 31 | `N_Tracking_EnvÃ­o` | texto | 98 % | se conserva *(encabezado danado)* |
| 32-38 | datos del cliente | mixto | 0-29 % | se conservan |
| 39-45 | fechas de gestion | texto ISO | 94-100 % | se conservan |
| 46-47 | `AuthCode`, `UniqueNumber` | numero | 0 % | se conservan |
| 48-53 | `VenedorOrigen` … `Orden_Pagada` | texto | 94-100 % | se conservan |
| 54-57 | `C&S_Warehouse`, `Â¿Gestionada_por_Reversso?`, `Â¿Item dividido?`, `Â¿Sg divididad por unidad?` | SI/NO | 0 % | se conservan |

Las 58 columnas se escriben de vuelta en el mismo orden y con el mismo texto de encabezado.

---

## 2. Columnas obligatorias

**Obligatorias** (sin ellas la app se detiene en el paso de validacion):

| Columna | Por que |
|---------|---------|
| `Order` | identifica el pedido en la vista previa y el reporte |
| `Estado` | determina que filas se reasignan |
| `SKU` | clave de consulta contra BigQuery |
| `Unidades` | cantidad a cubrir; sin ella no se puede validar el stock |

**Recomendadas** (la app avisa y continua con menos control):

`ShGroup`, `Sitio`, `Nom_Tienda_Asig`, `Cod_Tienda_Asig`, `Marca`, `Talla`, `Metodo_de_Despacho`.

**De salida**: `Nom Tda Reasignada`. En este archivo **ya viene** (columna 9, 100 % vacia).
Si un archivo futuro no la trae, la app la agrega automaticamente al final.

Todas se resuelven tambien por alias (`pedido`, `cantidad`, `tienda origen`, …), asi que
un cambio de encabezado en origen no rompe el proceso.

---

## 3. Estados encontrados

| Estado | Filas | Se reasigna |
|--------|------:|-------------|
| `SIN_STOCK` | 322 | **SI** |
| `SIN_DESPACHO` | 74 | **SI** |
| `ERROR_OPERADOR_LOGISTICO` | 6 | no |

**Total a reasignar: 396 de 402 filas.**

> Ojo: los estados vienen con **guion bajo**, no con espacio. La app normaliza
> (`sin stock`, `Sin-Stock`, `SIN_STOCK` son el mismo estado), y la lista de
> estados objetivo es editable en la hoja `Parametros`.

Distribucion por sitio de las filas a reasignar:

| Sitio | Filas |
|-------|------:|
| `columbiaperu` | 156 |
| `supermallpe` | 112 |
| `rockfordperu` | 48 |
| `hushpuppiesperu` | 39 |
| `vansperu` | 20 |
| `parfoispe` | 13 |
| `shopstarpe` / `kedspe` | 3 / 3 |
| `bsoulperu` | 2 |

---

## 4. Riesgos e inconsistencias

### Criticos

**1. `ShGroup` pierde precision en el archivo de origen.**
245 de 402 filas traen un `ShGroup` de 16-19 digitos guardado como numero de coma
flotante (`8.41824206260704e+17`). Un `float64` solo garantiza ~15 digitos exactos,
asi que **los ultimos digitos ya vienen alterados desde el origen** (`…704000`,
`…705024`). La app lo escribe como texto para no empeorarlo, pero si ese campo se
usa como clave en la plataforma destino hay que pedirlo como texto desde el sistema
que genera el archivo.

**2. `Nom_Tienda_Asig` esta vacio en el 94 % de las filas** (378 de 402).
La regla "no reasignar a la misma tienda de origen" **solo puede aplicarse en 18
filas**. En las demas no hay tienda de origen que excluir. La app lo reporta como
alerta explicita en el paso 2. Si esa columna deberia venir llena, conviene
corregirlo en el origen antes de operar en volumen.

### Medios

**3. `SKU` llega como numero decimal** (`5438957.0`). Sin normalizar, el cruce con
BigQuery falla en el 100 % de los casos. La app lo canoniza a `5438957`.

**4. Encabezados con doble codificacion UTF-8** (mojibake real, no de lectura):
`N_Tracking_EnvÃ­o`, `Â¿Quien_Recibe?`, `Â¿Gestionada_por_Reversso?`,
`Â¿Item dividido?`, `Â¿Sg divididad por unidad?`.
Se conservan **byte a byte**: si la plataforma destino los espera asi, "arreglarlos"
romperia la carga.

**5. 37 `ShGroup` tienen mas de una linea** (92 filas). Un despacho partido entre
varias tiendas genera dos envios al cliente. Configurable con `agrupar_por_shgroup`;
por defecto se resuelve linea por linea.

**6. 59 SKU se repiten entre pedidos** (hasta 5 veces el mismo SKU, p.ej. `5360780`).
Sin descuento temporal de stock se reasignaria el mismo par cientos de veces. El
motor descuenta en memoria a medida que asigna.

### Menores

**7. `Marca` con casing inconsistente**: `Columbia` (271) vs `COLUMBIA` (15),
`Rockford` (21) vs `ROCKFORD` (4), `HUSH PUPPIES` / `Hush Puppies`. Ademas 5 filas
sin marca. La comparacion de prioridad es insensible a mayusculas y tildes.

**8. `Talla` heterogenea**: `445` (=44.5 de VANS, x10), `43`, `S`/`M`/`L`/`XL`,
`O/S`, `M/R`, y dos valores claramente corruptos — **`46176` y `46362`, que son
numeros de serie de fecha de Excel** (una talla tipo `8-9` convertida a fecha).
No afecta el motor, porque el cruce se hace por SKU y no por talla, pero es un
sintoma de que el archivo pasa por una conversion que danna datos.

**9. `Fecha_Compra` trae comillas dobles literales dentro del valor**
(`"2026-07-08T15:40:05"`). Se conserva tal cual.

**10. Sitios sin bodegas ecommerce definidas**: `supermallpe` (112 filas),
`shopstarpe` y `kedspe` no aparecen en la matriz de bodegas por sitio. La plantilla
los cubre con una lista general (`sitio = *`), pero conviene definir su prioridad
real.

**11. 3 filas piden 2 unidades** del mismo SKU. Con `permitir_reasignacion_parcial`
en `NO` (por defecto), una tienda debe cubrir las 2 o se descarta.

---

## 5. Estructura recomendada de la tabla de prioridades

Archivo `config/prioridad_tiendas.xlsx`, **fuera del codigo**, con 3 hojas de datos
y una de ayuda. La app la lee en cada ejecucion.

### Hoja `Prioridad` — el orden de preferencia

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `sitio` | texto | `columbiaperu`, `vansperu`, … o `*` para todos |
| `marca` | texto | `Columbia`, … o `*` para todas |
| `cod_tienda` | numero | codigo de bodega (`59`, `320`) |
| `nom_tienda` | texto | nombre exacto que se escribira en `Nom Tda Reasignada` |
| `prioridad` | numero | **1 = primera opcion**. Sin empates dentro de un mismo sitio |
| `activo` | SI/NO | apaga la fila sin borrarla |
| `stock_seguridad` | numero | unidades intocables en esa tienda |
| `max_unidades` | numero | tope de unidades por corrida. `0` = sin tope |

Resolucion: si existe al menos una fila con el `sitio` exacto, las filas `*` se
ignoran para ese sitio. Lo mismo despues con `marca`. El resultado es siempre
predecible y facil de explicar.

### Hoja `Tiendas` — el maestro

| Columna | Descripcion |
|---------|-------------|
| `cod_tienda` | codigo de bodega |
| `nom_tienda` | nombre canonico |
| `activo` | `NO` la apaga en **todos** los sitios de una sola vez |
| `stock_seguridad` | reserva por defecto de esa tienda |

Sembrada con las 88 bodegas del maestro ecommerce de Forus.

### Hoja `Parametros` — las reglas conmutables

| Parametro | Default | Efecto |
|-----------|---------|--------|
| `estados_objetivo` | `SIN_STOCK,SIN_DESPACHO` | que estados se reasignan |
| `excluir_tienda_origen` | `SI` | regla 7 |
| `permitir_reasignacion_parcial` | `NO` | permite cubrir parte de las unidades |
| `agrupar_por_shgroup` | `NO` | todas las lineas de un despacho a la misma tienda |
| `fallback_linea_si_grupo_falla` | `SI` | si nadie cubre el grupo, resuelve linea por linea |
| `incluir_stock_bodega_central` | `SI` | en la bodega 320 suma `stock_bodega` |
| `stock_seguridad_global` | `0` | reserva minima en todas las tiendas |
| `max_unidades_por_tienda` | `0` | tope global por tienda |
| `columna_salida` | `Nom Tda Reasignada` | nombre exacto de la columna destino |

---

## 6. Fuente de stock en BigQuery

Tabla: `forus-analitica-prod-datalake.bronze.stg_pe_central_stock_bi`
(la misma que ya usa Catalogo Control Center en produccion).

| Campo | Rol |
|-------|-----|
| `id_producto` | corresponde al `SKU` del archivo de pedidos |
| `codigo_tienda` | codigo de bodega/tienda |
| `stock_tiendas` | unidades en sala |
| `stock_bodega` | unidades en bodega — **solo suman en la bodega central 320** |
| `fecha_corte` | se toma siempre el ultimo cierre disponible |

La consulta esta parametrizada por `@skus` (lotes de 5.000) y filtra por el maximo
`fecha_corte`. **Es exclusivamente `SELECT`**: la app no tiene ninguna ruta de
escritura hacia BigQuery.

El filtro **no compara `CAST(id_producto AS STRING)` a secas**: normaliza el codigo
dentro de la propia consulta (mayusculas, `TRIM`, se recorta el `.0` de un campo
numerico y los ceros a la izquierda de un codigo todo-digitos) con la misma regla
que `core.excel_io.normalize_sku` aplica al archivo. Comparar las formas crudas
hacia que `0005438957` (Excel) y `5438957` (BigQuery) no se encontraran nunca: la
consulta devolvia cero filas y todos los pedidos terminaban en
`SIN OPCION DE REASIGNACION` aunque hubiera stock.

Las filas repetidas de un mismo par (SKU, tienda) dentro del corte vigente se
**suman**. Antes se conservaba solo la ultima, lo que descartaba unidades reales
cuando la fuente abria el stock por talla o por almacen.
