#!/usr/bin/env python3
"""
scripts/probar_traspaso.py -- la ruta temporal que sirve la réplica.

    python scripts/probar_traspaso.py

QUE SE PRUEBA, Y POR QUE CADA COSA

Esta ruta sirve la réplica entera, con `tbl_users`: correos, RUT, teléfonos y
hashes de 144 personas reales. Es la superficie más sensible que este sistema
tuvo nunca, y existe sólo para una mudanza. Así que lo que se prueba no es que
funcione — es que **esté cerrada**.

  1. SIN LA VARIABLE, LA RUTA NO EXISTE. El blueprint no se registra, así que
     la respuesta es EXACTAMENTE la misma que la de una dirección inventada.
     No es una ruta apagada que contesta 403: una respuesta distinta ya
     delataría que ahí hay algo esperando el token correcto.

  2. UN TOKEN CORTO NO ARRANCA. Mejor no levantar que levantar creyendo que
     está protegido.

  3. SIN CABECERA, CON LA CABECERA MAL, Y CON EL TOKEN EN LA URL: 404 las tres.
     La tercera es la que importa y es la que alguien va a intentar por
     comodidad: si la ruta aceptara el token por query string, ese token
     terminaría en el log del proxy de Railway.

  4. CON EL TOKEN CORRECTO devuelve una base ENTERA Y VALIDA — se descomprime,
     abre, pasa `quick_check` y tiene las mismas filas que el origen.

  5. Y `sync_estado` VIAJA. Es la razón entera de esta vía: la base y su marca
     de agua salen del mismo archivo en el mismo instante, así que llegan
     consistentes. Si esto fallara, la vía no tendría sentido.
"""

import gzip
import io as _io
import os
import shutil
import sqlite3
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from temporales import carpeta_de_prueba              # noqa: E402

os.environ.setdefault("SECRET_KEY", "prueba")
os.environ.setdefault("PUBLIC_BASE_URL", "https://regla.example")
os.environ.setdefault("REGLA_SOLO_LOCAL", "1")
os.environ.setdefault("PUSH_LEGADO_ACTIVO", "0")

TOKEN = "k" * 48
FALLOS = []


def afirmar(condicion, que, detalle=""):
    print("   {}  {}".format("ok  " if condicion else "FALLA", que))
    if detalle:
        print("          {}".format(detalle))
    if not condicion:
        FALLOS.append(que)


def app_con(token):
    """Levanta la aplicación con ese TRASPASO_TOKEN (o sin ninguno)."""
    import importlib
    if token is None:
        os.environ.pop("TRASPASO_TOKEN", None)
    else:
        os.environ["TRASPASO_TOKEN"] = token
    import app as modulo_app
    importlib.reload(modulo_app)
    return modulo_app.app


def main():
    tmp = carpeta_de_prueba("regla_trasp_")
    prueba = os.path.join(tmp, "prueba.db")

    origen = os.path.join(RAIZ, "local.db")
    if not os.path.exists(origen):
        print("no hay local.db")
        return 1
    # Una copia consistente, que es la forma correcta y además la que este
    # módulo usa: `shutil.copy` sobre una base en WAL puede traer de menos.
    sqlite3.connect(origen).execute("VACUUM INTO ?", (prueba,))

    os.environ["DB_PATH"] = prueba
    os.environ["DATA_DIR"] = tmp

    esperado = {}
    o = sqlite3.connect(prueba)
    for (t,) in o.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        esperado[t] = o.execute('SELECT COUNT(*) FROM "{}"'.format(t)).fetchone()[0]
    marca_origen = o.execute(
        "SELECT entidad, marca_agua FROM sync_estado ORDER BY entidad"
    ).fetchall()
    o.close()

    print("")
    print("1. SIN LA VARIABLE, LA RUTA NO EXISTE")
    a = app_con(None)
    c0 = a.test_client()
    rutas = [str(x) for x in a.url_map.iter_rules()]
    afirmar(not any("traspaso" in x for x in rutas),
            "la ruta no esta ni registrada en el mapa")

    # Lo que importa no es QUE codigo devuelve, sino que devuelva EL MISMO que
    # una direccion inventada: si contestara distinto, la respuesta misma
    # delataria que ahi hay algo apagado.
    r1 = c0.get("/traspaso/replica.db.gz")
    r2 = c0.get("/una-direccion-que-no-existe")
    afirmar(r1.status_code == r2.status_code,
            "y contesta lo MISMO que una direccion inventada",
            "traspaso={} inventada={}".format(r1.status_code, r2.status_code))

    print("")
    print("2. UN TOKEN CORTO NO ARRANCA")
    try:
        app_con("corto123")
        afirmar(False, "revienta con un token de 8 caracteres")
    except RuntimeError as e:
        afirmar("minimo" in str(e), "revienta con un token de 8 caracteres",
                str(e).split(".")[0])

    print("")
    print("3. CON LA VARIABLE, PERO SIN EL TOKEN CORRECTO: 404")
    a = app_con(TOKEN)
    c = a.test_client()

    r = c.get("/traspaso/replica.db.gz")
    afirmar(r.status_code == 404, "sin cabecera", "dio {}".format(r.status_code))

    r = c.get("/traspaso/replica.db.gz",
              headers={"X-Traspaso-Token": "otro" * 12})
    afirmar(r.status_code == 404, "con la cabecera equivocada",
            "dio {}".format(r.status_code))

    # La que importa: el token por la URL NO puede servir, porque ahi queda
    # escrito en el log de cualquier proxy que haya en el camino.
    r = c.get("/traspaso/replica.db.gz?token=" + TOKEN)
    afirmar(r.status_code == 404,
            "con el token en la URL (que es lo que alguien va a intentar)",
            "dio {}".format(r.status_code))

    print("")
    print("4. CON EL TOKEN CORRECTO, UNA BASE ENTERA Y VALIDA")
    r = c.get("/traspaso/replica.db.gz",
              headers={"X-Traspaso-Token": TOKEN})
    afirmar(r.status_code == 200, "200", "dio {}".format(r.status_code))
    crudo = r.get_data()
    afirmar(len(crudo) > 0, "y devolvio algo",
            "{:.1f} MB comprimidos".format(len(crudo) / 1e6))

    bajada = os.path.join(tmp, "bajada.db")
    with gzip.open(_io.BytesIO(crudo), "rb") as g, open(bajada, "wb") as f:
        shutil.copyfileobj(g, f, 1 << 20)
    afirmar(True, "descomprime",
            "{:.1f} MB".format(os.path.getsize(bajada) / 1e6))

    b = sqlite3.connect(bajada)
    afirmar(b.execute("PRAGMA quick_check").fetchone()[0] == "ok",
            "quick_check ok")

    dif = []
    for t, n in sorted(esperado.items()):
        try:
            m = b.execute('SELECT COUNT(*) FROM "{}"'.format(t)).fetchone()[0]
        except Exception:                             # noqa: BLE001
            m = None
        if m != n:
            dif.append("{}: origen={} bajada={}".format(t, n, m))
    afirmar(not dif, "las {} tablas con las mismas filas".format(len(esperado)),
            "; ".join(dif[:3]))

    print("")
    print("5. Y LA MARCA DE AGUA VIAJA CON LA BASE")
    marca_bajada = b.execute(
        "SELECT entidad, marca_agua FROM sync_estado ORDER BY entidad"
    ).fetchall()
    afirmar(marca_bajada == marca_origen,
            "sync_estado identica -- es la razon entera de esta via",
            "{}".format(marca_bajada))
    b.close()

    print("")
    print("6. Y NO QUEDA NINGUNA COPIA DANDO VUELTAS")
    import tempfile as _tf
    restos = [n for n in os.listdir(_tf.gettempdir())
              if n.startswith("traspaso_")]
    afirmar(not restos,
            "el temporal con `tbl_users` se borro al terminar de servir",
            "quedaron: {}".format(restos))

    print("")
    print("=" * 64)
    if FALLOS:
        print("FALLARON {}:".format(len(FALLOS)))
        for f in FALLOS:
            print("   - {}".format(f))
        return 1
    print("la ruta no existe sin la variable, no se abre con el token en la")
    print("URL, y lo que entrega es una replica entera con su marca de agua.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
