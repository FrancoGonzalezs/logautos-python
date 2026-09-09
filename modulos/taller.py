"""
modulos/taller.py -- los dos resultados de revision de taller: PDI e IT.

Van juntos porque son la misma clase de pantalla: se elige una unidad, se
carga el resultado de una revision con catalogos chicos, y eso mueve el estado.
Comparten el catalogo OK / PRESENTA FALLAS y el patron de tabla, y separarlos
en dos modulos habria duplicado las dos cosas.

De donde sale cada uno
----------------------
PDI:  `views/patio/actualizar_pdi.php` + `Pedido.php:8305
      actualizar_pdi_process()`, que delega en el bloque
      `elseif($calle=='Pdi')` de `actulocproccess()`.
IT:   `views/patio/actualizar_it.php` + `Pedido.php:8352
      actualizar_it_process()`, que delega en `elseif($calle=='It')`.

Los catalogos NO estan inventados: salen del `<select>` de cada vista y estan
ademas validados en el servidor del PHP (`$estadosPermitidos`). Es la leccion
de `tipo_transporte`/`suciedad` en Revision de Contenedor, que se habian
escrito a ojo y estaban mal.

Divergencias deliberadas del PHP
--------------------------------
1. SE REGISTRA EL MOVIMIENTO. El IT del PHP cambia el estado y NO llama a
   `registromov()`: la unidad se mueve sin dejar rastro en el historial. Aca
   los dos escriben en `movimientos_regla`, que es nuestro registro propio y
   no depende de lo que haga el legado.

2. NO SE CONSTRUYE LA COMPUERTA DE COMBUSTIBLE. El PDI del PHP solo procede
   `if($stock > 20 || $combu == 'ELECTRICO')` y si no corta con "STOCK DE
   COMBUSTIBLE NO ES SUFICIENTE". Replicarlo implica traer el inventario de
   combustible y la OT automatica que lo consume, que estan fuera de alcance
   por ahora.

   DIVERGENCIA CONOCIDA, y hay que tenerla presente: el PDI en Python va a
   dejar pasar unidades que produccion frenaria por falta de stock. Se agrega
   cuando el inventario entre al sistema.

3. EL MENSAJE DE "YA TIENE PDI" NO ES UN ERROR. El PHP lo pinta como error
   aunque haya guardado y registrado el movimiento igual, que es la peor
   combinacion: dice que fallo algo que funciono. Aca se avisa, y se avisa
   antes, sin fingir una falla.

Lo que SI se replica aunque parezca un error
--------------------------------------------
Si la unidad ya tiene `fecha_proveedor_dyp` cargada, el estado NO se mueve:
se queda como esta, y el tilde de FR - MECANICA no aplica. En el PHP eso es
`if ($fecha_proveedor_dyp !== '0000-00-00') { $estado = getestadobyid($id); }`,
que pisa el estado elegido. No es un descuido: la unidad ya paso la etapa de
asignacion a proveedor, y volver a moverla desde el PDI seria retroceder algo
que otro modulo ya resolvio. Toca al 9,5% de las unidades (6.816 de 71.546).

Las cuatro fechas que no se preguntan
-------------------------------------
`aceite_coco`, `sistema_audio`, `adblue` y `aceite_diferencial` se completan
con la fecha de hoy al guardar, sin pedirlas. Es lo que hace el PHP y se
mantiene a proposito: son la evidencia para el cliente de que esas revisiones
se hicieron durante la PDI.
"""

from datetime import datetime

from flask import Blueprint, redirect, render_template, request, url_for
from markupsafe import escape

import os

from werkzeug.utils import secure_filename

from core import DATA_DIR, consultar, exigir_unidad_id, get_db
from modulos.acceso import id_actual, nombre_actual
from modulos.catalogos import normalizar
from modulos import avisos, combustible, destinatarios, imagenes, ot_pdi
from modulos.movimientos import (MOTIVOS, _buscar, es_desvio, estado_fisico,
                                 motivo_obligatorio, recomendar, registrar)
from modulos.push_legado import (asegurar_tablas, campos_it, encolar_movimiento_it, disparar_push,
                                 encolar_it)
from modulos.unidades import TABLA

bp = Blueprint("taller", __name__)

# El mismo catalogo para el resultado del IT y para los tres puntos de
# diagnostico de la PDI. Dos valores y nada mas: asi esta en los <select> de
# las dos vistas y asi lo valida el servidor del PHP.
RESULTADO = ["OK", "PRESENTA FALLAS"]

# Del <select name="tipo_combu"> de actualizar_pdi.php, tal cual -- incluido
# que la caja importa: el PHP compara `== 'Bencina'`, exacto.
#
# SE IMPORTA DE `ot_pdi` Y NO SE COPIA. Son la misma lista y tienen que
# separarse nunca: la pantalla ofrece lo que el calculo acepta, y el calculo
# acepta lo que el legado sabe leer. Copiada, alcanzaba con que alguien
# agregara 'GASOLINA' aca -- son 2.416 unidades en la replica -- para que la
# PDI se guardara y despues no generara OT ni descuento, sin un solo error.
from modulos.ot_pdi import COMBUSTIBLES

# Las cuatro revisiones que el PDI da por hechas y sella con la fecha del dia.
FECHAS_AUTOMATICAS = ["aceite_coco", "sistema_audio", "adblue",
                      "aceite_diferencial"]

# El centinela del sistema viejo para "sin fecha". En la replica el 90,5% de
# las unidades lo tiene asi.
SIN_FECHA = "0000-00-00"


# ---------------------------------------------------------------------------
# Tablas propias
# ---------------------------------------------------------------------------

def _asegurar_tablas(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS pdi_regla (
          id INTEGER PRIMARY KEY,
          unidad_id INTEGER,
          movimiento_id INTEGER,
          vin TEXT,
          fecha_pdi TEXT,
          tipo_combu TEXT,
          bateria TEXT,
          scanner TEXT,
          a_c TEXT,
          ob_mecanica TEXT,
          fr_mecanica INTEGER,
          -- Las cuatro que se sellan solas. Se guardan con su nombre real para
          -- que el push al sistema viejo sea un mapeo 1:1.
          aceite_coco TEXT,
          sistema_audio TEXT,
          adblue TEXT,
          aceite_diferencial TEXT,
          estado_desde TEXT,
          estado_hacia TEXT,
          encargado TEXT,
          usuario TEXT,
          creado_en TEXT
        )""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS ix_pdi_regla_vin ON pdi_regla (vin)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS it_regla (
          id INTEGER PRIMARY KEY,
          unidad_id INTEGER,
          movimiento_id INTEGER,
          vin TEXT,
          estado_it TEXT,
          observacion_it TEXT,
          destino_it TEXT,              -- ZD / DYP / FR
          patio TEXT,                   -- el que le toca al destino
          calle TEXT,                   -- idem
          estado_desde TEXT,
          estado_hacia TEXT,
          encargado TEXT,
          usuario TEXT,
          creado_en TEXT
        )""")

    # `destino_it`, `patio` y `calle` llegaron con el port del 2026-09-09.
    # ALTER y no un CREATE nuevo: la tabla ya existe en Railway con filas
    # adentro, y recrearla las perderia.
    cols = {r[1] for r in db.execute("PRAGMA table_info(it_regla)")}
    for columna in ("destino_it", "patio", "calle"):
        if columna not in cols:
            db.execute("ALTER TABLE it_regla ADD COLUMN {} TEXT".format(
                columna))

    # LAS FOTOS DEL IT. Tabla propia y SIN TOPE.
    #
    # El 6 es del formulario --sale de `procesar_fotos_it()` de Regla PHP-- y
    # no del modelo. Es la misma decision que las nueve de la inspeccion de
    # despacho: el limite del cable no puede ser el limite de lo que se guarda,
    # porque entonces la evidencia que no entra se pierde en vez de quedar
    # guardada y sin empujar.
    db.execute("""
        CREATE TABLE IF NOT EXISTS it_fotos_regla (
          id INTEGER PRIMARY KEY,
          it_id INTEGER NOT NULL,
          unidad_id INTEGER NOT NULL,
          vin TEXT NOT NULL,
          numero INTEGER NOT NULL,      -- 1..n, el orden en que se cargaron
          ruta TEXT NOT NULL,           -- relativa a DATA_DIR
          nota TEXT,                    -- si no se pudo recomprimir, por que
          creado_en TEXT
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_it_fotos_regla "
               "ON it_fotos_regla (it_id)")
    db.execute("CREATE INDEX IF NOT EXISTS ix_it_regla_vin ON it_regla (vin)")

    # La guarda: rechaza filas sin unidad. Va acá porque esta
    # funcion ya corre en cada request y es idempotente.
    exigir_unidad_id(db, "pdi_regla")
    exigir_unidad_id(db, "it_regla")

def _db():
    db = get_db()
    _asegurar_tablas(db)
    return db


def _unidad(id_unidad):
    return consultar('SELECT * FROM "{}" WHERE id = ?'.format(TABLA),
                     (id_unidad,), una=True)


def _texto(campo):
    return (request.form.get(campo) or "").strip()


def _valor(campo):
    """Como `_texto` pero mirando tambien la query string.

    Hace falta para el motivo. Cuando se llega desde Movimientos, el motivo se
    elige ALLA y `registrar_movimiento` redirige a este formulario con
    `?motivo=...` en la URL -- no en el cuerpo. `_texto` lee solo
    `request.form`, asi que ese motivo se perdia entero: Movimientos lo exigia,
    el operario lo elegia, y al llegar aca no existia mas."""
    return (request.values.get(campo) or "").strip()


# ---------------------------------------------------------------------------
# El motivo del desvio
# ---------------------------------------------------------------------------
#
# PDI e IT no exigian motivo NUNCA, a diferencia del endpoint generico
# `registrar_movimiento`, que corta con `error=falta_motivo` cuando la
# transicion esta en DESVIOS_CON_MOTIVO. Son dos agujeros distintos y los dos
# terminan en el mismo lugar -- un retrabajo indistinguible de un avance:
#
#   1. Viniendo DESDE Movimientos, el motivo se exige y se elige alla, pero se
#      pierde al redirigir: viaja en la query string y estas pantallas leian
#      solo el cuerpo del POST. Lo arregla `_valor` + el campo oculto.
#   2. Entrando por la puerta directa del menu (lista_pdi / lista_it), nunca
#      pasa por `registrar_movimiento`, asi que no hay quien lo exija. Lo
#      arregla `_falta_motivo`.
#
# El criterio es el mismo del endpoint generico y no uno propio: se exige solo
# en las transiciones de DESVIOS_CON_MOTIVO. NO se exige en todo desvio, y es
# deliberado -- el modulo ya decidio que "un motivo que se pide siempre deja de
# significar algo", y esas cuatro transiciones son las que miden retrabajo.

def _falta_motivo(estado_desde, estado_hacia):
    """La lista de motivos que hay que pedir, si falta el motivo. None si no
    hace falta pedir nada o si ya vino."""
    lista = motivo_obligatorio(estado_desde, estado_hacia)
    if lista and not _valor("motivo"):
        return lista
    return None


def _destinos_pdi(unidad):
    """A donde puede terminar un PDI de esta unidad.

    Son dos y no uno porque el destino depende del tilde de FR - MECANICA, que
    recien se sabe al enviar el formulario. Para decidir si hay que PEDIR el
    motivo se miran los dos: si cualquiera de los dos caminos lo exige, se pide
    antes y no despues de un intento fallido."""
    if _paso_asignacion_dyp(unidad):
        # No se mueve: el unico "destino" es donde ya esta.
        return [estado_fisico(unidad)]
    return ["FR - MECANICA", "EN ESPERA DYP CONSOLIDADO"]


def _contexto_motivo(unidad, destinos):
    """Lo que el formulario necesita para el bloque del motivo."""
    desde = estado_fisico(unidad)
    lista = None
    for hacia in destinos:
        lista = _falta_motivo(desde, hacia)
        if lista:
            break
    return {
        "lista_motivos": lista,
        "motivo_actual": _valor("motivo"),
        "motivo_detalle_actual": _valor("motivo_detalle"),
        "motivos": MOTIVOS,
    }


def _motivo_guardado(estado_desde, estado_hacia, hubo_desvio):
    """El motivo a guardar. Se guarda cuando hubo desvio O cuando la transicion
    es una de las que lo exigen -- igual que `registrar_movimiento`.

    La segunda mitad importa: el paso puede ser el recomendado y aun asi ser un
    retroceso de los que hay que medir."""
    if hubo_desvio or motivo_obligatorio(estado_desde, estado_hacia):
        return _valor("motivo") or None, _valor("motivo_detalle") or None
    return None, None


def pdi_de_unidad(unidad_id):
    """La ultima PDI DE ESTA PASADA. Por `unidad_id`, jamas por VIN.

    Buscar por VIN era un bug con consecuencia fisica, no cosmetica.
    `newstocks_cidef` tiene 71.546 filas para 61.447 VIN porque cada fila es
    UNA PASADA del vehiculo por el patio: el 14% de las filas son vehiculos
    que reingresaron. Con la busqueda por VIN, un vehiculo que vuelve a entrar
    le decia al movilizador "esta unidad ya tiene PDI" -- y la PDI era de la
    pasada anterior, de meses atras. El resultado no es una pantalla fea: es
    una PDI que no se hace sobre un vehiculo que la necesita.

    Comprobado sobre el dato real: la unidad 80022 devolvia la PDI de la 91987
    y la 87179 la de la 92049."""
    _db().commit()
    return consultar(
        "SELECT * FROM pdi_regla WHERE unidad_id = ? ORDER BY id DESC LIMIT 1",
        (unidad_id,), una=True)


def it_de_unidad(unidad_id):
    """El ultimo IT DE ESTA PASADA. Mismo motivo que `pdi_de_unidad`: la
    unidad 90389 traia el IT de la 92082."""
    _db().commit()
    return consultar(
        "SELECT * FROM it_regla WHERE unidad_id = ? ORDER BY id DESC LIMIT 1",
        (unidad_id,), una=True)


def _ya_tiene_pdi(unidad):
    """PDI previa, propia o de la replica.

    El PHP mira `fecha_pdi` de la unidad; se mira lo mismo, mas nuestra tabla,
    porque una PDI cargada desde REGLA todavia no viajo al sistema viejo."""
    if pdi_de_unidad(unidad["id"]) is not None:
        return True
    fecha = (unidad["fecha_pdi"] or "").strip()
    return bool(fecha) and fecha != SIN_FECHA


def _paso_asignacion_dyp(unidad):
    """Si la unidad ya tiene proveedor DYP asignado.

    Cuando lo tiene, el PDI no mueve el estado -- ver la nota del encabezado."""
    fecha = (unidad["fecha_proveedor_dyp"] or "").strip()
    return bool(fecha) and fecha != SIN_FECHA


# ---------------------------------------------------------------------------
# PDI
# ---------------------------------------------------------------------------

def _pintar_pdi(unidad, errores=None, codigo=200):
    es_post = request.method == "POST"
    pagina = render_template(
        "pdi.html", u=unidad,
        combustibles=COMBUSTIBLES, resultados=RESULTADO,
        encargado=nombre_actual(),
        hoy=datetime.now().date().isoformat(),
        solo_cidef=(normalizar(unidad["clientecompleto"]) != "CIDEF"),
        ya_tiene=_ya_tiene_pdi(unidad),
        paso_dyp=_paso_asignacion_dyp(unidad),
        estado_actual=estado_fisico(unidad),
        volver=request.values.get("volver", ""),
        errores=errores or [], v=request.form if es_post else {},
        **_contexto_motivo(unidad, _destinos_pdi(unidad)))
    return (pagina, codigo) if codigo != 200 else pagina


@bp.route("/movimientos/<int:id_unidad>/pdi")
def pdi(id_unidad):
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404
    return _pintar_pdi(unidad)


@bp.route("/movimientos/<int:id_unidad>/pdi", methods=["POST"])
def guardar_pdi(id_unidad):
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404

    # La PDI es de CIDEF. No es una regla nuestra: el proceso de CARFLEX no la
    # tiene, y su matriz de transiciones ni siquiera la nombra.
    if normalizar(unidad["clientecompleto"]) != "CIDEF":
        return _pintar_pdi(unidad, [
            "La PDI es del proceso CIDEF. Esta unidad es {}."
            .format(unidad["clientecompleto"] or "de otro cliente")], codigo=400)

    fr = request.form.get("fr_mecanica") == "1"
    datos = {
        "fecha_pdi": _texto("fecha"),
        "tipo_combu": _texto("tipo_combu"),
        "bateria": _texto("bateria").upper(),
        "scanner": _texto("scanner").upper(),
        "a_c": _texto("a_c").upper(),
        "ob_mecanica": _texto("ob_mecanica").upper(),
        "fr_mecanica": 1 if fr else 0,
    }

    errores = []
    if not datos["fecha_pdi"]:
        errores.append("Falta la fecha de la PDI.")
    if datos["tipo_combu"] not in COMBUSTIBLES:
        errores.append("Elegí el tipo de combustible.")
    # Los tres de diagnostico se validan contra el catalogo y no solo por
    # presencia: un valor fuera de la lista no puede entrar ni a mano.
    for campo, titulo in (("bateria", "la batería"), ("scanner", "el scanner"),
                          ("a_c", "el aire acondicionado")):
        if datos[campo] not in RESULTADO:
            errores.append("Elegí el resultado de {}.".format(titulo))
    if fr and not datos["ob_mecanica"]:
        errores.append("Si la unidad queda en FR - MECÁNICA hay que decir por qué: "
                       "la observación es obligatoria.")
    if errores:
        return _pintar_pdi(unidad, errores, codigo=400)

    # -- LA COMPUERTA DE COMBUSTIBLE -----------------------------------------
    #
    # El bloque entero del PDI del legado cuelga de
    # `if($stock > 20 || $combu == 'ELECTRICO')`, y si no pasa NO guarda nada:
    # ni la PDI, ni las OT, ni el descuento. Se replica igual, y se replica
    # FRENANDO -- no guardando la PDI y avisando -- porque guardarla de este
    # lado dejaria una PDI que el legado no tiene y que ademas nadie facturo.
    #
    # Se evalua contra la REPLICA: si el legado esta lento, la pantalla del
    # patio no se puede colgar. Ver modulos/combustible.py.
    #
    # HOY ESTO FRENA DE VERDAD: el diesel tiene 5 litros contra un umbral de
    # 20, asi que ninguna PDI a diesel pasa. No es un caso hipotetico ni un
    # camino que se pruebe con datos inventados.
    # `StockNoResuelto` se atrapa y se pinta: sin el stock no se puede decidir,
    # y un 500 le dice al operario menos que nada. NO se deja pasar la PDI --
    # "no se" no es "si": guardarla dejaria una PDI sin descuento y sin OT.
    try:
        compuerta = combustible.evaluar(datos["tipo_combu"])
    except combustible.StockNoResuelto as e:
        return _pintar_pdi(unidad, [e.motivo_usuario], codigo=400)
    if not compuerta["pasa"]:
        return _pintar_pdi(unidad, [compuerta["motivo"]], codigo=400)

    # ANTES de escribir nada: si se mira despues, nuestra propia fila cuenta
    # como "ya tenia" y no se empujaria nunca ninguna PDI. Ver el enganche del
    # push, mas abajo.
    ya_tenia_pdi = _ya_tiene_pdi(unidad)

    estado_actual = estado_fisico(unidad)
    if _paso_asignacion_dyp(unidad):
        # La unidad ya tiene proveedor DYP asignado: el estado se mantiene y el
        # tilde de FR - MECANICA no aplica. Ver la nota del encabezado.
        estado_hacia = estado_actual
    else:
        estado_hacia = "FR - MECANICA" if fr else "EN ESPERA DYP CONSOLIDADO"

    hoy = datetime.now().date().isoformat()
    for campo in FECHAS_AUTOMATICAS:
        datos[campo] = hoy

    lista = _falta_motivo(estado_actual, estado_hacia)
    if lista:
        return _pintar_pdi(unidad, [
            "Este movimiento va de {} a {}: hay que decir por que. Sin el "
            "motivo, un retrabajo no se distingue de un avance normal."
            .format(estado_actual, estado_hacia)], codigo=400)

    recomendado = recomendar(unidad)
    clave = recomendado["clave"] if recomendado else None
    motivo, motivo_detalle = _motivo_guardado(estado_actual, estado_hacia,
                                              es_desvio(clave, "pdi"))
    movimiento_id = registrar(unidad, {
        "paso": "pdi",
        "recomendado": clave,
        "es_desvio": es_desvio(clave, "pdi"),
        "estado_desde": estado_actual,
        "estado_hacia": estado_hacia,
        "motivo": motivo,
        "motivo_detalle": motivo_detalle,
        # La PDI es el unico paso con resultado en el motor; se conserva.
        "resultado_pdi": "taller_no_completado" if fr else "sin_novedad",
        "guia_ingreso": None,
        "fecha": datos["fecha_pdi"],
        "responsable": nombre_actual(),
    })

    db = _db()
    cur = db.execute("""
        INSERT INTO pdi_regla
          (unidad_id, movimiento_id, vin, fecha_pdi, tipo_combu, bateria,
           scanner, a_c, ob_mecanica, fr_mecanica, aceite_coco, sistema_audio,
           adblue, aceite_diferencial, estado_desde, estado_hacia, encargado,
           usuario, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        unidad["id"], movimiento_id, unidad["vin"], datos["fecha_pdi"],
        datos["tipo_combu"], datos["bateria"], datos["scanner"], datos["a_c"],
        datos["ob_mecanica"], datos["fr_mecanica"],
        datos["aceite_coco"], datos["sistema_audio"], datos["adblue"],
        datos["aceite_diferencial"], estado_actual, estado_hacia,
        nombre_actual(), id_actual(),
        datetime.now().isoformat(timespec="seconds")))
    pdi_id = cur.lastrowid

    # -- El push al legado ---------------------------------------------------
    #
    # PDI REPETIDA: NO SE EMPUJA. Es el criterio del PHP, replicado tal cual --
    # su bloque entero cuelga de
    #
    #     if($fecha_pdi == NULL || $fecha_pdi == '')
    #
    # o sea que una unidad que YA TENIA fecha de PDI antes de este guardado no
    # entra: no se actualiza, no se crea OT y no se descuenta combustible. La
    # pantalla del legado no avisa nada; simplemente no pasa.
    #
    # `ya_tenia_pdi` se calcula ANTES del INSERT de arriba a proposito. Si se
    # mirara despues, nuestra propia fila recien escrita contaria como "ya
    # tenia" y no se empujaria NUNCA ninguna PDI. Es el mismo orden que hay que
    # cuidar en `_ya_tiene_pdi`, que mira las dos fuentes.
    #
    # Y ojo: esto NO impide guardar la PDI de este lado. REGLA registra la
    # segunda PDI en su tabla -- es informacion real, la inspeccion se hizo --
    # y lo que no hace es mandarsela al legado, porque el legado la habria
    # descartado. Divergir para bien sigue siendo divergir.
    #
    # SON HASTA CUATRO ENTRADAS DE COLA Y TIENEN ORDEN. El movimiento lo encola
    # `registrar()` mas arriba, por su propio camino; las otras tres cuelgan de
    # el con `depende_de`:
    #
    #     movimiento  (registros + la unidad, en la transaccion del endpoint)
    #        |
    #        +-- pdi              las 14 columnas
    #        +-- ot_pdi           las dos OT
    #        +-- stock_consumibles  el descuento, si consume
    #
    # Las tres dependientes se escriben AHORA, en la misma transaccion que la
    # PDI, y no despues de que el movimiento vuelva OK. La diferencia importa:
    # encolarlas despues las pierde si el proceso muere en el medio, y eso
    # dejaria una PDI aplicada en el legado y sin cobrar.
    #
    # Y no se intentan hasta que el movimiento este resuelto SIN error. Un 409
    # en el movimiento significa que el legado gano, o sea que no hay PDI que
    # cobrar -- y `orden_trabajo` es append-only, la OT de mas no se borra.
    ids_cola = []
    if not ya_tenia_pdi:
        from modulos.push_legado import (asegurar_tablas, campos_pdi,
                                         encolar_descuento, encolar_ot_pdi,
                                         encolar_pdi)
        asegurar_tablas(db)

        # El id de cola del movimiento que `registrar()` acaba de encolar para
        # ESTA unidad. Se busca en vez de devolverse porque `registrar()` lo
        # llaman seis pantallas y cambiarle la firma para una sola las toca a
        # las seis.
        fila = db.execute(
            "SELECT id FROM sync_push_pendientes "
            " WHERE entidad = 'movimientos' AND python_id = ? "
            " ORDER BY id DESC LIMIT 1", (movimiento_id,)).fetchone()
        id_movimiento = fila["id"] if fila else None

        ids_cola.append(encolar_pdi(db, unidad, pdi_id,
                                    campos_pdi(datos, id_actual())))
        ids_cola.append(encolar_ot_pdi(db, unidad, pdi_id, datos,
                                       id_actual(), id_movimiento))
        if compuerta["consume"]:
            fila_stock = combustible.fila_de(compuerta["combustible"])
            ids_cola.append(encolar_descuento(
                db, pdi_id, fila_stock["id"],
                ot_pdi.litros_de(compuerta["combustible"],
                                 unidad["marca"], unidad["modelo"]),
                id_movimiento))
    db.commit()

    for id_cola in ids_cola:
        if id_cola:
            from modulos.push_legado import disparar_push
            disparar_push(id_cola)

    return _volver(id_unidad, "taller.lista_pdi", "pdi",
                   unidad["vin"], estado_hacia)


# ---------------------------------------------------------------------------
# IT
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# El IT: los tres destinos
# ---------------------------------------------------------------------------
#
# Copiados de `destinos_it()` de produccion/Pedido.php. La pantalla vieja de
# Regla Python no tenia destino: mandaba siempre `calle='It'` /
# `despachado='INGRESO A TALLER'`, que es la rama que en PRODUCCION NO EXISTE.
# Cada IT hecho asi le escribia a Regla PHP un estado que su propia pantalla ya
# no produce.
#
# `evidencia=True` obliga observacion Y al menos una foto.
DESTINOS_IT = {
    "ZD": {
        "nombre": "ZONA DE DESPACHO",
        "descripcion": "La unidad queda conforme y pasa a zona de despacho.",
        "patio": "PATIO 1",
        "calle": "ZD",
        "estado": "ZONA DE DESPACHO",
        "evidencia": False,
    },
    "DYP": {
        "nombre": "DESABOLLADURA Y PINTURA",
        "descripcion": "Presenta danos de carroceria y se entrega a DYP.",
        "patio": "PATIO 2",
        "calle": "ENTREGADO DYP",
        "estado": "DYP",
        "evidencia": True,
    },
    "FR": {
        "nombre": "FR - MECANICA",
        "descripcion": "Queda retenida por falla mecanica (taller De Parra).",
        # PATIO 2, Y NO EL PATIO 1 QUE DICE EL CODIGO DE REGLA PHP.
        #
        # Es el unico lugar de todo el proyecto donde NO aplica "coincidir vale
        # mas que tener razon", y es porque el dueño del sistema ya decidio:
        # `destinos_it()` tenia PATIO 1, Franco confirmo que es un bug vivo
        # desde el 2026-09-02 y lo corrigio en produccion. El historico decia
        # PATIO 2 con 98,9% sobre 809 casos y tenia razon.
        #
        # Coincidir vale mas que tener razon cuando la diferencia es una
        # convencion. Cuando es un error que el otro sistema ya esta
        # arreglando, copiarlo seria propagarlo.
        "patio": "PATIO 2",
        "calle": "Cmp3",
        "estado": "FR - MECANICA",
        "evidencia": True,
    },
}

# El maximo de fotos del FORMULARIO, no del modelo. `it_fotos_regla` no tiene
# tope: el 6 sale de `procesar_fotos_it()` de Regla PHP y es una decision de
# pantalla. Guardar siete y mostrar seis es una divergencia; no poder guardar
# la septima es perder evidencia de un daño.
TOPE_FOTOS_IT = 6

SUBCARPETA_IT = os.path.join("uploads", "it")
CARPETA_FOTOS_IT = os.path.join(DATA_DIR, SUBCARPETA_IT)
EXTENSIONES_IT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")


def destinos_para(unidad):
    """Los tres destinos, con el estado que le toca a ESTA unidad.

    La excepcion heredada de CARFLEX se resuelve aca y no en el guardado, para
    que la pantalla muestre el estado real al que va a ir la unidad y no uno
    generico que despues cambia."""
    salida = {}
    for clave, base in DESTINOS_IT.items():
        d = dict(base)
        if clave == "ZD" and normalizar(unidad["clientecompleto"]) == "CARFLEX":
            # Excepcion heredada: CARFLEX conserva su nomenclatura de
            # inspeccion de despacho. Es de Regla PHP y se replica tal cual.
            d["estado"] = "INSPECCION MECANICA DESPACHO"
            d["nombre"] = "ZONA DE DESPACHO (CARFLEX)"
        salida[clave] = d
    return salida


def _destino_it(unidad, clave=None):
    """El estado al que va la unidad con ese destino.

    Sin `clave` devuelve el de ZD, que es el conforme. Se conserva la firma de
    un solo argumento porque la usa `_pintar_it` para el contexto de motivo."""
    ds = destinos_para(unidad)
    return ds.get(clave or "ZD", ds["ZD"])["estado"]


def exige_evidencia(clave_destino, estado_it):
    """True si hay que pedir observacion Y al menos una foto.

    Las dos condiciones son un O, y la segunda es la que se olvida: una unidad
    que va a ZD --sin evidencia-- pero con estado `PRESENTA FALLAS` tambien
    tiene que traer foto. Es de `actualizar_it_process()`:

        $exigeEvidencia = ($destinos[$d]['evidencia'] === TRUE
                           || $estadoIt === 'PRESENTA FALLAS');
    """
    d = DESTINOS_IT.get(clave_destino)
    return bool(d and d["evidencia"]) or estado_it == "PRESENTA FALLAS"


def _guardar_foto_it(archivo, vin, numero):
    """Escribe una foto del IT con el perfil de daños. Devuelve (ruta, nota).

    PERFIL DE DAÑOS -- 800 px, calidad 0,8 -- porque una foto del IT es
    evidencia de un daño, igual que la del check list: es lo que sostiene que
    la unidad se entrego a DYP o que quedo retenida por mecanica.

    El nombre imita al de Regla PHP (`IT_{vin}_{Y-m-d_H-i-s}_{n}.jpg`) para que
    las dos carpetas se lean igual mientras convivan."""
    if not archivo or not archivo.filename:
        return None, None
    extension = os.path.splitext(archivo.filename)[1].lower()
    if extension not in EXTENSIONES_IT:
        extension = ".jpg"
    # Siempre `.jpg` de salida: `imagenes.procesar` recomprime a JPEG, y dejar
    # `.heic` en el nombre de un archivo que ya es JPEG confunde a todo el que
    # lo mire despues.
    salida = ".jpg"

    carpeta_vin = secure_filename(vin or "") or "sin-vin"
    destino = os.path.join(CARPETA_FOTOS_IT, carpeta_vin)
    sello = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nombre = "IT_{}_{}_{}{}".format(carpeta_vin, sello, numero, salida)
    _tam, nota = imagenes.guardar(archivo, os.path.join(destino, nombre),
                                  imagenes.DANOS)
    return os.path.join(SUBCARPETA_IT, carpeta_vin, nombre).replace(
        "\\", "/"), nota


def fotos_de_it(it_id):
    return consultar(
        "SELECT * FROM it_fotos_regla WHERE it_id = ? ORDER BY numero",
        (it_id,))




def _pintar_it(unidad, errores=None, codigo=200):
    es_post = request.method == "POST"
    pagina = render_template(
        "it.html", u=unidad, resultados=RESULTADO,
        encargado=nombre_actual(),
        destinos=destinos_para(unidad),
        tope_fotos=TOPE_FOTOS_IT,
        estado_actual=estado_fisico(unidad),
        previo=it_de_unidad(unidad["id"]),
        volver=request.values.get("volver", ""),
        errores=errores or [], v=request.form if es_post else {},
        # Los TRES destinos, no uno: el motivo se pide segun la transicion,
        # y hasta que el operario elija no se sabe cual va a ser. Pasar uno
        # solo dejaria sin pedir motivo a las otras dos.
        **_contexto_motivo(unidad,
                           [d["estado"] for d in destinos_para(unidad).values()]))
    return (pagina, codigo) if codigo != 200 else pagina


@bp.route("/movimientos/<int:id_unidad>/it")
def it(id_unidad):
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404
    return _pintar_it(unidad)


# ---------------------------------------------------------------------------
# El correo del IT
# ---------------------------------------------------------------------------
#
# Copiado de `enviar_correo_it()` de produccion/Pedido.php.
#
# ES EL SEGUNDO CANAL HACIA UN TERCERO que Regla Python maneja, y a diferencia
# del de la inspeccion --que resulto ser interno-- este SI sale de la empresa:
# `preentrega@cidef.cl` es del cliente, y con `$modoPrueba = FALSE` en
# produccion le llega de verdad.
#
# LAS FOTOS VAN INCRUSTADAS, NO COMO ENLACES. Regla PHP usa
# `addEmbeddedImage` con `cid:`, y la diferencia no es cosmetica: un correo
# donde las fotos se ven al abrirlo y otro donde hay que seguir seis enlaces no
# son el mismo correo para quien lo recibe. Ademas un enlace dependeria de que
# Regla Python este arriba cuando el cliente lo abra, que puede ser cualquier
# dia; una foto incrustada viaja adentro del mensaje y no depende de nadie.
REMITENTE_IT = "Regla Python - Revision IT <enviosdespacho@logautos.cl>"
RESPONDER_A_IT = "fgonzalez@logautos.cl"


def _cuerpo_correo_it(unidad, destino, cfg, estado_it, observacion, rutas):
    """Devuelve (asunto, texto, html, adjuntos)."""
    vin = unidad["vin"] or ""
    patente = (unidad["patente"] or "").strip() if "patente" in unidad.keys() \
        else ""

    asunto = "IT {} || VIN: {}".format(destino, vin)
    if patente:
        asunto += " || PATENTE: {}".format(patente)

    color = "#dd4b39" if destino == "FR" else "#3c8dbc"

    filas = [
        ("VIN", vin),
        ("Patente", patente),
        ("Marca", unidad["marca"]),
        ("Modelo", unidad["modelo"]),
        ("Color", unidad["color"]),
        ("Cliente", unidad["clientecompleto"]),
        ("Estado IT", estado_it),
        ("Destino", "{} - {}".format(destino, cfg["nombre"])),
        ("Registrado por", nombre_actual()),
        ("Fecha y hora", datetime.now().strftime("%d-%m-%Y %H:%M:%S")),
    ]
    tabla = ('<table cellpadding="7" cellspacing="0" border="0" '
             'style="border-collapse:collapse;font-family:Arial,Helvetica,'
             'sans-serif;font-size:13px;">')
    for etiqueta, valor in filas:
        valor = (str(valor) if valor is not None else "").strip()
        if not valor:
            continue
        tabla += (
            '<tr><td style="border:1px solid #ddd;background:#f7f7f7;'
            'font-weight:bold;width:150px;">{}</td>'
            '<td style="border:1px solid #ddd;">{}</td></tr>'.format(
                escape(etiqueta), escape(valor)))
    tabla += "</table>"

    adjuntos, galeria = [], ""
    for i, ruta in enumerate(rutas, start=1):
        absoluta = os.path.join(DATA_DIR, ruta)
        if not os.path.exists(absoluta):
            continue
        cid = "foto_it_{}".format(i)
        adjuntos.append({"ruta": absoluta, "cid": cid})
        galeria += (
            '<div style="display:inline-block;margin:0 10px 10px 0;'
            'text-align:center;">'
            '<img src="cid:{}" width="320" style="max-width:320px;'
            'border:1px solid #ddd;border-radius:4px;"><br>'
            '<small style="font-family:Arial;color:#777;">Foto {}</small>'
            "</div>".format(cid, i))
    if not galeria:
        galeria = ('<p style="font-family:Arial;font-size:13px;color:#777;">'
                   "(No se adjuntaron fotos en este registro.)</p>")

    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        'color:#333;">'
        '<h2 style="color:{c};margin:0 0 4px;">Revision IT - Destino {d}</h2>'
        '<p style="margin:0 0 16px;color:#777;font-size:12px;">'
        "Aviso automatico generado desde el modulo Actualizar IT.</p>"
        "{t}"
        '<h3 style="margin:22px 0 6px;">Observacion</h3>'
        '<div style="border-left:4px solid {c};background:#f7f7f7;'
        'padding:10px 14px;font-size:13px;white-space:pre-wrap;">{o}</div>'
        '<h3 style="margin:22px 0 6px;">Evidencia fotografica ({n})</h3>'
        "{g}"
        '<p style="margin-top:24px;font-size:11px;color:#999;">'
        "Correo generado automaticamente por REGLA - LOGAUTOS. No responder."
        "</p></div>"
    ).format(c=color, d=escape(destino), t=tabla,
             o=escape(observacion or "SIN OBSERVACION"),
             n=len(adjuntos), g=galeria)

    texto = (
        "Revision IT - Destino {}\n"
        "VIN: {}\n"
        "Estado IT: {}\n"
        "Observacion: {}\n"
        "Fotos adjuntas: {}"
    ).format(destino, vin, estado_it, observacion, len(adjuntos))

    return asunto, texto, html, adjuntos


def _encolar_correo_it(db, unidad, it_id, destino, cfg, estado_it,
                       observacion, rutas):
    """Deja el aviso listo para que lo mande el hilo de fondo.

    Se llama con la transaccion abierta: el correo se encola en el MISMO commit
    que la fila del IT, que las dos entradas de push y que las fotos. Si Resend
    esta caido, la revision NO se pierde y el aviso queda pendiente -- igual
    que en la inspeccion de despacho."""
    direcciones = destinatarios.sumando(
        destinatarios.MODULO_IT, unidad["clientecompleto"])
    if not direcciones:
        # Sin destinatarios no se encola nada: un aviso que no puede salir
        # solo agrega ruido a la cola y a la reconciliacion.
        return None
    asunto, texto, html, adjuntos = _cuerpo_correo_it(
        unidad, destino, cfg, estado_it, observacion, rutas)
    return avisos.encolar(db, "it", it_id, direcciones, asunto, texto, html,
                          remitente=REMITENTE_IT,
                          responder_a=RESPONDER_A_IT,
                          adjuntos=adjuntos)


@bp.route("/movimientos/<int:id_unidad>/it", methods=["POST"])
def guardar_it(id_unidad):
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404

    estado_it = _texto("estado_it").upper()
    observacion = _texto("observacion_it").upper()
    destino = _texto("destino_it").upper()
    disponibles = destinos_para(unidad)

    errores = []
    if estado_it not in RESULTADO:
        errores.append("Elegí el resultado de la revisión.")
    if destino not in disponibles:
        errores.append("Elegí a dónde va la unidad: ZD, DYP o FR.")

    # LAS FOTOS SE LEEN ANTES DE VALIDAR, y no despues.
    #
    # Si se validara primero y se leyeran despues, un formulario rechazado por
    # otro motivo perderia las fotos que el operario ya habia elegido -- el
    # navegador no las vuelve a mandar. Se leen, se cuentan, y si hay que
    # rechazar se rechaza; lo que no se hace es escribirlas al disco antes de
    # saber si el guardado va a salir.
    archivos = [a for a in request.files.getlist("fotos_it")
                if a and a.filename]

    if len(archivos) > TOPE_FOTOS_IT:
        errores.append(
            "Máximo {} fotos por revisión: mandaste {}."
            .format(TOPE_FOTOS_IT, len(archivos)))

    if destino in disponibles and exige_evidencia(destino, estado_it):
        porque = ("el destino {} exige evidencia".format(destino)
                  if DESTINOS_IT[destino]["evidencia"]
                  else "la unidad presenta fallas")
        if not observacion:
            errores.append(
                "Hay que escribir la observación: {}.".format(porque))
        if not archivos:
            errores.append(
                "Hay que adjuntar al menos una foto: {}.".format(porque))

    if errores:
        return _pintar_it(unidad, errores, codigo=400)

    estado_actual = estado_fisico(unidad)
    estado_hacia = disponibles[destino]["estado"]

    # Mismo corte que `registrar_movimiento`: sin motivo no se guarda. Va
    # DESPUES de validar el resultado del IT para no pedir dos cosas de a una,
    # y antes de escribir nada.
    lista = _falta_motivo(estado_actual, estado_hacia)
    if lista:
        return _pintar_it(unidad, [
            "Este movimiento va de {} a {}: hay que decir por que. Sin el "
            "motivo, un retrabajo no se distingue de un avance normal."
            .format(estado_actual, estado_hacia)], codigo=400)

    # El paso que se registra es el del ESTADO al que se llega, no un nombre
    # propio: si fuera otro, hacer el IT cuando el motor lo recomendaba
    # figuraria como desvio, que es justo lo contrario de lo que paso.
    from modulos.movimientos import CLAVE_DE_ESTADO
    paso = CLAVE_DE_ESTADO.get(estado_hacia, "ingreso_taller")

    recomendado = recomendar(unidad)
    clave = recomendado["clave"] if recomendado else None
    motivo, motivo_detalle = _motivo_guardado(estado_actual, estado_hacia,
                                              es_desvio(clave, paso))
    movimiento_id = registrar(unidad, {
        "paso": paso,
        "recomendado": clave,
        "es_desvio": es_desvio(clave, paso),
        "estado_desde": estado_actual,
        "estado_hacia": estado_hacia,
        # EL MOVIMIENTO SI SE EMPUJA, y hasta el 2026-09-09 no lo hacia.
        #
        # Aca decia `False`, apoyado en que "el bloque It de Regla PHP llama a
        # registromov() cero veces". Era falso: la rama VIVA lo llama UNA vez,
        # en produccion/Pedido.php:9505. El conteo se habia hecho sobre el
        # archivo de test, que ademas tiene una rama que produccion no tiene.
        #
        # El dato lo confirma sin leer codigo: `registros` con accion='It'
        # trae exactamente los dos estados que esa rama produce. O sea que
        # Regla Python le estaba SACANDO al historial de Regla PHP una fila que
        # Regla PHP si escribe -- y de ese historial salen sus reportes.
        #
        # Va por su propia entidad `it_movimiento`, con `depende_de` sobre la
        # entrada de `it`: las dos saldrian con el mismo
        # `legado_updated_at_conocido`, la primera avanzaria el `updated_at`
        # del otro lado y la segunda chocaria contra su propia escritura con un
        # 409 falso. Es el mismo patron que la PDI y sus OT.
        "empuja_movimiento": False,
        "motivo": motivo,
        "motivo_detalle": motivo_detalle,
        "resultado_pdi": None,
        "guia_ingreso": None,
        "fecha": datetime.now().date().isoformat(),
        "responsable": nombre_actual(),
    })

    db = _db()

    # Va ANTES del INSERT y no en el medio: `asegurar_tablas` usa
    # executescript, que en sqlite3 cierra la transaccion en curso con un
    # COMMIT implicito. Llamarlo despues del INSERT partiria en dos lo que
    # tiene que ser atomico.
    asegurar_tablas(db)
    _asegurar_tablas(db)

    cur = db.execute("""
        INSERT INTO it_regla
          (unidad_id, movimiento_id, vin, estado_it, observacion_it,
           destino_it, patio, calle,
           estado_desde, estado_hacia, encargado, usuario, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        unidad["id"], movimiento_id, unidad["vin"], estado_it, observacion,
        destino, disponibles[destino]["patio"], disponibles[destino]["calle"],
        estado_actual, estado_hacia, nombre_actual(), id_actual(),
        datetime.now().isoformat(timespec="seconds")))
    it_id = cur.lastrowid

    # -- LAS FOTOS, en la misma transaccion ---------------------------------
    #
    # El archivo se escribe al disco fuera de la transaccion --no hay forma de
    # hacer un rollback de un write-- pero la FILA que lo referencia entra con
    # todo lo demas. El orden importa: si el commit fallara quedaria un archivo
    # huerfano en disco, que no molesta a nadie; al reves quedaria una fila
    # apuntando a un archivo que no existe, y eso es una foto rota en el correo
    # de un cliente.
    rutas = []
    for n, archivo in enumerate(archivos, start=1):
        ruta, nota = _guardar_foto_it(archivo, unidad["vin"], n)
        if not ruta:
            continue
        rutas.append(ruta)
        db.execute("""
            INSERT INTO it_fotos_regla
              (it_id, unidad_id, vin, numero, ruta, nota, creado_en)
            VALUES (?,?,?,?,?,?,?)""", (
            it_id, unidad["id"], unidad["vin"], n, ruta, nota,
            datetime.now().isoformat(timespec="seconds")))

    # -- EL PUSH: `it` primero, el movimiento colgado de el -----------------
    #
    # Encolar es local y no le manda nada a nadie. Lo que sale a la red es
    # `disparar_push`, y eso ademas esta detras de PUSH_LEGADO_ACTIVO.
    id_cola = encolar_it(db, unidad, it_id,
                         campos_it(estado_it, observacion, estado_hacia,
                                   id_actual(),
                                   destino=destino,
                                   patio=disponibles[destino]["patio"],
                                   calle=disponibles[destino]["calle"]))

    id_mov = encolar_movimiento_it(db, unidad, movimiento_id, estado_hacia,
                                   id_actual(),
                                   calle=disponibles[destino]["calle"],
                                   patio=disponibles[destino]["patio"],
                                   depende_de=id_cola)

    # -- EL CORREO, encolado en la MISMA transaccion ------------------------
    #
    # Solo en los destinos con evidencia, igual que Regla PHP: `if
    # ($destinos[$destinoIt]['evidencia'] === TRUE)`. En ZD no sale correo.
    #
    # NUNCA se manda en el request. Un aviso de algo que despues no se guardo
    # es peor que no avisar, y este le llega a `preentrega@cidef.cl`, que es un
    # tercero: no se le puede avisar de una revision que no quedo escrita.
    if DESTINOS_IT[destino]["evidencia"]:
        _encolar_correo_it(db, unidad, it_id, destino, disponibles[destino],
                           estado_it, observacion, rutas)

    db.commit()

    # Despues del commit, nunca antes: si el hilo saliera con la transaccion
    # abierta podria pushear un dato que todavia puede no quedar guardado.
    disparar_push(id_cola)
    if id_mov:
        disparar_push(id_mov)

    return _volver(id_unidad, "taller.lista_it", "it",
                   unidad["vin"], estado_hacia)


# ---------------------------------------------------------------------------
# Puerta de entrada directa, desde el menu
# ---------------------------------------------------------------------------
#
# Ademas del camino por la tarjeta de Movimientos, PDI e IT tienen su propia
# pantalla de busqueda. No es duplicar: el guardado sigue siendo el mismo de
# arriba, esto es solo otra forma de llegar.
#
# La justifica un flujo real distinto. El movilizador escanea de a una unidad
# en el patio; el jefe de mecanicos junta VIN anotados en papel durante el dia
# y al final los pasa de a uno desde su notebook. Para ese segundo caso lo que
# importa es escribir el VIN y saltar al siguiente, no escanear -- por eso al
# guardar se vuelve acá con el campo vacio y con foco, en vez de mandar a la
# ficha de la unidad.

def _lista(titulo, bajada, endpoint_lista, destino_form):
    texto = request.args.get("q", "").strip()
    resultados = _buscar(texto) if texto else []

    # El estado que ve el movilizador es el de la FILA, y desde el 2026-08-27
    # eso ya incluye lo que REGLA acaba de hacer: `registrar()` la escribe al
    # guardar. Antes habia que superponerle el estado derivado con una marca,
    # porque la columna cruda seguia diciendo lo de antes -- el propio trabajo
    # del jefe de taller desactualizando la pantalla desde la que trabaja.

    # `fragmento=1` lo manda la busqueda en vivo: solo el bloque de
    # resultados, sin recargar. Y nunca redirige, por lo mismo que en
    # Movimientos -- si redirigiera, el fetch traeria la pagina equivocada.
    if request.args.get("fragmento") == "1":
        return render_template("_resultados_taller.html", texto=texto,
                               resultados=resultados, destino_form=destino_form)


    # Con un solo resultado y confirmacion explicita (Enter o el boton) se
    # entra derecho al formulario: es lo que hace rapido el encadenado.
    if texto and len(resultados) == 1:
        return redirect(url_for(destino_form, id_unidad=resultados[0]["id"],
                                volver="lista"))

    return render_template(
        "taller_lista.html", titulo=titulo, bajada=bajada,
        endpoint_lista=endpoint_lista, destino_form=destino_form,
        texto=texto, resultados=resultados,
        hecho=request.args.get("hecho"), quedo=request.args.get("quedo"))


@bp.route("/taller/pdi")
def lista_pdi():
    return _lista(
        "Actualizar PDI",
        "Buscá la unidad por VIN, patente o número de motor y cargá su PDI.",
        "taller.lista_pdi", "taller.pdi")


@bp.route("/taller/it")
def lista_it():
    return _lista(
        "Resultado de revisión IT",
        "Buscá la unidad y cargá si quedó OK o presenta fallas.",
        "taller.lista_it", "taller.it")


def _volver(id_unidad, endpoint_lista, registrado, vin, estado_hacia):
    """A donde se vuelve despues de guardar.

    Si se entro por la pantalla directa se vuelve a ella para seguir con el
    VIN siguiente; si se entro por la tarjeta de Movimientos, a la unidad."""
    if request.form.get("volver") == "lista":
        return redirect(url_for(endpoint_lista, hecho=vin, quedo=estado_hacia))
    return redirect(url_for("movimientos.unidad", id_unidad=id_unidad,
                            registrado=registrado))
