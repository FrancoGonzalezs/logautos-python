"""
crear_usuario.py -- siembra un usuario de prueba para poder entrar a REGLA en
local sin usar una credencial real.

Desde que el login valida de verdad contra `tbl_users`, para abrir la app hace
falta un email y una contrasena que existan. Este script deja uno, con el mismo
mecanismo que ya usan las suites de verificacion: un hash bcrypt de verdad, en
el mismo formato `$2y$` que escribe `password_hash()` de PHP.

LOS 144 USUARIOS REALES NO SE TOCAN
-----------------------------------
Los de prueba viven en un rango de userId reservado que arranca en 999000. Los
reales van de 0 a 2037, asi que no hay forma de que se pisen. Ademas:

  - `--borrar` solo borra en ese rango; no puede tocar una cuenta real ni por
    error de tipeo;
  - si el email que se pide ya pertenece a un usuario real, el script se niega
    en vez de sobrescribirlo -- eso seria cambiarle la contrasena a alguien;
  - correrlo dos veces con el mismo email no duplica nada: actualiza el que ya
    habia.

OJO: esto crea una credencial que sirve para entrar. Es para la base LOCAL. No
lo corras contra la base de produccion -- y si algun dia pasa, `--listar`
muestra todo lo sembrado y `--borrar` lo saca de una.

Uso:
    python scripts/crear_usuario.py
    python scripts/crear_usuario.py --email juan@regla.test --clave secreta123
    python scripts/crear_usuario.py --rol Manager
    python scripts/crear_usuario.py --listar
    python scripts/crear_usuario.py --borrar
"""

import argparse
import os
import sqlite3
import sys

try:
    import bcrypt
except ImportError:
    sys.exit("falta bcrypt: corre  pip install -r requirements.txt")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Se lee DB_PATH del entorno, igual que core.py: si la app esta apuntando a
# otra base, el usuario tiene que quedar en ESA y no en una que nadie lee.
DB_POR_DEFECTO = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "local.db"))

# Todo lo sembrado por este script vive de aca para arriba. Los userId reales
# del dump llegan hasta 2037.
ID_RESERVADO_DESDE = 999000

EMAIL_POR_DEFECTO = "prueba@regla.test"
CLAVE_POR_DEFECTO = "regla1234"
# Rol 6 = Patio: es el que usan los movilizadores, o sea el que mas se va a
# probar en Movimientos y en el Check List de Ingreso.
ROL_POR_DEFECTO = "6"
CLIENTE_POR_DEFECTO = "LOGAUTOS"


def conectar(ruta):
    if not os.path.exists(ruta):
        sys.exit("no existe la replica {}\n"
                 "corre primero: python scripts/importar_dump.py".format(ruta))
    db = sqlite3.connect(ruta)
    db.row_factory = sqlite3.Row
    for tabla in ("tbl_users", "tbl_roles"):
        existe = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (tabla,)).fetchone()
        if not existe:
            sys.exit(
                "falta la tabla {} en {}\n"
                "corre primero: python scripts/importar_dump.py "
                "--tablas tbl_users,tbl_roles".format(tabla, ruta))
    return db


def hash_como_php(clave):
    """Un hash bcrypt con el prefijo que escribe PHP.

    `password_hash($clave, PASSWORD_DEFAULT)` produce `$2y$10$...` y el bcrypt
    de Python produce `$2b$10$...`. Las dos variantes son el mismo algoritmo y
    `checkpw` acepta las dos, asi que para entrar a REGLA daria igual -- pero
    se guarda como `$2y$` para que la fila sea indistinguible de una real: si
    manana el sync empuja usuarios al sistema viejo, no tiene que haber un
    formato nuestro y otro de ellos.

    El costo va en 10 por lo mismo: es el que tienen las 144 filas del dump."""
    salteado = bcrypt.hashpw(clave.encode("utf-8"), bcrypt.gensalt(rounds=10))
    return salteado.replace(b"$2b$", b"$2y$", 1).decode("ascii")


def resolver_rol(db, pedido):
    """Acepta el id ('6') o el nombre ('Patio', 'patio') de un rol."""
    pedido = str(pedido).strip()
    if pedido.isdigit():
        fila = db.execute("SELECT roleId, role FROM tbl_roles WHERE roleId = ?",
                          (int(pedido),)).fetchone()
    else:
        fila = db.execute(
            "SELECT roleId, role FROM tbl_roles WHERE role = ? COLLATE NOCASE",
            (pedido,)).fetchone()
    if fila is None:
        disponibles = "\n".join(
            "  {:>2}  {}".format(r["roleId"], r["role"])
            for r in db.execute("SELECT roleId, role FROM tbl_roles ORDER BY roleId"))
        sys.exit("no existe el rol {!r}. Los que hay:\n{}".format(pedido, disponibles))
    return fila["roleId"], fila["role"]


def proximo_id(db):
    usado = db.execute("SELECT MAX(userId) FROM tbl_users WHERE userId >= ?",
                       (ID_RESERVADO_DESDE,)).fetchone()[0]
    return ID_RESERVADO_DESDE if usado is None else usado + 1


def existente(db, email):
    """La fila con ese email, sea de prueba o real. Se compara con NOCASE por
    lo mismo que el login: en MySQL la collation es insensible, asi que dos
    emails que solo difieren en mayusculas son el mismo usuario."""
    return db.execute(
        "SELECT userId, email, name, isDeleted FROM tbl_users "
        "WHERE email = ? COLLATE NOCASE", (email,)).fetchone()


def listar(db, ruta):
    filas = db.execute(
        "SELECT u.userId, u.email, u.name, u.roleId, r.role, u.isDeleted "
        "FROM tbl_users u LEFT JOIN tbl_roles r ON r.roleId = u.roleId "
        "WHERE u.userId >= ? ORDER BY u.userId", (ID_RESERVADO_DESDE,)).fetchall()
    if not filas:
        print("no hay usuarios de prueba sembrados en {}".format(ruta))
        return 0
    print("usuarios de prueba en {}:\n".format(ruta))
    print("  {:<8} {:<34} {:<24} {}".format("userId", "email", "nombre", "rol"))
    for f in filas:
        print("  {:<8} {:<34} {:<24} {} ({}){}".format(
            f["userId"], f["email"], f["name"], f["role"] or "?", f["roleId"],
            "  [borrado]" if f["isDeleted"] else ""))
    print("\n{} en total. Para sacarlos: python scripts/crear_usuario.py --borrar"
          .format(len(filas)))
    return 0


def borrar(db, email=None):
    if email:
        fila = existente(db, email)
        if fila is None:
            print("no hay ningun usuario con el email {}".format(email))
            return 1
        if fila["userId"] < ID_RESERVADO_DESDE:
            sys.exit(
                "{} es un usuario REAL (userId {}). Este script solo borra los "
                "de prueba, de {} para arriba.".format(
                    email, fila["userId"], ID_RESERVADO_DESDE))
        cur = db.execute("DELETE FROM tbl_users WHERE userId = ?", (fila["userId"],))
    else:
        cur = db.execute("DELETE FROM tbl_users WHERE userId >= ?",
                         (ID_RESERVADO_DESDE,))
    db.commit()
    print("borrados {} usuario(s) de prueba".format(cur.rowcount))
    return 0


def sembrar(db, ruta, email, clave, nombre, rol, cliente):
    id_rol, texto_rol = resolver_rol(db, rol)

    ya = existente(db, email)
    if ya is not None and ya["userId"] < ID_RESERVADO_DESDE:
        sys.exit(
            "el email {} es de un usuario REAL (userId {}, {}).\n"
            "Este script no toca cuentas reales: elegi otro email con --email."
            .format(email, ya["userId"], ya["name"]))

    contrasena = hash_como_php(clave)

    if ya is not None:
        # Ya habia uno de prueba con ese email: se actualiza en vez de fallar,
        # para que correr el script dos veces no obligue a borrar antes.
        id_usuario = ya["userId"]
        db.execute(
            "UPDATE tbl_users SET password = ?, name = ?, roleId = ?, "
            "cliente = ?, isDeleted = 0, bloqueo = 0, "
            "updatedDtm = datetime('now') WHERE userId = ?",
            (contrasena, nombre, id_rol, cliente, id_usuario))
        accion = "actualizado"
    else:
        id_usuario = proximo_id(db)
        db.execute(
            "INSERT INTO tbl_users "
            "(userId, email, password, name, mobile, roleId, grupo, cliente, "
            " cliente_name, isDeleted, createdBy, createdDtm, updatedBy, "
            " updatedDtm, rut, bloqueo) "
            "VALUES (?,?,?,?,'',?,'',?,NULL,0,0,datetime('now'),NULL,NULL,'',0)",
            (id_usuario, email, contrasena, nombre, id_rol, cliente))
        accion = "creado"

    db.commit()

    print("usuario de prueba {} en {}\n".format(accion, DB_POR_DEFECTO))
    print("  userId    {}".format(id_usuario))
    print("  email     {}".format(email))
    print("  password  {}".format(clave))
    print("  nombre    {}".format(nombre))
    print("  rol       {} ({})".format(texto_rol, id_rol))
    print("  cliente   {}".format(cliente))
    print("\nEntra en http://localhost:5000/login con ese email y esa contrasena.")
    print("Para sacarlo despues: python scripts/crear_usuario.py --borrar")
    return 0


def main():
    p = argparse.ArgumentParser(
        description="Siembra un usuario de prueba local para entrar a REGLA. "
                    "No toca ninguno de los usuarios reales.")
    # Sin default acá a proposito: hace falta distinguir "no me lo pidieron"
    # de "me pidieron justo el de por defecto", porque en --borrar significan
    # cosas distintas (borrar todos vs borrar ese). El default se aplica
    # recien al sembrar.
    p.add_argument("--email", default=None,
                   help="email con que se entra (default: {})".format(EMAIL_POR_DEFECTO))
    p.add_argument("--clave", default=CLAVE_POR_DEFECTO,
                   help="contrasena en texto plano; se guarda hasheada "
                        "(default: %(default)s)")
    p.add_argument("--nombre", default=None,
                   help="nombre que firma los movimientos y check lists "
                        "(default: 'Usuario De Prueba')")
    p.add_argument("--rol", default=ROL_POR_DEFECTO,
                   help="id o nombre del rol, ej. 6 o Patio (default: 6 = Patio)")
    p.add_argument("--cliente", default=CLIENTE_POR_DEFECTO,
                   help="cliente del usuario (default: %(default)s)")
    p.add_argument("--db", default=DB_POR_DEFECTO,
                   help="ruta de la replica (default: %(default)s)")
    p.add_argument("--listar", action="store_true",
                   help="muestra los usuarios de prueba ya sembrados y sale")
    p.add_argument("--borrar", action="store_true",
                   help="borra los usuarios de prueba (todos, o el de --email)")
    args = p.parse_args()

    db = conectar(args.db)
    try:
        if args.listar:
            return listar(db, args.db)
        if args.borrar:
            return borrar(db, args.email.strip() if args.email else None)
        return sembrar(db, args.db, (args.email or EMAIL_POR_DEFECTO).strip(),
                       args.clave, args.nombre or "Usuario De Prueba",
                       args.rol, args.cliente)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
