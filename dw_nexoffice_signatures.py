def install_nexoffice_signatures(app, db, User, Contract, fail, log):
    """Narrow NexOffice -> DocWallet signature bridge.

    Reuses the canonical dw_sign views inside nested Flask request contexts. Raw
    contract content never leaves DocWallet. NexOffice receives only request,
    party, status and hash references required for its operational UX.
    """
    import hmac
    import os
    import uuid
    from functools import wraps

    from flask import jsonify, request
    from sqlalchemy import text

    ENABLED = os.environ.get("NEXOFFICE_SIGNATURE_BRIDGE_ENABLED", "false").lower() == "true"
    SERVICE_KEY = os.environ.get("NEXOFFICE_SERVICE_KEY", "").strip()

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
                return fail("Assinaturas via NexOffice desabilitadas.", 503, {"code": "nexoffice_signature_bridge_disabled"})
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

    def linked_user(workspace):
        rows = db.session.execute(
            text("select user_id from nexoffice_connections where workspace_id=:workspace and status='active' order by connected_at asc limit 2"),
            {"workspace": workspace},
        ).fetchall()
        if not rows:
            return None, fail("Conecte uma conta DocWallet a este workspace antes de usar assinaturas.", 403, {"code": "docwallet_workspace_not_connected"})
        if len(rows) > 1:
            return None, fail("Há mais de um usuário DocWallet ligado ao workspace. Defina um proprietário antes de usar assinaturas.", 409, {"code": "docwallet_owner_ambiguous"})
        user = db.session.get(User, rows[0][0])
        if not user:
            return None, fail("Proprietário DocWallet não encontrado.", 404)
        return user, None

    def canonical(endpoint, user, path, method="GET", payload=None, *args):
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

    def safe_party(party):
        return {
            "id": str(party.get("id") or ""),
            "name": str(party.get("name") or ""),
            "email": str(party.get("email") or ""),
            "status": str(party.get("status") or "pending"),
            "signedAt": party.get("signed_at"),
            "code": str(party.get("code") or ""),
            "url": str(party.get("url") or ""),
        }

    def safe_request(value):
        value = value or {}
        return {
            "id": str(value.get("id") or ""),
            "title": str(value.get("title") or ""),
            "status": str(value.get("status") or "pending"),
            "contentHash": str(value.get("content_hash") or ""),
            "finalHash": str(value.get("final_hash") or ""),
            "createdAt": value.get("created_at"),
            "completedAt": value.get("completed_at"),
            "totalParties": int(value.get("total_parties") or 0),
            "signedCount": int(value.get("signed_count") or 0),
            "pendingCount": int(value.get("pending_count") or 0),
            "progressPercent": int(value.get("progress_percent") or 0),
            "parties": [safe_party(item) for item in (value.get("parties") or []) if isinstance(item, dict)],
        }

    def owned_contract(contract_id, user):
        contract = db.session.get(Contract, contract_id)
        if not contract or str(contract.user_id) != str(user.id):
            return None
        return contract

    def signature_owned_by_user(signature_id, user):
        row = db.session.execute(
            text("select id from signature_requests where id=:id and user_id=:user_id"),
            {"id": signature_id, "user_id": user.id},
        ).first()
        return bool(row)

    @app.post("/api/internal/nexoffice/signatures/request")
    @require_service
    def nexoffice_signature_request():
        workspace, denied = workspace_id()
        if denied:
            return denied
        user, denied = linked_user(workspace)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        contract_id = str(body.get("contractId") or "").strip()
        parties = body.get("parties") or []
        if not contract_id:
            return fail("contractId é obrigatório.", 400, {"code": "contract_id_required"})
        if not isinstance(parties, list) or not parties:
            return fail("Informe pelo menos uma parte para assinatura.", 400, {"code": "signature_parties_required"})
        contract = owned_contract(contract_id, user)
        if not contract:
            return fail("Contrato DocWallet não encontrado para este workspace.", 404, {"code": "contract_not_found"})
        payload, status = canonical(
            "create_signature_request",
            user,
            "/api/signatures/request",
            "POST",
            {
                "title": str(body.get("title") or contract.title or "Contrato DocWallet")[:240],
                "contract_content": contract.content,
                "parties": [
                    {"name": str(item.get("name") or "")[:180], "email": str(item.get("email") or "")[:180]}
                    for item in parties if isinstance(item, dict)
                ],
            },
        )
        if status >= 300 or not payload or not payload.get("request"):
            return jsonify(payload or {"success": False, "error": "signature_request_failed"}), status
        result = {
            "success": True,
            "workspaceId": workspace,
            "contractId": contract.id,
            "request": safe_request(payload.get("request")),
            "privacy": {
                "rawContentReturned": False,
                "signatureImageReturned": False,
                "ipReturned": False,
                "cpfReturned": False,
                "phoneReturned": False,
                "geolocationReturned": False,
                "deviceFingerprintReturned": False,
            },
        }
        try:
            log("integration.nexoffice.signature_requested", user.id, "signature", result["request"]["id"], {"workspace_id": workspace, "contract_id": contract.id, "parties": result["request"]["totalParties"]})
        except Exception:
            pass
        return jsonify(result), 201

    @app.get("/api/internal/nexoffice/signatures/<signature_id>")
    @require_service
    def nexoffice_signature_status(signature_id):
        workspace, denied = workspace_id()
        if denied:
            return denied
        user, denied = linked_user(workspace)
        if denied:
            return denied
        if not signature_owned_by_user(signature_id, user):
            return fail("Solicitação de assinatura não encontrada para este workspace.", 404)
        payload, status = canonical("read_signature_request", user, "/api/signatures/" + signature_id, "GET", None, signature_id)
        if status >= 300 or not payload:
            return jsonify(payload or {"success": False}), status
        return jsonify({"success": True, "workspaceId": workspace, "request": safe_request(payload.get("request")), "rawContentReturned": False})

    @app.post("/api/internal/nexoffice/signatures/<signature_id>/reminder")
    @require_service
    def nexoffice_signature_reminder(signature_id):
        workspace, denied = workspace_id()
        if denied:
            return denied
        user, denied = linked_user(workspace)
        if denied:
            return denied
        if not signature_owned_by_user(signature_id, user):
            return fail("Solicitação de assinatura não encontrada para este workspace.", 404)
        body = request.get_json(silent=True) or {}
        party_id = str(body.get("partyId") or "").strip()
        payload, status = canonical("create_signature_reminder", user, "/api/signatures/" + signature_id + "/reminder", "POST", {"party_id": party_id} if party_id else {}, signature_id)
        if status >= 300 or not payload:
            return jsonify(payload or {"success": False}), status
        return jsonify({"success": True, "party": safe_party(payload.get("party") or {}), "url": str(payload.get("url") or ""), "message": str(payload.get("message") or "")})

    @app.post("/api/internal/nexoffice/signatures/<signature_id>/cancel")
    @require_service
    def nexoffice_signature_cancel(signature_id):
        workspace, denied = workspace_id()
        if denied:
            return denied
        user, denied = linked_user(workspace)
        if denied:
            return denied
        if not signature_owned_by_user(signature_id, user):
            return fail("Solicitação de assinatura não encontrada para este workspace.", 404)
        payload, status = canonical("cancel_signature_request", user, "/api/signatures/" + signature_id + "/cancel", "POST", {}, signature_id)
        if status >= 300 or not payload:
            return jsonify(payload or {"success": False}), status
        return jsonify({"success": True, "request": safe_request(payload.get("request"))})

    print("DocWallet NexOffice signature bridge installed.")
