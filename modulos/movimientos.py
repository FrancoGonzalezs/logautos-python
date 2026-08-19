"""
modulos/movimientos.py -- Fase 1 del modulo de Movimientos: buscar una unidad
y recomendarle el siguiente paso del tramo Navegando -> PDI.

Por que un motor de reglas y no un flujo lineal
-----------------------------------------------
Los 299.322 movimientos de `registros` muestran que la operacion real tiene
vueltas atras y excepciones todo el tiempo: una unidad puede volver a lavado,
saltarse la revision de contenedor, o entrar a taller desde cualquier lado.
Un flowchart unico mentiria sobre como funciona. Aca el siguiente paso se
deduce de tres cosas -- el estado actual, el cliente y que hitos ya cumplio la
unidad -- y el usuario SIEMPRE puede elegir otro: eso no es un error, es la
operacion. Lo que se le pide es el motivo, para que el desvio quede medido.

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

# Motivos de desvio por tipo. La lista de 'de CC a taller' queda pendiente de
# definir con el dueño del sistema: es de la fase 2 y no se inventa aca.
MOTIVOS = {
    "lavado": ["Segundo Lavado", "Lavado de Revisión"],
    "generico": [
        "La unidad llegó en otro estado del esperado",
        "Se saltó un paso por urgencia de despacho",
        "El paso anterior ya estaba hecho y no figuraba",
        "Instrucción del cliente",
    ],
}


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


def _paso_tras_pdi(cliente):
    """Después de la PDI el camino se abre por cliente."""
    if cliente == "CARFLEX":
        return "check_mecanica", "CARFLEX sigue con revisión mecánica."
    if cliente == "CIDEF":
        return "lavado_produccion", "CIDEF sigue con lavado de producción."
    # SUPUESTO: los demás clientes van por el camino de CIDEF. Solo se
    # especificaron esos dos; confirmar antes de darlo por bueno.
    return "lavado_produccion", (
        "Sin regla propia para {}: se asume el camino de CIDEF.".format(cliente or "este cliente"))


def recomendar(unidad):
    """El siguiente paso sugerido, con el porqué a la vista.

    Devuelve None cuando la unidad ya pasó el tramo que cubre esta fase: los
    pasos de Lavado→IT→CC→ZD→Despacho son fase 2 y no se inventan acá."""
    cliente = normalizar(unidad["clientecompleto"])
    hitos = hitos_de(unidad)
    crudo, _ = estado_efectivo(unidad)
    estado = normalizar(crudo)

    # `ingresada` cubre el caso de una unidad que sigue marcada Navegando en la
    # replica pero cuyo ingreso ya se registro desde REGLA.
    if estado == "NAVEGANDO" and not hitos["ingresada"]:
        return _reco("ingreso", "La unidad está navegando: falta registrar su ingreso.")

    if hitos["pdi"]:
        paso, porque = _paso_tras_pdi(cliente)
        return _reco(paso, "La PDI ya está hecha. " + porque)

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


def _reco(clave, porque):
    paso = dict(PASOS[clave])
    paso["clave"] = clave
    paso["porque"] = porque
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
           usuario, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        unidad["id"], unidad["vin"], datos["paso"], datos["recomendado"],
        1 if datos["es_desvio"] else 0, datos.get("motivo"),
        datos.get("motivo_detalle"), datos.get("resultado_pdi"),
        datos.get("guia_ingreso"), datos.get("fecha"), datos.get("responsable"),
        id_actual(), datetime.now().isoformat(timespec="seconds")))
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
    otros = [dict(PASOS[c], clave=c) for c in PASOS
             if not recomendado or c != recomendado["clave"]]
    estado, desde_regla = estado_efectivo(fila)

    return render_template(
        "movimientos_unidad.html",
        u=fila, recomendado=recomendado, otros=otros,
        estado=estado, estado_desde_regla=desde_regla,
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
    es_desvio = paso != clave_recomendada

    registrar(fila, {
        "paso": paso,
        "recomendado": clave_recomendada,
        "es_desvio": es_desvio,
        "motivo": request.form.get("motivo") if es_desvio else None,
        "motivo_detalle": request.form.get("motivo_detalle") if es_desvio else None,
        "resultado_pdi": request.form.get("resultado_pdi") or None,
        "guia_ingreso": request.form.get("guia_ingreso") or None,
        "fecha": request.form.get("fecha") or None,
        # Firma el que esta logueado, no un nombre escrito a mano.
        "responsable": nombre_actual() or None,
    })

    return redirect(url_for("movimientos.unidad", id_unidad=id_unidad,
                            registrado=paso))
