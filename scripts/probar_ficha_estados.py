#!/usr/bin/env python3
"""
scripts/probar_ficha_estados.py -- que las tres pantallas que muestran el
estado de una unidad -- ficha, listado y buscador de taller -- digan la verdad
sobre los DOS estados y no se contradigan entre si.

Existe por un caso real. La unidad 91953 tenia cuatro movimientos registrados
en REGLA -- el ultimo un PDI -- y `/unidades/91953` seguia diciendo
'Navegando', que es donde el dump la habia dejado, mientras
`/movimientos/91953` decia 'EN ESPERA DYP CONSOLIDADO'. Las dos pantallas se
contradecian sobre la misma unidad y ninguna admitia que la otra existiera.
Costo una investigacion entera averiguar que ninguna estaba rota.

Los casos que hay que cubrir, sobre una base descartable:

    1. sin movimientos en REGLA   un solo estado, y se dice que REGLA no tiene
                                  nada registrado -- no se pintan dos badges
                                  con el mismo valor, que seria ruido
    2. coinciden                  los dos rotulados, sin aviso de desfase
    3. difieren                   los dos rotulados MAS el aviso, y la ficha
                                  dice lo mismo que la pantalla de Movimientos
    4. el listado                 marca la fila que diverge, SIN tocar el
                                  filtro ni el orden (los dos siguen sobre la
                                  columna cruda: eso se decidio medir aparte)
    5. el buscador de taller      muestra el de REGLA PRIMERO -- es la pantalla
                                  donde el movilizador encadena VIN y su propio
                                  trabajo desactualiza la columna cruda

La 3 es la que importa para la ficha; la 5, para el trabajo diario.

    python scripts/probar_ficha_estados.py
"""

import os
import re
import shutil
import sqlite3
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

os.environ["SECRET_KEY"] = "prueba"
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


def texto_visible(html):
    """El HTML sin etiquetas, para buscar lo que un humano leeria."""
    sin_estilo = re.sub(r"<(style|script)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", sin_estilo))


def bloque_estados(html):
    """Solo el bloque de los dos estados, para no confundirlo con el resto de
    la ficha -- que menciona estados en la tabla de otras pasadas.

    Se cuenta la profundidad de <div> en vez de usar un regex no-goloso: el
    bloque tiene divs adentro, y `(.*?)</div>` se corta en el primero de ellos.
    Fue el primer intento y daba un bloque con un solo estado, lo que se veia
    igual que el bug que esta prueba busca."""
    inicio = html.find('<div class="estados-unidad">')
    if inicio < 0:
        return "", ""
    i, hondo = inicio, 0
    for etiqueta in re.finditer(r"<(/?)div\b", html[inicio:]):
        hondo += -1 if etiqueta.group(1) else 1
        if hondo == 0:
            i = inicio + etiqueta.end()
            break
    bloque = html[inicio:html.find(">", i) + 1]
    aviso = re.search(r'<p class="estado-desfase">(.*?)</p>', html, flags=re.S)
    return texto_visible(bloque), texto_visible(aviso.group(1) if aviso else "")


def base_con(ruta, despachado, movimientos):
    """Una replica VACIA con el esquema completo, mas la unidad de prueba.

    Se copia el esquema entero de local.db en vez de enumerar las tablas que
    hacen falta. Enumerarlas fue el primer intento y fallo enseguida: la ficha
    toca `inspeccion_despacho`, `contenedor`, `check_list`, `piezas` y varias
    mas, y la lista se desincronizaria con la primera pantalla nueva. Copiar el
    esquema no se desincroniza nunca."""
    if os.path.exists(ruta):
        os.remove(ruta)
    origen = sqlite3.connect(os.path.join(RAIZ, "local.db"))
    db = sqlite3.connect(ruta)
    for (sql,) in origen.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%'"):
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            # Indices sobre tablas que no se crearon todavia, o vistas que
            # dependen de otra: no importan para esta prueba.
            pass

    cols = [r[1] for r in origen.execute("PRAGMA table_info(newstocks_cidef)")]
    fila = origen.execute(
        "SELECT * FROM newstocks_cidef WHERE vin <> '' LIMIT 1").fetchone()
    origen.close()

    valores = list(fila)
    valores[cols.index("id")] = 90001
    valores[cols.index("vin")] = "VINDEPRUEBA123456"
    valores[cols.index("despachado")] = despachado
    valores[cols.index("clientecompleto")] = "CIDEF"
    db.execute("INSERT INTO newstocks_cidef ({}) VALUES ({})".format(
        ", ".join('"{}"'.format(c) for c in cols), ", ".join("?" * len(cols))),
        valores)

    for paso_, hacia in movimientos:
        db.execute(
            "INSERT INTO movimientos_regla (unidad_id, vin, paso, estado_hacia, "
            "creado_en) VALUES (?,?,?,?,?)",
            (90001, "VINDEPRUEBA123456", paso_, hacia, "2026-08-26T10:00:00"))
    db.commit()
    db.close()


def pedir(ruta, camino):
    """Renderiza una pagina con la base `ruta`. DB_PATH se lee al importar
    core, asi que se recarga el modulo para que tome la base de la prueba."""
    import importlib
    os.environ["DB_PATH"] = ruta
    import core
    importlib.reload(core)
    for nombre in list(sys.modules):
        if nombre.startswith("modulos.") or nombre == "app":
            del sys.modules[nombre]
    import app as appmod
    with appmod.app.test_client() as c:
        with c.session_transaction() as s:
            s["isLoggedIn"] = True
            s["userId"] = 0
            s["name"] = "Prueba"
            s["email"] = "p@p.cl"
            s["roleId"] = 1
        r = c.get(camino)
    return r.status_code, r.get_data(as_text=True)


def main():
    tmp = tempfile.mkdtemp(prefix="probar_ficha_")
    ruta = os.path.join(tmp, "prueba.db")

    # ------------------------------------------------------------------ 1
    paso("1. sin movimientos en REGLA")
    base_con(ruta, "STOCK", [])
    codigo, html = pedir(ruta, "/unidades/90001")
    afirmar(codigo == 200, "la ficha responde 200", codigo)
    estados, aviso = bloque_estados(html)
    afirmar("sin movimientos registrados" in estados,
            "dice que REGLA no tiene movimientos", estados.strip()[:120])
    afirmar(estados.count("STOCK") == 1,
            "no pinta dos veces el mismo estado", estados.strip()[:120])
    afirmar(not aviso.strip(), "no hay aviso de desfase", aviso.strip()[:80])

    # ------------------------------------------------------------------ 2
    paso("2. los dos coinciden")
    base_con(ruta, "INGRESO A TALLER", [("ingreso_taller", "INGRESO A TALLER")])
    codigo, html = pedir(ruta, "/unidades/90001")
    estados, aviso = bloque_estados(html)
    afirmar("Estado en REGLA" in estados, "rotula el estado de REGLA")
    afirmar("Confirmado en el sistema anterior" in estados,
            "rotula el del sistema anterior", estados.strip()[:150])
    afirmar(estados.count("INGRESO A TALLER") == 2,
            "muestra los dos, aunque digan lo mismo", estados.strip()[:150])
    afirmar(not aviso.strip(), "sin aviso: no difieren", aviso.strip()[:80])

    # ------------------------------------------------------------------ 3
    paso("3. difieren — el caso que provoco la confusion")
    base_con(ruta, "Navegando", [("ingreso", "ZONA DE RECEPCION"),
                                 ("pdi", "EN ESPERA DYP CONSOLIDADO")])
    codigo, html = pedir(ruta, "/unidades/90001")
    estados, aviso = bloque_estados(html)
    afirmar("EN ESPERA DYP CONSOLIDADO" in estados,
            "la ficha YA muestra el estado de REGLA", estados.strip()[:150])
    # NAVEGANDO y no 'Navegando': badge_estado renderiza `valor | upper`, en
    # toda la app. Es tambien el motivo por el que _estados_de compara los dos
    # estados normalizados -- para el usuario la caja no existe, y marcar
    # 'Navegando' vs 'NAVEGANDO' como divergencia entrenaria a ignorar el aviso.
    afirmar("NAVEGANDO" in estados,
            "y sigue mostrando el del sistema anterior", estados.strip()[:150])
    afirmar("sistema anterior todavía no tiene este paso" in aviso,
            "avisa del desfase", aviso.strip()[:140])
    afirmar("no se pierde" in aviso,
            "y aclara que el dato esta guardado", aviso.strip()[:140])
    afirmar("error" not in aviso.lower() and "problema" not in aviso.lower(),
            "sin lenguaje de error: es esperado, no una falla")

    # -- y la comprobacion que cierra el bug: las dos pantallas de acuerdo --
    codigo_m, html_m = pedir(ruta, "/movimientos/90001")
    afirmar(codigo_m == 200, "Movimientos responde 200", codigo_m)
    visible_m = texto_visible(html_m)
    afirmar("EN ESPERA DYP CONSOLIDADO" in visible_m,
            "Movimientos dice lo mismo que la ficha")

    # ------------------------------------------------------------------ 4
    paso("4. el listado marca la fila que diverge")
    base_con(ruta, "Navegando", [("ingreso", "ZONA DE RECEPCION"),
                                 ("pdi", "EN ESPERA DYP CONSOLIDADO")])
    codigo, html = pedir(ruta, "/unidades/?q=VINDEPRUEBA123456&fragmento=1")
    afirmar(codigo == 200, "el listado responde 200", codigo)
    afirmar("marca-regla" in html, "la fila lleva la marca de divergencia")
    afirmar("EN ESPERA DYP CONSOLIDADO" in html,
            "y dice el estado de REGLA, no solo que difiere")
    afirmar("Navegando" in html, "sin sacar el crudo, que es por lo que filtra")

    # Con los dos iguales NO tiene que marcar nada: una marca que aparece
    # siempre deja de significar algo.
    base_con(ruta, "EN ESPERA DYP CONSOLIDADO",
             [("pdi", "EN ESPERA DYP CONSOLIDADO")])
    codigo, html = pedir(ruta, "/unidades/?q=VINDEPRUEBA123456&fragmento=1")
    afirmar("marca-regla" not in html, "no marca cuando coinciden")

    # ------------------------------------------------------------------ 5
    paso("5. el buscador de taller muestra la verdad operativa")
    base_con(ruta, "Navegando", [("ingreso", "ZONA DE RECEPCION"),
                                 ("pdi", "EN ESPERA DYP CONSOLIDADO")])
    codigo, html = pedir(ruta, "/taller/pdi?q=VINDEPRUEBA123456&fragmento=1")
    afirmar(codigo == 200, "el buscador responde 200", codigo)
    visible = texto_visible(html)
    afirmar("EN ESPERA DYP CONSOLIDADO" in visible,
            "muestra el estado de REGLA")
    # El de REGLA va PRIMERO: es el que el movilizador tiene que leer.
    afirmar(visible.find("EN ESPERA DYP CONSOLIDADO") < visible.find("Navegando"),
            "y lo pone antes que el crudo", visible.strip()[:200])
    afirmar("sist. anterior" in visible,
            "el crudo queda como referencia, rotulado")

    base_con(ruta, "STOCK", [])
    codigo, html = pedir(ruta, "/taller/pdi?q=VINDEPRUEBA123456&fragmento=1")
    afirmar("estado-crudo" not in html,
            "sin movimientos en REGLA muestra el crudo a secas")

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    if fallos:
        print("FALLARON {} comprobaciones:".format(len(fallos)))
        for f in fallos:
            print("  - {}".format(f))
        return 1
    print("los 5 casos de los dos estados pasaron")
    return 0


if __name__ == "__main__":
    sys.exit(main())
