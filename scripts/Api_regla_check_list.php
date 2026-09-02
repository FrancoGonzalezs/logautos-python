<?php if (!defined('BASEPATH')) exit('No direct script access allowed');

/**
 * Api_regla_check_list.php -- lo que necesita `Api_regla.php` para los dos
 * check list.
 *
 * NO VA EN ESTE REPOSITORIO NI SE INCLUYE. Son DOS ediciones sobre
 *
 *     application/controllers/Api_regla.php
 *
 * mas DOS rutas en routes.php.
 *
 * `php -l ~/public_html/application/controllers/Api_regla.php` despues de
 * subir, y los dos grep de CLAUDE.md antes de probar nada:
 *
 *     grep -o "function [a-z_]*" .../Api_regla.php | sort | uniq -d   -> vacio
 *     grep -c "<?php" .../Api_regla.php                               -> 1
 *
 *
 * LO QUE ESTE ARCHIVO **NO** CUBRE, Y POR QUE
 * ===========================================
 *
 * La OT del check list de ingreso. No es que falte escribirla: es que hay una
 * decision pendiente que no me toca a mi. Ver el informe -- el motor de precios
 * son 4.193 lineas con 24 ramas de tipo de daño, y replicarlo entero es un
 * proyecto, no un bloque de spec.
 *
 * Este archivo cubre LOS DATOS de los dos check list, que es la primera de las
 * tres partes del diseño y la que no depende de esa decision.
 */


/* ===========================================================================
   BLOQUE G -- las dos entidades nuevas, que son INSERT y no UPDATE
   ===========================================================================

   AQUI HAY UN AGUJERO DE FORMA, no de lista blanca, y conviene verlo antes de
   escribir codigo.

   `actualizar()` hace UPDATE sobre una fila que YA EXISTE, y por eso alcanzo
   para el IT y para la PDI: las dos escriben columnas de una unidad que el
   dump ya trajo. Un check list es una fila NUEVA en `check_list` o en
   `check_list_mecanica`. No hay nada que actualizar.

   O sea que la generalizacion del bloque A -- entidad -> (tabla, columnas) --
   sirve para la lista blanca pero NO para el verbo. Hace falta un `crear`, con
   la forma de `crear_movimiento`: INSERT + el UPDATE de la unidad, los dos en
   UNA transaccion.

   Y con la Idempotency-Key OBLIGATORIA, por el mismo motivo que en las OT y en
   el descuento de stock: `check_list` es append-only en la practica -- nada la
   borra -- y un reintento sin key deja dos check list del mismo VIN el mismo
   dia, que despues alguien tiene que ir a explicar.

   LAS COLUMNAS. Se listan aca para que la lista blanca no se escriba a ojo.
   Salen del array `$userInfo` de `Nota.php:check_list()` y de
   `check_list_mecanica_proces()`, contados uno por uno.

     check_list             24 columnas
     check_list_mecanica    82 columnas

   OJO CON TRES NOMBRES DE `check_list`, y no es un detalle de estilo: son la
   fuente de los daños y el nombre miente en los tres.

       observacion    -> las PIEZAS, unidas por '-'
       requerimiento  -> los TIPOS DE DAÑO, unidos por '-'
       gravedad       -> los NIVELES, unidos por '-'

   Verificado contra el modelo (`getPiezas_CLI` lee `observacion`,
   `getTipoDano_CLI` lee `requerimiento`, `getNivelDano_CLI` lee `gravedad`) y
   contra el dato: en la fila 20100, seis piezas, seis tipos y seis niveles,
   alineados por posicion.

   Hay ademas una `observaciones` en plural, que SI es la observacion libre. Un
   nombre de mas de diferencia entre "las piezas dañadas" y "lo que escribio el
   encargado".
   =========================================================================== */

    /**
     * REEMPLAZA `mapa_entidades()` COMPLETO.
     *
     * QUE BORRAR, EXACTAMENTE: desde la linea que dice
     *
     *     private function mapa_entidades()
     *
     * hasta el `}` que la cierra -- la ultima linea del metodo, la que esta
     * justo antes de `private function columnas_permitidas($entidad)`.
     * NO borres `columnas_permitidas` ni `tabla_de`: siguen igual y siguen
     * leyendo de este mapa.
     */
    private function mapa_entidades()
    {
        return array(
            'unidades' => array(
                'tabla'    => 'newstocks_cidef',
                'verbo'    => 'actualizar',
                'columnas' => array(
                    // -- el IT, desplegado el 2026-08-26 --------------------
                    'estado_it', 'observacion_it', 'despachado', 'calle',
                    'updated_by',
                    // -- la PDI, desplegada el 2026-08-27 -------------------
                    'fecha_pdi', 'mes_pdi', 'mespdinombre', 'estadostock',
                    'ubicacion', 'tipo_combu', 'bateria', 'scanner', 'a_c',
                    'ob_mecanica', 'aceite_coco', 'sistema_audio', 'adblue',
                    'aceite_diferencial',

                    // -- BLOQUE H: lo que escriben los dos check list -------
                    //
                    // SOLO LO DEL PASO UNO. Decidido el 2026-08-28: la OT la
                    // sigue creando el legado durante el mes en paralelo, asi
                    // que REGLA NO hace el paso del correo -- y el estado se
                    // mueve ahi, no al guardar.
                    //
                    // Lo que NO entra, y es a proposito:
                    //     estado_check_list   lo escribe el paso del correo
                    //     calle / despachado / patio  idem (ya estan arriba
                    //                         por el IT y la PDI, y para el
                    //                         check list NO se mandan)
                    //
                    // Las diez del `$historico` de `check_list()`, que si se
                    // escriben al guardar:
                    'ingreso', 'g_ingreso', 'kilometraje', 'observaciones',
                    'observacion_general', 'ob_faltante', 'fecha_check_list',
                    'estado_carflex', 'fecha_lavado_produccion', 'tapiz',
                    // Y la UNICA del check list MECANICO, tambien al guardar.
                    'fecha_check_list_mecanica',
                ),
            ),

            // -- LAS DOS NUEVAS. `verbo` es 'crear': son filas nuevas -------
            'check_list' => array(
                'tabla'    => 'check_list',
                'verbo'    => 'crear',
                'columnas' => array(
                    'vin', 'patente', 'guia_ingreso', 'cliente',
                    'fecha_ingreso', 'marca', 'modelo', 'color', 'encargado',
                    'estanque', 'kilometraje', 'fecha_entrega',
                    'equipamiento1', 'equipamiento2', 'equipamiento3',
                    'faltante', 'observaciones', 'link_unidad', 'link_guia',
                    'fecha_completa', 'id_vin', 'motonave', 'tapiz',
                    'n_asientos',
                    // Las tres de los daños, con los nombres que mienten. Ver
                    // el encabezado del bloque G.
                    'observacion', 'requerimiento', 'gravedad',
                ),
            ),

            'check_list_mecanica' => array(
                'tabla'    => 'check_list_mecanica',
                'verbo'    => 'crear',
                // OCHENTA Y DOS. No se abrevian ni se agrupan: la lista blanca
                // es la unica puerta de escritura remota y tiene que poder
                // leerse contra el `$userInfo` del PHP, uno por uno.
                'columnas' => array(
                    'vin', 'patente', 'id_vin', 'guia', 'cliente',
                    'fecha_ingreso', 'marca', 'modelo', 'color', 'encargado',
                    'estanque', 'kilometraje', 'estado_carflex',
                    'fecha_creacion', 'fecha_creacion_completa',
                    'llaves', 'tad', 'tat', 'tca', 'bateria', 'alternador',
                    'bocina', 'tdi', 'mdc', 'Limpiaparabrisas', 'er', 'cc',
                    'ae', 'Sunroof', 'Chapas', 'Airbag', 'fa', 'vc',
                    'Bluetooth', 'Neblineros', 'Gata', 'Extintor', 'Llantas',
                    'Radio', 'tet', 'aa', 'sd', 'st', 'sdd', 'hec', 'pfd',
                    'pft', 'dfd', 'dft', 'fde', 'cda', 'etv', 'mdr',
                    'Radiador', 'dde', 'cdac', 'cdac2', 'cbbe', 'fda', 'mdp',
                    'pef', 'cdas', 'ftcdt', 'sdacdt', 'nam', 'nlr', 'nldf',
                    'nldh', 'nlde', 'nata', 'fadm', 'flr', 'flshde', 'fldd',
                    'fatma', 'fdaed', 'nd', 'nt', 'nr', 'pocc', 'obs_general',
                    'estado',
                ),
            ),
        );
    }

/* ---------------------------------------------------------------------------
   Y las dos rutas, en routes.php. La del descuento sigue ARRIBA de la del GET
   de stock, como quedo; estas dos van despues y no se pisan con ninguna:

       $route['api_regla/check_list']['POST']          = 'api_regla/crear_fila/check_list';
       $route['api_regla/check_list_mecanica']['POST'] = 'api_regla/crear_fila/check_list_mecanica';
   --------------------------------------------------------------------------- */


/* ===========================================================================
   BLOQUE I -- `crear_fila`, el verbo que falta
   ===========================================================================

   Metodo NUEVO. NO reemplaza nada: se agrega antes del `}` que cierra la
   clase, junto a los otros publicos.

   Es GENERICO sobre el mapa -- a diferencia de `crear_ot_pdi`, que es estrecho
   a proposito. La diferencia esta en lo que hay del otro lado: `orden_trabajo`
   es la tabla de la que sale la facturacion y ahi una puerta ancha es un
   riesgo real; `check_list` es un formulario de inspeccion, la lista blanca ya
   acota las columnas, y dos endpoints casi identicos se separan con el tiempo.

   NO TOCA LA UNIDAD. El `$historico` y el cambio de estado van por el PUT de
   `unidades`, que ya existe, con las columnas del bloque H. Son dos entradas
   de cola distintas del lado de Python, y la del check list depende de la otra
   -- misma mecanica que las OT de la PDI, `depende_de`.

   Se decidio asi y no con una transaccion unica porque el orden importa al
   reves que en la PDI: si el check list entra y la unidad no, queda un check
   list sin efecto -- recuperable, se reintenta. Si la unidad entra y el check
   list no, la unidad dice que tiene check list y no hay ninguno.
   =========================================================================== */

// ---------------------------------------------------------------------------
// POST /api_regla/check_list  y  /api_regla/check_list_mecanica
// ---------------------------------------------------------------------------
//
// Cuerpo (JSON): las columnas de la lista blanca de esa entidad.
// Cabecera: Idempotency-Key (UUID). OBLIGATORIA.
//
//   201 {"ok":true,"id":<id de la fila creada>}
//   200 {"ok":true,"id":...,"idempotente":true}
//   400 / 401 / 404 / 500
public function crear_fila($entidad = null)
{
    if (strtolower($this->input->method()) !== 'post') {
        $this->json(405, array('error' => 'metodo no permitido'));
    }
    $this->exigir_api_key();

    $mapa = $this->mapa_entidades();
    if (!isset($mapa[$entidad]) || $mapa[$entidad]['verbo'] !== 'crear') {
        $this->json(404, array(
            'error' => 'entidad no soportada para crear: ' . $entidad));
    }
    $tabla    = $mapa[$entidad]['tabla'];
    $columnas = $mapa[$entidad]['columnas'];

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
                      . 'deja dos check list del mismo VIN el mismo dia',
            'codigo' => 'falta_idempotency_key',
        ));
    }

    $consulta = $this->db->get_where('api_idempotency', array(
        'idem_key' => $idem_key, 'entidad' => $entidad));
    if ($consulta === FALSE) {
        $this->json(500, array('error' => 'falta la tabla api_idempotency',
                               'codigo' => 'falta_tabla'));
    }
    $previo = $consulta->row();
    if ($previo) {
        $this->json(200, array('ok' => TRUE, 'idempotente' => TRUE,
                               'id' => (int) $previo->entidad_id));
    }

    // -- la lista blanca. Lo que no esta se IGNORA, igual que en `actualizar`.
    //
    // Se cuenta lo ignorado y se DEVUELVE, que es lo unico distinto de
    // `actualizar`: ese silencio nos costo una semana con las catorce columnas
    // de la PDI. Con el numero en la respuesta, Python puede afirmar sobre el.
    $fila = array();
    $ignoradas = array();
    foreach ($datos as $columna => $valor) {
        if (in_array($columna, $columnas, TRUE)) {
            $fila[$columna] = $valor;
        } else {
            $ignoradas[] = $columna;
        }
    }
    if (!$fila) {
        $this->json(400, array(
            'error'     => 'ninguna columna del cuerpo esta en la lista blanca',
            'ignoradas' => $ignoradas,
        ));
    }

    $this->db->trans_begin();
    $this->db->insert($tabla, $fila);
    $id_fila = $this->db->insert_id();
    if (!$id_fila) {
        $this->db->trans_rollback();
        $this->json(500, array('error' => 'no se pudo insertar en ' . $tabla,
                               'codigo' => 'db'));
    }
    $this->db->insert('api_idempotency', array(
        'idem_key'   => $idem_key,
        'entidad'    => $entidad,
        'entidad_id' => $id_fila,
        'updated_at' => $this->db->query('SELECT NOW() AS ahora')->row()->ahora,
    ));

    if ($this->db->trans_status() === FALSE) {
        $this->db->trans_rollback();
        $consulta = $this->db->get_where('api_idempotency', array(
            'idem_key' => $idem_key, 'entidad' => $entidad));
        $previo = ($consulta === FALSE) ? FALSE : $consulta->row();
        if ($previo) {
            $this->json(200, array('ok' => TRUE, 'idempotente' => TRUE,
                                   'id' => (int) $previo->entidad_id));
        }
        $this->json(500, array('error' => 'no se pudo crear la fila',
                               'codigo' => 'db'));
    }
    $this->db->trans_commit();

    $this->json(201, array(
        'ok'        => TRUE,
        'id'        => (int) $id_fila,
        'escritas'  => count($fila),
        'ignoradas' => $ignoradas,
    ));
}


/* ===========================================================================
   BLOQUE J -- `observaciones` SE ACUMULA, y la guarda no es un comentario
   ===========================================================================

   `newstocks_cidef.observaciones` NO se pisa: se le CONCATENA. El legado hace

       $obs_anterior  = getobservacion_dyp($id_vin);
       $obs_anterior .= ' '.$danos_historico;
       actualizar_vin($id_vin, array('observaciones' => $obs_anterior, ...));

   o sea que la columna guarda los daños de TODAS las pasadas, uno detras de
   otro. Un UPDATE que la reemplace no pierde un valor: pierde HISTORIA.

   Es peor que el borrado de `ubicacion` de la PDI. Aquello borraba un dato que
   se vuelve a escribir en el proximo movimiento; esto borra los daños de las
   pasadas anteriores de ese VIN, que no estan en ningun otro lado -- y no se
   nota, porque la columna sigue teniendo texto.

   POR QUE LA GUARDA VA ACA Y NO EN PYTHON
   ---------------------------------------
   Se podria concatenar del lado de Python: leer la columna, pegarle lo nuevo,
   mandar el resultado. Tres problemas, y el tercero decide:

     1. Python leeria la REPLICA, que tiene hasta 300 s de atraso. Si el legado
        agrego algo en el medio, se pisa.
     2. Dos push simultaneos sobre el mismo VIN leen lo mismo y el segundo pisa
        al primero. Es la misma carrera del stock de combustible.
     3. Es una regla que hay que RECORDAR. La proxima persona que agregue una
        columna acumulativa a la lista blanca no tiene forma de saberlo.

   Con la concatenacion del lado del servidor, la columna es acumulativa POR
   CONSTRUCCION: da igual lo que mande Python, no se puede pisar. Es el mismo
   criterio que `stock = stock - ?` en el descuento de combustible -- la
   operacion correcta se expresa en SQL, no en la aplicacion.

   AGREGAR UNA COLUMNA A ESTA LISTA CAMBIA EL VERBO DE ESA COLUMNA. No es
   configuracion: es la diferencia entre reemplazar y agregar.
   =========================================================================== */

    /**
     * Metodo NUEVO. Se agrega junto a `columnas_permitidas` y `tabla_de`,
     * antes de `actualizar()`. NO reemplaza nada.
     */
    private function columnas_que_acumulan($entidad)
    {
        $mapa = array(
            // El historial de daños de TODAS las pasadas del VIN. Ver el
            // encabezado del bloque J: pisarla borra historia.
            'unidades' => array('observaciones'),
        );
        return isset($mapa[$entidad]) ? $mapa[$entidad] : array();
    }

/* ---------------------------------------------------------------------------
   Y el armado del UPDATE en `actualizar()`.

   QUE BORRAR, EXACTAMENTE. En `actualizar()`, el bucle que arma `$cambios`.
   La PRIMERA linea a borrar es

       $cambios = array();

   y la ULTIMA es el `}` que cierra el `foreach` -- son seis lineas en total,
   las que estan justo antes de

       if (!$cambios) {

   Se reemplazan por lo de abajo. `$cambios` sigue existiendo y sigue usandose
   igual mas adelante -- el `updated_at`, el `update()` --, asi que no hay que
   tocar nada mas salvo la condicion del `if`, que se explica al final.

   OJO: el bucle recorre `$columnas` (la lista blanca) y pregunta si el cuerpo
   la trae, NO al reves. Se mantiene asi -- recorrer el cuerpo daria el mismo
   resultado hoy, pero cambia quien manda: la lista blanca tiene que ser el que
   itera, para que agregar una clave al cuerpo no pueda alterar nada.
   --------------------------------------------------------------------------- */

        $cambios = array();
        $acumulan = $this->columnas_que_acumulan($entidad);
        $hubo_acumulado = FALSE;

        foreach ($columnas as $columna) {
            if (!array_key_exists($columna, $datos)) {
                continue;
            }
            $valor = $datos[$columna];

            if (in_array($columna, $acumulan, TRUE)) {
                // ACUMULA: se concatena del lado del servidor, en la misma
                // sentencia, asi que no hay lectura previa que pueda quedar
                // vieja ni carrera entre dos push.
                //
                // El separador es UN espacio, como el `.= ' '.` del legado.
                // COALESCE porque la columna puede venir NULL de una fila que
                // nunca tuvo daños, y CONCAT(NULL, x) en MySQL da NULL --
                // borraria todo, que es exactamente lo que esto viene a
                // impedir.
                //
                // El tercer parametro FALSE de `set()` dice "esto es SQL, no
                // un valor", y por eso el valor va escapado a mano: sin
                // `escape()` esto seria una inyeccion.
                if ($valor === '' || $valor === NULL) {
                    continue;                    // nada que agregar
                }
                $this->db->set(
                    $columna,
                    "CONCAT(COALESCE(`{$columna}`, ''), ' ', "
                        . $this->db->escape($valor) . ")",
                    FALSE);
                $hubo_acumulado = TRUE;
                continue;
            }

            $cambios[$columna] = $valor;
        }

/* ---------------------------------------------------------------------------
   OJO CON EL `if (!$cambios)` QUE VIENE DESPUES.

   Un cuerpo que traiga SOLO `observaciones` deja `$cambios` vacio -- la
   concatenacion no pasa por ahi, pasa por `set()` -- y ese `if` lo rechazaria
   con 400 aunque la escritura sea legitima.

   Hay que cambiarle la condicion. La linea a reemplazar es exactamente

       if (!$cambios) {

   por

       if (!$cambios && !$hubo_acumulado) {

   Se usa la bandera y no `$this->db->qb_set` a proposito: `qb_set` es interno
   de CodeIgniter y depende de la version. Una bandera propia dice lo mismo y
   no se rompe con un upgrade.
   --------------------------------------------------------------------------- */
