def install_docflow(app, db, Document, auth_required, fail, log):
    """DocFlow by DocWallet: B2B document-to-process layer.

    This module is installed into the existing DocWallet Flask app and reuses
    the existing Document model plus DocWallet Intelligence tables. It does not
    duplicate the document intelligence engine. It creates workflow, submission,
    approval, integration, audit and usage tables with user/tenant isolation.
    """
    import datetime as dt
    import os
    import re
    import uuid
    from typing import Any, Dict, List, Optional

    from flask import jsonify, request
    from sqlalchemy import text

    ENABLED = os.environ.get("DOCFLOW_ENABLED", "true").lower() == "true"
    DEFAULT_TENANT_NAME = os.environ.get("DOCFLOW_DEFAULT_TENANT_NAME", "DocFlow Workspace")
    EXTERNAL_INTEGRATIONS_ENABLED = os.environ.get("DOCFLOW_EXTERNAL_INTEGRATIONS_ENABLED", "false").lower() == "true"

    WORKFLOW_BLOCKS = [
        "extract",
        "validate",
        "ask_user",
        "approve",
        "reject",
        "notify",
        "assign",
        "create_record",
        "webhook",
        "email",
        "export",
        "archive",
    ]

    class DocFlowTenant(db.Model):
        __tablename__ = "docflow_tenant"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        owner_user_id = db.Column(db.String(36), nullable=False, index=True)
        name = db.Column(db.String(180), nullable=False)
        slug = db.Column(db.String(120), nullable=False, index=True)
        plan = db.Column(db.String(60), nullable=False, default="starter")
        metadata_json = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)

    class DocFlowMember(db.Model):
        __tablename__ = "docflow_member"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        tenant_id = db.Column(db.String(36), nullable=False, index=True)
        user_id = db.Column(db.String(36), nullable=True, index=True)
        email = db.Column(db.String(255), nullable=False, index=True)
        role = db.Column(db.String(60), nullable=False, default="owner")
        department = db.Column(db.String(120), nullable=True)
        cost_center = db.Column(db.String(120), nullable=True)
        status = db.Column(db.String(40), nullable=False, default="active")
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)

    class DocFlowWorkflow(db.Model):
        __tablename__ = "docflow_workflow"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        tenant_id = db.Column(db.String(36), nullable=False, index=True)
        name = db.Column(db.String(200), nullable=False)
        slug = db.Column(db.String(150), nullable=False, index=True)
        description = db.Column(db.Text, nullable=True)
        template_key = db.Column(db.String(120), nullable=True, index=True)
        trigger_type = db.Column(db.String(80), nullable=False, default="document_received")
        document_type = db.Column(db.String(80), nullable=True, index=True)
        schema_json = db.Column(db.JSON, nullable=False, default=dict)
        questions_json = db.Column(db.JSON, nullable=True)
        steps_json = db.Column(db.JSON, nullable=False, default=list)
        integrations_json = db.Column(db.JSON, nullable=True)
        status = db.Column(db.String(40), nullable=False, default="active", index=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)

    class DocFlowSubmission(db.Model):
        __tablename__ = "docflow_submission"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        tenant_id = db.Column(db.String(36), nullable=False, index=True)
        workflow_id = db.Column(db.String(36), nullable=False, index=True)
        document_id = db.Column(db.String(36), nullable=True, index=True)
        title = db.Column(db.String(255), nullable=False)
        source_type = db.Column(db.String(80), nullable=False, default="upload")
        status = db.Column(db.String(60), nullable=False, default="received", index=True)
        current_step = db.Column(db.String(120), nullable=True)
        extracted_data = db.Column(db.JSON, nullable=True)
        answers_json = db.Column(db.JSON, nullable=True)
        validation_errors = db.Column(db.JSON, nullable=True)
        approval_status = db.Column(db.String(40), nullable=False, default="not_required", index=True)
        hash_value = db.Column(db.String(80), nullable=True, index=True)
        completed_at = db.Column(db.DateTime, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)

    class DocFlowApproval(db.Model):
        __tablename__ = "docflow_approval"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        tenant_id = db.Column(db.String(36), nullable=False, index=True)
        workflow_id = db.Column(db.String(36), nullable=False, index=True)
        submission_id = db.Column(db.String(36), nullable=False, index=True)
        approver_email = db.Column(db.String(255), nullable=True)
        approver_user_id = db.Column(db.String(36), nullable=True, index=True)
        status = db.Column(db.String(40), nullable=False, default="pending", index=True)
        decision_note = db.Column(db.Text, nullable=True)
        decided_at = db.Column(db.DateTime, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)

    class DocFlowIntegration(db.Model):
        __tablename__ = "docflow_integration"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        tenant_id = db.Column(db.String(36), nullable=False, index=True)
        workflow_id = db.Column(db.String(36), nullable=True, index=True)
        integration_type = db.Column(db.String(80), nullable=False, index=True)
        name = db.Column(db.String(160), nullable=False)
        config_json = db.Column(db.JSON, nullable=True)
        enabled = db.Column(db.Boolean, nullable=False, default=False)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)

    class DocFlowEvent(db.Model):
        __tablename__ = "docflow_event"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        tenant_id = db.Column(db.String(36), nullable=True, index=True)
        workflow_id = db.Column(db.String(36), nullable=True, index=True)
        submission_id = db.Column(db.String(36), nullable=True, index=True)
        action = db.Column(db.String(120), nullable=False, index=True)
        actor_user_id = db.Column(db.String(36), nullable=True, index=True)
        metadata_json = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)

    class DocFlowMetric(db.Model):
        __tablename__ = "docflow_metric"
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        tenant_id = db.Column(db.String(36), nullable=True, index=True)
        metric_name = db.Column(db.String(120), nullable=False, index=True)
        quantity = db.Column(db.Integer, nullable=False, default=1)
        resource_type = db.Column(db.String(80), nullable=True)
        resource_id = db.Column(db.String(80), nullable=True)
        metadata_json = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)

    def slugify(value: str) -> str:
        value = (value or "docflow").lower().strip()
        value = re.sub(r"[^a-z0-9]+", "-", value)
        value = re.sub(r"^-+|-+$", "", value)
        return value or "docflow"

    def iso(value):
        return value.isoformat() + "Z" if value else None

    def owned_tenant(tenant_id: str):
        return DocFlowTenant.query.filter_by(id=tenant_id, owner_user_id=request.user.id).first()

    def default_tenant():
        tenant = DocFlowTenant.query.filter_by(owner_user_id=request.user.id).order_by(DocFlowTenant.created_at.asc()).first()
        if tenant:
            return tenant
        tenant = DocFlowTenant(owner_user_id=request.user.id, name=DEFAULT_TENANT_NAME, slug=f"workspace-{request.user.id[:8]}", metadata_json={"source": "auto"})
        db.session.add(tenant)
        db.session.flush()
        db.session.add(DocFlowMember(tenant_id=tenant.id, user_id=request.user.id, email=request.user.email, role="owner", status="active"))
        event("docflow.tenant_created", tenant.id, None, None, {"plan": tenant.plan})
        return tenant

    def owned_workflow(workflow_id: str):
        return DocFlowWorkflow.query.filter_by(id=workflow_id, user_id=request.user.id).first()

    def owned_submission(submission_id: str):
        return DocFlowSubmission.query.filter_by(id=submission_id, user_id=request.user.id).first()

    def event(action: str, tenant_id: Optional[str], workflow_id: Optional[str], submission_id: Optional[str], metadata: Optional[Dict[str, Any]] = None):
        row = DocFlowEvent(user_id=request.user.id, tenant_id=tenant_id, workflow_id=workflow_id, submission_id=submission_id, action=action, actor_user_id=request.user.id, metadata_json=metadata or {})
        db.session.add(row)
        try:
            log(action, request.user.id, "docflow", submission_id or workflow_id or tenant_id, {"tenant_id": tenant_id, **(metadata or {})})
        except Exception:
            pass
        return row

    def metric(name: str, tenant_id: Optional[str], quantity: int = 1, resource_type: Optional[str] = None, resource_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None):
        row = DocFlowMetric(user_id=request.user.id, tenant_id=tenant_id, metric_name=name, quantity=quantity, resource_type=resource_type, resource_id=resource_id, metadata_json=metadata or {})
        db.session.add(row)
        return row

    def template_catalog() -> List[Dict[str, Any]]:
        def wf(key, name, description, document_type, fields, questions, steps):
            return {
                "key": key,
                "name": name,
                "description": description,
                "documentType": document_type,
                "schema": {"fields": fields, "version": 1},
                "questions": questions,
                "steps": steps,
            }
        base_approval = [
            {"type": "extract", "label": "Extrair dados do documento", "config": {"engine": "AV Document Intelligence"}},
            {"type": "validate", "label": "Validar campos obrigatórios"},
            {"type": "ask_user", "label": "Pedir informações complementares"},
            {"type": "approve", "label": "Enviar para aprovação"},
            {"type": "export", "label": "Exportar/registrar em integração"},
            {"type": "archive", "label": "Arquivar comprovante no DocWallet"},
        ]
        return [
            wf("travel_expense", "Prestação de contas de viagem", "Funcionário fotografa recibos; gestor aprova; financeiro reembolsa.", "RECEIPT", ["estabelecimento", "data", "valor", "moeda", "categoria"], ["projeto", "centro de custo", "motivo"], base_approval),
            wf("employee_reimbursement", "Reembolso de funcionário", "Transforma recibos e comprovantes em solicitação de reembolso aprovada.", "RECEIPT", ["fornecedor", "data", "valor", "categoria"], ["funcionário", "centro de custo", "motivo"], base_approval),
            wf("vendor_purchase", "Compra de fornecedor", "Lê proposta/nota/recibo, coleta dados e envia para aprovação de compra.", "INVOICE", ["fornecedor", "cnpj", "número do pedido", "valor", "data de entrega"], ["solicitante", "centro de custo"], base_approval),
            wf("accounts_payable", "Contas a pagar", "Extrai vencimento, fornecedor, valor e encaminha para financeiro.", "INVOICE", ["fornecedor", "cnpj", "valor", "vencimento", "número do documento"], ["categoria", "centro de custo"], base_approval),
            wf("invoice_check", "Conferência de notas", "Confere dados principais de notas e marca divergências para revisão.", "INVOICE", ["emitente", "destinatário", "cnpj", "valor", "data", "número da nota"], ["pedido interno"], base_approval),
            wf("construction_accountability", "Prestação de contas de obra", "Organiza comprovantes de obra por etapa, fornecedor e centro de custo.", "RECEIPT", ["fornecedor", "valor", "data", "categoria"], ["obra", "etapa", "responsável"], base_approval),
            wf("delivery_proof", "Comprovante de entrega", "Captura comprovante, identifica recebedor, data e pedido.", "OTHER", ["recebedor", "data", "número do pedido", "local"], ["motorista", "observações"], base_approval),
            wf("service_orders", "Ordens de serviço", "Converte OS em dados e acompanha execução/aprovação.", "OTHER", ["cliente", "serviço", "data", "valor", "responsável"], ["prioridade", "equipe"], base_approval),
            wf("contracts", "Contratos", "Extrai partes, prazos, valores, renovação, obrigações e envia para assinatura/prova.", "CONTRACT", ["contratante", "contratado", "objeto", "valor", "vigência", "renovação", "multa", "foro"], ["dono do contrato", "área responsável"], base_approval),
            wf("supplier_onboarding", "Onboarding documental de fornecedores", "Coleta documentos, valida dados e aprova cadastro de fornecedor.", "CORPORATE_DOCUMENT", ["fornecedor", "cnpj", "razão social", "documentos obrigatórios"], ["comprador responsável"], base_approval),
            wf("hr_documents", "Documentos de RH", "Organiza documentos admissionais, recibos e formulários de colaboradores.", "OTHER", ["colaborador", "cpf", "tipo de documento", "data"], ["departamento", "cargo"], base_approval),
            wf("field_inspection", "Inspeção de campo", "Transforma fotos e formulários em checklist revisável.", "OTHER", ["local", "data", "responsável", "não conformidade"], ["obra/projeto", "prioridade"], base_approval),
            wf("document_audit", "Auditoria documental", "Extrai dados, identifica pendências e gera trilha de auditoria.", "LEGAL_DOCUMENT", ["tipo", "partes", "data", "validade", "pendências"], ["auditor", "unidade"], base_approval),
        ]

    def pack_workflow(w: DocFlowWorkflow) -> Dict[str, Any]:
        return {
            "id": w.id,
            "tenantId": w.tenant_id,
            "name": w.name,
            "slug": w.slug,
            "description": w.description,
            "templateKey": w.template_key,
            "triggerType": w.trigger_type,
            "documentType": w.document_type,
            "schema": w.schema_json or {},
            "questions": w.questions_json or [],
            "steps": w.steps_json or [],
            "integrations": w.integrations_json or [],
            "status": w.status,
            "createdAt": iso(w.created_at),
            "updatedAt": iso(w.updated_at),
        }

    def pack_submission(s: DocFlowSubmission) -> Dict[str, Any]:
        approvals = DocFlowApproval.query.filter_by(user_id=s.user_id, submission_id=s.id).order_by(DocFlowApproval.created_at.asc()).all()
        return {
            "id": s.id,
            "tenantId": s.tenant_id,
            "workflowId": s.workflow_id,
            "documentId": s.document_id,
            "title": s.title,
            "sourceType": s.source_type,
            "status": s.status,
            "currentStep": s.current_step,
            "extractedData": s.extracted_data or {},
            "answers": s.answers_json or {},
            "validationErrors": s.validation_errors or [],
            "approvalStatus": s.approval_status,
            "hash": s.hash_value,
            "approvals": [pack_approval(a) for a in approvals],
            "completedAt": iso(s.completed_at),
            "createdAt": iso(s.created_at),
            "updatedAt": iso(s.updated_at),
        }

    def pack_approval(a: DocFlowApproval) -> Dict[str, Any]:
        return {"id": a.id, "submissionId": a.submission_id, "approverEmail": a.approver_email, "status": a.status, "decisionNote": a.decision_note, "decidedAt": iso(a.decided_at), "createdAt": iso(a.created_at)}

    def pack_integration(i: DocFlowIntegration) -> Dict[str, Any]:
        cfg = dict(i.config_json or {})
        if "secret" in cfg:
            cfg["secret"] = "***"
        if "token" in cfg:
            cfg["token"] = "***"
        return {"id": i.id, "workflowId": i.workflow_id, "type": i.integration_type, "name": i.name, "config": cfg, "enabled": i.enabled, "createdAt": iso(i.created_at)}

    def read_intelligence(document_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        row = db.session.execute(text("""
            SELECT id, document_type, title, summary, confidence, normalized_data, contract_data
            FROM document_intelligence
            WHERE document_id = :d AND user_id = :u AND is_current = true
            ORDER BY created_at DESC
            LIMIT 1
        """), {"d": document_id, "u": user_id}).mappings().first()
        if not row:
            return None
        normalized = row.get("normalized_data") or {}
        contract = row.get("contract_data") or {}
        return {"id": row.get("id"), "documentType": row.get("document_type"), "title": row.get("title"), "summary": row.get("summary"), "confidence": row.get("confidence"), "normalized": normalized, "contract": contract}

    def flatten_intelligence(intel: Dict[str, Any]) -> Dict[str, Any]:
        normalized = intel.get("normalized") or {}
        contract = intel.get("contract") or {}
        amounts = normalized.get("amounts") or []
        dates = normalized.get("dates") or []
        parties = normalized.get("parties") or []
        flat = {
            "document_type": intel.get("documentType") or normalized.get("documentType"),
            "title": normalized.get("title") or intel.get("title"),
            "issuer": normalized.get("issuer"),
            "summary": intel.get("summary") or normalized.get("summary"),
            "confidence": intel.get("confidence"),
            "parties": parties,
            "amounts": amounts,
            "dates": dates,
            "contract": contract,
            "contratante": next((p.get("name") for p in parties if (p.get("role") or "").lower() == "contratante"), None),
            "contratado": next((p.get("name") for p in parties if (p.get("role") or "").lower() in {"contratado", "contratada"}), None),
            "fornecedor": normalized.get("issuer") or next((p.get("name") for p in parties), None),
            "valor": contract.get("totalValue") or (amounts[0].get("value") if amounts else None),
            "valor_total": contract.get("totalValue"),
            "valor_recorrente": contract.get("recurringValue"),
            "data": (dates[0].get("value") if dates else None),
            "vencimento": normalized.get("expirationDate") or contract.get("expirationDate"),
            "renovacao": contract.get("renewalType"),
            "objeto": contract.get("object"),
            "foro": contract.get("jurisdiction"),
            "multa": (contract.get("penalties") or [{}])[0].get("value") if contract.get("penalties") else None,
        }
        return flat

    def map_schema(workflow: DocFlowWorkflow, intel: Optional[Dict[str, Any]], answers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        flat = flatten_intelligence(intel or {"normalized": {}, "contract": {}})
        answers = answers or {}
        schema = workflow.schema_json or {}
        fields = schema.get("fields") or []
        out = {"_source": "AV Document Intelligence", "_confidence": flat.get("confidence"), "_summary": flat.get("summary")}
        for f in fields:
            key = slugify(str(f)).replace("-", "_")
            candidates = [key, str(f).lower(), str(f).lower().replace(" ", "_"), str(f)]
            value = None
            for c in candidates:
                if c in answers:
                    value = answers[c]
                    break
                if c in flat:
                    value = flat[c]
                    break
            if value is None:
                low = str(f).lower()
                if "valor" in low:
                    value = flat.get("valor") or flat.get("valor_total")
                elif "data" in low or "venc" in low:
                    value = flat.get("vencimento") or flat.get("data")
                elif "fornecedor" in low or "estabelecimento" in low:
                    value = flat.get("fornecedor")
                elif "contratante" in low:
                    value = flat.get("contratante")
                elif "contratado" in low:
                    value = flat.get("contratado")
            out[key] = value
        for k, v in answers.items():
            out.setdefault(k, v)
        return out

    def validate_submission(workflow: DocFlowWorkflow, extracted: Dict[str, Any]) -> List[str]:
        errors = []
        required = (workflow.schema_json or {}).get("required") or []
        for field in required:
            key = slugify(str(field)).replace("-", "_")
            if not extracted.get(key):
                errors.append(f"Campo obrigatório ausente: {field}")
        return errors

    def workflow_requires_approval(workflow: DocFlowWorkflow) -> bool:
        return any((step.get("type") == "approve") for step in (workflow.steps_json or []))

    def execute_workflow(submission: DocFlowSubmission, workflow: DocFlowWorkflow):
        intel = read_intelligence(submission.document_id, request.user.id) if submission.document_id else None
        if submission.document_id and not intel:
            submission.status = "needs_intelligence"
            submission.current_step = "extract"
            submission.validation_errors = ["Documento ainda não possui DocWallet Intelligence. Analise o documento primeiro."]
            event("docflow.submission_needs_intelligence", submission.tenant_id, workflow.id, submission.id, {"document_id": submission.document_id})
            return submission
        extracted = map_schema(workflow, intel, submission.answers_json or {})
        submission.extracted_data = extracted
        errors = validate_submission(workflow, extracted)
        submission.validation_errors = errors
        if errors:
            submission.status = "needs_review"
            submission.current_step = "validate"
            event("docflow.submission_needs_review", submission.tenant_id, workflow.id, submission.id, {"missing_fields": len(errors)})
            return submission
        if workflow_requires_approval(workflow):
            submission.status = "awaiting_approval"
            submission.current_step = "approve"
            submission.approval_status = "pending"
            if not DocFlowApproval.query.filter_by(user_id=request.user.id, submission_id=submission.id).first():
                db.session.add(DocFlowApproval(user_id=request.user.id, tenant_id=submission.tenant_id, workflow_id=workflow.id, submission_id=submission.id, approver_email=request.user.email, approver_user_id=request.user.id, status="pending"))
            event("docflow.submission_awaiting_approval", submission.tenant_id, workflow.id, submission.id, {"workflow": workflow.name})
        else:
            submission.status = "completed"
            submission.current_step = "archive"
            submission.approval_status = "not_required"
            submission.completed_at = dt.datetime.utcnow()
            event("docflow.submission_completed", submission.tenant_id, workflow.id, submission.id, {"workflow": workflow.name})
        metric("docflow.submission_processed", submission.tenant_id, 1, "submission", submission.id, {"workflow_id": workflow.id})
        return submission

    with app.app_context():
        db.create_all()
        for sql in [
            "CREATE INDEX IF NOT EXISTS idx_docflow_workflow_user_status ON docflow_workflow(user_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_docflow_submission_user_status ON docflow_submission(user_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_docflow_approval_user_status ON docflow_approval(user_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_docflow_event_user_created ON docflow_event(user_id, created_at)",
        ]:
            try:
                db.session.execute(text(sql))
                db.session.commit()
            except Exception:
                db.session.rollback()

    @app.get("/api/docflow/health")
    def docflow_health():
        return jsonify({"success": True, "service": "DocFlow by DocWallet", "enabled": ENABLED, "engine": "AV Document Intelligence"})

    @app.get("/api/docflow/templates")
    @auth_required
    def docflow_templates():
        if not ENABLED:
            return fail("DocFlow não está habilitado.", 503)
        return jsonify({"success": True, "templates": template_catalog(), "blocks": WORKFLOW_BLOCKS, "capture": ["camera_mobile", "upload_web", "email_forwarding", "api", "batch_upload", "whatsapp_future"]})

    @app.get("/api/docflow/dashboard")
    @auth_required
    def docflow_dashboard():
        tenant = default_tenant()
        workflows = DocFlowWorkflow.query.filter_by(user_id=request.user.id, tenant_id=tenant.id).count()
        received = DocFlowSubmission.query.filter_by(user_id=request.user.id, tenant_id=tenant.id).count()
        processed = DocFlowSubmission.query.filter(DocFlowSubmission.user_id == request.user.id, DocFlowSubmission.tenant_id == tenant.id, DocFlowSubmission.status.in_(["completed", "awaiting_approval", "approved", "archived"])).count()
        pending = DocFlowSubmission.query.filter(DocFlowSubmission.user_id == request.user.id, DocFlowSubmission.tenant_id == tenant.id, DocFlowSubmission.status.in_(["received", "needs_intelligence", "needs_review"])).count()
        awaiting = DocFlowSubmission.query.filter_by(user_id=request.user.id, tenant_id=tenant.id, status="awaiting_approval").count()
        rejected = DocFlowSubmission.query.filter_by(user_id=request.user.id, tenant_id=tenant.id, status="rejected").count()
        completed = DocFlowSubmission.query.filter(DocFlowSubmission.user_id == request.user.id, DocFlowSubmission.tenant_id == tenant.id, DocFlowSubmission.status.in_(["completed", "approved", "archived"])).count()
        recent = DocFlowSubmission.query.filter_by(user_id=request.user.id, tenant_id=tenant.id).order_by(DocFlowSubmission.created_at.desc()).limit(10).all()
        recent_workflows = DocFlowWorkflow.query.filter_by(user_id=request.user.id, tenant_id=tenant.id).order_by(DocFlowWorkflow.created_at.desc()).limit(10).all()
        time_saved_minutes = processed * 8
        avg_minutes = 0 if processed == 0 else 2
        return jsonify({"success": True, "tenant": {"id": tenant.id, "name": tenant.name, "plan": tenant.plan}, "metrics": {"documentsReceived": received, "processed": processed, "pending": pending, "awaitingApproval": awaiting, "rejected": rejected, "completed": completed, "timeSavedMinutes": time_saved_minutes, "averageProcessingMinutes": avg_minutes, "users": DocFlowMember.query.filter_by(tenant_id=tenant.id, status="active").count(), "workflows": workflows}, "recentSubmissions": [pack_submission(s) for s in recent], "workflows": [pack_workflow(w) for w in recent_workflows]})

    @app.get("/api/docflow/workflows")
    @auth_required
    def list_workflows():
        tenant = default_tenant()
        rows = DocFlowWorkflow.query.filter_by(user_id=request.user.id, tenant_id=tenant.id).order_by(DocFlowWorkflow.created_at.desc()).all()
        return jsonify({"success": True, "workflows": [pack_workflow(w) for w in rows]})

    @app.post("/api/docflow/workflows")
    @auth_required
    def create_workflow():
        if not ENABLED:
            return fail("DocFlow não está habilitado.", 503)
        tenant = default_tenant()
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "Novo fluxo DocFlow").strip()
        fields = body.get("fields") or body.get("schema", {}).get("fields") or []
        schema = body.get("schema") or {"version": 1, "fields": fields, "required": body.get("required") or []}
        steps = body.get("steps") or [
            {"type": "extract", "label": "Extrair dados"},
            {"type": "validate", "label": "Validar campos"},
            {"type": "approve", "label": "Enviar para aprovação"},
            {"type": "archive", "label": "Arquivar"},
        ]
        invalid = [s.get("type") for s in steps if s.get("type") not in WORKFLOW_BLOCKS]
        if invalid:
            return fail(f"Bloco(s) inválido(s): {', '.join(invalid)}", 400)
        wf = DocFlowWorkflow(user_id=request.user.id, tenant_id=tenant.id, name=name, slug=f"{slugify(name)}-{uuid.uuid4().hex[:5]}", description=body.get("description"), template_key=body.get("templateKey"), trigger_type=body.get("triggerType") or "document_received", document_type=body.get("documentType"), schema_json=schema, questions_json=body.get("questions") or [], steps_json=steps, integrations_json=body.get("integrations") or [], status=body.get("status") or "active")
        db.session.add(wf)
        db.session.flush()
        event("docflow.workflow_created", tenant.id, wf.id, None, {"template_key": wf.template_key, "blocks": [s.get("type") for s in steps]})
        metric("workflows", tenant.id, 1, "workflow", wf.id)
        db.session.commit()
        return jsonify({"success": True, "workflow": pack_workflow(wf)})

    @app.post("/api/docflow/workflows/from-template")
    @auth_required
    def create_workflow_from_template():
        body = request.get_json(silent=True) or {}
        key = body.get("templateKey") or body.get("key")
        tpl = next((t for t in template_catalog() if t["key"] == key), None)
        if not tpl:
            return fail("Template não encontrado.", 404)
        tenant = default_tenant()
        wf = DocFlowWorkflow(user_id=request.user.id, tenant_id=tenant.id, name=body.get("name") or tpl["name"], slug=f"{slugify(body.get('name') or tpl['name'])}-{uuid.uuid4().hex[:5]}", description=tpl["description"], template_key=tpl["key"], trigger_type="document_received", document_type=tpl["documentType"], schema_json=tpl["schema"], questions_json=tpl["questions"], steps_json=tpl["steps"], integrations_json=[], status="active")
        db.session.add(wf)
        db.session.flush()
        event("docflow.workflow_created_from_template", tenant.id, wf.id, None, {"template_key": tpl["key"]})
        metric("workflows", tenant.id, 1, "workflow", wf.id, {"template_key": tpl["key"]})
        db.session.commit()
        return jsonify({"success": True, "workflow": pack_workflow(wf)})

    @app.get("/api/docflow/workflows/<workflow_id>")
    @auth_required
    def get_workflow(workflow_id):
        wf = owned_workflow(workflow_id)
        if not wf:
            return fail("Fluxo não encontrado.", 404)
        return jsonify({"success": True, "workflow": pack_workflow(wf)})

    @app.patch("/api/docflow/workflows/<workflow_id>")
    @auth_required
    def update_workflow(workflow_id):
        wf = owned_workflow(workflow_id)
        if not wf:
            return fail("Fluxo não encontrado.", 404)
        body = request.get_json(silent=True) or {}
        if "name" in body: wf.name = body.get("name") or wf.name
        if "description" in body: wf.description = body.get("description")
        if "documentType" in body: wf.document_type = body.get("documentType")
        if "schema" in body and isinstance(body.get("schema"), dict): wf.schema_json = body.get("schema")
        if "questions" in body and isinstance(body.get("questions"), list): wf.questions_json = body.get("questions")
        if "steps" in body and isinstance(body.get("steps"), list): wf.steps_json = body.get("steps")
        if "status" in body: wf.status = body.get("status") or wf.status
        event("docflow.workflow_updated", wf.tenant_id, wf.id, None, {"fields": sorted(body.keys())})
        db.session.commit()
        return jsonify({"success": True, "workflow": pack_workflow(wf)})

    @app.get("/api/docflow/submissions")
    @auth_required
    def list_submissions():
        tenant = default_tenant()
        status = request.args.get("status")
        q = DocFlowSubmission.query.filter_by(user_id=request.user.id, tenant_id=tenant.id)
        if status:
            q = q.filter_by(status=status)
        rows = q.order_by(DocFlowSubmission.created_at.desc()).limit(100).all()
        return jsonify({"success": True, "submissions": [pack_submission(s) for s in rows]})

    @app.post("/api/docflow/submissions")
    @auth_required
    def create_submission():
        tenant = default_tenant()
        body = request.get_json(silent=True) or {}
        workflow = owned_workflow(body.get("workflowId") or body.get("workflow_id") or "")
        if not workflow:
            return fail("Fluxo não encontrado.", 404)
        document_id = body.get("documentId") or body.get("document_id")
        doc = None
        if document_id:
            doc = Document.query.filter_by(id=document_id, user_id=request.user.id).first()
            if not doc:
                return fail("Documento não encontrado para este usuário.", 404)
        title = body.get("title") or (doc.name if doc else workflow.name)
        submission = DocFlowSubmission(user_id=request.user.id, tenant_id=tenant.id, workflow_id=workflow.id, document_id=document_id, title=title, source_type=body.get("sourceType") or "upload_web", status="received", current_step="received", extracted_data=body.get("extractedData") or {}, answers_json=body.get("answers") or {}, approval_status="not_required", hash_value=(doc.file_hash if doc else body.get("hash")))
        db.session.add(submission)
        db.session.flush()
        event("docflow.submission_created", tenant.id, workflow.id, submission.id, {"source_type": submission.source_type, "has_document": bool(document_id)})
        metric("docs", tenant.id, 1, "submission", submission.id)
        db.session.commit()
        return jsonify({"success": True, "submission": pack_submission(submission)})

    @app.get("/api/docflow/submissions/<submission_id>")
    @auth_required
    def get_submission(submission_id):
        s = owned_submission(submission_id)
        if not s:
            return fail("Processo não encontrado.", 404)
        return jsonify({"success": True, "submission": pack_submission(s)})

    @app.post("/api/docflow/submissions/<submission_id>/run")
    @auth_required
    def run_submission(submission_id):
        s = owned_submission(submission_id)
        if not s:
            return fail("Processo não encontrado.", 404)
        wf = owned_workflow(s.workflow_id)
        if not wf:
            return fail("Fluxo não encontrado.", 404)
        body = request.get_json(silent=True) or {}
        if isinstance(body.get("answers"), dict):
            merged = s.answers_json or {}
            merged.update(body.get("answers") or {})
            s.answers_json = merged
        execute_workflow(s, wf)
        db.session.commit()
        return jsonify({"success": True, "submission": pack_submission(s)})

    @app.post("/api/docflow/submissions/<submission_id>/approve")
    @auth_required
    def approve_submission(submission_id):
        s = owned_submission(submission_id)
        if not s:
            return fail("Processo não encontrado.", 404)
        body = request.get_json(silent=True) or {}
        approvals = DocFlowApproval.query.filter_by(user_id=request.user.id, submission_id=s.id, status="pending").all()
        for a in approvals:
            a.status = "approved"
            a.decision_note = body.get("note") or "Aprovado"
            a.decided_at = dt.datetime.utcnow()
        s.status = "approved"
        s.approval_status = "approved"
        s.current_step = "archive"
        s.completed_at = dt.datetime.utcnow()
        event("docflow.submission_approved", s.tenant_id, s.workflow_id, s.id, {"approvals": len(approvals)})
        metric("approvals", s.tenant_id, 1, "submission", s.id)
        db.session.commit()
        return jsonify({"success": True, "submission": pack_submission(s)})

    @app.post("/api/docflow/submissions/<submission_id>/reject")
    @auth_required
    def reject_submission(submission_id):
        s = owned_submission(submission_id)
        if not s:
            return fail("Processo não encontrado.", 404)
        body = request.get_json(silent=True) or {}
        approvals = DocFlowApproval.query.filter_by(user_id=request.user.id, submission_id=s.id, status="pending").all()
        for a in approvals:
            a.status = "rejected"
            a.decision_note = body.get("note") or "Rejeitado"
            a.decided_at = dt.datetime.utcnow()
        s.status = "rejected"
        s.approval_status = "rejected"
        s.current_step = "reject"
        event("docflow.submission_rejected", s.tenant_id, s.workflow_id, s.id, {"approvals": len(approvals)})
        db.session.commit()
        return jsonify({"success": True, "submission": pack_submission(s)})

    @app.get("/api/docflow/integrations")
    @auth_required
    def list_integrations():
        tenant = default_tenant()
        rows = DocFlowIntegration.query.filter_by(user_id=request.user.id, tenant_id=tenant.id).order_by(DocFlowIntegration.created_at.desc()).all()
        return jsonify({"success": True, "integrations": [pack_integration(i) for i in rows], "externalExecutionEnabled": EXTERNAL_INTEGRATIONS_ENABLED, "available": ["google_sheets", "excel_csv", "webhook", "email", "slack", "whatsapp_future", "erp_api", "accounting", "storage"]})

    @app.post("/api/docflow/integrations")
    @auth_required
    def create_integration():
        tenant = default_tenant()
        body = request.get_json(silent=True) or {}
        integration_type = body.get("type") or body.get("integrationType") or "webhook"
        workflow_id = body.get("workflowId")
        if workflow_id and not owned_workflow(workflow_id):
            return fail("Fluxo não encontrado para integração.", 404)
        cfg = body.get("config") or {}
        item = DocFlowIntegration(user_id=request.user.id, tenant_id=tenant.id, workflow_id=workflow_id, integration_type=integration_type, name=body.get("name") or integration_type, config_json=cfg, enabled=bool(body.get("enabled", False)))
        db.session.add(item)
        db.session.flush()
        event("docflow.integration_created", tenant.id, workflow_id, None, {"integration_type": integration_type, "enabled": item.enabled})
        db.session.commit()
        return jsonify({"success": True, "integration": pack_integration(item)})

    @app.get("/api/docflow/audit")
    @auth_required
    def docflow_audit():
        tenant = default_tenant()
        rows = DocFlowEvent.query.filter_by(user_id=request.user.id, tenant_id=tenant.id).order_by(DocFlowEvent.created_at.desc()).limit(100).all()
        return jsonify({"success": True, "events": [{"id": e.id, "action": e.action, "workflowId": e.workflow_id, "submissionId": e.submission_id, "metadata": e.metadata_json or {}, "createdAt": iso(e.created_at)} for e in rows]})

    @app.get("/api/docflow/metrics")
    @auth_required
    def docflow_metrics():
        tenant = default_tenant()
        rows = db.session.execute(text("""
            SELECT metric_name, COALESCE(SUM(quantity), 0) as total
            FROM docflow_metric
            WHERE user_id = :u AND tenant_id = :t
            GROUP BY metric_name
            ORDER BY metric_name ASC
        """), {"u": request.user.id, "t": tenant.id}).mappings().all()
        return jsonify({"success": True, "metrics": [{"name": r["metric_name"], "total": int(r["total"] or 0)} for r in rows]})

    print("DocFlow by DocWallet installed.")
