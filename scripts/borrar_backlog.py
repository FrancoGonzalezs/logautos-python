#!/usr/bin/env python3
"""
scripts/borrar_backlog.py -- borra movimientos de REGLA con TODO lo que cuelga.

    python scripts/borrar_backlog.py --conservar 17            # muestra y no toca
    python scripts/borrar_backlog.py --conservar 17 --borrar   # ejecuta

Sin `--borrar` no escribe nada: imprime exactamente que filas saldrian y de que
tablas. Es la unica forma de revisar un borrado antes de hacerlo.


POR QUE EXISTE
==============

El 2026-08-28 quedaron 17 movimientos en Railway, y solo el 17 -- unidad 92095,
stock, PATIO 2 - A -- llego al sistema anterior. Los otros dieciseis son de
antes de que existiera el push de movimientos: pruebas sobre una copia que se
descarta en el corte.

Dejarlos significa que la reconciliacion reporte para siempre unidades
divergentes que nadie va a arreglar, y una categoria con ruido deja de mirarse
-- que es la leccion que ya nos costo `SOLICITUD DESPACHO` en la categoria 2.
El 17 se conserva: es la evidencia de que el circuito cierra.


NO ES UN DELETE SUELTO
======================

`movimientos_regla` no tiene foreign keys -- `PRAGMA foreign_keys=ON` esta
explicitamente afuera del proyecto -- asi que NADA cascadea. Cada tabla hay que
nombrarla, y en orden: primero las hijas, despues la madre.

CINCO tablas cuelgan de un movimiento por `movimiento_id`:

    check_list_regla           el movimiento 1
    pdi_regla                  los movimientos 5, 11, 12, 13
    it_regla                   -
    revision_unidad_regla      -
    inspeccion_despacho_regla  -

Se descubren SOLAS, mirando el esquema, en vez de escribir la lista a mano: la
lista a mano envejece el dia que alguien agrega una tabla, y el sintoma seria
una fila hija apuntando a un movimiento que ya no existe.

Y DOS que cuelgan por `python_id`, que es lo que mas facil se olvida:

    sync_push_pendientes   la cola del push
    sync_conflictos        los conflictos registrados

Ojo con estas dos: la clave es (entidad, python_id), y `python_id` NO siempre
apunta a `movimientos_regla`. Para entidad='pdi' apunta a `pdi_regla`, para
'ot_pdi' tambien, y para 'stock_consumibles' igual. Borrar por python_id sin
mirar la entidad borraria entradas de otra tabla que casualmente comparte el
numero.


EL HUERFANO QUE IMPORTA, Y NO ES UNA FILA
=========================================

`newstocks_cidef.push_pendiente`. Lo pone en 1 quien encola y lo baja a 0 quien
resuelve. Si se borra una entrada de cola sin resolver, la unidad queda con el
flag en 1 PARA SIEMPRE -- y el UPSERT del pull SALTEA las filas con el flag en
1. O sea: esa unidad dejaria de recibir actualizaciones del legado, en silencio
y sin que ningun error lo diga.

Es el peor resultado posible de un borrado que parece inocente, y por eso este
script lo recalcula al final: pone el flag en 0 en toda unidad que se quede sin
entradas sin resolver.


LO QUE QUEDA HUERFANO EN DISCO, Y SE DEJA A PROPOSITO
=====================================================

`check_list_regla` guarda fotos en DATA_DIR/uploads/check_list y
`inspeccion_despacho_regla` referencia archivo1..archivo9. Al borrar la fila,
esos archivos quedan sin dueño.

NO se borran. Son 288 KB en total, no molestan a nadie, y un script que borra
filas Y archivos es un script que puede borrar el archivo de una fila que no
era. Si alguna vez pesan, se limpian aparte comparando contra las filas vivas
-- que es una operacion distinta y con su propio dry-run.
"""

import argparse
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SECRET_KEY", "prueba")

# Las entidades de la cola cuyo `python_id` apunta a `movimientos_regla`. Las
# demas apuntan a otras tablas y no se tocan por este camino.
ENTIDADES_DE_MOVIMIENTO = ("movimientos",)

# Y por cada tabla hija, la entidad de cola cuyo `python_id` la referencia.
ENTIDAD_DE_TABLA = {
    "pdi_regla": ("pdi", "ot_pdi", "stock_consumibles"),
    "it_regla": ("it",),
    "check_list_mecanica_regla": ("check_list_mecanica",
                                  "check_list_mecanica_falla",
                                  "check_mecanica_unidad"),
}


def tablas_hijas(db):
    """Las tablas que tienen `movimiento_id`, descubiertas del esquema."""
    hijas = []
    for (nombre,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "  AND name LIKE '%_regla' AND name <> 'movimientos_regla'"):
        columnas = [r[1] for r in db.execute('PRAGMA table_info("{}")'.format(nombre))]
        if "movimiento_id" in columnas:
            hijas.append(nombre)
    return sorted(hijas)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conservar", type=int, nargs="*", default=[],
                    help="ids de movimientos_regla que NO se borran")
    ap.add_argument("--borrar", action="store_true",
                    help="ejecuta de verdad; sin esto solo muestra")
    ap.add_argument("--db", default=os.environ.get("DB_PATH")
                    or os.path.join(RAIZ, "local.db"))
    args = ap.parse_args()

    import sqlite3
    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row

    todos = [r["id"] for r in db.execute(
        "SELECT id FROM movimientos_regla ORDER BY id")]
    conservar = set(args.conservar)
    a_borrar = [i for i in todos if i not in conservar]

    print("base: {}".format(args.db))
    print("movimientos en total : {}".format(len(todos)))
    print("se conservan         : {}".format(sorted(conservar) or "ninguno"))
    print("se borrarian         : {}\n".format(len(a_borrar)))
    if not a_borrar:
        print("nada que hacer")
        return 0

    faltan = conservar - set(todos)
    if faltan:
        print("  OJO: los ids a conservar {} NO existen en esta base. "
              "Revisa que sea la base correcta.".format(sorted(faltan)))
        return 1

    marcas = ", ".join("?" * len(a_borrar))

    print("LOS MOVIMIENTOS:")
    for r in db.execute(
            "SELECT id, unidad_id, paso, estado_hacia, creado_en "
            "  FROM movimientos_regla WHERE id IN ({}) ORDER BY id".format(marcas),
            a_borrar):
        print("   {:>4}  unidad {:<8} {:<22} -> {:<28} {}".format(
            r["id"], r["unidad_id"], r["paso"],
            r["estado_hacia"] or "(sin arco)", r["creado_en"]))

    hijas = tablas_hijas(db)
    print("\nLAS FILAS HERMANAS (se borran ANTES que el movimiento):")
    plan_hijas = {}
    for tabla in hijas:
        ids = [r["id"] for r in db.execute(
            'SELECT id FROM "{}" WHERE movimiento_id IN ({})'.format(tabla, marcas),
            a_borrar)]
        plan_hijas[tabla] = ids
        print("   {:<28} {}".format(tabla, ids or "ninguna"))

    print("\nLA COLA Y LOS CONFLICTOS:")
    cola = {}
    for tabla_cola in ("sync_push_pendientes", "sync_conflictos"):
        filas = []
        # Por el movimiento.
        marcas_e = ", ".join("?" * len(ENTIDADES_DE_MOVIMIENTO))
        filas += [dict(r) for r in db.execute(
            'SELECT id, entidad, python_id FROM "{}" '
            ' WHERE entidad IN ({}) AND python_id IN ({})'.format(
                tabla_cola, marcas_e, marcas),
            list(ENTIDADES_DE_MOVIMIENTO) + a_borrar)]
        # Y por cada hija: su propia entidad, con SU id.
        for tabla, ids in plan_hijas.items():
            for entidad in ENTIDAD_DE_TABLA.get(tabla, ()):
                if not ids:
                    continue
                m = ", ".join("?" * len(ids))
                filas += [dict(r) for r in db.execute(
                    'SELECT id, entidad, python_id FROM "{}" '
                    ' WHERE entidad = ? AND python_id IN ({})'.format(tabla_cola, m),
                    [entidad] + ids)]
        cola[tabla_cola] = filas
        print("   {:<28} {}".format(
            tabla_cola,
            [(f["entidad"], f["python_id"]) for f in filas] or "ninguna"))

    # -- el huerfano que importa ------------------------------------------
    unidades = sorted({r["unidad_id"] for r in db.execute(
        "SELECT unidad_id FROM movimientos_regla WHERE id IN ({})".format(marcas),
        a_borrar) if r["unidad_id"]})
    marcas_u = ", ".join("?" * len(unidades)) if unidades else "NULL"
    con_flag = [r["id"] for r in db.execute(
        'SELECT id FROM newstocks_cidef WHERE push_pendiente = 1 '
        '  AND id IN ({})'.format(marcas_u), unidades)] if unidades else []
    print("\nEL FLAG `push_pendiente` (el huerfano que deja ciega a la unidad):")
    print("   unidades tocadas          : {}".format(unidades or "ninguna"))
    print("   con el flag en 1 hoy      : {}".format(con_flag or "ninguna"))
    print("   se recalcula al final: queda en 0 la que no tenga entradas sin "
          "resolver")

    if not args.borrar:
        print("\n" + "=" * 66)
        print("NO SE TOCO NADA. Para ejecutar, agrega --borrar")
        return 0

    # -- ejecucion, TODO en una transaccion --------------------------------
    #
    # O sale entero o no sale nada: un borrado a medias deja hijas apuntando a
    # un movimiento que ya no esta, que es peor que el estado de partida.
    print("\n" + "=" * 66)
    try:
        db.execute("BEGIN")
        for tabla in hijas:                       # 1. las hijas
            db.execute('DELETE FROM "{}" WHERE movimiento_id IN ({})'.format(
                tabla, marcas), a_borrar)
        for tabla_cola, filas in cola.items():    # 2. la cola
            for f in filas:
                db.execute('DELETE FROM "{}" WHERE id = ?'.format(tabla_cola),
                           (f["id"],))
        db.execute(                               # 3. la madre
            "DELETE FROM movimientos_regla WHERE id IN ({})".format(marcas),
            a_borrar)
        if unidades:                              # 4. el flag
            db.execute(
                'UPDATE newstocks_cidef SET push_pendiente = 0 '
                ' WHERE id IN ({}) AND NOT EXISTS ('
                '   SELECT 1 FROM sync_push_pendientes p '
                '    WHERE p.legado_id = newstocks_cidef.id '
                '      AND p.resuelto_en = \'\')'.format(marcas_u), unidades)
        db.commit()
    except Exception as e:                        # noqa: BLE001
        db.rollback()
        print("FALLO, no se borro nada: {}: {}".format(type(e).__name__, e))
        return 1

    quedan = db.execute("SELECT COUNT(*) FROM movimientos_regla").fetchone()[0]
    huerfanas = 0
    for tabla in hijas:
        huerfanas += db.execute(
            'SELECT COUNT(*) FROM "{}" h WHERE h.movimiento_id IS NOT NULL '
            '  AND NOT EXISTS (SELECT 1 FROM movimientos_regla m '
            '                   WHERE m.id = h.movimiento_id)'.format(tabla)
        ).fetchone()[0]
    print("listo. movimientos que quedan: {}".format(quedan))
    print("filas hijas huerfanas: {}  (tiene que ser 0)".format(huerfanas))
    return 0 if huerfanas == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
