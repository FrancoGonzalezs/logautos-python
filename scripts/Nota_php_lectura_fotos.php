<?php
/*
================================================================================
 BLOQUE R3 -- que la exportacion masiva vea las fotos de Regla Python
 Archivo:  ~/public_html/application/controllers/Nota.php
 Medido contra:  C:\Regla_Python\produccion\Nota.php  (bajado el 2026-09-08)
================================================================================

ESTE BLOQUE REEMPLAZA AL R2, Y EL R2 ESTABA MAL. No por un error de logica:
estaba escrito contra el `Nota.php` de TEST, que NO tiene `listarFotosVin`.
Produccion si lo tiene -- es el arreglo de agosto de la motonave
`COSCO PACIFIC / YANTIAN`, cuya barra partia la URL.

Lo que el R2 proponia agregar (`listarUrlsDeFotos`, un metodo que devuelve URL
completas) YA EXISTE en produccion con otro nombre y mejor hecho, y la vista de
la masiva YA LO LLAMA:

    produccion/exportar_ot_masivo.php:108
    const urlListado = '<?php echo base_url('nota/listarFotosVin'); ?>/'
                     + encodeURIComponent(vin)
                     + '?motonave=' + encodeURIComponent(motonave || '');

ASI QUE DE LAS CUATRO EDICIONES DEL R2 QUEDA UNA:

  | R2 (contra TEST)                        | R3 (contra PRODUCCION)          |
  |-----------------------------------------|---------------------------------|
  | 1. `_fotosDeVinEnDanos()` nuevo          | ya no hace falta como metodo    |
  | 2. `listarUrlsDeFotos()` nuevo           | ES `listarFotosVin`, ya existe  |
  | 3. ruta nueva en routes.php              | la ruta ya funciona hoy         |
  | 4. 15 lineas de JS en la masiva          | el JS ya esta puesto            |
  |                                         | -> UNA edicion, y es esta       |

--------------------------------------------------------------------------------
QUE LE FALTA A `listarFotosVin` Y POR QUE NOS IMPORTA
--------------------------------------------------------------------------------

Busca en dos lugares, y los dos son CARPETAS POR VIN:

    1. assets/images/{motonave}/{VIN}/     con cuatro variantes del nombre
    2. assets/images/(*)/{VIN}/  y  (*)/(*)/{VIN}/      respaldo por glob

`assets/images/danos/` es PLANA: los archivos estan sueltos ahi, con el VIN
adentro del NOMBRE (`{VIN}_{pieza}_{tipo}_{nivel}_{fecha}_.jpg`). No hay ninguna
carpeta `danos/{VIN}/`, asi que los dos pasos fallan y devuelven cero.

Eso ya deja afuera al **26%** de los check lists del legado -- los que no tienen
motonave, 1.181 de 4.539 en 2026, que van a la plana. Y a partir del paralelo
deja afuera al **100% de los nuevos**, porque las fotos de Regla Python van
todas ahi.

NO ES UNA MOLESTIA. El paquete que arma la masiva -- una carpeta por VIN con el
PDF de la OT y las fotos -- es la EVIDENCIA que se sube al Drive para el
requerimiento DYP mensual de CIDEF, separado por embarque. Una exportacion sin
fotos es un paquete que le falta al cliente.

--------------------------------------------------------------------------------
LAS TRES DECISIONES DE ESTA EDICION
--------------------------------------------------------------------------------

1. SE SUMA SIEMPRE, NO ES UN RESPALDO. El paso 2 de `listarFotosVin` ya es un
   "si no encontre nada"; este NO. Una unidad puede tener fotos viejas en
   `{motonave}/{VIN}/` y fotos nuevas de Regla Python en `danos/`, y si esto
   fuera un respaldo, la carpeta vieja TAPARIA a las nuevas -- justo el caso
   del paralelo, que es para el que se escribe.

2. `glob` Y NO `scandir`. `listarFotosViejas` (19566) hace lo mismo con
   `scandir` + `strpos`, y funciona porque corre UNA vez por exportacion
   individual. La masiva llama a esto UNA VEZ POR OT: con `scandir` sobre
   ~109.500 archivos, un lote de cincuenta son cincuenta lecturas completas del
   directorio y ~5,5 millones de vueltas en PHP. Con `glob` el filtro por VIN lo
   hace la libreria de C y no se arma el arreglo de 109.500 nombres.

3. EL VIN SE LIMPIA ANTES DE METERLO EN EL PATRON. `glob` interpreta `*`, `?`,
   `[` y `{`. Un VIN es alfanumerico, asi que sacar todo lo demas no pierde
   nada y cierra la puerta a que el argumento de la URL se convierta en un
   comodin que barra el directorio entero.

--------------------------------------------------------------------------------
SI ESTA EDICION FALLA, NO ROMPE NADA
--------------------------------------------------------------------------------

Es ADITIVA sobre el arreglo `$urls` que el metodo ya construye: las rutas por
motonave se calculan igual y antes. Si el `glob` de `danos/` devuelve FALSE o
vacio, el metodo devuelve exactamente lo que devuelve hoy.

Y `listarFotos()` (19312), `listarFotosNuevas()` (19551) y `listarFotosViejas()`
(19566) NO SE TOCAN, asi que los dos botones individuales -- "EXPORTAR OT Y
FOTOS" y "EXPORTAR OT Y FOTOS (SIN MOTONAVE)" -- se comportan igual que hoy.

--------------------------------------------------------------------------------
COMO SE COMPRUEBA QUE QUEDO EL ARCHIVO CORRECTO
--------------------------------------------------------------------------------

Un reemplazo a medias no da error: responde 200 con la lista de siempre. La
comprobacion NO puede ser "no hay duplicados", tiene que MIRAR EL CONTENIDO del
metodo que quedo:

    grep -c "function listarFotosVin" ~/public_html/application/controllers/Nota.php
        --> tiene que dar 1

    grep -A3 "3) Carpeta plana" ~/public_html/application/controllers/Nota.php
        --> tiene que aparecer 'danos'. Si no aparece, quedo el metodo VIEJO
            y no hay ningun otro sintoma.

    php -l ~/public_html/application/controllers/Nota.php
        --> No syntax errors detected

Y despues, la prueba de verdad: una exportacion masiva de un lote con al menos
una unidad SIN motonave. Antes de esta edicion sale sin fotos; despues, con.

--------------------------------------------------------------------------------
QUE BORRAR, EXACTAMENTE
--------------------------------------------------------------------------------

En `produccion/Nota.php` el metodo va de la linea 19322 a la 19413. Los numeros
se corren en cuanto se edite algo, asi que van los TEXTOS:

  PRIMERA LINEA A BORRAR:
      public function listarFotosVin($vin = '')

  ULTIMA LINEA A BORRAR:
      (la llave que cierra el metodo, la que sigue a
       `echo json_encode(array_values(array_unique($urls)));`)

  La linea siguiente, que NO se borra, es la vacia anterior a
      public function generarZip() {

Se pega en su lugar el metodo completo de abajo. Es UNO SOLO: si despues del
pegado `grep -c "function listarFotosVin"` da 2, la clase no carga.
================================================================================
*/

    public function listarFotosVin($vin = '')
    {
        header('Content-Type: application/json');

        $vin = trim((string) $vin);

        if ($vin === '') {
            echo json_encode(array());
            return;
        }

        $motonave = trim((string) $this->input->get('motonave'));
        $base     = FCPATH . 'assets/images/';
        $dirs     = array();

        // 1) Variantes de como pudo quedar escrito el nombre de la motonave.
        if ($motonave !== '') {
            $sinSeparadores = str_replace(array(' ', '/', '\\'), '_', $motonave);

            $variantes = array(
                str_replace(' ', '_', $motonave),            // COSCO_PACIFIC_/_YANTIAN
                $sinSeparadores,                             // COSCO_PACIFIC___YANTIAN
                preg_replace('/_+/', '_', $sinSeparadores),  // COSCO_PACIFIC_YANTIAN
                $motonave                                    // tal cual, con espacios
            );

            foreach ($variantes as $variante) {
                $variante = trim((string) $variante, '/');

                if ($variante === '') {
                    continue;
                }

                $ruta = $base . $variante . '/' . $vin . '/';

                if (is_dir($ruta)) {
                    $dirs[] = $ruta;
                }
            }
        }

        // 2) Respaldo: buscar la carpeta del VIN sin depender de la motonave.
        //    Dos niveles, porque la barra de la motonave crea una carpeta anidada.
        if (empty($dirs)) {
            foreach (array($base.'*/'.$vin, $base.'*/*/'.$vin) as $patron) {
                $encontrados = glob($patron, GLOB_ONLYDIR);

                if (is_array($encontrados)) {
                    foreach ($encontrados as $encontrado) {
                        $dirs[] = rtrim($encontrado, '/') . '/';
                    }
                }
            }
        }

        $dirs        = array_values(array_unique($dirs));
        $extensiones = array('jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp');
        $urls        = array();

        foreach ($dirs as $dir) {
            $items = @scandir($dir);

            if ($items === FALSE) {
                continue;
            }

            // Ruta relativa a FCPATH, con cada segmento codificado para la URL.
            $relativa = trim(str_replace('\\', '/', substr($dir, strlen(FCPATH))), '/');
            $segmentos = array_map('rawurlencode', array_filter(explode('/', $relativa)));
            $prefijo   = implode('/', $segmentos);

            foreach ($items as $item) {
                if ($item === '.' || $item === '..') {
                    continue;
                }

                if (!is_file($dir . $item)) {
                    continue;
                }

                $ext = strtolower(pathinfo($item, PATHINFO_EXTENSION));

                if (!in_array($ext, $extensiones, true)) {
                    continue;
                }

                $urls[] = base_url($prefijo . '/' . rawurlencode($item));
            }
        }

        // 3) Carpeta plana `assets/images/danos/`, donde el VIN esta en el
        //    NOMBRE del archivo y no en una subcarpeta.
        //
        //    SE SUMA SIEMPRE, no es un respaldo como el paso 2: una unidad
        //    puede tener fotos viejas bajo su motonave Y fotos nuevas aca, y si
        //    esto fuera un `if (empty($dirs))` la carpeta vieja taparia a las
        //    nuevas. Es el caso del mes en paralelo, que es para el que se
        //    escribe esto.
        //
        //    `glob` y no `scandir`: la masiva llama a este metodo una vez por
        //    OT y el directorio tiene del orden de 109.500 archivos. Con
        //    `scandir` un lote de cincuenta OT son cincuenta lecturas completas
        //    del directorio mas medio millon de vueltas en PHP; con `glob`, el
        //    filtro por VIN lo resuelve la libreria de C.
        //
        //    El VIN se limpia antes de entrar al patron porque `glob`
        //    interpreta el asterisco, el signo de pregunta y los corchetes.
        //    Un VIN es alfanumerico, asi que sacar todo lo demas no pierde nada,
        //    y un argumento de URL no puede convertirse en un comodin que barra
        //    el directorio entero.
        $vinPatron = preg_replace('/[^A-Za-z0-9]/', '', $vin);

        if ($vinPatron !== '') {
            $planos = glob(
                $base . 'danos/*' . $vinPatron . '*.{jpg,jpeg,png,gif,bmp,webp}',
                GLOB_BRACE
            );

            if (is_array($planos)) {
                foreach ($planos as $plano) {
                    if (!is_file($plano)) {
                        continue;
                    }

                    $urls[] = base_url(
                        'assets/images/danos/' . rawurlencode(basename($plano))
                    );
                }
            }
        }

        echo json_encode(array_values(array_unique($urls)));
    }
