"""
test_login.py: place this in retrieval_framework (next to the `retrieval` folder),
NOT inside `retrieval`. Then run it with the VS Code "Run Python File" button.
"""
from pathlib import Path

from retrieval import db_users, auth

DB_FILE = Path(__file__).parent / "test.db"
USERNAME = "NL-2048"
PASSWORD = "password1234"

print(f"Using database: {DB_FILE.resolve()}")
db_users.init_db(DB_FILE)

# 1. Which users exist in the DB?
with db_users._connect() as conn:
    rows = conn.execute("SELECT username, document_ids FROM users").fetchall()
print("\nUsers in DB:")
for r in rows:
    print(f"  {r['username']!r}  docs={r['document_ids']}")

# 2. Does the account exist?
user = db_users.get_user(USERNAME)
print(f"\nget_user({USERNAME!r}) ->", user)

# 3. Does the password check out?
if user is not None:
    print("check_password ->", user.check_password(PASSWORD))

# 4. Full login through auth.py
try:
    token = auth.login(USERNAME, PASSWORD)
    print("\nLOGIN OK, token:", token[:12] + "...")
except auth.AuthError as e:
    print("\nLOGIN FAILED:", e)
