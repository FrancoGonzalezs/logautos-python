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


def caso(despachado, hacia, no_encola=False, pendientes=0,
         pendientes_con_error=0, conflictos=0):
    """Una unidad tocada por REGLA, con el estado de SU COLA.

    Ya no lleva `recorrido` ni `push_confirmado`: la pregunta dejo de ser
    "¿donde esta cada uno?" y paso a ser "¿se entero el legado?", y eso lo
    responde la cola. Ver `comparar_estados`."""
    fila = {
        "id": 1, "vin": "VINPRUEBA", "despachado": despachado,
        "estado_hacia": hacia, "estado_desde": "STOCK", "paso": "pdi",
        "creado_en": "2026-08-27T10:00:00", "ultimo_error": "se cayo",
        "no_encola": no_encola, "pendientes": pendientes,
        "pendientes_con_error": pendientes_con_error, "conflictos": conflictos,
        "reconocido_sin_ruta": normalizar_estado(despachado) in RECONOCIDOS_SIN_RUTA,
    }
    return _clasificar_una(fila, normalizar_estado)[0]


def main():
    print("\n--- 1. la pregunta es a la COLA, no a los estados ---")
    afirmar(caso("INGRESO A TALLER", "INGRESO A TALLER") == "entregado",
            "cola resuelta y sin error -> entregado")
    afirmar(caso("STOCK", "INGRESO A TALLER", pendientes=1) == "en_camino",
            "encolado y sin resolver -> en camino")
    afirmar(caso("STOCK", "INGRESO A TALLER", pendientes=1,
                 pendientes_con_error=1) == "trabado",
            "sin resolver Y con error -> trabado, que es lo que se mira a mano")
    afirmar(caso("STOCK", "INGRESO A TALLER", conflictos=1) == "conflicto",
            "hay conflicto registrado -> gano el legado")
    afirmar(caso("STOCK", "DYP", no_encola=True) == "no_viaja",
            "el estado no tiene enlace -> no viaja")
    afirmar(caso("ZONA DE DESPACHO", "INGRESO A TALLER") == "el_legado_siguio",
            "todo entregado y la fila ya no coincide -> lo trabajo despues")

    print("\n--- 2. EL CASO QUE MOTIVO EL CAMBIO ---")
    # Guardar un movimiento escribe la fila, asi que `despachado` y
    # `estado_hacia` COINCIDEN aunque el legado no se haya enterado. Con la
    # comparacion vieja eso daba "de acuerdo": medido el 2026-08-27, el
    # contador subia de 1 a 2 con la cola sin resolver.
    afirmar(caso("STOCK", "STOCK", pendientes=1) == "en_camino",
            "la fila YA dice STOCK porque la escribimos nosotros, y aun asi "
            "NO cuenta como entregado")
    afirmar(caso("STOCK", "STOCK", pendientes=1, pendientes_con_error=1)
            == "trabado",
            "y si ademas fallo, se ve que fallo")

    print("\n--- 3. el orden de las categorias ---")
    # `no_viaja` gana sobre todo: si no hay enlace, el resto no aplica.
    afirmar(caso("STOCK", "DYP", no_encola=True, conflictos=1) == "no_viaja",
            "no_viaja gana sobre conflicto")
    # Y `trabado` gana sobre `en_camino`: lo que falla se mira antes.
    afirmar(caso("STOCK", "INGRESO A TALLER", pendientes=2,
                 pendientes_con_error=1) == "trabado",
            "una entrada con error entre varias -> trabado")

    print("\n--- 4. los reconocidos sin ruta no ensucian `el_legado_siguio` ---")
    # SOLICITUD DESPACHO y compania no mueven la unidad de lugar: que el legado
    # quede ahi no significa que la haya trabajado despues.
    for estado in RECONOCIDOS_SIN_RUTA:
        afirmar(caso(estado, "INGRESO A TALLER") == "entregado",
                "{!r} no se cuenta como que el legado siguio".format(estado))
    afirmar(caso("SOLICTUD DESPACHO", "INGRESO A TALLER") == "entregado",
            "'SOLICTUD DESPACHO' (el typo del legado) tambien")

    print("\n--- 5. sin arco no se clasifica ---")
    afirmar(caso("STOCK", None) == "sin_arco",
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
