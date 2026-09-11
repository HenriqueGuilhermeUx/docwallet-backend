def install_icp_signature(app, db, auth_required, fail, log):
    """ICP-Brasil signature adapter layer for DocWallet Sign.

    This module does not claim to be an ICP-Brasil authority and does not handle
    private keys directly. It prepares DocWallet to delegate qualified signing to
    an approved external provider (PSC/AC/signing provider), while preserving the
    current DocWallet evidence signature flow.
    """
    import datetime as dt
    import os
    import uuid
    from typing import Any, Dict, Optional

    from flask import jsonify, request
    from sqlalchemy import text

    ENABLED = os.environ.get("ICP_SIGNATURE_ENABLED", "false").lower() == "true"
    PROVIDER_NAME = os.environ.get("ICP_SIGNATURE_PROVIDER", "pending_provider").strip() or "pending_provider"
    MODE = os.environ.get("ICP_SIGNATURE_MODE", "external_provider").strip() or "external_provider"
    PROVIDER_BASE_URL = os.environ.get("ICP_SIGNATURE_PROVIDER_BASE_URL", "").strip()
    PUBLIC_URL = os.environ.get("DOCWALLET_PUBLIC_URL", "https://docwallet.netlify.app").rstrip("/")

    class IcpSignatureSession(db.Model):
        __tablename__ = "icp_signature_session"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        request_id = db.Column(db.String(36), nullable=False, index=True)
        party_code = db.Column(db.String(90), nullable=False, index=True)
        user_id = db.Column(db.String(36), nullable=True, index=True)
        provider = db.Column(db.String(80), nullable=False, default=PROVIDER_NAME)
        mode = db.Column(db.String(80), nullable=False, default=MODE)
        status = db.Column(db.String(60), nullable=False, default="created", index=True)
        signature_standard = db.Column(db.String(40), nullable=False, default="PAdES")
        document_hash = db.Column(db.String(128), nullable=True, index=True)
        external_session_id = db.Column(db.String(180), nullable=True, index=True)
        redirect_url = db.Column(db.Text, nullable=True)
        signed_file_url = db.Column(db.Text, nullable=True)
        certificate_subject = db.Column(db.Text, nullable=True)
        certificate_issuer = db.Column(db.Text, nullable=True)
        certificate_serial = db.Column(db.String(180), nullable=True)
        validation_report = db.Column(db.JSON, nullable=True)
        metadata_json = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)
        completed_at = db.Column(db.DateTime, nullable=True)

    with app.app_context():
        db.create_all()
        for sql in [
            "CREATE INDEX IF NOT EXISTS idx_icp_session_request ON icp_signature_session(request_id)",
            "CREATE INDEX IF NOT EXISTS idx_icp_session_code_status ON icp_signature_session(party_code, status)",
        ]:
            try:
                db.session.execute(text(sql))
                db.session.commit()
            except Exception:
                db.session.rollback()

    def now():
        return dt.datetime.utcnow()

    def iso(value):
        return value.isoformat() + "Z" if value else None

    def row(sql: str, params: Dict[str, Any]):
        return db.session.execute(text(sql), params).mappings().first()

    def rows(sql: str, params: Dict[str, Any]):
        return db.session.execute(text(sql), params).mappings().all()

    def get_party_by_code(code: str):
        return row("SELECT id, request_id, code, name, email, status FROM signature_parties WHERE code = :code LIMIT 1", {"code": code})

    def get_signature_request(request_id: str):
        return row("SELECT id, user_id, title, contract_content, content_hash, final_hash, status FROM signature_requests WHERE id = :id LIMIT 1", {"id": request_id})

    def owned_signature_request(request_id: str):
        return row("SELECT id, user_id, title, content_hash, final_hash, status FROM signature_requests WHERE id = :id AND user_id = :user_id LIMIT 1", {"id": request_id, "user_id": request.user.id})

    def pack_session(s: IcpSignatureSession):
        return {
            "id": s.id,
            "requestId": s.request_id,
            "partyCode": s.party_code,
            "provider": s.provider,
            "mode": s.mode,
            "status": s.status,
            "signatureStandard": s.signature_standard,
            "documentHash": s.document_hash,
            "externalSessionId": s.external_session_id,
            "redirectUrl": s.redirect_url,
            "signedFileUrl": s.signed_file_url,
            "certificateSubject": s.certificate_subject,
            "certificateIssuer": s.certificate_issuer,
            "certificateSerial": s.certificate_serial,
            "validationReport": s.validation_report or {},
            "metadata": s.metadata_json or {},
            "createdAt": iso(s.created_at),
            "updatedAt": iso(s.updated_at),
            "completedAt": iso(s.completed_at),
        }

    def provider_capabilities():
        configured = bool(ENABLED and PROVIDER_NAME not in {"pending_provider", "mock"} and PROVIDER_BASE_URL)
        return {
            "success": True,
            "enabled": ENABLED,
            "configured": configured,
            "provider": PROVIDER_NAME,
            "mode": MODE,
            "publicUrl": PUBLIC_URL,
            "supports": {
                "icpBrasilQualified": configured,
                "padesPdf": True,
                "cadesHash": True,
                "a1Local": MODE in {"webpki", "external_provider"},
                "a3Desktop": MODE in {"webpki", "external_provider"},
                "cloudCertificate": MODE in {"cloud", "external_provider"},
                "mobileCloudSigning": MODE in {"cloud", "external_provider"},
            },
            "statusMessage": (
                "Integração ICP-Brasil configurada com provider externo."
                if configured
                else "Assinatura ICP-Brasil preparada no DocWallet, aguardando configuração de provider certificado."
            ),
            "safeLabel": "Assinatura qualificada somente quando concluída com certificado digital ICP-Brasil por provider configurado.",
        }

    class IcpSignatureProvider:
        name = "base"

        def create_session(self, req, party) -> Dict[str, Any]:
            raise NotImplementedError

    class PendingProvider(IcpSignatureProvider):
        name = "pending_provider"

        def create_session(self, req, party) -> Dict[str, Any]:
            return {
                "status": "provider_required",
                "redirect_url": None,
                "external_session_id": None,
                "message": "Configure ICP_SIGNATURE_PROVIDER e credenciais do provedor ICP-Brasil para habilitar assinatura qualificada.",
            }

    class ExternalProvider(IcpSignatureProvider):
        name = "external_provider"

        def create_session(self, req, party) -> Dict[str, Any]:
            if not ENABLED or not PROVIDER_BASE_URL or PROVIDER_NAME in {"pending_provider", "mock"}:
                return PendingProvider().create_session(req, party)
            callback = f"{PUBLIC_URL}/sign/{party['code']}"
            # The concrete provider adapter will replace this URL with the provider's signed session URL.
            # We keep the payload shape stable for Lacuna/BRy/Valid/Certisign/Soluti adapters.
            external_id = f"dw-{uuid.uuid4().hex}"
            return {
                "status": "awaiting_external_signature",
                "redirect_url": f"{PROVIDER_BASE_URL.rstrip('/')}/sign?session={external_id}&return_url={callback}",
                "external_session_id": external_id,
                "message": "Sessão de assinatura ICP-Brasil criada no provider externo.",
            }

    def provider() -> IcpSignatureProvider:
        if PROVIDER_NAME in {"pending_provider", "mock"}:
            return PendingProvider()
        return ExternalProvider()

    @app.get("/api/icp-signature/config")
    def icp_signature_config():
        return jsonify(provider_capabilities())

    @app.get("/api/signatures/<request_id>/icp/sessions")
    @auth_required
    def list_request_icp_sessions(request_id):
        req = owned_signature_request(request_id)
        if not req:
            return fail("Solicitação de assinatura não encontrada.", 404)
        sessions = IcpSignatureSession.query.filter_by(request_id=request_id).order_by(IcpSignatureSession.created_at.desc()).all()
        return jsonify({"success": True, "sessions": [pack_session(s) for s in sessions], "config": provider_capabilities()})

    @app.post("/api/signatures/<request_id>/icp/prepare")
    @auth_required
    def prepare_request_icp_sessions(request_id):
        req = owned_signature_request(request_id)
        if not req:
            return fail("Solicitação de assinatura não encontrada.", 404)
        parties = rows("SELECT id, code, name, email, status FROM signature_parties WHERE request_id = :request_id ORDER BY id", {"request_id": request_id})
        if not parties:
            return fail("Nenhuma parte encontrada para assinatura.", 404)
        created = []
        for p in parties:
            existing = IcpSignatureSession.query.filter_by(request_id=request_id, party_code=p["code"]).order_by(IcpSignatureSession.created_at.desc()).first()
            if existing and existing.status in {"awaiting_external_signature", "provider_required", "completed"}:
                created.append(existing)
                continue
            result = provider().create_session(req, p)
            session = IcpSignatureSession(
                request_id=request_id,
                party_code=p["code"],
                user_id=request.user.id,
                provider=PROVIDER_NAME,
                mode=MODE,
                status=result.get("status") or "provider_required",
                signature_standard="PAdES",
                document_hash=req["content_hash"],
                external_session_id=result.get("external_session_id"),
                redirect_url=result.get("redirect_url"),
                metadata_json={"party_name": p["name"], "party_email": p["email"], "message": result.get("message")},
            )
            db.session.add(session)
            created.append(session)
        db.session.commit()
        log("signature.icp.prepare", request.user.id, "signature", request_id, {"sessions": len(created), "provider": PROVIDER_NAME, "enabled": ENABLED})
        return jsonify({"success": True, "sessions": [pack_session(s) for s in created], "config": provider_capabilities()})

    @app.post("/api/sign/<code>/icp/start")
    def start_public_icp_signature(code):
        party = get_party_by_code(code)
        if not party:
            return fail("Link de assinatura não encontrado.", 404)
        req = get_signature_request(party["request_id"])
        if not req:
            return fail("Solicitação de assinatura não encontrada.", 404)
        if req["status"] == "cancelled":
            return fail("Solicitação de assinatura cancelada.", 410)
        existing = IcpSignatureSession.query.filter_by(request_id=req["id"], party_code=code).order_by(IcpSignatureSession.created_at.desc()).first()
        if existing and existing.status in {"awaiting_external_signature", "provider_required", "completed"}:
            session = existing
        else:
            result = provider().create_session(req, party)
            session = IcpSignatureSession(
                request_id=req["id"],
                party_code=code,
                user_id=req["user_id"],
                provider=PROVIDER_NAME,
                mode=MODE,
                status=result.get("status") or "provider_required",
                signature_standard="PAdES",
                document_hash=req["content_hash"],
                external_session_id=result.get("external_session_id"),
                redirect_url=result.get("redirect_url"),
                metadata_json={"party_name": party["name"], "party_email": party["email"], "message": result.get("message")},
            )
            db.session.add(session)
        db.session.execute(text("INSERT INTO signature_events (id, request_id, party_id, event_type, payload, created_at) VALUES (:id, :request_id, :party_id, :event_type, :payload, :created_at)"), {
            "id": str(uuid.uuid4()),
            "request_id": req["id"],
            "party_id": party["id"],
            "event_type": "icp.signature.started",
            "payload": {"provider": PROVIDER_NAME, "enabled": ENABLED, "status": session.status},
            "created_at": now(),
        })
        db.session.commit()
        return jsonify({"success": True, "session": pack_session(session), "config": provider_capabilities()})

    @app.post("/api/icp-signature/provider/callback")
    def icp_provider_callback():
        """Generic callback placeholder for future provider integration.

        Real adapters must verify provider signatures/secrets before trusting payloads.
        This endpoint intentionally refuses unauthenticated completion unless an external
        provider callback secret is configured and matched.
        """
        secret = os.environ.get("ICP_SIGNATURE_CALLBACK_SECRET", "").strip()
        provided = request.headers.get("X-DocWallet-ICP-Secret", "").strip()
        if not secret or provided != secret:
            return fail("Callback ICP não autorizado.", 401)
        body = request.get_json(silent=True) or {}
        external_id = body.get("externalSessionId") or body.get("external_session_id")
        session = IcpSignatureSession.query.filter_by(external_session_id=external_id).first() if external_id else None
        if not session:
            return fail("Sessão ICP não encontrada.", 404)
        session.status = body.get("status") or "completed"
        session.signed_file_url = body.get("signedFileUrl") or body.get("signed_file_url")
        session.certificate_subject = body.get("certificateSubject") or body.get("certificate_subject")
        session.certificate_issuer = body.get("certificateIssuer") or body.get("certificate_issuer")
        session.certificate_serial = body.get("certificateSerial") or body.get("certificate_serial")
        session.validation_report = body.get("validationReport") or body.get("validation_report") or {}
        if session.status == "completed":
            session.completed_at = now()
        db.session.commit()
        return jsonify({"success": True, "session": pack_session(session)})

    print("DocWallet ICP signature layer installed.")
