"""
modulos/combustible.py -- la compuerta de stock y el descuento.

El PDI del legado no procede siempre. Su bloque entero cuelga de

    $combu   = strtoupper($tipo_combustible);
    $id_combu = getIdCombustible($combu);          // busca por `nombre`
    $stock    = getStockCombustible_numerico($id_combu);
    if($stock > 20 || $combu == 'ELECTRICO') { ...toda la PDI... }
    else { flashdata('error', 'STOCK DE COMBUSTIBLE NO ES SUFICIENTE'); }

y si no pasa, NO se guarda la PDI, NO se crean las OT y NO se descuenta nada.


LA COMPUERTA SE EVALUA CONTRA LA REPLICA, NO CONTRA EL LEGADO
=============================================================

Decidido el 2026-08-27. Si el legado esta lento, la pantalla del patio no se
puede colgar: toda la arquitectura es fire-and-forget por eso mismo. El stock
lo trae el pull -- entidad `stock_consumibles`, dos filas enteras cada vuelta
-- y esta compuerta lee la replica.

La consecuencia es una carrera de hasta 300 s: entre que el legado descuenta y
que REGLA se entera, REGLA puede dejar pasar una PDI que alla no pasaria. Con
once PDI por dia es un limite aceptable y esta escrito. Lo que NO puede pasar
es lo contrario -- que REGLA frene una PDI que si pasaria -- porque el stock de
la replica solo puede estar MAS ALTO que el real, nunca mas bajo... salvo que
alguien cargue combustible, que es justo el caso en que frenar de mas dura 300
segundos y se cura solo.


LOS DOS VOCABULARIOS, OTRA VEZ, Y ACA CIERRAN EL CIRCULO
========================================================

`stock_consumibles` tiene DOS filas y los nombres en MAYUSCULAS:

    id | nombre  | stock | precio | promedio
     2 | DIESEL  |     5 |   1500 |     1091
     3 | BENCINA |   563 |   1500 |     1188

El formulario postea `Bencina` / `Diesel` / `Electrico`. El PHP resuelve la
diferencia con `strtoupper()` antes de buscar, y eso hay que replicarlo.

**NO HAY FILA PARA GASOLINA.** Es el mismo agujero que en la OT, visto desde el
otro lado: si esa cadena llegara hasta aca, `getIdCombustible('GASOLINA')`
del legado devolveria NULL, `getStockCombustible_numerico(NULL)` reventaria o
daria vacio, y la compuerta decidiria con basura. Son 2.416 unidades con ese
valor en `tipo_combu`.

Por eso `fila_de()` exige encontrar EXACTAMENTE UNA fila y revienta si no. Cero
filas o dos son las dos formas de que el dato este roto, y las dos terminan en
"no se descuento nada" si se las deja pasar en silencio.
"""

from core import consultar, get_db
from modulos.ot_pdi import CombustibleDesconocido, exigir_combustible

# El umbral del PHP: `if($stock > 20 || ...)`. ESTRICTAMENTE MAYOR -- con 20
# justo no pasa. Se replica el operador, no la intencion.
UMBRAL_STOCK = 20

# El unico que pasa la compuerta sin mirar el stock, porque no consume.
SIN_CONSUMO = "ELECTRICO"

TABLA = "stock_consumibles"


class StockNoResuelto(RuntimeError):
    """No se pudo identificar la fila de stock de ese combustible.

    Lleva `motivo_usuario`: el texto que la pantalla muestra. Los dos casos
    -- la tabla vacia y el combustible sin fila -- se arreglan distinto y el
    operario tiene que poder distinguirlos sin llamar a nadie."""

    def __init__(self, mensaje, motivo_usuario=""):
        super(StockNoResuelto, self).__init__(mensaje)
        self.motivo_usuario = motivo_usuario or mensaje


def asegurar_tabla(db=None):
    """Crea `stock_consumibles` vacia si no esta.

    La llena el PULL, no esto: son las dos filas del legado. Se crea vacia para
    que la consulta de la compuerta falle con un mensaje y no con
    'no such table', que es un 500 y no le dice nada a nadie.

    El esquema sale de la tabla real (InnoDB, dos filas):
        id int PK AUTO_INCREMENT | nombre varchar(50) | stock int
        precio int | promedio int"""
    (db or get_db()).execute("""
        CREATE TABLE IF NOT EXISTS stock_consumibles (
          id       INTEGER PRIMARY KEY,
          nombre   TEXT,
          stock    INTEGER,
          precio   INTEGER,
          promedio INTEGER
        )""")


def fila_de(combustible, db=None):
    """La fila de `stock_consumibles` de ese combustible.

    Revienta si no encuentra EXACTAMENTE UNA. Cero filas y dos filas son las
    dos formas de que el dato este roto, y las dos, si se dejaran pasar,
    terminan en un descuento que no ocurre y que nadie ve."""
    nombre = exigir_combustible(combustible)          # revienta si no es de los tres
    asegurar_tabla(db)
    filas = consultar(
        'SELECT * FROM "{}" WHERE UPPER(TRIM(nombre)) = ?'.format(TABLA),
        (nombre,))
    if len(filas) == 1:
        return filas[0]

    # LOS DOS CASOS SE SEPARAN, porque no se arreglan igual.
    total = consultar('SELECT COUNT(*) AS n FROM "{}"'.format(TABLA),
                      una=True)["n"]
    if total == 0:
        raise StockNoResuelto(
            "{} esta vacia: el pull del stock nunca corrio".format(TABLA),
            "Todavía no tengo el stock de combustible del sistema anterior, "
            "así que no puedo saber si alcanza. Se resuelve solo en unos "
            "minutos, cuando corra la sincronización.")
    raise StockNoResuelto(
        "combustible {!r} -> {} filas en {} (se esperaba 1). La tabla tiene "
        "DIESEL y BENCINA, en mayusculas, y nada mas: no hay fila para "
        "GASOLINA ni para HIBRIDO.".format(combustible, len(filas), TABLA),
        "El sistema anterior no tiene stock cargado para {}. Avisá a "
        "sistemas.".format(nombre))


def evaluar(combustible, db=None):
    """¿Procede la PDI? Devuelve un dict que dice que se hace y por que.

        {"pasa": bool,
         "combustible": 'BENCINA' | 'DIESEL' | 'ELECTRICO',
         "consume": bool,          # si hay que descontar
         "stock": int | None,      # el de la replica al momento de mirar
         "motivo": str}            # texto para la pantalla

    NUNCA devuelve "no se" -- o pasa, o no pasa con motivo, o revienta. Un
    tercer estado ambiguo se propaga como "seguimos igual" y termina en una PDI
    guardada sin descuento, que es exactamente el silencio que hay que romper.
    """
    nombre = exigir_combustible(combustible)

    if nombre == SIN_CONSUMO:
        # El `|| $combu == 'ELECTRICO'` del PHP. Pasa sin mirar el stock, y no
        # descuenta: no hay fila que descontar y no la busca.
        return {"pasa": True, "combustible": nombre, "consume": False,
                "stock": None,
                "motivo": "Eléctrico: no consume combustible."}

    fila = fila_de(nombre, db)
    stock = int(fila["stock"] or 0)
    if stock > UMBRAL_STOCK:
        return {"pasa": True, "combustible": nombre, "consume": True,
                "stock": stock,
                "motivo": "Stock de {}: {} litros.".format(nombre, stock)}
    return {"pasa": False, "combustible": nombre, "consume": True,
            "stock": stock,
            "motivo": ("STOCK DE COMBUSTIBLE NO ES SUFICIENTE. {} tiene {} "
                       "litros y el mínimo para hacer la PDI es más de {}."
                       .format(nombre, stock, UMBRAL_STOCK))}
