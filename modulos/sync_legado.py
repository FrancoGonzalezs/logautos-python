"""
modulos/sync_legado.py -- trae hacia REGLA lo que se sigue escribiendo en
claude.logautos.cl (el sistema legado: PHP + MariaDB) mientras los dos
conviven. Primera etapa: solo `newstocks_cidef` (las unidades).

La arquitectura es la del sync de Talca (taller-inventario), adaptada. Lo que
sigue son las decisiones que condicionan este archivo, cada una con la
evidencia que la sostiene.

1. PULL, NO PUSH
   Es Python quien sale a preguntar "que cambio". El legado no empuja nada, y
   no es una preferencia: `Examples.php` tiene 363 instancias de
   `grocery_CRUD`, de las cuales 172 editan `newstocks_cidef` directo, 83
   `orden_trabajo` y 8 hasta `registros` -- y CERO
   `callback_after_insert`/`callback_after_update`. No existe un solo lugar
   donde enganchar un push. Con hooks parciales, cualquier grilla que se
   escape hace divergir los datos en silencio.

   Ademas el pull se repara solo: si una vuelta falla, la marca de agua no
   avanza y la siguiente trae lo pendiente.

2. EL MATCH ES POR `id`, JAMAS POR VIN
   `newstocks_cidef` tiene 71.546 filas y 61.447 VIN distintos: 10.099 filas
   de mas, un 14,1%. Hay 4.232 VIN con dos filas y uno con CATORCE. Ademas
   8.084 VIN tienen largo invalido y 3 estan vacios. El `id` es unico en las
   71.546 y es lo unico estable: cada fila es una pasada de la unidad por el
   flujo, no el vehiculo.

3. LOS TIMESTAMPS SON TEXTO OPACO
   Este modulo no parsea ni una fecha ni le hace aritmetica a ninguna. La
   marca de agua es el campo `hasta` que devuelve el legado -- hora de SU
   servidor --, se guarda tal cual y se manda tal cual. Hasta el margen de
   solapamiento lo aplica el legado dentro de su propio SQL. Asi no hay dos
   relojes que conciliar: si Python usara el suyo, cualquier desfase con el
   hosting saltearia registros sin que nadie se entere.

4. EL USER-AGENT NO ES COSMETICA
   El hosting de claude.logautos.cl CIERRA LA CONEXION cuando el User-Agent
   dice "python-requests": no responde 403, cierra el socket. Del lado de
   Python llega como RemoteDisconnected / "connection aborted", que parece un
   problema de red y manda a diagnosticar en la direccion equivocada.

   Comprobado contra el server real: mismo GET, tres User-Agent distintos --
   python-requests falla con RemoteDisconnected, un UA de navegador responde
   200 en 191 ms y `REGLA-sync/1.0` responde 200 en 156 ms. Es el mismo
   comportamiento que documento el sync de Talca con Bluehosting.

El desfase horario del dump
---------------------------
La replica se armo de un dump exportado con otra zona horaria que la del
servidor vivo: los timestamps locales estan adelantados.

CUIDADO, ESTO SE MIDIO MAL LA PRIMERA VEZ. Decia "+4,0 h en las 200 filas --
uniforme, no ruido", y la muestra no daba para esa conclusion: las 200 caian
en la misma epoca del ano. El 2026-08-26, comparando la replica contra
produccion fila por fila, aparecio esto:

    unidad 66504   2026-06-11    +4 h
    unidad 67009   2025-07-02    +4 h
    unidad 65021   2025-04-04    +3 h   <--

No es un offset fijo: es una CONVERSION DE ZONA HORARIA, y Chile tiene
horario de verano. El 4 de abril de 2025 todavia estaba en DST (UTC-3) y las
otras dos fechas en horario normal (UTC-4). El desfase depende de en que lado
del cambio de hora cae cada timestamp.

Importa para lo que viene: cualquier arreglo del estilo "restarle 4 horas"
esta mal medio ano. La reconciliacion completa (`--desde ""`), que hace que
manden los valores del legado y no una cuenta nuestra, no tiene ese problema
-- que es otro argumento para no hacer aritmetica de fechas de este lado.

DECISION: el pull los alinea. Manda el legado, asi que al traer una fila se
reescriben `created_at` y `updated_at` con los de alla. Es un cambio visible
en toda fila que se toque, y es deliberado.

Esto ademas es la prueba de por que la nota 3 importa. La marca de agua es el
reloj del legado comparado contra si mismo, asi que el desfase no la afecta.
Si Python hubiera usado su propio reloj, 4 horas de diferencia habrian hecho
que el sync se salteara registros en silencio -- que es exactamente el modo
de fallar que no se nota hasta que faltan datos.

La guarda de `push_pendiente`
-----------------------------
Este modulo no empuja: eso es `push_legado.py`. Pero desde que el push existe,
el pull tiene que mirar por donde pisa, y ese chequeo esta en `_upsert`.

Una fila con `push_pendiente = 1` tiene un cambio nuestro sin confirmar del
otro lado. Lo que el legado devuelve de esa fila es la version vieja, asi que
escribirla desharia en la replica lo que estamos por mandar. Se saltea entera
hasta que el push la resuelva -- con exito o con conflicto, los dos bajan el
flag.

La columna se agrega sola (`push_legado.asegurar_tablas`) y la guarda se
enciende cuando aparece, asi que una base que nunca vio un push sigue andando
igual.

Limitacion conocida
-------------------
74 filas (0,1%, de las cuales 18 vivas) no tienen `created_at` NI
`updated_at` usables. Un pull por marca de agua no las trae nunca. No se
inventa una fecha para taparlo: se resuelve con una reconciliacion completa
ocasional (`--desde ""`), que es barata porque son 358 paginas de 200.

Como se corre
-------------
    python -m modulos.sync_legado unidades --dry-run
    python -m modulos.sync_legado unidades
    python -m modulos.sync_legado unidades --desde ""      # reconciliacion

y el hilo de fondo de app.py, que solo arranca si SYNC_INTERVALO_SEGUNDOS
esta definida y es > 0. Este modulo no sabe nada de ese hilo ni importa
app.py.
"""

import argparse
import os
import sqlite3
import sys
import time

from core import DB_PATH

try:
    import requests
except ImportError:                              # pragma: no cover
    requests = None


# Cuantas filas pedir por pagina. 200 sobre 71.546 unidades son 358 vueltas en
# una reconciliacion completa; un incremental normal se resuelve en una.
LIMITE_DEFECTO = int(os.environ.get("SYNC_LIMITE", "200"))

# Tope de paginas por corrida. No es una expectativa de volumen sino un
# cinturon de seguridad: si el endpoint tuviera un error de paginacion que lo
# hiciera devolver siempre la misma pagina, la corrida termina con un error
# claro en vez de girar para siempre contra produccion.
MAX_PAGINAS = int(os.environ.get("SYNC_MAX_PAGINAS", "500"))

TIMEOUT_DEFECTO = 30.0

# Ver la nota 4 del encabezado. Cualquier cosa menos "python-requests".
USER_AGENT = os.environ.get("SYNC_USER_AGENT", "REGLA-sync/1.0 (+https://logautos.cl)")

BASE_URL_DEFECTO = os.environ.get("LEGADO_BASE_URL", "https://claude.logautos.cl")


# ---------------------------------------------------------------------------
# Las entidades
# ---------------------------------------------------------------------------
#
# Una sola por ahora. La estructura queda armada para las que vienen porque el
# orden entre ellas importa: `registros` y `orden_trabajo` se atan a la unidad
# (por vin y por id_vehiculo), asi que nada se sincroniza antes que las
# unidades. Esa es toda la cadena de precondiciones de Logautos -- y es mas
# corta que la de Talca a proposito: alla es clientes -> vehiculos -> OT
# porque hay tabla `clientes` con FK NOT NULL, y aca NO hay tabla de clientes:
# el cliente es un campo de texto (`clientecompleto`) en la propia unidad.

ENTIDADES = {
    "unidades": {
        "tabla": "newstocks_cidef",
        "ruta": "/api_regla/cambios/unidades",
        "precondiciones": [],
    },
}


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------

def _conectar(db_path=None):
    db = sqlite3.connect(db_path or DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def asegurar_tablas(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS sync_estado (
          entidad            TEXT PRIMARY KEY,
          marca_agua         TEXT NOT NULL DEFAULT '',
          ultima_corrida_en  TEXT NOT NULL DEFAULT '',
          ultimo_resultado   TEXT NOT NULL DEFAULT '',
          ultimo_detalle     TEXT NOT NULL DEFAULT '',
          filas_recibidas    INTEGER NOT NULL DEFAULT 0,
          filas_creadas      INTEGER NOT NULL DEFAULT 0,
          filas_actualizadas INTEGER NOT NULL DEFAULT 0
        )""")


def estado_de(db, entidad):
    asegurar_tablas(db)
    fila = db.execute("SELECT * FROM sync_estado WHERE entidad = ?",
                      (entidad,)).fetchone()
    if fila is None:
        db.execute("INSERT INTO sync_estado (entidad) VALUES (?)", (entidad,))
        db.commit()
        fila = db.execute("SELECT * FROM sync_estado WHERE entidad = ?",
                          (entidad,)).fetchone()
    return fila


def _guardar_estado(db, entidad, marca_agua, resultado, detalle,
                    recibidas, creadas, actualizadas):
    db.execute("""
        UPDATE sync_estado
           SET marca_agua = ?, ultima_corrida_en = ?, ultimo_resultado = ?,
               ultimo_detalle = ?, filas_recibidas = ?, filas_creadas = ?,
               filas_actualizadas = ?
         WHERE entidad = ?""",
        (marca_agua, time.strftime("%Y-%m-%dT%H:%M:%S"), resultado,
         detalle[:500], recibidas, creadas, actualizadas, entidad))
    db.commit()


# ---------------------------------------------------------------------------
# El cliente HTTP
# ---------------------------------------------------------------------------

class Legado(object):
    """Habla con claude.logautos.cl.

    Config por entorno:
        LEGADO_BASE_URL   la base, por defecto https://claude.logautos.cl
        LEGADO_API_KEY    el header X-API-Key
    """

    def __init__(self, base_url=None, api_key=None, timeout=None):
        self.base_url = (base_url or BASE_URL_DEFECTO).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("LEGADO_API_KEY", "")
        self.timeout = timeout or TIMEOUT_DEFECTO

    def _cabeceras(self):
        return {"X-API-Key": self.api_key, "User-Agent": USER_AGENT}

    def cambios(self, ruta, desde, limite, pagina):
        if requests is None:
            raise RuntimeError("falta la dependencia `requests`")
        if not self.api_key:
            raise RuntimeError(
                "falta LEGADO_API_KEY: el endpoint la exige en X-API-Key")
        r = requests.get(
            self.base_url + ruta,
            params={"desde": desde, "limite": limite, "pagina": pagina},
            headers=self._cabeceras(), timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError("HTTP {} de {}: {}".format(
                r.status_code, ruta, (r.text or "")[:200]))
        try:
            return r.json()
        except ValueError:
            raise RuntimeError(
                "el endpoint no devolvio JSON. Primeros 200 caracteres: {!r}"
                .format((r.text or "")[:200]))


# ---------------------------------------------------------------------------
# El UPSERT
# ---------------------------------------------------------------------------

def _columnas(db, tabla):
    return [r[1] for r in db.execute('PRAGMA table_info("{}")'.format(tabla))]


def _upsert(db, tabla, filas, columnas_validas):
    """Inserta o actualiza por `id`. Devuelve (creadas, actualizadas, saltadas).

    Solo se escriben las columnas que EXISTEN en la replica: si el legado
    agrega una columna nueva, esto la ignora en vez de romper. La contraria --
    una columna que la replica tiene y el legado dejo de mandar -- se deja
    como esta, sin pisarla con NULL.

    LA GUARDA DE `push_pendiente`
    -----------------------------
    Una fila con push_pendiente=1 tiene un cambio nuestro todavia sin
    confirmar del otro lado. Lo que el legado devuelve de esa fila es, por
    definicion, la version VIEJA -- la de antes de nuestro cambio --, asi que
    escribirla seria deshacer en la replica lo que estamos por mandar, y ademas
    dejar la pantalla mostrando el estado anterior al que el usuario acaba de
    guardar. Se saltea entera y se retoma cuando el push la resuelva (con exito
    o con conflicto: los dos casos bajan el flag).

    La columna puede no existir todavia -- la agrega push_legado.asegurar_tablas
    y una base que nunca vio un push no la tiene --, asi que la guarda se
    activa sola cuando aparece en vez de exigir una migracion previa."""
    creadas = actualizadas = saltadas = 0
    vigila_push = "push_pendiente" in columnas_validas
    columna_estado = ("push_pendiente" if vigila_push else "1")
    for fila in filas:
        if "id" not in fila:
            continue
        campos = [c for c in fila.keys()
                  if c in columnas_validas and c not in ("id", "push_pendiente")]
        if not campos:
            continue
        actual = db.execute(
            'SELECT {} AS pendiente FROM "{}" WHERE id = ?'.format(
                columna_estado, tabla), (fila["id"],)).fetchone()
        if actual is not None and vigila_push and actual["pendiente"]:
            saltadas += 1
            continue
        if actual is not None:
            db.execute(
                'UPDATE "{}" SET {} WHERE id = ?'.format(
                    tabla, ", ".join('"{}" = ?'.format(c) for c in campos)),
                [fila[c] for c in campos] + [fila["id"]])
            actualizadas += 1
        else:
            db.execute(
                'INSERT INTO "{}" (id, {}) VALUES ({})'.format(
                    tabla, ", ".join('"{}"'.format(c) for c in campos),
                    ", ".join("?" * (len(campos) + 1))),
                [fila["id"]] + [fila[c] for c in campos])
            creadas += 1
    return creadas, actualizadas, saltadas


# ---------------------------------------------------------------------------
# La corrida
# ---------------------------------------------------------------------------

def sincronizar_entidad(entidad, dry_run=False, desde=None, limite=None,
                        db_path=None, cliente=None):
    """Una vuelta del pull. Devuelve un dict con lo que paso.

    `dry_run` pide y muestra, pero NO escribe: ni las filas ni la marca de
    agua. Es lo que se corre primero contra produccion."""
    if entidad not in ENTIDADES:
        raise ValueError("entidad desconocida: {}".format(entidad))
    conf = ENTIDADES[entidad]

    # Los contadores nacen FUERA del try porque el manejador de error los lee:
    # desde que se commitea por pagina, una corrida que se corta dejo filas
    # escritas de verdad, y decir "0 filas" en el estado seria mentir sobre lo
    # que hay en la base. Si se inicializaran adentro, una excepcion temprana
    # -- una precondicion que falta, por ejemplo -- haria estallar el propio
    # manejador con NameError y taparia el error original.
    recibidas = creadas = actualizadas = saltadas = 0

    db = _conectar(db_path)
    try:
        for previa in conf["precondiciones"]:
            est = estado_de(db, previa)
            if not est["marca_agua"]:
                raise RuntimeError(
                    "'{}' depende de '{}', que nunca se sincronizo"
                    .format(entidad, previa))

        est = estado_de(db, entidad)
        # `desde` explicito gana; si no, la marca de agua guardada. Cadena
        # vacia = traer todo (reconciliacion completa).
        marca = est["marca_agua"] if desde is None else desde
        limite = limite or LIMITE_DEFECTO
        cliente = cliente or Legado()

        columnas = _columnas(db, conf["tabla"])
        marca_nueva = marca
        pagina = 1
        muestra = []

        # Los ids ya vistos, para distinguir "hay mucho por traer" de "el
        # endpoint esta repitiendo la misma pagina". Sin esto las dos cosas se
        # ven igual desde acá: muchas vueltas.
        vistos = set()

        while pagina <= MAX_PAGINAS:
            datos = cliente.cambios(conf["ruta"], marca, limite, pagina)
            filas = datos.get("filas") or []

            # Una pagina con filas que no aporta NI UN id nuevo solo puede ser
            # el endpoint devolviendo lo mismo: si de verdad hubiera mas datos,
            # traeria ids distintos. Se corta acá y no al llegar al tope,
            # porque son 500 vueltas de diferencia contra el servidor.
            nuevos = {f.get("id") for f in filas if f.get("id") is not None} - vistos
            if filas and not nuevos:
                raise RuntimeError(
                    "el endpoint devolvio la pagina {} con {} filas y ningun "
                    "id nuevo: esta repitiendo la misma pagina. Revisar la "
                    "paginacion de {}".format(pagina, len(filas), conf["ruta"]))
            vistos |= nuevos

            recibidas += len(filas)
            if len(muestra) < 3:
                muestra.extend(filas[:3 - len(muestra)])
            if not dry_run and filas:
                c, a, s_ = _upsert(db, conf["tabla"], filas, columnas)
                creadas += c
                actualizadas += a
                saltadas += s_
                # UN COMMIT POR PAGINA, no uno solo al final.
                #
                # La marca de agua NO se toca acá: sigue avanzando una sola vez,
                # al terminar bien. Lo que se hace durable es el dato, no el
                # progreso. Si la corrida se corta en la pagina 200, las 199
                # anteriores quedan escritas y la marca sigue donde estaba, asi
                # que la proxima vuelta las vuelve a pedir y el UPSERT las pisa
                # con lo mismo. Traer dos veces la misma fila es gratis; perder
                # 199 paginas de trabajo, no.
                #
                # Antes esto era un solo commit despues del `while`, y las 358
                # paginas de una reconciliacion completa vivian en UNA
                # transaccion. Tres problemas, y el tercero es el que obligo:
                #
                #   1. Un corte en la pagina 350 tiraba las 349 anteriores.
                #   2. La transaccion se queda con el lock de escritura toda la
                #      corrida, asi que la app no puede escribir mientras dura.
                #   3. El WAL tiene que retener cada pagina sucia hasta el
                #      commit. Medido sobre la replica: 68 MB. El volumen de
                #      Railway tiene 69 MB libres de 434 -- seis megas de
                #      margen, que no es un margen. Commiteando por pagina,
                #      SQLite puede hacer checkpoint entre una y otra y el WAL
                #      se mantiene chico.
                #
                # Es el mismo criterio del pull entero: el trabajo hecho no se
                # tira, y lo que se repara solo es el progreso, no el dato.
                db.commit()
            # El `hasta` del legado es la hora de SU servidor. Se guarda tal
            # cual: ver la nota 3 del encabezado.
            if datos.get("hasta"):
                marca_nueva = datos["hasta"]
            if not datos.get("hay_mas"):
                break
            pagina += 1
        else:
            # Llegar al tope trayendo ids nuevos todo el tiempo no es un
            # endpoint roto -- eso ya lo habria cortado el chequeo de arriba.
            # Es que hay mas datos de los que entran con este limite.
            #
            # Paso de verdad: un dry-run de reconciliacion completa con
            # --limite 20 son 3.578 paginas sobre 71.546 filas, y el mensaje
            # viejo culpaba al endpoint. Mandaba a mirar el lugar equivocado, y
            # de paso a hacerle 500 requests al servidor para nada.
            raise RuntimeError(
                "se alcanzo el tope de {tope} paginas con {filas:,} filas "
                "traidas y limite={limite}. El endpoint pagina bien (cada "
                "pagina trajo ids nuevos): hay mas datos de los que entran. "
                "Subi --limite -- con {sugerido} alcanzaria -- o acota el "
                "rango con --desde.".format(
                    tope=MAX_PAGINAS, filas=recibidas, limite=limite,
                    sugerido=max(limite * 2,
                                 ((recibidas // MAX_PAGINAS) + 1) * 4)))

        if dry_run:
            db.rollback()
        else:
            # Las filas ya estan commiteadas pagina por pagina; esto solo cierra
            # una transaccion que haya quedado abierta por una pagina vacia.
            # Lo que de verdad pasa acá es que la marca de agua avanza, y recien
            # ahora: es el unico punto donde la corrida se declara completa.
            db.commit()
            _guardar_estado(db, entidad, marca_nueva, "ok", "",
                            recibidas, creadas, actualizadas)

        return {"entidad": entidad, "dry_run": dry_run,
                "marca_agua_previa": marca, "marca_agua_nueva": marca_nueva,
                "paginas": pagina, "recibidas": recibidas,
                "creadas": creadas, "actualizadas": actualizadas,
                "saltadas": saltadas, "muestra": muestra}
    except Exception as e:
        if not dry_run:
            try:
                # Los contadores REALES, no ceros. Con el commit por pagina
                # una corrida cortada deja filas escritas, y el estado tiene
                # que decir cuantas: es la diferencia entre "fallo y no hizo
                # nada" y "fallo en la pagina 200 de 358", que se atienden
                # distinto. La marca de agua se reescribe con la GUARDADA, sin
                # avanzar: el progreso no se conserva, el dato si.
                _guardar_estado(db, entidad, estado_de(db, entidad)["marca_agua"],
                                "error", "{}: {}".format(type(e).__name__, e),
                                recibidas, creadas, actualizadas)
            except Exception:                    # pragma: no cover
                pass
        raise
    finally:
        db.close()


def todo(dry_run=False):
    """Todas las entidades, en orden. Hoy es una sola."""
    return [sincronizar_entidad(e, dry_run=dry_run) for e in ENTIDADES]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Pull desde claude.logautos.cl")
    ap.add_argument("entidad", nargs="?", default=None,
                    help="unidades. Sin argumento corre todas.")
    ap.add_argument("--dry-run", action="store_true",
                    help="pide y muestra, pero no escribe nada")
    ap.add_argument("--desde", default=None,
                    help='marca de agua a usar. "" trae todo (reconciliacion)')
    ap.add_argument("--limite", type=int, default=None)
    args = ap.parse_args(argv)

    entidades = [args.entidad] if args.entidad else list(ENTIDADES)
    for entidad in entidades:
        try:
            r = sincronizar_entidad(entidad, dry_run=args.dry_run,
                                    desde=args.desde, limite=args.limite)
        except Exception as e:
            print("{}: ERROR {}: {}".format(entidad, type(e).__name__, e))
            return 1
        print("{}{}".format(entidad, "  (DRY-RUN, no se escribio nada)"
                            if r["dry_run"] else ""))
        print("  marca de agua : {!r} -> {!r}".format(
            r["marca_agua_previa"], r["marca_agua_nueva"]))
        print("  paginas       : {}".format(r["paginas"]))
        print("  recibidas     : {:,}".format(r["recibidas"]))
        if not r["dry_run"]:
            print("  creadas       : {:,}".format(r["creadas"]))
            print("  actualizadas  : {:,}".format(r["actualizadas"]))
            # Solo se nombra cuando hay: en la corrida normal es cero y una
            # linea fija en cero es ruido que se deja de leer.
            if r["saltadas"]:
                print("  saltadas      : {:,}  (push_pendiente=1: hay un "
                      "cambio nuestro sin confirmar)".format(r["saltadas"]))
        for fila in r["muestra"]:
            print("  muestra: id={} vin={} estado={}".format(
                fila.get("id"), fila.get("vin"), fila.get("despachado")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
