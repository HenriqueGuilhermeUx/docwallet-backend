def install_nexoffice_bridge(app, db, User, Document, auth_required, fail, log):
    """Secure service-to-service bridge between DocWallet and NexOffice.

    A DocWallet user explicitly links one or more NexOffice workspaces. Internal
    actions then require both the shared service key and an active workspace link
    owned by the document owner. Raw files are never returned by the bridge.
    """
    import base64
    import datetime as dt
    import hashlib
    import hmac
    import json
    import os
    import uuid
    from functools import wraps

    from flask import jsonify, request

    ENABLED = os.environ.get("NEXOFFICE_BRIDGE_ENABLED", "true").lower() == "true"
    SERVICE_KEY = os.environ.get("NEXOFFICE_SERVICE_KEY", "").strip()
    TOKEN_TTL_SECONDS = max(60, int(os.environ.get("NEXOFFICE_CONNECT_TOKEN_TTL_SECONDS", "600") or 600))

    class NexOfficeConnection(db.Model):
        __tablename__ = "nexoffice_connections"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        workspace_id = db.Column(db.String(64), nullable=False, index=True)
        user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
        status = db.Column(db.String(24), nullable=False, default="active", index=True)
        metadata_json = db.Column(db.JSON, nullable=True)
        connected_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
        revoked_at = db.Column(db.DateTime, nullable=True)
        updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)
        __table_args__ = (db.UniqueConstraint("workspace_id", "user_id", name="uq_nexoffice_workspace_user"),)

    with app.app_context():
        db.create_all()

    def b64url_decode(value):
        value = str(value or "")
        value += "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value.encode("ascii"))

    def verify_connect_token(token):
        if not SERVICE_KEY:
            return None, "service_key_not_configured"
        try:
            encoded, signature = str(token or "").split(".", 1)
            expected = hmac.new(SERVICE_KEY.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
            received = b64url_decode(signature)
            if not hmac.compare_digest(expected, received):
                return None, "invalid_signature"
            payload = json.loads(b64url_decode(encoded).decode("utf-8"))
            workspace_id = str(payload.get("workspaceId") or "").strip()
            exp = int(payload.get("exp") or 0)
            if not workspace_id:
                return None, "workspace_required"
            uuid.UUID(workspace_id)
            now = int(dt.datetime.utcnow().timestamp())
            if exp <= now or exp > now + TOKEN_TTL_SECONDS + 120:
                return None, "expired_token"
            return payload, None
        except Exception:
            return None, "invalid_token"

    def service_key_from_request():
        direct = request.headers.get("X-NexOffice-Key", "").strip()
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
                return fail("NexOffice bridge desabilitado.", 503)
            if not SERVICE_KEY:
                return fail("NexOffice bridge não configurado.", 503)
            supplied = service_key_from_request()
            if not supplied or not hmac.compare_digest(supplied, SERVICE_KEY):
                return fail("Credencial NexOffice inválida.", 401)
            return fn(*args, **kwargs)
        return wrapper

    def workspace_from_request(required=True):
        workspace_id = (request.headers.get("X-NexOffice-Workspace-ID") or "").strip()
        if not workspace_id:
            return (None, "workspace_required") if required else (None, None)
        try:
            uuid.UUID(workspace_id)
            return workspace_id, None
        except Exception:
            return None, "invalid_workspace"

    def active_connection(workspace_id, user_id):
        return NexOfficeConnection.query.filter_by(workspace_id=workspace_id, user_id=user_id, status="active").first()

    def connected_document(workspace_id, document_id):
        document = db.session.get(Document, document_id)
        if not document:
            return None, "document_not_found"
        if not active_connection(workspace_id, document.user_id):
            return None, "workspace_not_connected"
        return document, None

    def invoke_user_view(endpoint, user, *args, **kwargs):
        view = app.view_functions.get(endpoint)
        if not view:
            return fail("Capability DocWallet indisponível.", 503)
        original = getattr(view, "__wrapped__", None)
        if not original:
            return fail("Capability DocWallet não exposta para integração segura.", 503)
        request.user = user
        return original(*args, **kwargs)

    @app.get("/api/internal/nexoffice/health")
    @require_service
    def nexoffice_internal_health():
        workspace_id, error = workspace_from_request(required=False)
        linked_users = 0
        if workspace_id and not error:
            linked_users = NexOfficeConnection.query.filter_by(workspace_id=workspace_id, status="active").count()
        return jsonify({
            "success": True,
            "status": "ok",
            "service": "docwallet-nexoffice-bridge",
            "workspaceConnected": bool(linked_users) if workspace_id else None,
            "linkedUsers": linked_users if workspace_id else None,
        })

    @app.post("/api/integrations/nexoffice/connect")
    @auth_required
    def nexoffice_connect():
        if not ENABLED:
            return fail("Integração NexOffice não está habilitada.", 503)
        body = request.get_json(silent=True) or {}
        payload, error = verify_connect_token(body.get("token"))
        if error:
            return fail("Token de conexão NexOffice inválido ou expirado.", 400, {"code": error})
        workspace_id = str(payload["workspaceId"])
        row = NexOfficeConnection.query.filter_by(workspace_id=workspace_id, user_id=request.user.id).first()
        if not row:
            row = NexOfficeConnection(workspace_id=workspace_id, user_id=request.user.id, status="active", metadata_json={"source": "nexoffice_connect_token"})
            db.session.add(row)
        else:
            row.status = "active"
            row.revoked_at = None
            row.metadata_json = {"source": "nexoffice_connect_token"}
        db.session.commit()
        log("integration.nexoffice.connected", request.user.id, "nexoffice_connection", row.id, {"workspace_id": workspace_id})
        return jsonify({"success": True, "connection": {"id": row.id, "workspaceId": workspace_id, "status": row.status, "connectedAt": row.connected_at.isoformat() + "Z"}})

    @app.get("/api/integrations/nexoffice/connections")
    @auth_required
    def nexoffice_connections():
        rows = NexOfficeConnection.query.filter_by(user_id=request.user.id).order_by(NexOfficeConnection.updated_at.desc()).all()
        return jsonify({"success": True, "connections": [{
            "id": row.id,
            "workspaceId": row.workspace_id,
            "status": row.status,
            "connectedAt": row.connected_at.isoformat() + "Z",
            "revokedAt": row.revoked_at.isoformat() + "Z" if row.revoked_at else None,
        } for row in rows]})

    @app.delete("/api/integrations/nexoffice/connections/<workspace_id>")
    @auth_required
    def nexoffice_disconnect(workspace_id):
        try:
            uuid.UUID(workspace_id)
        except Exception:
            return fail("Workspace inválido.", 400)
        row = NexOfficeConnection.query.filter_by(workspace_id=workspace_id, user_id=request.user.id, status="active").first()
        if not row:
            return fail("Conexão não encontrada.", 404)
        row.status = "revoked"
        row.revoked_at = dt.datetime.utcnow()
        db.session.commit()
        log("integration.nexoffice.revoked", request.user.id, "nexoffice_connection", row.id, {"workspace_id": workspace_id})
        return jsonify({"success": True})

    @app.get("/api/internal/nexoffice/documents/<document_id>")
    @require_service
    def nexoffice_document_metadata(document_id):
        workspace_id, error = workspace_from_request()
        if error:
            return fail("Workspace NexOffice ausente ou inválido.", 400, {"code": error})
        document, error = connected_document(workspace_id, document_id)
        if error == "document_not_found":
            return fail("Documento não encontrado.", 404)
        if error:
            return fail("Documento não autorizado para este workspace.", 403)
        return jsonify({"success": True, "document": {
            "id": document.id,
            "name": document.name,
            "type": document.doc_type,
            "category": document.category,
            "fileHash": document.file_hash,
            "isNotarized": bool(document.is_notarized),
            "certificateId": document.certificate_id,
            "createdAt": document.created_at.isoformat() + "Z",
        }})

    @app.post("/api/internal/nexoffice/documents/<document_id>/analyze")
    @require_service
    def nexoffice_analyze_document(document_id):
        workspace_id, error = workspace_from_request()
        if error:
            return fail("Workspace NexOffice ausente ou inválido.", 400, {"code": error})
        document, error = connected_document(workspace_id, document_id)
        if error == "document_not_found":
            return fail("Documento não encontrado.", 404)
        if error:
            return fail("Documento não autorizado para este workspace.", 403)
        user = db.session.get(User, document.user_id)
        if not user:
            return fail("Proprietário do documento não encontrado.", 404)
        response = invoke_user_view("analyze_document", user, document_id)
        log("nexoffice.document.analyze", user.id, "document", document_id, {"workspace_id": workspace_id})
        return response

    @app.post("/api/internal/nexoffice/documents/<document_id>/signature-request")
    @require_service
    def nexoffice_signature_request(document_id):
        workspace_id, error = workspace_from_request()
        if error:
            return fail("Workspace NexOffice ausente ou inválido.", 400, {"code": error})
        document, error = connected_document(workspace_id, document_id)
        if error == "document_not_found":
            return fail("Documento não encontrado.", 404)
        if error:
            return fail("Documento não autorizado para este workspace.", 403)
        body = request.get_json(silent=True) or {}
        parties = body.get("parties") or body.get("signers") or []
        if not isinstance(parties, list) or not parties:
            return fail("Informe pelo menos um signatário.", 400)
        # The existing intelligence route accepts `parties`; keep the bridge payload aligned.
        body["parties"] = parties
        user = db.session.get(User, document.user_id)
        if not user:
            return fail("Proprietário do documento não encontrado.", 404)
        response = invoke_user_view("create_signature_from_document", user, document_id)
        log("nexoffice.document.signature_request", user.id, "document", document_id, {"workspace_id": workspace_id, "signers": len(parties)})
        return response

    print("DocWallet NexOffice bridge installed.")
