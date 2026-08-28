"""
modulos/ot_pdi.py -- las DOS ordenes de trabajo que genera una PDI.

Existen porque **el push no las crea**. `Api_regla.php` escribe con el query
builder, asi que no dispara nada del controlador: el legado crea sus OT desde
`Pedido.php`, no desde un trigger. Una PDI empujada por REGLA deja al legado
con la fecha puesta y sin las dos OT -- o sea sin cobrar. Las tiene que hacer
REGLA.

Son dos y son distintas:

    PDI                     precio fijo, costo 0, margen 100%
    COMBUSTIBLE POR NORMA   precio == costo, margen 0%. Es un traspaso: el
                            combustible se cobra a precio de costo.

TODO ESTO ES PLATA, asi que vale la regla 2 del proyecto: `core.peso()` con
Decimal y ROUND_HALF_UP, nunca `round()`. Y validacion al 100% contra el
historico -- si no llega al 100%, hay una regla que no entendimos.
`scripts/probar_ot_pdi.py` corre esa validacion y es parte de la suite.


LA VENTANA DE VALIDACION SON TRES MESES, NO SEIS
================================================

Regla 0 otra vez, y esta vez ni los 6 meses alcanzaban. La OT de combustible
tiene TRES formas distintas en el historico y la de hoy **arranca en junio de
2026**:

    hasta 2026-05    precio != costo, margenes de 4% a 38%   (0 filas de la forma actual)
    desde 2026-06    precio == costo, margen 0               (441 en junio, 505 en julio)

Y no es casualidad que coincida con el precio de la PDI: el 2026-06-02 hay 27
OT de PDI a 46.878 y 3 a 49.000. **El mismo despliegue cambio las dos cosas, a
mitad de ese dia.** Medir sobre 6 meses habria mezclado las dos versiones y
dado un promedio que no existe -- exactamente el `IT` 69,9% / `It` 30% de la
tabla de calles.

Por eso las dos constantes de abajo llevan fecha de vigencia y la validacion
mira `> 2026-06-02`, estricto: el dia del cambio tiene las dos formas y no se
puede clasificar sin la hora, que `createdDtm` no guarda.
"""

from core import peso

# ---------------------------------------------------------------------------
# Precios
# ---------------------------------------------------------------------------
#
# CON FECHA DE VIGENCIA, como TARIFAS_ACOPIO y por el mismo motivo: el precio
# de la PDI ya cambio una vez y la fila vieja es la que explica las facturas ya
# emitidas. Agregar un precio es agregar una FILA, nunca editar una existente.
#
# El precio anterior (46.878) queda anotado aunque REGLA no lo use: es lo que
# permite reconocer una OT vieja sin ir a buscar por que "no calza".
PRECIOS_PDI = [
    # (vigente_desde, precio)
    ("2026-06-03", 49000),   # 970 de 970 calzan
    ("2000-01-01", 46878),   # hasta el 2026-06-02 inclusive
]

# Pesos por litro, por combustible. Estan HARDCODEADOS en el PHP
# (`$valor_bencina = 1970; $valor_diesel = 2070;`) con las consultas al
# promedio de `stock_consumibles` comentadas justo arriba. Se replica el
# hardcode, no la intencion: si alguien descomenta aquello, esto se remide.
VALOR_LITRO = {
    "BENCINA": 1970,
    "DIESEL": 2070,
}

# Las marcas que cargan 15 litros en vez de 20, sea cual sea el modelo.
MARCAS_15_LITROS = {"DFM", "ZNA"}

# Y los prefijos de modelo que tambien cargan 15 -- PERO NO SON LOS MISMOS
# PARA LOS DOS COMBUSTIBLES, y ese es el hallazgo que costo encontrar.
#
# La rama Bencina compara CUATRO prefijos:
#     strncmp($modelo,'G7',2) || strncmp($modelo,'G9',2) ||
#     strncmp($modelo,'V7',2) || strncmp($modelo,'V9',2)
#
# La rama Diesel compara UNO:
#     strncmp($modelo,'G7',2)
#
# porque adentro de la rama Diesel solo se asigna `$g7 = 'G7'` -- las otras
# tres variables existen de un bloque anterior pero la condicion simplemente no
# las nombra. Y el bloque de MAS ARRIBA, el que calcula el precio del vehiculo,
# si compara los cuatro en las dos ramas. O sea que dentro del MISMO request
# hay dos reglas distintas para la misma pregunta.
#
# No se "arregla": un FOTON V9 a diesel carga 20 litros en el legado, y si REGLA
# le pone 15 la OT no coincide. Coincidir vale mas que tener razon.
#
# Costo 204 filas de 481 encontrarlo. Con los cuatro prefijos en las dos ramas
# la validacion daba 57,6%; con esta tabla da 100%.
PREFIJOS_15_LITROS = {
    "BENCINA": ("G7", "G9", "V7", "V9"),
    "DIESEL": ("G7",),
}

LITROS_POR_DEFECTO = 20
LITROS_REDUCIDO = 15

REQUERIMIENTO_PDI = "PDI"
REQUERIMIENTO_COMBUSTIBLE = "COMBUSTIBLE POR NORMA"


def precio_pdi(fecha):
    """El precio de la PDI vigente en esa fecha ('YYYY-MM-DD')."""
    for desde, valor in PRECIOS_PDI:
        if (fecha or "") >= desde:
            return valor
    return PRECIOS_PDI[-1][1]


class CombustibleDesconocido(ValueError):
    """El combustible no es ninguno de los tres que el legado sabe leer."""


# LOS TRES VALORES, EXACTOS, que el PHP compara:
#
#     if($tipo_combustible == 'Bencina')  elseif(... == 'Diesel')  ...
#
# Comparacion `==` de PHP entre strings: EXACTA y sensible a la caja. Cualquier
# otra cosa no entra en ninguna rama, y como no hay `else`, la unidad se queda
# sin OT y sin descuento de stock. En silencio.
COMBUSTIBLES = ("Bencina", "Diesel", "Electrico")

# Los que NO consumen combustible pero si son validos.
SIN_CARGA = {"ELECTRICO"}


def exigir_combustible(crudo):
    """El combustible en mayusculas, o revienta con un mensaje que explica.

    NO NORMALIZA VOCABULARIOS, Y ESO ES EL PUNTO. La columna `tipo_combu` de la
    replica tiene DOS juegos de valores, escritos por procesos distintos:

        Bencina 8.627 · Diesel 1.888     <- los que postea el formulario de PDI
                                            y los unicos que el PHP compara
        GASOLINA 2.416 · DIESEL 2.417    <- otro proceso, en mayusculas
        BENCINA 10 · HIBRIDO 1 · ...

    **'GASOLINA' no existe en ninguna comparacion del PHP.** Son 2.416 unidades
    que, si esa cadena llegara al `if`, se quedarian sin OT y sin descuento sin
    que nadie se entere. La primera version de este modulo la mapeaba a
    'Bencina' -- y eso estaba MAL por partida doble: primero porque es una
    adivinanza (GASOLINA podria ser el vocabulario de otro proveedor, no
    necesariamente bencina de 95), y segundo porque haria que REGLA facture una
    OT que el legado no factura. Divergencia inventada.

    Se acepta cualquier caja de los TRES nombres, porque REGLA guarda lo que
    eligio el operario y la caja es cosmetica de este lado. Lo que no se acepta
    es un nombre distinto.

    OJO AL GUARDAR: del OTRO lado la caja SI importa. `tipo_combu` tiene que
    viajar exactamente como 'Bencina'/'Diesel'/'Electrico' -- 'BENCINA' en
    mayusculas tampoco entra en el `if` del PHP. Es la misma trampa que
    CALLE_IT ('It' y no 'IT').

    REVIENTA en vez de devolver None a proposito. Un None se propaga como "esta
    unidad no carga" y termina en una PDI sin OT, que es exactamente el
    silencio que hay que romper. Hoy no puede pasar porque la pantalla valida
    contra su propia lista, pero eso es suerte estructural: alcanza con que
    alguien pase `unidad["tipo_combu"]` -- la columna sucia -- en vez del valor
    del formulario."""
    texto = (crudo or "").strip()
    for valido in COMBUSTIBLES:
        if texto.upper() == valido.upper():
            return valido.upper()
    raise CombustibleDesconocido(
        "combustible {!r}: el legado solo reconoce {}. Si viene de la columna "
        "`tipo_combu` de la replica, OJO -- ahi conviven dos vocabularios y "
        "'GASOLINA' (2.416 unidades) no es ninguno de los tres. El valor tiene "
        "que salir del formulario de PDI, no de la columna."
        .format(crudo, " / ".join(COMBUSTIBLES)))


def litros_de(combustible, marca, modelo):
    """Los litros que corresponden, o 0 si ese combustible no carga.

    Revienta con `CombustibleDesconocido` si el valor no es uno de los tres.
    Cero significa "electrico", no "no se"."""
    combustible = exigir_combustible(combustible)
    if combustible not in VALOR_LITRO:
        return 0
    if (marca or "").strip().upper() in MARCAS_15_LITROS:
        return LITROS_REDUCIDO
    prefijo = (modelo or "").strip().upper()[:2]
    if prefijo in PREFIJOS_15_LITROS[combustible]:
        return LITROS_REDUCIDO
    return LITROS_POR_DEFECTO


def ot_de_pdi(unidad, fecha):
    """La OT de PDI. Precio fijo, costo cero, margen 100%."""
    precio = precio_pdi(fecha)
    return {
        "requerimiento": REQUERIMIENTO_PDI,
        "precio": precio,
        "costo": 0,
        "utilidad": precio,
        # El legado guarda 100 y no lo calcula: con costo 0 la division
        # `utilidad / costo` seria por cero. `$margen_total = 100` esta escrito
        # a mano justo arriba del calculo que nunca corre.
        "margen_utilidad": "100.00",
        "con_iva": peso(precio * 1.19),
    }


def ot_de_combustible(unidad, combustible):
    """La OT de combustible, o None si esa unidad no carga.

    Devuelve None SOLO para ELECTRICO, que pasa la compuerta pero no consume.
    Un combustible que no se reconoce NO devuelve None: revienta. Ver
    `exigir_combustible` -- devolver None ahi seria convertir un dato roto en
    una PDI sin OT, callada."""
    litros = litros_de(combustible, unidad["marca"], unidad["modelo"])
    if not litros:
        return None
    valor = VALOR_LITRO[exigir_combustible(combustible)]
    precio = peso(valor * litros)
    return {
        "requerimiento": REQUERIMIENTO_COMBUSTIBLE,
        # PRECIO == COSTO, y no es un error de transcripcion: el PHP calcula
        # `$precio_combu = 1970 * $cantidad` y `$precio2 = round($valor*15)`,
        # que dan lo mismo. El combustible se traspasa a costo.
        "precio": precio,
        "costo": precio,
        "utilidad": 0,
        "margen_utilidad": "0.00",
        # Acá vive el caso que justifica toda la regla del dinero: 29.550 *
        # 1.19 = 35164.5, que PHP guarda 35165 y `round()` de Python daria
        # 35164. Eran 119 filas de 1.061.
        "con_iva": peso(precio * 1.19),
        "litros": litros,
        "detalle": "COMBUSTIBLE POR NORMA {}LTS".format(litros),
    }


def ots_de_pdi(unidad, combustible, fecha):
    """Las OT que corresponden a esta PDI. Una o dos."""
    ots = [ot_de_pdi(unidad, fecha)]
    combu = ot_de_combustible(unidad, combustible)
    if combu is not None:
        ots.append(combu)
    return ots
