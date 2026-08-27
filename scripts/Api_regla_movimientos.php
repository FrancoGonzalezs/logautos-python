<?php if (!defined('BASEPATH')) exit('No direct script access allowed');

/**
 * Api_regla_movimientos.php -- el metodo `crear_movimiento` de Api_regla.
 *
 * NO VA EN ESTE REPOSITORIO. Se copia DENTRO de la clase que ya existe:
 *
 *     application/controllers/Api_regla.php
 *
 * Se deja aca, aparte de `Api_regla.php`, para que se pueda revisar y aplicar
 * solo: el archivo grande ya esta desplegado y no conviene reemplazarlo entero
 * por un metodo nuevo.
 *
 * La ruta, en routes.php, junto a las dos que ya estan:
 *
 *     $route['api_regla/movimientos']['POST'] = 'api_regla/crear_movimiento';
 *
 * Reusa de `Api_regla.php`: exigir_api_key(), json(), claves_iguales() y la
 * tabla `api_idempotency`. No hace falta crear nada nuevo del lado MySQL.
 *
 *
 * POR QUE ESTE ENDPOINT EXISTE, Y QUE ARREGLA
 * ===========================================
 *
 * Un movimiento en el legado son DOS escrituras:
 *
 *     registromov($mov)   INSERT en `registros`   (Pedido_model.php:326)
 *     actudat($id, $aux)  UPDATE de la unidad     (Pedido_model.php:456)
 *
 * y NO comparten transaccion. Peor: `registromov` al menos envuelve su INSERT
 * en trans_start()/trans_complete(), y `actudat` no tiene NINGUNA -- es un
 * UPDATE pelado en autocommit.
 *
 * El orden entre las dos ademas cambia segun quien llame. Contado sobre los
 * dos controladores vivos: en Pedido.php hay 41 llamados con la unidad primero
 * y 1 con el movimiento primero; en Nota.php es al reves, 6 contra 1. Y hay 58
 * lugares que actualizan el estado SIN escribir la fila de `registros`, o sea
 * unidades que se mueven sin dejar rastro en el historial.
 *
 * Consecuencia: si el proceso muere entre las dos, queda un movimiento que
 * dice que la unidad se movio y una unidad que no se movio -- o al reves, una
 * unidad movida sin registro de quien ni por que.
 *
 * ESTE ENDPOINT LO CORRIGE A PROPOSITO: las dos escrituras van en UNA
 * transaccion. O entran las dos o no entra ninguna. Es una divergencia
 * deliberada del original y no un descuido -- se documenta acá para que nadie
 * la "arregle" mas adelante creyendo que se aparto del legado por error.
 *
 *
 * LAS COLUMNAS DE `registros` ESTAN INVERTIDAS
 * ============================================
 *
 * En `registros`:
 *
 *     accion / estado / patio          -> el DESTINO del movimiento
 *     newcalle / newestado / newpatio  -> el ORIGEN
 *
 * El prefijo `new` miente. Verificado por tres lados: en Nota.php:15236 los
 * tres `new*` se leen con getcallebyid()/getestadobyid()/getpatiobyid() ANTES
 * de que actudat() escriba el estado nuevo, o sea guardan lo que la unidad
 * TENIA; en Nota.php:20893 se escribe la cadena literal 'FUNCION FR' en las
 * tres, que solo tiene sentido como casillero de procedencia; y en los datos
 * la transicion mas frecuente es estado='DESPACHADO' con newestado='ZONA DE
 * DESPACHO', 27.389 filas, que al reves no significaria nada.
 *
 * Escribirlo al reves no rompe nada visible: corrompe en silencio el historial
 * del que salen los reportes del legado.
 *
 *
 * EL ORIGEN NO LO MANDA REGLA: SE LEE ACA
 * =======================================
 *
 * El cuerpo trae solo el destino. El origen (newcalle/newestado/newpatio) lo
 * lee este metodo de la propia fila, DENTRO de la transaccion y justo antes
 * del UPDATE.
 *
 * Es lo que hace el legado, y es lo correcto: el origen tiene que ser lo que
 * la unidad tenia en el sistema anterior en el instante del cambio. Si lo
 * mandara REGLA seria lo que REGLA cree que tenia, que puede tener hasta una
 * vuelta de sync de atraso -- y quedaria escrito en el historial como si fuera
 * un hecho.
 */

// ---------------------------------------------------------------------------
// POST /api_regla/movimientos  -- registrar un movimiento y mover la unidad
// ---------------------------------------------------------------------------
//
// Cuerpo (JSON):
//   unidad_id                    int    obligatorio, la PASADA (no el VIN)
//   accion                       string obligatorio, la calle DESTINO
//   estado                       string obligatorio, el estado DESTINO
//   patio                        string opcional,    el patio DESTINO
//   ubicacion                    string opcional
//   clientemov                   string opcional
//   obs                          string opcional
//   created_by                   int    opcional, el usuario de tbl_users
//   legado_updated_at_conocido   string obligatorio para el locking
//
// Cabecera: Idempotency-Key (UUID). Python la manda siempre.
//
// Respuestas:
//   201 {"ok":true,"id":<id de registros>,"updated_at":"..."}
//   200 {"ok":true,"id":...,"updated_at":"...","idempotente":true}
//   409 {"ok":false,"conflicto":true,"updated_at":"...","datos_actuales":{...}}
//   404 / 400 / 401 / 500
public function crear_movimiento()
{
    if (strtolower($this->input->method()) !== 'post') {
        $this->json(405, array('error' => 'metodo no permitido'));
    }

    $this->exigir_api_key();

    // CI3 no parsea el JSON del cuerpo solo. El fallback cubre una version
    // anterior a 3.0, donde `raw_input_stream` no existe.
    $crudo = $this->input->raw_input_stream;
    if ($crudo === NULL || $crudo === '') {
        $crudo = file_get_contents('php://input');
    }
    $datos = json_decode($crudo, TRUE);
    if (!is_array($datos)) {
        $this->json(400, array(
            'error'    => 'body invalido: se esperaba JSON',
            'recibido' => substr((string) $crudo, 0, 120),
        ));
    }

    $unidad_id = isset($datos['unidad_id']) ? (int) $datos['unidad_id'] : 0;
    if ($unidad_id <= 0) {
        $this->json(400, array('error' => 'unidad_id es obligatorio'));
    }

    // El destino. `accion` es la CALLE destino y `estado` el ESTADO destino:
    // ver la nota de las columnas invertidas, arriba.
    $accion = isset($datos['accion']) ? trim($datos['accion']) : '';
    $estado = isset($datos['estado']) ? trim($datos['estado']) : '';
    if ($accion === '' || $estado === '') {
        $this->json(400, array(
            'error'  => 'accion (calle destino) y estado (estado destino) son obligatorios',
            'codigo' => 'validacion',
        ));
    }
    $patio     = isset($datos['patio'])      ? trim($datos['patio'])      : '';
    $ubicacion = isset($datos['ubicacion'])  ? trim($datos['ubicacion'])  : '';
    $clientemov= isset($datos['clientemov']) ? trim($datos['clientemov']) : '';
    $obs       = isset($datos['obs'])        ? trim($datos['obs'])        : '';
    $created_by= isset($datos['created_by']) ? (int) $datos['created_by'] : 0;

    $idem_key = isset($_SERVER['HTTP_IDEMPOTENCY_KEY'])
        ? trim($_SERVER['HTTP_IDEMPOTENCY_KEY']) : '';

    // -- 1. La idempotencia, ANTES de cualquier otra cosa --------------------
    //
    // Mismo orden y mismo motivo que en `actualizar`: si el POST llego, se
    // aplico, y la respuesta se perdio, el reintento tiene que devolver la
    // respuesta de entonces. Sin esto un reintento crea un SEGUNDO movimiento
    // -- y `registros` es append-only, asi que el duplicado queda para
    // siempre en el historial.
    if ($idem_key !== '') {
        $consulta = $this->db->get_where('api_idempotency', array(
            'idem_key' => $idem_key,
            'entidad'  => 'movimientos',
        ));
        // Con db_debug = FALSE una consulta fallida devuelve FALSE, y llamarle
        // ->row() seria un fatal: 500 con cuerpo vacio. El caso real es tener
        // el controlador desplegado sin la tabla.
        if ($consulta === FALSE) {
            $this->json(500, array(
                'error'  => 'falta la tabla api_idempotency en la base',
                'codigo' => 'falta_tabla',
            ));
        }
        $previo = $consulta->row();
        if ($previo) {
            $this->json(200, array(
                'ok'          => TRUE,
                'id'          => (int) $previo->entidad_id,
                'updated_at'  => $previo->updated_at,
                'idempotente' => TRUE,
            ));
        }
    }

    // -- 2. La unidad tiene que existir --------------------------------------
    //
    // REGLA no crea unidades: si el id no esta, es un error de verdad.
    $consulta = $this->db->get_where('newstocks_cidef', array('id' => $unidad_id));
    if ($consulta === FALSE) {
        $this->json(500, array('error' => 'no se pudo leer newstocks_cidef'));
    }
    $fila = $consulta->row_array();
    if (!$fila) {
        $this->json(404, array('error' => 'unidad no encontrada: ' . $unidad_id));
    }

    // -- 3. Locking optimista -------------------------------------------------
    //
    // Se compara como TEXTO, sin parsear: los dos lados son 'Y-m-d H:i:s', que
    // ordena igual alfabetica que cronologicamente. Ninguna fecha se parsea de
    // este lado del cable, que es la misma regla del pull.
    $actual = isset($fila['updated_at']) ? (string) $fila['updated_at'] : '';
    if ($actual === '0000-00-00 00:00:00') { $actual = ''; }
    $conocido = isset($datos['legado_updated_at_conocido'])
        ? (string) $datos['legado_updated_at_conocido'] : '';
    if ($conocido === '0000-00-00 00:00:00') { $conocido = ''; }

    if ($actual !== '' && $conocido !== '' && strcmp($actual, $conocido) > 0) {
        // Gana el legado. No se escribe NADA -- ni el movimiento ni la unidad.
        $this->json(409, array(
            'ok'             => FALSE,
            'conflicto'      => TRUE,
            'updated_at'     => $actual,
            'datos_actuales' => array(
                'calle'      => isset($fila['calle'])      ? $fila['calle']      : null,
                'despachado' => isset($fila['despachado']) ? $fila['despachado'] : null,
                'patio'      => isset($fila['patio'])      ? $fila['patio']      : null,
                'ubicacion'  => isset($fila['ubicacion'])  ? $fila['ubicacion']  : null,
            ),
        ));
    }

    // -- 4. El ORIGEN, leido de la fila que estamos por pisar -----------------
    //
    // Va a las columnas `new*`, que son las de procedencia. Se toma de `$fila`,
    // que se leyo recien y dentro de esta misma peticion: es lo que la unidad
    // tiene en el sistema anterior justo antes del cambio.
    $origen_calle  = isset($fila['calle'])      ? $fila['calle']      : '';
    $origen_estado = isset($fila['despachado']) ? $fila['despachado'] : '';
    $origen_patio  = isset($fila['patio'])      ? $fila['patio']      : '';
    $vin = isset($fila['vin']) ? $fila['vin'] : '';

    $ahora = $this->db->query('SELECT NOW() AS ahora')->row()->ahora;

    $mov = array(
        'vin'        => $vin,
        // DESTINO
        'accion'     => $accion,
        'estado'     => $estado,
        'patio'      => $patio,
        // ORIGEN -- si, con el prefijo `new`. Ver la nota del encabezado.
        'newcalle'   => $origen_calle,
        'newestado'  => $origen_estado,
        'newpatio'   => $origen_patio,
        'clientemov' => $clientemov,
        'obs'        => $obs,
        'created_by' => $created_by,
        'created_at' => $ahora,
    );

    $aux = array(
        'calle'      => $accion,
        'despachado' => $estado,
        'updated_by' => $created_by,
        'updated_at' => $ahora,
    );
    if ($patio !== '')     { $aux['patio'] = $patio; }
    if ($ubicacion !== '') { $aux['ubicacion'] = $ubicacion; }

    // -- 5. LAS DOS ESCRITURAS, EN UNA TRANSACCION ----------------------------
    //
    // Esto es lo que el original NO hace, y es el motivo principal de que este
    // endpoint exista. Ver el encabezado.
    //
    // Transaccion MANUAL (trans_begin) y no trans_start(): hace falta mirar
    // trans_status() para distinguir el choque de idempotency_key duplicada
    // -- que es una carrera legitima entre dos reintentos simultaneos, y la
    // resuelve la PRIMARY KEY -- de una falla de base de verdad.
    //
    // `registros` y `newstocks_cidef` tienen que ser InnoDB para que el
    // ROLLBACK sirva. Con MyISAM seria un no-op silencioso y volveriamos a
    // tener el problema que este endpoint viene a resolver.
    $this->db->trans_begin();

    $this->db->insert('registros', $mov);
    $id_registro = $this->db->insert_id();
    if (!$id_registro) {
        $this->db->trans_rollback();
        $this->json(500, array('error' => 'no se pudo insertar el movimiento',
                               'codigo' => 'db'));
    }

    $this->db->where('id', $unidad_id);
    $this->db->update('newstocks_cidef', $aux);

    if ($idem_key !== '') {
        $this->db->insert('api_idempotency', array(
            'idem_key'   => $idem_key,
            'entidad'    => 'movimientos',
            'entidad_id' => $id_registro,
            'updated_at' => $ahora,
        ));
    }

    if ($this->db->trans_status() === FALSE) {
        $this->db->trans_rollback();

        // Si la key ya estaba, otro intento del MISMO push gano la carrera:
        // el movimiento esta aplicado y lo correcto es devolver su respuesta.
        if ($idem_key !== '') {
            $consulta = $this->db->get_where('api_idempotency', array(
                'idem_key' => $idem_key,
                'entidad'  => 'movimientos',
            ));
            $previo = ($consulta === FALSE) ? FALSE : $consulta->row();
            if ($previo) {
                $this->json(200, array(
                    'ok'          => TRUE,
                    'id'          => (int) $previo->entidad_id,
                    'updated_at'  => $previo->updated_at,
                    'idempotente' => TRUE,
                ));
            }
        }
        $this->json(500, array('error' => 'no se pudo registrar el movimiento',
                               'codigo' => 'db'));
    }

    $this->db->trans_commit();

    $this->json(201, array(
        'ok'         => TRUE,
        'id'         => (int) $id_registro,
        'updated_at' => $ahora,
    ));
}
