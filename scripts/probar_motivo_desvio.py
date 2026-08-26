#!/usr/bin/env python3
"""
scripts/probar_motivo_desvio.py -- que PDI e IT exijan y conserven el motivo
del desvio, con el mismo criterio que el endpoint generico.

Habia DOS agujeros distintos, y los dos terminaban en lo mismo -- un retrabajo
indistinguible de un avance en el historial:

  1. Viniendo desde Movimientos, `registrar_movimiento` EXIGE el motivo, el
     operario lo elige, y al redirigir al formulario de PDI/IT viaja en la
     query string. Estas pantallas leian solo `request.form`, asi que el
     motivo se perdia entero entre una pantalla y la otra.
  2. Entrando por la puerta directa del menu, nunca se pasa por
     `registrar_movimiento` y no habia quien lo exigiera.

El caso reachable de verdad es el IT desde CONTROL DE CALIDAD DESPACHO: esa
transicion esta en DESVIOS_CON_MOTIVO con la lista 'cc_taller', y es donde se
mide la calidad de la preparacion.

    python scripts/probar_motivo_desvio.py
"""

import importlib.util
import os
import shutil
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ["SECRET_KEY"] = "prueba"

# Se reusan el armado de base y el cliente de prueba de la otra suite: son la
# misma necesidad y dos copias se desincronizarian a la primera tabla nueva.
_spec = importlib.util.spec_from_file_location(
    "_pf", os.path.join(RAIZ, "scripts", "probar_ficha_estados.py"))
_pf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pf)
base_con, texto_visible = _pf.base_con, _pf.texto_visible

fallos = []


def paso(titulo):
    print("\n--- {} ---".format(titulo))


def afirmar(condicion, descripcion, detalle=""):
    if condicion:
        print("   ok  {}".format(descripcion))
    else:
        print("  FALLA {}{}".format(descripcion,
                                    ("  <- " + str(detalle)) if detalle else ""))
        fallos.append(descripcion)


def cliente(ruta):
    """Una app apuntando a `ruta`, con sesion abierta."""
    import importlib
    os.environ["DB_PATH"] = ruta
    import core
    importlib.reload(core)
    for nombre in list(sys.modules):
        if nombre.startswith("modulos.") or nombre == "app":
            del sys.modules[nombre]
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["isLoggedIn"] = True
        s["userId"] = 0
        s["name"] = "Prueba"
        s["email"] = "p@p.cl"
        s["roleId"] = 1
    return c


def movimientos(ruta):
    import sqlite3
    db = sqlite3.connect(ruta)
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute(
            "SELECT paso, estado_desde, estado_hacia, es_desvio, motivo, "
            "motivo_detalle FROM movimientos_regla ORDER BY id")]
    finally:
        db.close()


CAMPOS_IT = {"estado_it": "OK", "observacion_it": ""}
CC = "CONTROL DE CALIDAD DESPACHO"


def main():
    tmp = tempfile.mkdtemp(prefix="probar_motivo_")
    ruta = os.path.join(tmp, "prueba.db")

    # ------------------------------------------------------------------ 1
    paso("1. IT desde control de calidad SIN motivo: se corta")
    base_con(ruta, CC, [])
    c = cliente(ruta)
    r = c.post("/movimientos/90001/it", data=dict(CAMPOS_IT))
    afirmar(r.status_code == 400, "responde 400 y no redirige", r.status_code)
    visible = texto_visible(r.get_data(as_text=True))
    afirmar("hay que decir por" in visible, "explica por que corta",
            visible[visible.find("hay que decir") - 60:][:150].strip())
    afirmar("retrabajo" in visible, "y para que sirve el dato")
    afirmar(movimientos(ruta) == [], "NO registro el movimiento", movimientos(ruta))

    # -- y la pantalla ofrece la lista correcta para poder cumplir ---------
    afirmar("Terminación rechazada (pintura, pulido, detalle)" in
            r.get_data(as_text=True),
            "muestra la lista 'cc_taller', no una generica")

    # ------------------------------------------------------------------ 2
    paso("2. IT desde control de calidad CON motivo: guarda y conserva")
    base_con(ruta, CC, [])
    c = cliente(ruta)
    r = c.post("/movimientos/90001/it", data=dict(
        CAMPOS_IT, motivo="Daño detectado en el control de calidad",
        motivo_detalle="rayon en puerta trasera"))
    afirmar(r.status_code in (302, 303), "guarda y redirige", r.status_code)
    ms = movimientos(ruta)
    afirmar(len(ms) == 1, "registro el movimiento", len(ms))
    if ms:
        afirmar(ms[0]["motivo"] == "Daño detectado en el control de calidad",
                "guardo el motivo", ms[0]["motivo"])
        afirmar(ms[0]["motivo_detalle"] == "rayon en puerta trasera",
                "y el detalle", ms[0]["motivo_detalle"])
        afirmar(ms[0]["estado_desde"] == CC and
                ms[0]["estado_hacia"] == "INGRESO A TALLER",
                "con el arco correcto", ms[0])

    # ------------------------------------------------------------------ 3
    paso("3. el motivo que viene de Movimientos ya no se pierde")
    base_con(ruta, CC, [])
    c = cliente(ruta)
    # Asi redirige `registrar_movimiento`: el motivo en la query string.
    r = c.get("/movimientos/90001/it?motivo=Limpieza+insuficiente&motivo_detalle=tapiz")
    afirmar(r.status_code == 200, "el formulario abre", r.status_code)
    html = r.get_data(as_text=True)
    afirmar("Limpieza insuficiente" in html,
            "el formulario recibe el motivo elegido en Movimientos")
    afirmar('name="motivo"' in html,
            "y lo lleva en un campo, para que viaje en el POST")

    # El POST que haria el navegador con ese formulario.
    r = c.post("/movimientos/90001/it", data=dict(
        CAMPOS_IT, motivo="Limpieza insuficiente", motivo_detalle="tapiz"))
    ms = movimientos(ruta)
    afirmar(r.status_code in (302, 303), "guarda", r.status_code)
    afirmar(ms and ms[0]["motivo"] == "Limpieza insuficiente",
            "y el motivo llego hasta la base", ms[0]["motivo"] if ms else None)

    # ------------------------------------------------------------------ 4
    paso("4. IT desde un estado que NO exige motivo: no molesta")
    base_con(ruta, "DYP", [])
    c = cliente(ruta)
    r = c.post("/movimientos/90001/it", data=dict(CAMPOS_IT))
    ms = movimientos(ruta)
    afirmar(r.status_code in (302, 303), "guarda sin pedir nada", r.status_code)
    afirmar(len(ms) == 1 and ms[0]["es_desvio"] == 1,
            "lo marca como desvio igual", ms)
    afirmar(ms and not ms[0]["motivo"],
            "sin motivo, que es lo previsto: DYP->INGRESO A TALLER no esta en "
            "DESVIOS_CON_MOTIVO")

    # ------------------------------------------------------------------ 5
    paso("5. el caso de la unidad 91953 — el limite honesto")
    # STOCK -> EN ESPERA DYP CONSOLIDADO. Es desvio, pero NO esta en
    # DESVIOS_CON_MOTIVO, asi que con el criterio del endpoint generico
    # tampoco se exige motivo. Se deja escrito para que quede claro que este
    # cambio NO cubre ese caso.
    base_con(ruta, "STOCK", [])
    c = cliente(ruta)
    r = c.post("/movimientos/90001/pdi", data={
        "fecha": "2026-08-26", "tipo_combu": "Bencina", "bateria": "OK",
        "scanner": "OK", "a_c": "OK", "ob_mecanica": ""})
    ms = movimientos(ruta)
    afirmar(r.status_code in (302, 303),
            "el PDI de 91953 sigue guardando sin motivo", r.status_code)
    afirmar(len(ms) == 1 and ms[0]["es_desvio"] == 1,
            "marcado como desvio", ms)
    afirmar(ms and ms[0]["estado_hacia"] == "EN ESPERA DYP CONSOLIDADO",
            "con el destino que vio Franco", ms[0] if ms else None)
    print("      (documentado: ningun destino de PDI esta en DESVIOS_CON_MOTIVO,")
    print("       asi que para PDI la exigencia hoy no se dispara nunca)")

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    if fallos:
        print("FALLARON {} comprobaciones:".format(len(fallos)))
        for f in fallos:
            print("  - {}".format(f))
        return 1
    print("los 5 casos del motivo pasaron")
    return 0


if __name__ == "__main__":
    sys.exit(main())
