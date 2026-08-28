<?php if (!defined('BASEPATH')) exit('No direct script access allowed');

/**
 * Api_regla_pdi.php -- los cambios que necesita `Api_regla.php` para que entre
 * el push de PDI.
 *
 * NO VA EN ESTE REPOSITORIO NI SE INCLUYE. Son TRES ediciones sobre
 *
 *     application/controllers/Api_regla.php
 *
 * mas UNA ruta en routes.php. Van en el MISMO despliegue: la generalizacion
 * (bloque A) es lo que hace posible el bloque C, y el bloque B sin la
 * generalizacion queda a medias.
 *
 * `php -l Api_regla.php` antes de subir. No hay php en la maquina de
 * desarrollo, asi que este archivo nunca se linteo local.
 *
 *
 * POR QUE ESTO VA PRIMERO Y SOLO
 * ==============================
 *
 * `Api_regla.php` ignora EN SILENCIO lo que no esta en la lista blanca. Un
 * push de PDI con el lado Python listo y este lado sin desplegar devuelve 200,
 * no escribe nada, y la cola queda resuelta sin error. Es el tipo de falla que
 * se ve semanas despues, cuando alguien pregunta por que no hay fechas de PDI.
 *
 * Por eso las dos mitades entran juntas y esta es la que va primero.
 */


/* ===========================================================================
   BLOQUE A -- la generalizacion: entidad -> (tabla, columnas)
   ===========================================================================

   REEMPLAZA el metodo `columnas_permitidas($entidad)` completo (hoy en la
   linea 271) y toca DOS lineas de `actualizar()`.

   Hoy `actualizar()` tiene `newstocks_cidef` escrita a mano en dos lugares --
   el SELECT de la linea 411 y el UPDATE de la 498 -- asi que la lista blanca
   decide QUE columnas, pero la tabla es siempre la misma. Mientras la unica
   entidad era `unidades` eso no se notaba.

   El descuento de stock de combustible escribe en `stock_consumibles`, que es
   otra tabla. Sin esta generalizacion habria que copiar `actualizar()` entera
   -- con su idempotencia, su locking y su transaccion -- y a partir de ahi son
   dos copias que se separan.

   Se hace AHORA y no cuando llegue el stock porque va en el mismo despliegue:
   dos despliegues sobre el mismo metodo, con una semana de por medio, es la
   forma mas facil de dejar produccion a mitad de camino.
   =========================================================================== */

    /**
     * LAS TABLAS Y COLUMNAS QUE REGLA PUEDE ESCRIBIR, por entidad.
     *
     * Lista blanca, nunca `$data` a secas. `newstocks_cidef` tiene 144
     * columnas y esta es la unica puerta de escritura remota que va a tener:
     * un UPDATE armado con lo que llegue permite pisar cualquiera de las 144
     * -- incluido `id` -- a quien tenga la API key.
     *
     * Agregar una columna aca es un acto deliberado y va junto con la entidad
     * que la necesita.
     *
     * `tabla` es nueva: antes estaba escrita a mano dentro de `actualizar()`.
     * Ver el bloque A.
     */
    private function mapa_entidades()
    {
        return array(
            'unidades' => array(
                'tabla'    => 'newstocks_cidef',
                'columnas' => array(
                    // -- el IT, desplegado el 2026-08-26 --------------------
                    'estado_it',
                    'observacion_it',
                    'despachado',
                    'calle',
                    'updated_by',

                    // -- la PDI: ver el bloque B para de donde sale cada una --
                    'fecha_pdi',
                    'mes_pdi',
                    'mespdinombre',
                    'estadostock',
                    'ubicacion',
                    'tipo_combu',
                    'bateria',
                    'scanner',
                    'a_c',
                    'ob_mecanica',
                    // las cuatro automaticas
                    'aceite_coco',
                    'sistema_audio',
                    'adblue',
                    'aceite_diferencial',
                ),
            ),
        );
    }

    private function columnas_permitidas($entidad)
    {
        $mapa = $this->mapa_entidades();
        return isset($mapa[$entidad]) ? $mapa[$entidad]['columnas'] : null;
    }

    private function tabla_de($entidad)
    {
        $mapa = $this->mapa_entidades();
        return isset($mapa[$entidad]) ? $mapa[$entidad]['tabla'] : null;
    }

/* ---------------------------------------------------------------------------
   Y las dos lineas de `actualizar()` que dejan de tener la tabla escrita a
   mano. `$tabla` se resuelve arriba de todo, junto a `$columnas`:

       $columnas = $this->columnas_permitidas($entidad);
       if ($columnas === null) {
           $this->json(404, array('error' => 'entidad no soportada: ' . $entidad));
       }
   +   $tabla = $this->tabla_de($entidad);

   linea ~411, el SELECT:
       - $consulta = $this->db->get_where('newstocks_cidef', array('id' => $id));
       + $consulta = $this->db->get_where($tabla, array('id' => $id));
       - $this->json(500, array('error' => 'no se pudo leer newstocks_cidef'));
       + $this->json(500, array('error' => 'no se pudo leer ' . $tabla));

   linea ~498, el UPDATE:
       - $this->db->update('newstocks_cidef', $cambios);
       + $this->db->update($tabla, $cambios);

   El 404 de "unidad no encontrada" conviene dejarlo como esta: para `unidades`
   dice la verdad y es la unica entidad que usa este metodo hoy.
   --------------------------------------------------------------------------- */


/* ===========================================================================
   BLOQUE B -- las columnas de la PDI, y por que son CATORCE y no siete
   ===========================================================================

   Ya estan en el mapa de arriba; esto explica de donde sale cada una, porque
   la lista pedida eran siete y el bloque escribe catorce.

   Fuente: el array `$pdi` de `Pedido.php`, dentro de `elseif($calle=='Pdi')`.
   Escribe DIECIOCHO columnas. Cuatro no viajan y se dicen abajo.

     LAS SIETE QUE YA ESTABAN EN LA LISTA PEDIDA
       fecha_pdi           la fecha del formulario
       mes_pdi             la MISMA fecha -- el legado la guarda dos veces, con
                           dos nombres. No es un error nuestro: es asi alla, y
                           coincidir vale mas que tener razon
       mespdinombre        "Agosto 2026" -- el mes en palabras y el año, que el
                           PHP arma con un switch de doce casos
       aceite_coco         \
       sistema_audio        |  LAS CUATRO AUTOMATICAS: las cuatro se llenan con
       adblue               |  date('Y-m-d'), la fecha del dia, sin preguntarle
       aceite_diferencial  /   nada a nadie. No son booleanos: son fechas.

     LAS SIETE QUE FALTABAN, Y NO SON OPCIONALES
       tipo_combu          Bencina / Diesel / Electrico. Es lo que decide el
                           precio de la OT de combustible Y si se descuenta
                           stock. Sin ella la PDI viaja sin el dato del que
                           depende toda la plata.
       bateria             \
       scanner              |  los cuatro campos que el jefe de taller LLENA en
       a_c                  |  el formulario. Son la PDI: sin ellos se empuja
       ob_mecanica         /   la fecha de una inspeccion sin su resultado.
       estadostock         'STOCK CON PDI'. Fijo, lo pone el PHP. Verificado:
                           las 21 unidades con calle 'Pdi' en la replica lo
                           tienen, sin excepcion.
       ubicacion           VACIA, y hay que mandarla vacia. Ojo con esta: la
                           rama tiene un `$f = '1'` que NO se usa -- codigo
                           muerto -- y el array escribe `$numero`, que
                           `actualizar_pdi_process` postea como ''. O sea que
                           la PDI BORRA la ubicacion que la unidad tuviera.
                           Verificado igual que la anterior: las 21 en vacio.
                           Es destructivo y es lo que hace el legado, asi que
                           lo replicamos -- coincidir vale mas que tener razon,
                           y una ubicacion que alla se borra y aca no es
                           justamente el ruido que la reconciliacion tendria
                           que explicar despues.

     LAS CUATRO QUE NO VIAJAN, A PROPOSITO
       calle / despachado  ya estan en la lista por el IT, y ademas para la PDI
                           las escribe el endpoint de MOVIMIENTOS, no este.
       updated_at          lo pone ESTE servidor con su reloj. Nota 3 de
                           sync_legado: dos relojes que conciliar es justo lo
                           que no queremos.
       patio               el bloque PDI no lo escribe -- arranca con
                           `$patiopdi = ' '` y nunca lo usa. Las 3.241 filas de
                           PDI del semestre tienen el patio VACIO, las 3.241.
                           Python manda cadena vacia a proposito para coincidir.

   Si preferis desplegar solo las siete de la lista original, decilo y lo
   ajusto del lado Python -- pero entonces la PDI que llega al legado no tiene
   ni tipo de combustible ni resultado, y el propio legado no la puede facturar.
   =========================================================================== */


/* ===========================================================================
   BLOQUE C -- el descuento de stock de combustible
   ===========================================================================

   Metodo NUEVO, va DENTRO de la clase `Api_regla`, y una ruta:

       $route['api_regla/stock_consumibles/(:num)/descontar']['POST']
           = 'api_regla/descontar_stock/$1';

   POR QUE NO ES UN PUT A `actualizar()`
   -------------------------------------
   Porque no es una asignacion, es una RESTA, y las dos se rompen distinto.

   El legado hace `$resta = $stock - $cantidad` en PHP y despues guarda el
   resultado. Entre la lectura y la escritura no hay nada que impida que otro
   proceso haya movido el stock: dos PDI simultaneas leen 100, las dos calculan
   80, y las dos guardan 80 -- se descontaron 40 litros y el stock bajo 20.
   Replicar eso mandando el valor absoluto desde Python seria peor todavia,
   porque el valor que Python leyo puede tener hasta una vuelta de sync de
   atraso (300 s).

       UPDATE stock_consumibles SET stock = stock - ? WHERE id = ?

   lo resuelve en la base, sin leer primero. Es la unica forma correcta y no
   la puede expresar `actualizar()`, que arma un UPDATE de valores absolutos.

   LA IDEMPOTENCY-KEY ACA NO ES OPCIONAL
   -------------------------------------
   En `actualizar()` la key evita un conflicto falso; aca evita DESCONTAR DOS
   VECES. Una resta reintentada resta de nuevo -- no hay locking optimista que
   la ataje, porque `stock_consumibles` no tiene `updated_at` con que comparar.
   Por eso este metodo RECHAZA con 400 si la key no viene, en vez de tratarla
   como opcional. Es la unica diferencia de contrato con los otros dos
   endpoints, y es deliberada.

   LO QUE NO HACE, Y HAY QUE SABERLO
   ---------------------------------
   El legado, ademas de restar el stock, corre un costeo por boleta (FIFO sobre
   `stock_combustible_ingreso`) que reparte el costo entre las boletas de
   compra. Eso NO se replica: son ~120 lineas de PHP que tocan otras dos
   tablas, y el descuento del stock es independiente de como se costee. Queda
   anotado como divergencia conocida, no como olvido.

   DOS COSAS QUE NO PUDE VERIFICAR y te toca confirmar, porque
   `stock_consumibles` no vino en el dump y no esta en la replica:

     1. Que la tabla tenga `id`, `nombre` y `stock` -- es lo que se deduce de
        `getIdCombustible`, `getStockCombustible_numerico` y
        `actudat_stock_combustible` en Pedido_model.php. Si tiene mas columnas
        no importa; si `stock` se llama distinto, avisame.
     2. Que sea InnoDB. Con MyISAM el ROLLBACK es un no-op silencioso, igual
        que en `crear_movimiento`.
   =========================================================================== */

// ---------------------------------------------------------------------------
// POST /api_regla/stock_consumibles/{id}/descontar
// ---------------------------------------------------------------------------
//
// Cuerpo (JSON):
//   cantidad   number  obligatorio, > 0. Los litros a restar.
//   motivo     string  opcional, para el log
//
// Cabecera: Idempotency-Key (UUID). OBLIGATORIA -- ver arriba.
//
// Respuestas:
//   200 {"ok":true,"stock":<int>,"descontado":<num>}
//   200 {"ok":true,"stock":<int>,"idempotente":true}
//   400 falta la key, cantidad invalida, o body no-JSON
//   404 el consumible no existe
//   409 {"ok":false,"stock_insuficiente":true,"stock":<int>}
//   401 / 500
public function descontar_stock($id = null)
{
    if (strtolower($this->input->method()) !== 'post') {
        $this->json(405, array('error' => 'metodo no permitido'));
    }

    $this->exigir_api_key();

    $id = (int) $id;
    if ($id <= 0) {
        $this->json(400, array('error' => 'id invalido'));
    }

    $crudo = $this->input->raw_input_stream;
    if ($crudo === NULL || $crudo === '') {
        $crudo = file_get_contents('php://input');
    }
    $datos = json_decode($crudo, TRUE);
    if (!is_array($datos)) {
        $this->json(400, array('error' => 'body invalido: se esperaba JSON'));
    }

    $cantidad = isset($datos['cantidad']) ? $datos['cantidad'] + 0 : 0;
    if ($cantidad <= 0) {
        $this->json(400, array(
            'error'  => 'cantidad tiene que ser mayor que cero',
            'codigo' => 'validacion',
        ));
    }

    // -- La key es OBLIGATORIA. Ver el encabezado del bloque C ---------------
    $idem_key = isset($_SERVER['HTTP_IDEMPOTENCY_KEY'])
        ? trim($_SERVER['HTTP_IDEMPOTENCY_KEY']) : '';
    if ($idem_key === '') {
        $this->json(400, array(
            'error'  => 'Idempotency-Key es obligatoria: un descuento sin key '
                      . 'se aplica dos veces si se reintenta',
            'codigo' => 'falta_idempotency_key',
        ));
    }

    $consulta = $this->db->get_where('api_idempotency', array(
        'idem_key' => $idem_key,
        'entidad'  => 'stock_consumibles',
    ));
    if ($consulta === FALSE) {
        $this->json(500, array(
            'error'  => 'falta la tabla api_idempotency en la base',
            'codigo' => 'falta_tabla',
        ));
    }
    if ($consulta->row()) {
        // Ya se aplico. Se devuelve el stock ACTUAL, no el de entonces: el
        // valor de entonces ya no es cierto y quien pregunta quiere saber
        // cuanto hay, no cuanto habia.
        $fila = $this->db->get_where('stock_consumibles',
                                     array('id' => $id))->row_array();
        $this->json(200, array(
            'ok'          => TRUE,
            'stock'       => $fila ? (int) $fila['stock'] : null,
            'idempotente' => TRUE,
        ));
    }

    $this->db->trans_begin();

    // -- El descuento, en la base y sin leer primero -------------------------
    //
    // La condicion `stock >= ?` va en el WHERE y no en un `if` de PHP: asi la
    // comprobacion y la resta son la MISMA operacion. Con el `if` afuera
    // vuelve la carrera que este endpoint viene a cerrar.
    $this->db->query(
        'UPDATE stock_consumibles SET stock = stock - ? '
        . ' WHERE id = ? AND stock >= ?',
        array($cantidad, $id, $cantidad));
    $afectadas = $this->db->affected_rows();

    if ($afectadas < 1) {
        $this->db->trans_rollback();
        $fila = $this->db->get_where('stock_consumibles',
                                     array('id' => $id))->row_array();
        if (!$fila) {
            $this->json(404, array('error' => 'consumible no encontrado: ' . $id));
        }
        // Existe pero no alcanzaba. No es un error de programa: es la misma
        // compuerta que el legado evalua antes de guardar la PDI, y del lado
        // de Python se traduce en "la PDI se guardo pero el combustible no se
        // descontó", que es exactamente lo que pasa hoy alla.
        $this->json(409, array(
            'ok'                 => FALSE,
            'stock_insuficiente' => TRUE,
            'stock'              => (int) $fila['stock'],
        ));
    }

    $this->db->insert('api_idempotency', array(
        'idem_key'   => $idem_key,
        'entidad'    => 'stock_consumibles',
        'entidad_id' => $id,
        'updated_at' => $this->db->query('SELECT NOW() AS ahora')->row()->ahora,
    ));

    if ($this->db->trans_status() === FALSE) {
        $this->db->trans_rollback();
        // La key duplicada es una carrera legitima entre dos reintentos: gano
        // el otro, el descuento esta aplicado UNA vez, y lo correcto es
        // devolver exito.
        $consulta = $this->db->get_where('api_idempotency', array(
            'idem_key' => $idem_key,
            'entidad'  => 'stock_consumibles',
        ));
        if ($consulta !== FALSE && $consulta->row()) {
            $fila = $this->db->get_where('stock_consumibles',
                                         array('id' => $id))->row_array();
            $this->json(200, array(
                'ok'          => TRUE,
                'stock'       => $fila ? (int) $fila['stock'] : null,
                'idempotente' => TRUE,
            ));
        }
        $this->json(500, array('error' => 'no se pudo descontar el stock',
                               'codigo' => 'db'));
    }

    $this->db->trans_commit();

    $fila = $this->db->get_where('stock_consumibles',
                                 array('id' => $id))->row_array();
    $this->json(200, array(
        'ok'         => TRUE,
        'stock'      => $fila ? (int) $fila['stock'] : null,
        'descontado' => $cantidad,
    ));
}


/* ===========================================================================
   LO QUE **NO** HACE FALTA DEL LADO PHP, Y POR QUE
   ===========================================================================

   Lo reviso explicitamente porque la pregunta era si con esto alcanza.

   LAS DOS OT: SI, Y ES EL BLOQUE D. Decidido el 2026-08-27: la facturacion
   sigue saliendo del sistema viejo, asi que una PDI empujada sin OT es una PDI
   sin cobrar. Ver abajo.

   LOS CORREOS: NO HAY NADA QUE REPLICAR. Confirmado leyendo la rama entera --
   el bloque `elseif($calle=='Pdi')` no carga `phpmailer_lib` ni una sola vez.
   Los correos de revision salen de OTRAS pantallas. Es la diferencia con
   `DESPACHADO` y con `DYP`, que si mandan correo y por eso siguen sin
   empujarse.

   `registros`: NO. La fila del movimiento de PDI la escribe el endpoint que ya
   esta desplegado (`POST /api_regla/movimientos`), con `accion='PDI'`,
   `estado='EN ESPERA DYP CONSOLIDADO'` y `patio` VACIO. Nada que tocar.

   EL PULL DE `stock_consumibles`: SI, Y FALTA. Python necesita LEER el stock
   para evaluar la compuerta (`stock > 20 || ELECTRICO`) antes de guardar la
   PDI. Hoy no hay `GET /api_regla/cambios/stock_consumibles` y la tabla no
   esta en la replica.

   Se puede hacer de dos formas y prefiero que elijas vos:

     (a) otra entidad del pull, calcada de `cambios_unidades`. Necesita una
         columna de marca de agua en `stock_consumibles` -- y NO SE si la
         tiene. Si no la tiene, no hay pull incremental posible sin agregarla.
     (b) un `GET /api_regla/stock_consumibles` que devuelva las pocas filas
         enteras, cada vez. Son ~4 consumibles: no hay nada que paginar ni
         marca de agua que llevar, y es diez lineas.

   Yo iria por (b). El pull incremental existe porque `newstocks_cidef` tiene
   71.546 filas; para cuatro es maquinaria sin motivo, y ademas evita
   agregarle una columna a una tabla del legado, que es lo unico de todo este
   despliegue que cambiaria su esquema.
   =========================================================================== */


/* ===========================================================================
   BLOQUE D -- las dos OT de la PDI
   ===========================================================================

   Metodo NUEVO dentro de `Api_regla`, mas una ruta:

       $route['api_regla/pdi/(:num)/ot']['POST'] = 'api_regla/crear_ot_pdi/$1';

   ES ESTRECHO A PROPOSITO, Y ESO ES LA DECISION
   ---------------------------------------------
   No es una API de `orden_trabajo`. Crea EXACTAMENTE las dos OT de una PDI y
   nada mas: no acepta `requerimiento` libre, no acepta columnas arbitrarias, y
   si le mandan un combustible que el legado no reconoce, rechaza.

   `orden_trabajo` tiene 66 columnas y es la tabla de la que sale la
   facturacion. Una lista blanca generica sobre ella seria la puerta mas ancha
   del sistema: con la API key se podria crear cualquier OT, por cualquier
   monto, a nombre de cualquier cliente. Cerrarla a dos requerimientos con los
   precios calculados de este lado -- no mandados -- la deja del ancho del caso
   que existe.

   LOS PRECIOS SE RECALCULAN ACA Y NO SE CONFIAN
   ---------------------------------------------
   Python manda `fecha_pdi` y `tipo_combu`; marca y modelo salen de la unidad y
   el precio lo calcula este metodo. Es mas trabajo y es lo correcto: un precio
   que viaja es un precio que se puede modificar en transito, y las OT son la
   plata. Que las dos implementaciones tengan que coincidir es ademas lo que
   hace que `probar_ot_pdi.py` -- validado contra 970 OT reales -- signifique
   algo para este lado tambien.

   Las reglas, para que este PHP y `modulos/ot_pdi.py` no se separen:

     OT PDI            precio 49000 (desde el 2026-06-03; antes 46878),
                       costo 0, utilidad = precio, margen '100.00',
                       con_iva = round(precio * 1.19)
     OT COMBUSTIBLE    litros = 15 si marca es DFM o ZNA;
                         si no, y es Bencina: 15 si el modelo empieza con
                           G7, G9, V7 o V9
                         si no, y es Diesel: 15 SOLO si empieza con G7
                         si no: 20
                       valor = 1970 (Bencina) o 2070 (Diesel)
                       precio = costo = valor * litros
                       utilidad 0, margen '0.00'
                       con_iva = round(precio * 1.19)
                       Electrico NO genera esta OT.

   OJO CON LOS DOS PREFIJOS DISTINTOS. No es un typo de este archivo: en
   `Pedido.php` la rama Bencina compara cuatro prefijos y la Diesel solo 'G7',
   porque adentro de la rama Diesel solo se asigna la variable `$g7`. Un FOTON
   V9 a diesel carga 20 litros. Replicarlo con los cuatro en las dos ramas hace
   que 204 de cada 481 OT no coincidan -- medido, no supuesto.

   ENTRADA DE COLA APARTE, DEPENDIENTE DEL MOVIMIENTO
   --------------------------------------------------
   Eso es del lado de Python y se anota aca para que el contrato se lea entero:
   la entrada se encola DESPUES de que el movimiento confirme, no en la misma
   transaccion. Si el movimiento entro, las OT tienen que quedar; si no entro,
   no hay PDI que cobrar. Una sola entrada crea las DOS OT, en una transaccion:
   media PDI cobrada es peor que ninguna.

   Idempotency-Key OBLIGATORIA, como en el descuento de stock y por lo mismo:
   `orden_trabajo` es append-only y un reintento sin key cobra dos veces.
   =========================================================================== */

// ---------------------------------------------------------------------------
// POST /api_regla/pdi/{unidad_id}/ot
// ---------------------------------------------------------------------------
//
// Cuerpo (JSON):
//   fecha_pdi    string  obligatorio 'Y-m-d'. Decide el precio de la OT de PDI.
//   tipo_combu   string  obligatorio: 'Bencina' | 'Diesel' | 'Electrico'
//   created_by   int     opcional, el usuario de tbl_users
//
// Cabecera: Idempotency-Key (UUID). OBLIGATORIA.
//
// Respuestas:
//   201 {"ok":true,"ot":{"pdi":<id>,"combustible":<id>|null}}
//   200 {"ok":true,"ot":{...},"idempotente":true}
//   400 / 401 / 404 / 500
public function crear_ot_pdi($unidad_id = null)
{
    if (strtolower($this->input->method()) !== 'post') {
        $this->json(405, array('error' => 'metodo no permitido'));
    }
    $this->exigir_api_key();

    $unidad_id = (int) $unidad_id;
    if ($unidad_id <= 0) {
        $this->json(400, array('error' => 'unidad_id invalido'));
    }

    $crudo = $this->input->raw_input_stream;
    if ($crudo === NULL || $crudo === '') {
        $crudo = file_get_contents('php://input');
    }
    $datos = json_decode($crudo, TRUE);
    if (!is_array($datos)) {
        $this->json(400, array('error' => 'body invalido: se esperaba JSON'));
    }

    $fecha_pdi  = isset($datos['fecha_pdi'])  ? trim($datos['fecha_pdi'])  : '';
    $tipo_combu = isset($datos['tipo_combu']) ? trim($datos['tipo_combu']) : '';
    $created_by = isset($datos['created_by']) ? (int) $datos['created_by'] : 0;

    if ($fecha_pdi === '') {
        $this->json(400, array('error' => 'fecha_pdi es obligatoria'));
    }

    // Los TRES valores exactos, como el `if` de Pedido.php. Cualquier otra
    // cosa -- 'GASOLINA' es el caso real, 2.416 unidades en la replica -- se
    // RECHAZA, en vez de caer en un else que no existe y dejar la PDI sin OT.
    // Ese silencio es justamente lo que este endpoint viene a evitar.
    if (!in_array($tipo_combu, array('Bencina', 'Diesel', 'Electrico'), TRUE)) {
        $this->json(400, array(
            'error'  => 'tipo_combu tiene que ser Bencina, Diesel o Electrico. '
                      . 'Recibido: ' . $tipo_combu,
            'codigo' => 'combustible_desconocido',
        ));
    }

    $idem_key = isset($_SERVER['HTTP_IDEMPOTENCY_KEY'])
        ? trim($_SERVER['HTTP_IDEMPOTENCY_KEY']) : '';
    if ($idem_key === '') {
        $this->json(400, array(
            'error'  => 'Idempotency-Key es obligatoria: orden_trabajo es '
                      . 'append-only y un reintento cobraria dos veces',
            'codigo' => 'falta_idempotency_key',
        ));
    }

    $consulta = $this->db->get_where('api_idempotency', array(
        'idem_key' => $idem_key, 'entidad' => 'ot_pdi'));
    if ($consulta === FALSE) {
        $this->json(500, array('error' => 'falta la tabla api_idempotency',
                               'codigo' => 'falta_tabla'));
    }
    $previo = $consulta->row();
    if ($previo) {
        $this->json(200, array(
            'ok' => TRUE, 'idempotente' => TRUE,
            'ot' => array('pdi' => (int) $previo->entidad_id),
        ));
    }

    $consulta = $this->db->get_where('newstocks_cidef', array('id' => $unidad_id));
    $unidad = ($consulta === FALSE) ? FALSE : $consulta->row_array();
    if (!$unidad) {
        $this->json(404, array('error' => 'unidad no encontrada: ' . $unidad_id));
    }

    // -- Los precios, calculados ACA -----------------------------------------
    //
    // La fecha de vigencia es el 03 y no el 02: el 2026-06-02 conviven 27 OT a
    // 46.878 y 3 a 49.000 porque el despliegue fue a mitad de ese dia.
    $precio_pdi = ($fecha_pdi >= '2026-06-03') ? 49000 : 46878;

    $marca  = strtoupper(trim($unidad['marca']));
    $modelo = strtoupper(trim($unidad['modelo']));
    $litros = 0;
    $valor  = 0;
    if ($tipo_combu === 'Bencina' || $tipo_combu === 'Diesel') {
        $valor = ($tipo_combu === 'Bencina') ? 1970 : 2070;
        // Los prefijos NO son los mismos para los dos. Ver el encabezado.
        $prefijos = ($tipo_combu === 'Bencina')
            ? array('G7', 'G9', 'V7', 'V9')
            : array('G7');
        if ($marca === 'DFM' || $marca === 'ZNA') {
            $litros = 15;
        } elseif (in_array(substr($modelo, 0, 2), $prefijos, TRUE)) {
            $litros = 15;
        } else {
            $litros = 20;
        }
    }

    $ahora = $this->db->query('SELECT NOW() AS ahora')->row()->ahora;

    $base = array(
        'nombre'      => $unidad['clientecompleto'],
        'cliente'     => $unidad['clientecompleto'],
        'vehiculo'    => $unidad['vin'],
        'patente'     => $unidad['patente'],
        'marca'       => $unidad['marca'],
        'modelo'      => $unidad['modelo'],
        'color'       => $unidad['color'],
        'id_vehiculo' => $unidad_id,
        'createdBy'   => $created_by,
        'updated_by'  => $created_by,
        'createdDtm'  => $ahora,
        'tipo'        => 'INTERNO',
    );

    $ot_pdi = array_merge($base, array(
        'requerimiento'   => 'PDI',
        'precio'          => $precio_pdi,
        'costo'           => 0,
        'utilidad'        => $precio_pdi,
        // Fijo y no calculado: con costo 0 la division seria por cero. El PHP
        // original tambien lo escribe a mano.
        'margen_utilidad' => '100.00',
        'con_iva'         => round($precio_pdi * 1.19),
    ));

    // -- LAS DOS EN UNA TRANSACCION ------------------------------------------
    // Media PDI cobrada es peor que ninguna: si la de combustible falla, la de
    // PDI tampoco queda, y el reintento las crea a las dos.
    $this->db->trans_begin();

    $this->db->insert('orden_trabajo', $ot_pdi);
    $id_pdi = $this->db->insert_id();
    $id_combu = null;

    if ($litros > 0) {
        $precio_combu = round($valor * $litros);
        $this->db->insert('orden_trabajo', array_merge($base, array(
            'requerimiento'   => 'COMBUSTIBLE POR NORMA',
            // precio == costo: el combustible se traspasa a costo. No es un
            // error de transcripcion, es lo que hace el legado.
            'precio'          => $precio_combu,
            'costo'           => $precio_combu,
            'utilidad'        => 0,
            'margen_utilidad' => '0.00',
            'con_iva'         => round($precio_combu * 1.19),
            'detalle_externo' => 'PDI/COMBUSTIBLE POR NORMA ' . $litros . 'LTS',
            'nombre_externo'  => 'TALLER LOGAUTOS',
        )));
        $id_combu = $this->db->insert_id();
    }

    $this->db->insert('api_idempotency', array(
        'idem_key'   => $idem_key,
        'entidad'    => 'ot_pdi',
        'entidad_id' => $id_pdi,
        'updated_at' => $ahora,
    ));

    if ($this->db->trans_status() === FALSE) {
        $this->db->trans_rollback();
        // La key duplicada es una carrera legitima entre dos reintentos.
        $consulta = $this->db->get_where('api_idempotency', array(
            'idem_key' => $idem_key, 'entidad' => 'ot_pdi'));
        $previo = ($consulta === FALSE) ? FALSE : $consulta->row();
        if ($previo) {
            $this->json(200, array(
                'ok' => TRUE, 'idempotente' => TRUE,
                'ot' => array('pdi' => (int) $previo->entidad_id),
            ));
        }
        $this->json(500, array('error' => 'no se pudieron crear las OT',
                               'codigo' => 'db'));
    }

    $this->db->trans_commit();

    $this->json(201, array('ok' => TRUE, 'ot' => array(
        'pdi'         => (int) $id_pdi,
        'combustible' => $id_combu === null ? null : (int) $id_combu,
    )));
}


/* ===========================================================================
   VERIFICACION DESPUES DEL PRIMER PUSH REAL DE PDI
   ===========================================================================

   `ubicacion` es la unica columna del despliegue que BORRA en vez de agregar,
   asi que es donde un error destruye dato. Antes de habilitar el push masivo,
   sobre UNA unidad con `clientecompleto = 'PRUEBA'`:

     1. ANTES del push, guardar el valor: la unidad de prueba tiene que tener
        `ubicacion` NO vacia, para que el borrado se pueda ver. Si esta vacia
        de entrada, la prueba no distingue "lo borro" de "no lo toco".

     2. DESPUES del push, leer sin escribir -- un PUT con
        `legado_updated_at_conocido` del año 2000 devuelve 409 con
        `datos_actuales`, que incluye `ubicacion` -- y confirmar que quedo
        vacia.

     3. Confirmar que NADA MAS dependia de ese valor. Buscado en el legado:
        `ubicacion` se lee en las grillas de grocery_CRUD y en el buscador de
        patio. No alimenta ningun calculo ni ninguna OT. Vale la pena
        reconfirmarlo con un grep en el momento del despliegue, porque es la
        clase de dependencia que aparece en una vista que nadie mira.

     4. Y comparar contra el legado: las 21 unidades con calle 'Pdi' de la
        replica tienen la ubicacion vacia. Si despues del push la nuestra
        quedo vacia igual, coincidimos. Si quedo con valor, NO coincidimos --
        y ahi el que esta mal es el push, no el legado.
   =========================================================================== */


/* ===========================================================================
   BLOQUE E -- el 201 devuelve los precios (aprobado el 2026-08-27)
   ===========================================================================

   UNA sola edicion, sobre el `crear_ot_pdi` del bloque D. No se borra nada: se
   reemplaza el `json(201, ...)` FINAL del metodo por el de abajo.

   QUE BORRAR, EXACTAMENTE. Las ultimas cinco lineas del metodo, desde

       $this->json(201, array('ok' => TRUE, 'ot' => array(

   hasta

       )));

   inclusive -- la que esta justo antes del `}` que cierra `crear_ot_pdi`.


   POR QUE
   -------
   Sin esto, verificar que las dos implementaciones del precio coinciden exige
   CREAR OT reales y despues no se puede leer que precio escribio el PHP:
   `orden_trabajo` no esta en el pull. O sea que la verificacion crearia
   facturacion y no verificaria nada.

   Devolviendo lo calculado, la prueba se cierra sola: se empuja sobre una
   unidad `clientecompleto = 'PRUEBA'`, se compara contra `ot_pdi.py` y contra
   las 970 OT historicas, y queda como suite permanente. Es la unica forma de
   que dos implementaciones del mismo calculo de plata no se separen en
   silencio -- y que lo diga una prueba, no una factura.

   El eco NO es un dato nuevo ni una consulta extra: son las variables que el
   metodo ya tiene en la mano una linea antes.
   =========================================================================== */

    $this->json(201, array('ok' => TRUE, 'ot' => array(
        'pdi' => array(
            'id'      => (int) $id_pdi,
            'precio'  => (int) $ot_pdi['precio'],
            'con_iva' => (int) $ot_pdi['con_iva'],
        ),
        // null cuando la unidad es electrica: no genera esta OT.
        'combustible' => $id_combu === null ? null : array(
            'id'      => (int) $id_combu,
            'precio'  => (int) $precio_combu,
            'con_iva' => (int) round($precio_combu * 1.19),
            'litros'  => (int) $litros,
        ),
    )));


/* ===========================================================================
   BLOQUE F -- el GET de stock_consumibles (opcion (b), aprobada)
   ===========================================================================

   Metodo NUEVO dentro de `Api_regla`. NO reemplaza nada -- no hay que borrar
   ninguna linea. Se agrega antes del `}` que cierra la clase, junto a los
   otros metodos publicos.

   Y una ruta en routes.php:

       $route['api_regla/stock_consumibles']['GET'] = 'api_regla/listar_stock';

   OJO CON EL ORDEN DE LAS RUTAS. La del descuento ya existe y es
   `api_regla/stock_consumibles/(:num)/descontar`. Esta es mas corta y no se
   pisan, pero CI3 evalua en orden: conviene poner la del descuento ARRIBA de
   esta, para que un `(:any)` futuro no se la coma.


   POR QUE NO ES UNA ENTIDAD DEL PULL INCREMENTAL
   ---------------------------------------------
   Son DOS filas. El pull incremental existe porque `newstocks_cidef` tiene
   71.546: paginado, marca de agua, orden por (updated_at, id). Para dos filas
   eso es maquinaria sin motivo.

   Y hay una razon mas fuerte: `stock_consumibles` NO TIENE `updated_at` ni
   ninguna columna de tiempo -- confirmado sobre la tabla real, y `Update_time`
   viene NULL. O sea que un pull incremental habria obligado a AGREGARLE UNA
   COLUMNA a una tabla del legado, que es la unica cosa de todo este despliegue
   que le cambiaria el esquema. Devolver las dos filas enteras evita eso.

   La consecuencia, anotada: el stock que REGLA lee tiene hasta una vuelta de
   sync de atraso (300 s). Es deliberado -- la compuerta se evalua contra la
   REPLICA y no sincronicamente, porque si el legado esta lento la pantalla del
   patio no se puede colgar. Toda la arquitectura es fire-and-forget por eso.
   Con once PDI por dia, la carrera es un limite aceptable.
   =========================================================================== */

// ---------------------------------------------------------------------------
// GET /api_regla/stock_consumibles
// ---------------------------------------------------------------------------
//
// Sin parametros. Devuelve las filas enteras, siempre todas.
//
//   200 {"filas":[{"id":2,"nombre":"DIESEL","stock":5,"precio":1500,
//                  "promedio":1091}, ...], "hasta":"<NOW()>"}
//   401
//
// `hasta` viaja aunque no haya marca de agua que llevar: lo pone ESTE servidor
// con SU reloj, igual que `cambios_unidades`, y Python lo guarda sin parsearlo.
// Sirve para saber CUANDO se leyo el stock, que es justo lo que hace falta para
// entender una compuerta que decidio con datos viejos.
public function listar_stock()
{
    if (strtolower($this->input->method()) !== 'get') {
        $this->json(405, array('error' => 'metodo no permitido'));
    }

    $this->exigir_api_key();

    // SELECT explicito y no `*`: si alguien le agrega una columna a la tabla,
    // que no empiece a viajar sola. Son las cinco que tiene hoy.
    $this->db->select('id, nombre, stock, precio, promedio');
    $this->db->from('stock_consumibles');
    $this->db->order_by('id', 'ASC');
    $consulta = $this->db->get();
    if ($consulta === FALSE) {
        $this->json(500, array('error' => 'no se pudo leer stock_consumibles'));
    }

    $this->json(200, array(
        'filas' => $consulta->result_array(),
        'hasta' => $this->db->query('SELECT NOW() AS ahora')->row()->ahora,
    ));
}
