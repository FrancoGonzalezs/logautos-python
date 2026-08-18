# Logautos — migración a Python

Reescritura en Python del sistema Logautos (CodeIgniter + MariaDB), siguiendo
el mismo patrón que el proyecto de Talca: Flask + réplica local en SQLite.

Estado actual: **módulo Unidades en solo lectura sobre la réplica local.**
Todavía no hay escritura ni sincronización contra `claude.logautos.cl`.

Lo que bloquea el sync: falta confirmar si `claude.logautos.cl` ya expone
endpoints de push (hay que mirarlo por FTP/cPanel del hosting). Si no existen,
se calcan de `php_endpoints_push.php` del proyecto de Talca, que documenta el
contrato completo: rutas explícitas en `routes.php`, autenticación por API key,
body JSON leído de `raw_input_stream` (CodeIgniter 3 no lo parsea solo), y una
tabla `api_idempotency` compartida entre entidades con una columna `entidad`.

## Variables de entorno

Todas tienen default para que correr local no necesite configurar nada.

| Variable | Default local | En el contenedor |
|---|---|---|
| `DB_PATH` | `./local.db` | ruta dentro del volumen, ej. `/data/local.db` |
| `DATA_DIR` | `./data` | el volumen, ej. `/data` — ahí va la caché de UF |
| `SECRET_KEY` | clave fija de desarrollo | **obligatoria**, si no las cookies de sesión son falsificables |

`DATA_DIR` importa: fuera del volumen el disco del contenedor se borra en cada
redeploy, así que la caché de UF se perdería y cada arranque volvería a pegarle
a mindicador.cl.

La clave fija de desarrollo está en el repo a propósito, para que reiniciar el
servidor no cierre la sesión mientras se trabaja. Hoy no agrega riesgo porque
el login es una maqueta que acepta cualquier usuario y la app es de solo
lectura; **cuando el login valide de verdad, esa rama debería fallar en vez de
dar una clave por defecto.**

## Levantar el ambiente

```bash
pip install -r requirements.txt
python scripts/importar_dump.py    # ~2 min, genera local.db (~200 MB)
python app.py                      # http://localhost:5000
```

`local.db` no está versionado: se regenera siempre desde el dump.

## Scripts

| Script | Para qué |
|---|---|
| `scripts/importar_dump.py` | Traduce el dump de MariaDB a SQLite. `--tablas a,b` para importar otras tablas, `--solo-indices` para recrear los índices de trabajo. |
| `scripts/verificar_replica.py` | Conteos, unicidad del VIN y qué pasaría si las relaciones del análisis fueran FK reales. |

## Lo que se comprobó sobre el dato real

El dump tiene **121 tablas**. Se importaron las cinco de Prioridad 1:

| Tabla | Filas | Columnas |
|---|---:|---:|
| `newstocks_cidef` | 71.546 | 144 |
| `orden_trabajo` | 121.592 | 65 |
| `reparaciones_externas` | 268.022 | 34 |
| `contenedor` | 2.448 | 24 |
| `ot_contenedor` | 503 | 4 |

Y las cuatro que alimentan la ficha de unidad:

| Tabla | Filas | Columnas |
|---|---:|---:|
| `registros` | 299.322 | 14 |
| `check_list` | 20.013 | 33 |
| `inspeccion_despacho` | 16.365 | 29 |
| `check_list_mecanica` | 2.956 | 93 |
| `entradas_salidas` | 146.353 | 23 |
| `ingresos_roro` | 81 | 8 |
| `promedio_pdi` | 4 | 9 |
| `retornos` | 2 | 7 |
| `incidentes` | 0 | 8 |

Cinco hallazgos que corrigen el análisis de migración:

1. **`newstocks_cidef` no es "una fila por vehículo".** 71.546 filas para 61.447
   VIN distintos; 6.182 VIN se repiten. El caso extremo (`9V8FF4HY1PA800266`)
   tiene 14 filas: mismo VIN, misma patente, mismo modelo, cliente CARFLEX, con
   despachos escalonados entre 2023 y 2026. Cada fila es **una pasada por el
   patio**, no el vehículo. La separación `unidad` + `evento` que el análisis
   proponía como mejora opcional es en realidad la estructura que el dato ya
   tiene, sin declarar.

2. **El VIN funciona como llave, el `id` de OT menos.** `reparaciones_externas.vin`
   contra `newstocks_cidef.vin` deja solo 0,2% de huérfanas; `orden_trabajo.id_vehiculo`
   contra `newstocks_cidef.id` deja 6,6%. Aun así, **el identificador de unidad
   para el sync es `id`, no VIN** (decisión cerrada): el VIN se repite y pisaría
   pasadas distintas del mismo vehículo. Verificado que `local.db` preserva el
   `id` de origen exacto, comprobado id por id contra el dump.

3. **`ot_relacionada = 0` es el centinela de "sin OT".** El 26% de huérfanas
   aparente son 68.620 reparaciones con `ot_relacionada = 0` (repuestos y
   trabajos sueltos que solo cuelgan del VIN). Las referencias realmente rotas
   son ~1.072, o sea 0,4%.

4. **`ot_contenedor` está desconectada.** Sus 503 números de contenedor no
   intersectan con los 96 de `contenedor`, y `n_guia` no corresponde a
   `orden_trabajo.id` (99,6% sin match). `contenedor` tiene datos desde
   2025-01-21 hasta hoy; `ot_contenedor` parece una isla heredada.

5. **Las tablas del origen casi no tienen índices.** De las cinco, solo
   `newstocks_cidef` trae uno, y ninguna indexa el VIN. Los índices `ix_*` que
   crea el importador son de la réplica local, no copias del origen.

## Catálogo de `requerimiento` (§6b del análisis)

Los 21 conteos del §6b se contrastaron uno por uno contra la réplica: **todos
correctos**, incluidas las 49 OT con el campo vacío. Es la sección más confiable
del análisis. Dos precisiones:

- Los valores crudos distintos son **66**, no ~60. `modulos/catalogos.py` los
  reduce a **56 canónicos**, derivándolos de la tabla en vez de hardcodearlos
  (que es justamente el defecto del dashboard PHP, con su lista fija de 16).
- **`TNR` y `requerimiento = 'TRABAJO NO REALIZADO'` no son cosas distintas.**
  `TNR = 'TNR'` es una marca sobre 6.337 OT que conservan su requerimiento
  original (PRESUPUESTO 4.031, DYP 369, PICKING 266…). En 340 el requerimiento
  aparece *sobrescrito* con el literal `TRABAJO NO REALIZADO`.

  **Ese sobrescrito es deliberado, no un bug** (decisión del dueño del
  sistema): el legado pisa `requerimiento` para que el personal no pase por
  alto el estado TNR. La decisión para Python es conservar el comportamiento
  visible sin perder el dato:

  - guardar `requerimiento` original **intacto** y el flag `TNR` por separado;
  - generar la etiqueta "TRABAJO NO REALIZADO" **calculada en la UI** cuando
    `TNR` esté activo;
  - **nunca** sobrescribir la columna.

  Así el operador sigue viendo lo mismo que hoy y el sistema no pierde cuál era
  el trabajo, que es lo que hoy se destruye en esas 340 filas.

Las ocho normalizaciones que aplica el catálogo, todas verificadas en pantalla:

| Canónico | Total | Absorbe |
|---|---:|---|
| `DYP` | 15.406 | `' DYP'` (espacio inicial) |
| `SERVICIO MECANICO` | 8.324 | espacio final (16), `\r\n` literal (1) |
| `RECEPCION` | 7.805 | `RECEPCIÓN` (700) |
| `SALIDA DE MERCANCIA` | 7.167 | `SALIDA DE MERCARNCIA` (398) |
| `PICKING` | 3.205 | `PICKING\|` (53) |
| `PATENTES` | 797 | `PATENTE` (345) |
| `INSPECCION MECANICA` | 775 | `INSPECCIÓN MECÁNICA` (222) |
| `INSPECCION MECANICA DE INGRESO` | 102 | variante con tildes (95) |

La normalización está partida en dos capas a propósito: lo mecánico (tildes,
espacios, saltos, tipeos) va en `normalizar()`, y lo que requiere criterio de
negocio va en `FUSIONES_CRITERIO` — hoy solo `PATENTE` → `PATENTES`, para que
el dueño pueda revisarlo y discutirlo.

**No se fusionan**, y conviene que siga así: `COMBUSTIBLE` (3.960) con
`COMBUSTIBLE POR NORMA` (8.155), porque son cobros distintos; y las seis
variantes de `CARGA DE COMBUSTIBLE ADICIONAL A LA NORMA N LITROS`, porque
juntarlas perdería los litros. Estas últimas se agrupan como familia solo para
lectura.

## Qué se muestra de las 144 columnas

La función `stocklogautos()` del PHP usa **80** de las 144 columnas. Se reparten
en el orden en que aparecen en esa función:

- **40 en el listado** — desde `id` hasta `fecha_segundo_lavado`. Las 40 existen
  en la tabla y las 40 traen datos.
- **40 en la ficha** — desde `fecha_laminado` hasta `enviado_cidef_revision`.

Las 64 columnas restantes de la tabla no se muestran: no están activas en el
sistema. Entre ellas `estadostock`, que se sacó por irrelevante.

Cuatro de los 40 campos de la ficha no son columnas de `newstocks_cidef`.
**Los cuatro están resueltos**, así que la ficha no tiene campos pendientes:

| Campo | Cómo se resuelve |
|---|---|
| `fecha_inspeccion` | consulta a `inspeccion_despacho` |
| `fecha_revision_contenedor` | consulta a `contenedor`, cruzando por `vines` |
| `cant_danos_dyp` | cuenta sobre `observaciones`, separador `' \|'`, sin trozos vacíos |
| `cant_danos_aprob_cliente` | cuenta sobre `ob_dyp2`, separador `' /'`, menos uno |

Los dos contadores no necesitan otra tabla: el sistema viejo apila los daños en
una columna de texto. Dos detalles de implementación que importan:

- **Campo vacío da 0 en los dos.** El PHP daba 1 para `cant_danos_dyp` vacío,
  inconsistente con el otro contador; se decidió no replicar esa
  inconsistencia. Se considera vacío también el texto que trae **solo
  espacios**: hay 15 filas así, y como `' '` es truthy la fórmula literal les
  daría 1 daño donde no hay ninguno — la misma inconsistencia entrando por otra
  puerta. Son exactamente 15 filas de 71.546 las que difieren de la fórmula
  literal; en `cant_danos_aprob_cliente` no difiere ninguna.
- **Se decide "vacío" con `strip()` pero se parte siempre el valor original.**
  Las observaciones reales empiezan con espacios (`'    | <br>SIMUNIZADO... |'`)
  y ese primer `' |'` es un separador legítimo: stripear antes de partir se
  comería un daño de cada unidad.

El separador de `ob_dyp2` es `' /'` **con espacio**, lo que evita partir textos
como `PINTAR COST TRAS IZQ (1/2)` por el `/` interno.

**Las dos fechas usan `ORDER BY ... LIMIT 1` explícito**, que es un arreglo
deliberado sobre el comportamiento del PHP original y no una copia: 2.030 VIN
tienen más de una inspección de despacho, así que sin orden explícito cuál de
ellas gana depende de cómo el motor recorra la tabla. Se toma la más reciente.

- `fecha_inspeccion` usa `fecha_completa`, poblada en las 16.365 filas, y no
  `fecha_despacho`, que solo lo está en 4.171.
- `fecha_revision_contenedor` cruza por texto (`contenedor.vines LIKE '%vin%'`)
  porque ese campo guarda todos los VIN del contenedor juntos. Resuelve para
  ~39% de las unidades recientes: los contenedores del dump arrancan el
  2025-01-21, así que una unidad anterior no tiene con qué cruzarse.

### `cant_danos_dyp` cuenta daños, no trozos — y por eso difiere del PHP

Se descartan los trozos vacíos que deja el separador. Según la época del
registro el formato deja uno o dos: los viejos terminan en `' |'` y los nuevos
además empiezan con `'    | '`, así que contar pedazos a secas devolvía de más,
y cuánto de más dependía de cuándo se había guardado la fila.

| Unidad | Contando trozos | Contando daños |
|---|---:|---:|
| id 92090 | 6 | **4** |
| id 1979 | 7 | **6** |
| id 92049 | 11 | **9** |
| id 61013 | 9 | **8** |
| id 66028 | 1 | **0** |

**Esto hace que Python muestre un número distinto al de Logautos.PHP para la
misma unidad** mientras convivan los dos sistemas. Es esperable y está decidido:
el correcto es el de Python.

Ojo al comparar con `cantidad_danos_aprobados`: ese resta uno en vez de filtrar,
y no es un descuido. Ahí el separador `' /'` solo deja vacío al final, y filtrar
cambiaría el resultado de los valores sin separador (`DAÑO NO AUTORIZADO POR
CLIENTE` debe dar 0, no 1).

Un trozo cuenta como daño solo si le queda **al menos una letra o número**
después de sacarle las etiquetas HTML (`_trozo_sin_contenido`). No alcanza con
preguntar si queda algo: eso dejaba pasar los símbolos sueltos.

Los tres casos que descarta, en el orden en que fueron apareciendo:

| Caso | Ejemplo | Filas que corregía |
|---|---|---:|
| pedazo vacío del separador | `'    \|    \|'` | 11.952 |
| resto del WYSIWYG | `'…\|</p>'` | 6.913 |
| símbolos sin palabra | `'\|'` huérfano, `'()'` | 3.684 |

El total de daños contados baja de **177.512 a 153.067**.

Verificado que el `[a-zA-Z0-9]` ASCII no descarta nada legítimo: **cero filas**
difieren respecto a aceptar también letras acentuadas, así que
`'DAÑO EN PUERTA'` cuenta y ningún trozo real está compuesto solo de tildes.

## Las secciones de la ficha, según el cliente

- **CIDEF** — check list de ingreso (fotos en `link`) + inspección de despacho +
  movimientos. **Sin sección de check mecánico**: `check_list_mecanica` no tiene
  ni una fila CIDEF y nunca la tuvo (2.951 CARFLEX, 3 PIAMONTE, 1 PRUEBA, 1
  PARTICULAR). No es un caso vacío que haya que manejar, así que la sección
  directamente no se dibuja.
- **CARFLEX** — lo mismo, más el check mecánico.
- **Todos** — inspección de despacho sin filtro de cliente (fotos en
  `archivo1..archivo9`) e historial de movimientos desde `registros`, por VIN.

El filtro por cliente compara el valor **normalizado**, no con `=`: la columna
trae 6 filas guardadas como `'CIDEF '` con espacio final y 9 como
`'POMPEYO CARRASCO '`. Con igualdad exacta esas filas desaparecerían de la ficha
sin que nadie lo notara.

### Los campos de fotos tienen tres formatos conviviendo

El sistema viejo cambió el formato de los campos de foto al menos dos veces sin
migrar lo anterior, así que en `check_list` conviven:

| Formato | Dónde | Filas |
|---|---|---:|
| separador `' \| '` | actual | mayoría |
| separador `<br>` con URLs numeradas (`1- https://…`) | viejo | 2.087 |
| separador `<br />` con URLs numeradas | viejo | 1.343 |
| una sola URL sin separador | siempre | 12.083 (`link_guia` entero) |

A eso se suman dos envoltorios: la URL dentro de un ancla completa
(`1- <a href="URL">URL</a>`) y el campo entero dentro de `<p>…</p>` de un
WYSIWYG (31 filas de `check_list_mecanica.link_unidades`).

`urls_de()` en [modulos/unidades.py](modulos/unidades.py) los maneja todos.
Tres decisiones de implementación:

- **Se detecta `<br` en general, no `'<br />'` y `'<br/>'`.** Las variantes sin
  barra son 2.087 filas — el 61% de las afectadas —, así que buscar solo la
  forma con barra dejaría fuera a la mayoría.
- **Se parte primero por `<br>` y después cada pedazo por `' \| '`**, en vez de
  elegir uno u otro: hay una fila que usa los dos a la vez.
- **De cada pedazo se extrae la primera URL con regex**, no "lo que está antes
  del primer `http`". Con el formato de ancla, cortar por el `http` dejaría
  `URL">URL</a>` — la del atributo pegada a la del texto.

Efecto medido sobre las 20.013 filas de `check_list`: **20.625 fotos
recuperadas** y las **25.566 cadenas que no eran una URL bajaron a cero** (el
parser anterior las mandaba igual como `src` de un `<img>`). Los `archivoN` de
`inspeccion_despacho`, que son rutas relativas y no URLs, siguen resolviéndose
contra `BASE_FOTOS`.

### Daños del check list: tres campos paralelos

`observacion` (la pieza), `requerimiento` (el tipo de daño) y `gravedad` se
leen **por el mismo índice**, junto con las fotos de `link`. Es la lógica de
`views/nota/showcli.php`.

Que sean campos paralelos y no una tabla de detalle es frágil por definición:
alcanza con que alguien escriba un guion dentro del nombre de una pieza para
que las tres listas se desalineen. Se replica igual porque así está cargado el
dato de tres años.

Dos diferencias con el PHP, las dos por casos que en el dato son masivos:

- **Menos fotos que daños.** El original tira un notice y sigue con la foto
  vacía. Acá ese daño se muestra marcado "sin foto". Pasa en **3.760 de las
  18.060 filas con daños — el 21%**.
- **Dos formatos de separador.** El actual usa `'-'`; el viejo usa
  `' | <br>'` con cada ítem numerado, y son **2.909 filas (16%)**, todas de
  2023 y 2024. El PHP parte siempre por `'-'`, así que en esas filas mezcla
  pedazos de un daño con el número del siguiente y muestra cosas como
  `PORTALON | <br>8 8 MEDIO`. Acá se detecta el separador antes de partir —
  lo mismo que ya se hacía con `link`— y en las filas del formato nuevo el
  resultado es idéntico.

La grilla de daños va sobre un panel teñido para que las tarjetas blancas
resalten: **`#7DA5B5`** en el tema claro y `#2c4652` en el oscuro, porque ese
celeste sobre `#0d1117` sería un parche que encandila.

El lightbox agrupa por **`data-galeria` en el contenedor**, no por la
estructura del DOM. Al separar los daños en una tarjeta por daño, cada
miniatura quedó sola en su contenedor y el visor abría una galería de una foto
sin poder avanzar; con el atributo, las N fotos de la sección vuelven a ser un
solo recorrido.

`observaciones` de `newstocks_cidef` se lista con el **mismo parser que cuenta
`cant_danos_dyp`**: el contador es literalmente `len()` de esa lista, así que
el número de la ficha y la cantidad de ítems no pueden discrepar.

### `registros` — el historial de movimientos

La tabla guarda pares actual/anterior (`accion`/`newcalle`,
`estado`/`newestado`, `patio`/`newpatio`), pero la ficha **solo muestra los
actuales**: en una lista ordenada por fecha, el "anterior" de un movimiento es
el "actual" del que sigue más abajo, así que mostrar las dos mitades es decir
dos veces lo mismo.

`accion` es la posición: una calle física como `A` o `G`, o una zona lógica
como `STOCK`, `ZD`, `ZR` o `DESPACHADO`.

Tres cosas verificadas sobre el dato:

- **`fila` y `newfila` están vacías en las 299.322 filas**, no solo en una
  muestra: cero valores. No se leen.
- **Los movimientos "legacy" no son todo 2022, son julio de 2022** (1.245 de
  1.347 filas sin estado) y parte de agosto; desde septiembre el dato viene
  completo. Por eso se detectan **por fila** —sin estado ni patio— y no por año.
- **275.882 de los 299.322 movimientos (92%) cruzan con una unidad**; el resto
  son VIN que no están en `newstocks_cidef`.

`created_by` es un id numérico de `tbl_users`, tabla todavía no importada: se
muestra el id crudo.

## Dashboard de Facturación

`/facturacion` — acopio por cliente + OT cerradas del mes + total a facturar.
Equivale a `dash_acopio.php`. Con selector de mes.

Dos diferencias de fondo con el PHP, decididas a propósito:

- **El acopio se calcula en vivo, no se persiste.** El PHP hace un `UPDATE`
  sobre `newstocks_cidef` para dejar los días y el valor guardados en la fila
  de la unidad. Era un workaround del sistema legado, no una necesidad: acá la
  pantalla es lectura pura y el número se recalcula en cada carga, así que no
  puede quedar desactualizado ni corromper la tabla si algo falla a la mitad.
- **La UF sale de `mindicador.cl`, no de la columna `uf_mes`.** No es la UF de
  hoy sino la **última publicada del mes que se factura** — mismo criterio que
  `cambiar_uf.php`: se busca desde el último día del mes hacia atrás hasta dar
  con un día publicado. Se factura el mes completo con ese valor.

  Se cachea por (año, mes) en `data/uf.json`, no por día: una vez publicado, el
  valor de un mes ya no cambia. Consultar un mes viejo no vuelve a pegarle a la
  API nunca más.

  La UF se publica por adelantado para el período del 10 de un mes al 9 del
  siguiente, así que un mes publicado responde al primer intento (día 31). Un
  mes futuro no responde ningún día: la búsqueda se corta a los 10 intentos en
  vez de gastar 30 requests, y la pantalla muestra el motivo en vez de un 500.

### Solo clientes que facturan — listas blancas, sin fallback

- **Acopio: 6 clientes.** CIDEF ($660 fijo, no depende de la UF), CARFLEX
  (uf × 0,022), PIAMONTE (0,026), POMPEYO CARRASCO (0,0219), POMPEYO CARRASCO
  USADOS (0,0215) y CLIENTE PARTICULAR, que **no tiene tarifa propia**: cae en
  el fallback "MAS" del PHP (uf × 0,017, redondeado a peso).
- **OT: 5 clientes.** CIDEF, CARFLEX, POMPEYO CARRASCO (absorbe
  `POMPEYO CARRASCO FLOTA`), PIAMONTE y CLIENTE PARTICULAR.
- La fila TOTALES es la **suma de esos clientes**, no una consulta con
  `nombre <> 'LOGAUTOS'`. En agosto las dos formas dan lo mismo —474 OT por
  $18.463.504, verificado— porque no hay ningún otro nombre en el mes; la
  diferencia aparecería el mes que entre un nombre suelto de los muchos que
  tiene la tabla.

`CLIENTE PARTICULAR` es un cliente real y no debe confundirse con los otros
nombres parecidos que siguen fuera: `PARTICULAR`, `PARTICULAR - JUAN CRUZ`,
`PARTICULAR - LOGAUTOS` y una decena más con nombre y apellido.

El alias de FLOTA vive solo en las OT: en acopio, `POMPEYO CARRASCO FLOTA` no
factura, así que la asimetría es deliberada.

Sin lista blanca, 98 unidades de clientes que no facturan acopio entraban al
total, y como muchas están estacionadas hace años sin fecha de despacho
acumulaban días sin techo — ECARS aportaba $21 millones con 23 unidades
ingresadas antes de 2026. El total de agosto pasó de $164.903.490 a
$105.384.602.

Lo excluido no desaparece en silencio: al pie de cada tabla se lista qué
clientes quedaron fuera y cuánto sumaban.

### LOGAUTOS va en su propia tabla

Es la empresa dueña del sistema, no un cliente. Sus OT son costo interno
(merma): nunca llevan precio, y por eso el margen da siempre negativo. La
tabla lleva una nota que lo dice, para que no se lea como un error de datos.
En agosto son 16 OT por $0.

### Los nombres de cliente se comparan normalizados

Nunca con `=`. Cuatro lugares donde importa, y uno cuesta plata:

| Campo | Suciedad | Consecuencia de comparar exacto |
|---|---|---|
| `orden_trabajo.nombre` | `'LOGAUTOS '` (25 OT) | entrarían a los totales de los que debe estar excluido |
| `clientecompleto` | `'POMPEYO CARRASCO '` (77) | caería en la tarifa de fallback en vez de la suya |
| `clientecompleto` | `'Piamonte'` (2), `'CARFLEX '` (2) | ídem |
| `despachado` | `'navegando'` (4) | entrarían al acopio unidades que están navegando |

También se incluyen las unidades con `despachado` NULL. La condición
`despachado <> 'Navegando'` del PHP las descarta en silencio, porque en SQL
`NULL <> 'x'` no es verdadero — pero una unidad sin estado no está navegando.
Son 2 unidades; el conteo pasa de 1.328 a 1.329.

### `MONTH()` sin año

El PHP cuenta las ingresadas/despachadas del mes con `MONTH(fecha) = mes
actual`, que en agosto suma también los agostos de 2023, 2024 y 2025. Acá se
compara año **y** mes. Diferencia hoy: 151 contra 140 unidades ingresadas.

### Los días de acopio NO son una resta de fechas

`calcular_dias_acopio()` replica la aritmética de día-del-mes del PHP
(`date('d', ...)`), que no es lo mismo que restar fechas. Una unidad que entró
el 20 de julio y sigue en patio el 13 de agosto lleva 24 días corridos, pero
para el sistema son **13** — el día del mes de la fecha de corte.

La diferencia no es de detalle: con la resta real el acopio de agosto daba
$87.868.598, y con la fórmula correcta da **$10.298.891**.

En criollo: si la unidad ingresó en un mes anterior se le cobran los días
corridos del mes en curso; si ingresó dentro del mes, la diferencia de días del
mes más uno. El descuento de 7 días de CIDEF por unidad de puerto se aplica
solo en las ramas donde el PHP lo aplica, que no son todas.

### Contraste contra producción (agosto 2026, corte al día 13)

| Cliente | Unidades PHP / Python | Días PHP / Python | |
|---|---|---|---|
| CARFLEX | 450 / 450 | 4.997 / 4.997 | ✓ |
| PIAMONTE | 100 / 100 | 986 / 986 | ✓ |
| POMPEYO CARRASCO | 4 / 4 | 52 / 52 | ✓ |
| POMPEYO CARRASCO USADOS | 1 / 1 | 13 / 13 | ✓ |
| CIDEF | 674 / 675 | 7.074 / 7.087 | drift |

**Cuatro de los cinco calzan exacto en unidades y días.** Los dos filtros que
faltaban:

- **Solo NULL cuenta como "sin despachar".** Una fecha cero de MySQL
  (`'0000-00-00'`) es una fecha inválida, no la ausencia de fecha: tiene que
  fallar la comparación contra el inicio del mes. Meterla junto a NULL le
  cobraba 13 días a la unidad **id 81373** (VIN LJD0AA29AP0184812), que estaba
  `DESPACHADO`. El importador ya preservaba el `'0000-00-00'` correctamente —
  el error estaba en el `OR fecha_desp = '0000-00-00'` de esta consulta.
- **`despachado` NULL se descarta**, replicando el `<> 'Navegando'` del PHP.
  Está marcado en el código para revisarlo cuando se apague Logautos.PHP:
  descartarlas es un efecto colateral de la lógica de tres valores de SQL, no
  una regla de negocio. Afecta a la unidad **id 91246** (VIN DYLD55, PIAMONTE).

La comparación con `'Navegando'` sigue siendo insensible a mayúsculas, y eso
**no** es una desviación: MySQL usa una collation `_ci`, así que allá
`'navegando'` en minúscula también queda descartada.

### CIDEF: la diferencia es drift de datos, no un bug

Se comparó **id por id** la lista de las 675 unidades que produce este módulo
contra la que da el `WHERE` del PHP corrido sobre el dump de origen: **las dos
listas son idénticas**, cero diferencias en ambos sentidos. La unidad de más
respecto de producción (674) es movimiento de datos entre la toma del dump y el
momento en que se consultó el sistema en vivo. No hay nada que corregir.

### Producción redondea la tarifa diaria a peso entero

Con la UF de cierre de agosto (**40.873,77**), las tarifas calculadas redondeadas
coinciden **exactamente** con las que se deducen de las cifras de producción:

| Cliente | uf × factor | Redondeada | Implícita en producción |
|---|---:|---:|---:|
| CIDEF | 660,00 (fija) | 660 | 660,00 ✓ |
| CARFLEX | 899,22 | 899 | 899,00 ✓ |
| POMPEYO CARRASCO | 895,14 | 895 | 895,00 ✓ |
| POMPEYO CARRASCO USADOS | 878,79 | 879 | 879,00 ✓ |
| PIAMONTE | 1.062,72 | 1.063 | 1.058,15 ✗ |

Y con la tarifa redondeada, **cuatro de los cinco totales dan al peso exacto**
contra producción: CIDEF $4.668.840, CARFLEX $4.492.303, POMPEYO CARRASCO
$46.540 y USADOS $11.427.

**No está implementado para esos cinco**: el módulo usa la tarifa exacta sin
redondear, así que las cifras difieren en centésimas de porcentaje. Redondear
es una línea, pero cambia un número que el personal ve.

La excepción es la tarifa fallback de `CLIENTE PARTICULAR`, que **sí** va
redondeada (uf × 0,017 = $694,85 → **$695**), porque así se especificó al
agregarlo. Queda una inconsistencia a propósito —cinco tarifas sin redondear y
una redondeada— anotada en el código; se empareja sola cuando se decida el
redondeo global.

PIAMONTE es el único que no encaja con ninguna variante: su tarifa implícita de
$1.058,15 equivale a un factor de 0,02589 sobre la UF de agosto, no al 0,026
especificado. Vale la pena confirmar cuál es su tasa real.

## KPIs

`/kpis` — los indicadores de `Kpi.php`, con el mismo selector de mes que
facturación. **Los 21 están construidos.**

### El orden sale del PHP, no se elige

Son **21**, confirmado leyendo el array `$kpis` de
`application/views/kpi/dashboard.php` dentro de `application.zip`:

```
kpi_recepcion, kpi_tiempo_contenedor, kpi_tiempo_roro, kpi_patio,
kpi_tasa_retrabajo, kpi_promedio_pdi, kpi_fpy, kpi_retrabajo_lavado,
kpi_dias_patio, kpi_lead_time_despacho, kpi_lead_time_despacho_sucursal,
kpi_lead_time_despacho_concesionario, kpi_lead_time,
kpi_reclamos_concesionarios, kpi_cumplimiento_preparacion, kpi_reprocesos_dyp,
kpi_efectividad_pdi, kpi_incidencia, kpi_despachos_atrasados,
kpi_tasa_incidentes_operacionales, kpi_retorno_sucursales
```

Esa lista vive en `ORDEN_DASHBOARD`: cada KPI se registra en `CONSTRUIDOS` con
su clave del PHP y **cae solo en su posición**, sin reordenar nada a mano.

### No todos son porcentajes

Ocho de los 21 son **promedios**, no tasas: el PHP los marca con
`tipo_kpi='promedio_dias'` o `'promedio_minutos'` y mete el promedio en el
campo `tasa` para reusar la misma tarjeta. Acá se separan en `promedio` +
`unidad`, así la tarjeta muestra "53,3 días" o "10,8 min/unidad" en vez de un
porcentaje que no significa nada.

### Tablas que hicieron falta

`promedio_pdi` (4 filas), `ingresos_roro` (81) y `entradas_salidas` (146.353).
La última la consulta Despachos Atrasados por (fecha, RUT) para cada despacho
del mes; el PHP hace una consulta por despacho, acá se traen las marcas del
período una vez y se cruzan en memoria.

### Tres columnas que están vacías en el dump

Afectan a KPI que por eso dan siempre 0 o cero registros, y **no son bugs**:

| Columna / tabla | Estado | KPI afectado |
|---|---|---|
| `estado_it` | **NULL en las 71.546 filas** | Incidencia Mecánica siempre 0% |
| `incidentes` | 0 filas | Reclamos de Concesionarios siempre 0% |
| `ingresos_roro` | solo 2026-05-10 a 2026-07-05 | Tiempo RORO vacío fuera de ese rango |
| `promedio_pdi` | 4 filas, todas del 2026-07-23 | Promedio Diario de PDI vacío fuera de julio |

`observacion_it` también está NULL en las 71.546 filas. Como Incidencia Mecánica
alimenta a Tasa de Incidentes Operacionales, ese KPI compuesto hoy suma solo dos
de sus tres componentes.

### Extras fuera del dashboard

`ingreso_taller` y `scanners` existen como funciones en `Kpi.php` pero **no
figuran en `$kpis`**, así que el sistema viejo nunca las muestra. Van en una
sección aparte, claramente separadas de las 21.

Cada KPI es **una función propia** registrada en `KPIS`, no un motor genérico:
en el PHP cada indicador tiene su consulta y sus rarezas, y ya se vio con
acopio y OT que generalizar antes de tiempo esconde justamente las diferencias
que hay que verificar una por una.

A diferencia de facturación, el corte es siempre **el último día del mes** y no
la fecha de hoy: un KPI se mide sobre el período completo.

## Listado de Unidades

**Búsqueda en vivo** con debounce de 300ms. Como la app renderiza en el
servidor, "en vivo" es enviar el formulario tras la pausa; sin debounce saldría
una consulta por tecla contra 71.546 filas. El botón queda como fallback y es
lo único que funciona sin JS.

Al recargar hay que devolver el foco al campo **y poner el cursor al final**:
con `autofocus` solo, el cursor queda al principio y la siguiente letra se
escribe al revés. Sin eso la búsqueda en vivo es inusable.

**Barra de scroll horizontal duplicada arriba.** Con 40 columnas la tabla mide
5.224px contra ~660 visibles, y la única barra del navegador queda al pie: para
correrse a la derecha había que bajar hasta la última fila. Un div espejo se
estira al ancho real de la tabla y se sincroniza con ella en los dos sentidos.
Si la tabla entra entera, la barra de arriba se esconde sola.

La paginación también está arriba y abajo, con campo de "ir a página" que
arrastra los filtros activos en campos ocultos.

### El VIN se filtra por formato, no por lista

`^[A-Z0-9]{15,19}$` sobre el VIN en mayúsculas y sin separadores. En el stock
CIDEF de agosto los dos únicos rechazados son `'GCPS15'` y `'PT5731'` —
patentes escritas en el campo del VIN—, y los 510 aceptados miden **todos
exactamente 17 caracteres**. El filtro por formato evita mantener una lista de
exclusiones a mano.

El cruce con `orden_trabajo` va por la columna **`vehiculo`**, que es donde
vive el VIN: esa tabla no tiene columna `vin`.

### Unos cuentan VIN únicos y otros cuentan filas

No es un descuido, y conviene verificarlo en cada KPI nuevo:

- **Daños por Recepción** y **Daños en Patio** deduplican por VIN, porque el
  numerador sale de cruzar contra `orden_trabajo` y el VIN es la llave del
  cruce.
- **First Pass Yield** y **Retrabajo de Lavado** cuentan **filas**, porque el
  numerador es una propiedad de la misma fila (`ob_mecanica`,
  `fecha_segundo_lavado`).

La diferencia es real: el lavado de agosto tiene **127 filas para 126 VIN
distintos**, y producción reporta 127. Deduplicar habría dado 126 y no calzaba.

**Tasa de Retrabajo cuenta filas por coherencia, no porque agosto lo pruebe**:
ese mes tiene 18 filas y 18 VIN únicos, así que no distingue. Cualquier mes de
enero a julio sí lo haría — julio, por ejemplo, da 336/508 contando filas y
335/505 contando VIN únicos. Basta una cifra de producción de un mes cerrado
para zanjarlo.

### Contraste contra producción (agosto 2026)

| KPI | Producción | Python | |
|---|---|---|---|
| Daños por Recepción (CIDEF) | 18/61 = 29,51% | 18/61 = 29,51% | ✓ |
| Daños en Patio (STOCK) | 5/509 = 0,98% | 5/510 = 0,98% | tasa ✓, −1 en denominador |
| First Pass Yield (PDI) | 17/18 = 94,44% | 17/18 = 94,44% | ✓ |
| Retrabajo de Lavado | 19/127 = 14,96% | 19/127 = 14,96% | ✓ |
| Tasa de Retrabajo | 0/18 = 0,00% | 0/18 = 0,00% | ✓ |
| Reprocesos de DyP | 18/46 = 39,13% | 18/46 = 39,13% | ✓ |
| Reclamos de Concesionarios por PDI | 0% (esperado) | 0/162 = 0,00% | ✓ |
| Retorno Sucursales | 0% (esperado) | 0/162 = 0,00% | ✓ |

Extras (sin cifra de producción con que contrastar): **Ingreso a Taller = 121
unidades** (122 filas), **Scanners = 0/61 = 0,00%**.

### Los 13 restantes — resultados de agosto y julio 2026

Sin cifras de producción con que contrastar; se verificó que cada uno dé
números coherentes en un mes en curso y en uno cerrado.

| KPI | Agosto | Julio |
|---|---|---|
| Tiempo Inspección (Contenedor) | 3,0 min/u sobre 38 | 10,8 min/u sobre 289 |
| Tiempo Inspección (RORO) | sin datos | 20,1 min sobre 36 |
| Promedio Diario de PDI | sin datos | 44,8 min sobre 4 |
| Días Promedio en Patio | 81,3 días sobre 673 | 53,3 días sobre 1.152 |
| Lead Time Despacho | 2,8 días sobre 103 | 7,7 días sobre 515 |
| Lead Time — Sucursal | 1,2 días sobre 59 | 3,0 días sobre 251 |
| Lead Time — Concesionario | 5,1 días sobre 44 | 12,2 días sobre 264 |
| Lead Time Total (ZD → Despacho) | 3,2 días sobre 151 | 3,4 días sobre 454 |
| Cumplimiento de Preparación | 87,41% (125/143) | 96,79% (513/530) |
| Efectividad PDI | 47,37% (18/38) | 99,60% (499/501) |
| Incidencia Mecánica | 0% (0/122) | 0% (0/494) |
| Despachos Atrasados | 67,2 min sobre 163 | 59,6 min sobre 540 |
| Tasa de Incidentes Operacionales | 12,86% (36/280) | 5,19% (39/752) |

Despachos Atrasados es el más intrincado: cruza cada despacho con la marca de
entrada del encargado que vino a buscar la unidad, con hora de corte 15:30 los
viernes y 17:30 el resto, descuento de la colación de 14:00 a 15:00, y búsqueda
del RUT primero exacta y después por los últimos 7 dígitos (en una tabla se
guarda con puntos y guion y en la otra no). En agosto: 107 atrasados de 155
evaluados y 8 sin marca.

### La regla de Scanners está mal como quedó especificada

Buscar el substring `'DTC PRESENTES'` también encuentra `'OK SIN DTC
PRESENTES'`, que significa **lo contrario**: que la unidad no tiene códigos de
falla. En la tabla hay **1.871 filas que dicen SIN contra 228 que dicen CON**,
así que la regla literal contaría 2.099 donde deberían ser 228 — ocho veces de
más.

En agosto no se nota porque ninguna fila del período tiene un valor con DTC
(son `'OK'`, NULL o vacío) y el indicador da 0%. Se implementó tal cual se
especificó por ser un extra fuera del dashboard, pero **si esa regla se reusa
en alguno de los 13 KPI que faltan hay que invertir el criterio**: exigir
`'CON DTC'` y excluir `'SIN DTC'`.

### Los dos KPI de post-despacho dan 0% por falta de datos, no por un bug

Sus tablas fuente están prácticamente vacías: **`incidentes` no tiene ni una
fila** en todo el dump y **`retornos` tiene dos** (mayo y junio de 2026).

Para no dejar el cruce sin probar —un 0% no verifica nada— se corrió
`Retorno Sucursales` contra los meses que sí tienen datos: **mayo da 1/689 =
0,15% y junio 1/643 = 0,16%**, con los dos VIN de `retornos` cruzando
correctamente contra unidades CIDEF despachadas.

Ambos deduplican por VIN, porque el numerador cruza contra otra tabla. Importa:
en agosto son 163 filas para 162 VIN, y hay unidades despachadas dos veces en
el mismo mes — el VIN `LGJE5EE08TM521278` salió el 19 y el 22 de mayo.

`Reprocesos de DyP` es el primero que mira `orden_trabajo` directo: su
denominador son **OT**, no unidades, así que no cruza contra
`newstocks_cidef`. La marca de reproceso vive en la columna **`atraso`** — es
el campo real del sistema viejo, no un error de mapeo.

Cuidado si se usa `atraso` para otra cosa: no es un booleano limpio. Además de
`'SI'` (983 filas) y `'NO'` (42), tiene valores numéricos sueltos (`'12'`,
`'14'`, `'15'`, `'16'`) de algún uso anterior, y está en NULL en 120.551 de las
121.592 OT.

### Tasa de Retrabajo da 0% en agosto porque el mes es joven

Los dos campos del numerador (`fecha_revision_salida` y `fecha_cc`) se llenan
en pasos posteriores del flujo, así que las unidades que hicieron PDI hace
pocos días todavía no llegaron ahí. En agosto las 18 filas tienen los dos
campos en `'0000-00-00'`; los meses cerrados van entre **66% y 97%**.

No es un filtro roto: los campos se usan de verdad — 7.679 filas de CIDEF
tienen `fecha_revision_salida` y 4.975 tienen `fecha_cc`.

First Pass Yield es el único donde **más alto es mejor**; la tarjeta lo marca
para que no se lea al revés.

`fecha_segundo_lavado` dice "no hubo segundo lavado" de tres formas distintas:
NULL, la fecha cero de MySQL y el string literal `'NULL'`. En agosto, 108 de
las 127 filas son la fecha cero.

El numerador de los dos calza exacto, y la tasa mostrada también (5/509 y
5/510 redondean ambos a 0,98%). La unidad de diferencia en el denominador de
Daños en Patio no se explica por calidad de dato: no hay VIN duplicados, todos
los aceptados son de 17 caracteres, y ninguna variante del filtro
(`despachado <> 'Navegando'`, `ingreso <` en vez de `<=`, excluir fecha cero)
mueve el número. Es el mismo patrón de drift que ya se vio con las 675 unidades
de CIDEF en acopio: el dump se tomó el 2026-08-13 13:38 y el set incluye
unidades ingresadas ese mismo día.

## Pantalla de entrada

`/login` — réplica de la pantalla real: card de 430px, gradiente 135°
`#eef5fb → #d9e7f5`, logo + REGLA en `#3c8dbc`, inputs redondeados con ícono,
ojo para mostrar la contraseña y footer de Logística Automotriz.

**No es seguridad**, y está dicho así en `modulos/acceso.py`: acepta cualquier
usuario y solo deja una marca en la sesión. Cuando empiece el trabajo de sync
y escritura hay que reemplazar `_credencial_valida()` por una validación real
contra `tbl_users` (que existe en el dump con 16 columnas, todavía sin
importar) y fijar `SECRET_KEY` por entorno — hoy se genera al arrancar, así
que cada reinicio cierra las sesiones.

El login usa el mismo sistema de diseño que el resto y respeta el tema activo,
con su propio botón para cambiarlo antes de entrar.

## Sistema de diseño

Dos temas, **claro** y **oscuro**, con botón en la barra lateral y preferencia
en `localStorage`. Todo el color sale de variables CSS sobre `html[data-tema]`:
agregar o ajustar un tema es tocar un bloque y ninguna regla más.

| | Claro | Oscuro |
|---|---|---|
| Fondo de página | `#f4f6f9` | `#0d1117` |
| Tarjetas | `#ffffff` | `#161b22` |
| Acento | `#3c8dbc` | `#4a9fd4` |
| Texto | `#12181f` | `#e8edf4` |

Reglas del sistema, escritas al principio del CSS para no volver a mezclar
estilos con el tiempo:

- **Radios**: 12px en tarjetas, 8px en inputs y botones. Nada más.
- **Sin sombras.** Las tarjetas se separan del fondo por un borde de 1px y una
  diferencia leve de fondo. La única sombra de la app es la del visor de
  fotos, que flota sobre todo.
- **Un solo font-family** (`system-ui`) en toda la app.
- **Jerarquía de cifra**: rótulo 11px mayúsculas gris, valor 30px bold con
  cifras tabulares, descripción 13px gris.
- **Color con propósito**: el acento es para navegación activa, links, botones
  primarios y datos. Verde y rojo quedan reservados a plata a favor / en contra
  y a alertas — por eso las tarjetas de totales llevan el borde izquierdo verde
  y las de proyección lo llevan gris.
- **Aire**: 22px de padding dentro de cada tarjeta de cifra.

El atributo `data-tema` se aplica en un script del `<head>` **antes de pintar**:
si se dejara al final, cada navegación mostraría un destello del tema claro
antes de tomar el oscuro.

El logo de Logautos es azul sobre transparente y se pierde en el fondo oscuro,
así que en ese tema va sobre una plaquita blanca. No se invierte, porque eso le
cambiaría el color de marca.

### Barra lateral

Queda fija con **`position: fixed`**, no `sticky`. Sticky depende del contexto
de scroll del ancestro y cualquier `overflow` o altura mal calculada más arriba
lo rompe — pasaba en el navegador real aunque los estilos computados se vieran
correctos. Fixed se posiciona contra el viewport y no depende de nada.

El precio es que la barra sale del flujo: el contenido lleva
`margin-left: var(--ancho-lateral)`. El ancho vive en **una sola variable**
para que la barra y el margen no puedan quedar desalineados.

Bajo 860px la barra vuelve al flujo como cabecera horizontal y el margen del
contenido se devuelve a cero — si no, quedaría un hueco de 224px en móvil,
que es justo donde el ancho escasea.

Secciones numeradas por el orden del flujo operativo, no por id: la unidad
entra (**00**), genera órdenes de trabajo (**01**), eso se factura (**02**) y de
ahí salen los indicadores (**03**), más el catálogo de requerimientos (**04**).
El ítem activo se marca con fondo de acento sutil y borde, no con un bloque
sólido.

### Dos usos distintos de `.grupo`

`<details class="grupo">` es un panel plegable de la ficha y **sí** es una
tarjeta; `<section class="grupo">` es una región con título de un dashboard y
**no** lo es. Si la sección también fuera tarjeta, la tabla de adentro —que sí
lo es— quedaría como una tarjeta dentro de otra, con doble borde.

## Órdenes de trabajo

`/ot` — listado paginado con id, cliente, vehículo, requerimiento, estado,
cierre y precio. Es deliberadamente básico: el motor de OT del sistema viejo
(`Nota.php`, ~22.000 líneas) se migra aparte.

Cada requerimiento del catálogo enlaza al listado filtrado. **El filtro usa
todas las variantes crudas del canónico**, no el canónico a secas: entrar por
`SERVICIO MECANICO` da 8.324 órdenes, el mismo número que muestra el catálogo,
porque incluye `'SERVICIO MECANICO '` con espacio (16) y
`'SERVICIO MECANICO\r\n'` (1). Sin eso, el listado contradiría al catálogo del
que se llegó.

## Estructura

```
app.py                    punto de entrada Flask
core.py                   conexión SQLite (WAL) y helpers; no depende de nada
modulos/acceso.py         pantalla de login (maqueta, sin auth real)
modulos/unidades.py       módulo Unidades (equivale a Pedido.php)
modulos/ot.py             listado de órdenes de trabajo
modulos/catalogos.py      catálogo normalizado de requerimiento
modulos/facturacion.py    dashboard de acopio, OT y facturación total
modulos/kpis.py           indicadores (Kpi.php)
scripts/                  importador y verificador
templates/  static/       pantallas
```

`core.py` no importa de `app.py` ni de `modulos/`, a propósito: en Talca esa
dependencia hacia arriba terminó en un loop de imports al separar el primer
módulo.
