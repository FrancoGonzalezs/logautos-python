"""
modulos/destinatarios.py -- a quien le llega cada correo, en una TABLA.

POR QUE UNA TABLA Y NO UNA CONSTANTE
====================================

El legado los tiene cableados en el PHP. Nadie los puede cambiar sin desplegar,
y la lista de a quien le llega un correo con datos de un vehiculo es justo lo
que cambia sin avisar cuando alguien entra o sale de un puesto.

LO QUE HAY HOY, Y NO ES LO QUE PARECIA
======================================

`Nota.php:inspeccion_despacho()` manda a UNA sola direccion:

    controldespachos@logautos.cl

Todo el resto --las 13 ramas por `$destino`, CIDEF, POMPEYO, CARFLEX-- esta
dentro de un `/* */` de 116 lineas. Y NO es codigo olvidado: se comento A
PROPOSITO. Confirmado por Franco el 2026-09-08: el cliente NO quiere el correo
de la inspeccion; las imagenes le llegan en el PDF del despacho. La direccion
que quedo viva es para tener registro interno.

Asi que REGLA replica exactamente eso: una fila, interna. **Los destinatarios
comentados no se reviven** -- revivirlos seria un cambio de comportamiento
hacia terceros disfrazado de migracion.

LAS DOS CONDICIONES, escritas aunque hoy no tengan sobre que aplicarse
======================================================================

Quedan porque el dia que alguien agregue una regla por destino, tienen que
estar ya decididas y no discutirse de nuevo:

  1. LAS RAMAS EN CERO NO SE MIGRAN. `Vega`, `REAL` y `Grass` tenian cero
     inspecciones en doce meses. No entran.

  2. PERO UN DESTINO QUE NO CALZA CON NADA **CAE AL CONJUNTO POR DEFECTO Y SE
     REGISTRA**. Cero en doce meses no es "muerta": es "no observada". Es el
     precedente de `LAVADO KSM` -- un valor que no aparecia en la ventana que
     miramos y existia igual. Si se descarta sin registrar, el dia que
     reaparezca nadie se entera; con el registro, aparece en la reconciliacion
     y alguien decide.

Por eso `destinos_sin_regla_regla` existe desde el primer dia aunque hoy no
pueda llenarse: el mecanismo tiene que estar antes que el caso.
"""

from datetime import datetime

from core import consultar, get_db

# El modulo al que pertenece cada regla. Va explicito y no implicito por la
# tabla: el dia que el check list tambien mande correo, sus destinatarios no
# tienen por que ser los de la inspeccion.
MODULO_INSPECCION = "inspeccion_despacho"

# La clave comodin. Una regla con esta clave es "para todo destino de este
# modulo", y es la que hoy tiene la unica fila.
TODOS = "*"

# Lo que el legado manda hoy, copiado del archivo. Se siembra una sola vez; si
# alguien edita la tabla, no se vuelve a pisar.
SEMILLA = (
    (MODULO_INSPECCION, TODOS, "controldespachos@logautos.cl",
     "El unico destinatario vivo del correo de inspeccion. El resto del bloque "
     "del legado esta comentado A PROPOSITO: el cliente no quiere este correo, "
     "las imagenes le llegan en el PDF del despacho."),
)


def _asegurar_tablas(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS destinatarios_regla (
          id INTEGER PRIMARY KEY,
          modulo TEXT NOT NULL,
          clave TEXT NOT NULL,          -- el destino, o '*' para todos
          direccion TEXT NOT NULL,
          activo INTEGER NOT NULL DEFAULT 1,
          nota TEXT,
          creado_en TEXT
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_destinatarios_regla_modulo "
               "ON destinatarios_regla (modulo, clave)")

    # LOS DESTINOS QUE NO CALZARON CON NINGUNA REGLA.
    #
    # No es un log: es una tabla, porque un log que nadie abre no es una señal
    # -- ya nos costo el `push_pendiente` trabado y el aviso del truncamiento.
    # La reconciliacion diaria la mira.
    db.execute("""
        CREATE TABLE IF NOT EXISTS destinos_sin_regla_regla (
          id INTEGER PRIMARY KEY,
          modulo TEXT NOT NULL,
          destino TEXT NOT NULL,
          veces INTEGER NOT NULL DEFAULT 1,
          primera_vez TEXT,
          ultima_vez TEXT
        )""")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_destinos_sin_regla "
               "ON destinos_sin_regla_regla (modulo, destino)")

    # La semilla, UNA vez. `INSERT OR IGNORE` no sirve --no hay clave unica
    # sobre (modulo, clave, direccion) a proposito, porque dos filas iguales
    # son un error de carga y no algo que la base deba resolver sola-- asi que
    # se comprueba antes.
    for modulo, clave, direccion, nota in SEMILLA:
        ya = db.execute(
            "SELECT 1 FROM destinatarios_regla "
            " WHERE modulo = ? AND clave = ? AND direccion = ?",
            (modulo, clave, direccion)).fetchone()
        if ya is None:
            db.execute(
                "INSERT INTO destinatarios_regla "
                "  (modulo, clave, direccion, activo, nota, creado_en) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (modulo, clave, direccion, nota,
                 datetime.now().isoformat(timespec="seconds")))


def para(modulo, destino=None):
    """Las direcciones que corresponden, y si hubo que caer al conjunto general.

    Devuelve `(direcciones, uso_el_general)`.

    LA BUSQUEDA ES POR SUBCADENA, como el legado. Sus ramas eran
    `strlen(strstr($destino,'ROSSELOT'))>0`, o sea "el destino CONTIENE la
    palabra". Los destinos son texto libre --438 distintos en doce meses-- asi
    que una igualdad exacta no engancharia casi nunca.

    Si nada calza, devuelve el conjunto `'*'` Y REGISTRA el destino. Ver el
    encabezado: registrar es lo que hace que un destino nuevo se pueda ver."""
    db = get_db()
    _asegurar_tablas(db)
    db.commit()

    especificas = []
    if destino:
        arriba = str(destino).upper()
        for f in consultar(
                "SELECT clave, direccion FROM destinatarios_regla "
                " WHERE modulo = ? AND activo = 1 AND clave <> ?",
                (modulo, TODOS)):
            if str(f["clave"]).upper() in arriba:
                especificas.append(f["direccion"])

    if especificas:
        return especificas, False

    if destino:
        _registrar_sin_regla(db, modulo, destino)

    general = [f["direccion"] for f in consultar(
        "SELECT direccion FROM destinatarios_regla "
        " WHERE modulo = ? AND clave = ? AND activo = 1", (modulo, TODOS))]
    return general, True


def _registrar_sin_regla(db, modulo, destino):
    ahora = datetime.now().isoformat(timespec="seconds")
    fila = db.execute(
        "SELECT id, veces FROM destinos_sin_regla_regla "
        " WHERE modulo = ? AND destino = ?", (modulo, destino)).fetchone()
    if fila is None:
        db.execute(
            "INSERT INTO destinos_sin_regla_regla "
            "  (modulo, destino, veces, primera_vez, ultima_vez) "
            "VALUES (?, ?, 1, ?, ?)", (modulo, destino, ahora, ahora))
    else:
        db.execute(
            "UPDATE destinos_sin_regla_regla SET veces = ?, ultima_vez = ? "
            " WHERE id = ?", ((fila["veces"] or 0) + 1, ahora, fila["id"]))
    db.commit()


def sin_regla(modulo=None, tope=50):
    """Para la reconciliacion: los destinos que nunca calzaron."""
    if modulo:
        return consultar(
            "SELECT * FROM destinos_sin_regla_regla WHERE modulo = ? "
            " ORDER BY veces DESC LIMIT ?", (modulo, tope))
    return consultar(
        "SELECT * FROM destinos_sin_regla_regla ORDER BY veces DESC LIMIT ?",
        (tope,))
