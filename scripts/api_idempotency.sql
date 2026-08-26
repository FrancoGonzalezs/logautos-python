-- api_idempotency -- la tabla que el PUT de Api_regla.php necesita del lado
-- MySQL de claude.logautos.cl. Se corre UNA vez, en phpMyAdmin o donde se
-- administre la base del legado.
--
-- Para que sirve
-- --------------
-- Guarda la respuesta que se dio a cada Idempotency-Key. Si Python reintenta
-- un push porque la respuesta se perdio en la red, el endpoint encuentra la
-- key y devuelve lo mismo de la primera vez en vez de volver a decidir.
--
-- En un PUT con locking optimista eso no es un lujo: el reintento manda el
-- mismo `legado_updated_at_conocido`, pero el `updated_at` de la fila ya
-- avanzo -- lo avanzo ese mismo PUT. Sin esta tabla, el segundo intento se
-- choca con su propia escritura y responde 409: un conflicto inventado, y un
-- dato correcto marcado para revision manual.
--
-- InnoDB NO ES OPCIONAL
-- ---------------------
-- El UPDATE de newstocks_cidef y el INSERT de la key van en UNA transaccion.
-- Con MyISAM el ROLLBACK es un no-op silencioso: la fila quedaria actualizada
-- y la key sin registrar, que es el mismo agujero que la key viene a tapar.
--
-- OJO: si `newstocks_cidef` fuera MyISAM, la transaccion tampoco protege esa
-- mitad. Vale la pena confirmarlo antes de habilitar el push:
--
--     SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES
--      WHERE TABLE_SCHEMA = DATABASE()
--        AND TABLE_NAME IN ('newstocks_cidef', 'api_idempotency');
--
-- La PRIMARY KEY sobre idem_key es la que resuelve la carrera entre dos
-- reintentos simultaneos del mismo push: el segundo choca, y el codigo trata
-- ese choque como "ya estaba" y no como error.

CREATE TABLE IF NOT EXISTS api_idempotency (
    idem_key   VARCHAR(64) NOT NULL PRIMARY KEY,
    entidad    VARCHAR(32) NOT NULL,
    entidad_id INT         NOT NULL,
    updated_at VARCHAR(25) NOT NULL DEFAULT '',
    creado_en  TIMESTAMP   NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- Comprobacion despues de crearla:
--
--     SHOW CREATE TABLE api_idempotency;
--
-- Tiene que decir ENGINE=InnoDB. Si dice MyISAM, el hosting ignoro el ENGINE
-- (pasa en algunos MySQL viejos con InnoDB deshabilitado) y hay que resolver
-- eso ANTES de habilitar el push.
