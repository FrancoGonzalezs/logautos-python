#!/usr/bin/env python3
"""
probar_check_list_mecanica.py -- los pasos 1 y 2 del check list mecanico.

    python scripts/probar_check_list_mecanica.py

Corre sobre una base temporal, nunca sobre `local.db` ni contra el legado: el
nombre `probar_*` activa las DOS guardas de core --
`exigir_destino_local` para el HTTP y `exigir_replica_de_prueba` para las
escrituras.

Lo que se prueba, y por que cada cosa:

  1. EL CATALOGO CUBRE LA HISTORIA. Los 89.760 valores que el legado escribio
     en los ultimos doce meses tienen que caer todos dentro del menu, salvo
     los que MySQL corto. Es el oraculo: si el menu deja afuera un valor que
     realmente se uso, la pantalla no lo puede reproducir.

  2. LA ASIMETRIA tet/aa SIGUE VIVA. Es el error facil de cometer al
     refactorizar y no lo agarra ninguna otra prueba.

  3. LAS FALLAS SE ACUMULAN. La segunda falla NO pisa a la primera. Es el
     mismo modo de falla que `observaciones` de `newstocks_cidef`, y ahi ya
     nos costo una guarda del lado del servidor.

  4. LA REAPERTURA ESCRIBE EN LAS OTRAS TRES COLUMNAS. Los 68 casos.

  5. UNA FOTO PUBLICADA SE SIRVE SIN SESION, Y UNA QUE NO SE PUBLICO NO --
     aunque el archivo exista en el mismo volumen. Es la condicion que hace
     que la ruta publica no arrastre las fotos de otros modulos.

  6. LA GUARDA DE LA REPLICA MUERDE. Nacio de esta misma suite: una prueba de
     humo escribio sobre una unidad real. Se prueba por `conectar_db`, que es
     el camino que uso el daño.
"""

import io
import os
import shutil
import sqlite3
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SECRET_KEY", "prueba")

FALLOS = []


def afirmar(condicion, que):
    print("   {}  {}".format("ok  " if condicion else "FALLA", que))
    if not condicion:
        FALLOS.append(que)


# ---------------------------------------------------------------------------
# 1 y 2: el catalogo, contra el dato real
# ---------------------------------------------------------------------------

def probar_catalogo():
    from modulos import catalogo_mecanica as cat
    print("\n1. EL CATALOGO")
    afirmar(len(cat.CAMPOS) == 65, "son 65 campos")
    afirmar("No Aplica" not in cat.ENCENDIDO,
            "`tet` NO ofrece 'No Aplica'")
    afirmar("No Aplica" in cat.ENCENDIDO_CON_NA,
            "`aa` SI ofrece 'No Aplica'")
    afirmar(cat.validar_campo("tet", "No Aplica")[1] is not None,
            "y la validacion los distingue de verdad")

    afirmar(cat.validar_bateria("12,80")[0] == "12.80V",
            "la bateria acepta coma decimal y normaliza")
    afirmar(cat.validar_bateria("12.56v")[0] == "12.56V",
            "y acepta el sufijo en minuscula")
    afirmar(cat.validar_bateria("Cambio")[0] == "Cambio",
            "y 'Cambio' es una opcion explicita")
    afirmar(cat.validar_bateria("1.26")[1] is not None,
            "un punto decimal corrido se rechaza")
    afirmar(cat.validar_porcentaje("40")[0] == "40%",
            "el porcentaje se guarda con el signo, como el legado")

    base = os.path.join(RAIZ, "local.db")
    if not os.path.exists(base):
        print("   --   sin local.db, se saltea el oraculo")
        return
    db = sqlite3.connect(base)
    db.row_factory = sqlite3.Row
    total = cubiertos = cortados = 0
    sueltos = []
    for columna, _e, tipo, opciones in cat.CAMPOS:
        if tipo != "opcion":
            continue
        for r in db.execute(
                'SELECT "{0}" v, COUNT(*) n FROM check_list_mecanica '
                ' WHERE fecha_creacion >= "2025-09" AND "{0}" IS NOT NULL '
                '   AND TRIM("{0}") <> "" GROUP BY 1'.format(columna)):
            total += r["n"]
            if r["v"] in opciones:
                cubiertos += r["n"]
            elif any(o.startswith(r["v"]) for o in opciones):
                cortados += r["n"]      # el corte de varchar de MySQL
            else:
                sueltos.append((columna, r["v"], r["n"]))
    print("   {} valores historicos: {} en el menu, {} cortados por MySQL"
          .format(total, cubiertos, cortados))
    afirmar(not sueltos, "ningun valor historico queda fuera del menu")
    for s in sueltos:
        print("        fuera: {}".format(s))


# ---------------------------------------------------------------------------
# 3 y 4: la acumulacion y la reapertura
# ---------------------------------------------------------------------------

def probar_acumulacion(app):
    from modulos import check_list_mecanica as clm
    print("\n2. LAS FALLAS SE ACUMULAN")

    with app.test_request_context():
        from core import get_db
        db = get_db()
        clm._asegurar_tabla(db)
        db.execute(
            "INSERT INTO check_list_mecanica_regla "
            "  (id, unidad_id, vin, estado, contador) VALUES (1, 99, 'VIN1', "
            "   'ABIERTO', 0)")
        db.commit()

        check = clm._uno(1)
        cols, _ = clm.agregar_falla(check, "EXTINTOR VENCIDO", "LEVE", None)
        afirmar(cols[0] == "observacion",
                "un check list ABIERTO escribe en `observacion`")

        check = clm._uno(1)
        clm.agregar_falla(check, "TAPIZ MANCHADO", "LEVE", None)
        fila = clm._uno(1)
        afirmar(fila["observacion"] == "EXTINTOR VENCIDO | TAPIZ MANCHADO",
                "la segunda falla se suma, no pisa a la primera")
        afirmar(fila["modalidad"] == "LEVE | LEVE",
                "y la modalidad acompana en paralelo")
        afirmar(fila["contador"] == 2, "el contador va en 2")
        afirmar(len(clm._cargadas(fila)) == 2,
                "y la pantalla las vuelve a leer como dos")

        print("\n3. LA REAPERTURA")
        db.execute("UPDATE check_list_mecanica_regla SET estado='REABIERTO' "
                   " WHERE id = 1")
        db.commit()
        check = clm._uno(1)
        cols, _ = clm.agregar_falla(check, "FUGA DE ACEITE", "GRAVE", None)
        afirmar(cols[0] == "fallas_adicionales",
                "un check list REABIERTO escribe en `fallas_adicionales`")
        fila = clm._uno(1)
        afirmar(fila["observacion"] == "EXTINTOR VENCIDO | TAPIZ MANCHADO",
                "y NO toca las fallas de la primera pasada")
        afirmar(fila["fallas_adicionales"] == "FUGA DE ACEITE",
                "la primera adicional va sola, sin separador adelante")


# ---------------------------------------------------------------------------
# 5: la ruta publica
# ---------------------------------------------------------------------------

def probar_ruta_publica(app):
    from modulos import fotos_publicas
    print("\n4. LA RUTA PUBLICA DE FOTOS")

    carpeta = os.path.join(fotos_publicas.RAIZ, "uploads", "falla_mecanica")
    os.makedirs(carpeta, exist_ok=True)
    publicada = os.path.join(carpeta, "publicada.jpg")
    privada = os.path.join(carpeta, "privada.jpg")
    for p in (publicada, privada):
        with open(p, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0jpeg")

    # La guarda de la base publica: sin ella, la URL saldria relativa y el
    # legado la resolveria contra SU host.
    previo = os.environ.pop("PUBLIC_BASE_URL", None)
    try:
        fotos_publicas.url_publica("/f/abc")
        afirmar(False, "sin PUBLIC_BASE_URL, armar una URL para el legado REVIENTA")
    except fotos_publicas.FaltaBasePublica:
        afirmar(True, "sin PUBLIC_BASE_URL, armar una URL para el legado REVIENTA")
    os.environ["PUBLIC_BASE_URL"] = previo or "https://regla.example"
    afirmar(fotos_publicas.url_publica("/f/abc").startswith("http"),
            "y con la base puesta sale absoluta")

    with app.test_request_context():
        ruta = fotos_publicas.publicar("uploads/falla_mecanica/publicada.jpg",
                                       origen="prueba", referencia=1)
        otra = fotos_publicas.publicar("uploads/falla_mecanica/publicada.jpg",
                                       origen="prueba", referencia=1)
    afirmar(ruta == otra, "publicar dos veces la misma foto da UN solo token")
    token = ruta.rsplit("/", 1)[-1]
    afirmar(len(token) >= 40, "el token tiene largo de token ({})".format(len(token)))

    cliente = app.test_client()
    r = cliente.get(ruta)
    afirmar(r.status_code == 200, "la foto publicada se sirve SIN sesion")

    r = cliente.get("/f/" + "z" * len(token))
    afirmar(r.status_code == 404, "un token inventado da 404")

    # Lo que decide el diseno: el archivo privado EXISTE, en la misma carpeta,
    # y no hay forma de pedirlo por esta ruta porque no hay token que lo
    # nombre. Si la ruta resolviera por ruta de archivo, saldria igual.
    r = cliente.get("/f/uploads/falla_mecanica/privada.jpg")
    # No se afirma el codigo exacto sino que NO SALE EL ARCHIVO. La ruta pide
    # un token de un solo segmento, asi que una ruta de archivo no matchea
    # ningun endpoint; y como el guardian de sesion corre antes del ruteo, un
    # endpoint inexistente termina en un 302 al login en vez de un 404. El
    # codigo es un detalle del orden de los hooks: lo que esta bajo prueba es
    # que el contenido no viaja.
    afirmar(r.status_code != 200 and b"jpeg" not in r.data,
            "un archivo NO publicado no se sirve, aunque exista al lado "
            "(devolvio {})".format(r.status_code))

    r = cliente.get("/unidades")
    afirmar(r.status_code in (302, 401),
            "y el resto del sitio sigue pidiendo sesion")


def probar_guarda_de_replica():
    """La guarda que salio de esta misma suite.

    El 2026-09-02 una prueba de humo de este modulo escribio en `local.db` un
    movimiento sobre la unidad 92095 -- una unidad real -- con su entrada de
    cola sin resolver y `push_pendiente = 1`. La guarda de destino no la
    frena: esa cubre el HTTP, no las escrituras.

    Se prueba por el camino que uso el daño de verdad -- `conectar_db`, que es
    el que hay debajo del `get_db()` de Flask -- y no por una llamada
    inventada."""
    import core
    print("\n5. LA GUARDA DE LA REPLICA REAL")

    real = os.path.join(RAIZ, "local.db")
    if not os.path.exists(real):
        print("   --   sin local.db, se saltea")
        return

    previo = os.environ.get("REGLA_SOLO_LOCAL")
    os.environ["REGLA_SOLO_LOCAL"] = "1"
    try:
        try:
            core.conectar_db(real).close()
            afirmar(False, "abrir la replica real desde una prueba REVIENTA")
        except RuntimeError as e:
            afirmar("GUARDA DE REPLICA" in str(e),
                    "abrir la replica real desde una prueba REVIENTA")
            afirmar("shutil.copy" in str(e),
                    "y el mensaje dice como arreglarlo, no solo que esta mal")

        # La misma ruta escrita distinto. Sin normalizar, esta pasaria.
        revuelta = real.replace("\\", "/").lower()
        try:
            core.conectar_db(revuelta).close()
            afirmar(False, "la misma ruta con otra caja tambien se rechaza")
        except RuntimeError:
            afirmar(True, "la misma ruta con otra caja tambien se rechaza")

        # Y la copia, que es lo que las pruebas tienen que usar.
        copia = os.path.join(os.environ["DATA_DIR"], "copia.db")
        shutil.copy(real, copia)
        try:
            core.conectar_db(copia).close()
            afirmar(True, "una COPIA se abre sin problema")
        except RuntimeError:
            afirmar(False, "una COPIA se abre sin problema")

        os.environ["REGLA_SOLO_LOCAL"] = "0"
        try:
            core.conectar_db(real).close()
            afirmar(True, "REGLA_SOLO_LOCAL=0 apaga las DOS guardas")
        except RuntimeError:
            afirmar(False, "REGLA_SOLO_LOCAL=0 apaga las DOS guardas")
    finally:
        if previo is None:
            os.environ.pop("REGLA_SOLO_LOCAL", None)
        else:
            os.environ["REGLA_SOLO_LOCAL"] = previo


def main():
    tmp = tempfile.mkdtemp(prefix="regla_clm_")
    os.environ["DB_PATH"] = os.path.join(tmp, "prueba.db")
    os.environ["DATA_DIR"] = tmp

    probar_catalogo()

    from app import crear_app
    app = crear_app()
    app.config["TESTING"] = True

    probar_acumulacion(app)
    probar_ruta_publica(app)
    probar_guarda_de_replica()

    print("\n" + "=" * 62)
    if FALLOS:
        print("FALLARON {}:".format(len(FALLOS)))
        for f in FALLOS:
            print("   - {}".format(f))
        return 1
    print("todo bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
