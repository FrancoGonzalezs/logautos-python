"""
modulos/kpis.py -- los indicadores de Kpi.php.

Cada KPI es una funcion propia registrada en KPIS. No hay un "motor generico"
de KPIs a proposito: en Kpi.php cada indicador tiene su consulta, sus filtros y
sus rarezas, y ya se vio en acopio y OT que tratar de generalizar antes de
tiempo termina escondiendo justamente las diferencias que hay que verificar
una por una contra produccion. Agregar un KPI es escribir una funcion y
sumarla a la lista.

Cada funcion devuelve un dict con:
    clave, titulo, icono, numerador, denominador, tasa, meta, formula
`tasa` es None cuando el denominador es cero, y la tarjeta lo muestra como
"sin datos" en vez de dividir por cero o mostrar un 0% que se lee como bueno.
"""

import calendar
import re
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request

from core import consultar

bp = Blueprint("kpis", __name__, url_prefix="/kpis")

# Un VIN de verdad tiene 17 caracteres alfanumericos. Se acepta 15-19 para no
# descartar registros con un digito de mas o de menos, pero todo lo que quede
# fuera de ese rango es un placeholder: en el stock CIDEF de agosto los dos
# unicos rechazados son 'GCPS15' y 'PT5731', patentes escritas en el campo del
# VIN. Filtrar por formato evita tener que mantener una lista de exclusiones.
RE_VIN = re.compile(r"^[A-Z0-9]{15,19}$")


def vin_limpio(valor):
    """Normaliza un VIN y devuelve None si no tiene forma de VIN."""
    if valor is None:
        return None
    limpio = re.sub(r"[^A-Z0-9]", "", str(valor).upper())
    return limpio if RE_VIN.match(limpio) else None


def _vines(filas, campo="vin"):
    """El conjunto de VIN validos de un resultado."""
    salida = set()
    for fila in filas:
        limpio = vin_limpio(fila[campo])
        if limpio:
            salida.add(limpio)
    return salida


def _tarjeta(clave, titulo, icono, numerador, denominador, meta, formula,
             nota=None, mayor_es_mejor=False):
    tasa = round(100.0 * numerador / denominador, 2) if denominador else None
    return {
        "clave": clave, "titulo": titulo, "icono": icono,
        "numerador": numerador, "denominador": denominador,
        "tasa": tasa, "meta": meta, "formula": formula, "nota": nota,
        "mayor_es_mejor": mayor_es_mejor, "unidad": None,
    }


def _tarjeta_promedio(clave, titulo, icono, promedio, cantidad, unidad, meta,
                      formula, nota=None):
    """Varios KPI del PHP no son un porcentaje sino un promedio (dias en
    patio, minutos de inspeccion, lead time). El PHP los marca con
    tipo_kpi='promedio_dias'/'promedio_minutos' y mete el promedio en el campo
    `tasa` para reusar la misma tarjeta. Aca se separan: `promedio` es el
    numero grande y `denominador` la cantidad sobre la que se calculo."""
    return {
        "clave": clave, "titulo": titulo, "icono": icono,
        "numerador": None, "denominador": cantidad,
        "tasa": None, "promedio": promedio, "unidad": unidad,
        "meta": meta, "formula": formula, "nota": nota,
        "mayor_es_mejor": False,
    }


def _fecha_valida(valor):
    """Las tres formas de "sin fecha" que conviven en estas columnas: NULL, la
    fecha cero de MySQL y el string literal 'NULL'."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto == "" or texto == "0000-00-00" or texto.upper() == "NULL":
        return None
    try:
        return datetime.strptime(texto[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _minutos(fecha, hora_inicio, hora_fin, cruza_medianoche=True):
    """Minutos entre dos horas del mismo dia. Si el fin queda antes que el
    inicio se asume que cruzo la medianoche, igual que el PHP."""
    def _hora(texto):
        texto = str(texto or "").strip()
        if texto in ("", "00:00:00"):
            return None
        for formato in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(texto, formato).time()
            except ValueError:
                pass
        return None

    ini, fin = _hora(hora_inicio), _hora(hora_fin)
    if ini is None or fin is None:
        return None
    base = fecha or date(2000, 1, 1)
    dt_ini = datetime.combine(base, ini)
    dt_fin = datetime.combine(base, fin)
    if dt_fin < dt_ini:
        if not cruza_medianoche:
            return None
        dt_fin += timedelta(days=1)
    return (dt_fin - dt_ini).total_seconds() / 60.0


# ---------------------------------------------------------------------------
# KPI 1 -- Daños por Recepción (CIDEF)
# ---------------------------------------------------------------------------

def kpi_danos_recepcion(inicio, fin):
    """De las unidades CIDEF que ingresaron en el periodo, cuantas terminaron
    con una OT de DyP abierta a nombre de CIDEF dentro del mismo periodo.

    El cruce con orden_trabajo es por la columna `vehiculo`, que es donde vive
    el VIN: la tabla no tiene una columna `vin`."""
    denominador = _vines(consultar(
        "SELECT vin FROM newstocks_cidef "
        "WHERE TRIM(clientecompleto) = 'CIDEF' "
        "  AND ingreso BETWEEN ? AND ? "
        "  AND vin IS NOT NULL AND TRIM(vin) <> ''",
        (inicio.isoformat(), fin.isoformat())))

    con_dano = _vines(consultar(
        "SELECT vehiculo AS vin FROM orden_trabajo "
        "WHERE requerimiento = 'DYP' AND nombre LIKE '%CIDEF%' "
        "  AND createdDtm BETWEEN ? AND ?",
        (inicio.isoformat(), fin.isoformat() + " 23:59:59")))

    return _tarjeta(
        "danos_recepcion", "Daños por Recepción", "📦",
        len(denominador & con_dano), len(denominador), "< 5%",
        "unidades CIDEF ingresadas en el período con OT de DyP a nombre de "
        "CIDEF ÷ total de unidades CIDEF ingresadas × 100")


# ---------------------------------------------------------------------------
# KPI 2 -- Daños en Patio (STOCK)
# ---------------------------------------------------------------------------

def kpi_danos_patio(inicio, fin):
    """De las unidades CIDEF que estaban en stock al cierre del periodo,
    cuantas tuvieron una OT de DyP CERRADA a nombre de LOGAUTOS en el periodo.

    La OT va a nombre de LOGAUTOS y no de CIDEF a proposito: el daño se produjo
    en patio, o sea que lo asume el operador, no el cliente. Ese `LIKE
    '%LOGAUTOS%'` es lo unico que separa este indicador del anterior."""
    denominador = _vines(consultar(
        "SELECT vin FROM newstocks_cidef "
        "WHERE TRIM(clientecompleto) = 'CIDEF' "
        "  AND ingreso <= ? "
        "  AND (fecha_desp IS NULL OR fecha_desp = '' OR fecha_desp = '0000-00-00' "
        "       OR fecha_desp > ?) "
        "  AND vin IS NOT NULL AND TRIM(vin) <> ''",
        (fin.isoformat(), fin.isoformat())))

    con_dano = _vines(consultar(
        "SELECT vehiculo AS vin FROM orden_trabajo "
        "WHERE requerimiento = 'DYP' AND nombre LIKE '%LOGAUTOS%' "
        "  AND fecha_cierre BETWEEN ? AND ?",
        (inicio.isoformat(), fin.isoformat())))

    return _tarjeta(
        "danos_patio", "Daños en Patio", "🅿️",
        len(denominador & con_dano), len(denominador), "< 3%",
        "unidades CIDEF en stock al cierre con OT de DyP cerrada a nombre de "
        "LOGAUTOS ÷ total de unidades CIDEF en stock × 100")


# ---------------------------------------------------------------------------
# KPI 3 -- First Pass Yield (PDI)
# ---------------------------------------------------------------------------

# Lo que el taller escribe en `ob_mecanica` cuando NO encontro nada. Se compara
# en mayusculas y sin espacios de los bordes.
SIN_OBSERVACIONES = {"N/A", "NO", "SIN OBS", "NINGUNA"}


def _sin_observacion(valor):
    if valor is None:
        return True
    texto = str(valor).strip().upper()
    return texto == "" or texto in SIN_OBSERVACIONES


def kpi_first_pass_yield(inicio, fin):
    """De las unidades que pasaron PDI en el periodo, cuantas salieron sin
    ninguna observacion mecanica: pasaron bien a la primera.

    OJO: este KPI cuenta FILAS, no VIN unicos, a diferencia de Daños por
    Recepcion y Daños en Patio. La diferencia no es cosmetica -- en el lavado
    de agosto hay 127 filas para 126 VIN distintos, y produccion reporta 127.
    Tiene sentido: aca el numerador es una propiedad de la misma fila (su
    `ob_mecanica`), mientras que en los dos primeros habia que cruzar contra
    orden_trabajo y el VIN era la llave del cruce.

    Es el unico KPI donde MAS ALTO ES MEJOR."""
    filas = [f for f in consultar(
        "SELECT vin, ob_mecanica FROM newstocks_cidef "
        "WHERE TRIM(clientecompleto) = 'CIDEF' AND fecha_pdi BETWEEN ? AND ?",
        (inicio.isoformat(), fin.isoformat())) if vin_limpio(f["vin"])]

    limpias = sum(1 for f in filas if _sin_observacion(f["ob_mecanica"]))

    return _tarjeta(
        "first_pass_yield", "First Pass Yield (PDI)", "✅",
        limpias, len(filas), "> 95%",
        "unidades con PDI en el período y sin observación mecánica ÷ total de "
        "unidades con PDI en el período × 100",
        nota="Complemento de Tasa de Retrabajo",
        mayor_es_mejor=True)


# ---------------------------------------------------------------------------
# KPI 4 -- Retrabajo de Lavado
# ---------------------------------------------------------------------------

def _tiene_segundo_lavado(valor):
    """El campo trae tres formas distintas de decir "no hubo segundo lavado":
    NULL, la fecha cero de MySQL, y el string literal 'NULL' (108 de las 127
    filas de agosto son la fecha cero)."""
    if valor is None:
        return False
    texto = str(valor).strip()
    return texto != "" and texto != "0000-00-00" and texto.upper() != "NULL"


def kpi_retrabajo_lavado(inicio, fin):
    """De las unidades lavadas en el periodo, cuantas hubo que volver a lavar.

    El segundo lavado NO tiene que caer dentro del periodo: lo que se mide es
    cuantos de los lavados del mes terminaron necesitando otro, aunque el otro
    haya ocurrido despues. Por eso el numerador no filtra por fecha."""
    filas = [f for f in consultar(
        "SELECT vin, fecha_segundo_lavado FROM newstocks_cidef "
        "WHERE TRIM(clientecompleto) = 'CIDEF' "
        "  AND fecha_lavado_y_combustible BETWEEN ? AND ?",
        (inicio.isoformat(), fin.isoformat())) if vin_limpio(f["vin"])]

    relavadas = sum(1 for f in filas if _tiene_segundo_lavado(f["fecha_segundo_lavado"]))

    return _tarjeta(
        "retrabajo_lavado", "Retrabajo de Lavado", "🧼",
        relavadas, len(filas), "< 5%",
        "unidades lavadas en el período que necesitaron un segundo lavado ÷ "
        "total de unidades lavadas en el período × 100")


# ---------------------------------------------------------------------------
# KPI 5 -- Tasa de Retrabajo
# ---------------------------------------------------------------------------

def _fecha_poblada(valor):
    """True si el campo trae una fecha de verdad. Las fechas cero de MySQL
    ('0000-00-00') son 'sin fecha', no una fecha."""
    if valor is None:
        return False
    return str(valor).strip() not in ("", "0000-00-00")


def kpi_tasa_retrabajo(inicio, fin):
    """De las unidades que pasaron PDI en el periodo, cuantas llegaron a
    revision de salida o a control de calidad.

    Cuenta FILAS, igual que First Pass Yield: comparte con el su denominador
    (las unidades con PDI en el periodo) y el numerador vuelve a ser una
    propiedad de la misma fila.

    OJO al leer el 0% de un mes en curso: los dos campos del numerador se
    llenan en pasos posteriores del flujo, asi que las unidades que hicieron
    PDI hace pocos dias todavia no llegaron ahi. En agosto (mes joven) las 18
    filas tienen los dos campos en '0000-00-00' y el indicador da 0%, mientras
    que los meses cerrados van entre 66% y 97%. No es un filtro roto: los
    campos se usan (7.679 filas de CIDEF tienen fecha_revision_salida y 4.975
    tienen fecha_cc), simplemente no las de este mes."""
    filas = [f for f in consultar(
        "SELECT vin, fecha_revision_salida, fecha_cc FROM newstocks_cidef "
        "WHERE TRIM(clientecompleto) = 'CIDEF' "
        "  AND fecha_pdi BETWEEN ? AND ? "
        "  AND fecha_pdi IS NOT NULL AND fecha_pdi <> '' AND fecha_pdi <> '0000-00-00'",
        (inicio.isoformat(), fin.isoformat())) if vin_limpio(f["vin"])]

    con_retrabajo = sum(1 for f in filas
                        if _fecha_poblada(f["fecha_revision_salida"])
                        or _fecha_poblada(f["fecha_cc"]))

    return _tarjeta(
        "tasa_retrabajo", "Tasa de Retrabajo", "🔁",
        con_retrabajo, len(filas), "≥ 80%",
        "unidades con PDI en el período que llegaron a revisión de salida o a "
        "control de calidad ÷ total de unidades con PDI en el período × 100",
        mayor_es_mejor=True)


# ---------------------------------------------------------------------------
# KPI 6 -- Reprocesos de DyP
# ---------------------------------------------------------------------------

def kpi_reprocesos_dyp(inicio, fin):
    """De las OT de DyP de CIDEF cerradas en el periodo, cuantas quedaron
    marcadas para reproceso.

    La marca vive en la columna `atraso`, no en una que se llame reproceso: es
    el campo real del sistema viejo, no un error de mapeo. Ojo con esa columna
    si se la usa para otra cosa -- no es un booleano limpio: ademas de 'SI'
    (983 filas) y 'NO' (42), tiene valores numericos sueltos ('12', '14', '15',
    '16'...) de algun uso anterior, y en 120.551 de las 121.592 OT esta en
    NULL. Para este KPI solo cuenta el 'SI' exacto.

    Este KPI mira orden_trabajo directo, no cruza contra newstocks_cidef: el
    denominador son OT, no unidades. Cuenta filas, sin deduplicar por VIN."""
    filas = [f for f in consultar(
        "SELECT vehiculo, atraso FROM orden_trabajo "
        "WHERE nombre = 'CIDEF' AND requerimiento = 'DYP' "
        "  AND fecha_cierre BETWEEN ? AND ? "
        "  AND fecha_cierre IS NOT NULL AND fecha_cierre <> '' "
        "  AND fecha_cierre <> '0000-00-00'",
        (inicio.isoformat(), fin.isoformat())) if vin_limpio(f["vehiculo"])]

    reprocesos = sum(1 for f in filas
                     if str(f["atraso"] or "").strip().upper() == "SI")

    return _tarjeta(
        "reprocesos_dyp", "Reprocesos de DyP", "🔧",
        reprocesos, len(filas), "< 10%",
        "OT de DyP de CIDEF cerradas en el período marcadas para reproceso ÷ "
        "total de OT de DyP de CIDEF cerradas en el período × 100")


# ---------------------------------------------------------------------------
# KPI 7 y 8 -- lo que vuelve despues del despacho
# ---------------------------------------------------------------------------

def _vines_despachados(inicio, fin):
    """Los VIN de CIDEF despachados en el periodo, deduplicados.

    Denominador compartido por Reclamos y Retorno Sucursales. Se deduplica
    porque el numerador sale de cruzar contra otra tabla y el VIN es la llave
    del cruce -- misma familia que Daños por Recepcion y Daños en Patio.
    Importa: en agosto son 163 filas para 162 VIN, y hay unidades que se
    despachan dos veces en el mismo mes (el VIN LGJE5EE08TM521278 salio el 19
    y el 22 de mayo), asi que sin deduplicar el mismo vehiculo pesaria doble."""
    return _vines(consultar(
        "SELECT vin FROM newstocks_cidef "
        "WHERE TRIM(clientecompleto) = 'CIDEF' "
        "  AND fecha_desp BETWEEN ? AND ? "
        "  AND fecha_desp IS NOT NULL AND fecha_desp <> '' "
        "  AND fecha_desp <> '0000-00-00'",
        (inicio.isoformat(), fin.isoformat())))


def _vines_en_tabla(tabla, inicio, fin):
    return _vines(consultar(
        "SELECT vin FROM {} WHERE fecha BETWEEN ? AND ?".format(tabla),
        (inicio.isoformat(), fin.isoformat())))


def kpi_reclamos_concesionarios(inicio, fin):
    """De las unidades despachadas en el periodo, cuantas generaron un reclamo
    del concesionario.

    `incidentes` esta VACIA en el dump (cero filas), asi que este indicador da
    0% siempre por ahora. No es un bug del cruce: es que la tabla no tiene una
    sola fila todavia."""
    despachados = _vines_despachados(inicio, fin)
    con_incidente = _vines_en_tabla("incidentes", inicio, fin)

    return _tarjeta(
        "reclamos_concesionarios", "Reclamos de Concesionarios por PDI", "📣",
        len(despachados & con_incidente), len(despachados), "< 5%",
        "unidades despachadas en el período con al menos un incidente "
        "registrado en el período ÷ total de unidades despachadas × 100",
        nota="La tabla `incidentes` está vacía: el 0% es real, no un cruce roto")


def kpi_retorno_sucursales(inicio, fin):
    """De las unidades despachadas en el periodo, cuantas volvieron.

    `retornos` tiene 2 filas en todo el dump (mayo y junio de 2026), asi que
    agosto da 0%. El cruce SI funciona y esta verificado contra esos meses:
    mayo da 1/689 y junio 1/643."""
    despachados = _vines_despachados(inicio, fin)
    con_retorno = _vines_en_tabla("retornos", inicio, fin)

    return _tarjeta(
        "retorno_sucursales", "Retorno Sucursales", "↩️",
        len(despachados & con_retorno), len(despachados), "< 5%",
        "unidades despachadas en el período con al menos un retorno registrado "
        "en el período ÷ total de unidades despachadas × 100",
        nota="Basado en tabla retornos (ingreso manual por correo)")


# ---------------------------------------------------------------------------
# Tiempos de inspección al ingreso
# ---------------------------------------------------------------------------

def kpi_tiempo_contenedor(inicio, fin):
    """Minutos de inspeccion por unidad al desconsolidar un contenedor.

    Los RORO se excluyen mirando `nro_sello`: un contenedor real trae sello y
    los registros RORO se cargan con '000000'."""
    total_contenedores = total_unidades = 0
    suma = 0.0

    for f in consultar(
            "SELECT fecha_completa_inicio, fecha_completa_fin, cantidad_unidades "
            "FROM contenedor WHERE fecha BETWEEN ? AND ? "
            "  AND nro_sello IS NOT NULL AND nro_sello <> '000000'",
            (inicio.isoformat(), fin.isoformat())):
        ini = _instante(f["fecha_completa_inicio"])
        termino = _instante(f["fecha_completa_fin"])
        try:
            cantidad = int(f["cantidad_unidades"] or 0)
        except (TypeError, ValueError):
            cantidad = 0
        if ini is None or termino is None or cantidad <= 0:
            continue
        minutos = (termino - ini).total_seconds() / 60.0
        if minutos < 0:
            continue
        suma += minutos
        total_unidades += cantidad
        total_contenedores += 1

    promedio = round(suma / total_unidades, 1) if total_unidades else 0.0
    return _tarjeta_promedio(
        "tiempo_contenedor", "Tiempo Inspección (Contenedor)", "📦",
        promedio, total_unidades, "min/unidad", "Minutos",
        "Σ(fin − inicio de inspección) ÷ total de unidades del contenedor",
        nota="{} contenedor(es) inspeccionado(s)".format(total_contenedores))


def _instante(valor):
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto or texto.startswith("0000-00-00"):
        return None
    for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            pass
    return None


def kpi_tiempo_roro(inicio, fin):
    """Minutos de inspeccion de un ingreso RORO (nave con rampa, sin
    contenedor). Fuente propia: la tabla `ingresos_roro`."""
    total = 0
    suma = 0.0
    for f in consultar(
            "SELECT hora_entrada, hora_salida FROM ingresos_roro "
            "WHERE fecha BETWEEN ? AND ?",
            (inicio.isoformat(), fin.isoformat())):
        minutos = _minutos(None, f["hora_entrada"], f["hora_salida"])
        if minutos is None or minutos < 0:
            continue
        suma += minutos
        total += 1

    promedio = round(suma / total, 1) if total else 0.0
    return _tarjeta_promedio(
        "tiempo_roro", "Tiempo Inspección (RORO)", "🚢",
        promedio, total, "min", "Minutos",
        "Promedio(hora_salida − hora_entrada)")


# ---------------------------------------------------------------------------
# PDI
# ---------------------------------------------------------------------------

def kpi_promedio_pdi(inicio, fin):
    """Cuanto dura una PDI, en minutos. Sale de la tabla `promedio_pdi`, que
    es la unica que guarda hora de inicio y de fin del proceso."""
    total = 0
    suma = 0.0
    for f in consultar(
            "SELECT fecha, hora_inicio, hora_fin FROM promedio_pdi "
            "WHERE fecha BETWEEN ? AND ? "
            "  AND hora_inicio IS NOT NULL AND hora_fin IS NOT NULL "
            "  AND hora_inicio <> '' AND hora_fin <> '' "
            "  AND hora_inicio <> '00:00:00' AND hora_fin <> '00:00:00'",
            (inicio.isoformat(), fin.isoformat())):
        minutos = _minutos(_fecha_valida(f["fecha"]), f["hora_inicio"], f["hora_fin"])
        if minutos is None or minutos < 0:
            continue
        suma += minutos
        total += 1

    promedio = round(suma / total, 1) if total else 0.0
    return _tarjeta_promedio(
        "promedio_pdi", "Promedio Diario de PDI", "⏱️",
        promedio, total, "min", "Minutos",
        "Promedio(hora_fin − hora_inicio) de las PDI del período")


def kpi_efectividad_pdi(inicio, fin):
    """De las unidades que llegaron en las motonaves del periodo, a cuantas se
    les hizo la PDI.

    Se agrupa por motonave y se deduplica por VIN. La PDI NO tiene que caer
    dentro del periodo: lo que se mide es cuanto de lo comprometido por cada
    buque ya se proceso, aunque se haya procesado despues."""
    comprometidas = realizadas = 0
    motonaves = set()
    vistos = set()

    for f in consultar(
            "SELECT vin, motonave, fecha_pdi FROM newstocks_cidef "
            "WHERE TRIM(clientecompleto) = 'CIDEF' "
            "  AND ingreso BETWEEN ? AND ? "
            "  AND ingreso IS NOT NULL AND ingreso <> '' AND ingreso <> '0000-00-00' "
            "  AND motonave IS NOT NULL AND motonave <> '' "
            "  AND vin IS NOT NULL AND vin <> ''",
            (inicio.isoformat(), fin.isoformat())):
        vin = vin_limpio(f["vin"])
        if not vin or vin in vistos:
            continue
        vistos.add(vin)
        motonave = str(f["motonave"] or "").strip()
        if not motonave:
            continue
        motonaves.add(motonave.upper())
        comprometidas += 1
        if _fecha_valida(f["fecha_pdi"]):
            realizadas += 1

    return _tarjeta(
        "efectividad_pdi", "Efectividad PDI", "🎯",
        realizadas, comprometidas, "100%",
        "PDI realizadas ÷ unidades comprometidas por motonave × 100",
        nota="{} motonave(s) · {} unidad(es) pendientes".format(
            len(motonaves), comprometidas - realizadas),
        mayor_es_mejor=True)


# ---------------------------------------------------------------------------
# Tiempos en patio y lead times
# ---------------------------------------------------------------------------

def kpi_dias_patio(inicio, fin):
    """Cuantos dias promedio pasa una unidad en patio.

    Se excluyen 'Navegando' y 'RECHAZADO': no llegaron al patio. El termino es
    la fecha de despacho, topeada a la fecha de corte para que una unidad
    despachada despues del cierre no infle el promedio del mes."""
    total = 0
    suma = 0
    for f in consultar(
            "SELECT vin, ingreso, fecha_desp FROM newstocks_cidef "
            "WHERE TRIM(clientecompleto) = 'CIDEF' "
            "  AND ingreso IS NOT NULL AND ingreso <> '' AND ingreso <> '0000-00-00' "
            "  AND ingreso <= ? "
            "  AND (despachado IS NULL OR despachado NOT IN ('Navegando', 'RECHAZADO')) "
            "  AND (fecha_desp IS NULL OR fecha_desp = '' OR fecha_desp = '0000-00-00' "
            "       OR fecha_desp >= ?) "
            "  AND vin IS NOT NULL AND vin <> ''",
            (fin.isoformat(), inicio.isoformat())):
        if not vin_limpio(f["vin"]):
            continue
        ingreso = _fecha_valida(f["ingreso"])
        if ingreso is None:
            continue
        desp = _fecha_valida(f["fecha_desp"])
        termino = min(desp, fin) if desp else fin
        if termino < ingreso:
            continue
        suma += (termino - ingreso).days
        total += 1

    promedio = round(suma / float(total), 1) if total else 0.0
    return _tarjeta_promedio(
        "dias_patio", "Días Promedio en Patio", "🅿️",
        promedio, total, "días", "Días",
        "Σ(ingreso → despacho o corte) ÷ unidades activas en el mes",
        nota="Incluye despachadas en el mes y pendientes al cierre")


def _lead_time_solicitud(inicio, fin, tipo_destino, clave, titulo):
    """Dias entre la solicitud de despacho y el despacho efectivo.

    Si la solicitud quedo registrada ANTES del ingreso de la unidad, se usa el
    ingreso como arranque: no se le puede cobrar al proceso un tiempo en el
    que el vehiculo todavia no estaba."""
    condicion = ""
    params = [inicio.isoformat(), fin.isoformat()]
    if tipo_destino:
        condicion = " AND tipo_destino = ?"
        params.append(tipo_destino)

    total = 0
    suma = 0
    for f in consultar(
            "SELECT vin, ingreso, fecha_solicitud, fecha_desp FROM newstocks_cidef "
            "WHERE TRIM(clientecompleto) = 'CIDEF' "
            "  AND destino IS NOT NULL AND destino <> '' "
            "  AND fecha_solicitud BETWEEN ? AND ? "
            "  AND fecha_solicitud IS NOT NULL AND fecha_solicitud <> '' "
            "  AND fecha_solicitud <> '0000-00-00' "
            "  AND ingreso IS NOT NULL AND ingreso <> '' AND ingreso <> '0000-00-00' "
            "  AND fecha_desp IS NOT NULL AND fecha_desp <> '' "
            "  AND fecha_desp <> '0000-00-00' "
            "  AND vin IS NOT NULL AND vin <> ''" + condicion, params):
        if not vin_limpio(f["vin"]):
            continue
        ingreso = _fecha_valida(f["ingreso"])
        solicitud = _fecha_valida(f["fecha_solicitud"])
        desp = _fecha_valida(f["fecha_desp"])
        if not (ingreso and solicitud and desp):
            continue
        arranque = max(solicitud, ingreso)
        if desp < arranque:
            continue
        suma += (desp - arranque).days
        total += 1

    promedio = round(suma / float(total), 1) if total else 0.0
    return _tarjeta_promedio(
        clave, titulo, "🚚", promedio, total, "días", "< 3 días",
        "Promedio de días entre MAX(fecha_solicitud, ingreso) y fecha_desp")


def kpi_lead_time_despacho(inicio, fin):
    return _lead_time_solicitud(
        inicio, fin, None, "lead_time_despacho",
        "Lead Time Despacho (Solicitud → Despacho)")


def kpi_lead_time_despacho_sucursal(inicio, fin):
    return _lead_time_solicitud(
        inicio, fin, "SUCURSAL", "lead_time_despacho_sucursal",
        "Lead Time Despacho — Sucursal")


def kpi_lead_time_despacho_concesionario(inicio, fin):
    return _lead_time_solicitud(
        inicio, fin, "CONCESIONARIO", "lead_time_despacho_concesionario",
        "Lead Time Despacho — Concesionario")


def kpi_lead_time(inicio, fin):
    """Dias entre que la unidad llega a zona de despacho y sale."""
    total = 0
    suma = 0
    for f in consultar(
            "SELECT vin, fecha_zd, fecha_desp FROM newstocks_cidef "
            "WHERE TRIM(clientecompleto) = 'CIDEF' "
            "  AND fecha_desp BETWEEN ? AND ? "
            "  AND fecha_desp IS NOT NULL AND fecha_desp <> '0000-00-00' "
            "  AND fecha_zd IS NOT NULL AND fecha_zd <> '0000-00-00' "
            "  AND vin IS NOT NULL AND vin <> ''",
            (inicio.isoformat(), fin.isoformat())):
        if not vin_limpio(f["vin"]):
            continue
        zd = _fecha_valida(f["fecha_zd"])
        desp = _fecha_valida(f["fecha_desp"])
        if not (zd and desp) or desp < zd:
            continue
        suma += (desp - zd).days
        total += 1

    promedio = round(suma / float(total), 1) if total else 0.0
    return _tarjeta_promedio(
        "lead_time", "Lead Time Total (ZD a Despacho)", "⏳",
        promedio, total, "días", "Días",
        "Promedio de días entre llegada a ZD y despacho")


# ---------------------------------------------------------------------------
# Cumplimiento e incidencia
# ---------------------------------------------------------------------------

def _cumplimiento_preparacion(inicio, fin):
    """Devuelve (programadas, cumplidas) deduplicando por VIN.

    Cumple si llego a zona de despacho, o si se despacho a mas tardar el dia
    programado. La comparacion de fechas es de TEXTO, igual que en el PHP --
    con formato ISO ordena bien igual."""
    programadas = set()
    cumplidas = set()

    for f in consultar(
            "SELECT vin, fecha_programacion, fecha_zd, fecha_desp FROM newstocks_cidef "
            "WHERE TRIM(clientecompleto) = 'CIDEF' "
            "  AND destino IS NOT NULL AND destino <> '' "
            "  AND fecha_programacion BETWEEN ? AND ? "
            "  AND fecha_programacion IS NOT NULL AND fecha_programacion <> '0000-00-00' "
            "  AND vin IS NOT NULL AND vin <> ''",
            (inicio.isoformat(), fin.isoformat())):
        vin = vin_limpio(f["vin"])
        if not vin:
            continue
        programadas.add(vin)

        tiene_zd = _fecha_valida(f["fecha_zd"]) is not None
        desp = _fecha_valida(f["fecha_desp"])
        prog = _fecha_valida(f["fecha_programacion"])
        if tiene_zd or (desp and prog and desp <= prog):
            cumplidas.add(vin)

    return len(programadas), len(cumplidas)


def kpi_cumplimiento_preparacion(inicio, fin):
    programadas, cumplidas = _cumplimiento_preparacion(inicio, fin)
    return _tarjeta(
        "cumplimiento_preparacion", "Cumplimiento de Preparación", "📋",
        cumplidas, programadas, "≥ 90%",
        "(con ZD o despachadas dentro de lo programado) ÷ programadas × 100",
        mayor_es_mejor=True)


def _incidencia(inicio, fin):
    """Devuelve (revisiones, incidencias). Cuenta filas."""
    revisiones = incidencias = 0
    for f in consultar(
            "SELECT vin, fecha_revision_salida, estado_it FROM newstocks_cidef "
            "WHERE TRIM(clientecompleto) = 'CIDEF' "
            "  AND fecha_revision_salida BETWEEN ? AND ? "
            "  AND fecha_revision_salida IS NOT NULL "
            "  AND fecha_revision_salida <> '' AND fecha_revision_salida <> '0000-00-00' "
            "  AND vin IS NOT NULL AND vin <> ''",
            (inicio.isoformat(), fin.isoformat())):
        if not vin_limpio(f["vin"]):
            continue
        if _fecha_valida(f["fecha_revision_salida"]) is None:
            continue
        revisiones += 1
        # Cualquier estado informado que no sea OK cuenta como incidencia:
        # asi entran 'PRESENTA FALLAS', 'CON FALLAS', 'FALLA MECANICA' y las
        # variantes de redaccion, sin tener que enumerarlas.
        estado = str(f["estado_it"] or "").strip().upper()
        if estado not in ("", "NULL", "OK"):
            incidencias += 1
    return revisiones, incidencias


def kpi_incidencia(inicio, fin):
    revisiones, incidencias = _incidencia(inicio, fin)
    return _tarjeta(
        "incidencia", "Incidencia Mecánica", "🔩",
        incidencias, revisiones, "< 5%",
        "revisiones de salida con estado IT distinto de OK ÷ total de "
        "revisiones de salida × 100",
        nota="estado_it nunca se cargó en el sistema — el 0% es real, no un "
             "cruce roto")


# ---------------------------------------------------------------------------
# Despachos atrasados
# ---------------------------------------------------------------------------

MINUTOS_LIMITE_DESPACHO = 45
COLACION = (14, 15)  # de 14:00 a 15:00


def kpi_despachos_atrasados(inicio, fin):
    """Cuanto se demora en promedio un retiro, en minutos.

    Cruza cada despacho con la marca de entrada del encargado que vino a
    buscar la unidad (`entradas_salidas`, por RUT y fecha). Tres reglas del
    PHP que no son obvias:

      - la hora de corte es 15:30 los viernes y 17:30 el resto de la semana;
        si el encargado entro despues, el despacho se marca 'extension
        horaria' y NO entra al promedio;
      - la colacion de 14:00 a 15:00 se descuenta del tiempo computable;
      - el RUT se busca primero exacto y, si no aparece, por los ultimos 7
        digitos, porque en una tabla se guarda con puntos y guion y en la otra
        no."""
    despachos = consultar(
        "SELECT vin, fecha_desp, rut_encargado_retiro FROM newstocks_cidef "
        "WHERE TRIM(clientecompleto) = 'CIDEF' "
        "  AND fecha_desp BETWEEN ? AND ? "
        "  AND fecha_desp IS NOT NULL AND fecha_desp <> '' "
        "  AND fecha_desp <> '0000-00-00' "
        "  AND vin IS NOT NULL AND vin <> ''",
        (inicio.isoformat(), fin.isoformat()))

    marcas = _marcas_por_fecha(inicio, fin)

    total_despachos = len(despachos)
    normales = atrasados = extension = sin_registro = 0
    suma_minutos = 0.0

    for d in despachos:
        rut = str(d["rut_encargado_retiro"] or "").strip()
        fecha = _fecha_valida(d["fecha_desp"])
        if not rut or fecha is None:
            sin_registro += 1
            continue

        marca = _buscar_marca(marcas.get(fecha.isoformat(), {}), rut)
        if marca is None:
            sin_registro += 1
            continue

        calculo = _minutos_despacho(fecha, marca["hora_entrada"], marca["hora_salida"])
        if calculo is None:
            sin_registro += 1
            continue
        if calculo["extension"]:
            extension += 1
            continue

        normales += 1
        suma_minutos += calculo["minutos"]
        if calculo["minutos"] > MINUTOS_LIMITE_DESPACHO:
            atrasados += 1

    promedio = round(suma_minutos / normales, 1) if normales else 0.0
    return _tarjeta_promedio(
        "despachos_atrasados", "Despachos Atrasados", "⏰",
        promedio, total_despachos, "min", "≤ 45 min",
        "Promedio de minutos entre la entrada del encargado y la salida, "
        "descontando colación",
        nota="{} atrasados de {} evaluados · {} en extensión horaria · "
             "{} sin marca".format(atrasados, normales, extension, sin_registro))


def _marcas_por_fecha(inicio, fin):
    """Carga de una vez las marcas del periodo indexadas por fecha y RUT. El
    PHP hace una consulta por despacho; aca son 146.353 filas en total, asi
    que conviene traer solo las del mes y cruzar en memoria."""
    por_fecha = {}
    for f in consultar(
            "SELECT fecha, rut, hora_entrada, hora_salida FROM entradas_salidas "
            "WHERE fecha BETWEEN ? AND ? "
            "  AND hora_entrada IS NOT NULL AND hora_entrada <> '' "
            "  AND hora_entrada <> '00:00:00' "
            "ORDER BY hora_entrada ASC",
            (inicio.isoformat(), fin.isoformat())):
        dia = por_fecha.setdefault(str(f["fecha"]), {})
        rut = str(f["rut"] or "").strip()
        # ORDER BY ASC + no pisar = se conserva la entrada mas temprana, que
        # es lo que hace el LIMIT 1 del PHP.
        dia.setdefault(rut, dict(f))
        dia.setdefault("~" + _rut_corto(rut), dict(f))
    return por_fecha


def _rut_corto(rut):
    limpio = re.sub(r"[^0-9Kk]", "", str(rut or "")).upper()
    return limpio[-7:] if len(limpio) >= 6 else ""


def _buscar_marca(marcas_del_dia, rut):
    if rut in marcas_del_dia:
        return marcas_del_dia[rut]
    corto = _rut_corto(rut)
    return marcas_del_dia.get("~" + corto) if corto else None


def _hora_de(texto):
    """Parsea una hora. '00:00:00' se trata como AUSENCIA de marca, no como
    medianoche, igual que el PHP."""
    texto = str(texto or "").strip()
    if texto in ("", "00:00:00"):
        return None
    for formato in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(texto, formato).time()
        except ValueError:
            pass
    return None


def _minutos_despacho(fecha, hora_entrada, hora_salida):
    """Minutos computables de un retiro, o None si no se puede calcular."""
    entrada = _hora_de(hora_entrada)
    salida = _hora_de(hora_salida)
    if entrada is None or salida is None:
        return None

    dt_entrada = datetime.combine(fecha, entrada)
    dt_salida = datetime.combine(fecha, salida)

    # Viernes (weekday 4) cierra antes.
    hora_corte = 15.5 if fecha.weekday() == 4 else 17.5
    dt_corte = datetime.combine(fecha, datetime.min.time()) + timedelta(hours=hora_corte)

    if dt_entrada > dt_corte:
        return {"extension": True, "minutos": 0.0}

    if dt_salida < dt_entrada:
        dt_salida += timedelta(days=1)

    inicio_colacion = datetime.combine(fecha, datetime.min.time()) + timedelta(hours=COLACION[0])
    fin_colacion = datetime.combine(fecha, datetime.min.time()) + timedelta(hours=COLACION[1])

    # Entrar en plena colacion no cuenta: el reloj arranca al terminar.
    arranque = fin_colacion if inicio_colacion <= dt_entrada < fin_colacion else dt_entrada
    if dt_salida <= arranque:
        return {"extension": False, "minutos": 0.0}

    computables = (dt_salida - arranque).total_seconds()
    solape = (min(dt_salida, fin_colacion) - max(arranque, inicio_colacion)).total_seconds()
    computables -= max(0.0, solape)

    return {"extension": False, "minutos": round(max(0.0, computables) / 60.0, 1)}


# ---------------------------------------------------------------------------
# Tasa de incidentes operacionales (compuesto)
# ---------------------------------------------------------------------------

def kpi_tasa_incidentes_operacionales(inicio, fin):
    """Suma los incidentes de tres KPI distintos sobre el universo de unidades
    con solicitud o PDI en el periodo.

    Suma EVENTOS, no unidades: una misma unidad puede aportar un reproceso de
    DyP y ademas una incidencia mecanica, y cuentan los dos. Por eso la tasa
    puede pasar del 100%."""
    universo = _vines(consultar(
        "SELECT vin FROM newstocks_cidef "
        "WHERE TRIM(clientecompleto) = 'CIDEF' "
        "  AND vin IS NOT NULL AND vin <> '' "
        "  AND ((fecha_solicitud BETWEEN ? AND ? AND fecha_solicitud IS NOT NULL "
        "        AND fecha_solicitud <> '' AND fecha_solicitud <> '0000-00-00') "
        "    OR (fecha_pdi BETWEEN ? AND ? AND fecha_pdi IS NOT NULL "
        "        AND fecha_pdi <> '' AND fecha_pdi <> '0000-00-00'))",
        (inicio.isoformat(), fin.isoformat(), inicio.isoformat(), fin.isoformat())))

    reprocesos = kpi_reprocesos_dyp(inicio, fin)["numerador"]
    _, incidencias = _incidencia(inicio, fin)
    programadas, cumplidas = _cumplimiento_preparacion(inicio, fin)
    incumplimientos = max(programadas - cumplidas, 0)

    total = reprocesos + incidencias + incumplimientos
    return _tarjeta(
        "tasa_incidentes_operacionales", "Tasa de Incidentes Operacionales", "⚠️",
        total, len(universo), "≤ 15%",
        "(reprocesos DyP + incidencias mecánicas + incumplimientos de "
        "preparación) ÷ unidades con solicitud o PDI × 100",
        nota="{} reprocesos · {} incidencias · {} incumplimientos. "
             "Hoy suma solo 2 de sus 3 componentes: Incidencia Mecánica "
             "siempre aporta 0 porque estado_it nunca se cargó, así que el "
             "número queda estructuralmente por debajo de lo que el KPI "
             "pretende medir".format(reprocesos, incidencias, incumplimientos))


# ---------------------------------------------------------------------------
# Extras -- NO son parte del dashboard oficial
# ---------------------------------------------------------------------------
#
# Estas dos existen como funciones en Kpi.php pero NO figuran en el array
# $kpis de views/kpi/dashboard.php, o sea que el sistema viejo nunca las
# muestra. Se dejan construidas y aparte, en su propia seccion, para no
# inflar el dashboard con indicadores que produccion no tiene.

def extra_ingreso_taller(inicio, fin):
    """Cuantas unidades pasaron por revision mecanica de despacho.

    No es una tasa sino un conteo: el PHP deja la tasa fija en 100 para que la
    barra de progreso de la tarjeta salga llena, asi que aca se replica ese
    100,00 y el numero que importa es el conteo."""
    unidades = _vines_despachados_por_revision(inicio, fin)
    tarjeta = _tarjeta(
        "ingreso_taller", "Ingreso a Taller (Revisión Mecánica de Despacho)", "🔩",
        len(unidades), len(unidades), "≥ 100 unidades/mes",
        "COUNT(VINs con fecha_revision_salida en período)",
        mayor_es_mejor=True)
    tarjeta["tasa"] = 100.00
    tarjeta["es_conteo"] = True
    return tarjeta


def _vines_despachados_por_revision(inicio, fin):
    return _vines(consultar(
        "SELECT vin FROM newstocks_cidef "
        "WHERE TRIM(clientecompleto) = 'CIDEF' "
        "  AND fecha_revision_salida BETWEEN ? AND ? "
        "  AND fecha_revision_salida IS NOT NULL "
        "  AND fecha_revision_salida <> '' AND fecha_revision_salida <> '0000-00-00'",
        (inicio.isoformat(), fin.isoformat())))


def extra_scanners(inicio, fin):
    """Unidades con codigos de falla (DTC) presentes al ingreso.

    CUIDADO CON ESTA REGLA. Buscar el substring 'DTC PRESENTES' tambien
    encuentra 'OK SIN DTC PRESENTES', que significa exactamente lo contrario:
    que la unidad NO tiene codigos. En la tabla hay 1.871 filas que dicen SIN
    contra 228 que dicen CON, asi que la regla tal como esta especificada
    cuenta 2.099 donde deberian ser 228 -- ocho veces de mas.

    En agosto no se nota porque ninguna fila del periodo tiene un valor con
    DTC (son 'OK', NULL o vacio), y el indicador da 0%. Se implementa tal cual
    se especifico por ser un extra fuera del dashboard, pero si esta regla se
    reusa en alguno de los KPI que faltan hay que invertir el criterio: pedir
    'CON DTC' y excluir 'SIN DTC'."""
    filas = [f for f in consultar(
        "SELECT vin, scanner FROM newstocks_cidef "
        "WHERE TRIM(clientecompleto) = 'CIDEF' AND ingreso BETWEEN ? AND ?",
        (inicio.isoformat(), fin.isoformat())) if vin_limpio(f["vin"])]

    def con_dtc(valor):
        texto = str(valor or "").upper()
        return "CON DTC PRESENTES" in texto or "DTC PRESENTES" in texto

    return _tarjeta(
        "scanners", "Scanners (Unidades con DTC Presentes)", "🖥️",
        sum(1 for f in filas if con_dtc(f["scanner"])), len(filas), "< 10%",
        "unidades ingresadas en el período con DTC presentes ÷ total de "
        "unidades ingresadas × 100",
        nota="El substring 'DTC PRESENTES' también matchea 'SIN DTC PRESENTES'")


# ---------------------------------------------------------------------------
# El dashboard oficial
# ---------------------------------------------------------------------------

# Orden EXACTO del array $kpis de application/views/kpi/dashboard.php. Son 21,
# no 24. Se deja la lista completa aunque falten 13 por construir para que
# cada KPI nuevo caiga solo en su lugar y el layout calce con produccion sin
# tener que reordenar a mano.
ORDEN_DASHBOARD = [
    "kpi_recepcion",
    "kpi_tiempo_contenedor",
    "kpi_tiempo_roro",
    "kpi_patio",
    "kpi_tasa_retrabajo",
    "kpi_promedio_pdi",
    "kpi_fpy",
    "kpi_retrabajo_lavado",
    "kpi_dias_patio",
    "kpi_lead_time_despacho",
    "kpi_lead_time_despacho_sucursal",
    "kpi_lead_time_despacho_concesionario",
    "kpi_lead_time",
    "kpi_reclamos_concesionarios",
    "kpi_cumplimiento_preparacion",
    "kpi_reprocesos_dyp",
    "kpi_efectividad_pdi",
    "kpi_incidencia",
    "kpi_despachos_atrasados",
    "kpi_tasa_incidentes_operacionales",
    "kpi_retorno_sucursales",
]

# Los que ya estan construidos, indexados por su clave del PHP. El orden de
# este dict no importa: la pantalla los ordena por ORDEN_DASHBOARD.
CONSTRUIDOS = {
    "kpi_recepcion": kpi_danos_recepcion,
    "kpi_tiempo_contenedor": kpi_tiempo_contenedor,
    "kpi_tiempo_roro": kpi_tiempo_roro,
    "kpi_patio": kpi_danos_patio,
    "kpi_tasa_retrabajo": kpi_tasa_retrabajo,
    "kpi_promedio_pdi": kpi_promedio_pdi,
    "kpi_fpy": kpi_first_pass_yield,
    "kpi_retrabajo_lavado": kpi_retrabajo_lavado,
    "kpi_dias_patio": kpi_dias_patio,
    "kpi_lead_time_despacho": kpi_lead_time_despacho,
    "kpi_lead_time_despacho_sucursal": kpi_lead_time_despacho_sucursal,
    "kpi_lead_time_despacho_concesionario": kpi_lead_time_despacho_concesionario,
    "kpi_lead_time": kpi_lead_time,
    "kpi_reclamos_concesionarios": kpi_reclamos_concesionarios,
    "kpi_cumplimiento_preparacion": kpi_cumplimiento_preparacion,
    "kpi_reprocesos_dyp": kpi_reprocesos_dyp,
    "kpi_efectividad_pdi": kpi_efectividad_pdi,
    "kpi_incidencia": kpi_incidencia,
    "kpi_despachos_atrasados": kpi_despachos_atrasados,
    "kpi_tasa_incidentes_operacionales": kpi_tasa_incidentes_operacionales,
    "kpi_retorno_sucursales": kpi_retorno_sucursales,
}

KPIS = [CONSTRUIDOS[clave] for clave in ORDEN_DASHBOARD if clave in CONSTRUIDOS]

EXTRAS = [
    extra_ingreso_taller,
    extra_scanners,
]


# ---------------------------------------------------------------------------

def periodo_de(texto_mes):
    """(inicio, fin) del mes pedido. A diferencia de facturacion, el corte es
    siempre el ultimo dia del mes y no la fecha de hoy: un KPI se mide sobre el
    periodo completo, no hasta donde vamos."""
    hoy = date.today()
    inicio = hoy.replace(day=1)
    if texto_mes:
        try:
            inicio = datetime.strptime(texto_mes + "-01", "%Y-%m-%d").date()
        except ValueError:
            pass
    fin = inicio.replace(day=calendar.monthrange(inicio.year, inicio.month)[1])
    return inicio, fin


@bp.route("/")
def dashboard():
    inicio, fin = periodo_de(request.args.get("mes"))
    tarjetas = [funcion(inicio, fin) for funcion in KPIS]
    extras = [funcion(inicio, fin) for funcion in EXTRAS]
    return render_template(
        "kpis.html", tarjetas=tarjetas, extras=extras, inicio=inicio, fin=fin,
        mes=inicio.strftime("%Y-%m"),
        total_oficiales=len(ORDEN_DASHBOARD),
        sin_datos=all(t["denominador"] == 0 for t in tarjetas))
