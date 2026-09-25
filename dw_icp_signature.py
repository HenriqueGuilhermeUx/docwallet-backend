"""Lacuna Rest PKI Core adapter for DocWallet ICP-Brasil signing.

No private keys or certificate passwords are handled by DocWallet. The user's
certificate interaction happens in the provider-hosted signing session.
"""

from __future__ import annotations

import base64
import json
import os
import re
import textwrap
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Optional


class LacunaProviderError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


def _escape_pdf_text(value: str) -> bytes:
    raw = value.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _wrapped_lines(title: str, content: str, width: int = 92) -> List[str]:
    lines: List[str] = []
    clean_title = " ".join((title or "Documento DocWallet").split())
    lines.extend(textwrap.wrap(clean_title, width=width) or [clean_title])
    lines.append("")
    for raw_line in (content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        compact = raw_line.rstrip()
        if not compact:
            lines.append("")
            continue
        wrapped = textwrap.wrap(
            compact,
            width=width,
            replace_whitespace=False,
            drop_whitespace=True,
            break_long_words=True,
            break_on_hyphens=False,
        )
        lines.extend(wrapped or [""])
    return lines or ["Documento DocWallet"]


def build_text_pdf(title: str, content: str) -> bytes:
    """Build a simple, standards-compliant PDF from the text contract.

    We intentionally keep this dependency-free so the signing adapter does not
    add a rendering library to the production backend.
    """
    page_lines = 52
    lines = _wrapped_lines(title, content)
    pages = [lines[i:i + page_lines] for i in range(0, len(lines), page_lines)] or [["Documento DocWallet"]]

    objects: Dict[int, bytes] = {}
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    font_obj = 3
    objects[font_obj] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"

    page_object_numbers: List[int] = []
    next_obj = 4
    for page in pages:
        page_obj = next_obj
        content_obj = next_obj + 1
        next_obj += 2
        page_object_numbers.append(page_obj)

        stream_parts = [b"BT", b"/F1 10 Tf", b"48 800 Td", b"13 TL"]
        for line in page:
            stream_parts.append(b"(" + _escape_pdf_text(line) + b") Tj")
            stream_parts.append(b"T*")
        stream_parts.append(b"ET")
        stream = b"\n".join(stream_parts)

        objects[content_obj] = (
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" +
            stream + b"\nendstream"
        )
        objects[page_obj] = (
            b"<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 3 0 R >> >> "
            b"/Contents " + str(content_obj).encode("ascii") + b" 0 R >>"
        )

    kids = b" ".join(f"{number} 0 R".encode("ascii") for number in page_object_numbers)
    objects[2] = b"<< /Type /Pages /Kids [ " + kids + b" ] /Count " + str(len(page_object_numbers)).encode("ascii") + b" >>"

    max_obj = max(objects)
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * (max_obj + 1)

    for obj_num in range(1, max_obj + 1):
        offsets[obj_num] = len(output)
        output.extend(f"{obj_num} 0 obj\n".encode("ascii"))
        output.extend(objects[obj_num])
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {max_obj + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for obj_num in range(1, max_obj + 1):
        output.extend(f"{offsets[obj_num]:010d} 00000 n \n".encode("ascii"))

    output.extend(
        b"trailer\n<< /Size " + str(max_obj + 1).encode("ascii") + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_offset).encode("ascii") + b"\n%%EOF\n"
    )
    return bytes(output)


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip())
    value = value.strip("-._")
    return (value or "documento-docwallet")[:120]


class LacunaRestPkiProvider:
    name = "lacuna"

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        public_url: str,
        security_context_id: str = "",
        timeout_seconds: int = 30,
    ):
        self.endpoint = (endpoint or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.public_url = (public_url or "").strip().rstrip("/")
        self.security_context_id = (security_context_id or "").strip()
        self.timeout_seconds = max(int(timeout_seconds or 30), 5)

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.api_key)

    def _request_json(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.configured:
            raise LacunaProviderError("Endpoint e chave da Lacuna ainda não estão configurados.")

        url = f"{self.endpoint}/{path.lstrip('/')}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/json",
            "Accept-Language": "pt-BR",
            "X-Api-Key": self.api_key,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload_error = json.loads(raw) if raw else {}
            except Exception:
                payload_error = {"raw": raw[:1000]}
            code = payload_error.get("code") or payload_error.get("errorCode")
            message = payload_error.get("message") or payload_error.get("error") or f"Rest PKI retornou HTTP {exc.code}."
            if code:
                message = f"{message} ({code})"
            raise LacunaProviderError(message, status=exc.code, payload=payload_error) from exc
        except urllib.error.URLError as exc:
            raise LacunaProviderError(f"Não foi possível acessar o Rest PKI: {getattr(exc, 'reason', exc)}") from exc
        except TimeoutError as exc:
            raise LacunaProviderError("Tempo limite ao acessar o Rest PKI.") from exc

    def create_session(self, req: Dict[str, Any], party: Dict[str, Any]) -> Dict[str, Any]:
        pdf_bytes = build_text_pdf(req.get("title") or "Documento DocWallet", req.get("contract_content") or "")
        filename = f"{_safe_filename(req.get('title') or 'documento-docwallet')}.pdf"
        return_url = f"{self.public_url}/sign/{party['code']}"

        document = {
            "id": str(req["id"]),
            "file": {
                "mimeType": "application/pdf",
                "content": base64.b64encode(pdf_bytes).decode("ascii"),
                "name": filename,
                "length": len(pdf_bytes),
            },
            "signatureType": "Pdf",
            "metadata": {
                "docwalletRequestId": [str(req["id"])],
                "docwalletPartyCode": [str(party["code"])],
            },
        }

        payload: Dict[str, Any] = {
            "returnUrl": return_url,
            "callbackArgument": str(party["code"]),
            "enableBackgroundProcessing": False,
            "documents": [document],
            "documentMetadata": {
                "docwalletRequestId": [str(req["id"])],
                "docwalletPartyCode": [str(party["code"])],
            },
        }
        if self.security_context_id:
            payload["securityContextId"] = self.security_context_id

        result = self._request_json("POST", "/api/signature-sessions", payload)
        session_id = result.get("sessionId") or result.get("id")
        redirect_url = result.get("redirectUrl")
        if not session_id or not redirect_url:
            raise LacunaProviderError("Rest PKI não retornou sessionId/redirectUrl para a sessão de assinatura.", payload=result)

        return {
            "status": "awaiting_external_signature",
            "redirect_url": redirect_url,
            "external_session_id": str(session_id),
            "message": "Sessão ICP-Brasil criada na Lacuna Rest PKI.",
            "provider_payload": {
                "sourcePdfLength": len(pdf_bytes),
                "sourcePdfSha256": __import__("hashlib").sha256(pdf_bytes).hexdigest(),
            },
        }

    def get_session(self, session_id: str) -> Dict[str, Any]:
        return self._request_json("GET", f"/api/signature-sessions/{session_id}")

    @staticmethod
    def signed_file_url(details: Dict[str, Any]) -> Optional[str]:
        for document in details.get("documents") or []:
            signed_file = document.get("signedFile") or {}
            url = signed_file.get("url") or signed_file.get("location")
            if url:
                return str(url)
        return None

    @staticmethod
    def certificate_summary(details: Dict[str, Any]) -> Dict[str, Any]:
        cert = details.get("signerCertificate") or {}
        subject = cert.get("subjectDisplayName") or cert.get("subjectCommonName") or cert.get("subjectIdentifier")
        issuer = cert.get("issuerDisplayName")
        serial = cert.get("serialNumber")
        return {
            "subject": subject,
            "issuer": issuer,
            "serial": serial,
            "email": cert.get("emailAddress"),
            "identifier": cert.get("subjectIdentifier"),
            "organization": cert.get("organization"),
            "thumbprintSha256": cert.get("thumbprintSHA256") or cert.get("binaryThumbprintSHA256"),
            "validityStart": cert.get("validityStart"),
            "validityEnd": cert.get("validityEnd"),
            "pkiBrazil": cert.get("pkiBrazil") or {},
        }

    @staticmethod
    def validation_summary(details: Dict[str, Any]) -> Dict[str, Any]:
        docs = []
        for document in details.get("documents") or []:
            docs.append({
                "id": document.get("id"),
                "status": document.get("status"),
                "signatureType": document.get("signatureType"),
                "dateSigned": document.get("dateSigned"),
                "key": document.get("key"),
                "formattedKey": document.get("formattedKey"),
                "availableUntil": document.get("availableUntil"),
            })
        return {
            "providerStatus": details.get("status"),
            "processingErrorCode": details.get("processingErrorCode"),
            "documents": docs,
            "certificate": LacunaRestPkiProvider.certificate_summary(details),
        }


def install_icp_signature(app, db, auth_required, fail, log):
    """ICP-Brasil signature orchestration for DocWallet Sign.

    Qualified signing is delegated to an external provider. DocWallet stores the
    provider session metadata and evidence, but never receives the signer's
    private key or certificate password.
    """
    import datetime as dt
    import hashlib
    import json
    import os
    import uuid
    from typing import Any, Dict, Optional

    from flask import jsonify, request
    from sqlalchemy import text

    ENABLED = os.environ.get("ICP_SIGNATURE_ENABLED", "false").lower() == "true"
    PROVIDER_NAME = os.environ.get("ICP_SIGNATURE_PROVIDER", "pending_provider").strip().lower() or "pending_provider"
    MODE = os.environ.get("ICP_SIGNATURE_MODE", "external_provider").strip() or "external_provider"
    PROVIDER_BASE_URL = os.environ.get("ICP_SIGNATURE_PROVIDER_BASE_URL", "").strip()
    PROVIDER_API_KEY = os.environ.get("ICP_SIGNATURE_API_KEY", "").strip()
    SECURITY_CONTEXT_ID = os.environ.get("ICP_SIGNATURE_SECURITY_CONTEXT_ID", "").strip()
    PROVIDER_TIMEOUT_SECONDS = int(os.environ.get("ICP_SIGNATURE_TIMEOUT_SECONDS", "30") or "30")
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
            "CREATE INDEX IF NOT EXISTS idx_icp_session_external_id ON icp_signature_session(external_session_id)",
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
        return row(
            "SELECT id, request_id, code, name, email, status, signed_name, signed_email, signed_at "
            "FROM signature_parties WHERE code = :code LIMIT 1",
            {"code": code},
        )

    def get_signature_request(request_id: str):
        return row(
            "SELECT id, user_id, title, contract_content, content_hash, final_hash, status "
            "FROM signature_requests WHERE id = :id LIMIT 1",
            {"id": request_id},
        )

    def owned_signature_request(request_id: str):
        return row(
            "SELECT id, user_id, title, contract_content, content_hash, final_hash, status "
            "FROM signature_requests WHERE id = :id AND user_id = :user_id LIMIT 1",
            {"id": request_id, "user_id": request.user.id},
        )

    def is_lacuna():
        return PROVIDER_NAME in {"lacuna", "restpki", "rest_pki", "restpki_core", "lacuna_restpki"}

    def lacuna_provider():
        return LacunaRestPkiProvider(
            endpoint=PROVIDER_BASE_URL,
            api_key=PROVIDER_API_KEY,
            public_url=PUBLIC_URL,
            security_context_id=SECURITY_CONTEXT_ID,
            timeout_seconds=PROVIDER_TIMEOUT_SECONDS,
        )

    def provider_is_configured():
        if not ENABLED or PROVIDER_NAME in {"pending_provider", "mock"}:
            return False
        if is_lacuna():
            return lacuna_provider().configured
        return bool(PROVIDER_BASE_URL)

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
        configured = provider_is_configured()
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
                "Integração ICP-Brasil configurada com Lacuna Rest PKI."
                if configured and is_lacuna()
                else "Integração ICP-Brasil configurada com provider externo."
                if configured
                else "Assinatura ICP-Brasil preparada no DocWallet, aguardando endpoint/credenciais do provider."
            ),
            "safeLabel": "Assinatura qualificada somente quando concluída com certificado digital ICP-Brasil por provider configurado.",
        }

    class IcpSignatureProvider:
        name = "base"

        def create_session(self, req, party) -> Dict[str, Any]:
            raise NotImplementedError

        def get_session(self, external_session_id: str) -> Dict[str, Any]:
            raise NotImplementedError

    class PendingProvider(IcpSignatureProvider):
        name = "pending_provider"

        def create_session(self, req, party) -> Dict[str, Any]:
            return {
                "status": "provider_required",
                "redirect_url": None,
                "external_session_id": None,
                "message": "Configure endpoint e credenciais do provedor ICP-Brasil para habilitar assinatura qualificada.",
            }

        def get_session(self, external_session_id: str) -> Dict[str, Any]:
            return {"status": "ProviderRequired"}

    class LacunaProvider(IcpSignatureProvider):
        name = "lacuna"

        def __init__(self):
            self.client = lacuna_provider()

        def create_session(self, req, party) -> Dict[str, Any]:
            if not ENABLED or not self.client.configured:
                return PendingProvider().create_session(req, party)
            return self.client.create_session(req, party)

        def get_session(self, external_session_id: str) -> Dict[str, Any]:
            return self.client.get_session(external_session_id)

    class ExternalProvider(IcpSignatureProvider):
        name = "external_provider"

        def create_session(self, req, party) -> Dict[str, Any]:
            if not ENABLED or not PROVIDER_BASE_URL or PROVIDER_NAME in {"pending_provider", "mock"}:
                return PendingProvider().create_session(req, party)
            callback = f"{PUBLIC_URL}/sign/{party['code']}"
            external_id = f"dw-{uuid.uuid4().hex}"
            return {
                "status": "awaiting_external_signature",
                "redirect_url": f"{PROVIDER_BASE_URL.rstrip('/')}/sign?session={external_id}&return_url={callback}",
                "external_session_id": external_id,
                "message": "Sessão de assinatura ICP-Brasil criada no provider externo.",
            }

        def get_session(self, external_session_id: str) -> Dict[str, Any]:
            raise RuntimeError("Sincronização automática não implementada para este provider genérico.")

    def provider() -> IcpSignatureProvider:
        if PROVIDER_NAME in {"pending_provider", "mock"}:
            return PendingProvider()
        if is_lacuna():
            return LacunaProvider()
        return ExternalProvider()

    def build_session_metadata(party, result):
        payload = {
            "party_name": party["name"],
            "party_email": party["email"],
            "message": result.get("message"),
        }
        if result.get("provider_payload"):
            payload["provider"] = result["provider_payload"]
        return payload

    def create_or_reuse_session(req, party, user_id=None):
        existing = (
            IcpSignatureSession.query
            .filter_by(request_id=req["id"], party_code=party["code"])
            .order_by(IcpSignatureSession.created_at.desc())
            .first()
        )
        reusable = {"awaiting_external_signature", "processing", "completed"}
        if not provider_is_configured():
            reusable.add("provider_required")
        if existing and existing.status in reusable:
            return existing

        result = provider().create_session(req, party)
        session = IcpSignatureSession(
            request_id=req["id"],
            party_code=party["code"],
            user_id=user_id or req["user_id"],
            provider=PROVIDER_NAME,
            mode=MODE,
            status=result.get("status") or "provider_required",
            signature_standard="PAdES",
            document_hash=req["content_hash"],
            external_session_id=result.get("external_session_id"),
            redirect_url=result.get("redirect_url"),
            metadata_json=build_session_metadata(party, result),
        )
        db.session.add(session)
        return session

    def normalized_status(provider_status: str):
        clean = (provider_status or "").strip().lower().replace("_", "").replace("-", "")
        mapping = {
            "completed": "completed",
            "usercancelled": "user_cancelled",
            "cancelled": "user_cancelled",
            "processing": "processing",
            "processingerror": "processing_error",
            "pending": "awaiting_external_signature",
        }
        return mapping.get(clean, clean or "unknown")

    def extract_lacuna_evidence(details):
        client = lacuna_provider()
        cert = client.certificate_summary(details)
        validation = client.validation_summary(details)
        signed_url = client.signed_file_url(details)
        return cert, validation, signed_url

    def build_request_final_hash(request_id: str):
        req = get_signature_request(request_id)
        parties = rows(
            "SELECT id, code, name, email, status, signed_name, signed_email, signed_at, evidence_level "
            "FROM signature_parties WHERE request_id = :request_id ORDER BY id",
            {"request_id": request_id},
        )
        icp = rows(
            "SELECT id, party_code, provider, status, external_session_id, certificate_subject, certificate_issuer, "
            "certificate_serial, validation_report, completed_at "
            "FROM icp_signature_session WHERE request_id = :request_id ORDER BY created_at",
            {"request_id": request_id},
        )
        payload = {
            "parties": [
                {
                    "id": p["id"],
                    "code": p["code"],
                    "name": p["signed_name"] or p["name"],
                    "email": p["signed_email"] or p["email"],
                    "status": p["status"],
                    "signed_at": iso(p["signed_at"]),
                    "evidence_level": p["evidence_level"],
                }
                for p in parties
            ],
            "icp_sessions": [
                {
                    "id": s["id"],
                    "party_code": s["party_code"],
                    "provider": s["provider"],
                    "status": s["status"],
                    "external_session_id": s["external_session_id"],
                    "certificate_subject": s["certificate_subject"],
                    "certificate_issuer": s["certificate_issuer"],
                    "certificate_serial": s["certificate_serial"],
                    "validation_report": s["validation_report"] or {},
                    "completed_at": iso(s["completed_at"]),
                }
                for s in icp
            ],
        }
        source = (req["contract_content"] or "") + "\n\nDOCWALLET_SIGNATURES_V2\n" + json.dumps(
            payload, sort_keys=True, ensure_ascii=False, default=str
        )
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def insert_event(request_id, party_id, event_type, payload):
        db.session.execute(
            text(
                "INSERT INTO signature_events (id, request_id, party_id, event_type, payload, created_at) "
                "VALUES (:id, :request_id, :party_id, :event_type, CAST(:payload AS JSONB), :created_at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "request_id": request_id,
                "party_id": party_id,
                "event_type": event_type,
                "payload": json.dumps(payload, ensure_ascii=False, default=str),
                "created_at": now(),
            },
        )

    def sync_session(session: IcpSignatureSession, party):
        if not session.external_session_id:
            return session

        details = provider().get_session(session.external_session_id)
        p_status = details.get("status") or ""
        session.status = normalized_status(str(p_status))

        cert = {}
        validation = {"providerStatus": p_status}
        signed_url = None
        if is_lacuna():
            cert, validation, signed_url = extract_lacuna_evidence(details)

        session.signed_file_url = signed_url or session.signed_file_url
        session.certificate_subject = cert.get("subject") or session.certificate_subject
        session.certificate_issuer = cert.get("issuer") or session.certificate_issuer
        session.certificate_serial = cert.get("serial") or session.certificate_serial
        session.validation_report = validation

        if session.status == "completed":
            completed_at = session.completed_at or now()
            session.completed_at = completed_at
            signed_name = cert.get("subject") or party["name"]
            signed_email = cert.get("email") or party["email"] or ""
            db.session.execute(
                text(
                    "UPDATE signature_parties SET status = 'signed', signed_name = :signed_name, signed_email = :signed_email, "
                    "signed_at = COALESCE(signed_at, :signed_at), evidence_level = 'icp_brasil_qualified', "
                    "confirmation_phrase = 'ICP-BRASIL', "
                    "consent_text = 'Assinatura realizada com certificado digital ICP-Brasil via provider externo.' "
                    "WHERE id = :party_id"
                ),
                {
                    "signed_name": signed_name,
                    "signed_email": signed_email,
                    "signed_at": completed_at,
                    "party_id": party["id"],
                },
            )
            already = row(
                "SELECT id FROM signature_events WHERE request_id = :request_id AND party_id = :party_id "
                "AND event_type = 'icp.signature.completed' LIMIT 1",
                {"request_id": session.request_id, "party_id": party["id"]},
            )
            if not already:
                insert_event(
                    session.request_id,
                    party["id"],
                    "icp.signature.completed",
                    {
                        "provider": PROVIDER_NAME,
                        "external_session_id": session.external_session_id,
                        "certificate_subject": session.certificate_subject,
                        "certificate_issuer": session.certificate_issuer,
                        "certificate_serial": session.certificate_serial,
                        "signed_file_url": bool(session.signed_file_url),
                        "validation": validation,
                    },
                )

            remaining = row(
                "SELECT COUNT(*) AS count FROM signature_parties WHERE request_id = :request_id AND status <> 'signed'",
                {"request_id": session.request_id},
            )
            if remaining and int(remaining["count"] or 0) == 0:
                db.session.flush()
                final_hash = build_request_final_hash(session.request_id)
                db.session.execute(
                    text(
                        "UPDATE signature_requests SET status = 'completed', completed_at = COALESCE(completed_at, :completed_at), "
                        "final_hash = :final_hash WHERE id = :request_id"
                    ),
                    {
                        "completed_at": completed_at,
                        "final_hash": final_hash,
                        "request_id": session.request_id,
                    },
                )
                completed_event = row(
                    "SELECT id FROM signature_events WHERE request_id = :request_id "
                    "AND event_type = 'request.completed' LIMIT 1",
                    {"request_id": session.request_id},
                )
                if not completed_event:
                    insert_event(
                        session.request_id,
                        None,
                        "request.completed",
                        {"final_hash": final_hash, "evidence_version": "2.0", "includes_icp_brasil": True},
                    )

        elif session.status == "user_cancelled":
            insert_event(
                session.request_id,
                party["id"],
                "icp.signature.cancelled",
                {"provider": PROVIDER_NAME, "external_session_id": session.external_session_id},
            )
        elif session.status == "processing_error":
            insert_event(
                session.request_id,
                party["id"],
                "icp.signature.error",
                {
                    "provider": PROVIDER_NAME,
                    "external_session_id": session.external_session_id,
                    "processing_error_code": details.get("processingErrorCode"),
                },
            )

        db.session.commit()
        return session

    @app.get("/api/icp-signature/config")
    def icp_signature_config():
        return jsonify(provider_capabilities())

    @app.get("/api/signatures/<request_id>/icp/sessions")
    @auth_required
    def list_request_icp_sessions(request_id):
        req = owned_signature_request(request_id)
        if not req:
            return fail("Solicitação de assinatura não encontrada.", 404)
        sessions = (
            IcpSignatureSession.query
            .filter_by(request_id=request_id)
            .order_by(IcpSignatureSession.created_at.desc())
            .all()
        )
        return jsonify({"success": True, "sessions": [pack_session(s) for s in sessions], "config": provider_capabilities()})

    @app.post("/api/signatures/<request_id>/icp/prepare")
    @auth_required
    def prepare_request_icp_sessions(request_id):
        req = owned_signature_request(request_id)
        if not req:
            return fail("Solicitação de assinatura não encontrada.", 404)
        parties = rows(
            "SELECT id, code, name, email, status FROM signature_parties "
            "WHERE request_id = :request_id ORDER BY id",
            {"request_id": request_id},
        )
        if not parties:
            return fail("Nenhuma parte encontrada para assinatura.", 404)
        try:
            created = [create_or_reuse_session(req, p, request.user.id) for p in parties]
            db.session.commit()
        except LacunaProviderError as exc:
            db.session.rollback()
            return fail(f"Falha ao preparar sessão Lacuna Rest PKI: {str(exc)}", 502)
        except Exception as exc:
            db.session.rollback()
            return fail(f"Falha ao preparar assinatura ICP-Brasil: {str(exc)}", 502)

        log(
            "signature.icp.prepare",
            request.user.id,
            "signature",
            request_id,
            {"sessions": len(created), "provider": PROVIDER_NAME, "enabled": ENABLED},
        )
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
        if party["status"] == "signed":
            return fail("Esta parte já assinou.", 400)

        try:
            session = create_or_reuse_session(req, party, req["user_id"])
            insert_event(
                req["id"],
                party["id"],
                "icp.signature.started",
                {"provider": PROVIDER_NAME, "enabled": ENABLED, "status": session.status},
            )
            db.session.commit()
        except LacunaProviderError as exc:
            db.session.rollback()
            return fail(f"Falha ao iniciar assinatura na Lacuna Rest PKI: {str(exc)}", 502)
        except Exception as exc:
            db.session.rollback()
            return fail(f"Falha ao iniciar assinatura ICP-Brasil: {str(exc)}", 502)

        return jsonify({"success": True, "session": pack_session(session), "config": provider_capabilities()})

    @app.get("/api/sign/<code>/icp/status")
    def public_icp_signature_status(code):
        party = get_party_by_code(code)
        if not party:
            return fail("Link de assinatura não encontrado.", 404)

        requested_external_id = (request.args.get("signatureSessionId") or request.args.get("sessionId") or "").strip()
        query = IcpSignatureSession.query.filter_by(request_id=party["request_id"], party_code=code)
        if requested_external_id:
            query = query.filter_by(external_session_id=requested_external_id)
        session = query.order_by(IcpSignatureSession.created_at.desc()).first()
        if not session:
            return fail("Sessão ICP-Brasil não encontrada.", 404)

        try:
            session = sync_session(session, party)
        except LacunaProviderError as exc:
            db.session.rollback()
            return fail(f"Falha ao consultar a Lacuna Rest PKI: {str(exc)}", 502)
        except Exception as exc:
            db.session.rollback()
            return fail(f"Falha ao sincronizar assinatura ICP-Brasil: {str(exc)}", 502)

        refreshed_party = get_party_by_code(code)
        req = get_signature_request(party["request_id"])
        next_party = row(
            "SELECT code, name, email FROM signature_parties "
            "WHERE request_id = :request_id AND status <> 'signed' ORDER BY id LIMIT 1",
            {"request_id": party["request_id"]},
        )
        packed_next = (
            {
                "code": next_party["code"],
                "name": next_party["name"],
                "email": next_party["email"],
                "url": "/sign/" + next_party["code"],
            }
            if next_party else None
        )
        return jsonify(
            {
                "success": True,
                "session": pack_session(session),
                "partyStatus": refreshed_party["status"] if refreshed_party else party["status"],
                "requestStatus": req["status"] if req else None,
                "finalHash": req["final_hash"] if req else None,
                "nextParty": packed_next,
                "config": provider_capabilities(),
            }
        )

    @app.post("/api/icp-signature/provider/callback")
    def icp_provider_callback():
        """Generic callback for providers that can call back with a shared secret.

        Lacuna Rest PKI currently uses the redirect-return synchronization flow
        above. This endpoint remains available for other adapters.
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
