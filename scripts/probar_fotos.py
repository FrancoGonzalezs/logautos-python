#!/usr/bin/env python3
"""
scripts/probar_fotos.py -- que cada modulo guarde con SU perfil.

    python scripts/probar_fotos.py

POR QUE EXISTE

Los perfiles estaban decididos desde el 2026-09-04 —Franco los eligió mirando
la hoja de comparación con fotos reales— y **no estaban implementados en ningún
lado**: los cuatro módulos hacían `archivo.save()` y guardaban lo que mandaba
el teléfono, 3 a 6 MB por foto.

Con 18.300 fotos al mes eso son ~70 GB contra 4,6 GB de volumen: se llena en
dos días. Y el síntoma sería el peor de todos —el disco lleno— que ya nos costó
la suite entera una vez.

QUE SE PRUEBA

  1. LOS DOS PERFILES, cada uno en su módulo. Daños 800 px para check list, IT
     y mecánica; inspección 600 px para la inspección de despacho.
  2. QUE NO SE AGRANDE una foto chica: agrandarla no agrega detalle, sólo peso.
  3. QUE LA ORIENTACION EXIF SE APLIQUE. Un teléfono guarda la foto apaisada
     con una etiqueta que dice "rotala 90"; al recomprimir esa etiqueta se
     pierde y la foto queda acostada.
  4. QUE UNA IMAGEN ILEGIBLE SE GUARDE CRUDA y se anote por qué. Perder
     calidad es un problema de disco; perder la foto es un problema del
     cliente.
  5. Y QUE NO QUEDE NINGUN `archivo.save()` crudo en los módulos: es la única
     forma de que un módulo nuevo no se olvide en silencio.

OJO CON LOS PESOS DE ESTA SUITE: usa imágenes sintéticas de color plano, que
comprimen a casi nada. **Sirven para verificar que el redimensionado ocurre, no
para planificar capacidad** — sobre fotos reales el perfil de daños da 51 KB y
el de inspección 25 KB, y ésos son los números que dan los 758 MB/mes.
"""

import io as _io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from temporales import carpeta_de_prueba              # noqa: E402

os.environ.setdefault("SECRET_KEY", "prueba")
os.environ.setdefault("PUBLIC_BASE_URL", "https://regla.example")
os.environ.setdefault("REGLA_SOLO_LOCAL", "1")

FALLOS = []


def afirmar(condicion, que, detalle=""):
    print("   {}  {}".format("ok  " if condicion else "FALLA", que))
    if detalle:
        print("          {}".format(detalle))
    if not condicion:
        FALLOS.append(que)


def imagen(ancho, alto, exif_rotada=False):
    from PIL import Image
    b = _io.BytesIO()
    im = Image.new("RGB", (ancho, alto), (120, 90, 60))
    if exif_rotada:
        # Orientation = 6 -> "rotar 90 en sentido horario al mostrar".
        ex = im.getexif()
        ex[274] = 6
        im.save(b, "JPEG", exif=ex)
    else:
        im.save(b, "JPEG")
    b.seek(0)
    return b


def main():
    tmp = carpeta_de_prueba("regla_fotos_")
    os.environ["DATA_DIR"] = tmp
    os.environ["DB_PATH"] = os.path.join(tmp, "vacia.db")

    from PIL import Image
    from modulos import imagenes

    print("")
    print("1. LOS DOS PERFILES")
    for nombre, perfil, lado in (("daños", imagenes.DANOS, 800),
                                 ("inspección", imagenes.INSPECCION, 600)):
        salida, nota = imagenes.procesar(imagen(4000, 3000).read(), perfil)
        im = Image.open(_io.BytesIO(salida))
        afirmar(max(im.size) == lado and nota is None,
                "perfil {:<12} -> {} px de lado mayor".format(nombre, lado),
                "{} y {:.0f} KB (color plano: no sirve para planificar)".format(
                    im.size, len(salida) / 1024))

    print("")
    print("2. UNA FOTO CHICA NO SE AGRANDA")
    salida, _ = imagenes.procesar(imagen(400, 300).read(), imagenes.DANOS)
    im = Image.open(_io.BytesIO(salida))
    afirmar(im.size == (400, 300),
            "400x300 queda en 400x300", "{}".format(im.size))

    print("")
    print("3. LA ORIENTACION EXIF SE APLICA ANTES DE RECOMPRIMIR")
    # Apaisada 1200x900 con "rotar 90": al aplicarla queda vertical.
    salida, _ = imagenes.procesar(imagen(1200, 900, exif_rotada=True).read(),
                                  imagenes.DANOS)
    im = Image.open(_io.BytesIO(salida))
    afirmar(im.size[1] > im.size[0],
            "una foto con Orientation=6 queda VERTICAL, no acostada",
            "{}".format(im.size))

    print("")
    print("4. UNA IMAGEN ILEGIBLE SE GUARDA CRUDA Y LO DICE")
    crudo = b"esto no es una imagen"
    salida, nota = imagenes.procesar(crudo, imagenes.DANOS)
    afirmar(salida == crudo, "devuelve los bytes originales")
    afirmar(nota and "cruda" in nota, "y explica por que", nota)

    print("")
    print("5. NINGUN MODULO GUARDA CRUDO")
    #
    # Se mira el CODIGO de los modulos: un `archivo.save()` suelto es
    # exactamente el estado del que venimos, y es invisible -- la foto se
    # guarda, la pantalla anda, y el disco se llena tres semanas despues.
    import re
    crudos = []
    carpeta = os.path.join(RAIZ, "modulos")
    for nombre in sorted(os.listdir(carpeta)):
        if not nombre.endswith(".py"):
            continue
        fuente = _io.open(os.path.join(carpeta, nombre),
                          encoding="utf-8").read()
        # Sin comentarios: los que hay nombran `archivo.save()` para explicar
        # que ya NO se usa.
        vivo = re.sub(r"#[^\n]*", "", fuente)
        if re.search(r"\barchivo\.save\s*\(", vivo):
            crudos.append(nombre)
    afirmar(not crudos,
            "ningun modulo llama a archivo.save() directo",
            "lo hacen: {}".format(crudos) if crudos else "")

    # Y que los que guardan fotos usen el modulo de perfiles.
    esperados = ["check_list.py", "check_list_mecanica.py", "taller.py"]
    faltan = []
    for nombre in esperados:
        fuente = _io.open(os.path.join(carpeta, nombre),
                          encoding="utf-8").read()
        if "imagenes.guardar" not in fuente:
            faltan.append(nombre)
    afirmar(not faltan, "y los que guardan fotos pasan por `imagenes.guardar`",
            "faltan: {}".format(faltan) if faltan else "")

    # La inspeccion no guarda: reusa el de check_list, pero con SU perfil.
    fuente = _io.open(os.path.join(carpeta, "inspeccion_despacho.py"),
                      encoding="utf-8").read()
    afirmar(fuente.count("imagenes.INSPECCION") == 2,
            "la inspeccion pasa su perfil en sus DOS puntos de subida",
            "{} veces".format(fuente.count("imagenes.INSPECCION")))

    print("")
    print("=" * 64)
    if FALLOS:
        print("FALLARON {}:".format(len(FALLOS)))
        for f in FALLOS:
            print("   - {}".format(f))
        return 1
    print("cada modulo guarda con su perfil, y ninguno guarda crudo.")
    print("")
    print("Los pesos de arriba son de imagenes sinteticas y NO sirven para")
    print("planificar: sobre fotos reales son 51 KB (daños) y 25 KB")
    print("(inspeccion), que son los que dan los 758 MB por mes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
