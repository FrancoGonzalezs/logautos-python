"""
modulos/fotos_publicas.py -- las fotos que REGLA le muestra al sistema viejo.

POR QUE EXISTE
==============

`check_list_mecanica.link_unidades` guarda una URL, no un archivo. El legado
escribe ahi

    https://logautos.cl/clientes/assets/images/falla/<VIN>_FALLA_MECANICA_NRO_3_...jpg

y la muestra en sus pantallas y en el PDF que sale por correo. Si REGLA sube
la foto a su propio volumen y empuja una URL que pide sesion, la pantalla del
legado muestra un cuadrado roto -- y durante el mes en paralelo eso lo ve el
cliente, no nosotros.

Asi que hay que servirla sin sesion. La pregunta no es "publica o privada"
sino "que la protege".

LO QUE PROTEGE, Y LO QUE NO
===========================

Un token de 32 bytes aleatorios en la URL. La URL ES la credencial: quien la
tiene, ve la foto; quien no, no tiene por donde empezar.

Comparado con lo que hay hoy, esto SUBE la barra. La carpeta del legado es
publica y el nombre del archivo es `<VIN>_FALLA_MECANICA_NRO_<n>_<fecha>.jpg`
-- o sea que conociendo el VIN de una unidad se adivina el nombre probando
unas pocas fechas. Un token aleatorio no se adivina.

Y hay que decir con la misma claridad lo que NO protege:

  * NO SE REVOCA. Una vez que la URL salio, salio: queda en `link_unidades`
    del legado, en los logs del proxy, en el historial del que la abrio.
    Borrar la fila de aca deja de servirla, pero cualquier copia que se haya
    bajado ya esta afuera.
  * NO SABE QUIEN MIRA. No hay usuario, asi que no hay registro de accesos
    que sirva para nada.

Para fotos de danos de un vehiculo eso es aceptable, y es exactamente la
postura que el legado ya tiene. Para un documento con datos de una persona no
lo seria -- y de ahi sale la regla de abajo.

LA REGLA: SE SIRVE LO QUE SE PUBLICO, NO LO QUE ESTA EN EL DISCO
================================================================

La ruta resuelve POR TOKEN contra la tabla `fotos_publicadas`, y de ahi saca
la ruta del archivo. NUNCA al reves.

Es tentador ahorrarse la tabla y hacer la ruta `/f/<hmac(ruta_del_archivo)>`:
sale sin estado, sin migracion y sin fila que mantener. Y convierte a TODA
foto de REGLA en alcanzable para quien conozca el esquema -- incluidas las de
`check_list_regla`, que llevan `link_guia`, la guia de ingreso con nombres y
RUT. Nadie decidiria publicar eso; saldria publicado igual, de arrastre.

Con la tabla, lo que no se publico explicitamente no existe para esta ruta
aunque el archivo este en el mismo volumen. Publicar es un acto, no una
propiedad de estar guardado.
"""

import os
import secrets

from flask import Blueprint, abort, send_from_directory, url_for

from core import DATA_DIR, get_db

bp = Blueprint("fotos_publicas", __name__, url_prefix="/f")

# 32 bytes = 43 caracteres url-safe. Es holgado: con 2^256 posibilidades, un
# atacante que probara mil millones de URLs por segundo desde que existe el
# universo no habria tocado ninguna. El largo no es el eslabon debil aca --
# el eslabon debil es que la URL se reenvia por WhatsApp.
BYTES_DE_TOKEN = 32

# Las fotos publicadas viven bajo DATA_DIR igual que las del check list de
# ingreso; lo que cambia es quien las puede pedir, no donde estan.
RAIZ = DATA_DIR


def _asegurar_tabla(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS fotos_publicadas (
            token       TEXT PRIMARY KEY,
            ruta        TEXT NOT NULL,   -- relativa a DATA_DIR
            origen      TEXT NOT NULL,   -- que modulo la publico
            referencia  TEXT,            -- id de la fila que la usa
            publicada_en TEXT NOT NULL
        )""")
    # Por ruta, para no publicar dos veces el mismo archivo con dos tokens
    # distintos: dos tokens vivos para una foto son dos cosas que revocar.
    db.execute("CREATE INDEX IF NOT EXISTS ix_fotos_publicadas_ruta "
               "ON fotos_publicadas(ruta)")


def publicar(ruta_relativa, origen, referencia=None):
    """Registra una foto como publica y devuelve su URL absoluta-en-el-sitio.

    `ruta_relativa` es relativa a DATA_DIR. Devuelve una ruta que empieza con
    '/', no una URL completa: el host se le pega afuera, donde se sabe cual es
    el publico (ver `url_publica`)."""
    from datetime import datetime

    db = get_db()
    _asegurar_tabla(db)

    fila = db.execute(
        "SELECT token FROM fotos_publicadas WHERE ruta = ?",
        (ruta_relativa,)).fetchone()
    if fila is not None:
        return url_for("fotos_publicas.ver", token=fila["token"])

    token = secrets.token_urlsafe(BYTES_DE_TOKEN)
    db.execute(
        "INSERT INTO fotos_publicadas (token, ruta, origen, referencia, "
        "                              publicada_en) VALUES (?, ?, ?, ?, ?)",
        (token, ruta_relativa, origen, str(referencia or ""),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    db.commit()
    return url_for("fotos_publicas.ver", token=token)


class FaltaBasePublica(RuntimeError):
    """No hay con que armar una URL absoluta para el legado."""


def url_publica(ruta_en_el_sitio):
    """La URL que se le manda al legado, con host. REVIENTA si no hay base.

    El legado guarda esto tal cual y lo pinta en un `<img>`, asi que tiene que
    ser absoluta. Una ruta pelada como `/f/<token>` no falla al mandarla: el
    navegador del legado la resuelve contra SU propio host --
    `https://logautos.cl/f/<token>` -- y da 404 en la pantalla de otro sistema,
    que es donde nadie lo va a atribuir a REGLA.

    Antes esto devolvia la ruta pelada y el docstring decia que "el push exige
    la variable". No la exigia nadie: el comentario prometia una guarda que no
    existia. Ahora existe -- y esta aca y no en el push porque este es el unico
    lugar por el que se arma una URL para afuera."""
    base = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if not base:
        raise FaltaBasePublica(
            "PUBLIC_BASE_URL no esta puesta y hay que mandarle al legado la "
            "URL de una foto.\n"
            "  Sin ella saldria `{}`, que el legado resuelve contra su propio "
            "host y da 404.\n"
            "  En Railway va la URL publica del servicio.".format(
                ruta_en_el_sitio))
    return base + ruta_en_el_sitio


@bp.route("/<token>")
def ver(token):
    """Sirve una foto publicada. SIN sesion, a proposito.

    Resuelve por token contra la tabla. Una ruta de archivo que no este
    publicada no se sirve por aca aunque el archivo exista."""
    db = get_db()
    _asegurar_tabla(db)
    fila = db.execute(
        "SELECT ruta FROM fotos_publicadas WHERE token = ?", (token,)).fetchone()
    if fila is None:
        # 404 y no 403: un 403 confirmaria que ese token existe pero no se
        # puede ver, y aca no hay tal cosa -- o esta publicada o no existe.
        abort(404)
    carpeta, nombre = os.path.split(fila["ruta"])
    return send_from_directory(os.path.join(RAIZ, carpeta), nombre)
