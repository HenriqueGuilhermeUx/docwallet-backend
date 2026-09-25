def install_signature_delivery(app, db, auth_required, fail, log):
    import datetime as dt
    import html
    import json
    import os
    import re
    import urllib.error
    import urllib.request
    import uuid
    from flask import request, jsonify
    from sqlalchemy import text

    def _public_url():
        return (os.environ.get('DOCWALLET_PUBLIC_URL') or 'https://trydocwallet.com').rstrip('/')

    def _clean_phone(value):
        digits = re.sub(r'\D+', '', str(value or ''))
        if len(digits) in (10, 11):
            digits = '55' + digits
        return digits

    def _owned_party(request_id, party_id):
        row = db.session.execute(text('''
            SELECT sr.id AS request_id, sr.title, sr.user_id,
                   sp.id AS party_id, sp.code, sp.name, sp.email, sp.status
              FROM signature_requests sr
              JOIN signature_parties sp ON sp.request_id = sr.id
             WHERE sr.id = :request_id
               AND sp.id = :party_id
               AND sr.user_id = :user_id
             LIMIT 1
        '''), {
            'request_id': request_id,
            'party_id': party_id,
            'user_id': request.user.id,
        }).mappings().first()
        return row

    def _add_event(request_id, party_id, event_type, payload):
        db.session.execute(text('''
            INSERT INTO signature_events (id, request_id, party_id, event_type, payload, created_at)
            VALUES (:event_id, :request_id, :party_id, :event_type, CAST(:payload AS jsonb), NOW())
        '''), {
            'event_id': str(uuid.uuid4()),
            'request_id': request_id,
            'party_id': party_id,
            'event_type': event_type,
            'payload': json.dumps(payload, ensure_ascii=False),
        })
        db.session.commit()

    def _email_html(party_name, title, sign_url):
        safe_name = html.escape(party_name or 'Olá')
        safe_title = html.escape(title or 'Documento para assinatura')
        safe_url = html.escape(sign_url, quote=True)
        return f'''<!doctype html>
<html lang="pt-BR">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f8fafc;font-family:Arial,Helvetica,sans-serif;color:#0f172a;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f8fafc;padding:28px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;background:#ffffff;border-radius:20px;border:1px solid #e2e8f0;overflow:hidden;">
<tr><td style="padding:28px 30px 8px;font-size:24px;line-height:30px;font-weight:700;color:#0f172a;">DocWallet Sign</td></tr>
<tr><td style="padding:8px 30px;font-size:16px;line-height:24px;color:#334155;">Olá, {safe_name}.</td></tr>
<tr><td style="padding:4px 30px;font-size:16px;line-height:24px;color:#334155;">Você recebeu o documento <strong>{safe_title}</strong> para assinatura eletrônica.</td></tr>
<tr><td align="center" style="padding:24px 30px;">
<a href="{safe_url}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;font-size:16px;line-height:20px;font-weight:700;padding:14px 24px;border-radius:12px;">Revisar e assinar</a>
</td></tr>
<tr><td style="padding:0 30px 12px;font-size:13px;line-height:20px;color:#64748b;">O link é individual. Não encaminhe para terceiros.</td></tr>
<tr><td style="padding:0 30px 26px;font-size:12px;line-height:18px;color:#94a3b8;word-break:break-all;">Se o botão não abrir, copie: {safe_url}</td></tr>
</table>
</td></tr></table>
</body></html>'''

    def _send_resend(to_email, party_name, title, sign_url, reminder=False):
        api_key = (os.environ.get('RESEND_API_KEY') or '').strip()
        sender = (os.environ.get('SIGNATURE_EMAIL_FROM') or '').strip()
        if not api_key or not sender:
            raise RuntimeError('E-mail transacional ainda não configurado no servidor.')

        subject = f"{'Lembrete: ' if reminder else ''}{title} — assinatura solicitada"
        text_body = (
            f"Olá, {party_name}.\n\n"
            f"Você recebeu o documento \"{title}\" para assinatura eletrônica no DocWallet.\n\n"
            f"Acesse: {sign_url}\n\n"
            "O link é individual. Não encaminhe para terceiros."
        )
        payload = {
            'from': sender,
            'to': [to_email],
            'subject': subject,
            'text': text_body,
            'html': _email_html(party_name, title, sign_url),
            'tags': [
                {'name': 'product', 'value': 'docwallet-sign'},
                {'name': 'type', 'value': 'signature-reminder' if reminder else 'signature-invite'},
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
            raise RuntimeError(f'Falha ao enviar e-mail: {exc}')

    @app.get('/api/signatures/delivery/config')
    @auth_required
    def signature_delivery_config():
        return jsonify({
            'success': True,
            'emailConfigured': bool((os.environ.get('RESEND_API_KEY') or '').strip() and (os.environ.get('SIGNATURE_EMAIL_FROM') or '').strip()),
            'publicUrl': _public_url(),
            'whatsappMode': 'share_link',
        })

    @app.post('/api/signatures/<request_id>/deliver')
    @auth_required
    def deliver_signature(request_id):
        body = request.get_json(silent=True) or {}
        party_id = str(body.get('party_id') or '').strip()
        channel = str(body.get('channel') or '').strip().lower()
        reminder = bool(body.get('reminder'))
        if not party_id:
            return fail('Informe o destinatário.', 400)
        if channel not in ('email', 'whatsapp'):
            return fail('Canal de envio inválido.', 400)

        party = _owned_party(request_id, party_id)
        if not party:
            return fail('Destinatário não encontrado.', 404)

        sign_url = f"{_public_url()}/sign/{party['code']}"
        if channel == 'whatsapp':
            phone = _clean_phone(body.get('phone'))
            message = f"Olá, {party['name']}. Você recebeu o documento \"{party['title']}\" para assinar eletronicamente no DocWallet: {sign_url}"
            import urllib.parse
            base = f"https://wa.me/{phone}" if phone else 'https://wa.me/'
            share_url = base + '?text=' + urllib.parse.quote(message)
            _add_event(request_id, party_id, 'delivery.whatsapp.prepared', {
                'channel': 'whatsapp',
                'target_provided': bool(phone),
                'reminder': reminder,
                'prepared_at': dt.datetime.utcnow().isoformat() + 'Z',
            })
            log('signature.delivery.whatsapp', request.user.id, 'signature', request_id, {'party_id': party_id, 'reminder': reminder})
            return jsonify({'success': True, 'channel': 'whatsapp', 'url': share_url, 'signUrl': sign_url})

        if not party['email']:
            return fail('Este destinatário não possui e-mail informado.', 400)
        try:
            result = _send_resend(party['email'], party['name'], party['title'], sign_url, reminder=reminder)
        except RuntimeError as exc:
            return fail(str(exc), 503)

        _add_event(request_id, party_id, 'delivery.email.sent', {
            'channel': 'email',
            'provider': 'resend',
            'provider_message_id': result.get('id'),
            'reminder': reminder,
            'sent_at': dt.datetime.utcnow().isoformat() + 'Z',
        })
        log('signature.delivery.email', request.user.id, 'signature', request_id, {'party_id': party_id, 'reminder': reminder})
        return jsonify({
            'success': True,
            'channel': 'email',
            'provider': 'resend',
            'messageId': result.get('id'),
            'signUrl': sign_url,
        })
