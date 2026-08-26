#!/usr/bin/env python3
"""
scripts/probar_push.py -- prueba el push de punta a punta contra el endpoint
simulado, sobre una base descartable. NO toca local.db ni sale a produccion.

Levanta `legado_simulado.py` en un puerto libre, arma una base con lo minimo
(`newstocks_cidef` + `it_regla`), y corre los siete casos que importan:

    1. camino feliz         200, push_pendiente vuelve a 0, updated_at es el
                            del legado y la entrada queda resuelta
    2. conflicto            409, se guardan LAS DOS versiones, no se escribe
    3. falla de red         500, la entrada sigue en cola con backoff
    4. respuesta perdida    el reintento NO inventa un conflicto (idempotencia)
    5. guarda del pull      una fila con push_pendiente=1 no se sobrescribe
    6. aviso de conflicto   el 409 dispara el correo, con la unidad, el VIN,
                            los campos que difieren y desde que replica salio
    7. correo caido         si el aviso revienta, el conflicto se guarda igual

El caso 4 es el que justifica la Idempotency-Key en un PUT y es el unico que
no se puede probar contra el PHP real: hace falta un servidor que aplique el
cambio y despues corte sin responder.

    python scripts/probar_push.py
"""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

CLAVE = "clave-de-prueba"
os.environ["LEGADO_API_KEY"] = CLAVE
os.environ.setdefault("SECRET_KEY", "prueba")

UNIDAD_ID = 80405
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


def levantar(modo, puerto, updated_at="2026-08-26 11:00:00"):
    """Arranca el simulado y espera a que conteste. El sleep no es un tanteo:
    se consulta /_estado hasta que responda, con un tope."""
    proceso = subprocess.Popen(
        [sys.executable, os.path.join(RAIZ, "scripts", "legado_simulado.py"),
         "--puerto", str(puerto), "--modo", modo,
         "--sembrar-id", str(UNIDAD_ID), "--updated-at", updated_at],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=dict(os.environ))
    for _ in range(50):
        try:
            urllib.request.urlopen(
                "http://127.0.0.1:{}/_estado".format(puerto), timeout=0.5).read()
            return proceso
        except Exception:
            time.sleep(0.1)
    proceso.kill()
    raise RuntimeError("el simulado no arranco en el puerto {}".format(puerto))


def estado_simulado(puerto):
    with urllib.request.urlopen(
            "http://127.0.0.1:{}/_estado".format(puerto), timeout=2) as r:
        return json.loads(r.read().decode("utf-8"))


def base_nueva(ruta, updated_at="2026-08-26 11:00:00"):
    """La replica en chico. `newstocks_cidef` con las columnas que el push
    toca, no las 144: lo que se prueba es el mecanismo."""
    if os.path.exists(ruta):
        os.remove(ruta)
    db = sqlite3.connect(ruta)
    db.executescript("""
        CREATE TABLE newstocks_cidef (
            id INTEGER PRIMARY KEY, vin TEXT, clientecompleto TEXT,
            despachado TEXT, calle TEXT, estado_it TEXT, observacion_it TEXT,
            updated_at TEXT, updated_by INTEGER, created_at TEXT);
        CREATE TABLE it_regla (
            id INTEGER PRIMARY KEY, unidad_id INTEGER, movimiento_id INTEGER,
            vin TEXT, estado_it TEXT, observacion_it TEXT, estado_desde TEXT,
            estado_hacia TEXT, encargado TEXT, usuario TEXT, creado_en TEXT);
    """)
    db.execute("INSERT INTO newstocks_cidef "
               "(id, vin, clientecompleto, despachado, calle, updated_at) "
               "VALUES (?,?,?,?,?,?)",
               (UNIDAD_ID, "LVAV2AVB3TE316975", "CIDEF",
                "ZONA DE RECEPCION", "ZR", updated_at))
    db.commit()
    db.close()


def encolar_un_it(ruta):
    """Lo mismo que hace `taller.guardar_it`, sin Flask: INSERT + flag +
    entrada de cola, todo en un commit."""
    from modulos.push_legado import asegurar_tablas, campos_it, encolar_it
    db = sqlite3.connect(ruta)
    db.row_factory = sqlite3.Row
    try:
        asegurar_tablas(db)
        unidad = db.execute("SELECT * FROM newstocks_cidef WHERE id = ?",
                            (UNIDAD_ID,)).fetchone()
        cur = db.execute(
            "INSERT INTO it_regla (unidad_id, vin, estado_it, observacion_it, "
            "estado_desde, estado_hacia, encargado, usuario, creado_en) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (UNIDAD_ID, unidad["vin"], "PRESENTA FALLAS", "RUIDO EN EL MOTOR",
             "ZONA DE RECEPCION", "INGRESO A TALLER", "Franco", "281",
             "2026-08-26T12:00:00"))
        id_cola = encolar_it(db, unidad, cur.lastrowid,
                             campos_it("PRESENTA FALLAS", "RUIDO EN EL MOTOR",
                                       "INGRESO A TALLER", "281"))
        db.commit()
        return id_cola
    finally:
        db.close()


def leer(ruta, sql, params=()):
    db = sqlite3.connect(ruta)
    db.row_factory = sqlite3.Row
    try:
        fila = db.execute(sql, params).fetchone()
        return dict(fila) if fila else None
    finally:
        db.close()


def cliente(puerto):
    from modulos.push_legado import ClientePushLegadoHTTP
    return ClientePushLegadoHTTP(base_url="http://127.0.0.1:{}".format(puerto),
                                 api_key=CLAVE)


def main():
    import tempfile
    from modulos.push_legado import ejecutar_entrada, procesar_pendientes

    tmp = tempfile.mkdtemp(prefix="probar_push_")
    ruta = os.path.join(tmp, "prueba.db")

    # ----------------------------------------------------------------- 1
    paso("1. camino feliz")
    puerto = puerto_libre()
    base_nueva(ruta)
    id_cola = encolar_un_it(ruta)

    antes = leer(ruta, "SELECT * FROM newstocks_cidef WHERE id = ?", (UNIDAD_ID,))
    afirmar(antes["push_pendiente"] == 1,
            "encolar deja push_pendiente = 1", antes["push_pendiente"])
    cola = leer(ruta, "SELECT * FROM sync_push_pendientes WHERE id = ?", (id_cola,))
    afirmar(cola["resuelto_en"] == "", "la entrada nace pendiente")
    afirmar(len(cola["idempotency_key"]) == 36,
            "se genero idempotency_key tambien para un UPDATE",
            cola["idempotency_key"])
    afirmar(json.loads(cola["campos_json"])["calle"] == "It",
            "manda calle='It' (no 'IT': ucwords(strtolower) del PHP)",
            json.loads(cola["campos_json"])["calle"])

    srv = levantar("ok", puerto)
    try:
        resultado = ejecutar_entrada(id_cola, cliente(puerto), db_path=ruta)
        afirmar(resultado == "ok", "ejecutar_entrada devuelve 'ok'", resultado)
        fila = leer(ruta, "SELECT * FROM newstocks_cidef WHERE id = ?", (UNIDAD_ID,))
        afirmar(fila["push_pendiente"] == 0, "push_pendiente vuelve a 0")
        afirmar(fila["updated_at"].startswith("2026-08-26 12:00:"),
                "la replica guarda el updated_at del legado", fila["updated_at"])
        cola = leer(ruta, "SELECT * FROM sync_push_pendientes WHERE id = ?", (id_cola,))
        afirmar(cola["resuelto_en"] != "", "la entrada queda resuelta")
        alla = estado_simulado(puerto)["unidades"][str(UNIDAD_ID)]
        afirmar(alla.get("estado_it") == "PRESENTA FALLAS",
                "el legado recibio estado_it", alla.get("estado_it"))
        afirmar(alla.get("despachado") == "INGRESO A TALLER",
                "el legado recibio despachado", alla.get("despachado"))
        afirmar(alla.get("updated_by") == 281,
                "updated_by viaja como entero", alla.get("updated_by"))
    finally:
        srv.kill()

    # ----------------------------------------------------------------- 2
    paso("2. conflicto: el legado tiene algo mas nuevo")
    puerto = puerto_libre()
    base_nueva(ruta)
    id_cola = encolar_un_it(ruta)
    srv = levantar("conflicto", puerto)
    try:
        resultado = ejecutar_entrada(id_cola, cliente(puerto), db_path=ruta)
        afirmar(resultado == "conflicto", "devuelve 'conflicto'", resultado)
        conf = leer(ruta, "SELECT * FROM sync_conflictos ORDER BY id DESC LIMIT 1")
        afirmar(conf is not None, "se registro el conflicto")
        if conf:
            nuestra = json.loads(conf["nuestra_version_json"])
            afirmar(nuestra.get("estado_it") == "PRESENTA FALLAS",
                    "guarda NUESTRA version (si no, se pierde en el proximo pull)")
            afirmar(conf["version_legado_json"] != "{}",
                    "guarda tambien la version del legado")
        fila = leer(ruta, "SELECT * FROM newstocks_cidef WHERE id = ?", (UNIDAD_ID,))
        afirmar(fila["push_pendiente"] == 0,
                "baja el flag: no hay nada que reintentar")
        afirmar(fila["estado_it"] is None,
                "no se escribio nada local", fila["estado_it"])
        cola = leer(ruta, "SELECT * FROM sync_push_pendientes WHERE id = ?", (id_cola,))
        afirmar(cola["resuelto_en"] != "", "el conflicto cuenta como resuelto")
    finally:
        srv.kill()

    # ----------------------------------------------------------------- 3
    paso("3. falla de red: queda en cola con backoff")
    puerto = puerto_libre()
    base_nueva(ruta)
    id_cola = encolar_un_it(ruta)
    srv = levantar("caer", puerto)
    try:
        resultado = ejecutar_entrada(id_cola, cliente(puerto), db_path=ruta)
        afirmar(resultado == "error", "devuelve 'error'", resultado)
        cola = leer(ruta, "SELECT * FROM sync_push_pendientes WHERE id = ?", (id_cola,))
        afirmar(cola["resuelto_en"] == "", "la entrada SIGUE en cola")
        afirmar(cola["intentos"] == 1, "conto el intento", cola["intentos"])
        afirmar(cola["proximo_intento"] != "", "quedo agendada", cola["proximo_intento"])
        afirmar("500" in cola["ultimo_error"], "guardo el error",
                cola["ultimo_error"][:80])
        fila = leer(ruta, "SELECT * FROM newstocks_cidef WHERE id = ?", (UNIDAD_ID,))
        afirmar(fila["push_pendiente"] == 1,
                "push_pendiente SIGUE en 1: el pull no debe pisarla")

        # Con el backoff puesto, procesar_pendientes no la debe tomar todavia.
        resumen = procesar_pendientes(cliente(puerto), db_path=ruta)
        afirmar(resumen["intentados"] == 0,
                "procesar_pendientes respeta el backoff", resumen)

        segundo = ejecutar_entrada(id_cola, cliente(puerto), db_path=ruta)
        cola = leer(ruta, "SELECT * FROM sync_push_pendientes WHERE id = ?", (id_cola,))
        afirmar(cola["intentos"] == 2, "el segundo intento suma", cola["intentos"])
        del segundo
    finally:
        srv.kill()

    # ----------------------------------------------------------------- 4
    paso("4. respuesta perdida: el reintento NO inventa un conflicto")
    puerto = puerto_libre()
    base_nueva(ruta)
    id_cola = encolar_un_it(ruta)
    srv = levantar("perder", puerto)
    try:
        resultado = ejecutar_entrada(id_cola, cliente(puerto), db_path=ruta)
        afirmar(resultado == "error",
                "el primer intento se ve como fallo (la respuesta no llego)",
                resultado)
        alla = estado_simulado(puerto)["unidades"][str(UNIDAD_ID)]
        afirmar(alla.get("estado_it") == "PRESENTA FALLAS",
                "pero el legado SI lo aplico", alla.get("estado_it"))
        afirmar(not alla["updated_at"].startswith("2026-08-26 11:00"),
                "y ya avanzo su updated_at", alla["updated_at"])

        # El reintento manda el mismo legado_updated_at_conocido contra un
        # updated_at que ya avanzo. Sin idempotencia esto seria un 409.
        resultado = ejecutar_entrada(id_cola, cliente(puerto), db_path=ruta)
        afirmar(resultado == "ok",
                "el reintento resuelve OK, no en conflicto", resultado)
        contadores = estado_simulado(puerto)["contadores"]
        afirmar(contadores["idempotente"] == 1,
                "el legado lo reconocio por la Idempotency-Key", contadores)
        afirmar(contadores["conflicto"] == 0,
                "NO hubo ningun 409 contra nosotros mismos", contadores)
        conf = leer(ruta, "SELECT COUNT(*) n FROM sync_conflictos")
        afirmar(conf["n"] == 0, "no se registro ningun conflicto falso", conf)
        fila = leer(ruta, "SELECT * FROM newstocks_cidef WHERE id = ?", (UNIDAD_ID,))
        afirmar(fila["push_pendiente"] == 0, "y el flag quedo limpio")
    finally:
        srv.kill()

    # ----------------------------------------------------------------- 5
    paso("5. la guarda del pull")
    from modulos.sync_legado import _upsert
    base_nueva(ruta)
    id_cola = encolar_un_it(ruta)          # deja push_pendiente = 1
    db = sqlite3.connect(ruta)
    db.row_factory = sqlite3.Row
    try:
        columnas = [r[1] for r in db.execute("PRAGMA table_info(newstocks_cidef)")]
        afirmar("push_pendiente" in columnas,
                "asegurar_tablas agrego la columna a newstocks_cidef")
        # Lo que devolveria el legado: la version VIEJA, sin nuestro cambio.
        del_legado = [{"id": UNIDAD_ID, "despachado": "ZONA DE RECEPCION",
                       "calle": "ZR", "updated_at": "2026-08-26 11:30:00"}]
        c, a, s = _upsert(db, "newstocks_cidef", del_legado, columnas)
        afirmar((c, a, s) == (0, 0, 1), "el pull la saltea", (c, a, s))
        fila = db.execute("SELECT * FROM newstocks_cidef WHERE id = ?",
                          (UNIDAD_ID,)).fetchone()
        afirmar(fila["updated_at"] == "2026-08-26 11:00:00",
                "no le piso el updated_at", fila["updated_at"])

        db.execute("UPDATE newstocks_cidef SET push_pendiente = 0 WHERE id = ?",
                   (UNIDAD_ID,))
        c, a, s = _upsert(db, "newstocks_cidef", del_legado, columnas)
        afirmar((c, a, s) == (0, 1, 0),
                "con el flag limpio la actualiza normal", (c, a, s))
        db.commit()
    finally:
        db.close()
    del id_cola

    # ----------------------------------------------------------------- 6
    paso("6. el conflicto avisa por correo")
    from modulos import correo, push_legado

    enviados = []
    real_mandar, real_hilo = correo.mandar, correo.en_segundo_plano
    correo.mandar = lambda dest, asunto, texto, html, adj=(): (
        enviados.append({"dest": dest, "asunto": asunto, "texto": texto,
                         "html": html}) or ("enviado", "prueba"))
    # Sincronico para que la prueba sea determinista: lo que se verifica es el
    # contenido del aviso, no que threading funcione.
    correo.en_segundo_plano = lambda f, *a: f(*a)
    os.environ["SYNC_CONFLICTOS_DESTINATARIOS"] = "jefe@logautos.cl, otro@logautos.cl"
    os.environ["REGLA_ORIGEN"] = "notebook (prueba)"

    puerto = puerto_libre()
    base_nueva(ruta)
    id_cola = encolar_un_it(ruta)
    srv = levantar("conflicto", puerto)
    try:
        resultado = ejecutar_entrada(id_cola, cliente(puerto), db_path=ruta)
        afirmar(resultado == "conflicto", "sigue devolviendo 'conflicto'", resultado)
        afirmar(len(enviados) == 1, "se mando UN aviso", len(enviados))
        if enviados:
            e = enviados[0]
            afirmar(e["dest"] == ["jefe@logautos.cl", "otro@logautos.cl"],
                    "a los destinatarios de SYNC_CONFLICTOS_DESTINATARIOS", e["dest"])
            afirmar(str(UNIDAD_ID) in e["asunto"] and "Conflicto" in e["asunto"],
                    "el asunto nombra la unidad", e["asunto"])
            afirmar("LVAV2AVB3TE316975" in e["asunto"],
                    "y el VIN", e["asunto"])
            afirmar("notebook (prueba)" in e["html"],
                    "dice desde que replica salio el push")
            afirmar("PRESENTA FALLAS" in e["html"],
                    "muestra lo que se quiso escribir")
            afirmar("DIFIERE" in e["html"],
                    "marca los campos que difieren")
            afirmar("estado_it" in e["texto"] and "PRESENTA FALLAS" in e["texto"],
                    "la version de texto tambien lleva el detalle")
        conf = leer(ruta, "SELECT COUNT(*) n FROM sync_conflictos")
        afirmar(conf["n"] == 1, "y el conflicto quedo guardado igual", conf)
    finally:
        srv.kill()

    # -- el diff, aparte: es la parte con logica -------------------------
    filas = push_legado.diferencias(
        {"estado_it": "OK", "despachado": "INGRESO A TALLER", "updated_by": 0,
         "observacion_it": ""},
        {"estado_it": "PRESENTA FALLAS", "despachado": "INGRESO A TALLER",
         "updated_by": "0"})
    por_campo = {f[0]: f[3] for f in filas}
    afirmar(por_campo["estado_it"] is True, "detecta el campo que cambio")
    afirmar(por_campo["despachado"] is False, "no marca el que es igual")
    afirmar("updated_by" not in por_campo,
            "updated_by no entra en la tabla: difiere siempre, es metadato")
    afirmar(por_campo["observacion_it"] is False,
            "un campo que el legado no devolvio no se marca como diferencia")
    afirmar(len(filas) == 3, "lista los demas campos que viajaron", len(filas))
    afirmar(push_legado._comparable(0) == push_legado._comparable("0"),
            "0 y '0' se comparan iguales (el legado devuelve texto)")

    # ----------------------------------------------------------------- 7
    paso("7. si el correo falla, el conflicto se registra igual")

    def _explota(*a, **k):
        raise RuntimeError("Resend caido")
    correo.destinatarios = _explota

    puerto = puerto_libre()
    base_nueva(ruta)
    id_cola = encolar_un_it(ruta)
    srv = levantar("conflicto", puerto)
    try:
        resultado = ejecutar_entrada(id_cola, cliente(puerto), db_path=ruta)
        afirmar(resultado == "conflicto",
                "devuelve 'conflicto' aunque el correo reviente", resultado)
        conf = leer(ruta, "SELECT * FROM sync_conflictos ORDER BY id DESC LIMIT 1")
        afirmar(conf is not None, "el conflicto quedo guardado")
        fila = leer(ruta, "SELECT push_pendiente FROM newstocks_cidef WHERE id = ?",
                    (UNIDAD_ID,))
        afirmar(fila["push_pendiente"] == 0, "y el flag se limpio")
    finally:
        srv.kill()
        correo.mandar, correo.en_segundo_plano = real_mandar, real_hilo
        correo.destinatarios = push_legado.correo.destinatarios

    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    if fallos:
        print("FALLARON {} comprobaciones:".format(len(fallos)))
        for f in fallos:
            print("  - {}".format(f))
        return 1
    print("las 7 pruebas pasaron")
    return 0


if __name__ == "__main__":
    sys.exit(main())
