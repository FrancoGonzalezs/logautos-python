"""
modulos/imagenes.py -- los perfiles de foto, en un solo lugar.

POR QUE EXISTE
==============

Hasta el 2026-09-09 las fotos se guardaban TAL CUAL las mandaba el telefono.
Los perfiles estaban decididos --Franco los eligio mirando la hoja de
comparacion con fotos reales-- y no estaban implementados en ningun lado.

Un telefono actual saca 3 a 6 MB por foto. Con 18.300 fotos al mes, guardarlas
crudas son ~70 GB mensuales contra 4,6 GB de volumen: se llena en dos dias.

LOS DOS PERFILES, Y POR QUE SON DOS
===================================

  DANOS       800 px, calidad 0,8   check list de ingreso, mecanica, IT
  INSPECCION  600 px, calidad 0,7   inspeccion de despacho

El criterio es de Franco y esta medido: 800 px esta DENTRO de la banda que
Regla PHP ya entrega --existen fotos de 565x750 en produccion y nadie
reclamo--, asi que no es una apuesta. Y en danos se movio UNA SOLA palanca
respecto del original, porque la perdida de resolucion y la de compresion se
suman y en un rayon fino se nota el doble.

Medido sobre fotos REALES (n=8): danos 51 KB de promedio, inspeccion 25 KB.
Eso da 758 MB al mes y 6,2 meses de autonomia, contra 1,0-1,3 con los 1600 px
que se habian usado antes.

OJO CON MEDIR ESTO SOBRE UNA IMAGEN SINTETICA. Un rectangulo de color plano
--como el que genera la suite-- sale en 3 KB, porque no tiene detalle que
comprimir. Es util para verificar que el redimensionado ocurre, y NO SIRVE para
planificar capacidad: se equivoca por 17 veces. El numero que vale es el de las
fotos reales.

JPEG Y NO WEBP, CON EVIDENCIA
=============================

El PDF del despacho lo arma **dompdf 0.8.6**, y `Helpers::dompdf_getimagesize`
mapea solo `IMAGETYPE_JPEG`, `GIF`, `BMP` y `PNG`. Un WebP sale con
`$type = null` y la imagen se descarta. Asi que JPEG, aunque pese mas.

SI ALGO FALLA, SE GUARDA LA ORIGINAL
====================================

Una foto es evidencia de un dano. Que Pillow no sepa leer un formato raro no
puede hacer que la foto se pierda: se guarda cruda y se avisa. Perder calidad
es un problema de disco; perder la foto es un problema del cliente.
"""

import io
import os

# (lado_maximo, calidad)
DANOS = (800, 80)
INSPECCION = (600, 70)


def _abrir(datos):
    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(datos))
    # `exif_transpose` ANTES de cualquier otra cosa: un telefono guarda la foto
    # apaisada con una etiqueta EXIF que dice "rotala 90". Al redimensionar y
    # reguardar, esa etiqueta se pierde y la foto queda acostada. Es el detalle
    # que hace que una galeria de danos se vea desprolija sin que nadie sepa
    # por que.
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        # JPEG no soporta canal alfa. Un PNG con transparencia se aplana sobre
        # blanco en vez de fallar.
        from PIL import Image as _I
        fondo = _I.new("RGB", img.size, (255, 255, 255))
        fondo.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA")
                    else None)
        img = fondo
    return img


def procesar(datos, perfil=DANOS):
    """Devuelve (bytes_jpeg, nota). `nota` es None si salio todo bien.

    NUNCA levanta: si no se puede procesar, devuelve los bytes originales y una
    nota que explica por que. Ver el encabezado."""
    lado, calidad = perfil
    try:
        img = _abrir(datos)
    except Exception as e:                            # noqa: BLE001
        return datos, "no se pudo leer la imagen ({}): se guarda cruda".format(
            type(e).__name__)

    try:
        # `thumbnail` respeta la proporcion y NO agranda: una foto de 400 px
        # queda en 400. Agrandarla no agregaria detalle, solo peso.
        img.thumbnail((lado, lado))
        salida = io.BytesIO()
        img.save(salida, format="JPEG", quality=calidad, optimize=True)
        return salida.getvalue(), None
    except Exception as e:                            # noqa: BLE001
        return datos, "no se pudo recomprimir ({}): se guarda cruda".format(
            type(e).__name__)


def guardar(archivo, ruta_destino, perfil=DANOS):
    """Lee un `FileStorage`, lo procesa y lo escribe. Devuelve (bytes, nota)."""
    datos = archivo.read()
    procesados, nota = procesar(datos, perfil)
    os.makedirs(os.path.dirname(ruta_destino), exist_ok=True)
    with open(ruta_destino, "wb") as f:
        f.write(procesados)
    return len(procesados), nota
