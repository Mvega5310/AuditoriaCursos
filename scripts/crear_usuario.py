"""
Crea (o restablece) un usuario administrador de la app web para una empresa YA existente
(por ejemplo, la que quedo de migrar_v1.py). El usuario nace verificado.

Uso:
  python scripts/crear_usuario.py --empresa "Hospital Universitario" --email admin@hospital.com
  (la contrasena se pide por consola; no se pasa por argumento para que no quede en el historial)
"""
import argparse
import getpass
import sys
from datetime import datetime
from pathlib import Path

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core import conectar, inicializar_db  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--empresa", required=True)
    p.add_argument("--email", required=True)
    a = p.parse_args()

    conn = conectar()
    inicializar_db(conn)
    emp = conn.execute("SELECT id FROM empresas WHERE nombre = ?", (a.empresa,)).fetchone()
    if not emp:
        sys.exit(f"No existe la empresa '{a.empresa}'. Empresas: " +
                 ", ".join(r["nombre"] for r in conn.execute("SELECT nombre FROM empresas")))
    clave = getpass.getpass("Contrasena (min. 10 caracteres): ")
    if len(clave) < 10 or clave != getpass.getpass("Repita la contrasena: "):
        sys.exit("Contrasena corta o no coincide.")
    conn.execute("""
        INSERT INTO usuarios (empresa_id, email, password_hash, rol, verificado, creado_en)
        VALUES (?, ?, ?, 'admin', 1, ?)
        ON CONFLICT(email) DO UPDATE SET password_hash = excluded.password_hash, verificado = 1
        WHERE usuarios.empresa_id = excluded.empresa_id
    """, (emp["id"], a.email.lower(), generate_password_hash(clave), datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    print(f"Usuario {a.email.lower()} listo para '{a.empresa}'.")


if __name__ == "__main__":
    main()
