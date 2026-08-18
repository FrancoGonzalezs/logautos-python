"""
modulos/unidades.py -- el modulo de Unidades, equivalente en Python de lo que
en el sistema PHP hacen Pedido.php / Pedido_model.php sobre newstocks_cidef.

Dos cosas que se comprobaron sobre el dato real y que definen como esta
escrito este modulo:

1. newstocks_cidef NO es "una fila por vehiculo". Tiene 71.546 filas para
   61.447 VIN distintos: 6.182 VIN aparecen mas de una vez, uno de ellos 14
   veces, y cada repeticion trae su propia fecha de despacho y su propia
   guia, en orden cronologico. O sea que cada fila es UN PASO del vehiculo
   por el flujo logistico, no el vehiculo. Por eso el listado se llama de
   unidades (pasadas) y la busqueda por VIN devuelve varias -- mostrar solo
   una seria esconder historia real.

2. De las 144 columnas de la tabla, la funcion `stocklogautos()` del PHP usa
   80. Esas 80 se reparten en dos mitades y EN EL ORDEN en que aparecen en la
   funcion real: las primeras 40 en el listado (lo que se ve siempre) y las
   otras 40 en la ficha (solo al entrar por el id). Las 64 columnas restantes
   de la tabla no se muestran: no estan activas en el sistema.
"""

import calendar
import re
from datetime import datetime

from flask import Blueprint, render_template, request

from core import columnas_de, consultar, escalar
from modulos.catalogos import normalizar

bp = Blueprint("unidades", __name__, url_prefix="/unidades")

TABLA = "newstocks_cidef"
POR_PAGINA = 50

# La columna que manda el estado operativo de la unidad es `despachado`, no
# `estadostock`. Es contraintuitivo por el nombre -- suena a un booleano de
# "ya salio" -- pero es donde vive el estado del flujo completo: DESPACHADO
# (69.825), Navegando, STOCK, ZONA DE DESPACHO, ZONA DE RECEPCION, RECHAZADO,
# y los estados de DyP y taller.
COLUMNA_ESTADO = "despachado"

# Las primeras 40 de `stocklogautos()`, en su orden original. `id` va primero
# porque es la columna por la que se entra a la ficha.
CAMPOS_LISTADO = [
    "id", "crear_guia", "vin", "patente", "clientecompleto", "marca", "modelo",
    "color", "tapiz", "n_motor", "destino", "horario", "equipamiento",
    "laminado", "despachado", "patio", "calle", "ubicacion", "updated_by",
    "fecha_pdi", "fecha_pdi_2", "fecha_zd", "fecha_desp", "created_at",
    "updated_at", "n_solicitud", "fecha_solicitud", "fecha_programacion",
    "fecha_programacion_laminado", "kilometraje", "estado_carflex", "situacion",
    "guia_desp", "fecha_emi_guia", "transporte", "g_ingreso", "ingreso",
    "fecha_lavado_y_combustible", "fecha_lavado_produccion",
    "fecha_segundo_lavado",
]

# Las otras 40, tambien en el orden de la funcion.
#
# Cuatro de estas NO son columnas de newstocks_cidef -- se comprobo contra las
# 121 tablas del dump -- y las cuatro se resuelven por calculo:
#   - `fecha_inspeccion` sale de `inspeccion_despacho` (existe tambien en
#     `detalles_unidades_2`, pero esa tabla no tiene ni una fila en el dump).
#   - `fecha_revision_contenedor` sale de `contenedor`, cruzando por `vines`.
#   - `cant_danos_dyp` y `cant_danos_aprob_cliente` no existen en ninguna
#     tabla: se cuentan sobre las columnas de texto `observaciones` y
#     `ob_dyp2`, donde el sistema viejo apila los daños con un separador.
CAMPOS_FICHA = [
    "fecha_laminado", "fecha_estimada_dyp", "fecha_proveedor_dyp",
    "fecha_cierre_dyp", "fecha_revision_salida", "fecha_cc", "aceite_coco",
    "sistema_audio", "adblue", "aceite_diferencial", "proveedor_dyp",
    "motonave", "carpeta", "origen", "observacion_general", "fecha_check_list",
    "fecha_check_list_mecanica", "fecha_inspeccion", "estado_check_list",
    "observaciones", "cant_danos_dyp", "ob_dyp2", "fecha_revision_cidef",
    "cant_danos_aprob_cliente", "ob_faltante", "ob_mecanica", "observacion_it",
    "estado_it", "bateria", "a_c", "obs_no_visible", "tipo_lavado",
    "rut_encargado_retiro", "nombre_encargado_retiro", "tipo_destino",
    "modalidad", "fecha_revision_contenedor", "tipo_combu", "scanner",
    "enviado_cidef_revision",
]

# Titulos legibles para la cabecera del listado. Los que no estan aca salen con
# el nombre crudo de la columna, que para `fecha_*` ya se entiende solo.
TITULOS = {
    "id": "id",
    "crear_guia": "Crear guia",
    "vin": "VIN",
    "patente": "Patente",
    "clientecompleto": "Cliente",
    "marca": "Marca",
    "modelo": "Modelo",
    "color": "Color",
    "tapiz": "Tapiz",
    "n_motor": "N° motor",
    "destino": "Destino",
    "horario": "Horario",
    "equipamiento": "Equipamiento",
    "laminado": "Laminado",
    "despachado": "Estado",
    "patio": "Patio",
    "calle": "Calle",
    "ubicacion": "Ubicacion",
    "updated_by": "Modificado por",
    "kilometraje": "Km",
    "estado_carflex": "Estado Carflex",
    "situacion": "Situacion",
    "guia_desp": "Guia despacho",
    "transporte": "Transporte",
    "g_ingreso": "Guia ingreso",
    "ingreso": "Ingreso",
    "n_solicitud": "N° solicitud",
}


def titulo_de(campo):
    return TITULOS.get(campo, campo)

# Campos por los que busca el cuadro de texto unico de arriba. Se eligieron
# los que un operador tiene a mano en papel: el VIN, la patente, el motor,
# el numero de guia y el cliente.
COLUMNAS_BUSQUEDA = ["vin", "patente", "n_motor", "guia_desp", "clientecompleto", "pedido"]

def campos_de_ficha(fila, calculados=None):
    """Los 40 campos de la ficha, en el orden de `stocklogautos()`.

    Devuelve dicts en vez de tuplas porque cuatro de los 40 no son columnas
    de la tabla (ver el comentario de CAMPOS_FICHA): esos llegan ya resueltos
    en `calculados`, porque leerlos con fila[campo] reventaria. El flag
    `resuelto` se mantiene aunque hoy los 40 lo esten, para que si mañana se
    agrega un campo sin fuente la ficha lo muestre como pendiente en vez de
    romperse."""
    calculados = calculados or {}
    presentes = set(fila.keys())
    campos = []
    for campo in CAMPOS_FICHA:
        if campo in calculados:
            valor, resuelto = calculados[campo], True
        elif campo in presentes:
            valor, resuelto = fila[campo], True
        else:
            valor, resuelto = None, False
        campos.append({
            "campo": campo,
            "titulo": titulo_de(campo),
            "valor": valor,
            "resuelto": resuelto,
        })
    return campos


# ---------------------------------------------------------------------------
# Fotos
# ---------------------------------------------------------------------------

# Las fotos viven en el servidor del sistema viejo. Los campos de check_list
# guardan la URL completa; los `archivoN` de inspeccion_despacho guardan la
# ruta relativa, y hay que anteponerles esto.
BASE_FOTOS = "https://logautos.cl/clientes/"

_re_etiquetas = re.compile(r"<[^>]+>")
_re_br = re.compile(r"<br\s*/?>")
# Una URL termina donde empieza un espacio, una comilla o un '<'. Las tres
# cosas aparecen pegadas a la URL en el dato: comillas cuando viene envuelta
# en <a href="...">, y '<' cuando la sigue una etiqueta.
_re_url = re.compile(r"https?://[^\s\"'<>]+")


def urls_de(texto, base=""):
    """Saca la lista de URLs de un campo de fotos.

    Estos campos no guardan una URL sino varias, y el sistema viejo cambio de
    formato al menos dos veces sin migrar lo anterior, asi que conviven:

      - separador ' | ' (formato actual)
      - separador '<br>' o '<br />' con cada URL numerada ('1- https://...'):
        3.430 filas de check_list, de las cuales 2.087 usan '<br>' SIN barra.
        Detectar solo '<br />' y '<br/>' se comeria esas 2.087.
      - la URL envuelta en un ancla completa: '1- <a href="URL">URL</a>'
      - todo el campo envuelto en '<p>\\r\\n\\t...</p>' de un WYSIWYG, que es
        lo que traen 31 filas de check_list_mecanica.link_unidades
      - una sola URL sin ningun separador (los 12.083 valores de link_guia)

    Se parte primero por <br> y despues cada pedazo por ' | ' en vez de elegir
    uno u otro: hay una fila que usa los dos a la vez, y encadenarlos cuesta
    lo mismo que decidir.

    De cada pedazo se extrae la PRIMERA URL con regex, en vez de cortar por
    'lo que esta antes del http'. Con el formato de ancla, cortar por el http
    dejaria 'URL">URL</a>' -- la del atributo pegada a la del texto."""
    if texto is None or not str(texto).strip():
        return []

    texto = str(texto)
    trozos = []
    for parte in (_re_br.split(texto) if _re_br.search(texto) else [texto]):
        trozos.extend(parte.split(" | "))

    urls = []
    for trozo in trozos:
        encontrada = _re_url.search(trozo)
        if encontrada:
            urls.append(encontrada.group(0))
            continue
        # Sin http: solo puede ser una ruta relativa, y solo tiene sentido si
        # hay base con que completarla (los archivoN de inspeccion_despacho).
        # Sin base se descarta: en los campos de check_list un pedazo sin URL
        # es resto de HTML ('<p>'), y devolverlo tal cual armaria un <img> que
        # apunta al propio Flask en vez de a una foto.
        if not base:
            continue
        limpio = _re_etiquetas.sub(" ", trozo).strip().strip("|").strip()
        limpio = re.sub(r"^\d+\-\s*", "", limpio)
        if limpio:
            urls.append(base + limpio.lstrip("/"))
    return urls


def _fotos_de_inspeccion(fila):
    """Las inspecciones de despacho guardan las fotos en columnas sueltas
    archivo1..archivo9 (no en un campo con separadores). Son nueve columnas,
    no cinco: la fila mas reciente del dump usa las nueve."""
    urls = []
    for n in range(1, 10):
        campo = "archivo{}".format(n)
        if campo in fila.keys():
            urls.extend(urls_de(fila[campo], BASE_FOTOS))
    return urls


# ---------------------------------------------------------------------------
# Las secciones de la ficha
# ---------------------------------------------------------------------------

# check_list_mecanica no tiene ni una fila de CIDEF, y no es que este vacia
# por ahora: nunca la tuvo (2.951 CARFLEX, 3 PIAMONTE, 1 PRUEBA, 1
# PARTICULAR). Para una unidad CIDEF la seccion no se muestra en absoluto --
# mostrarla vacia sugeriria que falta cargar un dato que no existe.
CLIENTES_SIN_CHECK_MECANICO = {"CIDEF"}


def _del_cliente(filas, cliente):
    """Filtra por cliente comparando el valor normalizado.

    No se compara con `=` en el SQL porque la columna trae la misma suciedad
    que el resto del sistema: hay 6 filas guardadas como 'CIDEF ' con espacio
    final y 9 como 'POMPEYO CARRASCO '. Con igualdad exacta esas filas
    desapareceran de la ficha sin que nadie lo note."""
    objetivo = normalizar(cliente)
    return [f for f in filas if normalizar(f["cliente"]) == objetivo]


def _partes_dano(texto):
    """Parte uno de los tres campos paralelos de daños del check list.

    Conviven DOS formatos, igual que en los campos de fotos:

      - el actual, separado por '-':  'PARACH DEL-PTA DEL IZQ-ZOCALO DEL IZQ'
      - el viejo, separado por ' | <br>' y con cada item numerado, que es lo
        que traen 2.909 de las 18.060 filas con daños (el 16%), todas de 2023
        y 2024; desde 2025 no aparece mas.

    El PHP parte siempre por '-', asi que en las filas viejas mezcla pedazos
    de un daño con el numero del siguiente y muestra cosas como
    'PORTALON | <br>8 8 MEDIO'. Aca se detecta el separador antes de partir,
    que es lo mismo que ya se hace con `link`. En las filas del formato nuevo
    el resultado es identico, porque no tienen ni '|' ni '<br>'."""
    crudo = str(texto or "")
    if not crudo.strip():
        return []

    if "<br" in crudo.lower() or " | " in crudo or crudo.strip().endswith("|"):
        trozos = _re_br.split(crudo)
        partes = []
        for t in trozos:
            partes.extend(t.split("|"))
    else:
        partes = crudo.split("-")

    limpias = []
    for parte in partes:
        # Se saca la etiqueta suelta y la numeracion con que el formato viejo
        # prefijaba cada item ('3 - ZOCALO DEL IZQ').
        limpio = _re_etiquetas.sub(" ", parte)
        limpio = re.sub(r"^\s*\d+\s*[-.]?\s+", "", limpio)
        # El guion sobrante aparece cuando una fila del formato viejo mezcla
        # los dos separadores y deja trozos como '-MEDIO'.
        limpias.append(re.sub(r"\s+", " ", limpio).strip().strip("-").strip())
    return limpias


def danos_de_check_list(fila):
    """Empareja cada daño del check list con su foto.

    El check list guarda TRES campos paralelos separados por '-' que se leen
    por el mismo indice -- `observacion` es la pieza, `requerimiento` el tipo
    de daño y `gravedad` la severidad -- mas las fotos de `link`, en el mismo
    orden. Es la logica de views/nota/showcli.php.

    Que los campos sean paralelos y no una tabla de detalle es fragil por
    definicion: alcanza con que alguien escriba un guion dentro del nombre de
    una pieza para que las tres listas se desalineen. Se replica igual porque
    es como esta cargado el dato de tres años.

    Diferencia con el PHP: cuando hay menos fotos que daños, el original tira
    un notice de PHP y sigue con la foto vacia. Aca ese daño se muestra sin
    foto, marcado. No es un capricho -- pasa en 3.760 de las 18.060 filas con
    daños, o sea el 21%: si se rompiera la fila, una de cada cinco unidades
    mostraria mal sus daños."""
    piezas = _partes_dano(fila["observacion"])
    tipos = _partes_dano(fila["requerimiento"])
    gravedades = _partes_dano(fila["gravedad"])
    fotos = urls_de(fila["link"])

    def en(lista, i):
        return lista[i] if i < len(lista) else ""

    danos = []
    for i, pieza in enumerate(piezas):
        if not pieza and not en(tipos, i) and not en(gravedades, i):
            continue
        danos.append({
            "pieza": pieza,
            "tipo": en(tipos, i),
            "gravedad": en(gravedades, i),
            "foto": fotos[i] if i < len(fotos) else None,
        })
    return danos


def check_lists_de(vin, cliente):
    if not vin:
        return []
    filas = consultar(
        "SELECT * FROM check_list WHERE vin = ? ORDER BY fecha_completa DESC, id DESC", (vin,))
    return _del_cliente(filas, cliente)


def check_mecanica_de(vin, cliente):
    if not vin:
        return []
    filas = consultar(
        "SELECT * FROM check_list_mecanica WHERE vin = ? "
        "ORDER BY fecha_creacion_completa DESC, id DESC", (vin,))
    return _del_cliente(filas, cliente)


# Las columnas de check_list_mecanica que son cabecera o metadata. Todo lo
# demas de las 93 es un item revisado, con nombres abreviados internos (tad,
# tat, tca, mdc, hec, pfd...) que el sistema viejo nunca expandio.
CAMPOS_CABECERA_MECANICA = {
    "id", "vin", "patente", "id_vin", "guia", "cliente", "fecha_ingreso",
    "marca", "modelo", "color", "encargado", "estanque", "kilometraje",
    "faltante", "observacion", "estado", "estado_carflex", "fecha_creacion",
    "fecha_creacion_completa", "llaves", "link_unidades", "modalidad",
    "contador", "obs_general", "fallas_adicionales", "fotos_adicionales",
    "modalidad_adicional", "precios", "precios_adicionales",
}


def items_de_mecanica(fila):
    """Los items del check mecanico que traen algo.

    Se muestran solo los que tienen valor porque la tabla es una columna por
    item -- el patron que el analisis de migracion marca para normalizar en
    una tabla de detalle (item, estado). Mientras siga asi, listar los ~64
    items siempre, la mayoria vacios, haria ilegible la ficha."""
    items = []
    for campo in fila.keys():
        if campo in CAMPOS_CABECERA_MECANICA:
            continue
        valor = fila[campo]
        if valor is None or str(valor).strip() == "":
            continue
        items.append({"item": campo, "estado": valor})
    return items


def inspecciones_de(vin):
    """Sin filtro de cliente, a diferencia de los check lists: la inspeccion
    de despacho se hace igual para todos."""
    if not vin:
        return []
    return consultar(
        "SELECT * FROM inspeccion_despacho WHERE vin = ? "
        "ORDER BY fecha_completa DESC, id DESC", (vin,))


def movimientos_de(vin, tope=200):
    """El historial de `registros`.

    La tabla guarda pares actual/anterior (accion/newcalle, estado/newestado,
    patio/newpatio), pero SOLO se leen los actuales: en una lista ordenada por
    fecha, el "anterior" de un movimiento es el "actual" del que sigue mas
    abajo, asi que mostrar las dos mitades es decir dos veces lo mismo.

    `accion` es la posicion: una calle fisica como 'A' o 'G', o una zona
    logica como STOCK, ZD, ZR o DESPACHADO cuando la unidad no esta en una
    calle concreta.

    `fila` y `newfila` no se leen: estan vacias en las 299.322 filas de la
    tabla, no solo en una muestra.

    Se marca cada movimiento como legado cuando no trae ni estado ni patio.
    Se detecta por fila y no por año a proposito: el corte no es 2022 entero
    sino julio de 2022 (1.245 de 1.347 filas sin estado) y parte de agosto --
    de septiembre en adelante el dato viene completo."""
    if not vin:
        return []
    filas = consultar(
        "SELECT id, created_at, created_by, accion, estado, patio, "
        "clientemov, obs FROM registros "
        "WHERE vin = ? ORDER BY created_at DESC, id DESC LIMIT ?", (vin, tope))

    movimientos = []
    for f in filas:
        vacios = [f["estado"], f["patio"]]
        movimientos.append({
            "fila": f,
            "legado": all(v is None or str(v).strip() == "" for v in vacios),
        })
    return movimientos


# ---------------------------------------------------------------------------
# Contadores de daños (los dos ultimos campos calculados de la ficha)
# ---------------------------------------------------------------------------
#
# Ninguno de los dos necesita otra tabla: salen de columnas de texto de
# newstocks_cidef donde el sistema viejo apila los daños con un separador.
#
# Se decide "vacio" con strip() pero se parte SIEMPRE el valor original, sin
# stripear. La diferencia no es cosmetica: las observaciones reales empiezan
# con espacios ('    | <br>SIMUNIZADO COMPLETO ... |') y ese primer ' |' es un
# separador legitimo. Stripear el texto antes de partirlo se comeria un daño
# de cada unidad.

def _trozo_sin_contenido(pedazo):
    """Un trozo que no describe ningun daño.

    Se exige al menos una letra o numero despues de sacar las etiquetas. Los
    tres casos que descarta, en el orden en que fueron apareciendo:

      - el pedazo directamente vacio, el que el separador deja antes del
        primer ' |' y despues del ultimo (11.952 filas contaban de mas);
      - el que sobrevive solo por el '</p>' con que cierra el WYSIWYG en el
        que se editaban las observaciones (otras 6.913);
      - el que queda con simbolos sueltos y ninguna palabra: un '|' huerfano,
        o un '()' de una entrada que se guardo sin describir nada (3.684).

    No alcanza con preguntar si queda algo tras sacar las etiquetas: eso deja
    pasar los simbolos sueltos, que fue justamente el ultimo caso."""
    return not re.search(r"[a-zA-Z0-9]", _re_etiquetas.sub("", pedazo))


def cantidad_danos_dyp(observaciones):
    """Cuenta los daños apilados en `observaciones`, separados por ' |'.

    Cuenta DAÑOS, no pedazos: se descartan los trozos que no describen nada
    (ver _trozo_sin_contenido). Segun la epoca del registro el formato deja
    uno o dos vacios -- los viejos terminan en ' |' y los nuevos ademas
    empiezan con '    | ' -- asi que contar pedazos a secas devolvia de mas, y
    cuanto de mas dependia de cuando se habia guardado la fila.

    Esto hace que Python muestre un numero distinto al de Logautos.PHP para la
    misma unidad mientras convivan los dos sistemas -- la unidad 92090 da 4 aca
    y 6 alla. Es esperable y esta decidido: el correcto es este.

    Un campo vacio da 0. El PHP daba 1 en ese caso, inconsistente con el otro
    contador, y se decidio no replicar esa inconsistencia; se considera vacio
    tambien el texto de solo espacios (15 filas), que por ser truthy se colaba
    como un daño inexistente.

    Ojo si se compara con cantidad_danos_aprobados: ese resta uno en vez de
    filtrar, y no es un descuido. Ahi el separador ' /' solo deja vacio al
    final, y filtrar cambiaria el resultado de los valores que no traen
    separador ('DAÑO NO AUTORIZADO POR CLIENTE' debe dar 0, no 1)."""
    return len(danos_de_observaciones(observaciones))


def danos_de_observaciones(observaciones):
    """Los daños de `observaciones`, uno por elemento y ya legibles.

    Es la misma pasada que cuenta `cant_danos_dyp` -- de hecho el contador es
    len() de esto -- para que el numero de la tarjeta y la cantidad de items
    de la lista nunca puedan discrepar."""
    texto = observaciones or ""
    if not texto.strip():
        return []

    danos = []
    for trozo in texto.split(" |"):
        if _trozo_sin_contenido(trozo):
            continue
        limpio = re.sub(r"\s+", " ", _re_etiquetas.sub(" ", trozo)).strip()
        if limpio:
            danos.append(limpio)
    return danos


def cantidad_danos_aprobados(ob_dyp2):
    """Cuenta los daños aprobados por el cliente en `ob_dyp2`, separados por
    ' /'. Se resta uno porque el campo termina en el separador ('PINTAR ZOCALO
    DEL IZQ / '), asi que partir deja un ultimo pedazo vacio.

    El max(..., 0) evita negativos y el campo vacio ya daba 0 en el PHP; esto
    solo lo hace explicito."""
    texto = ob_dyp2 or ""
    if not texto.strip():
        return 0
    return max(len(texto.split(" /")) - 1, 0)


def fecha_inspeccion_de(vin):
    """Resuelve `fecha_inspeccion`, uno de los campos que en el PHP sale de un
    JOIN. Se usa `fecha_completa` porque viene poblada en las 16.365 filas,
    mientras que `fecha_despacho` solo en 4.171.

    El ORDER BY ... LIMIT 1 es el arreglo acordado: 2.030 VIN tienen mas de
    una inspeccion, y sin orden explicito cual de ellas gana depende de como
    el motor recorra la tabla. Se toma la mas reciente, y queda determinista."""
    if not vin:
        return None
    return escalar(
        "SELECT fecha_completa FROM inspeccion_despacho WHERE vin = ? "
        "ORDER BY fecha_completa DESC LIMIT 1", (vin,))


def fecha_revision_contenedor_de(vin):
    """Resuelve `fecha_revision_contenedor` desde `contenedor`.

    El cruce es por texto: `contenedor.vines` guarda todos los VIN del
    contenedor en un solo campo separados por ' | ', asi que no hay forma de
    hacerlo por igualdad. Con 2.448 contenedores el LIKE recorre poco y se
    consulta una sola vez por ficha.

    Resuelve para el ~39% de las unidades recientes: los contenedores del
    dump arrancan en 2025-01-21, asi que una unidad anterior a esa fecha no
    tiene con que cruzarse. Mismo ORDER BY ... LIMIT 1 que arriba."""
    if not vin:
        return None
    return escalar(
        "SELECT fecha FROM contenedor WHERE vines LIKE ? ORDER BY fecha DESC LIMIT 1",
        ("%{}%".format(vin),))


def estados_disponibles():
    """Los estados operativos que existen en el dato, plegando las variantes
    sucias sobre su valor canonico.

    Hace falta porque `despachado` trae la misma suciedad que ya se vio en
    `orden_trabajo.requerimiento`: hay 10 filas guardadas como
    '\\tZONA DE DESPACHO' (con tabulador al principio) y 4 como 'navegando'
    en minusculas contra 621 'Navegando'. Si el filtro comparara el texto
    crudo, elegir "ZONA DE DESPACHO" en el desplegable dejaria fuera esas 10
    filas sin que nadie se entere -- que es la peor forma de estar mal.

    Se reusa `normalizar()` de catalogos.py en vez de escribir otra limpieza:
    es el mismo problema y conviene que se arregle en un solo lugar."""
    filas = consultar(
        'SELECT "{}" AS valor, COUNT(*) AS n FROM "{}" GROUP BY 1'.format(COLUMNA_ESTADO, TABLA))

    agrupado = {}
    for fila in filas:
        canonico = normalizar(fila["valor"])
        if not canonico:
            continue
        entrada = agrupado.setdefault(canonico, {"canonico": canonico, "total": 0, "crudos": []})
        entrada["total"] += fila["n"]
        entrada["crudos"].append(fila["valor"])

    return sorted(agrupado.values(), key=lambda e: -e["total"])


# Campos numericos que admiten filtro de mayor/menor, al estilo de los
# filtros de grocery_CRUD del sistema viejo. Solo columnas reales: los campos
# calculados de la ficha (cant_danos_dyp y compania) no se pueden filtrar en
# SQL porque no existen como columna.
CAMPOS_NUMERICOS = [
    ("kilometraje", "Kilometraje"),
    ("guia_desp", "Guía de despacho"),
    ("g_ingreso", "Guía de ingreso"),
    ("n_solicitud", "N° solicitud"),
    ("cantidad_combustible", "Cantidad combustible"),
    ("precio_total_combustible", "Precio combustible"),
    ("anio", "Año"),
    ("totaldiasacopio", "Días de acopio"),
]

# Campos de fecha que admiten filtro de rango.
CAMPOS_FECHA = [
    ("ingreso", "Ingreso"),
    ("fecha_desp", "Despacho"),
    ("fecha_pdi", "PDI"),
    ("fecha_solicitud", "Solicitud"),
    ("fecha_programacion", "Programación"),
    ("fecha_lavado_y_combustible", "Lavado y combustible"),
    ("fecha_check_list", "Check list"),
    ("created_at", "Creación"),
]


def _filtro_numerico(condiciones, params, campo, minimo, maximo):
    """Compara como NUMERO, no como texto.

    Importa: estas columnas guardan el numero como texto y ahi '9' > '100'.
    CAST a REAL lo arregla. El `<> ''` de adelante evita que las filas vacias
    se cuelen como 0 -- sin eso, filtrar 'kilometraje menor a 100' devolveria
    las 48.000 unidades que no tienen el dato cargado."""
    if campo not in {c for c, _ in CAMPOS_NUMERICOS}:
        return
    base = ('"{c}" IS NOT NULL AND TRIM("{c}") <> \'\' '
            'AND CAST("{c}" AS REAL) {{}} ?').format(c=campo)
    if minimo not in (None, ""):
        condiciones.append("(" + base.format(">=") + ")")
        params.append(minimo)
    if maximo not in (None, ""):
        condiciones.append("(" + base.format("<=") + ")")
        params.append(maximo)


def _filtro_fechas(condiciones, params, campo, desde, hasta):
    """Rango de fechas. Se comparan como texto porque estan en ISO, que ordena
    bien, y asi las fechas cero ('0000-00-00') quedan fuera solas de cualquier
    rango real sin necesidad de excluirlas aparte."""
    if campo not in {c for c, _ in CAMPOS_FECHA}:
        return
    if desde:
        condiciones.append('"{}" >= ?'.format(campo))
        params.append(desde)
    if hasta:
        condiciones.append('"{}" <= ?'.format(campo))
        params.append(hasta)


def _filtros(busqueda, estado, estados, numerico=None, fechas=None):
    """Devuelve (fragmento WHERE, params). El WHERE se arma con placeholders
    siempre -- nunca interpolando el texto del usuario -- porque la busqueda
    entra tal cual desde la barra de direcciones."""
    condiciones = []
    params = []

    if busqueda:
        patron = "%{}%".format(busqueda.strip())
        columnas = [c for c in COLUMNAS_BUSQUEDA if c in columnas_de(TABLA)]
        condiciones.append("(" + " OR ".join('"{}" LIKE ?'.format(c) for c in columnas) + ")")
        params.extend([patron] * len(columnas))

    if estado:
        # Se filtra por TODAS las variantes crudas que caen en el canonico
        # elegido, en vez de normalizar dentro del SQL: asi el motor puede
        # usar el indice de la columna, cosa que con una funcion alrededor
        # del campo no haria.
        crudos = next((e["crudos"] for e in estados if e["canonico"] == estado), None)
        if crudos:
            condiciones.append('"{}" IN ({})'.format(COLUMNA_ESTADO, ", ".join("?" * len(crudos))))
            params.extend(crudos)
        else:
            condiciones.append("1 = 0")  # estado que no existe: no devuelve nada

    if numerico and numerico.get("campo"):
        _filtro_numerico(condiciones, params, numerico["campo"],
                         numerico.get("min"), numerico.get("max"))

    if fechas and fechas.get("campo"):
        _filtro_fechas(condiciones, params, fechas["campo"],
                       fechas.get("desde"), fechas.get("hasta"))

    where = (" WHERE " + " AND ".join(condiciones)) if condiciones else ""
    return where, params


@bp.route("/")
def listado():
    busqueda = request.args.get("q", "").strip()
    estado = request.args.get("estado", "").strip()
    try:
        pagina = max(1, int(request.args.get("pagina", 1)))
    except ValueError:
        pagina = 1

    numerico = {
        "campo": request.args.get("num_campo", "").strip(),
        "min": request.args.get("num_min", "").strip(),
        "max": request.args.get("num_max", "").strip(),
    }
    fechas = {
        "campo": request.args.get("fecha_campo", "").strip(),
        "desde": request.args.get("fecha_desde", "").strip(),
        "hasta": request.args.get("fecha_hasta", "").strip(),
    }
    # "Ingresado en tal mes" es un atajo del rango: se expande a los dos
    # extremos del mes para no tener otro camino de codigo que mantener.
    mes = request.args.get("fecha_mes", "").strip()
    if mes and fechas["campo"] and not (fechas["desde"] or fechas["hasta"]):
        try:
            primero = datetime.strptime(mes + "-01", "%Y-%m-%d").date()
            ultimo = primero.replace(
                day=calendar.monthrange(primero.year, primero.month)[1])
            fechas["desde"] = primero.isoformat()
            fechas["hasta"] = ultimo.isoformat()
        except ValueError:
            mes = ""

    estados = estados_disponibles()
    where, params = _filtros(busqueda, estado, estados, numerico, fechas)

    total = escalar('SELECT COUNT(*) FROM "{}"{}'.format(TABLA, where), params)

    filas = consultar(
        'SELECT {} FROM "{}"{} ORDER BY id DESC LIMIT ? OFFSET ?'.format(
            ", ".join('"{}"'.format(c) for c in CAMPOS_LISTADO), TABLA, where),
        params + [POR_PAGINA, (pagina - 1) * POR_PAGINA])

    columnas = [(c, titulo_de(c)) for c in CAMPOS_LISTADO]

    return render_template(
        "unidades_listado.html",
        filas=filas, columnas=columnas, total=total,
        pagina=pagina, por_pagina=POR_PAGINA,
        paginas=max(1, (total + POR_PAGINA - 1) // POR_PAGINA),
        busqueda=busqueda, estado=estado, estados=estados,
        numerico=numerico, fechas=fechas, mes=mes,
        campos_numericos=CAMPOS_NUMERICOS, campos_fecha=CAMPOS_FECHA,
        hay_filtros_avanzados=bool(
            numerico["campo"] and (numerico["min"] or numerico["max"])
            or fechas["campo"] and (fechas["desde"] or fechas["hasta"])))


@bp.route("/<int:id_unidad>")
def ficha(id_unidad):
    fila = consultar('SELECT * FROM "{}" WHERE id = ?'.format(TABLA), (id_unidad,), una=True)
    if fila is None:
        return render_template("no_encontrado.html", que="unidad", id=id_unidad), 404

    # Las otras pasadas del mismo VIN por el flujo (ver docstring del modulo).
    otras = []
    if fila["vin"]:
        otras = consultar(
            'SELECT id, fecha_desp, guia_desp, "{}" AS estado, patio, created_at '
            'FROM "{}" WHERE vin = ? AND id <> ? ORDER BY id'.format(COLUMNA_ESTADO, TABLA),
            (fila["vin"], id_unidad))

    ots = consultar(
        "SELECT id, requerimiento, estado, situacion, precio, costo, createdDtm "
        "FROM orden_trabajo WHERE id_vehiculo = ? ORDER BY id DESC LIMIT 50",
        (id_unidad,))

    reparaciones = []
    if fila["vin"]:
        reparaciones = consultar(
            "SELECT id_rep, nombre_reparador, detalle, tipo_ot, ot_relacionada, "
            "total, estado, fecha_text FROM reparaciones_externas "
            "WHERE vin = ? ORDER BY id_rep DESC LIMIT 50", (fila["vin"],))

    vin = fila["vin"]
    cliente = fila["clientecompleto"]

    calculados = {
        "fecha_inspeccion": fecha_inspeccion_de(vin),
        "fecha_revision_contenedor": fecha_revision_contenedor_de(vin),
        "cant_danos_dyp": cantidad_danos_dyp(fila["observaciones"]),
        "cant_danos_aprob_cliente": cantidad_danos_aprobados(fila["ob_dyp2"]),
    }

    # Que secciones de check list corresponden depende del cliente de la
    # unidad: CIDEF tiene check list de ingreso pero nunca check mecanico
    # (ver CLIENTES_SIN_CHECK_MECANICO), CARFLEX tiene los dos.
    muestra_mecanica = normalizar(cliente) not in CLIENTES_SIN_CHECK_MECANICO

    check_lists = [{
        "fila": f,
        "danos": danos_de_check_list(f),
        "foto_unidad": urls_de(f["link_unidad"]),
        "foto_guia": urls_de(f["link_guia"]),
    } for f in check_lists_de(vin, cliente)]

    mecanica = []
    if muestra_mecanica:
        mecanica = [{
            "fila": f,
            # La clave NO puede llamarse "items": en Jinja, `m.items` sobre un
            # dict resuelve al metodo .items() del diccionario y no a la clave,
            # asi que la plantilla recibia una funcion en vez de la lista.
            "revisados": items_de_mecanica(f),
            "fotos": urls_de(f["link_unidades"]) + urls_de(f["fotos_adicionales"]),
        } for f in check_mecanica_de(vin, cliente)]

    inspecciones = [
        {"fila": f, "fotos": _fotos_de_inspeccion(f)} for f in inspecciones_de(vin)]

    return render_template(
        "unidad_ficha.html",
        fila=fila, campos=campos_de_ficha(fila, calculados),
        danos_unidad=danos_de_observaciones(fila["observaciones"]),
        otras=otras, ots=ots, reparaciones=reparaciones,
        cliente=cliente,
        check_lists=check_lists,
        mecanica=mecanica, muestra_mecanica=muestra_mecanica,
        inspecciones=inspecciones,
        movimientos=movimientos_de(vin))
