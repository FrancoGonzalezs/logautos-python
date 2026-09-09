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
_SALTO = chr(10)
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

    # LA GUARDA VA ANTES DE ESCRIBIR NADA.
    #
    # `url_publica` tambien corta, pero para entonces el token ya estaria
    # registrado: quedaria una fila en `fotos_publicadas` de una foto que nadie
    # publico, y la proxima vez `publicar` la encontraria y la daria por buena.
    # Mismo argumento que la guarda de retencion en `registrar()`: la ultima
    # linea de defensa no reemplaza a la primera.
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if es_base_provisoria(base):
        raise BasePublicaProvisoria(
            "no se puede publicar una foto con PUBLIC_BASE_URL={} .{}"
            "  Esa direccion es TEMPORAL de Railway, y la URL que se armaria "
            "queda escrita en `archivo1..archivo9` de Regla PHP para siempre: "
            "Regla PHP guarda la URL, no el archivo.{}"
            "  Poner el dominio definitivo (por ejemplo "
            "https://regla.logautos.cl) y volver a intentar. La foto no se "
            "perdio: sigue guardada, falta publicarla.".format(
                base, _SALTO, _SALTO))

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
    """No hay con que armar una URL absoluta para Regla PHP."""


class BasePublicaProvisoria(RuntimeError):
    """La base publica es una direccion temporal de Railway.

    NO impide arrancar -- impide PUBLICAR. Son dos preguntas distintas y se
    contestan distinto:

      arrancar : el proyecto nuevo tiene que poder levantar antes de que exista
                 el CNAME. Si no, no se puede ni probar.
      publicar : la URL que se arma en ese momento viaja a `archivo1..archivo9`
                 de Regla PHP y queda ahi PARA SIEMPRE. Regla PHP guarda la
                 URL, no el archivo, asi que el dia que la direccion temporal
                 deje de resolver esa foto desaparece de la pantalla de otro
                 sistema, y no hay variable que la arregle.

    O sea: levantar es reversible, publicar no. La guarda va donde el dano es
    permanente."""


# Los dominios que Railway asigna solos. Son estables mientras el proyecto
# viva, pero se van con el proyecto: el dia que la aplicacion se mude otra vez
# --y esta es la segunda mudanza-- las URL escritas con este host quedan
# muertas en una tabla que ya nadie revisa.
HOSTS_PROVISORIOS = ("railway.app", "up.railway.app")


def es_base_provisoria(base):
    """True si `base` apunta a una direccion temporal de Railway."""
    if not base:
        return False
    host = base.split("//", 1)[-1].split("/", 1)[0].lower()
    return any(host == h or host.endswith("." + h) for h in HOSTS_PROVISORIOS)


def base_publica_configurada():
    """La base publica, validada. REVIENTA si falta o si esta mal formada.

    LA LLAMA `crear_app()` AL ARRANCAR, y ese es el punto entero: hasta el
    2026-09-09 la unica comprobacion vivia en `url_publica`, o sea que la
    aplicacion levantaba tranquila y reventaba recien cuando alguien publicaba
    la primera foto -- en medio de una inspeccion, con el usuario mirando.

    UNA URL MAL PUESTA NO SE PUEDE DESHACER. Lo que se manda va a
    `archivo1..archivo9` de Regla PHP, columnas permanentes que su pantalla
    pinta en un `<img>`. No hay copia del otro lado: Regla PHP guarda la URL,
    no el archivo. Un dominio equivocado ahi no es un error de configuracion
    que se arregla cambiando la variable -- es una foto que dejo de verse en
    el sistema de otro, para siempre, y en filas que ya nadie va a revisar.

    POR ESO NUNCA SALE DEL REQUEST. `request.host` seria lo comodo y seria una
    trampa: durante la mudanza conviven la direccion de Railway y el dominio
    propio, asi que la URL escrita dependeria de por donde entro el que subio
    la foto. Dos fotos de la misma inspeccion podrian quedar con hosts
    distintos, y el sintoma aparece meses despues, en la pantalla de otro."""
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base:
        raise FaltaBasePublica(
            "PUBLIC_BASE_URL no esta puesta.\n"
            "  Con ella se arman las URL de las fotos que se le mandan a Regla "
            "PHP, y quedan escritas en `archivo1..archivo9` PARA SIEMPRE.\n"
            "  Tiene que ser el dominio DEFINITIVO y con esquema, por ejemplo "
            "https://regla.logautos.cl\n"
            "  En Railway se pone como variable del servicio.")
    if not base.startswith(("http://", "https://")):
        raise FaltaBasePublica(
            "PUBLIC_BASE_URL tiene que empezar con http:// o https:// y vino "
            "{!r}.\n"
            "  Sin esquema, Regla PHP la resuelve contra SU propio host y la "
            "foto da 404 en la pantalla de otro sistema.".format(base))
    resto = base.split("//", 1)[1]
    if not resto or "/" in resto:
        raise FaltaBasePublica(
            "PUBLIC_BASE_URL tiene que ser solo esquema y host, sin ruta ni "
            "barra final, y vino {!r}.".format(base))
    return base


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
    # La MISMA validacion que corre al arrancar, no una copia: dos reglas
    # para la misma variable es la forma de que una se quede atras.
    base = base_publica_configurada()

    # Y ACA, ADEMAS, LA BASE NO PUEDE SER PROVISORIA.
    #
    # Al arrancar una direccion de Railway solo avisa; aca corta. Este es el
    # punto exacto en el que se arma la cadena que va a quedar escrita del otro
    # lado, asi que es el ultimo lugar donde todavia se puede no escribirla.
    if es_base_provisoria(base):
        raise BasePublicaProvisoria(
            "no se puede publicar una foto con PUBLIC_BASE_URL={} .{}"
            "  Esa direccion es TEMPORAL de Railway, y la URL que se armaria "
            "queda escrita en `archivo1..archivo9` de Regla PHP para siempre: "
            "Regla PHP guarda la URL, no el archivo.{}"
            "  Poner el dominio definitivo (por ejemplo "
            "https://regla.logautos.cl) y volver a intentar. La foto no se "
            "perdio: sigue guardada, falta publicarla.".format(
                base, _SALTO, _SALTO))

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
