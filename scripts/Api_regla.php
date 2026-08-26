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
 * Primera etapa: SOLO lectura de `newstocks_cidef`. No escribe nada. El
 * endpoint de escritura (push Python -> legado) viene despues, cuando el pull
 * este probado.
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
}
