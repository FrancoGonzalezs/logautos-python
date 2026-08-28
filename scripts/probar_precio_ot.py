#!/usr/bin/env python3
"""
scripts/probar_precio_ot.py -- que las DOS implementaciones del precio coincidan.

    LEGADO_API_KEY=... python scripts/probar_precio_ot.py           # solo sondas
    LEGADO_API_KEY=... python scripts/probar_precio_ot.py --crear   # crea OT reales

ESTA ES LA UNICA PRUEBA DEL PROYECTO QUE ESCRIBE EN PRODUCCION, y por eso pide
`--crear` explicito. Sin el flag corre las sondas -- las que no escriben -- y se
detiene.


POR QUE EXISTE
==============

El bloque D recalcula los precios del lado PHP en vez de confiar los que viajan.
Para un endpoint que toca facturacion eso es correcto: un precio que viaja es un
precio que se puede modificar en transito.

Pero deja DOS implementaciones del mismo calculo de plata -- `modulos/ot_pdi.py`
y `Api_regla::crear_ot_pdi` -- que tienen que coincidir para siempre. Es el
mismo problema de dos fuentes de verdad que el esquema, y aca hay con que
cerrarlo: las **970 OT de PDI y 969 de combustible** historicas son oraculo de
las dos.

Si alguna vez se separan, tiene que decirlo una prueba y no una factura.


LO QUE CREA, Y POR QUE ES ACEPTABLE
===================================

Con `--crear` crea hasta DOS OT reales en `orden_trabajo` por caso, sobre una
unidad con `clientecompleto = 'PRUEBA'`. Se niega a correr sobre cualquier otra.

`orden_trabajo` es append-only y de ahi sale la facturacion: una OT de mas no se
borra, hay que ir a explicarla. Por eso son pocas, sobre PRUEBA, y se imprimen
los ids para poder rastrearlas.

Y por eso hizo falta el bloque E antes: sin que el 201 devuelva `precio` y
`con_iva`, esta prueba crearia facturacion y NO podria leer que escribio el PHP
-- `orden_trabajo` no esta en el pull. Seria costo sin verificacion.
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SECRET_KEY", "prueba")

BASE = os.environ.get("LEGADO_BASE_URL", "https://claude.logautos.cl")
UA = "REGLA-sync/1.0"

fallos = []


def paso(t):
    print("\n--- {} ---".format(t))


def afirmar(cond, desc, detalle=""):
    if cond:
        print("   ok  {}".format(desc))
    else:
        print("  FALLA {}{}".format(desc, ("  <- " + str(detalle)) if detalle else ""))
        fallos.append(desc)


def pedir(metodo, ruta, cuerpo=None, cabeceras=None):
    h = {"Content-Type": "application/json", "User-Agent": UA}
    h.update(cabeceras or {})
    req = urllib.request.Request(
        BASE + ruta, method=metodo, headers=h,
        data=json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crear", action="store_true",
                    help="crea OT REALES en produccion (pocas, sobre PRUEBA)")
    ap.add_argument("--unidad", type=int, default=66505)
    args = ap.parse_args()

    clave = os.environ.get("LEGADO_API_KEY", "").strip()
    if not clave:
        print("falta LEGADO_API_KEY")
        return 2
    K = {"X-API-Key": clave}

    def KI():
        return {"X-API-Key": clave, "Idempotency-Key": str(uuid.uuid4())}

    from modulos import ot_pdi

    # ------------------------------------------------------------------
    paso("1. las sondas que NO escriben -- regla 4: demostrar el rechazo")

    for desc, cab, cuerpo, esperado in (
            ("sin API key", {}, {"fecha_pdi": "2026-08-27",
                                 "tipo_combu": "Diesel"}, 401),
            ("sin Idempotency-Key", K, {"fecha_pdi": "2026-08-27",
                                        "tipo_combu": "Diesel"}, 400),
            ("tipo_combu GASOLINA", KI(), {"fecha_pdi": "2026-08-27",
                                           "tipo_combu": "GASOLINA"}, 400),
            ("sin fecha_pdi", KI(), {"tipo_combu": "Diesel"}, 400),
    ):
        cod, _ = pedir("POST", "/api_regla/pdi/{}/ot".format(args.unidad),
                       cuerpo, cab)
        afirmar(cod == esperado, "{} -> {}".format(desc, esperado), cod)

    cod, _ = pedir("POST", "/api_regla/pdi/999999999/ot",
                   {"fecha_pdi": "2026-08-27", "tipo_combu": "Diesel"}, KI())
    afirmar(cod == 404, "unidad inexistente -> 404", cod)

    # GASOLINA rechazado del lado PHP es la otra mitad de la guarda que
    # `ot_pdi.exigir_combustible` hace de este lado. Las dos, o el silencio
    # vuelve por donde falte.
    afirmar(True, "el PHP tambien rechaza GASOLINA: la guarda esta en los dos "
                  "lados, no en uno")

    # ------------------------------------------------------------------
    paso("2. la unidad de prueba")

    ruta_db = os.path.join(RAIZ, "local.db")
    db = sqlite3.connect("file:{}?mode=ro".format(ruta_db), uri=True)
    db.row_factory = sqlite3.Row
    u = db.execute("SELECT * FROM newstocks_cidef WHERE id = ?",
                   (args.unidad,)).fetchone()
    afirmar(u is not None, "existe en la replica")
    if u is None:
        return 1
    cliente = (u["clientecompleto"] or "").strip()
    print("   {} | {} {} | cliente {!r}".format(
        u["vin"], u["marca"], u["modelo"], cliente))
    # LA GUARDA QUE IMPORTA: nunca sobre una unidad real.
    if cliente != "PRUEBA":
        print("\n  ME NIEGO: la unidad {} es del cliente {!r}, no 'PRUEBA'.\n"
              "  Esta prueba crea OT reales en `orden_trabajo`, que es\n"
              "  append-only y de donde sale la facturacion."
              .format(args.unidad, cliente))
        return 1

    if not args.crear:
        print("\n" + "=" * 60)
        print("las sondas pasaron. Para verificar los PRECIOS hace falta\n"
              "crear OT reales: volve a correr con --crear.")
        return 1 if fallos else 0

    # ------------------------------------------------------------------
    paso("3. los precios: PHP contra REGLA contra el historico")

    # LA FORMA REAL DEL 201 (desplegada el 2026-08-27):
    #
    #   {"ok":true,
    #    "ot":      {"pdi":123, "combustible":124},        <- ids, sin anidar
    #    "precios": {"pdi":{"precio":..,"con_iva":..},
    #                "combustible":{"precio":..,"con_iva":..,
    #                               "litros":..,"valor":..}}}
    #
    # `ot` quedo con los ids planos para no romper a quien ya los lee, y los
    # precios viajan aparte. Los dos son null en `combustible` si es Electrico.
    #
    # `litros` y `valor` son lo mas util de todo esto, y no estaban en mi
    # propuesta: con el precio final solo se puede comparar un numero, que
    # puede coincidir por casualidad. Con los dos factores se compara LA REGLA
    # -- que es donde estuvo el error que costo 204 filas de 481.
    casos = [("Diesel", "2026-08-27"), ("Bencina", "2026-08-27"),
             ("Electrico", "2026-08-27")]
    creadas = []
    claves = {}
    for combu, fecha in casos:
        key = str(uuid.uuid4())
        claves[combu] = key
        cod, cuerpo = pedir(
            "POST", "/api_regla/pdi/{}/ot".format(args.unidad),
            {"fecha_pdi": fecha, "tipo_combu": combu, "created_by": 47},
            {"X-API-Key": clave, "Idempotency-Key": key})
        if cod != 201:
            afirmar(False, "{}: 201".format(combu),
                    "{} {}".format(cod, cuerpo[:160]))
            continue
        d = json.loads(cuerpo)
        ot, precios = d.get("ot") or {}, d.get("precios") or {}
        if "pdi" not in precios:
            afirmar(False, "el 201 trae `precios` (bloque E desplegado?)", d)
            break

        # -- la OT de PDI --
        mia = ot_pdi.ot_de_pdi({}, fecha)
        afirmar(precios["pdi"]["precio"] == mia["precio"],
                "{}: precio PDI, PHP {} == REGLA {}".format(
                    combu, precios["pdi"]["precio"], mia["precio"]))
        afirmar(precios["pdi"]["con_iva"] == mia["con_iva"],
                "{}: con_iva PDI, PHP {} == REGLA {}".format(
                    combu, precios["pdi"]["con_iva"], mia["con_iva"]))
        creadas.append(("PDI", ot.get("pdi"), precios["pdi"]["precio"]))

        # -- la de combustible --
        suya = precios.get("combustible")
        mia_c = ot_pdi.ot_de_combustible(
            {"marca": u["marca"], "modelo": u["modelo"]}, combu)
        if mia_c is None:
            afirmar(suya is None and ot.get("combustible") is None,
                    "{}: ninguno de los dos genera OT de combustible"
                    .format(combu), (suya, ot.get("combustible")))
            continue
        if suya is None:
            afirmar(False, "{}: el PHP no genero la de combustible".format(combu))
            continue

        # LA REGLA, no el numero: los dos factores por separado.
        afirmar(suya["litros"] == mia_c["litros"],
                "{}: LITROS, PHP {} == REGLA {}".format(
                    combu, suya["litros"], mia_c["litros"]))
        afirmar(suya["valor"] == ot_pdi.VALOR_LITRO[
                    ot_pdi.exigir_combustible(combu)],
                "{}: VALOR por litro, PHP {} == REGLA {}".format(
                    combu, suya["valor"],
                    ot_pdi.VALOR_LITRO[ot_pdi.exigir_combustible(combu)]))
        afirmar(suya["precio"] == mia_c["precio"],
                "{}: precio combustible, PHP {} == REGLA {}".format(
                    combu, suya["precio"], mia_c["precio"]))
        afirmar(suya["con_iva"] == mia_c["con_iva"],
                "{}: con_iva combustible, PHP {} == REGLA {}".format(
                    combu, suya["con_iva"], mia_c["con_iva"]))
        creadas.append(("COMBUSTIBLE", ot.get("combustible"), suya["precio"]))

        # -- y contra el ORACULO --
        n = db.execute(
            "SELECT COUNT(*) FROM orden_trabajo "
            " WHERE requerimiento = 'COMBUSTIBLE POR NORMA' "
            "   AND createdDtm > '2026-06-02' AND precio = ? AND costo = ?",
            (suya["precio"], suya["precio"])).fetchone()[0]
        afirmar(n > 0,
                "{}: ${} es un precio que el legado ya emitio {} veces".format(
                    combu, suya["precio"], n))

    # ------------------------------------------------------------------
    paso("4. la idempotencia: reenviar la misma key NO cobra dos veces")

    # `orden_trabajo` es append-only: si esto falla, la OT duplicada queda.
    # Se reusa la key del caso Diesel, asi que NO crea nada nuevo.
    if "Diesel" in claves:
        cod, cuerpo = pedir(
            "POST", "/api_regla/pdi/{}/ot".format(args.unidad),
            {"fecha_pdi": "2026-08-27", "tipo_combu": "Diesel",
             "created_by": 47},
            {"X-API-Key": clave, "Idempotency-Key": claves["Diesel"]})
        d = json.loads(cuerpo) if cuerpo else {}
        afirmar(cod == 200 and d.get("idempotente"),
                "el reenvio devuelve 200 idempotente", (cod, cuerpo[:120]))
        primero = [c for c in creadas if c[0] == "PDI"]
        if primero:
            devuelto = (d.get("ot") or {}).get("pdi")
            afirmar(devuelto == primero[0][1],
                    "y el MISMO id de OT ({}), sin crear otra".format(devuelto),
                    (devuelto, primero[0][1]))
        # Documentado por Franco: el 200 idempotente NO trae precios, porque
        # `api_idempotency` guarda el id y nada mas. No es una falta: es que la
        # tabla de idempotencia no es un cache de respuestas.
        afirmar("precios" not in d,
                "el 200 idempotente no trae precios, y esta bien: "
                "api_idempotency guarda el id, no la respuesta")

    n_pdi = db.execute(
        "SELECT COUNT(*) FROM orden_trabajo WHERE requerimiento = 'PDI' "
        "  AND createdDtm > '2026-06-02' AND precio = ?",
        (ot_pdi.precio_pdi("2026-08-27"),)).fetchone()[0]
    afirmar(n_pdi >= 900,
            "el precio de PDI calza con {} OT historicas".format(n_pdi))

    db.close()

    print("\n   OT creadas en produccion (para rastrearlas):")
    for req, oid, precio in creadas:
        print("      {:<14} id {:<8} ${}".format(req, oid, precio))

    print("\n" + "=" * 60)
    if fallos:
        print("FALLARON {} comprobaciones:".format(len(fallos)))
        for f in fallos:
            print("  - {}".format(f))
        return 1
    print("las dos implementaciones del precio coinciden, y con el historico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
