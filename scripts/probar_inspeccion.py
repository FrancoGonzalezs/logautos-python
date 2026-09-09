#!/usr/bin/env python3
"""
scripts/probar_inspeccion.py -- la inspeccion de despacho, de punta a punta.

    python scripts/probar_inspeccion.py

NO MANDA CORREO (`RESEND_API_KEY` sin poner) ni escribe en Regla PHP
(`PUSH_LEGADO_ACTIVO=0`).

POR QUE EXISTE

El modulo esta construido y su PHP desplegado desde el 2026-09-04, y hasta hoy
su unica suite cubria **el correo**. El flujo central --crear, sumar fotos,
aplanar a las nueve ranuras, enviar-- no tenia ninguna, y ahi viven las tres
decisiones que mas facil se deshacen sin que nada avise.

LAS TRES QUE ESTA SUITE PROTEGE

  1. LA TABLA NO TIENE TOPE; EL CABLE SI. La decima foto se guarda igual y sale
     en `sobrantes`. Rechazarla al subir seria perder una foto que el operario
     ya saco, para respetar un limite que es de `archivo1..archivo9` y no del
     trabajo.

  2. `contador` LLEVA EL NUMERO REAL, no la cantidad de ranuras llenas. Con
     once fotos va 11. Es lo que hace Regla PHP, y poner 9 haria que la columna
     conteste "cuantas ranuras se llenaron" cuando su nombre pregunta "cuantas
     fotos hay".

  3. UNA INSPECCION SIN FOTOS NO SE ENVIA. El PDF del despacho es el UNICO
     canal hacia el cliente: una fila sin fotos es un PDF con la seccion vacia,
     y eso pasa entre 4 y 17 veces por mes en Regla PHP.

Y que las URL que viajan sean ABSOLUTAS, porque quedan en `archivo1..archivo9`
para siempre y Regla PHP guarda la URL, no el archivo.
"""

import io as _io
import json
import os
import shutil
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from temporales import carpeta_de_prueba              # noqa: E402

os.environ.setdefault("SECRET_KEY", "prueba")
os.environ["PUBLIC_BASE_URL"] = "https://regla.logautos.cl"
os.environ.setdefault("REGLA_SOLO_LOCAL", "1")
os.environ["PUSH_LEGADO_ACTIVO"] = "0"
os.environ.pop("RESEND_API_KEY", None)

UNIDAD = 960001
FALLOS = []


def afirmar(condicion, que, detalle=""):
    print("   {}  {}".format("ok  " if condicion else "FALLA", que))
    if detalle:
        print("          {}".format(detalle))
    if not condicion:
        FALLOS.append(que)


def foto(nombre="f.jpg"):
    from PIL import Image
    b = _io.BytesIO()
    Image.new("RGB", (1600, 1200), (100, 110, 90)).save(b, "JPEG")
    b.seek(0)
    return (b, nombre)


def main():
    tmp = carpeta_de_prueba("regla_insp_")
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
    db.execute(
        "INSERT OR REPLACE INTO newstocks_cidef "
        " (id, vin, clientecompleto, marca, modelo, color, patente, calle, "
        "  despachado, patio, updated_at) "
        " VALUES (?,?,'CIDEF','GREAT WALL','POER','BLANCO','ABCD12','Zd',"
        "         'ZONA DE DESPACHO','PATIO 1','2026-09-01 10:00:00')",
        (UNIDAD, "VINSP{:012d}".format(UNIDAD)))
    db.commit()
    db.close()

    c = app.test_client()
    with c.session_transaction() as s:
        s.update(isLoggedIn=True, userId=1, name="Movilizador", role=1,
                 email="fgonzalez@logautos.cl")

    # -- 1: crear -----------------------------------------------------------
    print("")
    print("1. CREAR LA INSPECCION")
    r = c.post("/movimientos/{}/inspeccion-despacho".format(UNIDAD), data={
        "destino": "ROSSELOT TEMUCO",
        "fecha_despacho": "2026-09-09",
        "observacion": "SIN NOVEDAD",
        "unidad": foto("unidad.jpg"),
    }, content_type="multipart/form-data")
    afirmar(r.status_code in (302, 303), "crea y redirige",
            "dio {}".format(r.status_code))

    with app.test_request_context():
        from core import consultar
        fila = consultar("SELECT * FROM inspeccion_despacho_regla "
                         " ORDER BY id DESC LIMIT 1", (), una=True)
        afirmar(fila is not None, "quedo la fila")
        id_insp = fila["id"] if fila else None
        afirmar(fila and not fila["enviado_en"],
                "sin enviar: enviar es un acto aparte")

    # -- 2: once fotos ------------------------------------------------------
    print("")
    print("2. LA TABLA NO TIENE TOPE: ONCE FOTOS ENTRAN")
    for n in range(11):
        c.post("/inspecciones/{}/foto".format(id_insp),
               data={"imagen": foto("f{}.jpg".format(n))},
               content_type="multipart/form-data")

    with app.test_request_context():
        from modulos import inspeccion_despacho as I
        fotos = I.fotos_guardadas(id_insp)
        afirmar(len(fotos) == 11, "las once quedaron guardadas",
                "{}".format(len(fotos)))

        print("")
        print("3. AL APLANAR: NUEVE VIAJAN, DOS SALEN EN `sobrantes`")
        publicadas = [(f["orden"], "https://regla.logautos.cl/f/tok{}".format(
            f["orden"])) for f in fotos]
        campos, sobrantes = I.aplanar_para_push(publicadas)
        afirmar(len([k for k in campos if k.startswith("archivo")]) == 9,
                "nueve `archivoN`",
                str(sorted(k for k in campos if k.startswith("archivo"))))
        afirmar(len(sobrantes) == 2, "y dos sobrantes, NO descartadas en "
                "silencio", "{}".format(len(sobrantes)))
        afirmar(campos["contador"] == 11,
                "`contador` lleva el numero REAL (11), no las ranuras (9)",
                "{}".format(campos["contador"]))
        afirmar(campos["link_unidad"].count("|") == 10,
                "y `link_unidad` va con las ONCE: la foto once llega igual, "
                "lo que no llega es a la seccion de fotos del PDF")

    # -- 4: una sin fotos no se envia ---------------------------------------
    print("")
    print("4. UNA INSPECCION SIN FOTOS NO SE ENVIA")
    r = c.post("/movimientos/{}/inspeccion-despacho".format(UNIDAD), data={
        "destino": "ROSSELOT TEMUCO", "fecha_despacho": "2026-09-09",
        "observacion": "OTRA",
    }, content_type="multipart/form-data")
    with app.test_request_context():
        from core import consultar
        vacia = consultar("SELECT * FROM inspeccion_despacho_regla "
                          " ORDER BY id DESC LIMIT 1", (), una=True)
    if vacia and vacia["id"] != id_insp:
        r = c.post("/inspecciones/{}/enviar".format(vacia["id"]))
        afirmar(r.status_code == 400, "400 al intentar enviarla",
                "dio {}".format(r.status_code))
        visible = r.get_data(as_text=True)
        afirmar("secci" in visible and "vac" in visible,
                "y explica la consecuencia: el PDF sale con la seccion vacia")
    else:
        afirmar(False, "se pudo crear una inspeccion sin fotos para el caso")

    # -- 5: el envio --------------------------------------------------------
    print("")
    print("5. EL ENVIO: UNA SOLA VEZ, COMPLETA")
    r = c.post("/inspecciones/{}/enviar".format(id_insp))
    afirmar(r.status_code in (302, 303), "envia y redirige",
            "dio {}".format(r.status_code))
    afirmar("sobrantes=2" in r.headers.get("Location", ""),
            "y avisa cuantas no entraron", r.headers.get("Location", ""))

    with app.test_request_context():
        from core import consultar
        fila = consultar("SELECT * FROM inspeccion_despacho_regla WHERE id = ?",
                         (id_insp,), una=True)
        afirmar(bool(fila["enviado_en"]), "queda marcada como enviada",
                fila["enviado_en"])

        cola = consultar(
            "SELECT * FROM sync_push_pendientes "
            " WHERE entidad LIKE 'inspeccion%' ORDER BY id DESC LIMIT 2")
        entidades = sorted(f["entidad"] for f in cola)
        afirmar(len(cola) == 2, "encolo las DOS escrituras", str(entidades))

        principal = [f for f in cola
                     if f["entidad"] == "inspeccion_despacho"]
        afirmar(principal, "la fila de la inspeccion")
        if principal:
            campos = json.loads(principal[0]["campos_json"])
            afirmar(campos.get("contador") == 11,
                    "con el contador real", campos.get("contador"))
            urls = [v for k, v in campos.items() if k.startswith("archivo")]
            afirmar(len(urls) == 9, "y nueve URL", "{}".format(len(urls)))
            afirmar(all(u.startswith("https://regla.logautos.cl/")
                        for u in urls),
                    "ABSOLUTAS y con el dominio configurado: quedan en "
                    "archivo1..archivo9 para siempre",
                    urls[0] if urls else "")

        estado = [f for f in cola
                  if f["entidad"] != "inspeccion_despacho"]
        if estado:
            afirmar(estado[0]["depende_de"] == principal[0]["id"],
                    "y el cambio de estado CUELGA de la fila",
                    "depende_de={}".format(estado[0]["depende_de"]))

    print("")
    print("6. Y NO SE PUEDE ENVIAR DOS VECES")
    r = c.post("/inspecciones/{}/enviar".format(id_insp))
    afirmar(r.status_code == 400, "el segundo envio da 400",
            "dio {}".format(r.status_code))
    afirmar("ya se envi" in r.get_data(as_text=True),
            "y dice que ya se habia enviado")

    print("")
    print("7. EL CORREO QUEDO ENCOLADO, NO SE MANDO")
    with app.test_request_context():
        from core import consultar
        aviso = consultar("SELECT * FROM avisos_pendientes_regla "
                          " WHERE modulo = 'inspeccion_despacho' "
                          " ORDER BY id DESC LIMIT 1", (), una=True)
        afirmar(aviso is not None, "hay aviso encolado")
        if aviso:
            afirmar(not aviso["enviado_en"],
                    "sin enviar --Resend no esta configurado-- y la "
                    "inspeccion NO se perdio")
            dirs = json.loads(aviso["destinatarios"])
            afirmar(dirs == ["controldespachos@logautos.cl"],
                    "a la unica direccion viva, que es INTERNA", str(dirs))

    print("")
    print("=" * 64)
    if FALLOS:
        print("FALLARON {}:".format(len(FALLOS)))
        for f in FALLOS:
            print("   - {}".format(f))
        return 1
    print("la inspeccion guarda todas las fotos, manda nueve con el contador")
    print("real, no se envia sin fotos ni dos veces, y avisa por dentro.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
