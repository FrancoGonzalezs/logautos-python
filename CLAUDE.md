# REGLA — estado del proyecto

**2026-08-27.** Reescritura en Python (Flask + réplica SQLite) del sistema
Logautos (CodeIgniter 3 + MariaDB, `claude.logautos.cl`). Los dos sistemas
corren **en paralelo**: el legado sigue siendo el que factura y el que usa la
gente. REGLA no lo reemplaza todavía, lo acompaña.

Eso manda sobre casi todas las decisiones raras que siguen. Cuando dos sistemas
escriben sobre el mismo dato, **coincidir vale más que tener razón**: cada
diferencia que aparece en la reconciliación es ruido que alguien tiene que
explicar, y el ruido se lleva puesta la señal.

### Cada entrega dice dónde quedó cada cosa

**Hay DOS despliegues y no se mueven juntos**, y la distinción no se sostiene
sola: hay que decirla en cada entrega.

| | qué | quién despliega |
|---|---|---|
| **Railway** | el Python: pantallas, pull, push, cálculo | push a `main` en GitHub |
| **claude.logautos.cl** | el PHP: `Api_regla.php`, los endpoints | Franco, a mano por cPanel |

Toda entrega dice explícitamente, por cada cosa: **si quedó en el árbol de
trabajo, si está commiteada, o si está desplegada — y en cuál de los dos.**

Escrito porque el 2026-08-27 se perdió una semana: se hablaba de "desplegado"
por los bloques PHP, que sí lo estaban, mientras el Python entero seguía en un
árbol sucio en una sola máquina. Franco probó la pantalla en Railway buscando el
campo de patio y calle y no estaba. Ninguna afirmación fue falsa; la que faltaba
era la que nadie hizo.

El pie de página y `/version` existen para que eso se vea sin preguntar, y el
sufijo `+` del commit avisa cuando lo que corre no es lo que está en git.

### Calibración: qué es "producción" acá

**`claude.logautos.cl` es la copia congelada, no la operación real de la
empresa.** Si el controlador se cae, se bloquea NUESTRO trabajo — el pull, el
push, las pruebas —, no el negocio. Nadie deja de facturar ni de mover autos.

Está escrito porque el 2026-08-27 el despliegue del PHP tiró abajo la clase
entera y se reportó como "incidente en producción". La urgencia era real —el
sync estuvo caído— pero la escala no, y esa clase de error de calibración es lo
que hace tomar decisiones apuradas: revertir sin diagnosticar, saltarse el lint
para "arreglar rápido", tocar dos cosas a la vez.

Lo que SÍ es delicado de verdad, y la diferencia hay que tenerla clara:

- **`orden_trabajo`**, porque de ahí sale la facturación y es append-only: una
  OT creada de más no se borra.
- **Cualquier columna que se BORRE** en vez de agregarse. Hoy una: `ubicacion`
  en el push de PDI.
- **`registros`**, append-only también: un movimiento duplicado queda para
  siempre en el historial del que salen sus reportes.

Un 500 en un endpoint se arregla y se vuelve a intentar. Una fila de más en
`orden_trabajo` hay que ir a explicarla.

---

### La fuente del estado es la FILA

`newstocks_cidef.despachado`, y nada más. **No** se deriva del historial de
REGLA. Cambiado el 2026-08-27, y es lo que ordenó dos semanas de enredo.

El motivo es de dato: `registros` —el historial del legado— tiene agujeros. El
**18,4%** de los cambios de estado no deja fila ahí, porque 58 lugares del PHP
actualizan la unidad sin llamar a `registromov()`. Un historial con agujeros no
puede ser la fuente de un estado; una fila que siempre está, sí. Que REGLA
derivara de SU historial era el mismo error espejado: completo para lo que REGLA
hizo, ciego para todo lo demás.

`movimientos_regla` **no se toca**: sigue siendo el registro de lo que REGLA
hizo y quién lo hizo, y de ahí salen los KPI y el push. Dejó de ser lo que
decide dónde está la unidad.

Al guardar, `registrar()` escribe también la fila — **sólo si el movimiento
viaja**. Si no encola, no se escribe: REGLA no puede afirmar un estado que nunca
va a entregar, y el pull lo revertiría a los 300 s de todos modos.

> **EL SUPUESTO QUE SOSTIENE TODO ESTO: que todo cambio del legado mueve
> `updated_at`.** Si una grilla escribe `despachado` sin tocarlo, el pull no ve
> la fila y REGLA muestra un estado viejo **como si fuera certero** — antes se
> mostraban los dos valores y la discrepancia saltaba sola. `updated_at` está
> poblado en el 85,6% de las filas y hay 111 escrituras a `despachado` en 24
> funciones; **cuántas lo tocan no está medido**. Si resulta que hay caminos que
> no lo hacen, las salidas son un trigger del lado MySQL o una reconciliación
> periódica completa que ignore la marca de agua (`--desde ''`).

## Las cinco reglas que no se negocian

### 0. La clave tiene que separar lo que la pregunta separa

**Antes de contar, de cruzar o de leer un valor, preguntá qué representa UNA
fila —o UNA variable— de la clave que vas a usar. Si representa dos cosas
distintas, vas a razonar sobre una mientras el dato es la otra** — y no se ve,
porque sale un número redondo y con volumen, o una frase que suena obvia.

Apareció **seis veces**, en formas que parecen no tener nada que ver:

| El nombre que se usó | Lo que cubría en realidad | Lo que dio |
|---|---|---|
| `vin` | el vehículo y **cada pasada** por el patio | la PDI de meses atrás contaba como la de este reingreso |
| todo el histórico | el código de hoy y **años de versiones viejas** | `IT` 69,9% / `It` 30% "conviviendo", cuando `It` es la forma vieja |
| `calle` | el estacionamiento y **la etapa de proceso** | `A` → PATIO 2 al 78,8%, "el único caso ambiguo" |
| `$patio` en `actulocproccess` | el patio de la unidad y **lo que el operario eligió en el formulario** | "las unidades con patio sucio no se pueden mover" — y se mueven igual |
| `observacion` / `requerimiento` / `gravedad` en `check_list` | **las piezas, los tipos de daño y los niveles** — ninguna guarda lo que su nombre dice | y al lado existe `observaciones`, en plural, que sí es la observación libre |
| `check_list_mecanica.estado` | el estado de **ahora** y el de **cuando se subió la foto** | la rama de reapertura medía 1 en vez de 68 — "está muerta, no se modela" |

Los tres primeros se arreglan igual: **partir la clave hasta que una fila sea
una cosa sola.** `vin` → `id` de la pasada. El histórico → los últimos 6 meses.
`calle` → el estado destino, con lo que los catorce quedaron entre 93,6% y 100%
y la ambigüedad de `A` desapareció: nunca había existido.

**El cuarto es el mismo error una capa más abajo, y por eso vale anotarlo.** No
es agregar sobre una clave mezclada, es leer una variable creyendo el nombre:
`$patio` se llama como la columna `patio` de la unidad, pero sale de
`$this->input->post('patio')` — del desplegable. Sobre esa lectura se construyó
un diagnóstico entero (147 filas sucias ⇒ unidades que no se pueden mover) que
era falso de punta a punta: la columna sucia sólo se lee como `$newpatio`, el
origen, que se copia a `registros` y no se compara contra nada.

Lo que lo destapó no fue mirar más código: fue **preguntar de dónde sale el
valor**, y después buscar la huella que tendría que haber dejado. Si el
desplegable hubiera ofrecido `PATIO 4 B` alguna vez, esa cadena estaría en
`registros.patio`, porque el `$mov` la copia. No está. Ahí se cayó.

**El quinto es el más peligroso de leer, porque no hay nada raro que notar.**
`check_list.observacion` guarda las piezas dañadas, `requerimiento` los tipos de
daño y `gravedad` los niveles — tres listas paralelas unidas por `-`. Y al lado,
`observaciones` en plural sí es la observación que escribió el encargado.

**Una letra de diferencia entre una columna y la otra.** En una revisión de
código, `observacion` y `observaciones` se leen igual; en un `SELECT` escrito de
memoria, la equivocada devuelve texto plausible y nadie se entera.

> **LA GUARDA:** ninguna consulta nueva nombra esas cuatro columnas sin un
> comentario al lado que diga qué guardan de verdad. Y en el código de REGLA no
> se llaman así: la entidad del push las mapea explícitamente, y quien lea
> `piezas`, `tipos_de_dano` y `niveles` no puede equivocarse. El nombre malo se
> queda del lado del cable, que es donde no se puede cambiar.

Verificado contra el modelo (`getPiezas_CLI` lee `observacion`, `getTipoDano_CLI`
lee `requerimiento`, `getNivelDano_CLI` lee `gravedad`) y contra el dato: fila
20100, seis piezas / seis tipos / seis niveles, alineados por posición.

**El sexto es el único donde la clave está bien y el ERROR es del tiempo**, y
por eso cierra la lista: no se arregla partiendo la columna.

`subida_foto_check_list_mecanico_proces()` elige a qué columnas escribir según
`if ($estado == 'REABIERTO')`. La pregunta era *"¿esta rama se sigue usando o se
puede no modelar?"*, y la respuesta parecía estar en la misma columna que decide:

    SELECT COUNT(*) ... WHERE estado = 'REABIERTO'   -->   1

Uno. Rama muerta, no se modela. **Y está mal por 67.** Son 68, y la prueba es
otra columna:

    SELECT COUNT(*) ... WHERE fallas_adicionales <> ''   -->   68
    de esas 68, hoy:  CERRADO 67   REABIERTO 1

`estado` es el estado **de ahora**. La rama se eligió con el estado **de
entonces**, y después el check list siguió caminando hasta cerrarse. La única
huella de que la rama corrió es **la columna que esa rama escribe**.

Es la misma forma de las otras cinco — una clave que cubre dos cosas — pero el
eje que las mezcla es el tiempo, no el significado. Y engaña más, porque el
número que sale es plausible: un 1 en una columna de estados no tiene nada raro.

> **LA GUARDA:** para medir si una rama de código se sigue usando, no se cuenta
> por la variable que la decide: **se cuenta por el efecto que deja**. La
> variable puede haber cambiado después; el efecto queda escrito.

Aplicado a la inversa el mismo día y con el mismo método: `precios` se descartó
contando `precios <> 0` (16 filas, última hace seis meses) y no `estado`, y
`faltante` se descartó por la vista —no tiene campo— más que por el dato.

Las tres señales de que estás por pisarla:

- el número es alto pero no redondo (78,8%, 69,9%);
- hay un caso que "no cierra" mientras el resto sí — **ese caso no es el borde,
  es el síntoma**;
- una variable te resulta obvia por el nombre y no fuiste a ver de dónde sale.

La regla 1 es el caso de esta regla que además rompe escrituras, y por eso tiene
guardas propias en vez de quedar acá.

### 1. El match es por `id`, jamás por VIN — en las tablas propias de REGLA

`newstocks_cidef` tiene 71.546 filas para 61.447 VIN. **Cada fila es UNA PASADA
del vehículo por el patio, no el vehículo**: 6.182 VIN aparecen más de una vez
(uno catorce veces), el 14% de las filas son reingresos.

Este bug apareció **seis veces**, cinco en una dirección y una en la otra. La
peor: `pdi_de(vin)` alimentaba `_ya_tiene_pdi()`, así que a un vehículo que
reingresaba el sistema le decía que ya tenía PDI cuando esa PDI era de meses
atrás — una PDI que **no se hace** sobre un vehículo que la necesita.

No se ve leyendo el punto de llamada, porque el nombre de la función no delata
la clave: `pdi_de(unidad["vin"])` parece correcto. De ahí las dos defensas:

- **Convención de nombres**: toda función que recibe un VIN lo dice en su
  nombre (`movimientos_por_vin`, `check_lists_por_vin`). Si no lo dice, no debe
  recibir un VIN.
- **La guarda**: `core.instalar_guardas()` pone triggers al arrancar, en
  `crear_app()`, que rechazan filas sin `unidad_id` en las seis tablas propias.
  Al arranque y no perezosamente, porque perezosa significaba que la tabla
  quedaba sin trigger hasta que alguien visitara esa pantalla.

**LA REGLA DE LA FRONTERA — vale para las tablas de REGLA, NO es universal.**
Las del legado usan la clave que el legado usa, y hay que preguntarla cada vez
que una consulta cruza:

| Tabla | Clave correcta |
|---|---|
| `registros`, `check_list`, `inspeccion_despacho`, `contenedor`, `reparaciones_externas` | **VIN** — es lo único que tienen |
| `orden_trabajo` | **VIN**, aunque la columna se llame `id_vehiculo`: el legado la llena con `getidbyvin($vin)` |

Aplicarla al revés es el mismo error con otra cara, y ya pasó: el sensor de
"PDI sin OT" reportó 88 sin cobrar en 2026 y **87 tenían su OT colgada de otra
pasada**. Sobrevivió 1 real.

Ojo con darlo por cerrado: la auditoría dio **0 movimientos contaminados**, pero
ese cero es la edad de REGLA, no una propiedad del sistema. **Vence solo** en
cuanto un VIN que ya pasó por REGLA reingrese.

### 2. La plata se redondea como el legado

`core.peso()` usa `Decimal` + `ROUND_HALF_UP`. **No** `round()` de Python, que
es bancario y rompe el empate para el par. PHP redondea alejándose del cero.
Validado al **100%** contra 972 OT de PDI y 971 de combustible históricas — si
no llega al 100%, hay una regla que no entendimos, no un caso borde.

Las tarifas están en `TARIFAS_ACOPIO`, tabla **con fecha de vigencia**, porque
el precio de la PDI ya cambió una vez. Agregar una tarifa es agregar una FILA,
nunca editar una existente: la fila vieja es la que explica las facturas ya
emitidas. Lo mismo `PRECIOS_PDI` en `ot_pdi.py`.

**El cambio de precio fue a mitad del 2026-06-02**: ese día conviven 27 OT a
46.878 y 3 a 49.000, y `createdDtm` no guarda la hora. La vigencia arranca el
**03**, no el 02 — `reconciliacion.PRECIO_PDI_VIGENTE_DESDE` tiene el 02 y por
eso le atribuye el precio nuevo a 27 OT que se cobraron al viejo.

**Un oráculo puede estar sucio, y hay que notarlo antes de "arreglar" el
código.** Validando la OT de combustible contra `tipo_combu` daba 96,5%, y las
35 que fallaban no eran la fórmula: `FOTON VIEW GRAND` figura 32 veces como
`GASOLINA` y 13 como `DIESEL` —mismo modelo, misma época— y las 45 con precio
de diesel. La columna se contradice a sí misma porque la reescribe otro
proceso. Validado contra lo que el código realmente decide —los litros y el
IVA, que no dependen de esa columna— da **969 de 969**.

Un cliente sin tarifa **se niega y avisa**; no cae a la genérica en silencio.

### 3. Los dos relojes no se concilian

Ni el pull ni el push parsean fechas cruzando sistemas. La marca de agua la pone
el legado con su reloj; `updated_at` lo pone el receptor con el suyo. El desfase
del dump **no es fijo**: es conversión de zona horaria y cambia con el horario
de verano (4 h o 3 h según el mes).

**LAS DOS GUARDAS DE PRUEBA.** Se activan igual — el nombre `probar_*` — y se
apagan con la misma variable — `REGLA_SOLO_LOCAL=0`. Dos guardas que se activan
igual son una regla; dos que se activan distinto son dos reglas que recordar.

| Guarda | Qué frena | Nació de |
|---|---|---|
| `exigir_destino_local` | que una prueba le hable al legado de **producción** | un GET real que salió, 2026-08-27 |
| `exigir_replica_de_prueba` | que una prueba abra la **réplica real** | una prueba de humo que escribió sobre la unidad 92095, 2026-09-02 |

La segunda vive en `conectar_db`, que es el único camino por el que se abre la
base — lo usan `get_db()` de Flask, los scripts y los hilos del push. Puesta
prueba por prueba, la próxima quedaba sin guarda; mismo argumento por el que el
push se enganchó en `registrar()`.

**Lo peor que dejó aquel incidente no fue la fila de más**, y por eso vale
anotarlo: fue `push_pendiente = 1` sobre una unidad real. El UPSERT del pull
**saltea** las filas con ese flag, así que esa unidad deja de recibir
actualizaciones del legado — en silencio, sin error, para siempre. Segunda: la
entrada de cola quedó viva, y si el demonio hubiera corrido, la prueba habría
escrito en el legado de producción sin que la guarda de destino la frenara (el
demonio no corre bajo un `probar_*`).

**Lo que NO cubre**, dicho para que nadie se confíe: un `sqlite3.connect` escrito
a mano la saltea. La prueba de humo que la originó era un `python - <<EOF` suelto
que ni siquiera se llamaba `probar_*`. La guarda protege el camino de la
aplicación, que es por donde entró el daño de verdad — **y está bien que sea
así**: hacerla universal significaría interceptar `sqlite3` entero, que es más
magia de la que vale.

> **LA REGLA DE HÁBITO, para todo lo que la guarda no alcanza:** ningún script
> suelto apunta a la réplica real. Ni a `/data/local.db` ni a la del repo. Si un
> script de exploración necesita datos de verdad, **copia primero**:
>
> ```
> shutil.copy(os.path.join(RAIZ, 'local.db'), os.path.join(tempfile.mkdtemp(), 'x.db'))
> ```
>
> Leer de la réplica real está bien y se hace todo el tiempo (el oráculo del
> catálogo mecánico lo hace). **Lo que no se hace es abrirla para escribir desde
> algo que no es la aplicación.** La guarda cubre la aplicación; esto cubre el
> resto, y es hábito, no código.

**Y un doble escrito desde la misma cabeza que la spec confirma la spec.** El
2026-09-02 el push real encontró dos defectos que el simulado no podía ver —el
`qb_set` protegido y el `updated_at` en una tabla que no lo tiene— porque el
doble implementaba lo que yo *había especificado*, no lo que `Api_regla.php`
*hace*. La spec y el doble salieron de la misma lectura, así que coincidían por
construcción.

El corolario, que ese mismo día costó un informe equivocado: **el doble tampoco
sirve para AVERIGUAR qué hace producción.** Cuando algo falla allá, se lee el
archivo desplegado. Ver la nota del pendiente 5e.

> **LA REGLA:** un script que habla con producción usa **el mismo cliente que
> produce el tráfico real**, no `requests` pelado ni un doble. Si no, prueba
> contra un servidor que no existe. `claude.logautos.cl` corta la conexión con
> el User-Agent de `requests` —cierra el socket, no responde 403— y eso sale
> como `ConnectionError: RemoteDisconnected`, que se lee igual que «no hay
> red». Ya hizo diagnosticar mal una vez. El `USER_AGENT` vive en
> `modulos/sync_legado.py` y **se importa, no se copia**.

**Y el doble puede ser generoso en lo que NO manda.** El 2026-09-02 el legado
simulado devolvía `200` con un `updated_at` inventado en la creación del check
list mecánico, donde el bloque I devuelve `201` sin ninguno. Eso tapó dos bugs
del lado de Python: que `crear()` tenía que aceptar 201, y que el camino de
éxito pisaba el `updated_at` de la unidad con la cadena vacía cuando la
respuesta no traía uno — dejando el siguiente push de esa unidad con
`conocido = ''`. El doble era nuestro, lo habíamos escrito ese mismo día, y aun
así mintió. **Un doble se parece al original también en lo que no devuelve.**

### 4. Un doble más permisivo que el original es un sello de goma

**El legado simulado tiene que RECHAZAR todo lo que rechaza el real.** Si acepta
de más, la prueba no falla cuando el sistema está roto: falla al revés, da verde
sobre algo que en producción no funciona. Y eso es peor que no tener prueba,
porque la prueba se usa para decidir desplegar.

Pasó **tres veces**, y las tres se descubrieron al querer probar algo nuevo, no
al revisar el doble:

| Lo que el doble no sabía | Lo que quedó sin probar |
|---|---|
| sólo devolvía `updated_at` en el 409 | el aviso de conflicto podía salir vacío |
| sólo sabía el PUT, no el pull | que el cambio VUELVA — justo lo que mide la reconciliación |
| aplicaba `fila.update(datos)`, aceptaba todo | que la lista blanca ignora en silencio: 200, cero efecto |

El tercero es el que mejor lo muestra. Con el doble permisivo, empujar una PDI
contra el simulado escribía las catorce columnas y la prueba pasaba — mientras
que contra producción, sin la lista blanca desplegada, no escribía ninguna. La
prueba habría autorizado un despliegue que no hacía nada.

**Las tres condiciones, para cada endpoint que se agregue al doble:**

1. **Rechaza lo que rechaza el real.** Falta de API key, falta de
   `Idempotency-Key` donde es obligatoria, un valor fuera del catálogo, una
   columna fuera de la lista blanca. Si el real dice 400, el doble dice 400.
2. **La prueba demuestra el rechazo, no sólo el camino feliz.** Un `afirmar`
   sobre el 400 y sobre el efecto CERO, no sólo sobre el 201.
3. **Cuando el contrato cambia por etapas, el doble modela las dos.**
   `--lista-blanca desplegada|con_pdi` existe por esto: la prueba corre contra
   las dos y afirma que dan distinto. Si dieran igual, no está mirando nada.

Y la señal de que estás por pisarla: estás por escribir en el doble un
`update(todo)`, un `return 200` incondicional, o un campo que "no hace falta
para esta prueba".

---

---

## Sincronización — cerrado y andando

Ida y vuelta automática entre Railway y `claude.logautos.cl` desde el
2026-08-26.

- **Pull**: `SYNC_INTERVALO_SEGUNDOS=300`, commit **por página** (el WAL
  desbordaba el volumen: 67,70 MB de pico contra 69 MB libres; ahora 4,81).
- **Push**: `PUSH_LEGADO_ACTIVO=1`. Cola + hilo demonio, backoff
  60/300/900/3600/21600/86400. La entrada se escribe **dentro de la misma
  transacción** que el dato local, así que la cola existe aunque el proceso
  muera antes del primer intento.
- **Locking optimista**: `legado_updated_at_conocido` → 409 con
  `datos_actuales`, y correo a `SYNC_CONFLICTOS_DESTINATARIOS` (Resend).
- **`Idempotency-Key` en POST y en PUT.** En el PUT no es redundante: un PUT con
  locking optimista **no es idempotente** — el reintento choca 409 contra su
  propia escritura anterior.

### Entidades vivas

| Entidad | Operación | Ruta | Estado |
|---|---|---|---|
| `it` | actualizar | `PUT /api_regla/unidades/{id}` | producción, 2026-08-26 |
| `movimientos` | crear | `POST /api_regla/movimientos` | **producción, verificado contra el endpoint real**, 2026-08-27 |
| `check_list_mecanica` | crear | `POST /api_regla/check_list_mecanica` | **PHP desplegado 2026-09-02**, sonda 401 OK; sin push real todavía |
| `check_list_mecanica_falla` | actualizar | `PUT /api_regla/check_list_mecanica_falla/{id}` | ídem. **La ruta se corrigió al desplegar**: la spec decía `POST` sin id y habría dado 405 — el cliente manda `PUT /<ruta>/<legado_id>` para todo verbo `actualizar` |
| `check_mecanica_unidad` | actualizar | `PUT /api_regla/unidades/{id}` | ídem, con `fecha_check_list_mecanica` en la lista blanca (bloque H) |
| `check_list` (ingreso) | crear | `POST /api_regla/check_list` | **verificado contra el endpoint real**, 2026-09-04 — filas 20104 y 20105 |
| `check_list_unidad` | actualizar | `PUT /api_regla/unidades/{id}` | ídem: las diez del `$historico` |

**El push real del mecánico está VERIFICADO contra el endpoint real** — 2026-09-04,
unidad 66505 de PRUEBA, fila `check_list_mecanica.id = 2964`.

| Criterio | Resultado |
|---|---|
| A. las 82 columnas, `ignoradas` vacío | ✅ `(ninguna)` |
| B. dos fallas en dos vueltas separadas, tres listas alineadas | ✅ `EXTINTOR VENCIDO \| TAPIZ MANCHADO` / `LEVE \| GRAVE` / dos URLs |
| C. `contador` = 2, sumado del lado del servidor | ✅ `2` |

Los bloques G–L quedan probados. Costó cuatro defectos, y ninguno lo podía ver el
legado simulado:

| | Qué | Cómo se encontró |
|---|---|---|
| `qb_set` | `protected` en CI3 → fatal 500 cuando `$cambios` queda vacío, que es siempre en el paso 2 | el 500 del primer push real |
| `updated_at` | se estampaba en tablas que no lo tienen | `SHOW COLUMNS` (cero filas) + `Unknown column 'updated_at' in 'SET'` |
| `legado_updated_at_conocido` | contaba como columna ignorada y arruinaba el único indicador de silencio | `ignoradas` no venía vacío |
| `columnas_que_acumulan` | quedó la versión vieja: las tres listas se pisaban | tres pedidos midiendo el verbo real de cada columna |

**Lo que quedó en producción:** cinco filas en `check_list_mecanica` (2960–2964)
de la unidad 66505 de PRUEBA, y `SONDA-ACUMULA-1` pegado en su `observaciones`
—columna acumulativa por construcción, no se puede sacar por la API—. Todo se
descarta en el corte.

### La verificación de sintaxis no ve el archivo equivocado

**Es la lección de la jornada, y la tuvimos dos veces.**

El bloque K avisaba de un riesgo **RUIDOSO**: pegar dos `columnas_que_acumulan`
da `Cannot redeclare` y se caen todas las rutas, como ya había pasado con
`columnas_permitidas`. Lo que ocurrió fue el **MUDO**: al ensamblar el archivo,
el script tomó la PRIMERA ocurrencia del método por nombre —la del bloque J— y
la de K quedó afuera. Quedó el viejo.

Y el sistema entero dijo que estaba todo bien:

| Verificación | Dijo |
|---|---|
| `php -l` | limpio |
| `grep "function [a-z_]*" \| uniq -d` | sin duplicados |
| las cuatro sondas | 401 correcto |
| el push | 200, cola resuelta |

**Ninguna podía verlo, porque todas comprueban que el archivo esté SANO, no que
sea el CORRECTO.** Un archivo con el método viejo está perfectamente sano.

> **Cuando un bloque REEMPLAZA algo ya desplegado, la comprobación no puede ser
> «no hay duplicados»: tiene que mirar el CONTENIDO del que quedó.** Sintaxis y
> unicidad detectan el fallo ruidoso; sólo el contenido detecta el mudo. Todo
> bloque que reemplace algo desplegado trae su `grep` de contenido, no sólo el
> de sintaxis:
>
> ```
> grep -A6 "function columnas_que_acumulan" .../Api_regla.php
> ```
>
> Si no aparece `check_list_mecanica_falla`, quedó el viejo — y no hay ningún
> otro síntoma.

Es el mismo modo de falla que la lista blanca que ignora en silencio, una capa
más arriba: allá se pierde una columna del cuerpo, acá se pierde un método
entero del despliegue. En los dos casos la respuesta es 200.

### El instrumento que mide tiene que estar bajo prueba

Va **tercera** vez con la misma forma: la herramienta que mira si algo está roto,
rota.

| Cuándo | Qué se rompió | Cómo se veía |
|---|---|---|
| bloque J | la sonda 4 de `verificar_push_produccion` | 500 en vez de 400 — la única que prueba el locking sin escribir |
| 2026-09-04 | `columnas_que_acumulan` quedó en la versión vieja | `php -l` limpio, sin duplicados, 401 en las sondas |
| 2026-09-04 | **`reconciliar.py` moría con `KeyError`** | la corrida diaria no corría |

El tercero es el peor. La reconciliación es **el instrumento de aceptación del
mes en paralelo**: es lo único que ve el 18,4% de cambios de estado que el legado
hace sin dejar fila en `registros`. Si puede morirse en silencio, *«la
reconciliación no mostró nada»* no es evidencia de nada — es la misma frase tanto
si todo está bien como si el informe no se imprimió.

**Arreglarlo no alcanzaba.** `probar_reconciliacion.py` corre ahora
`reconciliar.py` **como proceso, de punta a punta**, sobre una copia sembrada, y
falla si revienta, si el informe pierde una de sus cuatro secciones, si deja de
imprimir alguna categoría vigente, o si reaparece un nombre de antes del cambio
de arquitectura. Se corre como proceso y no importando `main()` a propósito: lo
que se rompió no fue una función, fue el script — su import, su parseo de
argumentos y su formateo. Verificado rompiéndolo a propósito: exit 1 nombrando la
regresión exacta.

### Un bloque es una unidad de despliegue, no una unidad de verdad

Dos veces en el mismo día un bloque se aplicó o se retiró a medias:

- El **bloque M** traía tres arreglos, uno falso y dos buenos. Al descartarlo por
  el falso se fueron los dos buenos con él, y el `updated_at` volvió tres horas
  después como bloque N.
- El **bloque K** reemplazaba un método que el bloque J ya había definido. De los
  dos quedó el viejo, en silencio.

> **Cuando un ítem de un bloque se cae, se cae ESE ítem, no el bloque.** Y cuando
> un bloque reemplaza algo que otro bloque puso, el que reemplaza tiene que decir
> **cómo comprobar cuál quedó** — porque la versión equivocada no da error,
> responde 200.

De ahí que cada bloque que toque algo ya desplegado lleve ahora su `grep` de
comprobación arriba, no sólo la instrucción de qué reemplazar.

### Lo que costó más que los bugs: leí la spec en vez del archivo desplegado

El 2026-09-02 reporté que `actualizar()` estaba clavado en `newstocks_cidef` y
que un push de falla podía escribirle a una unidad ajena. **Era falso.** El
método resuelve `tabla_de($entidad)` y lo usa en el SELECT y en el UPDATE; quedó
completo cuando se armó el archivo con los bloques E y F, porque el bloque A
había quedado a medio aplicar.

Dos errores encadenados, y el segundo es el que importa:

1. Tomé como evidencia el mensaje del 404 —`"unidad no encontrada"` para
   cualquier entidad—, que es texto viejo que yo mismo dejé así a propósito en
   el bloque A. Un mensaje no dice qué tabla se consultó.
2. **Leí el bloque que *especifica* `actualizar()` en este repo, no el
   `Api_regla.php` que corre en el servidor.** La spec y el desplegado se habían
   separado, y ganó la spec.

> **CUANDO ALGO FALLA CONTRA PRODUCCIÓN, LA FUENTE ES EL ARCHIVO DESPLEGADO.**
> No el documento que dice cómo debería ser. La spec describe una intención; el
> servidor ejecuta un archivo. Cuando no coinciden, el que tiene razón sobre lo
> que pasó es el archivo. Se lee sin bajarlo:
> `sed -n '/function actualizar/,/^    }/p' ~/public_html/application/controllers/Api_regla.php`

Y una consecuencia de haber retirado el bloque M entero: traía tres arreglos,
uno falso y dos buenos. **Retirar un bloque completo porque un ítem está mal se
lleva puesto lo que sí servía** — el `updated_at` volvió como bloque N tres
horas después. Un bloque es una unidad de despliegue, no una unidad de verdad.

**El estado anterior de esta nota, para referencia:** `scripts/verificar_check_mecanico_produccion.py`, dry-run por defecto, `--escribir` para el push de verdad. Faltan dos cosas, ninguna de código:

1. **El bloque L** (`GET /api_regla/check_list_mecanica/<id>`) sin desplegar. Es la mirilla: los bloques G–K escriben y no había forma de leer. Sin él la verificación termina en «devolvió 201» más un favor en phpMyAdmin, y una verificación que depende de un favor se deja de hacer.
2. **Red.** `claude.logautos.cl` está bloqueado desde el entorno donde corro; `github.com` y `example.com` responden. No es el sitio.

**`movimientos` se verificó contra el endpoint desplegado, no contra el
simulado**: tres sondas (401 sin clave / 400 `unidad_id es obligatorio` / 404
unidad inexistente), después un movimiento real sobre la unidad 66505 (PRUEBA)
con réplica y producción idénticas para que el locking estuviera armado de
verdad. `STOCK`/`C` → `CONTROL DE CALIDAD DESPACHO`/`Cc`, fila `registros`
**305637**. El reenvío con la misma key devuelve 200 idempotente con el mismo id
sin duplicar; un timestamp viejo con clave nueva da 409 sin escribir.

Cosas del push que no se deducen del código:

- **El enganche va en `movimientos.registrar()`**, no en cada pantalla. Es el
  único camino por el que se escribe un movimiento y lo llaman seis pantallas;
  pantalla por pantalla, la próxima quedaba sin push y sin que nadie lo note.
- **La tabla `estado → calle` se calculó con los últimos 6 meses**, no con los
  296k históricos. El histórico mezcla versiones del código y da mayorías
  falsas: `INGRESO A TALLER` daba `IT` 69,9% y `It` 30% como si convivieran,
  cuando `It` es la forma vieja (`IT` es 97,8% en lo reciente).
- **El IT pasa `empuja_movimiento=False`.** El motivo técnico es que encolaba
  dos entradas con el mismo `conocido` y la segunda chocaba contra la primera —
  un 409 falso. El motivo de fondo es mejor: el bloque `It` del legado llama a
  `registromov()` **cero veces**, así que empujarlo le metería al historial una
  fila que su propia pantalla nunca genera. El PDI llama **dos** veces, así que
  ahí sí va.
- **El check list mecánico también pasa `empuja_movimiento=False`**, por lo
  mismo y contado igual: `check_list_mecanica_proces()` llama a `registromov()`
  cero veces y a `actualizar_vin()` una — con `fecha_check_list_mecanica`, que
  se empuja por la entidad `check_mecanica_unidad`.
- **`check_list_ingreso` está en la misma situación y HOY SÍ empuja el
  movimiento.** `Nota.php:check_list()` también llama a `registromov()` cero
  veces. Se descubrió el 2026-09-02 al construir el mecánico, contando las
  llamadas de las cuatro funciones de una. **No se cambió en la misma tanda**:
  es código desplegado y en uso, y cambiarle el push a un módulo vivo no es un
  detalle de otra entrega. Ver el pendiente 8.

**Y una entidad puede compartir ruta sin compartir nombre.** `it`, `pdi` y
`check_mecanica_unidad` son las tres un `PUT /api_regla/unidades/{id}`. Aun así
cada una tiene su nombre, porque lo que se lee en la cola, en el log y en la
reconciliación es el nombre — y una entrada que dijera `it` para un check list
mecánico manda a buscar el problema al lugar equivocado. Es la Regla 0 aplicada
a la cola.
- **El origen no viaja; el destino sí, los tres.** `newcalle`/`newestado`/
  `newpatio` los resuelve el endpoint leyendo la fila dentro de su transacción:
  mandarlos sería mandar lo que REGLA *cree* que el legado tenía, con hasta una
  vuelta de sync de atraso, y quedaría escrito en su historial como un hecho.
  Del lado del destino viajan `accion`, `estado` **y `patio`** — este último
  desde el 2026-08-27, ver el pendiente 2. La distinción no es "qué columnas
  sabemos" sino **quién es la fuente**: el origen lo sabe el legado, el destino
  lo decide REGLA.
- **Las columnas de `registros` están invertidas** y el prefijo miente:
  `accion`/`estado`/`patio` son el **DESTINO**, `newcalle`/`newestado`/`newpatio`
  el **ORIGEN**. Verificado a mano sobre la fila 305637.

---

## Pendientes, en orden

### 1. STOCK sale de las exclusiones — RESUELTO el 2026-08-27

`modulos/ubicacion.py` + el bloque de la pantalla, y recién después `STOCK`
fuera de `SIN_CALLE`. **El orden es la mitad del trabajo**: al revés, el push
habría mandado la calle mayoritaria, que acierta el 25%, escrita en el historial
del legado como un hecho.

Es el único estado cuya calle y cuyo patio salen del formulario en vez de
`CALLE_POR_ESTADO` / `PATIO_POR_ESTADO`. `encolar_movimiento` acepta `calle` y
`patio` explícitos y ganan sobre las tablas: son la respuesta del movilizador
contra una inferencia.

**Las tres cosas que decidieron el diseño, y las tres salieron de medir:**

**Precargar el patio actual está mal el 45% de las veces.** Suena obvio y es
falso: la unidad entra a STOCK justo cuando *cambia* de patio. El predictor es
origen + cliente (79,0%) — CIDEF estaciona en 5 y 3, CARFLEX en 2.

**Ordenar las calles por frecuencia acierta 27,1%; por última usada en ese
patio, 72,4%.** Los movilizadores trabajan una calle por tanda. Es la diferencia
entre ordenar por el promedio del semestre y ordenar por lo que pasó hace diez
minutos.

**Pero la recencia vence, y de golpe**: 75,8% dentro de la hora, ~50% después,
plano. Por eso `VENTANA_RECENCIA_SEGUNDOS` es un corte duro y no un peso que
baja — entre las 2 h y las 30 h no hay nada que ponderar. Y por eso pasada la
ventana la pantalla **deja de sugerir** en vez de mostrar la antigüedad en letra
chica: un chip que se ve confiable cuando no lo es es peor que uno neutro.

> **Ese 3600 sale del sistema viejo, donde la calle se elegía en un `<select>`
> alfabético. La pantalla nueva cambia justo lo que se midió.** Hay que remedirlo
> con `movimientos_regla` cuando haya semanas de uso.

**La propiedad que lo hace seguro: la sugerencia sólo puede ahorrar toques,
nunca agregarlos.** El atajo *prepara*, no confirma — el piso son dos toques
siempre, y el botón nombra el destino completo antes de escribirlo. Un atajo
errado (1 de cada 6,5) cuesta exactamente lo mismo que no haber tenido atajo: un
toque en la calle correcta. Si alguna vez esto pasa a confirmar de una, ese
15,4% se convierte en ubicaciones falsas en el legado.

La recencia sale de `movimientos_regla` y **no** de `registros`, que en la
réplica sólo se actualiza con el dump y tiene semanas de atraso. Eso resuelve el
arranque en frío solo: el primer día no hay tanda, la pantalla entra en modo sin
sugerencia — que es el mismo modo correcto de un lunes a la mañana — y se cura
con el primer movimiento del turno.

`scripts/probar_ubicacion.py` cubre los siete casos. El 5 es el que protege la
ventana: si alguien hace que la tanda no expire, la pantalla sigue andando y
sigue sugiriendo, sólo que la mitad de las veces mal, y no hay forma de notarlo
mirándola.

Los otros tres siguen excluidos, pero **el motivo de `DYP` estaba mal escrito** y
se corrigió el 2026-08-27. Decía "depende del proveedor asignado, que REGLA no
elige" y la calle es **determinista**: la rama `if($calle == 'Dyp')` de
`Pedido.php:8577` descarta lo que el usuario eligió y fuerza `PATIO 2` /
`ENTREGADO DYP` / `ubicacion 1`. En los datos, 189 de 204 movimientos, **92,6%**
— más que diez de las catorce traducciones que ya están cargadas. La medición
vieja (`B` 48%) salía de contar la columna `calle` de las unidades y no el
destino de los movimientos.

`DYP` queda afuera por lo mismo que `DESPACHADO`: esa rama **manda un correo al
cliente** con la patente de la unidad entregada al proveedor. Empujar solo la
columna deja media entrega hecha. No se arregla agregando la línea a
`CALLE_POR_ESTADO` — se arregla migrando "Actualizar DYP" entera, correo
incluido. El comentario viejo invitaba justo a lo que no hay que hacer.

`SALIDA DYP` sin cambios: 8 movimientos en 6 meses, y las calles llevan el
nombre del proveedor. `DESPACHADO` sin cambios: correo más OT.

### 2. El patio destino quedó vacío — RESUELTO el 2026-08-27

`PATIO_POR_ESTADO` en `push_legado.py`, al lado de `CALLE_POR_ESTADO` y con la
misma clave. **Sin tocar el PHP**: `Api_regla_movimientos.php` ya aceptaba
`patio` como campo opcional del cuerpo desde el primer día.

**Las dos razones que estaban escritas acá eran falsas, y las dos se cayeron
midiendo.**

La primera era "el patio lo decide el legado por rama y REGLA no lo pregunta".
No hace falta preguntarlo: **el patio es función del estado destino**, porque
cada etapa vive en un patio fijo. Los catorce estados traducibles dan entre
93,6% y 100% sobre los últimos 6 meses.

La segunda era "como la unidad no cambia de patio, lo correcto sería repetir el
origen". **Es al revés.** Para `Cc`, las 3.247 filas del semestre van a PATIO 1
vengan de donde vengan, y 2.948 venían de PATIO 2 — ninguna repite el origen. Ir
a control de calidad *es* ir al patio 1. Repetir el origen en la 305637 habría
escrito `PATIO 5`, que es justo el valor que el legado nunca escribe para `Cc`.

**Por estado y no por calle**, que fue el segundo intento y era peor. Medido por
calle, `A` daba PATIO 2 al 78,8% y parecía el único caso ambiguo. No lo era: `A`
es a la vez calle de estacionamiento y calle de las dos esperas de check list, y
el 21% era el estacionamiento contaminando la cuenta. Separados por estado, los
dos que usan `A` dan 100%. Misma lección que el histórico contra los 6 meses: la
mayoría falsa aparece cuando se agrega sobre una clave que mezcla dos cosas.

**La excepción es la PDI**: el legado deja el patio vacío en las 3.241 filas del
semestre, las 3.241, porque su bloque arranca con `$patiopdi = ' '` y nunca lo
usa. `patio_para()` devuelve `None` y se manda vacío a propósito. Cuando entre
la entidad PDI hay que dejarlo así — coincidir vale más que tener razón.

La 305637 la corrigió Franco a mano a `PATIO 1`. El caso 8 de `probar_push.py`
verifica que las dos tablas cubran los mismos estados: el modo de falla de esta
pareja es silencioso, y agregar un estado a una y olvidarlo en la otra vuelve a
producir filas con el patio vacío sin que nadie se entere.

### 3. El push de PDI — CERRADO el 2026-08-27

Una PDI encola **cuatro** entradas, con orden:

```
movimiento  (registros + la unidad, en la transacción del endpoint)
   ├── pdi                 las 14 columnas
   ├── ot_pdi              las dos OT, una sola entrada
   └── stock_consumibles   el descuento, si consume
```

Las tres cuelgan del movimiento con **`depende_de`**. Se escriben en la misma
transacción que la PDI — sobreviven a que el proceso muera — y no se intentan
hasta que el movimiento esté resuelto **y sin error**. Encolarlas *después* de
que el movimiento vuelva OK las perdería si el proceso muere en el medio, y eso
deja una PDI aplicada en el legado y sin cobrar. Un 409 en el movimiento
significa que el legado ganó: no hay PDI que cobrar, y `orden_trabajo` es
append-only.

> **La guarda va en `ejecutar_entrada`, no sólo en el selector de pendientes.**
> `disparar_push` llama directo con el id y `guardar_pdi` dispara las cuatro
> seguidas: con la guarda sólo en el selector, la OT salía **antes** que el
> movimiento. Lo encontró `probar_circulo.py`. Las dos puertas o ninguna.

**Las dependientes tardan un ciclo de cola.** `procesar_pendientes` junta los
ids al principio, así que son elegibles en la vuelta siguiente. El hilo corre
continuo: es una espera, no una falla, y la prueba lo hace explícito.

**Los precios coinciden al 100%** entre `ot_pdi.py` y el PHP, verificado contra
producción sobre la unidad 66505 (PRUEBA) y contra el histórico. El 201 devuelve
`litros` y `valor` además del precio, y eso es lo que deja comparar **la regla**
y no sólo el número final, que puede coincidir de casualidad.

> **Lo que ese 100% NO cubre: la asimetría G7/G9.** Ninguna unidad `PRUEBA`
> tiene un modelo que empiece con `G9`/`V7`/`V9`, así que las tres pruebas
> corren por la rama de 20 litros. La asimetría —cuatro prefijos en Bencina, uno
> en Diesel— está validada contra las 969 OT históricas del lado Python, y **sin
> verificar del lado PHP**. Se cierra poniéndole `marca='FOTON'` y
> `modelo='V9 2.0'` a una unidad PRUEBA.

**La compuerta** (`modulos/combustible.py`) se evalúa contra la **réplica**: si
el legado está lento, la pantalla del patio no se puede colgar. Frena de verdad
hoy — el diesel tiene 5 litros contra un umbral de 20. `fila_de()` exige
encontrar **exactamente una** fila y distingue los dos modos de falla, porque no
se arreglan igual: tabla vacía (*"falta el pull"*) contra combustible sin fila
(*"avisá a sistemas"*). No hay fila para `GASOLINA`.

### 4. `registros` entra al pull — DESPUÉS del push de PDI

Hoy `registros` se lee solo del dump, y por eso **no se puede leer desde
producción el contenido de una fila recién escrita**: el punto 2 se verificó a
mano porque no había otra forma. Sirve para eso y para que la reconciliación
deje de depender del dump.

Va después de PDI a propósito: PDI es el que le falta al circuito, y meter una
entidad nueva al pull mientras el push todavía crece agrega superficie sin
cerrar nada.

### 5. El runbook del corte — NUNCA el PHP solo sobre el sistema vivo

Hoy quedó el **PHP adelante del Python**: el legado acepta 19 columnas de las
que REGLA manda 5, y tiene **tres endpoints de escritura que nadie llama** —
`descontar_stock`, `crear_ot_pdi` y el PUT ampliado.

Sobre la copia congelada eso es inerte. **Al corte, esos endpoints van al
sistema vivo**: puertas de escritura sobre `orden_trabajo` y sobre el stock de
combustible, abiertas, sin consumidor, protegidas sólo por la API key.

**La regla: no se despliega nunca el PHP solo, con puertas de escritura sin
consumidor, sobre el sistema real.** Van juntos o va primero el consumidor.

Cómo se llegó acá, para no repetirlo: el PHP se desplegó primero a propósito y
con buen motivo — la lista blanca ignora en silencio, así que cablear sólo
Python da 200 y cero efecto. Ese orden es el correcto **sobre la copia**. Sobre
el sistema vivo se invierte, y hay que acordarse de invertirlo.

### 5b. Que DYP empuje de verdad

Hoy DYP es **el único paso que la pantalla deja elegir y que no viaja**. Se
guarda en `movimientos_regla`, no se escribe la fila, y la pantalla lo dice:
*"Este paso todavía no viaja al sistema anterior"*.

El obstáculo escrito en `SIN_CALLE` es que la rama del legado **manda un correo
al cliente** con la patente de la unidad entregada al proveedor, así que empujar
sólo la columna deja media entrega hecha.

**Ese obstáculo ya no es lo que era.** Para las OT de la PDI decidimos que los
efectos que el query builder no dispara **los hace REGLA** — por eso existe
`ot_pdi.py` y el endpoint estrecho del bloque D. Mismo criterio: REGLA manda el
correo, o lo manda un endpoint estrecho como el de las OT.

La calle es determinista (`ENTREGADO DYP`, 92,6%) y el patio también (PATIO 2),
así que la traducción no es el problema. Lo que falta es el correo.

Más adelante, no ahora.

### 5c. La OT del check list: la sigue creando el legado

**Divergencia consciente, decidida el 2026-08-28**, para el mes en paralelo.

El flujo: el movilizador hace el check list en REGLA, el push escribe
`check_list`, y **administración abre la pantalla del correo en el sistema
viejo** — ahí se crea la OT, sale el correo y se mueve el estado, que vuelve por
el pull. Es un paso más para administración, que ya está en el sistema viejo
igual.

Por eso REGLA **no** empuja `estado_check_list` ni `calle`/`despachado`/`patio`
para el check list, y **no** manda ese correo.

**El motivo no es el tiempo, es la calidad.** El cálculo de la PDI eran diez
líneas y hicieron falta dos rondas y dos reglas descubiertas para llegar al
100%. El del check list son **4.193 líneas**: 24 ramas de tipo de daño × 3
niveles × 2 clientes, con los precios en `piezas` (429 filas × pulir/desab/
pintura × cidef/carflex). Emitir OT con precios mal calculados es el peor error
que este proyecto puede cometer, porque es **el primero que le llega al
cliente**.

**Y no son dos OT, es una.** `$otes_lavado` está comentada, y la segunda OT de
LAVADO también. La única viva es `PRESUPUESTO` — y ni siquiera siempre: para
CARFLEX el código hace `$id_ot = 28701` (un id fijo, no crea nada) y sin daños
hace `$id_ot = 666`. El dato lo confirma: `CHECK LIST DYP`, la variante CARFLEX,
murió en 2025-05.

**EL ORÁCULO YA ESTÁ, para cuando se retome.** `check_list.observacion` /
`requerimiento` / `gravedad` tienen las tres listas alineadas, `check_list.id_ot`
enlaza con la OT, `piezas` está en la réplica y el `precio` de la OT es la suma
de los ítems. Se valida al 100% igual que la PDI.

**Y lo primero es medir qué ramas están vivas** sobre las 232 OT de
`PRESUPUESTO` desde 2026-06, antes de escribir una línea. De las 24 ramas
posiblemente se usen seis — es la misma lección del catálogo de calles, donde 13
letras del menú tenían cero movimientos.

### 5d. Los destinatarios de correo van en una tabla

El legado los tiene **cableados en el PHP**, por cliente: seis direcciones para
CARFLEX, una para CIDEF, más ASTARA, POMPEYO e internos de Logautos. Nadie los
puede cambiar sin desplegar.

En REGLA van en una **tabla**. Es un problema heredado que no vale la pena
copiar, y la lista de a quién le llega un correo con daños de un vehículo es
justo lo que cambia sin avisar cuando alguien entra o sale de un puesto.

### 5e. El check list mecánico — pasos 1 y 2 construidos el 2026-09-02

**Dónde quedó cada cosa:** el Python está en el árbol de trabajo (sin commitear
al escribir esto); **el PHP no está desplegado** — los bloques G, H, I, J y K de
`scripts/Api_regla_check_list.php` siguen esperando. Hasta que lo estén, el push
del módulo va a fallar con 404, que es ruidoso y por lo tanto está bien.

**Lo que hace REGLA:** los pasos 1 (los 65 campos) y 2 (las fallas con su foto).
**El paso 3 no** — el correo que cierra el check list lo aprieta administración
en el sistema viejo y el estado vuelve por el pull. Mismo criterio que el check
list de ingreso; mantenerlos iguales vale más que optimizar cada uno.

**Divergencias conscientes, con su número al lado:**

| Qué | Por qué no se replica |
|---|---|
| `precios` / `precios_adicionales` | Comentados en el código del legado, y **16 filas de 2.956**; la última con valor ≠ 0 es del **2026-02-13**, seis meses. |
| `faltante` | No es que se use poco: **inalcanzable**. La línea que la leería está comentada y la vista no tiene ningún campo con ese nombre. 0 filas en 2026. |
| `servicios_mecanicos` | **No está replicada** — la única tabla del módulo que el pull no trae. REGLA sugiere contra lo que realmente se escribió, ordenado por frecuencia, que es mejor fuente además de ser la única. |

**Lo que sí se replicó y casi no se ve: la REAPERTURA.** Un check list en
`REABIERTO` escribe en `fallas_adicionales` / `modalidad_adicional` /
`fotos_adicionales` en vez de las tres normales. Son 68 filas, **todos los meses
desde que hay datos, 40 en 2026**. Ver el ejemplo nuevo de la Regla 0 sobre cómo
casi se mide mal.

**El catálogo salió del MENÚ, no del dato**, y la diferencia se midió: por
valores observados salen 19 vocabularios, por lo que el formulario ofrece salen
**7**. Los otros doce son espejismos — `bocina` aparece siempre como `Bueno`
porque a nadie se le rompió una, no porque el formulario ofrezca una sola opción.
Validado contra **89.760 valores históricos: 0 fuera del menú** (16 son el corte
de `varchar` de MySQL, que ya conocíamos).

**Las fotos van por una ruta pública con token**, `/f/<43 caracteres>`, y eso
sube la barra respecto del legado, que tiene la carpeta abierta con el nombre del
archivo adivinable (lleva el VIN y la fecha). Dos condiciones que son de diseño y
están construidas así:

1. **La ruta resuelve por TOKEN contra `fotos_publicadas`, nunca por ruta de
   archivo.** Si fuera `/f/<hmac(ruta)>`, toda foto de REGLA quedaría alcanzable
   para quien conozca el esquema — incluidas las de `check_list_regla`, que
   llevan `link_guia`, la guía con nombres y RUT. Publicar es un acto, no una
   propiedad de estar guardado en el disco.
2. **No se revoca.** La URL es la credencial: una vez que salió, queda en
   `link_unidades`, en los logs y en el historial de quien la abrió. Para fotos
   de daños de un vehículo es aceptable; para un documento con datos de una
   persona no lo sería, y por eso la condición 1 importa más que el largo del
   token.

### 6. Migraciones versionadas — aprobado, sin construir

Variante de fuente única. Migración 1 = esquema completo; migración 2 = arreglo
explícito del `unidad_id` que falta en `inspeccion_despacho_regla` en Railway.
Correr `estado` justo después de la 1 es **obligatorio** en el runbook.

`PRAGMA foreign_keys=ON` queda **explícitamente afuera**.

### 8. `check_list_ingreso` empujaba un movimiento espurio — RESUELTO el 2026-09-02

Descubierto contando las llamadas a `registromov()` de las cuatro funciones de
check list de una sola pasada:

| Función del legado | `registromov()` | `actualizar_vin()` |
|---|---|---|
| `check_list()` | **0** | 1 |
| `check_list_mecanica_proces()` | **0** | 1 |
| `subida_foto_check_list_mecanico_proces()` | 0 | 0 |

El mecánico nació con `empuja_movimiento=False`. El de ingreso lo empujaba, así
que cada check list hecho en REGLA le metía a `registros` una fila que la
pantalla del legado nunca genera — y de ese historial salen sus reportes.

**Se contó antes de tocar y el número era CERO:** no había ningún check list
cargado en REGLA en Railway, así que no quedó ninguna fila espuria que limpiar.
El cambio es sólo hacia adelante. `check_list.py` pasa `empuja_movimiento=False`
desde el 2026-09-02.

**Consecuencia que hay que tener presente: el check list de ingreso ahora no
empuja NADA.** Su entidad `check_list` (bloque G, 24 columnas) está desplegada
del lado PHP, pero Python no la tiene en `ENTIDADES` — nunca se construyó. Ver
el pendiente 9.

### 9. El check list de INGRESO — VERIFICADO el 2026-09-04

**Son DOS escrituras y no son simétricas.** Ésa es la decisión de diseño del
módulo, y no se resolvió sola porque en la prueba anduvieran las dos:

| Orden | Qué queda | Cómo termina |
|---|---|---|
| **1 entra, 2 falla** | el legado tiene el check list; la unidad todavía no lo dice | la cola reintenta con backoff y **converge sola**. Mientras tanto hay una fila en `check_list` que se puede encontrar |
| **2 entra, 1 falla** | la unidad **dice** que tiene check list y no existe ninguno | **nadie lo nota**: `fecha_check_list` es una fecha plausible y nada se ve roto |

Por eso `depende_de` va en la 2 y no al revés: la primera se recupera, la
segunda hay que hacerla **imposible**. Probado en `probar_circulo_ingreso.py`
forzando cada mitad — ejecutar la 2 primero devuelve `espera` y la unidad queda
intacta; con el legado caído la 2 falla, queda sin resolver con el intento
contado, la fila del check list sigue ahí, y al volver el legado entra sola.

**`observaciones` acumula y NO se duplica.** El endpoint concatena (bloque J),
así que REGLA manda **sólo los daños de esta pasada**. Verificado con dos
guardados seguidos sobre la 66505 en producción:

```
antes   : ...ANTORCHA CAJON DEL IZQ DESPEGADA (1) LEVE | SONDA-ACUMULA-1
agregado:  CAPOT RAYA (1) LEVE | PARACH DEL ABOLLADO (1) MEDIO |
```

Lo anterior no se tocó y la primera pasada aparece **una sola vez**. Si REGLA
mandara el acumulado completo —como hacía el legado, que leía con
`getobservacion_dyp()` y concatenaba del lado del cliente— cada guardado
duplicaría todo lo anterior. El riesgo acá es al revés que en la PDI: allá era
**pisar**, acá es **repetir**.

**El separador salió del DATO, no del código**, y la distinción importó:
`Nota.php:check_list()` arma `'1 -'.$pz1.' | <br>'`, un formato numerado que en
la réplica sólo aparece en filas viejas. Medido sobre **6.378 filas de los
últimos 12 meses** con las tres columnas cargadas: `-` alinea **6.376**, y 5.952
tienen más de un elemento.

**Las 2 que no alinean no son el separador: es TRUNCAMIENTO.** `observacion` y
`requerimiento` topan en **990 caracteres** y MySQL corta —la base no está en
modo estricto—, así que las tres listas se desalinean y la pieza *n* deja de
corresponderse con su tipo de daño. La fila 20058 tiene 63 piezas, 65 tipos y 90
niveles: `requerimiento` termina cortado en `-A`. REGLA lo avisa en el log antes
de mandar; no lo arregla, porque recortar del lado nuestro sería una divergencia
en vez de un aviso. (Y no es un guion dentro de un nombre: de los 429 nombres de
`piezas`, los 35 de `tipo_dano` y los 4 de `nivel_dano`, **ninguno** tiene uno.)

**Lo que REGLA no manda, con su número:**

| Columna | Por qué |
|---|---|
| `tapiz` | la pantalla no lo pide. **2,6%** de las filas del período, y sólo dos valores (`CAFE` 175, `NEGRO` 12) |
| `n_asientos` | ídem. **0,8%** |
| `link_unidad` / `link_guia` | las fotos quedan para el paso del correo con PDF adjunto. Mandar una URL que no abre es peor que no mandar nada |

Están en la lista blanca del otro lado, así que si alguna vez se agregan al
formulario entran sin tocar nada más.

**Replicado tal cual aunque esté mal**, con el comentario al lado:
`fecha_lavado_produccion` se sella con la fecha del día aunque nadie haya
lavado — está llena en el **60,3%** de las unidades con check list, así que
quien la mire ya la está leyendo mal. Y `fecha_entrega`, que es la misma
especie: **7.086 de 7.086** filas la tienen igual a la fecha de creación. Una
fecha de entrega que no sabe nada de ninguna entrega. **No se arreglan acá.**

**Y el cruce de nombres, que es la Regla 0 con otra cara:** la columna
`observaciones` de la UNIDAD recibe los **daños**, y `observacion_general`
recibe el texto libre que en `check_list_regla` se llama `observaciones`. La
misma palabra significa una cosa de un lado y otra del otro.

### 10. Inspección de despacho — construida, PHP desplegado el 2026-09-04

Las tres sondas verdes (401 / `no existe inspeccion_despacho 999999999` / 31
columnas con `patio`) y la fila mínima entró: **`inspeccion_despacho.id = 16408`**.

**Lo que el módulo hace** (`Nota.php:inspeccion_despacho()`, 486 líneas): escribe
la fila; hace **un** `actualizar_vin` con `patio='PATIO 2'`, `calle='IT'`,
`despachado='EN ESPERA CC ZD'` —los tres literales—; `registromov` **cero veces**
→ `empuja_movimiento=False`; **no crea OT**; y manda un correo **sin PDF**,
ramificado por `$cliente` y, dentro de CIDEF, por 13 `strstr` sobre `$destino`.

**El PDF con fotos no sale acá.** Sale en el DESPACHO: `Pedido.php:2130`, dentro
de `inicio_proces()`, llama a `generarPdfInspeccion()` (`Pedido.php:1463`), que
lee `archivo1..archivo9` y por cada una llama a `descargarImagenComoDataUri()`
(`Pedido.php:1396`). **Ese cargador no se tocó**: ya detecta URLs absolutas — el
`base_url()` está dentro del ternario, en la rama de la ruta relativa.

#### El envío es un acto aparte, y es mejor que el original

El legado inserta la fila al confirmar y después le pega las fotos con UPDATE.
REGLA manda la fila **una sola vez, completa**, con un botón que el legado no
tiene. No es una concesión al endpoint desplegado: **ese estado intermedio del
legado es el peligroso**, porque una fila sin fotos es la que el PDF del despacho
renderiza con la sección vacía — el bug de agosto — y el legado tiene esa ventana
abierta hoy.

**La ventana no es teórica, es 1 a 2% por mes.** Inspecciones sin ninguna foto
(`archivo1` vacío, que coincide exactamente con `contador = 0`):

| Mes | Sin fotos | De |
|---|---|---|
| 2026-06 | 16 | 892 (1,8%) |
| 2026-07 | 6 | 735 (0,8%) |
| 2026-08 | 4 | 272 (1,5%) |

Entre 4 y 17 por mes, sostenido. Cada una es un PDF que llegó al cliente con la
sección de fotos vacía. **No se pide la ruta PUT.**

#### El acoplamiento nuevo: un documento del cliente depende de REGLA

**Es la primera vez que algo que sale a un tercero depende del sistema nuevo.**
Las URL con token viven en `archivo1..archivo9` del legado, y el legado las
descarga **recién en el despacho, otro día**.

Cuánto después, medido sobre 8.953 pares inspección→despacho de los últimos 12
meses:

| | |
|---|---|
| el mismo día | **81%** |
| dentro de 7 días | 85% |
| p90 | 41 días |
| p95 | 137 días |
| máximo | 336 días |

*(El emparejamiento es por VIN contra `newstocks_cidef.fecha_desp`, así que la
cola larga puede estar inflada por reingresos — la regla de la frontera. El 81%
del mismo día no depende de eso.)*

**Las consecuencias, que no hay que arreglar pero sí tener anotadas:**

1. **Los tokens no vencen y nada los borra.** Verificado leyendo
   `modulos/fotos_publicas.py`: no hay TTL, ni `expira`, ni `timedelta`, ni un
   solo `DELETE FROM fotos_publicadas` en todo el código de la aplicación.
   `ver()` resuelve el token contra la tabla y sirve el archivo, sin mirar
   fechas. **Es permanente por construcción** — y desde ahora eso es un
   requisito, no una casualidad: `probar_check_list_mecanica.py` lo afirma por
   código, así que agregar un TTL o una limpieza rompe la suite.
2. **El correo al cliente depende de que REGLA esté arriba en el momento del
   despacho**, que puede ser dentro de un año. Cada despliegue a Railway es una
   ventana chica; si cae justo ahí, el PDF sale con «No se pudo cargar la
   imagen» y la URL impresa —el legado ya lo hace así, para poder diagnosticar—.
3. **Y depende del volumen de Railway.** Si alguien limpia `DATA_DIR`, los PDF
   de los despachos futuros pierden sus fotos. No hay copia del otro lado: el
   legado guarda la URL, no el archivo.

#### El tope de nueve es del cable, no del modelo

Las fotos viven en `inspeccion_despacho_fotos_regla`, una por fila, **sin
límite**. El aplanado a `archivo1..archivo9` pasa recién al empujar y
`aplanar_para_push` devuelve `sobrantes` con las que no entraron; la pantalla lo
dice antes de enviar. `link_unidad` va con **todas**, así que la foto once llega
igual al legado: lo que no llega es a la sección de fotos del PDF.

**`contador` lleva el número real, no la cantidad de slots.** Con once fotos va
11. Es lo que hace el legado —su `_proces()` guarda `'contador'=>$cont` siempre,
y la cadena de `elseif` sólo decide a qué `archivoN` va la foto— y no rompe nada
porque **nadie lo lee para recorrer los archivos**: el único lector es
`getcont_insp_desp` (`Nota_model:2994`), llamado desde el propio paso de subida
para elegir el slot siguiente. `generarPdfInspeccion` **no lo usa**: recorre los
nueve campos y descarta vacíos. Poner 9 con once fotos haría que la columna
conteste «cuántos slots se llenaron» cuando su nombre pregunta «cuántas fotos
hay» — la Regla 0 en chico.

#### `EN ESPERA CC ZD` entra en `RECONOCIDOS_SIN_RUTA`, con una condición nueva

El dato lo justifica: **0 filas** en `registros.estado` —coherente con
`registromov` cero veces— y **2** en `registros.newestado`, o sea que el único
rastro que deja es el movimiento que *sale* de él, los dos a `DESPACHADO`. Es una
marca de paso, la misma especie que `SOLICITUD DESPACHO`.

**Pero es el primero de esa lista que ORIGINA REGLA.** Hasta ahora esos estados
los ponía el legado y REGLA sólo los mostraba: una lista de pasos vacía era
coherente porque REGLA no había hecho nada. Acá el movilizador llega **justo
después de confirmar su trabajo**, y el cartel genérico —«no hay paso siguiente
definido»— se lee como que la pantalla se rompió.

De ahí `QUIEN_SIGUE`: la pantalla dice **«Esperando despacho — administración»**.
Si el estado **no** es de los reconocidos, la pantalla sigue diciendo que no hay
paso definido — ahí la ausencia sí es rara y no hay que taparla.

### 11. Almacenamiento: se escribe en LOS DOS lados, y el objeto queda postergado

**Decisión de Franco, 2026-09-04.** Durante el mes de mejoras las fotos van al
**legado** —para que su PDF las encuentre donde siempre— **y a Railway**, que es
la copia que tiene que sobrevivir.

**La subida al legado NO se enciende hasta que Franco confirme que el disco
bajó.** El cPanel está al **97% de 200 GB** (~8 GB libres) y él está liberando
correo (más de 160 GB en cuentas). Un cPanel al 97% es donde MySQL empieza a
fallar escrituras, y eso rompe el sistema con el que trabaja la empresa hoy, no
REGLA. Por eso el endpoint lleva un **interruptor explícito** en `FALSE`.

**Y no está resuelto:** Railway sigue acumulando sobre 4,6 GB, o sea ~3 meses aun
con fotos más chicas. **El almacenamiento de objetos queda POSTERGADO, no
cancelado**, y vuelve cuando dejen el cPanel — el plan de fondo es que a fin del
mes de pruebas ese servidor quede sólo para correo y para el legado.

#### El caudal y el peso, medidos

18.300 fotos/mes con los cuatro módulos vivos: check list de ingreso 8.116,
inspección 6.096, IT 3.584, mecánico 503.

Fotos reales del legado (n=24): **96 KB promedio, 201 KB máximo**. Y un hallazgo
que cambia la discusión: **ninguna foto del legado supera los 1000 px** — el PHP
las reduce al subirlas. Lo que el cliente ve en el PDF hace años es eso. **Los
1600 px de REGLA están por encima de la base que ya funciona.**

`hoja_fotos.html` es la hoja de comparación con tres fotos reales de daños en
1000/800/600 px × calidad 0,8/0,7/0,6, con el peso y un recorte al 100% para
juzgar si el rayón sobrevive, más la tabla de meses de autonomía por cada peso.
**El número lo elige Franco mirando, no se discute.**

> **WebP queda descartado, con evidencia.** El PDF lo arma **dompdf 0.8.6**, y
> `Helpers::dompdf_getimagesize` mapea sólo `IMAGETYPE_JPEG`, `GIF`, `BMP` y
> `PNG`. Un WebP sale con `$type = null` y la imagen se descarta. Seguimos en
> JPEG.

#### Perfiles por módulo — la distinción es de Franco y es mejor que un ajuste global

No todas las fotos prueban lo mismo: las de inspección prueban que algo **está**
(rueda de auxilio, gato, extintor); las del check list prueban **cómo está** un
daño. Punto de partida, a confirmar contra la hoja: inspección 800 px calidad
baja, check lists 1200 px calidad alta, IT 1200 px cuando lleva evidencia de DYP
o FR.

#### El endpoint de subida — `scripts/Api_regla_subir_foto.php`, sin desplegar

Cinco propiedades, y **dos cosas que no se pudieron cumplir tal cual**:

| | |
|---|---|
| **El margen de disco no ve la cuota de cPanel** | `disk_free_space()` informa el *filesystem*, que en hosting compartido es el del servidor entero: puede decir cientos de GB libres con la cuenta al 97%. Se implementan **dos frenos** y el que protege de verdad es el interruptor manual. |
| **La carpeta del check list de ingreso no se replica** | El legado usa `assets/images/{motonave}/{vin}/`, y las dos partes salen del dato — la motonave es texto libre y ya rompió una vez (`COSCO PACIFIC / YANTIAN` crea dos directorios). Replicarlo exigiría aceptar la carpeta desde el cuerpo, que es justo lo prohibido. Los daños de REGLA van a `assets/images/danos/`, que el legado ya usa. **No afecta al PDF**: ése lee `archivo1..9` → `assets/images/unidades/`, que sí es plana y se replica exacta. |

**Dónde escribe hoy cada módulo**, medido sobre el PHP:

| Módulo | Carpeta | Nombre |
|---|---|---|
| Check list ingreso, daños | `assets/images/{motonave}/{vin}/` | `{vin}_{pieza}_{tipo}_{nivel}_{Y-m-d H:i:s}_.jpg` |
| Check list ingreso, unidad | `assets/images/foto_unidad/` | `{vin}_foto_unidad_{Y-m-d}_.jpg` |
| Check list mecánico | `assets/images/falla/` | `{vin}_FALLA_MECANICA_NRO_{n}_{Y-m-d H:i:s}_.jpg` |
| Inspección de despacho | `assets/images/unidades/` | `{vin}_INSPECCION_{rótulo}_{Y-m-d H:i:s}_.jpg` |
| IT | `assets/images/it/{vin}/` | `IT_{vin}_{Y-m-d_H-i-s}_{n}.jpg` |

**Tres detalles del nombre que hay que copiar** para que los de REGLA no se
reconozcan de lejos: el saneo es `strtr($n, " ", "_")` —**sólo espacios**, los
dos puntos de la hora se quedan—; hay un **guion bajo antes de la extensión**
(`_.jpg`), que sale de un pegado accidental pero lo tienen los 16.365 archivos
que ya existen; y el IT es el único con otro estilo, porque lo escribió otra
persona.

### 5d. Los destinatarios, con las dos condiciones

Las **tres ramas en cero** (`Vega`, `REAL`, `Grass`) **no se migran**, pero la
tabla cae al conjunto por defecto **y registra** cuando un destino no calza con
nada conocido. *Cero en 12 meses no es muerta, es no observada* — es el
precedente de `LAVADO KSM`.

El número que lo hace urgente: **4.236 de 5.560** inspecciones CIDEF del período
**no calzan con ninguna de las 13 ramas** y caen al else. Y hay **438 destinos
distintos** contra 13 ramas.

### 7. Decisiones que esperan datos, no código

- **CARFLEX 0,022 vs 0,026.** El legado tiene **dos implementaciones vivas** con
  tarifas distintas para el mismo cliente (`dash_acopio.php` y
  `Examples.php::acopio_logautos`). REGLA replica la primera. **No se decide
  leyendo el código**: que haya dos valores vivos significa que nadie lo revisó
  en años, no que uno sea el bug. El oráculo es una factura emitida, que está
  fuera del sistema.
- **`SOLICITUD DESPACHO` como nodo enrutable**: el 21% de sus movimientos
  ocurren con la unidad todavía navegando.
- **`transicion_valida()`** sin conectar, y `DESVIOS_CON_MOTIVO` sin pares
  nuevos. El motivo ya se guarda en todo desvío (opcional), así que el dato se
  está juntando solo. Mismo criterio los tres: semanas de uso real y decidir con
  números.

---

## Cómo trabajar acá

**El orden de prueba, que se siguió entero y por eso el primer contacto con
producción no rompió nada**: endpoint simulado → sondas que no escriben → un
tiro real chico sobre una unidad `clientecompleto = 'PRUEBA'`.

**Todo script de verificación afirma que la fila que lee es la que pidió**
(`assert id_devuelto == id_pedido`) antes de comparar nada. Nació de un script
que leía la primera fila devuelta en vez de la pedida y daba por buena una
verificación que no había hecho.

Para leer el estado real de una unidad en producción **sin escribir**: un PUT con
`legado_updated_at_conocido` del año 2000 y sin campos de la lista blanca
devuelve 409 con `datos_actuales`.

**`C:\Regla_Python\application` es SOLO LECTURA** — es el legado. El PHP nuevo se
escribe como archivo aparte en `scripts/`.

**EL LINT SE CORRE EN EL SERVIDOR, Y NO ES OPCIONAL.** No hay `php` en la
notebook, pero el hosting es cPanel y trae Terminal — el servidor sí tiene PHP.
Después de subir y ANTES de probar nada:

```bash
php -l ~/public_html/application/controllers/Api_regla.php
```

Tiene que decir `No syntax errors detected`. Esto quedó sin hacerse mucho tiempo
porque se creía que hacía falta PHP en la notebook; no hace falta. El 2026-08-27
un método duplicado tiró la clase entera abajo y se diagnosticó a ciegas desde
Python, cuando una línea lo decía.

**Y EL LINT NO ALCANZA PARA UN MÉTODO DUPLICADO.** `php -l` sólo parsea, y un
`Cannot redeclare` es un fatal de COMPILACIÓN: aparece al cargar la clase, no al
parsearla. Así que además:

```bash
grep -o "function [a-z_]*" ~/public_html/application/controllers/Api_regla.php | sort | uniq -d
grep -c "<?php" ~/public_html/application/controllers/Api_regla.php
```

El primero tiene que salir **vacío** y el segundo dar **1**. El `uniq -d` es
mejor que contar un método concreto porque no hay que saber cuál buscar: lista
todos los duplicados, sea cual sea el que se pegó dos veces.

**LOS SPECS DICEN QUÉ BORRAR, NO "REEMPLAZÁ".** El bloque A decía "reemplazá el
método `columnas_permitidas` (hoy en la línea 271)" y quedaron los dos: el viejo
en la 271 y el nuevo en la 792. El riesgo de un spec que manda reemplazar no es
que el comportamiento cambie mal — es que el método quede DUPLICADO, y eso no es
un bug, es la clase que no carga. De ahí en más, todo bloque que reemplace algo
lleva el texto de la PRIMERA y de la ÚLTIMA línea a borrar, no un número de
línea que se corre con la primera edición.

**Y un reemplazo a medias deja código muerto que parece vivo.** En el mismo
despliegue, `tabla_de()` entró pero `actualizar()` siguió con `newstocks_cidef`
escrita a mano en el SELECT y en el UPDATE: el método nuevo existía y no lo
llamaba nadie. Un spec que agrega un helper tiene que listar TODOS los puntos de
llamada, y la verificación es buscar el literal viejo hasta que no quede
ninguno.

**El legado devuelve 200 con cuerpo vacío en vez de 404** (`404_override`), así
que el cliente exige `ok: true` explícito y no se conforma con un 2xx.

Las diez suites, ninguna escribe en producción:

```bash
for s in estados reconciliacion ficha_estados motivo_desvio push facturacion ubicacion ot_pdi; do python scripts/probar_$s.py; done
python scripts/probar_pull.py
python scripts/probar_circulo.py   # el circuito entero, y la PDI contra las DOS listas blancas
python scripts/verificar_push_produccion.py   # 5 sondas contra producción, ninguna escribe
python scripts/probar_precio_ot.py            # sondas; con --crear escribe OT reales sobre PRUEBA
```

El README tiene el detalle largo (esquema, hallazgos sobre el dato real,
decisiones de cada módulo). Este archivo es el estado; el README es el archivo.
