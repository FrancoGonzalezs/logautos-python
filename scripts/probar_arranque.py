#!/usr/bin/env python3
"""
scripts/probar_arranque.py -- lo que tiene que estar puesto para que la
aplicacion levante.

    python scripts/probar_arranque.py

POR QUE EXISTE ESTA SUITE

Las otras catorce prueban que el sistema hace bien lo suyo. Esta prueba que se
NIEGA a arrancar cuando el entorno esta mal, que es una garantia distinta, y en
una mudanza de infraestructura es la que importa.

El caso concreto: `PUBLIC_BASE_URL` es la raiz con la que se arman las URL de
las fotos que se le mandan a Regla PHP, y esas URL quedan escritas en
`archivo1..archivo9` -- columnas PERMANENTES. Regla PHP guarda la URL, no el
archivo. Una foto publicada con el host equivocado deja de verse en la pantalla
de otro sistema y no hay variable que la arregle despues.

Hasta el 2026-09-09 la unica comprobacion vivia dentro de `url_publica`: la
aplicacion levantaba tranquila y reventaba recien al publicar la primera foto,
en medio de una inspeccion y con el usuario mirando.
"""

import importlib
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from temporales import carpeta_de_prueba              # noqa: E402

os.environ.setdefault("SECRET_KEY", "prueba")
os.environ.setdefault("REGLA_SOLO_LOCAL", "1")
os.environ.setdefault("PUSH_LEGADO_ACTIVO", "0")
# Una base VACIA en el tempdir: esta suite no mira datos, y apuntar a la
# replica real haria saltar la guarda de replica al importar la app.
os.environ["DB_PATH"] = os.path.join(carpeta_de_prueba("regla_arranq_"),
                                     "vacia.db")
os.environ["DATA_DIR"] = os.path.dirname(os.environ["DB_PATH"])
# Sin base, el propio `import app` reventaria antes de llegar a los casos.
os.environ.setdefault("PUBLIC_BASE_URL", "https://regla.example")

FALLOS = []


def afirmar(condicion, que, detalle=""):
    print("   {}  {}".format("ok  " if condicion else "FALLA", que))
    if detalle:
        print("          {}".format(detalle))
    if not condicion:
        FALLOS.append(que)


def primera_linea(texto):
    return str(texto).split(chr(10))[0]


# (valor, tiene_que_arrancar, por_que)
CASOS = [
    (None, False,
     "sin poner: el caso que motivo la guarda"),
    ("", False,
     "vacia: declarada y sin valor es lo mismo que no tenerla"),
    ("   ", False,
     "solo espacios: se copia y se pega desde un panel, pasa"),
    ("regla.logautos.cl", False,
     "sin esquema: Regla PHP la resolveria contra SU host"),
    ("https://regla.logautos.cl/fotos", False,
     "con ruta: la ruta la agrega url_publica, aca duplicaria"),
    ("https://regla.logautos.cl/", True,
     "con barra final: se acepta y se normaliza"),
    ("https://regla.logautos.cl", True,
     "el dominio propio, que es el caso bueno"),
    ("https://logautos-production.up.railway.app", True,
     "la direccion de Railway: ARRANCA (publicar es otra cosa, seccion 4)"),
]

# Direcciones temporales de Railway, en las formas en que aparecen.
PROVISORIAS = [
    "https://logautos-production.up.railway.app",
    "https://regla.railway.app",
    "http://algo.up.railway.app",
]

# Y las que NO son provisorias, que es la mitad que se olvida: una guarda que
# ademas frena al dominio bueno no sirve para nada.
DEFINITIVAS = [
    "https://regla.logautos.cl",
    "https://logautos.cl",
    # El nombre contiene "railway" pero el HOST no es de Railway. Una
    # comprobacion por subcadena suelta se lo comeria.
    "https://railway.logautos.cl",
]


def main():
    from modulos import fotos_publicas

    print("")
    print("1. LA APLICACION NO ARRANCA CON UNA BASE PUBLICA INVALIDA")
    for valor, arranca, porque in CASOS:
        if valor is None:
            os.environ.pop("PUBLIC_BASE_URL", None)
        else:
            os.environ["PUBLIC_BASE_URL"] = valor

        # EL IMPORT ENTERO tiene que reventar, no solo `crear_app()`: `app.py`
        # termina con `app = crear_app()` a nivel de modulo, y eso es lo que
        # ejecuta gunicorn al levantar. Si la guarda solo cortara dentro de la
        # funcion, un despliegue con la variable mal puesta arrancaria igual.
        etiqueta = "<sin poner>" if valor is None else repr(valor)
        try:
            import app as modulo_app
            importlib.reload(modulo_app)
            ok = arranca
            visto = "arranca con {!r}".format(
                modulo_app.app.config["BASE_PUBLICA"])
        except fotos_publicas.FaltaBasePublica as e:
            ok = not arranca
            visto = "no arranca: {}".format(primera_linea(e))
        afirmar(ok, "{:<44} {}".format(etiqueta, porque), visto)

    print("")
    print("2. LA URL QUE SE LE MANDA A REGLA PHP SALE DE LA VARIABLE")
    os.environ["PUBLIC_BASE_URL"] = "https://regla.logautos.cl"
    u = fotos_publicas.url_publica("/f/abc123")
    afirmar(u == "https://regla.logautos.cl/f/abc123",
            "url_publica usa la base configurada", u)

    # La barra final no puede duplicarse: `//f/` no es la misma URL.
    os.environ["PUBLIC_BASE_URL"] = "https://regla.logautos.cl/"
    u2 = fotos_publicas.url_publica("/f/abc123")
    afirmar(u2 == "https://regla.logautos.cl/f/abc123",
            "y con barra final NO duplica la barra", u2)

    print("")
    print("3. Y NO SALE DEL REQUEST, NI AUNQUE HAYA UNO")
    # Es la garantia de fondo: durante la mudanza conviven la direccion de
    # Railway y el dominio propio, asi que si la base saliera del `Host` la URL
    # escrita dependeria de por donde entro el que subio la foto. Dos fotos de
    # la misma inspeccion podrian quedar con hosts distintos.
    os.environ["PUBLIC_BASE_URL"] = "https://regla.logautos.cl"
    import app as modulo_app
    importlib.reload(modulo_app)
    with modulo_app.app.test_request_context(
            "/", base_url="https://otro-host.example"):
        u3 = fotos_publicas.url_publica("/f/abc123")
    afirmar(u3 == "https://regla.logautos.cl/f/abc123",
            "con un request de otro host, la URL NO cambia", u3)

    # El modulo NO puede derivar la base del request. Se mira el CODIGO, no el
    # texto: los comentarios del modulo nombran `request.host` justamente para
    # explicar por que no se usa, y buscar la cadena a secas daria un falso
    # positivo sobre la explicacion.
    import ast
    fuente = io.open(os.path.join(RAIZ, "modulos", "fotos_publicas.py"),
                     encoding="utf-8").read()
    arbol = ast.parse(fuente)
    for nodo in ast.walk(arbol):
        # Los docstrings son expresiones sueltas de cadena: se vacian.
        if isinstance(nodo, ast.Expr) and isinstance(nodo.value, ast.Constant)                 and isinstance(nodo.value.value, str):
            nodo.value.value = ""
    codigo = ast.dump(arbol)
    afirmar("host" not in codigo.replace("'host'", "") or
            "request" not in codigo,
            "el CODIGO del modulo no toca request ni host",
            "(los comentarios si lo nombran, para explicar por que no)")

    print("")
    print("4. CON UNA DIRECCION DE RAILWAY: ARRANCA, PERO NO PUBLICA")
    #
    # Son dos preguntas distintas y por eso tienen dos respuestas. Levantar es
    # reversible: si la base esta mal se cambia la variable y se redespliega.
    # Publicar no: esa URL viaja a `archivo1..archivo9` de Regla PHP y queda
    # escrita para siempre -- Regla PHP guarda la URL, no el archivo.
    for base in PROVISORIAS:
        os.environ["PUBLIC_BASE_URL"] = base
        try:
            import app as modulo_app
            importlib.reload(modulo_app)
            arranco = True
        except fotos_publicas.FaltaBasePublica:
            arranco = False
        afirmar(arranco, "{:<44} arranca".format(repr(base)))

        try:
            fotos_publicas.url_publica("/f/abc123")
            afirmar(False, "{:<44} NO publica".format(repr(base)))
        except fotos_publicas.BasePublicaProvisoria as e:
            afirmar(True, "{:<44} NO publica".format(repr(base)),
                    primera_linea(e)[:70])

    for base in DEFINITIVAS:
        os.environ["PUBLIC_BASE_URL"] = base
        try:
            u = fotos_publicas.url_publica("/f/abc123")
            afirmar(u.startswith(base),
                    "{:<44} SI publica".format(repr(base)), u)
        except fotos_publicas.BasePublicaProvisoria:
            afirmar(False, "{:<44} SI publica".format(repr(base)),
                    "la freno de mas")

    print("")
    print("5. Y `publicar` CORTA ANTES DE ESCRIBIR EL TOKEN")
    # `url_publica` tambien corta, pero para entonces la fila ya estaria
    # escrita: quedaria un token de una foto que nadie publico, y la proxima
    # vez `publicar` lo encontraria y lo daria por bueno.
    os.environ["PUBLIC_BASE_URL"] = "https://logautos-production.up.railway.app"
    import app as modulo_app
    importlib.reload(modulo_app)
    with modulo_app.app.test_request_context():
        from core import get_db
        db = get_db()
        fotos_publicas._asegurar_tabla(db)
        antes = db.execute(
            "SELECT COUNT(*) FROM fotos_publicadas").fetchone()[0]
        try:
            fotos_publicas.publicar("uploads/x.jpg", origen="prueba",
                                    referencia=1)
            afirmar(False, "publicar() corta")
        except fotos_publicas.BasePublicaProvisoria:
            afirmar(True, "publicar() corta")
        despues = db.execute(
            "SELECT COUNT(*) FROM fotos_publicadas").fetchone()[0]
        afirmar(antes == despues, "y NO deja el token escrito",
                "{} -> {}".format(antes, despues))

    print("")
    print("=" * 64)
    if FALLOS:
        print("FALLARON {}:".format(len(FALLOS)))
        for f in FALLOS:
            print("   - {}".format(f))
        return 1
    print("la aplicacion se niega a arrancar sin una base publica valida,")
    print("y la URL de las fotos no depende de por donde entro nadie.")
    print("")
    print("Con una direccion de Railway ARRANCA --hace falta para poder")
    print("probar antes del CNAME-- pero PUBLICAR una foto falla, porque esa")
    print("URL quedaria en archivo1..archivo9 de Regla PHP para siempre.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
