"""
temporales.py -- carpetas de prueba que se borran solas.

POR QUE ESTE MODULO EXISTE APARTE, Y NO ADENTRO DE `core`
=========================================================

Porque `core.DB_PATH` se fija **al importar**:

    DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "local.db"))

Toda prueba hace las cosas en este orden: primero crea el temporal, despues
apunta `DB_PATH` ahi, y recien entonces importa la aplicacion. Si la funcion
que crea el temporal viviera en `core`, usarla importaria `core` en el paso
uno --con `DB_PATH` todavia apuntando a la REPLICA REAL-- y el `os.environ`
del paso dos no cambiaria nada.

O sea que meter esto en `core` construiria dentro del arreglo exactamente la
trampa que la guarda de `exigir_replica_de_prueba` avisa por escrito. Este
modulo no importa nada del proyecto a proposito, y asi se puede usar desde la
primera linea de cualquier script.

QUE ARREGLA
===========

La regla de habito --"ningun script suelto apunta a la replica real, copia
primero"-- tenia un costo que nadie habia contado: la copia son **370 MB**, hay
trece suites, y ninguna borraba su carpeta.

El 2026-09-08 el disco de la notebook llego a **cero libre**, con 104 copias
acumuladas (**33 GB**), y cinco suites murieron con `No space left on device`.

El modo de falla es de los feos: el error no apunta a nada roto del sistema
--dice `OSError` adentro de un `shutil.copy`-- y la suite entera queda sin
correr. **Una verificacion que no corre se lee igual que una que no encontro
nada.** Es la misma familia que `reconciliar.py` muriendo con `KeyError`.

Por eso la copia y su limpieza van JUNTAS, en una sola llamada. Un `finally` en
cada script alcanzaria, pero es lo mismo que pedirle a cada pantalla que se
acuerde de encolar el push: la proxima suite queda sin el.
"""

import atexit
import os
import shutil
import tempfile
import time


def carpeta_de_prueba(prefijo="regla_"):
    """Un tempdir que se borra solo cuando el proceso termina.

    `atexit` y no un `finally`: la carpeta tiene que **sobrevivir a que la
    prueba falle a la mitad** --si no, el fallo se lleva la evidencia justo
    cuando hace falta mirarla-- y borrarse igual cuando el proceso termina,
    salga por donde salga.

    La limpieza ignora errores: en Windows la base puede quedar abierta y el
    `rmtree` falla. No borrar un temporal no puede tumbar una prueba que ya dio
    su resultado."""
    _barrer_viejas(prefijo)
    carpeta = tempfile.mkdtemp(prefix=prefijo)
    atexit.register(shutil.rmtree, carpeta, ignore_errors=True)
    return carpeta


# Cuanto tiene que tener una carpeta para darla por abandonada. Seis horas es
# holgado a proposito: una suite tarda minutos, asi que nunca puede borrarle el
# temporal a otra corrida en curso -- ni a la de otra terminal.
HORAS_PARA_ABANDONADA = 6


def _barrer_viejas(prefijo):
    """Borra los temporales que quedaron de corridas anteriores.

    EL `atexit` SOLO NO ALCANZA, y se comprobo corriendo la suite entera: en
    Windows el `rmtree` falla si SQLite todavia tiene el archivo abierto, y el
    `ignore_errors` --que esta bien, no puede tumbar una prueba que ya dio su
    resultado-- lo tapa. De las trece suites, dos dejaron su copia igual: 717 MB
    por corrida.

    Asi que la limpieza va tambien **al crear**, y eso la vuelve auto-reparable:
    lo que una corrida no pudo borrar al salir lo borra la siguiente al entrar.
    El estado estable pasa a ser "una corrida de temporales", no "todas".

    Es la misma forma que el resto del sistema: la cola del push tampoco confia
    en que el intento salga bien, confia en que el siguiente lo retome."""
    raiz = tempfile.gettempdir()
    limite = time.time() - HORAS_PARA_ABANDONADA * 3600
    try:
        nombres = os.listdir(raiz)
    except OSError:
        return
    for nombre in nombres:
        if not nombre.startswith(prefijo):
            continue
        ruta = os.path.join(raiz, nombre)
        try:
            if os.path.isdir(ruta) and os.path.getmtime(ruta) < limite:
                shutil.rmtree(ruta, ignore_errors=True)
        except OSError:
            pass
