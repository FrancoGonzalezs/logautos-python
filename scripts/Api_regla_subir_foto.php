<?php if (!defined('BASEPATH')) exit('No direct script access allowed');

/**
 * Api_regla_subir_foto.php -- el endpoint de SUBIDA DE ARCHIVOS.
 *
 * NO VA EN ESTE REPOSITORIO NI SE INCLUYE. Es UNA edicion sobre
 *
 *     application/controllers/Api_regla.php
 *
 * mas UNA ruta en routes.php.
 *
 * ES EL ENDPOINT MAS DELICADO QUE LE AGREGAMOS AL LEGADO. Todo lo demas
 * escribe filas; esto escribe ARCHIVOS en un servidor que ademas sirve PHP.
 * El peor resultado posible de una fila mal escrita es un dato equivocado; el
 * peor resultado posible de un archivo mal escrito es ejecucion remota.
 *
 * Por eso el diseño esta antes que el codigo, y las cinco propiedades de abajo
 * no son configuraciones: son la razon por la que este endpoint se puede subir.
 *
 * COMPROBACION DE CONTENIDO despues de subir (Franco no tiene Terminal, va por
 * HTTP; ver el bloque Q4).
 */


/* ===========================================================================
   BLOQUE Q1 -- EL DISEÑO, y lo que NO se pudo cumplir tal cual
   ===========================================================================

   LAS CINCO PROPIEDADES

   1. EL CUERPO ES JSON, el archivo va en base64.
      Reusa `exigir_api_key()`, `json()` y `api_idempotency` como todo lo
      demas. Nada de multipart: multipart trae su propio parser, sus propios
      limites y su propia superficie, y el unico motivo para usarlo seria
      ahorrar el 33% del base64 -- que sobre una foto de 80 KB son 27 KB.

   2. LA CARPETA LA DECIDE LA ENTIDAD. Hay una lista blanca
      entidad -> carpeta, y la carpeta NUNCA sale del cuerpo. Una ruta que
      viene en el pedido es escritura arbitraria de archivos; con un `.php`
      adentro es ejecucion remota. No hay validacion de ruta que sea mas
      segura que no aceptar rutas.

   3. EL NOMBRE LO GENERA EL SERVIDOR. El cuerpo aporta a lo sumo datos
      (`vin`, un rotulo de una lista blanca, un numero), y el servidor arma el
      nombre con ellos ya saneados. El cliente nunca elige como se llama un
      archivo en disco.

   4. EL TIPO SE VALIDA POR BYTES, no por el nombre ni por el Content-Type.
      Se miran los BYTES MAGICOS y ademas `getimagesize()`, que es la misma
      funcion que usa dompdf para decidir si puede dibujar la imagen: si
      `getimagesize` no la reconoce, el PDF tampoco la va a poder usar, asi
      que aceptarla seria guardar un archivo inutil.

   5. FALLA SI EL DISCO ESTA BAJO. Una subida de foto no puede ser lo que
      llene el disco del servidor con el que trabaja la empresa hoy.

   ---------------------------------------------------------------------------
   LO QUE NO SE PUDO CUMPLIR TAL CUAL -- DOS COSAS, Y HAY QUE SABERLAS
   ---------------------------------------------------------------------------

   (A) EL MARGEN DE DISCO NO VE LA CUOTA DE cPANEL.

   `disk_free_space()` informa el FILESYSTEM, y en un hosting compartido ese
   filesystem es el del servidor entero, no la cuota de la cuenta. O sea que
   puede devolver cientos de GB libres mientras la cuenta esta al 97% de sus
   200 GB -- que es exactamente la situacion de hoy.

   No hay forma confiable de leer la cuota de cPanel desde PHP sin la API de
   cPanel, que es otra credencial y otra dependencia.

   Entonces el margen se implementa con DOS frenos, y el segundo es el que de
   verdad protege:

     * `disk_free_space()`, que atrapa el caso "el disco fisico se lleno".
     * Y un INTERRUPTOR EXPLICITO, `subida_habilitada()`, que devuelve FALSE
       hasta que alguien lo cambie a mano. Mientras este en FALSE el endpoint
       responde 503 y no escribe nada.

   El interruptor no es una formalidad: es la unica proteccion real contra la
   cuota, y es exactamente lo que se pidio -- que la subida al legado no se
   encienda hasta confirmar que el disco bajo. Se sube el codigo hoy, se
   prende el dia que el disco este sano, y encenderlo es cambiar `FALSE` por
   `TRUE` en una linea.

   (B) LA CARPETA DEL CHECK LIST DE INGRESO NO SE PUEDE REPLICAR.

   El legado guarda los daños del check list en

       assets/images/{motonave}/{vin}/

   y las dos partes salen del dato. La motonave es texto libre y ya rompio una
   vez: 'COSCO PACIFIC / YANTIAN' tiene una barra adentro, asi que un solo
   barco crea dos directorios anidados.

   Replicarlo exigiria aceptar la carpeta desde el cuerpo, que es justo lo que
   la propiedad 2 prohibe. Asi que los daños de REGLA van a
   `assets/images/danos/`, que es una carpeta que el legado YA usa en
   `subida_foto_check_list_proces`.

   No afecta al PDF del despacho: ese lee `archivo1..archivo9` de
   `inspeccion_despacho`, que apuntan a `assets/images/unidades/` -- y esa SI
   es plana y se replica exacta.
   =========================================================================== */


/* ===========================================================================
   BLOQUE Q2 -- DONDE ESCRIBE HOY CADA MODULO, medido sobre el PHP real
   ===========================================================================

   | modulo                    | carpeta                          | nombre
   |---------------------------|----------------------------------|--------
   | check list ingreso, daños | assets/images/{motonave}/{vin}/  | {vin}_{pieza}_{tipo}_{nivel}_{Y-m-d H:i:s}_.jpg
   | check list ingreso, guia  | assets/images/guia/              | idem patron
   | check list ingreso, unidad| assets/images/foto_unidad/       | {vin}_foto_unidad_{Y-m-d}_.jpg
   | check list mecanico       | assets/images/falla/             | {vin}_FALLA_MECANICA_NRO_{n}_{Y-m-d H:i:s}_.jpg
   | inspeccion de despacho    | assets/images/unidades/          | {vin}_INSPECCION_{rotulo}_{Y-m-d H:i:s}_.jpg
   | IT                        | assets/images/it/{vin}/          | IT_{vin}_{Y-m-d_H-i-s}_{n}.jpg

   TRES COSAS DEL NOMBRE que hay que copiar para que los nuestros no se
   distingan:

     * el saneo es `strtr($nombre, " ", "_")` -- SOLO espacios. Los DOS PUNTOS
       de la hora se quedan: los nombres reales dicen
       `..._2026-08-13_09:29:51_.jpg`. Sanearlos de mas haria que los de REGLA
       se reconozcan de lejos.
     * hay un guion bajo ANTES de la extension: `_.jpg`, no `.jpg`. Sale de
       `...date('Y-m-d H:i:s')."_".".jpg"`, que es un pegado accidental --
       pero es el que tienen los 16.365 archivos que ya existen.
     * el IT es el unico con otro estilo (`IT_` adelante y `Y-m-d_H-i-s` con
       guiones), porque lo escribio otra persona en otro momento.
   =========================================================================== */


/* ===========================================================================
   BLOQUE Q3 -- EL CODIGO
   ===========================================================================

   Metodos NUEVOS. Se agregan antes del `}` que cierra la clase, junto a
   `crear_fila` y `leer_fila`. NO reemplazan nada.
   =========================================================================== */

    /**
     * EL INTERRUPTOR. En FALSE no se escribe ni un byte.
     *
     * Se prende el dia que el disco de la cuenta este sano, cambiando esta
     * linea. Ver el bloque Q1 (A): `disk_free_space()` no ve la cuota de
     * cPanel, asi que esto es la unica proteccion real contra llenarla.
     */
    private function subida_habilitada()
    {
        return FALSE;
    }

    /**
     * Margen libre exigido en el filesystem, en MB. Atrapa el caso "el disco
     * fisico se lleno"; NO ve la cuota de la cuenta.
     */
    private function margen_disco_mb()
    {
        return 500;
    }

    /** Tope de una foto, en bytes YA DECODIFICADA. */
    private function tope_foto_bytes()
    {
        return 2 * 1024 * 1024;      // 2 MB
    }

    /**
     * LA LISTA BLANCA entidad -> carpeta. La carpeta NUNCA viene del cuerpo.
     *
     * Las rutas son relativas a FCPATH y terminan en barra. No hay
     * subcarpetas por dato: ver el bloque Q1 (B).
     */
    private function carpeta_de_foto($entidad)
    {
        $mapa = array(
            'inspeccion_despacho' => 'assets/images/unidades/',
            'check_list_dano'     => 'assets/images/danos/',
            'check_list_unidad'   => 'assets/images/foto_unidad/',
            'check_list_guia'     => 'assets/images/guia/',
            'falla_mecanica'      => 'assets/images/falla/',
        );
        return isset($mapa[$entidad]) ? $mapa[$entidad] : null;
    }

    /**
     * Los rotulos permitidos por entidad, para armar el nombre.
     *
     * Tambien lista blanca: el rotulo va DENTRO del nombre del archivo, asi
     * que dejarlo libre seria dejar el nombre libre por la ventana.
     */
    private function rotulos_de_foto($entidad)
    {
        $mapa = array(
            // Los cuatro de `inspeccion_despacho()`, mas el generico del paso
            // de fotos, que en el legado sale de un desplegable.
            'inspeccion_despacho' => array('TABLERO', 'GUIA', 'NIVELES',
                                           'NEUMATICOS', 'UNIDAD', 'DANO',
                                           'ACCESORIOS', 'MOTOR', 'INTERIOR'),
            'check_list_dano'     => array('DANO'),
            'check_list_unidad'   => array('foto_unidad'),
            'check_list_guia'     => array('GUIA'),
            'falla_mecanica'      => array('FALLA_MECANICA'),
        );
        return isset($mapa[$entidad]) ? $mapa[$entidad] : array();
    }

// ---------------------------------------------------------------------------
// POST /api_regla/subir_foto/<entidad>
// ---------------------------------------------------------------------------
//
// Cuerpo (JSON):
//   {"vin":"...", "rotulo":"TABLERO", "numero":3, "contenido_b64":"...."}
//
// Cabecera: Idempotency-Key (UUID). OBLIGATORIA -- sin ella un reintento deja
// dos archivos con nombres distintos y el segundo no lo referencia nadie.
//
//   201 {"ok":true,"ruta":"assets/images/unidades/XXX_.jpg","bytes":81234}
//   200 {"ok":true,"ruta":"...","idempotente":true}
//   400 / 401 / 404 / 413 / 415 / 503
public function subir_foto($entidad = null)
{
    if (strtolower($this->input->method()) !== 'post') {
        $this->json(405, array('error' => 'metodo no permitido'));
    }
    $this->exigir_api_key();

    // -- 0. EL INTERRUPTOR, antes que nada ---------------------------------
    if (!$this->subida_habilitada()) {
        $this->json(503, array(
            'error'  => 'la subida de fotos al sistema anterior esta apagada',
            'codigo' => 'subida_apagada',
        ));
    }

    $carpeta = $this->carpeta_de_foto($entidad);
    if ($carpeta === null) {
        $this->json(404, array('error' => 'entidad sin carpeta: ' . $entidad));
    }

    $crudo = $this->input->raw_input_stream;
    if ($crudo === NULL || $crudo === '') {
        $crudo = file_get_contents('php://input');
    }
    $datos = json_decode($crudo, TRUE);
    if (!is_array($datos)) {
        $this->json(400, array('error' => 'body invalido: se esperaba JSON'));
    }

    $idem_key = isset($_SERVER['HTTP_IDEMPOTENCY_KEY'])
        ? trim($_SERVER['HTTP_IDEMPOTENCY_KEY']) : '';
    if ($idem_key === '') {
        $this->json(400, array(
            'error'  => 'Idempotency-Key es obligatoria: un reintento sin ella '
                      . 'deja dos archivos y el segundo no lo referencia nadie',
            'codigo' => 'falta_idempotency_key',
        ));
    }
    $consulta = $this->db->get_where('api_idempotency', array(
        'idem_key' => $idem_key, 'entidad' => 'foto_' . $entidad));
    if ($consulta === FALSE) {
        $this->json(500, array('error' => 'falta la tabla api_idempotency'));
    }
    $previo = $consulta->row();
    if ($previo) {
        $this->json(200, array('ok' => TRUE, 'idempotente' => TRUE,
                               'ruta' => $previo->respuesta));
    }

    // -- 1. EL DISCO -------------------------------------------------------
    //
    // Ver el bloque Q1 (A): esto NO ve la cuota de cPanel. El freno de verdad
    // es `subida_habilitada()`. Esto atrapa el otro caso.
    $libre = @disk_free_space(FCPATH);
    if ($libre !== FALSE && $libre < $this->margen_disco_mb() * 1024 * 1024) {
        $this->json(503, array(
            'error'  => 'disco por debajo del margen',
            'codigo' => 'sin_espacio',
            'libre_mb' => round($libre / 1024 / 1024, 1),
            'margen_mb' => $this->margen_disco_mb(),
        ));
    }

    // -- 2. EL CONTENIDO ---------------------------------------------------
    $b64 = isset($datos['contenido_b64']) ? (string) $datos['contenido_b64'] : '';
    if ($b64 === '') {
        $this->json(400, array('error' => 'falta contenido_b64'));
    }
    // `strict` en TRUE: un base64 con basura adentro se rechaza en vez de
    // decodificarse a medias.
    $bin = base64_decode($b64, TRUE);
    if ($bin === FALSE || strlen($bin) < 100) {
        $this->json(400, array('error' => 'contenido_b64 no es base64 valido'));
    }
    if (strlen($bin) > $this->tope_foto_bytes()) {
        $this->json(413, array(
            'error'  => 'la foto supera el tope',
            'codigo' => 'muy_grande',
            'bytes'  => strlen($bin),
            'tope'   => $this->tope_foto_bytes(),
        ));
    }

    // -- 3. EL TIPO, POR BYTES ---------------------------------------------
    //
    // Primero los bytes magicos, que es lo unico que no se puede falsificar
    // con el nombre ni con el Content-Type. Despues `getimagesize()`, que es
    // LA MISMA funcion que usa dompdf (`Helpers::dompdf_getimagesize`): si
    // ella no la reconoce, el PDF del despacho tampoco va a poder dibujarla y
    // guardarla seria guardar un archivo inutil.
    //
    // WebP NO entra: dompdf 0.8.6 mapea solo JPEG, GIF, BMP y PNG. Verificado
    // en su Helpers.php.
    $ext = null;
    if (substr($bin, 0, 3) === "\xFF\xD8\xFF") {
        $ext = 'jpg';
    } elseif (substr($bin, 0, 8) === "\x89PNG\r\n\x1A\n") {
        $ext = 'png';
    }
    if ($ext === null) {
        $this->json(415, array(
            'error'  => 'solo jpg y png, verificado por bytes',
            'codigo' => 'tipo_no_permitido',
        ));
    }
    // Segunda puerta: que PHP la pueda abrir de verdad.
    $tmp = tempnam(sys_get_temp_dir(), 'regla_');
    file_put_contents($tmp, $bin);
    $info = @getimagesize($tmp);
    @unlink($tmp);
    if ($info === FALSE
        || !in_array($info[2], array(IMAGETYPE_JPEG, IMAGETYPE_PNG), TRUE)) {
        $this->json(415, array(
            'error'  => 'el contenido no es una imagen que se pueda dibujar',
            'codigo' => 'imagen_invalida',
        ));
    }

    // -- 4. EL NOMBRE, GENERADO ACA ----------------------------------------
    //
    // El cuerpo aporta datos, nunca el nombre. `$vin` se acota a
    // [A-Z0-9] y el rotulo sale de una lista blanca, asi que no hay forma de
    // meter una barra, un punto ni una segunda extension.
    $vin = isset($datos['vin']) ? strtoupper((string) $datos['vin']) : '';
    $vin = preg_replace('/[^A-Z0-9]/', '', $vin);
    if ($vin === '' || strlen($vin) > 20) {
        $this->json(400, array('error' => 'vin invalido'));
    }

    $rotulos = $this->rotulos_de_foto($entidad);
    $rotulo = isset($datos['rotulo']) ? (string) $datos['rotulo'] : '';
    if (!in_array($rotulo, $rotulos, TRUE)) {
        $this->json(400, array(
            'error'      => 'rotulo no permitido para ' . $entidad,
            'permitidos' => $rotulos,
        ));
    }
    $numero = isset($datos['numero']) ? (int) $datos['numero'] : 0;
    if ($numero < 0 || $numero > 99) { $numero = 0; }

    // El patron del legado, copiado incluido el `_` antes de la extension y
    // los dos puntos de la hora sin sanear. Ver el bloque Q2.
    if ($entidad === 'falla_mecanica') {
        $nombre = $vin . '_FALLA_MECANICA_NRO_' . $numero . '_'
                . date('Y-m-d H:i:s') . '_.' . $ext;
    } elseif ($entidad === 'inspeccion_despacho') {
        $nombre = $vin . '_INSPECCION_' . $rotulo . '_'
                . date('Y-m-d H:i:s') . '_.' . $ext;
    } elseif ($entidad === 'check_list_unidad') {
        $nombre = $vin . '_foto_unidad_' . date('Y-m-d') . '_.' . $ext;
    } else {
        $nombre = $vin . '_' . $rotulo . '_' . $numero . '_'
                . date('Y-m-d H:i:s') . '_.' . $ext;
    }
    // El mismo saneo del legado: SOLO espacios. Los dos puntos se quedan.
    $nombre = strtr($nombre, ' ', '_');

    // -- 5. ESCRIBIR -------------------------------------------------------
    $destino_dir = FCPATH . $carpeta;
    if (!is_dir($destino_dir) && !@mkdir($destino_dir, 0755, TRUE)) {
        $this->json(500, array('error' => 'no se pudo crear ' . $carpeta));
    }
    $destino = $destino_dir . $nombre;
    // Si ya existe --mismo VIN, mismo rotulo, mismo segundo-- se le agrega un
    // sufijo en vez de pisar. Pisar seria perder una foto que alguien saco.
    $i = 1;
    while (file_exists($destino) && $i < 50) {
        $destino = $destino_dir . preg_replace('/_\.' . $ext . '$/',
                   '_' . $i . '_.' . $ext, $nombre);
        $i++;
    }
    if (@file_put_contents($destino, $bin) === FALSE) {
        $this->json(500, array('error' => 'no se pudo escribir el archivo'));
    }
    @chmod($destino, 0644);

    $ruta = $carpeta . basename($destino);

    $this->db->insert('api_idempotency', array(
        'idem_key'   => $idem_key,
        'entidad'    => 'foto_' . $entidad,
        'entidad_id' => 0,
        'respuesta'  => $ruta,
        'creado_en'  => date('Y-m-d H:i:s'),
    ));

    // Devuelve la RUTA RELATIVA, que es lo que REGLA escribe en `archivoN` --
    // igual que el legado, que guarda `$carpeta.$filename`.
    $this->json(201, array('ok' => TRUE, 'ruta' => $ruta,
                           'bytes' => strlen($bin),
                           'ancho' => $info[0], 'alto' => $info[1]));
}

/* ---------------------------------------------------------------------------
   LA RUTA, en routes.php:

       $route['api_regla/subir_foto/(:any)']['POST'] =
           'api_regla/subir_foto/$1';

   OJO: `api_idempotency` necesita una columna `respuesta` (TEXT) si todavia no
   la tiene. Los otros endpoints guardan `entidad_id`; este guarda una ruta.
   Si la tabla no la tiene:

       ALTER TABLE api_idempotency ADD COLUMN respuesta TEXT NULL;
   --------------------------------------------------------------------------- */


/* ===========================================================================
   BLOQUE Q4 -- COMO COMPROBAR QUE QUEDO, sin Terminal
   ===========================================================================

   Las tres primeras NO escriben nada. La cuarta escribe UN archivo y recien
   se puede correr cuando el interruptor este en TRUE.

     1. sin clave  -> 401
        curl -s -o /dev/null -w '%{http_code}\n' -X POST \
          https://claude.logautos.cl/api_regla/subir_foto/inspeccion_despacho

     2. con clave, entidad inventada -> 404 'entidad sin carpeta: xxx'
        curl -s -X POST -H "X-API-Key: $CLAVE" \
          https://claude.logautos.cl/api_regla/subir_foto/xxx

     3. con clave, entidad buena -> 503 'subida_apagada'
        Esto confirma DOS cosas de una: que el metodo esta desplegado (si no,
        seria 404 de ruta) y que el interruptor esta en FALSE.
        curl -s -X POST -H "X-API-Key: $CLAVE" -H "Content-Type: application/json" \
          -d '{}' https://claude.logautos.cl/api_regla/subir_foto/inspeccion_despacho

     4. Y con el interruptor en TRUE, desde REGLA, con una foto real.

   LA COMPROBACION DE CONTENIDO, que es la que detecta el fallo mudo: el 503
   de la sonda 3 solo lo puede dar ESTE metodo. Un archivo sin el bloque daria
   404 de ruta; un archivo con el bloque a medias daria 500. Que diga
   exactamente `subida_apagada` prueba que el codigo que quedo es este.
   =========================================================================== */
