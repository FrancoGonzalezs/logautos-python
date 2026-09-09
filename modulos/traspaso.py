"""
modulos/traspaso.py -- servir la réplica UNA VEZ, para armar un proyecto nuevo.

Existe para la mudanza al proyecto nuevo de Railway del 2026-09-09: el viejo ya
tiene la réplica cargada, así que en vez de exportar 22 tablas de MySQL y volver
a importarlas, el nuevo se la baja del viejo.

LA VENTAJA NO ES COMODIDAD, Y ES LO QUE DECIDE LA VIA
=====================================================

La base y su marca de agua viajan JUNTAS, consistentes por construcción, porque
salen del mismo archivo en el mismo instante. Todo lo que la vía del volcado
obliga a resolver a mano --los dos relojes de phpMyAdmin, el margen, reponer
`sync_estado`, verificar que no quedó adelantada-- deja de existir.

ESTE MODULO ES TEMPORAL. SE BORRA.
==================================

No es una funcionalidad del sistema: es una herramienta de una mudanza. Cuando
el proyecto nuevo tenga su base, se borra este archivo, se saca su
`register_blueprint` de `app.py` y se quita la variable de entorno. Las tres
cosas, no dos.

LAS CUATRO CONDICIONES, Y NINGUNA ES DECORATIVA
===============================================

1. NO EXISTE SI NO ESTA LA VARIABLE. Sin `TRASPASO_TOKEN` el blueprint no se
   registra, y la respuesta pasa a ser EXACTAMENTE la misma que la de una
   direccion inventada -- hoy, la redireccion al login que da cualquier ruta
   desconocida. Comprobado en `probar_traspaso.py` comparando las dos.

   Con la variable puesta, un pedido sin token da 404 mientras que una
   direccion inventada da 302, asi que ahi SI se puede deducir que la ruta
   existe. Es aceptable --sigue haciendo falta el token, y la ventana son
   minutos-- pero es una razon mas para sacar la variable apenas termine la
   mudanza, y esta escrito para que nadie lo descubra de nuevo.

2. EL TOKEN VA EN UNA CABECERA, NUNCA EN LA URL. gunicorn no escribe access log
   por defecto y esta aplicación tampoco loguea rutas --los dos comprobados--,
   pero el proxy de Railway está fuera de nuestro control. Un token en la URL
   termina en el log de alguien; en una cabecera, no.

3. SE COMPARA EN TIEMPO CONSTANTE. `secrets.compare_digest`, no `==`.

4. TODO INTENTO SE IMPRIME, el que entra y el que no. Es una ruta que sirve
   `tbl_users` con correos, RUT, teléfonos y hashes de 144 personas reales:
   que se use tiene que verse.

POR QUE LA COPIA NO VA AL VOLUMEN
=================================

El volumen del proyecto viejo tiene del orden de **69 MB libres de 434**, y la
copia consistente son **387 MB**. No entra, y llenar el volumen tumbaría el
sistema que estamos copiando.

Va al disco del contenedor (`/tmp`), que es efímero y es exactamente lo que
corresponde para algo que se borra al terminar. Igual se comprueba el espacio
antes y se corta con los números si no alcanza, en vez de llenar el disco y
fallar a la mitad.

POR QUE `VACUUM INTO` Y NO COPIAR EL ARCHIVO
============================================

Medido el 2026-09-09, y es la parte que más fácil se repite mal:

  - Copiar SOLO `local.db` con 500 filas commiteadas en el WAL devuelve **una
    fila**. Y la copia ABRE, sin error: es una base SQLite válida con 500 filas
    menos.
  - Copiar los tres archivos en caliente dio **12 de 12 corrompidas**
    (`database disk image is malformed`), porque son tres instantes distintos.
    `VACUUM INTO` con el mismo escritor corriendo: **0 de 12**.

`VACUUM INTO` toma una foto transaccional, así que **no hay que apagar el hilo
de sync**, y deja un archivo único sin `-wal` al lado -- que es justo lo que se
quiere mover.
"""

import os
import secrets
import shutil
import sqlite3
import tempfile
import time
import zlib

from flask import Blueprint, Response, abort, request

from core import DB_PATH

bp = Blueprint("traspaso", __name__, url_prefix="/traspaso")

# La cabecera por la que viaja el token. NO hay variante por query string, y no
# es un olvido: si existiera, alguien la usaría por comodidad y el token
# quedaría en el log del proxy.
CABECERA = "X-Traspaso-Token"

# Un token corto no protege nada. Se exige largo al arrancar --ver
# `esta_activo`-- en vez de confiar en que quien lo genere elija bien.
LARGO_MINIMO = 32

# Cuánto espacio de sobra se exige antes de empezar, sobre el tamaño de la base.
MARGEN_DISCO = 1.15


def token_configurado():
    return (os.environ.get("TRASPASO_TOKEN") or "").strip()


def esta_activo():
    """True si hay que registrar el blueprint.

    REVIENTA si el token existe pero es corto. Un token de ocho caracteres no
    es una puerta cerrada con llave floja: es una puerta abierta con un cartel.
    Mejor no arrancar que arrancar creyendo que está protegido."""
    t = token_configurado()
    if not t:
        return False
    if len(t) < LARGO_MINIMO:
        raise RuntimeError(
            "TRASPASO_TOKEN tiene {} caracteres y el minimo son {}. "
            "Generalo con:  python -c \"import secrets;"
            "print(secrets.token_urlsafe(48))\"".format(len(t), LARGO_MINIMO))
    return True


def _avisar(texto):
    print("[traspaso] {}".format(texto), flush=True)


def _quien():
    """De donde vino, para el log. `X-Forwarded-For` porque detras del proxy de
    Railway `remote_addr` es siempre la IP interna."""
    return (request.headers.get("X-Forwarded-For")
            or request.remote_addr or "?")


@bp.before_request
def _exigir_token():
    esperado = token_configurado()
    recibido = (request.headers.get(CABECERA) or "").strip()
    if not recibido or not secrets.compare_digest(recibido, esperado):
        _avisar("RECHAZADO desde {} (token {})".format(
            _quien(), "ausente" if not recibido else "incorrecto"))
        # 404 y no 401: un 401 confirma que la ruta existe.
        abort(404)


@bp.route("/replica.db.gz")
def replica():
    """Una foto consistente de la replica, comprimida, en streaming."""
    _avisar("AUTORIZADO desde {} -- sirviendo la replica".format(_quien()))

    carpeta = tempfile.mkdtemp(prefix="traspaso_")
    snapshot = os.path.join(carpeta, "replica.db")

    try:
        tam = os.path.getsize(DB_PATH)
    except OSError as e:
        _avisar("no se pudo leer la base: {}".format(e))
        shutil.rmtree(carpeta, ignore_errors=True)
        abort(500)

    # El espacio SE COMPRUEBA ANTES. Quedarse sin disco a la mitad deja un
    # archivo enorme en el contenedor y una respuesta truncada que del otro
    # lado se ve como un .gz corrupto -- o peor, como una base incompleta.
    libre = shutil.disk_usage(carpeta).free
    if libre < tam * MARGEN_DISCO:
        _avisar("NO ALCANZA EL DISCO: hacen falta {:.0f} MB y hay {:.0f} MB "
                "libres en {}".format(tam * MARGEN_DISCO / 1e6, libre / 1e6,
                                      carpeta))
        shutil.rmtree(carpeta, ignore_errors=True)
        abort(507)

    t0 = time.time()
    try:
        # No se abre con `core.conectar_db` a proposito: eso pasa por las
        # guardas de prueba y ademas devuelve la conexion de Flask, que esta
        # atada al request. Aca hace falta una conexion suelta y de solo
        # lectura sobre el archivo.
        con = sqlite3.connect(DB_PATH, timeout=60)
        con.execute("VACUUM INTO ?", (snapshot,))
        con.close()
    except Exception as e:                          # noqa: BLE001
        _avisar("VACUUM INTO fallo: {}: {}".format(type(e).__name__, e))
        shutil.rmtree(carpeta, ignore_errors=True)
        abort(500)

    _avisar("copia consistente lista en {:.1f}s ({:.0f} MB)".format(
        time.time() - t0, os.path.getsize(snapshot) / 1e6))

    def emitir():
        """Comprime y manda de a pedazos.

        En streaming y no a un segundo archivo: comprimir a disco primero
        pediria 387 + 50 MB, y el contenedor no tiene por que tenerlos."""
        # `zlib.compressobj` con `16 + MAX_WBITS` produce formato gzip. Se usa
        # esto y no `gzip.GzipFile` porque hace falta ir entregando los pedazos
        # a medida que salen, y GzipFile quiere un archivo donde escribir.
        z = zlib.compressobj(6, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
        enviados = 0
        try:
            with open(snapshot, "rb") as f:
                while True:
                    trozo = f.read(1 << 20)
                    if not trozo:
                        break
                    dato = z.compress(trozo)
                    if dato:
                        enviados += len(dato)
                        yield dato
            cola = z.flush()
            if cola:
                enviados += len(cola)
                yield cola
            _avisar("enviados {:.1f} MB comprimidos".format(enviados / 1e6))
        finally:
            # Pase lo que pase --se corte la conexion, falle la compresion--
            # el temporal se va. Es una copia de `tbl_users`.
            shutil.rmtree(carpeta, ignore_errors=True)
            _avisar("temporal borrado")

    return Response(
        emitir(), mimetype="application/gzip",
        headers={"Content-Disposition":
                 'attachment; filename="replica.db.gz"'})
