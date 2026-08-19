"""
modulos/acceso.py -- la pantalla de entrada, ahora con autenticacion real
contra `tbl_users`.

Esto ya NO es una maqueta: valida email y contrasena contra las 144 filas de
`tbl_users` importadas del dump, con el mismo criterio que
`application/models/Login_model.php` del sistema viejo.

Compatibilidad de contrasenas
-----------------------------
El PHP guarda con `password_hash($clave, PASSWORD_DEFAULT)` y verifica con
`password_verify()` (helpers/cias_helper.php). PASSWORD_DEFAULT es bcrypt, y
las 144 filas del dump lo confirman: todas empiezan con `$2y$` y miden 60
caracteres, sin una sola vacia.

El `bcrypt` de Python lee ese formato DIRECTO -- `$2y$` es la variante que
escribe PHP y `checkpw` la acepta igual que `$2b$`. O sea que no hay que
resetear ni una contrasena: los usuarios entran a REGLA con la misma que usan
hoy en Logautos.PHP, y una contrasena cambiada alla sigue sirviendo aca.

Lo que se replica del Login_model.php, y lo que no
--------------------------------------------------
  - Se filtra `isDeleted = 0`, igual que el original. Son 18 usuarios de 144
    los que quedan afuera.

  - NO se mira `bloqueo`. La columna existe y 15 usuarios vivos la tienen en
    1, pero `Login_model.php` no la consulta: hoy esa gente entra al sistema
    viejo. Agregar el filtro seria inventar una regla que el sistema no
    aplica y dejar afuera de golpe a cuentas de patio y totems que pueden
    estar en uso. Queda anotado por si el dueno lo quiere cambiar -- es una
    linea, pero cambia quien puede trabajar manana.

  - El email se compara SIN distinguir mayusculas (COLLATE NOCASE), y eso no
    es una desviacion sino lo contrario: MySQL usa una collation `_ci`, asi
    que alla la comparacion ya es insensible. En SQLite el `=` sobre TEXT SI
    distingue, asi que sin NOCASE nueve usuarios vivos no podrian entrar --
    entre ellos 'CARLOS.MORALES@CARFLEX.CL', que esta guardado entero en
    mayusculas. Verificado que con NOCASE los emails vivos siguen siendo
    unicos, asi que la comparacion no vuelve ambiguo a nadie.

  - No se escribe `tbl_last_login`. El PHP registra ahi cada entrada; aca
    todavia no, porque esa tabla no esta importada y el historial de accesos
    es un modulo aparte.
"""

import bcrypt
from flask import (Blueprint, redirect, render_template, request, session,
                   url_for)

from core import consultar

bp = Blueprint("acceso", __name__)

# Rutas que se pueden ver sin haber entrado. `static` va incluido porque si no
# el propio login se veria sin estilos.
LIBRES = {"acceso.login", "acceso.salir", "static"}


def buscar_usuario(email):
    """El usuario vivo con ese email, con el texto de su rol ya resuelto.

    El JOIN contra `tbl_roles` es el mismo del PHP y no un LEFT JOIN a
    proposito: alla un usuario cuyo roleId no existiera en la tabla de roles
    directamente no podria entrar. Aca no cambia nada en la practica --se
    comprobo que los 144 usuarios tienen un roleId de los 8 que existen--,
    pero copiarlo mantiene el comportamiento si manana aparece uno huerfano.

    El COLLATE NOCASE va sobre la comparacion y no sobre la columna, para no
    depender de como quedo declarada la tabla al importarla."""
    if not email:
        return None
    return consultar(
        "SELECT u.userId, u.password, u.name, u.email, u.roleId, u.grupo, "
        "       u.cliente, u.bloqueo, r.role "
        "FROM tbl_users u "
        "JOIN tbl_roles r ON r.roleId = u.roleId "
        "WHERE u.email = ? COLLATE NOCASE AND u.isDeleted = 0",
        (email.strip(),), una=True)


def clave_valida(clave, hash_guardado):
    """`password_verify()` del PHP, en Python.

    Se envuelve en try/except porque `checkpw` revienta si el hash no tiene
    formato bcrypt. Hoy las 144 filas lo tienen, pero un hash corrupto o un
    campo vaciado a mano no deberia tumbar la pantalla de login: es un intento
    fallido, no un error 500."""
    if not clave or not hash_guardado:
        return False
    try:
        return bcrypt.checkpw(clave.encode("utf-8"),
                              str(hash_guardado).encode("utf-8"))
    except (ValueError, TypeError):
        return False


def autenticar(email, clave):
    """El usuario si las credenciales son correctas, None si no.

    Se busca primero y se verifica despues, igual que el PHP. No se corta
    antes cuando el usuario no existe: se corre igual una verificacion contra
    un hash de descarte, para que un email inexistente y uno con contrasena
    equivocada tarden lo mismo. Sin eso, la diferencia de tiempo dice cuales
    de los emails probados existen."""
    usuario = buscar_usuario(email)
    if usuario is None:
        clave_valida(clave, _HASH_DE_DESCARTE)
        return None
    if not clave_valida(clave, usuario["password"]):
        return None
    return usuario


# Hash valido de una contrasena que no es de nadie. Solo existe para gastar el
# mismo tiempo de bcrypt cuando el email no esta en la base.
_HASH_DE_DESCARTE = bcrypt.hashpw(b"-", bcrypt.gensalt(rounds=10)).decode()


def abrir_sesion(usuario):
    """Deja en la sesion los mismos campos que arma `Login.php`.

    OJO CON LOS NOMBRES: el PHP guarda el id del rol en `role` y el texto en
    `roleText` -- no hay ninguna clave `roleId` en su sesion. Se copian tal
    cual, con `grupo` incluido, para que la logica que dependa del rol se
    pueda portar sin traducir nombres a mano. `isLoggedIn` es la marca que el
    PHP usa para saber si hay sesion, y se conserva por lo mismo.

    `lastLogin` no se guarda: sale de `tbl_last_login`, que no esta
    importada."""
    session.clear()
    session["userId"] = usuario["userId"]
    session["role"] = usuario["roleId"]
    session["roleText"] = usuario["role"]
    session["name"] = (usuario["name"] or "").strip()
    session["email"] = usuario["email"]
    session["grupo"] = usuario["grupo"]
    session["cliente"] = usuario["cliente"]
    session["isLoggedIn"] = True


def usuario_actual():
    """Los datos del que esta logueado, o None. Lo usan las pantallas que
    necesitan saber quien firma lo que se guarda."""
    if not session.get("isLoggedIn"):
        return None
    return {
        "userId": session.get("userId"),
        "role": session.get("role"),
        "roleText": session.get("roleText"),
        "name": session.get("name"),
        "email": session.get("email"),
        "grupo": session.get("grupo"),
        "cliente": session.get("cliente"),
    }


def nombre_actual():
    """El nombre del usuario logueado. Es lo que firma los movimientos y los
    check lists, asi que sale SIEMPRE de la sesion y nunca de un campo del
    formulario: un input, aunque este de solo lectura, viaja en el POST y se
    puede editar antes de mandarlo."""
    return session.get("name") or ""


def id_actual():
    """El userId del logueado, como texto.

    Es lo que se guarda en la columna `usuario` de las tablas de REGLA. Va el
    id y no el nombre porque el nombre ya viaja en `responsable`/`encargado`, y
    porque un id no cambia si manana alguien corrige como esta escrito su
    nombre -- que es justo lo que el push al sistema viejo va a necesitar para
    saber de quien es cada fila."""
    uid = session.get("userId")
    return None if uid is None else str(uid)


def registrar_guardia(app):
    """Manda al login cualquier request sin sesion."""

    @app.before_request
    def _exigir_sesion():
        if request.endpoint in LIBRES or session.get("isLoggedIn"):
            return None
        # Una sesion vieja del login de maqueta tiene `usuario` pero no
        # `isLoggedIn`, asi que cae aca y se limpia. Es lo correcto: esa
        # cookie decia quien era el usuario sin que nadie hubiera validado
        # una contrasena.
        session.clear()
        return redirect(url_for("acceso.login", siguiente=request.full_path))


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        usuario = autenticar(email, request.form.get("clave", ""))
        if usuario is not None:
            abrir_sesion(usuario)
            siguiente = request.form.get("siguiente") or ""
            # Solo se acepta un destino interno: un 'siguiente' que venga con
            # host propio seria una redireccion abierta.
            if siguiente.startswith("/") and not siguiente.startswith("//"):
                return redirect(siguiente)
            return redirect(url_for("unidades.listado"))
        # Un solo mensaje para los dos casos -- email que no existe y
        # contrasena equivocada --, igual que el 'Email or password mismatch'
        # del PHP: distinguirlos le confirma a cualquiera que pruebe un email
        # si esa cuenta existe.
        error = "El email o la contraseña no coinciden."

    return render_template(
        "login.html", error=error, email=email,
        siguiente=request.args.get("siguiente", ""))


@bp.route("/salir")
def salir():
    session.clear()
    return redirect(url_for("acceso.login"))
