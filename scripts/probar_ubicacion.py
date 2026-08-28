#!/usr/bin/env python3
"""
scripts/probar_ubicacion.py -- patio y calle: catalogo, sugerencia, ventana de
recencia y el camino completo hasta la cola de push.

Base descartable, sin red y sin tocar local.db.

    python scripts/probar_ubicacion.py

Los siete casos:

    1. catalogo         solo calles de estacionamiento, sin las muertas
    2. patio sugerido   origen + cliente, no el patio actual
    3. arranque en frio sin tanda no hay atajos y el orden es por frecuencia
    4. tanda fresca     el movimiento propio reordena y marca la calle
    5. la ventana       pasada la hora la sugerencia SE APAGA, no se atenua
    6. validacion       el POST rechaza vacio, calle inexistente y patio que
                        no estaciona
    7. el indice        el cubridor sigue cubriendo: las dos listas coinciden
    8. el push          patio y calle llegan al payload como los eligio el
                        movilizador, no como los dedujo una tabla

EL CASO 5 ES EL QUE PROTEGE LA DECISION DE DISEÑO. La sugerencia acierta 76%
dentro de la hora y ~50% despues; si alguien "mejora" esto haciendo que la
tanda no expire, la pantalla va a seguir andando y va a seguir sugiriendo, solo
que la mitad de las veces mal. No hay forma de notarlo mirando la pantalla.

EL CASO 7 PROTEGE LA OTRA. `calle` y `patio` explicitos tienen que GANARLE a
CALLE_POR_ESTADO / PATIO_POR_ESTADO: son la respuesta del movilizador contra
una inferencia. Si el orden se invierte, STOCK empieza a empujar la calle de la
mayoria -- 25% de acierto -- escrita en el historial del legado como un hecho.
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

os.environ.setdefault("SECRET_KEY", "prueba")
os.environ.setdefault("LEGADO_API_KEY", "clave-de-prueba")
os.environ["PUSH_LEGADO_ACTIVO"] = "0"

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


UNIDAD_ID = 91522


def base():
    """Lo minimo: una unidad CIDEF parada en PATIO 2, que es el caso de mas
    volumen (1.373 movimientos a STOCK en el semestre)."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE newstocks_cidef (
        id INTEGER PRIMARY KEY, vin TEXT, clientecompleto TEXT,
        patio TEXT, calle TEXT, despachado TEXT, updated_at TEXT,
        push_pendiente INTEGER DEFAULT 0)""")
    db.execute("INSERT INTO newstocks_cidef VALUES (?,?,?,?,?,?,?,0)",
               (UNIDAD_ID, "LVAV2JVB8TE322643", "CIDEF", "PATIO 2", "Pdi",
                "EN ESPERA DYP CONSOLIDADO", "2026-08-27 10:00:00"))
    db.execute("""CREATE TABLE movimientos_regla (
        id INTEGER PRIMARY KEY, unidad_id INTEGER, usuario TEXT,
        creado_en TEXT, patio TEXT, calle TEXT)""")
    return db


def anotar(db, patio, calle, usuario="47", hace_segundos=0):
    cuando = datetime.now() - timedelta(seconds=hace_segundos)
    db.execute("""INSERT INTO movimientos_regla
                    (unidad_id, usuario, creado_en, patio, calle)
                  VALUES (?,?,?,?,?)""",
               (UNIDAD_ID, usuario, cuando.isoformat(timespec="seconds"),
                patio, calle))
    db.commit()


def main():
    from modulos import ubicacion

    db = base()
    unidad = db.execute("SELECT * FROM newstocks_cidef WHERE id=?",
                        (UNIDAD_ID,)).fetchone()

    # ------------------------------------------------------------------
    paso("1. el catalogo son las calles de estacionamiento vivas")

    afirmar("PATIO 1" not in ubicacion.PATIOS,
            "PATIO 1 no esta: no se estaciona ahi, su menu no ofrece letras")
    todas = set()
    for p in ubicacion.PATIOS:
        todas.update(c for c, _ in ubicacion.CALLES_POR_PATIO[p])

    for muerta in ("X", "Ñ", "K", "L", "N", "Z"):
        afirmar(muerta not in todas,
                "{!r} no esta: 0 movimientos en 6 meses".format(muerta))
    for etapa in ("Cc", "Zd", "PDI", "Lavando", "Cmp3", "Servicios Generales"):
        afirmar(etapa not in todas,
                "{!r} no esta: es una etapa, no un lugar".format(etapa))
    afirmar(ubicacion.valida("PATIO 5", "C"), "PATIO 5 - C es valida")
    afirmar(not ubicacion.valida("PATIO 1", "A"),
            "PATIO 1 - A NO es valida: PATIO 1 no estaciona")
    afirmar(not ubicacion.valida("PATIO 3", "X"),
            "PATIO 3 - X NO es valida: el switch del legado no la contempla")

    # ------------------------------------------------------------------
    paso("2. el patio sale de origen + cliente, no del patio actual")

    afirmar(ubicacion.patio_sugerido(unidad) == "PATIO 5",
            "CIDEF desde PATIO 2 -> PATIO 5 (58%), no PATIO 2",
            ubicacion.patio_sugerido(unidad))

    carflex = dict(unidad)
    carflex["clientecompleto"] = "CARFLEX | "
    afirmar(ubicacion.patio_sugerido(carflex) == "PATIO 2",
            "CARFLEX desde PATIO 2 -> PATIO 2 (92%)")
    raro = dict(unidad)
    raro["clientecompleto"] = "OTRO CLIENTE"
    raro["patio"] = "PATIO 9"
    afirmar(ubicacion.patio_sugerido(raro) == ubicacion.PATIO_POR_DEFECTO,
            "un par sin medicion cae al de mas volumen, no explota")

    # ------------------------------------------------------------------
    paso("3. arranque en frio: sin tanda, sin atajos")

    s = ubicacion.sugerencia(db, unidad, "47")
    afirmar(s["fresco"] is False, "no hay tanda")
    afirmar(s["atajos"] == [], "y por lo tanto no hay atajos")
    afirmar([c for c, _ in s["calles"]["PATIO 5"]["a_la_vista"]][:4]
            == ["C", "B", "A", "D"],
            "las calles van por frecuencia del semestre",
            s["calles"]["PATIO 5"]["a_la_vista"])
    afirmar(s["calles"]["PATIO 5"]["reciente"] is None,
            "ninguna se marca como reciente")
    afirmar(len(s["calles"]) == len(ubicacion.PATIOS),
            "vienen los cuatro patios, para no ir al servidor al cambiar")

    # ------------------------------------------------------------------
    paso("4. tanda fresca: reordena y marca")

    anotar(db, "PATIO 5", "D", hace_segundos=120)
    s = ubicacion.sugerencia(db, unidad, "47")
    afirmar(s["fresco"] is True, "hay tanda")
    afirmar(s["atajos"] == [("PATIO 5", "D")], "el atajo es el par recien usado")
    afirmar([c for c, _ in s["calles"]["PATIO 5"]["a_la_vista"]][0] == "D",
            "la D pasa a encabezar aunque es la 4a por frecuencia")
    afirmar(s["calles"]["PATIO 5"]["reciente"] == "D", "y queda marcada")
    afirmar(s["patio"] == "PATIO 5", "el patio sigue saliendo de la medicion")

    # el patio NO se toma de la tanda: si el movilizador viene estacionando
    # CIDEF y la unidad de adelante es CARFLEX, la tanda lo mandaria mal.
    s_cf = ubicacion.sugerencia(db, carflex, "47")
    afirmar(s_cf["patio"] == "PATIO 2",
            "una unidad CARFLEX no hereda el PATIO 5 de la tanda CIDEF",
            s_cf["patio"])

    # ------------------------------------------------------------------
    paso("5. la ventana de recencia: se apaga, no se atenua")

    despues = datetime.now() + timedelta(
        seconds=ubicacion.VENTANA_RECENCIA_SEGUNDOS + 60)
    s = ubicacion.sugerencia(db, unidad, "47", ahora=despues)
    afirmar(s["fresco"] is False, "pasada la ventana ya no hay tanda")
    afirmar(s["atajos"] == [], "y los atajos desaparecen, no se atenuan")
    afirmar([c for c, _ in s["calles"]["PATIO 5"]["a_la_vista"]][:4]
            == ["C", "B", "A", "D"],
            "el orden vuelve a frecuencia")
    afirmar(s["calles"]["PATIO 5"]["reciente"] is None,
            "y la marca de reciente se va: 76% adentro, ~50% afuera")

    # El borde se mide desde el MOVIMIENTO, no desde ahora: la fila se anoto
    # con 120 s de antiguedad, asi que hay que descontarlos o el "justo antes"
    # cae afuera igual y la prueba miente en la direccion comoda.
    justo_antes = datetime.now() + timedelta(
        seconds=ubicacion.VENTANA_RECENCIA_SEGUNDOS - 120 - 60)
    afirmar(ubicacion.sugerencia(db, unidad, "47", ahora=justo_antes)["fresco"],
            "un minuto antes del corte todavia esta fresca")

    # ------------------------------------------------------------------
    paso("6. el POST valida, y rechaza en vez de inventar")

    from modulos import movimientos
    afirmar("stock" in movimientos.PASOS_CON_UBICACION,
            "el paso 'stock' pide ubicacion")
    for patio, calle, por_que in (
            ("", "", "vacio"),
            ("PATIO 5", "", "sin calle"),
            ("PATIO 5", "X", "calle que no existe en el legado"),
            ("PATIO 1", "A", "patio donde no se estaciona"),
            ("PATIO 5", "c", "minuscula: el legado guarda mayuscula")):
        afirmar(not ubicacion.valida(patio, calle),
                "rechaza {!r} / {!r} -- {}".format(patio, calle, por_que))

    # ------------------------------------------------------------------
    paso("7. el indice cubridor sigue cubriendo")

    # UN INDICE CUBRIDOR SON DOS LISTAS QUE TIENEN QUE COINCIDIR. Cubre solo
    # mientras las columnas del SELECT sean subconjunto de las del indice; si
    # alguien agrega una al SELECT, SQLite vuelve a la tabla de 382 MB y la
    # busqueda pasa de 18 ms a un segundo. Sin error, sin log, sin nada.
    #
    # Hoy las dos se arman de la misma constante y no pueden separarse, pero
    # esto lo afirma igual: la proxima persona puede volver a escribir el
    # SELECT a mano, que es como empezo.
    from core import BUSQUEDA_DEVUELVE, BUSQUEDA_FILTRA, INDICES

    ddl = INDICES["ix_newstocks_busqueda"]
    del_indice = set(
        c.strip() for c in ddl[ddl.index("(") + 1:ddl.rindex(")")].split(","))
    faltan = (set(BUSQUEDA_DEVUELVE) | set(BUSQUEDA_FILTRA)) - del_indice
    afirmar(not faltan,
            "el indice cubre TODAS las columnas del SELECT y del WHERE",
            "faltan en el indice: " + ", ".join(sorted(faltan)))

    # Y que el SQL que se manda de verdad sea el que se armo con esas listas.
    from modulos import movimientos as M
    sql_visto = []
    real = M.consultar

    def espia(sql, params=(), una=False):
        sql_visto.append(sql)
        return []

    M.consultar = espia
    try:
        M._buscar("316254")
    finally:
        M.consultar = real
    sql = " ".join(sql_visto)
    afirmar("INDEXED BY ix_newstocks_busqueda" in sql,
            "la busqueda fuerza el indice -- el planificador no lo elige solo")
    for columna in BUSQUEDA_DEVUELVE:
        afirmar(columna in sql,
                "el SELECT lleva {!r}, como el indice".format(columna))

    # ------------------------------------------------------------------
    paso("8. el push manda lo que el movilizador eligio")

    import json
    from modulos import push_legado

    push_legado.asegurar_tablas(db)
    afirmar("STOCK" not in push_legado.SIN_CALLE,
            "STOCK ya no esta excluido")

    id_cola = push_legado.encolar_movimiento(
        db, unidad, 1, "STOCK", "47", calle="D", patio="PATIO 5")
    afirmar(id_cola is not None, "STOCK se encola")
    campos = json.loads(db.execute(
        "SELECT campos_json FROM sync_push_pendientes WHERE id=?",
        (id_cola,)).fetchone()[0])
    afirmar(campos["accion"] == "D",
            "la calle es la elegida, no la mayoritaria", campos["accion"])
    afirmar(campos["patio"] == "PATIO 5",
            "y el patio tambien", campos["patio"])
    afirmar(campos["estado"] == "STOCK", "el estado destino viaja igual")

    # Y los que NO pasan ubicacion siguen saliendo de las tablas.
    id2 = push_legado.encolar_movimiento(
        db, unidad, 2, "CONTROL DE CALIDAD DESPACHO", "47")
    c2 = json.loads(db.execute(
        "SELECT campos_json FROM sync_push_pendientes WHERE id=?",
        (id2,)).fetchone()[0])
    afirmar(c2["accion"] == "Cc" and c2["patio"] == "PATIO 1",
            "sin ubicacion explicita siguen mandando las tablas", c2)

    afirmar(push_legado.encolar_movimiento(db, unidad, 3, "DESPACHADO", "47")
            is None,
            "DESPACHADO sigue sin empujarse: manda correo y crea OT")

    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    if fallos:
        print("FALLARON {} comprobaciones:".format(len(fallos)))
        for f in fallos:
            print("  - {}".format(f))
        return 1
    print("los 8 casos de ubicacion pasaron")
    return 0


if __name__ == "__main__":
    sys.exit(main())
