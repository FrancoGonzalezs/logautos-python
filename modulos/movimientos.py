"""
modulos/movimientos.py -- modulo de Movimientos: buscar una unidad y
recomendarle el siguiente paso, de Navegando hasta Zona de Despacho.

De donde sale el proceso
------------------------
Fase 2 se construye sobre el documento "Definicion del proceso de unidades --
Navegando a Despachado, clientes CARFLEX y CIDEF" v1.0 (19-08-2026), armado
sobre 40.000 movimientos reales del modulo Tracking. Sus capitulos 4.1 y 5.1
-- la matriz de transiciones validas por cliente -- estan transcriptos tal
cual en TRANSICIONES, y son la regla del motor.

El documento manda sobre el PHP. No es una preferencia de estilo: el PHP tiene
dos caminos paralelos para el mismo tramo (un switch manual bajo PATIO 1 que
escribe 'Cc'/'It'/'Zd', y un motor automatico dentro del bloque de Lavado que
escribe 'CC'/'IT'/'ZD' y usa PATIO 2), y ninguno de los dos es el proceso
completo. El documento si lo es.

Por que un motor de reglas y no un flujo lineal
-----------------------------------------------
Los 299.322 movimientos de `registros` muestran que la operacion real tiene
vueltas atras y excepciones todo el tiempo: una unidad puede volver a lavado,
saltarse la revision de contenedor, o entrar a taller desde cualquier lado.
Un flowchart unico mentiria sobre como funciona. Aca el siguiente paso se
deduce de tres cosas -- el estado actual, el cliente y que hitos ya cumplio la
unidad -- y el usuario SIEMPRE puede elegir otro: eso no es un error, es la
operacion. Lo que se le pide es el motivo, para que el desvio quede medido.

Donde termina este modulo
-------------------------
En ZONA DE DESPACHO. El paso a DESPACHADO esta en la matriz porque es parte
del proceso, pero lo escribe Ingreso/Despacho > Despacho VIN y no esta
pantalla -- por eso es de solo lectura y la tarjeta lleva alla en vez de
ofrecer una confirmacion. Lo mismo con el ciclo PDI/DYP de CIDEF, que lo mueven
Actualizar PDI y Actualizar DYP.

Que DESPACHADO no lo escriba Crear Guia esta verificado: `creaguia()` solo
carga una vista y `creagd_proces.php` emite el documento en facto.cl, inserta
en `guias_despacho` y estampa `guia_desp` en la unidad, sin tocar el estado ni
registrar movimiento. El dato lo confirma: hay 51.575 unidades con folio de
guia contra 69.825 despachadas, o sea ~18.000 despachadas sin guia.

Donde se guardan los movimientos
--------------------------------
En una tabla propia, `movimientos_regla`, y NO en `registros`. Dos razones:

  - `registros` es la replica del sistema viejo y sirve de patron de
    comparacion contra produccion; escribirle arriba la ensuciaria.
  - el importador dropea las tablas que importa, asi que un movimiento
    escrito en `registros` se perderia en la proxima reimportacion.

Esa tabla es, ademas, la cola natural para el push cuando se construya el
sync: cada fila es un movimiento pendiente de mandar al sistema viejo.
"""

from datetime import date, datetime

from flask import Blueprint, redirect, render_template, request, session, url_for

from core import (BUSQUEDA_DEVUELVE, BUSQUEDA_FILTRA, consultar,
                  exigir_unidad_id, get_db)
from modulos.acceso import id_actual, nombre_actual, usuario_actual
from modulos.catalogos import normalizar
# `vin_limpio` vive en kpis.py porque ahi nacio (filtra los VIN invalidos de
# los indicadores). Es el mismo criterio que hace falta aca para decidir si lo
# que entrego el escaner es un VIN o texto suelto, asi que se reusa en vez de
# escribir una segunda validacion que pueda divergir.
from modulos.kpis import vin_limpio
# El modulo entero y no sus nombres sueltos: `ubicacion.valida` y
# `ubicacion.sugerencia` se leen mejor con el prefijo, que dice de que catalogo
# salen. Y no importa a `movimientos`, asi que no hay ciclo.
from modulos import ubicacion
from modulos.unidades import TABLA

bp = Blueprint("movimientos", __name__, url_prefix="/movimientos")


# ---------------------------------------------------------------------------
# Los pasos del tramo que cubre esta fase
# ---------------------------------------------------------------------------

PASOS = {
    "ingreso": {
        "titulo": "Ingreso",
        "detalle": "Registrar la llegada de la unidad al patio.",
        "estado_destino": "ZONA DE RECEPCION",
        "pide": [
            ("guia_ingreso", "Guía de ingreso", "text"),
            ("fecha", "Fecha de ingreso", "date"),
        ],
    },
    "revision_contenedor": {
        "titulo": "Revisión de Contenedor/Grúa",
        "detalle": "Revisar la unidad al desconsolidar, antes del check list.",
        "estado_destino": "ZONA DE RECEPCION",
        "pide": [],
        # La revision es por CONTENEDOR, no por unidad suelta: una unidad no se
        # revisa sola, se revisa el contenedor en que llego. Por eso el paso no
        # lleva a un formulario sino a `entrada`, que primero busca si el VIN ya
        # pertenece a un contenedor -- si pertenece va derecho a cargarle la
        # evidencia ahi, y si no ofrece crear uno.
        #
        # El estado destino es el MISMO de origen a proposito: la revision no
        # mueve la unidad en la maquina de estados.
        "formulario": "revision_contenedor.entrada",
    },
    "lavado_revision": {
        "titulo": "Lavado Revisión",
        "detalle": "Lavado previo al check list, para poder ver la carrocería.",
        "estado_destino": "ZONA DE LAVADO",
        "pide": [],
    },
    "check_list_ingreso": {
        "titulo": "Check List de Ingreso",
        "detalle": "Levantar daños, faltantes y equipamiento de la unidad.",
        "estado_destino": "ZONA DE RECEPCION",
        "pide": [],
        # Este paso no se confirma acá sino en su propio formulario
        # (modulos/check_list.py): son ~30 campos, tres catálogos y una foto
        # por daño, que no entran en la tarjeta del paso.
        "formulario": "check_list.formulario",
    },
    "pdi": {
        "titulo": "PDI",
        "detalle": "Inspección de preentrega: combustible, batería, scanner y "
                   "aire acondicionado.",
        # Destino NOMINAL. El real lo decide el formulario y queda en el
        # `estado_hacia` del movimiento: FR - MECANICA si el jefe de taller
        # tilda que espera repuesto, y el estado actual sin mover si la unidad
        # ya tiene proveedor DYP asignado. Ver modulos/taller.py.
        "estado_destino": "EN ESPERA DYP CONSOLIDADO",
        "pide": [],
        "formulario": "taller.pdi",
    },
    "lavado_produccion": {
        "titulo": "Lavado Producción",
        "detalle": "Lavado de salida.",
        "estado_destino": "ZONA DE LAVADO",
        "pide": [],
    },
    "inspeccion_despacho": {
        "titulo": "Inspección de Despacho",
        "detalle": "Registrar cómo sale la unidad: guía, destino, estanque, "
                   "kilometraje, llaves y fotos.",
        "estado_destino": "ZONA DE DESPACHO",
        "pide": [],
        # Como el check list y la revision de contenedor: son ~13 campos mas
        # una foto general mas hasta nueve de detalle, que no entran en la
        # tarjeta. El estado destino es el MISMO de origen porque la inspeccion
        # no mueve la unidad -- es un hito, no un arco.
        "formulario": "inspeccion_despacho.entrada",
    },
    "check_mecanica": {
        "titulo": "Check de Mecánica",
        "detalle": "Revisión mecánica (alimenta check_list_mecanica).",
        "estado_destino": "INGRESO A TALLER",
        "pide": [],
    },
}

# Motivos de desvio por tipo. Los dos puntos de desvio que exigen motivo si o
# si son los que la operacion ya tenia identificados: la vuelta a lavado y el
# rechazo de calidad que devuelve la unidad a taller.
MOTIVOS = {
    "lavado": ["Segundo Lavado", "Lavado de Revisión"],
    # CC -> INGRESO A TALLER esta en la matriz (5.1, "Rechazo de calidad,
    # retrabajo"), asi que no es un movimiento invalido: es un retroceso
    # legitimo y previsto. Se le pide motivo igual porque es el punto donde se
    # mide la calidad de la preparacion -- sin el, un retrabajo es
    # indistinguible de un avance normal en el historial.
    "cc_taller": [
        "Terminación rechazada (pintura, pulido, detalle)",
        "Daño detectado en el control de calidad",
        "Faltan accesorios o equipamiento",
        "Limpieza insuficiente",
    ],
    "generico": [
        "La unidad llegó en otro estado del esperado",
        "Se saltó un paso por urgencia de despacho",
        "El paso anterior ya estaba hecho y no figuraba",
        "Instrucción del cliente",
    ],
}


# ---------------------------------------------------------------------------
# Fase 2: catalogo de estados y matriz de transiciones
# ---------------------------------------------------------------------------
#
# Fuente: "Definicion del proceso de unidades -- Navegando a Despachado,
# clientes CARFLEX y CIDEF" v1.0 (19-08-2026), construido sobre 40.000
# movimientos reales del modulo Tracking entre el 20-03-2026 y el 13-08-2026
# (8.688 CARFLEX y 26.663 CIDEF).
#
# La matriz de los capitulos 4.1 y 5.1 se transcribe TAL CUAL. No se deriva
# del PHP: el documento es la definicion del proceso y el codigo viejo es una
# implementacion de el, con sus propios parches. Donde los dos difieren, manda
# el documento.
#
# Por que dos matrices y no una: el documento (1.1) muestra que CARFLEX y
# CIDEF no comparten la preparacion intermedia. CARFLEX va por inspeccion
# mecanica; CIDEF por el ciclo PDI/DYP. Una sola maquina de estados tendria
# que aceptar la union de las dos y dejaria de detectar justamente lo que hay
# que detectar -- el hallazgo 7 del documento es que hoy se registran estados
# de un cliente en unidades del otro.

# Como llega cada estado. Importa para saber que necesita formulario y que es
# solo lectura de otro modulo (capitulos 3.1 y 3.2):
#
#   manual  -- se elige en Mover Unidades > Actualizar Calle (3.1)
#   modulo  -- lo escribe otro modulo al ejecutar su operacion (3.2)
#   ambos   -- figura en las dos listas: se puede elegir a mano y ademas hay
#              un modulo que lo genera solo
MANUAL, MODULO, AMBOS = "manual", "modulo", "ambos"

ESTADOS = {
    "NAVEGANDO": {
        "origen": MODULO, "modulo": "Carga de embarque / nave", "cliente": "ambos"},
    "INGRESADO": {
        "origen": AMBOS, "modulo": "Ingreso/Despacho › Registrar ingreso VIN o PATENTE",
        "cliente": "ambos"},
    "ZONA DE RECEPCION": {
        "origen": MANUAL, "modulo": None, "cliente": "ambos"},
    "EN ESPERA DE CHECK LIST INGRESO": {
        "origen": MODULO, "modulo": "Check List de ingreso pendiente", "cliente": "CIDEF"},
    "EN ESPERA CHECK LIST MECANICA": {
        "origen": MODULO, "modulo": "Check List Mecánica pendiente", "cliente": "CARFLEX"},
    "SALIDA INSPECCION MECANICA": {
        "origen": MODULO, "modulo": "Cierre de inspección mecánica", "cliente": "CARFLEX"},
    "INSPECCION MECANICA DESPACHO": {
        "origen": MODULO, "modulo": "Inspección mecánica previa al despacho",
        "cliente": "CARFLEX"},
    "FALLA MECANICA": {
        "origen": MODULO, "modulo": "Resultado 'PRESENTA FALLAS' de la inspección",
        "cliente": "CARFLEX"},
    "EN ESPERA DE ASIGNACION DYP": {
        "origen": AMBOS, "modulo": "Cierre de recepción a la espera de PDI",
        "cliente": "CIDEF"},
    "EN ESPERA DYP CONSOLIDADO": {
        "origen": MODULO, "modulo": "Actualizar PDI (consolida a PDI)", "cliente": "CIDEF"},
    "DYP": {
        "origen": AMBOS, "modulo": "Actualizar DYP, según proveedor asignado",
        "cliente": "ambos"},
    "SALIDA DYP": {
        "origen": MODULO, "modulo": "Actualizar DYP (cierre del proveedor)",
        "cliente": "CIDEF"},
    "FR - MECANICA": {
        "origen": MODULO, "modulo": "Derivación a falla/reparación mecánica",
        "cliente": "CIDEF"},
    # El documento no lo clasificaba: aparece en la matriz de CARFLEX (4.1) y
    # en el anexo A.1, pero no estaba ni en 3.1 ni en 3.2, asi que quedo con
    # origen None en vez de inventarle uno.
    #
    # Resuelto contra el codigo real: es SELECCION MANUAL. Sale del endpoint
    # AJAX `modelos()` (Pedido.php:10583), que devuelve la lista de calles
    # segun el patio elegido, y figura como <option> de la lista de PATIO 2 --
    # junto con 'Falla Mecanica', 'Lavando', 'Vulcanizacion' y las 'Dyp *'. La
    # procesa el `case 'Servicios Generales'` del switch de PATIO 2, con estado
    # por defecto SERVICIOS GENERALES y sin escribir ninguna columna fecha_*.
    #
    # El dato acompaña: 150 movimientos, TODOS en PATIO 2; 148 quedaron en
    # SERVICIOS GENERALES y 2 en ZONA DE LAVADO por override del POST.
    "SERVICIOS GENERALES": {
        "origen": MANUAL, "modulo": "Mover Unidades › PATIO 2",
        "cliente": "CARFLEX"},
    "STOCK": {
        "origen": MANUAL, "modulo": None, "cliente": "ambos"},
    "STOCK CON DAÑO NO AUTORIZADO": {
        "origen": MANUAL, "modulo": None, "cliente": "ambos"},
    "NO DISPONIBLE": {
        "origen": MANUAL, "modulo": None, "cliente": "ambos"},
    "ZONA DE LAVADO": {
        "origen": AMBOS, "modulo": "Actualizar Lavado / Segundo Lavado", "cliente": "ambos"},
    "INGRESO A TALLER": {
        "origen": MANUAL, "modulo": None, "cliente": "ambos"},
    "CONTROL DE CALIDAD DESPACHO": {
        "origen": AMBOS, "modulo": "Control de calidad de terminación", "cliente": "ambos"},
    "ZONA DE DESPACHO": {
        "origen": MANUAL, "modulo": None, "cliente": "ambos"},
    # El despacho real es SIEMPRE por VIN, en portería: uno solo, o varios VIN
    # pegados separados por espacio cuando la grúa se lleva más de una unidad,
    # más el RUT del transportista. El "Despacho DT" existe en el codigo
    # (`despacho_dt_process()`, despacha en bloque todos los VIN de un DT) pero
    # en la practica no se uso nunca, asi que no se ofrece ni se nombra: dar
    # como destino una pantalla que nadie usa manda al operario al lugar
    # equivocado.
    #
    # El nombre del modulo lleva pegado que ademas manda el informe: la
    # tarjeta muestra `modulo` pero no el detalle del estado, y sin esa
    # aclaracion alguien puede entrar esperando un boton de "marcar como
    # despachado". Verificado en `inicio_proces()`, que adjunta los nueve
    # archivos de `inspeccion_despacho` -- guia firmada y fotos del scanner.
    "DESPACHADO": {
        "origen": MODULO,
        "modulo": "Ingreso/Despacho › Despacho VIN "
                  "(registra la salida y manda el informe de inspección)",
        "cliente": "ambos"},
}

# Lo que NO es un estado fisico y por eso no es nodo de la maquina (cap. 7).
# EDITAR es una edicion masiva de Herramientas.
#
# SOLICITUD DESPACHO SE MODELA COMO LO QUE ES: un evento comercial, no un
# lugar. Es la unica forma de modelarlo de verdad, y los datos lo sostienen --
# remedidos el 2026-08-27, y la premisa resulto MAS fuerte de lo que decia el
# comentario viejo:
#
#   - 2.138 movimientos historicos a este estado ocurren con la unidad todavia
#     NAVEGANDO o INGRESADA (el comentario decia 857);
#   - en los ultimos 6 meses son 911 de 4.344, el 21%;
#   - y HOY NO HAY NI UNA unidad parada en ese estado.
#
# O sea: es una marca transitoria que pisa la columna y que otra cosa
# sobrescribe enseguida. Si fuera nodo de la maquina, el motor recomendaria
# desde el a una unidad que fisicamente esta en el mar, y perderia su estado
# real. Se reconoce, se muestra y la reconciliacion no lo trata como
# desconocido -- pero no enruta.
NO_SON_ESTADO = {"SOLICITUD DESPACHO", "EDITAR"}

# Estados que EXISTEN en el legado y que REGLA reconoce, pero que no son nodos
# de la maquina de transiciones: se muestran, no se tratan como desconocidos, y
# el motor no recomienda desde ellos porque no estan en la matriz.
#
# Es una tercera categoria a proposito, distinta de ESTADOS y de NO_SON_ESTADO.
# Sin ella habia solo dos cajones -- "enruta" o "no es un estado" -- y estos
# tres no entran en ninguno: son estados de verdad, pasan de verdad, y el motor
# no tiene reglas para ellos.
#
# Vigencia medida en los ultimos 6 meses (2026-02-27 en adelante):
#   SOLICITUD DESPACHO      4.344   evento comercial, ver arriba
#   CC PDI                     64   control de calidad de la PDI
#   IT FALTA SEGUNDA PDI       45   la unidad necesita una segunda PDI
#
# LAVADO KSM (5 usos) queda deliberadamente afuera: la prueba de estados avisa
# si un estado con menos de 200 usos crece, y esa es la red por si reaparece.
RECONOCIDOS_SIN_RUTA = {
    "SOLICITUD DESPACHO": "Evento comercial: se pidió el despacho. No mueve la "
                          "unidad de lugar.",
    "CC PDI": "Control de calidad de la PDI.",
    "IT FALTA SEGUNDA PDI": "La unidad necesita una segunda PDI.",
}


def conocido(estado):
    """Si REGLA sabe QUE es este estado, aunque no sepa enrutarlo.

    Lo usa la reconciliacion: un estado reconocido y sin ruta no es una
    contradiccion ni un desconocido, es una zona del legado que REGLA todavia
    no maneja. Mezclarlos hace que el reporte pida atencion para algo que no
    la necesita."""
    canon = normalizar_estado(estado)
    return bool(canon) and (canon in ESTADOS or canon in RECONOCIDOS_SIN_RUTA
                            or canon in NO_SON_ESTADO)


# Variantes que son el mismo estado escrito de otra forma (hallazgos 3, 4 y 5
# del documento). Se resuelven ANTES de comparar contra el catalogo: si no, el
# motor no encuentra la transicion y falla en silencio -- devuelve "sin regla"
# para una unidad que en realidad esta en un estado perfectamente valido.
#
# La clave ya viene en mayusculas y sin tildes desde `normalizar`.
EQUIVALENCIAS = {
    # Hallazgo 4: el typo esta consolidado en 2.833 + 991 registros. Se corrige
    # de entrada para no arrastrarlo a la logica nueva.
    "SOLICTUD DESPACHO": "SOLICITUD DESPACHO",
    # Hallazgo 5: estados equivalentes duplicados.
    "EN ESPERA DE DYP CONSOLIDADO": "EN ESPERA DYP CONSOLIDADO",
    "EN ESPERA DYP": "EN ESPERA DE ASIGNACION DYP",
    # La misma sin el "DE", que es la forma MAS usada de las dos: 4.062
    # movimientos contra los que ya cubria la linea de arriba. Se escapaba por
    # una preposicion.
    "EN ESPERA ASIGNACION DYP": "EN ESPERA DE ASIGNACION DYP",
    # Typos consolidados. No se corrigen en la base -- el legado sigue
    # escribiendolos -- sino al leer, que es donde importa.
    "EN ESPERERA DYP": "EN ESPERA DE ASIGNACION DYP",      # 108
    "INGRESAO TALLER": "INGRESO A TALLER",                 # 11
    # Mismo estado con las palabras al reves.
    "EN ESPERA CONSOLIDADO DYP": "EN ESPERA DYP CONSOLIDADO",   # 9
    "FR": "FR - MECANICA",
    # Anexo A.1: 6 apariciones de la forma corta.
    "EN ESPERA CHECK MECANICA": "EN ESPERA CHECK LIST MECANICA",
    "STOCK SIN PDI": "STOCK",
    # El nombre viejo del control de calidad de despacho, 9.280 movimientos
    # entre 2022-08 y 2025-04. Va acá y NO como estado propio de la matriz
    # porque es el mismo paso renombrado, verificado por cinco lados:
    #
    #   - el relevo es limpio: el viejo muere en 2025 (3 registros) justo
    #     cuando el nuevo arranca (2024-11), y 2024 es el unico año en que
    #     conviven;
    #   - los producen las MISMAS acciones, 'CC' y 'Cc';
    #   - tienen los mismos vecinos: entran desde INGRESO A TALLER, ZONA DE
    #     LAVADO y STOCK, y salen a ZONA DE DESPACHO y DESPACHADO, con el
    #     mismo auto-bucle de re-control y la misma vuelta a taller;
    #   - el codigo de hoy NO puede escribirlo: el unico `case 'Cc'` y las
    #     seis ramas del motor de Lavado escriben todas la forma larga. Las
    #     cinco apariciones de la forma corta en `Examples.php` son
    #     `$crud->where(...)` de grillas de solo lectura, no asignaciones;
    #   - hoy no hay ninguna unidad en ninguno de los dos estados.
    "CONTROL DE CALIDAD": "CONTROL DE CALIDAD DESPACHO",
}

# Hallazgo 6: el proveedor esta modelado como estado. Para la matriz todos son
# el mismo nodo 'DYP'; cual proveedor es queda como dato aparte
# (`proveedor_dyp`), que es lo que recomienda el capitulo 10.6.
PROVEEDORES_DYP = {
    "DYP LOGAUTOS": "LOGAUTOS",
    "DYP AUTOREP": "AUTOREP",
    "DYP FABIAN": "FABIAN",
    "DYP FABIAN SANDOVAL": "FABIAN SANDOVAL",
    "DYP SERVICIOS LA": "SERVICIOS LA",
    "SALIDA DYP FABIAN": "FABIAN",
}


def normalizar_estado(crudo):
    """El estado listo para comparar contra el catalogo.

    Tres capas, en este orden: la limpieza mecanica de `catalogos.normalizar`
    (espacios, tildes, mayusculas), despues el proveedor DYP colapsado a su
    nodo, y al final las equivalencias del documento.

    El orden importa: 'DYP Fabian' tiene que pasar por la normalizacion
    mecanica antes de poder buscarse en PROVEEDORES_DYP, y 'SOLICTUD' solo se
    corrige una vez que ya esta en mayusculas."""
    texto = normalizar(crudo)
    if not texto:
        return ""
    if texto in PROVEEDORES_DYP:
        return "SALIDA DYP" if texto.startswith("SALIDA ") else "DYP"
    return EQUIVALENCIAS.get(texto, texto)


def proveedor_dyp(crudo):
    """Cual proveedor, cuando el estado venia con el nombre pegado."""
    return PROVEEDORES_DYP.get(normalizar(crudo))


# Matriz de transiciones validas, capitulos 4.1 y 5.1. Cada fila es
# (desde, hacia, condicion, principal), donde `principal` marca la ruta
# estandar cuando desde un mismo estado sale mas de un camino: es la que el
# motor recomienda, y las otras quedan disponibles como alternativa.
TRANSICIONES = {
    "CARFLEX": [
        ("NAVEGANDO", "INGRESADO", "Recepción física de la nave", True),
        ("INGRESADO", "ZONA DE RECEPCION", "Descarga y posicionamiento en ZR", True),
        ("ZONA DE RECEPCION", "EN ESPERA CHECK LIST MECANICA",
         "Derivación a inspección (ruta estándar)", True),
        ("ZONA DE RECEPCION", "STOCK", "Unidad sin inspección pendiente", False),
        ("EN ESPERA CHECK LIST MECANICA", "SALIDA INSPECCION MECANICA",
         "Check list mecánico ejecutado", True),
        ("SALIDA INSPECCION MECANICA", "STOCK", "Resultado OK", True),
        ("SALIDA INSPECCION MECANICA", "FALLA MECANICA", "Resultado PRESENTA FALLAS", False),
        ("SALIDA INSPECCION MECANICA", "DYP", "Requiere desabolladura y pintura", False),
        ("STOCK", "DYP", "Trabajo asignado a proveedor", False),
        ("STOCK", "SERVICIOS GENERALES", "Trabajo asignado a proveedor", False),
        ("FALLA MECANICA", "DYP", "Trabajo asignado a proveedor", True),
        ("FALLA MECANICA", "SERVICIOS GENERALES", "Trabajo asignado a proveedor", False),
        ("DYP", "STOCK", "Cierre del trabajo, unidad reintegrada", True),
        ("SERVICIOS GENERALES", "STOCK", "Cierre del trabajo, unidad reintegrada", True),
        ("STOCK", "INSPECCION MECANICA DESPACHO", "Inspección previa a entrega", False),
        ("STOCK", "ZONA DE LAVADO", "Lavado de preparación", True),
        ("ZONA DE LAVADO", "ZONA DE DESPACHO", "Lavado conforme", True),
        ("STOCK", "ZONA DE DESPACHO", "Posicionamiento para carga", False),
        ("INSPECCION MECANICA DESPACHO", "ZONA DE DESPACHO",
         "Posicionamiento para carga", True),
        ("ZONA DE DESPACHO", "CONTROL DE CALIDAD DESPACHO",
         "Control de calidad previo (opcional)", False),
        ("ZONA DE DESPACHO", "DESPACHADO", "Salida del recinto, por portería contra VIN y RUT", True),
        ("CONTROL DE CALIDAD DESPACHO", "DESPACHADO",
         "Salida del recinto, por portería contra VIN y RUT", True),
    ],
    "CIDEF": [
        ("NAVEGANDO", "INGRESADO", "Recepción física de la nave", True),
        ("INGRESADO", "ZONA DE RECEPCION", "Descarga y posicionamiento en ZR", True),
        ("ZONA DE RECEPCION", "EN ESPERA DE CHECK LIST INGRESO",
         "Check list de ingreso pendiente", False),
        ("EN ESPERA DE CHECK LIST INGRESO", "ZONA DE RECEPCION",
         "Check list ejecutado", True),
        ("ZONA DE RECEPCION", "EN ESPERA DE ASIGNACION DYP",
         "Recepción cerrada (ruta estándar)", True),
        ("EN ESPERA DE ASIGNACION DYP", "EN ESPERA DYP CONSOLIDADO",
         "Actualizar PDI ejecutado", True),
        ("EN ESPERA DYP CONSOLIDADO", "DYP", "Asignación de proveedor DYP", True),
        ("EN ESPERA DYP CONSOLIDADO", "FR - MECANICA",
         "Falla detectada, deriva a mecánica", False),
        ("EN ESPERA DYP CONSOLIDADO", "STOCK", "Unidad sin trabajos DYP requeridos", False),
        ("DYP", "SALIDA DYP", "Cierre del trabajo del proveedor", True),
        ("SALIDA DYP", "STOCK", "Unidad preparada, a stock", True),
        ("SALIDA DYP", "ZONA DE LAVADO", "Pasa directo a preparación de despacho", False),
        ("STOCK", "ZONA DE LAVADO", "Lavado de preparación", True),
        ("ZONA DE LAVADO", "INGRESO A TALLER", "Preparación final en taller", True),
        ("INGRESO A TALLER", "CONTROL DE CALIDAD DESPACHO",
         "Control de calidad de terminación", True),
        ("ZONA DE LAVADO", "CONTROL DE CALIDAD DESPACHO",
         "Ruta corta, sin paso por taller", False),
        ("CONTROL DE CALIDAD DESPACHO", "ZONA DE DESPACHO", "Calidad aprobada", True),
        ("CONTROL DE CALIDAD DESPACHO", "INGRESO A TALLER",
         "Rechazo de calidad, retrabajo", False),
        ("ZONA DE DESPACHO", "DESPACHADO", "Salida del recinto, por portería contra VIN y RUT", True),
    ],
}

# Los dos puntos donde el motivo es obligatorio aunque la transicion sea
# valida. Son retrocesos: sin motivo, en el historial se ven iguales que un
# avance y dejan de ser medibles.
DESVIOS_CON_MOTIVO = {
    ("CONTROL DE CALIDAD DESPACHO", "INGRESO A TALLER"): "cc_taller",
    ("INGRESO A TALLER", "ZONA DE LAVADO"): "lavado",
    ("CONTROL DE CALIDAD DESPACHO", "ZONA DE LAVADO"): "lavado",
    ("ZONA DE DESPACHO", "ZONA DE LAVADO"): "lavado",
}


def perfil_de(cliente):
    """Que matriz le corresponde a esta unidad.

    Solo CARFLEX y CIDEF tienen proceso definido en el documento. Para el
    resto se usa el de CIDEF -- es el ciclo de preparacion completo y el mas
    frecuente -- pero se devuelve tambien que fue un supuesto, para que la
    pantalla lo diga en vez de presentarlo como regla."""
    cliente = normalizar(cliente)
    if cliente in TRANSICIONES:
        return cliente, False
    return "CIDEF", True


def transiciones_desde(cliente, estado):
    """Las transiciones validas desde este estado, la principal primero."""
    perfil, _ = perfil_de(cliente)
    salidas = [t for t in TRANSICIONES[perfil] if t[0] == estado]
    return sorted(salidas, key=lambda t: not t[3])


def transicion_valida(cliente, desde, hacia):
    return any(t[1] == hacia for t in transiciones_desde(cliente, desde))


def motivo_obligatorio(desde, hacia):
    """Que lista de motivos hay que pedir, o None si no hace falta pedir."""
    return DESVIOS_CON_MOTIVO.get((desde, hacia))


# Pasos que ocurren en el mismo momento y NO dependen uno del otro: la unidad
# llega, se registra su ingreso y se revisa el contenedor en que vino, en el
# orden que se pueda. Verificado en el recorrido del sistema real: son dos
# pantallas separadas sin ninguna secuencia forzada entre ellas.
#
# Importa para el desvio: hacer uno cuando el motor sugeria el otro NO es
# desviarse, es hacer las cosas en el orden en que se pudieron hacer. Pedir un
# motivo ahi seria pedirlo todo el tiempo, y un motivo que se pide siempre
# deja de significar algo.
PASOS_INDEPENDIENTES = {"ingreso", "revision_contenedor"}

# Los pasos que ademas de mover el estado piden DONDE quedo la unidad.
#
# Hoy es uno solo. Es un conjunto y no un `if paso == "stock"` porque el que
# viene detras es 'zona_despacho' -- Zd tambien es una calle concreta de PATIO
# 1 -- y porque tenerlo como conjunto obliga a que la respuesta se busque en un
# solo lugar: la pantalla, el POST y el push preguntan todos aca.
PASOS_CON_UBICACION = {"stock"}


# Estados que produce un paso cuyo nombre no coincide con el del estado. La
# PDI es el caso: el motor recomienda el ESTADO al que hay que llegar
# ('espera_dyp_consolidado' o 'fr_mecanica', segun el tilde del jefe de
# taller), pero el movimiento se registra como el paso 'pdi', que es el nombre
# con el que el resto del motor lo conoce -- entre otras cosas, es su clave de
# hito. Sin esta equivalencia, hacer la PDI justo cuando el motor la pedia
# figuraria como desvio.
PASO_QUE_PRODUCE = {
    "espera_dyp_consolidado": "pdi",
    "fr_mecanica": "pdi",
}


def es_desvio(recomendado, elegido):
    """Si elegir `elegido` teniendo `recomendado` cuenta como desvio."""
    if elegido == recomendado:
        return False
    if PASO_QUE_PRODUCE.get(recomendado) == elegido:
        return False
    if recomendado in PASOS_INDEPENDIENTES and elegido in PASOS_INDEPENDIENTES:
        return False
    return True


# ---------------------------------------------------------------------------
# Los pasos de fase 2, derivados del catalogo
# ---------------------------------------------------------------------------
#
# No se escriben a mano uno por uno: se generan desde ESTADOS para que no
# puedan divergir del catalogo. Si manana se agrega un estado, aparece como
# paso solo; si se le cambia el origen, deja de pedir formulario solo.
#
# `solo_lectura` sale del origen: un estado que escribe otro modulo (3.2) no
# se confirma desde esta pantalla. Mostrarlo igual, en gris y diciendo que
# modulo lo genera, es mejor que esconderlo -- el movilizador necesita saber
# que la unidad esta esperando que Actualizar DYP la mueva, no que la pantalla
# se quedo sin recomendacion.

CLAVE_DE_ESTADO = {
    "EN ESPERA DE CHECK LIST INGRESO": "espera_check_list_ingreso",
    "EN ESPERA CHECK LIST MECANICA": "espera_check_mecanica",
    "SALIDA INSPECCION MECANICA": "salida_inspeccion_mecanica",
    "INSPECCION MECANICA DESPACHO": "inspeccion_mecanica_despacho",
    "FALLA MECANICA": "falla_mecanica",
    "EN ESPERA DE ASIGNACION DYP": "espera_asignacion_dyp",
    "EN ESPERA DYP CONSOLIDADO": "espera_dyp_consolidado",
    "DYP": "dyp",
    "SALIDA DYP": "salida_dyp",
    "FR - MECANICA": "fr_mecanica",
    "SERVICIOS GENERALES": "servicios_generales",
    "STOCK": "stock",
    "ZONA DE LAVADO": "lavado_produccion",
    "INGRESO A TALLER": "ingreso_taller",
    "CONTROL DE CALIDAD DESPACHO": "control_calidad",
    "ZONA DE DESPACHO": "zona_despacho",
    "DESPACHADO": "despachado",
}

# Titulo y explicacion de cada paso nuevo, con el texto del documento.
TEXTO_DE_ESTADO = {
    "EN ESPERA DE CHECK LIST INGRESO": (
        "En espera de Check List de Ingreso",
        "Check list de ingreso pendiente."),
    "EN ESPERA CHECK LIST MECANICA": (
        "En espera de Check List Mecánica",
        "Unidad en cola de inspección mecánica."),
    "SALIDA INSPECCION MECANICA": (
        "Salida de Inspección Mecánica",
        "Inspección cerrada. Si el resultado es PRESENTA FALLAS pasa a Falla Mecánica."),
    "INSPECCION MECANICA DESPACHO": (
        "Inspección Mecánica de Despacho",
        "Inspección mecánica previa a la entrega."),
    "FALLA MECANICA": (
        "Falla Mecánica",
        "Resultado PRESENTA FALLAS de la inspección."),
    "EN ESPERA DE ASIGNACION DYP": (
        "En espera de asignación DYP",
        "Recepción cerrada; la unidad espera asignación de PDI/DYP."),
    "EN ESPERA DYP CONSOLIDADO": (
        "En espera DYP consolidado",
        "PDI actualizada: la unidad se consolida para desabolladura y pintura."),
    "DYP": (
        "DYP",
        "Trabajo de desabolladura y pintura asignado a un proveedor."),
    "SALIDA DYP": (
        "Salida DYP",
        "Cierre del trabajo del proveedor."),
    "FR - MECANICA": (
        "FR - Mecánica",
        "Falla detectada, deriva a mecánica."),
    "SERVICIOS GENERALES": (
        "Servicios Generales",
        "Trabajo asignado a Servicios Generales."),
    "STOCK": (
        "Stock",
        "Unidad preparada y disponible, en su patio y calle definitiva."),
    "INGRESO A TALLER": (
        "Ingreso a Taller",
        "Preparación final en taller."),
    "CONTROL DE CALIDAD DESPACHO": (
        "Control de Calidad de Despacho",
        "Control de calidad de terminación."),
    "ZONA DE DESPACHO": (
        "Zona de Despacho",
        "Unidad aprobada y posicionada para carga."),
    # No es un "marcar como despachado": la misma pantalla arma y manda el
    # informe de inspección de despacho. Verificado en `inicio_proces()`, que
    # lee los nueve archivos de `inspeccion_despacho` (guía firmada y fotos del
    # scanner) y los adjunta al correo. Decirlo en la tarjeta evita que alguien
    # entre esperando un botón de un solo click.
    "DESPACHADO": (
        "Despachado",
        "Salida por portería: se registra contra el VIN y el RUT del "
        "transportista, y se manda el informe de inspección de despacho."),
}


# A donde lleva la tarjeta cuando el estado sugerido lo escribe otro modulo.
# Un estado de solo lectura sin destino deja al operario mirando un cartel: la
# pantalla le dice que ese paso no se hace desde acá, pero no adonde ir.
#
# Revisados los nueve, uno por uno, contra las rutas que hoy existen en la app:
#
#   EN ESPERA DE CHECK LIST INGRESO  -> check_list.formulario   INTEGRADO
#   EN ESPERA CHECK LIST MECANICA    -> Check List Mecánica     falta
#   SALIDA INSPECCION MECANICA       -> cierre de inspección    falta
#   INSPECCION MECANICA DESPACHO     -> inspección de despacho  falta
#   FALLA MECANICA                   -> resultado de inspección falta
#   EN ESPERA DYP CONSOLIDADO        -> Actualizar PDI          falta
#   SALIDA DYP                       -> Actualizar DYP          falta
#   FR - MECANICA                    -> derivación a mecánica   falta
#   DESPACHADO                       -> Despacho VIN            falta
#
# Ocho de los nueve todavia no tienen su formulario en Python: sus modulos no
# estan migrados. Para esos la tarjeta dice que modulo lo hace y que todavia no
# esta integrado, que es la verdad -- inventarles un boton que no lleva a
# ningun lado seria peor que no tenerlo.
# estado -> (endpoint del formulario, como se llama el formulario). El nombre
# va aparte porque el boton tiene que decir a que se entra ("Check List de
# Ingreso") y no repetir el nombre del estado ("En espera de Check List de
# Ingreso"), que es la espera y no la accion.
FORMULARIO_DE_ESTADO = {
    "EN ESPERA DE CHECK LIST INGRESO": ("check_list.formulario",
                                        "Check List de Ingreso"),
    # Los dos destinos de la PDI llevan al mismo formulario: cual de los dos
    # queda lo decide el tilde de FR - MECANICA adentro.
    "EN ESPERA DYP CONSOLIDADO": ("taller.pdi", "PDI"),
    "FR - MECANICA": ("taller.pdi", "PDI"),
    # Y los dos del IT, que se reparten por cliente.
    "INGRESO A TALLER": ("taller.it", "Resultado de revisión IT"),
    "INSPECCION MECANICA DESPACHO": ("taller.it", "Resultado de revisión IT"),
}


def _registrar_pasos_de_fase_2():
    """Agrega al catalogo de pasos los estados del documento que faltaban.

    `lavado_produccion` ya existia de la fase 1 y no se pisa: solo se le
    completa lo que la fase 2 necesita saber de el."""
    for estado, clave in CLAVE_DE_ESTADO.items():
        info = ESTADOS[estado]
        # Un estado que escribe otro modulo no se confirma desde aca. El que
        # figura en las dos listas (3.1 y 3.2) SI se puede elegir a mano: eso
        # es lo que significa estar en el selector de Mover Unidades.
        solo_lectura = info["origen"] == MODULO
        destino = FORMULARIO_DE_ESTADO.get(estado)
        comun = {
            "estado_destino": estado,
            "solo_lectura": solo_lectura,
            "modulo": info["modulo"],
            "origen": info["origen"],
            # `pendiente_integrar` es lo que separa "este paso se hace en otra
            # pantalla" de "este paso todavia no existe en Python". La tarjeta
            # necesita distinguirlos para no prometer un boton que no hay.
            "pendiente_integrar": solo_lectura and not destino,
        }
        if destino:
            comun["formulario"], comun["formulario_texto"] = destino
        if clave in PASOS:
            PASOS[clave].update(comun)
            continue
        titulo, detalle = TEXTO_DE_ESTADO[estado]
        PASOS[clave] = dict(comun, titulo=titulo, detalle=detalle, pide=[])


_registrar_pasos_de_fase_2()

# El camino de vuelta, para traducir un estado en el paso que lo produce.
PASO_DE_ESTADO = dict(CLAVE_DE_ESTADO)


def _vacio(valor, ceros=("", "0000-00-00", "0", "0000", "00000")):
    """Distinto del `vacio` de core: acá también son 'sin dato' los ceros con
    que se rellenan las guías ('0000', '00000'), que aparecen en la columna
    `g_ingreso` de unidades que todavía no ingresaron de verdad."""
    if valor is None:
        return True
    return str(valor).strip() in ceros


# ---------------------------------------------------------------------------
# Lectura del estado real de la unidad
# ---------------------------------------------------------------------------

# Que hito deja cumplido cada paso al registrarse.
HITO_DE_PASO = {
    "ingreso": "ingresada",
    "revision_contenedor": "revision_contenedor",
    "lavado_revision": "lavado_revision",
    "check_list_ingreso": "check_list",
    "pdi": "pdi",
    "check_mecanica": "check_mecanica",
}


def hitos_de(unidad):
    """Que ya cumplio esta unidad. Se mira el dato, no un contador de pasos:
    una unidad puede tener la PDI hecha aunque su estado diga otra cosa.

    A lo que dice la replica se le SUPERPONEN los movimientos registrados
    desde REGLA. Es lo que hace que el flujo avance: la replica es una foto
    del sistema viejo y no se toca -- se usa como patron de comparacion contra
    produccion, escribirle encima la arruinaria -- asi que si el avance no se
    superpusiera, registrar un ingreso no cambiaria nada y la pantalla
    seguiria recomendando el mismo paso para siempre."""
    hitos = {
        "ingresada": not _vacio(unidad["g_ingreso"]) or not _vacio(unidad["ingreso"]),
        "revision_contenedor": _tiene_contenedor_por_vin(unidad["vin"]),
        "lavado_revision": not _vacio(unidad["fecha_lavado_y_combustible"]),
        "check_list": not _vacio(unidad["fecha_check_list"]),
        "pdi": not _vacio(unidad["fecha_pdi"]),
        "check_mecanica": not _vacio(unidad["fecha_check_list_mecanica"]),
    }
    for paso in _pasos_registrados(unidad["id"]):
        hito = HITO_DE_PASO.get(paso)
        if hito:
            hitos[hito] = True
    return hitos


def _pasos_registrados(unidad_id):
    """Los pasos que ya se registraron desde REGLA para ESTA PASADA.

    Por `unidad_id`, jamas por VIN, y es el mas peligroso de los tres que
    estaban mal: esto alimenta `hitos_de` y por lo tanto a `recomendar`. Por
    VIN, un vehiculo que reingresa hereda los pasos de su pasada anterior y el
    motor le recomienda seguir desde donde quedo la otra vez -- salteandole
    justo lo que hay que rehacer.

    Comprobado sobre el dato real: la unidad 80022 figuraba con el paso 'pdi'
    cumplido sin haberlo hecho nunca (era de la pasada 91987), y la 90389 con
    'control_calidad' e 'ingreso_taller' de la 92082."""
    if not unidad_id:
        return set()
    db = get_db()
    _asegurar_tabla(db)
    db.commit()
    return {f["paso"] for f in consultar(
        "SELECT DISTINCT paso FROM movimientos_regla WHERE unidad_id = ?",
        (unidad_id,))}


def estado_de_movimiento(paso, estado_hacia):
    """El estado que declara UN movimiento, o None si no declara ninguno.

    Vive aparte porque hay tres lugares que necesitan lo mismo -- la ficha, el
    listado y el buscador de taller -- y con la regla copiada, la primera
    correccion los desincroniza. Ya paso con la ficha y el listado mostrando
    cosas distintas de la misma unidad; no vale la pena repetirlo dentro de un
    solo modulo."""
    # `estado_hacia` es donde la unidad quedo DE VERDAD, y manda sobre el
    # `estado_destino` del paso. Ver la nota larga en estado_efectivo.
    if estado_hacia:
        return estado_hacia
    # Movimientos viejos, anteriores a que se guardara el arco.
    if paso in PASOS:
        return PASOS[paso]["estado_destino"]
    return None


# `estados_regla_de` y `difieren_estados` VIVIERON ACA y se borraron el
# 2026-08-27. Servian para superponer "lo que REGLA sabe" sobre la columna
# cruda del listado, con una marca cuando los dos no coincidian.
#
# Dejaron de tener sentido cuando el estado paso a salir de la FILA: la columna
# ES lo que REGLA sabe, porque `registrar()` la escribe al guardar. La marca
# habria quedado apagada para siempre, que es la peor forma de morir de un
# aviso -- sigue en la pantalla y ya no significa nada.
#
# La pregunta que respondian de verdad -- "¿se entero el sistema anterior?" --
# no se perdio: se la hace la reconciliacion a la COLA, que la contesta mejor
# porque distingue en camino, trabado y conflicto.


def viaja_al_legado(clave_paso):
    """Si un movimiento por ese paso llega al sistema anterior.

    La pantalla lo usa para avisarle al operario. Sin el aviso, elegir DYP se
    ve exactamente igual que elegir STOCK y no pasa nada del otro lado -- que
    es la queja que origino todo este trabajo.

    TOMA EL PASO Y NO EL ESTADO, y esa distincion costo un bug. `calle_para`
    devuelve None para STOCK, porque su calle NO sale de `CALLE_POR_ESTADO`
    sino del formulario -- es el unico estado asi. Mirando solo el estado, la
    pantalla avisaba que STOCK no viaja, que es exactamente lo contrario de lo
    que acabamos de construir.

    Import diferido: `push_legado` no importa `movimientos`, pero `taller` si
    importa los dos y el orden se vuelve fragil arriba."""
    from modulos.push_legado import calle_para
    paso = PASOS.get(clave_paso) or {}
    if clave_paso in PASOS_CON_UBICACION:
        # Su calle la pone el operario, asi que viaja aunque la tabla no la
        # sepa deducir.
        return True
    return calle_para(paso.get("estado_destino")) is not None


def estado_efectivo(unidad):
    """El estado de la unidad: LA FILA DE LA REPLICA, siempre.

    CAMBIO DE ARQUITECTURA (2026-08-27). Hasta hoy esta funcion DERIVABA el
    estado del ultimo movimiento de `movimientos_regla` y caia al crudo solo si
    no habia ninguno. De ahi salian dos verdades que conciliar, y de las dos
    verdades salio casi todo el enredo: los dos estados en la ficha, la marca de
    divergencia en el listado, las categorias 1 y 2 de la reconciliacion.

    EL MOTIVO ES DE DATO, NO DE GUSTO. `registros` -- el historial del legado --
    no sirve como fuente: el 18,4% de los cambios de estado no deja fila ahi,
    porque hay 58 lugares del PHP que actualizan la unidad sin llamar a
    `registromov()`. `newstocks_cidef` tiene el estado completo SIEMPRE, sin
    importar por que camino se escribio. Un historial con agujeros no puede ser
    la fuente de un estado; una fila que siempre esta, si.

    Que REGLA derivara de SU historial era el mismo error del otro lado: el
    historial de REGLA es completo para lo que REGLA hizo, y ciego para todo lo
    demas.

    Y no se pierde nada: al guardar un movimiento REGLA escribe TAMBIEN la fila
    (ver `registrar`), asi que la pantalla refleja el cambio sin esperar el
    round trip. Si el push choca 409, el pull la corrige -- gana el legado, que
    es la regla que ya tenemos.

    Devuelve el estado, a secas. Devolvia una tupla `(estado, desde_regla)` y
    el segundo valor se borro el 2026-08-27: era siempre False, y un booleano
    que nunca cambia es peor que no estar -- invita a escribir un `if` que
    nadie va a ejecutar.


    EL SUPUESTO SOBRE EL QUE SE APOYA TODO ESTO
    ===========================================

    **Que TODO cambio del legado mueve `updated_at`.** Es lo que hace que el
    pull vea el cambio y que la fila siga siendo cierta.

    Si una grilla de administracion escribe `despachado` sin tocar
    `updated_at`, el pull no trae esa fila -- filtra por marca de agua -- y
    REGLA muestra un estado viejo COMO SI FUERA CERTERO. Antes de este cambio,
    la pantalla mostraba los dos valores y la discrepancia se veia sola; ahora
    hay uno solo y no hay con que compararlo.

    ESTA RAMA NO LO RESUELVE Y NO PRETENDE HACERLO. Queda escrito porque es el
    supuesto que sostiene la arquitectura nueva, y porque el dia que falle no
    se va a ver como un error sino como un dato correcto.

    Lo que se sabe hoy: `newstocks_cidef.updated_at` esta poblado en el 85,6%
    de las filas -- o sea que el 14,4% nunca lo tuvo --, y hay 111 escrituras a
    `despachado` repartidas en 24 funciones del legado. Cuantas de esas 111
    tocan `updated_at` NO ESTA MEDIDO. Franco lo esta averiguando.

    Si resulta que hay caminos que no lo tocan, las salidas son dos y ninguna
    es esta rama: un trigger del lado MySQL que lo mantenga, o una
    reconciliacion periodica completa que ignore la marca de agua -- el pull ya
    sabe hacerla, es `--desde ''`."""
    return unidad["despachado"]


def _tiene_contenedor_por_vin(vin):
    """El cruce con `contenedor` es por texto porque `vines` guarda todos los
    VIN del contenedor en un solo campo separados por ' | '."""
    if not vin:
        return False
    fila = consultar(
        "SELECT 1 FROM contenedor WHERE vines LIKE ? LIMIT 1",
        ("%{}%".format(vin),), una=True)
    return fila is not None


def _tiene_inspeccion_despacho_por_vin(vin):
    """Import diferido a proposito: `inspeccion_despacho` importa de este
    modulo (registrar, recomendar, estado_fisico), asi que hacerlo arriba seria
    un import circular. Es el unico lugar donde el motor necesita algo de ese
    modulo."""
    from modulos.inspeccion_despacho import tiene_inspeccion_por_vin
    return tiene_inspeccion_por_vin(vin)


def es_retorno(unidad):
    """True si este VIN ya pasó antes por el sistema.

    Se mira si hay otra fila del mismo VIN con id menor, porque cada fila de
    newstocks_cidef es UNA PASADA por el patio y no un vehículo (61.447 VIN
    distintos en 71.546 filas). De las 70 unidades que hoy están en zona de
    recepción, 12 son retorno."""
    if not unidad["vin"]:
        return False
    fila = consultar(
        'SELECT 1 FROM "{}" WHERE vin = ? AND id < ? LIMIT 1'.format(TABLA),
        (unidad["vin"], unidad["id"]), una=True)
    return fila is not None


def estado_fisico(unidad):
    """El estado fisico de la unidad, ya normalizado.

    SOLICITUD DESPACHO y EDITAR no mueven la unidad (cap. 7 del documento):
    son un evento comercial y una edicion masiva que hoy pisan la columna de
    estado. Si la unidad quedo marcada con uno de ellos, su estado fisico es
    el ultimo que si lo era -- se busca hacia atras en el historial en vez de
    recomendar desde un no-estado."""
    crudo = estado_efectivo(unidad)
    estado = normalizar_estado(crudo)
    if estado not in NO_SON_ESTADO:
        return estado

    for fila in consultar(
            "SELECT estado, newestado FROM registros WHERE vin = ? "
            "ORDER BY id DESC LIMIT 40", (unidad["vin"],)):
        for candidato in (fila["estado"], fila["newestado"]):
            anterior = normalizar_estado(candidato)
            if anterior and anterior not in NO_SON_ESTADO and anterior in ESTADOS:
                return anterior
    return estado


# La columna "Condicion / evento" de la matriz no siempre es prosa: en varias
# filas es una condicion verificable contra los hitos de la unidad. Estas son
# las que se pueden evaluar con el dato que hay, y son las que evitan el error
# grueso -- sin ellas el motor toma siempre la ruta estandar y le recomienda
# saltarse el check list a una unidad que todavia no lo tiene.
CONDICION_DE_HITO = {
    ("ZONA DE RECEPCION", "EN ESPERA DE CHECK LIST INGRESO"):
        lambda h: not h["check_list"],
    ("ZONA DE RECEPCION", "EN ESPERA DE ASIGNACION DYP"):
        lambda h: h["check_list"],
    ("ZONA DE RECEPCION", "EN ESPERA CHECK LIST MECANICA"):
        lambda h: not h["check_mecanica"],
    ("ZONA DE RECEPCION", "STOCK"):
        lambda h: h["check_mecanica"],
}


def _reco_por_matriz(cliente, estado, hitos):
    """La recomendacion que sale de la matriz del documento, o None si este
    estado no es un nodo con salida en el perfil del cliente."""
    salidas = transiciones_desde(cliente, estado)
    if not salidas:
        return None

    # Primero las que cumplen su condicion. Si ninguna la cumple no se
    # descarta la matriz: se cae en el orden normal y decide el usuario.
    aplicables = [t for t in salidas
                  if CONDICION_DE_HITO.get((t[0], t[1]), lambda h: True)(hitos)]
    if aplicables:
        salidas = aplicables + [t for t in salidas if t not in aplicables]

    _, hacia, condicion, _ = salidas[0]
    clave = PASO_DE_ESTADO.get(hacia)
    if clave is None:
        return None

    perfil, supuesto = perfil_de(cliente)
    porque = "Está en {}. Proceso {}: {}.".format(estado, perfil, condicion.rstrip("."))
    if supuesto:
        porque += (" Sin proceso propio para {}: se usa el de CIDEF.".format(
            normalizar(cliente) or "este cliente"))

    alternativas = []
    for _, otro, cond, _p in salidas[1:]:
        clave_otro = PASO_DE_ESTADO.get(otro)
        if clave_otro:
            alternativas.append({
                "clave": clave_otro,
                "titulo": PASOS[clave_otro]["titulo"],
                "condicion": cond,
                "pide_motivo": motivo_obligatorio(estado, otro),
            })

    return _reco(clave, porque, desde=estado, condicion=condicion,
                 alternativas=alternativas, perfil=perfil, supuesto=supuesto)


def recomendar(unidad):
    """El siguiente paso sugerido, con el porqué a la vista.

    Manda la matriz de transiciones del documento (caps. 4.1 y 5.1): si el
    estado actual es un nodo con salida en el perfil del cliente, el siguiente
    paso sale de ahi. Las reglas de la fase 1 quedan de respaldo para el tramo
    que el documento no modela -- revision de contenedor y lavado de revision
    no son estados suyos, pero son pasos reales de la operacion."""
    cliente = normalizar(unidad["clientecompleto"])
    hitos = hitos_de(unidad)
    estado = estado_fisico(unidad)

    # La revision de contenedor se evalua ANTES que la matriz, y no es un
    # parche: no es un estado del documento -- la unidad no se mueve de ZONA DE
    # RECEPCION al hacerla -- asi que la matriz no la conoce y nunca la
    # ofreceria. Si se dejara decidir a la matriz, una unidad CIDEF recien
    # ingresada saltaria directo al check list y este paso, que ocurre al
    # desconsolidar y antes del check list, no se sugeriria jamas.
    if (cliente == "CIDEF"
            and estado in ("ZONA DE RECEPCION", "INGRESADO")
            and not hitos["revision_contenedor"]
            and not hitos["check_list"]
            and not es_retorno(unidad)):
        return _reco("revision_contenedor",
                     "Primera vez de esta unidad en el sistema y es CIDEF: "
                     "corresponde revisión de contenedor antes del check list.",
                     desde=estado)

    # La inspeccion de despacho tampoco es un estado del documento: la unidad
    # entra y sale de ella en ZONA DE DESPACHO. Por eso va antes de la matriz,
    # por el mismo motivo que la revision de contenedor -- si se dejara decidir
    # a la matriz, ZONA DE DESPACHO derivaria directo a DESPACHADO y este paso
    # no se ofreceria nunca.
    #
    # Y el orden importa de verdad: la inspeccion documenta COMO sale la
    # unidad, asi que tiene que hacerse antes de que salga. Despues ya no hay
    # nada que documentar, y el propio sistema viejo la rechaza.
    if estado == "ZONA DE DESPACHO" and not _tiene_inspeccion_despacho_por_vin(unidad["vin"]):
        return _reco("inspeccion_despacho",
                     "Está en ZONA DE DESPACHO y todavía no tiene inspección "
                     "de despacho: se registra antes de que la unidad salga.",
                     desde=estado)

    por_matriz = _reco_por_matriz(cliente, estado, hitos)
    if por_matriz is not None:
        return por_matriz

    crudo = estado_efectivo(unidad)
    estado = normalizar(crudo)

    # `ingresada` cubre el caso de una unidad que sigue marcada Navegando en la
    # replica pero cuyo ingreso ya se registro desde REGLA.
    if estado == "NAVEGANDO" and not hitos["ingresada"]:
        return _reco("ingreso", "La unidad está navegando: falta registrar su ingreso.")

    if hitos["pdi"]:
        # Antes de acá el ciclo CIDEF entre recepción y stock era un solo paso
        # 'PDI'. El documento (cap. 5) muestra que son cuatro estados
        # encadenados -- EN ESPERA DE ASIGNACION DYP → EN ESPERA DYP
        # CONSOLIDADO → DYP (proveedor) → SALIDA DYP -- así que la PDI hecha no
        # deja la unidad en stock: la deja consolidada esperando el DYP. De ahí
        # en adelante manda la matriz.
        perfil, _supuesto = perfil_de(cliente)
        if perfil == "CIDEF":
            tras_pdi = _reco_por_matriz(cliente, "EN ESPERA DYP CONSOLIDADO", hitos)
            if tras_pdi is not None:
                tras_pdi["porque"] = "La PDI ya está hecha. " + tras_pdi["porque"]
                return tras_pdi
        return _reco("check_mecanica",
                     "La PDI ya está hecha. CARFLEX sigue con revisión mecánica.")

    if hitos["check_list"]:
        return _reco("pdi", "El check list de ingreso ya está hecho.")

    if hitos["revision_contenedor"] and not hitos["lavado_revision"]:
        return _reco("lavado_revision",
                     "La unidad tiene revisión de contenedor: corresponde el lavado previo "
                     "al check list.")

    # Zona de recepción (o ya ingresada) y sin check list todavía.
    if cliente == "CIDEF" and not es_retorno(unidad) and not hitos["revision_contenedor"]:
        return _reco("revision_contenedor",
                     "Primera vez de esta unidad en el sistema y es CIDEF: "
                     "corresponde revisión de contenedor antes del check list.")

    # El porqué tiene que decir la verdad de ESTA unidad: una que ya pasó por
    # revisión de contenedor y lavado llega acá por haber completado la
    # cadena, no por saltársela.
    if hitos["revision_contenedor"]:
        razon = "Ya pasó por revisión de contenedor y lavado: sigue el check list."
    elif cliente == "CIDEF" and es_retorno(unidad):
        razon = "Es retorno: no vuelve a pasar por revisión de contenedor."
    else:
        razon = "Cliente {}: va directo al check list, sin revisión de contenedor.".format(
            cliente or "sin identificar")
    return _reco("check_list_ingreso", razon)


def _reco(clave, porque, **extra):
    paso = dict(PASOS[clave])
    paso["clave"] = clave
    paso["porque"] = porque
    paso.setdefault("alternativas", [])
    paso.update(extra)
    return paso


# ---------------------------------------------------------------------------
# Registro de movimientos
# ---------------------------------------------------------------------------

def _asegurar_tabla(db):
    """Se crea al vuelo y con IF NOT EXISTS: el importador no la conoce, así
    que sobrevive a una reimportación de la réplica."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS movimientos_regla (
          id INTEGER PRIMARY KEY,
          unidad_id INTEGER,
          vin TEXT,
          paso TEXT,
          paso_recomendado TEXT,
          es_desvio INTEGER,
          motivo TEXT,
          motivo_detalle TEXT,
          resultado_pdi TEXT,
          guia_ingreso TEXT,
          fecha TEXT,
          responsable TEXT,
          usuario TEXT,
          creado_en TEXT
        )""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS ix_movimientos_regla_vin "
        "ON movimientos_regla (vin)")

    # Fase 2 agrega el arco: de que estado a que estado fue el movimiento. Se
    # agrega con ALTER y no cambiando el CREATE porque la tabla ya existe en
    # las notebooks donde se venia usando la fase 1, y un CREATE nuevo no la
    # tocaria. La regla de consistencia del documento (cap. 2) -- que el estado
    # anterior de un movimiento sea el actual del previo -- no se puede
    # verificar sin guardar los dos.
    ya_estan = {r[1] for r in db.execute("PRAGMA table_info(movimientos_regla)")}
    for columna in ("estado_desde", "estado_hacia"):
        if columna not in ya_estan:
            db.execute("ALTER TABLE movimientos_regla ADD COLUMN {} TEXT".format(columna))

    # La ubicacion fisica: donde quedo la unidad. Por el mismo ALTER y por el
    # mismo motivo que las dos de arriba.
    #
    # Se guardan ACA y no solo se mandan al legado porque son la fuente de la
    # recencia -- `ubicacion.tanda_en_curso` las lee para ordenar las calles de
    # la proxima pantalla. Es tambien lo que hace que REGLA pase de mirar la
    # ubicacion a saberla: hoy `patio`/`calle` llegan en el pull dentro de
    # `newstocks_cidef`, que es el estado ACTUAL sin historia ni autor.
    #
    # Solo se llenan en los movimientos que estacionan. En los demas quedan
    # NULL, que es distinto de vacio: vacio seria "quedo sin patio" y NULL es
    # "este movimiento no es de estacionamiento". `tanda_en_curso` filtra por
    # NULL y por vacio para no confundirlos.
    for columna in ("patio", "calle"):
        if columna not in ya_estan:
            db.execute("ALTER TABLE movimientos_regla ADD COLUMN {} TEXT".format(columna))

    # La consulta de la tanda filtra por fecha y ordena por fecha sobre las
    # filas con ubicacion, que son una minoria. Sin indice barre la tabla
    # entera en cada request de la pantalla.
    db.execute(
        "CREATE INDEX IF NOT EXISTS ix_movimientos_regla_ubicacion "
        "ON movimientos_regla (creado_en DESC) WHERE calle IS NOT NULL")

    # La guarda: rechaza filas sin unidad. Va acá porque esta
    # funcion ya corre en cada request y es idempotente.
    exigir_unidad_id(db, "movimientos_regla")

def movimientos_de_unidad(unidad_id):
    """El historial DE ESTA PASADA. Es lo que muestra /movimientos/<id>.

    Decision tomada: el historial es de la pasada, no del VIN. Un vehiculo que
    reingresa empieza una pasada nueva, y mezclar los movimientos de la
    anterior en la misma tabla presenta como propio algo que paso hace meses.
    Las anteriores se muestran, pero en un bloque aparte y rotulado -- ver
    `movimientos_por_vin`."""
    if not unidad_id:
        return []
    db = get_db()
    _asegurar_tabla(db)
    db.commit()
    return consultar(
        "SELECT * FROM movimientos_regla WHERE unidad_id = ? "
        "ORDER BY id DESC LIMIT 30", (unidad_id,))


def movimientos_por_vin(vin, excluir_unidad=None):
    """Los movimientos de las OTRAS pasadas de este VIN.

    El nombre dice la clave a proposito: este bug se repitio cuatro veces
    porque `movimientos_legado_por_vin(vin)` no delataba que emparejaba por VIN. Desde
    ahora, toda funcion que reciba un VIN lo dice en su nombre, para que el
    error se vea en el punto de llamada y no haya que entrar a leerla."""
    if not vin:
        return []
    db = get_db()
    _asegurar_tabla(db)
    db.commit()
    if excluir_unidad:
        return consultar(
            "SELECT * FROM movimientos_regla WHERE vin = ? AND unidad_id <> ? "
            "ORDER BY id DESC LIMIT 30", (vin, excluir_unidad))
    return consultar(
        "SELECT * FROM movimientos_regla WHERE vin = ? "
        "ORDER BY id DESC LIMIT 30", (vin,))


def registrar(unidad, datos):
    """Devuelve el id del movimiento escrito. Lo necesita el check list, que
    guarda su propia fila y la cuelga del movimiento que la originó."""
    db = get_db()
    _asegurar_tabla(db)
    cur = db.execute("""
        INSERT INTO movimientos_regla
          (unidad_id, vin, paso, paso_recomendado, es_desvio, motivo,
           motivo_detalle, resultado_pdi, guia_ingreso, fecha, responsable,
           usuario, creado_en, estado_desde, estado_hacia, patio, calle)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        unidad["id"], unidad["vin"], datos["paso"], datos["recomendado"],
        1 if datos["es_desvio"] else 0, datos.get("motivo"),
        datos.get("motivo_detalle"), datos.get("resultado_pdi"),
        datos.get("guia_ingreso"), datos.get("fecha"), datos.get("responsable"),
        id_actual(), datetime.now().isoformat(timespec="seconds"),
        datos.get("estado_desde"), datos.get("estado_hacia"),
        datos.get("patio"), datos.get("calle")))
    movimiento_id = cur.lastrowid

    # El push al legado, en el MISMO commit que la fila del movimiento.
    #
    # Se engancha acá y no en cada pantalla a proposito: `registrar` es el
    # unico camino por el que se escribe un movimiento -- lo llaman Movimientos,
    # PDI, IT, check list, revision de contenedor e inspeccion de despacho --,
    # asi que una sola linea cubre las seis. Engancharlo pantalla por pantalla
    # habria dejado a la proxima sin push y sin que nadie lo note.
    #
    # `encolar_movimiento` devuelve None cuando el estado destino no se puede
    # traducir a una calle (DYP, SALIDA DYP, DESPACHADO): esos no se empujan, y
    # NO es un error. Ver SIN_CALLE en push_legado.
    #
    # STOCK ya no esta en esa lista: desde el 2026-08-27 la pantalla pregunta
    # patio y calle, asi que la calle viaja porque el movilizador la dijo, no
    # porque REGLA la haya adivinado. Es el unico estado cuya calle y cuyo
    # patio vienen del formulario en vez de las tablas de traduccion.
    #
    # Import diferido: `push_legado` no importa `movimientos`, pero `taller` si
    # importa los dos y el orden se vuelve fragil al hacerlo arriba.
    # `empuja_movimiento=False` lo pasa la pantalla cuyo paso YA tiene su propia
    # entidad de push. Hoy es una sola: el IT.
    #
    # No es para evitar un choque tecnico -- aunque tambien lo evita: las dos
    # entradas saldrian con el mismo `legado_updated_at_conocido`, la primera
    # avanzaria el `updated_at` del legado y la segunda chocaria contra su
    # propia escritura con un 409 falso.
    #
    # El motivo de fondo es que EL LEGADO NO ESCRIBE ESA FILA. El bloque `It`
    # de Pedido.php:9219 cambia el estado y NO llama a `registromov()` -- son 0
    # llamadas, contadas --, que es la divergencia #1 que taller.py documento y
    # decidio no imitar EN NUESTRA tabla. Pero empujarla al legado seria meterle
    # a SU historial una fila que su propia pantalla nunca genera, y el
    # historial del legado es de donde salen sus reportes.
    #
    # El PDI es al reves: su bloque llama a registromov() dos veces, asi que
    # cuando entre esa entidad el movimiento SI se empuja.
    from modulos.push_legado import asegurar_tablas, encolar_movimiento
    asegurar_tablas(db)
    id_cola = None
    if datos.get("empuja_movimiento", True):
        id_cola = encolar_movimiento(db, unidad, movimiento_id,
                                     datos.get("estado_hacia"), id_actual(),
                                     calle=datos.get("calle"),
                                     patio=datos.get("patio"))

    # -- Y LA FILA DE LA REPLICA ---------------------------------------------
    #
    # Desde el cambio de arquitectura del 2026-08-27, el estado que ven las
    # pantallas sale de la FILA, no de este historial. Si el movimiento no la
    # escribiera, la pantalla mostraria el estado viejo hasta la proxima vuelta
    # del pull -- hasta 300 s de "no pasó nada" despues de confirmar.
    #
    # VA DESPUES DE `encolar_movimiento` Y NO ANTES, y el orden importa dos
    # veces:
    #
    #   1. `encolar_movimiento` lee `unidad["updated_at"]` para el locking
    #      optimista. `unidad` es la fila tal como la leyo el endpoint, asi que
    #      no cambiaria igual -- pero dejar la escritura despues hace que no
    #      dependa de eso.
    #   2. `encolar_movimiento` pone `push_pendiente = 1`, y ESA es la marca que
    #      impide que el proximo pull pise lo que acabamos de escribir. El
    #      UPSERT del pull saltea las filas con el flag en 1.
    #
    # `updated_at` NO SE TOCA. Es el reloj del legado y es contra el que se hace
    # el locking: escribirlo con el nuestro seria inventar una version. Regla 3.
    #
    # SOLO SI EL MOVIMIENTO VIAJA. Decidido el 2026-08-27 despues de medirlo:
    # para los estados de SIN_CALLE -- hoy DYP es el unico que la pantalla deja
    # elegir -- `encolar_movimiento` devuelve None y `push_pendiente` queda en
    # 0, asi que el proximo pull PISA esta escritura con el valor del legado.
    # Medido: la fila quedaba en 'DYP' y volvia a 'EN ESPERA DYP CONSOLIDADO'
    # en la vuelta siguiente. La pantalla mostraba el cambio y lo perdia, en
    # silencio, hasta 300 s despues.
    #
    # Escribirla igual seria peor que no escribirla: REGLA estaria afirmando un
    # estado que nunca va a entregar. La pantalla avisa en su lugar -- ver
    # `viaja_al_legado` y el aviso de `movimientos_unidad.html`.
    #
    # `updated_at` NO SE TOCA, ni siquiera cuando si viaja: es el reloj del
    # legado y es contra el que se hace el locking. Regla 3.
    if id_cola and datos.get("estado_hacia"):
        columnas = ["despachado = ?"]
        valores = [datos["estado_hacia"]]
        if datos.get("patio"):
            columnas.append("patio = ?")
            valores.append(datos["patio"])
        if datos.get("calle"):
            columnas.append("calle = ?")
            valores.append(datos["calle"])
        db.execute('UPDATE "{}" SET {} WHERE id = ?'.format(
            TABLA, ", ".join(columnas)), valores + [unidad["id"]])

    db.commit()

    # Despues del commit, nunca antes, y detras de PUSH_LEGADO_ACTIVO.
    if id_cola:
        from modulos.push_legado import disparar_push
        disparar_push(id_cola)
    return movimiento_id


# ---------------------------------------------------------------------------
# Vistas
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Asignación diaria por movilizador
# ---------------------------------------------------------------------------
#
# El PHP tiene `unidades_asignadas($id)`: filtra newstocks_cidef por
# encargado_patio = id y fecha_asignacion_movilizador = hoy, para que cada
# movilizador vea sus unidades del dia y no las 71.546.
#
# OJO CON EL DATO: la funcion existe pero casi no se uso. En todo el dump hay
# CINCO filas con asignacion, y cuatro son de prueba (VIN 'PRUEBAPRUEBA',
# 'VINARDO...', cliente 'PRUEBA'). La unica real es la unidad 80405, asignada
# el 2025-05-21. Ademas `encargado_patio` mezcla formatos: guarda ids ('666',
# '1007') y tambien nombres ('Carlos Cares') -- que resulto ser la misma
# persona escrita de dos maneras, ver `_identidades_del_movilizador`.
#
# Por eso la pantalla NO da por sentado que va a haber lista: cuando esta
# vacia lo dice y deja el buscador a mano, en vez de mostrar un panel en
# blanco que parece roto.
#
# La identidad ya NO se elige a mano: sale del usuario logueado.


def _identidades_del_movilizador():
    """Con que valores puede figurar el usuario logueado en `encargado_patio`.

    La columna mezcla formatos -- guarda ids ('666', '1007') y tambien nombres
    ('Carlos Cares') --, y ahora se sabe por que: los dos son la misma persona
    escrita de dos maneras. Se comprobo contra `tbl_users` que '666' y '1007'
    son userId reales y que el userId 1007 se llama exactamente 'Carlos Cares'.

    Con login real no hay que elegir cual de los dos formatos usar: se buscan
    los dos. Antes esto era imposible, porque la identidad se escribia a mano
    en un campo de texto y no habia con que cruzarla."""
    usuario = usuario_actual()
    if not usuario:
        return []
    valores = []
    if usuario["userId"] is not None:
        valores.append(str(usuario["userId"]))
    if usuario["name"]:
        valores.append(usuario["name"].strip())
    return valores


def unidades_asignadas(identidades, fecha):
    """Las unidades asignadas al movilizador para esa fecha.

    Se compara con TRIM porque `encargado_patio` es texto libre y ya se vio en
    otras columnas de esta tabla que los espacios sobrantes son la norma, y
    contra TODAS las formas en que el usuario puede figurar (su id y su
    nombre), por lo mismo que explica `_identidades_del_movilizador`."""
    if not identidades or not fecha:
        return []
    huecos = ",".join("?" for _ in identidades)
    return consultar(
        'SELECT id, vin, patente, marca, modelo, color, clientecompleto, '
        'despachado, patio FROM "{}" '
        "WHERE TRIM(encargado_patio) IN ({}) AND fecha_asignacion_movilizador = ? "
        "ORDER BY id DESC".format(TABLA, huecos),
        tuple(identidades) + (fecha,))


def _buscar(texto):
    """Busca por VIN exacto primero y por coincidencia después.

    El escáner entrega un VIN completo, así que la exacta es la que responde
    en ese caso; la parcial es la que sirve cuando se teclea de memoria o se
    lee mal una letra."""
    texto = (texto or "").strip()
    if not texto:
        return []

    limpio = vin_limpio(texto)
    if limpio:
        exactas = consultar(
            'SELECT {} FROM "{}" WHERE vin = ? ORDER BY id DESC'.format(
                ", ".join(BUSQUEDA_DEVUELVE), TABLA),
            (limpio,))
        if exactas:
            return exactas

    patron = "%{}%".format(texto)
    # `INDEXED BY` NO ES DECORACION Y NO ES PREMATURO: esta busqueda corre en
    # CADA TECLA de la busqueda en vivo, parado en el patio con un telefono.
    #
    # Un LIKE con `%` adelante no puede BUSCAR por indice -- hay que recorrer
    # todo --, pero si puede recorrer el INDICE en vez de la TABLA, y ahi esta
    # la diferencia: el indice cubridor pesa ~6 MB y `newstocks_cidef` pesa
    # 382. Medido en la replica: 66 ms -> 18 ms con la base caliente.
    #
    # Y en frio pesa mucho mas que eso, que es el caso real de Railway: el
    # volumen es lento, el pull escribe cada 300 s y desaloja paginas, y la
    # primera consulta despues de eso paga el archivo entero. Medida en frio
    # local: 980 ms.
    #
    # SE FUERZA porque el planificador no lo elige solo: con tres LIKE unidos
    # por OR descarta el indice y va a la tabla. Verificado con EXPLAIN QUERY
    # PLAN, antes y despues.
    #
    # Que `INDEXED BY` reviente si el indice no esta es DESEABLE: el indice lo
    # crea `core.instalar_indices()` al arrancar, y una busqueda que
    # silenciosamente vuelve a tardar un segundo es justo lo que nadie reporta.
    #
    # EL SELECT Y EL WHERE SE ARMAN CON LAS LISTAS DE `core`, las mismas con
    # las que se construye el indice. Escritos a mano serian dos listas que
    # tienen que coincidir y que nadie compara.
    return consultar(
        'SELECT {} FROM "{}" INDEXED BY ix_newstocks_busqueda WHERE {} '
        "ORDER BY id DESC LIMIT 25".format(
            ", ".join(BUSQUEDA_DEVUELVE), TABLA,
            " OR ".join("{} LIKE ?".format(c) for c in BUSQUEDA_FILTRA)),
        tuple(patron for _ in BUSQUEDA_FILTRA))


# La ruta /soy ya no existe. Era el parche con que se elegia a mano quien era
# el movilizador y se guardaba en la sesion, porque el login aceptaba
# cualquier usuario y no sabia de roles. Con login real la identidad sale del
# usuario autenticado, que es lo que esa nota anticipaba.


@bp.route("/")
def buscar():
    texto = request.args.get("q", "").strip()
    resultados = _buscar(texto)

    # Entrar directo a la unidad cuando hay un solo resultado es lo correcto
    # para el escaner y para el VIN escrito entero. Pero antes se disparaba con
    # CUALQUIER busqueda de un resultado, y eso rompia el tipeo:
    #
    #   escribiendo el VIN LVVDB21B1PD036098, al sexto caracter -- '036098' --
    #   ya no queda mas que una unidad en la replica, asi que la pantalla se
    #   iba sola a la ficha con el usuario a mitad de camino. Visto en video,
    #   cuadro por cuadro. Y explica el otro sintoma que parecia aparte: entrar
    #   por "buscar otra unidad", borrar y reescribir, y que "no tome el
    #   texto" -- no es que no lo tome, es que volvio a saltar.
    #
    # Por eso ahora hace falta una señal EXPLICITA de que el usuario termino.
    # Son dos, y alcanza con una:
    #
    #   - el envio no vino de la busqueda en vivo (Enter, el boton Buscar o el
    #     escaner, que arma la URL a mano): el usuario lo pidio;
    #   - lo tipeado ES el VIN completo de esa unidad. Se compara contra el VIN
    #     del resultado en vez de exigir 17 caracteres porque en la replica los
    #     VIN validos miden entre 15 y 19 (ver RE_VIN en kpis.py), asi que una
    #     regla de largo fijo dejaria unidades reales afuera.
    #
    # Sin señal, la lista se muestra igual aunque tenga un solo elemento: un
    # click de mas es barato, perder lo que se venia escribiendo no.
    # `fragmento=1` lo manda SOLO la busqueda en vivo, que pide el bloque de
    # resultados por fetch. Es la señal de "esto no lo pidio el usuario":
    # Enter, el boton Buscar y el escaner navegan de verdad y no lo llevan.
    en_vivo = request.args.get("fragmento") == "1"
    if texto and len(resultados) == 1:
        tipeado = vin_limpio(texto)
        es_vin_completo = bool(tipeado and tipeado == vin_limpio(resultados[0]["vin"]))
        if not en_vivo or es_vin_completo:
            return redirect(url_for("movimientos.unidad",
                                    id_unidad=resultados[0]["id"]))

    # La fecha se puede cambiar y no esta clavada en hoy. No es un capricho:
    # la ultima asignacion del dump es de 2025-06-17, asi que con "hoy" fijo
    # la pantalla seria imposible de probar contra el dato que existe.
    usuario = usuario_actual()
    fecha = request.args.get("fecha") or date.today().isoformat()
    identidades = _identidades_del_movilizador()

    if en_vivo:
        # Solo el bloque de resultados: el JS lo inyecta en `data-resultados`
        # sin tocar el resto de la pantalla.
        return render_template("_resultados_movimientos.html",
                               texto=texto, resultados=resultados)

    return render_template(
        "movimientos_buscar.html",
        texto=texto, resultados=resultados,
        movilizador=(usuario or {}).get("name", ""),
        fecha=fecha, hoy=date.today().isoformat(),
        asignadas=unidades_asignadas(identidades, fecha),
        identidades=identidades)


@bp.route("/<int:id_unidad>")
def unidad(id_unidad):
    fila = consultar('SELECT * FROM "{}" WHERE id = ?'.format(TABLA),
                     (id_unidad,), una=True)
    if fila is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404

    recomendado = recomendar(fila)
    # Los pasos de solo lectura no entran en 'otros': ese selector es para
    # elegir un movimiento distinto al recomendado, y esos no se pueden
    # ejecutar desde aca.
    otros = [dict(PASOS[c], clave=c) for c in PASOS
             if not PASOS[c].get("solo_lectura")
             and (not recomendado or c != recomendado["clave"])]
    estado = estado_efectivo(fila)
    fisico = estado_fisico(fila)
    perfil, supuesto = perfil_de(fila["clientecompleto"])

    return render_template(
        "movimientos_unidad.html",
        u=fila, recomendado=recomendado, otros=otros,
        estado=estado,
        estado_fisico=fisico, perfil=perfil, perfil_supuesto=supuesto,
        # Que motivo pedir para cada destino posible, para que el formulario
        # sepa cual lista mostrar sin repetir la regla en el template.
        motivo_por_paso={c: motivo_obligatorio(fisico, PASOS[c]["estado_destino"])
                         for c in PASOS},
        hitos=hitos_de(fila), retorno=es_retorno(fila),
        motivos=MOTIVOS, hoy=date.today().isoformat(),
        # El bloque de estacionamiento. Se calcula SIEMPRE y no solo cuando el
        # paso recomendado es 'stock', porque tambien se llega a STOCK por el
        # selector de desvio y ahi el bloque tiene que aparecer igual.
        ubic=ubicacion.sugerencia(get_db(), fila, id_actual()),
        pasos_con_ubicacion=PASOS_CON_UBICACION,
        # Que pasos NO llegan al sistema anterior. La pantalla lo dice ANTES de
        # que el operario confirme y despues de que confirmo: elegir uno de
        # estos se ve identico a elegir cualquier otro, y el movimiento queda
        # guardado en REGLA y en ningun lado mas.
        pasos_que_no_viajan={c for c in PASOS
                             if not PASOS[c].get("solo_lectura")
                             and PASOS[c].get("estado_destino")
                             and not viaja_al_legado(c)},
        historial=movimientos_de_unidad(fila["id"]))


@bp.route("/<int:id_unidad>/registrar", methods=["POST"])
def registrar_movimiento(id_unidad):
    fila = consultar('SELECT * FROM "{}" WHERE id = ?'.format(TABLA),
                     (id_unidad,), una=True)
    if fila is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404

    paso = request.form.get("paso", "")
    if paso not in PASOS:
        return redirect(url_for("movimientos.unidad", id_unidad=id_unidad))

    # Un estado que escribe otro modulo (cap. 3.2) no se confirma desde aca:
    # la unidad llega a el cuando Actualizar DYP, el check list o Despacho VIN
    # lo ejecutan. Dejar el boton activo seria prometer un movimiento que esta
    # pantalla no puede hacer.
    if PASOS[paso].get("solo_lectura"):
        return redirect(url_for("movimientos.unidad", id_unidad=id_unidad,
                                error="solo_lectura"))

    estado_desde = estado_fisico(fila)
    estado_hacia = PASOS[paso]["estado_destino"]

    # Los dos puntos de desvio con motivo obligatorio. Se valida en el POST y
    # no solo en el formulario: el motivo es el dato que hace medible el
    # retrabajo, y un submit sin el -- por javascript caido o por request
    # armado a mano -- dejaria el movimiento mudo justo donde mas importa.
    if motivo_obligatorio(estado_desde, estado_hacia) and not request.form.get("motivo"):
        return redirect(url_for("movimientos.unidad", id_unidad=id_unidad,
                                error="falta_motivo", paso=paso))

    # Los pasos que tienen formulario propio NO se registran acá: se manda al
    # usuario a llenarlo y el movimiento lo escribe ese formulario al
    # confirmar. El motivo del desvío viaja en la URL para no perderlo -- si
    # se registrara acá y además allá, el paso quedaría dos veces.
    destino = PASOS[paso].get("formulario")
    if destino:
        return redirect(url_for(
            destino, id_unidad=id_unidad,
            motivo=request.form.get("motivo") or None,
            motivo_detalle=request.form.get("motivo_detalle") or None))

    # DONDE QUEDO LA UNIDAD, para los pasos que estacionan.
    #
    # Se valida acá y no solo en el formulario, por lo mismo que el motivo del
    # desvío: `calle` es la UBICACION FISICA y de ella salen los reportes de
    # patio del legado. Un submit con javascript caído, o armado a mano, no
    # puede meter una calle que no existe -- y tampoco puede dejarla vacía y
    # que el push mande cadena vacía, que en la columna se ve igual que el
    # patio vacío que acabamos de terminar de arreglar.
    #
    # Se RECHAZA en vez de caer a un valor por defecto. No hay default posible:
    # la calle mayoritaria acierta el 25%, así que un default sería inventar la
    # ubicación tres de cada cuatro veces.
    patio = calle = None
    if paso in PASOS_CON_UBICACION:
        patio = (request.form.get("patio") or "").strip()
        calle = (request.form.get("calle") or "").strip()
        if not ubicacion.valida(patio, calle):
            return redirect(url_for("movimientos.unidad", id_unidad=id_unidad,
                                    error="falta_ubicacion", paso=paso))

    recomendado = recomendar(fila)
    clave_recomendada = recomendado["clave"] if recomendado else None
    desvio = es_desvio(clave_recomendada, paso)

    # El motivo se guarda tambien cuando el paso ES el recomendado pero la
    # transicion es uno de los dos retrocesos con motivo obligatorio: ahi el
    # dato no sobra, es el unico que distingue un retrabajo de un avance.
    pide_motivo = desvio or motivo_obligatorio(estado_desde, estado_hacia)

    registrar(fila, {
        "paso": paso,
        "recomendado": clave_recomendada,
        "es_desvio": desvio,
        "estado_desde": estado_desde,
        "estado_hacia": estado_hacia,
        "motivo": request.form.get("motivo") if pide_motivo else None,
        "motivo_detalle": request.form.get("motivo_detalle") if pide_motivo else None,
        "resultado_pdi": request.form.get("resultado_pdi") or None,
        "guia_ingreso": request.form.get("guia_ingreso") or None,
        "fecha": request.form.get("fecha") or None,
        # None y no cadena vacía en los pasos que no estacionan: la columna
        # distingue "este movimiento no es de estacionamiento" de "quedó sin
        # patio", y `tanda_en_curso` cuenta con esa distinción.
        "patio": patio,
        "calle": calle,
        # Firma el que esta logueado, no un nombre escrito a mano.
        "responsable": nombre_actual() or None,
    })

    return redirect(url_for("movimientos.unidad", id_unidad=id_unidad,
                            registrado=paso))
