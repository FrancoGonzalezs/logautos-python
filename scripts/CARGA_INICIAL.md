# Carga inicial de una réplica nueva

Para el proyecto nuevo de Railway, o para cualquier réplica que arranque vacía.

> **El pull NO puede hacer esto.** Sincroniza dos entidades de veintidós, no
> crea tablas, y sobre una base vacía baja todo y escribe cero. Medido:
> 2 min 42 s, 71.472 filas recibidas, **0 escritas**, resultado `ok`.
> Ver el pendiente 13 de CLAUDE.md.

Hay dos vías. **La A es la recomendada** y la B queda escrita porque no depende
de que la A esté disponible.

---

# Vía A — el proyecto viejo le sirve la base al nuevo

El viejo ya tiene la réplica cargada. No hay nada que exportar de MySQL.

## Por qué es mejor, y no es sólo comodidad

**La base y su marca de agua viajan juntas, consistentes por construcción.**

Todo el problema que la vía B tiene que resolver a mano —los dos relojes del
volcado, el margen de una hora, reponer `sync_estado`, verificar que no quedó
adelantada— **no existe acá**, porque no hay ningún momento en que los datos y
la marca vengan de fuentes distintas: salen del mismo archivo, en el mismo
instante.

Es la clase de problema que conviene no tener en vez de resolver bien.

## Lo que se validó antes de construirlo, y qué dio

### El `.db` no se puede copiar con `cp`. Ni con el WAL al lado

La réplica está en modo **WAL**. Copiar sólo `local.db` deja afuera todo lo que
esté en el WAL sin volcar:

```
base con 501 filas, 500 de ellas commiteadas y todavia en el WAL

A) copiando SOLO local.db        ->   1 fila
B) copiando .db + -wal + -shm    -> 501 filas
C) sqlite3 .backup()             -> 501 filas
D) VACUUM INTO                   -> 501 filas
```

**Y la copia con una fila abre perfectamente.** No da error, no avisa: es una
base SQLite válida con 500 filas menos.

Copiar los tres archivos tampoco sirve, porque **son tres instantes distintos**.
Con 388 MB la copia tarda cientos de milisegundos y el hilo de sync sigue
commiteando en el medio. Reproducido a propósito —copiar el `.db`, esperar
300 ms, copiar el `-wal`— con el escritor corriendo:

| método | resultado |
|---|---|
| copia cruda `.db` + `-wal` 300 ms después | **12 de 12 corrompidas** (`database disk image is malformed`) |
| `VACUUM INTO` con el mismo escritor | **0 de 12** |

### Entonces: `VACUUM INTO`, y NO hay que apagar el hilo de sync

Las dos vías consistentes son la API de respaldo (`backup()`) y `VACUUM INTO`.
Las dos toman una foto transaccional: lo que sale es la base en **algún**
instante válido, nunca a medio escribir.

Se elige `VACUUM INTO` porque además **compacta**, y porque deja un archivo
único sin `-wal` al lado — que es exactamente lo que se quiere mover.

Medido sobre la réplica real de 388,5 MB:

| | tiempo | tamaño |
|---|---|---|
| `VACUUM INTO` | **1,3 s** | 386,9 MB |
| `backup()` | 0,9 s | 388,5 MB |
| `cp` crudo | 0,2 s | 388,5 MB — **no consistente** |
| `gzip -6` sobre la copia | **5,9 s** | **49,8 MB** (7,8×) |

`PRAGMA quick_check` sobre la copia: `ok`. Las 35 tablas con el mismo conteo que
el origen, cero diferencias.

**El total es ~7 segundos y 49,8 MB**, sin tocar el hilo de sync.

## El procedimiento

### 1. En la consola del proyecto VIEJO — sacar la copia

```bash
python - <<'PY'
import gzip, os, shutil, sqlite3
src = os.environ.get("DB_PATH", "/data/local.db")
snap = "/data/copia.db"
sqlite3.connect(src).execute("VACUUM INTO ?", (snap,))
with open(snap, "rb") as f, gzip.open(snap + ".gz", "wb", 6) as g:
    shutil.copyfileobj(f, g, 1 << 20)
os.remove(snap)
print("listo:", snap + ".gz", os.path.getsize(snap + ".gz") // 1000000, "MB")
PY
```

Se borra el `.db` intermedio en el acto: **no se deja una copia de `tbl_users`
sin comprimir dando vueltas en el volumen**.

### 2. Pasarlo al proyecto NUEVO

Ésta es la parte que hay que decidir, y hay dos formas. **Ninguna deja el
archivo en una URL pública.**

**A1 — ruta temporal con token en el viejo.** Es lo más directo. Tres
condiciones, y las tres son por algo:

- **El token va en una CABECERA, nunca en la URL.** gunicorn no escribe access
  log por defecto y la aplicación tampoco loguea rutas —comprobado—, pero el
  proxy de Railway está fuera de nuestro control. Un token en la URL termina en
  el log de alguien; en una cabecera, no.
- **Detrás de una variable de entorno que arranca apagada**, y que se saca
  después. La ruta existe sólo mientras esa variable esté puesta.
- **Se quita del código y se redespliega.** Un blueprint borrado no deja rastro
  en el binario; lo que sí queda es la variable, así que se borra también.

**A2 — por el cPanel, sin HTTP en ningún momento.** Desde la consola del viejo
se sube el `.gz` por FTP a una carpeta **fuera de `public_html`**, y desde la
consola del nuevo se baja igual. Nunca pasa por una URL. Es más manual y
necesita las credenciales de FTP escritas a mano en las dos consolas.

> A2 no es lo mismo que la vía B: acá lo que viaja es **la réplica ya armada,
> con su marca de agua**. El cPanel es sólo el intermediario.

### 3. En el proyecto NUEVO — poner la base

```bash
gunzip -c /data/copia.db.gz > /data/local.db.nueva
python -c "
import sqlite3, sys
d = sqlite3.connect('/data/local.db.nueva')
print('quick_check:', d.execute('PRAGMA quick_check').fetchone()[0])
print('unidades   :', d.execute('SELECT COUNT(*) FROM newstocks_cidef').fetchone()[0])
print('usuarios   :', d.execute('SELECT COUNT(*) FROM tbl_users').fetchone()[0])
print('marca      :', d.execute('SELECT entidad, marca_agua FROM sync_estado').fetchall())"
```

**Recién si eso dice `ok`** se renombra sobre `local.db`, con el servicio
detenido o con el redespliegue. Y se borra el `.gz`.

### 4. Limpiar el arrastre

```bash
python scripts/limpiar_para_paralelo.py --db /data/local.db            # muestra
python scripts/limpiar_para_paralelo.py --db /data/local.db --borrar   # ejecuta
```

Lo que viaja de Regla Python y **sale**:

| tabla | por qué |
|---|---|
| `movimientos_regla` y las seis que le cuelgan | movimientos de prueba sobre la copia congelada |
| `sync_push_pendientes` | **la cola del push.** Si viaja, el día que se encienda el push saldrían hacia Regla PHP escrituras de pruebas de agosto |
| `avisos_pendientes_regla` | igual: no puede salir un correo de una prueba vieja |
| `fotos_publicadas` | los tokens apuntan a archivos del `DATA_DIR` del proyecto viejo, que no se copian. Serían URLs que dan 404 |
| `sync_conflictos`, `reconciliacion`, `permisos_regla`, `intentos_bloqueados_regla` | historial de pruebas; los permisos se resiembran solos |

**`sync_estado` NO se toca.** Es lo que se vino a buscar.

**Y `newstocks_cidef.push_pendiente` se baja a 0.** No es una fila y es el
huérfano que importa: el UPSERT del pull **saltea** las filas con el flag en 1,
así que una unidad que quede así deja de recibir actualizaciones de Regla PHP,
en silencio y para siempre. Vaciar la cola sin bajar el flag produce
exactamente eso.

**La lista del script es de lo que se CONSERVA, y una tabla sin clasificar lo
frena.** Al revés de lo natural, a propósito: con una lista de lo que se borra,
una tabla nueva de Regla Python sobreviviría en silencio; con un «borrá lo que
no reconozcas», una tabla nueva de Regla PHP se perdería en silencio. Las dos
fallas son mudas. Frenar y nombrarla no lo es.

### 5. Verificar

```bash
python scripts/verificar_carga.py --sql        # pegar en phpMyAdmin, exportar CSV
python scripts/verificar_carga.py --contra conteos.csv --db /data/local.db
```

---

# Vía B — volcado de phpMyAdmin

Si la A no está disponible. **Trae de vuelta el problema de la marca de agua**,
que acá hay que resolver y verificar.

## 1. Exportar

**Exportar → Personalizada**, formato **SQL**, compresión **gzip**, estructura
**y** datos. Sólo estas **22** tablas:

```
newstocks_cidef          orden_trabajo            reparaciones_externas
contenedor               ot_contenedor            validacion_color_unidad
check_list               check_list_mecanica      entradas_salidas
inspeccion_despacho      ingresos_roro            nivel_dano
piezas                   promedio_pdi             registros
retornos                 tbl_roles                tbl_users
tipo_dano                incidentes               stock_consumibles
fotos_it
```

- **`tbl_users` y `tbl_roles` no son opcionales**: sin ellas no entra nadie, y
  el síntoma que se ve —«no me deja iniciar sesión»— no dice que la carga quedó
  corta.
- **`fotos_it`** la creó el despliegue del IT del 2026-09-02. Un volcado
  anterior no la tiene.

Medido: las 21 primeras son **418,4 MB** sin comprimir y **38,5 MB** en
`.sql.gz`. El archivo de las 121 tablas son 597,7 MB y no hace falta.

**El archivo va a una carpeta FUERA de `public_html`** y se baja por FTP desde
la consola del proyecto nuevo. Nunca por una URL: lleva `tbl_users` con
correos, RUT, teléfonos y hashes de 144 personas reales.

## 2. Importar

```bash
python scripts/importar_dump.py --dump volcado.sql --db /data/local.db \
  --tablas newstocks_cidef,orden_trabajo,reparaciones_externas,contenedor,ot_contenedor,validacion_color_unidad,check_list,check_list_mecanica,entradas_salidas,inspeccion_despacho,ingresos_roro,nivel_dano,piezas,promedio_pdi,registros,retornos,tbl_roles,tbl_users,tipo_dano,incidentes,stock_consumibles,fotos_it
```

**~70 s** para 942.741 filas. Carga las tablas, crea los 20 índices, y **fija
la marca de agua**.

### El paso que la vía A no necesita

`sync_estado.marca_agua` es lo único que le dice al pull desde cuándo pedir. Si
la réplica trae datos del volcado pero la marca dice una fecha **posterior**,
todo lo que cambió en el medio no se pide **nunca**. No faltan filas: el sistema
queda convencido de que está al día, e informa `ok` cada vuelta.

**La fecha se lee del dato, no de la cabecera del volcado.** phpMyAdmin abre con
`SET time_zone = "+00:00"`, así que las columnas `timestamp` salen en UTC
mientras `Tiempo de generación` está en el reloj del servidor:

| | valor | reloj |
|---|---|---|
| `Tiempo de generación` | `2026-07-29 10:14:50` | servidor |
| `MAX(registros.created_at)` (`datetime`) | `2026-07-29 10:14:37` | servidor |
| `MAX(newstocks_cidef.updated_at)` (`timestamp`) | `2026-07-29 14:14:37` | **UTC** |

Cuatro horas dentro del mismo archivo, y el desfase cambia con el horario de
verano. Así que sale de `MAX(updated_at)` de la propia tabla —mismo reloj que el
endpoint, por construcción— **menos una hora de margen**:

| | qué pasa | cuánto cuesta |
|---|---|---|
| marca **atrasada** | el próximo pull re-trae filas que ya están | segundos, una vez |
| marca **adelantada** | esas filas no se piden nunca más | el dato, para siempre, en silencio |

Si la comprobación falla, el importador **deja la marca vacía** —«traer todo»,
el único valor seguro cuando no se sabe— y revienta.

## 3. Verificar

Igual que la vía A, más el chequeo de la marca, que acá es el que importa.

---

# Común a las dos vías

## La prueba del volumen

Con la base ya pesando lo suyo, **un redespliegue** y comprobar que sigue
pesando lo mismo. Si sobrevive a un despliegue, el volumen está montado bien.

`/version` lo dice sin entrar al contenedor: `volumen.base` trae el tamaño de
`local.db`, `-wal` y `-shm` por separado, y `volumen.contenido` itemiza
`DATA_DIR` entrada por entrada.

El WAL va con nombre propio a propósito: puede pasar de cero a decenas de MB
entre dos vueltas del pull —ya pasó, 67,70 MB contra 69 libres— y sumado dentro
de un total no se distingue de datos.

## Y recién ahí, el push

> **NO PUEDE HABER DOS PROYECTOS CON EL PUSH ENCENDIDO.**
>
> Serían dos sistemas escribiéndole el mismo movimiento a Regla PHP, con dos
> colas que no se conocen y dos locking optimistas peleándose por el mismo
> `updated_at`. **Antes de poner `PUSH_LEGADO_ACTIVO=1` en el proyecto nuevo,
> hay que apagarlo en el viejo.**
