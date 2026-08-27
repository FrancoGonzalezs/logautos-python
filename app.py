"""
app.py -- punto de entrada de Logautos en Python.

Por ahora expone solo el modulo de Unidades (newstocks_cidef) sobre la
replica local en SQLite. El resto de los modulos del analisis (OT,
reparaciones, contenedores, check lists, bodega) se van colgando aca a
medida que se migran, cada uno como un Blueprint en modulos/.
"""

import os

from flask import Flask, redirect, url_for

from core import DB_PATH, cerrar_db, clave_de_sesion, instalar_guardas, mostrar, numero, pesos, vacio
from modulos.acceso import bp as bp_acceso, registrar_guardia, usuario_actual
from modulos.catalogos import bp as bp_catalogos
from modulos.check_list import bp as bp_check_list
from modulos.facturacion import bp as bp_facturacion
from modulos.inspeccion_despacho import bp as bp_inspeccion_despacho
from modulos.kpis import bp as bp_kpis
from modulos.movimientos import bp as bp_movimientos
from modulos.ot import bp as bp_ot
from modulos.revision_contenedor import bp as bp_revision_contenedor
from modulos.taller import bp as bp_taller
from modulos.unidades import bp as bp_unidades


def crear_app():
    app = Flask(__name__)
    app.teardown_appcontext(cerrar_db)

    # La cookie de sesion ahora dice QUIEN es el usuario y con que rol, asi
    # que la clave que la firma dejo de ser un detalle: con una clave conocida
    # cualquiera puede fabricarse una sesion de administrador sin saber
    # ninguna contrasena. Por eso ya no hay clave fija en el repo.
    #
    # Sale de SECRET_KEY, y si no esta se genera una al azar que se guarda en
    # DATA_DIR -- fuera del repo. Nunca vuelve a haber una clave conocida, que
    # es lo unico que importaba: da igual si el que arranca es gunicorn o
    # `python app.py`, ninguno de los dos firma con algo publicado.
    #
    # Se guarda en vez de regenerarse en cada arranque porque con la recarga
    # automatica cada archivo guardado cerraria la sesion, y ahora volver a
    # entrar cuesta escribir una contrasena de verdad. En el contenedor eso
    # depende de que DATA_DIR sea el volumen: si no lo es, cada redeploy
    # cambia la clave y obliga a que todos vuelvan a entrar -- molesto, no
    # inseguro. Setear SECRET_KEY lo evita y es lo recomendado.
    app.secret_key = clave_de_sesion()

    # Las guardas de `unidad_id`, TODAS y al arrancar. Antes se instalaban
    # perezosamente desde cada modulo de pantalla y en Railway habia 6 de 12.
    # Acá quedan puestas antes del primer request, y como son esquema protegen
    # tambien al hilo del sync, al del push y a los comandos de consola.
    instalar_guardas()

    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

    app.jinja_env.filters["mostrar"] = mostrar
    app.jinja_env.filters["pesos"] = pesos
    app.jinja_env.filters["numero"] = numero
    app.jinja_env.tests["vacio"] = vacio

    app.register_blueprint(bp_acceso)
    app.register_blueprint(bp_unidades)
    app.register_blueprint(bp_movimientos)
    app.register_blueprint(bp_check_list)
    app.register_blueprint(bp_revision_contenedor)
    app.register_blueprint(bp_inspeccion_despacho)
    app.register_blueprint(bp_taller)
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
        # `usuario` es el dict del logueado (o None), no un string suelto: las
        # pantallas necesitan el nombre para mostrarlo y el rol para decidir
        # que ofrecer, y pasarlos por separado los deja desincronizarse.
        return {"db_path": DB_PATH, "usuario": usuario_actual()}

    return app


def _hilo_sync():
    """El pull contra el legado, y despues el push, en segundo plano.

    Solo arranca si SYNC_INTERVALO_SEGUNDOS esta definida y es > 0, asi que en
    local no corre salvo que se lo pida explicitamente. Nunca levanta hacia
    afuera: si una vuelta falla, la marca de agua no avanza y la siguiente
    trae lo pendiente -- ese es justamente el motivo de que el sync sea un
    pull y no un push.

    El push tiene ademas su propia compuerta, PUSH_LEGADO_ACTIVO, apagada por
    defecto. Son dos llaves distintas a proposito: el pull lee y el push
    escribe, y no tienen por que encenderse el mismo dia."""
    intervalo = float(os.environ.get("SYNC_INTERVALO_SEGUNDOS") or 0)
    if intervalo <= 0:
        return

    import threading
    import time

    def vueltas():
        from modulos.push_legado import procesar_pendientes, push_activo
        from modulos.sync_legado import todo
        while True:
            time.sleep(intervalo)
            try:
                for r in todo():
                    print("[sync] {} recibidas={} creadas={} actualizadas={}"
                          "{}".format(
                              r["entidad"], r["recibidas"], r["creadas"],
                              r["actualizadas"],
                              " saltadas={}".format(r["saltadas"])
                              if r.get("saltadas") else ""), flush=True)
            except Exception as e:              # noqa: BLE001 -- ver docstring
                print("[sync] error: {}: {}".format(type(e).__name__, e), flush=True)

            # El push va DESPUES del pull de la misma vuelta, no antes. El
            # UPSERT saltea las filas con push_pendiente=1; si el push corriera
            # primero y limpiara el flag, el pull de esta misma vuelta podria
            # sobrescribir la fila sin verla.
            #
            # Detras de PUSH_LEGADO_ACTIVO, que arranca apagado: mientras lo
            # este, esto no manda un solo byte y la cola se vacia a mano.
            if not push_activo():
                continue
            try:
                r = procesar_pendientes()
                if r["intentados"]:
                    print("[push] intentados={intentados} ok={ok} "
                          "conflictos={conflicto} errores={error}".format(**r),
                          flush=True)
            except Exception as e:              # noqa: BLE001
                print("[push] error: {}: {}".format(type(e).__name__, e), flush=True)

    threading.Thread(target=vueltas, daemon=True).start()
    print("[sync] hilo activo, cada {:.0f}s".format(intervalo), flush=True)


app = crear_app()
_hilo_sync()


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        raise SystemExit(
            "no existe la replica {}\n"
            "corre primero: python scripts/importar_dump.py".format(DB_PATH))
    app.run(debug=True, port=5000)
