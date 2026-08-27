#!/usr/bin/env python3
"""
scripts/probar_reconciliacion.py -- que la clasificacion de la reconciliacion
mande cada caso al cajon correcto.

Importa mas que la mayoria de las pruebas porque este reporte es la PRUEBA DE
ACEPTACION de cada enlace que se cierre con el legado: si clasifica mal, no
sirve para decidir si un enlace funciono.

Y las categorias solo valen si son limpias. La 3 (contradiccion) vale porque es
corta -- se mira a mano. La 2 (el legado adelante) vale porque detecta el
trabajo de administracion. Cualquiera de las dos con ruido adentro se deja de
mirar, y entonces el dia que dicen algo tampoco se lee.

    python scripts/probar_reconciliacion.py
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SECRET_KEY", "prueba")

from modulos.movimientos import RECONOCIDOS_SIN_RUTA, normalizar_estado  # noqa: E402
from modulos.reconciliacion import _clasificar_una                       # noqa: E402

fallos = []


def afirmar(c, d, det=""):
    print(("   ok  " if c else "  FALLA ") + d + (("  <- " + str(det)) if det and not c else ""))
    if not c:
        fallos.append(d)


def caso(despachado, hacia, desde, recorrido=(), push_confirmado=False):
    fila = {
        "id": 1, "vin": "VINPRUEBA", "despachado": despachado,
        "estado_hacia": hacia, "estado_desde": desde, "paso": "pdi",
        "creado_en": "2026-08-27T10:00:00",
        "recorrido": {normalizar_estado(x) for x in recorrido},
        "push_confirmado": push_confirmado,
        "reconocido_sin_ruta": normalizar_estado(despachado) in RECONOCIDOS_SIN_RUTA,
    }
    return _clasificar_una(fila, normalizar_estado)[0]


def main():
    print("\n--- 1. las tres categorias ---")
    afirmar(caso("INGRESO A TALLER", "INGRESO A TALLER", "STOCK") == "de_acuerdo",
            "los dos dicen lo mismo -> de acuerdo")
    afirmar(caso("STOCK", "INGRESO A TALLER", "STOCK", ["STOCK"]) == "regla_adelante",
            "el legado esta donde arrancamos -> REGLA adelante")
    afirmar(caso("Navegando", "STOCK", "ZONA DE RECEPCION",
                 ["NAVEGANDO", "ZONA DE RECEPCION"]) == "regla_adelante",
            "el legado esta al ARRANQUE de una cadena -> REGLA adelante")
    afirmar(caso("ZONA DE DESPACHO", "INGRESO A TALLER", "STOCK", ["STOCK"],
                 push_confirmado=True) == "legado_adelante",
            "empujamos, lo tomo, y despues se movio -> el legado adelante")
    afirmar(caso("ZONA DE DESPACHO", "INGRESO A TALLER", "STOCK", ["STOCK"],
                 push_confirmado=False) == "contradiccion",
            "sin push confirmado y en otro lado -> contradiccion")

    print("\n--- 2. los reconocidos sin ruta NO ensucian ninguna categoria ---")
    # El caso que motivo el arreglo: push confirmado Y el legado en un estado
    # que REGLA reconoce pero no enruta. Antes caia en 'legado_adelante'.
    for estado in RECONOCIDOS_SIN_RUTA:
        r_con = caso(estado, "INGRESO A TALLER", "STOCK", ["STOCK"],
                     push_confirmado=True)
        r_sin = caso(estado, "INGRESO A TALLER", "STOCK", ["STOCK"],
                     push_confirmado=False)
        afirmar(r_con == "fuera_de_alcance",
                "{!r} con push confirmado -> fuera_de_alcance, NO categoria 2"
                .format(estado), r_con)
        afirmar(r_sin == "fuera_de_alcance",
                "{!r} sin push confirmado -> fuera_de_alcance, NO categoria 3"
                .format(estado), r_sin)

    # Y la forma con typo que escribe el legado tambien.
    afirmar(caso("SOLICTUD DESPACHO", "INGRESO A TALLER", "STOCK", ["STOCK"],
                 push_confirmado=True) == "fuera_de_alcance",
            "'SOLICTUD DESPACHO' (el typo del legado) tambien")

    print("\n--- 3. sin arco no se clasifica ---")
    afirmar(caso("STOCK", None, None) == "sin_arco",
            "un movimiento sin estado_hacia no opina")

    print("\n" + "=" * 62)
    if fallos:
        print("FALLARON {}:".format(len(fallos)))
        for f in fallos:
            print("  - " + f)
        return 1
    print("la clasificacion manda cada caso a su cajon")
    return 0


if __name__ == "__main__":
    sys.exit(main())
