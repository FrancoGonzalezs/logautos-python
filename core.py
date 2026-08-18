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
