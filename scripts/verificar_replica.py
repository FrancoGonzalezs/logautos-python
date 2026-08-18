"""
verificar_replica.py -- comprueba que la replica local en SQLite quedo bien
cargada y mide que tan reales son las relaciones que el analisis de
migracion da por sentadas.

Esto no es un test de la app sino del DATO: en el sistema PHP original
practicamente no hay claves foraneas declaradas (VIN, id_vehiculo y
ot_relacionada son varchar/int sueltos), asi que antes de modelar nada en
Python conviene saber cuantas filas quedarian huerfanas si esas relaciones
se convirtieran en FK reales. Los porcentajes que imprime este script son
justamente el costo de agregar cada constraint.

Uso:
    python scripts/verificar_replica.py
"""

import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_POR_DEFECTO = os.path.join(BASE_DIR, "local.db")


def titulo(texto):
    print("\n" + texto)
    print("-" * len(texto))


def escalar(db, sql, *params):
    fila = db.execute(sql, params).fetchone()
    return fila[0] if fila else None


def pct(parte, total):
    return "{:.1f}%".format(100.0 * parte / total) if total else "-"


def conteos(db):
    titulo("Filas por tabla")
    for tabla in ["newstocks_cidef", "orden_trabajo", "reparaciones_externas",
                  "contenedor", "ot_contenedor"]:
        n = escalar(db, 'SELECT COUNT(*) FROM "{}"'.format(tabla))
        print("  {:<24} {:>9,}".format(tabla, n))


def unicidad_vin(db):
    """El analisis asume 'una fila por vehiculo en stock' en newstocks_cidef.
    Si el VIN se repite, esa premisa es falsa y la tabla es en realidad un
    historial de pasadas por el flujo, no un inventario -- cambia por
    completo como hay que modelarla en Python."""
    titulo("newstocks_cidef: el VIN, como llave")
    total = escalar(db, "SELECT COUNT(*) FROM newstocks_cidef")
    distintos = escalar(db, "SELECT COUNT(DISTINCT vin) FROM newstocks_cidef")
    vacios = escalar(db, "SELECT COUNT(*) FROM newstocks_cidef WHERE vin IS NULL OR TRIM(vin) = ''")
    print("  filas                {:>9,}".format(total))
    print("  VIN distintos        {:>9,}".format(distintos))
    print("  VIN vacios/NULL      {:>9,}  ({})".format(vacios, pct(vacios, total)))
    print("  filas por VIN        {:>9.2f} promedio".format(
        float(total) / distintos if distintos else 0))

    repetidos = escalar(db, """
        SELECT COUNT(*) FROM (
            SELECT vin FROM newstocks_cidef
            WHERE vin IS NOT NULL AND TRIM(vin) <> ''
            GROUP BY vin HAVING COUNT(*) > 1
        )""")
    print("  VIN con >1 fila      {:>9,}".format(repetidos))

    print("\n  los 5 VIN mas repetidos:")
    for vin, n in db.execute("""
            SELECT vin, COUNT(*) n FROM newstocks_cidef
            WHERE vin IS NOT NULL AND TRIM(vin) <> ''
            GROUP BY vin ORDER BY n DESC LIMIT 5"""):
        print("    {:<22} {:>5,} filas".format(vin, n))


def relacion(db, etiqueta, tabla_hija, col_hija, tabla_padre, col_padre, filtro=""):
    """Mide cuantas filas hija apuntan a un padre que no existe."""
    where_base = 'WHERE h."{}" IS NOT NULL AND TRIM(CAST(h."{}" AS TEXT)) <> \'\''.format(col_hija, col_hija)
    if filtro:
        where_base += " AND " + filtro
    total = escalar(db, 'SELECT COUNT(*) FROM "{}" h {}'.format(tabla_hija, where_base))
    huerfanas = escalar(db, """
        SELECT COUNT(*) FROM "{hija}" h
        {where}
          AND NOT EXISTS (SELECT 1 FROM "{padre}" p WHERE p."{cp}" = h."{ch}")
    """.format(hija=tabla_hija, where=where_base, padre=tabla_padre, cp=col_padre, ch=col_hija))
    print("  {:<52} {:>9,} con valor / {:>9,} huerfanas ({})".format(
        etiqueta, total, huerfanas, pct(huerfanas, total)))


def relaciones(db):
    titulo("Relaciones del analisis (§6): que pasaria si fueran FK reales")
    relacion(db, "orden_trabajo.id_vehiculo -> newstocks_cidef.id",
             "orden_trabajo", "id_vehiculo", "newstocks_cidef", "id")
    relacion(db, "reparaciones_externas.ot_relacionada -> orden_trabajo.id",
             "reparaciones_externas", "ot_relacionada", "orden_trabajo", "id")
    relacion(db, "reparaciones_externas.vin -> newstocks_cidef.vin",
             "reparaciones_externas", "vin", "newstocks_cidef", "vin")
    relacion(db, "orden_trabajo.patente -> newstocks_cidef.patente",
             "orden_trabajo", "patente", "newstocks_cidef", "patente")
    relacion(db, "ot_contenedor.n_guia -> orden_trabajo.id  (a confirmar)",
             "ot_contenedor", "n_guia", "orden_trabajo", "id")
    relacion(db, "ot_contenedor.n_contenedor -> contenedor.nro_contenedor",
             "ot_contenedor", "n_contenedor", "contenedor", "nro_contenedor")


def borrados_logicos(db):
    """El sistema PHP borra logico (isDeleted / deleted_at), asi que los
    conteos crudos de arriba incluyen filas que la app no muestra. Sin esto
    cualquier comparacion contra la pantalla del sistema viejo no cuadra."""
    titulo("Borrado logico: cuanto de lo cargado esta realmente vivo")
    for tabla, col, vivo in [
        ("newstocks_cidef", "deleted_at", 'deleted_at IS NULL'),
        ("orden_trabajo", "isDeleted", 'isDeleted = 0 OR isDeleted IS NULL'),
        ("reparaciones_externas", "estado", None),
    ]:
        cols = {r[1] for r in db.execute('PRAGMA table_info("{}")'.format(tabla))}
        if col not in cols:
            print("  {:<24} sin columna {}".format(tabla, col))
            continue
        total = escalar(db, 'SELECT COUNT(*) FROM "{}"'.format(tabla))
        if vivo:
            n = escalar(db, 'SELECT COUNT(*) FROM "{}" WHERE {}'.format(tabla, vivo))
            print("  {:<24} {:>9,} vivas de {:>9,}  ({})".format(tabla, n, total, pct(n, total)))
        else:
            print("  {:<24} valores de {}:".format(tabla, col))
            for valor, n in db.execute(
                    'SELECT "{}", COUNT(*) FROM "{}" GROUP BY 1 ORDER BY 2 DESC LIMIT 6'.format(col, tabla)):
                print("      {!r:<28} {:>9,}".format(valor, n))


def main():
    ruta = sys.argv[1] if len(sys.argv) > 1 else DB_POR_DEFECTO
    if not os.path.exists(ruta):
        print("no existe la replica: {} -- corre antes scripts/importar_dump.py".format(ruta),
              file=sys.stderr)
        return 1
    db = sqlite3.connect(ruta)
    print("replica: {}  ({:,} bytes)".format(ruta, os.path.getsize(ruta)))
    conteos(db)
    unicidad_vin(db)
    relaciones(db)
    borrados_logicos(db)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
