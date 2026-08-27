"""
modulos/check_list.py -- Check List de Ingreso, dentro de Movimientos.

Es el paso `check_list_ingreso` del motor de reglas (modulos/movimientos.py)
convertido en formulario: el operario que tiene la unidad delante levanta
danos, faltantes y equipamiento, y al confirmar quedan dos cosas escritas --
la fila del check list en `check_list_regla` y el movimiento en
`movimientos_regla`, igual que cualquier otro paso de la fase 1.

Que se replica del original y que no
------------------------------------
1. FOTOS GENERALES SOLO PARA CIDEF. El original envuelve los campos
   `placa_vin` y `foto_unidad` en un `if($cliente=='CIDEF')`: al resto de los
   clientes no se les piden. Se replica tal cual -- ver `pide_fotos_generales`.

2. VARIOS DANOS EN UN ENVIO. Aca SI hay desviacion, y es consciente: el
   original manda UN dano por envio y los va acumulando en la fila, asi que
   una unidad con seis danos son seis idas y vueltas al servidor con la
   unidad delante. Este formulario acepta N filas de una. El formato guardado
   es identico -- lo que cambia es cuantas veces hay que apretar Guardar.

3. EL ENCARGADO NO SE ELIGE: es el usuario logueado. En el original es un
   desplegable de `empleados`; aca, con login real contra `tbl_users`, el que
   firma el check list es el que lo esta haciendo. El nombre se toma de la
   sesion y nunca del formulario -- un campo de solo lectura igual viaja en el
   POST y se puede editar antes de mandarlo.

Donde se guarda
---------------
En `check_list_regla`, tabla propia, y NO en `check_list`. Mismo criterio ya
establecido para `movimientos_regla`: las tablas espejo son la foto del
sistema viejo y sirven de patron de comparacion contra produccion, ademas de
que el importador las dropea en cada reimportacion.

Las columnas se llaman IGUAL que las de `check_list` a proposito, aunque sean
nombres feos (`observacion` para las piezas, `requerimiento` para el tipo de
dano). Cuando se construya el push, el mapeo contra la tabla real es 1:1 y no
hay que mantener un diccionario de traduccion que se desincronice.

El formato de los danos
-----------------------
Los tres campos paralelos separados por '-' y las fotos por ' | ', que es
exactamente lo que `modulos/unidades.py` ya sabe leer (`_partes_dano` y
`urls_de`). Escribir en el mismo formato que ya decodificamos significa que la
ficha muestra un check list nuestro sin una linea de codigo nueva.

Ese formato es fragil por definicion -- un guion dentro del nombre de una
pieza desalinea las tres listas --, pero aca el riesgo esta acotado y
verificado: los valores NO son texto libre sino catalogos, y ninguno de los
429 nombres de `piezas` ni de los 35 de `tipo_dano` contiene un guion.
"""

import os
import re
from datetime import datetime

from flask import (Blueprint, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.utils import secure_filename

from core import DATA_DIR, consultar, exigir_unidad_id, get_db
from modulos.acceso import id_actual, nombre_actual
from modulos.catalogos import normalizar
from modulos.movimientos import es_desvio, estado_fisico, recomendar, registrar
from modulos.unidades import TABLA

bp = Blueprint("check_list", __name__, url_prefix="/movimientos")


# ---------------------------------------------------------------------------
# Fotos: donde viven y como se llaman
# ---------------------------------------------------------------------------

# Bajo DATA_DIR, que en el contenedor es el volumen persistente. Fuera del
# volumen el disco se borra en cada redeploy y las fotos del check list se
# irian con el -- que es justo el dato que no se puede volver a sacar, porque
# la unidad ya no esta en el patio.
CARPETA_FOTOS = os.path.join(DATA_DIR, "uploads", "check_list")

EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


def _guardar_foto(archivo, vin, etiqueta):
    """Escribe una foto subida y devuelve su ruta RELATIVA a CARPETA_FOTOS.

    Se guarda al toque y no se difiere: la subida diferida necesitaria una
    cola y un reintento, y mientras tanto la foto vive solo en el telefono del
    operario, que es donde no puede quedarse.

    La subcarpeta es el VIN. El sistema viejo usaba la MOTONAVE, y eso le
    costo carpetas partidas al medio: 'COSCO PACIFIC / YANTIAN' tiene una
    barra adentro, asi que el nombre de un barco terminaba creando dos
    directorios anidados. El VIN no tiene ese problema y ademas junta todas
    las fotos de la unidad en un solo lugar."""
    if not archivo or not archivo.filename:
        return None

    extension = os.path.splitext(archivo.filename)[1].lower()
    if extension not in EXTENSIONES:
        extension = ".jpg"

    carpeta_vin = secure_filename(vin or "") or "sin-vin"
    destino = os.path.join(CARPETA_FOTOS, carpeta_vin)
    os.makedirs(destino, exist_ok=True)

    # El sello de tiempo lleva microsegundos porque el nombre restante puede
    # repetirse dentro del mismo check list: dos danos de la misma pieza con
    # el mismo tipo y nivel es un caso normal (dos rayas en la misma puerta),
    # y sin el, el segundo pisaria al primero.
    sello = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    nombre = "{}_{}_{}{}".format(
        carpeta_vin, secure_filename(etiqueta) or "foto", sello, extension)

    archivo.save(os.path.join(destino, nombre))
    return "{}/{}".format(carpeta_vin, nombre)


def url_de_foto(ruta):
    if not ruta:
        return None
    return url_for("check_list.foto", ruta=ruta)


@bp.route("/fotos/<path:ruta>")
def foto(ruta):
    """Sirve una foto del volumen. `send_from_directory` es el que valida que
    la ruta pedida no se escape de la carpeta."""
    return send_from_directory(CARPETA_FOTOS, ruta)


# ---------------------------------------------------------------------------
# Catalogos
# ---------------------------------------------------------------------------

def piezas():
    """Las 429 piezas, SOLO id y nombre.

    `piezas` tiene ademas diez columnas de precio por cliente
    (pintura_cidef, pintura_carflex, desab_*, pulir_*, desploteo_*, cambio_*).
    No se leen: el check list de ingreso levanta que se dano, no cuanto sale
    arreglarlo, y esos precios son la tarifa comercial con cada cliente.

    Alfabetico y no por id: por id no queda ordenado (se comprobo que las dos
    listas difieren) y en un selector de 429 items encontrar la pieza es todo
    lo que importa."""
    return consultar("SELECT id, nombre FROM piezas ORDER BY nombre")


def tipos_de_dano():
    """Los 35 tipos, alfabeticos.

    El orden por id tampoco sirve aca: no es ni alfabetico ni por frecuencia
    de uso (ABOLLADO, el mas usado con 38.797, esta tercero; RAYA, que esta
    primero, va quinto con 9.626)."""
    return consultar(
        "SELECT id, TRIM(nombre) AS nombre FROM tipo_dano ORDER BY TRIM(nombre)")


def niveles_de_dano():
    """POR ID, no alfabetico: es una escala de severidad, no una lista.

    Ordenado por nombre saldria GRAVE, LEVE, MEDIO, PRESUPUESTO -- lo grave
    arriba de lo leve y lo medio al final, que se lee como una escala al
    reves. Por id sale LEVE, MEDIO, GRAVE, PRESUPUESTO, que es la escala."""
    return consultar("SELECT id, nombre FROM nivel_dano ORDER BY id")


# El encargado YA NO SE ELIGE. Antes salia de la tabla `empleados` (cuatro
# filas) porque el login era una maqueta y no habia forma de saber quien estaba
# usando la pantalla. Con login real el encargado ES el usuario logueado: la
# pantalla lo muestra y el servidor lo toma de la sesion.
#
# Se toma de la SESION y no del formulario a proposito. Un input de solo
# lectura igual viaja en el POST y se puede editar antes de mandarlo, asi que
# firmar con lo que llega del navegador seria dejar que cualquiera cargue un
# check list a nombre de otro. Ver `nombre_actual()` en modulos/acceso.py.


# El equipamiento son checkboxes HARDCODEADOS y no un catalogo: en el sistema
# viejo estan escritos en el HTML del formulario, no en una tabla. Se replica
# igual -- derivarlos del dato inventaria una tabla que no existe.
#
# La lista sale de los valores realmente guardados en check_list. Dos cosas
# que se resolvieron mirando el dato:
#
#  - BOTIQUIN VA UNA SOLA VEZ. En el formulario original aparece tres veces
#    duplicado. Como los tres comparten destino, el duplicado no agrega nada:
#    16.463 filas lo tienen en `equipamiento2` y una sola (del 2025-03-25) en
#    `equipamiento1`. Queda un checkbox, en el grupo 2.
#
#  - LAS VARIANTES RETIRADAS NO SE OFRECEN. El formulario cambio con los anos
#    y en el dato conviven combinaciones viejas con las actuales. Se toma la
#    vigente, mirando hasta cuando se uso cada una contra el corte del dump
#    (2026-08-13):
#      'Encendedor - Cenicero' (838, ultima 2024-02-19) -> hoy son dos
#      checkboxes separados, los dos vigentes;
#      'Conos' y 'Tapas (1-4)' sueltos (1.552 c/u, ultimos 2023-11-14) -> hoy
#      es el combinado 'Conos - Tapas (1-4)', usado hasta el ultimo dia;
#      'Pisos' (213, ultima 2024-02-19) -> hoy son 'Pisos De Goma' y
#      'Pisos De Felpa';
#      '1 Llave' (1 fila, 2025-03-25) -> tipeo suelto, no es un item.
#
# Los titulos de grupo son de esta pantalla. El original no los tiene: son
# tres columnas sin nombre que en la base se llaman equipamiento1/2/3, y el
# reparto entre los tres SI es el del original.
EQUIPAMIENTO = [
    ("equipamiento1", "Cabina y herramientas", [
        "M.Propietario - L.Garantia",
        "Encendedor",
        "Cenicero",
        "Gata",
        "Barrote Gata",
        "Tapa Combustible",
        "Bolso Herramientas",
    ]),
    ("equipamiento2", "Accesorios y ruedas", [
        "Botiquin",
        "Gancho Tiro",
        "Llave Rueda",
        "Radio",
        "Antena",
        "Neumatico Repuesto",
        "Llantas",
        "Conos - Tapas (1-4)",
        "Pisos De Goma",
        "Pisos De Felpa",
        "Parrilla Techo",
        "Barra Antivuelco",
        "Barra Interna",
    ]),
    ("equipamiento3", "Documentos y seguridad", [
        "Triangulo",
        "Extintor",
        "Revision Tecnica",
        "P. Circulacion",
        "Padron",
        "S.O.A.P",
        "Certificado Gases",
        "Tag",
    ]),
]

# El separador con que el original une los items marcados. Va con espacios a
# los lados y por eso convive con los nombres que llevan un guion adentro
# ('M.Propietario - L.Garantia' es UN item, no dos).
SEP_EQUIPAMIENTO = " / "

# El estanque se guarda como un numero suelto (0..10 en el dato) y no como
# fraccion ni porcentaje.
ESTANQUE_MAX = 10


# ---------------------------------------------------------------------------
# La regla del cliente
# ---------------------------------------------------------------------------

def pide_fotos_generales(unidad):
    """True si a esta unidad hay que pedirle placa_vin y foto_unidad.

    Es el `if($cliente=='CIDEF')` del original. La unica diferencia es que la
    comparacion va NORMALIZADA y no con '=', que es la regla que ya rige en
    todo el proyecto: `clientecompleto` trae 6 filas guardadas como 'CIDEF '
    con espacio final, y con igualdad exacta a esas seis unidades no se les
    pediria la foto sin que nadie se entere."""
    return normalizar(unidad["clientecompleto"]) == "CIDEF"


def _coincide(informado, observado):
    """None cuando no hay con que comparar; si no, True/False.

    Se compara normalizado por lo mismo de siempre: 'BLANCO ' con espacio y
    'Blanco' son el mismo color, y marcar eso como discrepancia haria que el
    operario deje de mirar el aviso a los dos dias."""
    if informado is None or observado is None:
        return None
    if not str(informado).strip() or not str(observado).strip():
        return None
    return normalizar(informado) == normalizar(observado)


# ---------------------------------------------------------------------------
# La tabla propia
# ---------------------------------------------------------------------------

def _asegurar_tabla(db):
    """IF NOT EXISTS y al vuelo, igual que `movimientos_regla`: el importador
    no conoce esta tabla, asi que sobrevive a una reimportacion de la
    replica."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS check_list_regla (
          id INTEGER PRIMARY KEY,
          unidad_id INTEGER,
          movimiento_id INTEGER,
          vin TEXT,
          patente TEXT,
          cliente TEXT,
          guia_ingreso TEXT,
          fecha_ingreso TEXT,
          encargado TEXT,
          marca TEXT,
          modelo TEXT,
          color TEXT,
          modelo_observado TEXT,
          color_observado TEXT,
          modelo_coincide INTEGER,
          color_coincide INTEGER,
          estanque TEXT,
          kilometraje TEXT,
          equipamiento1 TEXT,
          equipamiento2 TEXT,
          equipamiento3 TEXT,
          faltante TEXT,
          observaciones TEXT,
          motonave TEXT,
          observacion TEXT,
          requerimiento TEXT,
          gravedad TEXT,
          link TEXT,
          link_guia TEXT,
          link_unidad TEXT,
          usuario TEXT,
          creado_en TEXT
        )""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS ix_check_list_regla_vin "
        "ON check_list_regla (vin)")

    # La guarda: rechaza filas sin unidad. Va acá porque esta
    # funcion ya corre en cada request y es idempotente.
    exigir_unidad_id(db, "check_list_regla")

def check_lists_por_vin(vin):
    if not vin:
        return []
    db = get_db()
    _asegurar_tabla(db)
    db.commit()
    return consultar(
        "SELECT * FROM check_list_regla WHERE vin = ? ORDER BY id DESC", (vin,))


def _bandera(valor):
    """SQLite no tiene booleano y `_coincide` puede devolver None ('no hay con
    que comparar'), que NO es lo mismo que False ('no coincide'). Se guarda
    NULL para el primero y 0/1 para el resto."""
    return None if valor is None else (1 if valor else 0)


def guardar(unidad, datos, movimiento_id):
    db = get_db()
    _asegurar_tabla(db)
    cur = db.execute("""
        INSERT INTO check_list_regla
          (unidad_id, movimiento_id, vin, patente, cliente, guia_ingreso,
           fecha_ingreso, encargado, marca, modelo, color, modelo_observado,
           color_observado, modelo_coincide, color_coincide, estanque,
           kilometraje, equipamiento1, equipamiento2, equipamiento3, faltante,
           observaciones, motonave, observacion, requerimiento, gravedad,
           link, link_guia, link_unidad, usuario, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        unidad["id"], movimiento_id, unidad["vin"], unidad["patente"],
        unidad["clientecompleto"], datos["guia_ingreso"], datos["fecha_ingreso"],
        datos["encargado"], unidad["marca"], unidad["modelo"], unidad["color"],
        datos["modelo_observado"], datos["color_observado"],
        _bandera(datos["modelo_coincide"]), _bandera(datos["color_coincide"]),
        datos["estanque"], datos["kilometraje"],
        datos["equipamiento1"], datos["equipamiento2"], datos["equipamiento3"],
        datos["faltante"], datos["observaciones"], datos["motonave"],
        datos["observacion"], datos["requerimiento"], datos["gravedad"],
        datos["link"], datos["link_guia"], datos["link_unidad"],
        id_actual(), datetime.now().isoformat(timespec="seconds")))
    db.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Lectura del formulario
# ---------------------------------------------------------------------------

def _texto(campo):
    return (request.form.get(campo) or "").strip()


def _equipamiento_marcado(clave, items):
    """Los items tildados de un grupo, unidos como los une el original.

    Se respeta el orden en que estan escritos los checkboxes y no el orden en
    que los manda el navegador, para que dos check lists con lo mismo tildado
    se guarden con la misma cadena y se puedan comparar."""
    marcados = request.form.getlist(clave)
    return SEP_EQUIPAMIENTO.join([i for i in items if i in marcados])


def _indices_de_danos():
    """Los numeros de fila que llegaron en el formulario, en orden.

    Se leen del nombre de los campos en vez de asumir 0..N: las filas se
    agregan y se borran en el navegador, asi que la numeracion llega con
    huecos."""
    indices = set()
    for clave in request.form:
        encontrado = re.match(r"dano_pieza_(\d+)$", clave)
        if encontrado:
            indices.add(int(encontrado.group(1)))
    return sorted(indices)


def _leer_danos_por_vin(vin):
    """Arma las cuatro cadenas paralelas a partir de las filas del formulario.

    Se saltean las filas vacias: el formulario arranca con una fila y el
    usuario agrega las que necesita, asi que una fila en blanco es lo normal y
    no un error.

    LA FOTO ES OBLIGATORIA en toda fila con pieza, y no es una exigencia
    caprichosa: `link` es POSICIONAL -- la foto numero 3 es la del dano numero
    3 --, asi que un dano sin foto en el medio correria todas las fotos
    siguientes un lugar y cada una quedaria colgada del dano equivocado. Es
    exactamente el desfase que ya se ve en el dato viejo, donde 3.760 de las
    18.060 filas con danos tienen menos fotos que danos.

    Por eso ademas se valida ANTES de escribir: si a una fila le falta la
    foto, no se guarda ninguna."""
    filas = []
    faltan_fotos = []

    for indice in _indices_de_danos():
        pieza = (request.form.get("dano_pieza_{}".format(indice)) or "").strip()
        if not pieza:
            continue
        archivo = request.files.get("dano_foto_{}".format(indice))
        if not archivo or not archivo.filename:
            faltan_fotos.append(pieza)
            continue
        filas.append((
            pieza,
            (request.form.get("dano_tipo_{}".format(indice)) or "").strip(),
            (request.form.get("dano_nivel_{}".format(indice)) or "").strip(),
            archivo))

    if faltan_fotos:
        return {"faltan_fotos": faltan_fotos, "cantidad": 0}

    piezas_, tipos, gravedades, fotos = [], [], [], []
    for pieza, tipo, nivel, archivo in filas:
        piezas_.append(pieza)
        tipos.append(tipo)
        gravedades.append(nivel)
        fotos.append(_guardar_foto(
            archivo, vin, "{}_{}_{}".format(pieza, tipo, nivel)))

    return {
        "observacion": "-".join(piezas_),
        "requerimiento": "-".join(tipos),
        "gravedad": "-".join(gravedades),
        "link": " | ".join(fotos),
        "cantidad": len(piezas_),
        "faltan_fotos": [],
    }


# ---------------------------------------------------------------------------
# Vistas
# ---------------------------------------------------------------------------

def _unidad(id_unidad):
    return consultar('SELECT * FROM "{}" WHERE id = ?'.format(TABLA),
                     (id_unidad,), una=True)


def _filas_de_danos():
    """Las filas de danos tal como venian en el formulario, para poder
    repintarlas cuando algo falla.

    Las FOTOS no se pueden repoblar -- el navegador no deja rellenar un input
    de archivo por seguridad --, asi que la pantalla avisa que hay que volver
    a elegirlas en vez de dejar que el usuario piense que siguen puestas."""
    filas = [{"i": i,
              "pieza": request.form.get("dano_pieza_{}".format(i), ""),
              "tipo": request.form.get("dano_tipo_{}".format(i), ""),
              "nivel": request.form.get("dano_nivel_{}".format(i), "")}
             for i in _indices_de_danos()]
    return filas or [{"i": 0, "pieza": "", "tipo": "", "nivel": ""}]


def _pintar(unidad, errores=None, valores=None, codigo=200):
    recomendado = recomendar(unidad)
    pagina = render_template(
        "check_list_ingreso.html",
        u=unidad,
        con_fotos_generales=pide_fotos_generales(unidad),
        piezas=piezas(), tipos=tipos_de_dano(), niveles=niveles_de_dano(),
        encargado=nombre_actual(), equipamiento=EQUIPAMIENTO,
        estanque_max=ESTANQUE_MAX,
        hoy=datetime.now().date().isoformat(),
        # Se avisa cuando el check list NO es el paso sugerido para esta
        # unidad, pero no se bloquea: el operario puede tener razon y la
        # replica estar atrasada. Lo que se le pide es el motivo, igual que en
        # cualquier otro desvio de la fase 1.
        es_desvio=es_desvio(recomendado["clave"] if recomendado else None,
                            "check_list_ingreso"),
        recomendado=recomendado,
        motivo=request.values.get("motivo") or "",
        motivo_detalle=request.values.get("motivo_detalle") or "",
        errores=errores or [], v=valores or {},
        filas_danos=_filas_de_danos() if valores else
                    [{"i": 0, "pieza": "", "tipo": "", "nivel": ""}],
        anteriores=check_lists_por_vin(unidad["vin"]))
    return pagina if codigo == 200 else (pagina, codigo)


@bp.route("/<int:id_unidad>/check-list")
def formulario(id_unidad):
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404
    return _pintar(unidad)


@bp.route("/<int:id_unidad>/check-list", methods=["POST"])
def confirmar(id_unidad):
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404

    datos = {
        "guia_ingreso": _texto("guia_ingreso"),
        "fecha_ingreso": _texto("fecha_ingreso"),
        # de la sesion, NO del formulario: ver la nota de arriba
        "encargado": nombre_actual(),
        "estanque": _texto("estanque"),
        "kilometraje": _texto("kilometraje"),
        "faltante": _texto("faltante"),
        "observaciones": _texto("observaciones"),
        "motonave": _texto("motonave"),
        "modelo_observado": _texto("modelo_observado"),
        "color_observado": _texto("color_observado"),
    }
    for clave, _titulo, items in EQUIPAMIENTO:
        datos[clave] = _equipamiento_marcado(clave, items)

    # La coincidencia se DEDUCE comparando los dos valores, no se le pregunta
    # al operario: si tuviera que tildarla el mismo, seria un dato que dice lo
    # que el usuario cree y no lo que el formulario tiene escrito.
    datos["modelo_coincide"] = _coincide(unidad["modelo"], datos["modelo_observado"])
    datos["color_coincide"] = _coincide(unidad["color"], datos["color_observado"])

    errores = []
    if not datos["encargado"]:
        # Solo puede pasar si la sesion quedo sin nombre, que no deberia
        # ocurrir con login real; se avisa en vez de guardar un check list sin
        # firma.
        errores.append("La sesión no tiene un nombre de usuario con que firmar "
                       "el check list. Volvé a entrar.")
    if not datos["fecha_ingreso"]:
        errores.append("Falta la fecha de ingreso.")

    # Las fotos generales se revisan ANTES de escribir cualquier archivo: si
    # falta una, no tiene sentido haber dejado a medias las de los danos.
    con_fotos = pide_fotos_generales(unidad)
    if con_fotos:
        for campo, titulo in (("placa_vin", "la foto de la placa del VIN"),
                              ("foto_unidad", "la foto de la unidad")):
            archivo = request.files.get(campo)
            if not archivo or not archivo.filename:
                errores.append(
                    "Falta {}: es obligatoria para las unidades CIDEF.".format(titulo))

    danos = None
    if not errores:
        danos = _leer_danos_por_vin(unidad["vin"])
        if danos["faltan_fotos"]:
            errores.append(
                "Cada dano necesita su foto. Sin foto quedaron: {}.".format(
                    ", ".join(danos["faltan_fotos"])))

    if errores:
        return _pintar(unidad, errores, request.form, codigo=400)

    datos.update({k: danos[k] for k in
                  ("observacion", "requerimiento", "gravedad", "link")})
    datos["link_guia"] = (_guardar_foto(request.files.get("placa_vin"),
                                        unidad["vin"], "placa_vin")
                          if con_fotos else None)
    datos["link_unidad"] = (_guardar_foto(request.files.get("foto_unidad"),
                                          unidad["vin"], "foto_unidad")
                            if con_fotos else None)

    # El movimiento se registra igual que en la fase 1: el check list no es un
    # caso aparte del flujo, es el paso `check_list_ingreso` con formulario.
    recomendado = recomendar(unidad)
    clave_recomendada = recomendado["clave"] if recomendado else None
    desvio = es_desvio(clave_recomendada, "check_list_ingreso")

    # El arco del movimiento: el check list no mueve la unidad de estado -- se
    # hace en ZONA DE RECEPCION y ahi queda --, pero guardarlo igual es lo que
    # permite verificar la regla de consistencia del proceso (cap. 2), que pide
    # que el estado anterior de un movimiento sea el actual del previo.
    estado_actual = estado_fisico(unidad)

    movimiento_id = registrar(unidad, {
        "paso": "check_list_ingreso",
        "recomendado": clave_recomendada,
        "es_desvio": desvio,
        "estado_desde": estado_actual,
        "estado_hacia": estado_actual,
        "motivo": _texto("motivo") if desvio else None,
        "motivo_detalle": _texto("motivo_detalle") if desvio else None,
        "resultado_pdi": None,
        "guia_ingreso": datos["guia_ingreso"] or None,
        "fecha": datos["fecha_ingreso"] or None,
        "responsable": datos["encargado"] or None,
    })

    guardar(unidad, datos, movimiento_id)

    return redirect(url_for("movimientos.unidad", id_unidad=id_unidad,
                            registrado="check_list_ingreso"))
