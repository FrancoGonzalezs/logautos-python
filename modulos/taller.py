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

from core import consultar, get_db
from modulos.acceso import id_actual, nombre_actual
from modulos.catalogos import normalizar
from modulos.movimientos import (_buscar, es_desvio, estado_fisico, recomendar,
                                 registrar)
from modulos.push_legado import (asegurar_tablas, campos_it, disparar_push,
                                 encolar_it)
from modulos.unidades import TABLA

bp = Blueprint("taller", __name__)

# El mismo catalogo para el resultado del IT y para los tres puntos de
# diagnostico de la PDI. Dos valores y nada mas: asi esta en los <select> de
# las dos vistas y asi lo valida el servidor del PHP.
RESULTADO = ["OK", "PRESENTA FALLAS"]

# Del <select name="tipo_combu"> de actualizar_pdi.php, tal cual -- incluido
# 'Electrico' sin tilde, que es el `value` que viaja.
COMBUSTIBLES = ["Bencina", "Diesel", "Electrico"]

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
          estado_desde TEXT,
          estado_hacia TEXT,
          encargado TEXT,
          usuario TEXT,
          creado_en TEXT
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_it_regla_vin ON it_regla (vin)")


def _db():
    db = get_db()
    _asegurar_tablas(db)
    return db


def _unidad(id_unidad):
    return consultar('SELECT * FROM "{}" WHERE id = ?'.format(TABLA),
                     (id_unidad,), una=True)


def _texto(campo):
    return (request.form.get(campo) or "").strip()


def pdi_de(vin):
    _db().commit()
    return consultar("SELECT * FROM pdi_regla WHERE vin = ? ORDER BY id DESC LIMIT 1",
                     (vin,), una=True)


def it_de(vin):
    _db().commit()
    return consultar("SELECT * FROM it_regla WHERE vin = ? ORDER BY id DESC LIMIT 1",
                     (vin,), una=True)


def _ya_tiene_pdi(unidad):
    """PDI previa, propia o de la replica.

    El PHP mira `fecha_pdi` de la unidad; se mira lo mismo, mas nuestra tabla,
    porque una PDI cargada desde REGLA todavia no viajo al sistema viejo."""
    if pdi_de(unidad["vin"]) is not None:
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
        errores=errores or [], v=request.form if es_post else {})
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

    recomendado = recomendar(unidad)
    clave = recomendado["clave"] if recomendado else None
    movimiento_id = registrar(unidad, {
        "paso": "pdi",
        "recomendado": clave,
        "es_desvio": es_desvio(clave, "pdi"),
        "estado_desde": estado_actual,
        "estado_hacia": estado_hacia,
        "motivo": _texto("motivo") if es_desvio(clave, "pdi") else None,
        "motivo_detalle": None,
        # La PDI es el unico paso con resultado en el motor; se conserva.
        "resultado_pdi": "taller_no_completado" if fr else "sin_novedad",
        "guia_ingreso": None,
        "fecha": datos["fecha_pdi"],
        "responsable": nombre_actual(),
    })

    db = _db()
    db.execute("""
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
    db.commit()

    return _volver(id_unidad, "taller.lista_pdi", "pdi",
                   unidad["vin"], estado_hacia)


# ---------------------------------------------------------------------------
# IT
# ---------------------------------------------------------------------------

def _destino_it(unidad):
    """CARFLEX va a inspeccion mecanica; el resto entra a taller."""
    if normalizar(unidad["clientecompleto"]) == "CARFLEX":
        return "INSPECCION MECANICA DESPACHO"
    return "INGRESO A TALLER"


def _pintar_it(unidad, errores=None, codigo=200):
    es_post = request.method == "POST"
    pagina = render_template(
        "it.html", u=unidad, resultados=RESULTADO,
        encargado=nombre_actual(),
        destino=_destino_it(unidad),
        estado_actual=estado_fisico(unidad),
        previo=it_de(unidad["vin"]),
        volver=request.values.get("volver", ""),
        errores=errores or [], v=request.form if es_post else {})
    return (pagina, codigo) if codigo != 200 else pagina


@bp.route("/movimientos/<int:id_unidad>/it")
def it(id_unidad):
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404
    return _pintar_it(unidad)


@bp.route("/movimientos/<int:id_unidad>/it", methods=["POST"])
def guardar_it(id_unidad):
    unidad = _unidad(id_unidad)
    if unidad is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404

    estado_it = _texto("estado_it").upper()
    observacion = _texto("observacion_it").upper()

    errores = []
    if estado_it not in RESULTADO:
        errores.append("Elegí el resultado de la revisión.")
    if estado_it == "PRESENTA FALLAS" and not observacion:
        errores.append("Si la unidad presenta fallas hay que decir cuáles: "
                       "la observación es obligatoria.")
    if errores:
        return _pintar_it(unidad, errores, codigo=400)

    estado_actual = estado_fisico(unidad)
    estado_hacia = _destino_it(unidad)

    # El paso que se registra es el del ESTADO al que se llega, no un nombre
    # propio: si fuera otro, hacer el IT cuando el motor lo recomendaba
    # figuraria como desvio, que es justo lo contrario de lo que paso.
    from modulos.movimientos import CLAVE_DE_ESTADO
    paso = CLAVE_DE_ESTADO.get(estado_hacia, "ingreso_taller")

    recomendado = recomendar(unidad)
    clave = recomendado["clave"] if recomendado else None
    movimiento_id = registrar(unidad, {
        "paso": paso,
        "recomendado": clave,
        "es_desvio": es_desvio(clave, paso),
        "estado_desde": estado_actual,
        "estado_hacia": estado_hacia,
        "motivo": _texto("motivo") if es_desvio(clave, paso) else None,
        "motivo_detalle": None,
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

    cur = db.execute("""
        INSERT INTO it_regla
          (unidad_id, movimiento_id, vin, estado_it, observacion_it,
           estado_desde, estado_hacia, encargado, usuario, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?)""", (
        unidad["id"], movimiento_id, unidad["vin"], estado_it, observacion,
        estado_actual, estado_hacia, nombre_actual(), id_actual(),
        datetime.now().isoformat(timespec="seconds")))

    # El push al legado. Las tres escrituras -- la fila de it_regla, el
    # push_pendiente de la unidad y la entrada de cola -- caen en el mismo
    # commit, que es lo que hace que el pull no pueda pisarnos: mientras el
    # flag este en 1, el UPSERT saltea la fila.
    #
    # Encolar es local y no le manda nada a nadie. Lo que sale a la red es
    # disparar_push, y eso ademas esta detras de PUSH_LEGADO_ACTIVO.
    id_cola = encolar_it(db, unidad, cur.lastrowid,
                         campos_it(estado_it, observacion, estado_hacia,
                                   id_actual()))
    db.commit()

    # Despues del commit, nunca antes: si el hilo saliera con la transaccion
    # abierta podria pushear un dato que todavia puede no quedar guardado.
    disparar_push(id_cola)

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
