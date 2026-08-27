#!/usr/bin/env python3
"""
scripts/probar_estados.py -- que el catalogo de estados atrape las formas que
el legado escribe de verdad.

`normalizar_estado` es la puerta por la que pasa TODO estado que viene del
sistema anterior antes de compararse contra el catalogo de REGLA. Lo que no
atrapa queda como un estado desconocido: el motor no sabe recomendar desde ahi
y la unidad se queda muda.

Las variantes no se inventan: salen de contar `registros`, que tiene 296.529
movimientos con estado escritos por el legado a lo largo de cuatro años.

    python scripts/probar_estados.py
"""

import os
import sqlite3
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SECRET_KEY", "prueba")

from modulos.movimientos import ESTADOS, normalizar_estado   # noqa: E402

fallos = []


def afirmar(condicion, descripcion, detalle=""):
    if condicion:
        print("   ok  {}".format(descripcion))
    else:
        print("  FALLA {}{}".format(descripcion,
                                    ("  <- " + str(detalle)) if detalle else ""))
        fallos.append(descripcion)


# (crudo, a que estado del catalogo tiene que resolver, cuantos movimientos)
VARIANTES = [
    ("EN ESPERA DYP",             "EN ESPERA DE ASIGNACION DYP", None),
    ("EN ESPERA ASIGNACION DYP",  "EN ESPERA DE ASIGNACION DYP", 4062),
    ("EN ESPERERA DYP",           "EN ESPERA DE ASIGNACION DYP", 108),
    ("INGRESAO TALLER",           "INGRESO A TALLER",            11),
    ("EN ESPERA CONSOLIDADO DYP", "EN ESPERA DYP CONSOLIDADO",   9),
    ("EN ESPERA DE DYP CONSOLIDADO", "EN ESPERA DYP CONSOLIDADO", None),
    ("FR",                        "FR - MECANICA",               None),
    ("STOCK SIN PDI",             "STOCK",                       None),
]


def main():
    print("\n--- 1. las variantes resuelven al estado del catalogo ---")
    for crudo, esperado, _ in VARIANTES:
        obtenido = normalizar_estado(crudo)
        afirmar(obtenido == esperado,
                "{!r} -> {}".format(crudo, esperado), obtenido)
        afirmar(esperado in ESTADOS,
                "   y {!r} esta en el catalogo".format(esperado))

    print("\n--- 2. la caja y los espacios no cuentan ---")
    for crudo in ("navegando", "  Navegando  ", "NAVEGANDO"):
        afirmar(normalizar_estado(crudo) == "NAVEGANDO",
                "{!r} -> NAVEGANDO".format(crudo), normalizar_estado(crudo))

    print("\n--- 3. cobertura sobre los datos reales ---")
    ruta = os.path.join(RAIZ, "local.db")
    if not os.path.exists(ruta):
        print("   (saltada: no hay replica en {})".format(ruta))
    else:
        db = sqlite3.connect(ruta)
        dentro = fuera = 0
        huerfanos = []
        for estado, n in db.execute(
                "SELECT estado, COUNT(*) FROM registros "
                "WHERE estado IS NOT NULL AND estado <> '' GROUP BY estado"):
            if normalizar_estado(estado) in ESTADOS:
                dentro += n
            else:
                fuera += n
                huerfanos.append((estado, n))
        db.close()
        total = dentro + fuera
        pct = 100.0 * dentro / total
        print("   {:,} de {:,} movimientos con estado conocido = {:.1f}%"
              .format(dentro, total, pct))
        afirmar(pct >= 90.0,
                "la cobertura no baja del 90%",
                "{:.1f}%".format(pct))
        # Los que faltan son estados que REGLA no modela, no variantes sueltas.
        # Si aparece uno NUEVO por debajo de este umbral, es una variante que
        # se escapo y hay que agregarla, no un estado de negocio.
        chicos = [(e, n) for e, n in huerfanos if n < 200]
        print("   sin equivalente y con menos de 200 usos: {}".format(len(chicos)))
        for e, n in sorted(chicos, key=lambda x: -x[1])[:8]:
            print("      {!r}: {}".format(e, n))

    print("\n" + "=" * 62)
    if fallos:
        print("FALLARON {} comprobaciones:".format(len(fallos)))
        for f in fallos:
            print("  - {}".format(f))
        return 1
    print("el catalogo de estados esta al dia")
    return 0


if __name__ == "__main__":
    sys.exit(main())
