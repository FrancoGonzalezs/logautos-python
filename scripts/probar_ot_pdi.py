#!/usr/bin/env python3
"""
scripts/probar_ot_pdi.py -- las dos OT de la PDI, contra el historico real.

    python scripts/probar_ot_pdi.py

Lee `local.db` de solo lectura. No escribe nada, no sale a la red.

REGLA 2 DEL PROYECTO: si no llega al 100%, hay una regla que no entendimos, no
un caso borde. Las dos veces que esta prueba NO dio 100% mientras se escribia,
el que estaba mal era el codigo:

  57,6% en combustible  ->  la rama Diesel del PHP compara UN prefijo de modelo
                            ('G7') y la de Bencina compara CUATRO. Un FOTON V9
                            a diesel carga 20 litros, no 15. 204 filas.
  97,3% en PDI          ->  el 2026-06-02 conviven los dos precios (27 a 46.878
                            y 3 a 49.000): el despliegue fue a mitad de ese
                            dia. La vigencia arranca el 03, no el 02.

Las dos se ven igual desde afuera -- "unas filas no calzan" -- y las dos eran
una regla que faltaba.
"""

import os
import sqlite3
import sys
from collections import Counter

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SECRET_KEY", "prueba")

# El dia del cambio de version tiene las dos formas y `createdDtm` no guarda la
# hora, asi que no se puede clasificar. Se mira estrictamente DESPUES.
DESDE = "2026-06-02"

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


def main():
    ruta = os.path.join(RAIZ, "local.db")
    if not os.path.exists(ruta):
        print("no hay local.db: esta prueba necesita la replica")
        return 0

    from core import peso
    from modulos import ot_pdi

    def peso_de(precio):
        """El con_iva que corresponde a ese precio, con la regla del dinero."""
        return peso(precio * 1.19)

    db = sqlite3.connect("file:{}?mode=ro".format(ruta), uri=True)
    db.row_factory = sqlite3.Row

    # ------------------------------------------------------------------
    paso("1. la OT de PDI contra las {} historicas".format("970"))

    ok = mal = 0
    diferencias = Counter()
    for r in db.execute("""
            SELECT precio, costo, utilidad, margen_utilidad, con_iva, createdDtm
              FROM orden_trabajo
             WHERE requerimiento = 'PDI' AND createdDtm > ?""", (DESDE,)):
        calc = ot_pdi.ot_de_pdi({}, r["createdDtm"])
        esperado = (calc["precio"], calc["costo"], calc["utilidad"],
                    float(calc["margen_utilidad"]), str(calc["con_iva"]))
        real = (r["precio"], r["costo"], r["utilidad"],
                r["margen_utilidad"], str(r["con_iva"]))
        if real == esperado:
            ok += 1
        else:
            mal += 1
            diferencias[(real, esperado)] += 1
    total = ok + mal
    print("   {} de {} = {:.1f}%".format(ok, total, 100.0 * ok / (total or 1)))
    for d, n in diferencias.most_common(3):
        print("   real {} != calculado {}  x{}".format(d[0], d[1], n))
    afirmar(total > 500, "hay volumen suficiente para que el 100% signifique algo",
            total)
    afirmar(mal == 0, "las OT de PDI calzan al 100%", "{} no calzan".format(mal))

    # ------------------------------------------------------------------
    paso("2. el precio viejo se sigue reconociendo")

    afirmar(ot_pdi.precio_pdi("2026-05-30") == 46878,
            "antes del cambio, 46.878")
    afirmar(ot_pdi.precio_pdi("2026-08-27") == 49000,
            "despues, 49.000")

    # ------------------------------------------------------------------
    paso("3. la OT de combustible contra las historicas")

    # NO SE VALIDA CONTRA `tipo_combu`, Y ESO ES EL HALLAZGO.
    #
    # El primer intento comparaba el precio calculado a partir del combustible
    # de la unidad, y daba 96,5%. Las 35 que no calzaban no eran un error de la
    # formula: son unidades cuyo `tipo_combu` DE HOY no es el que el operario
    # eligio al hacer la PDI. El caso claro es FOTON VIEW GRAND, que aparece
    # 32 veces como 'GASOLINA' y 13 como 'DIESEL' -- mismo modelo, misma epoca
    # -- y las 45 con precio de diesel. O sea que la columna se contradice a si
    # misma, porque la reescribe despues el proceso de las mayusculas.
    #
    # Usarla como oraculo era medir el dato sucio en vez del calculo. Lo que el
    # codigo DECIDE son dos cosas, y las dos se pueden validar sin la columna:
    #
    #   1. los litros, a partir de marca y modelo
    #   2. el IVA sobre el precio
    #
    # El combustible no lo decide REGLA: lo elige el operario en la pantalla,
    # de una lista de tres. Validarlo contra una columna que otro proceso pisa
    # seria validar el proceso ajeno.
    #
    # El precio de la propia OT dice que combustible era: solo hay cuatro
    # combinaciones legales y ninguna se repite entre combustibles.
    LEGALES = {}
    for combu, valor in ot_pdi.VALOR_LITRO.items():
        for litros in (ot_pdi.LITROS_REDUCIDO, ot_pdi.LITROS_POR_DEFECTO):
            LEGALES[valor * litros] = (combu, litros)

    ok = mal = fuera = 0
    iva_ok = iva_mal = 0
    diferencias = Counter()
    for r in db.execute("""
            SELECT precio, costo, con_iva, marca, modelo
              FROM orden_trabajo
             WHERE requerimiento = 'COMBUSTIBLE POR NORMA'
               AND createdDtm > ? AND precio = costo""", (DESDE,)):
        par = LEGALES.get(r["precio"])
        if par is None:
            fuera += 1
            continue
        combu, litros = par
        if ot_pdi.litros_de(combu, r["marca"], r["modelo"]) == litros:
            ok += 1
        else:
            mal += 1
            diferencias[(combu, r["marca"], (r["modelo"] or "")[:12], litros)] += 1
        if str(r["con_iva"]) == str(peso_de(r["precio"])):
            iva_ok += 1
        else:
            iva_mal += 1

    total = ok + mal
    print("   litros:  {} de {} = {:.1f}%".format(
        ok, total, 100.0 * ok / (total or 1)))
    print("   con_iva: {} de {} = {:.1f}%".format(
        iva_ok, iva_ok + iva_mal, 100.0 * iva_ok / ((iva_ok + iva_mal) or 1)))
    for d, n in diferencias.most_common(5):
        print("   {} {} {!r} deberia dar {} litros  x{}".format(*d, n))

    afirmar(total > 300, "hay volumen suficiente", total)
    afirmar(fuera == 0,
            "todos los precios salen de una combinacion legal valor x litros",
            "{} fuera del catalogo".format(fuera))
    afirmar(mal == 0, "la regla de los litros calza al 100%",
            "{} no calzan".format(mal))
    afirmar(iva_mal == 0, "y el IVA tambien -- el caso de los 119 pesos",
            "{} no calzan".format(iva_mal))

    # ------------------------------------------------------------------
    paso("4. los litros: las dos ramas NO usan la misma regla")

    afirmar(ot_pdi.litros_de("Bencina", "FOTON", "V9 2.0") == 15,
            "Bencina + modelo V9 -> 15 litros")
    afirmar(ot_pdi.litros_de("Diesel", "FOTON", "V9 2.0") == 20,
            "Diesel  + modelo V9 -> 20 litros (la rama diesel solo mira G7)")
    afirmar(ot_pdi.litros_de("Diesel", "FOTON", "G7 4X4") == 15,
            "Diesel  + modelo G7 -> 15 litros")
    afirmar(ot_pdi.litros_de("Diesel", "DFM", "V9 2.0") == 15,
            "la marca gana sobre el modelo en las dos ramas")
    afirmar(ot_pdi.litros_de("Electrico", "FOTON", "V9") == 0,
            "un electrico no carga")

    # ------------------------------------------------------------------
    paso("5. el vocabulario doble de tipo_combu: revienta, no adivina")

    # 'GASOLINA' son 2.416 unidades y NO ESTA en ninguna comparacion del PHP.
    # Si esa cadena llegara al calculo, la PDI se quedaria sin OT de
    # combustible y sin descuento de stock, en silencio. Este caso existe para
    # que eso sea imposible sin que nadie lo note.
    for malo in ("GASOLINA", "HIBRIDO", "ELECTRICA", "", None, "nafta"):
        try:
            ot_pdi.exigir_combustible(malo)
            afirmar(False, "revienta con {!r}".format(malo))
        except ot_pdi.CombustibleDesconocido as e:
            afirmar("GASOLINA" in str(e) and "formulario" in str(e),
                    "revienta con {!r}, y el mensaje explica de donde viene"
                    .format(malo))

    for bueno in ("Bencina", "BENCINA", "diesel", "Electrico"):
        try:
            ot_pdi.exigir_combustible(bueno)
            afirmar(True, "acepta {!r} en cualquier caja".format(bueno))
        except ot_pdi.CombustibleDesconocido:
            afirmar(False, "acepta {!r}".format(bueno))

    # Y el que de verdad importa: que no se pueda colar por la puerta de al
    # lado. `litros_de` y `ot_de_combustible` tambien tienen que reventar.
    for funcion, args in ((ot_pdi.litros_de, ("GASOLINA", "FOTON", "V9")),
                          (ot_pdi.ot_de_combustible,
                           ({"marca": "FOTON", "modelo": "V9"}, "GASOLINA"))):
        try:
            funcion(*args)
            afirmar(False, "{} tampoco deja pasar GASOLINA".format(
                funcion.__name__))
        except ot_pdi.CombustibleDesconocido:
            afirmar(True, "{} tampoco deja pasar GASOLINA".format(
                funcion.__name__))

    # La pantalla y el calculo tienen que hablar el mismo idioma.
    from modulos.taller import COMBUSTIBLES as DE_LA_PANTALLA
    afirmar(tuple(DE_LA_PANTALLA) == ot_pdi.COMBUSTIBLES,
            "la lista de la pantalla y la del calculo son la MISMA",
            (DE_LA_PANTALLA, ot_pdi.COMBUSTIBLES))

    # ------------------------------------------------------------------
    paso("6. una PDI genera una OT o dos, nunca cero")

    unidad = {"marca": "FOTON", "modelo": "V9 2.0"}
    dos = ot_pdi.ots_de_pdi(unidad, "Diesel", "2026-08-27")
    afirmar(len(dos) == 2, "diesel -> dos OT", len(dos))
    afirmar(dos[1]["precio"] == 2070 * 20, "con 20 litros a 2.070")
    una = ot_pdi.ots_de_pdi(unidad, "Electrico", "2026-08-27")
    afirmar(len(una) == 1, "electrico -> una sola", len(una))
    afirmar(una[0]["requerimiento"] == "PDI", "y es la de PDI")

    db.close()
    print("\n" + "=" * 60)
    if fallos:
        print("FALLARON {} comprobaciones:".format(len(fallos)))
        for f in fallos:
            print("  - {}".format(f))
        return 1
    print("las dos OT de la PDI calzan con el historico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
