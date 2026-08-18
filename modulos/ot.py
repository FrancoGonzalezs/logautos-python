"""
modulos/ot.py -- listado de órdenes de trabajo.

Deliberadamente basico por ahora: una tabla paginada con lo minimo para poder
entrar desde el catalogo de requerimientos y ver que hay detras de cada uno.
El motor de OT del sistema viejo (Nota.php, ~22.000 lineas) es harina de otro
costal y se migra aparte.
"""

from flask import Blueprint, render_template, request

from core import consultar, escalar
from modulos.catalogos import normalizar

bp = Blueprint("ot", __name__, url_prefix="/ot")

POR_PAGINA = 50

COLUMNAS = [
    ("id", "id"),
    ("nombre", "Cliente"),
    ("vehiculo", "Vehículo"),
    ("requerimiento", "Requerimiento"),
    ("estado", "Estado"),
    ("fecha_cierre", "Cierre"),
    ("precio", "Precio"),
]


def variantes_de(canonico):
    """Todas las formas crudas en que ese requerimiento aparece en la tabla.

    El catalogo muestra el valor canonico (por ejemplo SERVICIO MECANICO), pero
    en la base conviven 'SERVICIO MECANICO', 'SERVICIO MECANICO ' con espacio
    y 'SERVICIO MECANICO\\r\\n' con salto de linea. Filtrar por el canonico a
    secas dejaria fuera 17 de las 8.324 filas, y peor: el total del listado no
    cuadraria con el numero que muestra el catalogo, que si las suma."""
    if not canonico:
        return []
    objetivo = normalizar(canonico)
    crudas = []
    for fila in consultar("SELECT DISTINCT requerimiento FROM orden_trabajo"):
        if normalizar(fila["requerimiento"]) == objetivo:
            crudas.append(fila["requerimiento"])
    return crudas


@bp.route("/")
def listado():
    requerimiento = request.args.get("requerimiento", "").strip()
    busqueda = request.args.get("q", "").strip()
    try:
        pagina = max(1, int(request.args.get("pagina", 1)))
    except ValueError:
        pagina = 1

    condiciones = []
    params = []
    crudas = []

    if requerimiento:
        crudas = variantes_de(requerimiento)
        if crudas:
            condiciones.append("requerimiento IN ({})".format(
                ", ".join("?" * len(crudas))))
            params.extend(crudas)
        else:
            condiciones.append("1 = 0")

    if busqueda:
        patron = "%{}%".format(busqueda)
        condiciones.append("(vehiculo LIKE ? OR nombre LIKE ? OR patente LIKE ?)")
        params.extend([patron] * 3)

    where = (" WHERE " + " AND ".join(condiciones)) if condiciones else ""

    total = escalar("SELECT COUNT(*) FROM orden_trabajo" + where, params)
    filas = consultar(
        "SELECT {} FROM orden_trabajo{} ORDER BY id DESC LIMIT ? OFFSET ?".format(
            ", ".join('"{}"'.format(c) for c, _ in COLUMNAS), where),
        params + [POR_PAGINA, (pagina - 1) * POR_PAGINA])

    return render_template(
        "ot_listado.html",
        filas=filas, columnas=COLUMNAS, total=total, pagina=pagina,
        paginas=max(1, (total + POR_PAGINA - 1) // POR_PAGINA),
        requerimiento=requerimiento, crudas=crudas, busqueda=busqueda)
