"""
modulos/ubicacion.py -- patio y calle: el catalogo vivo, la sugerencia y su
fecha de vencimiento.

Existe porque `STOCK` es el estado de mas volumen que REGLA no le puede empujar
al legado, y el unico motivo era que la pantalla no preguntaba DONDE quedo la
unidad. El movilizador lo sabe -- esta parado en el patio, el la estaciono.

Todo lo que hay aca se MIDIO sobre los 4.624 movimientos a STOCK de los ultimos
6 meses (13-02-2026 a 13-08-2026). Las tablas estan horneadas, no se calculan
en cada request, por lo mismo que `CALLE_POR_ESTADO`: son datos que cambian con
la operacion y no con el trafico, y remedirlas tiene que ser un acto
deliberado con la fecha anotada al lado.


LO QUE HAY QUE SABER ANTES DE TOCAR ESTO
========================================

**El orden de las calles NO es por frecuencia.** La calle mas frecuente del
patio acierta el 27,1%; la ULTIMA usada en ese patio, el 72,4%. Los
movilizadores trabajan una calle por tanda: llenan la C hasta que se llena y
recien ahi pasan a la D. Ordenar por frecuencia es ordenar por el promedio de
un mes, cuando lo que decide es lo que paso hace diez minutos.

**Pero la recencia vence, y de golpe.** Ver VENTANA_RECENCIA_SEGUNDOS.

**La sugerencia solo puede ahorrar toques, nunca agregarlos.** El atajo PREPARA
la respuesta, no la confirma: siempre hace falta el toque de confirmar, que
nombra el destino completo. Una sugerencia errada cuesta exactamente lo mismo
que no haber tenido sugerencia -- un toque en la calle correcta -- y se ve
antes de escribirse. Si alguna vez esto pasa a confirmar de una, el 15,4% de
atajos errados se convierte en ubicaciones falsas escritas en el legado.
"""

from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# Cuanto vale la ultima calle usada
# ---------------------------------------------------------------------------
#
# EL DECAIMIENTO ES UN ESCALON, NO UNA RAMPA. Acierto de "la ultima calle usada
# en este patio", segun cuanto hacia que se habia usado:
#
#     < 1h      75,8%     n=4.081
#     1-8h      48,2%     n=249
#     8-24h     42,5%     n=160
#     > 24h     53,5%     n=129
#
# Dentro de la hora la señal manda; pasada la hora se aplana en ~50% y deja de
# distinguir. Por eso el corte es duro y no un peso que baja: no hay nada que
# ponderar entre las 2h y las 30h, es todo el mismo ruido.
#
# Y por eso la pantalla no se limita a mostrar la antiguedad. Un chip que dice
# "hace 3 dias" en letra chica invita al pulgar igual que uno que dice "hace 5
# minutos"; lo unico honesto es DEJAR DE SUGERIR. Pasada la ventana, la lista
# se ordena por frecuencia -- que es el orden correcto cuando no hay tanda en
# curso -- y no se preselecciona nada.
#
# HAY QUE REMEDIRLO. Este numero sale de medir el comportamiento en el SISTEMA
# VIEJO, donde el movilizador elegia la calle en un <select> alfabetico. La
# pantalla nueva cambia justo lo que se esta midiendo: si ordenar por recencia
# hace que las tandas se estiren, la ventana real puede ser mas larga. Se
# remide con `movimientos_regla` cuando haya unas semanas de uso, con la misma
# consulta, y se cambia este numero con la fecha nueva al lado.
#
# Medido el 2026-08-27 sobre `registros`. Sin remedir todavia.
VENTANA_RECENCIA_SEGUNDOS = 3600


# Cuantos atajos se ofrecen. Tres, porque con el ranking por recencia el top-3
# de pares cubre el 84,6% y el cuarto agrega 2,4 puntos: no paga el ancho en un
# telefono.
CANTIDAD_ATAJOS = 3


# ---------------------------------------------------------------------------
# El catalogo: donde se estaciona de verdad
# ---------------------------------------------------------------------------
#
# NO SALE DE LEER EL PHP, y no podia salir. Hay tres fuentes que no coinciden:
# el menu que ve el usuario (`modelos()`, Pedido.php:11681), el `switch` que
# procesa el POST (`actulocproccess`) y lo que el sistema escribio. Solo entra
# lo que aparece en las tres.
#
# Lo que quedo afuera al cruzarlas:
#
#   X                     en el menu de PATIO 3 y 4-9, NO en sus `switch`.
#                         Elegirla no escribe nada y avisa que si (los cuatro
#                         switch no tienen `default:`). 0 filas en el
#                         historico, que no prueba que nadie la eligio: prueba
#                         que si alguien la eligio, no se guardo.
#                         Parche en scripts/actulocproccess_default.php.
#   Ñ                     en el `switch` de PATIO 2, en ningun menu. 0 filas.
#   K L N O P Q R S T V W Y Z
#                         en las dos listas, 0 movimientos en 6 meses. La
#                         ultima fue Z, en diciembre de 2025.
#   PATIO 6, 7, 8, 9      el menu los ofrece; 0 movimientos en el semestre y 34
#                         filas en toda la replica.
#
# CALLES DE PROCESO, que usan la misma columna y NO son lugares donde se
# estaciona: Cc, Zd, ZR, IT/It, Cmp3, Lavando, PDI, Falla Mecanica, Servicios
# Generales, REVISION CLIENTE, Dyp *, Vulcanizacion. No entran acá: a esas la
# unidad no llega estacionando sino ejecutando una etapa, y su calle la resuelve
# `CALLE_POR_ESTADO` en push_legado.
#
# El numero es movimientos a STOCK en los ultimos 6 meses, y es el orden de
# respaldo -- el que se usa cuando NO hay tanda en curso.
CALLES_POR_PATIO = {
    "PATIO 2": [("F", 513), ("A", 467), ("B", 382), ("C", 264), ("D", 179),
                ("E", 131), ("M", 38), ("U", 20), ("G", 16)],
    "PATIO 3": [("A", 281), ("B", 238), ("C", 212), ("F", 143), ("D", 129),
                ("E", 113), ("H", 67), ("I", 37), ("G", 15), ("J", 11)],
    "PATIO 5": [("C", 338), ("B", 330), ("A", 318), ("D", 256), ("I", 3),
                ("H", 2), ("J", 1)],
    # PATIO 4 es el sobrante de PATIO 2 y tiene una sola calle viva.
    "PATIO 4": [("A", 68)],
}

# Cuantas calles se muestran sin desplegar. El resto queda detras de "otras
# calles del patio": siguen disponibles, no compiten por el pulgar.
#
# Cinco y no tres: con el ranking por recencia, tres cubren el 90,2% y cinco el
# 97,2%, y en un telefono cinco chips de una letra entran en una fila. El corte
# de tres era del ranking, no de la pantalla.
CALLES_A_LA_VISTA = 5

# PATIO 1 NO ESTA EN EL CATALOGO y es a proposito: no se estaciona ahi. Su menu
# no ofrece ni una letra, solo Cc / It / Zd, que son etapas. Los 52 movimientos
# a STOCK que figuran en PATIO 1 son 51 de calle 'ZR' -- zona de recepcion, una
# etapa -- y ninguno desde abril.
PATIOS = ["PATIO 2", "PATIO 3", "PATIO 5", "PATIO 4"]


# ---------------------------------------------------------------------------
# Que patio proponer
# ---------------------------------------------------------------------------
#
# PRECARGAR EL PATIO ACTUAL ESTA MAL EL 45% DE LAS VECES, que es exactamente lo
# contrario de lo que parece: la unidad entra a STOCK justo cuando CAMBIA de
# patio. Acierto medido sobre los 4.624:
#
#     el patio que ya tiene        54,9%
#     argmax por patio de origen   61,8%
#     argmax por cliente           69,4%
#     argmax por origen + cliente  79,0%   <- esta tabla
#
# El cliente es el que manda -- CIDEF estaciona en 5 y 3, CARFLEX en 2 -- y el
# origen desempata. Las combinaciones con menos de 15 movimientos no entran:
# con esa n el argmax es ruido. Para las que faltan cae a `PATIO_POR_DEFECTO`.
PATIO_SUGERIDO = {
    ("PATIO 2", "CARFLEX"): "PATIO 2",   #  92%  n=1642
    ("PATIO 2", "CIDEF"):   "PATIO 5",   #  58%  n=1373
    ("PATIO 3", "CIDEF"):   "PATIO 3",   #  91%  n=571
    ("PATIO 5", "CIDEF"):   "PATIO 5",   #  73%  n=516
    ("PATIO 1", "CARFLEX"): "PATIO 2",   #  99%  n=275
    ("PATIO 4", "CARFLEX"): "PATIO 2",   # 100%  n=61
    ("PATIO 1", "CIDEF"):   "PATIO 5",   #  56%  n=41
    ("PATIO 5", "CARFLEX"): "PATIO 2",   # 100%  n=33
}

# El que mas volumen tiene. Solo se usa cuando el par (origen, cliente) no esta
# en la tabla -- unidad sin patio, cliente que no es CIDEF ni CARFLEX, o una
# combinacion demasiado rara para medirla.
PATIO_POR_DEFECTO = "PATIO 2"


def _perfil_cliente(crudo):
    """CIDEF / CARFLEX / OTRO. El legado escribe el cliente con sufijos
    ('CIDEF S.A.', 'CARFLEX | '), asi que se compara por prefijo y no por
    igualdad -- el mismo criterio que usa `perfil_de` en movimientos.py."""
    texto = (crudo or "").strip().upper()
    if texto.startswith("CIDEF"):
        return "CIDEF"
    if texto.startswith("CARFLEX"):
        return "CARFLEX"
    return "OTRO"


def patio_sugerido(unidad):
    """El patio a preseleccionar para esta unidad. Nunca None: si no hay
    medicion para el par, cae al de mas volumen."""
    origen = (unidad["patio"] or "").strip()
    cliente = _perfil_cliente(unidad["clientecompleto"])
    return PATIO_SUGERIDO.get((origen, cliente), PATIO_POR_DEFECTO)


# ---------------------------------------------------------------------------
# La tanda en curso
# ---------------------------------------------------------------------------

def tanda_en_curso(db, usuario, ahora=None):
    """Los ultimos pares (patio, calle) que se usaron dentro de la ventana, del
    mas reciente al mas viejo, sin repetir. Lista vacia = no hay tanda.

    SALE DE `movimientos_regla`, NO de `registros`, y esa eleccion decide como
    se comporta la pantalla el primer dia. `registros` en la replica solo se
    actualiza con el dump -- no esta en el pull, ver el pendiente 4 -- asi que
    su fila mas nueva tiene semanas: leer la recencia de ahi seria leer la
    tanda de otro mes, que es peor que no leer nada.

    Con `movimientos_regla` el arranque en frio queda resuelto solo: el primer
    dia la tabla esta vacia, no hay tanda, y la pantalla entra en modo sin
    sugerencia -- que es el mismo modo correcto de un lunes a la mañana. Se
    cura con el primer movimiento del turno.

    Se prefieren los del propio usuario y se completan con los de cualquiera:
    la tanda es del patio, no de la persona, pero si dos movilizadores estan
    trabajando patios distintos al mismo tiempo el suyo es el que vale. Medido:
    del operario 71,6%, del patio 72,4%, los dos combinados 73,3%."""
    corte = (ahora or datetime.now()) - timedelta(seconds=VENTANA_RECENCIA_SEGUNDOS)
    filas = db.execute("""
        SELECT patio, calle, usuario, creado_en
          FROM movimientos_regla
         WHERE creado_en >= ?
           AND patio IS NOT NULL AND patio <> ''
           AND calle IS NOT NULL AND calle <> ''
         ORDER BY creado_en DESC
         LIMIT 60""", (corte.isoformat(timespec="seconds"),)).fetchall()

    propios, ajenos, vistos = [], [], set()
    for fila in filas:
        par = (fila["patio"], fila["calle"])
        if par in vistos:
            continue
        vistos.add(par)
        destino = propios if str(fila["usuario"]) == str(usuario) else ajenos
        destino.append(par)
    return (propios + ajenos)[:CANTIDAD_ATAJOS]


def calles_de(patio, tanda=()):
    """Las calles del patio, ordenadas para mostrar. Devuelve
    (a_la_vista, plegadas).

    Con tanda en curso, la calle que se esta usando en ESE patio va primera y
    el resto sigue por frecuencia. Sin tanda, todo por frecuencia."""
    porfrec = [calle for calle, _ in CALLES_POR_PATIO.get(patio, [])]
    encabeza = [calle for p, calle in tanda if p == patio and calle in porfrec]
    orden = encabeza + [c for c in porfrec if c not in encabeza]
    return orden[:CALLES_A_LA_VISTA], orden[CALLES_A_LA_VISTA:]


def volumen(patio, calle):
    """Movimientos a STOCK de esa calle en el semestre medido. Para el
    subtitulo del chip: el operario ve si esta por elegir una calle que se usa
    todos los dias o una que se uso tres veces en seis meses."""
    for nombre, n in CALLES_POR_PATIO.get(patio, []):
        if nombre == calle:
            return n
    return 0


def valida(patio, calle):
    """True si el par existe en el catalogo. Se valida en el POST y no solo en
    el formulario: la calle es la UBICACION FISICA y de ella salen los reportes
    de patio del legado, asi que un submit armado a mano no puede meter una
    calle que no existe."""
    return calle in [c for c, _ in CALLES_POR_PATIO.get(patio, [])]


def sugerencia(db, unidad, usuario, ahora=None):
    """Todo lo que la pantalla necesita para dibujar el bloque de ubicacion.

        {"patio": str,            el preseleccionado
         "patios": [str],
         "atajos": [(patio, calle)],
         "calles": {patio: {"a_la_vista": [(calle, n)],
                            "plegadas":   [(calle, n)]}},
         "fresco": bool}

    `calles` trae LOS CUATRO PATIOS y no solo el sugerido. La pantalla los
    dibuja todos y muestra el del patio elegido: cambiar de patio no puede
    costar una vuelta al servidor, porque esto se usa parado en el patio con la
    señal que haya. Son 22 chips en total, no vale la pena pedirlos de a uno.

    CADA SEÑAL HACE SOLO LO QUE SE MIDIO QUE HACE, y por eso el patio NO sale
    de la tanda. Sale siempre de `patio_sugerido`, que mide origen + cliente.
    Tomarlo de la tanda parece mas fresco y es peor: si el movilizador viene
    estacionando CIDEF en PATIO 5 y la unidad que tiene delante es CARFLEX, la
    tanda lo manda al patio equivocado con toda confianza. Los dos atajos y el
    orden de las calles SI salen de la tanda -- ahi la recencia es lo medido.
    El atajo ademas lleva su propio patio, asi que tocarlo lo cambia igual.

    LA CALLE NUNCA VIENE PRESELECCIONADA, ni con tanda fresca. El patio es un
    achique y la calle es la respuesta: dejarla puesta convertiria el boton de
    confirmar en un solo toque sobre una prediccion, que es justo lo que hace
    caro el 15,4% de atajos errados. El piso son dos toques siempre.

    `fresco` False es el modo degradado y honesto: sin tanda no hay atajos y
    las calles van por frecuencia, que es el orden correcto cuando no hay tanda
    que seguir. Cuesta un toque mas en el primer movimiento del turno y se cura
    con ese mismo movimiento."""
    tanda = tanda_en_curso(db, usuario, ahora=ahora)
    calles = {}
    for p in PATIOS:
        a_la_vista, plegadas = calles_de(p, tanda)
        calles[p] = {
            "a_la_vista": [(c, volumen(p, c)) for c in a_la_vista],
            "plegadas": [(c, volumen(p, c)) for c in plegadas],
            # Cual encabeza por la tanda y no por frecuencia. La pantalla la
            # marca solo en ese caso: el punto de "reciente" tiene que
            # significar reciente, no primera.
            "reciente": next((c for p2, c in tanda if p2 == p), None),
        }
    return {
        "patio": patio_sugerido(unidad),
        "patios": PATIOS,
        "atajos": tanda,
        "calles": calles,
        "fresco": bool(tanda),
    }
