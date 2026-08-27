"""
modulos/reconciliacion.py -- el sensor diario de divergencia entre REGLA y el
sistema anterior.

POR QUE EXISTE, Y POR QUE COMPARA ESTADOS Y NO HISTORIALES
==========================================================

El sistema anterior mueve unidades sin dejar rastro. Contadas las escrituras a
`newstocks_cidef.despachado` en los dos controladores vivos: 111 en 24
funciones, y **58 de ellas no escriben la fila de `registros`** -- entre ellas
cuatro grillas de administracion (`proces_patio`, `proces_edit_2`,
`proces_con5tablas`, `proces_u_u`, 27 escrituras).

Para esas, el historial no sirve como sensor: no hay historial. **La diferencia
de columna es la unica señal que existe.** Por eso esto compara el ESTADO, no
la lista de movimientos.

Medido sobre la replica antes de empezar el paralelo: de las 51.623 unidades
con historial en `registros`, **9.485 (18,4%) tienen un estado que no coincide
con su propio ultimo movimiento del legado**. O sea que el problema no es una
hipotesis.


LAS TRES CATEGORIAS, Y COMO SE DISTINGUEN
=========================================

No alcanza con contar diferencias: una diferencia esperada y una contradiccion
se ven igual en un numero. Se clasifican con `estado_desde`, que es el dato que
REGLA guarda en cada movimiento y que dice de donde venia la unidad:

  (1) REGLA ADELANTE -- el legado esta exactamente en `estado_desde`, o sea
      donde la unidad estaba ANTES de que REGLA la moviera. Nosotros hicimos el
      paso y el legado todavia no se entero. Es lo esperado mientras el push no
      cubra ese paso, y es la categoria que TIENE QUE CAER cuando un enlace
      nuevo entra en produccion.

  (2) EL LEGADO ADELANTE -- REGLA empujo su cambio y el legado lo confirmo (la
      cola quedo resuelta y `push_pendiente` en 0), pero hoy el legado esta en
      otro estado. O sea que administracion trabajo la unidad DESPUES que
      nosotros. No es un error: es trabajo que REGLA todavia no vio.

  (3) CONTRADICCION -- lo que no encaja en ninguna de las dos: un push que
      fallo o quedo pendiente, un conflicto registrado, o dos estados que
      ninguna de las dos historias explica. **Es la unica que se mira a mano.**

Las unidades sin movimientos en REGLA quedan fuera: ahi el legado es la unica
fuente y no hay dos versiones que comparar.


ESTA ES LA PRUEBA DE ACEPTACION DE CADA ENLACE
==============================================

Cuando entre el push de movimientos, **la categoria 1 tiene que caer**. Si no
cae, el enlace no funciono -- por mas que las pruebas pasen y el endpoint
devuelva 200. Lo mismo para PDI y para cualquier entidad que se agregue
despues.

Por eso la medicion BASE se saca antes de empezar el paralelo: sin punto de
partida no hay forma de distinguir un numero malo de uno esperable.


EL SENSOR DE PLATA
==================

Aparte de los estados, se cuentan las PDI sin su OT de PDI. Una PDI sin OT es
una PDI sin cobrar, y no puede vivir en un listado que alguien mira cuando se
acuerda: dispara correo por el mismo camino de Resend que la alerta de
conflictos.

Base medida por año de `fecha_pdi`: 2021 y 2022 dan 99,9% y 97,5% sin OT
porque la OT automatica no existia todavia -- eso es historia, no fuga. Desde
2023 la tasa real es 0,4% a 2,4%, y 2026 va en **88 de 3.615**. Ese es el
numero a vigilar.
"""

import json
import os
from datetime import datetime

from flask import Blueprint, render_template

from core import conectar_db, get_db

bp = Blueprint("reconciliacion", __name__)

# Solo se avisa por correo de las PDI sin OT desde que la OT automatica existe.
# Antes de esta fecha el 98% no la tiene y avisar de eso seria ruido puro.
DESDE_OT_AUTOMATICA = "2023-01-01"

DESTINATARIOS = "SYNC_RECONCILIACION_DESTINATARIOS"


def asegurar_tabla(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS reconciliacion (
            id          INTEGER PRIMARY KEY,
            corrida_en  TEXT NOT NULL,
            resumen_json TEXT NOT NULL
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_reconciliacion_fecha "
               "ON reconciliacion (corrida_en)")


# ---------------------------------------------------------------------------
# Los estados
# ---------------------------------------------------------------------------

def _clasificar_una(fila, normalizar_estado):
    """(categoria, detalle) para una unidad que REGLA toco."""
    crudo = normalizar_estado(fila["despachado"])
    hacia = normalizar_estado(fila["estado_hacia"])
    desde = normalizar_estado(fila["estado_desde"])

    if not hacia:
        return "sin_arco", None
    if crudo == hacia:
        return "de_acuerdo", None
    if desde and crudo == desde:
        # El legado sigue donde la unidad estaba antes de que la movieramos.
        return "regla_adelante", None
    if fila["push_confirmado"]:
        # Empujamos, el legado lo tomo, y despues se movio por su cuenta.
        return "legado_adelante", None
    return "contradiccion", {
        "unidad": fila["id"], "vin": fila["vin"],
        "legado": fila["despachado"], "regla": fila["estado_hacia"],
        "desde": fila["estado_desde"], "paso": fila["paso"],
        "movimiento_en": fila["creado_en"],
    }


def comparar_estados(db):
    """Clasifica todas las unidades que REGLA toco."""
    from modulos.movimientos import normalizar_estado

    filas = db.execute("""
        SELECT n.id, n.vin, n.despachado, n.push_pendiente,
               m.paso, m.estado_desde, m.estado_hacia, m.creado_en,
               (SELECT COUNT(*) FROM sync_push_pendientes p
                 WHERE p.legado_id = n.id AND p.resuelto_en <> ''
                   AND p.ultimo_error = '') AS empujes_ok
          FROM newstocks_cidef n
          JOIN (SELECT unidad_id, MAX(id) AS mid FROM movimientos_regla
                 GROUP BY unidad_id) u ON u.unidad_id = n.id
          JOIN movimientos_regla m ON m.id = u.mid
    """).fetchall()

    conteo = {"de_acuerdo": 0, "regla_adelante": 0, "legado_adelante": 0,
              "contradiccion": 0, "sin_arco": 0}
    detalles = []
    for f in filas:
        d = dict(f)
        d["push_confirmado"] = bool(f["empujes_ok"]) and not f["push_pendiente"]
        categoria, detalle = _clasificar_una(d, normalizar_estado)
        conteo[categoria] += 1
        if detalle:
            detalles.append(detalle)
    return {"conteo": conteo, "contradicciones": detalles[:50],
            "unidades_miradas": len(filas)}


def estados_sin_registro(db):
    """Unidades cuyo estado NO coincide con su propio ultimo movimiento del
    legado. Es el sensor de las 58 escrituras que no dejan fila.

    Mira TODAS las unidades, las haya tocado REGLA o no: es la unica señal de
    esa clase de cambio, y afecta sobre todo a las que REGLA nunca vio."""
    from modulos.movimientos import normalizar_estado

    filas = db.execute("""
        SELECT n.id, n.despachado, r.estado AS ultimo
          FROM newstocks_cidef n
          JOIN (SELECT vin, MAX(id) mid FROM registros GROUP BY vin) u
            ON u.vin = n.vin
          JOIN registros r ON r.id = u.mid
         WHERE n.vin <> ''
    """).fetchall()
    total = difieren = 0
    for f in filas:
        total += 1
        if normalizar_estado(f["despachado"]) != normalizar_estado(f["ultimo"]):
            difieren += 1
    return {"con_historial": total, "estado_no_coincide": difieren,
            "porcentaje": round(100.0 * difieren / total, 1) if total else 0.0}


# ---------------------------------------------------------------------------
# El sensor de plata
# ---------------------------------------------------------------------------

def pdi_sin_ot(db, desde=DESDE_OT_AUTOMATICA):
    """PDI registradas en el legado que no tienen su OT de PDI.

    Cada una es una PDI hecha y no cobrada."""
    filas = db.execute("""
        SELECT id, vin, clientecompleto, fecha_pdi
          FROM newstocks_cidef n
         WHERE n.fecha_pdi NOT IN ('', '0000-00-00') AND n.fecha_pdi IS NOT NULL
           AND n.fecha_pdi >= ?
           AND NOT EXISTS (SELECT 1 FROM orden_trabajo o
                            WHERE o.id_vehiculo = n.id
                              AND o.requerimiento = 'PDI')
         ORDER BY n.fecha_pdi DESC
    """, (desde,)).fetchall()
    con_pdi = db.execute(
        "SELECT COUNT(*) FROM newstocks_cidef WHERE fecha_pdi NOT IN "
        "('', '0000-00-00') AND fecha_pdi IS NOT NULL AND fecha_pdi >= ?",
        (desde,)).fetchone()[0]
    return {
        "desde": desde,
        "pdi_totales": con_pdi,
        "sin_ot": len(filas),
        "porcentaje": round(100.0 * len(filas) / con_pdi, 1) if con_pdi else 0.0,
        "detalle": [dict(f) for f in filas[:50]],
    }


# ---------------------------------------------------------------------------
# La corrida
# ---------------------------------------------------------------------------

def correr(db_path=None, guardar=True):
    """Una reconciliacion completa. Devuelve el resumen."""
    db = conectar_db(db_path)
    try:
        asegurar_tabla(db)
        resumen = {
            "corrida_en": datetime.now().isoformat(timespec="seconds"),
            "estados": comparar_estados(db),
            "sin_registro": estados_sin_registro(db),
            "pdi_sin_ot": pdi_sin_ot(db),
        }
        if guardar:
            db.execute(
                "INSERT INTO reconciliacion (corrida_en, resumen_json) VALUES (?,?)",
                (resumen["corrida_en"],
                 json.dumps(resumen, ensure_ascii=False, default=str)))
            db.commit()
        return resumen
    finally:
        db.close()


def ultima(db):
    asegurar_tabla(db)
    f = db.execute("SELECT * FROM reconciliacion ORDER BY id DESC LIMIT 1").fetchone()
    if f is None:
        return None
    d = json.loads(f["resumen_json"])
    d["id"] = f["id"]
    return d


def historico(db, tope=30):
    asegurar_tabla(db)
    filas = db.execute(
        "SELECT id, corrida_en, resumen_json FROM reconciliacion "
        "ORDER BY id DESC LIMIT ?", (tope,)).fetchall()
    salida = []
    for f in filas:
        d = json.loads(f["resumen_json"])
        salida.append({
            "id": f["id"], "corrida_en": f["corrida_en"],
            "conteo": d.get("estados", {}).get("conteo", {}),
            "sin_ot": d.get("pdi_sin_ot", {}).get("sin_ot", 0),
        })
    return salida


# ---------------------------------------------------------------------------
# El aviso
# ---------------------------------------------------------------------------

def avisar(resumen):
    """Manda el correo si hay algo que mirar. Nunca levanta.

    Se avisa por dos motivos y se dicen por separado: las PDI sin cobrar y las
    contradicciones. Una corrida limpia NO manda correo -- un aviso que llega
    todos los dias diciendo que esta todo bien se deja de leer, y entonces el
    dia que dice otra cosa tampoco se lee."""
    from modulos import correo

    sin_ot = resumen["pdi_sin_ot"]["sin_ot"]
    contra = resumen["estados"]["conteo"]["contradiccion"]
    if not sin_ot and not contra:
        correo.log("sin_novedad", "reconciliacion",
                   "nada que avisar: 0 PDI sin OT, 0 contradicciones")
        return "sin_novedad"

    c = resumen["estados"]["conteo"]
    filas_pdi = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            d["id"], d["vin"] or "", d["clientecompleto"] or "", d["fecha_pdi"])
        for d in resumen["pdi_sin_ot"]["detalle"][:20])
    filas_contra = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            d["unidad"], d["vin"] or "", d["legado"] or "", d["regla"] or "")
        for d in resumen["estados"]["contradicciones"][:20])

    html = (
        "<style>.tb{{border-collapse:collapse}}.tb th,.tb td{{padding:5px;"
        "border:1px solid #777;font-family:sans-serif;font-size:13px}}"
        ".tb th{{background:lightblue}}</style>"
        "<h3><b>Reconciliación con el sistema anterior</b></h3>"
        "<p>Corrida del {cuando} desde <b>{origen}</b>.</p>"
        "<p><b>Estados</b> — de acuerdo: {ok} · REGLA adelante: {ra} · "
        "el sistema anterior adelante: {la} · <b>contradicciones: {co}</b></p>"
        "{bloque_contra}"
        "<h4>PDI sin su OT — {sin_ot} de {tot} desde {desde} ({pct}%)</h4>"
        "<p>Cada una es una PDI hecha y no cobrada.</p>"
        '<table class="tb"><tr><th>Unidad</th><th>VIN</th><th>Cliente</th>'
        "<th>Fecha PDI</th></tr>{filas_pdi}</table>"
        "<p>El detalle completo está en la pantalla de Reconciliación.</p>"
        "<p>Este mail fue enviado automaticamente por sistema REGLA</p>"
    ).format(
        cuando=resumen["corrida_en"], origen=correo.origen(),
        ok=c["de_acuerdo"], ra=c["regla_adelante"], la=c["legado_adelante"],
        co=c["contradiccion"],
        bloque_contra=(
            '<h4>Contradicciones ({})</h4><table class="tb"><tr><th>Unidad</th>'
            "<th>VIN</th><th>Sistema anterior</th><th>REGLA</th></tr>{}</table>"
            .format(contra, filas_contra)) if contra else "",
        sin_ot=sin_ot, tot=resumen["pdi_sin_ot"]["pdi_totales"],
        desde=resumen["pdi_sin_ot"]["desde"],
        pct=resumen["pdi_sin_ot"]["porcentaje"], filas_pdi=filas_pdi)

    texto = (
        "Reconciliacion con el sistema anterior — {cuando} — {origen}\n\n"
        "Estados: de acuerdo {ok} | REGLA adelante {ra} | "
        "el anterior adelante {la} | CONTRADICCIONES {co}\n"
        "PDI sin su OT: {sin_ot} de {tot} desde {desde} ({pct}%)\n\n"
        "Las contradicciones son las unicas que hay que mirar a mano."
    ).format(cuando=resumen["corrida_en"], origen=correo.origen(),
             ok=c["de_acuerdo"], ra=c["regla_adelante"],
             la=c["legado_adelante"], co=c["contradiccion"],
             sin_ot=sin_ot, tot=resumen["pdi_sin_ot"]["pdi_totales"],
             desde=resumen["pdi_sin_ot"]["desde"],
             pct=resumen["pdi_sin_ot"]["porcentaje"])

    correo.en_segundo_plano(
        correo.mandar, correo.destinatarios(DESTINATARIOS),
        "Reconciliación — {} contradicciones, {} PDI sin OT".format(contra, sin_ot),
        texto, html)
    return "avisado"


# ---------------------------------------------------------------------------
# La pantalla
# ---------------------------------------------------------------------------

@bp.route("/reconciliacion")
def pantalla():
    """El resultado consultable, ademas del correo.

    El correo avisa cuando hay algo; esta pantalla es donde se mira el detalle
    y la serie. Las dos cosas hacen falta: un aviso sin donde mirar obliga a
    entrar a la base, y una pantalla sin aviso se mira cuando alguien se
    acuerda."""
    db = get_db()
    return render_template("reconciliacion.html",
                           r=ultima(db), serie=historico(db))
