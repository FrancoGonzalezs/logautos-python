"""
modulos/catalogos.py -- catalogo de valores de negocio derivados del dato,
empezando por `orden_trabajo.requerimiento`.

El problema que resuelve: el dashboard PHP (`dash_ot_nuevo_new.php`) recorre
una lista de 16 requerimientos escrita a mano, pero en las 121.592 filas de
orden_trabajo hay 66 valores distintos. Siete de los que faltan tienen
volumen alto -- RECEPCION (7.105), SALIDA DE MERCANCIA (6.769), PRESUPUESTO
(4.205), COMBUSTIBLE (3.960), PICKING (3.152), LAMINADO (2.214), CONTENEDOR
(2.083) -- asi que hoy el dashboard subestima la operacion real por unas
29.000 OT. Por eso aca el catalogo se DERIVA de la tabla en vez de
escribirse a mano: un requerimiento nuevo aparece solo, sin tocar codigo.

La normalizacion esta partida en dos capas a proposito:

  - `normalizar()` hace lo mecanico y verificable: espacios, saltos de linea
    literales, basura al final, tildes y mayusculas. Nadie tiene que opinar
    sobre si 'RECEPCIÓN' y 'RECEPCION' son lo mismo.

  - `FUSIONES_CRITERIO` junta lo que requiere un juicio de negocio, como
    'PATENTE' con 'PATENTES'. Vive en una constante corta y legible para que
    el dueño del sistema pueda revisarla y discutirla, en vez de quedar
    enterrada dentro de una funcion.

Lo que NO se fusiona, y por que:
  - 'COMBUSTIBLE' (3.960) y 'COMBUSTIBLE POR NORMA' (8.155) son cobros
    distintos; juntarlos mezclaria lo facturable con lo que va incluido.
  - 'CARGA DE COMBUSTIBLE ADICIONAL A LA NORMA N LITROS' tiene seis
    variantes segun los litros. Fusionarlas perderia la cantidad, que es
    justamente el dato que las distingue; se agrupan como familia para
    mirarlas juntas, pero cada una conserva su valor.
"""

import re
import unicodedata

from flask import Blueprint, render_template

from core import consultar

bp = Blueprint("catalogos", __name__, url_prefix="/catalogos")

# Fusiones que dependen de un criterio de negocio, no de la ortografia.
# clave = valor ya normalizado; valor = el canonico al que se lleva.
FUSIONES_CRITERIO = {
    "PATENTE": "PATENTES",
}

# Valores que son claramente pruebas de desarrollo, no operacion real.
VALORES_DE_PRUEBA = {
    "PRUEBA",
    "PRUEBA IT 2",
    "GRABADO DE PATENTE // PRUEBA FUNCION",
}

# Correcciones de tipeo confirmadas contra el dato (la variante correcta es
# ordenes de magnitud mas frecuente que la equivocada).
TIPEOS = {
    "SALIDA DE MERCARNCIA": "SALIDA DE MERCANCIA",
}

# Familias para agrupar variantes que se distinguen solo por una cantidad.
RE_FAMILIA_COMBUSTIBLE = re.compile(
    r"^CARGA DE COMBUSTIBLE ADICIONAL A LA NORMA \d+ LITROS$")


def sin_tildes(texto):
    """Descompone en NFD y bota los diacriticos. Es lo que hace que
    'INSPECCIÓN MECÁNICA' (222 filas) y 'INSPECCION MECANICA' (553) cuenten
    como el mismo requerimiento."""
    descompuesto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def normalizar(valor):
    """Limpieza mecanica. Devuelve '' para lo que no tiene valor util.

    El orden importa: primero se sacan los saltos de linea literales (hay una
    fila con 'SERVICIO MECANICO\\r\\n' guardado tal cual), despues la basura
    de los bordes ('PICKING|', 53 filas), y recien ahi se colapsan espacios
    -- al reves, el '|' quedaria pegado a la ultima palabra."""
    if valor is None:
        return ""
    texto = str(valor).replace("\r", " ").replace("\n", " ")
    texto = texto.strip().strip("|/-").strip()
    texto = re.sub(r"\s+", " ", texto)
    texto = sin_tildes(texto).upper()
    texto = TIPEOS.get(texto, texto)
    return FUSIONES_CRITERIO.get(texto, texto)


def familia(canonico):
    """Etiqueta opcional para mirar juntas las variantes que solo cambian en
    una cantidad. No fusiona nada: es solo una agrupacion de lectura."""
    if RE_FAMILIA_COMBUSTIBLE.match(canonico):
        return "CARGA DE COMBUSTIBLE ADICIONAL (por litros)"
    return None


def catalogo_requerimientos():
    """Arma el catalogo desde la tabla: para cada valor canonico, cuantas OT
    tiene y de que variantes crudas viene."""
    filas = consultar(
        "SELECT requerimiento, COUNT(*) n FROM orden_trabajo GROUP BY requerimiento")

    catalogo = {}
    vacios = 0
    for fila in filas:
        crudo, n = fila["requerimiento"], fila["n"]
        canonico = normalizar(crudo)
        if not canonico:
            vacios += n
            continue
        entrada = catalogo.setdefault(canonico, {
            "canonico": canonico,
            "total": 0,
            "variantes": [],
            "familia": familia(canonico),
            "de_prueba": canonico in VALORES_DE_PRUEBA,
        })
        entrada["total"] += n
        entrada["variantes"].append({"crudo": crudo, "n": n, "limpio": crudo == canonico})

    for entrada in catalogo.values():
        entrada["variantes"].sort(key=lambda v: -v["n"])

    ordenado = sorted(catalogo.values(), key=lambda e: -e["total"])
    return ordenado, vacios


@bp.route("/requerimientos")
def requerimientos():
    catalogo, vacios = catalogo_requerimientos()
    crudos = consultar("SELECT COUNT(DISTINCT requerimiento) n FROM orden_trabajo")[0]["n"]
    total_ot = consultar("SELECT COUNT(*) n FROM orden_trabajo")[0]["n"]

    con_variantes = [e for e in catalogo if len(e["variantes"]) > 1]
    de_prueba = [e for e in catalogo if e["de_prueba"]]

    return render_template(
        "catalogo_requerimientos.html",
        catalogo=catalogo, vacios=vacios, crudos=crudos, total_ot=total_ot,
        con_variantes=con_variantes, de_prueba=de_prueba)
