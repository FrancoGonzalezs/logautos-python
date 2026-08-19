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
En ZONA DE DESPACHO. El paso a DESPACHADO lo escribe Ingreso/Despacho >
Despacho DT, no esta pantalla -- por eso figura como paso de solo lectura y no
como boton. Lo mismo con el ciclo PDI/DYP de CIDEF, que lo mueven Actualizar
PDI y Actualizar DYP.

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

from core import consultar, get_db
from modulos.acceso import id_actual, nombre_actual, usuario_actual
from modulos.catalogos import normalizar
# `vin_limpio` vive en kpis.py porque ahi nacio (filtra los VIN invalidos de
# los indicadores). Es el mismo criterio que hace falta aca para decidir si lo
# que entrego el escaner es un VIN o texto suelto, asi que se reusa en vez de
# escribir una segunda validacion que pueda divergir.
from modulos.kpis import vin_limpio
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
        "detalle": "Inspección de preentrega.",
        "estado_destino": "STOCK",
        "pide": [],
        # La PDI es el unico paso con resultado: no cambia cual es el siguiente
        # paso recomendado, pero si queda registrado cual de los tres fue.
        "resultados": [
            ("sin_novedad", "Sin novedad, nunca fue a taller"),
            ("taller_completado", "Fue a taller, reparación completada"),
            ("taller_no_completado", "Fue a taller, reparación no se completó"),
        ],
    },
    "lavado_produccion": {
        "titulo": "Lavado Producción",
        "detalle": "Lavado de salida.",
        "estado_destino": "ZONA DE LAVADO",
        "pide": [],
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
    # El documento no lo clasifica: aparece en la matriz de CARFLEX (4.1) y en
    # el anexo A.1, pero no esta ni en 3.1 ni en 3.2. Queda marcado para que la
    # validacion operativa lo resuelva, en vez de inventarle un origen.
    "SERVICIOS GENERALES": {
        "origen": None, "modulo": None, "cliente": "CARFLEX"},
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
    "DESPACHADO": {
        "origen": MODULO, "modulo": "Ingreso/Despacho › Despacho DT, VIN o PATENTE",
        "cliente": "ambos"},
}

# Lo que NO es un estado fisico y por eso no es nodo de la maquina (cap. 7).
# SOLICITUD DESPACHO es un evento comercial que hoy pisa la columna de estado
# (857 casos con la unidad todavia navegando), y EDITAR es una edicion masiva
# de Herramientas. Si se los tratara como estados, el motor recomendaria desde
# ellos y perderia el estado fisico real de la unidad.
NO_SON_ESTADO = {"SOLICITUD DESPACHO", "EDITAR"}


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
    "FR": "FR - MECANICA",
    # Anexo A.1: 6 apariciones de la forma corta.
    "EN ESPERA CHECK MECANICA": "EN ESPERA CHECK LIST MECANICA",
    "STOCK SIN PDI": "STOCK",
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
        ("ZONA DE DESPACHO", "DESPACHADO", "Emisión de DT y salida del recinto", True),
        ("CONTROL DE CALIDAD DESPACHO", "DESPACHADO",
         "Emisión de DT y salida del recinto", True),
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
        ("ZONA DE DESPACHO", "DESPACHADO", "Emisión de DT y salida del recinto", True),
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


def es_desvio(recomendado, elegido):
    """Si elegir `elegido` teniendo `recomendado` cuenta como desvio."""
    if elegido == recomendado:
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
    "DESPACHADO": (
        "Despachado",
        "Emisión de DT y salida del recinto."),
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
#   DESPACHADO                       -> Despacho DT             falta
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
        "revision_contenedor": _tiene_contenedor(unidad["vin"]),
        "lavado_revision": not _vacio(unidad["fecha_lavado_y_combustible"]),
        "check_list": not _vacio(unidad["fecha_check_list"]),
        "pdi": not _vacio(unidad["fecha_pdi"]),
        "check_mecanica": not _vacio(unidad["fecha_check_list_mecanica"]),
    }
    for paso in _pasos_registrados(unidad["vin"]):
        hito = HITO_DE_PASO.get(paso)
        if hito:
            hitos[hito] = True
    return hitos


def _pasos_registrados(vin):
    """Los pasos que ya se registraron desde REGLA para este VIN."""
    if not vin:
        return set()
    db = get_db()
    _asegurar_tabla(db)
    db.commit()
    return {f["paso"] for f in consultar(
        "SELECT DISTINCT paso FROM movimientos_regla WHERE vin = ?", (vin,))}


def estado_efectivo(unidad):
    """El estado que corresponde mostrar: el ultimo registrado desde REGLA si
    lo hay, y si no el de la replica."""
    if unidad["vin"]:
        ultimo = consultar(
            "SELECT paso FROM movimientos_regla WHERE vin = ? "
            "ORDER BY id DESC LIMIT 1", (unidad["vin"],), una=True)
        if ultimo and ultimo["paso"] in PASOS:
            return PASOS[ultimo["paso"]]["estado_destino"], True
    return unidad["despachado"], False


def _tiene_contenedor(vin):
    """El cruce con `contenedor` es por texto porque `vines` guarda todos los
    VIN del contenedor en un solo campo separados por ' | '."""
    if not vin:
        return False
    fila = consultar(
        "SELECT 1 FROM contenedor WHERE vines LIKE ? LIMIT 1",
        ("%{}%".format(vin),), una=True)
    return fila is not None


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
    crudo, _ = estado_efectivo(unidad)
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

    por_matriz = _reco_por_matriz(cliente, estado, hitos)
    if por_matriz is not None:
        return por_matriz

    crudo, _ = estado_efectivo(unidad)
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


def movimientos_de(vin):
    if not vin:
        return []
    db = get_db()
    _asegurar_tabla(db)
    db.commit()
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
           usuario, creado_en, estado_desde, estado_hacia)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        unidad["id"], unidad["vin"], datos["paso"], datos["recomendado"],
        1 if datos["es_desvio"] else 0, datos.get("motivo"),
        datos.get("motivo_detalle"), datos.get("resultado_pdi"),
        datos.get("guia_ingreso"), datos.get("fecha"), datos.get("responsable"),
        id_actual(), datetime.now().isoformat(timespec="seconds"),
        datos.get("estado_desde"), datos.get("estado_hacia")))
    db.commit()
    return cur.lastrowid


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
            'SELECT id, vin, patente, marca, modelo, color, clientecompleto, '
            'despachado FROM "{}" WHERE vin = ? ORDER BY id DESC'.format(TABLA),
            (limpio,))
        if exactas:
            return exactas

    patron = "%{}%".format(texto)
    return consultar(
        'SELECT id, vin, patente, marca, modelo, color, clientecompleto, '
        'despachado FROM "{}" '
        "WHERE vin LIKE ? OR patente LIKE ? OR n_motor LIKE ? "
        "ORDER BY id DESC LIMIT 25".format(TABLA),
        (patron, patron, patron))


# La ruta /soy ya no existe. Era el parche con que se elegia a mano quien era
# el movilizador y se guardaba en la sesion, porque el login aceptaba
# cualquier usuario y no sabia de roles. Con login real la identidad sale del
# usuario autenticado, que es lo que esa nota anticipaba.


@bp.route("/")
def buscar():
    texto = request.args.get("q", "").strip()
    resultados = _buscar(texto)

    # Un solo resultado: se entra directo, que es lo que pasa siempre que se
    # escanea un QR. Hacer clickear una lista de uno sería un paso al pedo.
    if texto and len(resultados) == 1:
        return redirect(url_for("movimientos.unidad", id_unidad=resultados[0]["id"]))

    # La fecha se puede cambiar y no esta clavada en hoy. No es un capricho:
    # la ultima asignacion del dump es de 2025-06-17, asi que con "hoy" fijo
    # la pantalla seria imposible de probar contra el dato que existe.
    usuario = usuario_actual()
    fecha = request.args.get("fecha") or date.today().isoformat()
    identidades = _identidades_del_movilizador()

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
    estado, desde_regla = estado_efectivo(fila)
    fisico = estado_fisico(fila)
    perfil, supuesto = perfil_de(fila["clientecompleto"])

    return render_template(
        "movimientos_unidad.html",
        u=fila, recomendado=recomendado, otros=otros,
        estado=estado, estado_desde_regla=desde_regla,
        estado_fisico=fisico, perfil=perfil, perfil_supuesto=supuesto,
        # Que motivo pedir para cada destino posible, para que el formulario
        # sepa cual lista mostrar sin repetir la regla en el template.
        motivo_por_paso={c: motivo_obligatorio(fisico, PASOS[c]["estado_destino"])
                         for c in PASOS},
        hitos=hitos_de(fila), retorno=es_retorno(fila),
        motivos=MOTIVOS, hoy=date.today().isoformat(),
        historial=movimientos_de(fila["vin"]))


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
    # la unidad llega a el cuando Actualizar DYP, el check list o Despacho DT
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
        # Firma el que esta logueado, no un nombre escrito a mano.
        "responsable": nombre_actual() or None,
    })

    return redirect(url_for("movimientos.unidad", id_unidad=id_unidad,
                            registrado=paso))
