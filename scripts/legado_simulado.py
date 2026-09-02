#!/usr/bin/env python3
"""
scripts/legado_simulado.py -- un `Api_regla.php` de mentira, para probar el
push sin mandarle trafico de escritura a produccion.

Existe por el mismo criterio con el que se probo el pull: el primer contacto de
codigo nuevo con el legado no puede ser una escritura contra la base real. Este
script implementa el MISMO contrato que el PHP -- la API key en X-API-Key, el
locking optimista con 409, la idempotencia por Idempotency-Key -- contra un
diccionario en memoria.

Y ademas sabe fallar a pedido, que es la parte que el PHP no puede hacer:

    --lista-blanca desplegada|con_pdi|con_check_list   que columnas acepta el PUT
    --modo ok         responde bien (por defecto)
    --modo conflicto  responde 409 siempre, con datos_actuales
    --modo caer       responde 500 siempre: prueba el backoff
    --modo perder     aplica el cambio y CORTA sin responder

`perder` es el importante. Reproduce el unico escenario donde la idempotencia
del PUT se gana el lugar: el legado aplico el cambio, la respuesta se perdio, y
el reintento manda el mismo legado_updated_at_conocido contra un updated_at que
ya avanzo. Sin la Idempotency-Key eso da un 409 contra nosotros mismos. Con
ella, el segundo intento devuelve la respuesta del primero.

Como se corre
-------------
    python scripts/legado_simulado.py --puerto 8770 --sembrar-id 80405
    python scripts/legado_simulado.py --puerto 8770 --modo perder

y del otro lado:

    python -m modulos.push_legado pendientes --simulado http://127.0.0.1:8770

La API key que exige sale de LEGADO_API_KEY, igual que la real. Si no esta
definida acepta 'x', para no obligar a configurar nada en una prueba local.
"""

import argparse
import json
import re
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# Las "unidades" del legado de mentira: id -> dict de columnas. Se siembra con
# --sembrar-id y se puede mirar con GET /_estado.
UNIDADES = {}

# idem_key -> la respuesta que se dio la primera vez. Es `api_idempotency`.
IDEMPOTENCIA = {}

# La tabla `registros` del legado de mentira. Es APPEND-ONLY, igual que la real
# -- nada la borra ni la edita -- y por eso es una lista y no un dict.
REGISTROS = []


# ---------------------------------------------------------------------------
# La lista blanca, que es parte del contrato y no un detalle
# ---------------------------------------------------------------------------
#
# `Api_regla::columnas_permitidas` decide QUE puede escribir REGLA, y lo que no
# esta en la lista SE IGNORA EN SILENCIO: 200, cero efecto, cola resuelta sin
# error. Es el modo de falla mas caro del sistema porque no deja rastro.
#
# Por eso el simulado tiene TRES listas y se elige con `--lista-blanca`:
#
#   desplegada  las cinco del IT. Es lo que hay en produccion HOY.
#   con_pdi     las cinco mas las catorce de la PDI, o sea despues del
#               despliegue de scripts/Api_regla_pdi.php.
#
# No es una comodidad: es lo que deja PROBAR la diferencia. Una prueba del
# circuito sobre PDI contra `desplegada` tiene que FALLAR, y contra `con_pdi`
# tiene que pasar. Si pasa con las dos, la prueba no esta mirando nada.
LISTAS_BLANCAS = {
    # Las del array `$it` de Pedido.php:9219, menos updated_at. Desplegadas el
    # 2026-08-26.
    "desplegada": (
        "estado_it", "observacion_it", "despachado", "calle", "updated_by",
    ),
    # Mas las de la PDI. Ver el bloque B de scripts/Api_regla_pdi.php para de
    # donde sale cada una.
    "con_pdi": (
        "estado_it", "observacion_it", "despachado", "calle", "updated_by",
        "fecha_pdi", "mes_pdi", "mespdinombre", "estadostock", "ubicacion",
        "tipo_combu", "bateria", "scanner", "a_c", "ob_mecanica",
        "aceite_coco", "sistema_audio", "adblue", "aceite_diferencial",
    ),
    # Y la del check list: `fecha_check_list_mecanica`, que la agrega el
    # BLOQUE H de scripts/Api_regla_check_list.php.
    #
    # Va en su propia lista y NO en las de arriba porque las de arriba dicen
    # que hay DESPLEGADO, y el bloque H todavia no lo esta. Mientras no lo
    # este, esa columna se ignora en silencio en produccion -- 200, cero
    # efecto -- y el doble tiene que poder mostrar exactamente eso.
    "con_check_list": (
        "estado_it", "observacion_it", "despachado", "calle", "updated_by",
        "fecha_pdi", "mes_pdi", "mespdinombre", "estadostock", "ubicacion",
        "tipo_combu", "bateria", "scanner", "a_c", "ob_mecanica",
        "aceite_coco", "sistema_audio", "adblue", "aceite_diferencial",
        "fecha_check_list_mecanica",
    ),
}

COLUMNAS_PERMITIDAS = LISTAS_BLANCAS["desplegada"]

# Lo que se ignoro, para que la prueba pueda AFIRMAR sobre eso en vez de
# adivinar por que la columna quedo sin escribir.
IGNORADAS = set()

# `orden_trabajo` del legado de mentira. Append-only, igual que la real.
OT = []

# `stock_consumibles`: las DOS filas reales del legado, con los valores de
# verdad. El diesel en 5 NO es un numero de adorno -- esta por debajo del
# umbral de 20, asi que es el camino que la compuerta frena hoy.
STOCK = {
    2: {"id": 2, "nombre": "DIESEL", "stock": 5, "precio": 1500, "promedio": 1091},
    3: {"id": 3, "nombre": "BENCINA", "stock": 563, "precio": 1500, "promedio": 1188},
}

# `check_list_mecanica` del legado de mentira. Las filas nacen por POST y las
# fallas se les van CONCATENANDO por PUT, exactamente como del otro lado.
CHECK_LIST_MECANICA = {}
PROXIMO_CHECK_ID = [1]

# Las 82 de la lista blanca del bloque G. Se escriben enteras y no se generan:
# la gracia de un doble es que su lista blanca sea INDEPENDIENTE de la de
# Python. Si las dos salieran del mismo lugar, la prueba no podria descubrir
# que una columna que Python manda no esta permitida del otro lado -- que es
# el silencio que ya nos costo el caso 0 de probar_circulo.
CLM_PERMITIDAS = set("""
vin patente id_vin guia cliente fecha_ingreso marca modelo color encargado
estanque kilometraje estado_carflex fecha_creacion fecha_creacion_completa
llaves tad tat tca bateria alternador bocina tdi mdc Limpiaparabrisas er cc
ae Sunroof Chapas Airbag fa vc Bluetooth Neblineros Gata Extintor Llantas
Radio tet aa sd st sdd hec pfd pft dfd dft fde cda etv mdr Radiador dde
cdac cdac2 cbbe fda mdp pef cdas ftcdt sdacdt nam nlr nldf nldh nlde nata
fadm flr flshde fldd fatma fdaed nd nt nr pocc obs_general estado
""".split())

# Las del bloque K, con su verbo. `acumulan` concatena con ' | ', `suman`
# incrementa. Cualquier otra cosa que llegue se IGNORA -- como del otro lado.
CLM_FALLA_ACUMULAN = ("observacion", "modalidad", "link_unidades",
                      "fallas_adicionales", "modalidad_adicional",
                      "fotos_adicionales")
CLM_FALLA_SUMAN = ("contador",)

MODO = "ok"
CLAVE = "x"

# Cuantas veces se llamo a cada cosa. Lo lee la prueba manual para confirmar
# que el reintento efectivamente ocurrio.
CONTADORES = {"put": 0, "post": 0, "conflicto": 0, "idempotente": 0,
              "perdidas": 0, "ignoradas": 0}


# Las columnas de la lista blanca de `Api_regla::columnas_permitidas`, con
# valores como los de una unidad que todavia no paso por el IT.
#
# NO es decoracion: el 409 devuelve `datos_actuales` con estas columnas, y es
# de ahi que sale el detalle del aviso de conflicto. Un doble que devolviera
# solo `updated_at` -- como hacia este antes -- deja pasar un aviso vacio sin
# que ninguna prueba se queje. Comprobado contra produccion: el 409 real trae
# estado_it, observacion_it, despachado, calle y updated_by.
def _unidad_nueva(legado_id, updated_at="2026-08-26 11:00:00", **columnas):
    fila = {"id": legado_id, "updated_at": columnas.pop("updated_at", updated_at),
            "estado_it": None, "observacion_it": None,
            "despachado": "ZONA DE RECEPCION", "calle": "ZR",
            "patio": "PATIO 1", "vin": "", "clientecompleto": "",
            "updated_by": "47"}
    # `patio`, `vin` y `clientecompleto` no estan en la lista blanca del PUT y
    # antes no hacian falta. Los pide el POST de movimientos: el origen
    # (`newpatio`, `newcalle`) sale de la fila, y sin patio el simulado
    # devolveria un origen vacio que el real no devuelve.
    fila.update({k: v for k, v in columnas.items() if v is not None})
    return fila


def _reloj():
    """El reloj del legado. Los segundos avanzan de a uno por escritura y no
    con el reloj real: asi el updated_at cambia siempre entre dos PUT seguidos,
    que es justo lo que hace falta para probar el locking. Con `date()` real,
    dos escrituras dentro del mismo segundo darian el mismo valor y el
    conflicto no se podria reproducir a mano."""
    _reloj.n += 1
    _reloj.ultimo = "2026-08-26 12:{:02d}:{:02d}".format(
        _reloj.n // 60, _reloj.n % 60)
    return _reloj.ultimo


_reloj.n = 0
# La ultima marca que entrego el reloj. La sirve `cambios/unidades` como
# `hasta`, que es la marca de agua: la pone el legado con SU reloj, nunca
# Python con el suyo. Ver la nota 3 de sync_legado.
_reloj.ultimo = ""


class Handler(BaseHTTPRequestHandler):

    def _json(self, codigo, cuerpo):
        datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def log_message(self, formato, *args):
        sys.stderr.write("  simulado: " + (formato % args) + "\n")

    def do_GET(self):
        if self.path == "/_check_list_mecanica":
            # Puerta de PRUEBA (prefijo `_`): deja mirar la tabla del check
            # list mecanico como quedo. La prueba del circulo no se cree lo que
            # dice la cola -- va a mirar la fila.
            return self._json(200, {"filas": list(
                CHECK_LIST_MECANICA.values())})
        if self.path == "/_estado":
            return self._json(200, {"unidades": UNIDADES,
                                    "registros": REGISTROS,
                                    "ot": OT,
                                    "stock": STOCK,
                                    "lista_blanca": sorted(COLUMNAS_PERMITIDAS),
                                    "ignoradas": sorted(IGNORADAS),
                                    "idempotencia": IDEMPOTENCIA,
                                    "contadores": CONTADORES,
                                    "modo": MODO})

        ruta = self.path.split("?")[0].rstrip("/")
        if ruta == "/api_regla/cambios/unidades":
            return self._cambios_unidades()
        if ruta == "/api_regla/stock_consumibles":
            return self._listar_stock()

        return self._json(404, {"error": "ruta no simulada: " + self.path})

    # -- GET /api_regla/cambios/unidades -- la punta del PULL -----------------
    #
    # Mismo contrato que `Api_regla::cambios_unidades`: filas / hasta / hay_mas
    # / pagina / limite, filtrando por `updated_at > desde` y ordenando por
    # (updated_at, id).
    #
    # ESTA PUNTA EXISTE PARA PODER CERRAR EL CIRCULO ENTERO EN UNA PRUEBA.
    # Hasta que se agrego, el simulado solo sabia recibir el push, y eso deja
    # sin probar justo lo que la reconciliacion mide: que el cambio VUELVA. Un
    # push que devuelve 200 y un legado que quedo con el estado nuevo se ven
    # igual desde Python; la unica forma de distinguirlos es traer la fila de
    # vuelta y compararla.
    def _cambios_unidades(self):
        if self.headers.get("X-API-Key", "") != CLAVE:
            return self._json(401, {"error": "API key invalida o ausente"})

        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query)
        desde = (q.get("desde") or [""])[0]
        limite = int((q.get("limite") or ["500"])[0])
        pagina = int((q.get("pagina") or ["1"])[0])

        filas = [f for f in UNIDADES.values()
                 if not desde or (f.get("updated_at") or "") > desde]
        filas.sort(key=lambda f: ((f.get("updated_at") or ""), f["id"]))

        arranque = (pagina - 1) * limite
        recorte = filas[arranque:arranque + limite + 1]
        hay_mas = len(recorte) > limite
        if hay_mas:
            recorte = recorte[:limite]

        return self._json(200, {
            "filas": recorte,
            # `hasta` es la marca de agua nueva y la pone el LEGADO con SU
            # reloj, igual que el PHP: Python no la calcula ni la parsea.
            "hasta": _reloj.ultimo or "",
            "hay_mas": hay_mas, "pagina": pagina, "limite": limite,
        })

    # -- POST /api_regla/movimientos -----------------------------------------
    #
    # Mismo contrato que `Api_regla_movimientos.php`: las DOS escrituras --
    # la fila de `registros` y el UPDATE de la unidad -- o las dos o ninguna.
    #
    # Y con la parte que importa para la prueba de aceptacion: el ORIGEN
    # (`new*`) lo lee de la fila ANTES de pisarla, como hace el PHP. Si el
    # simulado copiara lo que le mandan, la prueba no distinguiria un endpoint
    # que resuelve el origen de uno que lo inventa.

    # -- POST /api_regla/pdi/{id}/ot -----------------------------------------
    #
    # REGLA 4: rechaza lo mismo que el real. La lista sale de las sondas
    # corridas contra el endpoint desplegado el 2026-08-27:
    #
    #   sin X-API-Key ............ 401
    #   sin Idempotency-Key ...... 400 falta_idempotency_key
    #   tipo_combu no valido ..... 400 combustible_desconocido
    #   sin fecha_pdi ............ 400
    #   unidad inexistente ....... 404
    #
    # Y CALCULA EL PRECIO ACA, como el real -- no lo copia del cuerpo, porque
    # el cuerpo no lo lleva. Un doble que devolviera un precio fijo no probaria
    # nada: el punto de este endpoint es justamente que hay dos
    # implementaciones que tienen que coincidir.
    def _crear_ot_pdi(self, unidad_id):
        if self.headers.get("X-API-Key", "") != CLAVE:
            return self._json(401, {"error": "API key invalida o ausente"})

        largo = int(self.headers.get("Content-Length") or 0)
        try:
            datos = json.loads(self.rfile.read(largo).decode("utf-8"))
        except ValueError:
            return self._json(400, {"error": "body invalido"})

        fecha = (datos.get("fecha_pdi") or "").strip()
        combu = (datos.get("tipo_combu") or "").strip()
        if not fecha:
            return self._json(400, {"error": "fecha_pdi es obligatoria"})
        # Los TRES exactos, sensible a la caja, como el `in_array(..., TRUE)`
        # del PHP. 'GASOLINA' cae aca.
        if combu not in ("Bencina", "Diesel", "Electrico"):
            return self._json(400, {
                "error": "tipo_combu tiene que ser Bencina, Diesel o "
                         "Electrico. Recibido: " + combu,
                "codigo": "combustible_desconocido"})

        idem = self.headers.get("Idempotency-Key", "")
        if not idem:
            return self._json(400, {
                "error": "Idempotency-Key es obligatoria",
                "codigo": "falta_idempotency_key"})
        if idem in IDEMPOTENCIA:
            previo = dict(IDEMPOTENCIA[idem])
            previo["idempotente"] = True
            # El 200 idempotente NO trae precios: `api_idempotency` guarda el
            # id y nada mas. Se replica la ausencia, que es justo lo que la
            # prueba afirma.
            previo.pop("precios", None)
            return self._json(200, previo)

        fila = UNIDADES.get(int(unidad_id))
        if fila is None:
            return self._json(404, {
                "error": "unidad no encontrada: {}".format(unidad_id)})

        precio_pdi = 49000 if fecha >= "2026-06-03" else 46878
        marca = (fila.get("marca") or "").strip().upper()
        modelo = (fila.get("modelo") or "").strip().upper()
        litros = valor = 0
        if combu in ("Bencina", "Diesel"):
            valor = 1970 if combu == "Bencina" else 2070
            # LA ASIMETRIA: cuatro prefijos en Bencina, UNO en Diesel.
            prefijos = ("G7", "G9", "V7", "V9") if combu == "Bencina" else ("G7",)
            litros = 15 if (marca in ("DFM", "ZNA") or modelo[:2] in prefijos) else 20

        OT.append({"id": 900 + len(OT), "requerimiento": "PDI",
                   "precio": precio_pdi, "unidad_id": int(unidad_id)})
        id_pdi = OT[-1]["id"]
        precios = {"pdi": {"precio": precio_pdi,
                           "con_iva": int(round(precio_pdi * 1.19))}}
        id_combu = None
        if litros:
            precio_combu = int(round(valor * litros))
            OT.append({"id": 900 + len(OT),
                       "requerimiento": "COMBUSTIBLE POR NORMA",
                       "precio": precio_combu, "unidad_id": int(unidad_id)})
            id_combu = OT[-1]["id"]
            precios["combustible"] = {
                "precio": precio_combu,
                "con_iva": int(round(precio_combu * 1.19)),
                "litros": litros, "valor": valor}
        else:
            precios["combustible"] = None

        respuesta = {"ok": True,
                     "ot": {"pdi": id_pdi, "combustible": id_combu},
                     "precios": precios}
        IDEMPOTENCIA[idem] = dict(respuesta)
        return self._json(201, respuesta)

    # -- POST /api_regla/stock_consumibles/{id}/descontar ---------------------
    #
    # REGLA 4 otra vez. Y el 409 de stock insuficiente NO es opcional: es el
    # camino que hoy toma el diesel de verdad (5 litros contra umbral 20).
    def _descontar(self, consumible_id):
        if self.headers.get("X-API-Key", "") != CLAVE:
            return self._json(401, {"error": "API key invalida o ausente"})

        largo = int(self.headers.get("Content-Length") or 0)
        try:
            datos = json.loads(self.rfile.read(largo).decode("utf-8"))
        except ValueError:
            return self._json(400, {"error": "body invalido"})

        cantidad = datos.get("cantidad") or 0
        if cantidad <= 0:
            return self._json(400, {
                "error": "cantidad tiene que ser mayor que cero",
                "codigo": "validacion"})

        idem = self.headers.get("Idempotency-Key", "")
        if not idem:
            return self._json(400, {
                "error": "Idempotency-Key es obligatoria",
                "codigo": "falta_idempotency_key"})
        if idem in IDEMPOTENCIA:
            fila = STOCK.get(int(consumible_id)) or {}
            return self._json(200, {"ok": True, "idempotente": True,
                                    "stock": fila.get("stock")})

        fila = STOCK.get(int(consumible_id))
        if fila is None:
            return self._json(404, {
                "error": "consumible no encontrado: {}".format(consumible_id)})
        if int(fila["stock"]) < cantidad:
            # La resta y la comprobacion son la MISMA operacion del lado real
            # (`WHERE stock >= ?`). Aca alcanza con no restar.
            return self._json(409, {"ok": False, "stock_insuficiente": True,
                                    "stock": int(fila["stock"])})

        fila["stock"] = int(fila["stock"]) - cantidad
        IDEMPOTENCIA[idem] = {"ok": True, "stock": fila["stock"]}
        return self._json(200, {"ok": True, "stock": fila["stock"],
                                "descontado": cantidad})

    # -- GET /api_regla/stock_consumibles -------------------------------------
    #
    # Devuelve las filas enteras y `hasta`, sin `hay_mas` -- igual que el real.
    # Los valores van como STRING, tambien igual: CodeIgniter sobre MySQL
    # devuelve todo como texto, y comprobado contra produccion `stock` llega
    # como '5' y no como 5. Un doble que devolviera enteros escondería que el
    # `int()` de la compuerta hace falta.
    def _listar_stock(self):
        if self.headers.get("X-API-Key", "") != CLAVE:
            return self._json(401, {"error": "API key invalida o ausente"})
        filas = [{k: (str(v) if v is not None else None)
                  for k, v in STOCK[i].items()} for i in sorted(STOCK)]
        return self._json(200, {"filas": filas, "hasta": _reloj.ultimo or ""})

    def _crear_check_list_mecanico(self):
        """POST /api_regla/check_list_mecanica -- el paso 1.

        Devuelve el `id` de la fila creada, que es lo que el paso 2 necesita
        para saber sobre cual escribir."""
        largo = int(self.headers.get("Content-Length") or 0)
        try:
            datos = json.loads(self.rfile.read(largo).decode("utf-8"))
        except ValueError:
            return self._json(400, {"error": "body invalido"})

        idem = self.headers.get("Idempotency-Key", "")
        if idem and idem in IDEMPOTENCIA:
            CONTADORES["idempotente"] += 1
            return self._json(200, dict(IDEMPOTENCIA[idem], idempotente=True))

        fila = {}
        ignoradas = []
        for k, v in datos.items():
            if k == "legado_updated_at_conocido":
                continue
            if k in CLM_PERMITIDAS:
                fila[k] = v
            else:
                ignoradas.append(k)
                IGNORADAS.add(k)
                CONTADORES["ignoradas"] += 1

        nuevo_id = PROXIMO_CHECK_ID[0]
        PROXIMO_CHECK_ID[0] += 1
        fila["id"] = nuevo_id
        # Las seis del paso 2 nacen vacias, como en MySQL.
        for c in CLM_FALLA_ACUMULAN:
            fila.setdefault(c, None)
        fila["contador"] = 0
        CHECK_LIST_MECANICA[nuevo_id] = fila

        # 201 Y SIN `updated_at`, exactamente como el bloque I.
        #
        # Antes esto devolvia 200 y un `updated_at` inventado, y eso TAPABA dos
        # bugs de Python: que `crear()` tenia que aceptar 201, y que el camino
        # de exito pisaba el `updated_at` de la unidad con la cadena vacia
        # cuando la respuesta no traia ninguno. El doble tiene que parecerse al
        # original tambien en lo que NO manda -- ahi es donde un doble
        # generoso se vuelve un sello de goma.
        cuerpo = {"ok": True, "id": nuevo_id, "ignoradas": ignoradas}
        if idem:
            IDEMPOTENCIA[idem] = cuerpo
        return self._json(201, cuerpo)

    def _agregar_falla(self, check_id):
        """PUT /api_regla/check_list_mecanica_falla/<id> -- el paso 2.

        ACUMULA del lado del servidor: es la propiedad que se esta probando.
        Si esto reemplazara en vez de concatenar, la prueba del circulo lo
        veria como una fila con UNA sola falla."""
        largo = int(self.headers.get("Content-Length") or 0)
        try:
            datos = json.loads(self.rfile.read(largo).decode("utf-8"))
        except ValueError:
            return self._json(400, {"error": "body invalido"})

        fila = CHECK_LIST_MECANICA.get(int(check_id))
        if fila is None:
            # El 404 es lo que hace ruidoso un `legado_id` sin completar. Ver
            # `_propagar_id_creado` en push_legado.
            return self._json(404, {"error": "no existe el check list "
                                             + str(check_id)})

        idem = self.headers.get("Idempotency-Key", "")
        if idem and idem in IDEMPOTENCIA:
            CONTADORES["idempotente"] += 1
            return self._json(200, dict(IDEMPOTENCIA[idem], idempotente=True))

        for k, v in datos.items():
            if k == "legado_updated_at_conocido":
                continue
            if k in CLM_FALLA_ACUMULAN:
                if v in ("", None):
                    continue
                previo = fila.get(k)
                fila[k] = v if not previo else "{} | {}".format(previo, v)
            elif k in CLM_FALLA_SUMAN:
                fila[k] = (fila.get(k) or 0) + int(v)
            else:
                IGNORADAS.add(k)
                CONTADORES["ignoradas"] += 1

        cuerpo = {"ok": True, "updated_at": _reloj()}
        if idem:
            IDEMPOTENCIA[idem] = cuerpo
        return self._json(200, cuerpo)

    def do_POST(self):
        # -- POST /_sembrar -- puerta de PRUEBA, no del contrato --------------
        #
        # Carga una unidad entera de una. Existe porque el PUT solo deja tocar
        # la lista blanca -- `despachado`, `calle`, `estado_it`,
        # `observacion_it`, `updated_by` -- y una prueba del circulo necesita
        # sembrar tambien `patio`, `vin` y `clientecompleto`, que son de donde
        # sale el ORIGEN de la fila de `registros`.
        #
        # Va con el prefijo `_` como `/_estado`: lo que NO empieza con `_` es
        # el contrato del legado, lo que empieza con `_` es andamiaje. Sembrar
        # por el PUT habria sido peor -- ampliar la lista blanca del doble para
        # que la prueba pase es justo lo que hace que el doble deje de parecerse
        # al original.
        if self.path.split("?")[0].rstrip("/") == "/_sembrar":
            largo = int(self.headers.get("Content-Length") or 0)
            try:
                fila = json.loads(self.rfile.read(largo).decode("utf-8"))
            except ValueError:
                return self._json(400, {"error": "body invalido"})
            uid = int(fila.get("id") or 0)
            if uid <= 0:
                return self._json(400, {"error": "id es obligatorio"})
            UNIDADES[uid] = _unidad_nueva(uid, **{k: v for k, v in fila.items()
                                                  if k != "id"})
            return self._json(200, {"ok": True, "unidad": UNIDADES[uid]})

        ruta = self.path.split("?")[0].rstrip("/")
        m = re.match(r"^/api_regla/pdi/(\d+)/ot$", ruta)
        if m:
            return self._crear_ot_pdi(m.group(1))
        m = re.match(r"^/api_regla/stock_consumibles/(\d+)/descontar$", ruta)
        if m:
            return self._descontar(m.group(1))

        if self.headers.get("X-API-Key", "") != CLAVE:
            return self._json(401, {"error": "API key invalida o ausente"})

        if ruta == "/api_regla/check_list_mecanica":
            return self._crear_check_list_mecanico()

        if ruta != "/api_regla/movimientos":
            return self._json(404, {"error": "ruta no simulada: " + self.path})

        largo = int(self.headers.get("Content-Length") or 0)
        try:
            datos = json.loads(self.rfile.read(largo).decode("utf-8"))
        except ValueError:
            return self._json(400, {"error": "body invalido: se esperaba JSON"})

        CONTADORES["post"] += 1
        idem = self.headers.get("Idempotency-Key", "")
        if idem and idem in IDEMPOTENCIA:
            CONTADORES["idempotente"] += 1
            respuesta = dict(IDEMPOTENCIA[idem])
            respuesta["idempotente"] = True
            return self._json(200, respuesta)

        if MODO == "caer":
            return self._json(500, {"error": "falla simulada"})

        unidad_id = int(datos.get("unidad_id") or 0)
        if unidad_id <= 0:
            return self._json(400, {"error": "unidad_id es obligatorio"})
        accion = (datos.get("accion") or "").strip()
        estado = (datos.get("estado") or "").strip()
        if not accion or not estado:
            return self._json(400, {
                "error": "accion (calle destino) y estado (estado destino) "
                         "son obligatorios", "codigo": "validacion"})

        fila = UNIDADES.get(unidad_id)
        if fila is None:
            return self._json(404, {
                "error": "unidad no encontrada: {}".format(unidad_id)})

        conocido = (datos.get("legado_updated_at_conocido") or "")
        if MODO == "conflicto" or (conocido and fila["updated_at"] > conocido):
            CONTADORES["conflicto"] += 1
            return self._json(409, {
                "ok": False, "conflicto": True,
                "updated_at": fila["updated_at"],
                "datos_actuales": {k: v for k, v in fila.items() if k != "id"},
            })

        ahora = _reloj()
        patio = (datos.get("patio") or "").strip()

        REGISTROS.append({
            "id": len(REGISTROS) + 1,
            "vin": fila.get("vin", ""),
            # DESTINO, sin prefijo
            "accion": accion, "estado": estado, "patio": patio,
            # ORIGEN, con el prefijo `new` que miente. Se lee de la fila que
            # estamos por pisar, no de lo que mandaron.
            "newcalle": fila.get("calle") or "",
            "newestado": fila.get("despachado") or "",
            "newpatio": fila.get("patio") or "",
            "clientemov": datos.get("clientemov") or "",
            "obs": datos.get("obs") or "",
            "created_by": datos.get("created_by") or 0,
            "created_at": ahora,
        })

        fila["calle"] = accion
        fila["despachado"] = estado
        # El patio solo se pisa si vino con valor: es lo que hace el PHP, y es
        # lo que deja que la PDI mande vacio sin borrarle el patio a la unidad.
        if patio:
            fila["patio"] = patio
        fila["updated_by"] = datos.get("created_by") or fila.get("updated_by")
        fila["updated_at"] = ahora

        respuesta = {"ok": True, "id": REGISTROS[-1]["id"],
                     "updated_at": ahora}
        if idem:
            IDEMPOTENCIA[idem] = dict(respuesta)
        return self._json(201, respuesta)

    def do_PUT(self):
        ruta_put = self.path.split("?")[0].rstrip("/")
        mf = re.match(r"^/api_regla/check_list_mecanica_falla/(\d+)$",
                      ruta_put)
        if mf:
            if self.headers.get("X-API-Key", "") != CLAVE:
                return self._json(401, {"error": "API key invalida"})
            return self._agregar_falla(mf.group(1))

        # -- misma verificacion que exigir_api_key() del PHP ------------------
        if self.headers.get("X-API-Key", "") != CLAVE:
            return self._json(401, {"error": "API key invalida o ausente"})

        # El User-Agent no se valida aca (el filtro es del hosting, no del
        # PHP), pero se loguea: si el push alguna vez lo pierde, se ve.
        ua = self.headers.get("User-Agent", "")
        if "python-requests" in ua:
            self.log_message("OJO: User-Agent %r -- el hosting real corta la "
                             "conexion con este UA", ua)

        partes = [p for p in self.path.split("?")[0].split("/") if p]
        if len(partes) != 3 or partes[0] != "api_regla" or partes[1] != "unidades":
            return self._json(404, {"error": "ruta no simulada: " + self.path})
        try:
            legado_id = int(partes[2])
        except ValueError:
            return self._json(400, {"error": "id no numerico"})

        largo = int(self.headers.get("Content-Length") or 0)
        try:
            datos = json.loads(self.rfile.read(largo).decode("utf-8"))
        except ValueError:
            return self._json(400, {"error": "body invalido: se esperaba JSON"})

        CONTADORES["put"] += 1
        idem = self.headers.get("Idempotency-Key", "")

        # -- idempotencia: va ANTES de comparar timestamps --------------------
        # Este orden es el que cierra el conflicto contra uno mismo. Ver el
        # encabezado.
        if idem and idem in IDEMPOTENCIA:
            CONTADORES["idempotente"] += 1
            respuesta = dict(IDEMPOTENCIA[idem])
            respuesta["idempotente"] = True
            return self._json(200, respuesta)

        if MODO == "caer":
            return self._json(500, {"error": "falla simulada"})

        fila = UNIDADES.setdefault(legado_id, _unidad_nueva(legado_id))

        # -- locking optimista ------------------------------------------------
        conocido = datos.pop("legado_updated_at_conocido", "")
        hay_conflicto = MODO == "conflicto" or (
            conocido != "" and fila["updated_at"] > conocido)
        if hay_conflicto:
            CONTADORES["conflicto"] += 1
            return self._json(409, {
                "ok": False, "conflicto": True,
                "updated_at": fila["updated_at"],
                "datos_actuales": {k: v for k, v in fila.items() if k != "id"},
            })

        # -- se aplica, PERO SOLO LA LISTA BLANCA -----------------------------
        #
        # Lo que no esta en la lista se IGNORA EN SILENCIO, igual que el PHP.
        # Hasta el 2026-08-27 este doble hacia `fila.update($datos)` a secas --
        # aceptaba todo -- y esa era una mentira comoda: una prueba del
        # circuito sobre una entidad nueva pasaba en verde con las columnas sin
        # desplegar del otro lado, que es EXACTAMENTE el modo de falla que la
        # lista blanca produce en produccion (200, cero efecto, cola resuelta).
        #
        # Un doble que acepta mas que el original no prueba de menos: prueba al
        # reves. Es el mismo patron que cuando solo sabia el PUT y no el pull.
        ignoradas = sorted(k for k in datos if k not in COLUMNAS_PERMITIDAS)
        if ignoradas:
            CONTADORES["ignoradas"] += len(ignoradas)
            IGNORADAS.update(ignoradas)
            self.log_message("lista blanca: se ignoraron %s", ", ".join(ignoradas))
        fila.update({k: v for k, v in datos.items()
                     if k in COLUMNAS_PERMITIDAS})
        fila["updated_at"] = _reloj()
        respuesta = {"ok": True, "updated_at": fila["updated_at"]}
        if idem:
            IDEMPOTENCIA[idem] = dict(respuesta)

        if MODO == "perder":
            # El cambio YA se aplico. Se corta sin responder, que es lo que se
            # ve desde Python como una respuesta perdida.
            CONTADORES["perdidas"] += 1
            self.log_message("aplicado y CORTADO sin responder (modo perder)")
            self.close_connection = True
            try:
                self.connection.close()
            except OSError:
                pass
            return

        return self._json(200, respuesta)


def main(argv=None):
    global MODO, CLAVE
    ap = argparse.ArgumentParser(description="Api_regla.php de mentira")
    ap.add_argument("--puerto", type=int, default=8770)
    ap.add_argument("--modo", default="ok",
                    choices=("ok", "conflicto", "caer", "perder"))
    ap.add_argument("--lista-blanca", default="desplegada",
                    choices=tuple(LISTAS_BLANCAS),
                    help="que columnas acepta el PUT: 'desplegada' es lo que "
                         "hay en produccion hoy; 'con_pdi' es despues de "
                         "desplegar Api_regla_pdi.php")
    ap.add_argument("--sembrar-id", type=int, action="append", default=[],
                    help="id de unidad que ya existe del otro lado")
    ap.add_argument("--updated-at", default="2026-08-26 11:00:00",
                    help="el updated_at con el que nacen las sembradas")
    args = ap.parse_args(argv)

    MODO = args.modo
    global COLUMNAS_PERMITIDAS
    COLUMNAS_PERMITIDAS = LISTAS_BLANCAS[args.lista_blanca]
    CLAVE = os.environ.get("LEGADO_API_KEY", "x")
    for uid in args.sembrar_id:
        UNIDADES[uid] = _unidad_nueva(uid, args.updated_at)

    servidor = HTTPServer(("127.0.0.1", args.puerto), Handler)
    print("legado simulado en http://127.0.0.1:{}  modo={}  lista={}  unidades={}"
          .format(args.puerto, MODO, args.lista_blanca,
                  sorted(UNIDADES) or "ninguna"))
    print("  estado: curl http://127.0.0.1:{}/_estado".format(args.puerto))
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nlisto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
