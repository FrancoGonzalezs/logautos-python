"""
modulos/revision_contenedor.py -- Revision de Contenedor: por CONTENEDOR, con
varias unidades adentro.

Correccion de arquitectura
--------------------------
La primera version de este modulo era por unidad suelta: un formulario, una
unidad, un correo. Estaba mal. El proceso real del PHP son dos pantallas y
media, y la unidad de trabajo es el contenedor:

  1. `Pedido.php > revision_contenedor_proces()` crea el contenedor (nombre,
     encargado, nro_sello, tipo_transporte, guia_ingreso, suciedad,
     cantidad_unidades) y redirige a la pantalla 2 con el id nuevo.

  2. `Pedido.php > subida_foto_revision_contenedor_proces()` agrega UN VIN por
     submit -- observacion, nivel de dano, una foto y la validacion de
     modelo/color -- y vuelve a la misma pantalla para el siguiente.

  2b. En esa misma pantalla hay un segundo formulario que cierra el
      contenedor: marca estado CERRADO y dispara el correo de
      `Nota.php > envio_correo_danos_contenedor()` con todos sus VIN.

En la operacion real hay 6,2 unidades por contenedor de promedio y un maximo
de 46, asi que la pantalla 2 se recorre muchas veces seguidas. Por eso el VIN
NO se tipea: el PHP usa un input de 17 caracteres con un datalist de 1.706
unidades, y aca se reusa el buscador con escaner QR de la fase 1. Es el mismo
principio de todo el modulo -- que el movilizador escriba lo menos posible.

Dos desviaciones deliberadas del PHP, decididas con el dueno del sistema
-----------------------------------------------------------------------
1. DANOS ESTRUCTURADOS. El PHP guarda por VIN una `obs` de texto libre (con
   las piezas como autocompletado) mas un `nivel_dano`. Aca se cargan igual
   que en el Check List de Ingreso -- pieza, tipo y nivel de catalogo, N
   danos, foto por dano -- y de ahi se DERIVA el texto de `observacion` y el
   nivel del contenedor. Asi el dato queda estructurado sin perder el formato
   que produccion espera.

2. MODELO Y COLOR SE DEDUCEN. El PHP pregunta con radios y se niega a guardar
   sin respuesta. Aca los campos observados se dejan VACIOS cuando coinciden
   -- que es la enorme mayoria de los casos -- y solo si el movilizador
   escribe algo distinto se le pide confirmacion explicita. Menos tipeo en el
   caso normal, confirmacion justo donde importa: es lo unico que dispara el
   correo de diferencia.

Donde se guarda
---------------
En tablas propias (`contenedor_regla`, `revision_unidad_regla`,
`validacion_color_regla`) y NO en `contenedor`. Mismo criterio de siempre: la
replica es el patron de comparacion contra produccion y el importador la
dropea. `validacion_color_unidad` ademas ni siquiera esta en la replica -- el
importador no la trae --, asi que no habia con que trabajar.

Un contenedor de la replica se ADOPTA la primera vez que se lo toca: se copia
su encabezado a `contenedor_regla` y se sigue trabajando ahi. Es lo que
permite cumplir la regla de no escribirle a la replica sin perder el
contenedor que ya existia.
"""

import os
import sqlite3
from datetime import datetime

from flask import Blueprint, redirect, render_template, request, url_for

from core import DB_PATH, consultar, get_db
from modulos.acceso import id_actual, nombre_actual
from modulos.catalogos import normalizar
# Se reusan las piezas del check list a proposito: son el mismo dato con los
# mismos catalogos, y dos implementaciones se habrian desincronizado a la
# primera correccion.
from modulos.check_list import (_coincide, _filas_de_danos, _guardar_foto,
                                _leer_danos, niveles_de_dano, piezas,
                                tipos_de_dano, url_de_foto)
from modulos.movimientos import (_buscar, es_desvio, estado_fisico,
                                 recomendar, registrar)
from modulos.unidades import TABLA

bp = Blueprint("revision_contenedor", __name__)

PASO = "revision_contenedor"
MODULO_ORIGEN = "CONTENEDOR"

# Los valores exactos de los dos selects de `views/patio/revision_contenedor.php`.
# No son inventados: se leyeron del formulario real.
TIPOS_TRANSPORTE = ["CONTENEDOR", "RORO"]
NIVELES_SUCIEDAD = ["BAJO", "MEDIO", "ALTO"]

# Un contenedor esta cerrado SOLO si su estado dice CERRADO. Todo lo demas
# cuenta como abierto, y no es un detalle: el INSERT de
# `revision_contenedor_proces()` no setea `estado`, asi que un contenedor
# recien creado en el PHP queda con el campo VACIO, no en 'ABIERTO'. En la
# replica hay 2.215 CERRADO, 142 ABIERTO y 91 en blanco -- filtrar por
# estado='ABIERTO' dejaria afuera esos 91.
ESTADO_CERRADO = "CERRADO"


# ---------------------------------------------------------------------------
# Tablas propias
# ---------------------------------------------------------------------------

def _asegurar_tablas(db):
    """IF NOT EXISTS y al vuelo, igual que el resto de las tablas de REGLA."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS contenedor_regla (
          id INTEGER PRIMARY KEY,
          nombre_contenedor TEXT,
          encargado TEXT,
          nro_sello TEXT,
          tipo_transporte TEXT,
          guia_ingreso TEXT,
          suciedad TEXT,
          cantidad_unidades TEXT,
          fecha TEXT,
          fecha_completa_inicio TEXT,
          fecha_completa_fin TEXT,
          estado TEXT,
          -- Las cuatro listas paralelas separadas por ' | ', en el mismo
          -- formato que la tabla `contenedor` de produccion: es lo que hace
          -- que el push al sistema viejo sea un mapeo 1:1 y no una
          -- traduccion.
          vines TEXT,
          observacion TEXT,
          nivel_dano TEXT,
          link_fotos TEXT,
          contador INTEGER,
          ultimo_vin TEXT,
          -- De que fila de la replica se adopto, si venia de ahi.
          origen_replica_id INTEGER,
          usuario TEXT,
          creado_en TEXT
        )""")

    # Una fila por (contenedor, VIN): la evidencia estructurada, que es lo que
    # la tabla `contenedor` no puede guardar porque solo tiene listas planas.
    db.execute("""
        CREATE TABLE IF NOT EXISTS revision_unidad_regla (
          id INTEGER PRIMARY KEY,
          contenedor_id INTEGER,
          unidad_id INTEGER,
          movimiento_id INTEGER,
          vin TEXT,
          -- Los danos en el mismo formato del check list: tres campos
          -- paralelos separados por '-' y las fotos por ' | '.
          dano_piezas TEXT,
          dano_tipos TEXT,
          dano_niveles TEXT,
          dano_fotos TEXT,
          -- Derivados de los danos, para las listas del contenedor.
          observacion TEXT,
          nivel_dano TEXT,
          encargado TEXT,
          usuario TEXT,
          creado_en TEXT
        )""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS ix_revision_unidad_regla_cont "
        "ON revision_unidad_regla (contenedor_id)")

    # Espejo de `validacion_color_unidad`, con la MISMA clave unica: una
    # validacion por VIN por modulo y referencia. Esa unicidad no es un
    # detalle -- es lo que hace que a un VIN ya validado en este contenedor no
    # se le vuelva a preguntar.
    db.execute("""
        CREATE TABLE IF NOT EXISTS validacion_color_regla (
          id INTEGER PRIMARY KEY,
          vin TEXT NOT NULL,
          id_unidad INTEGER,
          modulo_origen TEXT NOT NULL,
          referencia_id INTEGER NOT NULL,
          modelo_informado TEXT,
          modelo_coincide INTEGER NOT NULL DEFAULT 1,
          modelo_observado TEXT,
          color_informado TEXT,
          coincide INTEGER NOT NULL DEFAULT 1,
          color_observado TEXT,
          usuario_id INTEGER,
          usuario_nombre TEXT,
          correo_enviado INTEGER NOT NULL DEFAULT 0,
          fecha_correo TEXT,
          fecha_registro TEXT
        )""")
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_validacion_color_regla "
        "ON validacion_color_regla (vin, modulo_origen, referencia_id)")


def _db():
    db = get_db()
    _asegurar_tablas(db)
    return db


# ---------------------------------------------------------------------------
# Contenedores
# ---------------------------------------------------------------------------

def contenedor(id_contenedor):
    _db().commit()
    return consultar("SELECT * FROM contenedor_regla WHERE id = ?",
                     (id_contenedor,), una=True)


def contenedor_abierto_de_vin(vin):
    """El contenedor ABIERTO al que pertenece este VIN, si hay alguno.

    Que sea abierto es la correccion de un bug real. Antes se buscaba
    cualquier contenedor con ese VIN y se caia siempre en el mismo pozo: una
    unidad que ya paso por el patio meses atras figura en el contenedor de
    AQUEL ingreso, que hace rato esta cerrado. La pantalla la mandaba ahi y la
    rebotaba con 'el contenedor ya esta cerrado', dejandola sin poder
    revisarse nunca.

    No es un caso de borde: 2.935 VIN de la replica aparecen en dos o mas
    contenedores, y de las 501 unidades vivas que ya figuran en alguno, 490
    los tienen TODOS cerrados. O sea que el bug bloqueaba practicamente a
    todas.

    Si no hay ninguno abierto se devuelve None y el flujo ofrece crear uno
    nuevo, que es lo correcto: un ingreso nuevo es un contenedor nuevo."""
    if not vin:
        return None
    patron = "%{}%".format(vin)
    _db().commit()
    propio = consultar(
        "SELECT * FROM contenedor_regla WHERE vines LIKE ? "
        "AND IFNULL(estado, '') <> ? ORDER BY id DESC LIMIT 1",
        (patron, ESTADO_CERRADO), una=True)
    if propio:
        return propio

    de_replica = consultar(
        "SELECT * FROM contenedor WHERE vines LIKE ? "
        "AND IFNULL(estado, '') <> ? ORDER BY id DESC LIMIT 1",
        (patron, ESTADO_CERRADO), una=True)
    if de_replica is None:
        return None
    return adoptar_de_replica(de_replica)


def contenedores_de_vin(vin):
    """TODOS los contenedores en que figura el VIN, abiertos y cerrados.

    Es lo que muestra la ficha de la unidad: ahi si interesa el historial
    completo, incluido el contenedor de un ingreso anterior."""
    if not vin:
        return []
    patron = "%{}%".format(vin)
    _db().commit()
    propios = consultar(
        "SELECT * FROM contenedor_regla WHERE vines LIKE ? ORDER BY id DESC",
        (patron,))
    adoptados = {c["origen_replica_id"] for c in propios if c["origen_replica_id"]}
    de_replica = [c for c in consultar(
        "SELECT * FROM contenedor WHERE vines LIKE ? ORDER BY id DESC", (patron,))
        if c["id"] not in adoptados]
    return list(propios) + de_replica


def adoptar_de_replica(fila):
    """Copia el encabezado de un contenedor de la replica a una fila propia.

    Idempotente por `origen_replica_id`: entrar dos veces al mismo contenedor
    no lo duplica. Se adopta en vez de escribirle a la replica porque la
    replica es el patron de comparacion contra produccion y el importador la
    dropea en cada reimportacion."""
    db = _db()
    ya = consultar("SELECT * FROM contenedor_regla WHERE origen_replica_id = ?",
                   (fila["id"],), una=True)
    if ya:
        return ya

    cur = db.execute("""
        INSERT INTO contenedor_regla
          (nombre_contenedor, encargado, nro_sello, tipo_transporte, guia_ingreso,
           suciedad, cantidad_unidades, fecha, fecha_completa_inicio,
           fecha_completa_fin, estado, vines, observacion, nivel_dano, link_fotos,
           contador, ultimo_vin, origen_replica_id, usuario, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        fila["nombre_contenedor"], fila["encargado"], fila["nro_sello"],
        fila["tipo_transporte"], fila["guia_ingreso"], fila["suciedad"],
        fila["cantidad_unidades"], fila["fecha"], fila["fecha_completa_inicio"],
        fila["fecha_completa_fin"], fila["estado"] or "ABIERTO", fila["vines"],
        fila["observacion"], fila["nivel_dano"], fila["link_fotos"],
        fila["contador"] or 0, fila["ultimo_vin"], fila["id"],
        id_actual(), datetime.now().isoformat(timespec="seconds")))
    db.commit()
    return contenedor(cur.lastrowid)


def crear_contenedor(datos):
    db = _db()
    ahora = datetime.now()
    cur = db.execute("""
        INSERT INTO contenedor_regla
          (nombre_contenedor, encargado, nro_sello, tipo_transporte, guia_ingreso,
           suciedad, cantidad_unidades, fecha, fecha_completa_inicio, estado,
           contador, usuario, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        datos["nombre_contenedor"], datos["encargado"], datos["nro_sello"],
        datos["tipo_transporte"], datos["guia_ingreso"], datos["suciedad"],
        datos["cantidad_unidades"], ahora.date().isoformat(),
        ahora.isoformat(timespec="seconds"), "ABIERTO", 0,
        id_actual(), ahora.isoformat(timespec="seconds")))
    db.commit()
    return cur.lastrowid


def unidades_del_contenedor(id_contenedor):
    """Las unidades ya cargadas, con su fila de la replica para poder
    mostrarlas con marca, modelo y color."""
    filas = consultar(
        "SELECT * FROM revision_unidad_regla WHERE contenedor_id = ? ORDER BY id",
        (id_contenedor,))
    salida = []
    for f in filas:
        unidad = consultar('SELECT * FROM "{}" WHERE id = ?'.format(TABLA),
                           (f["unidad_id"],), una=True)
        salida.append({"revision": f, "unidad": unidad,
                       "fotos": [url_de_foto(r) for r in
                                 (f["dano_fotos"] or "").split(" | ") if r]})
    return salida


# ---------------------------------------------------------------------------
# Derivar la observacion y el nivel desde los danos estructurados
# ---------------------------------------------------------------------------

def _orden_de_niveles():
    """La escala de severidad, en el orden del catalogo (por id: LEVE, MEDIO,
    GRAVE, PRESUPUESTO). Se lee de la tabla y no se escribe a mano para que un
    nivel nuevo entre solo."""
    return [n["nombre"] for n in niveles_de_dano()]


def derivar_observacion(danos):
    """El texto que va a la lista `observacion` del contenedor.

    El PHP guarda ahi una observacion de texto libre en mayusculas. Como aca
    los danos son estructurados, el texto se COMPONE a partir de ellos: se
    conserva el formato que produccion espera y ademas queda el detalle."""
    piezas_ = (danos.get("observacion") or "").split("-")
    tipos = (danos.get("requerimiento") or "").split("-")
    niveles = (danos.get("gravedad") or "").split("-")
    partes = []
    for i, pieza in enumerate(piezas_):
        if not pieza:
            continue
        tipo = tipos[i] if i < len(tipos) else ""
        nivel = niveles[i] if i < len(niveles) else ""
        texto = " ".join(p for p in (pieza, tipo) if p)
        if nivel:
            texto += " ({})".format(nivel)
        partes.append(texto)
    if not partes:
        return "SIN DAÑOS"
    return ", ".join(partes).upper()


def derivar_nivel(danos):
    """El nivel del VIN para la lista `nivel_dano`: el PEOR de sus danos.

    El PHP guarda un solo nivel por VIN, asi que hay que elegir uno. El peor
    es el unico que no miente: si una unidad tiene una raya leve y un golpe
    grave, decir 'LEVE' porque fue el primero cargado seria esconder el golpe."""
    presentes = [n for n in (danos.get("gravedad") or "").split("-") if n]
    if not presentes:
        return ""
    escala = _orden_de_niveles()
    def rango(nombre):
        try:
            return escala.index(nombre)
        except ValueError:
            return -1
    return max(presentes, key=rango)


def _agregar_a_lista(actual, valor):
    """Suma un elemento a una de las listas paralelas del contenedor."""
    valor = valor or ""
    return valor if not actual else "{} | {}".format(actual, valor)


# ---------------------------------------------------------------------------
# Validacion de modelo y color
# ---------------------------------------------------------------------------

def validacion_de(vin, id_contenedor):
    _db().commit()
    return consultar(
        "SELECT * FROM validacion_color_regla WHERE vin = ? AND modulo_origen = ? "
        "AND referencia_id = ?", (vin, MODULO_ORIGEN, id_contenedor), una=True)


def guardar_validacion(unidad, id_contenedor, modelo_obs, color_obs):
    """Guarda la validacion si no existia. Devuelve (fila, hay_diferencia).

    La coincidencia se DEDUCE: el campo observado vacio significa 'coincide',
    que es lo normal y no cuesta nada escribir. Solo cuando el movilizador
    escribe algo distinto queda registrada la diferencia."""
    existente = validacion_de(unidad["vin"], id_contenedor)
    if existente:
        return existente, False

    modelo_informado = normalizar(unidad["modelo"])
    color_informado = normalizar(unidad["color"])
    modelo_coincide = True if not modelo_obs else bool(
        _coincide(unidad["modelo"], modelo_obs))
    color_coincide = True if not color_obs else bool(
        _coincide(unidad["color"], color_obs))

    db = _db()
    db.execute("""
        INSERT INTO validacion_color_regla
          (vin, id_unidad, modulo_origen, referencia_id, modelo_informado,
           modelo_coincide, modelo_observado, color_informado, coincide,
           color_observado, usuario_id, usuario_nombre, correo_enviado,
           fecha_registro)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        unidad["vin"], unidad["id"], MODULO_ORIGEN, id_contenedor,
        modelo_informado, 1 if modelo_coincide else 0,
        None if modelo_coincide else normalizar(modelo_obs),
        color_informado, 1 if color_coincide else 0,
        None if color_coincide else normalizar(color_obs),
        id_actual(), nombre_actual(), 0,
        datetime.now().isoformat(timespec="seconds")))
    db.commit()
    fila = validacion_de(unidad["vin"], id_contenedor)
    return fila, not (modelo_coincide and color_coincide)


# ---------------------------------------------------------------------------
# Correos
# ---------------------------------------------------------------------------
#
# Son DOS, y distintos:
#
#   - el de diferencia de unidad, que sale al detectar que el modelo o el
#     color fisico no coinciden con el packing list;
#   - el de cierre del contenedor, con la tabla de todos sus VIN.
#
# El COMO se manda vive en `modulos/correo.py` desde que aparecio el segundo
# consumidor (el aviso de conflicto del push). Aca queda el QUE se manda, que
# es lo propio de este modulo: los dos cuerpos y sus destinatarios.
#
# Los nombres privados se conservan como alias para no tocar el resto del
# archivo -- lo que se movio es la implementacion, no el contrato.
from modulos.correo import REMITENTE, en_segundo_plano as _en_segundo_plano  # noqa: E402,F401
from modulos.correo import destinatarios as _destinatarios                    # noqa: E402
from modulos.correo import log as _log                                        # noqa: E402
from modulos.correo import mandar as _mandar                                  # noqa: E402


def enviar_correo_diferencia(unidad, validacion, cont):
    """Arma el correo de diferencia y lo manda EN SEGUNDO PLANO.

    El armado va acá, dentro del request, porque necesita leer la unidad y la
    validacion. Al hilo solo se le pasan valores ya resueltos: `get_db()`
    cuelga de `g`, que es del request, asi que un hilo que lo llamara se
    quedaria sin conexion."""
    destinatarios = _destinatarios("VALIDACION_DIFERENCIA_DESTINATARIOS")
    filas = ""
    if not validacion["modelo_coincide"]:
        filas += "<tr><td>MODELO</td><td>{}</td><td>{}</td></tr>".format(
            validacion["modelo_informado"] or "", validacion["modelo_observado"] or "")
    if not validacion["coincide"]:
        filas += "<tr><td>COLOR</td><td>{}</td><td>{}</td></tr>".format(
            validacion["color_informado"] or "", validacion["color_observado"] or "")
    html = (
        "<h3><b>Diferencia detectada en revisión de contenedor</b></h3>"
        "<p>VIN: <b>{vin}</b> — Contenedor: <b>{cont}</b></p>"
        '<table border="1" cellpadding="5" style="border-collapse:collapse">'
        "<tr><th>Campo</th><th>Packing List</th><th>Observado</th></tr>{filas}</table>"
        "<p>Revisado por {quien}.</p>"
        "<p>Este mail fue enviado automaticamente por sistema REGLA</p>"
    ).format(vin=unidad["vin"] or "", cont=cont["nombre_contenedor"] or "",
             filas=filas, quien=validacion["usuario_nombre"] or "")
    _en_segundo_plano(
        _mandar_diferencia,
        destinatarios,
        "Diferencia de unidad VIN: {}".format(unidad["vin"] or ""),
        "Diferencia detectada en el VIN {}.".format(unidad["vin"] or ""),
        html,
        validacion["id"])
    return "encolado", "se manda en segundo plano"


def _mandar_diferencia(destinatarios, asunto, texto, html, id_validacion):
    """Lo que corre en el hilo: mandar y, si salio, marcarlo.

    La conexion a SQLite se abre acá y no se reusa la del request: las
    conexiones de sqlite3 no se comparten entre hilos, y la del request ya no
    existe cuando esto corre."""
    estado, _detalle = _mandar(destinatarios, asunto, texto, html)
    if estado != "enviado":
        return
    try:
        db = sqlite3.connect(DB_PATH, timeout=10)
        db.execute("UPDATE validacion_color_regla SET correo_enviado = 1, "
                   "fecha_correo = ? WHERE id = ?",
                   (datetime.now().isoformat(timespec="seconds"), id_validacion))
        db.commit()
        db.close()
    except Exception as e:                       # noqa: BLE001
        _log("aviso", asunto, "se envio pero no se pudo marcar: {}".format(e))


def enviar_correo_cierre(cont, unidades):
    """El informe de cierre, con el mismo formato del PHP: encabezado con la
    guia y una tabla VIN / OBSERVACION con todas las unidades."""
    destinatarios = _destinatarios("REVISION_CONTENEDOR_DESTINATARIOS")
    filas = "".join(
        "<tr><td>{}</td><td>{}</td></tr>".format(
            (u["revision"]["vin"] or ""), (u["revision"]["observacion"] or ""))
        for u in unidades)
    html = (
        "<style>.tb {{ border-collapse: collapse; width:600px; }}"
        ".tb th, .tb td {{ padding: 5px; border: solid 1px #777; }}"
        ".tb th {{ background-color: lightblue; }}</style>"
        "<h3><b>Informe Check List Revision Transporte</b></h3>"
        "<h3><b>Guia: {guia}</b></h3>"
        '<table class="tb"><tr><th>VIN</th><th>OBSERVACION</th></tr>{filas}</table>'
        "<br><br><p>Este mail fue enviado automaticamente por sistema REGLA</p>"
    ).format(guia=cont["guia_ingreso"] or "sin guía", filas=filas)

    adjuntos = []
    from modulos.check_list import CARPETA_FOTOS
    for u in unidades:
        for rel in (u["revision"]["dano_fotos"] or "").split(" | "):
            if rel:
                adjuntos.append(os.path.join(CARPETA_FOTOS, rel.replace("/", os.sep)))

    # Diferido: cerrar un contenedor tiene que responderle al movilizador de
    # inmediato. El informe es para el cliente, no para el que aprieta el
    # boton, asi que puede tardar lo que tarde sin bloquear la pantalla.
    _en_segundo_plano(
        _mandar, destinatarios,
        "Check List Revision Transporte {}".format(cont["guia_ingreso"] or ""),
        "Informe de revisión de transporte con {} unidades.".format(len(unidades)),
        html, adjuntos)
    return "encolado", "se manda en segundo plano"


# ---------------------------------------------------------------------------
# Vistas
# ---------------------------------------------------------------------------

def _unidad(id_unidad):
    return consultar('SELECT * FROM "{}" WHERE id = ?'.format(TABLA),
                     (id_unidad,), una=True)


def _texto(campo):
    return (request.form.get(campo) or "").strip()


@bp.route("/movimientos/<int:id_unidad>/revision-contenedor")
def entrada(id_unidad):
    """El punto de entrada desde la tarjeta del paso.

    Primero se busca si el VIN YA pertenece a un contenedor: si pertenece, se
    va derecho a cargarle la evidencia ahi, que es lo que quiere el
    movilizador. Recien si no pertenece a ninguno se ofrece crear uno."""
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404

    cont = contenedor_abierto_de_vin(unidad["vin"])
    if cont is not None:
        return redirect(url_for("revision_contenedor.evidencia",
                                id_contenedor=cont["id"], id_unidad=id_unidad))

    return render_template(
        "contenedor_crear.html", u=unidad, encargado=nombre_actual(),
        tipos_transporte=TIPOS_TRANSPORTE, niveles_suciedad=NIVELES_SUCIEDAD,
        motonaves=motonaves_conocidas(),
        hoy=datetime.now().date().isoformat(), errores=[], v={})


@bp.route("/movimientos/<int:id_unidad>/revision-contenedor", methods=["POST"])
def crear(id_unidad):
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404

    datos = {
        "nombre_contenedor": _texto("nombre_contenedor").upper(),
        # De la sesion y nunca del formulario, igual que en todo el modulo.
        "encargado": nombre_actual(),
        "nro_sello": _texto("nro_sello"),
        "tipo_transporte": _texto("tipo_transporte"),
        "guia_ingreso": _texto("guia_ingreso"),
        "suciedad": _texto("suciedad"),
        "cantidad_unidades": _texto("cantidad_unidades"),
    }
    errores = []
    if not datos["nombre_contenedor"]:
        errores.append("Falta el nombre del contenedor o la motonave.")
    if not datos["encargado"]:
        errores.append("La sesión no tiene un nombre con que firmar. Volvé a entrar.")
    if errores:
        return render_template(
            "contenedor_crear.html", u=unidad, encargado=nombre_actual(),
            tipos_transporte=TIPOS_TRANSPORTE, niveles_suciedad=NIVELES_SUCIEDAD,
            motonaves=motonaves_conocidas(),
            hoy=datetime.now().date().isoformat(),
            errores=errores, v=request.form), 400

    nuevo = crear_contenedor(datos)
    return redirect(url_for("revision_contenedor.evidencia",
                            id_contenedor=nuevo, id_unidad=id_unidad))


@bp.route("/contenedores/<int:id_contenedor>")
def detalle(id_contenedor):
    cont = contenedor(id_contenedor)
    if cont is None:
        return render_template("no_encontrado.html", que="contenedor",
                               id=id_contenedor), 404
    texto = request.args.get("q", "").strip()

    # Igual que en Movimientos: la busqueda en vivo pide solo sus resultados.
    if request.args.get("fragmento") == "1":
        return render_template("_resultados_contenedor.html", c=cont, texto=texto,
                               resultados=_buscar(texto) if texto else [])

    return render_template(
        "contenedor_detalle.html", c=cont,
        unidades=unidades_del_contenedor(id_contenedor),
        texto=texto, resultados=_buscar(texto) if texto else [],
        cerrado=(cont["estado"] == "CERRADO"),
        correo=request.args.get("correo"))


@bp.route("/contenedores/<int:id_contenedor>/unidad/<int:id_unidad>")
def evidencia(id_contenedor, id_unidad):
    cont = contenedor(id_contenedor)
    unidad = _unidad(id_unidad)
    if cont is None or unidad is None:
        return render_template("no_encontrado.html", que="contenedor",
                               id=id_contenedor), 404
    return _pintar_evidencia(cont, unidad)


def diferencias_de(unidad, modelo_obs, color_obs):
    """Que campos observados NO coinciden con el packing list.

    El campo vacio significa 'coincide' -- es la deduccion que evita que el
    movilizador tenga que escribir nada en el caso normal."""
    fuera = []
    if modelo_obs and not _coincide(unidad["modelo"], modelo_obs):
        fuera.append(("modelo", normalizar(unidad["modelo"]), modelo_obs.upper()))
    if color_obs and not _coincide(unidad["color"], color_obs):
        fuera.append(("color", normalizar(unidad["color"]), color_obs.upper()))
    return fuera


def _pintar_evidencia(cont, unidad, errores=None, codigo=200):
    # Las diferencias se RECALCULAN acá, del formulario que llego, en vez de
    # recibirlas del que llama. Esto arregla un bucle sin salida que se comio
    # una prueba entera desde el celular:
    #
    #   el POST con foto se rechazaba por la diferencia de color -> el
    #   re-render vaciaba el input de archivo -> el reenvio sin foto se
    #   rechazaba por "falta la foto", y ESE camino repintaba sin las
    #   diferencias, asi que el checkbox de confirmacion desaparecia de la
    #   pantalla mientras `v=request.form` seguia reponiendo el color
    #   observado. El siguiente envio volvia a detectar la diferencia, otra vez
    #   sin confirmar, y de ahi no se salia salvo borrando el campo de color.
    #
    # Calculandolas acá, el bloque de confirmacion sobrevive a CUALQUIER
    # motivo de re-render, que es la unica forma de que no se pueda volver a
    # desincronizar del campo que las dispara.
    es_post = request.method == "POST"
    v = request.form if es_post else {}
    pagina = render_template(
        "contenedor_evidencia.html", c=cont, u=unidad,
        piezas=piezas(), tipos=tipos_de_dano(), niveles=niveles_de_dano(),
        filas_danos=_filas_de_danos() if es_post else [
            {"i": 0, "pieza": "", "tipo": "", "nivel": ""}],
        encargado=nombre_actual(),
        validacion=validacion_de(unidad["vin"], cont["id"]),
        diferencias=diferencias_de(unidad, (v.get("modelo_observado") or "").strip(),
                                   (v.get("color_observado") or "").strip()),
        errores=errores or [], v=v)
    return (pagina, codigo) if codigo != 200 else pagina


@bp.route("/contenedores/<int:id_contenedor>/unidad/<int:id_unidad>",
          methods=["POST"])
def guardar_evidencia(id_contenedor, id_unidad):
    cont = contenedor(id_contenedor)
    unidad = _unidad(id_unidad)
    if cont is None or unidad is None:
        return render_template("no_encontrado.html", que="contenedor",
                               id=id_contenedor), 404

    if cont["estado"] == "CERRADO":
        return _pintar_evidencia(
            cont, unidad, ["El contenedor ya está cerrado: no admite más unidades."],
            codigo=400)

    ya_estaba = (unidad["vin"] or "") in (cont["vines"] or "")
    if ya_estaba:
        return _pintar_evidencia(
            cont, unidad, ["Esta unidad ya fue cargada en este contenedor."],
            codigo=400)

    modelo_obs = _texto("modelo_observado")
    color_obs = _texto("color_observado")

    # Deduccion: el campo vacio significa 'coincide'. Solo si el movilizador
    # escribio algo distinto se le pide confirmar, y solo entonces sale el
    # correo de diferencia. En el navegador esto ya se resolvio sin recargar
    # (ver el JS de la pantalla); acá se revalida igual, porque el cliente
    # puede no tener JS y porque nada que llegue en un POST se da por bueno.
    diferencias = diferencias_de(unidad, modelo_obs, color_obs)
    if diferencias and not request.form.get("confirmar_diferencia"):
        return _pintar_evidencia(cont, unidad, codigo=400, errores=[
            "Hay una diferencia con el packing list: confirmala para poder "
            "guardar."])

    danos = _leer_danos(unidad["vin"])
    if danos["faltan_fotos"]:
        return _pintar_evidencia(cont, unidad, [
            "Cada daño necesita su foto. Sin foto quedaron: {}.".format(
                ", ".join(danos["faltan_fotos"]))], codigo=400)

    observacion = derivar_observacion(danos)
    nivel = derivar_nivel(danos)

    validacion, hubo_diferencia = guardar_validacion(
        unidad, cont["id"], modelo_obs, color_obs)

    # El movimiento se registra igual que el check list: origen y destino son
    # el MISMO estado, porque la revision no mueve la unidad en la maquina de
    # estados -- no escribe en Tracking.
    recomendado = recomendar(unidad)
    clave_recomendada = recomendado["clave"] if recomendado else None
    estado_actual = estado_fisico(unidad)
    movimiento_id = registrar(unidad, {
        "paso": PASO,
        "recomendado": clave_recomendada,
        "es_desvio": es_desvio(clave_recomendada, PASO),
        "estado_desde": estado_actual,
        "estado_hacia": estado_actual,
        "motivo": _texto("motivo") if es_desvio(clave_recomendada, PASO) else None,
        "motivo_detalle": None,
        "resultado_pdi": None,
        "guia_ingreso": cont["guia_ingreso"],
        "fecha": datetime.now().date().isoformat(),
        "responsable": nombre_actual(),
    })

    db = _db()
    db.execute("""
        INSERT INTO revision_unidad_regla
          (contenedor_id, unidad_id, movimiento_id, vin, dano_piezas, dano_tipos,
           dano_niveles, dano_fotos, observacion, nivel_dano, encargado, usuario,
           creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        cont["id"], unidad["id"], movimiento_id, unidad["vin"],
        danos.get("observacion"), danos.get("requerimiento"),
        danos.get("gravedad"), danos.get("link"), observacion, nivel,
        nombre_actual(), id_actual(),
        datetime.now().isoformat(timespec="seconds")))

    # Y se suman las cuatro listas paralelas del contenedor, en el formato de
    # produccion. El nombre NO se pisa con la motonave como hace el PHP: ahi
    # el nombre cargado en la pantalla 1 se perdia en el primer VIN.
    db.execute("""
        UPDATE contenedor_regla
           SET vines = ?, observacion = ?, nivel_dano = ?, link_fotos = ?,
               contador = ?, ultimo_vin = ?
         WHERE id = ?""", (
        _agregar_a_lista(cont["vines"], unidad["vin"]),
        _agregar_a_lista(cont["observacion"], observacion),
        _agregar_a_lista(cont["nivel_dano"], nivel),
        _agregar_a_lista(cont["link_fotos"], danos.get("link")),
        (cont["contador"] or 0) + 1, unidad["vin"], cont["id"]))
    db.commit()

    if hubo_diferencia and validacion is not None:
        enviar_correo_diferencia(unidad, validacion, cont)

    # Cierre automatico al completar la cantidad declarada. En el sistema
    # viejo el cierre es un boton y nada mas, y el resultado esta a la vista:
    # 233 contenedores de 2.448 nunca se cerraron, asi que su informe no salio
    # nunca. Si el contenedor dijo que traia N unidades y ya se cargaron N, no
    # hay nada mas que esperar.
    #
    # El cierre manual sigue existiendo para los incompletos: un contenedor
    # puede traer menos unidades de las declaradas, o quedar una para otro dia.
    cerrado_solo = False
    declaradas = _entero(cont["cantidad_unidades"])
    if declaradas and (cont["contador"] or 0) + 1 >= declaradas:
        cerrar_contenedor(cont["id"])
        cerrado_solo = True

    return redirect(url_for("revision_contenedor.detalle",
                            id_contenedor=cont["id"], agregada=unidad["vin"],
                            **({"correo": "auto"} if cerrado_solo else {})))


def _entero(valor):
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def cerrar_contenedor(id_contenedor):
    """Cierra el contenedor y manda el informe. Devuelve el estado del correo.

    OJO CON EL NOMBRE: en el PHP este boton se llama 'Finalizar Check List',
    igual que el del Check List de Ingreso, y son dos cosas distintas -- este
    cierra el CONTENEDOR y manda el informe de todas sus unidades; aquel
    guarda el check list de UNA unidad. Aca se llama cerrar/finalizar
    contenedor a proposito, para que nadie los confunda leyendo el codigo."""
    cont = contenedor(id_contenedor)
    if cont is None or cont["estado"] == ESTADO_CERRADO:
        return "ya_cerrado"

    unidades = unidades_del_contenedor(id_contenedor)

    # PRIMERO se cierra, DESPUES se manda el informe. El orden estaba al reves
    # y era el problema de fondo: el correo tarda -- hoy ni siquiera conecta,
    # porque Railway bloquea la salida SMTP --, asi que cualquier cosa que
    # fallara ahi se llevaba puesto el cierre, que ya estaba decidido y no
    # dependia del correo. El cierre es el dato; el informe es una
    # notificacion.
    db = _db()
    db.execute("UPDATE contenedor_regla SET estado = ?, fecha_completa_fin = ? "
               "WHERE id = ?",
               (ESTADO_CERRADO, datetime.now().isoformat(timespec="seconds"),
                id_contenedor))
    db.commit()

    # `_mandar` ya atrapa lo suyo, pero el armado del mensaje queda afuera de
    # ese try: leer los adjuntos, componer el HTML. Un except propio acá cierra
    # el hueco -- y sobre todo garantiza que este return no pueda tumbar el
    # cierre que ya quedo escrito arriba.
    try:
        estado, _detalle = enviar_correo_cierre(cont, unidades)
        return estado
    except Exception:                            # noqa: BLE001 -- ver arriba
        return "error"


@bp.route("/contenedores/<int:id_contenedor>/cerrar", methods=["POST"])
def cerrar(id_contenedor):
    """Cierre MANUAL, para el contenedor que quedo incompleto.

    El completo se cierra solo al cargar la ultima unidad; este boton es para
    cuando trajo menos de las declaradas o queda una para otro dia."""
    if contenedor(id_contenedor) is None:
        return render_template("no_encontrado.html", que="contenedor",
                               id=id_contenedor), 404
    estado = cerrar_contenedor(id_contenedor)
    return redirect(url_for("revision_contenedor.detalle",
                            id_contenedor=id_contenedor, correo=estado))


def resumen_para_ficha(vin):
    """Los contenedores de este VIN, listos para la ficha de la unidad.

    De cada uno se saca la fila de ESTE VIN, no el resumen del contenedor
    entero: `vines`, `observacion`, `nivel_dano` y `link_fotos` son cuatro
    listas paralelas indexadas por posicion, asi que hay que ubicar en que
    posicion cayo el VIN y leer esa misma posicion en las otras tres.

    Es justamente el formato que el sistema viejo desalinea cuando a un dano
    le falta la foto; por eso cada lista se lee con su propio largo y no se
    asume que las cuatro midan igual."""
    if not vin:
        return []

    salida = []
    for fila in contenedores_de_vin(vin):
        partes = [v.strip() for v in (fila["vines"] or "").split(" | ")]
        try:
            i = partes.index(vin)
        except ValueError:
            i = None

        def en(campo, sep=" | "):
            if i is None:
                return None
            lista = [x.strip() for x in (fila[campo] or "").split(sep)]
            return lista[i] if i < len(lista) else None

        fotos = []
        # Las fotos propias salen de la fila estructurada, que es la unica que
        # sabe cuantas fotos tiene realmente esta unidad.
        propio = "origen_replica_id" in fila.keys()
        if propio:
            rev = consultar(
                "SELECT dano_fotos FROM revision_unidad_regla "
                "WHERE contenedor_id = ? AND vin = ?", (fila["id"], vin), una=True)
            if rev:
                fotos = [url_de_foto(r) for r in
                         (rev["dano_fotos"] or "").split(" | ") if r]

        salida.append({
            "fila": fila,
            "propio": propio,
            "observacion": en("observacion"),
            "nivel": en("nivel_dano"),
            "fotos": fotos,
        })
    return salida


def motonaves_conocidas():
    """Las motonaves que ya existen, para sugerirlas sin obligar a tipear.

    El PHP tiene un <select> con DOS motonaves escritas a mano en la vista,
    contra 58 distintas que hay en el dato: cada barco nuevo obliga a editar
    el HTML, y por eso la lista quedo vieja. Acá se derivan de la tabla -- el
    mismo criterio que `catalogos.py` ya aplica -- y como es un datalist y no
    un select, una motonave nueva se escribe y listo."""
    de_replica = consultar(
        "SELECT DISTINCT nombre_contenedor AS n FROM contenedor "
        "WHERE nombre_contenedor IS NOT NULL AND TRIM(nombre_contenedor) <> ''")
    propias = consultar(
        "SELECT DISTINCT nombre_contenedor AS n FROM contenedor_regla "
        "WHERE nombre_contenedor IS NOT NULL AND TRIM(nombre_contenedor) <> ''")
    return sorted({f["n"].strip() for f in list(de_replica) + list(propias)})
