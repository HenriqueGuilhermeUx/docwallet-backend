def install_mydatamed_concierge_bridge(app, db, User, fail, log):
    """Service-to-service bridge for MyDataMed Concierge legal documents.

    MyDataMed may create and check signature requests, but DocWallet remains the
    legal evidence authority. Raw evidence packages, private keys, certificate
    passwords and ICP provider redirects never cross this bridge.
    """
    import datetime as dt
    import hashlib
    import hmac
    import json
    import os
    import uuid
    from functools import wraps

    from flask import jsonify, request
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    ENABLED = os.environ.get("MYDATAMED_BRIDGE_ENABLED", "false").lower() == "true"
    SERVICE_KEY = os.environ.get("MYDATAMED_SERVICE_KEY", "").strip()
    OWNER_USER_ID = os.environ.get("MYDATAMED_DOCWALLET_OWNER_USER_ID", "").strip()
    PUBLIC_URL = (os.environ.get("DOCWALLET_PUBLIC_URL") or "https://trydocwallet.com").rstrip("/")

    ALLOWED_DOCUMENT_TYPES = {
        "service_terms",
        "privacy_consent",
        "representation_authorization",
        "combined_onboarding",
    }

    class MyDataMedConciergeOperation(db.Model):
        __tablename__ = "mydatamed_concierge_operations"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        idempotency_key = db.Column(db.String(220), nullable=False, unique=True, index=True)
        external_reference = db.Column(db.String(180), nullable=True, index=True)
        action_type = db.Column(db.String(80), nullable=False)
        signature_request_id = db.Column(db.String(36), nullable=True, index=True)
        status = db.Column(db.String(24), nullable=False, default="processing")
        response_status = db.Column(db.Integer, nullable=True)
        response_json = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)

    with app.app_context():
        db.create_all()

    def supplied_key():
        direct = (request.headers.get("X-MyDataMed-Key") or "").strip()
        if direct:
            return direct
        auth = request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip()
        return ""

    def require_service(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not ENABLED:
                return fail("Bridge MyDataMed desabilitado.", 503, {"code": "mydatamed_bridge_disabled"})
            if not SERVICE_KEY or not OWNER_USER_ID:
                return fail("Bridge MyDataMed não configurado.", 503, {"code": "mydatamed_bridge_not_configured"})
            key = supplied_key()
            if not key or not hmac.compare_digest(key, SERVICE_KEY):
                return fail("Credencial MyDataMed inválida.", 401, {"code": "invalid_service_key"})
            return fn(*args, **kwargs)
        return wrapped

    def owner_user():
        try:
            uuid.UUID(OWNER_USER_ID)
        except Exception:
            return None
        return db.session.get(User, OWNER_USER_ID)

    def invoke_user_view(endpoint, user, path, method="GET", payload=None, *args):
        view = app.view_functions.get(endpoint)
        original = getattr(view, "__wrapped__", None) if view else None
        if not original:
            return None, 503
        with app.test_request_context(path, method=method, json=payload or None):
            request.user = user
            response = original(*args)
            status = response[1] if isinstance(response, tuple) and len(response) > 1 and isinstance(response[1], int) else getattr(response, "status_code", 200)
            body = response[0] if isinstance(response, tuple) else response
            packed = body.get_json(silent=True) if hasattr(body, "get_json") else None
            return packed or {"success": 200 <= status < 300}, int(status)

    def safe_request(value):
        value = value or {}
        parties = value.get("parties") or []
        safe_parties = []
        for party in parties:
            if not isinstance(party, dict):
                continue
            code = str(party.get("code") or "")
            safe_parties.append({
                "id": str(party.get("id") or ""),
                "name": str(party.get("name") or ""),
                "email": str(party.get("email") or ""),
                "status": str(party.get("status") or "pending"),
                "signedAt": party.get("signed_at") or party.get("signedAt"),
                "signUrl": f"{PUBLIC_URL}/sign/{code}" if code else None,
                "evidenceLevel": str(party.get("evidence_level") or ""),
                "identityVerified": bool((party.get("identity_verification") or {}).get("verified_at")),
            })

        return {
            "id": str(value.get("id") or ""),
            "title": str(value.get("title") or ""),
            "status": str(value.get("status") or "pending"),
            "contentHash": str(value.get("content_hash") or value.get("contentHash") or ""),
            "finalHash": str(value.get("final_hash") or value.get("finalHash") or ""),
            "createdAt": value.get("created_at") or value.get("createdAt"),
            "completedAt": value.get("completed_at") or value.get("completedAt"),
            "parties": safe_parties,
        }

    def idempotency_key():
        value = (request.headers.get("X-Idempotency-Key") or "").strip()
        if not value:
            return None
        return value[:220]

    def complete_operation(op, payload, status):
        op.status = "succeeded" if 200 <= status < 300 else "failed"
        op.response_status = status
        op.response_json = payload
        if isinstance(payload, dict):
            req = payload.get("request") or {}
            if isinstance(req, dict) and req.get("id"):
                op.signature_request_id = str(req["id"])
        db.session.commit()
        return jsonify(payload), status

    @app.get("/api/internal/mydatamed/concierge/health")
    @require_service
    def mydatamed_concierge_health():
        user = owner_user()
        return jsonify({
            "success": True,
            "status": "ok" if user else "owner_missing",
            "service": "docwallet-mydatamed-concierge-bridge",
            "configured": bool(user),
            "capabilities": [
                "signatures.create",
                "signatures.status.read",
                "signatures.verified_evidence_required",
                "signatures.icp_brasil_available_on_docwallet",
            ],
            "privacy": {
                "rawEvidenceReturned": False,
                "privateKeysHandledByMyDataMed": False,
                "certificatePasswordsHandledByMyDataMed": False,
                "icpProviderRedirectReturnedToMyDataMed": False,
            },
        }), 200 if user else 503

    @app.post("/api/internal/mydatamed/concierge/signatures")
    @require_service
    def mydatamed_create_signature():
        user = owner_user()
        if not user:
            return fail("Conta proprietária DocWallet do MyDataMed não encontrada.", 503, {"code": "owner_user_missing"})

        key = idempotency_key()
        if not key:
            return fail("X-Idempotency-Key é obrigatório.", 400, {"code": "idempotency_key_required"})

        existing = MyDataMedConciergeOperation.query.filter_by(idempotency_key=key).first()
        if existing and existing.response_json is not None:
            return jsonify(existing.response_json), int(existing.response_status or 200)
        if existing and existing.status == "processing":
            return fail("Operação já está em processamento.", 409, {"code": "operation_in_progress"})

        body = request.get_json(silent=True) or {}
        document_type = str(body.get("documentType") or "").strip()
        external_reference = str(body.get("externalReference") or "").strip()[:180]
        title = str(body.get("title") or "").strip()[:240]
        content = str(body.get("content") or "").strip()
        signer = body.get("signer") if isinstance(body.get("signer"), dict) else {}

        if document_type not in ALLOWED_DOCUMENT_TYPES:
            return fail("Tipo de documento não permitido.", 400, {"code": "invalid_document_type"})
        if not external_reference:
            return fail("externalReference é obrigatório.", 400, {"code": "external_reference_required"})
        if len(content) < 80 or len(content) > 100000:
            return fail("Conteúdo do documento inválido.", 400, {"code": "invalid_document_content"})

        signer_name = str(signer.get("name") or "").strip()[:180]
        signer_email = str(signer.get("email") or "").strip().lower()[:180]
        signer_phone = str(signer.get("phone") or "").strip()[:80]
        if not signer_name or not signer_email:
            return fail("Nome e e-mail do signatário são obrigatórios.", 400, {"code": "signer_identity_required"})

        operation = existing or MyDataMedConciergeOperation(
            idempotency_key=key,
            external_reference=external_reference,
            action_type="signature.create",
            status="processing",
        )
        if not existing:
            db.session.add(operation)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            concurrent = MyDataMedConciergeOperation.query.filter_by(idempotency_key=key).first()
            if concurrent and concurrent.response_json is not None:
                return jsonify(concurrent.response_json), int(concurrent.response_status or 200)
            return fail("Operação concorrente já registrada.", 409, {"code": "operation_in_progress"})

        payload, status = invoke_user_view(
            "create_signature_request",
            user,
            "/api/signatures/request",
            "POST",
            {
                "title": title or "Documento MyDataMed Concierge",
                "contract_content": content,
                "parties": [{
                    "name": signer_name,
                    "email": signer_email,
                    "phone": signer_phone,
                }],
            },
        )

        if status >= 300 or not isinstance(payload, dict):
            return complete_operation(operation, payload or {"success": False}, status)

        request_payload = payload.get("request") or {}
        signature_id = str(request_payload.get("id") or "")
        if not signature_id:
            return complete_operation(operation, {"success": False, "error": "signature_request_missing"}, 502)

        db.session.execute(text("""
            INSERT INTO signature_events (id, request_id, event_type, payload, created_at)
            VALUES (:id, :request_id, 'mydatamed.concierge.context', CAST(:payload AS jsonb), NOW())
        """), {
            "id": str(uuid.uuid4()),
            "request_id": signature_id,
            "payload": json.dumps({
                "external_reference": external_reference,
                "document_type": document_type,
                "source": "mydatamed_concierge",
            }, ensure_ascii=False),
        })
        db.session.execute(text("""
            INSERT INTO signature_events (id, request_id, event_type, payload, created_at)
            VALUES (:id, :request_id, 'policy.verified_evidence_required', CAST(:payload AS jsonb), NOW())
        """), {
            "id": str(uuid.uuid4()),
            "request_id": signature_id,
            "payload": json.dumps({
                "reason": "mydatamed_concierge_legal_document",
                "minimum_evidence": "verified_evidence",
                "identity_method": "email_otp",
            }, ensure_ascii=False),
        })
        db.session.commit()

        safe = {
            "success": True,
            "request": safe_request(request_payload),
            "documentType": document_type,
            "externalReference": external_reference,
            "requiredEvidence": "verified_evidence",
            "signatureModes": {
                "default": "electronic",
                "icpBrasilAvailableOnDocWallet": True,
            },
            "privacy": {
                "rawEvidenceReturned": False,
                "rawContentReturnedAfterCreation": False,
            },
        }
        try:
            log("integration.mydatamed.signature_created", user.id, "signature", signature_id, {
                "external_reference": external_reference,
                "document_type": document_type,
            })
        except Exception:
            pass
        return complete_operation(operation, safe, 201)

    @app.get("/api/internal/mydatamed/concierge/signatures/<signature_id>")
    @require_service
    def mydatamed_signature_status(signature_id):
        user = owner_user()
        if not user:
            return fail("Conta proprietária DocWallet do MyDataMed não encontrada.", 503)
        payload, status = invoke_user_view(
            "read_signature_request",
            user,
            f"/api/signatures/{signature_id}",
            "GET",
            None,
            signature_id,
        )
        if status >= 300 or not isinstance(payload, dict):
            return jsonify(payload or {"success": False}), status

        request_payload = payload.get("request") or {}
        return jsonify({
            "success": True,
            "request": safe_request(request_payload),
            "privacy": {
                "rawEvidenceReturned": False,
                "rawContentReturned": False,
            },
        })

    print("DocWallet MyDataMed Concierge bridge installed.")
