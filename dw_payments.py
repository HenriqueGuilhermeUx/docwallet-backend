def install_payments(app, db, auth_required, fail, log):
    import datetime as dt
    import json
    import os
    import uuid
    import urllib.parse
    import urllib.request
    import urllib.error
    from flask import request, jsonify

    class PaymentIntent(db.Model):
        __tablename__ = 'payment_intents'
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id = db.Column(db.String(36), nullable=False, index=True)
        product_type = db.Column(db.String(40), nullable=False)
        method = db.Column(db.String(40), nullable=False)
        amount_cents = db.Column(db.Integer, nullable=False)
        currency = db.Column(db.String(10), default='BRL', nullable=False)
        status = db.Column(db.String(40), default='pending', nullable=False)
        provider = db.Column(db.String(40), nullable=True)
        provider_ref = db.Column(db.String(120), nullable=True)
        payload = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
        paid_at = db.Column(db.DateTime, nullable=True)

    with app.app_context():
        db.create_all()

    def price_for(product_type):
        if product_type == 'contract':
            return 790
        if product_type == 'pro':
            return 2990
        return 390

    def product_label(product_type):
        if product_type == 'contract':
            return 'Contrato DocWallet com assinatura e hash'
        if product_type == 'pro':
            return 'Plano Pro DocWallet'
        return 'Validacao de documento DocWallet'

    def woovi_charge_url():
        direct_url = os.environ.get('WOOVI_CHARGE_URL', '').strip()
        if direct_url:
            return direct_url
        base_url = os.environ.get('WOOVI_BASE_URL', 'https://api.openpix.com.br').strip().rstrip('/')
        return base_url + '/api/v1/charge'

    def woovi_auth_headers():
        token = os.environ.get('WOOVI_APP_ID', '').strip()
        if not token:
            return None
        auth_value = token if token.lower().startswith('bearer ') else 'Bearer ' + token
        return {
            'Content-Type': 'application/json',
            'Authorization': auth_value,
        }

    def request_json(url, method='GET', payload=None):
        headers = woovi_auth_headers()
        if not headers:
            raise RuntimeError('WOOVI_APP_ID precisa ser configurado no Render.')

        data = None
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=25) as response:
            raw = response.read().decode('utf-8')
            return json.loads(raw) if raw else {}

    def charge_from_payload(provider_payload):
        if not isinstance(provider_payload, dict):
            return {}
        return provider_payload.get('charge') or provider_payload.get('pixQrCode') or provider_payload

    def pix_from_charge(charge):
        payment_methods = charge.get('paymentMethods') or {}
        return payment_methods.get('pix') or {}

    def normalize_provider_status(status):
        value = (status or '').strip().upper()
        if value in ('COMPLETED', 'PAID', 'APPROVED', 'CONCLUDED', 'CONFIRMED'):
            return 'paid'
        if value in ('EXPIRED', 'CANCELED', 'CANCELLED'):
            return 'expired'
        if value in ('ACTIVE', 'PENDING', 'CREATED'):
            return 'pending'
        return value.lower() or 'pending'

    def payment_to_dict(item):
        payload = item.payload or {}
        charge = charge_from_payload(payload)
        pix = pix_from_charge(charge)
        return {
            'id': item.id,
            'product_type': item.product_type,
            'method': item.method,
            'amount_cents': item.amount_cents,
            'amount_label': 'R$ ' + format(item.amount_cents / 100, '.2f').replace('.', ','),
            'currency': item.currency,
            'status': item.status,
            'provider': item.provider,
            'provider_ref': item.provider_ref,
            'br_code': charge.get('brCode') or pix.get('brCode'),
            'qr_code_image': charge.get('qrCodeImage') or pix.get('qrCodeImage'),
            'payment_link_url': charge.get('paymentLinkUrl'),
            'correlation_id': charge.get('correlationID') or item.provider_ref,
            'tx_id': charge.get('txId') or pix.get('txId') or pix.get('transactionID'),
            'created_at': item.created_at.isoformat() + 'Z' if item.created_at else None,
            'paid_at': item.paid_at.isoformat() + 'Z' if item.paid_at else None,
        }

    def refresh_woovi_status(item):
        if item.provider != 'woovi' or not item.provider_ref:
            return item

        encoded_ref = urllib.parse.quote(item.provider_ref, safe='')
        url = woovi_charge_url().rstrip('/') + '/' + encoded_ref
        provider_payload = request_json(url, method='GET')
        charge = charge_from_payload(provider_payload)
        pix = pix_from_charge(charge)
        provider_status = charge.get('status') or pix.get('status')
        item.status = normalize_provider_status(provider_status)
        item.payload = provider_payload
        if item.status == 'paid' and not item.paid_at:
            item.paid_at = dt.datetime.utcnow()
        db.session.commit()
        return item

    @app.get('/api/payments/products')
    def payment_products():
        return jsonify({'success': True, 'products': {
            'document_storage': {'amount_cents': 0, 'label': 'Guardar e compartilhar documentos'},
            'document_validation': {'amount_cents': 390, 'label': 'Validar documento em blockchain'},
            'contract_validation': {'amount_cents': 790, 'label': 'Gerar contrato e validar hash'},
            'pro_monthly': {'amount_cents': 2990, 'label': 'Plano Pro mensal'},
        }})

    @app.post('/api/payments/intent')
    @auth_required
    def create_payment_intent():
        body = request.get_json(silent=True) or {}
        product_type = body.get('product_type') or 'document'
        method = body.get('method') or 'wallet'
        amount = price_for(product_type)
        item = PaymentIntent(user_id=request.user.id, product_type=product_type, method=method, amount_cents=amount, provider='docwallet')
        db.session.add(item)
        db.session.commit()
        log('payment.intent', request.user.id, 'payment', item.id, {'product_type': product_type, 'method': method})
        return jsonify({'success': True, 'payment': payment_to_dict(item)}), 201

    @app.post('/api/payments/woovi')
    @auth_required
    def create_woovi_payment():
        body = request.get_json(silent=True) or {}
        product_type = body.get('product_type') or 'document'
        amount = price_for(product_type)
        if not os.environ.get('WOOVI_APP_ID', '').strip():
            item = PaymentIntent(user_id=request.user.id, product_type=product_type, method='pix', amount_cents=amount, provider='woovi', status='config_required')
            db.session.add(item)
            db.session.commit()
            return jsonify({'success': False, 'error': 'WOOVI_APP_ID precisa ser configurado no Render.', 'payment': payment_to_dict(item)}), 400

        item = PaymentIntent(user_id=request.user.id, product_type=product_type, method='pix', amount_cents=amount, provider='woovi', status='pending')
        db.session.add(item)
        db.session.commit()

        correlation_id = 'dw_' + item.id.replace('-', '')
        payload = {
            'correlationID': correlation_id,
            'value': amount,
            'comment': product_label(product_type),
            'expiresIn': 1800,
            'customer': {
                'name': getattr(request.user, 'name', None) or request.user.email,
                'email': request.user.email,
            },
            'additionalInfo': [
                {'key': 'Product', 'value': product_label(product_type)},
                {'key': 'PaymentID', 'value': item.id},
            ],
        }

        url = woovi_charge_url()
        separator = '&' if '?' in url else '?'
        url = url + separator + 'return_existing=true'

        try:
            provider_payload = request_json(url, method='POST', payload=payload)
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode('utf-8') if exc.fp else ''
            item.status = 'failed'
            item.payload = {'error': body_text[:500]}
            db.session.commit()
            return fail('Erro ao criar cobrança Pix Woovi: ' + body_text[:200], 400)
        except Exception as exc:
            item.status = 'failed'
            item.payload = {'error': str(exc)}
            db.session.commit()
            return fail('Erro ao conectar com Woovi: ' + str(exc), 400)

        charge = charge_from_payload(provider_payload)
        pix = pix_from_charge(charge)
        item.provider_ref = charge.get('correlationID') or correlation_id
        item.status = normalize_provider_status(charge.get('status') or pix.get('status') or 'ACTIVE')
        item.payload = provider_payload
        db.session.commit()
        log('payment.woovi.created', request.user.id, 'payment', item.id, {'product_type': product_type, 'amount_cents': amount, 'provider_ref': item.provider_ref})
        return jsonify({'success': True, 'payment': payment_to_dict(item)}), 201

    @app.get('/api/payments/<payment_id>')
    @auth_required
    def get_payment(payment_id):
        item = PaymentIntent.query.filter_by(id=payment_id, user_id=request.user.id).first()
        if not item:
            return fail('Pagamento não encontrado.', 404)

        if item.provider == 'woovi' and item.provider_ref:
            try:
                item = refresh_woovi_status(item)
            except urllib.error.HTTPError as exc:
                body_text = exc.read().decode('utf-8') if exc.fp else ''
                return fail('Erro ao consultar pagamento Woovi: ' + body_text[:200], 400)
            except Exception as exc:
                return fail('Erro ao consultar pagamento Woovi: ' + str(exc), 400)

        return jsonify({'success': True, 'payment': payment_to_dict(item)})

    @app.post('/api/payments/woovi/webhook')
    def woovi_webhook():
        body = request.get_json(silent=True) or {}
        charge = charge_from_payload(body)
        pix = pix_from_charge(charge)
        correlation_id = charge.get('correlationID') or pix.get('correlationID')
        if not correlation_id:
            return jsonify({'success': True, 'ignored': True})

        item = PaymentIntent.query.filter_by(provider='woovi', provider_ref=correlation_id).first()
        if not item:
            return jsonify({'success': True, 'ignored': True})

        item.status = normalize_provider_status(charge.get('status') or pix.get('status'))
        item.payload = body
        if item.status == 'paid' and not item.paid_at:
            item.paid_at = dt.datetime.utcnow()
        db.session.commit()
        log('payment.woovi.webhook', item.user_id, 'payment', item.id, {'status': item.status})
        return jsonify({'success': True})
