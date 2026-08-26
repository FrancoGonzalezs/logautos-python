<?php if (!defined('BASEPATH')) exit('No direct script access allowed');

/**
 * Api_regla.php -- el endpoint que REGLA (el sistema en Python) consulta para
 * saber que cambio en el legado.
 *
 * NO VA EN ESTE REPOSITORIO. Vive en el proyecto PHP:
 *
 *     application/controllers/Api_regla.php
 *
 * Se deja aca porque es codigo que REGLA necesita que exista del otro lado, y
 * asi queda versionado junto con el cliente que lo consume
 * (modulos/sync_legado.py). Si cambia uno, tiene que cambiar el otro.
 *
 * Dos cosas:
 *
 *   GET /api_regla/cambios/unidades   lo que el legado escribio (el pull)
 *   PUT /api_regla/unidades/{id}      lo que REGLA devuelve  (el push)
 *
 * Rutas EXPLICITAS en routes.php -- no salen por convencion de nombres:
 *
 *     $route['api_regla/cambios/(:any)']['GET'] = 'api_regla/cambios/$1';
 *     $route['api_regla/unidades/(:num)']['PUT'] = 'api_regla/actualizar/unidades/$1';
 *
 * El PUT necesita ademas la tabla `api_idempotency` (ver `actualizar`).
 *
 * No hay POST y es deliberado: hoy ninguna entidad CREA filas en el legado.
 * Las unidades nacen alla y llegan por el pull; el IT es un UPDATE. Un POST
 * ahora seria codigo muerto y ademas una puerta de escritura abierta sin nadie
 * que la use. Se agrega junto con movimientos, que es la primera entidad que
 * inserta de verdad (una fila de `registros` por movimiento).
 *
 * OJO CON `db_debug` en application/config/database.php: tiene que estar en
 * FALSE. Con TRUE, CI3 corta la ejecucion en cualquier error de base pintando
 * una pagina HTML, asi que nunca se llega al rollback ni a la respuesta JSON.
 * Degrada seguro (InnoDB revierte la transaccion sin commit al cerrar la
 * conexion), pero lo que Python recibe es un cuerpo que no es JSON y lo trata
 * como fallo: reintenta con la misma Idempotency-Key.
 *
 * Por que el pull y no un push desde aca
 * --------------------------------------
 * Porque no hay donde enganchar un push. `Examples.php` tiene 363 instancias
 * de grocery_CRUD -- 172 editan newstocks_cidef directo -- y ni un solo
 * callback_after_insert/update. Cualquier grilla que se escape haria divergir
 * los datos en silencio. Que Python pregunte cubre todas por igual.
 *
 * La marca de agua
 * ----------------
 * El cliente manda `desde` y este endpoint devuelve `hasta`, que es la hora
 * de ESTE servidor. Python la guarda como texto y la devuelve tal cual en la
 * proxima vuelta: nunca la parsea ni le hace aritmetica. Asi hay un solo
 * reloj -- el de aca -- y no dos que conciliar.
 *
 * El margen de solapamiento de 2 minutos se aplica ACA, dentro del SQL, por
 * el mismo motivo: es este servidor el que sabe que hora es. Cubre las filas
 * escritas entre que se leyo NOW() y que se cerro la transaccion.
 *
 * Por que COALESCE(updated_at, created_at)
 * ----------------------------------------
 * En newstocks_cidef `updated_at` esta poblado en el 85,6% de las filas y
 * `created_at` en el 93,6%: una fila recien creada y nunca editada no tiene
 * updated_at. Mirar solo updated_at se saltearia las altas.
 *
 * LIMITACION CONOCIDA: 74 filas (0,1%) no tienen ninguna de las dos, asi que
 * este endpoint no las devuelve nunca. Se resuelve con una reconciliacion
 * completa ocasional (`desde` vacio), no inventandoles una fecha.
 */
class Api_regla extends CI_Controller
{
    /** Paginas mas grandes que esto no se sirven: protege la memoria del PHP. */
    const LIMITE_MAXIMO = 500;

    /** Minutos de solapamiento. Ver la nota de la marca de agua. */
    const MARGEN_MINUTOS = 2;

    public function __construct()
    {
        parent::__construct();
        $this->load->database();
    }

    /**
     * La API key viaja en X-API-Key y se compara en tiempo constante -- ver
     * `claves_iguales` -- para no filtrar informacion por lo que tarda la
     * comparacion.
     *
     * La clave sale del entorno o de config, NUNCA escrita aca: este archivo
     * se versiona, y ya hay dos credenciales en texto plano en el codigo del
     * legado (el SMTP de enviosdespacho y la API de facto.cl) que no conviene
     * imitar.
     */
    private function exigir_api_key()
    {
        $esperada = getenv('REGLA_API_KEY');
        $origen   = 'putenv/getenv';
        if (!$esperada) {
            $esperada = $this->config->item('regla_api_key');
            $origen   = 'config.php';
        }
        if (!$esperada) {
            // El detalle dice DONDE se busco, no solo que falto: es la
            // diferencia entre "hay que arreglar putenv" y "hay que revisar
            // otra cosa", y sin eso hay que salir a adivinar desde afuera.
            $this->json(500, array(
                'error'  => 'REGLA_API_KEY no configurada en el servidor',
                'buscada_en' => array(
                    'getenv(REGLA_API_KEY)'        => 'vacio',
                    'config item regla_api_key'    => 'vacio',
                ),
                'php' => PHP_VERSION,
            ));
        }

        $recibida = isset($_SERVER['HTTP_X_API_KEY']) ? $_SERVER['HTTP_X_API_KEY'] : '';
        if (!is_string($recibida) || !$this->claves_iguales($esperada, $recibida)) {
            // Se dice de donde salio la clave buena, pero NUNCA la clave: eso
            // confirma que putenv tomo sin exponer nada.
            $this->json(401, array(
                'error'  => 'API key invalida o ausente',
                'origen_de_la_clave' => $origen,
            ));
        }
    }

    /**
     * OJO CON EL ECHO: es a proposito, y la version anterior de este metodo
     * estaba mal.
     *
     * Decia `set_output(...)` y despues `exit()`. CodeIgniter no manda lo que
     * le pasas a set_output() en ese momento -- lo guarda y lo vuelca al
     * final, cuando el controlador retorna, desde CodeIgniter.php. El exit()
     * se saltea ese volcado, asi que el header del status SI llegaba (es una
     * llamada real a header()) pero el cuerpo se perdia entero.
     *
     * El sintoma era exactamente el que costo un rato entender: 500 con
     * cuerpo vacio y Content-Type text/html, sin forma de saber que error
     * era. El endpoint no podia contar lo que le pasaba.
     *
     * `echo` escribe directo al flujo de salida, asi que sobrevive al exit().
     * Es ademas el patron que ya usa el resto de este proyecto: 37 lugares
     * hacen `echo json_encode(...)` contra 5 que usan set_output().
     */
    private function json($codigo, $cuerpo)
    {
        $this->output->set_status_header($codigo);
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode($cuerpo, JSON_UNESCAPED_UNICODE);
        // Se corta acá: los que llaman a json() asumen que no se sigue.
        exit();
    }

    /**
     * Comparacion en tiempo constante, sin depender de hash_equals().
     *
     * hash_equals() existe desde PHP 5.6, y no hay forma de saber desde
     * afuera que version corre este hosting: no expone X-Powered-By. Si
     * fuera anterior, llamarla seria un fatal -- y un fatal aca se ve igual
     * que cualquier otro error, o sea 500 sin cuerpo.
     *
     * Se usa hash_equals cuando esta y si no esta el equivalente a mano. La
     * comparacion recorre la cadena entera igual (no corta en la primera
     * diferencia) para no filtrar por tiempo cuantos caracteres coincidian.
     */
    private function claves_iguales($esperada, $recibida)
    {
        if (function_exists('hash_equals')) {
            return hash_equals((string) $esperada, (string) $recibida);
        }
        $a = (string) $esperada;
        $b = (string) $recibida;
        if (strlen($a) !== strlen($b)) {
            return FALSE;
        }
        $diferencia = 0;
        for ($i = 0; $i < strlen($a); $i++) {
            $diferencia |= ord($a[$i]) ^ ord($b[$i]);
        }
        return $diferencia === 0;
    }

    /**
     * GET /api_regla/cambios/unidades?desde=&limite=&pagina=
     *
     * Devuelve:
     *   filas    array de filas de newstocks_cidef
     *   hasta    la hora de ESTE servidor, para guardar como marca de agua
     *   hay_mas  si quedan paginas
     */
    public function cambios($entidad = null)
    {
        $this->exigir_api_key();

        if ($entidad !== 'unidades') {
            $this->json(404, array('error' => 'entidad no soportada: ' . $entidad));
        }

        $desde  = (string) $this->input->get('desde');
        $limite = (int) $this->input->get('limite');
        $pagina = (int) $this->input->get('pagina');

        if ($limite <= 0)                    { $limite = 200; }
        if ($limite > self::LIMITE_MAXIMO)   { $limite = self::LIMITE_MAXIMO; }
        if ($pagina <= 0)                    { $pagina = 1; }

        // La hora de este servidor, capturada ANTES de leer: si se tomara
        // despues, las filas escritas durante la consulta quedarian por
        // debajo de la marca y no se traerian nunca.
        $hasta = $this->db->query('SELECT NOW() AS ahora')->row()->ahora;

        // `cambiado` es la fecha efectiva de la fila. NULLIF cubre las que
        // tienen '' o el centinela '0000-00-00 00:00:00' de MySQL.
        $cambiado = "COALESCE("
                  . "NULLIF(NULLIF(updated_at, ''), '0000-00-00 00:00:00'), "
                  . "NULLIF(NULLIF(created_at, ''), '0000-00-00 00:00:00'))";

        $condicion = '';
        $parametros = array();
        if ($desde !== '') {
            // El margen se resta ACA, con el reloj de este servidor.
            $condicion = " WHERE {$cambiado} >= DATE_SUB(?, INTERVAL "
                       . self::MARGEN_MINUTOS . " MINUTE)";
            $parametros[] = $desde;
        } else {
            // Reconciliacion completa: igual se excluyen las filas sin
            // ninguna fecha, para que el orden sea estable entre paginas.
            $condicion = " WHERE {$cambiado} IS NOT NULL";
        }

        // El ORDEN es por (cambiado, id) y no solo por cambiado: sin el id de
        // desempate, dos filas con el mismo timestamp pueden repartirse entre
        // dos paginas o aparecer dos veces, segun como MySQL resuelva el
        // orden. Con 71.546 filas y timestamps al segundo, los empates son
        // seguros, no hipoteticos.
        $sql = "SELECT * FROM newstocks_cidef{$condicion} "
             . "ORDER BY {$cambiado} ASC, id ASC LIMIT ? OFFSET ?";
        $parametros[] = $limite + 1;   // uno de mas: asi se sabe si hay pagina siguiente
        $parametros[] = ($pagina - 1) * $limite;

        $filas = $this->db->query($sql, $parametros)->result_array();

        $hay_mas = count($filas) > $limite;
        if ($hay_mas) {
            array_pop($filas);
        }

        $this->json(200, array(
            'filas'   => $filas,
            'hasta'   => $hasta,
            'hay_mas' => $hay_mas,
            'pagina'  => $pagina,
            'limite'  => $limite,
        ));
    }

    /**
     * LAS COLUMNAS QUE REGLA PUEDE ESCRIBIR, por entidad.
     *
     * Lista blanca, nunca `$data` a secas. `newstocks_cidef` tiene 144
     * columnas y esta es la unica puerta de escritura remota que va a tener:
     * un UPDATE armado con lo que llegue permite pisar cualquiera de las 144
     * -- incluido `id` -- a quien tenga la API key.
     *
     * Las cuatro de abajo son exactamente las del array `$it` de
     * `Pedido.php:9219` (el bloque `elseif ($calle == 'It')`), menos
     * `updated_at`, que lo pone ESTE servidor con su propio reloj.
     *
     * Agregar una columna aca es un acto deliberado y va junto con la entidad
     * que la necesita. Cuando entre movimientos, `calle`/`despachado`/`patio`
     * se comparten y aparecen `estadostock` y `ubicacion`.
     */
    private function columnas_permitidas($entidad)
    {
        $mapa = array(
            'unidades' => array(
                'estado_it',
                'observacion_it',
                'despachado',
                'calle',
                'updated_by',
            ),
        );
        return isset($mapa[$entidad]) ? $mapa[$entidad] : null;
    }

    /**
     * PUT /api_regla/unidades/{id}
     *
     * Cuerpo: las columnas permitidas + `legado_updated_at_conocido`.
     * Cabecera: `Idempotency-Key` (opcional pero Python siempre la manda).
     *
     * Devuelve:
     *   200 {"ok":true, "updated_at":"..."}                      aplicado
     *   200 {"ok":true, "updated_at":"...", "idempotente":true}  ya estaba
     *   409 {"ok":false,"conflicto":true,"updated_at":"...",
     *        "datos_actuales":{...}}                             gana el legado
     *   404 / 400 / 401                                          lo obvio
     *
     * ---------------------------------------------------------------------
     * LA TABLA `api_idempotency` (hay que crearla antes de habilitar esto):
     *
     *   CREATE TABLE IF NOT EXISTS api_idempotency (
     *       idem_key   VARCHAR(64) NOT NULL PRIMARY KEY,
     *       entidad    VARCHAR(32) NOT NULL,
     *       entidad_id INT         NOT NULL,
     *       updated_at VARCHAR(25) NOT NULL DEFAULT '',
     *       creado_en  TIMESTAMP   NOT NULL DEFAULT current_timestamp()
     *   ) ENGINE=InnoDB DEFAULT CHARSET=utf8;
     *
     * InnoDB no es un detalle: el UPDATE y el registro de la key van en una
     * transaccion, y con MyISAM el ROLLBACK seria un no-op silencioso.
     *
     * ---------------------------------------------------------------------
     * POR QUE UN **PUT** NECESITA IDEMPOTENCIA
     *
     * Un PUT normal es idempotente por si solo. Uno con locking optimista NO,
     * y falla de una forma que confunde: si el PUT llega, se aplica, y la
     * respuesta se pierde en el camino, Python reintenta con el MISMO
     * `legado_updated_at_conocido` -- pero el `updated_at` de aca ya avanzo,
     * porque lo avanzo ese mismo PUT. La comparacion da conflicto y el legado
     * responde 409 contra el cambio que el propio legado acaba de aplicar.
     *
     * Resultado sin la key: un conflicto inventado, un dato correcto marcado
     * para revision manual, y un push que "fallo" habiendo funcionado.
     *
     * Por eso la consulta de la key va ANTES de comparar timestamps. El orden
     * es la mitad del arreglo.
     */
    public function actualizar($entidad = null, $id = null)
    {
        // CodeIgniter no distingue el verbo por el nombre del metodo: se
        // comprueba a mano, porque la ruta de routes.php ya filtra pero un
        // llamado directo a /api_regla/actualizar/... no pasaria por ella.
        if (strtolower($this->input->method()) !== 'put') {
            $this->json(405, array('error' => 'metodo no permitido'));
        }

        $this->exigir_api_key();

        $columnas = $this->columnas_permitidas($entidad);
        if ($columnas === null) {
            $this->json(404, array('error' => 'entidad no soportada: ' . $entidad));
        }

        $id = (int) $id;
        if ($id <= 0) {
            $this->json(400, array('error' => 'id invalido'));
        }

        // CI3 no parsea el JSON del cuerpo solo, y menos en un PUT.
        // `raw_input_stream` existe desde CI 3.0; el fallback cubre el caso de
        // que este hosting corriera algo anterior, donde la propiedad no
        // existe y devolveria NULL con un notice.
        $crudo = $this->input->raw_input_stream;
        if ($crudo === NULL || $crudo === '') {
            $crudo = file_get_contents('php://input');
        }
        $datos = json_decode($crudo, TRUE);
        if (!is_array($datos)) {
            $this->json(400, array(
                'error'   => 'body invalido: se esperaba JSON',
                'recibido' => substr((string) $crudo, 0, 120),
            ));
        }

        // La cabecera llega como HTTP_IDEMPOTENCY_KEY. Que las cabeceras
        // propias lleguen a $_SERVER en este hosting ya esta comprobado: es
        // por donde entra X-API-Key en el pull, que corre en produccion.
        $idem_key = isset($_SERVER['HTTP_IDEMPOTENCY_KEY'])
            ? trim($_SERVER['HTTP_IDEMPOTENCY_KEY']) : '';

        // -- 1. La key, antes que nada. Ver la nota de arriba ----------------
        //
        // La consulta va FUERA de la transaccion a proposito: es el camino
        // normal del reintento y no necesita bloquear nada.
        if ($idem_key !== '') {
            $consulta = $this->db->get_where('api_idempotency', array(
                'idem_key' => $idem_key,
                'entidad'  => $entidad,
            ));

            // Con db_debug = FALSE una consulta que falla devuelve FALSE, no
            // un objeto, y llamarle ->row() a FALSE es un fatal de PHP: 500
            // con cuerpo vacio, que es exactamente el error que este archivo
            // mas trabaja para no producir (ver la nota del ECHO en json()).
            //
            // El caso real no es hipotetico: es desplegar este controlador
            // antes de crear api_idempotency. Se dice QUE falta y DONDE esta
            // el DDL, en vez de dejar un 500 mudo que manda a revisar la red.
            if ($consulta === FALSE) {
                $this->json(500, array(
                    'error'  => 'falta la tabla api_idempotency en la base',
                    'codigo' => 'falta_tabla',
                    'ddl'    => 'ver el comentario de Api_regla::actualizar',
                ));
            }

            $previo = $consulta->row();
            if ($previo) {
                $this->json(200, array(
                    'ok'          => TRUE,
                    'updated_at'  => $previo->updated_at,
                    'idempotente' => TRUE,
                ));
            }
        }

        // -- 2. La fila tiene que existir ------------------------------------
        //
        // REGLA no crea unidades: si el id no esta, es un error de verdad y no
        // algo que reintentar. Se responde 404 y Python lo cuenta como fallo.
        $consulta = $this->db->get_where('newstocks_cidef', array('id' => $id));
        if ($consulta === FALSE) {               // mismo motivo que arriba
            $this->json(500, array('error' => 'no se pudo leer newstocks_cidef'));
        }
        $fila = $consulta->row_array();
        if (!$fila) {
            $this->json(404, array('error' => 'unidad no encontrada: ' . $id));
        }

        // -- 3. Locking optimista --------------------------------------------
        //
        // Se compara como TEXTO, sin parsear: los dos lados son 'Y-m-d H:i:s',
        // que ordena igual alfabeticamente que cronologicamente. Es la misma
        // decision que la marca de agua del pull (nota 3 de sync_legado.py):
        // ninguna fecha se parsea de este lado del cable.
        //
        // Los centinelas de "sin fecha" cuentan como vacio: con '' o
        // '0000-00-00 00:00:00' no hay con que comparar, y en ese caso se deja
        // pasar. Es fallar abierto, y es deliberado -- una fila sin updated_at
        // no tiene una version mas nueva que defender.
        $actual = isset($fila['updated_at']) ? (string) $fila['updated_at'] : '';
        if ($actual === '0000-00-00 00:00:00') {
            $actual = '';
        }
        $conocido = isset($datos['legado_updated_at_conocido'])
            ? (string) $datos['legado_updated_at_conocido'] : '';
        if ($conocido === '0000-00-00 00:00:00') {
            $conocido = '';
        }
        unset($datos['legado_updated_at_conocido']);

        if ($actual !== '' && $conocido !== '' && strcmp($actual, $conocido) > 0) {
            // Gana el legado. No se escribe NADA. Se devuelven las columnas
            // permitidas para que Python guarde las dos versiones del
            // conflicto: la suya, si no la guarda ahi, se pierde -- el proximo
            // pull le trae esta y la pisa.
            $actuales = array();
            foreach ($columnas as $columna) {
                if (array_key_exists($columna, $fila)) {
                    $actuales[$columna] = $fila[$columna];
                }
            }
            $this->json(409, array(
                'ok'             => FALSE,
                'conflicto'      => TRUE,
                'updated_at'     => $actual,
                'datos_actuales' => $actuales,
            ));
        }

        // -- 4. El UPDATE, solo con lo permitido -----------------------------
        //
        // Lo que llega y no esta en la lista blanca se ignora EN SILENCIO, a
        // proposito: Python puede empezar a mandar un campo nuevo antes de que
        // este archivo se despliegue, y eso no debe romper el push entero.
        $cambios = array();
        foreach ($columnas as $columna) {
            if (array_key_exists($columna, $datos)) {
                $cambios[$columna] = $datos[$columna];
            }
        }
        if (!$cambios) {
            $this->json(400, array(
                'error'      => 'ningun campo actualizable en el body',
                'permitidos' => $columnas,
            ));
        }

        // El reloj es el de ESTE servidor. Mismo motivo que el `hasta` del
        // pull: un solo reloj y no dos que conciliar. Y ademas es el valor que
        // Python va a guardar en la replica y usar como
        // legado_updated_at_conocido del proximo push de esta unidad.
        $ahora = $this->db->query('SELECT NOW() AS ahora')->row()->ahora;
        $cambios['updated_at'] = $ahora;

        // El UPDATE y el registro de la key, en UNA transaccion. Sueltos
        // quedaria una ventana chica pero real -- fila actualizada, key sin
        // registrar -- que es el mismo agujero que la key viene a tapar, solo
        // que mas angosto.
        //
        // Transaccion MANUAL (trans_begin) y no trans_start(): hace falta
        // mirar trans_status() para distinguir el choque de key duplicada
        // -- que es una carrera legitima entre dos reintentos simultaneos, y
        // la resuelve la PRIMARY KEY -- de una falla de base de verdad.
        $this->db->trans_begin();

        $this->db->where('id', $id);
        $this->db->update('newstocks_cidef', $cambios);

        if ($idem_key !== '') {
            $this->db->insert('api_idempotency', array(
                'idem_key'   => $idem_key,
                'entidad'    => $entidad,
                'entidad_id' => $id,
                'updated_at' => $ahora,
            ));
        }

        if ($this->db->trans_status() === FALSE) {
            $this->db->trans_rollback();

            // Si la key ya estaba, otro intento del MISMO push gano la
            // carrera: el cambio esta aplicado y lo correcto es devolver su
            // respuesta, no un error.
            if ($idem_key !== '') {
                $consulta = $this->db->get_where('api_idempotency', array(
                    'idem_key' => $idem_key,
                    'entidad'  => $entidad,
                ));
                $previo = ($consulta === FALSE) ? FALSE : $consulta->row();
                if ($previo) {
                    $this->json(200, array(
                        'ok'          => TRUE,
                        'updated_at'  => $previo->updated_at,
                        'idempotente' => TRUE,
                    ));
                }
            }
            $this->json(500, array('error' => 'no se pudo actualizar la unidad'));
        }

        $this->db->trans_commit();

        $this->json(200, array('ok' => TRUE, 'updated_at' => $ahora));
    }
}
