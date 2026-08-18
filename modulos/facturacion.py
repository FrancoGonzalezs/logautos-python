"""
modulos/facturacion.py -- el dashboard de Facturacion: acopio por cliente, OT
cerradas del mes y el total a facturar. Equivale a `dash_acopio.php` del
sistema viejo.

Dos diferencias de fondo con el PHP, decididas a proposito:

1. El acopio se CALCULA EN VIVO, no se persiste. El PHP hace un UPDATE sobre
   newstocks_cidef para dejar los dias y el valor guardados en la propia fila
   de la unidad. Eso era un workaround del sistema legado, no una necesidad:
   aca la pantalla es lectura pura y el numero se recalcula cada vez que se
   pide, asi que no puede quedar desactualizado ni corromper la tabla si algo
   falla a la mitad.

2. La UF sale de mindicador.cl, no de la columna `uf_mes` de la base de
   origen. Se cachea por dia en un archivo para no golpear la API en cada
   carga, y si la API se cae se usa el ultimo valor conocido.

Sobre los nombres de cliente: se comparan NORMALIZADOS, nunca con '='. La
base trae 'LOGAUTOS ' con espacio final en 25 ordenes de trabajo, y LOGAUTOS
es justamente el cliente que NO debe entrar en los totales de facturacion --
con igualdad exacta esas 25 se colarian en la plata a cobrar. Lo mismo pasa
con 'POMPEYO CARRASCO ' (77 unidades), que sin normalizar caeria en la tarifa
de fallback en vez de la suya.
"""

import calendar
import json
import os
from datetime import date, datetime

from flask import Blueprint, render_template, request

from core import BASE_DIR, consultar
from modulos.catalogos import normalizar

bp = Blueprint("facturacion", __name__, url_prefix="/facturacion")

IVA = 1.19

RUTA_CACHE_UF = os.path.join(BASE_DIR, "data", "uf.json")
URL_UF = "https://mindicador.cl/api/uf"

# Los UNICOS cinco clientes que facturan acopio. No hay tarifa de fallback: si
# un cliente no esta en esta tabla, sus unidades no aparecen en la seccion.
#
# Es deliberado y corrige un problema real. Con fallback, 98 unidades de
# clientes que no facturan acopio (GELLONA, ECARS, KSM, MAS AUTOS, unidades de
# PRUEBA y una decena de particulares) entraban al total, y como muchas estan
# estacionadas hace años sin fecha de despacho acumulaban dias sin techo:
# ECARS aportaba $21 millones con 23 unidades ingresadas antes de 2026.
#
# CIDEF es un monto FIJO en pesos, no un multiplo de la UF -- el PHP tiene la
# version en UF comentada al lado y usa el fijo.
TARIFA_FIJA = {"CIDEF": 660}
TARIFA_EN_UF = {
    "CARFLEX": 0.022,
    "PIAMONTE": 0.026,
    "POMPEYO CARRASCO": 0.0219,
    "POMPEYO CARRASCO USADOS": 0.0215,
}

# CLIENTE PARTICULAR factura acopio pero no tiene tarifa propia: cae en el
# fallback "MAS" del PHP, el ultimo `else` de la cadena de if/elseif por
# cliente. No es una lista abierta -- sigue siendo lista blanca, solo que este
# cliente cobra la tarifa generica.
TARIFA_UF_FALLBACK = 0.017
CLIENTES_ACOPIO_FALLBACK = {"CLIENTE PARTICULAR"}

CLIENTES_ACOPIO = set(TARIFA_FIJA) | set(TARIFA_EN_UF) | CLIENTES_ACOPIO_FALLBACK

# Dias de gracia que CIDEF no paga cuando la unidad viene de puerto.
DIAS_GRACIA_CIDEF_PUERTO = 7

# Los clientes cuyas OT se facturan. Igual que arriba, es lista blanca: lo que
# no este aca no entra ni en la tabla ni en los totales.
CLIENTES_OT = {"CIDEF", "CARFLEX", "POMPEYO CARRASCO", "PIAMONTE",
               "CLIENTE PARTICULAR"}

# 'POMPEYO CARRASCO FLOTA' es el mismo cliente que 'POMPEYO CARRASCO' para
# efectos de OT (no para acopio, donde FLOTA no factura: por eso el alias vive
# aca y no en la normalizacion general).
ALIAS_OT = {"POMPEYO CARRASCO FLOTA": "POMPEYO CARRASCO"}

# LOGAUTOS es la empresa dueña del sistema, no un cliente. Sus OT son costo
# interno y van en una tabla aparte, nunca en los totales a facturar.
CLIENTE_INTERNO = "LOGAUTOS"


# ---------------------------------------------------------------------------
# UF
# ---------------------------------------------------------------------------

def _leer_cache_uf():
    try:
        with open(RUTA_CACHE_UF, "r", encoding="utf-8") as f:
            datos = json.load(f)
        if isinstance(datos, dict):
            return datos
    except (IOError, OSError, ValueError):
        pass
    return {}


def _guardar_cache_uf(clave, valor):
    datos = _leer_cache_uf()
    datos[clave] = valor
    try:
        os.makedirs(os.path.dirname(RUTA_CACHE_UF), exist_ok=True)
        with open(RUTA_CACHE_UF, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=1, sort_keys=True)
    except (IOError, OSError):
        pass  # sin cache se sigue andando, solo se pega a la API mas seguido


# Cuantos dias se prueban hacia atras antes de darse por vencido. La UF se
# publica por adelantado para todo el periodo (del 10 de un mes al 9 del
# siguiente), asi que si el mes esta publicado el ultimo dia responde al primer
# intento. Si no responde en unos pocos dias, el mes entero no esta publicado
# todavia y seguir hasta el dia 1 son 30 requests al pedo: un mes futuro
# tardaria minutos en fallar y dejaria la pantalla colgada mientras tanto.
INTENTOS_UF = 10


def obtener_uf(anio, mes):
    """La ultima UF publicada del mes que se esta facturando.

    NO es la UF de hoy. Se factura el mes completo con un solo valor, y ese
    valor es el de cierre del mes -- mismo criterio que `cambiar_uf.php` en
    produccion: se busca desde el ultimo dia del mes hacia atras hasta dar con
    un dia publicado.

    Se cachea por (año, mes) y no por dia: una vez publicado, el valor de un
    mes ya no cambia, asi que no hay nada que refrescar. Eso ademas hace que
    consultar meses viejos no pegue a la API nunca mas.

    Devuelve (valor, origen) para que la pantalla pueda decir de donde salio."""
    clave = "{:04d}-{:02d}".format(anio, mes)
    cache = _leer_cache_uf()
    if clave in cache:
        return cache[clave], "cache"

    import requests

    ultimo_dia = calendar.monthrange(anio, mes)[1]
    for intento, dia in enumerate(range(ultimo_dia, 0, -1), start=1):
        fecha = date(anio, mes, dia).strftime("%d-%m-%Y")
        try:
            respuesta = requests.get("{}/{}".format(URL_UF, fecha), timeout=5)
            serie = respuesta.json().get("serie") or []
            if serie:
                valor = serie[0]["valor"]
                _guardar_cache_uf(clave, valor)
                return valor, "mindicador.cl ({})".format(fecha)
        except Exception:
            pass
        if intento >= INTENTOS_UF:
            break

    raise RuntimeError("No se encontro UF publicada para {}/{}".format(mes, anio))


def tarifa_diaria(cliente, uf):
    """Lo que cuesta un dia de acopio para ese cliente, o None si el cliente
    no factura acopio."""
    canon = normalizar(cliente)
    if canon in TARIFA_FIJA:
        return TARIFA_FIJA[canon]
    if canon in TARIFA_EN_UF:
        return uf * TARIFA_EN_UF[canon]
    if canon in CLIENTES_ACOPIO_FALLBACK:
        # OJO: esta va redondeada a peso y las de arriba no. Es asi por ahora
        # a proposito, no es un descuido -- ver la nota del README sobre el
        # redondeo: produccion redondea TODAS las tarifas, pero cambiar las
        # otras mueve numeros que el personal ya esta mirando, asi que esa
        # decision quedo pendiente. Cuando se tome, esto queda parejo solo.
        return round(uf * TARIFA_UF_FALLBACK)
    return None


# ---------------------------------------------------------------------------
# Acopio
# ---------------------------------------------------------------------------

def _fecha(texto):
    """Las fechas del origen son ISO, pero conviven con NULL, '' y
    '0000-00-00'. Devuelve None para todo lo que no sea una fecha real."""
    if not texto:
        return None
    texto = str(texto)[:10]
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        return None


def calcular_dias_acopio(ingreso, fecha_desp, inicio_mes, hoy, cliente, origen):
    """Los dias de acopio que se le cobran a una unidad en el mes consultado.

    OJO: NO es una resta de fechas. El PHP original hace aritmetica sobre el
    DIA DEL MES (date('d', ...)), y esta funcion lo replica tal cual porque es
    lo que factura hoy el negocio. La diferencia no es teorica: una unidad que
    entro el 20 de julio y sigue en patio el 13 de agosto lleva 24 dias reales,
    pero para el PHP son 13 -- el dia del mes de la fecha de corte. Calcular la
    resta real inflaba el total y era la causa del desfase con produccion.

    La logica, en criollo:
      - si la unidad ingreso en un mes anterior, se le cobra el dia del mes de
        la fecha de corte (o del despacho, si ya salio): los dias corridos de
        este mes;
      - si ingreso dentro del mes, se cobra la diferencia de dias del mes mas
        uno (ambos extremos incluidos);
      - si el despacho quedo en el futuro respecto de la fecha de corte, manda
        la fecha de corte.

    El descuento de 7 dias de CIDEF por unidad de puerto se aplica solo en las
    ramas donde el PHP lo aplica, que no son todas -- por eso va en una funcion
    interna y no al final."""

    def dom(f):
        return f.day

    def ajustar_cidef(dia):
        if cliente == "CIDEF" and origen and origen.startswith("PUERTO"):
            dia -= 7
            if dia < 1:
                dia = 0
        return dia

    if fecha_desp is not None:
        if fecha_desp > hoy:
            if ingreso is not None:
                dia = (dom(hoy) - dom(ingreso) + 1) if ingreso > inicio_mes else dom(hoy)
            else:
                dia = dom(fecha_desp)
        else:
            if ingreso is not None:
                if ingreso < inicio_mes:
                    dia = dom(fecha_desp)
                elif (fecha_desp.year, fecha_desp.month) == (ingreso.year, ingreso.month):
                    dia = ajustar_cidef(dom(fecha_desp) - dom(ingreso) + 1)
                else:
                    dia = ajustar_cidef(dom(fecha_desp))
            else:
                dia = dom(fecha_desp)
    else:
        if ingreso is not None:
            if ingreso < inicio_mes:
                dia = dom(hoy)
            else:
                dia = ajustar_cidef(dom(hoy) - dom(ingreso) + 1)
        else:
            dia = 0

    return max(dia, 0)


def acopio_por_cliente(uf, hoy=None):
    """Calcula el acopio del mes, agrupado por cliente. No escribe nada."""
    hoy = hoy or date.today()
    inicio_mes = hoy.replace(day=1)
    prefijo_mes = hoy.strftime("%Y-%m")

    # Solo NULL cuenta como "sin despachar". Una fecha cero de MySQL
    # ('0000-00-00') es una fecha invalida, no la ausencia de fecha: tiene que
    # FALLAR la comparacion contra el inicio del mes, no colarse por el OR.
    # Meterla junto a NULL le cobraba 13 dias de acopio a una unidad ya
    # despachada (id 81373, VIN LJD0AA29AP0184812, despachado='DESPACHADO').
    # No hace falta escribir nada especial: '0000-00-00' >= '2026-08-01' ya es
    # falso en la comparacion de texto, que es como se guardan las fechas.
    filas = consultar(
        "SELECT id, vin, clientecompleto, ingreso, fecha_desp, despachado, origen "
        'FROM "newstocks_cidef" '
        "WHERE ingreso IS NOT NULL AND ingreso <> '' AND ingreso <> '0000-00-00' "
        "  AND ingreso <= ? "
        "  AND (fecha_desp >= ? OR fecha_desp IS NULL)",
        (hoy.isoformat(), inicio_mes.isoformat()))

    por_cliente = {}
    unidades = []
    excluidas = {}

    for fila in filas:
        # Replica exacta del `despachado <> 'Navegando'` del PHP, que descarta
        # DOS cosas: las unidades navegando y las que tienen el campo en NULL
        # (en SQL, 'NULL <> x' no es verdadero, asi que la fila no pasa el
        # filtro). Se descartan las dos.
        #
        # REVISAR CUANDO SE APAGUE Logautos.PHP: descartar las de despachado
        # NULL es un efecto colateral de la logica de tres valores de SQL, no
        # una regla de negocio -- una unidad sin estado no esta navegando.
        # Hoy se replica a proposito para que los numeros calcen contra
        # produccion mientras los dos sistemas convivan. Con el PHP apagado,
        # lo correcto es incluirlas. Afecta a la unidad id 91246
        # (VIN DYLD55, PIAMONTE).
        #
        # La comparacion va normalizada y no con '=': MySQL usa una collation
        # _ci (case-insensitive), asi que alla 'navegando' en minuscula
        # tambien queda descartada, y con '=' a secas aca no lo estaria.
        if fila["despachado"] is None or normalizar(fila["despachado"]) == "NAVEGANDO":
            continue

        # Lista blanca: los clientes que no facturan acopio no entran. Se
        # cuentan aparte para poder decir en pantalla cuantas quedaron fuera,
        # en vez de hacerlas desaparecer sin dejar rastro.
        canon_cliente = normalizar(fila["clientecompleto"])
        if canon_cliente not in CLIENTES_ACOPIO:
            excluidas[canon_cliente or "(sin cliente)"] = \
                excluidas.get(canon_cliente or "(sin cliente)", 0) + 1
            continue

        ingreso = _fecha(fila["ingreso"])
        if ingreso is None:
            continue

        canon = canon_cliente
        desp = _fecha(fila["fecha_desp"])
        dias = calcular_dias_acopio(
            ingreso, desp, inicio_mes, hoy, canon, normalizar(fila["origen"]))

        tarifa = tarifa_diaria(canon, uf)
        valor = dias * tarifa

        entrada = por_cliente.setdefault(canon, {
            "cliente": canon, "unidades": 0, "dias": 0, "valor": 0.0,
            "tarifa": tarifa, "ingresadas_mes": 0, "despachadas_mes": 0,
        })
        entrada["unidades"] += 1
        entrada["dias"] += dias
        entrada["valor"] += valor

        # Ingresadas/despachadas del mes: se compara año Y mes. El PHP usa
        # MONTH() a secas, que en agosto cuenta tambien los agostos de 2023,
        # 2024 y 2025 -- para un tablero del mes en curso eso es un error, no
        # una decision, asi que aca se compara el mes completo.
        if str(fila["ingreso"])[:7] == prefijo_mes:
            entrada["ingresadas_mes"] += 1
        if (normalizar(fila["despachado"]) == "DESPACHADO"
                and str(fila["fecha_desp"] or "")[:7] == prefijo_mes):
            entrada["despachadas_mes"] += 1

        unidades.append({
            "id": fila["id"], "vin": fila["vin"], "cliente": canon,
            "ingreso": fila["ingreso"], "fecha_desp": fila["fecha_desp"],
            "dias": dias, "valor": valor,
        })

    ordenado = sorted(por_cliente.values(), key=lambda e: -e["valor"])
    fuera = sorted(excluidas.items(), key=lambda kv: -kv[1])
    return ordenado, unidades, fuera


# ---------------------------------------------------------------------------
# Ordenes de trabajo
# ---------------------------------------------------------------------------

def _cliente_ot(nombre):
    """El cliente al que se le imputa una OT, ya normalizado y con los alias
    resueltos."""
    canon = normalizar(nombre)
    return ALIAS_OT.get(canon, canon)


def ot_cerradas_por_cliente(hoy=None):
    """OT cerradas del mes: las de los cuatro clientes que se facturan, las de
    LOGAUTOS aparte, y la cuenta de todo lo demas.

    Se excluyen las TNR (trabajo no realizado): no se facturan.

    La fila de totales es la suma de los cuatro clientes, no una consulta
    aparte con `nombre <> 'LOGAUTOS'` -- si fuera eso, cualquier nombre suelto
    de la tabla (CLIENTE PARTICULAR, CLASQUIN, ASTARA, los particulares con
    nombre y apellido) sumaria plata que nadie factura."""
    hoy = hoy or date.today()
    filas = consultar(
        "SELECT nombre, precio FROM orden_trabajo "
        "WHERE estado = 'CERRADA' AND (TNR IS NULL OR TNR <> 'TNR') "
        "  AND fecha_cierre LIKE ?", (hoy.strftime("%Y-%m") + "%",))

    por_cliente = {}
    interno = None
    fuera = {}

    for fila in filas:
        canon = _cliente_ot(fila["nombre"])
        precio = fila["precio"] or 0

        if canon == CLIENTE_INTERNO:
            interno = interno or {"cliente": canon, "ot": 0, "precio": 0}
            interno["ot"] += 1
            interno["precio"] += precio
            continue

        if canon not in CLIENTES_OT:
            e = fuera.setdefault(canon or "(sin nombre)", {"ot": 0, "precio": 0})
            e["ot"] += 1
            e["precio"] += precio
            continue

        entrada = por_cliente.setdefault(canon, {"cliente": canon, "ot": 0, "precio": 0})
        entrada["ot"] += 1
        entrada["precio"] += precio

    facturables = sorted(por_cliente.values(), key=lambda e: -e["precio"])
    otros = sorted(fuera.items(), key=lambda kv: -kv[1]["precio"])
    return facturables, interno, otros


def proyeccion_por_cliente(hoy=None):
    """OT abiertas de DyP y servicio mecanico programadas para este mes: la
    plata que se espera facturar si se cierran. Mismos cuatro clientes."""
    hoy = hoy or date.today()
    filas = consultar(
        "SELECT nombre, requerimiento, precio FROM orden_trabajo "
        "WHERE estado = 'ABIERTA' AND fecha_programacion LIKE ?",
        (hoy.strftime("%Y-%m") + "%",))

    por_cliente = {}
    for fila in filas:
        if normalizar(fila["requerimiento"]) not in ("DYP", "SERVICIO MECANICO"):
            continue
        canon = _cliente_ot(fila["nombre"])
        if canon not in CLIENTES_OT:
            continue
        entrada = por_cliente.setdefault(canon, {"cliente": canon, "ot": 0, "precio": 0})
        entrada["ot"] += 1
        entrada["precio"] += fila["precio"] or 0

    return sorted(por_cliente.values(), key=lambda e: -e["precio"])


# ---------------------------------------------------------------------------

@bp.route("/")
def dashboard():
    # `hoy` es la FECHA DE CORTE del mes que se mira, y de ella sale el
    # dia-del-mes con que se cuentan los dias de acopio. Para el mes en curso
    # es hoy; para un mes ya cerrado tiene que ser su ULTIMO dia, no el
    # primero -- con el primero, dia_del_mes vale 1 y todas las unidades
    # facturaban un solo dia.
    hoy = date.today()
    if request.args.get("mes"):
        try:
            elegido = datetime.strptime(request.args["mes"] + "-01", "%Y-%m-%d").date()
        except ValueError:
            elegido = hoy
        if (elegido.year, elegido.month) != (hoy.year, hoy.month):
            hoy = elegido.replace(day=calendar.monthrange(elegido.year, elegido.month)[1])

    try:
        uf, origen_uf = obtener_uf(hoy.year, hoy.month)
    except RuntimeError as error:
        # Sin UF no se puede calcular el acopio de cuatro de los cinco
        # clientes. Se avisa en la pantalla en vez de reventar con un 500.
        return render_template("facturacion.html", error_uf=str(error),
                               mes=hoy.strftime("%Y-%m"), hoy=hoy)

    acopio, _unidades, acopio_fuera = acopio_por_cliente(uf, hoy)
    ot_externas, ot_interna, ot_fuera = ot_cerradas_por_cliente(hoy)
    proyecciones = proyeccion_por_cliente(hoy)

    total_ot = sum(e["precio"] for e in ot_externas)
    total_acopio = sum(e["valor"] for e in acopio)
    total_facturar = total_ot + total_acopio
    total_proyeccion = sum(e["precio"] for e in proyecciones)

    totales = {
        "ot": total_ot,
        "acopio": total_acopio,
        "facturar": total_facturar,
        "facturar_iva": total_facturar * IVA,
        "proyeccion": total_proyeccion,
        "proyeccion_iva": total_proyeccion * IVA,
        "con_proyeccion": total_facturar + total_proyeccion,
        "con_proyeccion_iva": (total_facturar + total_proyeccion) * IVA,
    }

    return render_template(
        "facturacion.html",
        uf=uf, origen_uf=origen_uf, mes=hoy.strftime("%Y-%m"), hoy=hoy,
        acopio=acopio, ot_externas=ot_externas, ot_interna=ot_interna,
        proyecciones=proyecciones, totales=totales,
        acopio_fuera=acopio_fuera, ot_fuera=ot_fuera,
        clientes_acopio=sorted(CLIENTES_ACOPIO), clientes_ot=sorted(CLIENTES_OT),
        total_unidades=sum(e["unidades"] for e in acopio),
        total_dias=sum(e["dias"] for e in acopio),
        total_ot_cant=sum(e["ot"] for e in ot_externas))
