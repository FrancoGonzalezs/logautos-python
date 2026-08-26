#!/usr/bin/env python3
"""
scripts/probar_pull.py -- prueba el pull sobre una base descartable, con un
cliente falso en lugar del legado. No sale a la red y no toca local.db.

Lo que se verifica es el commit POR PAGINA y sus consecuencias, que es lo que
cambio para que la reconciliacion completa entre en el volumen de Railway:

    1. camino feliz        todas las paginas quedan escritas y la marca de agua
                           avanza al terminar
    2. EN VUELO            mirando la base DESDE ADENTRO de la corrida, en la
                           pagina 3: las paginas 1 y 2 ya estan commiteadas y
                           la marca de agua TODAVIA no se movio
    3. corte a mitad       la corrida muere en la pagina 4: lo traido en 1-3
                           queda guardado, la marca NO avanza, y el estado dice
                           'error' con los contadores reales
    4. retomar             la vuelta siguiente completa y deja todo
    5. dry-run             no escribe ni una fila ni mueve la marca
    6. WAL                 se mide contra una copia de la replica de verdad
                           (71.546 filas, 358 paginas), que es el numero por el
                           que se hizo el cambio

La 2 es la que de verdad demuestra el contrato: el dato se hace durable de a
poco, el progreso solo al final. Las dos cosas a la vez, y no una despues de
la otra, que es como se ven desde afuera.

    python scripts/probar_pull.py
    python scripts/probar_pull.py --sin-wal     # saltea la 6 (copia 380 MB)
"""

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

os.environ.setdefault("SECRET_KEY", "prueba")

from core import DB_PATH                                      # noqa: E402
from modulos import sync_legado                               # noqa: E402
from modulos.sync_legado import sincronizar_entidad           # noqa: E402

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


# ---------------------------------------------------------------------------
# El legado de mentira
# ---------------------------------------------------------------------------

class ClienteFalso(object):
    """Sirve filas paginadas como lo hace Api_regla::cambios.

    `fallar_en` corta la corrida en esa pagina, que es como se ve un timeout o
    un corte del hosting. `espiar` recibe el numero de pagina antes de
    responder: es el gancho para mirar la base EN VUELO."""

    def __init__(self, filas, fallar_en=None, espiar=None, hasta="2026-08-26 10:00:00"):
        self.filas = filas
        self.fallar_en = fallar_en
        self.espiar = espiar
        self.hasta = hasta
        self.paginas_servidas = 0

    def cambios(self, ruta, desde, limite, pagina):
        if self.espiar:
            self.espiar(pagina)
        if self.fallar_en is not None and pagina == self.fallar_en:
            raise RuntimeError("corte simulado en la pagina {}".format(pagina))
        desde_i = (pagina - 1) * limite
        trozo = self.filas[desde_i:desde_i + limite]
        self.paginas_servidas += 1
        return {"filas": trozo, "hasta": self.hasta,
                "hay_mas": desde_i + limite < len(self.filas)}


def base_nueva(ruta, cuantas=0):
    if os.path.exists(ruta):
        os.remove(ruta)
    db = sqlite3.connect(ruta)
    db.executescript("""
        CREATE TABLE newstocks_cidef (
            id INTEGER PRIMARY KEY, vin TEXT, despachado TEXT, calle TEXT,
            updated_at TEXT, created_at TEXT);
    """)
    db.commit()
    db.close()
    return [{"id": i, "vin": "VIN{:013d}".format(i), "despachado": "STOCK",
             "calle": "A", "updated_at": "2026-08-26 09:00:00",
             "created_at": "2026-01-01 00:00:00"}
            for i in range(1, cuantas + 1)]


def leer(ruta, sql, params=()):
    db = sqlite3.connect(ruta)
    db.row_factory = sqlite3.Row
    try:
        f = db.execute(sql, params).fetchone()
        return dict(f) if f else None
    finally:
        db.close()


def contar(ruta):
    return leer(ruta, "SELECT COUNT(*) n FROM newstocks_cidef")["n"]


def marca(ruta):
    f = leer(ruta, "SELECT * FROM sync_estado WHERE entidad = 'unidades'")
    return f if f else {}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-wal", action="store_true",
                    help="saltea la medicion de WAL (copia la replica, ~380 MB)")
    args = ap.parse_args(argv)

    tmp = tempfile.mkdtemp(prefix="probar_pull_")
    ruta = os.path.join(tmp, "prueba.db")

    # ------------------------------------------------------------------ 1
    paso("1. camino feliz: 5 paginas de 200")
    filas = base_nueva(ruta, 1000)
    cli = ClienteFalso(filas)
    r = sincronizar_entidad("unidades", db_path=ruta, limite=200, cliente=cli)
    afirmar(contar(ruta) == 1000, "las 1.000 filas quedaron", contar(ruta))
    afirmar(r["paginas"] == 5, "5 paginas", r["paginas"])
    afirmar(marca(ruta)["marca_agua"] == "2026-08-26 10:00:00",
            "la marca de agua avanzo al terminar", marca(ruta)["marca_agua"])
    afirmar(marca(ruta)["ultimo_resultado"] == "ok", "estado ok")

    # ------------------------------------------------------------------ 2
    paso("2. EN VUELO: dato durable de a poco, progreso solo al final")
    filas = base_nueva(ruta, 1000)
    visto = {}

    def espiar(pagina):
        # Conexion APARTE: ve solo lo que ya esta commiteado. Es exactamente lo
        # que veria la app -- o un proceso que arranca despues de un corte.
        if pagina == 3:
            visto["filas"] = contar(ruta)
            visto["marca"] = marca(ruta).get("marca_agua", "?")

    cli = ClienteFalso(filas, espiar=espiar)
    sincronizar_entidad("unidades", db_path=ruta, limite=200, cliente=cli)
    afirmar(visto.get("filas") == 400,
            "al pedir la pagina 3, las paginas 1-2 YA estaban commiteadas",
            visto.get("filas"))
    afirmar(visto.get("marca") == "",
            "y la marca de agua todavia NO se habia movido",
            repr(visto.get("marca")))
    afirmar(marca(ruta)["marca_agua"] == "2026-08-26 10:00:00",
            "recien al final avanzo")

    # ------------------------------------------------------------------ 3
    paso("3. corte en la pagina 4: no se pierde lo traido")
    filas = base_nueva(ruta, 1000)
    cli = ClienteFalso(filas, fallar_en=4)
    try:
        sincronizar_entidad("unidades", db_path=ruta, limite=200, cliente=cli)
        afirmar(False, "la corrida tenia que fallar")
    except RuntimeError as e:
        afirmar("corte simulado" in str(e), "propago el error", e)
    afirmar(contar(ruta) == 600,
            "las 3 paginas anteriores quedaron escritas (600 filas)", contar(ruta))
    est = marca(ruta)
    afirmar(est["marca_agua"] == "",
            "la marca de agua NO avanzo", repr(est["marca_agua"]))
    afirmar(est["ultimo_resultado"] == "error", "el estado dice error")
    afirmar(est["filas_recibidas"] == 600,
            "y cuenta cuanto alcanzo a traer, no cero", est["filas_recibidas"])
    afirmar("corte simulado" in est["ultimo_detalle"], "guardo el motivo")

    # ------------------------------------------------------------------ 4
    paso("4. retomar despues del corte")
    cli = ClienteFalso(filas)          # ahora sin fallar
    r = sincronizar_entidad("unidades", db_path=ruta, limite=200, cliente=cli)
    afirmar(contar(ruta) == 1000, "quedaron las 1.000", contar(ruta))
    afirmar(r["recibidas"] == 1000,
            "volvio a pedir todo: la marca estaba en cero", r["recibidas"])
    afirmar(marca(ruta)["marca_agua"] == "2026-08-26 10:00:00",
            "ahora si avanzo")
    afirmar(marca(ruta)["ultimo_resultado"] == "ok", "estado ok")

    # ------------------------------------------------------------------ 5
    paso("5. dry-run no escribe nada")
    filas = base_nueva(ruta, 1000)
    cli = ClienteFalso(filas)
    r = sincronizar_entidad("unidades", db_path=ruta, limite=200, cliente=cli,
                            dry_run=True)
    afirmar(contar(ruta) == 0, "no escribio ni una fila", contar(ruta))
    afirmar(marca(ruta).get("marca_agua") == "",
            "no movio la marca", repr(marca(ruta).get("marca_agua")))
    afirmar(r["recibidas"] == 1000, "pero si leyo las 1.000", r["recibidas"])

    # ------------------------------------------------------------------ 6
    if args.sin_wal:
        paso("6. WAL: SALTEADA (--sin-wal)")
    elif not os.path.exists(DB_PATH):
        paso("6. WAL: SALTEADA (no hay replica en {})".format(DB_PATH))
    else:
        paso("6. WAL sobre la replica de verdad (71.546 filas)")
        copia = os.path.join(tmp, "replica.db")
        t0 = time.time()
        shutil.copy(DB_PATH, copia)
        print("   copia hecha en {:.0f}s ({:.0f} MB)".format(
            time.time() - t0, os.path.getsize(copia) / 1048576))

        db = sqlite3.connect(copia)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode = WAL")
        cols = [c[1] for c in db.execute("PRAGMA table_info(newstocks_cidef)")]
        reales = [dict(r) for r in db.execute("SELECT * FROM newstocks_cidef")]
        db.close()
        # La reconciliacion empieza sin marca de agua.
        db = sqlite3.connect(copia)
        db.execute("DELETE FROM sync_estado WHERE entidad = 'unidades'")
        db.commit()
        db.close()

        wal = copia + "-wal"
        pico = {"max": 0}

        def espiar_wal(pagina):
            if os.path.exists(wal):
                pico["max"] = max(pico["max"], os.path.getsize(wal))

        cli = ClienteFalso(reales, espiar=espiar_wal)
        t0 = time.time()
        r = sincronizar_entidad("unidades", db_path=copia, limite=200,
                                cliente=cli, desde="")
        tardo = time.time() - t0
        if os.path.exists(wal):
            pico["max"] = max(pico["max"], os.path.getsize(wal))

        print("   paginas   : {}".format(r["paginas"]))
        print("   filas     : {:,}".format(r["recibidas"]))
        print("   tardo     : {:.0f}s".format(tardo))
        print("   PICO WAL  : {:.2f} MB".format(pico["max"] / 1048576))
        print("   antes era : 67.70 MB (una sola transaccion)")
        print("   libre RW  : 69 MB de 434 (df del volumen)")
        afirmar(pico["max"] / 1048576 < 10,
                "el WAL se mantiene por debajo de 10 MB",
                "{:.2f} MB".format(pico["max"] / 1048576))
        afirmar(r["recibidas"] == len(reales),
                "trajo las 71.546", r["recibidas"])
        del cols

    # ------------------------------------------------------------------
    try:
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass

    print("\n" + "=" * 62)
    if fallos:
        print("FALLARON {} comprobaciones:".format(len(fallos)))
        for f in fallos:
            print("  - {}".format(f))
        return 1
    print("todas las pruebas del pull pasaron")
    return 0


if __name__ == "__main__":
    sys.exit(main())
