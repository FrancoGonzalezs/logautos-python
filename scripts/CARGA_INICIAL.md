# Carga inicial de una réplica nueva

Para el proyecto nuevo de Railway, o para cualquier réplica que arranque vacía.

> **El pull NO puede hacer esto.** Sincroniza dos entidades de veintidós, no
> crea tablas, y sobre una base vacía baja todo y escribe cero. Medido:
> 2 min 42 s, 71.472 filas recibidas, **0 escritas**, resultado `ok`.
> Ver el pendiente 13 de CLAUDE.md.

Hay dos vías. **La A es la recomendada** y la B queda escrita porque no depende
de que la A esté disponible.

---

## Las tres cosas que alguien va a repetir mal dentro de seis meses

Están medidas, no razonadas. Van con el número porque «es más seguro» no frena
a nadie.

### 1. Copiar sólo `local.db` devuelve una base VÁLIDA con 500 filas menos

La réplica está en modo WAL. Sobre una base con 501 filas, 500 de ellas
commiteadas y todavía en el WAL:

```
copiando SOLO local.db        ->   1 fila
copiando .db + -wal + -shm    -> 501 filas
backup() / VACUUM INTO        -> 501 filas
```

**La copia de una fila abre perfectamente.** No da error, no avisa, pasa
`quick_check`. Es una base SQLite legítima a la que le faltan 500 filas.

Es el peor modo de falla que hay: el que copia ve un archivo, lo pone, la
aplicación levanta, y lo que falta se descubre semanas después y lejos.

### 2. Copiar los tres archivos en caliente: 12 de 12 corrompidas

Porque **son tres instantes distintos**. Con 388 MB la copia tarda cientos de
milisegundos y el hilo de sync sigue commiteando en el medio.

Reproducido a propósito —copiar el `.db`, esperar 300 ms, copiar el `-wal`— con
un escritor corriendo, doce vueltas:

| método | corrompidas |
|---|---|
| copia cruda de los tres archivos | **12 de 12** (`database disk image is malformed`) |
| `VACUUM INTO`, mismo escritor | **0 de 12** |

No es «más seguro»: es la diferencia entre no funcionar nunca y funcionar
siempre.

### 3. La lista del script de limpieza es de lo que se CONSERVA

Y una tabla sin clasificar **frena el script**. Está al revés de lo natural, a
propósito, porque las dos alternativas fallan calladas:

| si la lista fuera… | qué pasaría con una tabla nueva |
|---|---|
| de lo que se **borra** | una tabla nueva **de Regla Python** sobreviviría a la limpieza, en silencio |
| «borrá lo que no reconozcas» | una tabla nueva **de Regla PHP** se perdería, en silencio |
| **de lo que se conserva, y frená si no la conocés** | el script se detiene y la nombra |

Las dos primeras son mudas. La tercera obliga a decidir, que es lo único que
no se puede automatizar.

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

## Lo que se validó antes de construirlo

Las dos trampas del WAL están arriba, en **Las tres cosas**: copiar sólo el
`.db` da una base válida con 500 filas menos, y copiar los tres archivos en
caliente dio 12 de 12 corrompidas.

La conclusión operativa es que hay que tomar una **foto transaccional**, y hay
dos formas: la API de respaldo (`backup()`) y `VACUUM INTO`. Se elige la
segunda porque además **compacta** y deja un archivo único sin `-wal` al lado
— que es exactamente lo que se quiere mover.

**Y no hay que apagar el hilo de sync.** Con un escritor commiteando sin parar,
`VACUUM INTO` dio 0 de 12 fallos.

Medido sobre la réplica real de 388,5 MB:

| | tiempo | tamaño |
|---|---|---|
| `VACUUM INTO` | **1,3 s** | 386,9 MB |
| `backup()` | 0,9 s | 388,5 MB |
| `cp` crudo | 0,2 s | 388,5 MB — **no consistente** |
| `gzip -6` sobre la copia | **5,9 s** | **49,8 MB** (7,8×) |

`PRAGMA quick_check` sobre la copia: `ok`. Las 35 tablas con el mismo conteo que
el origen, cero diferencias. **Total ~7 segundos.**

## El procedimiento

### 1. Levantar la ruta de traspaso en el proyecto VIEJO

**Construida: `modulos/traspaso.py`.** Sirve una foto consistente de la réplica,
comprimida, en streaming. No hay que apagar el hilo de sync.

> **Se eligió esto sobre pasarlo por FTP del cPanel, y el argumento es de
> Franco:** el FTP no elimina la confianza, **la mueve** — de un token
> desechable a las credenciales que abren la cuenta entera donde vive el
> sistema de la empresa, tecleadas en la misma consola. Entre exponer algo que
> se borra en diez minutos y exponer la llave maestra, no hay duda.

```bash
# 1. generar el token
python -c "import secrets; print(secrets.token_urlsafe(48))"

# 2. ponerlo como TRASPASO_TOKEN en las variables del proyecto VIEJO
#    (el redespliegue lo levanta; en el log tiene que aparecer TRASPASO ACTIVO)
```

**Las cuatro condiciones, y ninguna es decorativa:**

- **Sin la variable, la ruta no existe.** El blueprint no se registra, y la
  respuesta pasa a ser **exactamente la misma** que la de una dirección
  inventada. La prueba compara las dos.
- **El token va en una CABECERA (`X-Traspaso-Token`), nunca en la URL.**
  gunicorn no escribe access log por defecto y la aplicación tampoco loguea
  rutas —los dos comprobados—, pero el proxy de Railway está fuera de nuestro
  control. **La ruta rechaza el token por query string**, y hay una prueba que
  lo afirma: es lo que alguien va a intentar por comodidad.
- **Un token corto no arranca.** Menos de 32 caracteres y la aplicación
  revienta al levantar. Mejor no arrancar que arrancar creyendo que está
  protegido.
- **Todo intento se imprime**, el que entra y el que no, con la IP de origen.
  Es una ruta que sirve `tbl_users`: que se use tiene que verse.

**La copia NO va al volumen.** El volumen del proyecto viejo tiene del orden de
**69 MB libres de 434** y la copia son **387 MB**: no entra, y llenarlo tumbaría
el sistema que estamos copiando. Va al disco efímero del contenedor, y el
espacio se comprueba **antes** — si no alcanza corta con los números en vez de
fallar a la mitad y entregar un `.gz` truncado.

### 2. Bajarla desde la consola del proyecto NUEVO

```bash
curl -f -H "X-Traspaso-Token: EL_TOKEN" \
     https://<el-viejo>/traspaso/replica.db.gz \
     -o /tmp/replica.db.gz
```

`-f` para que un 404 —token mal escrito— no deje un archivo con el cuerpo del
error adentro.

### 3. Apagar la ruta, y son TRES cosas

Apenas la copia esté en el proyecto nuevo, en el viejo:

1. **quitar la variable `TRASPASO_TOKEN`** — con eso la ruta deja de existir;
2. **borrar `modulos/traspaso.py` y su bloque en `app.py`**;
3. **redesplegar**, que es lo que hace efectivas las dos anteriores.

Las tres. Quitar sólo la variable deja el código listo para que alguien la
vuelva a poner; borrar sólo el código sin redesplegar no cambia lo que corre.

### 4. Poner la base en su lugar

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

### 5. Limpiar el arrastre

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

**Las fotos publicadas el script las lista UNA POR UNA** —origen, referencia,
fecha y ruta— antes de borrar nada. No se decide de memoria: una URL que ya haya
viajado a `archivo1..archivo9` de Regla PHP queda en 404 si se borra su token,
porque Regla PHP guarda la URL y no el archivo. Se mira lo que hay **en esa
base**, no lo que uno recuerda.
| `sync_conflictos`, `reconciliacion`, `permisos_regla`, `intentos_bloqueados_regla` | historial de pruebas; los permisos se resiembran solos |

**`sync_estado` NO se toca.** Es lo que se vino a buscar.

> ### `push_pendiente` a 0 no es un paso más de la lista
>
> Es el detalle que más caro habría salido, y no es una fila: es una columna de
> `newstocks_cidef`.
>
> Lo pone en 1 quien encola y lo baja quien resuelve. **El UPSERT del pull
> SALTEA las filas con el flag en 1.** Así que vaciar `sync_push_pendientes` sin
> bajar el flag deja esas unidades **sin recibir actualizaciones de Regla PHP,
> para siempre y sin ninguna señal**: no hay error, no hay log, no hay fila
> divergente que la reconciliación pueda ver — la unidad simplemente se congela
> en el estado que tenía ese día.
>
> El script lo recalcula y lo verifica al terminar. Es el mismo huérfano que ya
> documentó `borrar_backlog.py`, y sigue siendo el peor resultado posible de un
> borrado que parece inocente.

**La lista del script es de lo que se CONSERVA, y una tabla sin clasificar lo
frena.** Al revés de lo natural, a propósito: con una lista de lo que se borra,
una tabla nueva de Regla Python sobreviviría en silencio; con un «borrá lo que
no reconozcas», una tabla nueva de Regla PHP se perdería en silencio. Las dos
fallas son mudas. Frenar y nombrarla no lo es.

### 6. Verificar

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
