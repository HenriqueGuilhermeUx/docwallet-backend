def install_nexoffice_signature_modes(app, db, User, fail, log):
    """Expose DocWallet electronic + ICP-Brasil signing safely to NexOffice.

    The canonical electronic signer remains dw_sign. ICP-Brasil remains owned by
    dw_icp_signature/Lacuna Rest PKI Core. This bridge only orchestrates those
    existing capabilities and never returns raw document content, private keys,
    certificate passwords, drawn signatures, IP/device/geolocation evidence or
    Lacuna redirect URLs to NexOffice.
    """
    import hmac
    import os
    import uuid
    from functools import wraps

    from flask import jsonify, request
    from sqlalchemy import text

    ENABLED = os.environ.get("NEXOFFICE_SIGNATURE_BRIDGE_ENABLED", "false").lower() == "true"
    ICP_ENABLED = os.environ.get("NEXOFFICE_ICP_SIGNATURE_ENABLED", "false").lower() == "true"
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

    def signature_owned_by_user(signature_id, user):
        row = db.session.execute(
            text("select id from signature_requests where id=:id and user_id=:user_id"),
            {"id": signature_id, "user_id": user.id},
        ).first()
        return bool(row)

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

    def canonical_public(endpoint, path):
        view = app.view_functions.get(endpoint)
        if not view:
            return None, 503
        with app.test_request_context(path, method="GET"):
            response = view()
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
            "signedAt": party.get("signed_at") or party.get("signedAt"),
            "code": str(party.get("code") or ""),
            "url": str(party.get("url") or ""),
        }

    def safe_request(value):
        value = value or {}
        parties = value.get("parties") or []
        total = int(value.get("total_parties") or value.get("totalParties") or len(parties))
        signed = int(value.get("signed_count") or value.get("signedCount") or sum(1 for p in parties if str(p.get("status") or "").lower() == "signed"))
        pending = int(value.get("pending_count") or value.get("pendingCount") or max(total - signed, 0))
        progress = int(value.get("progress_percent") or value.get("progressPercent") or (round(signed / total * 100) if total else 0))
        return {
            "id": str(value.get("id") or ""),
            "title": str(value.get("title") or ""),
            "status": str(value.get("status") or "pending"),
            "contentHash": str(value.get("content_hash") or value.get("contentHash") or ""),
            "finalHash": str(value.get("final_hash") or value.get("finalHash") or ""),
            "createdAt": value.get("created_at") or value.get("createdAt"),
            "completedAt": value.get("completed_at") or value.get("completedAt"),
            "totalParties": total,
            "signedCount": signed,
            "pendingCount": pending,
            "progressPercent": progress,
            "parties": [safe_party(item) for item in parties if isinstance(item, dict)],
        }

    def safe_icp_session(value):
        value = value or {}
        validation = value.get("validationReport") or value.get("validation_report") or {}
        provider_status = validation.get("providerStatus") if isinstance(validation, dict) else None
        return {
            "id": str(value.get("id") or ""),
            "requestId": str(value.get("requestId") or value.get("request_id") or ""),
            "provider": str(value.get("provider") or "lacuna"),
            "mode": str(value.get("mode") or "external_provider"),
            "status": str(value.get("status") or "created"),
            "signatureStandard": str(value.get("signatureStandard") or value.get("signature_standard") or "PAdES"),
            "providerStatus": provider_status,
            "completedAt": value.get("completedAt") or value.get("completed_at"),
            "hasCertificateEvidence": bool(value.get("certificateSubject") or value.get("certificate_subject")),
            "hasSignedDocument": bool(value.get("signedFileUrl") or value.get("signed_file_url")),
        }

    def safe_icp_config(value):
        value = value or {}
        return {
            "enabled": bool(value.get("enabled")),
            "configured": bool(value.get("configured")),
            "provider": str(value.get("provider") or "lacuna"),
            "mode": str(value.get("mode") or "external_provider"),
            "signatureStandard": str(value.get("signatureStandard") or "PAdES"),
            "nexofficeEnabled": ICP_ENABLED,
        }

    @app.get("/api/internal/nexoffice/signature-modes")
    @require_service
    def nexoffice_signature_modes():
        workspace, denied = workspace_id()
        if denied:
            return denied
        user, denied = linked_user(workspace)
        if denied:
            return denied
        icp_payload, icp_status = canonical_public("icp_signature_config", "/api/icp-signature/config")
        icp = safe_icp_config(icp_payload if icp_status < 300 else {})
        return jsonify({
            "success": True,
            "workspaceId": workspace,
            "modes": [
                {
                    "id": "electronic",
                    "label": "Assinatura eletrônica DocWallet",
                    "available": True,
                    "provider": "docwallet",
                    "externalProvider": False,
                    "signatureStandard": "electronic_evidence",
                    "description": "Aceite eletrônico com trilha de evidências DocWallet.",
                },
                {
                    "id": "icp_brasil",
                    "label": "Assinatura digital ICP-Brasil",
                    "available": bool(icp.get("enabled") and icp.get("configured") and ICP_ENABLED),
                    "provider": icp.get("provider") or "lacuna",
                    "externalProvider": True,
                    "signatureStandard": icp.get("signatureStandard") or "PAdES",
                    "description": "Assinatura digital com certificado ICP-Brasil via Lacuna Rest PKI Core.",
                },
            ],
            "icp": icp,
            "privacy": {
                "privateKeysHandledByDocWallet": False,
                "certificatePasswordsHandledByDocWallet": False,
                "lacunaRedirectUrlReturnedToNexOffice": False,
                "sensitiveEvidenceReturnedToNexOffice": False,
                "rawDocumentReturnedToNexOffice": False,
            },
            "externalEffect": False,
        })

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
        return jsonify({
            "success": True,
            "workspaceId": workspace,
            "request": safe_request(payload.get("request")),
            "privacy": {"rawContentReturned": False, "sensitiveEvidenceReturned": False},
            "externalEffect": False,
        })

    @app.get("/api/internal/nexoffice/signatures/<signature_id>/icp/sessions")
    @require_service
    def nexoffice_icp_sessions(signature_id):
        workspace, denied = workspace_id()
        if denied:
            return denied
        user, denied = linked_user(workspace)
        if denied:
            return denied
        if not signature_owned_by_user(signature_id, user):
            return fail("Solicitação de assinatura não encontrada para este workspace.", 404)
        payload, status = canonical("list_request_icp_sessions", user, f"/api/signatures/{signature_id}/icp/sessions", "GET", None, signature_id)
        if status >= 300 or not payload:
            return jsonify(payload or {"success": False}), status
        return jsonify({
            "success": True,
            "workspaceId": workspace,
            "sessions": [safe_icp_session(item) for item in (payload.get("sessions") or []) if isinstance(item, dict)],
            "config": safe_icp_config(payload.get("config") or {}),
            "privacy": {"redirectUrlReturned": False, "certificateIdentityReturned": False, "signedFileUrlReturned": False},
            "externalEffect": False,
        })

    @app.post("/api/internal/nexoffice/signatures/<signature_id>/icp/prepare")
    @require_service
    def nexoffice_icp_prepare(signature_id):
        workspace, denied = workspace_id()
        if denied:
            return denied
        user, denied = linked_user(workspace)
        if denied:
            return denied
        if not signature_owned_by_user(signature_id, user):
            return fail("Solicitação de assinatura não encontrada para este workspace.", 404)
        body = request.get_json(silent=True) or {}
        if body.get("humanConfirmed") is not True:
            return fail("Confirmação humana é obrigatória para preparar assinatura ICP-Brasil.", 400, {"code": "human_confirmation_required"})
        if not ICP_ENABLED:
            return fail("Assinatura ICP-Brasil via NexOffice está desabilitada neste ambiente.", 409, {"code": "nexoffice_icp_disabled"})
        payload, status = canonical("prepare_request_icp_sessions", user, f"/api/signatures/{signature_id}/icp/prepare", "POST", {}, signature_id)
        if status >= 300 or not payload:
            return jsonify(payload or {"success": False}), status
        safe_sessions = [safe_icp_session(item) for item in (payload.get("sessions") or []) if isinstance(item, dict)]
        try:
            log("integration.nexoffice.icp_prepared", user.id, "signature", signature_id, {"workspace_id": workspace, "sessions": len(safe_sessions), "provider": "lacuna", "human_confirmed": True})
        except Exception:
            pass
        return jsonify({
            "success": True,
            "workspaceId": workspace,
            "sessions": safe_sessions,
            "config": safe_icp_config(payload.get("config") or {}),
            "privacy": {"redirectUrlReturned": False, "certificateIdentityReturned": False, "signedFileUrlReturned": False},
            "externalEffect": True,
            "humanConfirmed": True,
        }), status

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
        payload, status = canonical("create_signature_reminder", user, f"/api/signatures/{signature_id}/reminder", "POST", {"party_id": party_id} if party_id else {}, signature_id)
        if status >= 300 or not payload:
            return jsonify(payload or {"success": False}), status
        return jsonify({"success": True, "party": safe_party(payload.get("party") or {}), "message": str(payload.get("message") or ""), "sensitiveEvidenceReturned": False, "externalEffect": True})

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
        payload, status = canonical("cancel_signature_request", user, f"/api/signatures/{signature_id}/cancel", "POST", {}, signature_id)
        if status >= 300 or not payload:
            return jsonify(payload or {"success": False}), status
        return jsonify({"success": True, "request": safe_request(payload.get("request")), "sensitiveEvidenceReturned": False, "externalEffect": True})

    print("DocWallet NexOffice dual signature modes installed.")
