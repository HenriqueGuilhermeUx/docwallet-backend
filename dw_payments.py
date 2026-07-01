def install_payments(app, db, auth_required, fail, log):
    import datetime as dt
    import json
    import os
    import uuid
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

    with app.app_context():
        db.create_all()

    def price_for(product_type):
        if product_type == 'contract':
            return 790
        return 390

    @app.get('/api/payments/products')
    def payment_products():
        return jsonify({'success': True, 'products': {
            'document_storage': {'amount_cents': 0, 'label': 'Guardar e compartilhar documentos'},
            'document_validation': {'amount_cents': 390, 'label': 'Validar documento em blockchain'},
            'contract_validation': {'amount_cents': 790, 'label': 'Gerar contrato e validar hash'},
        }})

    @app.post('/api/payments/intent')
    @auth_required
    def create_payment_intent():
        body = request.get_json(silent=True) or {}
        product_type = body.get('product_type') or 'document'
        method = body.get('method') or 'wallet'
        amount = price_for('contract' if product_type == 'contract' else 'document')
        item = PaymentIntent(user_id=request.user.id, product_type=product_type, method=method, amount_cents=amount, provider='docwallet')
        db.session.add(item)
        db.session.commit()
        log('payment.intent', request.user.id, 'payment', item.id, {'product_type': product_type, 'method': method})
        return jsonify({'success': True, 'payment': {'id': item.id, 'amount_cents': amount, 'currency': 'BRL', 'status': item.status}}), 201

    @app.post('/api/payments/woovi')
    @auth_required
    def create_woovi_payment():
        body = request.get_json(silent=True) or {}
        product_type = body.get('product_type') or 'document'
        amount = price_for('contract' if product_type == 'contract' else 'document')
        url = os.environ.get('WOOVI_CHARGE_URL', '').strip()
        app_id = os.environ.get('WOOVI_APP_ID', '').strip()
        if not url or not app_id:
            item = PaymentIntent(user_id=request.user.id, product_type=product_type, method='pix', amount_cents=amount, provider='woovi', status='config_required')
            db.session.add(item)
            db.session.commit()
            return jsonify({'success': False, 'error': 'WOOVI_CHARGE_URL e WOOVI_APP_ID precisam ser configurados no Render.', 'payment_id': item.id, 'amount_cents': amount}), 400

        payload = {
            'correlationID': str(uuid.uuid4()),
            'value': amount,
            'comment': 'DocWallet pay per use',
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json', 'Authorization': app_id}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                raw = response.read().decode('utf-8')
                provider_payload = json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode('utf-8') if exc.fp else ''
            return fail('Erro ao criar cobrança Pix Woovi: ' + body_text[:200], 400)
        except Exception as exc:
            return fail('Erro ao conectar com Woovi: ' + str(exc), 400)

        item = PaymentIntent(user_id=request.user.id, product_type=product_type, method='pix', amount_cents=amount, provider='woovi', status='created', payload=provider_payload)
        db.session.add(item)
        db.session.commit()
        return jsonify({'success': True, 'payment': {'id': item.id, 'amount_cents': amount, 'currency': 'BRL', 'status': item.status, 'provider_payload': provider_payload}}), 201
