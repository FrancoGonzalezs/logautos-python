"""
modulos/avisos.py -- los correos que REGLA le debe a alguien, en una cola.

POR QUE UNA COLA Y NO UN `mandar()` Y LISTO

`correo.mandar()` ya no levanta excepciones, asi que un Resend caido nunca
tumbo un guardado. Pero "no tumbar" no es lo mismo que "no perder": hasta hoy,
un correo que fallaba se perdia -- quedaba una linea en el log y nada mas.

Para la inspeccion de despacho eso no alcanza, y el motivo es de este modulo y
no general: **`controldespachos@logautos.cl` es el UNICO registro interno de
que la inspeccion se hizo.** El PDF con las fotos sale despues, en el despacho,
y va a otra gente. Si el aviso se pierde, adentro de Logautos no queda rastro
del momento en que la unidad se inspecciono.

Asi que el aviso se ENCOLA en la misma transaccion que la inspeccion, igual
que el push, y se reintenta en la misma vuelta del hilo de fondo.

EL ORDEN IMPORTA Y ES EL MISMO DE SIEMPRE

    1. la fila local
    2. la cola del push        (la fila del legado y las tres columnas)
    3. la cola del aviso       (este modulo)

Los tres en el MISMO commit. Si el proceso muere entre el 1 y el 2, no hay
inspeccion; si muere entre el 2 y el 3, hay inspeccion sin aviso -- y eso es
exactamente lo que la cola viene a impedir, porque estan en la misma
transaccion.

EL CORREO NUNCA VA PRIMERO. Un aviso de algo que despues no se guardo es peor
que no avisar: alguien lee que la inspeccion se hizo y no esta.
"""

import json
from datetime import datetime, timedelta

from core import conectar_db, consultar, get_db

# El backoff, mas corto que el del push a proposito: un correo que sale con
# veinte minutos de atraso sigue sirviendo; uno que sale al dia siguiente ya no.
ESPERAS_SEGUNDOS = (60, 300, 900, 3600, 21600)

# Despues de agotarlas, el aviso queda con `agotado = 1` y deja de intentarse.
# NO se borra: la fila es la evidencia de que alguien tiene que mirar, y la
# reconciliacion la cuenta.
MAXIMO_INTENTOS = len(ESPERAS_SEGUNDOS)


def _asegurar_tabla(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS avisos_pendientes_regla (
          id INTEGER PRIMARY KEY,
          modulo TEXT NOT NULL,
          referencia_id INTEGER,        -- la fila que lo origino
          destinatarios TEXT NOT NULL,  -- JSON, una lista
          remitente TEXT,
          responder_a TEXT,
          asunto TEXT NOT NULL,
          texto TEXT,
          html TEXT,
          intentos INTEGER NOT NULL DEFAULT 0,
          proximo_intento TEXT,
          ultimo_error TEXT,
          agotado INTEGER NOT NULL DEFAULT 0,
          creado_en TEXT,
          enviado_en TEXT
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_avisos_pendientes "
               "ON avisos_pendientes_regla (enviado_en, proximo_intento)")

    # `adjuntos` llego con el IT, cuyo correo lleva las fotos INCRUSTADAS.
    # `ALTER TABLE ADD COLUMN` y no un CREATE nuevo: la tabla ya existe en
    # Railway con avisos adentro, y recrearla los perderia.
    cols = {r[1] for r in db.execute(
        "PRAGMA table_info(avisos_pendientes_regla)")}
    if "adjuntos" not in cols:
        db.execute("ALTER TABLE avisos_pendientes_regla "
                   "ADD COLUMN adjuntos TEXT")


def encolar(db, modulo, referencia_id, destinatarios, asunto, texto, html,
            remitente=None, responder_a=None, adjuntos=()):
    """Deja el aviso listo para salir. NO lo manda.

    Recibe `db` en vez de abrirlo: tiene que escribirse en la MISMA
    transaccion que la fila que lo origina. Ver el encabezado."""
    _asegurar_tabla(db)
    ahora = datetime.now()
    cur = db.execute(
        "INSERT INTO avisos_pendientes_regla "
        "  (modulo, referencia_id, destinatarios, remitente, responder_a, "
        "   asunto, texto, html, adjuntos, proximo_intento, creado_en) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (modulo, referencia_id,
         json.dumps(list(destinatarios), ensure_ascii=False),
         remitente, responder_a, asunto, texto, html,
         # Las RUTAS, no los bytes. Un aviso con seis fotos de 51 KB en base64
         # serian ~400 KB por fila en una tabla que se consulta seguido, y las
         # fotos ya estan guardadas: duplicarlas para reintentar es guardar dos
         # veces lo mismo por si acaso.
         json.dumps(list(adjuntos), ensure_ascii=False) if adjuntos else None,
         ahora.isoformat(timespec="seconds"),
         ahora.isoformat(timespec="seconds")))
    return cur.lastrowid


def procesar(db_path=None, limite=20):
    """Una vuelta sobre los avisos vencidos. Devuelve un resumen.

    La llama el mismo hilo de fondo que el push, en la misma vuelta. Abre su
    propia conexion: corre sin contexto de Flask."""
    from modulos import correo

    db = conectar_db(db_path)
    try:
        _asegurar_tabla(db)
        db.commit()
        ahora = datetime.now().isoformat(timespec="seconds")
        filas = db.execute(
            "SELECT * FROM avisos_pendientes_regla "
            " WHERE enviado_en IS NULL AND agotado = 0 "
            "   AND (proximo_intento IS NULL OR proximo_intento <= ?) "
            " ORDER BY id LIMIT ?", (ahora, limite)).fetchall()

        resumen = {"intentados": 0, "enviados": 0, "errores": 0, "agotados": 0}
        for f in filas:
            resumen["intentados"] += 1
            # Las claves de la fila se leen con `keys()` porque `adjuntos`
            # es una columna que llego despues: una base todavia sin migrar
            # --o una fila vieja-- no la tiene, y `f["adjuntos"]` reventaria.
            crudos = (f["adjuntos"] if "adjuntos" in f.keys() else None) or "[]"
            adjuntos = json.loads(crudos)
            estado, detalle = correo.mandar(
                json.loads(f["destinatarios"] or "[]"),
                f["asunto"], f["texto"] or "", f["html"] or "",
                remitente=f["remitente"], responder_a=f["responder_a"],
                adjuntos=adjuntos)

            if estado == "enviado":
                db.execute(
                    "UPDATE avisos_pendientes_regla SET enviado_en = ?, "
                    "       ultimo_error = '' WHERE id = ?",
                    (datetime.now().isoformat(timespec="seconds"), f["id"]))
                resumen["enviados"] += 1
                continue

            # `no_configurado` cuenta como error a proposito: si falta la clave
            # de Resend, el aviso NO salio, y disfrazarlo de exito seria la
            # misma mentira que un 200 con cero efecto.
            intentos = (f["intentos"] or 0) + 1
            if intentos >= MAXIMO_INTENTOS:
                db.execute(
                    "UPDATE avisos_pendientes_regla SET intentos = ?, "
                    "       ultimo_error = ?, agotado = 1 WHERE id = ?",
                    (intentos, detalle[:500], f["id"]))
                resumen["agotados"] += 1
            else:
                espera = ESPERAS_SEGUNDOS[min(intentos - 1,
                                              len(ESPERAS_SEGUNDOS) - 1)]
                proximo = (datetime.now() + timedelta(seconds=espera))
                db.execute(
                    "UPDATE avisos_pendientes_regla SET intentos = ?, "
                    "       ultimo_error = ?, proximo_intento = ? WHERE id = ?",
                    (intentos, detalle[:500],
                     proximo.isoformat(timespec="seconds"), f["id"]))
                resumen["errores"] += 1
        db.commit()
        return resumen
    finally:
        db.close()


def pendientes(db=None):
    """Para la reconciliacion: cuantos avisos deben y cuantos se agotaron."""
    sql = ("SELECT "
           " SUM(CASE WHEN enviado_en IS NULL AND agotado = 0 THEN 1 ELSE 0 END) pendientes,"
           " SUM(CASE WHEN agotado = 1 THEN 1 ELSE 0 END) agotados,"
           " SUM(CASE WHEN enviado_en IS NOT NULL THEN 1 ELSE 0 END) enviados"
           " FROM avisos_pendientes_regla")
    if db is None:
        d = get_db()
        _asegurar_tabla(d)
        d.commit()
        f = consultar(sql, una=True)
    else:
        _asegurar_tabla(db)
        f = db.execute(sql).fetchone()
    return {"pendientes": (f["pendientes"] or 0) if f else 0,
            "agotados": (f["agotados"] or 0) if f else 0,
            "enviados": (f["enviados"] or 0) if f else 0}
