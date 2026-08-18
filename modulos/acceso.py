"""
modulos/acceso.py -- la pantalla de entrada.

OJO: ESTO NO ES SEGURIDAD. Es una maqueta funcional de la pantalla de login
del sistema real, para que la app tenga puerta de entrada mientras se define
el modelo de usuarios. Acepta cualquier credencial y solo deja una marca en la
sesion; no valida contra nada, no hashea nada y no distingue roles.

Cuando empiece el trabajo de sync y escritura hay que reemplazar
`_credencial_valida()` por una validacion de verdad contra `tbl_users` (que ya
existe en el dump, con 16 columnas, pero todavia no esta importada) y poner
una SECRET_KEY estable por configuracion. Mientras la app sea de solo lectura
sobre una replica local, el riesgo de dejarlo asi es que alguien vea datos que
ya estan en su propio disco.
"""

import os

from flask import (Blueprint, redirect, render_template, request, session,
                   url_for)

bp = Blueprint("acceso", __name__)

# Rutas que se pueden ver sin haber entrado. `static` va incluido porque si no
# el propio login se veria sin estilos.
LIBRES = {"acceso.login", "acceso.salir", "static"}

USUARIO_DEMO = "demo"


def _credencial_valida(usuario, clave):
    """Maqueta: alcanza con que el usuario no venga vacio.

    Se deja como funcion aparte, y no inline en la vista, justamente para que
    el dia que haya usuarios de verdad se cambie una sola cosa."""
    return bool((usuario or "").strip())


def registrar_guardia(app):
    """Manda al login cualquier request sin sesion."""

    @app.before_request
    def _exigir_sesion():
        if request.endpoint in LIBRES or session.get("usuario"):
            return None
        return redirect(url_for("acceso.login", siguiente=request.full_path))


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        clave = request.form.get("clave", "")
        if _credencial_valida(usuario, clave):
            session["usuario"] = usuario
            siguiente = request.form.get("siguiente") or ""
            # Solo se acepta un destino interno: un 'siguiente' que venga con
            # host propio seria una redireccion abierta.
            if siguiente.startswith("/") and not siguiente.startswith("//"):
                return redirect(siguiente)
            return redirect(url_for("unidades.listado"))
        error = "Escribe un usuario para entrar."

    return render_template(
        "login.html", error=error,
        siguiente=request.args.get("siguiente", ""),
        usuario_demo=USUARIO_DEMO)


@bp.route("/salir")
def salir():
    session.pop("usuario", None)
    return redirect(url_for("acceso.login"))
