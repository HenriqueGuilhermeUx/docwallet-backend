def install_nexoffice_contracts(app, db, User, Contract, Document, fail, log):
    """Narrow NexOffice -> DocWallet contract creation bridge.

    Creates a DocWallet draft contract for an already linked NexOffice workspace,
    converts it to a DocWallet Document, and returns metadata only. It never
    returns contract text, downloads raw files, requests signatures or triggers
    blockchain/payment side effects.
    """
    import datetime as dt
    import hmac
    import json
    import os
    import uuid
    from functools import wraps

    from flask import jsonify, request

    ENABLED = os.environ.get("NEXOFFICE_CONTRACT_CREATE_ENABLED", "false").lower() == "true"
    SERVICE_KEY = os.environ.get("NEXOFFICE_SERVICE_KEY", "").strip()

    class NexOfficeContractOperation(db.Model):
        __tablename__ = "nexoffice_contract_operations"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        workspace_id = db.Column(db.String(64), nullable=False, index=True)
        idempotency_key = db.Column(db.String(220), nullable=False)
        user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
        contract_id = db.Column(db.String(36), nullable=True, index=True)
        document_id = db.Column(db.String(36), nullable=True, index=True)
        status = db.Column(db.String(24), nullable=False, default="processing")
        response_json = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)
        __table_args__ = (db.UniqueConstraint("workspace_id", "idempotency_key", name="uq_nexoffice_contract_operation_key"),)

    with app.app_context():
        db.create_all()

    def service_key_from_request():
        direct = request.headers.get("X-NexOffice-Key", "").strip()
        if direct:
            return direct
        auth = request.headers.get("Authorization", "")
        return auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else ""

    def require_service(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not ENABLED:
                return fail("Criação de contratos via NexOffice desabilitada.", 503, {"code": "nexoffice_contract_create_disabled"})
            if not SERVICE_KEY:
                return fail("Bridge NexOffice não configurado.", 503, {"code": "nexoffice_service_key_missing"})
            supplied = service_key_from_request()
            if not supplied or not hmac.compare_digest(supplied, SERVICE_KEY):
                return fail("Credencial NexOffice inválida.", 401)
            return fn(*args, **kwargs)
        return wrapper

    def workspace_id():
        value = (request.headers.get("X-NexOffice-Workspace-ID") or "").strip()
        try:
            uuid.UUID(value)
            return value, None
        except Exception:
            return None, fail("Workspace NexOffice ausente ou inválido.", 400, {"code": "invalid_workspace"})

    def idempotency_key():
        value = (request.headers.get("X-Idempotency-Key") or "").strip()
        if not value:
            return None, fail("X-Idempotency-Key é obrigatório.", 400, {"code": "idempotency_key_required"})
        if len(value) > 220:
            return None, fail("X-Idempotency-Key é muito longo.", 400, {"code": "idempotency_key_too_long"})
        return value, None

    def linked_user(workspace):
        rows = db.session.execute(
            db.text("select user_id from nexoffice_connections where workspace_id=:workspace and status='active' order by connected_at asc limit 2"),
            {"workspace": workspace},
        ).fetchall()
        if not rows:
            return None, fail("Conecte uma conta DocWallet a este workspace antes de criar contratos.", 403, {"code": "docwallet_workspace_not_connected"})
        if len(rows) > 1:
            return None, fail("Há mais de um usuário DocWallet ligado ao workspace. Defina um proprietário antes de criar contratos.", 409, {"code": "docwallet_owner_ambiguous"})
        user = db.session.get(User, rows[0][0])
        if not user:
            return None, fail("Proprietário DocWallet não encontrado.", 404)
        return user, None

    def invoke_user_view(endpoint, user, *args):
        view = app.view_functions.get(endpoint)
        original = getattr(view, "__wrapped__", None) if view else None
        if not original:
            return None, 503
        request.user = user
        response = original(*args)
        status = response[1] if isinstance(response, tuple) and len(response) > 1 and isinstance(response[1], int) else getattr(response, "status_code", 200)
        body = response[0] if isinstance(response, tuple) else response
        payload = body.get_json(silent=True) if hasattr(body, "get_json") else None
        return payload or {"success": 200 <= status < 300}, int(status)

    def contract_meta(contract):
        return {
            "id": contract.id,
            "title": contract.title,
            "contractType": contract.contract_type,
            "status": contract.status,
            "contentHash": contract.content_hash,
            "certificateId": contract.certificate_id,
            "createdAt": contract.created_at.isoformat() + "Z",
        }

    def document_meta(document):
        return {
            "id": document.id,
            "name": document.name,
            "type": document.doc_type,
            "category": document.category,
            "fileHash": document.file_hash,
            "isNotarized": bool(document.is_notarized),
            "certificateId": document.certificate_id,
            "createdAt": document.created_at.isoformat() + "Z",
        }

    @app.get("/api/internal/nexoffice/contracts/templates")
    @require_service
    def nexoffice_contract_templates():
        workspace, denied = workspace_id()
        if denied:
            return denied
        _user, denied = linked_user(workspace)
        if denied:
            return denied
        view = app.view_functions.get("contract_templates")
        if not view:
            return fail("Catálogo de contratos indisponível.", 503)
        response = view()
        payload = response.get_json(silent=True) if hasattr(response, "get_json") else {}
        return jsonify({"success": True, "templates": payload.get("templates") or [], "source": "docwallet", "externalEffects": False})

    @app.post("/api/internal/nexoffice/contracts/create")
    @require_service
    def nexoffice_contract_create():
        workspace, denied = workspace_id()
        if denied:
            return denied
        key, denied = idempotency_key()
        if denied:
            return denied
        user, denied = linked_user(workspace)
        if denied:
            return denied

        body = request.get_json(silent=True) or {}
        contract_type = str(body.get("type") or "").strip()[:80]
        party_a = str(body.get("party_a") or "").strip()[:255]
        party_b = str(body.get("party_b") or "").strip()[:255]
        description = str(body.get("description") or "").strip()[:8000]
        if not contract_type or not party_a or not party_b or not description:
            return fail("type, party_a, party_b e description são obrigatórios.", 400)

        op = NexOfficeContractOperation.query.filter_by(workspace_id=workspace, idempotency_key=key).first()
        if op and op.status == "succeeded" and op.response_json:
            return jsonify(op.response_json), 201
        if op and op.status == "processing":
            return fail("Operação já está em processamento.", 409, {"code": "operation_in_progress"})
        if not op:
            op = NexOfficeContractOperation(workspace_id=workspace, idempotency_key=key, user_id=user.id, status="processing")
            db.session.add(op)
        else:
            op.user_id = user.id
            op.status = "processing"
        db.session.commit()

        try:
            contract = db.session.get(Contract, op.contract_id) if op.contract_id else None
            if not contract:
                payload, status = invoke_user_view("create_contract", user)
                if status >= 300 or not payload or not payload.get("contract", {}).get("id"):
                    op.status = "failed"
                    db.session.commit()
                    return jsonify(payload or {"success": False, "error": "contract_create_failed"}), status
                op.contract_id = payload["contract"]["id"]
                db.session.commit()
                contract = db.session.get(Contract, op.contract_id)

            document = db.session.get(Document, op.document_id) if op.document_id else None
            if not document:
                payload, status = invoke_user_view("save_contract_as_document", user, contract.id)
                if status >= 300 or not payload or not payload.get("document", {}).get("id"):
                    op.status = "failed"
                    db.session.commit()
                    return jsonify(payload or {"success": False, "error": "contract_document_create_failed"}), status
                op.document_id = payload["document"]["id"]
                db.session.commit()
                document = db.session.get(Document, op.document_id)

            result = {
                "success": True,
                "workspaceId": workspace,
                "contract": contract_meta(contract),
                "document": document_meta(document),
                "contentReturned": False,
                "rawFilesReturned": False,
                "signatureRequested": False,
                "externalEffects": False,
            }
            op.status = "succeeded"
            op.response_json = result
            db.session.commit()
            try:
                log("integration.nexoffice.contract_created", user.id, "contract", contract.id, {"workspace_id": workspace, "document_id": document.id, "contract_type": contract.contract_type})
            except Exception:
                pass
            return jsonify(result), 201
        except Exception:
            db.session.rollback()
            op = NexOfficeContractOperation.query.filter_by(workspace_id=workspace, idempotency_key=key).first()
            if op:
                op.status = "failed"
                db.session.commit()
            raise

    print("DocWallet NexOffice contract bridge installed.")
