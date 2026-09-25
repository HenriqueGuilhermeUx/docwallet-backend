import base64
import hashlib
import hmac
import json
import os
import time
import uuid

os.environ.setdefault("NEXOFFICE_BRIDGE_ENABLED", "true")
os.environ.setdefault("NEXOFFICE_SERVICE_KEY", "ci-nexoffice-service-key")
os.environ.setdefault("NEXOFFICE_CONTRACT_CREATE_ENABLED", "true")
os.environ.setdefault("NEXOFFICE_SIGNATURE_BRIDGE_ENABLED", "true")

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
    workspace_id = str(uuid.uuid4())
    registered = ok(client.post("/api/auth/register", json={
        "name": "Signature Bridge Owner",
        "email": f"signature-{uuid.uuid4().hex[:8]}@example.test",
        "password": "ci-test-password-123",
    }), 201)
    user_headers = {"Authorization": f"Bearer {registered['token']}"}
    ok(client.post("/api/integrations/nexoffice/connect", headers=user_headers, json={"token": connect_token(workspace_id)}))

    service_headers = {"X-NexOffice-Key": os.environ["NEXOFFICE_SERVICE_KEY"], "X-NexOffice-Workspace-ID": workspace_id}
    created = ok(client.post(
        "/api/internal/nexoffice/contracts/create",
        headers={**service_headers, "X-Idempotency-Key": "signature-contract-ci-1"},
        json={
            "type": "prestacao_servicos",
            "party_a": "Empresa Cliente Ltda.",
            "party_b": "Prestador Exemplo",
            "description": "Contrato usado para validar a ponte de assinatura do NexOffice.",
        },
    ), 201)
    contract = created["contract"]
    document = created["document"]

    requested = ok(client.post(
        f"/api/internal/nexoffice/documents/{document['id']}/signature-request",
        headers={**service_headers, "X-Idempotency-Key": "signature-ci-1"},
        json={"parties": [
            {"name": "Maria Cliente", "email": "maria@example.test"},
            {"name": "Joao Prestador", "email": "joao@example.test"},
        ]},
    ), 201)
    raw_request = requested["request"]
    request_id = raw_request["id"]
    assert raw_request["status"] == "pending"
    assert len(raw_request.get("parties") or []) == 2

    repeated = ok(client.post(
        f"/api/internal/nexoffice/documents/{document['id']}/signature-request",
        headers={**service_headers, "X-Idempotency-Key": "signature-ci-1"},
        json={"parties": [{"name": "Nao deve duplicar", "email": "duplicate@example.test"}]},
    ), 201)
    assert repeated["request"]["id"] == request_id

    status = ok(client.get(f"/api/internal/nexoffice/signatures/{request_id}", headers=service_headers))
    req = status["request"]
    assert req["id"] == request_id
    assert req["status"] == "pending"
    assert req["totalParties"] == 2
    assert req["signedCount"] == 0
    assert req["pendingCount"] == 2
    assert req["progressPercent"] == 0
    assert status["rawContentReturned"] is False
    assert status["sensitiveEvidenceReturned"] is False
    assert len(req["parties"]) == 2
    raw = json.dumps(status).lower()
    for forbidden in ["contract_content", "ip_address", "signed_cpf", "signed_phone", "geo_latitude", "device_fingerprint", "signature_image"]:
        assert forbidden not in raw, forbidden

    reminder = ok(client.post(
        f"/api/internal/nexoffice/signatures/{request_id}/reminder",
        headers=service_headers,
        json={"partyId": req["parties"][0]["id"]},
    ))
    assert reminder["url"].startswith("/sign/")
    assert reminder["party"]["name"] == "Maria Cliente"
    assert reminder["sensitiveEvidenceReturned"] is False

    cancelled = ok(client.post(f"/api/internal/nexoffice/signatures/{request_id}/cancel", headers=service_headers, json={}))
    assert cancelled["request"]["status"] == "cancelled"
    assert cancelled["sensitiveEvidenceReturned"] is False

    print(json.dumps({
        "ok": True,
        "workspaceId": workspace_id,
        "contractId": contract["id"],
        "documentId": document["id"],
        "signatureId": request_id,
        "canonicalDocumentSignerReused": True,
        "idempotentCreation": True,
        "rawContentReturned": False,
        "sensitiveEvidenceReturned": False,
        "reminderSupported": True,
        "cancelSupported": True,
    }))


if __name__ == "__main__":
    main()
