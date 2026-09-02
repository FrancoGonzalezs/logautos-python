#!/usr/bin/env python3
"""
scripts/probar_circulo_mecanica.py -- el circulo completo del check list mecanico.

    python scripts/probar_circulo_mecanica.py

Levanta el legado simulado, carga un check list con sus 65 campos y dos
fallas desde la pantalla de REGLA, procesa la cola y va a MIRAR LA FILA DEL
OTRO LADO. No se cree lo que dice la cola: la cola dice 'ok' cuando el HTTP
volvio 200, y una columna fuera de la lista blanca vuelve 200 sin escribir
nada. Ese es el silencio que esta prueba existe para romper.

Lo que verifica, en orden:

  0. NINGUNA COLUMNA SE IGNORA. Las 82 que manda Python tienen que estar en la
     lista blanca del otro lado. El doble las tiene escritas aparte, a mano, y
     no derivadas del mismo lugar que las de Python -- si salieran del mismo
     lugar, coincidirian por construccion y la prueba no probaria nada.

  1. EL ID VUELVE Y SE PROPAGA. El paso 2 no puede salir antes de saber sobre
     que fila escribe.

  2. LAS FALLAS SE ACUMULAN DEL LADO DEL LEGADO, y las tres listas quedan
     alineadas: la falla n tiene la modalidad n y la foto n.

  3. EL CONTADOR SE SUMA, no se asigna.

  4. EL ORDEN. Si la falla sale antes que el check list, el legado responde
     404 y la entrada espera. Se prueba a proposito ejecutando la hija
     primero.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SECRET_KEY", "prueba")

PUERTO = 8799
BASE = "http://127.0.0.1:{}".format(PUERTO)
FALLOS = []


def afirmar(condicion, que):
    print("   {}  {}".format("ok  " if condicion else "FALLA", que))
    if not condicion:
        FALLOS.append(que)


def _get(ruta):
    with urllib.request.urlopen(BASE + ruta, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(ruta, cuerpo):
    pedido = urllib.request.Request(
        BASE + ruta, data=json.dumps(cuerpo).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(pedido, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    origen = os.path.join(RAIZ, "local.db")
    if not os.path.exists(origen):
        print("no hay local.db: esta prueba necesita la replica")
        return 1
    tmp = tempfile.mkdtemp(prefix="regla_circ_mec_")
    # Se COPIA la replica, igual que probar_circulo.py: la prueba necesita una
    # unidad de verdad con sus columnas de verdad, y no puede tocar local.db.
    import shutil
    shutil.copy(origen, os.path.join(tmp, "prueba.db"))
    os.environ["DB_PATH"] = os.path.join(tmp, "prueba.db")
    os.environ["DATA_DIR"] = tmp
    os.environ["LEGADO_BASE_URL"] = BASE
    os.environ["LEGADO_API_KEY"] = "x"
    os.environ["PUBLIC_BASE_URL"] = "https://regla.example"
    os.environ["PUSH_LEGADO_ACTIVO"] = "0"       # se dispara a mano, no por hilo

    servidor = subprocess.Popen(
        [sys.executable, os.path.join(RAIZ, "scripts", "legado_simulado.py"),
         "--puerto", str(PUERTO),
         # La lista blanca del bloque H, que es lo que este modulo NECESITA
         # desplegado. Se pide explicitamente: el doble arranca con la
         # `desplegada`, que es la verdad sobre produccion hoy, y esta prueba
         # dice en voz alta contra que version del contrato corre.
         "--lista-blanca", "con_check_list"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                _get("/_estado")
                break
            except Exception:
                time.sleep(0.1)
        else:
            print("el legado simulado no levanto")
            return 1

        _post("/_sembrar", {"id": 4242, "vin": "VINPRUEBA0000001",
                            "patente": "AA1122", "clientecompleto": "CIDEF",
                            "marca": "GREAT WALL", "modelo": "POER",
                            "color": "BLANCO", "patio": "PATIO 2"})

        from app import crear_app
        from core import conectar_db
        app = crear_app()
        app.config["TESTING"] = True

        # La unidad tiene que existir en la replica local tambien.
        db = conectar_db()
        db.execute(
            "INSERT OR REPLACE INTO newstocks_cidef (id, vin, patente, "
            " clientecompleto, marca, modelo, color, updated_at) "
            " VALUES (4242,'VINPRUEBA0000001','AA1122','CIDEF','GREAT WALL',"
            "         'POER','BLANCO','2026-08-26 11:00:00')")
        db.commit()
        db.close()

        c = app.test_client()
        with c.session_transaction() as s:
            s["isLoggedIn"] = True
            s["name"] = "Cristian Toledo"
            s["userId"] = 1
            s["role"] = "Admin"

        from modulos.catalogo_mecanica import CAMPOS
        datos = {"guia": "G-99", "fecha_ingreso": "2026-09-02",
                 "kilometraje": "54001", "estado_carflex": "Usado",
                 "estanque": "7", "obs_general": "sin novedad"}
        for col, _e, tipo, ops in CAMPOS:
            datos[col] = (ops[0] if tipo == "opcion" else
                          "12,60" if tipo == "bateria" else
                          "40" if tipo == "porcentaje" else "2")

        print("\nPASO 1 -- los 65 campos")
        r = c.post("/movimientos/4242/check-list-mecanico", data=datos)
        afirmar(r.status_code == 302, "la pantalla guarda y redirige")
        id_check = int(r.headers["Location"].rstrip("/").split("/")[-2])

        print("\nPASO 2 -- dos fallas")
        for texto, modalidad, nombre in (
                ("EXTINTOR VENCIDO", "LEVE", "a.jpg"),
                ("TAPIZ MANCHADO", "GRAVE", "b.jpg")):
            r = c.post("/movimientos/check-list-mecanico/{}/fallas".format(id_check),
                       data={"falla": texto, "modalidad": modalidad,
                             "foto": (io.BytesIO(b"\xff\xd8\xff\xe0jpg"), nombre)},
                       content_type="multipart/form-data")
            afirmar(r.status_code == 302, "se carga la falla " + texto)

        # -- el orden: se dispara la HIJA primero, a proposito ---------------
        print("\n4. EL ORDEN (se ejecuta la falla ANTES que el check list)")
        from modulos.push_legado import ejecutar_entrada, procesar_pendientes
        db = conectar_db()
        entradas = db.execute(
            "SELECT id, entidad, legado_id, depende_de FROM sync_push_pendientes"
            " WHERE entidad LIKE 'check_list_mecanica%' ORDER BY id").fetchall()
        db.close()
        hijas = [e for e in entradas if e["entidad"].endswith("_falla")]
        afirmar(len(hijas) == 2, "hay dos entradas de falla en la cola")
        afirmar(all(h["depende_de"] for h in hijas),
                "las dos dependen de la entrada del paso 1")
        afirmar(all((h["legado_id"] or 0) == 0 for h in hijas),
                "y todavia no saben el id del legado (va en 0)")
        r = ejecutar_entrada(hijas[0]["id"])
        afirmar(r == "espera", "ejecutar la hija primero devuelve 'espera'")

        print("\n1. EL CIRCULO")
        for _ in range(4):
            procesar_pendientes()
        estado = _get("/_estado")

        db = conectar_db()
        pend = db.execute(
            "SELECT entidad, legado_id, resuelto_en, ultimo_error "
            "  FROM sync_push_pendientes WHERE entidad LIKE 'check_list_mec%' "
            " ORDER BY id").fetchall()
        fila_local = db.execute(
            "SELECT legado_id FROM check_list_mecanica_regla WHERE id = ?",
            (id_check,)).fetchone()
        db.close()
        for p in pend:
            print("      {:<28} legado_id={:<4} {}".format(
                p["entidad"], p["legado_id"],
                "resuelta" if p["resuelto_en"] else
                ("ERROR: " + (p["ultimo_error"] or ""))))
        afirmar(all(p["resuelto_en"] and not p["ultimo_error"] for p in pend),
                "las tres entradas se resolvieron sin error")
        afirmar((fila_local["legado_id"] or 0) > 0,
                "el id del legado quedo guardado en la fila de REGLA")
        afirmar(all((p["legado_id"] or 0) == fila_local["legado_id"]
                    for p in pend if p["entidad"].endswith("_falla")),
                "y se propago a las dos entradas hijas")

        print("\n0. NINGUNA COLUMNA SE IGNORO")
        ignoradas = estado.get("ignoradas") or []
        afirmar(not ignoradas,
                "la lista blanca del legado acepta las 82 ({})".format(
                    ignoradas or "ninguna afuera"))

        print("\n2 y 3. LA FILA, DEL LADO DEL LEGADO")
        fila = _get("/_check_list_mecanica")["filas"][0]
        print("      observacion  : {}".format(fila.get("observacion")))
        print("      modalidad    : {}".format(fila.get("modalidad")))
        print("      link_unidades: {}".format(fila.get("link_unidades")))
        print("      contador     : {}".format(fila.get("contador")))
        afirmar(fila.get("observacion") == "EXTINTOR VENCIDO | TAPIZ MANCHADO",
                "las dos fallas llegaron ACUMULADAS, no pisadas")
        afirmar(fila.get("modalidad") == "LEVE | GRAVE",
                "la modalidad quedo alineada con su falla")
        fotos = (fila.get("link_unidades") or "").split(" | ")
        afirmar(len(fotos) == 2 and all(f.startswith("https://regla.example/f/")
                                        for f in fotos),
                "las dos fotos son URL publicas de REGLA")
        afirmar(fila.get("contador") == 2,
                "el contador se SUMO dos veces (2), no se asigno")
        afirmar(fila.get("bateria") == "12.60V" and fila.get("nd") == "40%",
                "los 65 campos llegaron normalizados (bateria y %)")
        afirmar(fila.get("estado") == "ABIERTO",
                "y la fila nace ABIERTA: el cierre lo hace el legado")

        print("\n" + "=" * 62)
        if FALLOS:
            print("FALLARON {}:".format(len(FALLOS)))
            for f in FALLOS:
                print("   - {}".format(f))
            return 1
        print("el circulo cierra")
        return 0
    finally:
        servidor.terminate()
        servidor.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
