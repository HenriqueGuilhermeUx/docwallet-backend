from pathlib import Path

path = Path(__file__).resolve().parent / 'app.py'
text = path.read_text(encoding='utf-8')

snippet = r'''

# BEGIN_DW_REVIEW_USER
@app.get('/api/review/ensure-google-user')
@app.post('/api/review/ensure-google-user')
def _dw_ensure_google_review_user():
    import os as _os
    secret = (_os.environ.get('DOCWALLET_REVIEW_SETUP_SECRET') or '').strip()
    provided = (__import__('flask').request.headers.get('X-Setup-Secret') or __import__('flask').request.args.get('secret') or '').strip()
    if secret and provided != secret:
        return error_response('Acesso negado.', 403)

    email = (_os.environ.get('DOCWALLET_REVIEW_EMAIL') or 'google.review@docwallet.app').strip().lower()
    password = _os.environ.get('DOCWALLET_REVIEW_PASSWORD') or 'DocWalletReview@2026'
    name = _os.environ.get('DOCWALLET_REVIEW_NAME') or 'Google Play Reviewer'

    user = User.query.filter_by(email=email).first()
    if user:
        user.name = name
        user.password_hash = hash_password(password)
        user.plan = user.plan or 'free'
    else:
        user = User(name=name, email=email, password_hash=hash_password(password), plan='free')
        db.session.add(user)
    db.session.commit()
    audit('review.google_user_ready', user.id, 'user', user.id)
    return jsonify({'success': True, 'user': user_to_dict(user), 'email': email})
# END_DW_REVIEW_USER
'''

if 'BEGIN_DW_REVIEW_USER' not in text:
    text = text.replace('\n\nif __name__ == "__main__":', snippet + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding='utf-8')
print('DocWallet review user patch applied.')
