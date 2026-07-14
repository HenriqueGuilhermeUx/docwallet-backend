def install_paid_certificates(app, db, auth_required, fail, log, Certificate, certificate_to_dict, create_certificate_id, normalize_hash):
    import os
    import datetime as dt
    from sqlalchemy import text

    try:
        from web3 import Web3
    except Exception:  # pragma: no cover
        Web3 = None

    def explorer_url(tx_hash):
        base = os.environ.get('DOCWALLET_EXPLORER_URL', 'https://polygonscan.com/tx').rstrip('/')
        return base + '/' + tx_hash

    def payment_row(payment_id, user_id):
        row = db.session.execute(
            text('SELECT id, user_id, amount_cents, currency, status FROM payment_intents WHERE id = :id AND user_id = :user_id'),
            {'id': payment_id, 'user_id': user_id},
        ).mappings().first()
        return row

    def send_hash_transaction(file_hash):
        private_key = os.environ.get('DOCWALLET_SIGNER_PRIVATE_KEY', '').strip()
        treasury = os.environ.get('DOCWALLET_TREASURY_ADDRESS', '').strip()
        rpc_url = os.environ.get('DOCWALLET_RPC_URL', 'https://polygon-rpc.com').strip()
        chain_id = int(os.environ.get('DOCWALLET_CHAIN_ID', '137'))

        if Web3 is None:
            raise RuntimeError('web3 não está disponível no backend.')
        if not private_key:
            raise RuntimeError('DOCWALLET_SIGNER_PRIVATE_KEY precisa ser configurada no Render para registro automático por Pix.')
        if not treasury:
            raise RuntimeError('DOCWALLET_TREASURY_ADDRESS precisa ser configurada no Render.')

        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 30}))
        if not w3.is_connected():
            raise RuntimeError('Não foi possível conectar ao RPC da blockchain.')

        account = w3.eth.account.from_key(private_key)
        to_address = Web3.to_checksum_address(treasury)
        nonce = w3.eth.get_transaction_count(account.address)
        tx = {
            'to': to_address,
            'value': 0,
            'data': '0x' + file_hash,
            'chainId': chain_id,
            'nonce': nonce,
            'gasPrice': w3.eth.gas_price,
        }

        try:
            tx['gas'] = w3.eth.estimate_gas({**tx, 'from': account.address})
        except Exception:
            tx['gas'] = 70000

        signed = account.sign_transaction(tx)
        raw = getattr(signed, 'rawTransaction', None) or getattr(signed, 'raw_transaction')
        tx_hash_bytes = w3.eth.send_raw_transaction(raw)
        tx_hash = w3.to_hex(tx_hash_bytes)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt and receipt.get('status') != 1:
            raise RuntimeError('A transação foi enviada, mas falhou na blockchain.')
        return tx_hash, account.address, receipt.get('blockNumber') if receipt else None

    @app.post('/api/blockchain/register-paid')
    @auth_required
    def register_paid_certificate():
        body = __import__('flask').request.get_json(silent=True) or {}
        payment_id = (body.get('payment_id') or '').strip()
        document_name = (body.get('document_name') or 'Documento DocWallet').strip()[:255]
        try:
            file_hash = normalize_hash(body.get('file_hash') or '')
        except Exception as exc:
            return fail(str(exc), 400)

        if not payment_id:
            return fail('payment_id é obrigatório para registro pago por Pix.', 400)

        row = payment_row(payment_id, __import__('flask').request.user.id)
        if not row:
            return fail('Pagamento não encontrado.', 404)
        if row['status'] != 'paid':
            return fail('Pagamento Pix ainda não confirmado.', 402, {'payment_status': row['status']})

        try:
            tx_hash, wallet_address, block_number = send_hash_transaction(file_hash)
        except Exception as exc:
            return fail(str(exc), 400)

        cert = Certificate(
            user_id=__import__('flask').request.user.id,
            document_id=None,
            document_name=document_name,
            file_hash=file_hash,
            certificate_id=create_certificate_id(),
            wallet_address=wallet_address,
            tx_hash=tx_hash,
            chain_id=int(os.environ.get('DOCWALLET_CHAIN_ID', '137')),
            network_name=os.environ.get('DOCWALLET_NETWORK_NAME', 'Polygon'),
            block_number=block_number,
            explorer_url=explorer_url(tx_hash),
            price_paid='R$ ' + format((row['amount_cents'] or 0) / 100, '.2f').replace('.', ','),
            currency=row['currency'] or 'BRL',
            status='confirmed',
            verification_payload={'payment_id': payment_id, 'method': 'pix_woovi'},
        )
        db.session.add(cert)
        db.session.commit()
        log('certificate.register_paid', __import__('flask').request.user.id, 'certificate', cert.id, {'payment_id': payment_id, 'file_hash': file_hash})
        return __import__('flask').jsonify({'success': True, 'certificate': certificate_to_dict(cert)}), 201
