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

Apareció **cuatro veces**, en cuatro formas que parecen no tener nada que ver:

| El nombre que se usó | Lo que cubría en realidad | Lo que dio |
|---|---|---|
| `vin` | el vehículo y **cada pasada** por el patio | la PDI de meses atrás contaba como la de este reingreso |
| todo el histórico | el código de hoy y **años de versiones viejas** | `IT` 69,9% / `It` 30% "conviviendo", cuando `It` es la forma vieja |
| `calle` | el estacionamiento y **la etapa de proceso** | `A` → PATIO 2 al 78,8%, "el único caso ambiguo" |
| `$patio` en `actulocproccess` | el patio de la unidad y **lo que el operario eligió en el formulario** | "las unidades con patio sucio no se pueden mover" — y se mueven igual |

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

### 6. Migraciones versionadas — aprobado, sin construir

Variante de fuente única. Migración 1 = esquema completo; migración 2 = arreglo
explícito del `unidad_id` que falta en `inspeccion_despacho_regla` en Railway.
Correr `estado` justo después de la 1 es **obligatorio** en el runbook.

`PRAGMA foreign_keys=ON` queda **explícitamente afuera**.

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
