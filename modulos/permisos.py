"""
modulos/permisos.py -- capacidades con nombre, otorgadas persona por persona.

POR QUE NO ALCANZA EL ROL, Y ESTO NO ES UNA PREFERENCIA DE DISENO
=================================================================

Regla PHP tiene la excepcion cableada por correo:

    produccion/Pedido.php:8738
    ... && $_SESSION['email'] != "fgonzalez@logautos.cl"
        && $_SESSION['email'] != "rparra@logautos.cl")

La decision de Franco fue sacar eso del codigo y ponerlo como permiso. La
tentacion es colgarlo del rol que ya existe en la sesion, y ESO ROMPERIA LA
GUARDA. Medido sobre `tbl_users` de la replica:

    rparra@logautos.cl   userId 241   ACTIVO   roleId 6 = "Patio"
    fgonzalez@logautos.cl userId 666  ACTIVO   roleId 1 = "System Administrator"

El rol 6 lo tienen **36 usuarios activos**: son los movilizadores de patio, o
sea justo la gente a la que la guarda le tiene que decir que no. Un permiso
derivado del rol se lo daria a los 36 y la guarda quedaria prendida sin frenar
a nadie -- que es peor que no tenerla, porque se ve encendida.

Asi que el permiso se otorga POR PERSONA. El rol sigue existiendo y sirve para
lo que sirve; esto es otra cosa y por eso tiene su propia tabla.

POR QUE UNA TABLA Y NO UNA CONSTANTE
====================================

Mismo argumento que `destinatarios.py`: quien puede destrabar una unidad
retenida cambia cuando alguien entra o sale de un puesto, y no puede depender
de un despliegue. La semilla copia lo que Regla PHP tiene hoy; de ahi en mas se
edita la tabla.

LO QUE ESTA TABLA NO ES
=======================

No es un sistema de permisos general, y no conviene que se vuelva uno de a
poco. Hoy tiene UNA capacidad. Cada capacidad nueva tiene que poder explicarse
sola: que bloquea, quien la tiene, y que ve el que no la tiene.
"""

from datetime import datetime

from core import consultar, get_db
from modulos.acceso import usuario_actual

# ---------------------------------------------------------------------------
# Las capacidades
# ---------------------------------------------------------------------------

# Mover una unidad que Regla PHP retiene por estado o por calle. Ver
# `movimientos.motivo_retencion`.
MOVER_RETENIDA = "mover_unidad_retenida"

# La semilla es exactamente lo que Regla PHP tiene cableado en
# produccion/Pedido.php:8738. Ni una direccion mas: agregar a alguien "porque
# seguro tambien puede" es cambiar el comportamiento durante una migracion.
SEMILLA = (
    (MOVER_RETENIDA, "fgonzalez@logautos.cl",
     "Cableado en produccion/Pedido.php:8738 (guarda de actulocproccess)."),
    (MOVER_RETENIDA, "rparra@logautos.cl",
     "Cableado en produccion/Pedido.php:8738. Rodrigo Parra es roleId 6 "
     "(Patio), el mismo de otros 36 usuarios: por eso esto va por persona y "
     "no por rol."),
)


def _asegurar_tablas(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS permisos_regla (
          id INTEGER PRIMARY KEY,
          permiso TEXT NOT NULL,
          email TEXT NOT NULL,
          activo INTEGER NOT NULL DEFAULT 1,
          nota TEXT,
          otorgado_por TEXT,
          otorgado_en TEXT
        )""")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_permisos_regla "
               "ON permisos_regla (permiso, email)")

    # LOS INTENTOS BLOQUEADOS SON DATO, NO RUIDO.
    #
    # Si la guarda se dispara cincuenta veces por dia, o el patio no entiende
    # que la unidad esta retenida o hay un flujo real que nadie modelo. Las dos
    # cosas hay que verlas, y un log no se mira. La reconciliacion diaria si.
    db.execute("""
        CREATE TABLE IF NOT EXISTS intentos_bloqueados_regla (
          id INTEGER PRIMARY KEY,
          permiso TEXT NOT NULL,
          unidad_id INTEGER,
          vin TEXT,
          motivo TEXT,
          email TEXT,
          usuario TEXT,
          ocurrido_en TEXT
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_intentos_bloqueados "
               "ON intentos_bloqueados_regla (ocurrido_en)")

    for permiso, email, nota in SEMILLA:
        ya = db.execute("SELECT 1 FROM permisos_regla "
                        " WHERE permiso = ? AND email = ?",
                        (permiso, email)).fetchone()
        if ya is None:
            db.execute(
                "INSERT INTO permisos_regla "
                "  (permiso, email, activo, nota, otorgado_por, otorgado_en) "
                "VALUES (?, ?, 1, ?, 'semilla', ?)",
                (permiso, email, nota,
                 datetime.now().isoformat(timespec="seconds")))


def _correo(email=None):
    """El correo con el que decidir. Sale de la sesion si no lo pasan."""
    if email is not None:
        return (email or "").strip().lower()
    u = usuario_actual() or {}
    return (u.get("email") or "").strip().lower()


def tiene(permiso, email=None):
    """True si esa persona tiene la capacidad.

    La comparacion es en minusculas de los dos lados: Regla PHP compara el
    correo de sesion con `!=` contra un literal, o sea que alli una mayuscula
    distinta le sacaria el permiso a quien lo tiene. No se replica ESO: es un
    borde que nunca se probo, no un comportamiento que alguien eligio."""
    quien = _correo(email)
    if not quien:
        return False
    db = get_db()
    _asegurar_tablas(db)
    db.commit()
    fila = consultar(
        "SELECT 1 FROM permisos_regla "
        " WHERE permiso = ? AND LOWER(email) = ? AND activo = 1",
        (permiso, quien), una=True)
    return fila is not None


def quienes(permiso):
    """Las personas que tienen la capacidad. La pantalla la usa para decir a
    quien pedirle: un cartel que dice 'no podes' y no dice quien si, manda a
    preguntar por los pasillos."""
    db = get_db()
    _asegurar_tablas(db)
    db.commit()
    return [f["email"] for f in consultar(
        "SELECT email FROM permisos_regla "
        " WHERE permiso = ? AND activo = 1 ORDER BY email", (permiso,))]


def registrar_intento(db, permiso, unidad, motivo, email=None):
    """Deja constancia de un intento que la guarda freno.

    Recibe `db` para caer en la transaccion de quien llama, igual que
    `avisos.encolar`."""
    _asegurar_tablas(db)
    u = usuario_actual() or {}
    db.execute(
        "INSERT INTO intentos_bloqueados_regla "
        "  (permiso, unidad_id, vin, motivo, email, usuario, ocurrido_en) "
        "VALUES (?,?,?,?,?,?,?)",
        (permiso,
         unidad["id"] if unidad is not None else None,
         unidad["vin"] if unidad is not None else None,
         motivo, _correo(email), u.get("name"),
         datetime.now().isoformat(timespec="seconds")))


def intentos(desde=None, tope=50):
    """Para la reconciliacion."""
    db = get_db()
    _asegurar_tablas(db)
    db.commit()
    if desde:
        return consultar(
            "SELECT * FROM intentos_bloqueados_regla "
            " WHERE ocurrido_en >= ? ORDER BY id DESC LIMIT ?", (desde, tope))
    return consultar(
        "SELECT * FROM intentos_bloqueados_regla ORDER BY id DESC LIMIT ?",
        (tope,))
