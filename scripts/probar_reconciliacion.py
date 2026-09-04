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


def probar_el_instrumento_entero():
    """Corre `reconciliar.py` DE PUNTA A PUNTA, como proceso, y falla si
    revienta o si el informe perdio una seccion.

    POR QUE ESTA PRUEBA EXISTE

    Las de arriba prueban `_clasificar_una`, que es la logica. Ninguna probaba
    el SCRIPT, y el 2026-09-04 el script estaba roto: imprimia `de_acuerdo`,
    `regla_adelante`, `legado_adelante` y `contradiccion` -- las categorias de
    ANTES del cambio de arquitectura del 2026-08-27 -- y moria con KeyError en
    la primera linea del informe. La reconciliacion diaria no corria, y nadie
    lo sabia.

    Eso es peor que cualquier bug que la reconciliacion pueda encontrar. Es el
    instrumento de aceptacion del mes en paralelo: es lo UNICO que ve el 18,4%
    de cambios de estado que el legado hace sin dejar fila en `registros`. Si
    puede morirse en silencio, "la reconciliacion no mostro nada" no es
    evidencia de nada -- es la misma frase tanto si todo esta bien como si el
    informe no se imprimio.

    Es la tercera vez con la misma forma: la herramienta que mira si algo esta
    roto, rota. Las otras dos fueron la sonda 4 de `verificar_push_produccion`
    (500 desde el bloque J) y `columnas_que_acumulan` (la version vieja quedo
    desplegada, con `php -l` limpio).

    POR QUE COMO SUBPROCESO Y NO IMPORTANDO `main()`

    Porque lo que se rompio no fue una funcion: fue el script, con su import,
    su parseo de argumentos y su formateo. Importar `main()` habria pasado por
    arriba justamente del pedazo que estaba roto. Se corre lo mismo que corre
    el cron.

    QUE AFIRMA, y por que no solo el exit code

    El exit code atrapa el KeyError. Pero un informe que pierde una seccion
    entera -- porque alguien renombro una clave del resumen -- sale con exit 0
    y sin nada. Asi que ademas se exige que las CUATRO secciones esten, y que
    no aparezca ninguno de los nombres viejos."""
    import subprocess
    import sys as _sys

    print("\n=== EL INSTRUMENTO, de punta a punta ===")

    origen = os.path.join(RAIZ, "local.db")
    if not os.path.exists(origen):
        print("   --  sin local.db, se saltea")
        return

    import shutil
    import sqlite3
    import tempfile
    tmp = tempfile.mkdtemp(prefix="regla_recon_")
    copia = os.path.join(tmp, "prueba.db")
    shutil.copy(origen, copia)

    # SE SIEMBRA. Con la base vacia de movimientos, el informe imprimiria
    # ceros y no probaria el camino que formatea detalles.
    db = sqlite3.connect(copia)
    db.execute("INSERT OR REPLACE INTO newstocks_cidef (id, vin, patente, "
               " clientecompleto, despachado, updated_at) VALUES "
               " (999001,'VINRECON00000001','RR1122','PRUEBA','STOCK',"
               "  '2026-09-01 10:00:00')")
    db.execute("""CREATE TABLE IF NOT EXISTS movimientos_regla (
        id INTEGER PRIMARY KEY, unidad_id INTEGER, vin TEXT, paso TEXT,
        paso_recomendado TEXT, es_desvio INTEGER, motivo TEXT,
        motivo_detalle TEXT, resultado_pdi TEXT, guia_ingreso TEXT,
        fecha TEXT, responsable TEXT, usuario TEXT, creado_en TEXT,
        estado_desde TEXT, estado_hacia TEXT, patio TEXT, calle TEXT)""")
    db.execute("INSERT INTO movimientos_regla (unidad_id, vin, paso, "
               " es_desvio, usuario, creado_en, estado_desde, estado_hacia) "
               " VALUES (999001,'VINRECON00000001','stock',0,'1',"
               "  '2026-09-01T11:00:00','STOCK','INGRESO A TALLER')")
    db.commit()
    db.close()

    entorno = dict(os.environ)
    entorno["DB_PATH"] = copia
    entorno["DATA_DIR"] = tmp
    entorno["SECRET_KEY"] = "prueba"
    # Sin correo y sin guardar: la prueba no manda mails ni ensucia la tabla.
    r = subprocess.run(
        [_sys.executable, os.path.join(RAIZ, "scripts", "reconciliar.py"),
         "--sin-correo", "--sin-guardar"],
        capture_output=True, text=True, env=entorno, timeout=300)

    salida = (r.stdout or "") + (r.stderr or "")
    afirmar(r.returncode == 0,
            "`reconciliar.py` corre de punta a punta sin reventar",
            "exit={}  {}".format(r.returncode,
                                 (r.stderr or "").strip().splitlines()[-1:]))
    if r.returncode != 0:
        for linea in (r.stderr or "").strip().splitlines()[-6:]:
            print("        {}".format(linea))

    # LAS CUATRO SECCIONES. Si una desaparece porque alguien renombro una
    # clave del resumen, el script sale con exit 0 y sin ella.
    for seccion in ("ESTADOS", "ESTADO SIN FILA", "PDI SIN SU OT",
                    "DAÑOS CORTADOS"):
        afirmar(seccion in salida,
                "el informe trae la seccion {!r}".format(seccion))

    # LAS CATEGORIAS QUE EL MODULO PRODUCE HOY, con su rotulo. Si el script
    # vuelve a escribir una lista propia, esta afirmacion la agarra.
    from modulos.reconciliacion import CATEGORIAS, ROTULOS
    faltan = [c for c in CATEGORIAS if ROTULOS[c] not in salida]
    afirmar(not faltan,
            "el informe imprime las {} categorias vigentes".format(
                len(CATEGORIAS)),
            "faltaron: {}".format(faltan) if faltan else "")

    # Y LAS VIEJAS NO. Es la regresion exacta del 2026-09-04.
    viejas = [v for v in ("de_acuerdo", "regla_adelante", "legado_adelante",
                          "contradiccion") if v in salida]
    afirmar(not viejas,
            "no quedo ningun nombre de categoria de antes del 2026-08-27",
            "aparecieron: {}".format(viejas) if viejas else "")


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

    # EL INSTRUMENTO ENTERO VA ANTES DEL RECUENTO. Puesto despues del
    # `return 1` solo corria cuando ya estaba todo bien -- que es justo cuando
    # no hace falta -- y sus fallas no se contaban.
    probar_el_instrumento_entero()

    print("\n" + "=" * 62)
    if fallos:
        print("FALLARON {}:".format(len(fallos)))
        for f in fallos:
            print("  - " + f)
        return 1
    print("la clasificacion manda cada caso a su cajon, y el informe corre "
          "entero")
    return 0


if __name__ == "__main__":
    sys.exit(main())
