"""Local-only Flask fixture for cross-repository HTTP tests.

Never runs without an explicit CI guard, isolated SQLite database and loopback bind.
All accounts, credentials and files are synthetic.
"""
import json
import os
from pathlib import Path

if os.environ.get("DOCWALLET_OFFLINE_E2E") != "1":
    raise SystemExit("Refusing to run without DOCWALLET_OFFLINE_E2E=1")
uri = os.environ.get("DATABASE_URL", "")
if not uri.startswith("sqlite:///"):
    raise SystemExit("Refusing to run against a non-SQLite database")
if os.environ.get("DOCWALLET_LOCAL_E2E_PORT") != "8765":
    raise SystemExit("Missing expected local-only port")
out_path = os.environ.get("DOCWALLET_LOCAL_E2E_FIXTURES", "")
if not out_path:
    raise SystemExit("Missing temporary fixture path")

from app import app, db, User, Document, create_token  # noqa: E402
import hashlib

bytes_pdf = b"%PDF-1.4\n% synthetic offline test only\n"
storage = Path(os.environ["UPLOAD_DIR"])
storage.mkdir(parents=True, exist_ok=True)
path = storage / "synthetic-only.pdf"
path.write_bytes(bytes_pdf)

with app.app_context():
    owner = User(name="Fixture owner", email="fixture-owner@example.invalid", password_hash="not-a-password")
    other = User(name="Fixture other", email="fixture-other@example.invalid", password_hash="not-a-password")
    db.session.add_all([owner, other])
    db.session.flush()
    document = Document(
        user_id=owner.id,
        name="Synthetic fixture",
        original_filename=path.name,
        stored_filename=path.name,
        file_path=str(path),
        file_type="application/pdf",
        file_size=len(bytes_pdf),
        file_hash=hashlib.sha256(bytes_pdf).hexdigest(),
        doc_type="rg",
        category="ids",
    )
    db.session.add(document)
    db.session.commit()
    fixtures = {
        "document_id": document.id,
        "owner_token": create_token(owner),
        "other_token": create_token(other),
    }
    target = Path(out_path)
    target.write_text(json.dumps(fixtures), encoding="utf-8")
    target.chmod(0o600)

# Intentional loopback-only; no external staging exposure or production data.
app.run(host="127.0.0.1", port=8765, debug=False, use_reloader=False)
