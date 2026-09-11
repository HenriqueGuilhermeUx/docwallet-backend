def install_intelligence(app, db, Document, auth_required, fail, log):
    """DocWallet Intelligence: document intelligence, alerts, search, lifecycle and audit.

    Installed into the existing Flask app. It preserves auth, documents, sharing,
    signatures and blockchain routes; all endpoints enforce user isolation.
    """
    import datetime as dt
    import hashlib
    import json
    import os
    import re
    import secrets
    import uuid
    from abc import ABC, abstractmethod
    from pathlib import Path
    from typing import Any, Dict, List, Optional

    from flask import jsonify, request
    from sqlalchemy import text

    ENABLED = os.environ.get("DOCUMENT_INTELLIGENCE_ENABLED", "true").lower() == "true"
    PROVIDER_NAME = os.environ.get("DOCUMENT_INTELLIGENCE_PROVIDER", "internal").lower().strip()
    STORE_RAW = os.environ.get("DOCUMENT_INTELLIGENCE_STORE_RAW_TEXT", "true").lower() == "true"
    ALLOW_EXTERNAL = os.environ.get("DOCUMENT_INTELLIGENCE_ALLOW_EXTERNAL", "false").lower() == "true"

    DOCUMENT_TYPES = {"CONTRACT", "NDA", "INVOICE", "RECEIPT", "IDENTITY", "CERTIFICATE", "POWER_OF_ATTORNEY", "CORPORATE_DOCUMENT", "LEGAL_DOCUMENT", "MEDICAL_DOCUMENT", "OTHER"}

    class DocumentIntelligence(db.Model):
        __tablename__ = "document_intelligence"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        document_id = db.Column(db.String(36), nullable=False, index=True)
        provider = db.Column(db.String(80), nullable=False, default="internal")
        document_type = db.Column(db.String(80), nullable=False, default="OTHER", index=True)
        title = db.Column(db.String(255), nullable=True)
        issuer = db.Column(db.String(255), nullable=True)
        document_number = db.Column(db.String(120), nullable=True)
        issue_date = db.Column(db.Date, nullable=True, index=True)
        expiration_date = db.Column(db.Date, nullable=True, index=True)
        summary = db.Column(db.Text, nullable=True)
        confidence = db.Column(db.Float, nullable=False, default=0.0)
        raw_text = db.Column(db.Text, nullable=True)
        metadata_json = db.Column(db.JSON, nullable=True)
        contract_data = db.Column(db.JSON, nullable=True)
        normalized_data = db.Column(db.JSON, nullable=True)
        status = db.Column(db.String(40), nullable=False, default="ready")
        reviewed = db.Column(db.Boolean, nullable=False, default=False)
        version_number = db.Column(db.Integer, nullable=False, default=1)
        is_current = db.Column(db.Boolean, nullable=False, default=True, index=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)

    class DocumentParty(db.Model):
        __tablename__ = "document_party"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        document_id = db.Column(db.String(36), nullable=False, index=True)
        intelligence_id = db.Column(db.String(36), nullable=False, index=True)
        role = db.Column(db.String(80), nullable=True)
        name = db.Column(db.String(255), nullable=False)
        email = db.Column(db.String(255), nullable=True)
        identifier = db.Column(db.String(120), nullable=True)
        source_text = db.Column(db.Text, nullable=True)
        confidence = db.Column(db.Float, nullable=False, default=0.7)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)

    class DocumentDate(db.Model):
        __tablename__ = "document_date"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        document_id = db.Column(db.String(36), nullable=False, index=True)
        intelligence_id = db.Column(db.String(36), nullable=False, index=True)
        kind = db.Column(db.String(80), nullable=False, index=True)
        label = db.Column(db.String(160), nullable=True)
        value = db.Column(db.Date, nullable=False, index=True)
        source_text = db.Column(db.Text, nullable=True)
        confidence = db.Column(db.Float, nullable=False, default=0.7)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)

    class DocumentAmount(db.Model):
        __tablename__ = "document_amount"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        document_id = db.Column(db.String(36), nullable=False, index=True)
        intelligence_id = db.Column(db.String(36), nullable=False, index=True)
        kind = db.Column(db.String(80), nullable=False, default="amount")
        label = db.Column(db.String(160), nullable=True)
        currency = db.Column(db.String(16), nullable=False, default="BRL")
        value = db.Column(db.String(80), nullable=False)
        raw_value = db.Column(db.String(120), nullable=True)
        source_text = db.Column(db.Text, nullable=True)
        confidence = db.Column(db.Float, nullable=False, default=0.7)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)

    class DocumentObligation(db.Model):
        __tablename__ = "document_obligation"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        document_id = db.Column(db.String(36), nullable=False, index=True)
        intelligence_id = db.Column(db.String(36), nullable=False, index=True)
        party = db.Column(db.String(255), nullable=True)
        description = db.Column(db.Text, nullable=False)
        due_date = db.Column(db.Date, nullable=True, index=True)
        status = db.Column(db.String(40), nullable=False, default="open")
        source_text = db.Column(db.Text, nullable=True)
        confidence = db.Column(db.Float, nullable=False, default=0.65)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)

    class DocumentAlert(db.Model):
        __tablename__ = "document_alert"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        document_id = db.Column(db.String(36), nullable=True, index=True)
        intelligence_id = db.Column(db.String(36), nullable=True, index=True)
        alert_type = db.Column(db.String(80), nullable=False, index=True)
        title = db.Column(db.String(255), nullable=False)
        message = db.Column(db.Text, nullable=False)
        due_date = db.Column(db.Date, nullable=True, index=True)
        severity = db.Column(db.String(40), nullable=False, default="info")
        status = db.Column(db.String(40), nullable=False, default="active", index=True)
        entity_type = db.Column(db.String(80), nullable=True)
        entity_id = db.Column(db.String(80), nullable=True)
        metadata_json = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)

    class DocumentVersion(db.Model):
        __tablename__ = "document_version"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        document_id = db.Column(db.String(36), nullable=False, index=True)
        version_number = db.Column(db.Integer, nullable=False, default=1)
        file_hash = db.Column(db.String(80), nullable=False, index=True)
        parent_version_id = db.Column(db.String(36), nullable=True)
        lifecycle_status = db.Column(db.String(40), nullable=False, default="uploaded", index=True)
        created_by_user_id = db.Column(db.String(36), nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)

    class AuditEvent(db.Model):
        __tablename__ = "audit_event"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=True, index=True)
        action = db.Column(db.String(120), nullable=False, index=True)
        resource_type = db.Column(db.String(80), nullable=True, index=True)
        resource_id = db.Column(db.String(80), nullable=True, index=True)
        metadata_json = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)

    class UsageMetric(db.Model):
        __tablename__ = "usage_metric"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=True, index=True)
        metric_name = db.Column(db.String(120), nullable=False, index=True)
        quantity = db.Column(db.Integer, nullable=False, default=1)
        resource_type = db.Column(db.String(80), nullable=True)
        resource_id = db.Column(db.String(80), nullable=True)
        metadata_json = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)

    with app.app_context():
        db.create_all()
        try:
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_dw_intel_current ON document_intelligence(user_id, document_id, is_current)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_dw_alert_due ON document_alert(user_id, due_date, status)"))
            db.session.commit()
        except Exception:
            db.session.rollback()
        try:
            if db.engine.dialect.name == "postgresql":
                db.session.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(40) DEFAULT 'uploaded'"))
                db.session.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS intelligence_status VARCHAR(40) DEFAULT 'not_analyzed'"))
                db.session.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS version_number INTEGER DEFAULT 1"))
                db.session.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMP NULL"))
                db.session.commit()
            elif db.engine.dialect.name == "sqlite":
                cols = {r[1] for r in db.session.execute(text("PRAGMA table_info(documents)")).fetchall()}
                for col, ddl in {
                    "lifecycle_status": "ALTER TABLE documents ADD COLUMN lifecycle_status VARCHAR(40) DEFAULT 'uploaded'",
                    "intelligence_status": "ALTER TABLE documents ADD COLUMN intelligence_status VARCHAR(40) DEFAULT 'not_analyzed'",
                    "version_number": "ALTER TABLE documents ADD COLUMN version_number INTEGER DEFAULT 1",
                    "analyzed_at": "ALTER TABLE documents ADD COLUMN analyzed_at TIMESTAMP NULL",
                }.items():
                    if col not in cols:
                        db.session.execute(text(ddl))
                db.session.commit()
        except Exception:
            db.session.rollback()

    def iso(v):
        return v.isoformat() + "Z" if v else None

    def diso(v):
        return v.isoformat() if v else None

    def today():
        return dt.date.today()

    def parse_date(value: str) -> Optional[dt.date]:
        value = (value or "").strip()
        for fmt, pattern in [("%d/%m/%Y", r"\d{2}/\d{2}/\d{4}"), ("%d-%m-%Y", r"\d{2}-\d{2}-\d{4}"), ("%Y-%m-%d", r"\d{4}-\d{2}-\d{2}")]:
            m = re.search(pattern, value)
            if m:
                try:
                    return dt.datetime.strptime(m.group(0), fmt).date()
                except Exception:
                    pass
        return None

    def compact(s: str, limit: int = 700) -> str:
        return re.sub(r"\s+", " ", s or "").strip()[:limit]

    def money_value(raw: str) -> str:
        clean = re.sub(r"[^\d,\.]", "", raw or "")
        if "," in clean:
            clean = clean.replace(".", "").replace(",", ".")
        try:
            return f"{float(clean):.2f}"
        except Exception:
            return raw or "0"

    def safe_audit(action, user_id, resource_type, resource_id, metadata=None):
        safe = {k: v for k, v in (metadata or {}).items() if k not in {"content", "raw_text", "contract_content", "parties", "identifiers", "cpf", "cnpj"}}
        db.session.add(AuditEvent(user_id=user_id, action=action, resource_type=resource_type, resource_id=resource_id, metadata_json=safe))

    def metric(name, user_id, qty=1, resource_type=None, resource_id=None, metadata=None):
        # AV OS-safe: aggregate only; no document text, names, CPF, CNPJ or private content.
        db.session.add(UsageMetric(user_id=user_id, metric_name=name, quantity=qty, resource_type=resource_type, resource_id=resource_id, metadata_json=metadata or {}))

    def update_doc_state(doc_id, user_id, lifecycle, intel_status):
        try:
            db.session.execute(text("UPDATE documents SET lifecycle_status=:l, intelligence_status=:s, analyzed_at=:a WHERE id=:id AND user_id=:u"), {"l": lifecycle, "s": intel_status, "a": dt.datetime.utcnow(), "id": doc_id, "u": user_id})
        except Exception:
            db.session.rollback()

    def read_text(document) -> str:
        p = Path(document.file_path)
        if not p.exists():
            return ""
        try:
            if (document.file_type or "").startswith("text/") or p.suffix.lower() == ".txt":
                return p.read_text(encoding="utf-8", errors="ignore")
            if p.suffix.lower() == ".pdf" or document.file_type == "application/pdf":
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(str(p))
                    return "\n".join([(page.extract_text() or "") for page in reader.pages[:40]])
                except Exception:
                    return p.read_bytes()[:2_000_000].decode("latin-1", errors="ignore")
            return p.read_bytes()[:1_000_000].decode("utf-8", errors="ignore")
        except Exception:
            return ""

    class DocumentExtractionProvider(ABC):
        name = "base"
        @abstractmethod
        def extract(self, document, raw_text: str) -> Dict[str, Any]:
            raise NotImplementedError

    class InternalAIProvider(DocumentExtractionProvider):
        name = "internal"

        def classify(self, filename, text_value):
            h = f"{filename}\n{text_value}".lower()
            checks = [
                ("NDA", ["nda", "confidencialidade", "non-disclosure"]),
                ("CONTRACT", ["contrato", "contratante", "contratado", "vigência", "vigencia", "rescisão", "rescisao", "foro"]),
                ("INVOICE", ["nota fiscal", "nfs-e", "nf-e", "fatura", "invoice"]),
                ("RECEIPT", ["recibo", "recebi", "comprovante de pagamento"]),
                ("IDENTITY", ["cpf", "rg", "cnh", "passaporte", "identidade"]),
                ("CERTIFICATE", ["certificado", "certificate", "validade do certificado"]),
                ("POWER_OF_ATTORNEY", ["procuração", "procuracao", "outorgante", "outorgado"]),
                ("CORPORATE_DOCUMENT", ["contrato social", "estatuto", "ata de assembleia", "cnpj"]),
                ("LEGAL_DOCUMENT", ["petição", "peticao", "processo", "sentença", "sentenca", "tribunal"]),
                ("MEDICAL_DOCUMENT", ["laudo", "exame", "receita médica", "receita medica", "prontuário", "prontuario"]),
            ]
            for doc_type, keys in checks:
                if any(k in h for k in keys):
                    return doc_type
            return "OTHER"

        def parties(self, txt):
            out = []
            for role, pattern in [
                ("contratante", r"contratante[:\s-]+([^\n;]{3,160})"),
                ("contratado", r"contratad[oa][:\s-]+([^\n;]{3,160})"),
                ("outorgante", r"outorgante[:\s-]+([^\n;]{3,160})"),
                ("outorgado", r"outorgad[oa][:\s-]+([^\n;]{3,160})"),
                ("emitente", r"emitente[:\s-]+([^\n;]{3,160})"),
                ("destinatario", r"destinat[aá]rio[:\s-]+([^\n;]{3,160})"),
            ]:
                for m in re.finditer(pattern, txt, flags=re.I):
                    name = compact(m.group(1), 160).strip(" .,-")
                    if name and not any(p["name"].lower() == name.lower() and p["role"] == role for p in out):
                        out.append({"role": role, "name": name, "identifier": None, "source": compact(m.group(0), 240), "confidence": 0.74})
            ids = re.findall(r"\b(?:\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\b", txt)
            for i, identifier in enumerate(ids[:len(out)]):
                out[i]["identifier"] = identifier
            return out[:12]

        def dates(self, txt):
            out = []
            for m in re.finditer(r"\b\d{2}[/-]\d{2}[/-]\d{4}\b|\b\d{4}-\d{2}-\d{2}\b", txt):
                d = parse_date(m.group(0))
                if not d:
                    continue
                ctx = txt[max(0, m.start()-80):m.end()+80]
                l = ctx.lower()
                kind, label = "important", "Data relevante"
                if any(k in l for k in ["vencimento", "vence", "validade", "expira", "término", "termino"]):
                    kind, label = "expiration", "Vencimento"
                elif any(k in l for k in ["vigência", "vigencia", "início", "inicio"]):
                    kind, label = "effective", "Início/vigência"
                elif "pagamento" in l or "parcela" in l:
                    kind, label = "payment_due", "Pagamento"
                elif "emissão" in l or "emissao" in l:
                    kind, label = "issue", "Emissão"
                out.append({"kind": kind, "label": label, "value": d.isoformat(), "source": compact(ctx, 220), "confidence": 0.68})
            return list({(x["kind"], x["value"]): x for x in out}.values())[:25]

        def amounts(self, txt):
            out = []
            for m in re.finditer(r"R\$\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?|R\$\s?\d+(?:,\d{2})?", txt, flags=re.I):
                ctx = txt[max(0, m.start()-70):m.end()+70]
                l = ctx.lower()
                kind, label = "amount", "Valor"
                if any(k in l for k in ["mensal", "recorrente", "por mês", "por mes"]):
                    kind, label = "recurring_value", "Valor recorrente"
                elif any(k in l for k in ["total", "global", "contrato"]):
                    kind, label = "total_value", "Valor total"
                elif "multa" in l or "penalidade" in l:
                    kind, label = "penalty", "Multa/penalidade"
                out.append({"kind": kind, "label": label, "currency": "BRL", "value": money_value(m.group(0)), "raw": m.group(0), "source": compact(ctx, 220), "confidence": 0.72})
            return out[:25]

        def obligations(self, txt):
            out = []
            for line in re.split(r"[\n\r]+|(?<=\.)\s+", txt):
                c = compact(line, 700)
                l = c.lower()
                if len(c) > 18 and any(k in l for k in ["obriga", "deverá", "devera", "deve ", "compromete-se", "responsável", "responsavel", "entregar", "pagar"]):
                    out.append({"party": None, "description": c, "dueDate": None, "source": c, "confidence": 0.62})
            return out[:25]

        def contract(self, txt, dates, amounts, parties):
            l = txt.lower()
            obj = re.search(r"objeto[:\s-]+(.{20,900}?)(?:\n\s*\n|cl[áa]usula|valor|vig[êe]ncia|pagamento|$)", txt, flags=re.I | re.S)
            pay = re.search(r"(pagamento[:\s-]+.{20,600}?)(?:\n\s*\n|cl[áa]usula|multa|rescis|foro|$)", txt, flags=re.I | re.S)
            term = re.search(r"(rescis[aã]o[:\s-]+.{20,700}?)(?:\n\s*\n|cl[áa]usula|foro|$)", txt, flags=re.I | re.S)
            foro = re.search(r"foro\s+(?:da\s+)?(?:comarca\s+de\s+)?([A-Za-zÀ-ÿ\s/-]{3,100})", txt, flags=re.I)
            effective = next((d["value"] for d in dates if d["kind"] == "effective"), None)
            expiration = next((d["value"] for d in dates if d["kind"] == "expiration"), None)
            total = next((a["value"] for a in amounts if a["kind"] == "total_value"), None) or (amounts[0]["value"] if amounts else None)
            recurring = next((a["value"] for a in amounts if a["kind"] == "recurring_value"), None)
            risks = []
            if "renovação automática" in l or "renovacao automatica" in l:
                risks.append("Renovação automática identificada. Revise prazo de cancelamento.")
            if "multa" in l:
                risks.append("Cláusula de multa/penalidade identificada.")
            if "30 dias" in l and ("rescis" in l or "cancel" in l):
                risks.append("Aviso prévio de 30 dias identificado.")
            if "ipca" in l:
                risks.append("Reajuste por IPCA identificado.")
            return {
                "parties": parties,
                "object": compact(obj.group(1), 900) if obj else None,
                "effectiveDate": effective,
                "expirationDate": expiration,
                "renewalType": "automática" if ("renovação automática" in l or "renovacao automatica" in l) else ("manual" if "renovação" in l or "renovacao" in l else None),
                "totalValue": total,
                "recurringValue": recurring,
                "paymentTerms": compact(pay.group(1), 600) if pay else None,
                "obligations": self.obligations(txt),
                "penalties": [a for a in amounts if a["kind"] == "penalty"],
                "terminationRules": compact(term.group(1), 700) if term else None,
                "jurisdiction": compact(foro.group(1), 120) if foro else None,
                "signatories": [p for p in parties if p.get("role") in {"contratante", "contratado", "outorgante", "outorgado"}],
                "importantDates": dates,
                "riskFlags": risks,
            }

        def extract(self, document, raw_text):
            txt = raw_text or ""
            doc_type = self.classify(document.original_filename or document.name or "", txt)
            parties = self.parties(txt)
            dates = self.dates(txt)
            amounts = self.amounts(txt)
            identifiers = sorted(set(re.findall(r"\b(?:\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\b", txt)))
            issue = next((d["value"] for d in dates if d["kind"] == "issue"), None)
            expiration = next((d["value"] for d in dates if d["kind"] == "expiration"), None)
            contract = self.contract(txt, dates, amounts, parties) if doc_type in {"CONTRACT", "NDA", "POWER_OF_ATTORNEY", "LEGAL_DOCUMENT"} else {}
            summary_bits = [f"Documento: {document.name}", f"Tipo detectado: {doc_type}"]
            if parties:
                summary_bits.append("Partes: " + ", ".join([p["name"] for p in parties[:3]]))
            if amounts:
                summary_bits.append("Valores: " + ", ".join([a["raw"] for a in amounts[:3]]))
            if expiration:
                summary_bits.append(f"Vencimento: {expiration}")
            if contract.get("renewalType"):
                summary_bits.append(f"Renovação: {contract['renewalType']}")
            if contract.get("riskFlags"):
                summary_bits.append("Atenção: " + " ".join(contract["riskFlags"][:3]))
            confidence = min(0.93, 0.52 + (0.15 if txt else 0) + (0.1 if dates else 0) + (0.08 if amounts else 0) + (0.1 if parties else 0) + (0.05 if doc_type != "OTHER" else 0))
            normalized = {
                "documentType": doc_type,
                "title": document.name,
                "issuer": parties[0]["name"] if parties else None,
                "documentNumber": identifiers[0] if identifiers else None,
                "issueDate": issue,
                "expirationDate": expiration,
                "parties": parties,
                "identifiers": identifiers,
                "amounts": amounts,
                "dates": dates,
                "summary": "\n".join(summary_bits) if txt else f"Documento {document.name} salvo. O texto interno não foi extraído automaticamente.",
                "confidence": round(confidence, 2),
                "rawText": txt,
                "metadata": {"provider": self.name, "mimeType": document.file_type, "fileSize": document.file_size, "fileHash": document.file_hash, "externalProviderUsed": False},
                "contract": contract,
            }
            return normalized

    class MockProvider(InternalAIProvider):
        name = "mock"

    class DocStructProvider(InternalAIProvider):
        name = "docstruct"
        def extract(self, document, raw_text):
            result = super().extract(document, raw_text)
            result["metadata"]["provider"] = "docstruct-fallback-internal" if not ALLOW_EXTERNAL else "docstruct-adapter-pending"
            result["metadata"]["externalProviderUsed"] = False
            return result

    def provider():
        if PROVIDER_NAME == "mock":
            return MockProvider()
        if PROVIDER_NAME == "docstruct":
            return DocStructProvider()
        return InternalAIProvider()

    def owned_doc(document_id):
        return Document.query.filter_by(id=document_id, user_id=request.user.id).first()

    def current_intel(document_id, user_id):
        return DocumentIntelligence.query.filter_by(document_id=document_id, user_id=user_id, is_current=True).order_by(DocumentIntelligence.created_at.desc()).first()

    def ensure_version(document, user_id):
        existing = DocumentVersion.query.filter_by(user_id=user_id, document_id=document.id, file_hash=document.file_hash).order_by(DocumentVersion.version_number.desc()).first()
        if existing:
            return existing
        last = DocumentVersion.query.filter_by(user_id=user_id, document_id=document.id).order_by(DocumentVersion.version_number.desc()).first()
        row = DocumentVersion(user_id=user_id, document_id=document.id, version_number=(last.version_number + 1 if last else 1), file_hash=document.file_hash, parent_version_id=(last.id if last else None), lifecycle_status="uploaded", created_by_user_id=user_id)
        db.session.add(row)
        return row

    def make_alert(user_id, document_id, intelligence_id, alert_type, title, message, due_date=None, severity="info", entity_type=None, entity_id=None, metadata=None):
        row = DocumentAlert(user_id=user_id, document_id=document_id, intelligence_id=intelligence_id, alert_type=alert_type, title=title[:255], message=message, due_date=due_date, severity=severity, entity_type=entity_type, entity_id=entity_id, metadata_json=metadata or {})
        db.session.add(row)
        return row

    def regenerate_alerts(user_id, document_id, intel_id, result):
        DocumentAlert.query.filter_by(user_id=user_id, document_id=document_id, status="active").update({"status": "archived"})
        now = today()
        exp = parse_date(result.get("expirationDate") or "")
        doc_type = result.get("documentType") or "OTHER"
        if exp:
            days = (exp - now).days
            if -30 <= days <= 90:
                sev = "danger" if days <= 7 else ("warning" if days <= 30 else "info")
                label = "Contrato" if doc_type in {"CONTRACT", "NDA"} else "Documento"
                make_alert(user_id, document_id, intel_id, "document.expiring", f"{label} vence em {days} dias" if days >= 0 else f"{label} vencido há {abs(days)} dias", f"{label} '{result.get('title')}' tem vencimento em {exp.isoformat()}.", exp, sev, "document", document_id)
                if doc_type in {"CONTRACT", "NDA"}:
                    make_alert(user_id, document_id, intel_id, "contract.renewal_upcoming", "Renovação/vencimento contratual próximo", "Revise renovação, aviso prévio, rescisão e obrigações antes do vencimento.", exp, sev, "document", document_id)
        for d in result.get("dates", [])[:30]:
            due = parse_date(d.get("value") or "")
            if due and d.get("kind") == "payment_due" and -7 <= (due - now).days <= 45:
                make_alert(user_id, document_id, intel_id, "payment.due", "Pagamento próximo", f"Data de pagamento encontrada: {due.isoformat()}.", due, "warning", "document_date", None)
        if (result.get("contract") or {}).get("signatories"):
            make_alert(user_id, document_id, intel_id, "signature.pending", "Documento pode exigir assinatura", "Foram encontrados possíveis signatários. Você pode criar uma solicitação de assinatura a partir deste documento.", None, "info", "document", document_id)

    def store(document, user_id, result):
        DocumentIntelligence.query.filter_by(user_id=user_id, document_id=document.id, is_current=True).update({"is_current": False})
        version = ensure_version(document, user_id)
        row = DocumentIntelligence(user_id=user_id, document_id=document.id, provider=result.get("metadata", {}).get("provider") or PROVIDER_NAME, document_type=result.get("documentType") or "OTHER", title=result.get("title") or document.name, issuer=result.get("issuer"), document_number=result.get("documentNumber"), issue_date=parse_date(result.get("issueDate") or ""), expiration_date=parse_date(result.get("expirationDate") or ""), summary=result.get("summary"), confidence=float(result.get("confidence") or 0), raw_text=(result.get("rawText") if STORE_RAW else compact(result.get("rawText") or "", 1000)), metadata_json=result.get("metadata") or {}, contract_data=result.get("contract") or {}, normalized_data={k: v for k, v in result.items() if k != "rawText"}, status=("ready" if float(result.get("confidence") or 0) >= 0.62 else "needs_review"), version_number=version.version_number, is_current=True)
        db.session.add(row)
        db.session.flush()
        for p in result.get("parties", [])[:30]:
            if (p.get("name") or "").strip():
                db.session.add(DocumentParty(user_id=user_id, document_id=document.id, intelligence_id=row.id, role=p.get("role"), name=p.get("name")[:255], email=(p.get("email") or "")[:255], identifier=(p.get("identifier") or "")[:120], source_text=p.get("source"), confidence=float(p.get("confidence") or 0.7)))
        for d in result.get("dates", [])[:40]:
            value = parse_date(d.get("value") or "")
            if value:
                db.session.add(DocumentDate(user_id=user_id, document_id=document.id, intelligence_id=row.id, kind=(d.get("kind") or "important")[:80], label=(d.get("label") or "Data")[:160], value=value, source_text=d.get("source"), confidence=float(d.get("confidence") or 0.7)))
        for a in result.get("amounts", [])[:40]:
            db.session.add(DocumentAmount(user_id=user_id, document_id=document.id, intelligence_id=row.id, kind=(a.get("kind") or "amount")[:80], label=(a.get("label") or "Valor")[:160], currency=(a.get("currency") or "BRL")[:16], value=str(a.get("value") or a.get("raw") or "0")[:80], raw_value=str(a.get("raw") or "")[:120], source_text=a.get("source"), confidence=float(a.get("confidence") or 0.7)))
        for o in (result.get("contract") or {}).get("obligations", [])[:40]:
            if (o.get("description") or "").strip():
                db.session.add(DocumentObligation(user_id=user_id, document_id=document.id, intelligence_id=row.id, party=o.get("party"), description=o.get("description")[:1200], due_date=parse_date(o.get("dueDate") or ""), status=o.get("status") or "open", source_text=o.get("source"), confidence=float(o.get("confidence") or 0.65)))
        regenerate_alerts(user_id, document.id, row.id, result)
        safe_audit("document.analyzed", user_id, "document", document.id, {"document_type": row.document_type, "confidence": row.confidence, "provider": row.provider})
        metric("documents_processed", user_id, 1, "document", document.id, {"provider": row.provider, "document_type": row.document_type})
        metric("ai_pages_processed", user_id, max(1, int((document.file_size or 1) / 4000)), "document", document.id, {"provider": row.provider})
        return row

    def pack_party(x):
        return {"id": x.id, "role": x.role, "name": x.name, "email": x.email, "identifier": x.identifier, "source": x.source_text, "confidence": x.confidence}
    def pack_date(x):
        return {"id": x.id, "kind": x.kind, "label": x.label, "value": diso(x.value), "source": x.source_text, "confidence": x.confidence}
    def pack_amount(x):
        return {"id": x.id, "kind": x.kind, "label": x.label, "currency": x.currency, "value": x.value, "raw": x.raw_value, "source": x.source_text, "confidence": x.confidence}
    def pack_obligation(x):
        return {"id": x.id, "party": x.party, "description": x.description, "dueDate": diso(x.due_date), "status": x.status, "source": x.source_text, "confidence": x.confidence}
    def pack_alert(x):
        return {"id": x.id, "documentId": x.document_id, "intelligenceId": x.intelligence_id, "type": x.alert_type, "title": x.title, "message": x.message, "dueDate": diso(x.due_date), "severity": x.severity, "status": x.status, "entityType": x.entity_type, "entityId": x.entity_id, "metadata": x.metadata_json or {}, "createdAt": iso(x.created_at)}
    def pack_version(x):
        return {"id": x.id, "documentId": x.document_id, "versionNumber": x.version_number, "fileHash": x.file_hash, "parentVersionId": x.parent_version_id, "lifecycleStatus": x.lifecycle_status, "createdAt": iso(x.created_at)}

    def pack_intel(row, include_raw=False):
        parties = DocumentParty.query.filter_by(user_id=row.user_id, intelligence_id=row.id).all()
        dates = DocumentDate.query.filter_by(user_id=row.user_id, intelligence_id=row.id).order_by(DocumentDate.value.asc()).all()
        amounts = DocumentAmount.query.filter_by(user_id=row.user_id, intelligence_id=row.id).all()
        obligations = DocumentObligation.query.filter_by(user_id=row.user_id, intelligence_id=row.id).all()
        alerts = DocumentAlert.query.filter_by(user_id=row.user_id, intelligence_id=row.id, status="active").order_by(DocumentAlert.created_at.desc()).all()
        versions = DocumentVersion.query.filter_by(user_id=row.user_id, document_id=row.document_id).order_by(DocumentVersion.version_number.desc()).all()
        payload = {"id": row.id, "documentId": row.document_id, "provider": row.provider, "documentType": row.document_type, "title": row.title, "issuer": row.issuer, "documentNumber": row.document_number, "issueDate": diso(row.issue_date), "expirationDate": diso(row.expiration_date), "summary": row.summary, "confidence": row.confidence, "metadata": row.metadata_json or {}, "contract": row.contract_data or {}, "parties": [pack_party(p) for p in parties], "dates": [pack_date(d) for d in dates], "amounts": [pack_amount(a) for a in amounts], "obligations": [pack_obligation(o) for o in obligations], "alerts": [pack_alert(a) for a in alerts], "versions": [pack_version(v) for v in versions], "status": row.status, "reviewed": row.reviewed, "versionNumber": row.version_number, "isCurrent": row.is_current, "createdAt": iso(row.created_at), "updatedAt": iso(row.updated_at)}
        if include_raw:
            payload["rawText"] = row.raw_text
        return payload

    def pending_signatures(user_id):
        try:
            return int(db.session.execute(text("""
                SELECT COUNT(*) FROM signature_parties sp
                JOIN signature_requests sr ON sr.id = sp.request_id
                WHERE sr.user_id = :u AND sp.status != 'signed' AND sr.status != 'cancelled'
            """), {"u": user_id}).scalar() or 0)
        except Exception:
            return 0

    @app.post("/api/documents/<document_id>/analyze")
    @auth_required
    def analyze_document(document_id):
        if not ENABLED:
            return fail("DocWallet Intelligence não está habilitado.", 503)
        document = owned_doc(document_id)
        if not document:
            return fail("Documento não encontrado.", 404)
        update_doc_state(document.id, request.user.id, "processing", "processing")
        raw = read_text(document)
        try:
            result = provider().extract(document, raw)
            row = store(document, request.user.id, result)
            update_doc_state(document.id, request.user.id, "ready" if row.status == "ready" else "needs_review", row.status)
            db.session.commit()
            log("document.analyzed", request.user.id, "document", document.id, {"document_type": row.document_type, "confidence": row.confidence})
            return jsonify({"success": True, "intelligence": pack_intel(row)})
        except Exception as exc:
            db.session.rollback()
            try:
                update_doc_state(document.id, request.user.id, "needs_review", "error")
                db.session.commit()
            except Exception:
                db.session.rollback()
            return fail(f"Erro ao analisar documento: {str(exc)}", 500)

    @app.get("/api/documents/<document_id>/intelligence")
    @auth_required
    def get_document_intelligence(document_id):
        document = owned_doc(document_id)
        if not document:
            return fail("Documento não encontrado.", 404)
        row = current_intel(document.id, request.user.id)
        if not row:
            return jsonify({"success": True, "intelligence": None, "message": "Documento ainda não analisado."})
        return jsonify({"success": True, "intelligence": pack_intel(row, request.args.get("include_raw", "false").lower() == "true")})

    @app.patch("/api/documents/<document_id>/intelligence")
    @auth_required
    def patch_document_intelligence(document_id):
        document = owned_doc(document_id)
        if not document:
            return fail("Documento não encontrado.", 404)
        row = current_intel(document.id, request.user.id)
        if not row:
            return fail("Inteligência ainda não criada para este documento.", 404)
        body = request.get_json(silent=True) or {}
        if body.get("documentType") in DOCUMENT_TYPES:
            row.document_type = body["documentType"]
        if "title" in body: row.title = body.get("title")
        if "issuer" in body: row.issuer = body.get("issuer")
        if "documentNumber" in body: row.document_number = body.get("documentNumber")
        if "summary" in body: row.summary = body.get("summary")
        if "issueDate" in body: row.issue_date = parse_date(body.get("issueDate") or "")
        if "expirationDate" in body: row.expiration_date = parse_date(body.get("expirationDate") or "")
        if isinstance(body.get("contract"), dict):
            merged = row.contract_data or {}
            merged.update(body["contract"])
            row.contract_data = merged
        row.reviewed = True
        row.status = "reviewed"
        safe_audit("document.intelligence_reviewed", request.user.id, "document", document.id, {"fields": sorted([k for k in body.keys() if k != "contract"])})
        db.session.commit()
        return jsonify({"success": True, "intelligence": pack_intel(row)})

    @app.get("/api/documents/<document_id>/alerts")
    @auth_required
    def get_document_alerts(document_id):
        document = owned_doc(document_id)
        if not document:
            return fail("Documento não encontrado.", 404)
        alerts = DocumentAlert.query.filter_by(user_id=request.user.id, document_id=document.id, status="active").order_by(DocumentAlert.created_at.desc()).all()
        return jsonify({"success": True, "alerts": [pack_alert(a) for a in alerts]})

    @app.get("/api/documents/<document_id>/audit-trail")
    @auth_required
    def get_audit_trail(document_id):
        document = owned_doc(document_id)
        if not document:
            return fail("Documento não encontrado.", 404)
        events = AuditEvent.query.filter_by(user_id=request.user.id, resource_type="document", resource_id=document.id).order_by(AuditEvent.created_at.desc()).limit(100).all()
        return jsonify({"success": True, "events": [{"id": e.id, "action": e.action, "resourceType": e.resource_type, "resourceId": e.resource_id, "metadata": e.metadata_json or {}, "createdAt": iso(e.created_at)} for e in events]})

    @app.post("/api/documents/<document_id>/signature-request")
    @auth_required
    def create_signature_from_document(document_id):
        document = owned_doc(document_id)
        if not document:
            return fail("Documento não encontrado.", 404)
        intel = current_intel(document.id, request.user.id)
        body = request.get_json(silent=True) or {}
        parties = body.get("parties") or []
        if not parties and intel:
            parties = [{"name": p.name, "email": p.email} for p in DocumentParty.query.filter_by(user_id=request.user.id, intelligence_id=intel.id).all()]
        parties = [p for p in parties if (p.get("name") or "").strip()]
        if not parties:
            return fail("Informe pelo menos um signatário.", 400)
        req_table = db.metadata.tables.get("signature_requests")
        party_table = db.metadata.tables.get("signature_parties")
        event_table = db.metadata.tables.get("signature_events")
        if req_table is None or party_table is None:
            return fail("Motor de assinatura ainda não está disponível.", 503)
        content = body.get("contract_content") or (intel.raw_text if intel and intel.raw_text else f"Documento: {document.name}\nHash SHA-256: {document.file_hash}")
        request_id = str(uuid.uuid4())
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = dt.datetime.utcnow()
        db.session.execute(req_table.insert().values(id=request_id, user_id=request.user.id, title=(body.get("title") or document.name)[:240], contract_content=content, content_hash=content_hash, final_hash=None, status="pending", created_at=now, completed_at=None))
        created = []
        for p in parties[:20]:
            pid, code = str(uuid.uuid4()), secrets.token_hex(20)
            name, email = (p.get("name") or "").strip()[:180], (p.get("email") or "").strip().lower()[:180]
            db.session.execute(party_table.insert().values(id=pid, request_id=request_id, code=code, name=name, email=email, status="pending", signed_name=None, signed_email=None, signed_at=None, ip_address=None, user_agent=None))
            created.append({"id": pid, "name": name, "email": email, "status": "pending", "code": code, "url": "/sign/" + code})
        if event_table is not None:
            db.session.execute(event_table.insert().values(id=str(uuid.uuid4()), request_id=request_id, party_id=None, event_type="request.created_from_document", payload={"document_id": document.id}, created_at=now))
        make_alert(request.user.id, document.id, intel.id if intel else None, "signature.pending", "Assinaturas pendentes", f"{len(created)} assinatura(s) pendente(s) para este documento.", None, "warning", "signature_request", request_id)
        safe_audit("signature_request.created_from_document", request.user.id, "document", document.id, {"signature_request_id": request_id, "signers": len(created)})
        metric("signature_requests", request.user.id, 1, "document", document.id, {})
        db.session.commit()
        return jsonify({"success": True, "request": {"id": request_id, "title": document.name, "status": "pending", "parties": created}}), 201

    @app.get("/api/documents/search")
    @auth_required
    def search_documents():
        q = (request.args.get("q") or request.args.get("query") or "").strip().lower()
        days = int(request.args.get("days", "60") or 60)
        if not q:
            return jsonify({"success": True, "results": []})
        now, horizon = today(), today() + dt.timedelta(days=days)
        results = []
        docs = Document.query.filter_by(user_id=request.user.id).order_by(Document.created_at.desc()).limit(250).all()
        for doc in docs:
            intel = current_intel(doc.id, request.user.id)
            alerts = DocumentAlert.query.filter_by(user_id=request.user.id, document_id=doc.id, status="active").all()
            blob = f"{doc.name} {doc.original_filename} {doc.doc_type} {doc.category}".lower()
            if intel:
                blob += " " + " ".join([intel.document_type or "", intel.title or "", intel.issuer or "", intel.summary or "", json.dumps(intel.contract_data or {}, ensure_ascii=False), intel.raw_text or ""]).lower()
            score, reasons = 0, []
            if q in blob:
                score += 50; reasons.append("conteúdo/metadados")
            if any(k in q for k in ["vence", "vencem", "vencimento", "expira", "60 dias", "30 dias"]):
                rel = [a for a in alerts if a.due_date and now <= a.due_date <= horizon]
                if rel:
                    score += 40; reasons.append(f"{len(rel)} alerta(s) no período")
            if "multa" in q and intel and "multa" in json.dumps(intel.contract_data or {}, ensure_ascii=False).lower():
                score += 30; reasons.append("multa identificada")
            if score:
                results.append({"document": {"id": doc.id, "name": doc.name, "type": doc.doc_type, "category": doc.category, "fileHash": doc.file_hash, "createdAt": iso(doc.created_at)}, "intelligence": pack_intel(intel) if intel else None, "alerts": [pack_alert(a) for a in alerts], "score": score, "reasons": reasons})
        return jsonify({"success": True, "query": q, "results": sorted(results, key=lambda x: x["score"], reverse=True)[:30]})

    @app.get("/api/contracts/upcoming-expirations")
    @auth_required
    def upcoming_expirations():
        days = int(request.args.get("days", "60") or 60)
        now, horizon = today(), today() + dt.timedelta(days=days)
        alerts = DocumentAlert.query.filter(DocumentAlert.user_id == request.user.id, DocumentAlert.status == "active", DocumentAlert.due_date.isnot(None), DocumentAlert.due_date >= now, DocumentAlert.due_date <= horizon).order_by(DocumentAlert.due_date.asc()).limit(100).all()
        return jsonify({"success": True, "days": days, "alerts": [pack_alert(a) for a in alerts]})

    @app.get("/api/intelligence/dashboard")
    @auth_required
    def dashboard():
        user_id = request.user.id
        now, d30 = today(), today() + dt.timedelta(days=30)
        total_docs = Document.query.filter_by(user_id=user_id).count()
        analyzed = DocumentIntelligence.query.filter_by(user_id=user_id, is_current=True).count()
        contracts = DocumentIntelligence.query.filter(DocumentIntelligence.user_id == user_id, DocumentIntelligence.is_current == True, DocumentIntelligence.document_type.in_(["CONTRACT", "NDA", "POWER_OF_ATTORNEY", "LEGAL_DOCUMENT"])).count()
        expiring = DocumentAlert.query.filter(DocumentAlert.user_id == user_id, DocumentAlert.status == "active", DocumentAlert.due_date.isnot(None), DocumentAlert.due_date >= now, DocumentAlert.due_date <= d30).count()
        active_alerts = DocumentAlert.query.filter_by(user_id=user_id, status="active").count()
        upcoming = DocumentAlert.query.filter(DocumentAlert.user_id == user_id, DocumentAlert.status == "active", DocumentAlert.due_date.isnot(None), DocumentAlert.due_date >= now).order_by(DocumentAlert.due_date.asc()).limit(8).all()
        recent_docs = Document.query.filter_by(user_id=user_id).order_by(Document.created_at.desc()).limit(8).all()
        high_value = DocumentAmount.query.filter_by(user_id=user_id, kind="total_value").limit(8).all()
        return jsonify({"success": True, "metrics": {"documents": total_docs, "documentsAnalyzed": analyzed, "activeContracts": contracts, "expiringIn30Days": expiring, "pendingSignatures": pending_signatures(user_id), "documentsWithAlerts": active_alerts}, "upcomingExpirations": [pack_alert(a) for a in upcoming], "recentDocuments": [{"id": d.id, "name": d.name, "type": d.doc_type, "createdAt": iso(d.created_at), "fileHash": d.file_hash} for d in recent_docs], "highValueContracts": [pack_amount(a) for a in high_value]})

    @app.get("/api/intelligence/metrics")
    @auth_required
    def metrics():
        rows = UsageMetric.query.filter_by(user_id=request.user.id).order_by(UsageMetric.created_at.desc()).limit(250).all()
        totals = {}
        for row in rows:
            totals[row.metric_name] = totals.get(row.metric_name, 0) + int(row.quantity or 0)
        return jsonify({"success": True, "metrics": totals})

    print("DocWallet Intelligence installed.")
