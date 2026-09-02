#!/usr/bin/env python3
"""
scripts/verificar_check_mecanico_produccion.py -- el push REAL del check list
mecanico contra claude.logautos.cl, y despues ir a MIRAR la fila.

    LEGADO_API_KEY=... python scripts/verificar_check_mecanico_produccion.py
    LEGADO_API_KEY=... python scripts/verificar_check_mecanico_produccion.py --escribir

SIN `--escribir` NO ESCRIBE NADA en el legado: corre las sondas de solo lectura
y muestra exactamente que se mandaria. Con `--escribir` crea una fila de verdad
en `check_list_mecanica`.

POR QUE ESTE SCRIPT NO USA `requests` PELADO
===========================================
`claude.logautos.cl` corta la conexion cuando el User-Agent es el de
`requests`: no responde 403, cierra el socket. Del lado de Python sale un
`ConnectionError` que se lee igual que "no hay red" -- y ese error ya hizo
diagnosticar mal una vez, el 2026-09-02.

Todas las llamadas van por `SESION`, que lleva el `USER_AGENT` importado de
`modulos/sync_legado`, el mismo que mandan el cliente del pull y el del push.
Se importa y no se copia: dos definiciones de la misma cadena se separan.

POR QUE NO ALCANZAN LAS SUITES
==============================
`probar_circulo_mecanica.py` corre contra el legado simulado, y el simulado lo
escribimos nosotros. Ya nos mintio una vez: devolvia 200 con un `updated_at`
inventado donde el bloque I devuelve 201 sin ninguno, y eso tapaba dos bugs de
Python. Un doble mas generoso que el original es un sello de goma (regla 4), y
la unica forma de saber que el doble no miente es preguntarle al original.

QUE SE AFIRMA, Y CONTRA QUE
===========================
Las tres cosas se afirman leyendo la RESPUESTA DEL LEGADO, no la cola. Que el
push diga 'ok' significa que hubo un 2xx; una columna fuera de la lista blanca
tambien da 2xx.

  A. LAS 82 COLUMNAS DEL PASO 1 LLEGARON. El 201 de `crear_fila` devuelve
     `ignoradas`, que es exactamente lo que el bloque I agrego para que este
     silencio se pueda afirmar en vez de suponer. Tiene que venir VACIO.

  B. LAS DOS FALLAS SE ACUMULARON, EN DOS LLAMADAS SEPARADAS. Es la propiedad
     que se esta probando, asi que tiene que haber dos vueltas: una llamada con
     dos fallas no probaria nada. Las tres listas -- `observacion`,
     `modalidad`, `link_unidades` -- con dos elementos cada una y el n-esimo de
     cada una correspondiendose.

  C. `contador` DICE 2 Y NO 1. O sea que el endpoint lo INCREMENTO
     (`COALESCE(contador,0) + ?`) en vez de escribir lo que mando Python. Si
     dijera 1, cada falla estaria pisando el contador con el valor calculado
     contra NUESTRA fila, y las fotos siguientes se llamarian igual que las
     anteriores.

COMO SE MIRA LA FILA
====================
Con `GET /api_regla/check_list_mecanica/<id>`, que es el BLOQUE L y existe
justamente para esto. Antes del bloque L no habia forma de leer lo que los
bloques G-K escriben, y la verificacion terminaba en "devolvio 201" mas un
favor: alguien entrando a phpMyAdmin. Una verificacion que depende de un favor
se deja de hacer.

Si el bloque L no esta desplegado, el script lo dice y deja escrita la consulta
para correr a mano -- no da por buena la fila que no pudo mirar.

LO QUE ESTA CORRIDA DEJA EN PRODUCCION
======================================
Una fila en `check_list_mecanica` para la unidad de PRUEBA, y
`fecha_check_list_mecanica` escrita en esa unidad. `check_list_mecanica` es
append-only: la fila NO se puede deshacer desde la aplicacion. Por eso la
unidad tiene que ser una de PRUEBA y el script lo exige.

LAS DOS FOTOS VAN A QUEDAR CON UNA URL QUE NO ABRE, y esta dicho a proposito:
los archivos estan en el disco de esta maquina, no en el volumen de Railway,
asi que la URL apunta a un lugar donde el archivo no esta. Para lo que se
verifica aca -- que las tres listas quedan alineadas -- la URL es una cadena
opaca y alcanza. La primera foto que de verdad se vea va a ser la del primer
check list cargado DESDE Railway.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SECRET_KEY", "verificacion")

try:
    import requests
except ImportError:                              # pragma: no cover
    print("falta la dependencia `requests`")
    sys.exit(2)

from modulos.sync_legado import USER_AGENT       # noqa: E402

# ---------------------------------------------------------------------------
# LA SESION. Todas las llamadas de este script pasan por aca.
# ---------------------------------------------------------------------------
#
# `claude.logautos.cl` CORTA LA CONEXION si el User-Agent es el de `requests`.
# No responde 403 ni 429: cierra el socket, y del lado de Python sale un
# `ConnectionError: RemoteDisconnected('Remote end closed connection without
# response')` -- que se lee igual que "no hay red" y por eso engaña.
#
# El cliente de verdad (`ClientePushLegadoHTTP._cabeceras`) manda el
# User-Agent y por eso los push funcionan. Un script de verificacion que use
# `requests` pelado esta probando contra un servidor que le cuelga el telefono:
# no verifica nada, y peor, informa un problema que no existe.
#
# EL User-Agent SE IMPORTA, NO SE COPIA. Vive en `modulos/sync_legado.py` --
# una sola definicion, con su variable de entorno -- y lo usan el cliente del
# pull y el del push. Escribir la cadena de nuevo aca serian dos lugares que se
# separan el dia que alguien cambie uno.
SESION = requests.Session()
SESION.headers["User-Agent"] = USER_AGENT

FALLOS = []


def afirmar(condicion, que, detalle=""):
    print("   {}  {}".format("ok  " if condicion else "FALLA", que))
    if detalle:
        print("          {}".format(detalle))
    if not condicion:
        FALLOS.append(que)


# ---------------------------------------------------------------------------
# La unidad de prueba
# ---------------------------------------------------------------------------

def _exigir_unidad_de_prueba(db, unidad_id):
    """La fila tiene que existir Y tiene que ser de PRUEBA.

    No es una formalidad: esta corrida INSERTA una fila append-only en
    `check_list_mecanica` de produccion y le escribe
    `fecha_check_list_mecanica` a la unidad. En una unidad real eso le dice al
    sistema viejo que el auto tiene un check list mecanico que nadie hizo."""
    fila = db.execute(
        "SELECT id, vin, patente, clientecompleto, marca, modelo, color, "
        "       despachado, fecha_check_list_mecanica, updated_at "
        "  FROM newstocks_cidef WHERE id = ?", (unidad_id,)).fetchone()
    if fila is None:
        raise SystemExit("la unidad {} no existe en la replica".format(unidad_id))

    marcas = " ".join(str(fila[c] or "") for c in
                      ("vin", "patente", "clientecompleto", "marca")).upper()
    if "PRUEB" not in marcas and "FRANCO" not in marcas:
        raise SystemExit(
            "NEGADO: la unidad {} no parece de prueba.\n"
            "  vin={!r} patente={!r} cliente={!r} marca={!r}\n"
            "  Esta corrida INSERTA una fila append-only en produccion y le\n"
            "  marca `fecha_check_list_mecanica` a la unidad. En una unidad\n"
            "  real eso le dice al sistema viejo que tiene un check list que\n"
            "  nadie hizo, y la fila no se puede deshacer desde la aplicacion."
            .format(unidad_id, fila["vin"], fila["patente"],
                    fila["clientecompleto"], fila["marca"]))
    return fila


# ---------------------------------------------------------------------------
# Las sondas de solo lectura
# ---------------------------------------------------------------------------

def sondas(base, clave):
    """Cortan ANTES de cualquier escritura. Se pueden correr cuantas veces sea."""
    print("\nSONDAS (no escriben)")
    cab = {"X-API-Key": clave, "Content-Type": "application/json"}

    r = SESION.post(base + "/api_regla/check_list_mecanica",
                    json={"vin": "X"}, timeout=20)
    afirmar(r.status_code == 401,
            "POST check_list_mecanica sin clave -> 401",
            "HTTP {} {}".format(r.status_code, (r.text or "")[:90]))

    r = SESION.post(base + "/api_regla/check_list_mecanica",
                    json={"vin": "X"}, headers=cab, timeout=20)
    afirmar(r.status_code == 400 and "idem" in (r.text or "").lower(),
            "POST con clave y sin Idempotency-Key -> 400 que la exige",
            "HTTP {} {}".format(r.status_code, (r.text or "")[:110]))

    # Un PUT de falla sobre una fila que no existe. No escribe: el endpoint
    # busca la fila y corta.
    r = SESION.put(base + "/api_regla/check_list_mecanica_falla/999999999",
                   json={"observacion": "SONDA", "legado_updated_at_conocido": ""},
                   headers=dict(cab, **{"Idempotency-Key": str(uuid.uuid4())}),
                   timeout=20)
    afirmar(r.status_code == 404,
            "PUT falla sobre una fila inexistente -> 404",
            "HTTP {} {}".format(r.status_code, (r.text or "")[:110]))
    if r.status_code == 405:
        print("          OJO: 405 significa que la RUTA quedo con el metodo "
              "equivocado.\n          Tiene que ser "
              "$route['api_regla/check_list_mecanica_falla/(:num)']['PUT'].")


# ---------------------------------------------------------------------------
# La escritura real
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, default=66505,
                    help="unidad de PRUEBA sobre la que correr")
    ap.add_argument("--escribir", action="store_true",
                    help="hace el push de verdad; sin esto solo sondas")
    ap.add_argument("--base", default=os.environ.get(
        "LEGADO_BASE_URL", "https://claude.logautos.cl"))
    # `.invalid` es un TLD reservado que NO puede resolver nunca (RFC 2606).
    # Es a proposito: las fotos de esta corrida estan en el disco de esta
    # maquina, no en el volumen de Railway, asi que CUALQUIER URL que mandemos
    # va a estar muerta. Con un host inventado que parece real, la de esta
    # prueba seria indistinguible de una rota de verdad; con `.invalid`, quien
    # la vea en `link_unidades` sabe de una que es una fila de verificacion.
    ap.add_argument("--publico",
                    default=os.environ.get("PUBLIC_BASE_URL")
                            or "https://verificacion.invalid",
                    help="base publica de REGLA para las URL de foto")
    args = ap.parse_args()

    clave = os.environ.get("LEGADO_API_KEY", "").strip()
    if not clave:
        raise SystemExit("falta LEGADO_API_KEY")

    base = args.base.rstrip("/")
    print("legado : {}".format(base))
    print("unidad : {}".format(args.id))

    real = os.path.join(RAIZ, "local.db")
    db = sqlite3.connect(real)
    db.row_factory = sqlite3.Row
    unidad = _exigir_unidad_de_prueba(db, args.id)
    db.close()
    print("         vin={} patente={} cliente={}".format(
        unidad["vin"], unidad["patente"], unidad["clientecompleto"]))
    print("         fecha_check_list_mecanica actual: {!r}".format(
        unidad["fecha_check_list_mecanica"]))

    sondas(base, clave)

    if not args.escribir:
        print("\n" + "=" * 64)
        print("NO SE ESCRIBIO NADA. Para el push real, agrega --escribir")
        return 1 if FALLOS else 0

    # -- LA COPIA. Nunca se trabaja sobre la replica real -------------------
    #
    # Este script no se llama `probar_*`, asi que `exigir_replica_de_prueba` no
    # se activa sola. La regla de habito de CLAUDE.md aplica igual: se copia.
    tmp = tempfile.mkdtemp(prefix="regla_verif_")
    copia = os.path.join(tmp, "prueba.db")
    shutil.copy(real, copia)
    os.environ["DB_PATH"] = copia
    os.environ["DATA_DIR"] = tmp

    # PONER LA VARIABLE NO ALCANZA. `core.DB_PATH` se evalua al importar
    # `core`, y este script ya lo importo arriba, al traer `USER_AGENT` de
    # `sync_legado`. La primera version confiaba en el entorno: copio la
    # replica, apunto DB_PATH a la copia, y escribio igual en la REAL -- y
    # ademas empujo esa fila a produccion.
    #
    # Se asigna a mano, igual que hace `probar_circulo.py`, y se PRENDE la
    # guarda de replica para este proceso: de ahi en adelante, cualquier
    # intento de abrir la base de verdad revienta en vez de escribir.
    import core
    core.DB_PATH = copia
    core.DATA_DIR = tmp
    os.environ["REGLA_REPLICA_PROTEGIDA"] = "1"
    assert core._normal(core.DB_PATH) == core._normal(copia)
    os.environ["LEGADO_BASE_URL"] = base
    os.environ["PUSH_LEGADO_ACTIVO"] = "0"       # se dispara a mano
    os.environ["PUBLIC_BASE_URL"] = args.publico
    print("base publica de fotos: {}".format(args.publico))
    print("\ncopia de trabajo: {}".format(copia))

    from app import crear_app
    from core import conectar_db
    from modulos.push_legado import procesar_pendientes

    # `DATA_DIR` no solo se congela: se PROPAGA. `fotos_publicas` lo copia en
    # su `RAIZ` al importarse y `check_list_mecanica` arma su `CARPETA_FOTOS`
    # con el, asi que reasignar `core.DATA_DIR` despues no alcanza para los
    # modulos que ya lo leyeron.
    from modulos import fotos_publicas
    from modulos import check_list_mecanica as _clm
    fotos_publicas.RAIZ = tmp
    _clm.CARPETA_FOTOS = os.path.join(tmp, _clm.SUBCARPETA)

    # -- ARMAR EL LOCKING --------------------------------------------------
    #
    # El PUT de `fecha_check_list_mecanica` hace locking optimista contra el
    # `updated_at` de PRODUCCION. Si la copia tiene uno viejo, el push da 409
    # y no se prueba nada. Se lee el de produccion SIN escribir, con la sonda
    # del timestamp imposible: el 409 devuelve `datos_actuales` y `updated_at`.
    print("\nARMANDO EL LOCKING (lee el updated_at de produccion sin escribir)")
    r = SESION.put(
        "{}/api_regla/unidades/{}".format(base, args.id),
        json={"legado_updated_at_conocido": "2000-01-01 00:00:00"},
        headers={"X-API-Key": clave, "Content-Type": "application/json"},
        timeout=20)
    if r.status_code == 409:
        marca = (r.json() or {}).get("updated_at") or ""
        print("   produccion dice updated_at = {!r}".format(marca))
        d = conectar_db(copia)
        d.execute("UPDATE newstocks_cidef SET updated_at = ? WHERE id = ?",
                  (marca, args.id))
        d.commit()
        d.close()
        afirmar(bool(marca), "se leyo el updated_at de produccion sin escribir")
    else:
        afirmar(False, "la sonda de locking devolvio 409",
                "HTTP {} {}".format(r.status_code, (r.text or "")[:140]))
        print("   sin el updated_at real el push daria 409. Se corta.")
        return 1

    # -- EL CHECK LIST, POR LA PANTALLA ------------------------------------
    app = crear_app()
    app.config["TESTING"] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s["isLoggedIn"] = True
        s["name"] = "Verificacion REGLA"
        s["userId"] = 1
        s["role"] = "Admin"

    from modulos.catalogo_mecanica import CAMPOS
    datos = {"guia": "VERIF-{}".format(uuid.uuid4().hex[:6]),
             "fecha_ingreso": "2026-09-02", "kilometraje": "12345",
             "estado_carflex": "Usado", "estanque": "7",
             "obs_general": "PRUEBA DE VERIFICACION DEL PUSH - REGLA"}
    for col, _e, tipo, ops in CAMPOS:
        datos[col] = (ops[0] if tipo == "opcion" else
                      "12,60" if tipo == "bateria" else
                      "40" if tipo == "porcentaje" else "2")

    print("\nPASO 1 -- se carga por la pantalla y se empuja")
    r = c.post("/movimientos/{}/check-list-mecanico".format(args.id), data=datos)
    if r.status_code != 302:
        afirmar(False, "la pantalla guardo el check list",
                "HTTP {}".format(r.status_code))
        return 1
    id_check = int(r.headers["Location"].rstrip("/").split("/")[-2])

    procesar_pendientes()

    d = conectar_db(copia)
    entrada = d.execute(
        "SELECT id, resuelto_en, ultimo_error, respuesta_json "
        "  FROM sync_push_pendientes WHERE entidad = 'check_list_mecanica' "
        " ORDER BY id DESC LIMIT 1").fetchone() if _tiene_respuesta(d) else None
    fila_local = d.execute(
        "SELECT legado_id FROM check_list_mecanica_regla WHERE id = ?",
        (id_check,)).fetchone()
    d.close()

    legado_id = fila_local["legado_id"] if fila_local else None
    afirmar(bool(legado_id),
            "A1. el legado creo la fila y devolvio su id",
            "id en el legado: {}".format(legado_id))
    if not legado_id:
        print("   sin id no se puede seguir. Mira el log de arriba.")
        return 1

    # A. `ignoradas` vacio -- se relee mandando la MISMA Idempotency-Key, que
    # responde el cuerpo guardado sin volver a insertar.
    print("\nA. LAS 82 COLUMNAS")
    ignoradas = _ignoradas_del_push(copia)
    afirmar(ignoradas == [],
            "el 201 devolvio `ignoradas` VACIO",
            "ignoradas: {}".format(ignoradas if ignoradas else "(ninguna)"))

    # -- LAS DOS FALLAS, EN DOS LLAMADAS SEPARADAS -------------------------
    print("\nB. DOS FALLAS, EN DOS VUELTAS SEPARADAS")
    import io as _io
    for n, (texto, modalidad) in enumerate(
            (("EXTINTOR VENCIDO", "LEVE"), ("TAPIZ MANCHADO", "GRAVE")), 1):
        r = c.post("/movimientos/check-list-mecanico/{}/fallas".format(id_check),
                   data={"falla": texto, "modalidad": modalidad,
                         "foto": (_io.BytesIO(b"\xff\xd8\xff\xe0jpeg"),
                                  "verif{}.jpg".format(n))},
                   content_type="multipart/form-data")
        afirmar(r.status_code == 302, "vuelta {}: se cargo {}".format(n, texto))
        procesar_pendientes()
        print("      -- push de la vuelta {} hecho".format(n))

    # -- MIRAR LA FILA -----------------------------------------------------
    print("\nMIRANDO LA FILA EN EL LEGADO")
    fila = _leer_fila(base, clave, legado_id)
    if fila is None:
        print("   el endpoint no devuelve la fila. Consulta a mano:")
        print("      SELECT observacion, modalidad, link_unidades, contador")
        print("        FROM check_list_mecanica WHERE id = {};".format(legado_id))
        return 1

    for col in ("observacion", "modalidad", "link_unidades", "contador"):
        print("      {:<14} {}".format(col, fila.get(col)))

    obs = (fila.get("observacion") or "").split(" | ")
    mod = (fila.get("modalidad") or "").split(" | ")
    lnk = (fila.get("link_unidades") or "").split(" | ")
    afirmar(obs == ["EXTINTOR VENCIDO", "TAPIZ MANCHADO"],
            "B1. las dos fallas quedaron ACUMULADAS, no pisadas")
    afirmar(mod == ["LEVE", "GRAVE"],
            "B2. la modalidad quedo alineada: la falla n con su modalidad n")
    afirmar(len(lnk) == 2 and all(x.strip() for x in lnk),
            "B3. las dos fotos, una por falla")
    afirmar(len(obs) == len(mod) == len(lnk) == 2,
            "B4. las TRES listas tienen dos elementos")
    afirmar(str(fila.get("contador")) == "2",
            "C. `contador` dice 2 -- se incremento del lado del servidor",
            "si dijera 1, Python le estaria escribiendo el total")

    print("\n" + "=" * 64)
    if FALLOS:
        print("FALLARON {}:".format(len(FALLOS)))
        for f in FALLOS:
            print("   - {}".format(f))
        return 1
    print("LOS BLOQUES G-K QUEDAN PROBADOS CONTRA EL ENDPOINT REAL")
    print("fila creada en produccion: check_list_mecanica.id = {}".format(legado_id))
    return 0


def _tiene_respuesta(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(sync_push_pendientes)")]
    return "respuesta_json" in cols


def _ignoradas_del_push(copia):
    """Lo que el 201 dijo que ignoro.

    Si la cola guarda la respuesta, sale de ahi. Si no, sale del log -- y en
    ese caso se devuelve None para que la afirmacion lo diga en vez de dar un
    falso ok."""
    d = sqlite3.connect(copia)
    d.row_factory = sqlite3.Row
    try:
        cols = [r[1] for r in d.execute("PRAGMA table_info(sync_push_pendientes)")]
        if "respuesta_json" not in cols:
            return None
        fila = d.execute(
            "SELECT respuesta_json FROM sync_push_pendientes "
            " WHERE entidad = 'check_list_mecanica' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not fila or not fila["respuesta_json"]:
            return None
        return (json.loads(fila["respuesta_json"]) or {}).get("ignoradas")
    finally:
        d.close()


def _leer_fila(base, clave, legado_id):
    """La fila del check list, como la ve el legado (bloque L).

    Devuelve None si el endpoint no esta -- y entonces el script NO afirma
    nada sobre la fila: imprime la consulta para correr a mano. Una
    verificacion que no pudo mirar tiene que decirlo, no aprobar por
    ausencia."""
    for ruta in ("/api_regla/check_list_mecanica/{}".format(legado_id),
                 "/api_regla/leer_fila/check_list_mecanica/{}".format(legado_id)):
        try:
            r = SESION.get(base + ruta, headers={"X-API-Key": clave},
                           timeout=20)
        except Exception:                        # noqa: BLE001
            continue
        if r.status_code == 200:
            try:
                cuerpo = r.json()
            except ValueError:
                continue
            if isinstance(cuerpo, dict) and cuerpo.get("fila"):
                return cuerpo["fila"]
            if isinstance(cuerpo, dict) and "contador" in cuerpo:
                return cuerpo
    return None


if __name__ == "__main__":
    sys.exit(main())
