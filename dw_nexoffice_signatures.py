def install_nexoffice_signatures(app, db, User, Contract, fail, log):
    """Read/control extension for NexOffice-created DocWallet signatures.

    Signature creation already lives in dw_nexoffice.py and reuses the canonical
    document signature route with idempotency. This module adds only safe status,
    reminder and cancellation operations, returning no raw content or sensitive
    evidence fields to NexOffice.
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

    def signature_owned_by_user(signature_id, user):
        row = db.session.execute(
            text("select id from signature_requests where id=:id and user_id=:user_id"),
            {"id": signature_id, "user_id": user.id},
        ).first()
        return bool(row)

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
        return jsonify({"success": True, "workspaceId": workspace, "request": safe_request(payload.get("request")), "rawContentReturned": False, "sensitiveEvidenceReturned": False})

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
        return jsonify({"success": True, "party": safe_party(payload.get("party") or {}), "url": str(payload.get("url") or ""), "message": str(payload.get("message") or ""), "sensitiveEvidenceReturned": False})

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
        return jsonify({"success": True, "request": safe_request(payload.get("request")), "sensitiveEvidenceReturned": False})

    print("DocWallet NexOffice signature controls installed.")
