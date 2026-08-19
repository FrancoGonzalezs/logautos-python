"""
app.py -- punto de entrada de Logautos en Python.

Por ahora expone solo el modulo de Unidades (newstocks_cidef) sobre la
replica local en SQLite. El resto de los modulos del analisis (OT,
reparaciones, contenedores, check lists, bodega) se van colgando aca a
medida que se migran, cada uno como un Blueprint en modulos/.
"""

import os

from flask import Flask, redirect, session, url_for

from core import DB_PATH, cerrar_db, mostrar, numero, pesos, vacio
from modulos.acceso import bp as bp_acceso, registrar_guardia
from modulos.catalogos import bp as bp_catalogos
from modulos.facturacion import bp as bp_facturacion
from modulos.kpis import bp as bp_kpis
from modulos.movimientos import bp as bp_movimientos
from modulos.ot import bp as bp_ot
from modulos.unidades import bp as bp_unidades


def crear_app():
    app = Flask(__name__)
    app.teardown_appcontext(cerrar_db)

    # La sesion del login sale de SECRET_KEY del entorno. Si no esta, se usa
    # una clave FIJA de desarrollo en vez de generar una al azar: con
    # os.urandom() cada reinicio del servidor cerraba la sesion, y con
    # recarga automatica eso pasa cada vez que se guarda un archivo.
    #
    # La clave fija no es un descuido pero tampoco es segura: esta en el repo,
    # asi que cualquiera puede firmar una cookie de sesion. Hoy da lo mismo
    # porque el login es una maqueta que acepta cualquier usuario
    # (modulos/acceso.py) y la app es de solo lectura. EN PRODUCCION HAY QUE
    # SETEAR SECRET_KEY -- y cuando el login valide de verdad, esta rama
    # deberia pasar a fallar en vez de dar una clave por defecto.
    app.secret_key = os.environ.get("SECRET_KEY") or "regla-desarrollo-no-usar-en-produccion"

    app.jinja_env.filters["mostrar"] = mostrar
    app.jinja_env.filters["pesos"] = pesos
    app.jinja_env.filters["numero"] = numero
    app.jinja_env.tests["vacio"] = vacio

    app.register_blueprint(bp_acceso)
    app.register_blueprint(bp_unidades)
    app.register_blueprint(bp_movimientos)
    app.register_blueprint(bp_ot)
    app.register_blueprint(bp_catalogos)
    app.register_blueprint(bp_facturacion)
    app.register_blueprint(bp_kpis)

    registrar_guardia(app)

    @app.route("/")
    def inicio():
        return redirect(url_for("unidades.listado"))

    @app.context_processor
    def contexto():
        return {"db_path": DB_PATH, "usuario": session.get("usuario")}

    return app


app = crear_app()


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        raise SystemExit(
            "no existe la replica {}\n"
            "corre primero: python scripts/importar_dump.py".format(DB_PATH))
    app.run(debug=True, port=5000)
