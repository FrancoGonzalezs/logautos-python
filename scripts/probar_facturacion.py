#!/usr/bin/env python3
"""
scripts/probar_facturacion.py -- las reglas de plata de Facturacion.

Tres cosas, y las tres salieron de encontrar un bug de redondeo en las OT:

    1. redondeo    las seis tarifas y los totales con IVA redondean al peso
                   MEDIO ARRIBA, como el legado -- no con round() de Python,
                   que redondea al par y da un peso menos en los .5
    2. vigencia    la tarifa se busca por fecha: un mes viejo se calcula con
                   la que regia ese mes, no con la de hoy
    3. sin tarifa  un cliente que no tiene tarifa Y no esta declarado como que
                   no factura acopio se AVISA, no desaparece del calculo

    python scripts/probar_facturacion.py
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SECRET_KEY", "prueba")

from core import con_iva, peso                                    # noqa: E402
from modulos import facturacion as F                              # noqa: E402

fallos = []


def afirmar(c, d, det=""):
    print(("   ok  " if c else "  FALLA ") + d + (("  <- " + str(det)) if det and not c else ""))
    if not c:
        fallos.append(d)


def main():
    print("\n--- 1. redondeo medio arriba, como el legado ---")
    afirmar(peso(671.5) == 672, "peso(671.5) = 672, no 671")
    afirmar(peso(29550 * 1.19) == 35165,
            "peso(29550*1.19) = 35165 (round() de Python da 35164)",
            peso(29550 * 1.19))
    afirmar(con_iva(29550) == 35165, "con_iva(29550) = 35165")
    afirmar(isinstance(F.tarifa_diaria("CARFLEX", 39500.0, "2026-08-27"), int),
            "la tarifa vuelve redondeada a entero")
    # Las seis redondean: ninguna deja decimales sueltos.
    for cli in sorted(F.clientes_con_tarifa("2026-08-27")):
        t = F.tarifa_diaria(cli, 40123.45, "2026-08-27")
        afirmar(t == int(t), "{} redondeada".format(cli), t)

    print("\n--- 2. vigencia por fecha ---")
    afirmar(F.tarifa_diaria("CARFLEX", 39500.0, "1999-01-01") is None,
            "antes de la vigencia no hay tarifa")
    afirmar(F.tarifa_diaria("CARFLEX", 39500.0, "2026-08-27") == peso(39500 * 0.022),
            "en vigencia usa la tabla")
    # Agregar una fila con fecha posterior NO cambia el calculo de un mes viejo.
    F.TARIFAS_ACOPIO.append(("2030-01-01", "CARFLEX", "uf", 0.030))
    try:
        afirmar(F.tarifa_diaria("CARFLEX", 39500.0, "2026-08-27") == peso(39500 * 0.022),
                "una tarifa futura no toca el pasado")
        afirmar(F.tarifa_diaria("CARFLEX", 39500.0, "2030-06-01") == peso(39500 * 0.030),
                "y si rige desde su fecha")
    finally:
        F.TARIFAS_ACOPIO.pop()

    print("\n--- 3. cliente sin tarifa: se avisa, no se esconde ---")
    filas = [{"clientecompleto": "CIDEF"},
             {"clientecompleto": "GELLONA - LOGAUTOS"},   # declarado sin acopio
             {"clientecompleto": "CLIENTE NUEVO SPA"},    # ni tarifa ni declarado
             {"clientecompleto": "CLIENTE NUEVO SPA"}]
    faltan = F.clientes_sin_tarifa(filas, "2026-08-27")
    afirmar("CLIENTE NUEVO SPA" in faltan, "detecta el cliente sin tarifa", faltan)
    afirmar(faltan.get("CLIENTE NUEVO SPA") == 2, "y cuenta sus unidades", faltan)
    afirmar("CIDEF" not in faltan, "no avisa del que tiene tarifa")
    afirmar("GELLONA - LOGAUTOS" not in faltan,
            "ni del declarado como que no factura acopio")

    print("\n" + "=" * 62)
    if fallos:
        print("FALLARON {}:".format(len(fallos)))
        for f in fallos:
            print("  - " + f)
        return 1
    print("las reglas de plata estan bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
