import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PRODUCTS_JSON = ROOT / "app" / "data" / "products.json"

_CHILD_SCRIPT = r"""
import os
import sys

db_path = os.environ["AURA_SQLITE_PATH"]
os.environ["DATABASE_URL"] = "sqlite:///" + db_path.replace("\\", "/")

from app.db.database import db_service, get_db_connection, DB_PATH

if os.path.normpath(DB_PATH) != os.path.normpath(db_path):
    raise SystemExit(f"DB_PATH mismatch: {DB_PATH!r} != {db_path!r}")

conn = get_db_connection(DB_PATH)
try:
    n_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    n_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
finally:
    conn.close()

if n_users <= 0:
    raise SystemExit(f"users table empty after fresh init ({n_users})")
if n_products <= 0:
    raise SystemExit(f"products table empty after fresh init ({n_products})")
print(f"ok users={n_users} products={n_products}")
"""


def test_fresh_database_boot_serves_products():
    """Khoá lỗi init_db crash khi database mới tinh (users rỗng + FK loyalty)."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "aura_store.db")
        env = os.environ.copy()
        env["AURA_SQLITE_PATH"] = db_path
        env["DATABASE_URL"] = "sqlite:///" + db_path.replace("\\", "/")

        result = subprocess.run(
            [sys.executable, "-c", _CHILD_SCRIPT],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Khởi tạo database mới bị crash.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/products")
    assert response.status_code == 200
    with open(PRODUCTS_JSON, encoding="utf-8") as f:
        expected_count = len(json.load(f))
    assert len(response.json()) == expected_count
