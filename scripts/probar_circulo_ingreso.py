#!/usr/bin/env python3
"""
scripts/probar_circulo_ingreso.py -- las DOS escrituras del check list de
ingreso, y sobre todo QUE PASA CUANDO UNA DE LAS DOS FALLA.

    python scripts/probar_circulo_ingreso.py

El camino feliz ya esta verificado contra produccion
(`verificar_check_ingreso_produccion.py`, filas 20104 y 20105). Lo que esta
prueba cubre es lo otro: las dos mitades del fallo parcial, que contra
produccion no se pueden forzar sin romper algo de verdad.

LAS DOS ESCRITURAS
==================
  1. `check_list`         la fila nueva          (verbo crear)
  2. `check_list_unidad`  las diez del $historico (verbo actualizar)

Y NO SON SIMETRICAS. Esa es la razon por la que la 2 depende de la 1 y no al
reves:

  * 1 entra, 2 falla  ->  el legado tiene el check list; la unidad todavia no
                          lo dice. La cola reintenta la 2 con backoff y
                          converge sola. Mientras tanto el estado es visible:
                          hay una fila en `check_list` que se puede encontrar.

  * 2 entra, 1 falla  ->  la unidad DICE que tiene check list --
                          `fecha_check_list` es una fecha plausible -- y no
                          existe ninguno. Nadie lo nota, porque nada se ve
                          roto. Es el estado que hay que hacer IMPOSIBLE, no
                          recuperable.

Por eso `depende_de` va en la 2, y por eso `ejecutar_entrada` chequea la
dependencia ademas del selector de pendientes -- `disparar_push` llama directo
con el id y se saltearia el filtro.

LA TERCERA PROPIEDAD: `observaciones` NO SE DUPLICA
===================================================
`newstocks_cidef.observaciones` acumula del lado del servidor, asi que REGLA
manda SOLO los daños de esta pasada. Si mandara el acumulado --como hacia el
legado, que leia con `getobservacion_dyp()` y concatenaba del lado del
cliente-- cada guardado duplicaria todo lo anterior. Se prueba con dos pasadas.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SECRET_KEY", "prueba")

PUERTO = 8801
BASE = "http://127.0.0.1:{}".format(PUERTO)
FALLOS = []


def afirmar(condicion, que, detalle=""):
    print("   {}  {}".format("ok  " if condicion else "FALLA", que))
    if detalle:
        print("          {}".format(detalle))
    if not condicion:
        FALLOS.append(que)


def _get(ruta):
    with urllib.request.urlopen(BASE + ruta, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(ruta, cuerpo):
    p = urllib.request.Request(
        BASE + ruta, data=json.dumps(cuerpo).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(p, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def guardar(c, unidad_id, pieza, tipo, nivel, nota):
    return c.post(
        "/movimientos/{}/check-list".format(unidad_id),
        data={"guia_ingreso": "G-1", "fecha_ingreso": "2026-09-04",
              "estanque": "5", "kilometraje": "1000", "faltante": "NADA",
              "observaciones": nota, "motonave": "NAVE",
              "modelo_observado": "", "color_observado": "",
              "dano_pieza_0": pieza, "dano_tipo_0": tipo,
              "dano_nivel_0": nivel,
              "dano_foto_0": (io.BytesIO(b"\xff\xd8\xff\xe0jpg"), "d.jpg"),
              # La unidad sembrada es CIDEF, y el original envuelve estos dos
              # campos en un `if($cliente=='CIDEF')`. La pantalla los exige
              # igual; sin ellos el guardado da 400, que es lo correcto.
              "placa_vin": (io.BytesIO(b"\xff\xd8\xff\xe0jpg"), "placa.jpg"),
              "foto_unidad": (io.BytesIO(b"\xff\xd8\xff\xe0jpg"), "uni.jpg"),
              "motivo": "prueba", "motivo_detalle": ""},
        content_type="multipart/form-data")


def main():
    origen = os.path.join(RAIZ, "local.db")
    if not os.path.exists(origen):
        print("no hay local.db")
        return 1
    tmp = tempfile.mkdtemp(prefix="regla_ing_c_")
    shutil.copy(origen, os.path.join(tmp, "prueba.db"))
    os.environ["DB_PATH"] = os.path.join(tmp, "prueba.db")
    os.environ["DATA_DIR"] = tmp
    os.environ["LEGADO_BASE_URL"] = BASE
    os.environ["LEGADO_API_KEY"] = "x"
    os.environ["PUSH_LEGADO_ACTIVO"] = "0"

    servidor = subprocess.Popen(
        [sys.executable, os.path.join(RAIZ, "scripts", "legado_simulado.py"),
         "--puerto", str(PUERTO), "--lista-blanca", "con_check_list"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                _get("/_estado")
                break
            except Exception:
                time.sleep(0.1)
        else:
            print("el simulado no levanto")
            return 1

        _post("/_sembrar", {"id": 4343, "vin": "VINPRUEBAINGRESO",
                            "patente": "II1122", "clientecompleto": "CIDEF",
                            "marca": "GREAT WALL", "modelo": "POER",
                            "color": "BLANCO", "patio": "PATIO 2"})

        from app import crear_app
        from core import conectar_db
        app = crear_app()
        app.config["TESTING"] = True
        db = conectar_db()
        db.execute(
            "INSERT OR REPLACE INTO newstocks_cidef (id, vin, patente, "
            " clientecompleto, marca, modelo, color, updated_at, observaciones)"
            " VALUES (4343,'VINPRUEBAINGRESO','II1122','CIDEF','GREAT WALL',"
            "         'POER','BLANCO','2026-08-26 11:00:00','YA HABIA ALGO')")
        db.commit()
        db.close()

        c = app.test_client()
        with c.session_transaction() as s:
            s["isLoggedIn"] = True
            s["name"] = "Prueba"
            s["userId"] = 1
            s["role"] = "Admin"

        from modulos.push_legado import ejecutar_entrada, procesar_pendientes

        # ------------------------------------------------------------------
        print("\n1. EL ORDEN: la 2 NO puede salir antes que la 1")
        # ------------------------------------------------------------------
        r = guardar(c, 4343, "CAPOT", "RAYA (1)", "LEVE", "PRIMERA")
        afirmar(r.status_code == 302, "la pantalla guardo")

        db = conectar_db()
        ent = db.execute(
            "SELECT id, entidad, depende_de FROM sync_push_pendientes "
            " WHERE entidad LIKE 'check_list%' ORDER BY id").fetchall()
        db.close()
        afirmar([e["entidad"] for e in ent] ==
                ["check_list", "check_list_unidad"],
                "se encolaron las DOS, en ese orden")
        hija = [e for e in ent if e["entidad"] == "check_list_unidad"][0]
        madre = [e for e in ent if e["entidad"] == "check_list"][0]
        afirmar(hija["depende_de"] == madre["id"],
                "la del $historico DEPENDE de la de la fila")

        # Se dispara la hija primero, a proposito.
        afirmar(ejecutar_entrada(hija["id"]) == "espera",
                "ejecutarla primero devuelve 'espera', no la manda")
        u = _get("/_estado")["unidades"]["4343"]
        afirmar(not u.get("fecha_check_list"),
                "y la unidad NO quedo diciendo que tiene check list",
                "fecha_check_list={!r}".format(u.get("fecha_check_list")))

        # ------------------------------------------------------------------
        print("\n2. LA 1 ENTRA Y LA 2 FALLA: se reintenta y converge")
        # ------------------------------------------------------------------
        # Se manda la madre sola. La hija queda pendiente.
        afirmar(ejecutar_entrada(madre["id"]) == "ok", "la fila del check list entro")
        filas = _get("/_check_list")["filas"]
        afirmar(len(filas) == 1, "el legado tiene la fila del check list")

        # Ahora el legado se cae para el PUT: la 2 falla.
        _post("/_modo", {"modo": "caer"})
        r2 = ejecutar_entrada(hija["id"])
        afirmar(r2 == "error", "con el legado caido, la 2 falla")
        db = conectar_db()
        e = db.execute("SELECT resuelto_en, intentos, ultimo_error "
                       "  FROM sync_push_pendientes WHERE id = ?",
                       (hija["id"],)).fetchone()
        db.close()
        afirmar(not e["resuelto_en"] and e["intentos"] >= 1,
                "queda SIN resolver y con el intento contado",
                "intentos={} error={}".format(
                    e["intentos"], (e["ultimo_error"] or "")[:60]))
        u = _get("/_estado")["unidades"]["4343"]
        afirmar(not u.get("fecha_check_list"),
                "la unidad sigue sin decir que tiene check list")
        afirmar(len(_get("/_check_list")["filas"]) == 1,
                "y la fila del check list sigue ahi -- no se perdio nada")

        # Se levanta y converge.
        _post("/_modo", {"modo": "ok"})
        afirmar(ejecutar_entrada(hija["id"]) == "ok",
                "cuando el legado vuelve, la 2 entra sola")
        u = _get("/_estado")["unidades"]["4343"]
        afirmar(bool(u.get("fecha_check_list")),
                "y AHORA si la unidad lo dice",
                "fecha_check_list={!r}".format(u.get("fecha_check_list")))

        # ------------------------------------------------------------------
        print("\n3. `observaciones` ACUMULA y NO se duplica")
        # ------------------------------------------------------------------
        antes = _get("/_estado")["unidades"]["4343"].get("observaciones") or ""
        r = guardar(c, 4343, "PARACH DEL", "ABOLLADO (1)", "MEDIO", "SEGUNDA")
        afirmar(r.status_code == 302, "segunda pasada guardada")
        for _ in range(3):
            procesar_pendientes()
        despues = _get("/_estado")["unidades"]["4343"].get("observaciones") or ""
        print("      antes  : {!r}".format(antes))
        print("      despues: {!r}".format(despues))
        afirmar(despues.startswith(antes), "lo anterior no se toco")
        afirmar(despues.count("CAPOT RAYA (1) LEVE |") == 1,
                "la PRIMERA pasada sigue apareciendo UNA sola vez")
        afirmar("PARACH DEL ABOLLADO (1) MEDIO |" in despues,
                "y la segunda se agrego")

        # ------------------------------------------------------------------
        print("\n4. LOS TRES NOMBRES QUE MIENTEN, alineados")
        # ------------------------------------------------------------------
        filas = _get("/_check_list")["filas"]
        f = filas[-1]
        print("      observacion  (PIEZAS) : {!r}".format(f.get("observacion")))
        print("      requerimiento(TIPOS)  : {!r}".format(f.get("requerimiento")))
        print("      gravedad     (NIVELES): {!r}".format(f.get("gravedad")))
        afirmar(f.get("observacion") == "PARACH DEL",
                "`observacion` lleva la PIEZA, no una observacion")
        afirmar(f.get("requerimiento") == "ABOLLADO (1)",
                "`requerimiento` lleva el TIPO DE DAÑO")
        afirmar(f.get("gravedad") == "MEDIO", "`gravedad` lleva el NIVEL")
        afirmar(f.get("observaciones") == "SEGUNDA",
                "y `observaciones` en PLURAL lleva el texto libre -- "
                "una letra de diferencia")

        print("\n" + "=" * 62)
        if FALLOS:
            print("FALLARON {}:".format(len(FALLOS)))
            for x in FALLOS:
                print("   - {}".format(x))
            return 1
        print("el circulo del check list de ingreso cierra")
        return 0
    finally:
        servidor.terminate()
        servidor.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
