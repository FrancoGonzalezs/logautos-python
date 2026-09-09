"""
modulos/correo.py -- el envio de correo del sistema, en un solo lugar.

Es un modulo HOJA a proposito: importa solo la biblioteca estandar y NADA de
`modulos/` ni de `app.py`. Es la misma regla que sigue `core.py`, y aca hace
falta por un motivo concreto: lo usan tanto los modulos de pantalla (Revision
de Contenedor) como `push_legado.py`, que corre en hilos de fondo y desde la
linea de comandos, sin Flask por ningun lado.

Esto vivia dentro de `revision_contenedor.py`, que fue donde nacio, y salio de
ahi cuando aparecio el segundo consumidor. Importar `revision_contenedor` desde
`push_legado` habria arrastrado los blueprints, `movimientos`, `check_list` y
`kpis` a un modulo que hoy no necesita ni Flask -- y ademas metido a
`push_legado` en el ciclo que ya existe entre `unidades` y
`revision_contenedor`. Es un movimiento de codigo, no un mecanismo nuevo: mismo
Resend, mismas variables de entorno, misma cuenta.

Las credenciales salen del entorno y los destinatarios NO tienen valor por
defecto: si nadie los configura no se manda nada y queda anotado en el log. En
el PHP viejo estan en texto plano dentro del codigo, que es justamente lo que
no hay que replicar.

Por que Resend y no SMTP
------------------------
Porque desde Railway el SMTP no sale. Verificado desde el contenedor: los
puertos 25, 465, 587 y 2525 dan timeout contra CUALQUIER proveedor -- el
propio, Gmail y SendGrid --, mientras `mail.logautos.cl` responde en 0,2 s por
80 y 443 y el HTTPS a Internet sale al instante. O sea que el bloqueo es por
puerto y no por destino: es la politica antispam de la red saliente de Railway.
Resend manda por HTTPS, que es justo lo unico que si sale.

Ademas arregla de raiz el 500 del cierre automatico: la conexion SMTP se
colgaba 20 s y, sumada a la subida de la foto, pasaba el timeout del worker de
Gunicorn, que moria antes de que ningun try/except llegara a actuar. Contra
HTTPS no hay nada que esperar, y encima el envio va diferido.

El dominio verificado en Resend es la RAIZ, logautos.cl -- no send.logautos.cl.
Es facil equivocarse leyendo el DNS y ya paso una vez: `send.logautos.cl` tiene
SPF (include:amazonses.com) y MX (feedback-smtp.sa-east-1.amazonses.com), lo
que lo hace PARECER el dominio de envio, pero eso es el subdominio de REBOTES
-- el Return-Path que Resend pide crear al verificar la raiz. La firma que dice
cual es el dominio de envio es el DKIM, y esta en
`resend._domainkey.logautos.cl`.

Mandar desde @send.logautos.cl da: "The send.logautos.cl domain is not
verified".
"""

import os
import socket
import threading

REMITENTE = os.environ.get("RESEND_FROM", "REGLA <notificaciones@logautos.cl>")


def log(estado, asunto, detalle):
    print("[correo] {} | {} | {}".format(estado, asunto, detalle), flush=True)


def destinatarios(clave):
    """Los de la variable de entorno `clave`, separados por coma."""
    return [d.strip() for d in os.environ.get(clave, "").split(",") if d.strip()]


def mandar(destinatarios, asunto, texto, html, adjuntos=(),
           remitente=None, responder_a=None):
    """Manda por la API de Resend. Devuelve (estado, detalle) y NUNCA levanta.

    El correo es una notificacion: que el proveedor este caido no puede hacer
    perder la revision que el operario acaba de cargar, ni tumbar el cierre de
    un contenedor que ya quedo escrito, ni -- desde que lo usa el push -- el
    registro de un conflicto que ya esta en la base."""
    clave = os.environ.get("RESEND_API_KEY")
    if not clave:
        log("no_configurado", asunto, "falta RESEND_API_KEY")
        return "no_configurado", "Falta RESEND_API_KEY."
    if not destinatarios:
        log("no_configurado", asunto, "sin destinatarios configurados")
        return "no_configurado", "No hay destinatarios configurados."

    # ADJUNTOS, Y LOS INCRUSTADOS SON OTRA COSA.
    #
    # Un elemento de `adjuntos` puede ser:
    #   "ruta/a/foto.jpg"                    adjunto comun, se baja aparte
    #   {"ruta": "...", "cid": "foto_1"}     INCRUSTADO: el cuerpo lo muestra
    #                                        con <img src="cid:foto_1">
    #
    # La distincion no es cosmetica y la trajo el IT. Regla PHP manda ese
    # correo con las fotos INCRUSTADAS --`addEmbeddedImage`, no
    # `addAttachment`--, y le llega a `preentrega@cidef.cl`, que es un
    # tercero. Un correo donde las fotos se ven al abrirlo y otro donde hay
    # que bajar seis adjuntos no son el mismo correo para quien lo recibe, y
    # el criterio de este proyecto es que el destinatario no note que cambio
    # el sistema que se lo manda.
    #
    # Resend incrusta cuando el adjunto trae `content_id` y el HTML lo
    # referencia con `cid:`.
    adjuntos_api = []
    for item in adjuntos:
        if isinstance(item, dict):
            ruta, cid = item.get("ruta"), item.get("cid")
        else:
            ruta, cid = item, None
        if not ruta or not os.path.exists(ruta):
            continue
        try:
            with open(ruta, "rb") as f:
                entrada = {
                    "filename": os.path.basename(ruta),
                    "content": list(f.read()),
                }
            if cid:
                entrada["content_id"] = cid
            adjuntos_api.append(entrada)
        except OSError:
            # Una foto ilegible no puede impedir que salga el informe: el
            # cuerpo con la tabla de VIN es lo que el cliente necesita.
            continue

    try:
        import resend
        resend.api_key = clave
        cuerpo = {
            # `remitente` y `responder_a` son opcionales y por defecto None,
            # asi que todo lo que ya llamaba a `mandar` sigue mandando desde
            # REMITENTE. Existen porque la inspeccion de despacho tiene que
            # SALIR IGUAL que la del legado -- mismo From y mismo Reply-To,
            # con el VIN en el nombre -- y el destinatario no tiene por que
            # notar que cambio el sistema que se lo manda.
            #
            # OJO: el dominio de `remitente` tiene que estar verificado en
            # Resend. `logautos.cl` lo esta (la raiz, ver el encabezado);
            # `operaciones@logautos.cl` entra por esa verificacion.
            "from": remitente or REMITENTE,
            "to": destinatarios,
            "subject": asunto,
            "text": texto,
            "html": html,
            "attachments": adjuntos_api,
        }
        if responder_a:
            cuerpo["reply_to"] = responder_a
        r = resend.Emails.send(cuerpo)
        id_correo = (r or {}).get("id") if isinstance(r, dict) else getattr(r, "id", None)
        detalle = "id={} para {}".format(id_correo, ", ".join(destinatarios))
        log("enviado", asunto, detalle)
        return "enviado", detalle
    except Exception as e:                       # noqa: BLE001 -- ver docstring
        # El error REAL de Resend, no un "correo fallo" generico: sin esto hubo
        # que entrar al contenedor a diagnosticar a mano por que no salia.
        detalle = "{}: {}".format(type(e).__name__, e)
        log("error", asunto, detalle)
        return "error", detalle


def en_segundo_plano(funcion, *args):
    """Manda el correo fuera del camino que lo origino.

    Cerrar un contenedor o guardar una diferencia tiene que responderle al
    movilizador de inmediato: el correo es para otra persona y puede tardar lo
    que tarde. El hilo es `daemon` para que no retenga el proceso al apagarse,
    y todo lo que hace adentro ya esta envuelto en su propio try."""
    hilo = threading.Thread(target=funcion, args=args, daemon=True)
    hilo.start()
    return hilo


# ---------------------------------------------------------------------------
# De que replica salio esto
# ---------------------------------------------------------------------------

def origen():
    """Una etiqueta corta que diga desde donde se mando: 'Railway
    (production)', 'notebook (DESKTOP-XYZ)', o lo que diga REGLA_ORIGEN.

    Existe porque hay DOS replicas vivas -- la notebook de Franco y Railway --
    y las dos empujan al mismo legado. Un aviso de conflicto que no diga de
    cual salio obliga a adivinar en que base mirar.

    El orden es deliberado: primero una variable explicita, despues la
    deteccion de Railway, y al final el hostname.

    `core.py` advierte contra atarse a RAILWAY_ENVIRONMENT, y con razon: alla
    se usaria para decidir si estamos en produccion, y equivocarse cae del lado
    inseguro. Aca es una ETIQUETA en un correo. Si el dia de manana esto se
    muda de hosting, lo peor que pasa es que el mail diga 'notebook' donde
    deberia decir otra cosa -- y para eso esta REGLA_ORIGEN, que gana siempre."""
    explicito = (os.environ.get("REGLA_ORIGEN") or "").strip()
    if explicito:
        return explicito
    entorno = (os.environ.get("RAILWAY_ENVIRONMENT_NAME")
               or os.environ.get("RAILWAY_ENVIRONMENT") or "").strip()
    if entorno:
        servicio = (os.environ.get("RAILWAY_SERVICE_NAME") or "").strip()
        return "Railway ({}{})".format(servicio + " / " if servicio else "", entorno)
    try:
        return "notebook ({})".format(socket.gethostname())
    except Exception:                            # pragma: no cover
        return "origen desconocido"
