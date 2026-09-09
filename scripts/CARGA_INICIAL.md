# Carga inicial de una réplica nueva

Para el proyecto nuevo de Railway, o para cualquier réplica que arranque vacía.

> **El pull NO puede hacer esto.** Sincroniza dos entidades de veintiuna, no
> crea tablas, y sobre una base vacía baja todo y escribe cero. Medido:
> 2 min 42 s, 71.472 filas recibidas, **0 escritas**, resultado `ok`. La carga
> inicial es de `importar_dump.py`. Ver el pendiente 13 de CLAUDE.md.

---

## 1. Exportar de phpMyAdmin

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
  anterior a esa fecha no la tiene.

Medido sobre el volcado real: las 21 primeras son **418,4 MB** sin comprimir y
**38,5 MB** en `.sql.gz` (10,9×). El archivo completo de 121 tablas son 597,7 MB
y no hace falta.

---

## 2. Importar

```bash
python scripts/importar_dump.py \
  --dump ruta/al/volcado.sql \
  --db   ruta/a/local.db \
  --tablas newstocks_cidef,orden_trabajo,reparaciones_externas,contenedor,ot_contenedor,validacion_color_unidad,check_list,check_list_mecanica,entradas_salidas,inspeccion_despacho,ingresos_roro,nivel_dano,piezas,promedio_pdi,registros,retornos,tbl_roles,tbl_users,tipo_dano,incidentes,stock_consumibles,fotos_it
```

Tarda **~70 s** para 942.741 filas, y hace tres cosas: carga las tablas, crea
los 20 índices de trabajo, y **fija la marca de agua del pull**.

### El paso que no se puede saltear: la marca de agua

Ese tercer paso es el que convierte una carga buena en una mentira si falta.

`sync_estado.marca_agua` es lo único que le dice al pull desde cuándo pedir. Si
la réplica trae datos del volcado pero la marca dice una fecha **posterior**,
todo lo que cambió en el medio no se pide **nunca**. No faltan filas y ya: el
sistema queda convencido de que está al día, y el pull informa `ok` cada vuelta.

Y no es hipotético. Pasa de dos maneras, las dos vistas:

1. Un pull sobre una base sin tablas terminaba con `ok` **y la marca avanzada a
   la hora de esa corrida**. Ya está arreglado —ahora corta— pero **la marca que
   quedó escrita sigue escrita**.
2. El proyecto nuevo corre el pull cada 300 s desde que se desplegó, contra una
   base vacía. Su marca viene avanzando sola.

**La fecha no se calcula ni se convierte: se lee del dato.** Sale de
`MAX(updated_at)` de la propia tabla, menos una hora de margen.

No se usa la cabecera del volcado, y el motivo está medido. phpMyAdmin abre con
`SET time_zone = "+00:00"`, así que las columnas `timestamp` salen en UTC
mientras que la línea `Tiempo de generación` está en el reloj del servidor:

| | valor | reloj |
|---|---|---|
| `Tiempo de generación` | `2026-07-29 10:14:50` | servidor |
| `MAX(registros.created_at)` (`datetime`) | `2026-07-29 10:14:37` | servidor |
| `MAX(newstocks_cidef.updated_at)` (`timestamp`) | `2026-07-29 14:14:37` | **UTC** |

Cuatro horas de diferencia **dentro del mismo archivo**, y ese desfase cambia
con el horario de verano. `MAX(updated_at)` está por construcción en el mismo
reloj contra el que el endpoint compara, así que no hay aritmética de zonas.

**El margen se resta porque los dos errores no cuestan lo mismo:**

| | qué pasa | cuánto cuesta |
|---|---|---|
| marca **atrasada** | el próximo pull re-trae filas que ya están; el UPSERT las reescribe iguales | segundos, una vez |
| marca **adelantada** | esas filas no se piden nunca más | el dato, para siempre, y en silencio |

Con esa asimetría no hay nada que optimizar. Y un volcado de phpMyAdmin no es
una foto instantánea —exporta tabla por tabla—, así que una fila que cambió
mientras se exportaba no está y `MAX(updated_at)` no la ve.

**Si la comprobación falla, el importador deja la marca VACÍA** y revienta.
Vacía significa «traer todo»: es el único valor seguro cuando no se sabe.

---

## 3. Verificar que está completa

No que lo parezca. Una base cargada a medias abre, responde y tiene tablas.

```bash
# 1. la consulta para Regla PHP
python scripts/verificar_carga.py --sql
#    pegarla en phpMyAdmin > SQL, exportar el resultado a CSV

# 2. comparar
python scripts/verificar_carga.py --contra conteos.csv --db ruta/a/local.db
```

`COUNT(*)` y **no** `information_schema.TABLE_ROWS`: en InnoDB esa columna es
una estimación del optimizador y puede errarle por miles. Una verificación que
compara contra una estimación no verifica nada.

Corta si:

- falta una tabla, o alguna tiene menos filas que Regla PHP;
- `tbl_users` no tiene hashes bcrypt —sin eso no entra nadie—;
- faltan índices de trabajo —la base anda, va lentísima, y el síntoma aparece
  días después y lejos de la causa—;
- **la marca de agua es posterior al dato más nuevo de la réplica.**

---

## 4. La prueba del volumen

Con la base ya pesando lo suyo, **un redespliegue** y comprobar que sigue
pesando lo mismo. Si sobrevive a un despliegue, el volumen está montado bien.

`/version` lo dice sin entrar al contenedor: `volumen.base` trae el tamaño de
`local.db` y de sus dos archivos de al lado, y `volumen.contenido` itemiza
`DATA_DIR` entrada por entrada.

`local.db-wal` va aparte y con nombre propio a propósito: puede pasar de cero a
decenas de MB entre dos vueltas del pull —ya pasó: 67,70 MB contra 69 libres— y
sumado dentro de un total no se distingue de datos.

---

## 5. Recién ahí, el push

> **NO PUEDE HABER DOS PROYECTOS CON EL PUSH ENCENDIDO.**
>
> Serían dos sistemas escribiéndole el mismo movimiento a Regla PHP, con dos
> colas que no se conocen y dos locking optimistas peleándose por el mismo
> `updated_at`. **Antes de poner `PUSH_LEGADO_ACTIVO=1` en el proyecto nuevo,
> hay que apagarlo en el viejo.**

---

## Lo que este procedimiento NO resuelve todavía

**Cómo llega el archivo al volumen de Railway.** El repo no puede llevarlo y no
hay consola. `semilla_volumen.py` documenta correr comandos *dentro* del
contenedor, así que hay una vía, pero nunca quedó escrito cómo llegaron las
tablas grandes la primera vez al proyecto viejo.

Los 38,5 MB comprimidos hacen que casi cualquier camino sirva; falta elegir uno.
