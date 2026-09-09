#!/usr/bin/env python3
"""
scripts/probar_retencion.py -- las unidades que Regla PHP no deja mover.

    python scripts/probar_retencion.py

QUE SE PRUEBA, Y POR QUE CADA COSA

  1. LOS CUATRO MOTIVOS, uno por uno. Copiados de produccion/Pedido.php:8738.
     La calle `Cmp3` va aparte del estado `FR - MECANICA` y NO es redundante:
     una unidad puede estar en esa calle con otro estado, y en la replica hoy
     hay una asi.

  2. UNA UNIDAD SANA NO SE FRENA. Una guarda que frena de mas es peor que
     ninguna, porque el patio deja de creerle.

  3. EL PERMISO ES POR PERSONA Y NO POR ROL. `rparra@logautos.cl` es roleId 6
     ("Patio"), el mismo de otros 36 usuarios activos: si el permiso saliera
     del rol, los 36 saltearian la guarda y estaria prendida sin frenar a
     nadie. Esta prueba lo afirma con los dos casos.

  4. `registrar()` CORTA AUNQUE LA PANTALLA SE OLVIDE. Es la ultima linea de
     defensa: se llama derecho, sin pasar por ninguna pantalla, y tiene que
     levantar `UnidadRetenida` y NO escribir.

  5. Y EL INTENTO QUEDA REGISTRADO. Un intento frenado es dato: si la guarda
     se dispara cincuenta veces por dia, o el patio no entiende el cartel o
     hay un flujo real que nadie modelo.
"""

import os
import shutil
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

# La base publica de las fotos ahora se exige AL ARRANCAR (ver
# `fotos_publicas.base_publica_configurada`). Una prueba tiene que declarar su
# entorno igual que produccion: `regla.example` no resuelve a ningun lado, que
# es exactamente lo que se quiere de una prueba que no debe publicar nada.
os.environ.setdefault("PUBLIC_BASE_URL", "https://regla.example")

from temporales import carpeta_de_prueba              # noqa: E402

os.environ.setdefault("SECRET_KEY", "prueba")

FALLOS = []


def afirmar(condicion, que, detalle=""):
    print("   {}  {}".format("ok  " if condicion else "FALLA", que))
    if detalle:
        print("          {}".format(detalle))
    if not condicion:
        FALLOS.append(que)


# Los cuatro motivos, y una unidad sana. El id es el de la replica.
CASOS = (
    (990001, "NO DISPONIBLE",        "IT",   "estado NO DISPONIBLE"),
    (990002, "FR - MECANICA",        "Cmp3", "estado FR - MECANICA"),
    (990003, "IT FALTA SEGUNDA PDI", "IT",   "estado IT FALTA SEGUNDA PDI"),
    # La calle sola, con un estado que NO retiene: es el caso que se pierde si
    # alguien "simplifica" la guarda dejando solo los estados.
    (990004, "INGRESO A TALLER",     "Cmp3", "calle Cmp3"),
    (990005, "INGRESO A TALLER",     "IT",   None),
)


def main():
    origen = os.path.join(RAIZ, "local.db")
    if not os.path.exists(origen):
        print("no hay local.db")
        return 1
    tmp = carpeta_de_prueba("regla_reten_")
    shutil.copy(origen, os.path.join(tmp, "prueba.db"))
    os.environ["DB_PATH"] = os.path.join(tmp, "prueba.db")
    os.environ["DATA_DIR"] = tmp
    os.environ["PUSH_LEGADO_ACTIVO"] = "0"

    from app import crear_app
    from core import conectar_db
    app = crear_app()
    app.config["TESTING"] = True

    db = conectar_db()
    for uid, estado, calle, _ in CASOS:
        db.execute(
            "INSERT OR REPLACE INTO newstocks_cidef "
            " (id, vin, clientecompleto, marca, modelo, color, calle, "
            "  despachado, patio, updated_at) "
            " VALUES (?,?,'PRUEBA','GREAT WALL','POER','BLANCO',?,?, "
            "         'PATIO 2','2026-09-01 10:00:00')",
            (uid, "VINRETEN{:09d}".format(uid), calle, estado))
    db.commit()
    db.close()

    from modulos import movimientos as M
    from modulos import permisos as P

    # -- 1 y 2: los cuatro motivos, y la sana -------------------------------
    print("\n1. LOS MOTIVOS, UNO POR UNO")
    with app.test_request_context():
        from core import consultar
        for uid, estado, calle, esperado in CASOS:
            u = consultar("SELECT * FROM newstocks_cidef WHERE id = ?",
                          (uid,), una=True)
            visto = M.motivo_retencion(u)
            if esperado is None:
                afirmar(visto is None,
                        "{} / calle {} NO se retiene".format(estado, calle),
                        "vio {!r}".format(visto))
            else:
                afirmar(visto == esperado,
                        "{} / calle {} -> {!r}".format(estado, calle, esperado),
                        "vio {!r}".format(visto))

    # -- 3: el permiso es por persona ---------------------------------------
    print("\n2. EL PERMISO ES POR PERSONA, NO POR ROL")
    with app.test_request_context():
        afirmar(P.tiene(P.MOVER_RETENIDA, "fgonzalez@logautos.cl"),
                "fgonzalez lo tiene (esta cableado en Regla PHP)")
        afirmar(P.tiene(P.MOVER_RETENIDA, "RParra@Logautos.CL"),
                "rparra tambien, y sin importar mayusculas")
        afirmar(not P.tiene(P.MOVER_RETENIDA, "otro@logautos.cl"),
                "otro movilizador NO, aunque tenga el mismo rol que rparra")
        afirmar(sorted(P.quienes(P.MOVER_RETENIDA)) ==
                ["fgonzalez@logautos.cl", "rparra@logautos.cl"],
                "y la pantalla puede decir QUIENES son")

    # El dato que obliga a que sea por persona, afirmado contra la replica.
    with app.test_request_context():
        from core import consultar
        rol_rparra = consultar(
            "SELECT roleId FROM tbl_users WHERE email = ? "
            " AND COALESCE(isDeleted,0)=0 LIMIT 1",
            ("rparra@logautos.cl",), una=True)
        if rol_rparra:
            cuantos = consultar(
                "SELECT COUNT(*) n FROM tbl_users WHERE roleId = ? "
                " AND COALESCE(isDeleted,0)=0", (rol_rparra["roleId"],),
                una=True)["n"]
            afirmar(cuantos > 5,
                    "el rol de rparra lo comparten muchos: por eso NO va por rol",
                    "roleId {} lo tienen {} usuarios activos".format(
                        rol_rparra["roleId"], cuantos))

    # -- 4: registrar() corta ------------------------------------------------
    print("\n3. registrar() CORTA AUNQUE LA PANTALLA SE OLVIDE")
    c = app.test_client()
    with c.session_transaction() as s:
        s["isLoggedIn"] = True
        s["name"] = "Movilizador"
        s["userId"] = 1
        s["role"] = 6
        s["email"] = "otro@logautos.cl"

    with app.test_request_context():
        from flask import session
        # `isLoggedIn` es la marca que `usuario_actual()` mira primero, y sin
        # ella devuelve None -- con lo cual `tiene()` da False y la guarda
        # FRENA. Que falle cerrada es lo correcto y por eso se arma la sesion
        # completa aca en vez de aflojar la comprobacion.
        session["isLoggedIn"] = True
        session["email"] = "otro@logautos.cl"
        session["userId"] = 1
        session["role"] = 6
        session["name"] = "Movilizador"
        from core import consultar
        u = consultar("SELECT * FROM newstocks_cidef WHERE id = 990002",
                      (), una=True)
        antes = consultar("SELECT COUNT(*) n FROM movimientos_regla",
                          (), una=True)["n"]
        try:
            M.registrar(u, {"paso": "ingreso_taller", "recomendado": None,
                            "es_desvio": False, "estado_desde": "FR - MECANICA",
                            "estado_hacia": "ZONA DE DESPACHO",
                            "empuja_movimiento": False,
                            "motivo": None, "motivo_detalle": None,
                            "resultado_pdi": None, "guia_ingreso": None,
                            "fecha": "2026-09-08", "responsable": "Movilizador"})
            afirmar(False, "levanta UnidadRetenida")
        except M.UnidadRetenida as e:
            afirmar(True, "levanta UnidadRetenida", "motivo: {}".format(e))
        despues = consultar("SELECT COUNT(*) n FROM movimientos_regla",
                            (), una=True)["n"]
        afirmar(antes == despues, "y NO escribio el movimiento",
                "{} -> {}".format(antes, despues))

        # -- 5: el intento queda registrado ---------------------------------
        print("\n4. EL INTENTO QUEDA REGISTRADO")
        filas = P.intentos()
        afirmar(len(filas) == 1, "hay una fila", "{}".format(len(filas)))
        if filas:
            f = filas[0]
            afirmar(f["motivo"] == "estado FR - MECANICA",
                    "con el motivo", f["motivo"])
            afirmar(f["email"] == "otro@logautos.cl",
                    "y con QUIEN lo intento", f["email"])

        # -- y quien SI puede, pasa -----------------------------------------
        print("\n5. Y QUIEN TIENE EL PERMISO, PASA")
        session["email"] = "fgonzalez@logautos.cl"
        antes = consultar("SELECT COUNT(*) n FROM movimientos_regla",
                          (), una=True)["n"]
        try:
            M.registrar(u, {"paso": "ingreso_taller", "recomendado": None,
                            "es_desvio": False, "estado_desde": "FR - MECANICA",
                            "estado_hacia": "ZONA DE DESPACHO",
                            "empuja_movimiento": False,
                            "motivo": None, "motivo_detalle": None,
                            "resultado_pdi": None, "guia_ingreso": None,
                            "fecha": "2026-09-08", "responsable": "Franco"})
            paso = True
        except M.UnidadRetenida:
            paso = False
        afirmar(paso, "fgonzalez mueve la unidad retenida")
        despues = consultar("SELECT COUNT(*) n FROM movimientos_regla",
                            (), una=True)["n"]
        afirmar(despues == antes + 1, "y el movimiento SI se escribio",
                "{} -> {}".format(antes, despues))

    print("\n" + "=" * 62)
    if FALLOS:
        print("FALLARON {}:".format(len(FALLOS)))
        for f in FALLOS:
            print("   - {}".format(f))
        return 1
    print("la retencion frena a quien tiene que frenar, y a nadie mas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
