"""
modulos/catalogo_mecanica.py -- los 65 campos del check list mecanico.

El catalogo sale del MENU, no del DATO
======================================

Los valores de abajo se leyeron de `views/nota/check_list_mecanica.php` -- de
los `<input type=radio>` que el formulario ofrece --, y NO de los valores
distintos que aparecen en la tabla replicada.

La diferencia no es teorica, la medimos. Agrupando por valores observados en
los ultimos doce meses salen DIECINUEVE vocabularios distintos; agrupando por
lo que el formulario ofrece salen SIETE. Los otros doce son espejismos: el
grupo ['Bueno'] de `bocina`, `tdi`, `cc`, `Chapas` y `fa` existe porque a
nadie se le rompio nunca la bocina, no porque el formulario ofrezca una sola
opcion. Un catalogo derivado del dato le habria sacado a esos cinco campos las
opciones `Regular`, `Malo` y `No Aplica` -- y el dia que aparezca una bocina
mala, REGLA no tendria como anotarla.

Es la misma leccion del audit de los `switch` del legado: el dato dice que
paso, el menu dice que se puede elegir. Para construir una pantalla manda el
menu.

LA ASIMETRIA QUE HAY QUE NO EMPAREJAR
-------------------------------------
`tet` (testigos en tablero) ofrece TRES opciones y `aa` (aire acondicionado)
ofrece esas mismas tres MAS `No Aplica`. Se ven iguales y estan uno al lado
del otro en el formulario. Tienen su propia constante cada uno a proposito:
un solo `ENCENDIDO` compartido seria mas corto y estaria mal en uno de los
dos, y el error saldria como una opcion de mas o de menos en una pantalla que
nadie vuelve a mirar.

LOS PORCENTAJES SE GUARDAN CON EL SIGNO
---------------------------------------
`nd`, `nt` y `nr` son sliders de 0 a 100 de a 10, y el controlador viejo hace
`'nd'=>$nd.'%'`: el signo lo pega PHP al guardar, no el navegador. REGLA hace
lo mismo, si no la columna quedaria con `40` donde el legado escribe `40%` y
la comparacion de la reconciliacion marcaria divergencia en todas las filas.

`estanque` NO lleva signo: es un slider de 0 a 10 que se guarda pelado.
"""

import re

# ---------------------------------------------------------------------------
# Los siete menus
# ---------------------------------------------------------------------------

ESTADO_PIEZA = ("Bueno", "Regular", "Malo", "No Aplica")
TIPO_RADIO = ("Panel", "Tactil", "No Equipado")
ENCENDIDO = ("Bueno", "No Enciende", "Encendido")                      # solo `tet`
ENCENDIDO_CON_NA = ("Bueno", "No Enciende", "Encendido", "No Aplica")  # solo `aa`
CUARTOS = ("0-25", "25-50", "50-75", "75-100")
NIVEL = ("En nivel", "Fuera de Nivel", "N/A", "N/D")
SI_NO = ("Si", "No", "N/A")
PRESENCIA = ("Presenta", "No Presenta")

# Y el desplegable que no es un radio.
ESTADO_CARFLEX = ("Usado", "Semi Nuevo", "Nuevo")

# El slider del estanque: 0 a 10, de a 1. Se guarda pelado, sin unidad.
#
# Ojo con este campo al leer sus datos: el 89% de las filas dice `1`, que es
# el valor con el que el slider ARRANCA. No es que casi todos los autos
# lleguen con un decimo de estanque: es que casi nadie lo mueve. Es un dato
# que existe y no informa, y conviene saberlo antes de construir cualquier
# cosa encima.
ESTANQUE_MINIMO = 0
ESTANQUE_MAXIMO = 10


# ---------------------------------------------------------------------------
# Los 65 campos, en el orden del formulario original
# ---------------------------------------------------------------------------
#
# (columna, etiqueta, tipo, opciones)
#
# El tipo dice como se pinta y como se valida:
#
#   "opcion"      un valor de la tupla, obligatorio
#   "porcentaje"  slider 0..100 de a 10; se guarda con '%' pegado
#   "texto"       libre
#   "bateria"     el caso especial, ver `validar_bateria`
#
# El orden importa: el operario compara su pantalla contra la del sistema
# viejo durante el mes en paralelo, y un campo movido de lugar es un campo que
# se saltea.

ACCESORIOS = (
    ("llaves",           "Llaves",                        "texto",   None),
    ("tad",              "Tapiz Asientos Delanteros",     "opcion",  ESTADO_PIEZA),
    ("tat",              "Tapiz Asientos Traseros",       "opcion",  ESTADO_PIEZA),
    ("tca",              "Tercera Corrida Asiento",       "opcion",  ESTADO_PIEZA),
    ("bateria",          "Bateria",                       "bateria", None),
    ("alternador",       "Alternador",                    "opcion",  ESTADO_PIEZA),
    ("bocina",           "Bocina",                        "opcion",  ESTADO_PIEZA),
    ("tdi",              "Tablero de Instrumento",        "opcion",  ESTADO_PIEZA),
    ("mdc",              "Mando de calefaccion",          "opcion",  ESTADO_PIEZA),
    ("Limpiaparabrisas", "Limpia Parabrisa",              "opcion",  ESTADO_PIEZA),
    ("er",               "Espejos retrovisores",          "opcion",  ESTADO_PIEZA),
    ("cc",               "Cierre centralizado",           "opcion",  ESTADO_PIEZA),
    ("ae",               "Alzavidrios electricos",        "opcion",  ESTADO_PIEZA),
    ("Sunroof",          "Sunroof",                       "opcion",  ESTADO_PIEZA),
    ("Chapas",           "Chapas",                        "opcion",  ESTADO_PIEZA),
    ("Airbag",           "Airbag",                        "opcion",  ESTADO_PIEZA),
    ("fa",               "Frenos ABS",                    "opcion",  ESTADO_PIEZA),
    ("vc",               "Velocidad crucero",             "opcion",  ESTADO_PIEZA),
    ("Bluetooth",        "Bluetooth",                     "opcion",  ESTADO_PIEZA),
    ("Neblineros",       "Neblineros",                    "opcion",  ESTADO_PIEZA),
    ("Gata",             "Gata",                          "opcion",  ESTADO_PIEZA),
    ("Extintor",         "Extintor",                      "opcion",  ESTADO_PIEZA),
    ("Llantas",          "Llantas",                       "opcion",  ESTADO_PIEZA),
    ("Radio",            "Radio",                         "opcion",  TIPO_RADIO),
    ("tet",              "Testigos en Tablero",           "opcion",  ENCENDIDO),
    ("aa",               "Aire Acondicionado",            "opcion",  ENCENDIDO_CON_NA),
)

TREN_MOTRIZ = (
    ("sd",       "Suspension delantera",                 "opcion", ESTADO_PIEZA),
    ("st",       "Suspension trasera",                   "opcion", ESTADO_PIEZA),
    ("sdd",      "Sistema de direccion",                 "opcion", ESTADO_PIEZA),
    ("hec",      "Homocineticas y/o Eje Cardan",         "opcion", ESTADO_PIEZA),
    ("pfd",      "Pastillas frenos delanteros",          "opcion", ESTADO_PIEZA),
    ("pft",      "Pastillas frenos traseros",            "opcion", ESTADO_PIEZA),
    ("dfd",      "Discos frenos delanteros",             "opcion", ESTADO_PIEZA),
    ("dft",      "Discos frenos traseros",               "opcion", ESTADO_PIEZA),
    ("fde",      "Freno de estacionamiento",             "opcion", ESTADO_PIEZA),
    ("cda",      "Carter de aceite",                     "opcion", ESTADO_PIEZA),
    ("etv",      "Empaquetadura tapa V/V",               "opcion", ESTADO_PIEZA),
    ("mdr",      "Mangueras de refrigeracion",           "opcion", ESTADO_PIEZA),
    ("Radiador", "Radiador",                             "opcion", ESTADO_PIEZA),
    ("dde",      "Deposito de Expansion",                "opcion", ESTADO_PIEZA),
    ("cdac",     "Compresor de A/C",                     "opcion", ESTADO_PIEZA),
    ("cdac2",    "Condensador de A/C",                   "opcion", ESTADO_PIEZA),
    ("cbbe",     "Cables de bujias / Bobinas de Encendido", "opcion", ESTADO_PIEZA),
    ("fda",      "Filtro de aire",                       "opcion", ESTADO_PIEZA),
    ("mdp",      "Motor de Partida",                     "opcion", ESTADO_PIEZA),
    ("pef",      "Partida en frio (si aplica)",          "opcion", ESTADO_PIEZA),
    ("cdas",     "Correa(s) de accesorio(s)",            "opcion", ESTADO_PIEZA),
    ("ftcdt",    "Funcionamiento testigos caja de transferencia (4X4)",
                                                         "opcion", ESTADO_PIEZA),
    ("sdacdt",   "Sonido de acople caja de transferencia", "opcion", ESTADO_PIEZA),
    ("nam",      "Nivel aceite motor",                   "opcion", CUARTOS),
    ("nlr",      "Nivel liquido refrigerante",           "opcion", CUARTOS),
    ("nldf",     "Nivel liquido de frenos",              "opcion", NIVEL),
    ("nldh",     "Nivel liquido direccion hidraulica",   "opcion", NIVEL),
    ("nlde",     "Nivel liquido de embrague",            "opcion", NIVEL),
    ("nata",     "Nivel aceite transmision automatica",  "opcion", NIVEL),
    ("fadm",     "Fugas aceite de motor",                "opcion", SI_NO),
    ("flr",      "Fugas liquido refrigerante",           "opcion", SI_NO),
    ("flshde",   "Fugas liquido sistema hidraulico de embrague", "opcion", SI_NO),
    ("fldd",     "Fugas liquido de direccion",           "opcion", SI_NO),
    ("fatma",    "Fugas aceite transmision mecanica y automatica", "opcion", SI_NO),
    ("fdaed",    "Fugas de aceite en diferenciales",     "opcion", SI_NO),
    ("nd",       "Neumaticos delanteros",                "porcentaje", None),
    ("nt",       "Neumaticos traseros",                  "porcentaje", None),
    ("nr",       "Neumatico repuesto",                   "porcentaje", None),
    ("pocc",     "Presencia Oxido Chasis/Carroceria",    "opcion", PRESENCIA),
)

SECCIONES = (
    ("Accesorios", ACCESORIOS),
    ("Tren Motriz", TREN_MOTRIZ),
)

CAMPOS = ACCESORIOS + TREN_MOTRIZ

# Indexado por columna, que es como lo consulta el guardado.
POR_COLUMNA = {c[0]: c for c in CAMPOS}


# ---------------------------------------------------------------------------
# `faltante`: la columna que el formulario no tiene
# ---------------------------------------------------------------------------
#
# `check_list_mecanica.faltante` existe en el esquema y esta VACIA en las 1011
# filas de 2026. No es que se use poco: en el controlador la linea que la
# leeria esta comentada --
#
#     //$faltante = $this->input->post('faltante');
#
# -- y en la vista no hay ningun campo con ese nombre. Es inalcanzable, no
# infrautilizada. REGLA no la ofrece ni la empuja.
#
# Se anota aca y no se saca de la replica: la replica es la foto del sistema
# viejo y tiene que seguir teniendo las columnas que el sistema viejo tiene.
COLUMNAS_MUERTAS = ("faltante",)


# ---------------------------------------------------------------------------
# `bateria`: numero con unidad, o "Cambio"
# ---------------------------------------------------------------------------

# El voltaje de una bateria de auto. El rango es generoso a proposito: 6 V
# cubre las baterias viejas de 6 voltios y 16 V cubre una medicion con el
# motor en marcha, que sube a 14 y algo. Fuera de eso es un error de tipeo,
# no una bateria.
BATERIA_MINIMO = 6.0
BATERIA_MAXIMO = 16.0

# La palabra que dice "esta bateria no se mide, se cambia". Va explicita en
# vez de dejar que alguien la escriba en un campo de texto libre: si es una
# opcion del menu, la pantalla la puede contar; si es texto libre, no.
BATERIA_CAMBIO = "Cambio"

_BATERIA = re.compile(r"^\s*(\d{1,2})[.,](\d{1,2})\s*[vV]?\s*$")


def validar_bateria(valor):
    """Devuelve (valor_normalizado, error).

    En la replica esta columna es un basural de formatos: '12.60', '12,80',
    '12.56v', '12.34v'. Los cuatro significan lo mismo y se guardan distinto,
    asi que ninguna consulta puede promediarlos ni ordenarlos.

    REGLA normaliza a UN formato -- dos decimales y el sufijo 'V' -- y sigue
    aceptando las cuatro formas de escribirlo. La coma decimal se acepta
    porque en un teclado de telefono en espanol es la que esta a mano, y
    rechazarla seria hacerle pelear al mecanico con el teclado.

    NO se convierte lo que ya esta guardado en el legado. Normalizar hacia
    atras es reescribir la historia de un sistema que sigue vivo y que en el
    mes en paralelo puede estar leyendo esas mismas filas."""
    texto = (valor or "").strip()
    if not texto:
        return None, "Falta la bateria"
    if texto.lower() == BATERIA_CAMBIO.lower():
        return BATERIA_CAMBIO, None

    m = _BATERIA.match(texto)
    if not m:
        return None, ("La bateria va como voltaje -- 12.60, 12,60 o 12.6v -- "
                      "o la opcion '{}'".format(BATERIA_CAMBIO))
    volts = float("{}.{}".format(m.group(1), m.group(2)))
    if not (BATERIA_MINIMO <= volts <= BATERIA_MAXIMO):
        return None, ("{:.2f} V esta fuera de rango ({:.0f} a {:.0f} V). "
                      "Revisa el punto decimal.".format(
                          volts, BATERIA_MINIMO, BATERIA_MAXIMO))
    return "{:.2f}V".format(volts), None


def validar_porcentaje(valor):
    """Los sliders `nd`/`nt`/`nr`: 0 a 100 de a 10, y se guarda con el '%'."""
    texto = (valor or "").strip().rstrip("%").strip()
    if not texto:
        return None, "Falta el porcentaje"
    try:
        n = int(texto)
    except ValueError:
        return None, "El porcentaje va como numero"
    if n < 0 or n > 100 or n % 10:
        return None, "El porcentaje va de 0 a 100, de a 10"
    return "{}%".format(n), None


def validar_estanque(valor):
    """El slider del estanque: entero de 0 a 10, sin unidad."""
    texto = (valor or "").strip()
    if not texto:
        return None, "Falta el estanque"
    try:
        n = int(texto)
    except ValueError:
        return None, "El estanque va como numero"
    if n < ESTANQUE_MINIMO or n > ESTANQUE_MAXIMO:
        return None, "El estanque va de {} a {}".format(
            ESTANQUE_MINIMO, ESTANQUE_MAXIMO)
    return str(n), None


def validar_campo(columna, valor):
    """Valida UN campo del catalogo. Devuelve (valor_normalizado, error)."""
    campo = POR_COLUMNA.get(columna)
    if campo is None:
        return None, "Campo desconocido: {}".format(columna)
    _, etiqueta, tipo, opciones = campo

    if tipo == "bateria":
        return validar_bateria(valor)
    if tipo == "porcentaje":
        return validar_porcentaje(valor)
    if tipo == "texto":
        texto = (valor or "").strip()
        return (texto, None) if texto else (None, "Falta " + etiqueta)

    texto = (valor or "").strip()
    if not texto:
        return None, "Falta " + etiqueta
    if texto not in opciones:
        # El valor no esta en el menu. NO se acepta igual ni se guarda como
        # viene: el catalogo es el contrato de la columna, y una pantalla que
        # acepta lo que sea es la lista blanca que ignora en silencio con otro
        # nombre.
        return None, "{}: '{}' no es una opcion".format(etiqueta, texto)
    return texto, None
