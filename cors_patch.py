from pathlib import Path

path = Path(__file__).resolve().parent / 'app.py'
text = path.read_text(encoding='utf-8')

old = '''CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")
origins = "*" if CORS_ORIGINS == "*" else [item.strip() for item in CORS_ORIGINS.split(",") if item.strip()]
CORS(app, resources={r"/api/*": {"origins": origins}})
'''

new = '''CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")
if CORS_ORIGINS == "*":
    origins = "*"
else:
    origins = [item.strip() for item in CORS_ORIGINS.split(",") if item.strip()]
    # Capacitor/Android app origins. Without these, the APK WebView can fail with
    # "Failed to fetch" even when the backend is online.
    for app_origin in [
        "https://localhost",
        "http://localhost",
        "capacitor://localhost",
        "ionic://localhost",
        "https://docwallet.netlify.app",
    ]:
        if app_origin not in origins:
            origins.append(app_origin)
CORS(app, resources={r"/api/*": {"origins": origins}}, supports_credentials=False)
'''

if old in text and 'Capacitor/Android app origins' not in text:
    text = text.replace(old, new)

path.write_text(text, encoding='utf-8')
print('DocWallet CORS patch applied.')
