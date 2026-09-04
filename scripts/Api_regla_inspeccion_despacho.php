<?php if (!defined('BASEPATH')) exit('No direct script access allowed');

/**
 * Api_regla_inspeccion_despacho.php -- lo que necesita `Api_regla.php` para la
 * INSPECCION DE DESPACHO.
 *
 * NO VA EN ESTE REPOSITORIO NI SE INCLUYE. Son DOS ediciones sobre
 *
 *     application/controllers/Api_regla.php
 *
 * mas DOS rutas en routes.php.
 *
 * NO HAY METODOS NUEVOS. `crear_fila` y `leer_fila` ya estan desplegados y son
 * genericos sobre `mapa_entidades()`: alcanza con darle de alta la entidad.
 *
 * Y NO HAY CAMBIO EN EL CARGADOR DE IMAGENES DEL PDF. Ver el bloque P2, que
 * explica por que -- se verifico leyendo el archivo desplegado antes de
 * escribir nada.
 *
 * Despues de subir, en cPanel Terminal:
 *
 *     php -l ~/public_html/application/controllers/Api_regla.php
 *     grep -o "function [a-z_]*" .../Api_regla.php | sort | uniq -d   -> vacio
 *
 * Y LA COMPROBACION DE CONTENIDO, que es la que detecta el fallo mudo -- que
 * el metodo viejo se quede en lugar del nuevo, con `php -l` limpio y sin
 * duplicados (paso el 2026-09-04 con `columnas_que_acumulan`):
 *
 *     grep -c "inspeccion_despacho" .../Api_regla.php     -> 2  (mapa + tabla)
 *     grep -A3 "'unidades' =>" .../Api_regla.php | grep -c "patio"  -> 1
 */


/* ===========================================================================
   BLOQUE P1 -- la entidad `inspeccion_despacho`, y `patio` en `unidades`
   ===========================================================================

   SON DOS CAMBIOS AL MISMO METODO `mapa_entidades()`, y el segundo es de UNA
   PALABRA -- pero hay que decirlo, porque sin el la mitad del modulo se ignora
   en silencio.

   1. La entidad NUEVA `inspeccion_despacho`, verbo 'crear'.

   2. `patio` AGREGADO a la lista blanca de `unidades`.

      `Nota.php:inspeccion_despacho()` hace UN `actualizar_vin` con

          $aux = array('patio'=>$p, 'calle'=>$c, 'despachado'=>$e,
                       'updated_by'=>..., 'updated_at'=>...);

      donde $p='PATIO 2', $c='IT', $e='EN ESPERA CC ZD' -- los tres son
      literales, no salen de ningun formulario.

      `calle` y `despachado` YA estan en la lista blanca; `patio` NO. Se
      verifico contra el archivo DESPLEGADO, no contra esta spec: un PUT con un
      cuerpo invalido devuelve 400 con `permitidos`, y ahi son 30 columnas sin
      `patio`.

      Sin agregarlo, la inspeccion de REGLA movería el estado y la calle y
      dejaría el patio en el valor viejo. 200, cola resuelta, media escritura.
      Es exactamente el silencio de la lista blanca.

   LAS 28 COLUMNAS DE `inspeccion_despacho`

   Salen del `$userInfo` de `Nota.php:inspeccion_despacho()` (15 columnas) mas
   las que escribe el paso de fotos, `subida_foto_inspeccion_despacho_proces()`
   (`link_unidad`, `contador`, y `archivo1..archivo9`), mas `faltante` y
   `observaciones` de la tabla. Contadas una por una contra el esquema: la
   tabla tiene 29 columnas y `id` es la que no viaja.
   =========================================================================== */

    /**
     * En `mapa_entidades()`, junto a `check_list` y `check_list_mecanica`.
     */
            'inspeccion_despacho' => array(
                'tabla'    => 'inspeccion_despacho',
                'verbo'    => 'crear',
                'columnas' => array(
                    // -- las 15 del $userInfo de inspeccion_despacho() -------
                    'vin', 'patente', 'guia_despacho', 'cliente', 'destino',
                    'fecha_despacho', 'marca', 'modelo', 'color', 'encargado',
                    'estanque', 'kilometraje', 'fecha_entrega',
                    'fecha_completa', 'llaves',
                    // -- las dos de la tabla que el formulario tambien llena --
                    'faltante', 'observaciones',
                    // -- las del paso de fotos -------------------------------
                    //
                    // `link_unidad` acumula con ' | ' TODAS las fotos, sin
                    // tope. `archivo1..archivo9` son NUEVE posiciones fijas, y
                    // son las que lee el PDF del despacho -- ver el bloque P2.
                    //
                    // Ojo: la decima foto del legado entra en `link_unidad` y
                    // NO entra en ningun `archivoN` (ninguna rama del
                    // `elseif($cont == N)` la toma). No se pierde: se vuelve
                    // invisible para el PDF. En 16.365 filas el contador nunca
                    // paso de 9, asi que hoy no ocurre.
                    'link_unidad', 'contador',
                    'archivo1', 'archivo2', 'archivo3', 'archivo4', 'archivo5',
                    'archivo6', 'archivo7', 'archivo8', 'archivo9',
                ),
            ),

/* ---------------------------------------------------------------------------
   Y EL AGREGADO A `unidades`. En la entrada que ya existe, en el array
   'columnas', se agrega UNA linea. NO se reemplaza el metodo: se agrega
   `'patio',` al final de la lista, junto a las del check list.
   --------------------------------------------------------------------------- */

                    // Y la de la INSPECCION DE DESPACHO. `calle` y
                    // `despachado` ya estaban; `patio` no, y sin ella el push
                    // de la inspeccion mueve dos de las tres columnas.
                    'patio',

/* ---------------------------------------------------------------------------
   LAS DOS RUTAS, en routes.php.

   La del GET va ANTES que la del POST: CodeIgniter resuelve por orden y
   `api_regla/inspeccion_despacho` a secas ya estaria mapeada al POST, con lo
   cual el segmento numerico se comeria como parametro de aquella.

       $route['api_regla/inspeccion_despacho/(:num)']['GET'] =
           'api_regla/leer_fila/inspeccion_despacho/$1';
       $route['api_regla/inspeccion_despacho']['POST'] =
           'api_regla/crear_fila/inspeccion_despacho';

   Y las dos sondas que NO escriben:

       # 401
       curl -s -o /dev/null -w '%{http_code}\n' \
         https://claude.logautos.cl/api_regla/inspeccion_despacho/999999999

       # 404 "no existe inspeccion_despacho 999999999"
       curl -s -H "X-API-Key: $CLAVE" \
         https://claude.logautos.cl/api_regla/inspeccion_despacho/999999999
   --------------------------------------------------------------------------- */


/* ===========================================================================
   BLOQUE P2 -- EL CARGADOR DE IMAGENES NO SE TOCA
   ===========================================================================

   Se pidio la funcion completa, corregida para que detecte una URL absoluta y
   la use tal cual en vez de concatenarla con `base_url()`.

   ESA CORRECCION YA ESTA HECHA. La funcion es

       Pedido.php:1396   private function descargarImagenComoDataUri($rutaOUrl)

   y arranca, textualmente:

       $esUrlAbsoluta = (stripos($rutaOUrl, 'http://') === 0
                      || stripos($rutaOUrl, 'https://') === 0);

       if (!$esUrlAbsoluta) {
           // Intento 1: archivo local, relativo a la raiz del proyecto (FCPATH)
           $rutaLocal = FCPATH . ltrim($rutaOUrl, '/');
           ...
       }

       if ($data === false || $data === '') {
           // Intento 2: por HTTP. Si ya era una URL absoluta se usa tal cual;
           // si era relativa, se arma con el base_url() ...
           $url = $esUrlAbsoluta ? $rutaOUrl
                : rtrim($this->config->item('base_url'), '/') . '/'
                  . ltrim($rutaOUrl, '/');
           ...
       }

   O sea que el `base_url()` esta DENTRO del ternario, en la rama de la ruta
   relativa. Una URL absoluta salta el intento 1 entero y se usa sin tocar.

   Ademas descarga con `User-Agent: Mozilla/5.0`, timeout 10 s, y con
   `verify_peer => false` -- asi que una URL de REGLA servida por HTTPS se baja
   sin problema. Y si falla, el PDF imprime la URL en el lugar de la foto en
   vez de dejar un hueco, que es exactamente lo que hace falta para
   diagnosticar sin entrar al servidor.

   NO HAY NADA QUE CAMBIAR. Escribir la funcion "corregida" habria sido
   desplegar un cambio para un problema que no existe -- que es lo que ya paso
   con el bloque M1, cuando reporte que `actualizar()` estaba clavado en
   `newstocks_cidef` leyendo la spec en vez del archivo que corre.

   DONDE SE USA, para que quede el circuito completo:

       Pedido.php:1463  private function generarPdfInspeccion($indice)
                        lee archivo1..archivo9 con getarchivoNbyvin(),
                        descarta los vacios, y por cada uno llama a
                        descargarImagenComoDataUri() y lo embebe como data URI.

       Pedido.php:2130  $pdf_output = $this->generarPdfInspeccion($indice);
                        dentro de `inicio_proces()`, que es EL DESPACHO --
                        no la inspeccion. Ahi se manda el correo con
                        addStringAttachment($pdf_output, ...).

   CONSECUENCIA DE ALCANCE, y es la que importa: el PDF con las fotos NO sale
   cuando el movilizador hace la inspeccion, sale cuando administracion
   DESPACHA. Son dos pantallas distintas de dos controladores distintos. REGLA
   hace la primera; la segunda la sigue haciendo el legado, y por eso las fotos
   tienen que estar en `archivo1..archivo9` para cuando llegue.

   UNA COSA QUE CONVIENE SABER, aunque no cambia nada hoy:
   `getarchivo1byvin($vin)` busca por VIN con `ORDER BY fecha_entrega DESC`.
   Para un VIN que reingresa, el PDF toma las fotos de la inspeccion mas
   reciente -- que es lo correcto -- pero es match por VIN, no por pasada. Es
   la regla de la frontera: las tablas del legado usan la clave del legado.
   =========================================================================== */
