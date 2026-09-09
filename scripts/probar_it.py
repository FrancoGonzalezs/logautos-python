#!/usr/bin/env python3
"""
scripts/probar_it.py -- el port del IT.

    python scripts/probar_it.py

NO MANDA NINGUN CORREO: `RESEND_API_KEY` se deja sin poner a propósito, porque
el camino de fallo es el que hay que probar. Tampoco escribe en Regla PHP:
`PUSH_LEGADO_ACTIVO=0`.

QUE SE PRUEBA, Y POR QUE CADA COSA

  1. LOS TRES DESTINOS, con su patio, su calle y su estado. Copiados de
     `destinos_it()` de produccion/Pedido.php. **FR va a PATIO 2**, no al
     PATIO 1 que decía el código: era un bug vivo que Franco ya corrigió, y el
     histórico decía PATIO 2 con 98,9% sobre 809 casos.

  2. LA EXCEPCION DE CARFLEX, que es de Regla PHP y se replica tal cual:
     CARFLEX con ZD queda en INSPECCION MECANICA DESPACHO.

  3. LA EVIDENCIA ES UN «O», y la segunda mitad es la que se olvida: DYP y FR
     la exigen siempre, pero ZD **también** la exige si el estado es PRESENTA
     FALLAS.

  4. EL PUSH MANDA EL DESTINO, no la rama vieja. Hasta el 2026-09-09 mandaba
     `calle='It'` / `despachado='INGRESO A TALLER'` — el `case 'It'` que en
     producción NO EXISTE.

  5. Y EL MOVIMIENTO SE EMPUJA, colgado del IT con `depende_de`. La afirmación
     de que Regla PHP llamaba a `registromov()` cero veces era falsa: la rama
     viva lo llama una vez (produccion/Pedido.php:9505).

  6. EL CORREO SOLO EN DYP Y FR, con las fotos INCRUSTADAS por `cid`, a los
     tres internos más `preentrega@cidef.cl` cuando el cliente es CIDEF. Y si
     Resend está caído, la revisión NO se pierde.

  7. LAS FOTOS con el perfil de daños (800 px), y el tope de 6 que es del
     formulario y no del modelo.
"""

import io as _io
import os
import shutil
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from temporales import carpeta_de_prueba              # noqa: E402

os.environ.setdefault("SECRET_KEY", "prueba")
os.environ.setdefault("PUBLIC_BASE_URL", "https://regla.example")
os.environ.setdefault("REGLA_SOLO_LOCAL", "1")
os.environ["PUSH_LEGADO_ACTIVO"] = "0"
# A PROPOSITO sin poner: el camino que hay que probar es el de Resend caido.
os.environ.pop("RESEND_API_KEY", None)

FALLOS = []


def afirmar(condicion, que, detalle=""):
    print("   {}  {}".format("ok  " if condicion else "FALLA", que))
    if detalle:
        print("          {}".format(detalle))
    if not condicion:
        FALLOS.append(que)


def foto(nombre="f.jpg", lado=2400):
    """Una foto grande, como la de un telefono.

    ES UN COLOR PLANO, y por eso comprime a ~3 KB. Sirve para comprobar que el
    redimensionado OCURRE --800 px de lado mayor-- y NO sirve para medir cuanto
    va a pesar una foto de verdad: sobre fotos reales el perfil de daños da
    51 KB de promedio. Planificar el volumen con el numero de esta imagen se
    equivoca por 17 veces."""
    from PIL import Image
    b = _io.BytesIO()
    Image.new("RGB", (lado, int(lado * 0.75)), (90, 120, 60)).save(
        b, "JPEG", quality=95)
    b.seek(0)
    return (b, nombre)


# (id, cliente, estado inicial)
CIDEF = 970001
CARFLEX = 970002
OTRO = 970003


def main():
    tmp = carpeta_de_prueba("regla_it_")
    prueba = os.path.join(tmp, "prueba.db")
    origen = os.path.join(RAIZ, "local.db")
    if not os.path.exists(origen):
        print("no hay local.db")
        return 1
    shutil.copy(origen, prueba)
    os.environ["DB_PATH"] = prueba
    os.environ["DATA_DIR"] = tmp

    from app import crear_app
    from core import conectar_db
    app = crear_app()
    app.config["TESTING"] = True

    db = conectar_db()
    for uid, cliente in ((CIDEF, "CIDEF"), (CARFLEX, "CARFLEX"),
                         (OTRO, "PRUEBA")):
        db.execute(
            "INSERT OR REPLACE INTO newstocks_cidef "
            " (id, vin, clientecompleto, marca, modelo, color, patente, "
            "  calle, despachado, patio, updated_at) "
            " VALUES (?,?,?,'GREAT WALL','POER','BLANCO','ABCD12','IT',"
            "         'INGRESO A TALLER','PATIO 2','2026-09-01 10:00:00')",
            (uid, "VINIT{:012d}".format(uid), cliente))
    db.commit()
    db.close()

    from modulos import taller

    # -- 1 y 2: la tabla de destinos ----------------------------------------
    print("")
    print("1. LOS TRES DESTINOS, COPIADOS DE produccion/Pedido.php")
    with app.test_request_context():
        from core import consultar
        u = consultar("SELECT * FROM newstocks_cidef WHERE id = ?", (OTRO,),
                      una=True)
        d = taller.destinos_para(u)
        esperado = {
            "ZD":  ("PATIO 1", "ZD", "ZONA DE DESPACHO", False),
            "DYP": ("PATIO 2", "ENTREGADO DYP", "DYP", True),
            "FR":  ("PATIO 2", "Cmp3", "FR - MECANICA", True),
        }
        for clave, (patio, calle, estado, evid) in esperado.items():
            visto = (d[clave]["patio"], d[clave]["calle"], d[clave]["estado"],
                     d[clave]["evidencia"])
            afirmar(visto == (patio, calle, estado, evid),
                    "{:<4} -> {} / {} / {}".format(clave, patio, calle, estado),
                    "vio {}".format(visto))

        afirmar(d["FR"]["patio"] == "PATIO 2",
                "FR va a PATIO 2 y no al PATIO 1 que decia el codigo",
                "era un bug vivo; el historico decia PATIO 2 al 98,9% (n=809)")

        print("")
        print("2. LA EXCEPCION HEREDADA DE CARFLEX")
        uc = consultar("SELECT * FROM newstocks_cidef WHERE id = ?",
                       (CARFLEX,), una=True)
        dc = taller.destinos_para(uc)
        afirmar(dc["ZD"]["estado"] == "INSPECCION MECANICA DESPACHO",
                "CARFLEX con ZD queda en INSPECCION MECANICA DESPACHO",
                dc["ZD"]["estado"])
        afirmar(d["ZD"]["estado"] == "ZONA DE DESPACHO",
                "y el resto de los clientes NO", d["ZD"]["estado"])
        afirmar(dc["FR"]["estado"] == "FR - MECANICA",
                "la excepcion es SOLO de ZD, no de los tres",
                dc["FR"]["estado"])

    # -- 3: la evidencia ----------------------------------------------------
    print("")
    print("3. LA EVIDENCIA ES UN «O», Y LA SEGUNDA MITAD SE OLVIDA")
    casos = [
        ("ZD",  "OK",              False, "conforme a zona de despacho"),
        ("ZD",  "PRESENTA FALLAS", True,
         "ZD PERO CON FALLAS: la exige igual"),
        ("DYP", "OK",              True,  "DYP siempre"),
        ("FR",  "OK",              True,  "FR siempre"),
    ]
    for destino, estado, esperado, porque in casos:
        visto = taller.exige_evidencia(destino, estado)
        afirmar(visto == esperado,
                "{:<4} + {:<16} -> {}".format(
                    destino, estado, "exige" if esperado else "no exige"),
                porque)

    # -- 4, 5, 6, 7: un IT real, de punta a punta ---------------------------
    print("")
    print("4. UN IT A FR, CON FOTO: QUE SE ESCRIBE")
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(isLoggedIn=True, userId=1, name="Jefe de mecanicos",
                 role=1, email="fgonzalez@logautos.cl")

    r = c.post("/movimientos/{}/it".format(CIDEF), data={
        "estado_it": "PRESENTA FALLAS",
        "destino_it": "FR",
        "observacion_it": "RUIDO EN TREN DELANTERO",
        "fotos_it": [foto("uno.jpg"), foto("dos.jpg")],
    }, content_type="multipart/form-data", follow_redirects=False)
    afirmar(r.status_code in (302, 303), "guarda y redirige",
            "dio {}".format(r.status_code))

    with app.test_request_context():
        from core import consultar
        fila = consultar("SELECT * FROM it_regla ORDER BY id DESC LIMIT 1",
                         (), una=True)
        afirmar(fila is not None and fila["destino_it"] == "FR",
                "la fila de it_regla lleva el destino",
                fila["destino_it"] if fila else "sin fila")
        afirmar(fila["patio"] == "PATIO 2" and fila["calle"] == "Cmp3",
                "con el patio y la calle del destino",
                "{} / {}".format(fila["patio"], fila["calle"]))

        fotos = consultar("SELECT * FROM it_fotos_regla WHERE it_id = ?",
                          (fila["id"],))
        afirmar(len(fotos) == 2, "las dos fotos quedaron guardadas",
                "{}".format(len(fotos)))

        # El perfil de daños: 800 px de lado mayor.
        from PIL import Image
        if fotos:
            ruta = os.path.join(tmp, fotos[0]["ruta"])
            afirmar(os.path.exists(ruta), "y el archivo existe", ruta)
            im = Image.open(ruta)
            afirmar(max(im.size) == 800,
                    "con el perfil de daños: 800 px de lado mayor",
                    "{}".format(im.size))

        print("")
        print("5. EL PUSH: EL DESTINO, Y EL MOVIMIENTO COLGADO")
        # Acotado a ESTE IT: la replica copiada trae entradas viejas de las
        # pruebas de agosto, y un `ORDER BY id DESC LIMIT 4` las mezclaria.
        cola = consultar(
            "SELECT * FROM sync_push_pendientes "
            " WHERE id > (SELECT COALESCE(MAX(id),0) FROM sync_push_pendientes"
            "             WHERE creado_en < ?) ORDER BY id",
            (fila["creado_en"],))
        entidades = [f["entidad"] for f in cola]
        afirmar("it" in entidades, "hay entrada de `it`", str(entidades))
        afirmar("it_movimiento" in entidades,
                "y el MOVIMIENTO se empuja (antes no se empujaba)",
                str(entidades))

        import json
        e_it = [f for f in cola if f["entidad"] == "it"][0]
        campos = json.loads(e_it["campos_json"])
        afirmar(campos.get("calle") == "Cmp3",
                "manda la calle del DESTINO, no 'It' de la rama vieja",
                campos.get("calle"))
        afirmar(campos.get("despachado") == "FR - MECANICA",
                "y el estado del destino", campos.get("despachado"))
        afirmar(campos.get("patio") == "PATIO 2", "y el patio",
                campos.get("patio"))
        afirmar(campos.get("destino_it") == "FR",
                "y `destino_it`, aunque hoy la lista blanca la ignore",
                campos.get("destino_it"))

        e_mov = [f for f in cola if f["entidad"] == "it_movimiento"][0]
        afirmar(e_mov["depende_de"] == e_it["id"],
                "el movimiento CUELGA de la entrada de `it`",
                "depende_de={} it={}".format(e_mov["depende_de"], e_it["id"]))

        print("")
        print("6. EL CORREO: SOLO EN DYP Y FR, CON LAS FOTOS INCRUSTADAS")
        aviso = consultar(
            "SELECT * FROM avisos_pendientes_regla ORDER BY id DESC LIMIT 1",
            (), una=True)
        afirmar(aviso is not None and aviso["modulo"] == "it",
                "quedo encolado un aviso del IT")
        dirs = json.loads(aviso["destinatarios"])
        afirmar(sorted(dirs) == sorted([
            "fgonzalez@logautos.cl", "nrodriguez@logautos.cl",
            "felipe.leon@logautos.cl", "preentrega@cidef.cl"]),
            "a los TRES internos MAS preentrega@cidef.cl (cliente CIDEF)",
            str(dirs))
        adj = json.loads(aviso["adjuntos"] or "[]")
        afirmar(len(adj) == 2 and all(a.get("cid") for a in adj),
                "con las dos fotos INCRUSTADAS por cid, no como enlaces",
                str([a.get("cid") for a in adj]))
        afirmar("cid:foto_it_1" in (aviso["html"] or ""),
                "y el cuerpo las referencia con un img src de cid")
        afirmar(not aviso["enviado_en"],
                "sin enviar: Resend no esta configurado, y la revision NO se "
                "perdio")

        print("")
        print("7. UN IT A ZD NO MANDA CORREO")
        antes = consultar("SELECT COUNT(*) n FROM avisos_pendientes_regla",
                          (), una=True)["n"]

    r2 = c.post("/movimientos/{}/it".format(OTRO), data={
        "estado_it": "OK", "destino_it": "ZD", "observacion_it": "",
    }, content_type="multipart/form-data")
    with app.test_request_context():
        from core import consultar
        despues = consultar("SELECT COUNT(*) n FROM avisos_pendientes_regla",
                            (), una=True)["n"]
        afirmar(r2.status_code in (302, 303), "ZD sin foto ni observacion: guarda",
                "dio {}".format(r2.status_code))
        afirmar(antes == despues, "y NO encola correo", "{} -> {}".format(
            antes, despues))

    print("")
    print("8. LO QUE LA PANTALLA RECHAZA")
    r3 = c.post("/movimientos/{}/it".format(OTRO), data={
        "estado_it": "OK", "destino_it": "FR", "observacion_it": "ALGO",
    }, content_type="multipart/form-data")
    afirmar(r3.status_code == 400, "FR sin foto: 400",
            "dio {}".format(r3.status_code))
    afirmar("al menos una foto" in r3.get_data(as_text=True),
            "y dice que falta la foto")

    r4 = c.post("/movimientos/{}/it".format(OTRO), data={
        "estado_it": "PRESENTA FALLAS", "destino_it": "ZD",
        "observacion_it": "", "fotos_it": [foto()],
    }, content_type="multipart/form-data")
    afirmar(r4.status_code == 400,
            "ZD con PRESENTA FALLAS y sin observacion: 400",
            "dio {}".format(r4.status_code))

    r5 = c.post("/movimientos/{}/it".format(OTRO), data={
        "estado_it": "OK", "destino_it": "", "observacion_it": "",
    }, content_type="multipart/form-data")
    afirmar(r5.status_code == 400, "sin destino: 400",
            "dio {}".format(r5.status_code))

    r6 = c.post("/movimientos/{}/it".format(OTRO), data={
        "estado_it": "OK", "destino_it": "DYP", "observacion_it": "X",
        "fotos_it": [foto("a.jpg"), foto("b.jpg"), foto("c.jpg"),
                     foto("d.jpg"), foto("e.jpg"), foto("f.jpg"),
                     foto("g.jpg")],
    }, content_type="multipart/form-data")
    afirmar(r6.status_code == 400, "siete fotos: 400 (el tope son 6)",
            "dio {}".format(r6.status_code))
    afirmar("Máximo 6" in r6.get_data(as_text=True), "y lo dice con el numero")

    print("")
    print("=" * 64)
    if FALLOS:
        print("FALLARON {}:".format(len(FALLOS)))
        for f in FALLOS:
            print("   - {}".format(f))
        return 1
    print("el IT manda el destino elegido, empuja el movimiento, guarda las")
    print("fotos con el perfil de daños y avisa a CIDEF con las fotos adentro.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
