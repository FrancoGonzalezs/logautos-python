<?php
/*
================================================================================
 BLOQUE IT -- lo unico que el port del IT necesita del lado PHP
 Medido contra:  C:\Regla_Python\produccion\Pedido.php  (bajado el 2026-09-08)
                 y la lista blanca del endpoint, sondeada EN VIVO el 2026-09-08
================================================================================

SON DOS EDICIONES, LAS DOS ADITIVAS. Ningun metodo existente se reemplaza, asi
que no aplica el modo de falla mudo del bloque K -- no hay "cual de las dos
versiones quedo".

  1. `destino_it` en la lista blanca de `newstocks_cidef`   -> Api_regla.php
  2. la entidad `it` en el endpoint de subida de fotos      -> Api_regla_subir_foto.php

Regla Python YA MANDA las dos cosas. Hasta que esto suba:

  - `destino_it` viaja y el endpoint la IGNORA -- responde 200 y la reporta en
    `ignoradas`, que es exactamente para lo que sirve ese campo. La revision se
    guarda igual; lo que falta es esa columna del otro lado.
  - las fotos del IT dan 404 al empujarse, que es ruidoso y por lo tanto esta
    bien. Quedan guardadas en Regla Python y en el correo, que es donde el
    cliente las ve.

O sea: **esto no bloquea el uso del IT**. Bloquea que Regla PHP tenga el
destino y las fotos.

--------------------------------------------------------------------------------
EDICION 1 -- `destino_it` en la lista blanca
--------------------------------------------------------------------------------

MEDIDO EN VIVO, no leido: un PUT con `legado_updated_at_conocido` del año 2000
devuelve 409 con `datos_actuales`, que es el eco de la lista blanca. Son 31
columnas y `destino_it` NO esta entre ellas. `estado_it` y `observacion_it` SI.

En `Api_regla.php`, dentro de `columnas_permitidas()`, en el arreglo de
`newstocks_cidef`, agregar UNA linea al final de la lista:

        'destino_it',

COMO SE COMPRUEBA (y no alcanza con `php -l`):

    grep -c "destino_it" ~/public_html/application/controllers/Api_regla.php
        --> tiene que dar 1

Y despues, desde Regla Python, el push de un IT tiene que volver con
`ignoradas` VACIO. Si `destino_it` sigue apareciendo ahi, quedo el archivo
viejo -- y no hay ningun otro sintoma, porque la respuesta es 200 igual.

OJO: la columna `destino_it` tiene que EXISTIR en `newstocks_cidef`. Se
confirmo que produccion la escribe (produccion/Pedido.php:9475), asi que
existe; pero si el despliegue del 2026-09-02 se hizo sin correr el ALTER, esto
daria `Unknown column 'destino_it' in 'field list'` y tumbaria el PUT de la
unidad. Comprobar antes, en phpMyAdmin:

    SHOW COLUMNS FROM newstocks_cidef LIKE 'destino_it';

    -- si devuelve CERO filas, primero:
    -- ALTER TABLE newstocks_cidef ADD COLUMN destino_it VARCHAR(10) NULL;

--------------------------------------------------------------------------------
EDICION 2 -- la entidad `it` en el endpoint de subida de fotos
--------------------------------------------------------------------------------

En `Api_regla_subir_foto.php` hay DOS listas blancas y hay que tocar las dos.

LA CARPETA DEL IT ES LA UNICA CON SUBCARPETA POR VIN, y ahi esta el cuidado que
justifica este bloque. Regla PHP guarda en `assets/images/it/{VIN}/`, o sea que
la carpeta depende del dato -- que es justo lo que la regla del endpoint
prohibe ("la carpeta la decide la entidad, NUNCA el cuerpo").

La salida es partirlo en dos: el PREFIJO lo pone la entidad y es fijo (`it/`),
y el VIN viene como campo pero **el servidor lo valida contra
`newstocks_cidef` antes de usarlo**. Un VIN que no existe no crea carpeta.

Asi, lo que viaja en el cuerpo no es una ruta: es un identificador que el
servidor resuelve. Un `../` o un `it/../../` no llegan a ningun lado porque el
VIN se compara contra la base, no se concatena a ciegas.

(a) En `carpeta_de_foto()`, agregar al mapa:

            'it'                  => 'assets/images/it/',

(b) En `rotulos_de_foto()`, agregar:

            'it'                  => array('IT'),

(c) Y en `subir_foto()`, DESPUES de resolver `$carpeta` y ANTES de mover el
    archivo, el bloque que valida el VIN y arma la subcarpeta:

--------------------------------------------------------------------------------
*/

    // ---- IT: subcarpeta por VIN, validada contra la base -------------------
    //
    // Va DESPUES de `$carpeta = $this->carpeta_de_foto($entidad);` y ANTES de
    // cualquier `move_uploaded_file`.
    //
    // El VIN NO se concatena a ciegas. Se limpia a alfanumerico, se exige el
    // largo de 17, y se busca en `newstocks_cidef`: si no esta, 404 y no se
    // crea ninguna carpeta. Lo que viaja en el cuerpo es un identificador que
    // el servidor resuelve, no un pedazo de ruta.
    if ($entidad === 'it') {
        $vin = strtoupper(trim((string) $this->input->post('vin')));
        $vin = preg_replace('/[^A-Z0-9]/', '', $vin);

        if (strlen($vin) !== 17) {
            $this->json(400, array(
                'error' => 'vin invalido para la entidad it: se esperan 17 '
                         . 'caracteres alfanumericos',
            ));
            return;
        }

        // La comprobacion que hace que el VIN no sea una ruta: tiene que
        // EXISTIR. Un VIN inventado no crea carpeta.
        $existe = $this->db
            ->select('id')
            ->from('newstocks_cidef')
            ->where('vin', $vin)
            ->limit(1)
            ->get()
            ->row();

        if (!$existe) {
            $this->json(404, array(
                'error' => 'no existe ninguna unidad con vin ' . $vin,
            ));
            return;
        }

        $carpeta = $carpeta . $vin . '/';
    }

/*
--------------------------------------------------------------------------------
COMO SE COMPRUEBA LA EDICION 2
--------------------------------------------------------------------------------

    php -l ~/public_html/application/controllers/Api_regla_subir_foto.php
        --> No syntax errors detected

    grep -c "'it' *=>" ~/public_html/application/controllers/Api_regla_subir_foto.php
        --> tiene que dar 2   (una por cada lista blanca)

    grep -A3 "entidad === 'it'" ~/public_html/application/controllers/Api_regla_subir_foto.php
        --> tiene que aparecer `newstocks_cidef`. Si no aparece, quedo el
            archivo sin la validacion del VIN, y ESO SI ES GRAVE: la carpeta
            saldria del cuerpo del pedido.

Y las tres sondas, en este orden:

  1. sin API key                      -> 401
  2. con key, entidad 'it', sin vin   -> 400 'vin invalido'
  3. con key, entidad 'it', vin falso -> 404 'no existe ninguna unidad'

Las tres ANTES de subir una foto de verdad. La 3 es la que importa: si
devolviera 201 en vez de 404, la validacion no quedo y hay que parar.

--------------------------------------------------------------------------------
Y LO QUE NO ENTRA EN ESTE BLOQUE, A PROPOSITO
--------------------------------------------------------------------------------

LA TABLA `fotos_it`. Regla PHP le inserta una fila por foto
(`insertar_foto_it`, produccion/Pedido_model.php:3494), y Regla Python todavia
NO la escribe: la entidad de push `it_foto` sube el ARCHIVO, no la fila.

No entra porque necesita un endpoint propio --como el de `check_list_mecanica`--
y porque el orden correcto es al reves: primero que las fotos lleguen al disco
y se vean en el PDF, despues la fila que las indexa. Una fila que apunta a un
archivo que no existe es peor que no tener la fila.

Queda anotado como pendiente en CLAUDE.md, no como olvido.

EL INTERRUPTOR DEL ENDPOINT DE FOTOS SIGUE EN FALSE. Este bloque agrega la
entidad `it` a un endpoint que esta apagado; encenderlo es la decision aparte
que espera a que baje el disco del cPanel.
================================================================================
*/
