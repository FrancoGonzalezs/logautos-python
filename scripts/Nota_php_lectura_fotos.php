<?php if (!defined('BASEPATH')) exit('No direct script access allowed');

/**
 * Nota_php_lectura_fotos.php -- que la exportacion MASIVA de OT encuentre
 * tambien las fotos que estan en `assets/images/danos/`.
 *
 * NO VA EN ESTE REPOSITORIO NI SE INCLUYE. Son DOS ediciones:
 *
 *     application/controllers/Nota.php          -- dos metodos NUEVOS
 *     application/views/nota/exportar_ot_masivo.php  -- un bloque de JS
 *
 * NINGUNA sobre Pedido.php, ninguna sobre Api_regla.php, y NINGUN metodo
 * existente se reemplaza.
 *
 * =========================================================================
 * ESTE BLOQUE CAMBIO DOS VECES ANTES DE LLEGAR ACA. Vale contarlo, porque las
 * dos correcciones salieron de mirar y no de suponer.
 * =========================================================================
 *
 * La primera version reemplazaba `listarFotos()` para que devolviera objetos
 * con `nombre` y `url`. ESTABA MAL: sus dos consumidores arman la URL ellos
 * mismos --`${baseURL}/${motonaveRuta}/${vin}/${encodeURIComponent(nombre)}`--
 * asi que cambiar la forma del JSON los rompe a los dos. Y romper la propiedad
 * "si el agregado falla, devuelve lo mismo que hoy" es perder la unica razon
 * por la que este cambio es barato.
 *
 * La segunda version editaba `procesarExportacionMasiva()`. TAMBIEN ESTABA MAL:
 * ese metodo NO LO LLAMA NADIE. Tiene su ruta en routes.php y ninguna vista lo
 * referencia. Es codigo muerto, y editarlo habria sido trabajo invisible.
 *
 * =========================================================================
 * QUIEN EXPORTA QUE, medido sobre el menu y las vistas
 * =========================================================================
 *
 *   boton del menu                        vista                   fuente
 *   ------------------------------------- ----------------------- ---------------
 *   EXPORTAR OT Y FOTOS                   exportar_ot_fotos       {motonave}/{vin}/
 *   EXPORTAR OT Y FOTOS (SIN MOTONAVE)    exportar_ot_fotos_new   danos/ PLANA
 *   EXPORTAR OT/FOTOS MASIVO              exportar_ot_masivo      {motonave}/{vin}/
 *   (sin boton)                           --                      procesarExportacionMasiva, MUERTO
 *
 * LO INDIVIDUAL YA ESTA RESUELTO: el segundo boton se llama literalmente
 * "SIN MOTONAVE" y usa `listarFotosViejas`, que barre `danos/`. El legado ya
 * tenia el problema y ya lo habia resuelto con un boton aparte.
 *
 * LO QUE NO TIENE SALIDA ES LA MASIVA. Es la unica que se queda sin las fotos,
 * y es la que importa: durante el mes en paralelo TODOS los check list se hacen
 * en REGLA, que escribe en la plana, asi que la masiva pasaria de no ver el 26%
 * a no ver nada.
 *
 * =========================================================================
 * DONDE VA CADA COSA, contra la copia que tenemos (Nota.php, 2026-08-20)
 * =========================================================================
 *
 *   1. `_fotosDeVinEnDanos($vin)`    METODO NUEVO, `private`.
 *   2. `listarUrlsDeFotos($m, $vin)`  METODO NUEVO, `public`.
 *
 *      Los dos se agregan juntos, por ejemplo justo antes de
 *      `    private function _generarPdfOtServer($otId, $outputPath)`
 *      que en nuestra copia esta en la linea 21837.
 *
 *      Verificado que ninguno de los dos nombres existe en el archivo.
 *
 *   3. Una ruta en routes.php.
 *   4. Un bloque de JS en `views/nota/exportar_ot_masivo.php`, lineas 98 a 112.
 *
 * `listarFotos()` (lineas 19396 a 19404) NO SE TOCA. `exportar_ot_fotos` sigue
 * funcionando exactamente igual que hoy.
 *
 * =========================================================================
 * SOBRE SI LA COPIA LOCAL ESTA VIEJA
 * =========================================================================
 *
 * El despliegue del IT nuevo del 2026-09-02 NO toco Nota.php:
 *
 *     Nota.php     2026-08-20    0 referencias al IT nuevo
 *     Pedido.php   2026-09-02    5 referencias
 *
 * El riesgo aplica a Pedido.php, que no se toca. Aun asi, la comprobacion
 * barata antes de editar, desde el navegador ya logueado (`nota/listarFotos`
 * responde 307 al login, asi que curl no sirve):
 *
 *     https://logautos.cl/clientes/nota/listarFotos/NO_EXISTE/VINQUENOEXISTE1
 *     -> tiene que devolver exactamente  []
 *
 * Si devuelve otra cosa, la copia local no es la desplegada y hay que bajar el
 * archivo antes de tocarlo.
 */


/* ===========================================================================
   EDICION 1 -- `_fotosDeVinEnDanos`, metodo NUEVO
   ===========================================================================

   POR QUE `glob` Y NO `scandir`

   `listarFotosViejas()` --que ya existe-- usa `scandir()` y recorre TODO el
   directorio en PHP buscando el VIN con `strpos`. Para una pantalla que se abre
   de a una OT, da igual.

   La masiva no: llama a esto UNA VEZ POR CADA OT del lote. Y `danos/` es
   grande: contando las URL guardadas en `check_list.link` hay del orden de
   109.000 archivos. Recorrer 109.000 entradas en PHP por cada OT de un lote de
   cincuenta es medio millon de vueltas de bucle y la lista entera en memoria
   cincuenta veces.

   `glob()` con el VIN adentro del patron deja que el sistema de archivos
   filtre.

   EL VIN SE SANEA ANTES DE ENTRAR AL PATRON: un `*`, un `?` o un `[` cambiarian
   el significado del glob. Los VIN son [A-Z0-9] y el saneo no deberia sacar
   nada -- pero el VIN sale de la base, no de una lista blanca.

   Y SE EXIGE LARGO MINIMO 10: con un VIN vacio o de dos letras el patron `*XX*`
   engancharia fotos de otros vehiculos y las meteria en el ZIP equivocado.
   Preferible no devolver nada.
   =========================================================================== */

    /**
     * Las fotos de un VIN que quedaron en `assets/images/danos/` (plana).
     *
     * El legado escribe ahi cuando la unidad NO tiene motonave
     * (`if (empty($motonave))` en `subida_foto_check_list_proces`), y ahi
     * escribe REGLA. Devuelve rutas absolutas del sistema de archivos.
     */
    private function _fotosDeVinEnDanos($vin)
    {
        $vin = preg_replace('/[^A-Za-z0-9]/', '', (string) $vin);
        if (strlen($vin) < 10) {
            return array();
        }
        $dir = FCPATH . 'assets/images/danos/';
        if (!is_dir($dir)) {
            return array();
        }
        $fotos = glob($dir . '*' . $vin . '*.{jpg,jpeg,png,gif,JPG,JPEG,PNG,GIF}',
                      GLOB_BRACE);
        return is_array($fotos) ? $fotos : array();
    }


/* ===========================================================================
   EDICION 2 -- `listarUrlsDeFotos`, metodo NUEVO
   ===========================================================================

   ES UN METODO NUEVO Y NO UN CAMBIO A `listarFotos`, y esa es toda la gracia:

     * `listarFotos` queda intacta, asi que `exportar_ot_fotos` --el boton
       "EXPORTAR OT Y FOTOS"-- se comporta exactamente igual que hoy. Cero
       riesgo sobre lo que ya funciona.
     * Si algo de esto falla, falla la masiva y nada mas.

   DEVUELVE URL COMPLETAS, no basenames. Esa es la diferencia que hace que
   funcione: las fotos de las dos carpetas viven en lugares distintos, asi que
   el que las lista tiene que decir DONDE esta cada una. `listarFotos` devuelve
   basenames y por eso su consumidor tiene que adivinar la carpeta -- y solo
   sabe adivinar una.
   =========================================================================== */

    /**
     * Las fotos de un VIN, de LAS DOS carpetas, como URL COMPLETAS.
     *
     * EL NOMBRE DICE QUE DEVUELVE, y no es un detalle de estilo: al lado vive
     * `listarFotos()`, que devuelve NOMBRES. Dos metodos casi homonimos con
     * contratos distintos es exactamente la forma de la Regla 0 -- el que lee
     * `listarFotosTodas` --el nombre que este metodo tuvo un rato-- asume que
     * es `listarFotos` con mas filas, y arma la URL a mano como hace el JS
     * viejo. Le sale una ruta rota y no hay error que lo diga.
     *
     * `listarUrlsDeFotos` no se puede confundir. Es gratis ahora e imposible
     * despues, cuando haya tres consumidores.
     *
     * GET /nota/listarUrlsDeFotos/{motonave_con_guiones_bajos}/{vin}
     *
     * La motonave puede venir vacia: en ese caso solo devuelve las de `danos/`,
     * que es exactamente lo que corresponde a una unidad sin motonave.
     */
    public function listarUrlsDeFotos($motonave, $vin)
    {
        $salida = array();
        $vistos = array();

        // 1. La carpeta por motonave, la de siempre.
        $motonave = trim((string) $motonave);
        if ($motonave !== '' && $motonave !== 'NO_EXISTE') {
            $ruta = FCPATH . "assets/images/{$motonave}/{$vin}/";
            if (is_dir($ruta)) {
                $fotos = glob($ruta . "*.{jpg,jpeg,png,gif,JPG,JPEG,PNG,GIF}",
                              GLOB_BRACE);
                if (is_array($fotos)) {
                    foreach ($fotos as $f) {
                        $n = basename($f);
                        if (isset($vistos[$n])) { continue; }
                        $vistos[$n] = TRUE;
                        $salida[] = base_url("assets/images/{$motonave}/{$vin}/" . rawurlencode($n));
                    }
                }
            }
        }

        // 2. La carpeta plana. Ver el encabezado del bloque.
        foreach ($this->_fotosDeVinEnDanos($vin) as $f) {
            $n = basename($f);
            if (isset($vistos[$n])) { continue; }
            $vistos[$n] = TRUE;
            $salida[] = base_url('assets/images/danos/' . rawurlencode($n));
        }

        header('Content-Type: application/json');
        echo json_encode($salida);
    }


/* ---------------------------------------------------------------------------
   EDICION 3 -- LA RUTA, en routes.php.

   Va junto a las otras dos de exportacion, que estan en las lineas 93 y 94:

       $route['nota/listarUrlsDeFotos/(:any)/(:any)'] = 'nota/listarUrlsDeFotos/$1/$2';

   Sin la ruta, CodeIgniter igual resuelve `nota/listarUrlsDeFotos/a/b` por el
   ruteo por defecto. Se agrega igual para que quede al lado de las otras y no
   dependa de que nadie cambie el ruteo por defecto.
   --------------------------------------------------------------------------- */


/* ===========================================================================
   EDICION 4 -- el JS de `views/nota/exportar_ot_masivo.php`, lineas 98 a 112
   ===========================================================================

   ES LA UNICA EDICION SOBRE ALGO QUE YA EXISTE, y es de quince lineas de JS.

   HOY DICE (lineas 98 a 112):

       const fotosRes = await fetch(`<?php echo base_url('nota/listarFotos'); ?>/${motonaveRuta}/${vin}`);
       const fotos = await fotosRes.json();

       for(const nombre of fotos) {
           const fotoUrl = `${baseURL}/${motonaveRuta}/${vin}/${encodeURIComponent(nombre)}`;
           try {
               const r = await fetch(fotoUrl);
               if(r.ok) {
                   const blob = await r.blob();
                   folder.file(nombre, blob);
               }
           } catch(e) {
               console.warn(`Foto ${nombre} no cargada`);
           }
       }

   PASA A DECIR:
   --------------------------------------------------------------------------- */
?>
                // Las fotos de las DOS carpetas. `listarUrlsDeFotos` devuelve
                // URL completas justamente porque las dos carpetas estan en
                // lugares distintos: acá ya no se puede adivinar la ruta.
                const fotosRes = await fetch(`<?php echo base_url('nota/listarUrlsDeFotos'); ?>/${motonaveRuta || 'NO_EXISTE'}/${vin}`);
                const fotos = await fotosRes.json();

                for(const fotoUrl of fotos) {
                    const nombre = decodeURIComponent(fotoUrl.split('/').pop());
                    try {
                        const r = await fetch(fotoUrl);
                        if(r.ok) {
                            const blob = await r.blob();
                            folder.file(nombre, blob);
                        }
                    } catch(e) {
                        console.warn(`Foto ${nombre} no cargada`);
                    }
                }
<?php
/* ===========================================================================
   LA COMPROBACION DE CONTENIDO, desde el navegador logueado
   ===========================================================================

   `nota/...` responde 307 al login para un cliente sin sesion, asi que esto se
   mira desde el navegador de Franco.

   ANTES de subir -- confirma que la copia local es la desplegada:

       .../nota/listarFotos/NO_EXISTE/VINQUENOEXISTE1      ->  []

   DESPUES de subir, las tres en orden:

   1. QUE EL METODO NUEVO EXISTE. Con un VIN que tenga fotos en `danos/`
      --sirve cualquiera de un check list sin motonave:

          .../nota/listarUrlsDeFotos/NO_EXISTE/<VIN>

      Tiene que devolver una lista de URL que terminan en
      `/assets/images/danos/...`.

      ESTA ES LA COMPROBACION DE CONTENIDO. Un Nota.php sin la edicion
      responde 404, no una lista vacia: `listarUrlsDeFotos` no existiria. Es la
      diferencia con los fallos mudos de las otras veces -- aca la ausencia se
      ve, porque lo que se agrega es un metodo nuevo y no el reemplazo de uno
      que ya estaba.

   2. QUE LA FUENTE VIEJA SIGUE. Con una unidad que SI tiene motonave:

          .../nota/listarUrlsDeFotos/<MOTONAVE_CON_GUIONES_BAJOS>/<VIN>
          -> las mismas fotos de siempre, ahora como URL completas.

      Y en paralelo, que lo de siempre no cambio:

          .../nota/listarFotos/<MOTONAVE>/<VIN>
          -> la MISMA lista de nombres que antes de subir. Si esto cambio,
             algo se toco que no habia que tocar.

   3. LA MASIVA DE VERDAD: exportar un lote de dos OT, una con motonave y otra
      sin, y abrir el ZIP. Las dos carpetas del ZIP tienen que traer fotos.

   SI ALGO SALE MAL, lo que se rompe es la masiva y nada mas: `listarFotos` no
   se toco, asi que los dos botones individuales siguen andando.
   =========================================================================== */


/* ===========================================================================
   BLOQUE R2 -- PRE-APROBADO, NO desplegar todavia
   Un solo glob para todo el lote, con indice VIN -> archivos
   ===========================================================================

   CUANDO SE USA ESTO

   Solo si la PRIMERA exportacion masiva despues de desplegar el bloque R tarda
   notoriamente mas que antes. No se despliega "por las dudas": es codigo mas
   complicado para un problema que puede no existir, y el unico dato que dice si
   existe es una corrida real.

   POR QUE PODRIA HACER FALTA

   `danos/` tiene del orden de 109.000 archivos, y `_fotosDeVinEnDanos()` hace
   un `glob()` por cada OT del lote. `glob` filtra a nivel del sistema de
   archivos --mucho mejor que `scandir` + bucle-- pero igual RECORRE el
   directorio entero cada vez: cincuenta OT son cincuenta barridos de 109.000
   entradas.

   No se puede medir desde aca ni desde el cPanel sin terminal. Y estimar cuanto
   tarda un `glob` sobre un ext4 con 109.000 archivos, sin saber si hay
   `dir_index` ni como esta el cache del inode, seria inventar un numero. La
   corrida real lo contesta en un minuto.

   QUE CAMBIA

   Un solo barrido por LOTE en vez de uno por OT, y un indice
   `VIN -> [archivos]` en memoria. Con 109.000 archivos el indice son unos pocos
   MB de strings; el `memory_limit` ya esta en 512M en ese metodo.

   EL INDICE SE ARMA CON LOS VIN QUE EL LOTE PIDE, no con todos. Asi el mapa
   tiene tantas entradas como OT haya --cincuenta, no 109.000-- y el barrido
   descarta al vuelo lo que no interesa.

   COMO SE APLICA

     1. Se agrega `_indiceDeFotosEnDanos(array $vins)` junto a los otros
        `private`.
     2. En `listarUrlsDeFotos` NO se toca nada: esa es de a un VIN y el glob
        puntual le sirve mejor.
     3. En el JS de la masiva se agrega UNA llamada previa que pide el indice
        del lote entero, y el bucle usa el indice en vez de llamar por OT.

   O sea que R2 tampoco reemplaza nada de R: agrega un metodo y cambia el mismo
   bloque de JS que R ya habia cambiado.
   =========================================================================== */

    /**
     * Un solo barrido de `danos/` para TODO un lote. Devuelve VIN -> [urls].
     *
     * Se le pasan los VIN del lote y arma el indice solo con esos: el mapa
     * queda del tamaño del lote, no del directorio.
     */
    private function _indiceDeFotosEnDanos(array $vins)
    {
        $indice = array();
        $buscados = array();
        foreach ($vins as $v) {
            $v = preg_replace('/[^A-Za-z0-9]/', '', (string) $v);
            if (strlen($v) >= 10) {
                $buscados[$v] = TRUE;
                $indice[$v] = array();
            }
        }
        if (!$buscados) {
            return array();
        }

        $dir = FCPATH . 'assets/images/danos/';
        if (!is_dir($dir)) {
            return $indice;
        }

        // UN barrido. `glob` sin patron de VIN devuelve todo el directorio, asi
        // que se usa un iterador: no carga los 109.000 nombres en un array de
        // una sola vez.
        $it = new DirectoryIterator($dir);
        foreach ($it as $f) {
            if ($f->isDot() || !$f->isFile()) { continue; }
            $nombre = $f->getFilename();
            if (!preg_match('/\.(jpg|jpeg|png|gif)$/i', $nombre)) { continue; }
            // El VIN esta al principio del nombre en todos los patrones del
            // legado (`{vin}_...`), pero se busca en cualquier posicion por si
            // alguno no lo respeta.
            foreach ($buscados as $vin => $_) {
                if (strpos($nombre, $vin) !== FALSE) {
                    $indice[$vin][] = base_url('assets/images/danos/'
                                               . rawurlencode($nombre));
                    break;
                }
            }
        }
        return $indice;
    }

/* ---------------------------------------------------------------------------
   Y el endpoint que lo expone, para que el JS lo pida una sola vez.

       POST /nota/indiceDeFotosDanos     cuerpo: vins[] = [...]

   POST y no GET porque un lote de cincuenta VIN de diecisiete caracteres no
   entra comodo en una URL.
   --------------------------------------------------------------------------- */

    public function indiceDeFotosDanos()
    {
        $vins = $this->input->post('vins');
        if (!is_array($vins)) { $vins = array(); }
        header('Content-Type: application/json');
        echo json_encode($this->_indiceDeFotosEnDanos($vins));
    }

/* ---------------------------------------------------------------------------
   Y el JS de la masiva, que pasa a pedir el indice UNA vez antes del bucle.

   ANTES del `for` que recorre las OT:

       // Un solo barrido de danos/ para todo el lote (ver bloque R2).
       let indiceDanos = {};
       try {
           const r = await fetch(`<?php echo base_url('nota/indiceDeFotosDanos'); ?>`, {
               method: 'POST',
               headers: {'Content-Type': 'application/x-www-form-urlencoded'},
               body: new URLSearchParams(vinsDelLote.map(v => ['vins[]', v]))
           });
           indiceDanos = await r.json();
       } catch (e) {
           console.warn('sin indice de danos, se sigue sin esas fotos', e);
       }

   Y adentro del bucle, en vez de la llamada por OT:

       const porMotonave = await (await fetch(
           `<?php echo base_url('nota/listarUrlsDeFotos'); ?>/${motonaveRuta || 'NO_EXISTE'}/${vin}`
       )).json();
       const fotos = porMotonave.concat(indiceDanos[vin] || []);

   OJO: `listarUrlsDeFotos` seguiria devolviendo TAMBIEN las de `danos/`, asi
   que habria dos fuentes para lo mismo y se duplicarian. Si se aplica R2, hay
   que sacarle a `listarUrlsDeFotos` su segunda fuente O deduplicar por nombre
   en el JS. La segunda es mas segura: no toca un metodo que ya anda.

       const vistos = new Set();
       const fotos = porMotonave.concat(indiceDanos[vin] || [])
                                .filter(u => !vistos.has(u) && vistos.add(u));

   ESO ES LO UNICO DE R2 QUE PUEDE MORDER, y por eso queda dicho acá y no en un
   comentario suelto: R2 agrega una segunda ruta hacia las mismas fotos, y dos
   caminos al mismo dato sin deduplicar es un ZIP con todo repetido.
   --------------------------------------------------------------------------- */
