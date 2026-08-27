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

from modulos.reconciliacion import avisar, correr        # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reconciliacion con el legado")
    ap.add_argument("--sin-correo", action="store_true")
    ap.add_argument("--sin-guardar", action="store_true")
    args = ap.parse_args(argv)

    r = correr(guardar=not args.sin_guardar)
    c = r["estados"]["conteo"]

    print("reconciliacion del {}".format(r["corrida_en"]))
    print()
    print("ESTADOS  (unidades que REGLA toco: {})".format(r["estados"]["unidades_miradas"]))
    print("   de acuerdo                    : {:>6}".format(c["de_acuerdo"]))
    print("   (1) REGLA adelante            : {:>6}   <- tiene que CAER con cada enlace".format(c["regla_adelante"]))
    print("   (2) el sistema anterior adelante: {:>4}   <- trabajo de administracion".format(c["legado_adelante"]))
    print("   (3) CONTRADICCION             : {:>6}   <- lo unico que se mira a mano".format(c["contradiccion"]))
    if c["sin_arco"]:
        print("   sin arco guardado             : {:>6}".format(c["sin_arco"]))

    for d in r["estados"]["contradicciones"][:10]:
        print("      unidad {} ({}): anterior={!r} REGLA={!r} desde={!r} paso={}"
              .format(d["unidad"], d["vin"], d["legado"], d["regla"],
                      d["desde"], d["paso"]))

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
