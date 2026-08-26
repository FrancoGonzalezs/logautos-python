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
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# Las "unidades" del legado de mentira: id -> dict de columnas. Se siembra con
# --sembrar-id y se puede mirar con GET /_estado.
UNIDADES = {}

# idem_key -> la respuesta que se dio la primera vez. Es `api_idempotency`.
IDEMPOTENCIA = {}

MODO = "ok"
CLAVE = "x"

# Cuantas veces se llamo a cada cosa. Lo lee la prueba manual para confirmar
# que el reintento efectivamente ocurrio.
CONTADORES = {"put": 0, "conflicto": 0, "idempotente": 0, "perdidas": 0}


# Las columnas de la lista blanca de `Api_regla::columnas_permitidas`, con
# valores como los de una unidad que todavia no paso por el IT.
#
# NO es decoracion: el 409 devuelve `datos_actuales` con estas columnas, y es
# de ahi que sale el detalle del aviso de conflicto. Un doble que devolviera
# solo `updated_at` -- como hacia este antes -- deja pasar un aviso vacio sin
# que ninguna prueba se queje. Comprobado contra produccion: el 409 real trae
# estado_it, observacion_it, despachado, calle y updated_by.
def _unidad_nueva(legado_id, updated_at="2026-08-26 11:00:00"):
    return {"id": legado_id, "updated_at": updated_at,
            "estado_it": None, "observacion_it": None,
            "despachado": "ZONA DE RECEPCION", "calle": "ZR",
            "updated_by": "47"}


def _reloj():
    """El reloj del legado. Los segundos avanzan de a uno por escritura y no
    con el reloj real: asi el updated_at cambia siempre entre dos PUT seguidos,
    que es justo lo que hace falta para probar el locking. Con `date()` real,
    dos escrituras dentro del mismo segundo darian el mismo valor y el
    conflicto no se podria reproducir a mano."""
    _reloj.n += 1
    return "2026-08-26 12:00:{:02d}".format(_reloj.n % 60)


_reloj.n = 0


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
        if self.path == "/_estado":
            return self._json(200, {"unidades": UNIDADES,
                                    "idempotencia": IDEMPOTENCIA,
                                    "contadores": CONTADORES,
                                    "modo": MODO})
        return self._json(404, {"error": "ruta no simulada: " + self.path})

    def do_PUT(self):
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

        # -- se aplica --------------------------------------------------------
        fila.update(datos)
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
    ap.add_argument("--sembrar-id", type=int, action="append", default=[],
                    help="id de unidad que ya existe del otro lado")
    ap.add_argument("--updated-at", default="2026-08-26 11:00:00",
                    help="el updated_at con el que nacen las sembradas")
    args = ap.parse_args(argv)

    MODO = args.modo
    CLAVE = os.environ.get("LEGADO_API_KEY", "x")
    for uid in args.sembrar_id:
        UNIDADES[uid] = _unidad_nueva(uid, args.updated_at)

    servidor = HTTPServer(("127.0.0.1", args.puerto), Handler)
    print("legado simulado en http://127.0.0.1:{}  modo={}  unidades={}"
          .format(args.puerto, MODO, sorted(UNIDADES) or "ninguna"))
    print("  estado: curl http://127.0.0.1:{}/_estado".format(args.puerto))
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nlisto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
