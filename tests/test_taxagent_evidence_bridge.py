import base64
import json
import os
import uuid

os.environ.setdefault("TAXAGENT_EVIDENCE_BRIDGE_ENABLED", "true")
os.environ.setdefault("DOCWALLET_TAXAGENT_SERVICE_KEY", "ci-taxagent-evidence-key")
os.environ.setdefault("DOCUMENT_INTELLIGENCE_ENABLED", "true")
os.environ.setdefault("DOCUMENT_INTELLIGENCE_PROVIDER", "internal")
os.environ.setdefault("DOCUMENT_INTELLIGENCE_ALLOW_EXTERNAL", "false")
os.environ.setdefault("UPLOAD_DIR", "/tmp/docwallet-taxagent-evidence")

from app import app


def assert_ok(response, expected=None):
    if expected is not None:
        assert response.status_code == expected, (response.status_code, response.get_json())
    else:
        assert 200 <= response.status_code < 300, (response.status_code, response.get_json())
    return response.get_json()


def main():
    client = app.test_client()
    company_id = f"comp_ci_{uuid.uuid4().hex[:12]}"
    headers = {"X-TaxAgent-Key": os.environ["DOCWALLET_TAXAGENT_SERVICE_KEY"]}

    health = assert_ok(client.get("/api/internal/taxagent/health", headers=headers))
    assert health["service"] == "docwallet-taxagent-evidence-bridge"
    assert health["rawFilesReturned"] is False
    assert health["rawTextReturned"] is False

    provisioned = assert_ok(client.post(
        f"/api/internal/taxagent/companies/{company_id}/provision",
        headers=headers,
        json={"companyLabel": "Empresa Fiscal CI"},
    ), 201)
    assert provisioned["provisioned"] is True
    provisioned_again = assert_ok(client.post(
        f"/api/internal/taxagent/companies/{company_id}/provision",
        headers=headers,
        json={"companyLabel": "Empresa Fiscal CI"},
    ))
    assert provisioned_again["reused"] is True

    content = b"CONTRATO DE PRESTACAO DE SERVICOS\nValor total R$ 4.850,00\nVigencia 31/12/2026\nPagamento 30/11/2026"
    uploaded = assert_ok(client.post(
        f"/api/internal/taxagent/companies/{company_id}/documents",
        headers=headers,
        json={
            "filename": "contrato-fiscal-ci.txt",
            "title": "Contrato Fiscal CI",
            "mimeType": "text/plain",
            "documentType": "contract",
            "category": "customer-operation",
            "dataBase64": base64.b64encode(content).decode("ascii"),
        },
    ), 201)
    document_id = uploaded["document"]["id"]
    assert uploaded["document"]["sha256"]
    assert uploaded["document"]["rawFileReturned"] is False

    duplicated = assert_ok(client.post(
        f"/api/internal/taxagent/companies/{company_id}/documents",
        headers=headers,
        json={
            "filename": "contrato-fiscal-ci.txt",
            "title": "Contrato Fiscal CI",
            "mimeType": "text/plain",
            "documentType": "contract",
            "category": "customer-operation",
            "dataBase64": base64.b64encode(content).decode("ascii"),
        },
    ))
    assert duplicated["reused"] is True
    assert duplicated["document"]["id"] == document_id

    analyzed = assert_ok(client.post(
        f"/api/internal/taxagent/companies/{company_id}/documents/{document_id}/analyze",
        headers=headers,
        json={},
    ))
    intelligence = analyzed["intelligence"]
    assert intelligence["documentId"] == document_id
    assert intelligence["rawTextReturned"] is False
    assert intelligence["partyIdentifiersReturned"] is False
    assert "rawText" not in intelligence
    assert "parties" not in intelligence

    fetched = assert_ok(client.get(
        f"/api/internal/taxagent/companies/{company_id}/documents/{document_id}/intelligence",
        headers=headers,
    ))
    assert fetched["intelligence"]["id"] == intelligence["id"]
    assert "rawText" not in fetched["intelligence"]

    alerts = assert_ok(client.get(
        f"/api/internal/taxagent/companies/{company_id}/documents/{document_id}/alerts",
        headers=headers,
    ))
    assert isinstance(alerts["alerts"], list)

    audit = assert_ok(client.get(
        f"/api/internal/taxagent/companies/{company_id}/documents/{document_id}/audit",
        headers=headers,
    ))
    assert isinstance(audit["events"], list)
    serialized_audit = json.dumps(audit).lower()
    assert "raw_text" not in serialized_audit
    assert "password" not in serialized_audit

    summary = assert_ok(client.get(
        f"/api/internal/taxagent/companies/{company_id}/evidence-summary",
        headers=headers,
    ))
    assert summary["summary"]["documents"] == 1
    assert summary["summary"]["analyzed"] == 1
    assert summary["summary"]["hashCoverage"] == 1.0
    assert summary["rawFilesReturned"] is False

    denied = client.get("/api/internal/taxagent/health", headers={"X-TaxAgent-Key": "wrong"})
    assert denied.status_code == 401, (denied.status_code, denied.get_json())

    print(json.dumps({
        "ok": True,
        "companyId": company_id,
        "documentId": document_id,
        "intelligenceId": intelligence["id"],
        "rawFilesReturned": False,
        "rawTextReturned": False,
        "separateTaxAgentVault": True,
    }))


if __name__ == "__main__":
    main()
