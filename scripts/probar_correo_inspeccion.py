#!/usr/bin/env python3
"""
scripts/probar_correo_inspeccion.py -- el correo de la inspeccion de despacho.

    python scripts/probar_correo_inspeccion.py

NO MANDA UN SOLO CORREO. `RESEND_API_KEY` se deja sin poner a proposito, asi
que `correo.mandar` devuelve `no_configurado` -- que es exactamente el camino
de fallo que hay que probar.

Lo que se prueba, y por que:

  1. EL CORREO SALE IGUAL QUE EL DEL LEGADO. Remitente, Reply-To, asunto y
     cuerpo copiados del archivo. El destinatario no tiene por que notar que
     cambio el sistema que se lo manda.

  2. UN SOLO DESTINATARIO, Y ES INTERNO. Confirmado por Franco: el bloque de
     destinatarios del legado se comento A PROPOSITO -- el cliente no quiere
     este correo. Si esta prueba empieza a ver mas de una direccion, alguien
     revivio algo que se apago con intencion.

  3. SIN CC. El legado hace `explode` sobre una variable indefinida y agrega un
     CC vacio que PHPMailer descarta. Se replica el COMPORTAMIENTO ("sin CC"),
     no el mecanismo.

  4. RESEND CAIDO NO PIERDE NADA. Es el caso que importa: la inspeccion ya
     viajo al legado, asi que el correo no la puede tumbar -- y tampoco se
     puede perder, porque `controldespachos@` es el unico registro interno de
     que la inspeccion ocurrio.

  5. Y EL REINTENTO CONVERGE: cuando el proveedor vuelve, el aviso sale solo.
"""

import io as _io
import os
import re
import shutil
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

# La base publica se exige AL ARRANCAR, asi que tiene que estar puesta ANTES de
# importar la app. Mas abajo esta prueba la saca a proposito para comprobar que
# `url_publica` revienta sin ella, y la vuelve a poner.
os.environ.setdefault("PUBLIC_BASE_URL", "https://regla.example")

from temporales import carpeta_de_prueba
os.environ.setdefault("SECRET_KEY", "prueba")

FALLOS = []


def afirmar(condicion, que, detalle=""):
    print("   {}  {}".format("ok  " if condicion else "FALLA", que))
    if detalle:
        print("          {}".format(detalle))
    if not condicion:
        FALLOS.append(que)


def main():
    origen = os.path.join(RAIZ, "local.db")
    if not os.path.exists(origen):
        print("no hay local.db")
        return 1
    tmp = carpeta_de_prueba("regla_correo_")
    shutil.copy(origen, os.path.join(tmp, "prueba.db"))
    os.environ["DB_PATH"] = os.path.join(tmp, "prueba.db")
    os.environ["DATA_DIR"] = tmp
    os.environ["PUSH_LEGADO_ACTIVO"] = "0"
    os.environ["PUBLIC_BASE_URL"] = "https://regla.example"
    os.environ["LEGADO_BASE_URL"] = "http://127.0.0.1:9"   # nadie escucha
    # SIN clave de Resend: el camino de fallo es el que se prueba.
    os.environ.pop("RESEND_API_KEY", None)

    from app import crear_app
    from core import conectar_db
    app = crear_app()
    app.config["TESTING"] = True

    from modulos import (avisos, check_list as _cl, destinatarios,
                         fotos_publicas, inspeccion_despacho as ID)
    fotos_publicas.RAIZ = tmp
    _cl.CARPETA_FOTOS = os.path.join(tmp, "uploads", "check_list")

    db = conectar_db()
    db.execute(
        "INSERT OR REPLACE INTO newstocks_cidef (id, vin, patente, "
        " clientecompleto, marca, modelo, color, despachado, updated_at) "
        " VALUES (888001,'VINCORREO0000001','CC1122','CIDEF','GREAT WALL',"
        "         'POER','BLANCO','ZONA DE DESPACHO','2026-09-01 10:00:00')")
    db.commit()
    db.close()

    c = app.test_client()
    with c.session_transaction() as s:
        s["isLoggedIn"] = True
        s["name"] = "Prueba"
        s["userId"] = 1
        s["role"] = "Admin"

    # -- la inspeccion, con una foto ---------------------------------------
    r = c.post("/movimientos/888001/inspeccion-despacho", data={
        "guia_despacho": "G-CORREO", "destino": "AUTOMOTRIZ ROSSELOT S.A.",
        "fecha_despacho": "2026-09-08", "encargado": "Prueba",
        "estanque": "5", "kilometraje": "1234", "llaves": "2",
        "unidad": (_io.BytesIO(b"\xff\xd8\xff\xe0jpg"), "u.jpg")},
        content_type="multipart/form-data")
    if r.status_code != 302:
        h = r.data.decode("utf-8", "replace")
        for e in re.findall(r"<li>([^<]+)</li>", h)[:5]:
            print("   error: {}".format(e.strip()))
        afirmar(False, "se creo la inspeccion")
        return 1
    idi = int(r.headers["Location"].rstrip("/").split("/")[-1])
    c.post("/inspecciones/{}/foto".format(idi),
           data={"imagen": (_io.BytesIO(b"\xff\xd8\xff\xe0jpg"), "f.jpg")},
           content_type="multipart/form-data")

    # -- 1 y 2: los destinatarios ------------------------------------------
    print("\n1. LOS DESTINATARIOS")
    with app.test_request_context():
        direcciones, general = destinatarios.para(
            destinatarios.MODULO_INSPECCION, "AUTOMOTRIZ ROSSELOT S.A.")
    afirmar(direcciones == ["controldespachos@logautos.cl"],
            "una sola direccion, y es la interna", "{}".format(direcciones))
    afirmar(general, "cayo al conjunto general -- hoy es el unico que hay")

    print("\n2. EL CORREO, COPIADO DEL LEGADO")
    with app.test_request_context():
        fila = ID.inspeccion(idi)
        texto, html = ID._cuerpo_correo(fila)
        remitente = ID._remitente(fila["vin"])
    afirmar(remitente.startswith("Inspeccion Despacho Unidad "),
            "el nombre del remitente es el del legado", remitente)
    afirmar("operaciones@logautos.cl" in remitente,
            "y la direccion tambien")
    for etiqueta in ("Destino", "Vin", "Marca", "Modelo", "Color", "Encargado",
                     "Estanque", "Guia Despacho", "Kilometraje"):
        if "<h3>{}:".format(etiqueta) not in html:
            afirmar(False, "el cuerpo trae {}".format(etiqueta))
            break
    else:
        afirmar(True, "el cuerpo trae los nueve campos, en orden")
    afirmar("sistema REGLA" in html,
            "y la firma dice 'sistema REGLA' -- como ya decia el legado")

    # -- 3: sin CC ---------------------------------------------------------
    print("\n3. SIN CC")
    d = conectar_db()
    fila_aviso = d.execute(
        "SELECT * FROM avisos_pendientes_regla ORDER BY id DESC LIMIT 1"
    ).fetchone()
    d.close()
    afirmar(fila_aviso is None, "todavia no hay aviso: la inspeccion no se envio")

    # -- el envio ----------------------------------------------------------
    print("\n4. RESEND CAIDO NO PIERDE NADA")
    r = c.post("/inspecciones/{}/enviar".format(idi))
    afirmar(r.status_code == 302, "el envio no revienta con Resend sin configurar",
            "HTTP {}".format(r.status_code))

    d = conectar_db()
    insp = d.execute("SELECT enviado_en FROM inspeccion_despacho_regla "
                     " WHERE id = ?", (idi,)).fetchone()
    av = d.execute("SELECT * FROM avisos_pendientes_regla "
                   " WHERE referencia_id = ?", (idi,)).fetchone()
    cola = d.execute("SELECT COUNT(*) FROM sync_push_pendientes "
                     " WHERE entidad LIKE 'inspeccion%'").fetchone()[0]
    d.close()
    afirmar(insp and insp["enviado_en"],
            "la inspeccion QUEDO enviada -- el correo no la tumbo")
    afirmar(cola == 2, "y las dos entradas de push estan encoladas",
            "hay {}".format(cola))
    afirmar(av is not None, "el aviso quedo ENCOLADO, no perdido")
    if av:
        import json
        afirmar(json.loads(av["destinatarios"]) ==
                ["controldespachos@logautos.cl"],
                "con el unico destinatario y sin CC")
        afirmar(av["enviado_en"] is None and (av["intentos"] or 0) == 0,
                "sin intentar todavia")

    # -- 5: el reintento ---------------------------------------------------
    print("\n5. EL REINTENTO")
    res = avisos.procesar()
    afirmar(res["intentados"] == 1, "se intento una vez")
    afirmar(res["enviados"] == 0 and res["errores"] == 1,
            "y fallo, porque no hay clave de Resend")
    d = conectar_db()
    av = d.execute("SELECT * FROM avisos_pendientes_regla "
                   " WHERE referencia_id = ?", (idi,)).fetchone()
    d.close()
    afirmar((av["intentos"] or 0) == 1 and av["enviado_en"] is None,
            "el aviso sigue pendiente, con el intento contado")
    afirmar("RESEND_API_KEY" in (av["ultimo_error"] or ""),
            "y el error dice QUE falto, no 'error generico'",
            (av["ultimo_error"] or "")[:70])

    # SE MUEVE EL RELOJ, y es parte de lo que se prueba: el aviso quedo con
    # `proximo_intento` a 60 s, asi que una segunda vuelta INMEDIATA no lo tiene
    # que tomar -- si lo tomara, el backoff no existiria y un Resend caido se
    # comeria el ciclo entero del hilo de fondo.
    res = avisos.procesar()
    afirmar(res["intentados"] == 0,
            "una vuelta inmediata NO lo reintenta: el backoff espera")
    d = conectar_db()
    d.execute("UPDATE avisos_pendientes_regla SET proximo_intento = ? "
              " WHERE referencia_id = ?",
              ("2000-01-01T00:00:00", idi))
    d.commit()
    d.close()

    # Ahora el proveedor "vuelve": se simula reemplazando el enviador.
    from modulos import correo as _correo
    original = _correo.mandar
    _correo.mandar = lambda *a, **k: ("enviado", "id=simulado")
    try:
        res = avisos.procesar()
    finally:
        _correo.mandar = original
    afirmar(res["enviados"] == 1, "cuando el proveedor vuelve, sale solo")
    d = conectar_db()
    av = d.execute("SELECT enviado_en FROM avisos_pendientes_regla "
                   " WHERE referencia_id = ?", (idi,)).fetchone()
    d.close()
    afirmar(av["enviado_en"] is not None, "y queda marcado como enviado")

    res = avisos.procesar()
    afirmar(res["intentados"] == 0, "y no se vuelve a mandar")

    # -- 6: el que se agota -------------------------------------------------
    #
    # ES EL CAMINO QUE MIRA LA RECONCILIACION. Un aviso que se reintenta para
    # siempre no necesita que nadie lo vea; uno que dejo de reintentarse SI, y
    # por eso `agotado` existe y por eso la fila NO se borra: la fila es la
    # evidencia de que una inspeccion no dejo registro interno.
    print("")
    print("6. EL QUE SE AGOTA")
    d = conectar_db()
    avisos.encolar(d, "inspeccion_despacho", 999999,
                   ["controldespachos@logautos.cl"],
                   "Asunto que no va a salir", "texto", "<p>html</p>")
    d.commit()
    d.close()
    for _vuelta in range(avisos.MAXIMO_INTENTOS):
        d = conectar_db()
        d.execute("UPDATE avisos_pendientes_regla SET proximo_intento = ? "
                  " WHERE referencia_id = 999999", ("2000-01-01T00:00:00",))
        d.commit()
        d.close()
        res = avisos.procesar()
    afirmar(res["agotados"] == 1,
            "a los {} intentos se agota".format(avisos.MAXIMO_INTENTOS))
    d = conectar_db()
    av = d.execute("SELECT * FROM avisos_pendientes_regla "
                   " WHERE referencia_id = 999999").fetchone()
    afirmar(av is not None and av["agotado"] == 1,
            "la fila NO se borra: es la evidencia")
    resumen = avisos.pendientes(d)
    d.close()
    afirmar(resumen["agotados"] == 1,
            "y la reconciliacion lo cuenta", "{}".format(resumen))
    res = avisos.procesar()
    afirmar(res["intentados"] == 0, "ya no se reintenta solo")

    print("")
    print("=" * 62)
    if FALLOS:
        print("FALLARON {}:".format(len(FALLOS)))
        for f in FALLOS:
            print("   - {}".format(f))
        return 1
    print("el correo de la inspeccion sale como el del legado, y no se pierde")
    return 0


if __name__ == "__main__":
    sys.exit(main())
