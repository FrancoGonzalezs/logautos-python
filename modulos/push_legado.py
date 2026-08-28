"""
modulos/push_legado.py -- escribe hacia claude.logautos.cl lo que REGLA hace
desde Python. Es la otra mitad de `sync_legado.py`: aquel trae, este devuelve.

Primera entidad: **IT** (`it_regla` -> UPDATE de `newstocks_cidef`).

La arquitectura es la de `push_talca.py` (taller-inventario), adaptada. Lo que
sigue son las decisiones que condicionan este archivo, cada una con la
evidencia que la sostiene.


Por que IT primero y no movimientos
===================================

La candidata natural era `movimientos_regla` -> `registros`, por ser la tabla
mas central del flujo. Mirando el PHP que la recibe, no es la mas simple: es
la mas peligrosa. Tres hallazgos, en orden de gravedad.

1. LOS NOMBRES DE COLUMNA DE `registros` ESTAN INVERTIDOS.
   `accion`, `estado` y `patio` son el DESTINO del movimiento. `newcalle`,
   `newestado` y `newpatio` son el ORIGEN. El prefijo `new` miente.

   En `Nota.php:15236` los tres `new*` se leen con `getcallebyid()` /
   `getestadobyid()` / `getpatiobyid()` ANTES de que `actudat()` escriba el
   estado nuevo, asi que guardan lo que la unidad TENIA. En `Nota.php:20893`
   se ve sin ninguna ambiguedad: escribe la cadena literal `'FUNCION FR'` en
   las tres columnas `new*`, o sea son el casillero de procedencia.

   Los datos coinciden: la transicion mas frecuente de la replica es
   `estado='DESPACHADO'` con `newestado='ZONA DE DESPACHO'`, 27.389 filas. Al
   reves no tendria sentido.

   Escribirlo al reves no rompe nada visible. Corrompe en silencio el
   historial del legado, que es el dato del que salen sus reportes.

2. UN MOVIMIENTO NO ES UN INSERT, SON DOS ESCRITURAS.
   Cada `registromov()` viene pegado a un `actudat()` que actualiza `calle`,
   `despachado`, `patio` y `updated_by` en `newstocks_cidef`. El escenario de
   conflicto simple que promete una tabla append-only cubre solo la mitad; la
   otra mitad es justo la parte conflictiva.

   Y el `calle` que habria que mandar no lo tenemos: sale de cadenas de
   `if/else` de cientos de lineas que dependen de cliente, motonave,
   `fecha_pdi` y `estado_carflex`. El vocabulario real esta sucio -- 'A', 'B',
   'ZR', 'IT', 'Zd', 'ZD', 'Cc', 'CC', 'Lavando', 'Cmp3', 'BOTON'. Traducirlo
   es el trabajo; el push es la parte facil.

3. PDI TAMPOCO ES SIMPLE: FACTURA.
   `Pedido.php:9285` (`elseif($calle=='Pdi')`) ademas de actualizar 18
   columnas y meter la fila en `registros`, calcula precio (`$precio_pdi =
   49000`, UF del mes, IVA, margen) y crea una OT con `addNewUser($otInfo)`.
   Empujar la PDI sin eso deja una PDI hecha y no cobrada.

   El comentario de `pdi_regla` sobre el mapeo 1:1 es cierto para las
   columnas. Las columnas no son todo lo que hace la PDI del legado.

IT, en cambio, es `Pedido.php:9219` y es UN SOLO UPDATE a UNA fila de UNA
tabla, seis columnas: `estado_it`, `observacion_it`, `despachado`, `calle`,
`updated_by`, `updated_at`. No inserta en `registros` -- que es exactamente la
divergencia #1 que `taller.py` ya habia documentado y decidido no imitar -- y
no factura.

Dos cosas mas lo hacen el mejor banco de pruebas:

  * `estado_it` esta en NULL en las 71.546 filas de la replica. La pantalla IT
    del legado nunca se uso. El primer push contra produccion escribe sobre un
    campo que nadie toco jamas: el peor caso de un error de mapeo es una
    columna vacia con un valor raro, no un dato bueno pisado.

  * Se identifica por `id`, la misma clave que el pull decidio que es la unica
    estable (61.447 VIN distintos para 71.546 filas). El PHP hace
    `actupdi($vin, ...)` -- actualiza POR VIN, y con 14% de VIN duplicados le
    pega a varias filas de una. Eso no se replica.

Movimientos viene despues, y su trabajo de verdad es traducir `calle`. PDI al
final, cuando se decida que hacer con la OT de facturacion.


Las cinco decisiones del mecanismo
==================================

1. LA ATOMICIDAD ESTA EN EL FLAG, NO EN EL ORDEN DE LAS LLAMADAS.
   `encolar_push()` se llama DENTRO de la transaccion del endpoint (antes del
   commit), junto con `SET push_pendiente = 1` sobre la unidad. Las tres
   escrituras -- la fila de `it_regla`, el flag y la entrada de cola -- son
   atomicas. El pull comprueba `push_pendiente` antes de sobrescribir, asi que
   no existe ventana donde el dato esta en Python y el pull puede pisarlo con
   la version vieja del legado.

2. EL PUSH ES FIRE-AND-FORGET: NO BLOQUEA AL USUARIO.
   Despues del commit, `disparar_push()` lanza un hilo que intenta el PUT de
   inmediato. Si falla, la entrada de cola ya existe y `procesar_pendientes()`
   la retoma con backoff. El endpoint redirige sin esperar.

3. LOCKING OPTIMISTA: SI EL LEGADO TIENE UN CAMBIO MAS RECIENTE, GANA EL
   LEGADO. Cada PUT manda `legado_updated_at_conocido`. Si el legado tiene un
   `updated_at` posterior responde 409, y aca se registra el conflicto sin
   sobrescribir. `push_pendiente` vuelve a 0 y el dato correcto llega en la
   proxima vuelta del pull.

4. IDEMPOTENCIA TAMBIEN EN EL PUT, Y NO ES DE ADORNO.
   Un PUT con locking optimista NO es idempotente por si solo, y el modo de
   fallar es traicionero: si el PUT llega, el legado lo aplica y la respuesta
   se pierde en la red, el reintento manda el MISMO
   `legado_updated_at_conocido` -- pero el legado ya avanzo su `updated_at`,
   porque lo avanzamos nosotros. El legado ve un cambio mas nuevo que el que
   conocemos y responde 409. Resultado: un conflicto inventado contra nosotros
   mismos, y un dato correcto marcado para revision manual.

   La `idempotency_key` lo cierra: el receptor la busca antes de comparar
   timestamps y, si ya la proceso, devuelve la respuesta de entonces.

5. AUTENTICACION Y USER-AGENT, IGUAL QUE EL PULL.
   Header `X-API-Key` contra el mismo `Api_regla.php`, y el mismo User-Agent:
   el hosting CIERRA EL SOCKET cuando dice "python-requests" (ver la nota 4
   del encabezado de `sync_legado.py`). Del lado de Python eso llega como
   RemoteDisconnected y parece un problema de red.


LIMITACION CONOCIDA Y ACEPTADA: el locking falla abierto
========================================================

La replica se armo de un dump exportado con otra zona horaria: sus timestamps
estan ADELANTE de los del servidor vivo -- 4 horas en horario normal y 3 en
horario de verano, porque es una conversion de zona horaria y no un offset
fijo (medido contra produccion el 2026-08-26; ver el encabezado de
sync_legado.py). En toda fila que el pull todavia no haya tocado, el
`legado_updated_at_conocido` que mandamos viene del futuro respecto del reloj
real. El legado compara, ve que conocemos una version mas nueva que la suya,
NO detecta conflicto y deja sobrescribir.

Que el desfase varie con el horario de verano descarta de entrada el arreglo
que parecia obvio -- restarle 4 horas al valor guardado --: estaria mal medio
ano, y mal en silencio.

O sea: en esas filas el locking optimista no protege nada.

Se decidio dejarlo asi por ahora, y para IT es tolerable por lo mismo que lo
hace buen banco de pruebas -- `estado_it` esta vacio en las 71.546 filas, no
hay nada que pisar. `despachado` si es un dato vivo, pero moverlo es
justamente lo que el usuario pidio al guardar el IT.

**DEJA DE SER TOLERABLE CUANDO ENTRE MOVIMIENTOS.** Antes de habilitar la
segunda entidad hay que resolverlo, y la salida barata no necesita codigo: una
reconciliacion completa (`python -m modulos.sync_legado unidades --desde ""`,
358 paginas de 200) deja todos los `updated_at` con el reloj del legado. La
alternativa con codigo es marcar que filas vinieron de un pull y rechazar el
encolado del resto.


Sobre la conexion a la base
===========================

`encolar_push()` recibe la conexion del endpoint que lo llama. Las funciones
de fondo abren la suya con `conectar_db()` de core.py: corren en hilos sin
contexto de Flask, asi que no pueden usar `get_db()`. Este modulo no importa
app.py.


Como se corre
=============

    python -m modulos.push_legado estado          # que hay en la cola
    python -m modulos.push_legado pendientes      # una vuelta de reintentos
    python -m modulos.push_legado pendientes --simulado URL
    python -m modulos.push_legado una <id_cola>   # una entrada puntual

El hilo de fondo NO esta enganchado todavia. Se engancha a `procesar_pendientes`
al final de cada vuelta del pull recien cuando una corrida manual salga bien.
"""

import argparse
import datetime
import json
import logging
import os
import sys
import threading
import uuid

from core import conectar_db, exigir_destino_local
from modulos import correo

try:
    import requests as _requests
except ImportError:                              # pragma: no cover
    _requests = None

# El pull ya resolvio el User-Agent y la base. Se importan de ahi y no se
# vuelven a declarar: si el hosting cambia de criterio, se arregla en un lugar.
from modulos.sync_legado import BASE_URL_DEFECTO, USER_AGENT  # noqa: E402

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------

# Mas corto que el timeout del pull (30 s) a proposito: el push corre en un
# hilo por cada guardado, y un host lento no debe dejar hilos colgados.
PUSH_TIMEOUT = float(os.environ.get("LEGADO_PUSH_TIMEOUT") or 8.0)

# Backoff exponencial en segundos. Indice 0 = primer reintento. Pasado el
# ultimo se repite el ultimo valor.
PUSH_BACKOFF = (60, 300, 900, 3600, 21600, 86400)   # 1m 5m 15m 1h 6h 1d


def push_activo():
    """Si el disparo automatico esta habilitado. Por defecto NO.

    Apagado, el endpoint sigue encolando -- que es una escritura local y no le
    manda nada a nadie -- pero no sale a la red. La cola se vacia a mano con
        python -m modulos.push_legado pendientes

    Es la misma cautela con la que se probo el pull, aplicada a la direccion
    que escribe: el primer trafico de escritura contra produccion tiene que ser
    una corrida que alguien mira, no un hilo que se disparo solo. Se prende con
    PUSH_LEGADO_ACTIVO=1 recien despues de que esa corrida salga bien.

    Se lee en cada llamada y no al importar: asi se puede apagar en produccion
    sin redesplegar el codigo, solo reiniciando con la variable en 0."""
    return (os.environ.get("PUSH_LEGADO_ACTIVO") or "").strip() in ("1", "true", "True")


def now_iso():
    """Hora LOCAL en ISO, al segundo.

    Local y no UTC porque es con lo que se compara `proximo_intento`. En UTC,
    el backoff se llevaria puesto el huso de Chile: cuatro horas extra en cada
    reintento, sin mas sintoma que la lentitud."""
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Las entidades
# ---------------------------------------------------------------------------
#
# `tabla_origen`  de donde salio el dato (nuestra tabla _regla).
# `tabla_espejo`  donde vive `push_pendiente` y el `updated_at` del legado.
#                 Para IT las dos cosas estan en la unidad, no en it_regla:
#                 lo que se empuja es un UPDATE de la unidad.
# `ruta`          el segmento de la URL: PUT /api_regla/<ruta>/<legado_id>.

ENTIDADES = {
    # Los movimientos. `operacion` es 'crear': un movimiento INSERTA una fila
    # en `registros` del legado y ademas actualiza la unidad -- las dos cosas
    # en la transaccion del endpoint, que es lo que el legado original no hace.
    "movimientos": {
        "tabla_origen": "movimientos_regla",
        "tabla_espejo": "newstocks_cidef",
        "ruta": "movimientos",
    },
    "it": {
        "tabla_origen": "it_regla",
        "tabla_espejo": "newstocks_cidef",
        "ruta": "unidades",
    },
    # La PDI. Misma forma que el IT -- PUT sobre la unidad -- porque escribe
    # las mismas columnas de la misma tabla; lo que cambia es CUALES.
    #
    # El MOVIMIENTO de la PDI se empuja aparte y por la entidad `movimientos`,
    # y ahi `empuja_movimiento` va en True, al reves que el IT. El motivo esta
    # contado en `movimientos.registrar()`: el bloque `It` del legado llama a
    # `registromov()` cero veces y el de la PDI lo llama DOS. Empujar el del IT
    # le meteria al historial ajeno una fila que su propia pantalla no genera;
    # no empujar el de la PDI le sacaria una que si genera.
    "pdi": {
        "tabla_origen": "pdi_regla",
        "tabla_espejo": "newstocks_cidef",
        "ruta": "unidades",
    },
    # Las DOS OT de la PDI, en una sola entrada de cola. Una entrada y no dos
    # porque el endpoint las crea en una transaccion: media PDI cobrada es peor
    # que ninguna, y dos entradas independientes podrian dejar exactamente eso.
    #
    # `depende_de` apunta a la entrada del MOVIMIENTO de la PDI. Ver la nota de
    # esa columna en `asegurar_tablas`: la entrada existe desde la misma
    # transaccion que la PDI -- sobrevive a que el proceso muera -- y no se
    # intenta hasta que el movimiento este confirmado.
    "ot_pdi": {
        "tabla_origen": "pdi_regla",
        "tabla_espejo": "newstocks_cidef",
        "ruta": "pdi/{id}/ot",
    },
    # El descuento de combustible. Es una RESTA y no una asignacion, por eso no
    # va por `actualizar`: ver el bloque C de scripts/Api_regla_pdi.php.
    "stock_consumibles": {
        "tabla_origen": "pdi_regla",
        "tabla_espejo": "stock_consumibles",
        "ruta": "stock_consumibles/{id}/descontar",
    },
}

# Las CATORCE columnas de la PDI, con el nombre que tienen del otro lado.
# Salen del array `$pdi` de `Pedido.php`, dentro de `elseif($calle=='Pdi')`.
#
# La lista blanca de `Api_regla.php` tiene que tenerlas a las catorce o se
# IGNORAN EN SILENCIO: 200, cero efecto, cola resuelta sin error. Ver
# scripts/Api_regla_pdi.php, bloque B -- y el caso 0 de probar_circulo.py, que
# corre el circuito contra las dos listas blancas justamente para que ese
# silencio se pueda ver.
#
# `calle` y `despachado` NO estan: para la PDI las escribe el endpoint de
# MOVIMIENTOS, no este. Mandarlas por los dos lados seria pisarlas dos veces
# con el mismo valor en la misma vuelta, y la segunda escritura avanzaria el
# `updated_at` contra el que el primer push acaba de hacer locking.
CAMPOS_PDI = (
    "fecha_pdi", "mes_pdi", "mespdinombre",
    "estadostock", "ubicacion", "tipo_combu",
    "bateria", "scanner", "a_c", "ob_mecanica",
    # Las cuatro automaticas: el PHP las llena con date('Y-m-d'), la fecha del
    # dia, sin preguntarle nada a nadie. Son fechas, no booleanos.
    "aceite_coco", "sistema_audio", "adblue", "aceite_diferencial",
)

# Lo que el PHP pone fijo en esa rama.
ESTADOSTOCK_PDI = "STOCK CON PDI"

# `ubicacion` VACIA, y es la unica columna del push que BORRA en vez de
# agregar. No es un descuido del legado que estemos copiando por copiar: su
# rama tiene un `$f = '1'` que no se usa -- codigo muerto -- y el array escribe
# `$numero`, que la pantalla de PDI postea vacio. Verificado sobre el dato: las
# 21 unidades con calle 'Pdi' de la replica tienen la ubicacion en blanco, sin
# excepcion.
#
# Se replica porque coincidir vale mas que tener razon: una ubicacion que alla
# se borra y aca no es exactamente el ruido que despues hay que explicar. Pero
# por ser destructiva, el primer push real se verifica a mano -- ver el final
# de scripts/Api_regla_pdi.php.
UBICACION_PDI = ""

# Los cuatro campos que viajan en el IT, con el nombre que tienen del OTRO
# lado. Salen de `Pedido.php:9219`, del array `$it`:
#
#     'estado_it', 'observacion_it', 'despachado', 'calle',
#     'updated_by', 'updated_at'
#
# `updated_at` no viaja: lo pone el receptor con SU reloj, por lo mismo que la
# marca de agua del pull la pone el legado (nota 3 de sync_legado.py). Dos
# relojes que conciliar es justo lo que no queremos.
#
# `updated_by` SI viaja, y es legitimo: `id_actual()` devuelve el `userId` de
# `tbl_users`, que es la tabla de usuarios del propio legado traida en el
# dump. Es el mismo id que el PHP guarda como `$this->vendorId`.
CAMPOS_IT = ("estado_it", "observacion_it", "despachado", "calle")

# OJO CON LA CAJA. La vista `actualizar_it.php:29` postea
# `<input type="hidden" name="calle" value="IT">`, pero `Pedido.php:8415` hace
#
#     $calle = ucwords(strtolower($this->input->post('calle')));
#
# antes de comparar y de guardar. O sea el valor que termina en la columna es
# **'It'**, no 'IT'. Las 9 filas de la replica que tienen calle='IT' vienen del
# check list, que lo escribe literal.
#
# MySQL compara sin distinguir mayusculas y no notaria la diferencia; PHP si.
# Es la misma trampa que push_talca.py documento para los estados de Talca.
CALLE_IT = "It"


# ---------------------------------------------------------------------------
# La traduccion de `calle` para los movimientos
# ---------------------------------------------------------------------------
#
# El legado guarda en `registros.accion` y en `newstocks_cidef.calle` un
# vocabulario propio de 88 valores que no es el catalogo de estados de REGLA.
# Esta tabla lo traduce, y NO sale de leer las cadenas de if/else de
# `actulocproccess` -- salen de contar lo que el sistema REALMENTE escribio.
#
# SE MIRARON SOLO LOS ULTIMOS 6 MESES, y eso no es un detalle. El historico
# completo mezcla años de versiones distintas del PHP y da mayorias falsas: con
# los 296.529 movimientos, INGRESO A TALLER daba 'IT' con 69,9% y 'It' con 30%,
# como si convivieran. Con los recientes, 'IT' es el 97,8% -- una es la forma
# vieja y la otra la de hoy. Misma leccion que el precio de la PDI, que parecia
# variable hasta que se acoto al periodo del codigo actual.
#
# El porcentaje de cada linea es cuanto manda esa calle sobre ese estado en los
# ultimos 6 meses. Se anota para que la proxima revision sepa cual estaba
# floja.
CALLE_POR_ESTADO = {
    "ZONA DE RECEPCION":              "ZR",                   # 99,7%
    "INGRESO A TALLER":               "IT",                   # 97,8%
    "ZONA DE LAVADO":                 "Lavando",              # 97,3%
    "CONTROL DE CALIDAD DESPACHO":    "Cc",                   # 94,2%
    "EN ESPERA DYP CONSOLIDADO":      "PDI",                  # 86,6%
    "FR - MECANICA":                  "Cmp3",                 # 86,4%
    "ZONA DE DESPACHO":               "Zd",                   # 84,7%
    "EN ESPERA DE ASIGNACION DYP":    "IT",                   # 100%
    "EN ESPERA DE CHECK LIST INGRESO": "A",                   # 100%
    "EN ESPERA CHECK LIST MECANICA":  "A",                    # 100%
    "FALLA MECANICA":                 "Falla Mecanica",       # 100%
    "INSPECCION MECANICA DESPACHO":   "It",                   # 100%
    "SALIDA INSPECCION MECANICA":     "REVISION CLIENTE",     # 100%
    "SERVICIOS GENERALES":            "Servicios Generales",  # 100%
}


# ---------------------------------------------------------------------------
# La traduccion de `patio`, que va CON la de calle y no aparte
# ---------------------------------------------------------------------------
#
# EL PATIO ES FUNCION DEL ESTADO DESTINO, NO DEL ORIGEN. Esto contradice lo que
# estaba escrito acá y en CLAUDE.md hasta el 2026-08-27 -- "como la unidad no
# cambia de patio en estos movimientos, lo correcto seria repetir el origen" --
# y la contradiccion es medible, no de criterio:
#
#     accion='Cc' en 6 meses: 2.948 filas vienen de PATIO 2 y 295 de PATIO 1,
#     y las 3.247 van a PATIO 1. NINGUNA repite el origen.
#
# O sea que en la fila 305637 repetir el origen habria escrito 'PATIO 5', que
# es justo el valor que el legado nunca escribe para 'Cc'. La unidad SI cambia
# de patio: ir a control de calidad es ir al patio 1.
#
# Cada etapa vive en un patio fijo y el movimiento la lleva ahi. Por eso el
# patio sale de la misma clave que la calle -- el estado destino -- y no de
# mirar de donde venia.
#
# POR ESTADO Y NO POR CALLE, que fue el segundo intento y era peor. Medido por
# calle, 'A' daba PATIO 2 al 78,8% y parecia el unico caso ambiguo de los doce.
# No lo era: 'A' es a la vez calle de estacionamiento y calle de las dos
# esperas de check list, y el 21% era el estacionamiento contaminando la
# medicion. Separados por estado, los dos que usan 'A' dan PATIO 2 al 100%.
# Misma leccion que el historico contra los 6 meses: la mayoria falsa aparece
# cuando se agrega sobre una clave que mezcla dos cosas.
#
# El porcentaje es sobre los movimientos de ESE estado con ESA calle en los
# ultimos 6 meses, contando los vacios en el denominador.
PATIO_POR_ESTADO = {
    "ZONA DE RECEPCION":              "PATIO 1",   # 100%   n=4.314
    # La mas floja de las catorce, y la unica por debajo de 95%. Las 196 filas
    # de PATIO 1 son la pantalla de segundo lavado, que fuerza PATIO 1 / IT.
    # Hoy da igual: el IT pasa `empuja_movimiento=False` y REGLA no empuja
    # ningun movimiento a este estado. Si eso cambia, remedir primero.
    "INGRESO A TALLER":               "PATIO 2",   # 93,6%  n=3.044
    "ZONA DE LAVADO":                 "PATIO 2",   # 99,8%  n=3.495
    "CONTROL DE CALIDAD DESPACHO":    "PATIO 1",   # 99,0%  n=3.282
    "FR - MECANICA":                  "PATIO 2",   # 98,9%  n=809
    "ZONA DE DESPACHO":               "PATIO 1",   # 99,9%  n=3.679
    "EN ESPERA DE ASIGNACION DYP":    "PATIO 2",   # 100%   n=3.203
    "EN ESPERA DE CHECK LIST INGRESO": "PATIO 2",  # 100%   n=877
    "EN ESPERA CHECK LIST MECANICA":  "PATIO 2",   # 100%   n=803
    "FALLA MECANICA":                 "PATIO 2",   # 100%   n=360
    "INSPECCION MECANICA DESPACHO":   "PATIO 1",   # 100%   n=85
    "SALIDA INSPECCION MECANICA":     "PATIO 2",   # 100%   n=809
    "SERVICIOS GENERALES":            "PATIO 2",   # 100%   n=130

    # EN ESPERA DYP CONSOLIDADO -- la PDI -- NO ESTA ACA, Y ES A PROPOSITO.
    #
    # El legado deja el patio VACIO en las 3.241 filas de PDI de los ultimos 6
    # meses. Las 3.241, sin una sola excepcion. No es que se le escape: el
    # bloque de la PDI arranca con `$patiopdi = ' '` y nunca lo usa, asi que el
    # `$mov` de esa rama sale sin patio.
    #
    # PARA CUANDO ENTRE LA ENTIDAD PDI (pendiente 3): hay que mandarlo VACIO.
    # Es la unica forma de coincidir, y coincidir vale mas que tener razon.
    # Ponerle un patio "correcto" seria producir una forma de fila que el
    # sistema viejo no produce nunca -- exactamente el problema que esta tabla
    # viene a cerrar, pero al reves.
    #
    # `patio_para()` devuelve None para este estado, y quien encola manda el
    # campo vacio. Ver la nota de `encolar_movimiento`.
}


def patio_para(estado):
    """El patio destino que corresponde a ese estado, o None si el legado no
    escribe patio para ese estado (hoy, solo la PDI). Ver PATIO_POR_ESTADO.

    None NO significa "no se pudo traducir": significa "el legado lo deja
    vacio y nosotros tambien". Un estado que no se puede traducir no llega
    hasta aca -- lo frena `calle_para()` con SIN_CALLE."""
    from modulos.movimientos import normalizar_estado
    return PATIO_POR_ESTADO.get(normalizar_estado(estado))


# Estados a los que REGLA NO puede traducir la calle, y por que. Un movimiento
# hacia uno de estos NO se encola: es preferible que el legado no se entere a
# que se entere de una calle inventada, porque la calle es la UBICACION FISICA
# y de ella salen los reportes de patio.
#
# No es una limitacion del push sino un dato que REGLA no pide todavia.
SIN_CALLE = {
    # STOCK SALIO DE ACA EL 2026-08-27, y el orden en que salio es el punto.
    #
    # Estuvo excluido con esta razon: "se alcanza estacionando en una calle
    # concreta -- A, B, C, D, F -- y ninguna manda: A 25%, B 21%, C 17%,
    # F 15%". El diagnostico era correcto y la conclusion no. Que ninguna calle
    # mande no significa que el dato no exista: significa que no se puede
    # DEDUCIR. El movilizador lo sabe -- esta parado en el patio, el la
    # estaciono -- y lo que faltaba era preguntarlo.
    #
    # Se saco DESPUES de que la pantalla tuviera el campo, no antes. Al reves
    # habria mandado la calle de la mayoria, 25% de acierto, escrita en el
    # historial del legado como un hecho. Es el unico estado cuya calle no sale
    # de CALLE_POR_ESTADO sino del formulario, y por eso `encolar_movimiento`
    # acepta `calle` y `patio` explicitos.
    #
    # Que quede el hueco anotado y no borrado: el razonamiento que lo mantuvo
    # afuera un mes es el mismo que mantiene afuera a los otros tres, y ahi
    # sigue siendo valido.
    # DYP: OJO, EL MOTIVO NO ES QUE FALTE LA TRADUCCION. Decia "depende del
    # proveedor DYP asignado, que REGLA no elige" y era falso, remedido el
    # 2026-08-27: la calle es DETERMINISTA. El bloque `if($calle == 'Dyp')` de
    # Pedido.php:8577 descarta lo que el usuario eligio y fuerza a mano
    #
    #     patio 'PATIO 2' / calle 'ENTREGADO DYP' / estado 'DYP' / ubicacion '1'
    #
    # y el dato lo confirma: 189 de 204 movimientos a DYP en 6 meses son
    # 'ENTREGADO DYP' en PATIO 2, 92,6%. Eso es MAS que diez de las catorce
    # traducciones que ya estan en CALLE_POR_ESTADO -- 'ZONA DE DESPACHO' entro
    # con 84,7%. La medicion vieja (B 48%, ENTREGADO DYP 38%) salio de contar
    # la columna `calle` de las unidades, no el destino de los movimientos.
    #
    # QUEDA AFUERA POR LO MISMO QUE 'DESPACHADO': esa misma rama, si el UPDATE
    # sale bien, MANDA UN CORREO AL CLIENTE con la patente de la unidad
    # entregada al proveedor. Empujar solo la columna deja media entrega hecha
    # -- el proveedor figura con la unidad y nadie le aviso a nadie -- que es
    # peor que no empujarla.
    #
    # Por eso no se resuelve agregando la linea a CALLE_POR_ESTADO. Se resuelve
    # cuando REGLA migre "Actualizar DYP" entera, correo incluido.
    "DYP": "el camino del legado tambien manda correo al cliente con la patente",
    # 8 movimientos en 6 meses repartidos en tres formas. Muy poco para fijar
    # una traduccion; se resuelve cuando haya volumen.
    "SALIDA DYP": "solo 8 movimientos recientes, insuficiente para decidir",
    # El despacho hace MUCHO mas que mover el estado: fechas, correo al cliente
    # con la guia firmada, y OT. Empujar solo la columna dejaria un despacho a
    # medias, que es peor que no empujarlo.
    "DESPACHADO": "el despacho del legado tambien manda correo y crea OT",
}


def calle_para(estado):
    """La calle que hay que mandarle al legado para ese estado destino, o None
    si REGLA no puede traducirla. Ver SIN_CALLE."""
    from modulos.movimientos import normalizar_estado
    return CALLE_POR_ESTADO.get(normalizar_estado(estado))


# ---------------------------------------------------------------------------
# Esquema
# ---------------------------------------------------------------------------

def asegurar_tablas(db):
    """Crea la cola, la tabla de conflictos y la columna `push_pendiente`.

    Idempotente: se puede llamar en cada request. `push_pendiente` se agrega
    por ALTER y no en un CREATE porque `newstocks_cidef` la crea el importador
    del dump, que no la conoce -- y sobrevive asi a una reimportacion."""
    db.executescript("""
        -- Cola de reintentos del push Python -> legado.
        --
        -- La entrada se escribe dentro de la misma transaccion que el dato
        -- local, antes del commit, asi que la cola existe aunque el proceso
        -- muera entre el commit y el primer intento.
        --
        -- resuelto_en vacio = pendiente. Cuenta como resuelto tanto el push
        -- exitoso como el conflicto (409): en los dos casos push_pendiente
        -- vuelve a 0 y no queda nada que reintentar.
        CREATE TABLE IF NOT EXISTS sync_push_pendientes (
            id              INTEGER PRIMARY KEY,
            entidad         TEXT    NOT NULL,              -- 'it'
            python_id       INTEGER NOT NULL,              -- id en la tabla _regla
            legado_id       INTEGER,                       -- NULL si operacion='crear'
            operacion       TEXT    NOT NULL,              -- 'crear' | 'actualizar'
            campos_json     TEXT    NOT NULL DEFAULT '{}',
            -- El updated_at que el legado tenia cuando armamos este push.
            -- Es todo el locking optimista. Ver la limitacion del encabezado:
            -- hoy falla abierto en las filas que el pull no toco.
            legado_updated_at_conocido TEXT NOT NULL DEFAULT '',
            -- UUID por entrada. Viaja en cada intento -- tambien en el PUT,
            -- ver la decision 4 del encabezado.
            idempotency_key TEXT    NOT NULL DEFAULT '',
            -- Marca que el payload necesita un dato que NO se guarda aca y se
            -- resuelve al momento de pushear.
            --
            -- Para 'it' es siempre 0: el UPDATE se identifica por el id de la
            -- unidad, que ya tenemos, y no necesita nada mas.
            --
            -- Existe porque movimientos lo va a necesitar de verdad: la fila
            -- de `registros` lleva en newcalle/newestado/newpatio el estado
            -- del que la unidad VIENE, que hay que leer del legado recien al
            -- pushear. Guardarlo al encolar seria congelar un valor que puede
            -- haber cambiado entre medio -- el mismo motivo por el que
            -- push_talca.py resuelve `dueno` tarde y no al encolar.
            requiere_unidad INTEGER NOT NULL DEFAULT 0,
            intentos        INTEGER NOT NULL DEFAULT 0,
            proximo_intento TEXT    NOT NULL DEFAULT '',   -- ISO local; vacio = ya
            ultimo_error    TEXT    NOT NULL DEFAULT '',
            creado_en       TEXT    NOT NULL,
            resuelto_en     TEXT    NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS ix_push_pendientes_activos
            ON sync_push_pendientes (resuelto_en, proximo_intento);

        -- Registro permanente de conflictos: el legado tenia un cambio mas
        -- reciente que el nuestro. No se sobrescribio nada. Se guardan LAS DOS
        -- versiones porque la nuestra, si no queda aca, se pierde: el proximo
        -- pull trae la del legado y la pisa.
        CREATE TABLE IF NOT EXISTS sync_conflictos (
            id                    INTEGER PRIMARY KEY,
            entidad               TEXT    NOT NULL,
            python_id             INTEGER NOT NULL,
            legado_id             INTEGER NOT NULL,
            nuestra_version_json  TEXT    NOT NULL DEFAULT '{}',
            version_legado_json   TEXT    NOT NULL DEFAULT '{}',
            legado_updated_at     TEXT    NOT NULL DEFAULT '',
            registrado_en         TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_conflictos_entidad
            ON sync_conflictos (entidad, registrado_en);
    """)

    # `depende_de`: el id de la entrada de cola que tiene que resolverse BIEN
    # antes de intentar esta. Se agrega por ALTER y no en el CREATE porque la
    # tabla ya existe en Railway y en las notebooks.
    #
    # LA USA EL PUSH DE LAS OT DE PDI, y es lo que resuelve "la OT depende de
    # que el movimiento confirme". La alternativa era encolar la OT DESPUES de
    # que el movimiento volviera OK, y eso pierde la OT si el proceso muere en
    # el medio: quedaria una PDI aplicada en el legado y sin cobrar, que es
    # justo lo que este push viene a evitar.
    #
    # Con la dependencia, la entrada EXISTE desde la misma transaccion que la
    # PDI -- sobrevive a la muerte del proceso -- y simplemente no se intenta
    # hasta que la otra este resuelta.
    cols_cola = {r[1] for r in db.execute("PRAGMA table_info(sync_push_pendientes)")}
    if "depende_de" not in cols_cola:
        db.execute("ALTER TABLE sync_push_pendientes ADD COLUMN depende_de INTEGER")

    ya_estan = {r[1] for r in db.execute("PRAGMA table_info(newstocks_cidef)")}
    if "push_pendiente" not in ya_estan:
        db.execute("ALTER TABLE newstocks_cidef "
                   "ADD COLUMN push_pendiente INTEGER NOT NULL DEFAULT 0")


# ---------------------------------------------------------------------------
# Encolado (dentro de la transaccion del endpoint)
# ---------------------------------------------------------------------------

def encolar_push(db, entidad, python_id, legado_id, operacion, campos,
                 legado_updated_at_conocido, requiere_unidad=0,
                 depende_de=None):
    """Escribe la entrada de cola. Devuelve su id, que es lo que despues
    recibe `disparar_push()`.

    DEBE llamarse con la transaccion del endpoint todavia abierta, antes del
    commit, y despues de haber puesto `push_pendiente = 1` en la fila espejo.

    No sale a la red y no captura excepciones: si falla, el error se propaga y
    el commit del endpoint se cae con el, que es lo correcto -- un dato local
    guardado sin su entrada de cola es un dato que nunca llegaria al legado."""
    conf = ENTIDADES[entidad]
    cur = db.execute(
        """
        INSERT INTO sync_push_pendientes
            (entidad, python_id, legado_id, operacion, campos_json,
             legado_updated_at_conocido, idempotency_key, requiere_unidad,
             creado_en, depende_de)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (entidad, python_id, legado_id, operacion,
         json.dumps(campos, ensure_ascii=False),
         legado_updated_at_conocido or "",
         # La key se genera SIEMPRE, tambien para 'actualizar'. Ver la
         # decision 4 del encabezado: sin ella, un reintento despues de una
         # respuesta perdida se choca con su propia escritura y produce un
         # conflicto falso.
         str(uuid.uuid4()),
         1 if requiere_unidad else 0,
         now_iso(), depende_de))
    del conf                                     # solo valida la entidad
    return cur.lastrowid


def encolar_it(db, unidad, it_id, campos):
    """Envoltura para el IT: marca la unidad y encola, en ese orden.

    `unidad` es la fila de newstocks_cidef como la leyo el endpoint -- de ahi
    sale tanto el id del legado como el `updated_at` conocido.

    Se llama con la transaccion abierta. Devuelve el id de la entrada de cola.
    """
    legado_id = unidad["id"]
    db.execute("UPDATE newstocks_cidef SET push_pendiente = 1 WHERE id = ?",
               (legado_id,))
    return encolar_push(
        db, "it",
        python_id=it_id,
        legado_id=legado_id,
        operacion="actualizar",
        campos=campos,
        legado_updated_at_conocido=(unidad["updated_at"] or ""),
        requiere_unidad=0)


def encolar_movimiento(db, unidad, movimiento_id, estado_hacia, usuario,
                       calle=None, patio=None):
    """Encola un movimiento hacia el legado. Devuelve el id de cola, o None si
    ese estado no se puede traducir a una calle.

    DEVOLVER None NO ES UN ERROR: hay estados a los que REGLA no sabe ponerle
    calle -- STOCK, DYP, SALIDA DYP, DESPACHADO. Ver SIN_CALLE. Preferimos que
    el legado no se entere a que se entere de una calle inventada, porque la
    calle es la ubicacion fisica y de ahi salen los reportes de patio. Esos
    movimientos quedan igual en `movimientos_regla` y la reconciliacion los
    cuenta como "REGLA adelante", que es exactamente lo que son.

    Se llama con la transaccion del endpoint abierta, igual que `encolar_it`.

    EL ORIGEN NO VIAJA. `newcalle`/`newestado`/`newpatio` los resuelve el
    endpoint leyendo la fila dentro de su propia transaccion, justo antes del
    UPDATE. Mandarlo desde aca seria mandar lo que REGLA CREE que el legado
    tenia -- con hasta una vuelta de sync de atraso -- y quedaria escrito en el
    historial del legado como si fuera un hecho.

    EL PATIO DESTINO SI VIAJA, DESDE EL 2026-08-27, y sale de PATIO_POR_ESTADO
    -- la misma clave que la calle. Hasta esa fecha no se mandaba, con dos
    razones escritas que resultaron las dos falsas:

    1. "El legado lo decide por rama y REGLA no lo pregunta." No hace falta
       preguntarlo: el patio es funcion del estado destino, porque cada etapa
       vive en un patio fijo. Medido, no supuesto.

    2. "Como la unidad no cambia de patio, lo correcto seria repetir el
       origen." Al reves: para 'Cc' las 3.247 filas del semestre van a PATIO 1
       vengan de donde vengan, y 2.948 venian de PATIO 2. Repetir el origen en
       la fila 305637 habria escrito 'PATIO 5', el unico valor que el legado
       nunca escribe para 'Cc'.

    Y NO HIZO FALTA TOCAR EL PHP. `Api_regla_movimientos.php` ya acepta `patio`
    como campo opcional del cuerpo, lo escribe en `registros.patio` y -- solo
    si viene con valor -- tambien en la unidad. Estaba desde el primer dia.

    El campo se manda SIEMPRE, incluso vacio, y esa es la diferencia con no
    mandarlo: `patio_para()` devuelve None para la PDI, donde el legado deja el
    patio vacio en las 3.241 filas del semestre, y ahi mandamos vacio a
    proposito para coincidir. Vacio elegido y vacio por omision se escriben
    igual en la base pero no son lo mismo, y el que quede documentado es el
    punto.

    `calle` y `patio` explicitos son la excepcion, y hoy la usa solo STOCK: son
    lo que el movilizador ELIGIO en la pantalla, no lo que REGLA dedujo. Ganan
    sobre las tablas porque son de mejor calidad -- una respuesta contra una
    inferencia. Vienen validados contra el catalogo por `ubicacion.valida()`
    antes de llegar aca; esto no los vuelve a validar, y esa es una decision:
    validar en dos lados invita a que los dos catalogos se separen."""
    if calle is None:
        calle = calle_para(estado_hacia)
        patio = patio_para(estado_hacia)
    if calle is None:
        return None

    legado_id = unidad["id"]
    db.execute("UPDATE newstocks_cidef SET push_pendiente = 1 WHERE id = ?",
               (legado_id,))
    campos = {
        # `accion` es la CALLE destino y `estado` el ESTADO destino: en
        # `registros` las columnas sin prefijo son el destino y las `new*` el
        # origen. El prefijo miente; ver el encabezado del endpoint.
        "unidad_id": legado_id,
        "accion": calle,
        "estado": estado_hacia,
        # El patio destino, tambien destino y tambien sin prefijo. Cadena vacia
        # cuando el legado lo deja vacio para ese estado (la PDI): el endpoint
        # trata '' como "no tocar la unidad" y lo escribe igual en `registros`,
        # que es exactamente lo que hace el legado.
        "patio": patio or "",
        "clientemov": unidad["clientecompleto"] or "",
    }
    if usuario:
        try:
            campos["created_by"] = int(usuario)
        except (TypeError, ValueError):
            pass
    return encolar_push(
        db, "movimientos",
        python_id=movimiento_id,
        legado_id=legado_id,
        operacion="crear",
        campos=campos,
        legado_updated_at_conocido=(unidad["updated_at"] or ""),
        requiere_unidad=0)


def campos_pdi(datos, usuario):
    """El payload de la PDI, con los nombres del legado.

    `datos` es lo que junto el formulario de `taller.guardar_pdi`. Las cuatro
    automaticas y las dos fijas se ponen aca y no alla: son del CONTRATO con el
    legado, no del formulario, y tenerlas en un solo lugar evita que la
    pantalla y el push se separen.

    `mes_pdi` lleva la MISMA fecha que `fecha_pdi`. No es un error de
    transcripcion: el legado guarda el mismo valor en dos columnas con dos
    nombres, y coincidir vale mas que tener razon."""
    fecha = (datos.get("fecha_pdi") or "").strip()
    campos = {
        "fecha_pdi": fecha,
        "mes_pdi": fecha,
        "mespdinombre": mes_en_palabras(fecha),
        "estadostock": ESTADOSTOCK_PDI,
        "ubicacion": UBICACION_PDI,
        "tipo_combu": datos.get("tipo_combu") or "",
        "bateria": datos.get("bateria") or "",
        "scanner": datos.get("scanner") or "",
        "a_c": datos.get("a_c") or "",
        "ob_mecanica": datos.get("ob_mecanica") or "",
    }
    # Las cuatro automaticas: `date('Y-m-d')` del PHP, o sea el dia en que se
    # guarda -- NO la fecha de la PDI, que puede ser anterior.
    hoy = datetime.date.today().isoformat()
    for automatica in ("aceite_coco", "sistema_audio", "adblue",
                       "aceite_diferencial"):
        campos[automatica] = hoy
    if usuario:
        try:
            campos["updated_by"] = int(usuario)
        except (TypeError, ValueError):
            pass
    return campos


# El switch de doce casos de `actulocproccess`, que arma "Agosto 2026".
MESES = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
         "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre")


def mes_en_palabras(fecha):
    """'2026-08-27' -> 'Agosto 2026'. Vacio si la fecha no se entiende.

    El PHP lo arma con `date('m')` sobre la fecha del formulario y un switch de
    doce casos. Se replica en vez de usar `strftime('%B')`, que depende del
    locale del proceso: en Railway saldria 'August'."""
    partes = (fecha or "").strip().split("-")
    if len(partes) < 2:
        return ""
    try:
        anio, mes = int(partes[0]), int(partes[1])
    except ValueError:
        return ""
    if not 1 <= mes <= 12:
        return ""
    return "{} {}".format(MESES[mes - 1], anio)


def encolar_pdi(db, unidad, pdi_id, campos):
    """Encola la PDI. Misma forma que `encolar_it`."""
    legado_id = unidad["id"]
    db.execute("UPDATE newstocks_cidef SET push_pendiente = 1 WHERE id = ?",
               (legado_id,))
    return encolar_push(
        db, "pdi",
        python_id=pdi_id,
        legado_id=legado_id,
        operacion="actualizar",
        campos=campos,
        legado_updated_at_conocido=(unidad["updated_at"] or ""),
        requiere_unidad=0)


def encolar_ot_pdi(db, unidad, pdi_id, datos, usuario, depende_de):
    """Encola las DOS OT de la PDI. Una entrada, no dos.

    `depende_de` es la entrada del MOVIMIENTO. Sin ella la OT podria crearse
    para una PDI que despues no entra -- un 409 en el movimiento significa que
    el legado gano y no hay nada que cobrar --, y `orden_trabajo` es
    append-only: la OT de mas no se borra, hay que ir a explicarla.

    Los precios NO viajan. Los recalcula el endpoint, que es lo correcto para
    plata: un precio que viaja es un precio que se puede modificar en transito.
    Que las dos implementaciones tengan que coincidir se verifica aparte, con
    `scripts/probar_precio_ot.py`, contra las 970 OT historicas."""
    campos = {
        "fecha_pdi": (datos.get("fecha_pdi") or "").strip(),
        "tipo_combu": datos.get("tipo_combu") or "",
    }
    if usuario:
        try:
            campos["created_by"] = int(usuario)
        except (TypeError, ValueError):
            pass
    return encolar_push(
        db, "ot_pdi", python_id=pdi_id, legado_id=unidad["id"],
        operacion="crear_en", campos=campos,
        legado_updated_at_conocido="", requiere_unidad=0,
        depende_de=depende_de)


def encolar_descuento(db, pdi_id, consumible_id, litros, depende_de):
    """Encola el descuento de stock. Tambien depende del movimiento.

    `legado_id` es el id del CONSUMIBLE, no el de la unidad: la ruta es
    `/api_regla/stock_consumibles/{id}/descontar` y ese id identifica la fila
    de stock. Es la unica entidad donde `legado_id` no apunta a una unidad, y
    por eso `tabla_espejo` es `stock_consumibles` -- no se marca
    `push_pendiente` en ninguna unidad."""
    return encolar_push(
        db, "stock_consumibles", python_id=pdi_id, legado_id=consumible_id,
        operacion="crear_en", campos={"cantidad": litros},
        legado_updated_at_conocido="", requiere_unidad=0,
        depende_de=depende_de)


def campos_it(estado_it, observacion_it, estado_hacia, usuario):
    """El payload del IT, con los nombres del legado.

    `estado_hacia` es lo que `taller.py:_destino_it()` ya calculo, y coincide
    rama por rama con el PHP: CARFLEX -> 'INSPECCION MECANICA DESPACHO', el
    resto -> 'INGRESO A TALLER'. No se vuelve a decidir aca para que no haya
    dos lugares donde se pueda desincronizar."""
    campos = {
        "estado_it": estado_it,
        "observacion_it": observacion_it,
        "despachado": estado_hacia,
        "calle": CALLE_IT,
    }
    if usuario:
        # `updated_by` es int del otro lado. `id_actual()` devuelve texto.
        try:
            campos["updated_by"] = int(usuario)
        except (TypeError, ValueError):
            pass
    return campos


# ---------------------------------------------------------------------------
# El cliente HTTP
# ---------------------------------------------------------------------------

class PushLegadoError(Exception):
    """Error al escribir hacia el legado. El mensaje se loguea: nunca incluye
    la API key."""


class ClientePushLegado(object):
    """Contrato de acceso. La clase real hace HTTP; los tests y el endpoint
    simulado implementan lo mismo."""

    def actualizar(self, ruta, legado_id, campos, legado_updated_at_conocido,
                   idem_key=""):
        """PUT /api_regla/<ruta>/<legado_id>. Devuelve:

            {"ok": True,  "updated_at": <str>}                       exito
            {"ok": False, "conflicto": True, "updated_at": <str>,
             "datos_actuales": {...}}                                409

        Lanza PushLegadoError para cualquier otra cosa."""
        raise NotImplementedError

    def crear(self, ruta, campos, idem_key=""):
        """POST /api_regla/<ruta>. Devuelve {"ok": True, "id": <int>,
        "updated_at": <str>}.

        Lo usa `movimientos`: un movimiento INSERTA una fila en `registros` del
        legado, no actualiza una existente. El `id` que vuelve es el de esa
        fila."""
        raise NotImplementedError

    def crear_en(self, ruta, idem_key="", **campos):
        """POST a una ruta que YA lleva el id adentro.

        Existe porque las dos rutas nuevas no tienen la forma
        `/api_regla/<entidad>` sino `/api_regla/pdi/{id}/ot` y
        `/api_regla/stock_consumibles/{id}/descontar`: el id va en el CAMINO,
        no en el cuerpo.

        Y no reusa `crear()` por una diferencia que importa: `crear()` exige un
        `id` en la respuesta, y estas dos no devuelven uno. La de OT devuelve
        `ot: {pdi: {...}, combustible: {...}}` y la de stock devuelve `stock`.
        Aflojar `crear()` para que acepte las tres habria sacado justamente la
        comprobacion que ataja el 404_override -- un 200 con cuerpo vacio."""
        raise NotImplementedError


class ClientePushLegadoHTTP(ClientePushLegado):
    """El cliente real.

    Mismas variables de entorno que el pull:
        LEGADO_BASE_URL     la raiz del sitio
        LEGADO_API_KEY      el header X-API-Key
    Y una propia:
        LEGADO_PUSH_TIMEOUT segundos (8 por defecto)
    """

    def __init__(self, base_url=None, api_key=None, timeout=None):
        self.base_url = exigir_destino_local(
            (base_url if base_url is not None
             else BASE_URL_DEFECTO).rstrip("/"), "el cliente del PUSH")
        self.api_key = (api_key if api_key is not None
                        else os.environ.get("LEGADO_API_KEY", "")).strip()
        self.timeout = float(timeout if timeout is not None else PUSH_TIMEOUT)

    def _cabeceras(self, idem_key):
        cabeceras = {"X-API-Key": self.api_key, "User-Agent": USER_AGENT}
        if idem_key:
            cabeceras["Idempotency-Key"] = idem_key
        return cabeceras

    def _verificar_config(self):
        if _requests is None:                    # pragma: no cover
            raise PushLegadoError("falta la dependencia `requests`")
        if not self.base_url:
            raise PushLegadoError("falta LEGADO_BASE_URL")
        if not self.api_key:
            raise PushLegadoError(
                "falta LEGADO_API_KEY: el endpoint la exige en X-API-Key")

    def crear(self, ruta, campos, idem_key=""):
        self._verificar_config()
        url = "{}/api_regla/{}".format(self.base_url, ruta)
        try:
            r = _requests.post(url, json=dict(campos),
                               headers=self._cabeceras(idem_key),
                               timeout=self.timeout)
        except Exception as e:
            raise PushLegadoError(
                "no se pudo conectar con el legado ({}): {}".format(url, e))

        # El 409 tambien existe en el POST: el endpoint compara
        # `legado_updated_at_conocido` ANTES de insertar, asi que un conflicto
        # no deja ni la fila de `registros` ni el UPDATE.
        if r.status_code == 409:
            try:
                return r.json()
            except ValueError:
                raise PushLegadoError(
                    "409 sin JSON valido ({}): {!r}".format(
                        url, (r.text or "")[:200]))

        if r.status_code not in (200, 201):
            raise PushLegadoError(
                "HTTP {} al crear en {}: {!r}".format(
                    r.status_code, url, (r.text or "")[:200]))
        try:
            datos = r.json()
        except ValueError:
            raise PushLegadoError(
                "el endpoint no devolvio JSON ({}): {!r}".format(
                    url, (r.text or "")[:200]))
        # Mismo motivo que en `actualizar`: en este sitio una ruta que no
        # resuelve responde 200 con cuerpo vacio, asi que un 200 no alcanza.
        if not datos.get("ok") or "id" not in datos:
            raise PushLegadoError(
                "respuesta 200 sin 'ok'/'id' ({}): {!r}. Si el cuerpo esta "
                "vacio, la ruta no resolvio y cayo en el 404_override".format(
                    url, (r.text or "")[:200]))
        return datos

    def crear_en(self, ruta, idem_key="", **campos):
        self._verificar_config()
        url = "{}/api_regla/{}".format(self.base_url, ruta.lstrip("/"))
        try:
            r = _requests.post(url, json=dict(campos),
                               headers=self._cabeceras(idem_key),
                               timeout=self.timeout)
        except Exception as e:
            raise PushLegadoError(
                "no se pudo conectar con el legado ({}): {}".format(url, e))

        if r.status_code == 409:
            try:
                return r.json()
            except ValueError:
                raise PushLegadoError("409 sin JSON valido ({})".format(url))

        if r.status_code not in (200, 201):
            raise PushLegadoError(
                "HTTP {} en {}: {!r}".format(
                    r.status_code, url, (r.text or "")[:200]))
        try:
            datos = r.json()
        except ValueError:
            raise PushLegadoError(
                "el endpoint no devolvio JSON ({}): {!r}".format(
                    url, (r.text or "")[:200]))
        # `ok` explicito y no un 2xx a secas: en este sitio una ruta que no
        # resuelve devuelve 200 con cuerpo vacio (404_override).
        if not datos.get("ok"):
            raise PushLegadoError(
                "respuesta sin 'ok' ({}): {!r}. Si el cuerpo esta vacio, la "
                "ruta no resolvio y cayo en el 404_override".format(
                    url, (r.text or "")[:200]))
        return datos

    def actualizar(self, ruta, legado_id, campos, legado_updated_at_conocido,
                   idem_key=""):
        self._verificar_config()
        url = "{}/api_regla/{}/{}".format(self.base_url, ruta, legado_id)
        cuerpo = dict(campos)
        cuerpo["legado_updated_at_conocido"] = legado_updated_at_conocido or ""
        try:
            r = _requests.put(url, json=cuerpo,
                              headers=self._cabeceras(idem_key),
                              timeout=self.timeout)
        except Exception as e:
            raise PushLegadoError(
                "no se pudo conectar con el legado ({}): {}".format(url, e))

        if r.status_code == 409:
            try:
                return r.json()
            except ValueError:
                raise PushLegadoError(
                    "409 sin JSON valido ({}): {!r}".format(
                        url, (r.text or "")[:200]))

        if r.status_code != 200:
            raise PushLegadoError(
                "HTTP {} al actualizar {} {} en {}: {!r}".format(
                    r.status_code, ruta, legado_id, url, (r.text or "")[:200]))
        try:
            datos = r.json()
        except ValueError:
            raise PushLegadoError(
                "el endpoint no devolvio JSON ({}): {!r}".format(
                    url, (r.text or "")[:200]))

        # UN 200 NO ALCANZA PARA DARLO POR ESCRITO. Hace falta el `ok` explicito.
        #
        # No es paranoia de manual: el legado tiene
        # `$route['404_override'] = 'error'` en routes.php, asi que CUALQUIER
        # ruta que no resuelva NO da 404 -- cae en el controlador Error, que
        # intenta pintar el login y termina respondiendo **HTTP 200, text/html,
        # cuerpo vacio**. Comprobado el 2026-08-26 contra produccion con tres
        # rutas inexistentes distintas.
        #
        # O sea: en este sitio, un PUT mal ruteado se ve igual que uno exitoso
        # si uno solo mira el codigo de estado.
        #
        # Hoy el cuerpo vacio ya no pasa el json() de arriba, asi que falla
        # solo. Pero apoyarse en eso es apoyarse en que la pagina de error siga
        # siendo vacia: el dia que devuelva cualquier JSON, `conflicto` seria
        # None, esto caeria por el camino del exito, y el push se marcaria como
        # hecho habiendo escrito nada -- push_pendiente en 0, entrada resuelta,
        # el dato perdido en silencio. Es justo el modo de fallar que toda esta
        # arquitectura existe para no tener.
        #
        # El contrato dice `{"ok": true, ...}`. Se exige.
        if not datos.get("ok"):
            raise PushLegadoError(
                "respuesta 200 sin 'ok' del endpoint ({}): {!r}. Si el cuerpo "
                "esta vacio o es HTML, la ruta del PUT no esta resolviendo y "
                "la peticion cayo en el 404_override del legado".format(
                    url, (r.text or "")[:200]))
        return datos


def _cliente_por_defecto():
    """Se construye en el momento de usarlo, no al importar: asi lee las
    variables de entorno vigentes y no las que habia al arrancar."""
    return ClientePushLegadoHTTP()


# ---------------------------------------------------------------------------
# Ejecucion de una entrada
# ---------------------------------------------------------------------------

def _resolver_entrada(db, id_cola):
    db.execute("UPDATE sync_push_pendientes SET resuelto_en = ? WHERE id = ?",
               (now_iso(), id_cola))


def _registrar_fallo(db, id_cola, mensaje):
    """Suma un intento, calcula `proximo_intento` con el backoff y guarda el
    error. La entrada SIGUE en cola: `resuelto_en` no se toca."""
    fila = db.execute("SELECT intentos FROM sync_push_pendientes WHERE id = ?",
                      (id_cola,)).fetchone()
    intentos = (fila["intentos"] if fila else 0) + 1
    demora = PUSH_BACKOFF[min(intentos - 1, len(PUSH_BACKOFF) - 1)]
    proximo = (datetime.datetime.now()
               + datetime.timedelta(seconds=demora)).isoformat(timespec="seconds")
    db.execute(
        "UPDATE sync_push_pendientes "
        "   SET intentos = ?, proximo_intento = ?, ultimo_error = ? "
        " WHERE id = ?",
        (intentos, proximo, str(mensaje)[:500], id_cola))


def ejecutar_entrada(id_cola, cliente=None, db_path=None):
    """Intenta una entrada de la cola. Devuelve 'ok', 'conflicto' o 'error'.

    Abre su propia conexion: se llama desde hilos sin contexto de Flask. NO
    lanza excepciones -- lo que sale mal queda en la tabla y en el log, porque
    quien la llama suele ser un hilo daemon donde nadie las veria."""
    cliente = cliente or _cliente_por_defecto()
    db = conectar_db(db_path)
    try:
        entrada = db.execute("SELECT * FROM sync_push_pendientes WHERE id = ?",
                             (id_cola,)).fetchone()
        # LA DEPENDENCIA SE CHEQUEA ACA, no solo en `procesar_pendientes`.
        #
        # `disparar_push` llama a esta funcion DIRECTAMENTE con el id, sin
        # pasar por el selector de pendientes -- y `guardar_pdi` dispara las
        # cuatro entradas seguidas despues del commit. Sin esta guarda, la OT
        # y el descuento saldrian ANTES que el movimiento, que es exactamente
        # lo que `depende_de` viene a impedir: `orden_trabajo` es append-only
        # y una OT creada para una PDI que despues choca 409 no se borra.
        #
        # Encontrado por `probar_circulo.py`: el selector filtraba bien y el
        # disparo inmediato no. Las dos puertas o ninguna.
        if entrada is not None and entrada["depende_de"]:
            padre = db.execute(
                "SELECT resuelto_en, ultimo_error FROM sync_push_pendientes "
                " WHERE id = ?", (entrada["depende_de"],)).fetchone()
            if padre is None or not padre["resuelto_en"] or padre["ultimo_error"]:
                _log.info("push %s espera a la entrada %s", id_cola,
                          entrada["depende_de"])
                return "espera"

        if entrada is None or entrada["resuelto_en"]:
            # Ya la resolvio otro hilo. No es un error: el disparo inmediato y
            # procesar_pendientes pueden cruzarse.
            return "ok"

        entidad = entrada["entidad"]
        if entidad not in ENTIDADES:
            _registrar_fallo(db, id_cola,
                             "entidad desconocida: {}".format(entidad))
            db.commit()
            return "error"

        conf = ENTIDADES[entidad]
        espejo = conf["tabla_espejo"]
        legado_id = entrada["legado_id"]
        campos = json.loads(entrada["campos_json"] or "{}")

        if entrada["operacion"] not in ("actualizar", "crear", "crear_en"):
            _registrar_fallo(db, id_cola,
                             "operacion no soportada todavia: {}"
                             .format(entrada["operacion"]))
            db.commit()
            return "error"

        try:
            if entrada["operacion"] == "crear_en":
                # La ruta lleva el id adentro y el cuerpo NO lleva
                # `legado_updated_at_conocido`: ninguno de los dos endpoints
                # que usan esta forma hace locking optimista, y por eso los dos
                # exigen `Idempotency-Key`. En `stock_consumibles` no hay con
                # que hacer locking -- la tabla no tiene `updated_at` --, y en
                # `orden_trabajo` no tendria sentido: se INSERTA, no se pisa
                # una fila existente.
                resp = cliente.crear_en(
                    conf["ruta"].format(id=legado_id),
                    idem_key=entrada["idempotency_key"] or "", **campos)
            elif entrada["operacion"] == "crear":
                # El conocido viaja DENTRO del cuerpo: el POST no tiene un id
                # en la URL donde colgarlo, a diferencia del PUT.
                cuerpo = dict(campos)
                cuerpo["legado_updated_at_conocido"] = (
                    entrada["legado_updated_at_conocido"] or "")
                resp = cliente.crear(conf["ruta"], cuerpo,
                                     idem_key=entrada["idempotency_key"] or "")
            else:
                resp = cliente.actualizar(
                    conf["ruta"], legado_id, campos,
                    entrada["legado_updated_at_conocido"] or "",
                    idem_key=entrada["idempotency_key"] or "")
        except PushLegadoError as e:
            _registrar_fallo(db, id_cola, str(e))
            db.commit()
            _log.warning("push %s fallo (legado_id=%s intento %s): %s",
                         entidad, legado_id, entrada["intentos"] + 1, e)
            return "error"

        if resp.get("conflicto"):
            # Gana el legado. Se guardan LAS DOS versiones y se limpia el
            # flag: no hay nada que reintentar, y el proximo pull traera la
            # version buena.
            db.execute(
                """
                INSERT INTO sync_conflictos
                    (entidad, python_id, legado_id, nuestra_version_json,
                     version_legado_json, legado_updated_at, registrado_en)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (entidad, entrada["python_id"], legado_id,
                 json.dumps(campos, ensure_ascii=False),
                 json.dumps(resp.get("datos_actuales") or {}, ensure_ascii=False),
                 resp.get("updated_at") or "", now_iso()))
            db.execute('UPDATE "{}" SET push_pendiente = 0 WHERE id = ?'
                       .format(espejo), (legado_id,))
            _resolver_entrada(db, id_cola)
            db.commit()
            _log.warning("push %s CONFLICTO legado_id=%s: el legado tiene "
                         "updated_at=%s, se registro para revision",
                         entidad, legado_id, resp.get("updated_at"))
            # El aviso va DESPUES del commit y envuelto en su propio try: el
            # conflicto ya esta guardado y es lo que no se puede perder. Que
            # falle el correo -- Resend caido, sin destinatarios, sin clave --
            # no puede convertir un conflicto registrado en una excepcion.
            try:
                _avisar_conflicto(db, entidad, espejo, legado_id, campos,
                                  resp.get("datos_actuales") or {},
                                  resp.get("updated_at") or "")
            except Exception:                    # noqa: BLE001 -- ver arriba
                _log.exception("no se pudo armar el aviso de conflicto de %s %s",
                               entidad, legado_id)
            return "conflicto"

        # Exito. El `updated_at` que devuelve el legado se guarda en la
        # replica: es el reloj de ALLA, y con el, el proximo push de esta misma
        # unidad arranca con el locking bien parado aunque el pull no haya
        # pasado en el medio.
        #
        # SOLO PARA LAS ENTIDADES QUE ESPEJAN UNA UNIDAD. `stock_consumibles`
        # no: su tabla no tiene `updated_at` ni `push_pendiente` -- son cinco
        # columnas, `id nombre stock precio promedio` -- y tampoco los
        # necesita. No hay locking optimista sobre ella (por eso la
        # Idempotency-Key es obligatoria) y el pull la trae entera cada vuelta,
        # asi que no hay nada que proteger de un sobreescrito.
        #
        # Se decide por el nombre de la tabla espejo y no por una bandera nueva:
        # la condicion real es "esta fila es una unidad", y eso ya lo dice
        # `tabla_espejo`.
        if espejo == "newstocks_cidef":
            db.execute('UPDATE "{}" SET updated_at = ?, push_pendiente = 0 '
                       ' WHERE id = ?'.format(espejo),
                       (resp.get("updated_at") or "", legado_id))
        _resolver_entrada(db, id_cola)
        db.commit()
        _log.info("push %s ok: legado_id=%s campos=%s%s",
                  entidad, legado_id, sorted(campos),
                  " (idempotente: el legado ya lo tenia)"
                  if resp.get("idempotente") else "")
        return "ok"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# El aviso de conflicto
# ---------------------------------------------------------------------------
#
# Un conflicto es el unico caso en que REGLA descarta un cambio que un operario
# cargo a mano. No se pierde -- las dos versiones quedan en `sync_conflictos`
# --, pero hasta ahora nadie se enteraba: habia que acordarse de correr
# `python -m modulos.push_legado estado`.
#
# Deja de ser hipotetico desde que hay DOS replicas empujando al mismo legado
# (la notebook y Railway): tocar la misma unidad desde las dos dentro de la
# ventana del pull produce exactamente esto.
#
# El correo va donde ya se escribe el conflicto y no en un proceso que sondee
# la tabla: un sondeo agrega una pieza que puede morirse sin que nadie lo note,
# y ademas llegaria tarde por definicion.

DESTINATARIOS_CONFLICTOS = "SYNC_CONFLICTOS_DESTINATARIOS"


def _comparable(valor):
    """Para decidir si dos valores son 'el mismo'. NULL y '' se colapsan, y
    todo se mira como texto: el legado devuelve `updated_by` como '0' y
    nosotros lo mandamos como 0, y esa no es una diferencia real."""
    return "" if valor is None else str(valor).strip()


# Campos que viajan pero NO son "lo que el operario quiso escribir": los sella
# el sistema. `updated_by` difiere SIEMPRE -- es quien hizo cada cambio, y en
# un conflicto por definicion fueron dos personas distintas --, asi que
# contarlo como diferencia infla el numero y tapa las de verdad. En la primera
# prueba contra produccion el aviso decia "3 campos con diferencia" cuando las
# sustantivas eran dos.
#
# No se esconde: sube al encabezado, que es donde sirve. Saber QUIEN hizo el
# cambio que gano es lo primero que uno necesita para resolver el conflicto.
CAMPOS_METADATO = ("updated_by",)


def diferencias(nuestra, del_legado):
    """[(campo, lo_nuestro, lo_del_legado, difiere)] para los campos que el
    operario intento escribir. Los de CAMPOS_METADATO quedan afuera.

    Se listan TODOS los que viajaron, no solo los que difieren, y cada uno dice
    si difiere. Ver solo las diferencias obliga a preguntarse que mas se estaba
    mandando; verlas en contexto contesta esa pregunta sola."""
    filas = []
    for campo in sorted(nuestra):
        if campo in CAMPOS_METADATO:
            continue
        a = _comparable(nuestra.get(campo))
        b = _comparable(del_legado.get(campo))
        # Un campo que el legado no devolvio no es una diferencia: es un campo
        # del que no sabemos nada. Se marca como tal en vez de inventar un ''.
        conocido = campo in del_legado
        filas.append((campo, nuestra.get(campo),
                      del_legado.get(campo) if conocido else None,
                      conocido and a != b))
    return filas


def _quien(db, id_usuario):
    """El nombre detras de un `updated_by`. `tbl_users` es la tabla de usuarios
    del propio legado, traida en el dump, asi que el id de alla resuelve aca.

    Si no resuelve se devuelve el id pelado: es preferible a no decir nada."""
    crudo = _comparable(id_usuario)
    if crudo == "":
        return ""
    try:
        fila = db.execute("SELECT name FROM tbl_users WHERE userId = ?",
                          (int(crudo),)).fetchone()
        if fila is not None and fila["name"]:
            return "{} (id {})".format(fila["name"], crudo)
    except Exception:                            # noqa: BLE001
        pass
    return "id {}".format(crudo)


def _avisar_conflicto(db, entidad, espejo, legado_id, campos, del_legado,
                      legado_updated_at):
    """Arma el correo y lo manda en segundo plano. No levanta."""
    vin = ""
    try:
        fila = db.execute('SELECT vin FROM "{}" WHERE id = ?'.format(espejo),
                          (legado_id,)).fetchone()
        if fila is not None:
            vin = fila["vin"] or ""
    except Exception:                            # pragma: no cover
        pass

    filas = diferencias(campos, del_legado)
    cuantas = sum(1 for f in filas if f[3])
    de_donde = correo.origen()
    # Quien hizo el cambio que gano, y quien el que se descarto. Es lo primero
    # que hace falta para resolver un conflicto: con los dos nombres se sabe a
    # quien preguntarle sin abrir la base.
    gano = _quien(db, del_legado.get("updated_by"))
    perdio = _quien(db, campos.get("updated_by"))

    def celda(v):
        return "<i>(vacío)</i>" if _comparable(v) == "" else str(v)

    cuerpo_html = "".join(
        '<tr{estilo}><td>{campo}</td><td>{nuestro}</td><td>{legado}</td>'
        '<td>{marca}</td></tr>'.format(
            estilo=' style="background:#fff3cd"' if difiere else "",
            campo=campo, nuestro=celda(nuestro),
            legado="<i>(no informado)</i>" if legado is None and not difiere
                   else celda(legado),
            marca="DIFIERE" if difiere else "")
        for campo, nuestro, legado, difiere in filas)

    asunto = "Conflicto de sync — unidad {}{}".format(
        legado_id, " ({})".format(vin) if vin else "")

    html = (
        "<style>.tb {{ border-collapse: collapse; }}"
        ".tb th, .tb td {{ padding: 5px; border: solid 1px #777; "
        "font-family: sans-serif; font-size: 13px; }}"
        ".tb th {{ background-color: lightblue; }}</style>"
        "<h3><b>Conflicto al sincronizar con el sistema anterior</b></h3>"
        "<p>El sistema anterior tenía un cambio más reciente que el nuestro, "
        "así que <b>NO se sobrescribió</b>. Gana el sistema anterior: el dato "
        "correcto va a llegar en la próxima vuelta del sync.</p>"
        "<p>Unidad: <b>{uid}</b>{vin}<br>"
        "Entidad: <b>{entidad}</b><br>"
        "Empujado desde: <b>{origen}</b>{perdio}<br>"
        "El sistema anterior fue modificado el <b>{cuando}</b>{gano}</p>"
        '<table class="tb">'
        "<tr><th>Campo</th><th>Lo que se quiso escribir</th>"
        "<th>Lo que tiene el sistema anterior</th><th></th></tr>{filas}</table>"
        "<p>Quedó guardado en <code>sync_conflictos</code> con las dos "
        "versiones. No hace falta hacer nada para que el dato del sistema "
        "anterior vuelva; si el cambio que se perdió era el correcto, hay que "
        "volver a cargarlo.</p>"
        "<p>Este mail fue enviado automaticamente por sistema REGLA</p>"
    ).format(uid=legado_id, vin=" — VIN <b>{}</b>".format(vin) if vin else "",
             entidad=entidad, origen=de_donde,
             perdio=" por <b>{}</b>".format(perdio) if perdio else "",
             cuando=legado_updated_at or "(sin fecha)",
             gano=" por <b>{}</b>".format(gano) if gano else "",
             filas=cuerpo_html)

    texto = (
        "Conflicto al sincronizar con el sistema anterior.\n\n"
        "Unidad {uid}{vin} | entidad {entidad}\n"
        "Lo que se descarto : desde {origen}{perdio}\n"
        "Lo que gano        : del {cuando}{gano}\n\n"
        "{cuantas} campo(s) con diferencia:\n{lista}\n"
        "No se sobrescribio nada. Gana el sistema anterior."
    ).format(uid=legado_id, vin=" (VIN {})".format(vin) if vin else "",
             entidad=entidad, origen=de_donde,
             perdio=", cargado por {}".format(perdio) if perdio else "",
             cuando=legado_updated_at or "sin fecha",
             gano=", hecho por {}".format(gano) if gano else "",
             cuantas=cuantas,
             lista="".join(
                 "  - {}: se quiso escribir {!r}, el legado tiene {!r}\n".format(
                     c, n, l)
                 for c, n, l, d in filas if d) or "  (ninguno)\n")

    correo.en_segundo_plano(
        correo.mandar, correo.destinatarios(DESTINATARIOS_CONFLICTOS),
        asunto, texto, html)


# ---------------------------------------------------------------------------
# Disparo inmediato (fire-and-forget desde el endpoint)
# ---------------------------------------------------------------------------

def disparar_push(id_cola, cliente=None):
    """Lanza un hilo daemon que intenta el push ya mismo.

    Se llama DESPUES del commit del endpoint. Si el hilo falla, o el proceso
    muere antes de que termine, la entrada de cola ya esta escrita y
    `procesar_pendientes()` la retoma. El usuario no espera nada de esto.

    Que sea daemon es a proposito: un push a medio camino no debe demorar el
    cierre del proceso, porque la cola ya garantiza que se retoma.

    Con PUSH_LEGADO_ACTIVO apagado no lanza nada y lo dice en el log: la
    entrada queda en cola esperando una corrida manual. La compuerta esta ACA y
    no en cada llamador para que no haya forma de saltearla por descuido."""
    if not push_activo():
        _log.info("push %s encolado, disparo apagado (PUSH_LEGADO_ACTIVO): "
                  "se procesa con `python -m modulos.push_legado pendientes`",
                  id_cola)
        return

    def _correr():
        try:
            ejecutar_entrada(id_cola, cliente)
        except Exception:                        # pragma: no cover
            # Un hilo que muere con excepcion no se lo cuenta a nadie. La
            # entrada sigue en cola igual, pero sin esto no habria rastro.
            _log.exception("hilo de push %s murio", id_cola)

    threading.Thread(target=_correr, daemon=True,
                     name="push-legado-{}".format(id_cola)).start()


# ---------------------------------------------------------------------------
# Reintentos
# ---------------------------------------------------------------------------

def procesar_pendientes(cliente=None, db_path=None):
    """Una vuelta sobre las entradas vencidas. Devuelve un resumen.

    Cuando se enganche al hilo de fondo va al FINAL de cada vuelta del pull, no
    al principio: el UPSERT del pull saltea las filas con push_pendiente=1, asi
    que si esto corriera primero y limpiara el flag, el pull de esa misma
    vuelta podria sobrescribir la fila sin verla.

    Un fallo en una entrada no detiene a las demas."""
    ahora = now_iso()
    db = conectar_db(db_path)
    try:
        # La condicion de `depende_de` es la que ordena la PDI y sus OT: una
        # entrada dependiente no se intenta hasta que la otra este resuelta y
        # SIN error. `ultimo_error = ''` no alcanza sola -- una entrada
        # resuelta por CONFLICTO tambien limpia el error --, pero un conflicto
        # en la PDI significa que el legado gano y no hay nada que cobrar, asi
        # que tampoco hay que crear las OT. Por eso alcanza con exigir que la
        # padre este resuelta bien.
        ids = [r["id"] for r in db.execute(
            "SELECT p.id FROM sync_push_pendientes p "
            " WHERE p.resuelto_en = '' "
            "   AND (p.proximo_intento = '' OR p.proximo_intento <= ?) "
            "   AND (p.depende_de IS NULL OR EXISTS ("
            "         SELECT 1 FROM sync_push_pendientes m "
            "          WHERE m.id = p.depende_de "
            "            AND m.resuelto_en <> '' AND m.ultimo_error = '')) "
            " ORDER BY p.creado_en", (ahora,))]
    finally:
        db.close()

    resumen = {"intentados": 0, "ok": 0, "conflicto": 0, "error": 0}
    for id_cola in ids:
        resumen["intentados"] += 1
        resultado = ejecutar_entrada(id_cola, cliente, db_path=db_path)
        resumen[resultado] = resumen.get(resultado, 0) + 1
    if resumen["intentados"]:
        _log.info("push pendientes: %s", resumen)
    return resumen


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------

def resumen_cola(db):
    """Que hay en la cola ahora mismo. Para el CLI y para la pantalla de
    estado del sync cuando se agregue."""
    pendientes = db.execute(
        "SELECT entidad, COUNT(*) n, MIN(creado_en) mas_vieja, MAX(intentos) max_intentos "
        "  FROM sync_push_pendientes WHERE resuelto_en = '' "
        " GROUP BY entidad").fetchall()
    conflictos = db.execute(
        "SELECT COUNT(*) n FROM sync_conflictos").fetchone()["n"]
    ultimos_errores = db.execute(
        "SELECT id, entidad, legado_id, intentos, proximo_intento, ultimo_error "
        "  FROM sync_push_pendientes "
        " WHERE resuelto_en = '' AND ultimo_error <> '' "
        " ORDER BY id DESC LIMIT 5").fetchall()
    return {
        "pendientes": [dict(f) for f in pendientes],
        "conflictos": conflictos,
        "ultimos_errores": [dict(f) for f in ultimos_errores],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cliente_de_args(args):
    """`--simulado URL` apunta el push al endpoint de mentira en vez de a
    produccion. Es el modo en que se prueba primero, siempre."""
    if args.simulado:
        return ClientePushLegadoHTTP(base_url=args.simulado,
                                     api_key=os.environ.get("LEGADO_API_KEY", "x"))
    return None


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Push hacia claude.logautos.cl")
    ap.add_argument("comando", choices=("estado", "pendientes", "una"))
    ap.add_argument("id_cola", nargs="?", type=int, default=None)
    ap.add_argument("--simulado", default=None,
                    help="URL del endpoint simulado (ej. http://127.0.0.1:877)")
    args = ap.parse_args(argv)

    if args.comando == "estado":
        db = conectar_db()
        try:
            asegurar_tablas(db)
            db.commit()
            r = resumen_cola(db)
        finally:
            db.close()
        if not r["pendientes"]:
            print("cola: vacia")
        for f in r["pendientes"]:
            print("cola: {} pendientes de '{}' (mas vieja {}, hasta {} intentos)"
                  .format(f["n"], f["entidad"], f["mas_vieja"], f["max_intentos"]))
        print("conflictos registrados: {}".format(r["conflictos"]))
        for f in r["ultimos_errores"]:
            print("  #{} {} legado_id={} intento {} -> reintenta {}: {}"
                  .format(f["id"], f["entidad"], f["legado_id"], f["intentos"],
                          f["proximo_intento"], f["ultimo_error"][:120]))
        return 0

    if args.comando == "una":
        if args.id_cola is None:
            print("falta el id de la entrada: `una <id_cola>`")
            return 2
        print(ejecutar_entrada(args.id_cola, _cliente_de_args(args)))
        return 0

    resumen = procesar_pendientes(_cliente_de_args(args))
    print("intentados: {intentados}  ok: {ok}  conflictos: {conflicto}  "
          "errores: {error}".format(**resumen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
