import os
import uuid

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/docwallet-ci.db")
os.environ.setdefault("UPLOAD_DIR", "/tmp/docwallet-uploads")
os.environ.setdefault("JWT_SECRET", "ci-jwt-secret")

import app as app_module


def test_nexa_federation_disabled_by_default():
    original = app_module.NEXA_SSO_ENABLED
    app_module.NEXA_SSO_ENABLED = False
    try:
        client = app_module.app.test_client()
        response = client.post("/api/auth/nexa", json={"token": "test"})
        assert response.status_code == 503
        payload = response.get_json()
        assert payload["success"] is False
    finally:
        app_module.NEXA_SSO_ENABLED = original


def test_nexa_federation_provisions_and_reuses_local_account():
    email = f"nexa-federation-{uuid.uuid4().hex[:10]}@example.test"
    nexa_user_id = f"nexa-{uuid.uuid4()}"
    original_enabled = app_module.NEXA_SSO_ENABLED
    original_validator = app_module.validate_nexa_sso_token

    app_module.NEXA_SSO_ENABLED = True
    app_module.validate_nexa_sso_token = lambda token: {
        "id": nexa_user_id,
        "nexaId": "NEXA-TEST",
        "fullName": "Usuário Federação",
        "email": email,
    }

    try:
        client = app_module.app.test_client()

        first = client.post("/api/auth/nexa", json={"token": "one-time-token"})
        assert first.status_code == 200
        first_payload = first.get_json()
        assert first_payload["success"] is True
        assert first_payload["token"]
        assert first_payload["user"]["email"] == email
        assert first_payload["federation"]["source"] == "nexa"

        second = client.post("/api/auth/nexa", json={"token": "another-one-time-token"})
        assert second.status_code == 200
        second_payload = second.get_json()
        assert second_payload["success"] is True
        assert second_payload["user"]["id"] == first_payload["user"]["id"]

        with app_module.app.app_context():
            users = app_module.User.query.filter_by(email=email).all()
            assert len(users) == 1
    finally:
        app_module.NEXA_SSO_ENABLED = original_enabled
        app_module.validate_nexa_sso_token = original_validator


if __name__ == "__main__":
    test_nexa_federation_disabled_by_default()
    test_nexa_federation_provisions_and_reuses_local_account()
    print("Nexa ID federation smoke: OK")
