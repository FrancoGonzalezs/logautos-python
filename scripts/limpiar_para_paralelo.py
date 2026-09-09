#!/usr/bin/env python3
"""
limpiar_para_paralelo.py -- deja la réplica con SOLO los datos de Regla PHP.

    python scripts/limpiar_para_paralelo.py --db /data/local.db           # muestra
    python scripts/limpiar_para_paralelo.py --db /data/local.db --borrar  # ejecuta

Sin `--borrar` no escribe nada.

PARA QUE SIRVE
==============

Cuando la réplica del proyecto nuevo se arma COPIANDO la del viejo, viene todo:
las tablas de Regla PHP --que es lo que se quiere-- y también los datos propios
de Regla Python, que son movimientos de prueba sobre una copia congelada. El
paralelo arranca limpio, así que esos salen.

LO QUE SE CONSERVA, Y ES EL PUNTO DE COPIAR EN VEZ DE IMPORTAR
==============================================================

`sync_estado` NO SE TOCA.

Ésa es la ventaja entera de esta vía: la base y su marca de agua viajan juntas,
consistentes por construcción, porque salieron del mismo archivo en el mismo
instante. Con un volcado de phpMyAdmin hay que reponer la marca a mano --y ahí
aparecen los dos relojes del volcado, el margen, y la verificación de que no
quede adelantada--. Copiando, ese problema no existe.

Borrar `sync_estado` acá sería tirar exactamente lo que se vino a buscar.

LA LISTA ES DE LO QUE SE CONSERVA, NO DE LO QUE SE BORRA
========================================================

Y una tabla que no esté en ninguna de las dos listas FRENA el script.

Es al revés de lo natural, a propósito. Si la lista fuera de lo que se borra,
una tabla nueva de Regla Python sobreviviría en silencio a la limpieza. Si
fuera "borrá todo lo que no reconozcas", una tabla nueva de Regla PHP se
perdería en silencio. Las dos fallas son mudas. Frenar y nombrarla no lo es.

EL HUÉRFANO QUE NO ES UNA FILA
==============================

`newstocks_cidef.push_pendiente`. Lo pone en 1 quien encola y lo baja quien
resuelve. Al vaciar `sync_push_pendientes` sin más, toda unidad con el flag en 1
queda así PARA SIEMPRE -- y el UPSERT del pull SALTEA esas filas. Esa unidad
dejaría de recibir actualizaciones de Regla PHP, en silencio y sin que ningún
error lo diga.

Es el mismo huérfano que ya documentó `borrar_backlog.py`, y por eso este
script también lo recalcula.
"""

import argparse
import os
import sqlite3
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Las tablas de Regla PHP: se conservan enteras. Es la misma lista que usan
# `importar_dump.py` y `verificar_carga.py`.
DE_REGLA_PHP = [
    "newstocks_cidef", "orden_trabajo", "reparaciones_externas", "contenedor",
    "ot_contenedor", "validacion_color_unidad", "check_list",
    "check_list_mecanica", "entradas_salidas", "inspeccion_despacho",
    "ingresos_roro", "nivel_dano", "piezas", "promedio_pdi", "registros",
    "retornos", "tbl_roles", "tbl_users", "tipo_dano", "incidentes",
    "stock_consumibles", "fotos_it",
]

# Propias de Regla Python que TAMBIEN se conservan, con el motivo al lado.
SE_CONSERVAN_IGUAL = {
    "sync_estado":
        "LA MARCA DE AGUA. Es lo que hace que copiar sea mejor que importar: "
        "viaja consistente con los datos porque salio del mismo archivo.",
    "sqlite_stat1":
        "estadisticas del planificador; se regeneran solas con ANALYZE, pero "
        "conservarlas evita que las primeras consultas vayan a ciegas.",
}

# Propias de Regla Python que se vacian. Cada una con por que se puede.
SE_VACIAN = {
    "movimientos_regla":        "movimientos de prueba sobre la copia congelada",
    "it_regla":                 "cuelga de un movimiento de prueba",
    "pdi_regla":                "idem",
    "check_list_regla":         "idem",
    "check_list_mecanica_regla": "idem",
    "inspeccion_despacho_regla": "idem",
    "inspeccion_despacho_fotos_regla": "idem",
    "revision_unidad_regla":    "idem",
    "contenedor_regla":         "idem",
    "validacion_color_regla":   "idem",
    "sync_push_pendientes":
        "LA COLA DEL PUSH. Si viaja, el dia que se encienda el push del "
        "proyecto nuevo saldrian hacia Regla PHP escrituras de pruebas viejas.",
    "sync_conflictos":          "historial de conflictos de las pruebas",
    "avisos_pendientes_regla":
        "la cola de correos. Igual que la del push: no puede salir un correo "
        "de una prueba de agosto.",
    "fotos_publicadas":
        "los tokens apuntan a archivos de DATA_DIR del proyecto VIEJO, que no "
        "se copian. Dejarlos serian URLs que dan 404.",
    "reconciliacion":           "el estado de la ultima corrida",
    "permisos_regla":           "se resiembra sola al arrancar",
    "intentos_bloqueados_regla": "intentos frenados durante las pruebas",
}

PROBLEMAS = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(RAIZ, "local.db"))
    ap.add_argument("--borrar", action="store_true",
                    help="sin esto no escribe nada")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print("no existe la base: {}".format(args.db))
        return 1

    db = sqlite3.connect(args.db)
    presentes = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

    # -- LA GUARDA: ninguna tabla sin clasificar -------------------------
    conocidas = set(DE_REGLA_PHP) | set(SE_CONSERVAN_IGUAL) | set(SE_VACIAN)
    desconocidas = [t for t in presentes
                    if t not in conocidas and not t.startswith("sqlite_")]
    if desconocidas:
        print("")
        print("FRENO: hay tablas que este script no sabe clasificar.")
        for t in desconocidas:
            n = db.execute('SELECT COUNT(*) FROM "{}"'.format(t)).fetchone()[0]
            print("   {:<34} {:>8} filas".format(t, n))
        print("")
        print("No se borra ni se conserva nada por las dudas: agregalas a")
        print("DE_REGLA_PHP, a SE_CONSERVAN_IGUAL o a SE_VACIAN, con el")
        print("motivo al lado, y volvé a correr.")
        return 1

    print("")
    print("base: {}  ({:.1f} MB)".format(args.db,
                                         os.path.getsize(args.db) / 1e6))
    print("")
    print("SE CONSERVA (Regla PHP)")
    faltan = []
    for t in DE_REGLA_PHP:
        if t not in presentes:
            faltan.append(t)
            print("   {:<34}  NO ESTA".format(t))
            continue
        n = db.execute('SELECT COUNT(*) FROM "{}"'.format(t)).fetchone()[0]
        print("   {:<34} {:>9,}".format(t, n))

    print("")
    print("SE CONSERVA IGUAL, aunque sea de Regla Python")
    for t, porque in sorted(SE_CONSERVAN_IGUAL.items()):
        if t not in presentes:
            print("   {:<34}  no esta".format(t))
            continue
        n = db.execute('SELECT COUNT(*) FROM "{}"'.format(t)).fetchone()[0]
        print("   {:<34} {:>9,}   {}".format(t, n, porque.split(".")[0]))

    if "sync_estado" in presentes:
        print("")
        print("   la marca de agua que se conserva:")
        for e, m, r in db.execute(
                "SELECT entidad, marca_agua, ultimo_resultado FROM sync_estado "
                " ORDER BY entidad"):
            print("      {:<20} {!r}  ({})".format(e, m, r))

    print("")
    print("SE VACIA")
    total = 0
    a_vaciar = []
    for t in sorted(SE_VACIAN):
        if t not in presentes:
            continue
        n = db.execute('SELECT COUNT(*) FROM "{}"'.format(t)).fetchone()[0]
        total += n
        if n:
            a_vaciar.append(t)
            print("   {:<34} {:>9,}   {}".format(t, n, SE_VACIAN[t].split(".")[0]))
    if not a_vaciar:
        print("   (ninguna tiene filas)")
    print("   {:<34} {:>9,}".format("TOTAL", total))

    # -- El flag, que no es una fila -------------------------------------
    pendientes = db.execute(
        "SELECT COUNT(*) FROM newstocks_cidef "
        " WHERE COALESCE(push_pendiente, 0) = 1").fetchone()[0]
    print("")
    print("EL FLAG QUE NO ES UNA FILA")
    print("   newstocks_cidef.push_pendiente = 1 : {}".format(pendientes))
    if pendientes:
        print("      Se bajan a 0. Dejarlos en 1 con la cola vacia haria que")
        print("      el pull SALTEE esas unidades para siempre, en silencio.")

    # -- Un aviso que no es un problema, pero hay que verlo ---------------
    if "fotos_publicadas" in presentes:
        publicadas = db.execute(
            "SELECT COUNT(*) FROM fotos_publicadas").fetchone()[0]
        if publicadas:
            print("")
            print("LAS FOTOS PUBLICADAS SE LISTAN UNA POR UNA: {}".format(
                publicadas))
            print("")
            print("   Una URL que ya haya viajado a archivo1..archivo9 de Regla")
            print("   PHP queda en 404 si se borra su token: Regla PHP guarda la")
            print("   URL, no el archivo. Asi que esto NO se decide de memoria --")
            print("   se mira lo que hay ACA, en esta base, antes de borrar.")
            print("")
            print("   {:<22} {:<12} {:<20} {}".format(
                "origen", "referencia", "publicada", "ruta"))
            for o, r, f, ruta in db.execute(
                    "SELECT origen, referencia, publicada_en, ruta "
                    "  FROM fotos_publicadas ORDER BY publicada_en, rowid LIMIT 40"):
                print("   {:<22} {:<12} {:<20} {}".format(
                    (o or "")[:22], (str(r) or "")[:12], (f or "")[:20],
                    (ruta or "")[:44]))
            if publicadas > 40:
                print("   ... y {} mas".format(publicadas - 40))
            print("")
            print("   Si TODAS son de pruebas --origen `check_list_mecanica` o")
            print("   `prueba`, sobre unidades PRUEBA-- se borran sin problema.")
            print("   Si alguna es de una inspeccion REAL, hay que decidirla")
            print("   aparte ANTES de correr con --borrar.")

    if not args.borrar:
        print("")
        print("=" * 64)
        print("no se toco nada. Para ejecutar:  --borrar")
        return 0

    # -- Ejecutar ---------------------------------------------------------
    for t in a_vaciar:
        db.execute('DELETE FROM "{}"'.format(t))
    if pendientes:
        db.execute("UPDATE newstocks_cidef SET push_pendiente = 0 "
                   " WHERE COALESCE(push_pendiente, 0) = 1")
    db.commit()

    # -- Y comprobar que quedo como se dijo -------------------------------
    print("")
    print("=" * 64)
    print("COMPROBACION")
    for t in a_vaciar:
        n = db.execute('SELECT COUNT(*) FROM "{}"'.format(t)).fetchone()[0]
        if n:
            PROBLEMAS.append("{} quedo con {} filas".format(t, n))
    n = db.execute("SELECT COUNT(*) FROM newstocks_cidef "
                   " WHERE COALESCE(push_pendiente,0)=1").fetchone()[0]
    if n:
        PROBLEMAS.append("quedaron {} unidades con push_pendiente=1".format(n))
    if "sync_estado" in presentes:
        n = db.execute("SELECT COUNT(*) FROM sync_estado").fetchone()[0]
        if n == 0:
            PROBLEMAS.append(
                "sync_estado quedo VACIA: se perdio la marca de agua, que es "
                "justo lo que esta via viene a conservar")
    for t in DE_REGLA_PHP:
        if t in presentes:
            n = db.execute('SELECT COUNT(*) FROM "{}"'.format(t)).fetchone()[0]
            if n == 0 and t not in ("incidentes", "fotos_it"):
                PROBLEMAS.append("{} quedo vacia y es de Regla PHP".format(t))

    if PROBLEMAS:
        for p in PROBLEMAS:
            print("   FALLA {}".format(p))
        return 1
    print("   las tablas de Regla Python quedaron vacias")
    print("   las de Regla PHP intactas")
    print("   la marca de agua conservada")
    print("   push_pendiente en 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
