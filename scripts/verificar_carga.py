#!/usr/bin/env python3
"""
verificar_carga.py -- que la replica quedo COMPLETA, no que "parece".

    python scripts/verificar_carga.py --sql            # la consulta para phpMyAdmin
    python scripts/verificar_carga.py                  # solo conteos locales
    python scripts/verificar_carga.py --contra conteos.csv
    python scripts/verificar_carga.py --contra conteos.csv --db /data/local.db

POR QUE NO ALCANZA CON MIRAR LA BASE LOCAL

Una base cargada a medias se ve igual que una completa: abre, responde, tiene
tablas. El unico modo de saber que esta completa es comparar contra el origen,
tabla por tabla. Y el origen es Regla PHP, no la copia de la maquina de
desarrollo -- que tambien puede estar incompleta.

`verificar_replica.py` (el otro script) mide RELACIONES: cuantas filas
quedarian huerfanas si el VIN fuera una FK de verdad. Eso responde "que tan
sano es el dato". Esto responde "esta todo", que es otra pregunta y es la que
hace falta antes de encender nada.

LO QUE ESTE SCRIPT CORTA EN SECO

  1. Una tabla que falta o que vino a medias.
  2. `tbl_users` vacia o sin hashes: sin eso NADIE puede entrar, y el sintoma
     --"no me deja iniciar sesion"-- no dice que la carga quedo corta.
  3. Los indices de trabajo sin crear. La base ANDA sin ellos, solo que cada
     ficha de unidad recorre `registros` entera. El sintoma es "esta lento" y
     aparece dias despues, lejos de la causa.
"""

import argparse
import io
import os
import sqlite3
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Las tablas que vienen de Regla PHP.
DEL_LEGADO = [
    "newstocks_cidef", "orden_trabajo", "reparaciones_externas", "contenedor",
    "ot_contenedor", "validacion_color_unidad", "check_list",
    "check_list_mecanica", "entradas_salidas", "inspeccion_despacho",
    "ingresos_roro", "nivel_dano", "piezas", "promedio_pdi", "registros",
    "retornos", "tbl_roles", "tbl_users", "tipo_dano", "incidentes",
    "stock_consumibles",
]

# El mapa entidad -> tabla del pull. Se escribe aca y no se importa de
# `sync_legado` para que este script siga corriendo sobre una base suelta, sin
# la aplicacion cargada ni sus variables de entorno puestas.
TABLA_DE_ENTIDAD = {
    "unidades": "newstocks_cidef",
    "stock_consumibles": "stock_consumibles",
}

# Las entidades que piden todo en cada vuelta e ignoran la marca de agua.
COMPLETAS = {"stock_consumibles"}

# Sin estas dos no se puede ni entrar, asi que su ausencia no es un aviso.
IMPRESCINDIBLES = ["tbl_users", "tbl_roles"]

# Los indices que crea `importar_dump.py`. Si faltan, la base anda y es lenta.
INDICES_ESPERADOS = [
    "ix_newstocks_cidef_vin", "ix_newstocks_cidef_patente",
    "ix_newstocks_cidef_estadostock", "ix_newstocks_cidef_despachado",
    "ix_orden_trabajo_id_vehiculo", "ix_orden_trabajo_patente",
    "ix_reparaciones_externas_ot_relacionada", "ix_reparaciones_externas_vin",
    "ix_contenedor_nro_contenedor", "ix_ot_contenedor_n_guia",
    "ix_ot_contenedor_n_contenedor", "ix_check_list_vin",
    "ix_check_list_mecanica_vin", "ix_inspeccion_despacho_vin",
    "ix_registros_vin", "ix_incidentes_vin", "ix_retornos_vin",
    "ix_entradas_salidas_fecha", "ix_ingresos_roro_fecha",
    "ix_promedio_pdi_fecha",
]

SALTO = chr(10)
PROBLEMAS = []


def mal(texto):
    PROBLEMAS.append(texto)


def sql_para_phpmyadmin():
    """La consulta que se pega en phpMyAdmin. Una fila por tabla.

    `COUNT(*)` y NO `information_schema.TABLES.TABLE_ROWS`: en InnoDB esa
    columna es una ESTIMACION del optimizador y puede errarle por miles. Una
    verificacion que compara contra una estimacion no verifica nada."""
    partes = ["SELECT '{0}' AS tabla, COUNT(*) AS filas FROM `{0}`".format(t)
              for t in DEL_LEGADO]
    return (SALTO + "UNION ALL" + SALTO).join(partes) + SALTO + "ORDER BY tabla;"


def leer_conteos(ruta):
    """Lee lo que devolvio phpMyAdmin. Acepta CSV, con o sin cabecera, y
    tambien el texto pegado a mano con cualquier separador razonable."""
    conteos = {}
    with io.open(ruta, encoding="utf-8-sig", errors="replace") as f:
        for linea in f:
            linea = linea.strip().strip(";")
            if not linea:
                continue
            partes = None
            for sep in (",", ";", chr(9), "|"):
                if sep in linea:
                    partes = [p.strip().strip('"').strip("'")
                              for p in linea.split(sep)]
                    break
            if partes is None:
                partes = linea.split()
            if len(partes) < 2:
                continue
            tabla, valor = partes[0], partes[-1]
            limpio = valor.replace(".", "").replace(",", "")
            if not limpio.isdigit():
                continue          # la cabecera, o una linea de adorno
            conteos[tabla] = int(limpio)
    return conteos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(RAIZ, "local.db"))
    ap.add_argument("--contra", help="CSV con los conteos de Regla PHP")
    ap.add_argument("--sql", action="store_true",
                    help="imprime la consulta para phpMyAdmin y sale")
    args = ap.parse_args()

    if args.sql:
        print("-- Pegar en phpMyAdmin > SQL, sobre la base de Regla PHP.")
        print("-- Despues: Exportar el resultado como CSV y guardarlo.")
        print("")
        print(sql_para_phpmyadmin())
        return 0

    if not os.path.exists(args.db):
        print("no existe la base: {}".format(args.db))
        return 1

    db = sqlite3.connect(args.db)
    presentes = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    esperados = leer_conteos(args.contra) if args.contra else {}

    print("")
    print("base: {}  ({:.1f} MB)".format(args.db,
                                         os.path.getsize(args.db) / 1e6))
    print("")
    print("TABLAS DE REGLA PHP")
    if esperados:
        print("  {:<26} {:>10} {:>10} {:>9}".format(
            "tabla", "replica", "Regla PHP", "dif"))
    else:
        print("  {:<26} {:>10}".format("tabla", "replica"))

    for t in DEL_LEGADO:
        if t not in presentes:
            print("  {:<26} {:>10}   <- FALTA LA TABLA".format(t, "-"))
            if t in IMPRESCINDIBLES:
                mal("falta {}: sin ella nadie puede iniciar sesion".format(t))
            else:
                mal("falta la tabla {}".format(t))
            continue
        n = db.execute('SELECT COUNT(*) FROM "{}"'.format(t)).fetchone()[0]
        if not esperados:
            print("  {:<26} {:>10}".format(t, n))
            continue
        e = esperados.get(t)
        if e is None:
            print("  {:<26} {:>10} {:>10} {:>9}".format(t, n, "?", "sin dato"))
            mal("{}: no vino en los conteos de Regla PHP, no se pudo "
                "comparar".format(t))
            continue
        dif = n - e
        nota = "" if dif == 0 else "   <- NO COINCIDE"
        print("  {:<26} {:>10} {:>10} {:>+9}{}".format(t, n, e, dif, nota))
        # Menos filas es una carga incompleta. Mas puede ser legitimo --filas
        # de prueba con id alto-- pero se avisa igual: nadie deberia tener que
        # adivinar de donde salieron.
        if dif < 0:
            mal("{}: faltan {} filas respecto de Regla PHP".format(t, -dif))
        elif dif > 0:
            mal("{}: la replica tiene {} filas de MAS".format(t, dif))

    print("")
    print("LO QUE HACE FALTA PARA PODER ENTRAR")
    if "tbl_users" in presentes:
        total = db.execute("SELECT COUNT(*) FROM tbl_users").fetchone()[0]
        activos = db.execute(
            "SELECT COUNT(*) FROM tbl_users "
            " WHERE COALESCE(isDeleted,0)=0").fetchone()[0]
        con_clave = db.execute(
            "SELECT COUNT(*) FROM tbl_users "
            " WHERE COALESCE(isDeleted,0)=0 AND COALESCE(password,'') <> ''"
        ).fetchone()[0]
        print("  usuarios              {:>10}".format(total))
        print("  activos               {:>10}".format(activos))
        print("  activos con hash      {:>10}".format(con_clave))
        if con_clave == 0:
            mal("ningun usuario activo tiene hash de contrasena: nadie va a "
                "poder entrar")
        # El hash de bcrypt de Regla PHP empieza con $2y$; el de Python con
        # $2b$. Los dos los valida `acceso.clave_valida`. Una tabla sin ningun
        # $2 adentro significa que la columna vino de otra cosa.
        raros = db.execute(
            "SELECT COUNT(*) FROM tbl_users "
            " WHERE COALESCE(isDeleted,0)=0 AND COALESCE(password,'') <> '' "
            "   AND password NOT LIKE '$2%'").fetchone()[0]
        if raros:
            print("  con hash NO bcrypt    {:>10}   <- revisar".format(raros))
    else:
        print("  tbl_users             NO ESTA")

    if "tbl_roles" in presentes:
        print("  roles                 {:>10}".format(
            db.execute("SELECT COUNT(*) FROM tbl_roles").fetchone()[0]))

    print("")
    print("LA MARCA DE AGUA DEL PULL")
    #
    # Es la comprobacion mas importante de este script, y la menos visible.
    #
    # Una marca por DELANTE del dato no rompe nada hoy: el pull corre, dice ok,
    # y no trae los cambios que quedaron en el medio. La replica no queda con
    # filas faltantes -- queda CONVENCIDA de que esta al dia. No hay sintoma.
    #
    # Pasa de dos maneras, las dos vistas: un pull sobre una base sin tablas
    # que igual avanzaba la marca (arreglado en `sync_legado`, pero lo que ya
    # quedo escrito sigue escrito), y una carga que importa un volcado sobre
    # una base cuya marca es posterior a ese volcado.
    if "sync_estado" not in presentes:
        print("  sync_estado NO ESTA: el pull no corrio nunca sobre esta base")
        mal("falta sync_estado: la marca de agua no esta fijada, y sin ella "
            "no se sabe desde cuando va a pedir el pull")
    else:
        filas = list(db.execute(
            "SELECT entidad, marca_agua, ultimo_resultado FROM sync_estado "
            " ORDER BY entidad"))
        if not filas:
            print("  sin filas")
            mal("sync_estado vacia: la marca de agua no quedo fijada")
        for entidad, marca, resultado in filas:
            # `stock_consumibles` pide todo en cada vuelta e IGNORA la marca,
            # asi que vacia es su valor correcto y no un problema.
            if entidad in COMPLETAS:
                print("  {:<20} (completa: no usa marca)".format(entidad))
                continue
            tabla = TABLA_DE_ENTIDAD.get(entidad)
            tope = None
            if tabla and tabla in presentes:
                cols = {c[1] for c in db.execute(
                    'PRAGMA table_info("{}")'.format(tabla))}
                if "updated_at" in cols:
                    tope = db.execute(
                        'SELECT MAX(updated_at) FROM "{}" WHERE updated_at '
                        " IS NOT NULL AND TRIM(updated_at) NOT IN "
                        "('', '0000-00-00 00:00:00', '0000-00-00')".format(
                            tabla)).fetchone()[0]
            print("  {:<20} marca={!r}  dato mas nuevo={!r}  ({})".format(
                entidad, marca, tope, resultado))
            if not marca:
                # Vacia es SEGURO: significa "traer todo". Se avisa igual
                # porque el proximo pull va a ser el completo.
                print("     vacia = traer todo. Seguro, pero el proximo pull "
                      "baja la entidad entera.")
                continue
            if tope and marca > tope:
                mal("{}: la marca de agua ({}) es POSTERIOR al dato mas nuevo "
                    "de la replica ({}). Todo lo que cambio en Regla PHP entre "
                    "esas dos fechas no se va a pedir nunca, y el pull va a "
                    "decir que anduvo bien.".format(entidad, marca, tope))

    print("")
    print("INDICES DE TRABAJO")
    hay = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    faltan = [i for i in INDICES_ESPERADOS if i not in hay]
    print("  esperados {}, presentes {}".format(
        len(INDICES_ESPERADOS), len(INDICES_ESPERADOS) - len(faltan)))
    for i in faltan:
        print("     falta {}".format(i))
    if faltan:
        mal("faltan {} indices de trabajo: la base anda y va a estar lenta, y "
            "el sintoma aparece lejos de la causa".format(len(faltan)))

    print("")
    print("=" * 64)
    if PROBLEMAS:
        print("LA CARGA NO ESTA COMPLETA. {} cosa(s):".format(len(PROBLEMAS)))
        for p in PROBLEMAS:
            print("   - {}".format(p))
        return 1
    if not esperados:
        print("conteos locales impresos. Para AFIRMAR que esta completa hace")
        print("falta compararlos contra Regla PHP:")
        print("   python scripts/verificar_carga.py --sql")
        print("   (pegar en phpMyAdmin, exportar el resultado a CSV)")
        print("   python scripts/verificar_carga.py --contra conteos.csv")
        return 0
    print("la carga esta completa: coincide con Regla PHP tabla por tabla,")
    print("tbl_users tiene con que iniciar sesion, y los indices estan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
