import base64
import hashlib
import hmac
import io
import json
import os
import time
import uuid

os.environ.setdefault("NEXOFFICE_BRIDGE_ENABLED", "true")
os.environ.setdefault("NEXOFFICE_SERVICE_KEY", "ci-nexoffice-service-key")
os.environ.setdefault("DOCUMENT_INTELLIGENCE_ENABLED", "true")
os.environ.setdefault("DOCUMENT_INTELLIGENCE_PROVIDER", "internal")

from app import app


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def connect_token(workspace_id: str) -> str:
    payload = {
        "workspaceId": workspace_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "nonce": uuid.uuid4().hex,
    }
    encoded = b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(os.environ["NEXOFFICE_SERVICE_KEY"].encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{b64url(signature)}"


def assert_ok(response, expected=None):
    if expected is not None:
        assert response.status_code == expected, (response.status_code, response.get_json())
    else:
        assert 200 <= response.status_code < 300, (response.status_code, response.get_json())
    return response.get_json()


def main():
    client = app.test_client()
    workspace_id = str(uuid.uuid4())

    registered = assert_ok(client.post("/api/auth/register", json={
        "name": "NexOffice Bridge CI",
        "email": f"bridge-{uuid.uuid4().hex[:8]}@example.test",
        "password": "StrongPass123!",
    }), 201)
    user_token = registered["token"]
    user_headers = {"Authorization": f"Bearer {user_token}"}

    connected = assert_ok(client.post(
        "/api/integrations/nexoffice/connect",
        headers=user_headers,
        json={"token": connect_token(workspace_id)},
    ))
    assert connected["connection"]["workspaceId"] == workspace_id

    uploaded = assert_ok(client.post(
        "/api/documents/upload",
        headers=user_headers,
        data={
            "file": (io.BytesIO(b"CONTRATO DE TESTE\nValor total R$ 1.250,00\nVigencia 31/12/2026"), "contrato.txt"),
            "name": "Contrato NexOffice CI",
            "type": "contract",
            "category": "contracts",
        },
        content_type="multipart/form-data",
    ), 201)
    document_id = uploaded["document"]["id"]

    service_headers = {
        "X-NexOffice-Key": os.environ["NEXOFFICE_SERVICE_KEY"],
        "X-NexOffice-Workspace-ID": workspace_id,
    }
    health = assert_ok(client.get("/api/internal/nexoffice/health", headers=service_headers))
    assert health["workspaceConnected"] is True
    assert health["linkedUsers"] == 1
    assert "documents.intelligence.read" in health["capabilities"]
    assert "documents.alerts.read" in health["capabilities"]
    assert health["rawFilesReturned"] is False
    assert health["rawTextReturned"] is False

    analyze_headers = {**service_headers, "X-Idempotency-Key": "ci-analyze-1"}
    analyzed = assert_ok(client.post(
        f"/api/internal/nexoffice/documents/{document_id}/analyze",
        headers=analyze_headers,
        json={},
    ))
    assert analyzed["success"] is True
    intelligence_id = analyzed["intelligence"]["id"]

    analyzed_again = assert_ok(client.post(
        f"/api/internal/nexoffice/documents/{document_id}/analyze",
        headers=analyze_headers,
        json={},
    ))
    assert analyzed_again["intelligence"]["id"] == intelligence_id

    intelligence = assert_ok(client.get(
        f"/api/internal/nexoffice/documents/{document_id}/intelligence?include_raw=true",
        headers=service_headers,
    ))
    assert intelligence["intelligence"]["id"] == intelligence_id
    assert "rawText" not in intelligence["intelligence"]
    assert intelligence["rawTextReturned"] is False
    assert intelligence["intelligence"]["amounts"]

    alerts = assert_ok(client.get(
        f"/api/internal/nexoffice/documents/{document_id}/alerts",
        headers=service_headers,
    ))
    assert isinstance(alerts["alerts"], list)

    upcoming = assert_ok(client.get(
        "/api/internal/nexoffice/contracts/upcoming-expirations?days=365",
        headers=service_headers,
    ))
    assert upcoming["workspaceId"] == workspace_id
    assert upcoming["days"] == 365
    assert isinstance(upcoming["alerts"], list)

    signature_headers = {**service_headers, "X-Idempotency-Key": "ci-sign-1"}
    signature = assert_ok(client.post(
        f"/api/internal/nexoffice/documents/{document_id}/signature-request",
        headers=signature_headers,
        json={"parties": [{"name": "Cliente CI", "email": "cliente-ci@example.test"}]},
    ), 201)
    request_id = signature["request"]["id"]

    signature_again = assert_ok(client.post(
        f"/api/internal/nexoffice/documents/{document_id}/signature-request",
        headers=signature_headers,
        json={"parties": [{"name": "Cliente CI", "email": "cliente-ci@example.test"}]},
    ), 201)
    assert signature_again["request"]["id"] == request_id

    assert_ok(client.delete(
        f"/api/integrations/nexoffice/connections/{workspace_id}",
        headers=user_headers,
    ))
    denied = client.get(f"/api/internal/nexoffice/documents/{document_id}", headers=service_headers)
    assert denied.status_code == 403, (denied.status_code, denied.get_json())
    denied_intelligence = client.get(f"/api/internal/nexoffice/documents/{document_id}/intelligence", headers=service_headers)
    assert denied_intelligence.status_code == 403, (denied_intelligence.status_code, denied_intelligence.get_json())

    print(json.dumps({
        "ok": True,
        "workspaceId": workspace_id,
        "documentId": document_id,
        "intelligenceId": intelligence_id,
        "signatureRequestId": request_id,
        "readCapabilities": True,
        "rawTextReturned": False,
    }))


if __name__ == "__main__":
    main()
