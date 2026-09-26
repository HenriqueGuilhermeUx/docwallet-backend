import base64
import hashlib
import hmac
import io
import json
import os
import time
import uuid

os.environ.setdefault("NEXOFFICE_BRIDGE_ENABLED", "true")
os.environ.setdefault("NEXOFFICE_SIGNATURE_BRIDGE_ENABLED", "true")
os.environ.setdefault("NEXOFFICE_ICP_SIGNATURE_ENABLED", "false")
os.environ.setdefault("NEXOFFICE_SERVICE_KEY", "ci-nexoffice-service-key")
os.environ.setdefault("DOCUMENT_INTELLIGENCE_ENABLED", "true")
os.environ.setdefault("DOCUMENT_INTELLIGENCE_PROVIDER", "internal")
os.environ.setdefault("ICP_SIGNATURE_ENABLED", "false")
os.environ.setdefault("ICP_SIGNATURE_PROVIDER", "lacuna")
os.environ.setdefault("ICP_SIGNATURE_MODE", "external_provider")
os.environ.setdefault("ICP_SIGNATURE_PROVIDER_BASE_URL", "https://homolog.core.pki.rest")
os.environ.setdefault("ICP_SIGNATURE_API_KEY", "")

from app import app


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def connect_token(workspace_id: str) -> str:
    payload = {"workspaceId": workspace_id, "iat": int(time.time()), "exp": int(time.time()) + 300, "nonce": uuid.uuid4().hex}
    encoded = b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(os.environ["NEXOFFICE_SERVICE_KEY"].encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{b64url(signature)}"


def ok(response, expected=None):
    if expected is not None:
        assert response.status_code == expected, (response.status_code, response.get_json())
    else:
        assert 200 <= response.status_code < 300, (response.status_code, response.get_json())
    return response.get_json()


def main():
    client = app.test_client()
    workspace = str(uuid.uuid4())
    registered = ok(client.post("/api/auth/register", json={
        "name": "NexOffice Signature Modes CI",
        "email": f"sig-modes-{uuid.uuid4().hex[:8]}@example.test",
        "password": "CiOnlyPass-1234",
    }), 201)
    user_headers = {"Authorization": f"Bearer {registered['token']}"}
    ok(client.post("/api/integrations/nexoffice/connect", headers=user_headers, json={"token": connect_token(workspace)}))

    uploaded = ok(client.post(
        "/api/documents/upload",
        headers=user_headers,
        data={
            "file": (io.BytesIO(b"CONTRATO CI - assinatura eletrônica e ICP-Brasil"), "contrato.txt"),
            "name": "Contrato modos assinatura CI",
            "type": "contract",
            "category": "contracts",
        },
        content_type="multipart/form-data",
    ), 201)
    document_id = uploaded["document"]["id"]
    service_headers = {"X-NexOffice-Key": os.environ["NEXOFFICE_SERVICE_KEY"], "X-NexOffice-Workspace-ID": workspace}

    modes = ok(client.get("/api/internal/nexoffice/signature-modes", headers=service_headers))
    by_id = {item["id"]: item for item in modes["modes"]}
    assert by_id["electronic"]["available"] is True
    assert by_id["electronic"]["provider"] == "docwallet"
    assert by_id["icp_brasil"]["provider"] == "lacuna"
    assert by_id["icp_brasil"]["available"] is False
    assert modes["icp"]["nexofficeEnabled"] is False
    assert modes["privacy"] == {
        "privateKeysHandledByDocWallet": False,
        "certificatePasswordsHandledByDocWallet": False,
        "lacunaRedirectUrlReturnedToNexOffice": False,
        "sensitiveEvidenceReturnedToNexOffice": False,
        "rawDocumentReturnedToNexOffice": False,
    }

    created = ok(client.post(
        f"/api/internal/nexoffice/documents/{document_id}/signature-request",
        headers={**service_headers, "X-Idempotency-Key": "sig-modes-electronic-1"},
        json={"parties": [{"name": "Cliente CI", "email": "cliente@example.test"}]},
    ), 201)
    signature_id = created["request"]["id"]

    status = ok(client.get(f"/api/internal/nexoffice/signatures/{signature_id}", headers=service_headers))
    assert status["request"]["id"] == signature_id
    assert status["request"]["totalParties"] == 1
    assert status["externalEffect"] is False
    serialized = json.dumps(status).lower()
    for forbidden in ["contract_content", "signature_image", "ip_address", "signed_cpf", "signed_phone", "geo_latitude", "device_fingerprint", "private_key", "certificate_password", "redirecturl"]:
        assert forbidden not in serialized, forbidden

    sessions = ok(client.get(f"/api/internal/nexoffice/signatures/{signature_id}/icp/sessions", headers=service_headers))
    assert sessions["sessions"] == []
    assert sessions["privacy"]["redirectUrlReturned"] is False
    assert sessions["externalEffect"] is False

    no_confirm = client.post(
        f"/api/internal/nexoffice/signatures/{signature_id}/icp/prepare",
        headers=service_headers,
        json={"humanConfirmed": False},
    )
    assert no_confirm.status_code == 400, (no_confirm.status_code, no_confirm.get_json())
    assert no_confirm.get_json()["code"] == "human_confirmation_required"

    gated = client.post(
        f"/api/internal/nexoffice/signatures/{signature_id}/icp/prepare",
        headers=service_headers,
        json={"humanConfirmed": True},
    )
    assert gated.status_code == 409, (gated.status_code, gated.get_json())
    assert gated.get_json()["code"] == "nexoffice_icp_disabled"

    raw = json.dumps({"modes": modes, "status": status, "sessions": sessions}).lower()
    assert "ci-nexoffice-service-key" not in raw
    assert "x-api-key" not in raw
    assert "https://homolog.core.pki.rest" not in raw

    print(json.dumps({
        "ok": True,
        "workspaceId": workspace,
        "signatureId": signature_id,
        "electronicAvailable": True,
        "icpCapabilityVisible": True,
        "icpExternalCallBlockedInCi": True,
        "humanConfirmationRequired": True,
        "rawDocumentReturned": False,
        "sensitiveEvidenceReturned": False,
    }))


if __name__ == "__main__":
    main()
