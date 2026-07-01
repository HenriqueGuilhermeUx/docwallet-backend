def install_sign(app, db, auth_required, fail, log):
    import datetime as dt
    import hashlib
    import json
    import secrets
    import uuid
    from flask import request, jsonify

    class SignatureRequest(db.Model):
        __tablename__ = 'signature_requests'
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        title = db.Column(db.String(240), nullable=False)
        contract_content = db.Column(db.Text, nullable=False)
        content_hash = db.Column(db.String(128), nullable=False, index=True)
        final_hash = db.Column(db.String(128), nullable=True, index=True)
        status = db.Column(db.String(40), default='pending', nullable=False)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
        completed_at = db.Column(db.DateTime, nullable=True)

    class SignatureParty(db.Model):
        __tablename__ = 'signature_parties'
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        request_id = db.Column(db.String(36), nullable=False, index=True)
        code = db.Column(db.String(80), unique=True, nullable=False, index=True)
        name = db.Column(db.String(180), nullable=False)
        email = db.Column(db.String(180), nullable=True)
        status = db.Column(db.String(40), default='pending', nullable=False)
        signed_name = db.Column(db.String(180), nullable=True)
        signed_email = db.Column(db.String(180), nullable=True)
        signed_at = db.Column(db.DateTime, nullable=True)
        ip_address = db.Column(db.String(80), nullable=True)
        user_agent = db.Column(db.Text, nullable=True)

    class SignatureEvent(db.Model):
        __tablename__ = 'signature_events'
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        request_id = db.Column(db.String(36), nullable=False, index=True)
        party_id = db.Column(db.String(36), nullable=True, index=True)
        event_type = db.Column(db.String(80), nullable=False)
        payload = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)

    with app.app_context():
        db.create_all()

    def hash_text(value):
        return hashlib.sha256(value.encode('utf-8')).hexdigest()

    def pack_party(p, public=False):
        out = {'id': p.id, 'name': p.name, 'email': p.email, 'status': p.status, 'signed_at': p.signed_at.isoformat() + 'Z' if p.signed_at else None}
        if not public:
            out['code'] = p.code
            out['url'] = '/sign/' + p.code
        return out

    def pack_next_party(p):
        if not p:
            return None
        return {'name': p.name, 'email': p.email, 'url': '/sign/' + p.code, 'code': p.code}

    def pack_request(req, parties=None):
        return {
            'id': req.id,
            'title': req.title,
            'content_hash': req.content_hash,
            'final_hash': req.final_hash,
            'status': req.status,
            'created_at': req.created_at.isoformat() + 'Z',
            'completed_at': req.completed_at.isoformat() + 'Z' if req.completed_at else None,
            'parties': [pack_party(p) for p in (parties or [])],
        }

    def build_final_hash(req):
        parties = SignatureParty.query.filter_by(request_id=req.id).order_by(SignatureParty.id).all()
        evidence = []
        for p in parties:
            evidence.append({'name': p.signed_name or p.name, 'email': p.signed_email or p.email, 'signed_at': p.signed_at.isoformat() if p.signed_at else None, 'ip': p.ip_address})
        return hash_text(req.contract_content + '\n\nDOCWALLET_SIGNATURES\n' + json.dumps(evidence, sort_keys=True))

    @app.post('/api/signatures/request')
    @auth_required
    def create_signature_request():
        body = request.get_json(silent=True) or {}
        title = (body.get('title') or 'Contrato DocWallet').strip()
        content = body.get('contract_content') or body.get('content') or ''
        parties = body.get('parties') or []
        if not content.strip():
            return fail('Conteúdo do contrato é obrigatório.', 400)
        if len(parties) < 1:
            return fail('Informe pelo menos uma parte para assinatura.', 400)
        req = SignatureRequest(user_id=request.user.id, title=title, contract_content=content, content_hash=hash_text(content))
        db.session.add(req)
        db.session.flush()
        created = []
        for item in parties:
            name = (item.get('name') or '').strip()
            email = (item.get('email') or '').strip().lower()
            if not name:
                continue
            p = SignatureParty(request_id=req.id, code=secrets.token_hex(16), name=name, email=email)
            db.session.add(p)
            created.append(p)
        if not created:
            db.session.rollback()
            return fail('Nenhuma parte válida informada.', 400)
        ev = SignatureEvent(request_id=req.id, event_type='request.created', payload={'count': len(created)})
        db.session.add(ev)
        db.session.commit()
        log('signature.request.create', request.user.id, 'signature', req.id, {'parties': len(created)})
        return jsonify({'success': True, 'request': pack_request(req, created)}), 201

    @app.get('/api/signatures/<request_id>')
    @auth_required
    def read_signature_request(request_id):
        req = SignatureRequest.query.filter_by(id=request_id, user_id=request.user.id).first()
        if not req:
            return fail('Solicitação não encontrada.', 404)
        parties = SignatureParty.query.filter_by(request_id=req.id).all()
        return jsonify({'success': True, 'request': pack_request(req, parties), 'contract_content': req.contract_content})

    @app.get('/api/sign/<code>')
    def public_sign_page(code):
        party = SignatureParty.query.filter_by(code=code).first()
        if not party:
            return fail('Link de assinatura não encontrado.', 404)
        req = SignatureRequest.query.filter_by(id=party.request_id).first()
        if not req:
            return fail('Contrato não encontrado.', 404)
        parties = SignatureParty.query.filter_by(request_id=req.id).all()
        return jsonify({'success': True, 'request': pack_request(req, parties), 'party': pack_party(party, public=True), 'contract_content': req.contract_content})

    @app.post('/api/sign/<code>/accept')
    def accept_signature(code):
        body = request.get_json(silent=True) or {}
        party = SignatureParty.query.filter_by(code=code).first()
        if not party:
            return fail('Link de assinatura não encontrado.', 404)
        if party.status == 'signed':
            return fail('Esta parte já assinou.', 400)
        req = SignatureRequest.query.filter_by(id=party.request_id).first()
        if not req:
            return fail('Contrato não encontrado.', 404)
        signed_name = (body.get('signed_name') or body.get('name') or '').strip()
        signed_email = (body.get('signed_email') or body.get('email') or party.email or '').strip().lower()
        accepted = bool(body.get('accepted'))
        if not signed_name or not accepted:
            return fail('Informe o nome completo e aceite os termos.', 400)
        party.status = 'signed'
        party.signed_name = signed_name
        party.signed_email = signed_email
        party.signed_at = dt.datetime.utcnow()
        party.ip_address = request.headers.get('X-Forwarded-For', request.remote_addr or '')[:80]
        party.user_agent = request.headers.get('User-Agent', '')[:1000]
        db.session.add(SignatureEvent(request_id=req.id, party_id=party.id, event_type='party.signed', payload={'name': signed_name, 'email': signed_email, 'ip': party.ip_address}))
        all_parties = SignatureParty.query.filter_by(request_id=req.id).all()
        if all(p.status == 'signed' or p.id == party.id for p in all_parties):
            req.status = 'completed'
            req.completed_at = dt.datetime.utcnow()
            db.session.flush()
            req.final_hash = build_final_hash(req)
            db.session.add(SignatureEvent(request_id=req.id, event_type='request.completed', payload={'final_hash': req.final_hash}))
        db.session.commit()
        parties = SignatureParty.query.filter_by(request_id=req.id).all()
        next_party = SignatureParty.query.filter(SignatureParty.request_id == req.id, SignatureParty.status != 'signed').order_by(SignatureParty.id).first()
        return jsonify({'success': True, 'request': pack_request(req, parties), 'party': pack_party(party, public=True), 'next_party': pack_next_party(next_party)})
