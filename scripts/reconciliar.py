#!/usr/bin/env python3
"""
scripts/reconciliar.py -- corre la reconciliacion diaria y avisa si hay algo.

    python scripts/reconciliar.py            # corre, guarda y avisa
    python scripts/reconciliar.py --sin-correo
    python scripts/reconciliar.py --sin-guardar   # la medicion BASE

Lo corre el hilo de fondo de app.py una vez por dia; esto es para correrlo a
mano y para sacar la medicion base.
"""

import argparse
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SECRET_KEY", "prueba")

from modulos.reconciliacion import (CATEGORIAS, ROTULOS, avisar,  # noqa: E402
                                    correr)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reconciliacion con el legado")
    ap.add_argument("--sin-correo", action="store_true")
    ap.add_argument("--sin-guardar", action="store_true")
    args = ap.parse_args(argv)

    r = correr(guardar=not args.sin_guardar)
    c = r["estados"]["conteo"]

    print("reconciliacion del {}".format(r["corrida_en"]))
    print()
    # LAS CATEGORIAS SE LEEN DE `ROTULOS`, no se escriben aca.
    #
    # Este bloque nombraba `de_acuerdo`, `regla_adelante`, `legado_adelante` y
    # `contradiccion`, que son las de ANTES del cambio de arquitectura del
    # 2026-08-27 -- cuando la reconciliacion comparaba dos verdades. Desde que
    # le pregunta a la COLA son otras seis, asi que el script reventaba con
    # KeyError: la reconciliacion diaria no corria.
    #
    # Es el mismo patron que ya nos mordio dos veces: la herramienta que mira
    # si algo esta roto, rota. Y por eso ahora las categorias salen del mismo
    # diccionario que las define -- si mañana se agrega una septima, aparece
    # sola en vez de faltar en silencio.
    print("ESTADOS  (unidades que REGLA toco: {})".format(
        r["estados"]["unidades_miradas"]))
    for clave in CATEGORIAS:
        print("   {:<32}: {:>6}".format(ROTULOS.get(clave, clave),
                                        c.get(clave, 0)))
    for clave in sorted(k for k in c if k not in CATEGORIAS):
        print("   {:<32}: {:>6}".format(ROTULOS.get(clave, clave), c[clave]))

    for d in r["estados"].get("trabadas", [])[:10]:
        print("      {}".format(d))

    s = r["sin_registro"]
    print()
    print("ESTADO SIN FILA EN registros  (las 58 escrituras silenciosas)")
    print("   unidades con historial        : {:>6,}".format(s["con_historial"]))
    print("   el estado NO coincide         : {:>6,}  ({}%)".format(
        s["estado_no_coincide"], s["porcentaje"]))

    p = r["pdi_sin_ot"]
    print()
    print("PDI SIN SU OT  (plata sin cobrar), desde {}".format(p["desde"]))
    print("   PDI en el periodo             : {:>6,}".format(p["pdi_totales"]))
    print("   sin OT                        : {:>6,}  ({}%)".format(
        p["sin_ot"], p["porcentaje"]))
    for d in p["detalle"][:8]:
        print("      unidad {} {} {} {}".format(
            d["id"], d["vin"], (d["clientecompleto"] or "")[:18], d["fecha_pdi"]))

    if not args.sin_correo:
        print()
        print("correo:", avisar(r))

    d = r.get("danos_truncados") or {}
    if d:
        print()
        print("DAÑOS CORTADOS  (las tres listas de check_list, tope {} chars)"
              .format(d["tope"]))
        print("   check lists mirados desde {}   : {:>6}".format(
            d["desde"], d["miradas"]))
        print("   con alguna columna EN EL TOPE  : {:>6}".format(d["en_el_tope"]))
        print("   con las tres listas DESALINEADAS: {:>5}   <- la pieza n deja "
              "de ir con su tipo".format(d["desalineadas"]))
        print("   de esas, SIN explicar por el tope: {:>4}   <- estas se miran "
              "de a una".format(d["sin_explicar"]))
        for x in d.get("detalle", [])[:5]:
            print("      check_list {} ({}): {} piezas / {} tipos / {} niveles{}"
                  .format(x["id"], x["fecha"], x["piezas"], x["tipos"],
                          x["niveles"],
                          "" if x["toca_el_tope"] else "   <- NO toca el tope"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
