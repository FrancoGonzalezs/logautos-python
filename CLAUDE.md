# REGLA — estado del proyecto

**2026-08-27.** Reescritura en Python (Flask + réplica SQLite) del sistema
Logautos (CodeIgniter 3 + MariaDB, `claude.logautos.cl`). Los dos sistemas
corren **en paralelo**: el legado sigue siendo el que factura y el que usa la
gente. REGLA no lo reemplaza todavía, lo acompaña.

Eso manda sobre casi todas las decisiones raras que siguen. Cuando dos sistemas
escriben sobre el mismo dato, **coincidir vale más que tener razón**: cada
diferencia que aparece en la reconciliación es ruido que alguien tiene que
explicar, y el ruido se lleva puesta la señal.

---

## Las tres reglas que no se negocian

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
emitidas.

Un cliente sin tarifa **se niega y avisa**; no cae a la genérica en silencio.

### 3. Los dos relojes no se concilian

Ni el pull ni el push parsean fechas cruzando sistemas. La marca de agua la pone
el legado con su reloj; `updated_at` lo pone el receptor con el suyo. El desfase
del dump **no es fijo**: es conversión de zona horaria y cambia con el horario
de verano (4 h o 3 h según el mes).

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
- **El origen no viaja.** `newcalle`/`newestado`/`newpatio` los resuelve el
  endpoint leyendo la fila dentro de su transacción. Mandarlos sería mandar lo
  que REGLA *cree* que el legado tenía, con hasta una vuelta de sync de atraso,
  y quedaría escrito en su historial como un hecho.
- **Las columnas de `registros` están invertidas** y el prefijo miente:
  `accion`/`estado`/`patio` son el **DESTINO**, `newcalle`/`newestado`/`newpatio`
  el **ORIGEN**. Verificado a mano sobre la fila 305637.

---

## Pendientes, en orden

### 1. STOCK sale de las exclusiones — decidido, sin implementar

`SIN_CALLE` excluye cuatro estados. La razón que quedó escrita para `STOCK` era
"la calle de patio es el dato y REGLA no la pide", y **esa razón no se
sostiene**: el movilizador sí sabe patio y calle — está parado en el patio, él
lo estacionó. El problema no es que el dato no exista, es que REGLA no lo
pregunta, y eso se arregla preguntándolo.

STOCK es además el estado de mayor volumen de los cuatro, así que es el que más
historial le falta al legado hoy.

> **El código todavía tiene `STOCK` en `SIN_CALLE`** y no hay ningún campo de
> calle ni de patio en las pantallas. Esto es trabajo por hacer, no estado
> actual.

Los otros tres siguen excluidos por razones distintas, que no cambian: `DYP`
(depende del proveedor asignado, que REGLA no elige), `SALIDA DYP` (8
movimientos en 6 meses, insuficiente para fijar una traducción) y `DESPACHADO`
(el camino del legado además manda correo y crea OT; empujar solo la columna
deja un despacho a medias, que es peor que no empujarlo).

### 2. El patio destino quedó vacío — hay que revisarlo

En la fila 305637, verificada a mano:

```
accion='Cc'   estado='CONTROL DE CALIDAD DESPACHO'   patio=''
newcalle='C'  newestado='STOCK'                      newpatio='PATIO 5'
```

La fila dice de dónde salió pero no adónde llegó. Como la unidad **no cambia de
patio** en estos movimientos, lo correcto sería repetir el origen: vacío no es
lo mismo que igual, y un reporte que agrupe el historial por `patio` no ve estas
filas.

**No se arregla desde Python** — mandar el patio desde acá es exactamente lo que
decidimos no hacer. El endpoint ya tiene el valor a mano dentro de su
transacción. Es una línea del PHP y la decide Franco.

### 3. El push de PDI

`ENTIDADES` no tiene PDI y `guardar_pdi` no encola: hoy no llega nada al legado,
ni `fecha_pdi` ni el estado. Necesita **las dos mitades a la vez**:

- Python: la entidad y el enganche (y acá el movimiento **sí** se empuja).
- PHP: ampliar la lista blanca de `Api_regla.php` con `fecha_pdi`, `mes_pdi`,
  `mespdinombre` y las cuatro automáticas. **Lo que no está en la lista se
  ignora en silencio**, así que cablear solo el lado Python da 200 y cero efecto
  — el tipo de error que se ve semanas después.

Entra además la OT automática de PDI y el descuento de stock de combustible (la
carrera de la compuerta es un límite conocido: la ventana de REGLA es de 300 s
contra el mismo-request del legado).

### 4. `registros` entra al pull — DESPUÉS del push de PDI

Hoy `registros` se lee solo del dump, y por eso **no se puede leer desde
producción el contenido de una fila recién escrita**: el punto 2 se verificó a
mano porque no había otra forma. Sirve para eso y para que la reconciliación
deje de depender del dump.

Va después de PDI a propósito: PDI es el que le falta al circuito, y meter una
entidad nueva al pull mientras el push todavía crece agrega superficie sin
cerrar nada.

### 5. Migraciones versionadas — aprobado, sin construir

Variante de fuente única. Migración 1 = esquema completo; migración 2 = arreglo
explícito del `unidad_id` que falta en `inspeccion_despacho_regla` en Railway.
Correr `estado` justo después de la 1 es **obligatorio** en el runbook.

`PRAGMA foreign_keys=ON` queda **explícitamente afuera**.

### 6. Decisiones que esperan datos, no código

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
escribe como archivo aparte en `scripts/`. No hay `php` en la máquina, así que
nunca se lintea local: `php -l` antes de subir.

**El legado devuelve 200 con cuerpo vacío en vez de 404** (`404_override`), así
que el cliente exige `ok: true` explícito y no se conforma con un 2xx.

Las siete suites, ninguna escribe en producción:

```bash
for s in estados reconciliacion ficha_estados motivo_desvio push facturacion; do python scripts/probar_$s.py; done
python scripts/probar_pull.py
python scripts/verificar_push_produccion.py   # 5 sondas contra producción, ninguna escribe
```

El README tiene el detalle largo (esquema, hallazgos sobre el dato real,
decisiones de cada módulo). Este archivo es el estado; el README es el archivo.
