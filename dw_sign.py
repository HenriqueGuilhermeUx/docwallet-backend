def install_sign(app, db, auth_required, fail, log):
    import datetime as dt
    import hashlib
    import json
    import secrets
    import uuid
    from flask import request, jsonify
    from sqlalchemy import text

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
        evidence_level = db.Column(db.String(60), nullable=True)
        signed_cpf = db.Column(db.String(40), nullable=True)
        signed_phone = db.Column(db.String(80), nullable=True)
        confirmation_phrase = db.Column(db.String(120), nullable=True)
        signature_image = db.Column(db.Text, nullable=True)
        geo_latitude = db.Column(db.String(80), nullable=True)
        geo_longitude = db.Column(db.String(80), nullable=True)
        geo_accuracy = db.Column(db.String(80), nullable=True)
        device_fingerprint = db.Column(db.JSON, nullable=True)
        consent_text = db.Column(db.Text, nullable=True)

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
        for sql in [
            "ALTER TABLE signature_parties ADD COLUMN IF NOT EXISTS evidence_level VARCHAR(60)",
            "ALTER TABLE signature_parties ADD COLUMN IF NOT EXISTS signed_cpf VARCHAR(40)",
            "ALTER TABLE signature_parties ADD COLUMN IF NOT EXISTS signed_phone VARCHAR(80)",
            "ALTER TABLE signature_parties ADD COLUMN IF NOT EXISTS confirmation_phrase VARCHAR(120)",
            "ALTER TABLE signature_parties ADD COLUMN IF NOT EXISTS signature_image TEXT",
            "ALTER TABLE signature_parties ADD COLUMN IF NOT EXISTS geo_latitude VARCHAR(80)",
            "ALTER TABLE signature_parties ADD COLUMN IF NOT EXISTS geo_longitude VARCHAR(80)",
            "ALTER TABLE signature_parties ADD COLUMN IF NOT EXISTS geo_accuracy VARCHAR(80)",
            "ALTER TABLE signature_parties ADD COLUMN IF NOT EXISTS device_fingerprint JSONB",
            "ALTER TABLE signature_parties ADD COLUMN IF NOT EXISTS consent_text TEXT",
            "CREATE INDEX IF NOT EXISTS idx_signature_parties_evidence_level ON signature_parties(evidence_level)",
        ]:
            try:
                db.session.execute(text(sql))
                db.session.commit()
            except Exception:
                db.session.rollback()

    def utc_now():
        return dt.datetime.utcnow()

    def iso(value):
        return value.isoformat() + 'Z' if value else None

    def hash_text(value):
        return hashlib.sha256(value.encode('utf-8')).hexdigest()

    def clean_text(value, limit=1000):
        if value is None:
            return ''
        return str(value).strip()[:limit]

    def evidence_payload_for_party(p):
        return {
            'evidence_level': p.evidence_level or 'basic_evidence',
            'signed_cpf': p.signed_cpf,
            'signed_phone': p.signed_phone,
            'confirmation_phrase': p.confirmation_phrase,
            'has_drawn_signature': bool(p.signature_image),
            'signature_image_sha256': hash_text(p.signature_image) if p.signature_image else None,
            'geo_latitude': p.geo_latitude,
            'geo_longitude': p.geo_longitude,
            'geo_accuracy': p.geo_accuracy,
            'device_fingerprint': p.device_fingerprint or {},
            'consent_text': p.consent_text,
        }

    def progress_for(parties):
        total = len(parties or [])
        signed = len([p for p in (parties or []) if p.status == 'signed'])
        pending = max(total - signed, 0)
        percent = int(round((signed / total) * 100)) if total else 0
        return {'total': total, 'signed': signed, 'pending': pending, 'percent': percent}

    def pack_party(p, public=False):
        out = {
            'id': p.id,
            'name': p.name,
            'email': p.email,
            'status': p.status,
            'signed_at': iso(p.signed_at),
        }
        if not public:
            out.update({
                'code': p.code,
                'url': '/sign/' + p.code,
                'signed_name': p.signed_name,
                'signed_email': p.signed_email,
                'ip_address': p.ip_address,
                'user_agent': p.user_agent,
                'evidence_level': p.evidence_level or 'basic_evidence',
                'signed_cpf': p.signed_cpf,
                'signed_phone': p.signed_phone,
                'confirmation_phrase': p.confirmation_phrase,
                'has_drawn_signature': bool(p.signature_image),
                'geo_latitude': p.geo_latitude,
                'geo_longitude': p.geo_longitude,
                'geo_accuracy': p.geo_accuracy,
                'device_fingerprint': p.device_fingerprint or {},
            })
        return out

    def pack_next_party(p):
        if not p:
            return None
        return {'name': p.name, 'email': p.email, 'url': '/sign/' + p.code, 'code': p.code}

    def pack_request(req, parties=None):
        parties = parties if parties is not None else SignatureParty.query.filter_by(request_id=req.id).order_by(SignatureParty.id).all()
        progress = progress_for(parties)
        return {
            'id': req.id,
            'title': req.title,
            'content_hash': req.content_hash,
            'final_hash': req.final_hash,
            'status': req.status,
            'created_at': iso(req.created_at),
            'completed_at': iso(req.completed_at),
            'total_parties': progress['total'],
            'signed_count': progress['signed'],
            'pending_count': progress['pending'],
            'progress_percent': progress['percent'],
            'parties': [pack_party(p) for p in parties],
        }

    def build_evidence(req, parties=None, events=None):
        parties = parties if parties is not None else SignatureParty.query.filter_by(request_id=req.id).order_by(SignatureParty.id).all()
        events = events if events is not None else SignatureEvent.query.filter_by(request_id=req.id).order_by(SignatureEvent.created_at).all()
        return {
            'provider': 'DocWallet Docs',
            'evidence_version': '1.1',
            'generated_at': iso(utc_now()),
            'signature_request': {
                'id': req.id,
                'title': req.title,
                'status': req.status,
                'created_at': iso(req.created_at),
                'completed_at': iso(req.completed_at),
                'content_hash_sha256': req.content_hash,
                'final_hash_sha256': req.final_hash,
                'progress': progress_for(parties),
                'legal_note': 'Assinatura eletrônica com evidências digitais DocWallet. Não é assinatura qualificada ICP-Brasil, salvo quando assinada por provider ICP-Brasil habilitado.',
            },
            'parties': [
                {
                    'id': p.id,
                    'name': p.name,
                    'email': p.email,
                    'status': p.status,
                    'signed_name': p.signed_name,
                    'signed_email': p.signed_email,
                    'signed_at': iso(p.signed_at),
                    'ip_address': p.ip_address,
                    'user_agent': p.user_agent,
                    'evidence': evidence_payload_for_party(p),
                    'signature_image_data_url': p.signature_image,
                }
                for p in parties
            ],
            'events': [
                {
                    'id': ev.id,
                    'party_id': ev.party_id,
                    'event_type': ev.event_type,
                    'payload': ev.payload,
                    'created_at': iso(ev.created_at),
                }
                for ev in events
            ],
        }

    def build_final_hash(req):
        parties = SignatureParty.query.filter_by(request_id=req.id).order_by(SignatureParty.id).all()
        evidence = []
        for p in parties:
            evidence.append({
                'party_id': p.id,
                'name': p.signed_name or p.name,
                'email': p.signed_email or p.email,
                'signed_at': p.signed_at.isoformat() if p.signed_at else None,
                'ip': p.ip_address,
                'user_agent': p.user_agent,
                'evidence': evidence_payload_for_party(p),
            })
        return hash_text(req.contract_content + '\n\nDOCWALLET_SIGNATURES\n' + json.dumps(evidence, sort_keys=True, ensure_ascii=False))

    def owned_request(request_id):
        req = SignatureRequest.query.filter_by(id=request_id, user_id=request.user.id).first()
        return req

    @app.get('/api/signatures')
    @auth_required
    def list_signature_requests():
        requests = SignatureRequest.query.filter_by(user_id=request.user.id).order_by(SignatureRequest.created_at.desc()).limit(100).all()
        packed = []
        for req in requests:
            parties = SignatureParty.query.filter_by(request_id=req.id).order_by(SignatureParty.id).all()
            packed.append(pack_request(req, parties))
        return jsonify({'success': True, 'requests': packed})

    @app.post('/api/signatures/request')
    @auth_required
    def create_signature_request():
        body = request.get_json(silent=True) or {}
        title = (body.get('title') or 'Contrato DocWallet Docs').strip()
        content = body.get('contract_content') or body.get('content') or ''
        parties = body.get('parties') or []
        if not content.strip():
            return fail('Conteúdo do contrato é obrigatório.', 400)
        if len(parties) < 1:
            return fail('Informe pelo menos uma parte para assinatura.', 400)
        req = SignatureRequest(user_id=request.user.id, title=title[:240], contract_content=content, content_hash=hash_text(content))
        db.session.add(req)
        db.session.flush()
        created = []
        seen = set()
        for item in parties:
            name = (item.get('name') or '').strip()
            email = (item.get('email') or '').strip().lower()
            key = (name.lower(), email)
            if not name or key in seen:
                continue
            seen.add(key)
            p = SignatureParty(request_id=req.id, code=secrets.token_hex(20), name=name[:180], email=email[:180])
            db.session.add(p)
            created.append(p)
        if not created:
            db.session.rollback()
            return fail('Nenhuma parte válida informada.', 400)
        db.session.add(SignatureEvent(request_id=req.id, event_type='request.created', payload={'count': len(created), 'title': req.title}))
        db.session.commit()
        log('signature.request.create', request.user.id, 'signature', req.id, {'parties': len(created)})
        return jsonify({'success': True, 'request': pack_request(req, created)}), 201

    @app.get('/api/signatures/<request_id>')
    @auth_required
    def read_signature_request(request_id):
        req = owned_request(request_id)
        if not req:
            return fail('Solicitação não encontrada.', 404)
        parties = SignatureParty.query.filter_by(request_id=req.id).order_by(SignatureParty.id).all()
        return jsonify({'success': True, 'request': pack_request(req, parties), 'contract_content': req.contract_content})

    @app.get('/api/signatures/<request_id>/evidence')
    @auth_required
    def read_signature_evidence(request_id):
        req = owned_request(request_id)
        if not req:
            return fail('Solicitação não encontrada.', 404)
        parties = SignatureParty.query.filter_by(request_id=req.id).order_by(SignatureParty.id).all()
        events = SignatureEvent.query.filter_by(request_id=req.id).order_by(SignatureEvent.created_at).all()
        return jsonify({'success': True, 'evidence': build_evidence(req, parties, events), 'contract_content': req.contract_content})

    @app.post('/api/signatures/<request_id>/reminder')
    @auth_required
    def create_signature_reminder(request_id):
        req = owned_request(request_id)
        if not req:
            return fail('Solicitação não encontrada.', 404)
        body = request.get_json(silent=True) or {}
        party_id = body.get('party_id') or ''
        party = SignatureParty.query.filter_by(id=party_id, request_id=req.id).first() if party_id else None
        if not party:
            party = SignatureParty.query.filter(SignatureParty.request_id == req.id, SignatureParty.status != 'signed').order_by(SignatureParty.id).first()
        if not party:
            return fail('Não há assinaturas pendentes para lembrar.', 400)
        db.session.add(SignatureEvent(request_id=req.id, party_id=party.id, event_type='reminder.created', payload={'party': party.name, 'email': party.email}))
        db.session.commit()
        log('signature.reminder.create', request.user.id, 'signature', req.id, {'party_id': party.id})
        sign_path = '/sign/' + party.code
        message = f'Olá, {party.name}. Você recebeu um documento para assinatura eletrônica no DocWallet Docs: {sign_path}'
        return jsonify({'success': True, 'party': pack_party(party), 'url': sign_path, 'message': message})

    @app.post('/api/signatures/<request_id>/cancel')
    @auth_required
    def cancel_signature_request(request_id):
        req = owned_request(request_id)
        if not req:
            return fail('Solicitação não encontrada.', 404)
        if req.status == 'completed':
            return fail('Contrato já concluído não pode ser cancelado.', 400)
        req.status = 'cancelled'
        db.session.add(SignatureEvent(request_id=req.id, event_type='request.cancelled', payload={'by': request.user.id}))
        db.session.commit()
        parties = SignatureParty.query.filter_by(request_id=req.id).order_by(SignatureParty.id).all()
        return jsonify({'success': True, 'request': pack_request(req, parties)})

    @app.get('/api/sign/<code>')
    def public_sign_page(code):
        party = SignatureParty.query.filter_by(code=code).first()
        if not party:
            return fail('Link de assinatura não encontrado.', 404)
        req = SignatureRequest.query.filter_by(id=party.request_id).first()
        if not req:
            return fail('Contrato não encontrado.', 404)
        if req.status == 'cancelled':
            return fail('Solicitação de assinatura cancelada.', 410)
        parties = SignatureParty.query.filter_by(request_id=req.id).order_by(SignatureParty.id).all()
        db.session.add(SignatureEvent(request_id=req.id, party_id=party.id, event_type='link.opened', payload={'ip': request.headers.get('X-Forwarded-For', request.remote_addr or '')[:80]}))
        db.session.commit()
        return jsonify({'success': True, 'request': pack_request(req, parties), 'party': pack_party(party, public=True), 'contract_content': req.contract_content})

    @app.post('/api/sign/<code>/accept')
    def accept_signature(code):
        body = request.get_json(silent=True) or {}
        party = SignatureParty.query.filter_by(code=code).first()
        if not party:
            return fail('Link de assinatura não encontrado.', 404)
        req = SignatureRequest.query.filter_by(id=party.request_id).first()
        if not req:
            return fail('Contrato não encontrado.', 404)
        if req.status == 'cancelled':
            return fail('Solicitação de assinatura cancelada.', 410)
        if party.status == 'signed':
            return fail('Esta parte já assinou.', 400)
        signed_name = clean_text(body.get('signed_name') or body.get('name') or '', 180)
        signed_email = clean_text(body.get('signed_email') or body.get('email') or party.email or '', 180).lower()
        signed_cpf = clean_text(body.get('signed_cpf') or body.get('cpf') or '', 40)
        signed_phone = clean_text(body.get('signed_phone') or body.get('phone') or '', 80)
        confirmation_phrase = clean_text(body.get('confirmation_phrase') or '', 120)
        signature_image = clean_text(body.get('signature_image') or '', 250000)
        consent_text = clean_text(body.get('consent_text') or '', 3000)
        geo = body.get('geolocation') or {}
        device = body.get('device_fingerprint') or body.get('device') or {}
        evidence_level = clean_text(body.get('evidence_level') or 'reinforced_evidence', 60)
        accepted = bool(body.get('accepted'))
        phrase_ok = confirmation_phrase.upper().strip() in {'EU ACEITO', 'ACEITO', 'EU ACEITO ASSINAR'}
        signature_ok = signature_image.startswith('data:image/') and len(signature_image) > 200
        if not signed_name or not accepted:
            return fail('Informe o nome completo e aceite os termos.', 400)
        if evidence_level == 'reinforced_evidence' and not phrase_ok:
            return fail('Digite EU ACEITO para confirmar a assinatura reforçada.', 400)
        if evidence_level == 'reinforced_evidence' and not signature_ok:
            return fail('Desenhe sua assinatura para concluir a assinatura reforçada.', 400)
        party.status = 'signed'
        party.signed_name = signed_name[:180]
        party.signed_email = signed_email[:180]
        party.signed_cpf = signed_cpf[:40] or None
        party.signed_phone = signed_phone[:80] or None
        party.confirmation_phrase = confirmation_phrase[:120]
        party.signature_image = signature_image
        party.geo_latitude = clean_text(geo.get('latitude') if isinstance(geo, dict) else '', 80) or None
        party.geo_longitude = clean_text(geo.get('longitude') if isinstance(geo, dict) else '', 80) or None
        party.geo_accuracy = clean_text(geo.get('accuracy') if isinstance(geo, dict) else '', 80) or None
        party.device_fingerprint = device if isinstance(device, dict) else {}
        party.evidence_level = evidence_level
        party.consent_text = consent_text or 'Li, aceito e desejo assinar eletronicamente este documento pelo DocWallet Docs.'
        party.signed_at = utc_now()
        party.ip_address = request.headers.get('X-Forwarded-For', request.remote_addr or '')[:80]
        party.user_agent = request.headers.get('User-Agent', '')[:1000]
        db.session.add(SignatureEvent(request_id=req.id, party_id=party.id, event_type='party.signed', payload={
            'name': signed_name,
            'email': signed_email,
            'ip': party.ip_address,
            'evidence_level': party.evidence_level,
            'has_drawn_signature': bool(party.signature_image),
            'signature_image_sha256': hash_text(party.signature_image) if party.signature_image else None,
            'confirmation_phrase': party.confirmation_phrase,
            'cpf_provided': bool(party.signed_cpf),
            'phone_provided': bool(party.signed_phone),
            'geo_provided': bool(party.geo_latitude and party.geo_longitude),
            'device_fingerprint': party.device_fingerprint or {},
        }))
        all_parties = SignatureParty.query.filter_by(request_id=req.id).order_by(SignatureParty.id).all()
        if all(p.status == 'signed' for p in all_parties):
            req.status = 'completed'
            req.completed_at = utc_now()
            db.session.flush()
            req.final_hash = build_final_hash(req)
            db.session.add(SignatureEvent(request_id=req.id, event_type='request.completed', payload={'final_hash': req.final_hash, 'evidence_version': '1.1'}))
        db.session.commit()
        parties = SignatureParty.query.filter_by(request_id=req.id).order_by(SignatureParty.id).all()
        next_party = SignatureParty.query.filter(SignatureParty.request_id == req.id, SignatureParty.status != 'signed').order_by(SignatureParty.id).first()
        return jsonify({'success': True, 'request': pack_request(req, parties), 'party': pack_party(party, public=True), 'next_party': pack_next_party(next_party)})
