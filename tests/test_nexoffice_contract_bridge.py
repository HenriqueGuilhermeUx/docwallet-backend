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


def register_and_connect(client, workspace_id, suffix):
    registered = assert_ok(client.post("/api/auth/register", json={
        "name": f"Contract Bridge {suffix}",
        "email": f"contract-{suffix}-{uuid.uuid4().hex[:8]}@example.test",
        "password": "StrongPass123!",
    }), 201)
    token = registered["token"]
    headers = {"Authorization": f"Bearer {token}"}
    connected = assert_ok(client.post(
        "/api/integrations/nexoffice/connect",
        headers=headers,
        json={"token": connect_token(workspace_id)},
    ))
    assert connected["connection"]["workspaceId"] == workspace_id
    return token, headers


def main():
    client = app.test_client()
    workspace_id = str(uuid.uuid4())
    _token, user_headers = register_and_connect(client, workspace_id, "owner")

    service_headers = {
        "X-NexOffice-Key": os.environ["NEXOFFICE_SERVICE_KEY"],
        "X-NexOffice-Workspace-ID": workspace_id,
    }

    templates = assert_ok(client.get(
        "/api/internal/nexoffice/contracts/templates",
        headers=service_headers,
    ))
    assert templates["source"] == "docwallet"
    assert templates["externalEffects"] is False
    assert any(item.get("id") == "prestacao_servicos" for item in templates["templates"])

    before_contracts = assert_ok(client.get("/api/contracts", headers=user_headers))["contracts"]
    before_documents = assert_ok(client.get("/api/documents", headers=user_headers))["documents"]
    assert len(before_contracts) == 0
    assert len(before_documents) == 0

    create_headers = {**service_headers, "X-Idempotency-Key": "contract-ci-1"}
    body = {
        "type": "prestacao_servicos",
        "party_a": "NexOffice Cliente Ltda.",
        "party_b": "Fornecedor Exemplo Ltda.",
        "description": "Prestação mensal de serviços de marketing e atendimento comercial.",
    }
    created = assert_ok(client.post(
        "/api/internal/nexoffice/contracts/create",
        headers=create_headers,
        json=body,
    ), 201)

    assert created["success"] is True
    assert created["contentReturned"] is False
    assert created["rawFilesReturned"] is False
    assert created["signatureRequested"] is False
    assert created["externalEffects"] is False
    assert "content" not in created["contract"]
    assert created["contract"]["contractType"] == "prestacao_servicos"
    assert created["document"]["type"] == "contract"
    assert created["document"]["fileHash"] == created["contract"]["contentHash"]

    repeated = assert_ok(client.post(
        "/api/internal/nexoffice/contracts/create",
        headers=create_headers,
        json=body,
    ), 201)
    assert repeated["contract"]["id"] == created["contract"]["id"]
    assert repeated["document"]["id"] == created["document"]["id"]

    contracts = assert_ok(client.get("/api/contracts", headers=user_headers))["contracts"]
    documents = assert_ok(client.get("/api/documents", headers=user_headers))["documents"]
    assert len(contracts) == 1
    assert len(documents) == 1

    metadata = assert_ok(client.get(
        f"/api/internal/nexoffice/documents/{created['document']['id']}",
        headers=service_headers,
    ))
    assert metadata["document"]["id"] == created["document"]["id"]

    # A workspace with more than one linked DocWallet owner is intentionally
    # rejected until NexOffice can select an explicit owner deterministically.
    register_and_connect(client, workspace_id, "second-owner")
    ambiguous = client.post(
        "/api/internal/nexoffice/contracts/create",
        headers={**service_headers, "X-Idempotency-Key": "contract-ci-2"},
        json=body,
    )
    assert ambiguous.status_code == 409, (ambiguous.status_code, ambiguous.get_json())
    assert ambiguous.get_json().get("code") == "docwallet_owner_ambiguous"

    print(json.dumps({
        "ok": True,
        "workspaceId": workspace_id,
        "contractId": created["contract"]["id"],
        "documentId": created["document"]["id"],
        "idempotent": True,
        "contentReturned": False,
        "rawFilesReturned": False,
        "ambiguousOwnerBlocked": True,
    }))


if __name__ == "__main__":
    main()
