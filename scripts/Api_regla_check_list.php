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
 * ESOS TRES COMPRUEBAN QUE EL ARCHIVO ESTE SANO. NO comprueban que sea el
 * correcto, y la diferencia costo medio dia el 2026-09-04: al ensamblar, la
 * version vieja de `columnas_que_acumulan` se quedo en lugar de la nueva. `php
 * -l` limpio, cero duplicados, las sondas en 401 y el push en 200 -- y las tres
 * listas de fallas pisandose en silencio.
 *
 * Un archivo con el metodo viejo esta perfectamente sano.
 *
 * POR ESO: todo bloque que REEMPLACE algo ya desplegado trae, arriba de todo,
 * su propia comprobacion DE CONTENIDO -- un grep que busque algo que solo la
 * version nueva tiene. La de sintaxis detecta el fallo ruidoso; solo la de
 * contenido detecta el mudo.
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


/* ===========================================================================
   BLOQUE K -- el PASO 2 del check list mecanico: las fallas
   ===========================================================================

   !!! ANTES DE NADA: ESTE BLOQUE REEMPLAZA `columnas_que_acumulan` !!!

   El bloque J trae ese mismo metodo con una sola clave. El K lo trae con dos.
   Si se pegan los DOS en la clase, PHP tira

       Fatal error: Cannot redeclare Api_regla::columnas_que_acumulan()

   y con eso se caen TODAS las rutas del controlador, no solo la nueva -- que
   es exactamente lo que paso con `columnas_permitidas` duplicada.

   Asi que, al desplegar:

     * si el bloque J YA ESTA puesto -> BORRAR su `columnas_que_acumulan`
       entero (desde su linea `private function columnas_que_acumulan($entidad)`
       hasta el `}` que la cierra) y dejar el de aca.
     * si el bloque J todavia NO esta -> poner el de aca y saltear el de alla.

   Para comprobarlo despues de subir, desde cPanel Terminal:

       grep -c "function columnas_que_acumulan" Api_regla.php     # tiene que dar 1
       grep -n "private function" Api_regla.php | \
           awk '{print $NF}' | sort | uniq -d                     # no tiene que imprimir nada
       php -l Api_regla.php

   ---------------------------------------------------------------------------

   El bloque G le dio a `check_list_mecanica` el verbo 'crear', que cubre el
   paso 1: la fila nace con sus 82 columnas y `estado='ABIERTO'`.

   El paso 2 es OTRA cosa. Cada falla que se carga hace un UPDATE sobre esa
   misma fila, y ese UPDATE no reemplaza: CONCATENA.

       $link_total = $link.' | '.$link_i_1;
       $obs_total  = $obs.' | '.$observacion;
       $modalidad_total = $modalidad_anterior.' | '.$modalidad;
       $cont_total = $contador + 1;

   Son 2.635 filas de 2.956 con al menos una falla cargada. No es un caso
   raro: es la salida del modulo.

   POR QUE UNA ENTIDAD NUEVA Y NO UN SEGUNDO VERBO EN LA MISMA
   -----------------------------------------------------------
   `check_list_mecanica` ya significa 'crear'. Meterle un segundo verbo a la
   misma clave obliga a mirar el cuerpo del pedido para saber que va a hacer,
   y a partir de ahi el nombre de la entidad ya no dice que operacion es.

   Una clave, un verbo. La entidad nueva se llama `check_list_mecanica_falla`
   -- el nombre dice que agrega UNA falla, que es exactamente lo que hace.

   LAS TRES COLUMNAS SE MUEVEN JUNTAS, Y POR ESO VA DEL LADO DEL SERVIDOR
   ---------------------------------------------------------------------
   `observacion`, `modalidad` y `link_unidades` son tres listas PARALELAS: la
   falla numero 3 es el tercer pedazo de las tres. Si se desalinean, la foto
   de una falla queda pegada al texto de otra -- y no hay error, solo un
   informe que miente.

   Concatenar del lado de Python seria leer las tres, pegarles lo nuevo y
   mandar el resultado. Dos cargas simultaneas sobre el mismo check list leen
   lo mismo y la segunda pisa a la primera: se pierde una falla ENTERA, con
   su foto.

   Con el CONCAT del lado del servidor las tres viajan en UNA sentencia, y
   MySQL la aplica sobre la fila bloqueada: dos cargas simultaneas quedan una
   detras de la otra y las tres listas siguen alineadas. Es el mismo criterio
   que `stock = stock - ?` en el descuento de combustible, y aca el argumento
   es mas fuerte todavia, porque lo que se pierde no es un numero sino la
   correspondencia entre tres columnas.

   Y `contador` NO se manda como valor: se INCREMENTA. Si Python mandara el
   numero que calculo contra SU fila, y el legado tuviera otro -- porque
   alguien cargo una falla desde el sistema viejo durante el mes en paralelo
   --, el contador quedaria mal y las fotos siguientes se llamarian igual que
   las anteriores.
   =========================================================================== */

    /**
     * En `mapa_entidades()`, junto a las dos del bloque G.
     */
            'check_list_mecanica_falla' => array(
                'tabla'    => 'check_list_mecanica',
                'verbo'    => 'actualizar',
                'columnas' => array(
                    // Las tres listas paralelas de la PRIMERA pasada.
                    'observacion', 'modalidad', 'link_unidades',
                    // Y las tres de un check list REABIERTO. Son 68 filas
                    // historicas, en TODOS los meses desde que hay datos y 40
                    // de ellas en 2026 -- no es una rama muerta.
                    //
                    // Ojo si alguna vez se vuelve a medir: hoy 67 de esas 68
                    // filas dicen `estado='CERRADO'`, porque la rama se elige
                    // por el estado que la fila tenia al subir la foto y
                    // despues el estado sigue caminando. Contarlas con
                    // `WHERE estado='REABIERTO'` da 1 y hace concluir que
                    // esto no se usa.
                    'fallas_adicionales', 'modalidad_adicional',
                    'fotos_adicionales',
                    // El contador. Va en la lista blanca para pasar el
                    // filtro, pero NO se escribe con el valor recibido: ver
                    // `columnas_que_suman` mas abajo.
                    'contador',
                ),
            ),

/* ---------------------------------------------------------------------------
   Y las dos listas de verbos por columna.

   `columnas_que_acumulan` YA EXISTE (bloque J). Se le AGREGA una clave; el
   metodo entero queda como esto, no se agrega un segundo metodo.
   --------------------------------------------------------------------------- */

    private function columnas_que_acumulan($entidad)
    {
        $mapa = array(
            // El historial de daños de TODAS las pasadas del VIN. Ver el
            // encabezado del bloque J: pisarla borra historia.
            'unidades' => array('observaciones'),

            // Las tres listas paralelas de fallas, en sus dos versiones. Ver
            // el encabezado del bloque K: si se desalinean, la foto de una
            // falla queda pegada al texto de otra.
            'check_list_mecanica_falla' => array(
                'observacion', 'modalidad', 'link_unidades',
                'fallas_adicionales', 'modalidad_adicional', 'fotos_adicionales',
            ),
        );
        return isset($mapa[$entidad]) ? $mapa[$entidad] : array();
    }

    /**
     * Metodo NUEVO. Columnas que se SUMAN en vez de escribirse.
     *
     * Es la tercera forma de escribir una columna, despues de reemplazar y
     * concatenar, y existe por la misma razon que las otras dos: la operacion
     * correcta se expresa en SQL, no en la aplicacion. Un contador que el
     * cliente calcula es un contador que dos clientes calculan igual y pisan.
     */
    private function columnas_que_suman($entidad)
    {
        $mapa = array(
            'check_list_mecanica_falla' => array('contador'),
        );
        return isset($mapa[$entidad]) ? $mapa[$entidad] : array();
    }

/* ---------------------------------------------------------------------------
   Y el bucle de `actualizar()`.

   QUE BORRAR, EXACTAMENTE. Lo que dejo el bloque J: desde la linea

       $cambios = array();

   hasta la linea

       unset($cambios['__acumulado']);          // no es una columna real

   inclusive. Se reemplaza por lo de abajo, que es lo mismo con una rama mas.
   El `if (!$cambios && !$this->db->qb_set)` que viene despues NO se toca:
   `qb_set` tambien recoge lo que agrega la suma, asi que un cuerpo que traiga
   solo `contador` sigue pasando.
   --------------------------------------------------------------------------- */

        $cambios  = array();
        $acumulan = $this->columnas_que_acumulan($entidad);
        $suman    = $this->columnas_que_suman($entidad);

        foreach ($datos as $columna => $valor) {
            if (!in_array($columna, $columnas, TRUE)) {
                continue;                        // la lista blanca, igual que antes
            }

            if (in_array($columna, $acumulan, TRUE)) {
                // ACUMULA. El separador es UN espacio para `observaciones` de
                // `unidades` --el `.= ' '.` del legado-- y ' | ' para las
                // listas de fallas. La diferencia importa: `unidades` guarda
                // un texto corrido y las fallas guardan una lista que despues
                // se parte por '|'. Un separador equivocado no falla: arma una
                // lista con la cantidad de elementos equivocada.
                if ($valor === '' || $valor === NULL) {
                    continue;                    // nada que agregar
                }
                $sep = ($entidad === 'unidades') ? ' ' : ' | ';
                // El CASE y no un CONCAT pelado: con la columna vacia, un
                // CONCAT dejaria el separador ADELANTE del primer valor y la
                // lista tendria un elemento vacio al principio. El legado
                // resuelve lo mismo con su `if($link == NULL)`.
                $this->db->set(
                    $columna,
                    "CASE WHEN `{$columna}` IS NULL OR `{$columna}` = '' "
                        . "THEN " . $this->db->escape($valor) . " "
                        . "ELSE CONCAT(`{$columna}`, "
                        . $this->db->escape($sep) . ", "
                        . $this->db->escape($valor) . ") END",
                    FALSE);
                $cambios['__acumulado'] = TRUE;
                continue;
            }

            if (in_array($columna, $suman, TRUE)) {
                // SUMA. El valor recibido es CUANTO sumar, no el total.
                $n = (int) $valor;
                $this->db->set(
                    $columna,
                    "COALESCE(`{$columna}`, 0) + " . $n,
                    FALSE);
                $cambios['__acumulado'] = TRUE;
                continue;
            }

            $cambios[$columna] = $valor;
        }
        unset($cambios['__acumulado']);          // no es una columna real

/* ---------------------------------------------------------------------------
   Y la ruta, en routes.php, junto a las dos del bloque H:

       $route['api_regla/check_list_mecanica_falla/(:num)']['PUT'] =
           'api_regla/actualizar/check_list_mecanica_falla/$1';

   CORREGIDA el 2026-09-02: la primera version decia POST y sin id, y habria
   dado 405 en el primer push real. `ClientePushLegadoHTTP.actualizar()` arma
   `PUT /api_regla/<ruta>/<legado_id>` para TODA entidad de verbo
   'actualizar' -- el id va en la URL, no en el cuerpo, que es lo que la
   distingue de `crear`.

   Va DESPUES de la de `check_list_mecanica` para que el prefijo mas corto no
   se coma al mas largo.
   --------------------------------------------------------------------------- */


/* ===========================================================================
   BLOQUE L -- leer UNA fila de check list, para poder verificar de verdad
   ===========================================================================

   POR QUE HACE FALTA

   Los bloques G a K escriben. No hay forma de LEER lo que quedó escrito: el
   unico GET que existe es `cambios/unidades`, y esta clavado en esa entidad --
   ademas de que su marca de agua sale de `updated_at`/`created_at`, columnas
   que `check_list_mecanica` no tiene.

   Sin esto, la verificacion del push termina en "el endpoint devolvio 201" y
   hay que ir a phpMyAdmin a mirar si las tres listas quedaron alineadas. Eso
   convierte una verificacion repetible en un favor que alguien tiene que
   hacer, y las verificaciones que dependen de un favor se dejan de hacer.

   Las tres cosas que hay que poder afirmar sobre la fila, y ninguna se puede
   hoy:

     - `ignoradas` vacio         (esa si vuelve en el 201, ver el bloque I)
     - las tres listas de fallas con la misma cantidad de elementos y el
       n-esimo de cada una correspondiendose
     - `contador` = 2 despues de dos vueltas, o sea que el endpoint lo
       INCREMENTO en vez de escribir lo que mando Python

   ES DE SOLO LECTURA Y ESTRECHO A PROPOSITO

   Un id, una fila, y nada de listar ni filtrar. No es una API de consulta: es
   la mirilla que hace verificable lo que los otros bloques escriben. Si alguna
   vez hace falta listar check lists, eso es otro endpoint con su propia
   decision sobre paginado y sobre que columnas salen.

   Va detras de la API key como todo lo demas. La fila no tiene datos
   personales mas alla del nombre del encargado, que es un empleado y ya
   aparece en el resto de la API.
   =========================================================================== */

// ---------------------------------------------------------------------------
// GET /api_regla/check_list_mecanica/<id>
//     GET /api_regla/check_list/<id>
// ---------------------------------------------------------------------------
//
//   200 {"ok":true,"fila":{...}}
//   404 {"error":"no existe ..."}
//
// Metodo NUEVO. Se agrega junto a los otros publicos, antes del `}` que cierra
// la clase. NO reemplaza nada.
public function leer_fila($entidad = null, $id = null)
{
    if (strtolower($this->input->method()) !== 'get') {
        $this->json(405, array('error' => 'metodo no permitido'));
    }
    $this->exigir_api_key();

    // Solo las entidades de verbo 'crear'. `unidades` NO se lee por aca: para
    // eso esta `cambios/unidades`, que ademas pagina y respeta la marca de
    // agua. Dos puertas a la misma tabla es una que se olvida de arreglar.
    $mapa = $this->mapa_entidades();
    if (!isset($mapa[$entidad]) || $mapa[$entidad]['verbo'] !== 'crear') {
        $this->json(404, array(
            'error' => 'entidad no soportada para leer: ' . $entidad));
    }

    $id = (int) $id;
    if ($id <= 0) {
        $this->json(400, array('error' => 'id invalido'));
    }

    $tabla = $mapa[$entidad]['tabla'];
    $consulta = $this->db->get_where($tabla, array('id' => $id), 1);
    if ($consulta === FALSE) {
        $this->json(500, array('error' => 'no se pudo leer ' . $tabla));
    }
    $fila = $consulta->row_array();
    if (!$fila) {
        $this->json(404, array(
            'error' => 'no existe ' . $entidad . ' ' . $id));
    }

    $this->json(200, array('ok' => TRUE, 'fila' => $fila));
}

/* ---------------------------------------------------------------------------
   Y las dos rutas, en routes.php.

   VAN ANTES que las de POST de los bloques G/H. CodeIgniter resuelve por
   ORDEN, y `api_regla/check_list_mecanica` a secas ya esta mapeada al POST de
   `crear_fila`: si estas quedaran despues, el segmento numerico se comeria
   como parametro de aquella y este metodo no se llamaria nunca.

       $route['api_regla/check_list_mecanica/(:num)']['GET'] =
           'api_regla/leer_fila/check_list_mecanica/$1';
       $route['api_regla/check_list/(:num)']['GET'] =
           'api_regla/leer_fila/check_list/$1';

   COMPROBAR DESPUES DE SUBIR, desde cPanel Terminal:

       php -l ~/public_html/application/controllers/Api_regla.php
       grep -o "function [a-z_]*" .../Api_regla.php | sort | uniq -d   -> vacio

   Y una sonda que no escribe nada:

       curl -s -o /dev/null -w '%{http_code}\n' \
         -H "X-API-Key: $CLAVE" \
         https://claude.logautos.cl/api_regla/check_list_mecanica/999999999
       -> 404

       curl -s -o /dev/null -w '%{http_code}\n' \
         https://claude.logautos.cl/api_regla/check_list_mecanica/999999999
       -> 401
   --------------------------------------------------------------------------- */


/* ===========================================================================
   BLOQUE M -- el `qb_set` del bloque J. YA DESPLEGADO el 2026-09-02.
   ===========================================================================

   Se deja escrito porque el archivo tiene que contar que se cambio y por que,
   no solo lo que falta hacer.

   EL BUG

   El bloque J dejo esta linea:

       if (!$cambios && !$this->db->qb_set) {

   `qb_set` es una propiedad PROTECTED de `CI_DB_mysqli_driver`, asi que leerla
   desde el controlador es

       Error: Cannot access protected property CI_DB_mysqli_driver::$qb_set

   -- un 500 fatal.

   POR QUE NO SE NOTO AL DESPLEGAR, y por que reventaba justo en el paso 2

   PHP corta el `&&` cuando el primer operando es falso. `!$cambios` es falso
   siempre que haya UNA columna normal en el cuerpo, asi que ningun push del IT
   ni de la PDI llegaba a tocar `qb_set`.

   El paso 2 del check list mecanico es el unico caso donde TODAS las columnas
   del cuerpo son acumulativas o sumativas: las siete pasan por `set()` y
   ninguna entra en `$cambios`. Entonces `$cambios` queda vacio, PHP NO corta,
   y revienta. Era el 500 entero -- no habia un segundo bug debajo.

   LA OTRA VICTIMA, que estuvo rota varios dias en silencio

   La rama "ningun campo actualizable en el body" es exactamente la que usa la
   SONDA 4 de `scripts/verificar_push_produccion.py`: manda un timestamp del
   ano 2000 y NINGUN campo, y espera 409 o 400. Es la unica sonda que prueba el
   locking optimista contra la base real sin escribir. Desde que se desplego el
   bloque J devolvia 500, o sea que esa verificacion no estaba corriendo.

   Un bug en el camino de error solo se ve cuando algo falla, y lo que mira si
   los errores fallan bien es la herramienta de verificacion -- que era
   justamente la que estaba rota.

   EL ARREGLO, tal como quedo desplegado

   El bucle lleva su propia bandera en vez de espiar el estado interno del
   query builder:

       $hubo_sql = FALSE;                        // arriba del foreach

       ... adentro, donde antes decia $cambios['__acumulado'] = TRUE;
       $hubo_sql = TRUE;

       if (!$cambios && !$hubo_sql) {            // en vez de !$this->db->qb_set

   y el `unset($cambios['__acumulado']);` se fue: ya no hay nada que sacar.

   Ademas de que compila, no depende de como CodeIgniter guarde su estado
   interno -- que es un detalle de implementacion de una libreria ajena y puede
   cambiar sin avisar.
   =========================================================================== */


/* ===========================================================================
   LO QUE **NO** HAY QUE TOCAR, y por que esta escrito aca
   ===========================================================================

   `actualizar()` YA RESUELVE LA TABLA POR ENTIDAD. Hace
   `$tabla = $this->tabla_de($entidad)` y lo usa en el SELECT y en el UPDATE.
   Quedo completo cuando se armo el archivo con los bloques E y F, porque el
   bloque A habia quedado a medio aplicar.

   El 2026-09-02 esta spec llego a decir lo contrario -- que el metodo estaba
   clavado en `newstocks_cidef` y que un PUT de falla iba a escribirle a una
   unidad ajena. ERA FALSO, y la correccion importa mas que el error:

     * La "evidencia" era el mensaje del 404, que dice "unidad no encontrada"
       para CUALQUIER entidad. Es texto viejo que quedo asi a proposito desde
       el bloque A; no dice nada sobre que tabla se consulto.

     * Y el metodo se leyo del BLOQUE QUE LO ESPECIFICA en este archivo, no del
       `Api_regla.php` que esta corriendo en el servidor. La spec y el
       desplegado se habian separado, y gano la spec.

   > CUANDO ALGO FALLA CONTRA PRODUCCION, LA FUENTE ES EL ARCHIVO DESPLEGADO.
   > No el documento que dice como deberia ser. La spec describe una intencion;
   > el servidor ejecuta un archivo. Cuando no coinciden, el que tiene razon
   > sobre lo que paso es el archivo.

   Para leerlo sin bajarlo, desde cPanel Terminal:

       sed -n '/function actualizar/,/^    }/p' \
           ~/public_html/application/controllers/Api_regla.php
   =========================================================================== */


/* ===========================================================================
   BLOQUE N -- `updated_at` solo en las tablas que lo tienen
   ===========================================================================

   ES UNA LINEA. Es lo que falta para que el paso 2 del check list mecanico
   funcione.

   EL SINTOMA

   Con el bloque M ya desplegado, `PUT /api_regla/check_list_mecanica_falla/<id>`
   sigue devolviendo

       500 {"error":"no se pudo actualizar la unidad"}

   LA MEDICION QUE LO SEÑALA

   Tres pedidos sobre la MISMA fila, cambiando solo el cuerpo:

       {"contador": 1}              -> 500      (columna SUMATIVA)
       {"observacion": "SONDA A"}   -> 500      (columna ACUMULATIVA)
       {"estado": "ABIERTO"}        -> 400      (fuera de la lista blanca; correcto)

   Las dos que 500 son exactamente las que pasan por `$this->db->set()` y NO
   por `$cambios`. El 400 de la tercera prueba que el `if (!$cambios &&
   !$hubo_sql)` del bloque M quedo bien.

   O sea: cuando TODAS las columnas del cuerpo son acumulativas o sumativas,
   `$cambios` llega vacio a la linea

       $cambios['updated_at'] = $ahora;

   y esa linea lo deja con UNA sola clave: `updated_at`. El UPDATE sale con una
   columna que la tabla no tiene.

       check_list_mecanica   ->  NO tiene updated_at   (ni created_at)
       check_list            ->  NO tiene updated_at
       newstocks_cidef       ->  SI

   `unidades` es la unica entidad cuya tabla la tiene, y era la unica que
   existia cuando esa linea se escribio.

   POR QUE NO SE VIO ANTES, y por que el paso 1 si anda

   El paso 1 va por `crear_fila`, que es otro metodo y no toca `updated_at`.
   Y en `actualizar()`, hasta el bloque K todas las entidades eran `unidades`.
   El check list mecanico es la primera que actualiza otra tabla, y ademas la
   primera cuyo cuerpo puede ser 100% columnas de `set()`. Las dos condiciones
   juntas hacen falta para llegar a este error.

   LO QUE QUEDA POR CONFIRMAR, y como

   La evidencia es fuerte pero indirecta: se dedujo del esquema y de que las
   dos ramas de `set()` fallan igual. Lo que lo cierra del todo es el error de
   MySQL, que CodeIgniter escribe en su log:

       tail -50 ~/public_html/application/logs/log-$(date +%Y-%m-%d).php

   Tiene que decir algo como

       Unknown column 'updated_at' in 'field list'

   Si dijera otra cosa -- un error de sintaxis en el CONCAT o en el CASE --,
   entonces el problema es la SQL del bloque K y no esta linea. Vale mirarlo
   antes de dar esto por cerrado.
   =========================================================================== */

/* ---------------------------------------------------------------------------
   EL CAMBIO. En `actualizar()`, la linea

       $cambios['updated_at'] = $ahora;

   se reemplaza por lo de abajo. Es la unica linea que se toca.
   --------------------------------------------------------------------------- */

        // Solo si la tabla la tiene. `$fila` ya vino de la base unas lineas mas
        // arriba, asi que sus claves SON las columnas reales de esta tabla --
        // no hace falta preguntarle al esquema ni mantener una lista.
        //
        // `check_list_mecanica` y `check_list` no tienen `updated_at`: no la
        // necesitan, porque su locking no es optimista sino la
        // Idempotency-Key. Escribirsela igual es un "Unknown column" que tumba
        // el UPDATE entero -- y el mensaje que sale es el generico
        // 'no se pudo actualizar la unidad', que no dice nada de columnas.
        if (array_key_exists('updated_at', $fila)) {
            $cambios['updated_at'] = $ahora;
        }

/* ---------------------------------------------------------------------------
   DESPUES DE SUBIR, dos sondas. La primera NO escribe (id inexistente); la
   segunda escribe sobre la fila 2962, que es de la unidad 66505 de PRUEBA.

       # sigue dando 404 y no rompio nada
       curl -s -X PUT -H "X-API-Key: $CLAVE" -H "Content-Type: application/json" \
            -H "Idempotency-Key: $(uuidgen)" \
            -d '{"observacion":"SONDA","legado_updated_at_conocido":""}' \
            https://claude.logautos.cl/api_regla/check_list_mecanica_falla/999999999

       # ahora tiene que dar 200
       curl -s -X PUT -H "X-API-Key: $CLAVE" -H "Content-Type: application/json" \
            -H "Idempotency-Key: $(uuidgen)" \
            -d '{"contador":1,"legado_updated_at_conocido":""}' \
            https://claude.logautos.cl/api_regla/check_list_mecanica_falla/2962

   Y con eso corre entero:

       LEGADO_API_KEY=... python scripts/verificar_check_mecanico_produccion.py --escribir
   --------------------------------------------------------------------------- */


/* ===========================================================================
   NOTA SOBRE EL BLOQUE M QUE SE RETIRO
   ===========================================================================

   El bloque M original traia TRES arreglos a `actualizar()`. El primero --
   "esta clavado en newstocks_cidef" -- era FALSO, y con razon se descarto
   entero.

   Pero traia dos que no lo eran, y se fueron con el: el `qb_set` (que ya se
   arreglo por otro lado) y este `updated_at`. Retirar un bloque completo
   porque uno de sus items esta mal se lleva puesto lo que si servia.

   > Cuando se retira una correccion equivocada, hay que mirar si arrastra
   > alguna que no lo era. Un bloque es una unidad de despliegue, no una
   > unidad de verdad.
   =========================================================================== */


/* ===========================================================================
   BLOQUE O -- a `columnas_que_acumulan` le falta la clave del check list
   ===========================================================================

   ES UN METODO. Es lo ultimo que falta para que el paso 2 cierre.

   LA MEDICION, tres pedidos contra produccion y ninguna suposicion:

       unidades / observaciones                  ->  ACUMULA   (crecio 16 chars)
       check_list_mecanica_falla / contador      ->  SUMA      (0 -> 2)
       check_list_mecanica_falla / observacion   ->  SE PISA

   Los dos primeros dicen que el bucle del bloque K esta desplegado y entero:
   `contador` solo puede sumar por la rama `$suman`, y `observaciones` solo
   puede crecer por la rama `$acumulan`. Las dos ramas viven en el mismo
   `foreach`.

   Entonces la unica explicacion que queda es que

       $this->columnas_que_acumulan('check_list_mecanica_falla')

   devuelva un array vacio -- o sea que el metodo desplegado es el del BLOQUE J,
   que tiene la clave `unidades` y nada mas. El del bloque K, con las dos
   claves, no llego.

   POR QUE PASO, Y POR QUE NO SE NOTO

   El bloque K arranca avisando que REEMPLAZA `columnas_que_acumulan` y que
   pegar los dos da `Cannot redeclare`. Lo que ocurrio fue lo contrario y es
   peor: no se pego ninguno de los dos encima del otro, quedo el de J, y no
   hubo ningun error.

   El fatal que yo anticipaba se ve al toque -- se caen todas las rutas. Esto
   no se ve nunca: el endpoint responde 200, la cola marca resuelta, y la
   columna se pisa en silencio. Es el mismo modo de falla que la lista blanca,
   con otro disfraz.

   POR QUE `contador` FUNCIONO IGUAL

   Porque `columnas_que_suman` es un metodo NUEVO: no tenia una version previa
   con la que competir, asi que entro completo. La colision solo afecta a los
   metodos que un bloque anterior ya habia definido.
   =========================================================================== */

/* ---------------------------------------------------------------------------
   EL CAMBIO. Buscar en `Api_regla.php`

       private function columnas_que_acumulan($entidad)

   y reemplazar ESE METODO ENTERO -- desde esa linea hasta el `}` que la cierra
   -- por lo de abajo. Es lo unico que se toca; el `foreach` de `actualizar()`
   ya esta bien.

   COMO SABER SI HACE FALTA, ANTES de tocar nada:

       grep -A6 "function columnas_que_acumulan" \
            ~/public_html/application/controllers/Api_regla.php

   Si ahi aparece `check_list_mecanica_falla`, ya esta puesto y este bloque no
   hace falta. Si solo aparece `unidades`, es este.
   --------------------------------------------------------------------------- */

    private function columnas_que_acumulan($entidad)
    {
        $mapa = array(
            // El historial de daños de TODAS las pasadas del VIN. Pisarla no
            // pierde un valor: pierde historia, y no se nota porque la columna
            // sigue teniendo texto.
            'unidades' => array('observaciones'),

            // Las tres listas PARALELAS de fallas del check list mecanico, en
            // sus dos versiones -- la primera pasada y la de un check list
            // REABIERTO.
            //
            // Son paralelas: la falla numero 3 es el tercer pedazo de las
            // tres. Si esta clave falta, las tres se PISAN y queda solamente
            // la ultima falla cargada, con 200 y sin error. Medido en
            // produccion el 2026-09-02: dos fallas cargadas, una sola en la
            // fila.
            'check_list_mecanica_falla' => array(
                'observacion', 'modalidad', 'link_unidades',
                'fallas_adicionales', 'modalidad_adicional', 'fotos_adicionales',
            ),
        );
        return isset($mapa[$entidad]) ? $mapa[$entidad] : array();
    }

/* ---------------------------------------------------------------------------
   DESPUES DE SUBIR

       php -l ~/public_html/application/controllers/Api_regla.php
       grep -c "function columnas_que_acumulan" .../Api_regla.php    -> 1
       grep -c "check_list_mecanica_falla" .../Api_regla.php         -> 3
           (el mapa de entidades, este mapa, y la ruta)

   Y la prueba de verdad, que no depende de leer nada:

       LEGADO_API_KEY=... python scripts/verificar_check_mecanico_produccion.py --escribir

   `observacion` tiene que quedar en

       EXTINTOR VENCIDO | TAPIZ MANCHADO

   y no en `TAPIZ MANCHADO` solo.
   --------------------------------------------------------------------------- */


/* ===========================================================================
   LO QUE ESTE BLOQUE DEJA COMO REGLA
   ===========================================================================

   Van dos veces que un bloque entero se aplica a medias o se retira a medias:

     * el bloque M traia tres arreglos, uno falso y dos buenos, y al descartarlo
       por el falso se fueron los dos buenos con el;
     * el bloque K reemplazaba un metodo que el bloque J ya habia definido, y
       de los dos quedo el viejo -- en silencio.

   > UN BLOQUE ES UNA UNIDAD DE DESPLIEGUE, NO UNA UNIDAD DE VERDAD.
   > Cuando un item de un bloque se cae, se cae ESE ITEM, no el bloque. Y
   > cuando un bloque reemplaza algo que otro bloque puso, el que reemplaza
   > tiene que decir COMO COMPROBAR cual quedo -- porque la version equivocada
   > no da error, responde 200.

   De ahi que cada bloque que toque algo ya desplegado lleve ahora su `grep` de
   comprobacion arriba, y no solo la instruccion de que reemplazar.
   =========================================================================== */
