"""
modulos/inspeccion_despacho.py -- Inspeccion de Despacho, dentro de
Movimientos.

Que es
------
El registro que se hace en ZONA DE DESPACHO, antes de que la unidad salga: la
guia, el destino, el estado con que se entrega (estanque, kilometraje, llaves)
y las fotos. Es lo que despues viaja adjunto en el correo de Despacho VIN --
`inicio_proces()` levanta los nueve archivos de `inspeccion_despacho` y los
manda con el informe.

Las dos pantallas del PHP
-------------------------
1. `Nota.php:11050 inspeccion_despacho()` crea la fila con los datos de la
   entrega y UNA foto general (campo `unidad` -> `link_unidad`).

2. `Nota.php:17296 subida_foto_inspeccion_despacho($id)` y su `_proces()`
   (17319) agregan UNA foto por envio a `archivo1`..`archivo9`, eligiendo el
   slot con el `contador` de la fila. El tope es duro: no hay `archivo10`.

Es el mismo patron de Revision de Contenedor -- crear y despues ir sumando
evidencia -- y por eso se resuelve igual: una pantalla de alta y otra de
carga, sin que el operario salga de Movimientos.

Tres cosas que se replican y una que no
---------------------------------------
SE REPLICA el rechazo de unidades despachadas. El PHP lo dice con todas las
letras: "UNIDAD DESPACHADA, NO ES POSIBLE HACER CHECK LIST DE UNIDADES
DESPACHADAS". Tiene sentido: la inspeccion documenta como sale la unidad, y
una vez que salio ya no hay nada que documentar.

SE REPLICA el tope de nueve fotos, porque la tabla tiene nueve columnas y no
mas.

NO SE SEPARA POR CLIENTE. Es el mismo formulario para CIDEF y CARFLEX: la
unica rama por cliente del original esta COMENTADA (`if($cliente ==
'CARFLEX')`, que movia la unidad a PATIO 2 / EN ESPERA CC ZD). Lo unico que
difiere entre ellos es cuantas fotos sube cada uno -- CIDEF 4,5 de promedio
sobre 11.749 inspecciones, CARFLEX 6,0 sobre 4.590 --, y eso es
comportamiento del operario, no una regla del formulario.

NO SE PIDEN DAÑOS en la pantalla de fotos. La vista del PHP carga los
catalogos de piezas, tipo de daño y nivel, pero el `_proces()` tiene
`$req1`, `$pz1`, `$dano1` y `$cantidad` TODOS comentados y el UPDATE guarda
solo `link_unidad`, el slot de archivo y el contador. Cargar catalogos que no
se guardan seria copiar el adorno sin la funcion.

No mueve el estado
------------------
La inspeccion es un hito de la unidad, no un arco de la maquina de estados: la
unidad entra y sale de ella en ZONA DE DESPACHO. Por eso el movimiento se
registra con el mismo estado de origen y destino, igual que la revision de
contenedor.
"""

import os
from datetime import datetime

from flask import Blueprint, redirect, render_template, request, url_for

from core import consultar, get_db
from modulos.acceso import id_actual, nombre_actual
# Se reusa el guardado de fotos del check list: deja todas las fotos de la
# unidad en la misma carpeta y evita una segunda implementacion del manejo de
# archivos subidos que pueda divergir de la primera.
from modulos.check_list import _guardar_foto, url_de_foto
from modulos.movimientos import es_desvio, estado_fisico, recomendar, registrar
from modulos.unidades import TABLA

bp = Blueprint("inspeccion_despacho", __name__)

PASO = "inspeccion_despacho"

# NUEVE ES UN LIMITE DEL CABLE, NO DEL MODELO.
#
# `inspeccion_despacho` del legado tiene `archivo1..archivo9`, y su `_proces()`
# elige el slot con un `elseif($cont == N)` que llega hasta 9. Una decima foto
# no entra en ningun slot: cae en `link_unidad` --que acumula sin tope-- y se
# vuelve INVISIBLE para el PDF del despacho, que lee los nueve campos.
#
# REGLA no hereda ese tope en su modelo. Las fotos viven en su propia tabla,
# una fila por foto, sin limite; el aplanado a los nueve slots pasa RECIEN al
# empujar, y `aplanar_para_push` devuelve explicitamente cuales quedaron
# afuera. Un tope de almacenamiento y un tope de transporte son dos cosas, y
# mezclarlos hace que el dato se pierda antes de que nadie pueda decidir que
# hacer con el.
#
# En 16.365 filas del legado el contador nunca paso de 9, asi que hoy el
# sobrante no ocurre. Que no ocurra no es razon para no poder verlo.
ARCHIVOS_EN_EL_LEGADO = 9

# Nombre viejo, por si quedo alguna referencia.
MAX_ARCHIVOS = ARCHIVOS_EN_EL_LEGADO


def _asegurar_tabla(db):
    """IF NOT EXISTS y al vuelo, igual que el resto de las tablas de REGLA."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS inspeccion_despacho_regla (
          id INTEGER PRIMARY KEY,
          unidad_id INTEGER,
          movimiento_id INTEGER,
          vin TEXT,
          patente TEXT,
          guia_despacho TEXT,
          cliente TEXT,
          destino TEXT,
          fecha_despacho TEXT,
          marca TEXT,
          modelo TEXT,
          color TEXT,
          encargado TEXT,
          estanque TEXT,
          kilometraje TEXT,
          llaves TEXT,
          -- Los mismos nombres que `inspeccion_despacho` de produccion, para
          -- que el push sea un mapeo 1:1 y no una traduccion.
          link_unidad TEXT,
          archivo1 TEXT, archivo2 TEXT, archivo3 TEXT,
          archivo4 TEXT, archivo5 TEXT, archivo6 TEXT,
          archivo7 TEXT, archivo8 TEXT, archivo9 TEXT,
          contador INTEGER,
          fecha_entrega TEXT,
          fecha_completa TEXT,
          usuario TEXT,
          creado_en TEXT
        )""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS ix_inspeccion_despacho_regla_vin "
        "ON inspeccion_despacho_regla (vin)")

    # LAS FOTOS, una fila por foto y SIN TOPE.
    #
    # `orden` es el numero que ve el operario y el que decide a que `archivoN`
    # va al empujar. Se guarda explicito en vez de derivarlo del `id` porque el
    # id es global a la tabla y el orden es por inspeccion.
    #
    # `publicada` guarda la URL absoluta con token que se le mando al legado.
    # Se guarda para poder responder "que URL tiene esa foto del otro lado" sin
    # volver a mirar la tabla de publicaciones.
    db.execute("""
        CREATE TABLE IF NOT EXISTS inspeccion_despacho_fotos_regla (
          id INTEGER PRIMARY KEY,
          inspeccion_id INTEGER NOT NULL,
          orden INTEGER NOT NULL,
          ruta TEXT NOT NULL,        -- relativa a DATA_DIR
          publicada TEXT,            -- la URL absoluta que viajo al legado
          creado_en TEXT
        )""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS ix_insp_fotos_inspeccion "
        "ON inspeccion_despacho_fotos_regla (inspeccion_id, orden)")

    # `legado_id` y `enviado_en`, por ALTER: la tabla ya existe en Railway y
    # `CREATE TABLE IF NOT EXISTS` no cambia el esquema de una tabla creada.
    columnas = [c[1] for c in
                db.execute("PRAGMA table_info(inspeccion_despacho_regla)")]
    if "legado_id" not in columnas:
        db.execute("ALTER TABLE inspeccion_despacho_regla "
                   "ADD COLUMN legado_id INTEGER")
    if "enviado_en" not in columnas:
        db.execute("ALTER TABLE inspeccion_despacho_regla "
                   "ADD COLUMN enviado_en TEXT")


def _db():
    db = get_db()
    _asegurar_tabla(db)
    return db


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

def inspeccion(id_inspeccion):
    _db().commit()
    return consultar("SELECT * FROM inspeccion_despacho_regla WHERE id = ?",
                     (id_inspeccion,), una=True)


def inspeccion_de_vin(vin):
    """La inspeccion propia mas reciente de este VIN, si hay alguna."""
    if not vin:
        return None
    _db().commit()
    return consultar(
        "SELECT * FROM inspeccion_despacho_regla WHERE vin = ? ORDER BY id DESC LIMIT 1",
        (vin,), una=True)


def inspecciones_previas_por_vin(vin):
    """Las de la replica, del sistema viejo. Solo para mostrar: son 16.365 y
    son el historial que ya existe."""
    if not vin:
        return []
    return consultar(
        "SELECT * FROM inspeccion_despacho WHERE vin = ? ORDER BY id DESC", (vin,))


def fotos_guardadas(id_inspeccion):
    """Las filas de la tabla de fotos, en orden. SIN tope."""
    return consultar(
        "SELECT * FROM inspeccion_despacho_fotos_regla "
        " WHERE inspeccion_id = ? ORDER BY orden", (id_inspeccion,))


def fotos_de(fila):
    """Las fotos de una inspeccion, para la pantalla. Devuelve (rotulo, url).

    Sirve para las dos fuentes, y no son la misma:

      * una inspeccion de REGLA tiene sus fotos en
        `inspeccion_despacho_fotos_regla`, sin tope;
      * una del sistema viejo las tiene en `archivo1..archivo9` de su propia
        fila, y ahi el tope existe.

    La pantalla muestra las dos igual porque al operario no le importa de
    donde salio la inspeccion."""
    if fila is None:
        return []
    salida = []

    # Las nuestras.
    try:
        propias = fotos_guardadas(fila["id"]) if "unidad_id" in fila.keys() else []
    except Exception:                            # noqa: BLE001
        propias = []
    for f in propias:
        salida.append(("foto {}".format(f["orden"]), url_de_foto(f["ruta"])))
    if salida:
        return salida

    # Y las del legado, que si tienen slots.
    if fila["link_unidad"]:
        for r in str(fila["link_unidad"]).split(" | "):
            if r:
                salida.append(("unidad", url_de_foto(r)))
    for i in range(1, ARCHIVOS_EN_EL_LEGADO + 1):
        try:
            ruta = fila["archivo{}".format(i)]
        except (IndexError, KeyError):
            ruta = None
        if ruta:
            salida.append(("archivo{}".format(i), url_de_foto(ruta)))
    return salida


def aplanar_para_push(fotos):
    """Convierte N fotos en los nueve slots del legado.

    Devuelve `(campos, sobrantes)`:

      campos    {'archivo1': url, ..., 'archivo9': url, 'contador': n}
      sobrantes [(orden, url), ...]  las que NO entraron

    NUNCA DESCARTA EN SILENCIO. Si hay una decima, sale en `sobrantes` y quien
    llama decide -- hoy, avisarlo en la pantalla y dejarlo escrito. El legado
    hace lo contrario: su `elseif($cont == N)` simplemente no toma la decima, y
    la foto queda en `link_unidad` sin que nada lo diga.

    `link_unidad` se arma con TODAS, sobrantes incluidas, porque esa columna no
    tiene tope y es donde el legado igual las acumula. O sea que la foto once
    llega al legado; lo que no llega es a la seccion de fotos del PDF."""
    campos = {}
    sobrantes = []
    urls = []
    for i, (orden, url) in enumerate(fotos, 1):
        urls.append(url)
        if i <= ARCHIVOS_EN_EL_LEGADO:
            campos["archivo{}".format(i)] = url
        else:
            sobrantes.append((orden, url))
    campos["contador"] = min(len(fotos), ARCHIVOS_EN_EL_LEGADO)
    campos["link_unidad"] = " | ".join(urls)
    return campos, sobrantes


def tiene_inspeccion_por_vin(vin):
    """Si la unidad ya tiene inspeccion, propia o del sistema viejo."""
    if not vin:
        return False
    if inspeccion_de_vin(vin) is not None:
        return True
    return consultar("SELECT 1 FROM inspeccion_despacho WHERE vin = ? LIMIT 1",
                     (vin,), una=True) is not None


# ---------------------------------------------------------------------------
# Vistas
# ---------------------------------------------------------------------------

def _unidad(id_unidad):
    return consultar('SELECT * FROM "{}" WHERE id = ?'.format(TABLA),
                     (id_unidad,), una=True)


def _texto(campo):
    return (request.form.get(campo) or "").strip()


def _despachada(unidad):
    """El rechazo del PHP, replicado: una unidad que ya salio no tiene nada que
    documentar."""
    return estado_fisico(unidad) == "DESPACHADO"


@bp.route("/movimientos/<int:id_unidad>/inspeccion-despacho")
def entrada(id_unidad):
    """Punto de entrada desde la tarjeta del paso.

    Si la unidad ya tiene una inspeccion propia se va derecho a cargarle
    fotos, que es lo que falta; si no, se ofrece crearla."""
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404

    ya = inspeccion_de_vin(unidad["vin"])
    if ya is not None:
        return redirect(url_for("inspeccion_despacho.detalle", id_inspeccion=ya["id"]))

    return _pintar_alta(unidad)


def _pintar_alta(unidad, errores=None, codigo=200):
    es_post = request.method == "POST"
    pagina = render_template(
        "inspeccion_despacho_alta.html", u=unidad,
        encargado=nombre_actual(),
        hoy=datetime.now().date().isoformat(),
        despachada=_despachada(unidad),
        previas=inspecciones_previas_por_vin(unidad["vin"]),
        errores=errores or [], v=request.form if es_post else {})
    return (pagina, codigo) if codigo != 200 else pagina


@bp.route("/movimientos/<int:id_unidad>/inspeccion-despacho", methods=["POST"])
def crear(id_unidad):
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404

    if _despachada(unidad):
        return _pintar_alta(unidad, [
            "La unidad ya está DESPACHADA: no se le puede hacer la inspección "
            "de despacho."], codigo=400)

    ahora = datetime.now()
    datos = {
        "guia_despacho": _texto("guia_despacho"),
        "destino": _texto("destino"),
        "fecha_despacho": _texto("fecha_despacho"),
        "estanque": _texto("estanque"),
        "kilometraje": _texto("kilometraje"),
        "llaves": _texto("llaves"),
        # De la sesion y nunca del formulario, igual que en todo el modulo.
        "encargado": nombre_actual(),
    }

    errores = []
    if not datos["encargado"]:
        errores.append("La sesión no tiene un nombre con que firmar. Volvé a entrar.")
    if not datos["fecha_despacho"]:
        errores.append("Falta la fecha de despacho.")
    if errores:
        return _pintar_alta(unidad, errores, codigo=400)

    # La foto general se guarda recien cuando lo demas ya valido, para no dejar
    # archivos huerfanos de un intento rechazado.
    link_unidad = _guardar_foto(request.files.get("unidad"), unidad["vin"], "unidad")

    # El movimiento se registra igual que el check list y la revision de
    # contenedor: origen y destino son el MISMO estado, porque la inspeccion no
    # mueve la unidad en la maquina de estados.
    recomendado = recomendar(unidad)
    clave = recomendado["clave"] if recomendado else None
    estado_actual = estado_fisico(unidad)
    movimiento_id = registrar(unidad, {
        "paso": PASO,
        "recomendado": clave,
        "es_desvio": es_desvio(clave, PASO),
        "estado_desde": estado_actual,
        "estado_hacia": estado_actual,
        "motivo": _texto("motivo") if es_desvio(clave, PASO) else None,
        "motivo_detalle": None,
        "resultado_pdi": None,
        "guia_ingreso": None,
        "fecha": datos["fecha_despacho"] or None,
        "responsable": datos["encargado"] or None,
    })

    db = _db()
    cur = db.execute("""
        INSERT INTO inspeccion_despacho_regla
          (unidad_id, movimiento_id, vin, patente, guia_despacho, cliente,
           destino, fecha_despacho, marca, modelo, color, encargado, estanque,
           kilometraje, llaves, link_unidad, contador, fecha_entrega,
           fecha_completa, usuario, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        unidad["id"], movimiento_id, unidad["vin"], unidad["patente"],
        datos["guia_despacho"], unidad["clientecompleto"], datos["destino"],
        datos["fecha_despacho"], unidad["marca"], unidad["modelo"],
        unidad["color"], datos["encargado"], datos["estanque"],
        datos["kilometraje"], datos["llaves"], link_unidad, 0,
        ahora.date().isoformat(), ahora.isoformat(timespec="seconds"),
        id_actual(), ahora.isoformat(timespec="seconds")))
    db.commit()

    return redirect(url_for("inspeccion_despacho.detalle",
                            id_inspeccion=cur.lastrowid))


@bp.route("/inspecciones/<int:id_inspeccion>")
def detalle(id_inspeccion):
    fila = inspeccion(id_inspeccion)
    if fila is None:
        return render_template("no_encontrado.html", que="inspección",
                               id=id_inspeccion), 404
    return _pintar_detalle(fila)


def _pintar_detalle(fila, errores=None, codigo=200):
    unidad = _unidad(fila["unidad_id"])
    pagina = render_template(
        "inspeccion_despacho_detalle.html", i=fila, u=unidad,
        fotos=fotos_de(fila),
        cargadas=len(fotos_guardadas(fila["id"])),
        # El tope YA NO CIERRA LA PANTALLA. Se sigue mostrando porque el
        # operario tiene que saber cuantas van a entrar en el PDF del
        # despacho, pero la decima se puede cargar igual: quien decide que
        # hacer con ella es una persona, no un `elseif`.
        archivos_en_el_legado=ARCHIVOS_EN_EL_LEGADO,
        # Cuantas quedarian afuera del PDF si se enviara ahora.
        sobrantes=max(0, len(fotos_guardadas(fila["id"]))
                      - ARCHIVOS_EN_EL_LEGADO),
        enviado_en=fila["enviado_en"] if "enviado_en" in fila.keys() else None,
        errores=errores or [])
    return (pagina, codigo) if codigo != 200 else pagina


@bp.route("/inspecciones/<int:id_inspeccion>/foto", methods=["POST"])
def agregar_foto(id_inspeccion):
    """Una foto por envio, al siguiente slot libre. Es literalmente lo que hace
    el `_proces()` del PHP: mira el contador, elige `archivo{contador+1}` y lo
    vuelve a guardar."""
    fila = inspeccion(id_inspeccion)
    if fila is None:
        return render_template("no_encontrado.html", que="inspección",
                               id=id_inspeccion), 404

    # SIN TOPE. La decima se guarda igual; lo que cambia es que al empujar va
    # a salir en `sobrantes` y la pantalla lo va a decir. Rechazarla aca seria
    # perder una foto que el operario ya sacó, para respetar un limite que es
    # del cable y no del trabajo.
    cont = len(fotos_guardadas(id_inspeccion)) + 1

    archivo = request.files.get("imagen")
    if not archivo or not archivo.filename:
        return _pintar_detalle(fila, ["Elegí una foto para agregar."], codigo=400)

    ruta = _guardar_foto(archivo, fila["vin"], "inspeccion_despacho_{}".format(cont))
    if not ruta:
        return _pintar_detalle(fila, ["No se pudo guardar la foto."], codigo=400)

    from datetime import datetime
    db = _db()
    db.execute(
        "INSERT INTO inspeccion_despacho_fotos_regla "
        "  (inspeccion_id, orden, ruta, creado_en) VALUES (?, ?, ?, ?)",
        (id_inspeccion, cont, ruta,
         datetime.now().isoformat(timespec="seconds")))
    # `contador` de NUESTRA fila sigue al dia para que la pantalla y el push
    # lean lo mismo. Al empujar se recalcula acotado a nueve.
    db.execute("UPDATE inspeccion_despacho_regla SET contador = ? WHERE id = ?",
               (cont, id_inspeccion))
    db.commit()

    return redirect(url_for("inspeccion_despacho.detalle",
                            id_inspeccion=id_inspeccion, agregada=cont))


# ---------------------------------------------------------------------------
# El envio al sistema anterior
# ---------------------------------------------------------------------------
#
# POR QUE ES UN ACTO APARTE Y NO PASA AL GUARDAR
#
# El legado inserta la fila al confirmar el formulario y despues le va pegando
# las fotos con UPDATE, una por una. REGLA no puede hacer eso: el endpoint
# desplegado tiene `POST` para crear y `GET` para leer, y NINGUNA ruta de
# update sobre `inspeccion_despacho`. Empujar al crear mandaria la fila sin
# fotos, y las fotos son justo lo que el PDF del despacho necesita.
#
# Asi que la fila viaja UNA vez, completa. La contra es que aparece un boton
# donde el legado no tiene ninguno; a cambio no hay estado a medias --  no
# existe el momento en que el legado tiene la inspeccion y no tiene las fotos.
#
# Se podria evitar el boton pidiendo una ruta PUT nueva. No se pidio a
# proposito: seria una ruta mas en el legado para reproducir un orden de
# escritura que no le sirve a nadie.

@bp.route("/inspecciones/<int:id_inspeccion>/enviar", methods=["POST"])
def enviar(id_inspeccion):
    """Publica las fotos, aplana a los nueve slots y encola las dos escrituras."""
    from datetime import datetime

    fila = inspeccion(id_inspeccion)
    if fila is None:
        return render_template("no_encontrado.html", que="inspección",
                               id=id_inspeccion), 404
    if fila["enviado_en"]:
        return _pintar_detalle(fila, [
            "Esta inspección ya se envió el {}.".format(fila["enviado_en"])],
            codigo=400)

    unidad = _unidad(fila["unidad_id"])
    if unidad is None:
        return _pintar_detalle(fila, [
            "No se encontró la unidad de esta inspección."], codigo=400)

    fotos = fotos_guardadas(id_inspeccion)
    if not fotos:
        return _pintar_detalle(fila, [
            "La inspección no tiene fotos. El PDF del despacho las necesita: "
            "sin ellas el cliente recibe la sección de fotos vacía."],
            codigo=400)

    db = _db()
    try:
        from modulos.fotos_publicas import publicar, url_publica
        from modulos.push_legado import (campos_inspeccion,
                                         encolar_estado_inspeccion,
                                         encolar_inspeccion)

        # LAS FOTOS SE PUBLICAN ACA, no al subirlas. Publicar es un acto: una
        # foto que el operario cargo y despues borro no tiene por que quedar
        # con una URL viva para siempre.
        publicadas = []
        for f in fotos:
            url = url_publica(publicar(f["ruta"], origen="inspeccion_despacho",
                                       referencia=id_inspeccion))
            db.execute("UPDATE inspeccion_despacho_fotos_regla "
                       "   SET publicada = ? WHERE id = ?", (url, f["id"]))
            publicadas.append((f["orden"], url))

        campos_fotos, sobrantes = aplanar_para_push(publicadas)
        cuerpo = campos_inspeccion(unidad, fila, campos_fotos)
        id_cola = encolar_inspeccion(db, unidad, id_inspeccion, cuerpo)
        encolar_estado_inspeccion(db, unidad, id_inspeccion,
                                  depende_de=id_cola)
        db.execute("UPDATE inspeccion_despacho_regla SET enviado_en = ? "
                   " WHERE id = ?",
                   (datetime.now().isoformat(timespec="seconds"),
                    id_inspeccion))
        db.commit()
    except Exception:                            # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception(
            "no se pudo enviar la inspeccion %s", id_inspeccion)
        return _pintar_detalle(inspeccion(id_inspeccion), [
            "No se pudo encolar el envío. La inspección quedó guardada; se "
            "puede reintentar."], codigo=500)

    return redirect(url_for("inspeccion_despacho.detalle",
                            id_inspeccion=id_inspeccion,
                            enviada=1, sobrantes=len(sobrantes)))
