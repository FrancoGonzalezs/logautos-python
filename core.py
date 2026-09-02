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

import decimal
import os
import sqlite3
import sys

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
    'database is locked'.

    Y la guarda de la REPLICA: una prueba no abre la replica real. Ver
    `exigir_replica_de_prueba`."""
    exigir_replica_de_prueba(path or DB_PATH)
    db = sqlite3.connect(path or DB_PATH, timeout=DB_BUSY_TIMEOUT_MS / 1000.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA busy_timeout = {}".format(DB_BUSY_TIMEOUT_MS))
    return db


# ---------------------------------------------------------------------------
# REGLA DEL PROYECTO: la clave de union cruza una frontera
# ---------------------------------------------------------------------------
#
# "El match es por `id`, jamas por VIN" vale para las tablas PROPIAS de REGLA.
# NO es una regla universal, y tratarla como universal ya produjo su propio bug.
#
# El motivo de la regla: `newstocks_cidef` tiene 71.546 filas para 61.447 VIN
# porque cada fila es UNA PASADA del vehiculo por el patio -- el 14% son
# reingresos. Nuestras tablas (`movimientos_regla`, `pdi_regla`, `it_regla`,
# `check_list_regla`, `revision_unidad_regla`, `validacion_color_regla`)
# cuelgan de `unidad_id`, asi que unirlas por VIN mezcla pasadas distintas.
#
# PERO LAS TABLAS DEL LEGADO USAN LA CLAVE QUE EL LEGADO USA. `orden_trabajo`
# tiene `id_vehiculo`, y parece un id -- pero el legado lo llena con
# `getidbyvin($vin)` (Pedido_model.php:841), que devuelve la pasada NO
# DESPACHADA del VIN y puede no ser la que se estaba procesando. Buscar la OT
# por `id_vehiculo = n.id` dio 88 "PDI sin cobrar" en 2026; comprobadas una por
# una, 87 tenian su OT colgada de otra pasada. Una sola era real.
#
# ENTONCES: cada vez que una consulta cruza la frontera entre nuestras tablas y
# las del legado, hay que PREGUNTAR QUE CLAVE USA ESE LADO. No heredar la
# nuestra ni suponer que un campo que se llama `id_*` se comporta como un id.
#
# Esta confusion aparecio SEIS veces con caras distintas: `estado_efectivo`,
# `estados_regla_de`, `pdi_de`, `it_de`, `_pasos_registrados` -- las cinco por
# usar VIN donde iba id -- y el sensor de PDI sin OT, por usar id donde iba VIN.
# Las dos direcciones del mismo error.
#
# Ayuda de lectura, ya aplicada al codigo: toda funcion que reciba un VIN lo
# dice en su nombre (`check_lists_por_vin`, `movimientos_por_vin`). Si el
# nombre no lo dice, no deberia recibir un VIN.

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
    "check_list_mecanica_regla": "unidad_id",
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


# ---------------------------------------------------------------------------
# Dinero
# ---------------------------------------------------------------------------
#
# REGLA DEL PROYECTO: todo calculo de plata redondea con Decimal y
# ROUND_HALF_UP. Nunca `round()` sobre un float.
#
# No es preferencia de estilo, son dos bichos distintos:
#
#   1. `round()` de Python redondea AL PAR (banquero): round(0.5)=0,
#      round(1.5)=2, round(2.5)=2. PHP redondea medio LEJOS DEL CERO, tanto en
#      `round()` como en `number_format()`. Un calculo replicado del legado con
#      `round()` da un peso de menos cada vez que cae exacto en .5.
#
#   2. El float binario no representa los decimales de base 10. 35164.5 puede
#      estar guardado como 35164.499999... y entonces hasta el redondeo
#      correcto da el numero de abajo.
#
# Paso de verdad y costo encontrarlo: replicando el precio de las OT de
# combustible, 119 de 1.061 no calzaban por exactamente 1 peso -- `29550 * 1.19
# = 35164.5`, que PHP guarda como 35165 y Python como 35164. Con ROUND_HALF_UP
# sobre Decimal, las 971 calzan al peso.
#
# Donde hay plata hoy: `modulos/facturacion.py` -- tarifas de acopio, precios
# de OT y los totales con IVA. `kpis.py` usa round() pero sobre tasas y
# minutos, no sobre pesos.

IVA = decimal.Decimal("1.19")


def peso(valor):
    """Un monto redondeado al peso, como lo redondea el legado.

    Acepta float, int, str o Decimal. Devuelve int, porque los pesos chilenos
    no tienen centavos y guardar el decimal invita a que alguien lo sume."""
    if valor is None:
        return 0
    return int(decimal.Decimal(str(valor)).quantize(
        decimal.Decimal("1"), rounding=decimal.ROUND_HALF_UP))


def con_iva(neto):
    """El monto con IVA, redondeado al peso. `neto` puede venir en float."""
    return peso(decimal.Decimal(str(neto or 0)) * IVA)


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


# ---------------------------------------------------------------------------
# La guarda de destino: una prueba no le habla a produccion
# ---------------------------------------------------------------------------
#
# NACIO DE UN ERROR REAL (2026-08-27). Escribiendo `probar_circulo.py` se seteo
# `LEGADO_BASE_URL` por entorno DESPUES de importar el modulo, y las dos
# constantes de base se leen AL IMPORTAR. El pull se fue a
# claude.logautos.cl. Fue un GET, volvio 401 y no escribio nada -- pero el
# mismo descuido en el push habria sido una escritura contra produccion desde
# una prueba.
#
# EL ARREGLO OBVIO ERA DISCIPLINA -- "pasa siempre base_url explicito" -- y la
# disciplina de este proyecto ya fallo seis veces con el match por VIN. Asi que
# la guarda no la aplica quien escribe la prueba: la aplican los CLIENTES, y se
# enciende sola.
#
# COMO SABE QUE ESTA EN UNA PRUEBA: por el nombre del script. La convencion ya
# existe y se respeta hace meses -- `scripts/probar_*.py` no toca produccion,
# `scripts/verificar_push_produccion.py` si y lo dice en el nombre. Atarse a
# esa convencion es lo que hace que una prueba NUEVA quede protegida sin que
# nadie se acuerde de nada, que es justo lo que fallaba.
#
# Se puede forzar en los dos sentidos:
#   REGLA_SOLO_LOCAL=1   exige local aunque el script no se llame probar_*
#   REGLA_SOLO_LOCAL=0   lo apaga, para el caso raro de una prueba que de
#                        verdad quiera salir (hoy no hay ninguna)

DESTINOS_LOCALES = ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def _es_prueba():
    forzado = os.environ.get("REGLA_SOLO_LOCAL", "").strip()
    if forzado:
        return forzado not in ("0", "no", "false")
    guion = os.path.basename(sys.argv[0] or "")
    return guion.startswith("probar_")


# Las bases que son LA REPLICA DE VERDAD y no una copia. Una prueba no las
# abre.
#
# `BASE_DIR/local.db` es la de la notebook. `DATA_DIR/local.db` es la del
# volumen de Railway -- en el contenedor las dos rutas son distintas, y en la
# notebook DATA_DIR cae adentro del repo, asi que nombrar las dos no es
# redundante: es cubrir los dos despliegues con la misma linea.
def _replicas_reales():
    return {
        _normal(os.path.join(BASE_DIR, "local.db")),
        _normal(os.path.join(DATA_DIR, "local.db")),
    }


def _normal(ruta):
    """Para comparar rutas: resuelve enlaces y '..', y en Windows aplana
    mayusculas. Sin esto, 'C:/Regla_Python/local.db' y
    'c:\\regla_python\\local.db' serian dos bases distintas -- y la guarda
    dejaria pasar la que se escribio con otra caja."""
    try:
        return os.path.normcase(os.path.realpath(ruta))
    except Exception:                            # noqa: BLE001
        return os.path.normcase(os.path.abspath(ruta))


def exigir_replica_de_prueba(path):
    """Revienta si una prueba intenta abrir la replica REAL.

    POR QUE EXISTE
    --------------
    `exigir_destino_local` cubre las llamadas HTTP: una prueba no le habla al
    legado de produccion. No cubria las ESCRITURAS, y el agujero no era
    teorico -- el 2026-09-02 una prueba de humo del check list mecanico
    escribio en `local.db` un movimiento sobre la unidad 92095, que es una
    unidad real, con su entrada de cola sin resolver y `push_pendiente = 1`.

    Las tres consecuencias, de menor a mayor:

      1. Una fila de mas en el historial de REGLA.
      2. Una entrada de cola viva: si el demonio de push hubiera corrido, esa
         prueba habria escrito en el LEGADO DE PRODUCCION. La guarda de
         destino no la habria frenado, porque el demonio no corre bajo un
         `probar_*`.
      3. `push_pendiente = 1` sobre una unidad real. El UPSERT del pull
         SALTEA las filas con ese flag, asi que esa unidad deja de recibir
         actualizaciones del legado -- en silencio, sin que ningun error lo
         diga, para siempre.

    Se limpio a mano y quedo bien. Pero limpiar a mano depende de acordarse, y
    acá la disciplina ya fallo siete veces.

    MISMO CRITERIO Y MISMO LUGAR QUE LA OTRA GUARDA. Se activa sola por el
    nombre del script (`probar_*`), se apaga con la misma variable
    (`REGLA_SOLO_LOCAL=0`) y vive al lado. Dos guardas que se activan igual
    son una regla; dos que se activan distinto son dos reglas que recordar.

    VA EN `conectar_db` Y NO EN CADA PRUEBA. Es el unico camino por el que se
    abre la base -- lo usan `get_db()` de Flask, los scripts y los hilos del
    push --, asi que una linea cubre los tres. Puesta prueba por prueba, la
    proxima quedaba sin guarda y sin que nadie lo note; que es exactamente el
    argumento por el que el push se engancho en `registrar()`.

    LO QUE NO CUBRE, dicho para que nadie se confie: un `sqlite3.connect`
    escrito a mano la saltea. La prueba de humo que origino esto era un
    `python - <<EOF` suelto, que ni siquiera se llama `probar_*`. Esta guarda
    protege el camino que usa la aplicacion, que es por donde entro el daño
    de verdad -- las tres consecuencias de arriba salieron del `get_db()` de
    Flask, no del `connect` suelto."""
    # DOS FORMAS DE ACTIVARLA, y la segunda existe por una asimetria real.
    #
    # `_es_prueba()` --el nombre `probar_*`-- activa LAS DOS guardas juntas, y
    # eso alcanza para las suites, que no le hablan a produccion.
    #
    # Pero un script de VERIFICACION necesita las dos al reves: hablarle a
    # produccion de verdad Y no tocar la replica real. Con una sola palanca eso
    # no se puede pedir, y el 2026-09-02 esa asimetria costo exactamente lo que
    # esta guarda existe para impedir: `verificar_check_mecanico_produccion.py`
    # copio la replica, apunto DB_PATH a la copia... y escribio igual en la
    # real, porque `core.DB_PATH` se fija al IMPORTAR y el script importaba
    # `sync_legado` doce lineas antes de tocar el entorno.
    #
    # `REGLA_REPLICA_PROTEGIDA=1` prende esta guarda sola, sin prender la de
    # destino.
    forzada = os.environ.get("REGLA_REPLICA_PROTEGIDA", "").strip()
    protegida = (forzada not in ("", "0", "no", "false")) or _es_prueba()
    if not protegida:
        return path
    if _normal(path) not in _replicas_reales():
        return path
    raise RuntimeError(
        "GUARDA DE REPLICA: una prueba intento abrir la replica REAL\n"
        "  {}\n"
        "  Las pruebas trabajan sobre una COPIA. Las dos formas de hacerlo, "
        "las dos ya usadas en el proyecto:\n"
        "    tmp = tempfile.mkdtemp()\n"
        "    shutil.copy(os.path.join(RAIZ, 'local.db'), "
        "os.path.join(tmp, 'prueba.db'))   # necesita datos reales\n"
        "    os.environ['DB_PATH'] = os.path.join(tmp, 'prueba.db')\n"
        "  o, si no necesita datos, apuntar DB_PATH a una base vacia del "
        "tempdir.\n"
        "  Si de verdad hace falta tocar la replica -- un script de "
        "mantenimiento que se llame probar_* --, REGLA_SOLO_LOCAL=0 apaga "
        "las DOS guardas; para apagar solo esta, REGLA_REPLICA_PROTEGIDA=0.\n"
        "  OJO CON EL ORDEN DE LOS IMPORTS: `core.DB_PATH` se fija al "
        "importar. Poner DB_PATH en el entorno DESPUES de que algo haya "
        "importado `core` no cambia nada -- hay que asignar `core.DB_PATH` "
        "a mano.".format(path))


def exigir_destino_local(url, quien=""):
    """Revienta si `url` no apunta a localhost y estamos en una prueba.

    La llaman los dos clientes HTTP en su __init__, asi que cubre tanto al que
    pasa mal el base_url como al que se olvida de pasarlo y cae al de
    produccion por defecto.

    Falla al CONSTRUIR el cliente y no al pedir: asi el error sale antes de que
    la prueba escriba nada local, y el traceback apunta a quien lo construyo."""
    if not _es_prueba():
        return url
    host = ""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url or "").hostname or "").lower()
    except Exception:                            # noqa: BLE001
        host = ""
    if host in DESTINOS_LOCALES:
        return url
    raise RuntimeError(
        "GUARDA DE DESTINO: {} intento apuntar a {!r} desde {!r}.\n"
        "  Las pruebas (scripts/probar_*.py) solo pueden hablarle al legado "
        "simulado en localhost.\n"
        "  Pasale `base_url` EXPLICITO al cliente -- setear LEGADO_BASE_URL por "
        "entorno NO alcanza:\n"
        "  las constantes de base se leen al importar el modulo, asi que "
        "cambiarla despues no tiene efecto\n"
        "  y la peticion se va a produccion. Eso mismo paso el 2026-08-27."
        .format(quien or "un cliente", url,
                os.path.basename(sys.argv[0] or "?")))


# ---------------------------------------------------------------------------
# Que version se esta sirviendo
# ---------------------------------------------------------------------------
#
# NACIO DE PERDER UNA SEMANA. El 2026-08-27 se probo la pantalla de Movimientos
# en Railway buscando el campo de patio y calle, y no estaba: el commit
# desplegado era anterior a todo el trabajo de la semana. No habia forma de
# saberlo mirando la pantalla, y ya habia pasado antes con el deploy de b1f81dd.
#
# El problema no es el despliegue: es que "lo que estoy probando" y "lo que creo
# que estoy probando" son dos cosas y nada las compara. Esto las compara.
#
# De donde sale el commit, en orden:
#
#   1. REGLA_COMMIT           si alguien la define a mano
#   2. RAILWAY_GIT_COMMIT_SHA la pone Railway sola en cada despliegue
#   3. `git rev-parse`        en la maquina de desarrollo, donde si hay repo
#   4. 'desconocido'          antes que mentir
#
# El paso 3 NO cachea el resultado a proposito: en desarrollo el commit cambia
# mientras el proceso vive, y un valor congelado seria justo el tipo de mentira
# que esto viene a evitar. Cuesta un fork por request de una pagina que nadie
# recarga en bucle.

def commit_desplegado():
    """El commit que este proceso esta sirviendo. Siempre devuelve algo."""
    for variable in ("REGLA_COMMIT", "RAILWAY_GIT_COMMIT_SHA"):
        valor = (os.environ.get(variable) or "").strip()
        if valor:
            return valor[:7]
    try:
        import subprocess
        salida = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=BASE_DIR, capture_output=True, text=True, timeout=2)
        if salida.returncode == 0 and salida.stdout.strip():
            sucio = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=BASE_DIR, capture_output=True, text=True, timeout=2)
            # EL SUFIJO `+` ES LA MITAD DEL VALOR. Un commit limpio dice que lo
            # que corre es exactamente lo que esta en git; con cambios sin
            # commitear, el commit NO describe lo que se esta sirviendo, y eso
            # es precisamente el caso que hay que poder ver.
            marca = "+" if (sucio.returncode == 0 and sucio.stdout.strip()) else ""
            return salida.stdout.strip() + marca
    except Exception:                            # noqa: BLE001
        pass
    return "desconocido"


# ---------------------------------------------------------------------------
# Los indices que la app necesita y el dump no trae
# ---------------------------------------------------------------------------
#
# El importador crea la tabla desde el dump del legado, asi que los indices que
# REGLA necesita para SUS consultas no vienen. Se crean al arrancar, junto a las
# guardas y por el mismo motivo: perezoso significa que el primero que entra
# paga, y en Railway ese primero es un movilizador parado en el patio.

# La busqueda de Movimientos, que corre en CADA TECLA de la busqueda en vivo.
#
# UN INDICE CUBRIDOR SON DOS LISTAS QUE TIENEN QUE COINCIDIR, y cubre SOLO
# mientras las columnas del SELECT sean subconjunto de las del indice. Basta
# con que alguien agregue una columna al SELECT para que el indice deje de
# cubrir, SQLite vuelva a la tabla de 382 MB, y la busqueda pase de 18 ms a un
# segundo -- sin un error, sin un log, sin nada. Es el mismo patron de la lista
# blanca y de CALLE_POR_ESTADO/PATIO_POR_ESTADO, y ya sabemos como termina.
#
# POR ESO NO SON DOS LISTAS: son estas dos, y el SQL de la busqueda se ARMA con
# ellas (ver `movimientos._buscar`). No hay un literal que pueda separarse.
# `scripts/probar_ubicacion.py` lo afirma igual, por si alguien vuelve a
# escribir el SELECT a mano.
BUSQUEDA_FILTRA = ("vin", "patente", "n_motor")
BUSQUEDA_DEVUELVE = ("id", "vin", "patente", "marca", "modelo", "color",
                     "clientecompleto", "despachado")

# El orden importa: primero las del WHERE. Un LIKE con `%` adelante no puede
# SALTAR por indice -- hay que recorrer todo -- pero si puede recorrer el
# INDICE (6 MB) en vez de la TABLA (382 MB). Medido: 66 ms -> 18 ms caliente.
INDICES = {
    "ix_newstocks_busqueda": (
        "CREATE INDEX IF NOT EXISTS ix_newstocks_busqueda ON newstocks_cidef ({})"
        .format(", ".join(BUSQUEDA_FILTRA
                          + tuple(c for c in BUSQUEDA_DEVUELVE
                                  if c not in BUSQUEDA_FILTRA)))),
}


def instalar_indices(db_path=None):
    """Crea los indices de INDICES. Idempotente, se llama desde `crear_app()`.

    No revienta si falla: un indice que no se pudo crear hace la app LENTA, no
    incorrecta, y tirar el arranque por eso seria peor. Pero lo dice en el log,
    porque una consulta con `INDEXED BY` sobre un indice que no existe si
    revienta -- y el mensaje de ahi no explicaria por que."""
    db = None
    try:
        db = conectar_db(db_path)
        creados = []
        for nombre, sql in INDICES.items():
            db.execute(sql)
            creados.append(nombre)
        db.commit()
        print("[indices] {} de {}: {}".format(
            len(creados), len(INDICES), ", ".join(creados)), flush=True)
    except Exception as e:                       # noqa: BLE001
        print("[indices] NO se pudieron crear: {}: {}".format(
            type(e).__name__, e), flush=True)
    finally:
        if db is not None:
            db.close()
