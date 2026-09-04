#!/usr/bin/env python3
"""
scripts/verificar_check_ingreso_produccion.py -- el push del check list de
INGRESO contra claude.logautos.cl, y despues ir a mirar las dos escrituras.

    LEGADO_API_KEY=... python scripts/verificar_check_ingreso_produccion.py
    LEGADO_API_KEY=... python scripts/verificar_check_ingreso_produccion.py --escribir

SON DOS ESCRITURAS Y NO UNA
===========================
  1. la fila nueva en `check_list`                    (entidad `check_list`)
  2. las diez columnas del `$historico` en la unidad  (`check_list_unidad`)

La 2 depende de la 1, y el orden NO es simetrico -- ver `check_list.guardar()`.

LO QUE SE PRUEBA, Y POR QUE DOS VECES
=====================================
`newstocks_cidef.observaciones` ACUMULA del lado del servidor. Un solo guardado
no distingue "acumula" de "reemplaza": las dos cosas dejan la columna con el
texto de esta pasada. Hacen falta DOS guardados seguidos sobre la misma unidad
para ver si el segundo se suma o se come al primero.

Y el riesgo aca es al reves que en la PDI. Alla el peligro era PISAR; aca es
REPETIR: si REGLA mandara el acumulado completo -- como hacia el legado, que
leia con `getobservacion_dyp()` y concatenaba del lado del cliente -- cada
guardado duplicaria todo lo anterior. Con dos pasadas eso se ve.

CORRE CONTRA UNA COPIA DE LA REPLICA. `core.DB_PATH` se asigna a mano: ponerlo
en el entorno no alcanza porque se fija al importar, y eso ya escribio una vez
en la replica real.
"""

import argparse
import io as _io
import os
import shutil
import sqlite3
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SECRET_KEY", "verificacion")

try:
    import requests
except ImportError:                              # pragma: no cover
    print("falta la dependencia `requests`")
    sys.exit(2)

from modulos.sync_legado import USER_AGENT       # noqa: E402

# El hosting corta la conexion con el User-Agent de `requests`. Ver la nota de
# `verificar_check_mecanico_produccion.py`.
SESION = requests.Session()
SESION.headers["User-Agent"] = USER_AGENT

FALLOS = []


def afirmar(condicion, que, detalle=""):
    print("   {}  {}".format("ok  " if condicion else "FALLA", que))
    if detalle:
        print("          {}".format(detalle))
    if not condicion:
        FALLOS.append(que)


def _observaciones(base, clave, unidad_id):
    """Lee `observaciones` de la unidad SIN escribir: el 409 del locking trae
    `datos_actuales`."""
    r = SESION.put(
        "{}/api_regla/unidades/{}".format(base, unidad_id),
        json={"legado_updated_at_conocido": "2000-01-01 00:00:00"},
        headers={"X-API-Key": clave, "Content-Type": "application/json"},
        timeout=25)
    j = r.json() if r.status_code == 409 else {}
    d = j.get("datos_actuales") or {}
    return d.get("observaciones") or "", j.get("updated_at") or ""


def _fila(base, clave, id_fila):
    r = SESION.get("{}/api_regla/check_list/{}".format(base, id_fila),
                   headers={"X-API-Key": clave}, timeout=25)
    if r.status_code != 200:
        return None
    return (r.json() or {}).get("fila")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, default=66505)
    ap.add_argument("--escribir", action="store_true")
    ap.add_argument("--base", default=os.environ.get(
        "LEGADO_BASE_URL", "https://claude.logautos.cl"))
    args = ap.parse_args()

    clave = os.environ.get("LEGADO_API_KEY", "").strip()
    if not clave:
        raise SystemExit("falta LEGADO_API_KEY")
    base = args.base.rstrip("/")

    real = os.path.join(RAIZ, "local.db")
    db = sqlite3.connect(real)
    db.row_factory = sqlite3.Row
    u = db.execute("SELECT id, vin, patente, clientecompleto, marca "
                   "  FROM newstocks_cidef WHERE id = ?", (args.id,)).fetchone()
    db.close()
    if u is None:
        raise SystemExit("la unidad {} no existe en la replica".format(args.id))
    marcas = " ".join(str(u[c] or "") for c in
                      ("vin", "patente", "clientecompleto", "marca")).upper()
    if "PRUEB" not in marcas and "FRANCO" not in marcas:
        raise SystemExit(
            "NEGADO: la unidad {} no parece de prueba. Esta corrida inserta "
            "filas append-only y le CONCATENA texto a `observaciones`, que no "
            "se puede deshacer por la API.".format(args.id))

    print("legado : {}".format(base))
    print("unidad : {}  vin={} cliente={}".format(
        args.id, u["vin"], u["clientecompleto"]))

    if not args.escribir:
        print("\nNO SE ESCRIBIO NADA. Para el push real, agrega --escribir")
        return 0

    tmp = tempfile.mkdtemp(prefix="regla_ing_")
    copia = os.path.join(tmp, "prueba.db")
    shutil.copy(real, copia)
    os.environ["DB_PATH"] = copia
    os.environ["DATA_DIR"] = tmp
    os.environ["LEGADO_BASE_URL"] = base
    os.environ["PUSH_LEGADO_ACTIVO"] = "0"

    # A MANO, no por el entorno: `core.DB_PATH` se fijo al importar
    # `sync_legado` arriba. Esto ya escribio una vez en la replica real.
    import core
    core.DB_PATH = copia
    core.DATA_DIR = tmp
    os.environ["REGLA_REPLICA_PROTEGIDA"] = "1"
    assert core._normal(core.DB_PATH) == core._normal(copia)

    from app import crear_app
    from core import conectar_db
    from modulos.push_legado import procesar_pendientes
    from modulos import check_list as _cl
    _cl.CARPETA_FOTOS = os.path.join(tmp, "uploads", "check_list")

    print("copia de trabajo: {}".format(copia))

    # -- armar el locking --------------------------------------------------
    antes, marca = _observaciones(base, clave, args.id)
    d = conectar_db(copia)
    d.execute("UPDATE newstocks_cidef SET updated_at = ? WHERE id = ?",
              (marca, args.id))
    d.commit()
    d.close()
    print("\nOBSERVACIONES ANTES ({} chars):".format(len(antes)))
    print("   ...{!r}".format(antes[-90:]))

    app = crear_app()
    app.config["TESTING"] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s["isLoggedIn"] = True
        s["name"] = "Verificacion REGLA"
        s["userId"] = 1
        s["role"] = "Admin"

    def guardar(pieza, tipo, nivel, nota):
        datos = {
            "guia_ingreso": "VERIF-ING", "fecha_ingreso": "2026-09-04",
            "estanque": "5", "kilometraje": "1234",
            "faltante": "NADA", "observaciones": nota,
            "motonave": "MOTONAVE PRUEBA",
            "modelo_observado": "", "color_observado": "",
            "dano_pieza_0": pieza, "dano_tipo_0": tipo, "dano_nivel_0": nivel,
            "motivo": "verificacion del push", "motivo_detalle": "",
            # La pantalla exige UNA foto por daño y esta bien que lo haga: un
            # daño sin evidencia no se puede discutir con el cliente. La foto
            # se guarda en el tmpdir de esta corrida y NO se publica -- las
            # fotos del check list de ingreso quedan para el paso del correo.
            "dano_foto_0": (_io.BytesIO(b"\xff\xd8\xff\xe0jpeg"), "d.jpg"),
        }
        r = c.post("/movimientos/{}/check-list".format(args.id), data=datos,
                   content_type="multipart/form-data")
        return r

    ids = []
    for n, (pieza, tipo, nivel, nota) in enumerate((
            ("CAPOT", "RAYA (1)", "LEVE", "PRIMERA PASADA"),
            ("PARACH DEL", "ABOLLADO (1)", "MEDIO", "SEGUNDA PASADA")), 1):
        print("\nPASADA {} -- {} / {} / {}".format(n, pieza, tipo, nivel))
        r = guardar(pieza, tipo, nivel, nota)
        afirmar(r.status_code == 302,
                "pasada {}: la pantalla guardo".format(n),
                "HTTP {}".format(r.status_code))
        if r.status_code != 302:
            h = r.data.decode("utf-8", "replace")
            import re
            for e in re.findall(r"<li>([^<]+)</li>", h)[:5]:
                print("        error: {}".format(e.strip()))
            return 1
        procesar_pendientes()
        procesar_pendientes()

        d = conectar_db(copia)
        fila = d.execute("SELECT id, legado_id FROM check_list_regla "
                         " ORDER BY id DESC LIMIT 1").fetchone()
        pend = d.execute(
            "SELECT entidad, resuelto_en, ultimo_error FROM sync_push_pendientes"
            " WHERE entidad LIKE 'check_list%' AND python_id = ? ORDER BY id",
            (fila["id"],)).fetchall()
        d.close()
        for p in pend:
            print("      {:<22} {}".format(
                p["entidad"], "resuelta" if p["resuelto_en"]
                else "ERROR: " + (p["ultimo_error"] or "")))
        afirmar(all(p["resuelto_en"] and not p["ultimo_error"] for p in pend),
                "pasada {}: las DOS escrituras se resolvieron".format(n))
        afirmar((fila["legado_id"] or 0) > 0,
                "pasada {}: el legado creo la fila y devolvio su id".format(n),
                "check_list.id = {}".format(fila["legado_id"]))
        ids.append(fila["legado_id"])

    # -- LA FILA DEL LEGADO ------------------------------------------------
    print("\nLAS DOS FILAS DE `check_list`, LEIDAS DE LA BASE")
    for i, cid in enumerate(ids, 1):
        f = _fila(base, clave, cid)
        if f is None:
            afirmar(False, "se pudo leer la fila {}".format(cid))
            continue
        print("   id={}  observacion={!r}".format(cid, f.get("observacion")))
        print("          requerimiento={!r}  gravedad={!r}".format(
            f.get("requerimiento"), f.get("gravedad")))
        print("          motonave={!r} faltante={!r} estanque={!r}".format(
            f.get("motonave"), f.get("faltante"), f.get("estanque")))
        afirmar(bool((f.get("observacion") or "").strip()),
                "fila {}: las piezas llegaron".format(cid))
        afirmar(len((f.get("observacion") or "").split("-")) ==
                len((f.get("requerimiento") or "").split("-")) ==
                len((f.get("gravedad") or "").split("-")),
                "fila {}: las tres listas alineadas".format(cid))

    # -- LA ACUMULACION ----------------------------------------------------
    despues, _ = _observaciones(base, clave, args.id)
    agregado = despues[len(antes):] if despues.startswith(antes) else None
    print("\nOBSERVACIONES DESPUES ({} chars, +{}):".format(
        len(despues), len(despues) - len(antes)))
    print("   agregado: {!r}".format(agregado))

    afirmar(despues.startswith(antes),
            "lo que ya estaba NO se toco -- se agrego al final")
    afirmar(agregado is not None and "CAPOT RAYA (1) LEVE |" in agregado,
            "la PRIMERA pasada esta en el texto agregado")
    afirmar(agregado is not None and
            "PARACH DEL ABOLLADO (1) MEDIO |" in agregado,
            "la SEGUNDA pasada tambien -- o sea que ACUMULO")
    afirmar(agregado is not None and agregado.count("CAPOT RAYA (1) LEVE |") == 1,
            "y la primera aparece UNA sola vez -- no se duplico",
            "si REGLA mandara el acumulado completo, aca habria dos")

    print("\n" + "=" * 64)
    if FALLOS:
        print("FALLARON {}:".format(len(FALLOS)))
        for f in FALLOS:
            print("   - {}".format(f))
        return 1
    print("EL CHECK LIST DE INGRESO EMPUJA LAS DOS ESCRITURAS")
    print("filas creadas en produccion: check_list.id = {}".format(ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
