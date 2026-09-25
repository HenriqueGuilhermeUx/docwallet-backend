def install_docflow_sign_bridge(app, db, auth_required, fail, log):
    import datetime as dt
    import hashlib
    import json
    import secrets
    import uuid
    from flask import jsonify, request
    from sqlalchemy import text

    SIGNABLE_STATUSES = {'approved', 'completed', 'archived'}

    def _now():
        return dt.datetime.utcnow()

    def _public_url():
        import os
        return (os.environ.get('DOCWALLET_PUBLIC_URL') or 'https://trydocwallet.com').rstrip('/')

    def _json(value):
        if value is None:
            return {}
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except Exception:
            return {}

    def _submission(submission_id):
        return db.session.execute(text('''
            SELECT s.id, s.user_id, s.tenant_id, s.workflow_id, s.document_id, s.title,
                   s.status, s.current_step, s.extracted_data, s.answers_json,
                   s.validation_errors, s.approval_status, s.hash_value,
                   w.name AS workflow_name
              FROM docflow_submission s
              JOIN docflow_workflow w ON w.id = s.workflow_id
             WHERE s.id = :submission_id AND s.user_id = :user_id
             LIMIT 1
        '''), {
            'submission_id': submission_id,
            'user_id': request.user.id,
        }).mappings().first()

    def _document(document_id):
        if not document_id:
            return None
        return db.session.execute(text('''
            SELECT id, name, file_hash, doc_type, category, created_at
              FROM documents
             WHERE id = :document_id AND user_id = :user_id
             LIMIT 1
        '''), {'document_id': document_id, 'user_id': request.user.id}).mappings().first()

    def _link(submission_id):
        return db.session.execute(text('''
            SELECT l.id, l.signature_request_id, l.status, l.created_at, l.updated_at,
                   sr.title, sr.status AS request_status, sr.content_hash, sr.final_hash,
                   sr.completed_at
              FROM docflow_signature_link l
              JOIN signature_requests sr ON sr.id = l.signature_request_id
             WHERE l.submission_id = :submission_id AND l.user_id = :user_id
             ORDER BY l.created_at DESC
             LIMIT 1
        '''), {'submission_id': submission_id, 'user_id': request.user.id}).mappings().first()

    def _parties(signature_request_id):
        rows = db.session.execute(text('''
            SELECT id, name, email, phone, status, code, signed_at, evidence_level
              FROM signature_parties
             WHERE request_id = :request_id
             ORDER BY id
        '''), {'request_id': signature_request_id}).mappings().all()
        return [dict(row) for row in rows]

    def _add_docflow_event(submission, action, metadata=None):
        db.session.execute(text('''
            INSERT INTO docflow_event
                (id, user_id, tenant_id, workflow_id, submission_id, action, actor_user_id, metadata_json, created_at)
            VALUES
                (:id, :user_id, :tenant_id, :workflow_id, :submission_id, :action, :actor_user_id, CAST(:metadata AS jsonb), NOW())
        '''), {
            'id': str(uuid.uuid4()),
            'user_id': request.user.id,
            'tenant_id': submission['tenant_id'],
            'workflow_id': submission['workflow_id'],
            'submission_id': submission['id'],
            'action': action,
            'actor_user_id': request.user.id,
            'metadata': json.dumps(metadata or {}, ensure_ascii=False),
        })

    def _build_content(submission, document=None):
        extracted = _json(submission['extracted_data'])
        answers = _json(submission['answers_json'])
        lines = [
            'DOCFLOW BY DOCWALLET — TERMO DE ACEITE E ASSINATURA',
            '',
            f"Processo: {submission['title']}",
            f"Fluxo: {submission['workflow_name']}",
            f"Identificador do processo: {submission['id']}",
            f"Status anterior: {submission['status']}",
            f"Aprovação: {submission['approval_status']}",
        ]
        if document:
            lines.extend([
                '',
                'DOCUMENTO VINCULADO',
                f"Nome: {document['name']}",
                f"Tipo: {document['doc_type']}",
                f"Hash SHA-256: {document['file_hash']}",
                f"ID: {document['id']}",
            ])
        lines.extend([
            '',
            'DADOS ESTRUTURADOS DO PROCESSO',
            json.dumps(extracted, ensure_ascii=False, indent=2, sort_keys=True),
            '',
            'INFORMAÇÕES COMPLEMENTARES',
            json.dumps(answers, ensure_ascii=False, indent=2, sort_keys=True),
            '',
            'DECLARAÇÃO',
            'Ao assinar, a parte declara que revisou o conteúdo apresentado e manifesta seu aceite no processo DocFlow. '
            'A assinatura poderá ser eletrônica com evidências DocWallet ou qualificada com certificado digital ICP-Brasil, conforme a opção escolhida pelo signatário.',
        ])
        return '\n'.join(lines)

    def _pack(link):
        parties = _parties(link['signature_request_id'])
        return {
            'submissionId': None,
            'signatureRequestId': link['signature_request_id'],
            'status': link['request_status'],
            'title': link['title'],
            'contentHash': link['content_hash'],
            'finalHash': link['final_hash'],
            'completedAt': link['completed_at'].isoformat() + 'Z' if link['completed_at'] else None,
            'parties': [
                {
                    'id': p['id'],
                    'name': p['name'],
                    'email': p['email'],
                    'phone': p['phone'],
                    'status': p['status'],
                    'evidenceLevel': p['evidence_level'],
                    'signedAt': p['signed_at'].isoformat() + 'Z' if p['signed_at'] else None,
                    'signUrl': f"{_public_url()}/sign/{p['code']}",
                }
                for p in parties
            ],
        }

    with app.app_context():
        statements = [
            '''CREATE TABLE IF NOT EXISTS docflow_signature_link (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL,
                submission_id VARCHAR(36) NOT NULL,
                signature_request_id VARCHAR(36) NOT NULL,
                status VARCHAR(40) NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )''',
            'CREATE INDEX IF NOT EXISTS idx_docflow_signature_submission ON docflow_signature_link(user_id, submission_id)',
            'CREATE UNIQUE INDEX IF NOT EXISTS ux_docflow_signature_submission ON docflow_signature_link(user_id, submission_id)',
            'CREATE INDEX IF NOT EXISTS idx_docflow_signature_request ON docflow_signature_link(signature_request_id)',
        ]
        for statement in statements:
            try:
                db.session.execute(text(statement))
                db.session.commit()
            except Exception:
                db.session.rollback()

    @app.get('/api/docflow/submissions/<submission_id>/signature')
    @auth_required
    def get_docflow_signature(submission_id):
        submission = _submission(submission_id)
        if not submission:
            return fail('Processo DocFlow não encontrado.', 404)
        link = _link(submission_id)
        if not link:
            return jsonify({'success': True, 'signature': None})

        request_status = link['request_status']
        if request_status == 'completed' and submission['status'] != 'completed':
            now = _now()
            db.session.execute(text('''
                UPDATE docflow_submission
                   SET status = 'completed', current_step = 'archive', completed_at = :completed_at, updated_at = :completed_at
                 WHERE id = :submission_id AND user_id = :user_id
            '''), {'completed_at': now, 'submission_id': submission_id, 'user_id': request.user.id})
            db.session.execute(text("UPDATE docflow_signature_link SET status = 'completed', updated_at = NOW() WHERE id = :id"), {'id': link['id']})
            _add_docflow_event(submission, 'docflow.signature_completed', {'signature_request_id': link['signature_request_id']})
            db.session.commit()
            try:
                log('docflow.signature_completed', request.user.id, 'docflow', submission_id, {'signature_request_id': link['signature_request_id']})
            except Exception:
                pass

        packed = _pack(link)
        packed['submissionId'] = submission_id
        return jsonify({'success': True, 'signature': packed})

    @app.post('/api/docflow/submissions/<submission_id>/signature')
    @auth_required
    def create_docflow_signature(submission_id):
        submission = _submission(submission_id)
        if not submission:
            return fail('Processo DocFlow não encontrado.', 404)

        existing = _link(submission_id)
        if existing:
            packed = _pack(existing)
            packed['submissionId'] = submission_id
            return jsonify({'success': True, 'signature': packed, 'existing': True})

        if submission['status'] not in SIGNABLE_STATUSES:
            return fail('O processo precisa estar aprovado ou concluído antes de ser enviado para assinatura.', 409)

        body = request.get_json(silent=True) or {}
        parties = body.get('parties') or []
        clean_parties = []
        seen = set()
        for raw in parties:
            name = str(raw.get('name') or '').strip()
            email = str(raw.get('email') or '').strip().lower()
            phone = str(raw.get('phone') or '').strip()
            key = (name.lower(), email, phone)
            if not name or key in seen:
                continue
            seen.add(key)
            clean_parties.append({'name': name[:180], 'email': email[:180], 'phone': phone[:80]})
        if not clean_parties:
            clean_parties = [{'name': request.user.name, 'email': request.user.email, 'phone': ''}]

        document = _document(submission['document_id'])
        content = _build_content(submission, document)
        signature_request_id = str(uuid.uuid4())
        content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
        title = str(body.get('title') or f"{submission['title']} — assinatura").strip()[:240]
        now = _now()

        db.session.execute(text('''
            INSERT INTO signature_requests
                (id, user_id, title, contract_content, content_hash, status, created_at)
            VALUES
                (:id, :user_id, :title, :content, :content_hash, 'pending', :created_at)
        '''), {
            'id': signature_request_id,
            'user_id': request.user.id,
            'title': title,
            'content': content,
            'content_hash': content_hash,
            'created_at': now,
        })

        for party in clean_parties:
            party_id = str(uuid.uuid4())
            code = secrets.token_hex(20)
            db.session.execute(text('''
                INSERT INTO signature_parties
                    (id, request_id, code, name, email, phone, status)
                VALUES
                    (:id, :request_id, :code, :name, :email, :phone, 'pending')
            '''), {
                'id': party_id,
                'request_id': signature_request_id,
                'code': code,
                'name': party['name'],
                'email': party['email'] or None,
                'phone': party['phone'] or None,
            })

        db.session.execute(text('''
            INSERT INTO signature_events (id, request_id, event_type, payload, created_at)
            VALUES (:id, :request_id, 'request.created', CAST(:payload AS jsonb), NOW())
        '''), {
            'id': str(uuid.uuid4()),
            'request_id': signature_request_id,
            'payload': json.dumps({'count': len(clean_parties), 'title': title, 'source': 'docflow', 'submission_id': submission_id}, ensure_ascii=False),
        })
        link_id = str(uuid.uuid4())
        db.session.execute(text('''
            INSERT INTO docflow_signature_link
                (id, user_id, submission_id, signature_request_id, status, created_at, updated_at)
            VALUES (:id, :user_id, :submission_id, :signature_request_id, 'pending', :created_at, :created_at)
        '''), {
            'id': link_id,
            'user_id': request.user.id,
            'submission_id': submission_id,
            'signature_request_id': signature_request_id,
            'created_at': now,
        })
        db.session.execute(text('''
            UPDATE docflow_submission
               SET status = 'awaiting_signature', current_step = 'sign', completed_at = NULL, updated_at = :updated_at
             WHERE id = :submission_id AND user_id = :user_id
        '''), {'updated_at': now, 'submission_id': submission_id, 'user_id': request.user.id})
        _add_docflow_event(submission, 'docflow.signature_requested', {
            'signature_request_id': signature_request_id,
            'parties': len(clean_parties),
            'content_hash': content_hash,
            'previous_status': submission['status'],
        })
        db.session.commit()
        try:
            log('docflow.signature_requested', request.user.id, 'docflow', submission_id, {'signature_request_id': signature_request_id, 'parties': len(clean_parties), 'previous_status': submission['status']})
        except Exception:
            pass

        link = _link(submission_id)
        packed = _pack(link)
        packed['submissionId'] = submission_id
        return jsonify({'success': True, 'signature': packed, 'existing': False}), 201
