#!/usr/bin/env python3
"""
scripts/probar_circulo.py -- LA PRUEBA DE ACEPTACION DEL ENLACE.

Corre el MISMO movimiento a STOCK dos veces, sobre dos copias de la replica:
una con el enlace apagado (el codigo de ayer) y otra con el enlace puesto. Y
despues compara la reconciliacion de las dos, que es lo unico que decide si el
enlace sirve:

    ¿bajo la categoria "REGLA adelante"?

    python scripts/probar_circulo.py

No toca local.db, no sale a produccion.


POR QUE ESTA PRUEBA Y NO LAS OTRAS OCHO
=======================================

Las ocho suites verifican piezas: que la tabla traduzca, que el payload lleve
el patio, que el endpoint devuelva 201, que la ventana de recencia expire. Las
ocho pueden pasar con el enlace roto.

La reconciliacion mide otra cosa: si el legado TERMINO SABIENDO lo que REGLA
sabe. Eso no se ve desde ningun extremo -- un push que devuelve 200 y un legado
que quedo con el estado nuevo se ven identicos desde Python -- y por eso el
circuito tiene que cerrarse de verdad:

    movimiento en REGLA
      -> push            (POST /api_regla/movimientos)
      -> el legado aplica y mueve la unidad
      -> pull            (GET /api_regla/cambios/unidades)
      -> la replica ve el estado nuevo
      -> reconciliacion  -> "de acuerdo"

El paso que faltaba probar hasta hoy es el CUARTO. `registros` no esta en el
pull (pendiente 4), asi que la fila del movimiento sigue sin poder leerse; pero
la unidad si vuelve, y es la unidad la que la reconciliacion compara.


POR QUE A/B Y NO UN ANTES/DESPUES
=================================

Porque "bajo la categoria 1" no se puede leer de una sola corrida. La linea
base de la replica local es de 3 movimientos viejos que este cambio no toca:
agregar un movimiento nuevo SUMA a un cajon, no mueve los que ya estaban, y el
numero podria subir o bajar por razones que no tienen que ver con el enlace.

Lo que si es una medicion limpia es el mismo movimiento por los dos caminos.
La rama de control no simula nada a mano: restaura `STOCK` en `SIN_CALLE` y
llama a `encolar_movimiento` sin ubicacion explicita, que es LITERALMENTE como
lo llamaba `movimientos.registrar()` hasta ayer.


LO QUE ESTA PRUEBA NO ES: una prueba contra produccion. El simulado implementa
el mismo contrato pero no es el PHP. Sirve para saber que el circuito CIERRA;
el numero que decide en serio es el de la reconciliacion de Railway despues de
unos dias de uso real.
"""

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

CLAVE = "clave-de-prueba"
os.environ["LEGADO_API_KEY"] = CLAVE
os.environ.setdefault("SECRET_KEY", "prueba")

# NUNCA hacia produccion: los dos clientes se construyen con `base_url`
# explicito apuntando al simulado. `LEGADO_BASE_URL` no sirve -- las dos
# constantes de base se leen del entorno AL IMPORTAR el modulo, asi que
# setearla despues no tiene efecto y la peticion se va a claude.logautos.cl.
# Paso de verdad mientras se escribia esta prueba: el GET se fue a produccion y
# volvio 401.
BASE_SIMULADA = None

# La unidad: PRUEBA, y en INGRESO A TALLER. Que NO este ya en STOCK es lo que
# hace que el movimiento sea un cambio real -- con una unidad que ya esta en
# STOCK, las dos ramas dan el mismo resultado y la prueba no mide nada.
UNIDAD_ID = 66504

fallos = []


def paso(titulo):
    print("\n--- {} ---".format(titulo))


def afirmar(condicion, descripcion, detalle=""):
    if condicion:
        print("   ok  {}".format(descripcion))
    else:
        print("  FALLA {}{}".format(descripcion,
                                    ("  <- " + str(detalle)) if detalle else ""))
        fallos.append(descripcion)


def puerto_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def levantar(puerto, lista_blanca="desplegada"):
    proc = subprocess.Popen(
        [sys.executable, os.path.join(RAIZ, "scripts", "legado_simulado.py"),
         "--puerto", str(puerto), "--lista-blanca", lista_blanca],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=dict(os.environ))
    for _ in range(60):
        try:
            urllib.request.urlopen(
                "http://127.0.0.1:{}/_estado".format(puerto), timeout=1).read()
            return proc
        except Exception:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("el legado simulado no levanto")


def estado_simulado(puerto):
    with urllib.request.urlopen(
            "http://127.0.0.1:{}/_estado".format(puerto), timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def sembrar(puerto, unidad):
    """Carga el legado simulado con la unidad TAL COMO esta en la replica.

    El `updated_at` identico no es un detalle: con uno distinto el locking
    optimista daria 409 y la prueba mediria el conflicto en vez del circuito.
    Misma precaucion que se tomo para el primer push real contra produccion."""
    fila = {k: unidad[k] for k in
            ("id", "updated_at", "vin", "clientecompleto", "despachado",
             "calle", "patio", "estado_it", "observacion_it", "updated_by")}
    urllib.request.urlopen(urllib.request.Request(
        "http://127.0.0.1:{}/_sembrar".format(puerto),
        data=json.dumps(fila).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"), timeout=5).read()


def copia(sufijo):
    origen = os.path.join(RAIZ, "local.db")
    if not os.path.exists(origen):
        return None
    destino = os.path.join(tempfile.gettempdir(),
                           "regla_circulo_{}.db".format(sufijo))
    if os.path.exists(destino):
        os.remove(destino)
    shutil.copy(origen, destino)
    return destino


def abrir(ruta):
    db = sqlite3.connect(ruta)
    db.row_factory = sqlite3.Row
    return db


def conteo(ruta):
    from modulos.reconciliacion import comparar_estados
    db = abrir(ruta)
    try:
        return comparar_estados(db)
    finally:
        db.close()


ORDEN = ("de_acuerdo", "regla_adelante", "legado_adelante", "contradiccion",
         "sin_arco", "fuera_de_alcance")
ROTULO = {"de_acuerdo": "de acuerdo", "regla_adelante": "REGLA adelante",
          "legado_adelante": "el anterior adelante",
          "contradiccion": "contradicciones", "sin_arco": "sin arco",
          "fuera_de_alcance": "fuera de alcance"}


def linea(c):
    return " | ".join("{} {}".format(ROTULO[k], c[k]) for k in ORDEN)


def registrar_movimiento(ruta, unidad, con_enlace, puerto):
    """Escribe el movimiento a STOCK y lo empuja (o no). Devuelve el id de cola
    o None.

    `con_enlace=False` es el codigo de ayer, no una simulacion: se restaura
    `STOCK` en `SIN_CALLE` y se llama a `encolar_movimiento` SIN ubicacion
    explicita, que es exactamente como lo llamaba `registrar()`."""
    from modulos import push_legado
    from modulos.movimientos import _asegurar_tabla

    db = abrir(ruta)
    _asegurar_tabla(db)
    push_legado.asegurar_tablas(db)

    cur = db.execute("""
        INSERT INTO movimientos_regla
          (unidad_id, vin, paso, paso_recomendado, es_desvio, usuario,
           creado_en, estado_desde, estado_hacia, patio, calle, responsable)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (unidad["id"], unidad["vin"], "stock", "stock", 0, "47",
         "2026-08-27T16:00:00", (unidad["despachado"] or "").strip(), "STOCK",
         "PATIO 5" if con_enlace else None, "D" if con_enlace else None,
         "Prueba del circulo"))
    movimiento_id = cur.lastrowid

    if con_enlace:
        id_cola = push_legado.encolar_movimiento(
            db, unidad, movimiento_id, "STOCK", "47",
            calle="D", patio="PATIO 5")
    else:
        guardado = dict(push_legado.SIN_CALLE)
        push_legado.SIN_CALLE["STOCK"] = (
            "la calle de patio es el dato y REGLA no la pide")
        try:
            id_cola = push_legado.encolar_movimiento(
                db, unidad, movimiento_id, "STOCK", "47")
        finally:
            push_legado.SIN_CALLE.clear()
            push_legado.SIN_CALLE.update(guardado)
    db.commit()
    db.close()

    if id_cola is not None:
        cliente = push_legado.ClientePushLegadoHTTP(base_url=BASE_SIMULADA)
        resultado = push_legado.ejecutar_entrada(id_cola, cliente=cliente,
                                                 db_path=ruta)
        afirmar(resultado == "ok", "el push se aplico", resultado)
    return id_cola


def correr_pull(ruta):
    """Una vuelta del pull contra el simulado. El cliente se construye con
    `base_url` explicito: ver la nota de BASE_SIMULADA."""
    from modulos import sync_legado
    cliente = sync_legado.Legado(base_url=BASE_SIMULADA)
    return sync_legado.sincronizar_entidad("unidades", desde="",
                                           db_path=ruta, cliente=cliente)


def rama(nombre, con_enlace, unidad_base):
    print("\n" + "=" * 62)
    print("RAMA {}: {}".format(nombre, "con el enlace puesto" if con_enlace
                               else "con el enlace apagado (codigo de ayer)"))
    print("=" * 62)

    ruta = copia(nombre)
    puerto = puerto_libre()
    srv = levantar(puerto)
    global BASE_SIMULADA
    BASE_SIMULADA = "http://127.0.0.1:{}".format(puerto)
    try:
        db = abrir(ruta)
        unidad = db.execute("SELECT * FROM newstocks_cidef WHERE id=?",
                            (UNIDAD_ID,)).fetchone()
        db.close()
        sembrar(puerto, unidad)

        base = conteo(ruta)
        print("   base:    {}".format(linea(base["conteo"])))

        id_cola = registrar_movimiento(ruta, unidad, con_enlace, puerto)
        if con_enlace:
            afirmar(id_cola is not None, "STOCK se encola")
            sim = estado_simulado(puerto)
            fl = sim["unidades"][str(UNIDAD_ID)]
            afirmar(fl["despachado"] == "STOCK", "el legado quedo en STOCK",
                    fl["despachado"])
            afirmar(fl["calle"] == "D" and fl["patio"] == "PATIO 5",
                    "con la calle y el patio que eligio el movilizador",
                    (fl["calle"], fl["patio"]))
            reg = sim["registros"][-1]
            afirmar(reg["patio"] == "PATIO 5",
                    "y la fila de registros con el patio DESTINO lleno "
                    "-- el bug de la 305637", reg["patio"])
            afirmar(reg["newestado"] == (unidad["despachado"] or "").strip(),
                    "y el ORIGEN resuelto por el endpoint, no mandado por REGLA",
                    reg["newestado"])
        else:
            afirmar(id_cola is None,
                    "STOCK NO se encola: es lo que hacia el codigo de ayer")

        r = correr_pull(ruta)
        print("   pull: recibidas {} | actualizadas {} | marca {} -> {}".format(
            r.get("recibidas"), r.get("actualizadas"),
            r.get("marca_agua_previa") or "(vacia)", r.get("marca_agua_nueva")))

        db = abrir(ruta)
        f = db.execute("SELECT despachado, calle, patio FROM newstocks_cidef "
                       " WHERE id=?", (UNIDAD_ID,)).fetchone()
        db.close()
        print("   la replica ve: {} / {} / {}".format(
            f["despachado"], f["calle"], f["patio"]))

        despues = conteo(ruta)
        print("   despues: {}".format(linea(despues["conteo"])))
        return base["conteo"], despues["conteo"]
    finally:
        srv.kill()
        BASE_SIMULADA = None




# ---------------------------------------------------------------------------
# La PDI: el mismo push contra las DOS listas blancas
# ---------------------------------------------------------------------------
#
# ESTA SECCION NO SE PODIA ESCRIBIR HASTA EL 2026-08-27, y el motivo es el
# hallazgo. El simulado aplicaba `fila.update(datos)` a secas -- aceptaba todo
# --, asi que una prueba del circuito sobre PDI pasaba en verde con las
# columnas SIN desplegar del otro lado. Un doble que acepta mas que el original
# no prueba de menos: prueba al reves.

UNIDAD_PDI = 66505


def rama_pdi(lista_blanca):
    """Empuja una PDI contra el simulado con esa lista blanca.

    Devuelve (resultado, fila del legado, estado del simulado)."""
    from modulos import push_legado as P

    ruta = copia("pdi_" + lista_blanca)
    puerto = puerto_libre()
    srv = levantar(puerto, lista_blanca)
    global BASE_SIMULADA
    BASE_SIMULADA = "http://127.0.0.1:{}".format(puerto)
    try:
        db = abrir(ruta)
        P.asegurar_tablas(db)
        db.commit()
        unidad = db.execute("SELECT * FROM newstocks_cidef WHERE id=?",
                            (UNIDAD_PDI,)).fetchone()
        fila = {k: unidad[k] for k in
                ("id", "updated_at", "vin", "clientecompleto", "despachado",
                 "calle", "patio", "estado_it", "observacion_it", "updated_by")}
        # `ubicacion` se siembra CON VALOR a proposito: es la unica columna del
        # despliegue que BORRA en vez de agregar, y si arrancara vacia la
        # prueba no distinguiria "la borro" de "no la toco".
        fila["ubicacion"] = "7"
        urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:{}/_sembrar".format(puerto),
            data=json.dumps(fila).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"), timeout=5).read()

        datos = {"fecha_pdi": "2026-08-27", "tipo_combu": "Diesel",
                 "bateria": "OK", "scanner": "OK", "a_c": "OK",
                 "ob_mecanica": ""}
        id_cola = P.encolar_pdi(db, unidad, 1, P.campos_pdi(datos, "47"))
        db.commit()
        db.close()

        cliente = P.ClientePushLegadoHTTP(base_url=BASE_SIMULADA)
        resultado = P.ejecutar_entrada(id_cola, cliente=cliente, db_path=ruta)

        sim = estado_simulado(puerto)
        return resultado, sim["unidades"][str(UNIDAD_PDI)], sim
    finally:
        srv.kill()
        BASE_SIMULADA = None


def main():
    if copia("chequeo") is None:
        print("no hay local.db: esta prueba necesita la replica")
        return 0

    import core
    ruta_cualquiera = copia("chequeo")
    core.DB_PATH = ruta_cualquiera

    db = abrir(ruta_cualquiera)
    unidad = db.execute("SELECT * FROM newstocks_cidef WHERE id=?",
                        (UNIDAD_ID,)).fetchone()
    db.close()
    afirmar(unidad is not None, "la unidad de prueba existe en la replica")
    if unidad is None:
        return 1
    afirmar((unidad["clientecompleto"] or "").strip() == "PRUEBA",
            "y es de PRUEBA, no una unidad real", unidad["clientecompleto"])
    afirmar((unidad["despachado"] or "").strip() != "STOCK",
            "y NO esta ya en STOCK: si lo estuviera, las dos ramas darian "
            "igual y la prueba no mediria nada", unidad["despachado"])

    # ------------------------------------------------------------------
    paso("0. la guarda de destino -- que esta prueba no pueda salir afuera")

    from core import exigir_destino_local
    from modulos import push_legado, sync_legado

    afirmar(exigir_destino_local("http://127.0.0.1:8770", "prueba")
            == "http://127.0.0.1:8770",
            "localhost pasa")
    for fabrica, quien in ((push_legado.ClientePushLegadoHTTP, "push"),
                           (sync_legado.Legado, "pull")):
        for url, caso in ((None, "el que se OLVIDA de pasar base_url"),
                          ("https://claude.logautos.cl", "el que apunta mal")):
            try:
                fabrica(base_url=url)
                afirmar(False, "el cliente del {} frena a {}".format(quien, caso))
            except RuntimeError:
                afirmar(True, "el cliente del {} frena a {}".format(quien, caso))

    base_a, sin_enlace = rama("A", False, unidad)
    base_b, con_enlace = rama("B", True, unidad)

    # ------------------------------------------------------------------
    print("\n" + "=" * 62)
    paso("LA PRUEBA DE ACEPTACION: el mismo movimiento por los dos caminos")

    afirmar(base_a == base_b, "las dos ramas arrancan de la misma linea base")

    print("\n   {:<24} {:>12} {:>12}".format("", "SIN enlace", "CON enlace"))
    for k in ORDEN:
        print("   {:<24} {:>12} {:>12}{}".format(
            ROTULO[k], sin_enlace[k], con_enlace[k],
            "   <--" if sin_enlace[k] != con_enlace[k] else ""))
    print()

    afirmar(con_enlace["regla_adelante"] < sin_enlace["regla_adelante"],
            "'REGLA adelante' BAJA con el enlace: {} -> {}".format(
                sin_enlace["regla_adelante"], con_enlace["regla_adelante"]))
    afirmar(con_enlace["de_acuerdo"] > sin_enlace["de_acuerdo"],
            "'de acuerdo' SUBE: {} -> {}".format(
                sin_enlace["de_acuerdo"], con_enlace["de_acuerdo"]))
    afirmar(con_enlace["contradiccion"] == sin_enlace["contradiccion"] == 0,
            "sin contradicciones en ninguna de las dos")
    afirmar(sum(sin_enlace.values()) == sum(con_enlace.values()),
            "y el total de unidades miradas es el mismo: la unidad cambio de "
            "cajon, no aparecio una nueva")

    # ------------------------------------------------------------------
    paso("LA PDI: el mismo push contra las dos listas blancas")

    from modulos.push_legado import CAMPOS_PDI

    res_vieja, fila_vieja, sim_vieja = rama_pdi("desplegada")
    res_nueva, fila_nueva, sim_nueva = rama_pdi("con_pdi")

    print("\n   {:<20} {:>20} {:>20}".format(
        "", "lista desplegada", "lista con_pdi"))
    for col in ("fecha_pdi", "mespdinombre", "tipo_combu", "estadostock",
                "ubicacion"):
        print("   {:<20} {:>20} {:>20}".format(
            col, repr(fila_vieja.get(col)), repr(fila_nueva.get(col))))
    print("   {:<20} {:>20} {:>20}".format(
        "columnas ignoradas", sim_vieja["contadores"]["ignoradas"],
        sim_nueva["contadores"]["ignoradas"]))
    print()

    # EL PUNTO DE TODA ESTA SECCION: con la lista de HOY el push SALE BIEN y no
    # escribe nada. Ese 'ok' con cero efecto es el modo de falla que produce la
    # lista blanca, y es el que se ve semanas despues cuando alguien pregunta
    # por que no hay fechas de PDI.
    afirmar(res_vieja == "ok",
            "con la lista DESPLEGADA el push devuelve 'ok'...", res_vieja)
    afirmar(fila_vieja.get("fecha_pdi") is None,
            "...y sin embargo no escribio la fecha de PDI",
            fila_vieja.get("fecha_pdi"))
    afirmar(sim_vieja["contadores"]["ignoradas"] == len(CAMPOS_PDI),
            "se ignoraron las {} columnas de la PDI, en silencio".format(
                len(CAMPOS_PDI)), sim_vieja["ignoradas"])

    afirmar(res_nueva == "ok",
            "con la lista CON_PDI el push tambien sale bien")
    afirmar(sim_nueva["contadores"]["ignoradas"] == 0,
            "y ahi no se ignora nada", sim_nueva["ignoradas"])
    for col, esperado in (("fecha_pdi", "2026-08-27"),
                          ("mespdinombre", "Agosto 2026"),
                          ("tipo_combu", "Diesel"),
                          ("estadostock", "STOCK CON PDI")):
        afirmar(fila_nueva.get(col) == esperado,
                "{} = {!r}".format(col, esperado), fila_nueva.get(col))
    afirmar(fila_nueva.get("aceite_coco"),
            "las cuatro automaticas se sellan con la fecha del dia",
            fila_nueva.get("aceite_coco"))

    # `mespdinombre` en castellano y no 'August': el PHP lo arma con un switch
    # de doce casos y `strftime('%B')` dependeria del locale del proceso.
    afirmar("Agosto" in (fila_nueva.get("mespdinombre") or ""),
            "el mes va en castellano, no depende del locale de Railway")

    # La destructiva. Se sembro '7' justamente para poder verla.
    afirmar(fila_vieja.get("ubicacion") == "7",
            "sin la lista, `ubicacion` no se toca")
    afirmar(fila_nueva.get("ubicacion") == "",
            "con la lista, `ubicacion` queda VACIA -- borra, y es lo que hace "
            "el legado en sus 21 filas", fila_nueva.get("ubicacion"))

    # ------------------------------------------------------------------
    paso("LA PDI COMPLETA: las cuatro entradas de cola, en orden")

    from modulos import combustible, push_legado as P

    ruta = copia("pdi_cuatro")
    puerto = puerto_libre()
    srv = levantar(puerto, "con_pdi")
    global BASE_SIMULADA
    BASE_SIMULADA = "http://127.0.0.1:{}".format(puerto)
    try:
        db = abrir(ruta)
        P.asegurar_tablas(db)
        combustible.asegurar_tabla(db)
        db.commit()
        unidad = db.execute("SELECT * FROM newstocks_cidef WHERE id=?",
                            (UNIDAD_PDI,)).fetchone()
        fila = {k: unidad[k] for k in
                ("id", "updated_at", "vin", "clientecompleto", "despachado",
                 "calle", "patio", "estado_it", "observacion_it", "updated_by")}
        fila["marca"] = unidad["marca"]
        fila["modelo"] = unidad["modelo"]
        urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:{}/_sembrar".format(puerto),
            data=json.dumps(fila).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"), timeout=5).read()

        # El stock, por el pull real contra el simulado. No se siembra a mano:
        # asi la prueba cubre tambien que el pull de la entidad `completa`
        # funcione y que los valores lleguen como STRING, que es como los manda
        # CodeIgniter sobre MySQL.
        from modulos import sync_legado
        cliente_pull = sync_legado.Legado(base_url=BASE_SIMULADA)
        rp = sync_legado.sincronizar_entidad("stock_consumibles",
                                             db_path=ruta, cliente=cliente_pull)
        afirmar(rp["recibidas"] == 2, "el pull trae las 2 filas de stock",
                rp["recibidas"])
        db.close()
        db = abrir(ruta)
        stock = {f["nombre"]: f["stock"] for f in
                 db.execute("SELECT nombre, stock FROM stock_consumibles")}
        afirmar(stock == {"DIESEL": 5, "BENCINA": 563},
                "y quedan como enteros aunque viajen como texto", stock)

        # -- LA COMPUERTA, LOS DOS CAMINOS, CON EL DATO TRAIDO ---------------
        import core
        core.DB_PATH = ruta
        from flask import Flask
        app = Flask(__name__)
        with app.app_context():
            g_diesel = combustible.evaluar("Diesel")
            g_bencina = combustible.evaluar("Bencina")
        afirmar(not g_diesel["pasa"],
                "DIESEL con 5 litros NO pasa la compuerta (umbral > 20)",
                g_diesel)
        afirmar(g_bencina["pasa"] and g_bencina["consume"],
                "BENCINA con 563 si pasa y consume", g_bencina)

        # -- LAS CUATRO ENTRADAS ----------------------------------------------
        datos = {"fecha_pdi": "2026-08-27", "tipo_combu": "Bencina",
                 "bateria": "OK", "scanner": "OK", "a_c": "OK",
                 "ob_mecanica": ""}
        cur = db.execute("""
            INSERT INTO movimientos_regla
              (unidad_id, vin, paso, usuario, creado_en, estado_desde,
               estado_hacia)
            VALUES (?,?,?,?,?,?,?)""",
            (UNIDAD_PDI, unidad["vin"], "pdi", "47", "2026-08-27T17:00:00",
             unidad["despachado"], "EN ESPERA DYP CONSOLIDADO"))
        mov_id = cur.lastrowid
        id_mov = P.encolar_movimiento(db, unidad, mov_id,
                                      "EN ESPERA DYP CONSOLIDADO", "47")
        id_pdi = P.encolar_pdi(db, unidad, 1, P.campos_pdi(datos, "47"))
        id_ot = P.encolar_ot_pdi(db, unidad, 1, datos, "47", id_mov)
        with app.app_context():
            fstock = combustible.fila_de("Bencina")
        from modulos import ot_pdi
        litros = ot_pdi.litros_de("Bencina", unidad["marca"], unidad["modelo"])
        id_desc = P.encolar_descuento(db, 1, fstock["id"], litros, id_mov)
        db.commit()
        db.close()

        afirmar(None not in (id_mov, id_pdi, id_ot, id_desc),
                "se encolan las CUATRO entradas en la misma transaccion")

        # -- EL ORDEN: las dependientes no se intentan antes -------------------
        #
        # Se corren las OT y el descuento ANTES que el movimiento. Tienen que
        # quedarse en la cola: si se aplicaran, una PDI cuyo movimiento despues
        # choca 409 quedaria cobrada igual, y `orden_trabajo` es append-only.
        cli = P.ClientePushLegadoHTTP(base_url=BASE_SIMULADA)
        sim = estado_simulado(puerto)
        afirmar(len(sim["ot"]) == 0, "el legado arranca sin OT")

        # DOS VUELTAS, y esa es la forma. `procesar_pendientes` junta los ids
        # al principio, asi que las dependientes recien son elegibles en la
        # vuelta SIGUIENTE a la que resuelve al movimiento. El hilo de fondo
        # corre continuo, asi que en produccion es un ciclo de espera, no una
        # falla -- pero la prueba lo hace explicito en vez de esconderlo con un
        # bucle "hasta que no queden".
        res1 = P.procesar_pendientes(cliente=cli, db_path=ruta)
        print("   vuelta 1: {}".format(res1))
        afirmar(res1["intentados"] == 2,
                "la vuelta 1 intenta SOLO el movimiento y la PDI: las dos "
                "dependientes esperan", res1)
        res2 = P.procesar_pendientes(cliente=cli, db_path=ruta)
        print("   vuelta 2: {}".format(res2))
        sim = estado_simulado(puerto)
        afirmar(len(sim["ot"]) == 2,
                "quedaron las DOS OT -- una entrada, dos filas",
                len(sim["ot"]))
        afirmar(sim["stock"]["3"]["stock"] == 563 - litros,
                "y el stock de BENCINA bajo {} litros: {} -> {}".format(
                    litros, 563, sim["stock"]["3"]["stock"]),
                sim["stock"]["3"]["stock"])

        db = abrir(ruta)
        estados = {r["entidad"]: (r["resuelto_en"] != "", r["ultimo_error"])
                   for r in db.execute(
                       "SELECT entidad, resuelto_en, ultimo_error "
                       "  FROM sync_push_pendientes")}
        db.close()
        for entidad in ("movimientos", "pdi", "ot_pdi", "stock_consumibles"):
            resuelta, error = estados.get(entidad, (False, "?"))
            afirmar(resuelta and not error,
                    "{} quedo resuelta y sin error".format(entidad),
                    estados.get(entidad))

        # -- EL RECHAZO, que la regla 4 exige demostrar -----------------------
        for desc, cab, cuerpo, esperado in (
                ("sin API key", {}, {"fecha_pdi": "2026-08-27",
                                     "tipo_combu": "Diesel"}, 401),
                ("sin Idempotency-Key", {"X-API-Key": CLAVE},
                 {"fecha_pdi": "2026-08-27", "tipo_combu": "Diesel"}, 400),
                ("tipo_combu GASOLINA",
                 {"X-API-Key": CLAVE, "Idempotency-Key": "k1"},
                 {"fecha_pdi": "2026-08-27", "tipo_combu": "GASOLINA"}, 400),
        ):
            req = urllib.request.Request(
                "{}/api_regla/pdi/{}/ot".format(BASE_SIMULADA, UNIDAD_PDI),
                data=json.dumps(cuerpo).encode("utf-8"), method="POST",
                headers=dict({"Content-Type": "application/json"}, **cab))
            try:
                urllib.request.urlopen(req, timeout=5)
                cod = 200
            except urllib.error.HTTPError as e:
                cod = e.code
            afirmar(cod == esperado,
                    "el doble rechaza {} con {}".format(desc, esperado), cod)

        # Y el 409 de stock insuficiente, que es el camino del diesel real.
        req = urllib.request.Request(
            "{}/api_regla/stock_consumibles/2/descontar".format(BASE_SIMULADA),
            data=json.dumps({"cantidad": 15}).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "X-API-Key": CLAVE,
                     "Idempotency-Key": "k-diesel"})
        try:
            urllib.request.urlopen(req, timeout=5)
            cod = 200
        except urllib.error.HTTPError as e:
            cod = e.code
        afirmar(cod == 409,
                "y descontar 15 de un stock de 5 da 409, no un stock negativo",
                cod)
    finally:
        srv.kill()
        BASE_SIMULADA = None


    print("\n" + "=" * 62)
    if fallos:
        print("FALLARON {} comprobaciones:".format(len(fallos)))
        for f in fallos:
            print("  - {}".format(f))
        return 1
    print("el circulo cierra: STOCK baja la categoria 1, y la PDI "
          "no escribe nada sin su lista blanca")
    return 0


if __name__ == "__main__":
    sys.exit(main())
