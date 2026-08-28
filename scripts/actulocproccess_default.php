<?php if (!defined('BASEPATH')) exit('No direct script access allowed');

/**
 * actulocproccess_default.php -- el `default:` que le falta a los cuatro
 * switch de calle, mas el `else` que le falta a la cadena de patios.
 *
 * NO VA EN ESTE REPOSITORIO NI SE INCLUYE. Es un parche para copiar a mano
 * dentro de:
 *
 *     application/controllers/Pedido.php  ->  function actulocproccess()
 *
 * Son CINCO inserciones, todas del mismo bloque salvo la ultima. No se
 * modifica ni se borra ninguna linea existente.
 *
 * `php -l Pedido.php` antes de subir. En la maquina de desarrollo no hay php,
 * asi que este archivo nunca se linteo local.
 *
 *
 * QUE ESTA ROTO HOY
 * =================
 *
 * `actulocproccess()` resuelve la ubicacion en dos etapas. Primero una cadena
 * `if ($calle == 'Dyp') ... elseif ($calle == 'Pdi_2')` que atiende las calles
 * de PROCESO; cada una de esas ramas termina en `redirect()`, que en CI3 hace
 * exit, asi que la etapa siguiente no las ve. Lo que sobrevive cae en la
 * segunda cadena, `if ($patio == "PATIO 1") ... elseif ($patio == "PATIO 4"
 * || ...)`, con un `switch ($calle)` adentro de cada rama.
 *
 * NINGUNO DE LOS CUATRO `switch` TIENE `default:`. Una calle que el menu
 * ofrece pero el switch no contempla no entra a ningun `case`, no ejecuta
 * ningun `actuestado()` ni `registromov()`, y sale del switch sin haber
 * escrito nada. Pero abajo del switch esta esto, que corre igual:
 *
 *     if ($result > 0) { ... 'EL VIN FUE ACTUALIZADO, PATIO Y CALLE CORRECTOS' }
 *     else             { ... 'EL VIN NO FUE ACTUALIZADO, UNIDAD DESPACHADA'   }
 *
 * O sea que el operario recibe una de dos explicaciones y las dos son falsas:
 * o "quedo actualizado" cuando no se escribio nada, o "unidad despachada"
 * cuando la unidad no esta despachada. En los dos casos la unidad se queda
 * fisicamente donde el operario la dejo y el sistema sigue creyendo que esta
 * donde estaba.
 *
 * EL CASO CONCRETO ES LA CALLE `X`. `modelos()` la ofrece en PATIO 3, 4, 5, 6,
 * 7, 8 y 9; los switch de PATIO 3 y de PATIO 4-9 saltan de la W a la Y. Tiene
 * 0 filas en todo el historico, lo que no prueba que nadie la haya elegido:
 * prueba que si alguien la eligio, no se guardo.
 *
 * Pero el agujero no es la X. Es que el switch no tiene piso, asi que
 * cualquier calle que se agregue al menu manana y se olvide en el switch entra
 * por el mismo lugar. El `default:` cierra la clase de error, no el caso.
 *
 *
 * POR QUE `redirect()` Y NO SOLO `flashdata`
 * ==========================================
 *
 * Porque el `if ($result > 0)` de abajo del switch pisaria el mensaje. Con
 * `break;` a secas, `$result` queda sin definir en ese camino, PHP lo evalua
 * como falso y el operario termina viendo 'UNIDAD DESPACHADA' en vez del
 * mensaje del `default`. El `redirect()` corta ahi mismo -- y es exactamente
 * lo que ya hacen las ramas de la primera cadena, asi que no introduce una
 * forma nueva de salir de la funcion.
 *
 * NO SE ESCRIBE NADA, a proposito. La calle no contemplada podria "adivinarse"
 * mandandola igual, y seria peor: `calle` es la ubicacion fisica y de ahi
 * salen los reportes de patio. Que no se escriba y se avise es el
 * comportamiento correcto; lo que estaba mal era no avisar.
 */


// ---------------------------------------------------------------------------
// BLOQUE A -- va en los CUATRO switch, como ultimo caso, JUSTO ANTES de la
// llave que cierra el switch.
//
// Lineas de referencia sobre el Pedido.php desplegado al 2026-08-27. Insertar
// de ABAJO HACIA ARRIBA para que los numeros de las de arriba no se corran:
//
//     switch de PATIO 4-9   ->  antes de la linea 11648
//     switch de PATIO 3     ->  antes de la linea 11391
//     switch de PATIO 2     ->  antes de la linea 11251
//     switch de PATIO 1     ->  antes de la linea 10815
//
// Las cuatro lineas son la misma: una llave `}` sola, seguida de `if($result >
// 0)`. Si no coinciden los numeros, ese es el patron a buscar.
// ---------------------------------------------------------------------------

                                        default :
                                                // La calle no esta contemplada
                                                // para este patio. No se
                                                // escribe: `calle` es la
                                                // ubicacion fisica y una
                                                // ubicacion inventada es peor
                                                // que ninguna.
                                                //
                                                // El redirect corta antes del
                                                // `if($result > 0)` de abajo,
                                                // que si no pisaria este
                                                // mensaje con 'UNIDAD
                                                // DESPACHADA'.
                                                $this->session->set_flashdata(
                                                    'error',
                                                    'LA CALLE "' . $calle . '" NO EXISTE EN ' . $patio .
                                                    '. NO SE GUARDO NADA. ELEGI UNA CALLE DE LA LISTA O AVISA A SISTEMAS.'
                                                );
                                                redirect('pedido/actuloc');
                                                break;


// ---------------------------------------------------------------------------
// BLOQUE B -- el `else` de la cadena de patios.
//
// Va DESPUES de la llave que cierra `elseif ($patio == "PATIO 4" || ...)` y
// ANTES de `redirect('pedido/actuloc');`. En el archivo desplegado, entre las
// lineas 11657 y 11658.
//
// El mismo agujero un nivel mas arriba: si `$patio` no es ninguno de los
// nueve -- vacio, o una de las variantes sucias que hay en los datos
// ('PATIO 4 B', 'PATIO 4-A', '2') -- no entra a ninguna rama, no se escribe
// nada y se cae al redirect SIN NINGUN MENSAJE. El operario ve la pantalla
// limpia y no tiene forma de saber que no paso nada.
// ---------------------------------------------------------------------------

                        else
                        {
                            $this->session->set_flashdata(
                                'error',
                                'EL PATIO "' . $patio . '" NO ESTA CONFIGURADO. NO SE GUARDO NADA.'
                            );
                        }
