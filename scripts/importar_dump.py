"""
importar_dump.py -- carga las tablas de Logautos desde el dump de MariaDB
(phpMyAdmin, 602 MB) a la replica local en SQLite.

Por que un importador propio y no "mysql < dump.sql": en esta maquina no hay
MariaDB ni Docker instalados, y la decision fue seguir el patron de Talca
(SQLite local + push al PHP remoto). Asi que el dump hay que traducirlo, no
importarlo.

El dump viene con tres cosas que SQLite no traga tal cual:

  1. Escapes con backslash de MySQL (\\' \\" \\\\ \\n \\r \\0 \\Z). SQLite
     solo entiende el doblado de comillas ('' dentro de un literal), asi que
     los valores hay que parsearlos de verdad -- no alcanza con reemplazar
     texto, porque un backslash puede ser un escape o un caracter literal
     dentro de una ruta de foto tipo C:\\fotos\\x.jpg segun donde caiga.

  2. phpMyAdmin declara las claves primarias e indices en ALTER TABLE al
     final del archivo, no dentro del CREATE TABLE. SQLite no soporta
     ALTER TABLE ADD PRIMARY KEY, asi que hay que conocer la PK ANTES de
     crear la tabla -- de ahi que el script haga dos pasadas sobre el
     archivo: la primera junta solo DDL, la segunda carga datos.

  3. Fechas cero ('0000-00-00'), legales en MariaDB e imposibles en casi
     cualquier otro motor. Se guardan como TEXT tal cual, sin intentar
     convertirlas: son datos reales del negocio (una unidad sin fecha de
     despacho), y convertirlas a NULL aca perderia la distincion entre
     "nunca se despacho" y "el campo venia vacio".

Uso:
    python scripts/importar_dump.py                    # tablas de Prioridad 1
    python scripts/importar_dump.py --tablas a,b,c     # tablas puntuales
    python scripts/importar_dump.py --dump RUTA --db RUTA
"""

import argparse
import io
import os
import re
import sqlite3
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DUMP_POR_DEFECTO = r"C:\Users\Franco\Documents\LOGAUTOS\base de datos\logautos_regla_claude.sql"
DB_POR_DEFECTO = os.path.join(BASE_DIR, "local.db")

# Prioridad 1 del analisis de migracion: el motor de unidades y el de ordenes
# de trabajo. El resto de las 121 tablas del dump se importa despues, por
# tanda, con --tablas.
TABLAS_PRIORIDAD_1 = [
    "newstocks_cidef",
    "orden_trabajo",
    "reparaciones_externas",
    "contenedor",
    "ot_contenedor",
    # No es del motor de unidades, pero entra acá porque sin ella la replica
    # no puede comparar contra produccion la validacion de modelo/color que
    # hace la Revision de Contenedor. Es chica -- 47 filas -- y su ausencia ya
    # obligo una vez a construir a ciegas: `validacion_color_regla` se diseño
    # sin poder mirar como estaba poblada la original.
    "validacion_color_unidad",
]


# ---------------------------------------------------------------------------
# Traduccion de tipos MySQL -> SQLite
# ---------------------------------------------------------------------------

def tipo_sqlite(tipo_mysql):
    """SQLite tiene afinidad de tipos, no tipos estrictos, asi que esto no
    valida nada -- solo elige la afinidad que hace que las comparaciones y
    los ORDER BY se comporten como en MariaDB. Lo importante es que los
    enteros queden INTEGER (para que id ordene numericamente y no como
    texto: '10' < '9') y que las fechas queden TEXT en formato ISO, que
    ordena bien lexicograficamente."""
    t = tipo_mysql.strip().lower()
    if t.startswith(("tinyint", "smallint", "mediumint", "bigint", "int", "year", "bit")):
        return "INTEGER"
    if t.startswith(("decimal", "numeric", "float", "double", "real")):
        return "REAL"
    if t.startswith(("blob", "binary", "varbinary", "tinyblob", "mediumblob", "longblob")):
        return "BLOB"
    return "TEXT"


re_create = re.compile(r"^CREATE TABLE `([^`]+)`")
re_columna = re.compile(r"^\s*`([^`]+)`\s+([a-zA-Z]+(?:\([^)]*\))?(?:\s+unsigned)?)(.*)$")
re_alter = re.compile(r"^ALTER TABLE `([^`]+)`")
re_pk = re.compile(r"ADD PRIMARY KEY \(([^)]*)\)")
re_indice = re.compile(r"ADD (UNIQUE )?KEY `([^`]+)` \(([^)]*)\)")
re_autoinc = re.compile(r"MODIFY `([^`]+)` [^,;]*AUTO_INCREMENT")
re_insert_cab = re.compile(r"^INSERT INTO `([^`]+)` \(([^)]*)\) VALUES\s*", re.S)


def _nombres_backtick(texto):
    return [n.strip(" `") for n in texto.split(",") if n.strip()]


# ---------------------------------------------------------------------------
# Pasada 1 -- estructura
# ---------------------------------------------------------------------------

def leer_ddl(ruta_dump, tablas_objetivo):
    """Recorre el dump juntando CREATE TABLE y los ALTER TABLE del final.

    Se salta las lineas de datos lo antes posible (la comparacion contra
    'INSERT'/'(' es lo primero que se evalua) porque el 99% de los 602 MB
    son INSERT y esta pasada no los necesita."""
    ddl = {}
    tabla_actual = None

    with io.open(ruta_dump, "r", encoding="utf-8", errors="replace", newline="") as f:
        for linea in f:
            if linea.startswith(("INSERT", "(")):
                continue

            if tabla_actual is not None:
                if linea.startswith(")"):
                    tabla_actual = None
                    continue
                m = re_columna.match(linea)
                if m:
                    nombre, tipo, resto = m.group(1), m.group(2), m.group(3)
                    ddl[tabla_actual]["columnas"].append({
                        "nombre": nombre,
                        "tipo": tipo_sqlite(tipo),
                        "tipo_mysql": tipo,
                        "notnull": " NOT NULL" in resto,
                    })
                continue

            m = re_create.match(linea)
            if m and m.group(1) in tablas_objetivo:
                tabla_actual = m.group(1)
                ddl[tabla_actual] = {"columnas": [], "pk": [], "indices": [], "autoinc": None}
                continue

            m = re_alter.match(linea)
            if m and m.group(1) in ddl:
                t = m.group(1)
                # phpMyAdmin parte el ALTER en varias lineas; la de PK/KEY
                # puede venir en la misma linea o en la siguiente, asi que
                # se buscan los patrones en cualquier linea entre ALTER y ';'
                _absorber_alter(f, linea, ddl[t])

    return ddl


def _absorber_alter(f, primera_linea, info):
    """Lee un ALTER TABLE completo (hasta el ';') y extrae PK, indices y
    AUTO_INCREMENT. Consume lineas del mismo iterador que la pasada
    principal, que es seguro porque un ALTER nunca contiene INSERT."""
    linea = primera_linea
    while True:
        m = re_pk.search(linea)
        if m:
            info["pk"] = _nombres_backtick(m.group(1))
        for unico, nombre, cols in re_indice.findall(linea):
            info["indices"].append({
                "nombre": nombre,
                "unico": bool(unico.strip()),
                "columnas": _nombres_backtick(cols),
            })
        m = re_autoinc.search(linea)
        if m:
            info["autoinc"] = m.group(1)

        if ";" in linea:
            return
        linea = next(f, None)
        if linea is None:
            return


def sql_create(tabla, info):
    """Arma el CREATE TABLE de SQLite.

    Caso especial: si la PK es una sola columna entera con AUTO_INCREMENT, se
    declara 'INTEGER PRIMARY KEY' para que sea alias del rowid -- asi el id
    se autogenera igual que en MariaDB cuando la app inserte filas nuevas.
    Con una PK compuesta o no entera se usa PRIMARY KEY (...) al final."""
    partes = []
    pk = info["pk"]
    pk_simple_entera = (
        len(pk) == 1
        and any(c["nombre"] == pk[0] and c["tipo"] == "INTEGER" for c in info["columnas"])
    )

    for col in info["columnas"]:
        linea = '"{}" {}'.format(col["nombre"], col["tipo"])
        if pk_simple_entera and col["nombre"] == pk[0]:
            linea += " PRIMARY KEY"
        # El NOT NULL del origen se deja caer a proposito: MariaDB acepta ''
        # y '0000-00-00' donde SQLite querria un valor real, y varias columnas
        # del dump son NOT NULL sin default. Replicar la restriccion haria
        # fallar inserts que en produccion funcionan hoy.
        partes.append(linea)

    if pk and not pk_simple_entera:
        partes.append('PRIMARY KEY ({})'.format(", ".join('"{}"'.format(c) for c in pk)))

    return 'CREATE TABLE "{}" (\n  {}\n)'.format(tabla, ",\n  ".join(partes))


# ---------------------------------------------------------------------------
# Pasada 2 -- datos
# ---------------------------------------------------------------------------

ESCAPES = {
    "0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t",
    "Z": "\x1a", "\\": "\\", "'": "'", '"': '"', "%": "\\%", "_": "\\_",
}


def parsear_valores(texto, pos):
    """Parsea las tuplas de un VALUES devolviendo listas de valores Python.

    Se hace a mano, caracter por caracter, en vez de con una regex: los
    campos de texto del dump traen comas, parentesis, comillas escapadas y
    saltos de linea reales adentro (observaciones, links de fotos), y
    cualquier regex que intente partir por ',' o ')' se rompe con el primer
    'observacion' que contenga una coma."""
    n = len(texto)
    filas = []

    while pos < n:
        while pos < n and texto[pos] in " \t\r\n,":
            pos += 1
        if pos >= n or texto[pos] == ";":
            break
        if texto[pos] != "(":
            pos += 1
            continue

        pos += 1
        fila = []
        while pos < n:
            while pos < n and texto[pos] in " \t\r\n":
                pos += 1
            if pos >= n:
                break
            c = texto[pos]

            if c == ")":
                pos += 1
                break
            if c == ",":
                pos += 1
                continue

            if c == "'":
                pos += 1
                trozos = []
                while pos < n:
                    c = texto[pos]
                    if c == "\\" and pos + 1 < n:
                        siguiente = texto[pos + 1]
                        trozos.append(ESCAPES.get(siguiente, siguiente))
                        pos += 2
                        continue
                    if c == "'":
                        # Comilla doblada ('') = comilla literal; una sola
                        # cierra el literal.
                        if pos + 1 < n and texto[pos + 1] == "'":
                            trozos.append("'")
                            pos += 2
                            continue
                        pos += 1
                        break
                    trozos.append(c)
                    pos += 1
                fila.append("".join(trozos))
                continue

            # Token sin comillas: NULL, numero, o palabra suelta.
            inicio = pos
            while pos < n and texto[pos] not in ",)":
                pos += 1
            token = texto[inicio:pos].strip()
            fila.append(_valor_token(token))

        filas.append(fila)

    return filas


def _valor_token(token):
    if token.upper() == "NULL":
        return None
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        return token


class LectorStatements(object):
    """Va entregando statements SQL completos del dump.

    No se puede partir por ';' a secas ni leer linea a linea: un INSERT de
    phpMyAdmin ocupa varias lineas y los textos adentro contienen tanto ';'
    como saltos de linea. Este lector arrastra el estado 'estoy dentro de un
    literal' entre lineas, que es lo unico que hace falta para saber si un
    ';' cierra el statement o es parte de una observacion.

    Las lineas de comentario ('--', '/*!') y las vacias se descartan cuando
    caen ENTRE statements, no se acumulan. Hay dos razones: la primera es que
    si no, el statement empieza con el bloque de comentarios que phpMyAdmin
    pone antes de cada tabla ("-- Volcado de datos para la tabla `x`") y deja
    de empezar con 'INSERT INTO', que es como se decide si la fila interesa
    -- eso hizo que en la primera version se perdiera el unico INSERT de
    ot_contenedor y el primero de cada tabla. La segunda es que un comentario
    en castellano puede traer un apostrofe suelto, y contarlo como apertura
    de literal desincronizaria todo lo que viene despues."""

    def __init__(self, f):
        self.f = f
        self.en_literal = False

    def __iter__(self):
        buffer = []
        for linea in self.f:
            if not buffer and not self.en_literal:
                pelada = linea.lstrip()
                if not pelada or pelada.startswith(("--", "/*")):
                    continue
            buffer.append(linea)
            if self._cierra_statement(linea):
                yield "".join(buffer)
                buffer = []
        if buffer:
            resto = "".join(buffer).strip()
            if resto:
                yield resto

    def _cierra_statement(self, linea):
        """Actualiza el estado de literal con lo que trae la linea y dice si
        el statement termina aca.

        El camino rapido (sin backslashes) resuelve la linea con un solo
        count(): dentro de una linea, cualquier cantidad par de comillas
        deja el estado como estaba y una impar lo invierte -- y eso vale
        tambien para las comillas dobladas ('') de MySQL, que suman dos. Se
        hizo asi porque recorrer caracter por caracter los 602 MB del dump
        costaba ~160s contra ~10s de esta version, y el 99% de las lineas no
        tiene un solo backslash."""
        if "\\" in linea:
            i, n = 0, len(linea)
            while i < n:
                c = linea[i]
                if self.en_literal:
                    if c == "\\":
                        i += 2
                        continue
                    if c == "'":
                        self.en_literal = False
                elif c == "'":
                    self.en_literal = True
                i += 1
        elif linea.count("'") % 2:
            self.en_literal = not self.en_literal

        return not self.en_literal and linea.rstrip().endswith(";")


def cargar_datos(ruta_dump, db, ddl, lote=500):
    """Segunda pasada: streamea los INSERT de las tablas objetivo y los
    reinserta con parametros (?) para no reescapar nada a mano."""
    conteos = {t: 0 for t in ddl}
    cur = db.cursor()

    with io.open(ruta_dump, "r", encoding="utf-8", errors="replace", newline="") as f:
        for statement in LectorStatements(f):
            if not statement.startswith("INSERT INTO `"):
                continue
            m = re_insert_cab.match(statement)
            if not m:
                continue
            tabla = m.group(1)
            if tabla not in ddl:
                continue

            columnas = _nombres_backtick(m.group(2))
            filas = parsear_valores(statement, m.end())
            if not filas:
                continue

            sql = 'INSERT INTO "{}" ({}) VALUES ({})'.format(
                tabla,
                ", ".join('"{}"'.format(c) for c in columnas),
                ", ".join("?" * len(columnas)),
            )
            buenas = [f_ for f_ in filas if len(f_) == len(columnas)]
            descartadas = len(filas) - len(buenas)
            if descartadas:
                print("  aviso: {} filas de {} con aridad distinta, descartadas".format(
                    descartadas, tabla), file=sys.stderr)

            for i in range(0, len(buenas), lote):
                cur.executemany(sql, buenas[i:i + lote])
            conteos[tabla] += len(buenas)

    db.commit()
    return conteos


# ---------------------------------------------------------------------------
# Indices de trabajo (nuestros, no del dump)
# ---------------------------------------------------------------------------

# El dump viene practicamente sin indices: de las cinco tablas de Prioridad 1
# solo newstocks_cidef trae uno, y ninguna indexa el VIN pese a que el VIN es
# la llave natural con la que el sistema cruza todo. Es decir que hoy, en
# produccion, buscar las reparaciones de una unidad recorre las 268.022 filas
# de reparaciones_externas enteras. Estos indices son de la replica local, no
# copias del origen -- se crean aparte y con nombre 'ix_' para que quede claro
# cual es cual si alguna vez hay que comparar esquemas contra el servidor.
INDICES_DE_TRABAJO = [
    ("newstocks_cidef", ["vin"]),
    ("newstocks_cidef", ["patente"]),
    ("newstocks_cidef", ["estadostock"]),
    # `despachado` es la columna de estado operativo con la que filtra el
    # listado de unidades (ver COLUMNA_ESTADO en modulos/unidades.py).
    ("newstocks_cidef", ["despachado"]),
    ("orden_trabajo", ["id_vehiculo"]),
    ("orden_trabajo", ["patente"]),
    ("orden_trabajo", ["vin"]),
    ("reparaciones_externas", ["ot_relacionada"]),
    ("reparaciones_externas", ["vin"]),
    ("contenedor", ["nro_contenedor"]),
    ("ot_contenedor", ["n_guia"]),
    ("ot_contenedor", ["n_contenedor"]),
    # Las cuatro tablas de la ficha de unidad: todas se consultan por VIN, y
    # `registros` tiene 299.322 filas -- sin este indice cada ficha abierta
    # recorreria la tabla entera.
    ("check_list", ["vin"]),
    ("check_list_mecanica", ["vin"]),
    ("inspeccion_despacho", ["vin"]),
    ("registros", ["vin"]),
    # Fuentes de los KPI de reclamos y retornos. Son chicas (incidentes esta
    # vacia y retornos tiene 2 filas), pero se consultan por VIN igual que las
    # demas y el indice no cuesta nada.
    ("incidentes", ["vin"]),
    ("retornos", ["vin"]),
    # `entradas_salidas` la consulta el KPI de despachos atrasados por
    # (fecha, rut) para cada despacho del mes; sin indice son 146.353 filas
    # recorridas por cada uno.
    ("entradas_salidas", ["fecha"]),
    ("ingresos_roro", ["fecha"]),
    ("promedio_pdi", ["fecha"]),
]


def crear_indices_de_trabajo(db):
    creados = 0
    for tabla, columnas in INDICES_DE_TRABAJO:
        existentes = {r[1] for r in db.execute('PRAGMA table_info("{}")'.format(tabla))}
        if not existentes:
            continue  # la tabla no esta en esta replica
        if not set(columnas) <= existentes:
            continue  # la columna no existe en esta version del esquema
        nombre = "ix_{}_{}".format(tabla, "_".join(columnas))
        db.execute('CREATE INDEX IF NOT EXISTS "{}" ON "{}" ({})'.format(
            nombre, tabla, ", ".join('"{}"'.format(c) for c in columnas)))
        creados += 1
    db.commit()
    return creados


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Importa el dump de Logautos a SQLite")
    ap.add_argument("--dump", default=DUMP_POR_DEFECTO)
    ap.add_argument("--db", default=DB_POR_DEFECTO)
    ap.add_argument("--tablas", default=None,
                    help="lista separada por comas; por defecto, Prioridad 1")
    ap.add_argument("--solo-indices", action="store_true",
                    help="no reimporta: solo (re)crea los indices de trabajo sobre la replica")
    args = ap.parse_args()

    if args.solo_indices:
        db = sqlite3.connect(args.db)
        n = crear_indices_de_trabajo(db)
        db.execute("ANALYZE")
        db.close()
        print("{} indices de trabajo listos en {}".format(n, args.db))
        return 0

    tablas = [t.strip() for t in args.tablas.split(",")] if args.tablas else TABLAS_PRIORIDAD_1
    tablas_objetivo = set(tablas)

    if not os.path.exists(args.dump):
        print("no existe el dump: {}".format(args.dump), file=sys.stderr)
        return 1

    t0 = time.time()
    print("pasada 1/2 -- leyendo estructura de {} tablas...".format(len(tablas_objetivo)))
    ddl = leer_ddl(args.dump, tablas_objetivo)

    faltantes = tablas_objetivo - set(ddl)
    if faltantes:
        print("no se encontraron en el dump: {}".format(", ".join(sorted(faltantes))), file=sys.stderr)
    if not ddl:
        return 1

    for tabla, info in sorted(ddl.items()):
        print("  {:<24} {:>3} columnas  pk={}  {} indices".format(
            tabla, len(info["columnas"]), info["pk"] or "-", len(info["indices"])))
    print("  ({:.0f}s)".format(time.time() - t0))

    db = sqlite3.connect(args.db)
    db.execute("PRAGMA journal_mode = WAL")
    # El dump ya viene consistente; sincronizar cada commit contra disco
    # multiplica por varias veces el tiempo de una carga de 265 MB y no
    # protege de nada que no se arregle volviendo a correr el importador.
    db.execute("PRAGMA synchronous = OFF")

    for tabla, info in sorted(ddl.items()):
        db.execute('DROP TABLE IF EXISTS "{}"'.format(tabla))
        db.execute(sql_create(tabla, info))
        for idx in info["indices"]:
            db.execute('CREATE {}INDEX "{}_{}" ON "{}" ({})'.format(
                "UNIQUE " if idx["unico"] else "",
                tabla, idx["nombre"],
                tabla,
                ", ".join('"{}"'.format(c) for c in idx["columnas"]),
            ))
    db.commit()

    t1 = time.time()
    print("\npasada 2/2 -- cargando datos...")
    conteos = cargar_datos(args.dump, db, ddl)
    print("  ({:.0f}s)".format(time.time() - t1))

    print("\nfilas cargadas en {}:".format(args.db))
    for tabla in sorted(conteos):
        print("  {:<24} {:>9,}".format(tabla, conteos[tabla]))

    n_idx = crear_indices_de_trabajo(db)
    print("\n{} indices de trabajo creados".format(n_idx))

    db.execute("ANALYZE")
    db.execute("PRAGMA optimize")
    db.close()
    print("\ntotal {:.0f}s".format(time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
