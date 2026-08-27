#!/usr/bin/env python3
"""
scripts/verificar_push_produccion.py -- comprueba que el PUT quedo bien
desplegado en claude.logautos.cl SIN ESCRIBIR NI UN BYTE en la base.

Va entre "subi el archivo" y "hacer un IT de verdad". Cada sonda esta elegida
para cortar ANTES del UPDATE, asi que se puede correr contra produccion
cuantas veces haga falta:

  0. GET del pull          que el archivo nuevo no rompio lo que ya andaba.
                           Es una lectura, y ademas la regresion mas probable:
                           se reemplazo el controlador entero.
  1. PUT sin API key       401. Si responde 404, la RUTA no esta puesta -- que
                           es el modo de fallar mas probable de este despliegue.
  2. PUT a un id que no    404. Prueba que la key pasa, que la entidad se
     existe                reconoce y que llega hasta la busqueda de la fila.
  3. PUT a una ruta que    que el CLIENTE lo rechace. Ojo: el legado tiene
     no existe             404_override, asi que una ruta inexistente responde
                           200 con cuerpo vacio, no 404. Lo que se comprueba es
                           que push_legado no lo confunda con un push exitoso.
  4. PUT a una unidad      409 o 400, nunca 200. Manda un
     real, timestamp       legado_updated_at_conocido del ano 2000 y NINGUN
     viejo, sin campos     campo actualizable:
                             - si la fila tiene updated_at -> 409 (el locking
                               corta antes de escribir)
                             - si no lo tiene -> 400 "ningun campo actualizable"
                           Las dos ramas terminan sin UPDATE. Por eso es segura.

La 4 es la que de verdad importa: es la unica que prueba el locking optimista
contra la base real, y lo hace sin tocarla.

    LEGADO_API_KEY=... python scripts/verificar_push_produccion.py
    LEGADO_API_KEY=... python scripts/verificar_push_produccion.py --id 92095
"""

import argparse
import json
import os
import sqlite3
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

try:
    import requests
except ImportError:                              # pragma: no cover
    print("falta la dependencia `requests`")
    sys.exit(2)

from core import DB_PATH                         # noqa: E402
from modulos.sync_legado import BASE_URL_DEFECTO, USER_AGENT  # noqa: E402

fallos = []


def sonda(numero, titulo, esperado, respuesta, condicion, detalle=""):
    marca = "  ok " if condicion else " FALLA"
    print("{} {}. {}".format(marca, numero, titulo))
    print("        esperado: {}".format(esperado))
    cuerpo = (respuesta.text or "")[:200] if respuesta is not None else "(sin respuesta)"
    codigo = respuesta.status_code if respuesta is not None else "-"
    print("        recibido: HTTP {}  {}".format(codigo, cuerpo))
    if detalle:
        print("        {}".format(detalle))
    if not condicion:
        fallos.append("{}. {}".format(numero, titulo))


def _unidad_de_prueba(id_pedido):
    """Un id de unidad que exista. Se toma de la replica local: es una lectura
    de nuestra base, no del legado, y solo hace falta el numero."""
    if id_pedido:
        return int(id_pedido)
    db = sqlite3.connect(DB_PATH)
    try:
        fila = db.execute(
            "SELECT id FROM newstocks_cidef "
            " WHERE vin <> '' AND updated_at NOT IN ('', '0000-00-00 00:00:00') "
            " ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        db.close()
    if fila is None:
        raise RuntimeError("no hay unidades en la replica local: pasa --id")
    return fila[0]


def _confirmar_unidad(unidad_id):
    """AFIRMA que la fila local de ese id existe y es la de ese id.

    Un VIN puede tener varias pasadas en `newstocks_cidef` -- 71.546 filas para
    61.447 VIN --, asi que una consulta por VIN devuelve varias. Leer la
    primera y darla por la pedida ya produjo un commit con un bug adentro. Un
    script de verificacion afirma que lo que leyo es lo que pidio ANTES de
    comparar nada."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        f = db.execute("SELECT id, vin, updated_at FROM newstocks_cidef "
                       "WHERE id = ?", (unidad_id,)).fetchone()
    finally:
        db.close()
    assert f is not None, "la unidad {} no esta en la replica".format(unidad_id)
    assert f["id"] == unidad_id, (
        "se leyo id={} y se pidio id={}".format(f["id"], unidad_id))
    return dict(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Humo del PUT contra produccion")
    ap.add_argument("--base", default=BASE_URL_DEFECTO)
    ap.add_argument("--id", type=int, default=None,
                    help="id de unidad para la sonda 4 (por defecto, una de la replica)")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args(argv)

    clave = os.environ.get("LEGADO_API_KEY", "").strip()
    if not clave:
        print("falta LEGADO_API_KEY en el entorno")
        return 2

    base = args.base.rstrip("/")
    unidad_id = _unidad_de_prueba(args.id)
    local = _confirmar_unidad(unidad_id)   # afirma antes de comparar nada
    ua = {"User-Agent": USER_AGENT}               # nunca python-requests
    con_clave = dict(ua, **{"X-API-Key": clave})

    print("base   : {}".format(base))
    print("unidad : {} vin={} (solo para la sonda 4; no se escribe)"
          .format(unidad_id, local["vin"]))
    print("NINGUNA de estas sondas escribe en la base.\n")

    # -- 0 -----------------------------------------------------------------
    r = requests.get(base + "/api_regla/cambios/unidades",
                     params={"desde": "", "limite": 1, "pagina": 1},
                     headers=con_clave, timeout=args.timeout)
    ok = False
    detalle = ""
    if r.status_code == 200:
        try:
            datos = r.json()
            ok = "filas" in datos and "hasta" in datos
            detalle = "hasta={!r}  filas={}".format(
                datos.get("hasta"), len(datos.get("filas") or []))
        except ValueError:
            detalle = "respondio 200 pero no es JSON"
    sonda(0, "el GET del pull sigue andando", "HTTP 200 con filas/hasta",
          r, ok, detalle)

    # -- 1 -----------------------------------------------------------------
    url = "{}/api_regla/unidades/{}".format(base, unidad_id)
    r = requests.put(url, json={"estado_it": "NO DEBE ESCRIBIRSE"},
                     headers=ua, timeout=args.timeout)
    sonda(1, "PUT sin API key", "HTTP 401", r, r.status_code == 401,
          "un 404 aca significa que FALTA LA RUTA en routes.php"
          if r.status_code == 404 else
          ("un 405 significa que el hosting bloquea el verbo PUT"
           if r.status_code == 405 else ""))

    # -- 2 -----------------------------------------------------------------
    r = requests.put("{}/api_regla/unidades/999999999".format(base),
                     json={"estado_it": "NO DEBE ESCRIBIRSE",
                           "legado_updated_at_conocido": ""},
                     headers=con_clave, timeout=args.timeout)
    sonda(2, "PUT a un id inexistente", "HTTP 404 'unidad no encontrada'",
          r, r.status_code == 404 and "no encontrada" in (r.text or ""))

    # -- 3 -----------------------------------------------------------------
    #
    # OJO: esta sonda esperaba un 404 y estaba equivocada. El legado tiene
    # `$route['404_override'] = 'error'` en routes.php, asi que una ruta que no
    # resuelve NO da 404: cae en el controlador Error, que intenta pintar el
    # login y responde **HTTP 200, text/html, cuerpo vacio**. Verificado el
    # 2026-08-26 contra produccion con tres rutas inexistentes distintas.
    #
    # Es comportamiento del sitio entero y no algo que este despliegue pueda
    # arreglar desde el controlador de la API. Asi que la sonda ya no mide el
    # codigo de estado -- mide lo unico que de verdad importa: que NUESTRO
    # cliente no confunda esa respuesta con un push exitoso.
    #
    # Se prueba con el cliente de verdad, no con requests a mano: lo que hay
    # que verificar es la decision que toma el codigo que va a correr.
    from modulos.push_legado import ClientePushLegadoHTTP, PushLegadoError
    cli = ClientePushLegadoHTTP(base_url=base, api_key=clave,
                                timeout=args.timeout)
    try:
        devuelto = cli.actualizar("inventadas", 1, {}, "", idem_key="")
        ok3, detalle3 = False, ("el cliente ACEPTO la respuesta: {!r}. Un push "
                                "mal ruteado se estaria dando por escrito"
                                .format(devuelto))
    except PushLegadoError as e:
        ok3, detalle3 = True, "el cliente lo rechazo: {}".format(str(e)[:150])
    print("{} 3. una ruta que no existe no se confunde con exito".format(
        "  ok " if ok3 else " FALLA"))
    print("        esperado: PushLegadoError (el sitio responde 200 vacio)")
    print("        {}".format(detalle3))
    if not ok3:
        fallos.append("3. una ruta que no existe no se confunde con exito")

    # -- 4 -----------------------------------------------------------------
    #
    # El cuerpo NO lleva ni un campo de la lista blanca, a proposito: si por lo
    # que sea el locking dejara pasar, lo siguiente que encuentra es el chequeo
    # de "ningun campo actualizable" y devuelve 400 sin escribir. Es un
    # cinturon y un tirante para una sonda que corre contra produccion.
    r = requests.put(url, json={"legado_updated_at_conocido": "2000-01-01 00:00:00"},
                     headers=con_clave, timeout=args.timeout)
    cuerpo = {}
    try:
        cuerpo = r.json()
    except ValueError:
        pass
    es_conflicto = r.status_code == 409 and cuerpo.get("conflicto") is True
    es_sin_campos = r.status_code == 400 and "actualizable" in (r.text or "")
    detalle = ""
    if es_conflicto:
        detalle = ("el locking corto: el legado tiene updated_at={!r}"
                   .format(cuerpo.get("updated_at")))
    elif es_sin_campos:
        detalle = ("la unidad {} no tiene updated_at del otro lado, asi que el "
                   "locking no aplica y corto el chequeo de campos. Sano, pero "
                   "el locking quedo SIN probar: reintenta con --id de una "
                   "unidad que si lo tenga.".format(unidad_id))
    sonda(4, "PUT con timestamp viejo y sin campos", "HTTP 409 (o 400)",
          r, es_conflicto or es_sin_campos, detalle)

    if r.status_code == 200:
        print("\n  ATENCION: respondio 200. Eso significa que ESCRIBIO algo con "
              "un timestamp del ano 2000, o sea que el locking no esta "
              "funcionando. Revisar antes de seguir.")

    print("\n" + "=" * 62)
    if fallos:
        print("FALLARON {} sondas:".format(len(fallos)))
        for f in fallos:
            print("  - {}".format(f))
        print("\nNo hacer el IT real hasta resolverlas.")
        return 1
    print("las 5 sondas pasaron: el PUT esta desplegado y el locking corta.")
    print("Recien ahora tiene sentido el IT real, de a UNO.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
