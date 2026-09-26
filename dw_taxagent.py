def install_taxagent_bridge(app, db, User, Document, fail, log, hash_password, upload_dir):
    """Privacy-safe DocWallet evidence bridge for TaxAgent.

    TaxAgent owns the fiscal experience. DocWallet stays the canonical owner of
    document bytes, document intelligence and evidence. A dedicated internal
    DocWallet user is provisioned per TaxAgent company; no DocWallet login or
    customer-facing DocWallet account is required.
    """
    import base64
    import datetime as dt
    import hashlib
    import hmac
    import os
    import secrets
    import uuid
    from functools import wraps
    from pathlib import Path

    from flask import jsonify, request
    from sqlalchemy import text

    ENABLED = os.environ.get("TAXAGENT_EVIDENCE_BRIDGE_ENABLED", "false").lower() == "true"
    SERVICE_KEY = os.environ.get("DOCWALLET_TAXAGENT_SERVICE_KEY", "").strip()
    MAX_BYTES = max(1024, int(os.environ.get("TAXAGENT_EVIDENCE_MAX_BYTES", str(10 * 1024 * 1024))))
    ROOT = Path(upload_dir)

    class TaxAgentCompanyConnection(db.Model):
        __tablename__ = "taxagent_company_connections"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        company_id = db.Column(db.String(120), nullable=False, unique=True, index=True)
        user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, unique=True, index=True)
        company_label = db.Column(db.String(200), nullable=True)
        status = db.Column(db.String(24), nullable=False, default="active", index=True)
        metadata_json = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)

    with app.app_context():
        db.create_all()

    def service_key_from_request():
        direct = (request.headers.get("X-TaxAgent-Key") or "").strip()
        if direct:
            return direct
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip()
        return ""

    def require_service(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not ENABLED:
                return fail("TaxAgent evidence bridge desabilitado.", 503, {"code": "taxagent_evidence_disabled"})
            if not SERVICE_KEY:
                return fail("TaxAgent evidence bridge não configurado.", 503, {"code": "taxagent_evidence_not_configured"})
            supplied = service_key_from_request()
            if not supplied or not hmac.compare_digest(supplied, SERVICE_KEY):
                return fail("Credencial TaxAgent inválida.", 401)
            return fn(*args, **kwargs)
        return wrapper

    def connection(company_id):
        return TaxAgentCompanyConnection.query.filter_by(company_id=str(company_id), status="active").first()

    def company_user(company_id):
        row = connection(company_id)
        if not row:
            return None, None
        return row, db.session.get(User, row.user_id)

    def internal_email(company_id):
        digest = hashlib.sha256(str(company_id).encode("utf-8")).hexdigest()[:24]
        return f"taxagent-{digest}@internal.docwallet.invalid"

    def document_for_company(company_id, document_id):
        row, user = company_user(company_id)
        if not row or not user:
            return None, None, fail("Empresa TaxAgent ainda não provisionada no cofre documental.", 404, {"code": "company_not_provisioned"})
        document = Document.query.filter_by(id=document_id, user_id=user.id).first()
        if not document:
            return None, None, fail("Documento não encontrado para esta empresa.", 404)
        return document, user, None

    def safe_document(document):
        return {
            "id": document.id,
            "name": document.name,
            "originalFilename": document.original_filename,
            "mimeType": document.file_type,
            "fileSize": int(document.file_size or 0),
            "sha256": document.file_hash,
            "type": document.doc_type,
            "category": document.category,
            "isNotarized": bool(document.is_notarized),
            "certificateId": document.certificate_id,
            "createdAt": document.created_at.isoformat() + "Z" if document.created_at else None,
            "updatedAt": document.updated_at.isoformat() + "Z" if document.updated_at else None,
            "rawFileReturned": False,
        }

    def invoke_user_view(endpoint, user, *args, **kwargs):
        view = app.view_functions.get(endpoint)
        if not view:
            return fail("Capability DocWallet indisponível.", 503, {"code": "capability_unavailable"})
        original = getattr(view, "__wrapped__", None)
        if not original:
            return fail("Capability DocWallet não exposta para integração segura.", 503, {"code": "capability_not_bridge_safe"})
        request.user = user
        return original(*args, **kwargs)

    def unpack_response(response):
        status = 200
        body = response
        if isinstance(response, tuple):
            body = response[0]
            if len(response) > 1 and isinstance(response[1], int):
                status = response[1]
        elif hasattr(response, "status_code"):
            status = int(response.status_code)
        payload = None
        if hasattr(body, "get_json"):
            try:
                payload = body.get_json(silent=True)
            except TypeError:
                payload = body.get_json()
        return (payload if payload is not None else {"success": 200 <= status < 300}), status

    def sanitize_intelligence(payload):
        if not isinstance(payload, dict):
            return None
        intel = payload.get("intelligence") if "intelligence" in payload else payload
        if not isinstance(intel, dict):
            return None
        def keep(items, fields):
            out = []
            for item in items or []:
                if isinstance(item, dict):
                    out.append({key: item.get(key) for key in fields if key in item})
            return out
        return {
            "id": intel.get("id"),
            "documentId": intel.get("documentId"),
            "provider": intel.get("provider"),
            "documentType": intel.get("documentType"),
            "title": intel.get("title"),
            "issuer": intel.get("issuer"),
            "documentNumber": intel.get("documentNumber"),
            "issueDate": intel.get("issueDate"),
            "expirationDate": intel.get("expirationDate"),
            "summary": intel.get("summary"),
            "confidence": intel.get("confidence"),
            "status": intel.get("status"),
            "reviewed": bool(intel.get("reviewed")),
            "versionNumber": intel.get("versionNumber"),
            "dates": keep(intel.get("dates"), ["kind", "label", "value", "confidence"]),
            "amounts": keep(intel.get("amounts"), ["kind", "label", "currency", "value", "confidence"]),
            "obligations": keep(intel.get("obligations"), ["description", "dueDate", "status", "confidence"]),
            "alerts": keep(intel.get("alerts"), ["id", "type", "title", "message", "dueDate", "severity", "status"]),
            "versions": keep(intel.get("versions"), ["id", "versionNumber", "fileHash", "lifecycleStatus", "createdAt"]),
            "rawTextReturned": False,
            "partyIdentifiersReturned": False,
            "partyContactsReturned": False,
        }

    def sanitize_alerts(payload):
        alerts = payload.get("alerts") if isinstance(payload, dict) else []
        result = []
        for item in alerts or []:
            if not isinstance(item, dict):
                continue
            result.append({key: item.get(key) for key in ["id", "type", "title", "message", "dueDate", "severity", "status", "entityType", "createdAt"] if key in item})
        return result

    def sanitize_audit(payload):
        events = payload.get("events") if isinstance(payload, dict) else []
        result = []
        for item in events or []:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            safe_meta = {k: v for k, v in metadata.items() if k in {"document_type", "confidence", "provider", "fields", "file_hash", "version_number"}}
            result.append({"id": item.get("id"), "action": item.get("action"), "resourceType": item.get("resourceType"), "resourceId": item.get("resourceId"), "metadata": safe_meta, "createdAt": item.get("createdAt")})
        return result

    @app.get("/api/internal/taxagent/health")
    @require_service
    def taxagent_evidence_health():
        return jsonify({
            "success": True,
            "status": "ok",
            "service": "docwallet-taxagent-evidence-bridge",
            "capabilities": [
                "company.evidence_vault.provision",
                "documents.upload",
                "documents.metadata.read",
                "documents.analyze",
                "documents.intelligence.read",
                "documents.alerts.read",
                "documents.audit.read",
                "evidence.summary.read",
            ],
            "rawFilesReturned": False,
            "rawTextReturned": False,
            "privateEvidenceReturned": False,
        })

    @app.post("/api/internal/taxagent/companies/<company_id>/provision")
    @require_service
    def taxagent_provision_company(company_id):
        body = request.get_json(silent=True) or {}
        label = str(body.get("companyLabel") or company_id).strip()[:200]
        existing = TaxAgentCompanyConnection.query.filter_by(company_id=company_id).first()
        if existing:
            existing.status = "active"
            existing.company_label = label or existing.company_label
            existing.metadata_json = {"source": "taxagent", "customerVisibleBrand": "TaxAgent"}
            db.session.commit()
            return jsonify({"success": True, "companyId": company_id, "vaultId": existing.id, "provisioned": True, "reused": True})
        user = User(
            name=f"TaxAgent Evidence · {label}"[:160],
            email=internal_email(company_id),
            password_hash=hash_password(secrets.token_urlsafe(48)),
            plan="free",
        )
        db.session.add(user)
        db.session.flush()
        row = TaxAgentCompanyConnection(company_id=company_id, user_id=user.id, company_label=label, status="active", metadata_json={"source": "taxagent", "customerVisibleBrand": "TaxAgent"})
        db.session.add(row)
        db.session.commit()
        log("integration.taxagent.evidence_vault_provisioned", user.id, "taxagent_company_connection", row.id, {"company_id": company_id})
        return jsonify({"success": True, "companyId": company_id, "vaultId": row.id, "provisioned": True, "reused": False}), 201

    @app.get("/api/internal/taxagent/companies/<company_id>/documents")
    @require_service
    def taxagent_list_documents(company_id):
        row, user = company_user(company_id)
        if not row or not user:
            return fail("Empresa TaxAgent ainda não provisionada no cofre documental.", 404, {"code": "company_not_provisioned"})
        try:
            limit = max(1, min(200, int(request.args.get("limit", "50"))))
        except Exception:
            limit = 50
        documents = Document.query.filter_by(user_id=user.id).order_by(Document.created_at.desc()).limit(limit).all()
        return jsonify({"success": True, "companyId": company_id, "documents": [safe_document(document) for document in documents], "rawFilesReturned": False})

    @app.post("/api/internal/taxagent/companies/<company_id>/documents")
    @require_service
    def taxagent_upload_document(company_id):
        row, user = company_user(company_id)
        if not row or not user:
            return fail("Empresa TaxAgent ainda não provisionada no cofre documental.", 404, {"code": "company_not_provisioned"})
        body = request.get_json(silent=True) or {}
        encoded = str(body.get("dataBase64") or "").strip()
        filename = str(body.get("filename") or "documento.bin").strip()[:255]
        title = str(body.get("title") or filename).strip()[:255]
        mime_type = str(body.get("mimeType") or "application/octet-stream").strip()[:120]
        doc_type = str(body.get("documentType") or "other").strip()[:80]
        category = str(body.get("category") or "taxagent-evidence").strip()[:80]
        if not encoded:
            return fail("dataBase64 é obrigatório.", 400)
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception:
            return fail("dataBase64 inválido.", 400)
        if not data:
            return fail("Documento vazio.", 400)
        if len(data) > MAX_BYTES:
            return fail("Documento excede o limite do bridge TaxAgent.", 413, {"maxBytes": MAX_BYTES})
        digest = hashlib.sha256(data).hexdigest()
        existing = Document.query.filter_by(user_id=user.id, file_hash=digest).order_by(Document.created_at.desc()).first()
        if existing:
            return jsonify({"success": True, "companyId": company_id, "document": safe_document(existing), "reused": True})
        user_dir = ROOT / user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix[:20]
        stored_filename = f"taxagent-{uuid.uuid4().hex}{suffix}"
        path = user_dir / stored_filename
        path.write_bytes(data)
        document = Document(
            user_id=user.id,
            name=title or filename,
            original_filename=filename or "documento.bin",
            stored_filename=stored_filename,
            file_path=str(path),
            file_type=mime_type,
            file_size=len(data),
            file_hash=digest,
            doc_type=doc_type or "other",
            category=category or "taxagent-evidence",
        )
        db.session.add(document)
        db.session.commit()
        log("integration.taxagent.document_received", user.id, "document", document.id, {"company_id": company_id, "file_hash": digest, "category": document.category})
        return jsonify({"success": True, "companyId": company_id, "document": safe_document(document), "reused": False}), 201

    @app.get("/api/internal/taxagent/companies/<company_id>/documents/<document_id>")
    @require_service
    def taxagent_document_metadata(company_id, document_id):
        document, _user, denied = document_for_company(company_id, document_id)
        if denied:
            return denied
        return jsonify({"success": True, "companyId": company_id, "document": safe_document(document)})

    @app.post("/api/internal/taxagent/companies/<company_id>/documents/<document_id>/analyze")
    @require_service
    def taxagent_analyze_document(company_id, document_id):
        document, user, denied = document_for_company(company_id, document_id)
        if denied:
            return denied
        payload, status = unpack_response(invoke_user_view("analyze_document", user, document.id))
        if status >= 300:
            return jsonify(payload), status
        return jsonify({"success": True, "companyId": company_id, "intelligence": sanitize_intelligence(payload), "rawTextReturned": False}), status

    @app.get("/api/internal/taxagent/companies/<company_id>/documents/<document_id>/intelligence")
    @require_service
    def taxagent_document_intelligence(company_id, document_id):
        document, user, denied = document_for_company(company_id, document_id)
        if denied:
            return denied
        payload, status = unpack_response(invoke_user_view("get_document_intelligence", user, document.id))
        if status >= 300:
            return jsonify(payload), status
        return jsonify({"success": True, "companyId": company_id, "intelligence": sanitize_intelligence(payload), "rawTextReturned": False}), status

    @app.get("/api/internal/taxagent/companies/<company_id>/documents/<document_id>/alerts")
    @require_service
    def taxagent_document_alerts(company_id, document_id):
        document, user, denied = document_for_company(company_id, document_id)
        if denied:
            return denied
        payload, status = unpack_response(invoke_user_view("get_document_alerts", user, document.id))
        if status >= 300:
            return jsonify(payload), status
        return jsonify({"success": True, "companyId": company_id, "alerts": sanitize_alerts(payload)}), status

    @app.get("/api/internal/taxagent/companies/<company_id>/documents/<document_id>/audit")
    @require_service
    def taxagent_document_audit(company_id, document_id):
        document, user, denied = document_for_company(company_id, document_id)
        if denied:
            return denied
        payload, status = unpack_response(invoke_user_view("get_audit_trail", user, document.id))
        if status >= 300:
            return jsonify(payload), status
        return jsonify({"success": True, "companyId": company_id, "events": sanitize_audit(payload)}), status

    @app.get("/api/internal/taxagent/companies/<company_id>/evidence-summary")
    @require_service
    def taxagent_evidence_summary(company_id):
        row, user = company_user(company_id)
        if not row or not user:
            return fail("Empresa TaxAgent ainda não provisionada no cofre documental.", 404, {"code": "company_not_provisioned"})
        documents = Document.query.filter_by(user_id=user.id).order_by(Document.created_at.desc()).limit(200).all()
        analyzed = 0
        active_alerts = 0
        urgent_alerts = 0
        expiring = 0
        for document in documents:
            intel_payload, intel_status = unpack_response(invoke_user_view("get_document_intelligence", user, document.id))
            intel = sanitize_intelligence(intel_payload) if intel_status < 300 else None
            if intel:
                analyzed += 1
                for alert in intel.get("alerts") or []:
                    active_alerts += 1
                    if alert.get("severity") in {"danger", "warning"}:
                        urgent_alerts += 1
                    if alert.get("type") in {"document.expiring", "contract.renewal_upcoming"}:
                        expiring += 1
        return jsonify({
            "success": True,
            "companyId": company_id,
            "summary": {
                "documents": len(documents),
                "analyzed": analyzed,
                "activeAlerts": active_alerts,
                "attentionAlerts": urgent_alerts,
                "expiringEvidence": expiring,
                "hashCoverage": 1.0 if documents else 0.0,
            },
            "recentDocuments": [safe_document(document) for document in documents[:20]],
            "rawFilesReturned": False,
            "rawTextReturned": False,
        })

    print("DocWallet TaxAgent evidence bridge installed.")
