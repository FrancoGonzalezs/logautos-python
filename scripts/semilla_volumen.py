"""
semilla_volumen.py -- pone al dia la base del volumen (Railway) con las tablas
chicas que se importaron despues del ultimo deploy.

El problema que resuelve: `local.db` no esta versionado y el que vive en el
volumen quedo de antes de importar `tbl_users`, `tbl_roles` y los catalogos del
check list. Aunque el codigo llegue actualizado, el login falla porque esas
tablas no existen alla. Reimportar el dump entero no es opcion -- son 602 MB y
no estan en el contenedor -- pero estas tablas suman 621 filas, asi que viajan
en un archivo de pocos KB.

Que NO viaja aca, y por que:

  - `check_list_regla` y `movimientos_regla` las crea la propia app con
    CREATE TABLE IF NOT EXISTS en cuanto alguien entra a Movimientos. Subirlas
    seria pisar movimientos ya registrados en produccion con los de la maquina
    de desarrollo.
  - `empleados` ya no se consulta desde que el encargado sale de la sesion.
  - Las tablas grandes (newstocks_cidef, orden_trabajo, registros...) ya estan
    en el volumen desde el primer deploy y no cambiaron.

OJO CON EL DATO PERSONAL: `tbl_users` lleva emails, RUT, telefonos y los hashes
de contrasena de 144 personas reales. El archivo que genera `--exportar` NO va
al repo -- esta en .gitignore -- y conviene borrarlo despues de aplicarlo.

Uso:
    # 1. ver que tiene hoy la base del volumen (correr DENTRO del contenedor)
    python scripts/semilla_volumen.py --revisar --db /data/local.db

    # 2. generar la semilla desde la maquina local
    python scripts/semilla_volumen.py --exportar semilla.sql.gz

    # 3. aplicarla sobre la base del volumen
    python scripts/semilla_volumen.py --aplicar semilla.sql.gz --db /data/local.db
"""

import argparse
import gzip
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_POR_DEFECTO = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "local.db"))

# Las tablas que hay que llevar, con para que sirve cada una. El orden importa
# al aplicar: `tbl_roles` antes que `tbl_users` porque el login las cruza.
TABLAS = [
    ("tbl_roles", "login: el texto de cada rol"),
    ("tbl_users", "login: los usuarios (DATO PERSONAL)"),
    ("piezas", "check list: catalogo de piezas"),
    ("tipo_dano", "check list: catalogo de tipos de dano"),
    ("nivel_dano", "check list: escala de severidad"),
]

# Las que la app se crea sola. Se listan para poder informarlas en --revisar y
# que nadie las suba a mano por las dudas.
SE_CREAN_SOLAS = ["movimientos_regla", "check_list_regla"]


def abrir(ruta, para_escribir=False):
    if not os.path.exists(ruta):
        sys.exit("no existe la base {}".format(ruta))
    db = sqlite3.connect(ruta)
    db.row_factory = sqlite3.Row
    if para_escribir:
        db.execute("PRAGMA journal_mode = WAL")
    return db


def existe(db, tabla):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (tabla,)).fetchone() is not None


def filas(db, tabla):
    return db.execute('SELECT COUNT(*) FROM "{}"'.format(tabla)).fetchone()[0]


def revisar(ruta):
    """Que tablas tiene hoy esa base. Es el paso que se corre en el contenedor
    para saber que falta antes de tocar nada."""
    db = abrir(ruta)
    print("base: {}\n".format(ruta))
    print("  {:<22} {:>9}  {}".format("tabla", "filas", "estado"))
    faltan = []
    for tabla, para_que in TABLAS:
        if existe(db, tabla):
            print("  {:<22} {:>9,}  ok -- {}".format(tabla, filas(db, tabla), para_que))
        else:
            print("  {:<22} {:>9}  FALTA -- {}".format(tabla, "-", para_que))
            faltan.append(tabla)

    print("\n  tablas que la app crea sola al arrancar:")
    for tabla in SE_CREAN_SOLAS:
        estado = "ya existe ({:,} filas)".format(filas(db, tabla)) if existe(db, tabla) \
                 else "todavia no, se crea al primer uso"
        print("    {:<20} {}".format(tabla, estado))

    grandes = [t for t in ("newstocks_cidef", "orden_trabajo", "registros") if existe(db, t)]
    print("\n  tablas grandes presentes: {}".format(", ".join(grandes) or "NINGUNA"))
    db.close()

    if faltan:
        print("\nFALTAN {} tabla(s): {}".format(len(faltan), ", ".join(faltan)))
        print("Generá la semilla en local y aplicala:")
        print("  python scripts/semilla_volumen.py --exportar semilla.sql.gz")
        print("  python scripts/semilla_volumen.py --aplicar semilla.sql.gz --db {}".format(ruta))
        return 1
    print("\nNo falta ninguna: la base ya esta al dia.")
    return 0


def exportar(ruta_db, destino):
    """Vuelca las tablas a un .sql.gz autocontenido.

    Se usa `iterdump` acotado a estas tablas y no un COPY binario para que el
    archivo sea legible y aplicable sobre una base que ya tiene datos, sin
    tocar el resto."""
    db = abrir(ruta_db)
    faltan_aca = [t for t, _ in TABLAS if not existe(db, t)]
    if faltan_aca:
        sys.exit("la base local tampoco tiene {}.\n"
                 "corre: python scripts/importar_dump.py --tablas {}"
                 .format(", ".join(faltan_aca), ",".join(faltan_aca)))

    partes = []
    for tabla, _ in TABLAS:
        # DROP + CREATE: la semilla deja la tabla exactamente como en local. Es
        # seguro porque son tablas de catalogo y de usuarios, que se importan
        # del dump y no se editan en produccion.
        partes.append('DROP TABLE IF EXISTS "{}";'.format(tabla))
        # Se arma a mano y no con iterdump(), que vuelca la base entera y no
        # sabe acotarse a unas pocas tablas.
        crear = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (tabla,)).fetchone()[0]
        partes.append(crear + ";")
        cols = [r[1] for r in db.execute('PRAGMA table_info("{}")'.format(tabla))]
        lista = ", ".join('"{}"'.format(c) for c in cols)
        for fila in db.execute('SELECT * FROM "{}"'.format(tabla)):
            valores = ", ".join(_literal(fila[c]) for c in cols)
            partes.append('INSERT INTO "{}" ({}) VALUES ({});'.format(tabla, lista, valores))
        for idx in db.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
                "AND sql IS NOT NULL", (tabla,)):
            partes.append(idx[0] + ";")

    cuerpo = "BEGIN;\n" + "\n".join(partes) + "\nCOMMIT;\n"
    with gzip.open(destino, "wb") as f:
        f.write(cuerpo.encode("utf-8"))

    db.close()
    kb = os.path.getsize(destino) / 1024.0
    print("semilla escrita en {} ({:.1f} KB, {:,} sentencias)".format(
        destino, kb, len(partes)))
    print("\nOJO: lleva dato personal de tbl_users (emails, RUT, hashes).")
    print("No lo subas al repo y borralo cuando termines.")
    return 0


def _literal(valor):
    if valor is None:
        return "NULL"
    if isinstance(valor, (int, float)):
        return repr(valor)
    if isinstance(valor, bytes):
        return "X'{}'".format(valor.hex())
    return "'" + str(valor).replace("'", "''") + "'"


def aplicar(origen, ruta_db):
    if not os.path.exists(origen):
        sys.exit("no existe la semilla {}".format(origen))
    with gzip.open(origen, "rb") as f:
        guion = f.read().decode("utf-8")

    db = abrir(ruta_db, para_escribir=True)
    antes = {t: (filas(db, t) if existe(db, t) else None) for t, _ in TABLAS}
    db.executescript(guion)
    db.commit()

    print("aplicada sobre {}\n".format(ruta_db))
    print("  {:<22} {:>9}  {:>9}".format("tabla", "antes", "despues"))
    for tabla, _ in TABLAS:
        previo = antes[tabla]
        print("  {:<22} {:>9}  {:>9,}".format(
            tabla, "(no estaba)" if previo is None else "{:,}".format(previo),
            filas(db, tabla)))
    db.close()
    print("\nListo. Reinicia el servicio si estaba corriendo.")
    return 0


def main():
    p = argparse.ArgumentParser(
        description="Lleva al volumen las tablas chicas importadas despues del deploy.")
    p.add_argument("--db", default=DB_POR_DEFECTO,
                   help="base sobre la que se trabaja (default: %(default)s)")
    p.add_argument("--revisar", action="store_true",
                   help="lista que tablas tiene esa base y cuales faltan")
    p.add_argument("--exportar", metavar="ARCHIVO",
                   help="genera la semilla .sql.gz desde --db")
    p.add_argument("--aplicar", metavar="ARCHIVO",
                   help="aplica una semilla .sql.gz sobre --db")
    args = p.parse_args()

    if args.exportar:
        return exportar(args.db, args.exportar)
    if args.aplicar:
        return aplicar(args.aplicar, args.db)
    return revisar(args.db)


if __name__ == "__main__":
    sys.exit(main())
