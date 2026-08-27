"""
core.py -- piezas compartidas del sistema (conexion a SQLite, helpers de
formato y de consulta) que tanto app.py como los modulos de modulos/
necesitan.

Este archivo NO importa nada de app.py ni de modulos/, a proposito: es la
base de la que cuelga todo lo demas, asi que si dependiera hacia arriba se
armaria un import circular apenas hubiera mas de un modulo. Es la misma
regla que se sigue en el proyecto de Talca, donde separar un modulo sin esta
capa aparte termino en un loop de imports.
"""

import os
import sqlite3

from flask import g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Las dos rutas que la app necesita escribir o leer del disco salen del
# entorno, con el layout local como default para que correr en la maquina no
# necesite configurar nada. En un contenedor las dos tienen que apuntar al
# volumen persistente: fuera de el, el disco se borra en cada redeploy.
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "local.db"))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))

DB_BUSY_TIMEOUT_MS = 5000

# Como se decide que estamos en produccion: por si hay SECRET_KEY. No hay
# deteccion de entorno por otro lado -- ni variable propia ni marca de la
# plataforma -- porque no hace falta ninguna: SECRET_KEY ya era obligatoria en
# el contenedor, y atarse a algo como RAILWAY_ENVIRONMENT dejaria de valer el
# dia que esto se mueva de hosting, justo del lado inseguro.
NOMBRE_CLAVE_LOCAL = ".secret_key"


def clave_de_sesion():
    """La clave con que se firma la cookie de sesion.

    En produccion sale de SECRET_KEY y no hay alternativa. Antes habia una
    clave fija escrita en el repo, que era tolerable mientras el login aceptaba
    cualquier usuario y la app era de solo lectura; con login real esa clave
    permite fabricarse una sesion de cualquier usuario, con cualquier rol, sin
    saber una sola contrasena.

    En local se genera una al azar la primera vez y se guarda en DATA_DIR,
    que esta fuera del repo. Se guarda en vez de regenerarla en cada arranque
    porque con la recarga automatica cada archivo guardado cerraria la sesion,
    y ahora volver a entrar cuesta escribir una contrasena de verdad."""
    del_entorno = os.environ.get("SECRET_KEY")
    if del_entorno:
        return del_entorno

    ruta = os.path.join(DATA_DIR, NOMBRE_CLAVE_LOCAL)
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            guardada = f.read().strip()
        if guardada:
            return guardada

    import secrets
    nueva = secrets.token_hex(32)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(nueva)
    return nueva


def conectar_db(path=None):
    """WAL + busy_timeout: WAL deja que las lecturas sigan andando mientras
    hay una escritura en curso (sin el, cualquier pantalla de listado se
    bloquea cuando alguien guarda), y busy_timeout hace que una escritura
    que se encuentra la base tomada espere en vez de reventar al toque con
    'database is locked'."""
    db = sqlite3.connect(path or DB_PATH, timeout=DB_BUSY_TIMEOUT_MS / 1000.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA busy_timeout = {}".format(DB_BUSY_TIMEOUT_MS))
    return db


# ---------------------------------------------------------------------------
# La guarda de `unidad_id`
# ---------------------------------------------------------------------------
#
# Toda tabla propia de REGLA cuelga de UNA PASADA de la unidad, no del
# vehiculo: `newstocks_cidef` tiene 71.546 filas para 61.447 VIN porque cada
# fila es una entrada distinta al patio. Una fila sin `unidad_id` no se puede
# atribuir a ninguna, y ninguna consulta la va a encontrar -- desde que todo
# empareja por id, un movimiento sin dueño es invisible para la ficha, para el
# listado y para el motor. Se pierde en silencio.
#
# POR QUE UN TRIGGER Y NO UN `if` EN CADA INSERT. Un chequeo en Python protege
# a quien se acuerda de llamarlo, y este bug se repitio cuatro veces
# justamente porque la disciplina no alcanza. El trigger lo aplica la base a
# TODA escritura, venga de donde venga.
#
# Y sobre todo: `CREATE TRIGGER IF NOT EXISTS` funciona sobre una tabla que YA
# EXISTE, sin recrearla. Es lo que lo hace servible en las bases desplegadas,
# que es donde no llega ninguna migracion -- el proyecto crea sus tablas con
# `CREATE TABLE IF NOT EXISTS`, asi que una base ya creada conserva su esquema
# viejo para siempre. Un `NOT NULL` exigiria recrear y copiar; esto no.
#
# El mensaje va en el propio RAISE, asi que llega a Python como
# `sqlite3.IntegrityError: <mensaje>` y se lee sin ir al codigo.

TABLAS_CON_UNIDAD = {
    "movimientos_regla": "unidad_id",
    "pdi_regla": "unidad_id",
    "it_regla": "unidad_id",
    "check_list_regla": "unidad_id",
    "revision_unidad_regla": "unidad_id",
    "validacion_color_regla": "id_unidad",
}


def exigir_unidad_id(db, tabla):
    """Instala el trigger que rechaza escrituras sin unidad para `tabla`.

    Idempotente y barato: se puede llamar en cada request. Si la tabla todavia
    no existe -- o no tiene la columna, que pasa en bases viejas -- no hace
    nada y no rompe: la guarda se instala sola cuando la tabla aparezca."""
    columna = TABLAS_CON_UNIDAD.get(tabla)
    if columna is None:
        raise ValueError("tabla sin unidad declarada: {}".format(tabla))
    try:
        cols = [c[1] for c in db.execute('PRAGMA table_info("{}")'.format(tabla))]
    except Exception:                            # pragma: no cover
        return False
    if columna not in cols:
        return False
    mensaje = ("{tabla}.{columna} no puede ser NULL: cada fila cuelga de UNA "
               "pasada de la unidad, y sin ese id la fila es invisible para "
               "toda la aplicacion".format(tabla=tabla, columna=columna))
    for momento in ("INSERT", "UPDATE"):
        db.execute(
            'CREATE TRIGGER IF NOT EXISTS "exige_{col}_{mom}_{tabla}" '
            'BEFORE {mom} ON "{tabla}" FOR EACH ROW '
            'WHEN NEW."{col}" IS NULL '
            "BEGIN SELECT RAISE(ABORT, '{msg}'); END".format(
                tabla=tabla, col=columna, mom=momento,
                mom_low=momento.lower(), msg=mensaje.replace("'", "''")))
    return True


def instalar_guardas(db_path=None):
    """Instala LAS DOCE guardas de una, al arrancar.

    Antes se instalaban desde cada `_asegurar_tabla*`, o sea de forma
    perezosa: cada tabla recibia su trigger recien cuando alguien visitaba la
    pantalla que la usa. Medido en Railway, eso dejaba 6 de 12 puestas -- media
    proteccion ausente en produccion, no un detalle de auditoria.

    Se llama desde `crear_app()`. Como los triggers son ESQUEMA y no estado de
    conexion, quedan en el archivo: desde ese momento protegen tambien a lo que
    no sirve un request -- el hilo del sync, el del push y los comandos de
    consola --, aunque abran su propia conexion y no pasen por ningun modulo de
    pantalla.

    No levanta: una guarda que no se pudo instalar no puede impedir que la app
    arranque. Se anota en el log y se sigue."""
    puestas, faltantes = [], []
    db = None
    try:
        db = conectar_db(db_path)
        for tabla in TABLAS_CON_UNIDAD:
            try:
                (puestas if exigir_unidad_id(db, tabla) else faltantes).append(tabla)
            except Exception as e:               # noqa: BLE001 -- ver docstring
                faltantes.append("{} ({})".format(tabla, e))
        db.commit()
    except Exception as e:                       # noqa: BLE001
        print("[guardas] no se pudieron instalar: {}: {}".format(
            type(e).__name__, e), flush=True)
        return [], list(TABLAS_CON_UNIDAD)
    finally:
        if db is not None:
            db.close()
    print("[guardas] unidad_id exigida en {} de {} tablas{}".format(
        len(puestas), len(TABLAS_CON_UNIDAD),
        "" if not faltantes else " -- sin guarda: {}".format(", ".join(faltantes))),
        flush=True)
    return puestas, faltantes


def get_db():
    if "db" not in g:
        g.db = conectar_db()
    return g.db


def cerrar_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def consultar(sql, params=(), una=False):
    cur = get_db().execute(sql, params)
    filas = cur.fetchall()
    cur.close()
    if una:
        return filas[0] if filas else None
    return filas


def escalar(sql, params=()):
    fila = consultar(sql, params, una=True)
    return fila[0] if fila else None


def columnas_de(tabla):
    """Los nombres de columna de una tabla, en el orden del esquema.

    Se usa para armar las pantallas sin hardcodear las 144 columnas de
    newstocks_cidef: si el dump se vuelve a importar con una version
    distinta del esquema, las pantallas se adaptan solas en vez de romperse
    con 'no such column'."""
    return [r[1] for r in get_db().execute('PRAGMA table_info("{}")'.format(tabla))]


def vacio(valor):
    """En el sistema original 'sin dato' se escribe de cuatro formas
    distintas segun la columna y la epoca: NULL, cadena vacia, '0000-00-00'
    y el string '0'. Cualquier pantalla que no las trate igual muestra
    basura, asi que la decision de que es 'vacio' vive aca y no repartida
    por las plantillas."""
    if valor is None:
        return True
    texto = str(valor).strip()
    return texto in ("", "0000-00-00", "0000-00-00 00:00:00")


def mostrar(valor, por_defecto="—"):
    return por_defecto if vacio(valor) else valor


def numero(valor):
    """Entero con punto de miles, que es como se escriben los numeros en
    Chile. Se arma con el separador de coma y se reemplaza, porque el locale
    del sistema no es confiable: en el servidor puede no estar instalado el
    es_CL y quedaria en formato ingles sin que nadie lo note."""
    try:
        return "{:,}".format(int(round(float(valor)))).replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def pesos(valor):
    entero = numero(valor)
    return entero if entero == "—" else "$" + entero
