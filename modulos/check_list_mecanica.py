"""
modulos/check_list_mecanica.py -- Check List Mecanico, pasos 1 y 2.

El original son TRES pantallas encadenadas y no una:

  1. `check_list_mecanica`  -- los 65 campos del estado del vehiculo.
     Inserta la fila con `estado='ABIERTO'` y redirige a la 2.
  2. `subida_foto_check_list_mecanica` -- las fallas: una pieza, una
     modalidad y una foto por vez, acumuladas en la misma fila con ' | '.
  3. el correo, que cierra el check list y lo pasa a 'CERRADO'.

REGLA HACE 1 Y 2. EL 3 NO
=========================
El paso 3 lo sigue apretando administracion en el sistema viejo, y el estado
vuelve por el pull. Es la misma decision que ya tomamos para el check list de
ingreso, y mantenerla igual entre los dos modulos vale mas que optimizar cada
uno por separado: dos modulos que se comportan distinto en el mismo punto son
dos reglas que aprender en vez de una.

El paso 2 no es un extra: 2.635 de las 2.956 filas tienen al menos una falla
cargada. Es la salida del modulo, no un adjunto.

LO QUE MEDIMOS Y NO SE REPLICA
==============================

`precios` / `precios_adicionales`. En el codigo del legado ya estan
COMENTADOS -- `//$precio = $this->input->post('precio_valor');` -- y en el
dato son 16 filas de 2.956, la ultima con valor distinto de cero el
2026-02-13. Divergencia consciente: REGLA no los pide ni los empuja.

`faltante`. Inalcanzable desde el formulario; ver `catalogo_mecanica`.

LO QUE SI SE REPLICA Y CASI NO SE VE
====================================

LA REAPERTURA. Cuando la fila esta en 'REABIERTO', el paso 2 escribe en
`fallas_adicionales` / `fotos_adicionales` / `modalidad_adicional` en vez de
en `observacion` / `link_unidades` / `modalidad`. Son 68 filas, todos los
meses desde que hay datos, 40 de ellas en 2026.

Y ojo con como se mide: de esas 68 filas, HOY 67 dicen 'CERRADO' y una dice
'REABIERTO'. La rama se elige por el estado que la fila tenia cuando se subio
la foto, y despues el estado sigue caminando. Contar `WHERE estado =
'REABIERTO'` da 1 y hace concluir que la rama esta muerta; la unica prueba de
que corrio es que la columna `fallas_adicionales` tiene algo. La pregunta era
"cuando corrio esta rama" y la clave que la contesta es la columna que
escribe, no la columna de estado. (Regla 0.)

`es_pieza_nueva` ES OTRA COSA. Vive en la misma funcion y por eso parece la
misma rama, pero no toca `fallas_adicionales`: inserta el nombre tipeado en
`servicios_mecanicos`, el catalogo de autocompletado. No se puede medir su
recencia porque `servicios_mecanicos` NO esta replicada -- es la unica tabla
del modulo que el pull no trae.

EL CATALOGO DE FALLAS NO ES UN CATALOGO
=======================================
`observacion` guarda texto libre. En 2026 hay 1.142 valores distintos, con
`RECARGA EXTINTOR` y `RECARGAR EXTINTOR` como filas separadas, y `EXTINTOR
VENCIDO` junto a `EXTINTOR CADUCADO`. `servicios_mecanicos` existe para
sugerir mientras se escribe, y `es_pieza_nueva` es lo que lo hace crecer --
pero nada junta los sinonimos, asi que el catalogo crece y no ordena.

REGLA sugiere contra lo que REALMENTE se escribio, ordenado por frecuencia,
que es informacion que el legado tiene y no usa. No impone: el campo sigue
siendo libre, porque cerrarlo a una lista con esos 1.142 valores adentro seria
convertir el desorden en obligatorio.
"""

import os
from datetime import datetime

from flask import (Blueprint, redirect, render_template, request, session,
                   url_for)
from werkzeug.utils import secure_filename

from core import DATA_DIR, consultar, exigir_unidad_id, get_db
from modulos.acceso import id_actual, nombre_actual
from modulos.catalogo_mecanica import (CAMPOS, ESTADO_CARFLEX, POR_COLUMNA,
                                       SECCIONES, validar_campo,
                                       validar_estanque)
from modulos.fotos_publicas import publicar, url_publica
from modulos.movimientos import (es_desvio, estado_fisico, recomendar,
                                 registrar)
from modulos.unidades import TABLA

bp = Blueprint("check_list_mecanica", __name__, url_prefix="/movimientos")

# La clave del paso YA EXISTIA en `movimientos.PASOS` y en `HITO_DE_PASO`.
# Se usa esa y no una nueva: dos claves para el mismo paso es exactamente lo
# que la Regla 0 prohibe, y el sintoma seria un check list mecanico que no
# marca su propio hito.
PASO = "check_mecanica"

# El separador con el que se acumulan las fallas. Es el ' | ' del legado, con
# los espacios: `$obs.' | '.$observacion`. Un '|' pelado partiria distinto al
# releer y las fallas de REGLA no se verian igual que las del legado en la
# misma pantalla.
SEPARADOR = " | "

# La modalidad de la falla. El desplegable del legado ofrece dos y guarda la
# primera palabra: la etiqueta dice 'LEVE (TRABAJO RAPIDO)' y la columna
# guarda 'LEVE'.
#
# GRAVE aparece UNA vez en 8.952 fallas. Se ofrece igual -- esta en el menu,
# y el criterio es que el menu manda sobre el dato -- pero saber que
# practicamente nadie la usa importa: si en REGLA empieza a aparecer seguido,
# no es que el parque se rompio, es que la pantalla la puso muy a mano.
MODALIDADES = (
    ("LEVE", "LEVE (trabajo rápido)"),
    ("GRAVE", "GRAVE (trabajo lento)"),
)

ESTADO_INICIAL = "ABIERTO"
ESTADO_REABIERTO = "REABIERTO"

# Las fotos de fallas van bajo DATA_DIR igual que las del check list de
# ingreso. La ruta que se guarda es relativa a DATA_DIR porque es la que
# `fotos_publicas` sabe resolver.
SUBCARPETA = os.path.join("uploads", "falla_mecanica")
CARPETA_FOTOS = os.path.join(DATA_DIR, SUBCARPETA)

EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


# ---------------------------------------------------------------------------
# La tabla
# ---------------------------------------------------------------------------

def _asegurar_tabla(db):
    """IF NOT EXISTS y al vuelo, igual que las demas tablas `_regla`: el
    importador no las conoce y tienen que sobrevivir a una reimportacion."""
    # La coma FINAL no es cosmetica. Sin ella el ultimo campo del catalogo
    # queda pegado a la columna que sigue y SQLite lo acepta sin chistar --
    # se traga `"pocc" TEXT observacion TEXT` como una sola columna `pocc` de
    # tipo raro --, asi que la tabla se crea SIN `observacion` y sin un solo
    # error. Se descubre recien al escribir la primera falla.
    columnas = "".join(
        '          "{}" TEXT,\n'.format(c[0]) for c in CAMPOS)
    db.execute("""
        CREATE TABLE IF NOT EXISTS check_list_mecanica_regla (
          id INTEGER PRIMARY KEY,
          unidad_id INTEGER,
          movimiento_id INTEGER,
          vin TEXT,
          patente TEXT,
          cliente TEXT,
          guia TEXT,
          fecha_ingreso TEXT,
          marca TEXT,
          modelo TEXT,
          color TEXT,
          encargado TEXT,
          estanque TEXT,
          kilometraje TEXT,
          estado_carflex TEXT,
          obs_general TEXT,
          estado TEXT,
{}
          -- Las fallas del paso 2. Se ACUMULAN con ' | ', nunca se pisan:
          -- cada una es una falla distinta de la misma unidad y perder una es
          -- perder trabajo que alguien hizo con el auto delante.
          observacion TEXT,
          modalidad TEXT,
          link_unidades TEXT,
          contador INTEGER DEFAULT 0,
          -- Y las mismas tres para cuando el check list se REABRE. Ver el
          -- encabezado del modulo.
          fallas_adicionales TEXT,
          modalidad_adicional TEXT,
          fotos_adicionales TEXT,
          legado_id INTEGER,
          usuario TEXT,
          creado_en TEXT
        )""".format(columnas))
    db.execute(
        "CREATE INDEX IF NOT EXISTS ix_clm_regla_vin "
        "ON check_list_mecanica_regla (vin)")
    exigir_unidad_id(db, "check_list_mecanica_regla")


def por_vin(vin):
    if not vin:
        return []
    db = get_db()
    _asegurar_tabla(db)
    db.commit()
    return consultar(
        "SELECT * FROM check_list_mecanica_regla WHERE vin = ? "
        "ORDER BY id DESC", (vin,))


def _uno(id_check):
    db = get_db()
    _asegurar_tabla(db)
    db.commit()
    return consultar(
        "SELECT * FROM check_list_mecanica_regla WHERE id = ?",
        (id_check,), una=True)


# ---------------------------------------------------------------------------
# Paso 1: los 65 campos
# ---------------------------------------------------------------------------

def leer_campos(form):
    """Lee y valida los 65 campos. Devuelve (valores, errores).

    Se validan TODOS antes de devolver, no se corta en el primero: con 65
    campos, avisar de a uno significa 65 viajes para alguien que dejo tres sin
    marcar."""
    valores, errores = {}, []
    for columna, _etiqueta, _tipo, _ops in CAMPOS:
        valor, error = validar_campo(columna, form.get(columna))
        if error:
            errores.append(error)
        valores[columna] = valor
    return valores, errores


def guardar(unidad, cabecera, campos, movimiento_id):
    db = get_db()
    _asegurar_tabla(db)
    nombres = [c[0] for c in CAMPOS]
    fijas = ["unidad_id", "movimiento_id", "vin", "patente", "cliente", "guia",
             "fecha_ingreso", "marca", "modelo", "color", "encargado",
             "estanque", "kilometraje", "estado_carflex", "obs_general",
             "estado", "contador", "usuario", "creado_en"]
    valores = [
        unidad["id"], movimiento_id, unidad["vin"], unidad["patente"],
        unidad["clientecompleto"], cabecera["guia"], cabecera["fecha_ingreso"],
        unidad["marca"], unidad["modelo"], unidad["color"],
        cabecera["encargado"], cabecera["estanque"], cabecera["kilometraje"],
        cabecera["estado_carflex"], cabecera["obs_general"],
        ESTADO_INICIAL, 0, id_actual(),
        datetime.now().isoformat(timespec="seconds"),
    ] + [campos[n] for n in nombres]

    todas = fijas + nombres
    cur = db.execute(
        'INSERT INTO check_list_mecanica_regla ({}) VALUES ({})'.format(
            ", ".join('"{}"'.format(c) for c in todas),
            ", ".join("?" * len(todas))),
        valores)
    fila_id = cur.lastrowid

    # El push del PASO 1, en el MISMO commit que la fila. Igual que la PDI y
    # el check list de ingreso: si la fila se guarda y el encolado no, REGLA
    # tiene un check list que el legado nunca va a ver -- y administracion no
    # puede cerrarlo, que es el paso 3 y es lo unico que le dejamos al sistema
    # viejo.
    try:
        from modulos.push_legado import (campos_check_list_mecanico,
                                         encolar_check_list_mecanico,
                                         encolar_fecha_check_mecanico)
        fila = db.execute(
            "SELECT * FROM check_list_mecanica_regla WHERE id = ?",
            (fila_id,)).fetchone()
        encolar_check_list_mecanico(
            db, unidad, fila_id,
            campos_check_list_mecanico(unidad, fila))
        # Y LA UNICA COLUMNA QUE EL LEGADO LE ESCRIBE A LA UNIDAD.
        # `check_list_mecanica_proces()` hace exactamente un `actualizar_vin`,
        # con `fecha_check_list_mecanica`. Sin esto, la unidad quedaria en el
        # legado sin la marca de que tiene check list mecanico -- y esa
        # columna es la que `movimientos.hitos_de()` lee para saber si el paso
        # ya esta hecho, de los DOS lados.
        encolar_fecha_check_mecanico(db, unidad, fila_id,
                                     (fila["creado_en"] or "")[:10])
    except Exception:                            # noqa: BLE001
        # Que el encolado falle NO puede perder el check list: son 65 campos
        # cargados con el auto delante. Queda en el log y la reconciliacion lo
        # va a ver como una unidad que REGLA tiene y el legado no.
        import logging
        logging.getLogger(__name__).exception(
            "no se pudo encolar el check list mecanico %s", fila_id)

    db.commit()
    return fila_id


# ---------------------------------------------------------------------------
# Paso 2: las fallas
# ---------------------------------------------------------------------------

def fallas_sugeridas(limite=400):
    """Las fallas ya escritas, ordenadas por cuantas veces aparecieron.

    Sale de la replica y no de `servicios_mecanicos`, que no esta replicada.
    Es mejor fuente ademas de ser la unica: el catalogo del legado tiene los
    nombres que alguien dio de alta, esto tiene los que se usan.

    El split por ' | ' es el mismo con el que se guardaron."""
    from collections import Counter
    cuenta = Counter()
    for fila in consultar(
            "SELECT observacion FROM check_list_mecanica "
            " WHERE observacion IS NOT NULL AND observacion <> ''"
            "   AND fecha_creacion >= ?",
            (_desde_hace_un_ano(),)):
        for parte in (fila["observacion"] or "").split("|"):
            parte = parte.strip()
            if parte:
                cuenta[parte] += 1
    return [nombre for nombre, _n in cuenta.most_common(limite)]


def _desde_hace_un_ano():
    hoy = datetime.now().date()
    return "{}-{:02d}".format(hoy.year - 1, hoy.month)


def _guardar_foto(archivo, vin, numero):
    """Escribe la foto de una falla y devuelve su ruta relativa a DATA_DIR.

    El nombre imita al del legado -- `<VIN>_FALLA_MECANICA_NRO_<n>_<fecha>` --
    para que las dos carpetas se lean igual mientras convivan, pero el numero
    sale del `contador` de NUESTRA fila, no del de la del legado."""
    if not archivo or not archivo.filename:
        return None
    extension = os.path.splitext(archivo.filename)[1].lower()
    if extension not in EXTENSIONES:
        extension = ".jpg"

    carpeta_vin = secure_filename(vin or "") or "sin-vin"
    destino = os.path.join(CARPETA_FOTOS, carpeta_vin)
    os.makedirs(destino, exist_ok=True)
    sello = datetime.now().strftime("%Y-%m-%d_%H%M%S-%f")
    nombre = "{}_FALLA_MECANICA_NRO_{}_{}{}".format(
        carpeta_vin, numero, sello, extension)
    archivo.save(os.path.join(destino, nombre))
    return os.path.join(SUBCARPETA, carpeta_vin, nombre).replace("\\", "/")


def _acumular(anterior, nuevo):
    """El ' | ' del legado, con el mismo comportamiento en el primer valor:
    cuando no hay nada previo se guarda el valor solo, SIN separador
    adelante."""
    anterior = (anterior or "").strip()
    return nuevo if not anterior else anterior + SEPARADOR + nuevo


def agregar_falla(check, falla, modalidad, ruta_foto):
    """Suma una falla al check list. Devuelve (columnas_escritas, url_publica).

    Elige la rama por el estado que la fila tiene AHORA, igual que el legado.
    Que las columnas sean otras cuando esta reabierto no es un detalle de
    implementacion: es como distingue el sistema viejo las fallas de la
    primera pasada de las de la segunda."""
    db = get_db()
    _asegurar_tabla(db)

    reabierto = (check["estado"] or "").upper() == ESTADO_REABIERTO
    if reabierto:
        col_falla, col_modalidad, col_foto = (
            "fallas_adicionales", "modalidad_adicional", "fotos_adicionales")
    else:
        col_falla, col_modalidad, col_foto = (
            "observacion", "modalidad", "link_unidades")

    url = None
    if ruta_foto:
        url = url_publica(publicar(ruta_foto, origen="check_list_mecanica",
                                   referencia=check["id"]))

    db.execute(
        'UPDATE check_list_mecanica_regla SET "{}" = ?, "{}" = ?, "{}" = ?, '
        '       contador = ? WHERE id = ?'.format(
            col_falla, col_modalidad, col_foto),
        (_acumular(check[col_falla], falla),
         _acumular(check[col_modalidad], modalidad),
         _acumular(check[col_foto], url) if url else check[col_foto],
         (check["contador"] or 0) + 1,
         check["id"]))

    # El push del PASO 2. `depende_de` es la entrada del paso 1: hasta que esa
    # no vuelva, el legado no tiene la fila -- ni nosotros su id.
    try:
        from modulos.push_legado import encolar_falla_mecanica
        padre = db.execute(
            "SELECT id FROM sync_push_pendientes "
            " WHERE entidad = 'check_list_mecanica' AND python_id = ? "
            " ORDER BY id DESC LIMIT 1", (check["id"],)).fetchone()
        fila = db.execute(
            "SELECT id, legado_id FROM check_list_mecanica_regla WHERE id = ?",
            (check["id"],)).fetchone()
        encolar_falla_mecanica(
            db, fila, falla, modalidad, url,
            (col_falla, col_modalidad, col_foto),
            depende_de=(padre["id"] if padre else None))
    except Exception:                            # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception(
            "no se pudo encolar la falla del check list %s", check["id"])

    db.commit()
    return (col_falla, col_modalidad, col_foto), url


# ---------------------------------------------------------------------------
# Vistas
# ---------------------------------------------------------------------------

def _unidad(id_unidad):
    return consultar('SELECT * FROM "{}" WHERE id = ?'.format(TABLA),
                     (id_unidad,), una=True)


def _pintar(unidad, errores=None, valores=None, codigo=200):
    recomendado = recomendar(unidad)
    pagina = render_template(
        "check_list_mecanico.html",
        u=unidad, secciones=SECCIONES,
        estados_carflex=ESTADO_CARFLEX,
        encargado=nombre_actual(),
        hoy=datetime.now().date().isoformat(),
        es_desvio=es_desvio(recomendado["clave"] if recomendado else None,
                            PASO),
        recomendado=recomendado,
        motivo=request.values.get("motivo") or "",
        motivo_detalle=request.values.get("motivo_detalle") or "",
        errores=errores or [], v=valores or {},
        anteriores=por_vin(unidad["vin"]))
    return pagina if codigo == 200 else (pagina, codigo)


@bp.route("/<int:id_unidad>/check-list-mecanico")
def formulario(id_unidad):
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad",
                               id=id_unidad), 404
    return _pintar(unidad)


@bp.route("/<int:id_unidad>/check-list-mecanico", methods=["POST"])
def confirmar(id_unidad):
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad",
                               id=id_unidad), 404

    cabecera = {
        "guia": (request.form.get("guia") or "").strip(),
        "fecha_ingreso": (request.form.get("fecha_ingreso") or "").strip(),
        # De la sesion, NO del formulario: el que firma el check list es el
        # que lo esta haciendo. Mismo criterio que el check list de ingreso.
        "encargado": nombre_actual(),
        "kilometraje": (request.form.get("kilometraje") or "").strip(),
        "estado_carflex": (request.form.get("estado_carflex") or "").strip(),
        # `strtoupper` como el legado: la columna del sistema viejo esta toda
        # en mayusculas y una fila en minusculas se ve como de otro sistema.
        "obs_general": (request.form.get("obs_general") or "").strip().upper(),
    }

    campos, errores = leer_campos(request.form)

    estanque, error = validar_estanque(request.form.get("estanque"))
    if error:
        errores.append(error)
    cabecera["estanque"] = estanque

    if not cabecera["encargado"]:
        errores.append("La sesión no tiene un nombre de usuario con que firmar "
                       "el check list. Volvé a entrar.")
    if not cabecera["kilometraje"]:
        errores.append("Falta el kilometraje.")
    if cabecera["estado_carflex"] not in ESTADO_CARFLEX:
        errores.append("Falta el estado Carflex.")

    if errores:
        return _pintar(unidad, errores=errores, valores=request.form, codigo=400)

    # El movimiento se registra igual que en la fase 1, con el mismo diccionario
    # que usan el check list de ingreso y la PDI: `registrar` es el unico camino
    # por el que se escribe un movimiento, y por ahi cuelga tambien el push.
    #
    # El arco no mueve la unidad: el check list mecanico se hace donde la unidad
    # esta y ahi queda. Se guarda igual el estado en los dos extremos para que
    # la regla de consistencia del proceso -- el estado anterior de un
    # movimiento es el actual del previo -- siga cerrando.
    recomendado_ahora = recomendar(unidad)
    clave_recomendada = recomendado_ahora["clave"] if recomendado_ahora else None
    desvio = es_desvio(clave_recomendada, PASO)
    estado_actual = estado_fisico(unidad)

    movimiento_id = registrar(unidad, {
        "paso": PASO,
        "recomendado": clave_recomendada,
        "es_desvio": desvio,
        "estado_desde": estado_actual,
        "estado_hacia": estado_actual,
        "motivo": (request.form.get("motivo") or "").strip() if desvio else None,
        "motivo_detalle": (request.form.get("motivo_detalle") or "").strip()
                          if desvio else None,
        "resultado_pdi": None,
        "guia_ingreso": cabecera["guia"] or None,
        "fecha": cabecera["fecha_ingreso"] or None,
        "responsable": cabecera["encargado"] or None,
        # NO se empuja el movimiento. `Nota.php:check_list_mecanica_proces()`
        # llama a `registromov()` CERO veces -- contadas -- y a
        # `actualizar_vin()` una sola, con `fecha_check_list_mecanica`.
        # Mandarle a `registros` una fila que su propia pantalla no genera le
        # ensucia el historial de donde salen sus reportes. Es la misma
        # decision que ya se tomo para el IT.
        "empuja_movimiento": False,
    })

    id_check = guardar(unidad, cabecera, campos, movimiento_id)
    return redirect(url_for("check_list_mecanica.fallas", id_check=id_check))


@bp.route("/check-list-mecanico/<int:id_check>/fallas")
def fallas(id_check):
    check = _uno(id_check)
    if check is None:
        return render_template("no_encontrado.html", que="check list mecánico",
                               id=id_check), 404
    return render_template(
        "check_list_mecanico_fallas.html",
        c=check, modalidades=MODALIDADES,
        sugeridas=fallas_sugeridas(),
        reabierto=(check["estado"] or "").upper() == ESTADO_REABIERTO,
        cargadas=_cargadas(check),
        errores=[])


def _cargadas(check):
    """Las fallas ya cargadas, desarmadas para poder listarlas.

    Se leen de la rama que corresponde al estado actual, que es la misma en la
    que se van a escribir las siguientes."""
    reabierto = (check["estado"] or "").upper() == ESTADO_REABIERTO
    cf, cm, ck = (("fallas_adicionales", "modalidad_adicional",
                   "fotos_adicionales") if reabierto else
                  ("observacion", "modalidad", "link_unidades"))
    partes = [(check[cf] or "").split("|"),
              (check[cm] or "").split("|"),
              (check[ck] or "").split("|")]
    filas = []
    for i, falla in enumerate(partes[0]):
        falla = falla.strip()
        if not falla:
            continue
        filas.append({
            "n": i + 1,
            "falla": falla,
            "modalidad": partes[1][i].strip() if i < len(partes[1]) else "",
            "foto": partes[2][i].strip() if i < len(partes[2]) else "",
        })
    return filas


@bp.route("/check-list-mecanico/<int:id_check>/fallas", methods=["POST"])
def agregar(id_check):
    check = _uno(id_check)
    if check is None:
        return render_template("no_encontrado.html", que="check list mecánico",
                               id=id_check), 404

    falla = (request.form.get("falla") or "").strip().upper()
    modalidad = (request.form.get("modalidad") or "").strip()
    archivo = request.files.get("foto")

    errores = []
    if not falla:
        errores.append("Falta la falla.")
    if modalidad not in dict(MODALIDADES):
        errores.append("Falta la modalidad.")
    if archivo is None or not archivo.filename:
        # El legado tampoco guarda una falla sin foto: toda la funcion cuelga
        # de `if (isset($_FILES["imagen"]))`. La foto ES la evidencia.
        errores.append("Falta la foto de la falla.")

    if errores:
        return render_template(
            "check_list_mecanico_fallas.html",
            c=check, modalidades=MODALIDADES, sugeridas=fallas_sugeridas(),
            reabierto=(check["estado"] or "").upper() == ESTADO_REABIERTO,
            cargadas=_cargadas(check), errores=errores,
            v=request.form), 400

    ruta = _guardar_foto(archivo, check["vin"], (check["contador"] or 0) + 1)
    agregar_falla(check, falla, modalidad, ruta)
    return redirect(url_for("check_list_mecanica.fallas", id_check=id_check))
