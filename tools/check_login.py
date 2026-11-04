"""
check_login.py — backend sign-in diagnostic (not a pytest test).

Run from the project root:
    python tools/check_login.py [USERNAME PASSWORD]

Prints the database path, the users in it, whether the account exists, whether
the password matches, and the result of a full auth.login().  Opens the same
instance/users.db the server uses (honours FETCH_INSTANCE_DIR).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from retrieval import auth, db_users  # noqa: E402

DB_FILE = Path(os.environ.get("FETCH_INSTANCE_DIR") or ROOT / "instance") / "users.db"
USERNAME = sys.argv[1] if len(sys.argv) > 1 else "BLFD-01"
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else "blfd2024"

print(f"Using database: {DB_FILE.resolve()}")
db_users.init_db(DB_FILE)

with db_users._connect() as conn:
    rows = conn.execute("SELECT username, document_ids FROM users").fetchall()
print("\nUsers in DB:")
for r in rows:
    print(f"  {r['username']!r}  docs={r['document_ids']}")

user = db_users.get_user(USERNAME)
print(f"\nget_user({USERNAME!r}) ->", user)
if user is not None:
    print("check_password ->", user.check_password(PASSWORD))

try:
    token = auth.login(USERNAME, PASSWORD)
    print("\nLOGIN OK, token:", token[:12] + "...")
except auth.AuthError as e:
    print("\nLOGIN FAILED:", e)
