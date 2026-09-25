def install_signature_identity(app, db, fail, log):
    import datetime as dt
    import hashlib
    import hmac
    import html
    import json
    import os
    import secrets
    import urllib.error
    import urllib.request
    import uuid
    from flask import request, jsonify
    from sqlalchemy import text

    def _utcnow():
        return dt.datetime.utcnow()

    def _otp_secret():
        return (os.environ.get('SIGNATURE_OTP_SECRET') or os.environ.get('SECRET_KEY') or '').encode('utf-8')

    def _hash_target(value):
        return hashlib.sha256(str(value or '').strip().lower().encode('utf-8')).hexdigest()

    def _hash_code(challenge_id, party_id, code):
        secret = _otp_secret()
        if not secret:
            raise RuntimeError('SIGNATURE_OTP_SECRET não configurado.')
        payload = f'{challenge_id}:{party_id}:{code}'.encode('utf-8')
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()

    def _mask_email(value):
        value = str(value or '').strip()
        if '@' not in value:
            return ''
        local, domain = value.split('@', 1)
        if len(local) <= 2:
            masked_local = local[:1] + '*'
        else:
            masked_local = local[:1] + ('*' * min(max(len(local) - 2, 2), 6)) + local[-1:]
        return f'{masked_local}@{domain}'

    def _party_for_code(code):
        return db.session.execute(text('''
            SELECT sp.id AS party_id, sp.request_id, sp.name, sp.email, sp.status,
                   sp.identity_verification, sr.title, sr.status AS request_status
              FROM signature_parties sp
              JOIN signature_requests sr ON sr.id = sp.request_id
             WHERE sp.code = :code
             LIMIT 1
        '''), {'code': code}).mappings().first()

    def _event(request_id, party_id, event_type, payload):
        db.session.execute(text('''
            INSERT INTO signature_events (id, request_id, party_id, event_type, payload, created_at)
            VALUES (:id, :request_id, :party_id, :event_type, CAST(:payload AS jsonb), NOW())
        '''), {
            'id': str(uuid.uuid4()),
            'request_id': request_id,
            'party_id': party_id,
            'event_type': event_type,
            'payload': json.dumps(payload, ensure_ascii=False),
        })

    def _send_otp_email(to_email, party_name, title, code):
        api_key = (os.environ.get('RESEND_API_KEY') or '').strip()
        sender = (os.environ.get('SIGNATURE_EMAIL_FROM') or '').strip()
        if not api_key or not sender:
            raise RuntimeError('E-mail transacional ainda não configurado no servidor.')

        safe_name = html.escape(party_name or 'Olá')
        safe_title = html.escape(title or 'Documento para assinatura')
        safe_code = html.escape(code)
        payload = {
            'from': sender,
            'to': [to_email],
            'subject': f'{code} — código de verificação DocWallet',
            'text': (
                f'Olá, {party_name}.\n\n'
                f'Seu código de verificação para assinar "{title}" no DocWallet é: {code}\n\n'
                'O código expira em 10 minutos. Não compartilhe este código.'
            ),
            'html': f'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f8fafc;font-family:Arial,Helvetica,sans-serif;color:#0f172a;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f8fafc;padding:28px 12px;"><tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border:1px solid #e2e8f0;border-radius:20px;">
<tr><td style="padding:28px 30px 8px;font-size:24px;line-height:30px;font-weight:700;color:#0f172a;">DocWallet Sign</td></tr>
<tr><td style="padding:8px 30px;font-size:16px;line-height:24px;color:#334155;">Olá, {safe_name}. Confirme seu e-mail para assinar <strong>{safe_title}</strong>.</td></tr>
<tr><td align="center" style="padding:24px 30px;"><span style="display:inline-block;font-family:Arial,Helvetica,sans-serif;font-size:34px;line-height:42px;font-weight:700;letter-spacing:8px;color:#0f172a;background:#f1f5f9;padding:14px 18px;border-radius:14px;">{safe_code}</span></td></tr>
<tr><td style="padding:0 30px 26px;font-size:13px;line-height:20px;color:#64748b;">O código expira em 10 minutos. Não compartilhe este código com terceiros.</td></tr>
</table></td></tr></table></body></html>''',
            'tags': [
                {'name': 'product', 'value': 'docwallet-sign'},
                {'name': 'type', 'value': 'signature-email-otp'},
            ],
        }
        req = urllib.request.Request(
            'https://api.resend.com/emails',
            data=json.dumps(payload).encode('utf-8'),
            method='POST',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
                'User-Agent': 'DocWallet-Sign/1.0',
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                body = response.read().decode('utf-8')
                return json.loads(body or '{}')
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            try:
                detail = json.loads(body or '{}').get('message') or body
            except Exception:
                detail = body
            raise RuntimeError(f'Falha no provedor de e-mail: {detail or exc.code}')
        except Exception as exc:
            raise RuntimeError(f'Falha ao enviar código: {exc}')

    with app.app_context():
        statements = [
            '''CREATE TABLE IF NOT EXISTS signature_identity_challenges (
                id VARCHAR(36) PRIMARY KEY,
                party_id VARCHAR(36) NOT NULL,
                channel VARCHAR(40) NOT NULL,
                target_hash VARCHAR(128) NOT NULL,
                code_hash VARCHAR(128) NOT NULL,
                status VARCHAR(40) NOT NULL DEFAULT 'sent',
                attempts INTEGER NOT NULL DEFAULT 0,
                expires_at TIMESTAMP NOT NULL,
                verified_at TIMESTAMP NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )''',
            'CREATE INDEX IF NOT EXISTS idx_signature_identity_party ON signature_identity_challenges(party_id)',
            'CREATE INDEX IF NOT EXISTS idx_signature_identity_status ON signature_identity_challenges(status)',
            'ALTER TABLE signature_parties ADD COLUMN IF NOT EXISTS identity_verification JSONB',
        ]
        for sql in statements:
            try:
                db.session.execute(text(sql))
                db.session.commit()
            except Exception:
                db.session.rollback()

    @app.get('/api/sign/<code>/identity')
    def signature_identity_config(code):
        party = _party_for_code(code)
        if not party:
            return fail('Link de assinatura não encontrado.', 404)
        identity = party['identity_verification'] or {}
        return jsonify({
            'success': True,
            'emailAvailable': bool(party['email']),
            'maskedEmail': _mask_email(party['email']),
            'verified': bool(identity.get('verified_at')),
            'method': identity.get('method'),
            'verifiedAt': identity.get('verified_at'),
            'evidenceLevel': 'verified_evidence' if identity.get('verified_at') else 'reinforced_evidence',
        })

    @app.post('/api/sign/<code>/identity/email-otp/request')
    def request_signature_email_otp(code):
        party = _party_for_code(code)
        if not party:
            return fail('Link de assinatura não encontrado.', 404)
        if party['request_status'] == 'cancelled':
            return fail('Solicitação de assinatura cancelada.', 410)
        if party['status'] == 'signed':
            return fail('Esta parte já assinou.', 400)
        email = str(party['email'] or '').strip().lower()
        if not email:
            return fail('Este destinatário não possui e-mail cadastrado para verificação.', 400)

        latest = db.session.execute(text('''
            SELECT created_at FROM signature_identity_challenges
             WHERE party_id = :party_id AND channel = 'email'
             ORDER BY created_at DESC LIMIT 1
        '''), {'party_id': party['party_id']}).mappings().first()
        now = _utcnow()
        if latest and latest['created_at'] and (now - latest['created_at']).total_seconds() < 45:
            return fail('Aguarde alguns segundos antes de solicitar outro código.', 429)

        challenge_id = str(uuid.uuid4())
        otp_code = f'{secrets.randbelow(1000000):06d}'
        ttl_seconds = max(300, min(int(os.environ.get('SIGNATURE_OTP_TTL_SECONDS') or '600'), 1800))
        expires_at = now + dt.timedelta(seconds=ttl_seconds)
        target_hash = _hash_target(email)
        code_hash = _hash_code(challenge_id, party['party_id'], otp_code)

        db.session.execute(text('''
            UPDATE signature_identity_challenges
               SET status = 'superseded'
             WHERE party_id = :party_id AND channel = 'email' AND status = 'sent'
        '''), {'party_id': party['party_id']})
        db.session.execute(text('''
            INSERT INTO signature_identity_challenges
                (id, party_id, channel, target_hash, code_hash, status, attempts, expires_at, created_at)
            VALUES (:id, :party_id, 'email', :target_hash, :code_hash, 'sent', 0, :expires_at, :created_at)
        '''), {
            'id': challenge_id,
            'party_id': party['party_id'],
            'target_hash': target_hash,
            'code_hash': code_hash,
            'expires_at': expires_at,
            'created_at': now,
        })
        db.session.commit()

        try:
            result = _send_otp_email(email, party['name'], party['title'], otp_code)
        except RuntimeError as exc:
            db.session.execute(text("UPDATE signature_identity_challenges SET status = 'delivery_failed' WHERE id = :id"), {'id': challenge_id})
            db.session.commit()
            return fail(str(exc), 503)

        _event(party['request_id'], party['party_id'], 'identity.email_otp.sent', {
            'challenge_id': challenge_id,
            'target_hash': target_hash,
            'provider': 'resend',
            'provider_message_id': result.get('id'),
            'expires_at': expires_at.isoformat() + 'Z',
        })
        db.session.commit()
        log('signature.identity.email_otp.sent', None, 'signature', party['request_id'], {'party_id': party['party_id']})
        return jsonify({
            'success': True,
            'challengeId': challenge_id,
            'maskedEmail': _mask_email(email),
            'expiresIn': ttl_seconds,
        })

    @app.post('/api/sign/<code>/identity/email-otp/verify')
    def verify_signature_email_otp(code):
        body = request.get_json(silent=True) or {}
        challenge_id = str(body.get('challenge_id') or '').strip()
        otp_code = ''.join(ch for ch in str(body.get('code') or '') if ch.isdigit())[:6]
        if not challenge_id or len(otp_code) != 6:
            return fail('Informe o código de 6 dígitos.', 400)

        party = _party_for_code(code)
        if not party:
            return fail('Link de assinatura não encontrado.', 404)
        row = db.session.execute(text('''
            SELECT * FROM signature_identity_challenges
             WHERE id = :id AND party_id = :party_id AND channel = 'email'
             LIMIT 1
        '''), {'id': challenge_id, 'party_id': party['party_id']}).mappings().first()
        if not row:
            return fail('Código de verificação não encontrado.', 404)
        if row['status'] == 'verified':
            identity = party['identity_verification'] or {}
            return jsonify({'success': True, 'verified': True, 'verifiedAt': identity.get('verified_at')})
        if row['status'] != 'sent':
            return fail('Este código não está mais disponível. Solicite um novo.', 400)
        now = _utcnow()
        if row['expires_at'] <= now:
            db.session.execute(text("UPDATE signature_identity_challenges SET status = 'expired' WHERE id = :id"), {'id': challenge_id})
            db.session.commit()
            return fail('O código expirou. Solicite um novo.', 400)

        max_attempts = max(3, min(int(os.environ.get('SIGNATURE_OTP_MAX_ATTEMPTS') or '5'), 10))
        attempts = int(row['attempts'] or 0) + 1
        expected = _hash_code(challenge_id, party['party_id'], otp_code)
        if not hmac.compare_digest(expected, row['code_hash']):
            status = 'locked' if attempts >= max_attempts else 'sent'
            db.session.execute(text('UPDATE signature_identity_challenges SET attempts = :attempts, status = :status WHERE id = :id'), {
                'attempts': attempts,
                'status': status,
                'id': challenge_id,
            })
            db.session.commit()
            if status == 'locked':
                return fail('Muitas tentativas incorretas. Solicite um novo código.', 429)
            return fail('Código incorreto.', 400)

        verified_at = now.isoformat() + 'Z'
        identity = {
            'method': 'email_otp',
            'verified_at': verified_at,
            'challenge_id': challenge_id,
            'target_hash': row['target_hash'],
            'assurance': 'verified_possession_of_invited_email',
        }
        db.session.execute(text('''
            UPDATE signature_identity_challenges
               SET attempts = :attempts, status = 'verified', verified_at = :verified_at
             WHERE id = :id
        '''), {'attempts': attempts, 'verified_at': now, 'id': challenge_id})
        db.session.execute(text('''
            UPDATE signature_parties
               SET identity_verification = CAST(:identity AS jsonb)
             WHERE id = :party_id
        '''), {'identity': json.dumps(identity, ensure_ascii=False), 'party_id': party['party_id']})
        _event(party['request_id'], party['party_id'], 'identity.email_otp.verified', {
            'challenge_id': challenge_id,
            'target_hash': row['target_hash'],
            'verified_at': verified_at,
        })
        db.session.commit()
        log('signature.identity.email_otp.verified', None, 'signature', party['request_id'], {'party_id': party['party_id']})
        return jsonify({'success': True, 'verified': True, 'verifiedAt': verified_at, 'evidenceLevel': 'verified_evidence'})
