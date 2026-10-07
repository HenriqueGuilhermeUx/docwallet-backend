import os
import runpy
import sys
import uuid
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/docwallet-ci.db")
os.environ.setdefault("UPLOAD_DIR", "/tmp/docwallet-uploads")
os.environ.setdefault("JWT_SECRET", "ci-jwt-secret")
os.environ["NEXA_ECOSYSTEM_ATTENTION_ENABLED"] = "true"
os.environ["NEXA_ECOSYSTEM_ATTENTION_SERVICE_KEY"] = "ci-attention-key"

ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

runpy.run_path(str(ROOT / "fix_sqlalchemy.py"), run_name="__main__")
runpy.run_path(str(ROOT / "sign_patch.py"), run_name="__main__")

spec = spec_from_file_location("_docwallet_nexa_attention_runtime", ROOT / "app.py")
if spec is None or spec.loader is None:
    raise RuntimeError("Could not load DocWallet app.py")
app_module = module_from_spec(spec)
sys.modules[spec.name] = app_module
spec.loader.exec_module(app_module)


def test_attention_requires_service_key():
    client = app_module.app.test_client()
    response = client.get(
        "/api/nexa/attention",
        headers={"X-Nexa-User-ID": "nexa-nobody"},
    )
    assert response.status_code == 401


def test_attention_returns_only_sanitized_signature_summary():
    email = f"attention-{uuid.uuid4().hex[:10]}@example.test"
    nexa_user_id = f"nexa-{uuid.uuid4()}"

    with app_module.app.app_context():
        user = app_module.User(
            name="Attention Test",
            email=email,
            password_hash=app_module.hash_password("test-password"),
            nexa_user_id=nexa_user_id,
            nexa_id="NEXA-ATTENTION",
        )
        app_module.db.session.add(user)
        app_module.db.session.commit()

        request_id = str(uuid.uuid4())
        party_id = str(uuid.uuid4())
        app_module.db.session.execute(
            app_module.text(
                """
                INSERT INTO signature_requests
                  (id, user_id, title, contract_content, content_hash, status, created_at)
                VALUES
                  (:id, :user_id, :title, :content, :hash, 'pending', CURRENT_TIMESTAMP)
                """
            ),
            {
                "id": request_id,
                "user_id": user.id,
                "title": "Sensitive contract title",
                "content": "Sensitive contract body",
                "hash": uuid.uuid4().hex,
            },
        )
        app_module.db.session.execute(
            app_module.text(
                """
                INSERT INTO signature_parties
                  (id, request_id, code, name, email, status)
                VALUES
                  (:id, :request_id, :code, :name, :email, 'pending')
                """
            ),
            {
                "id": party_id,
                "request_id": request_id,
                "code": uuid.uuid4().hex,
                "name": "Sensitive signer name",
                "email": email,
            },
        )
        app_module.db.session.commit()

    client = app_module.app.test_client()
    response = client.get(
        "/api/nexa/attention",
        headers={
            "Authorization": "Bearer ci-attention-key",
            "X-Nexa-User-ID": nexa_user_id,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["sensitivePayloadIncluded"] is False
    assert len(payload["items"]) == 1

    item = payload["items"][0]
    assert item["kind"] == "document.signature.pending"
    assert item["count"] == 1

    serialized = str(payload)
    assert "Sensitive contract title" not in serialized
    assert "Sensitive contract body" not in serialized
    assert "Sensitive signer name" not in serialized


if __name__ == "__main__":
    test_attention_requires_service_key()
    test_attention_returns_only_sanitized_signature_summary()
    print("Nexa DocWallet attention smoke: OK")
