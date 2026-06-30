"""
DocWallet Backend Standalone
API para carteira de documentos, contratos e validação blockchain.
Preparado para Render + PostgreSQL.
"""

import datetime as dt
import hashlib
import os
import re
import uuid
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import bcrypt
import jwt
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.utils import secure_filename

try:
    from web3 import Web3
except Exception:  # pragma: no cover
    Web3 = None

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", BASE_DIR / "uploads")).resolve()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "txt"}
ALLOWED_MIMES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "text/plain",
    "application/octet-stream",
}

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-docwallet-change-me")
JWT_EXPIRES_HOURS = int(os.environ.get("JWT_EXPIRES_HOURS", "168"))

DOCWALLET_TREASURY_ADDRESS = os.environ.get("DOCWALLET_TREASURY_ADDRESS", "").strip()
DOCWALLET_CHAIN_ID = int(os.environ.get("DOCWALLET_CHAIN_ID", "137"))
DOCWALLET_NETWORK_NAME = os.environ.get("DOCWALLET_NETWORK_NAME", "Polygon")
DOCWALLET_RPC_URL = os.environ.get("DOCWALLET_RPC_URL", "https://polygon-rpc.com")
DOCWALLET_EXPLORER_URL = os.environ.get("DOCWALLET_EXPLORER_URL", "https://polygonscan.com/tx")
DOCWALLET_NOTARIZATION_PRICE_NATIVE = os.environ.get("DOCWALLET_NOTARIZATION_PRICE_NATIVE", "0.01")
DOCWALLET_NATIVE_SYMBOL = os.environ.get("DOCWALLET_NATIVE_SYMBOL", "POL")
DOCWALLET_REQUIRE_ONCHAIN_VERIFY = os.environ.get("DOCWALLET_REQUIRE_ONCHAIN_VERIFY", "true").lower() == "true"
DOCWALLET_ALLOW_UNVERIFIED_TX = os.environ.get("DOCWALLET_ALLOW_UNVERIFIED_TX", "false").lower() == "true"

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL or f"sqlite:///{BASE_DIR / 'docwallet.db'}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")
origins = "*" if CORS_ORIGINS == "*" else [item.strip() for item in CORS_ORIGINS.split(",") if item.strip()]
CORS(app, resources={r"/api/*": {"origins": origins}})

db = SQLAlchemy(app)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    plan = db.Column(db.String(40), nullable=False, default="free")
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.Text, nullable=False)
    file_type = db.Column(db.String(120), nullable=True)
    file_size = db.Column(db.Integer, nullable=False, default=0)
    file_hash = db.Column(db.String(64), nullable=False, index=True)
    doc_type = db.Column(db.String(80), nullable=False, default="other")
    category = db.Column(db.String(80), nullable=False, default="other")
    is_notarized = db.Column(db.Boolean, default=False, nullable=False)
    certificate_id = db.Column(db.String(80), nullable=True)
    notarized_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)


class Certificate(db.Model):
    __tablename__ = "certificates"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    document_id = db.Column(db.String(36), db.ForeignKey("documents.id"), nullable=True, index=True)
    document_name = db.Column(db.String(255), nullable=False)
    file_hash = db.Column(db.String(64), nullable=False, index=True)
    certificate_id = db.Column(db.String(80), unique=True, nullable=False, index=True)
    wallet_address = db.Column(db.String(80), nullable=False)
    tx_hash = db.Column(db.String(120), unique=True, nullable=False, index=True)
    chain_id = db.Column(db.Integer, nullable=False)
    network_name = db.Column(db.String(80), nullable=False)
    block_number = db.Column(db.Integer, nullable=True)
    explorer_url = db.Column(db.Text, nullable=True)
    price_paid = db.Column(db.String(80), nullable=True)
    currency = db.Column(db.String(20), nullable=True)
    status = db.Column(db.String(40), nullable=False, default="confirmed")
    verification_payload = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)


class Contract(db.Model):
    __tablename__ = "contracts"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    contract_type = db.Column(db.String(80), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    party_a = db.Column(db.String(255), nullable=False)
    party_b = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)
    content = db.Column(db.Text, nullable=False)
    content_hash = db.Column(db.String(64), nullable=False, index=True)
    certificate_id = db.Column(db.String(80), nullable=True)
    status = db.Column(db.String(40), nullable=False, default="draft")
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(36), nullable=True, index=True)
    action = db.Column(db.String(120), nullable=False)
    resource_type = db.Column(db.String(80), nullable=True)
    resource_id = db.Column(db.String(80), nullable=True)
    metadata = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)


with app.app_context():
    db.create_all()
    try:
        db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_documents_user_hash ON documents(user_id, file_hash)"))
        db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_certificates_hash_status ON certificates(file_hash, status)"))
        db.session.commit()
    except Exception:
        db.session.rollback()


def now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_token(user: User) -> str:
    payload = {
        "sub": user.id,
        "email": user.email,
        "iat": dt.datetime.utcnow(),
        "exp": dt.datetime.utcnow() + dt.timedelta(hours=JWT_EXPIRES_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])


def current_user_from_request() -> Optional[User]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.replace("Bearer ", "", 1).strip()
    try:
        payload = decode_token(token)
    except Exception:
        return None
    return db.session.get(User, payload.get("sub"))


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user_from_request()
        if not user:
            return error_response("Token inválido ou ausente.", 401)
        request.user = user
        return fn(*args, **kwargs)
    return wrapper


def error_response(message: str, status: int = 400, extra: Optional[Dict[str, Any]] = None):
    payload = {"success": False, "error": message}
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def user_to_dict(user: User) -> Dict[str, Any]:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "plan": user.plan,
        "created_at": user.created_at.isoformat() + "Z",
    }


def document_to_dict(document: Document) -> Dict[str, Any]:
    return {
        "id": document.id,
        "user_id": document.user_id,
        "name": document.name,
        "original_filename": document.original_filename,
        "file_type": document.file_type,
        "file_size": document.file_size,
        "file_hash": document.file_hash,
        "type": document.doc_type,
        "category": document.category,
        "is_notarized": document.is_notarized,
        "certificate_id": document.certificate_id,
        "created_at": document.created_at.isoformat() + "Z",
        "download_url": f"/api/documents/{document.id}/download",
    }


def certificate_to_dict(cert: Certificate) -> Dict[str, Any]:
    return {
        "id": cert.id,
        "user_id": cert.user_id,
        "document_id": cert.document_id,
        "document_name": cert.document_name,
        "file_hash": cert.file_hash,
        "certificate_id": cert.certificate_id,
        "wallet_address": cert.wallet_address,
        "tx_hash": cert.tx_hash,
        "chain_id": cert.chain_id,
        "network_name": cert.network_name,
        "block_number": cert.block_number,
        "explorer_url": cert.explorer_url,
        "price_paid": cert.price_paid,
        "currency": cert.currency,
        "status": cert.status,
        "created_at": cert.created_at.isoformat() + "Z",
    }


def contract_to_dict(contract: Contract) -> Dict[str, Any]:
    return {
        "id": contract.id,
        "title": contract.title,
        "contract_type": contract.contract_type,
        "party_a": contract.party_a,
        "party_b": contract.party_b,
        "description": contract.description,
        "content": contract.content,
        "content_hash": contract.content_hash,
        "certificate_id": contract.certificate_id,
        "status": contract.status,
        "created_at": contract.created_at.isoformat() + "Z",
    }


def normalize_hash(value: str) -> str:
    clean = re.sub(r"^0x", "", (value or "").strip().lower())
    if not re.fullmatch(r"[0-9a-f]{64}", clean):
        raise ValueError("Hash SHA-256 inválido.")
    return clean


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extension_allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def create_certificate_id(prefix: str = "DW-CERT") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:8].upper()}"


def audit(action: str, user_id: Optional[str] = None, resource_type: Optional[str] = None, resource_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None):
    try:
        log = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata or {},
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


def treasury_address_normalized() -> Optional[str]:
    if not DOCWALLET_TREASURY_ADDRESS:
        return None
    try:
        if Web3:
            return Web3.to_checksum_address(DOCWALLET_TREASURY_ADDRESS)
    except Exception:
        return DOCWALLET_TREASURY_ADDRESS.lower()
    return DOCWALLET_TREASURY_ADDRESS.lower()


def verify_onchain_transaction(tx_hash: str, expected_hash: str) -> Tuple[bool, Dict[str, Any]]:
    if not DOCWALLET_RPC_URL:
        return False, {"reason": "DOCWALLET_RPC_URL não configurada."}
    if not DOCWALLET_TREASURY_ADDRESS:
        return False, {"reason": "DOCWALLET_TREASURY_ADDRESS não configurada."}
    if Web3 is None:
        return False, {"reason": "web3 não está disponível no backend."}

    w3 = Web3(Web3.HTTPProvider(DOCWALLET_RPC_URL, request_kwargs={"timeout": 20}))
    if not w3.is_connected():
        return False, {"reason": "Não foi possível conectar ao RPC da blockchain."}

    try:
        tx = w3.eth.get_transaction(tx_hash)
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except Exception as exc:
        return False, {"reason": f"Transação não encontrada ou RPC indisponível: {str(exc)}"}

    tx_to = tx.get("to")
    expected_to = treasury_address_normalized()
    if tx_to and expected_to and tx_to.lower() != expected_to.lower():
        return False, {"reason": "Transação não foi enviada para a carteira recebedora configurada."}

    data_hex = tx.get("input", "") or ""
    if isinstance(data_hex, bytes):
        data_hex = data_hex.hex()
    data_hex = str(data_hex).lower().replace("0x", "")
    if expected_hash not in data_hex:
        return False, {"reason": "Hash do documento não está presente no calldata da transação."}

    required_wei = w3.to_wei(float(DOCWALLET_NOTARIZATION_PRICE_NATIVE), "ether")
    if int(tx.get("value", 0)) < required_wei:
        return False, {"reason": "Valor pago é menor que o preço configurado."}

    if receipt and receipt.get("status") != 1:
        return False, {"reason": "Transação existe, mas falhou na blockchain."}

    return True, {
        "tx_hash": tx_hash,
        "from": tx.get("from"),
        "to": tx_to,
        "value_wei": str(tx.get("value", 0)),
        "block_number": receipt.get("blockNumber") if receipt else None,
        "gas_used": receipt.get("gasUsed") if receipt else None,
        "calldata_contains_hash": True,
    }


CONTRACT_TEMPLATES = [
    {"id": "prestacao_servicos", "name": "Prestação de Serviços", "category": "Comercial"},
    {"id": "compra_venda", "name": "Compra e Venda", "category": "Bens"},
    {"id": "locacao_comercial", "name": "Locação Comercial", "category": "Imóveis"},
    {"id": "emprestimo_p2p", "name": "Empréstimo P2P", "category": "Financeiro"},
    {"id": "confissao_divida", "name": "Confissão de Dívida", "category": "Financeiro"},
    {"id": "nda", "name": "NDA / Confidencialidade", "category": "Empresarial"},
    {"id": "parceria_empresarial", "name": "Parceria Empresarial", "category": "Comercial"},
    {"id": "cessao_direitos", "name": "Cessão de Direitos", "category": "Financeiro"},
]


def generate_contract_content(contract_type: str, party_a: str, party_b: str, description: str) -> Tuple[str, str]:
    today = dt.date.today().strftime("%d/%m/%Y")
    title_map = {item["id"]: item["name"] for item in CONTRACT_TEMPLATES}
    title = title_map.get(contract_type, "Contrato Personalizado")

    clauses: Dict[str, str] = {
        "prestacao_servicos": f"""CLÁUSULA 1ª — DO OBJETO\nO presente contrato tem por objeto a prestação dos seguintes serviços: {description}\n\nCLÁUSULA 2ª — DAS OBRIGAÇÕES\nO contratado obriga-se a executar os serviços com diligência, qualidade e boa-fé. O contratante obriga-se a fornecer informações e realizar pagamentos nas condições acordadas.\n\nCLÁUSULA 3ª — DA VALIDADE DIGITAL\nAs partes reconhecem a validade deste instrumento em meio eletrônico e sua integridade poderá ser verificada pelo hash registrado no DocWallet.""",
        "compra_venda": f"""CLÁUSULA 1ª — DO OBJETO\nO vendedor vende ao comprador o bem ou direito descrito: {description}\n\nCLÁUSULA 2ª — DO PREÇO E ENTREGA\nAs partes acordam livremente as condições de preço, pagamento e entrega.\n\nCLÁUSULA 3ª — DA INTEGRIDADE\nO conteúdo deste contrato poderá ser validado por hash e certificado DocWallet.""",
        "locacao_comercial": f"""CLÁUSULA 1ª — DO OBJETO\nO locador cede ao locatário o uso comercial do bem descrito: {description}\n\nCLÁUSULA 2ª — DO USO\nO locatário compromete-se a utilizar o espaço para finalidade lícita, conservando-o conforme recebido.\n\nCLÁUSULA 3ª — DA VALIDADE DIGITAL\nEste instrumento poderá ser assinado eletronicamente e validado por registro de hash.""",
        "emprestimo_p2p": f"""CLÁUSULA 1ª — DO EMPRÉSTIMO\nO credor concede ao devedor o empréstimo descrito: {description}\n\nCLÁUSULA 2ª — DO PAGAMENTO\nO devedor compromete-se a restituir o valor nas condições pactuadas entre as partes.\n\nCLÁUSULA 3ª — DA PROVA DIGITAL\nO hash deste instrumento poderá ser registrado em blockchain para prova de integridade.""",
        "confissao_divida": f"""CLÁUSULA 1ª — DA CONFISSÃO\nO devedor reconhece, de forma livre, a existência da obrigação descrita: {description}\n\nCLÁUSULA 2ª — DO PAGAMENTO\nO pagamento ocorrerá conforme condições pactuadas entre credor e devedor.\n\nCLÁUSULA 3ª — DA INTEGRIDADE\nA autenticidade do conteúdo poderá ser verificada pelo certificado DocWallet.""",
        "nda": f"""CLÁUSULA 1ª — DAS INFORMAÇÕES CONFIDENCIAIS\nAs informações relacionadas a {description} serão tratadas como confidenciais.\n\nCLÁUSULA 2ª — DAS OBRIGAÇÕES\nA parte receptora compromete-se a não divulgar informações confidenciais a terceiros sem autorização.\n\nCLÁUSULA 3ª — DA VIGÊNCIA\nO dever de confidencialidade permanecerá pelo prazo acordado entre as partes.""",
        "parceria_empresarial": f"""CLÁUSULA 1ª — DA PARCERIA\nAs partes estabelecem parceria para: {description}\n\nCLÁUSULA 2ª — DAS RESPONSABILIDADES\nCada parte responderá por suas obrigações específicas, sem formação automática de sociedade.\n\nCLÁUSULA 3ª — DA PROVA DIGITAL\nO conteúdo poderá ser validado por hash registrado no DocWallet.""",
        "cessao_direitos": f"""CLÁUSULA 1ª — DA CESSÃO\nO cedente transfere ao cessionário os direitos descritos: {description}\n\nCLÁUSULA 2ª — DAS GARANTIAS\nO cedente declara possuir legitimidade para realizar a cessão, salvo disposição diversa entre as partes.\n\nCLÁUSULA 3ª — DA INTEGRIDADE\nA integridade do contrato poderá ser certificada por hash.""",
    }

    body = clauses.get(contract_type, f"OBJETO\n{description}\n\nAs partes celebram o presente instrumento conforme condições livremente pactuadas.")
    content = f"""{title.upper()}\n\nDATA: {today}\n\nPARTE A: {party_a}\nPARTE B: {party_b}\n\n{body}\n\nASSINATURAS\n\n______________________________\n{party_a}\n\n______________________________\n{party_b}\n\nDocumento gerado pelo DocWallet. A validade do conteúdo pode ser comprovada por hash SHA-256 e certificado blockchain."""
    return title, content


@app.get("/")
def root():
    return jsonify({
        "service": "DocWallet Backend",
        "status": "online",
        "version": "1.0.0",
        "docs": "/api/health",
    })


@app.get("/api/health")
def health():
    return jsonify({
        "success": True,
        "status": "online",
        "service": "DocWallet Backend",
        "timestamp": now_iso(),
        "database": "connected",
        "storage_dir": str(UPLOAD_DIR),
        "chain_id": DOCWALLET_CHAIN_ID,
        "network": DOCWALLET_NETWORK_NAME,
    })


@app.post("/api/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not name or not email or not password:
        return error_response("Nome, e-mail e senha são obrigatórios.")
    if len(password) < 6:
        return error_response("A senha deve ter no mínimo 6 caracteres.")

    existing = User.query.filter_by(email=email).first()
    if existing:
        return error_response("E-mail já cadastrado.", 409)

    user = User(name=name, email=email, password_hash=hash_password(password))
    db.session.add(user)
    db.session.commit()
    audit("auth.register", user.id, "user", user.id)

    return jsonify({"success": True, "token": create_token(user), "user": user_to_dict(user)}), 201


@app.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = User.query.filter_by(email=email).first()
    if not user or not verify_password(password, user.password_hash):
        return error_response("E-mail ou senha incorretos.", 401)

    audit("auth.login", user.id, "user", user.id)
    return jsonify({"success": True, "token": create_token(user), "user": user_to_dict(user)})


@app.get("/api/auth/me")
@require_auth
def me():
    return jsonify({"success": True, "user": user_to_dict(request.user)})


@app.post("/api/documents/upload")
@require_auth
def upload_document():
    if "file" not in request.files:
        return error_response("Arquivo não enviado.")

    file = request.files["file"]
    if not file.filename:
        return error_response("Nome do arquivo inválido.")

    if not extension_allowed(file.filename):
        return error_response("Formato não suportado. Use PDF, JPG, PNG ou TXT.")

    name = (request.form.get("name") or file.filename).strip()
    doc_type = (request.form.get("type") or "other").strip()
    category = (request.form.get("category") or "other").strip()

    safe_original = secure_filename(file.filename)
    ext = safe_original.rsplit(".", 1)[1].lower()
    stored_filename = f"{uuid.uuid4().hex}.{ext}"
    user_dir = UPLOAD_DIR / request.user.id
    user_dir.mkdir(parents=True, exist_ok=True)
    file_path = user_dir / stored_filename
    file.save(file_path)

    file_hash = sha256_file(file_path)
    file_size = file_path.stat().st_size
    file_type = file.mimetype if file.mimetype in ALLOWED_MIMES else file.mimetype

    document = Document(
        user_id=request.user.id,
        name=name,
        original_filename=safe_original,
        stored_filename=stored_filename,
        file_path=str(file_path),
        file_type=file_type,
        file_size=file_size,
        file_hash=file_hash,
        doc_type=doc_type,
        category=category,
    )
    db.session.add(document)
    db.session.commit()
    audit("document.upload", request.user.id, "document", document.id, {"file_hash": file_hash})

    return jsonify({"success": True, "document": document_to_dict(document)}), 201


@app.get("/api/documents")
@require_auth
def list_documents():
    docs = Document.query.filter_by(user_id=request.user.id).order_by(Document.created_at.desc()).all()
    return jsonify({"success": True, "documents": [document_to_dict(doc) for doc in docs]})


@app.get("/api/documents/<document_id>")
@require_auth
def get_document(document_id: str):
    document = Document.query.filter_by(id=document_id, user_id=request.user.id).first()
    if not document:
        return error_response("Documento não encontrado.", 404)
    return jsonify({"success": True, "document": document_to_dict(document)})


@app.get("/api/documents/<document_id>/download")
@require_auth
def download_document(document_id: str):
    document = Document.query.filter_by(id=document_id, user_id=request.user.id).first()
    if not document:
        return error_response("Documento não encontrado.", 404)
    path = Path(document.file_path)
    if not path.exists():
        return error_response("Arquivo não encontrado no storage.", 404)
    return send_file(path, as_attachment=True, download_name=document.original_filename, mimetype=document.file_type)


@app.delete("/api/documents/<document_id>")
@require_auth
def delete_document(document_id: str):
    document = Document.query.filter_by(id=document_id, user_id=request.user.id).first()
    if not document:
        return error_response("Documento não encontrado.", 404)
    path = Path(document.file_path)
    if path.exists():
        path.unlink()
    db.session.delete(document)
    db.session.commit()
    audit("document.delete", request.user.id, "document", document_id)
    return jsonify({"success": True, "message": "Documento excluído."})


@app.post("/api/blockchain/prepare")
@require_auth
def prepare_notarization():
    data = request.get_json(silent=True) or {}
    document_id = data.get("document_id")
    file_hash = data.get("file_hash")
    document_name = data.get("document_name") or "Documento DocWallet"

    document = None
    if document_id:
        document = Document.query.filter_by(id=document_id, user_id=request.user.id).first()
        if not document:
            return error_response("Documento não encontrado.", 404)
        file_hash = document.file_hash
        document_name = document.name

    try:
        normalized_hash = normalize_hash(file_hash)
    except ValueError as exc:
        return error_response(str(exc))

    return jsonify({
        "success": True,
        "document_id": document.id if document else None,
        "document_name": document_name,
        "file_hash": normalized_hash,
        "calldata": "0x" + normalized_hash,
        "treasury_address": DOCWALLET_TREASURY_ADDRESS,
        "chain_id": DOCWALLET_CHAIN_ID,
        "network_name": DOCWALLET_NETWORK_NAME,
        "rpc_url": DOCWALLET_RPC_URL,
        "explorer_url": DOCWALLET_EXPLORER_URL,
        "price_native": DOCWALLET_NOTARIZATION_PRICE_NATIVE,
        "currency": DOCWALLET_NATIVE_SYMBOL,
    })


@app.post("/api/blockchain/confirm")
@require_auth
def confirm_notarization():
    data = request.get_json(silent=True) or {}
    tx_hash = (data.get("tx_hash") or "").strip()
    document_id = data.get("document_id")
    document_name = (data.get("document_name") or "Documento DocWallet").strip()

    if not tx_hash:
        return error_response("tx_hash é obrigatório.")

    document = None
    if document_id:
        document = Document.query.filter_by(id=document_id, user_id=request.user.id).first()
        if not document:
            return error_response("Documento não encontrado.", 404)
        file_hash = document.file_hash
        document_name = document.name
    else:
        file_hash = data.get("file_hash")

    try:
        normalized_hash = normalize_hash(file_hash)
    except ValueError as exc:
        return error_response(str(exc))

    existing = Certificate.query.filter_by(tx_hash=tx_hash).first()
    if existing:
        return jsonify({"success": True, "certificate": certificate_to_dict(existing), "message": "Transação já registrada."})

    verified, verification_payload = verify_onchain_transaction(tx_hash, normalized_hash)
    if not verified and DOCWALLET_REQUIRE_ONCHAIN_VERIFY and not DOCWALLET_ALLOW_UNVERIFIED_TX:
        return error_response("Não foi possível confirmar a transação on-chain.", 400, {"verification": verification_payload})

    certificate_id = create_certificate_id()
    explorer = f"{DOCWALLET_EXPLORER_URL.rstrip('/')}/{tx_hash}"
    cert = Certificate(
        user_id=request.user.id,
        document_id=document.id if document else None,
        document_name=document_name,
        file_hash=normalized_hash,
        certificate_id=certificate_id,
        wallet_address=(verification_payload.get("from") or data.get("wallet_address") or "unknown"),
        tx_hash=tx_hash,
        chain_id=DOCWALLET_CHAIN_ID,
        network_name=DOCWALLET_NETWORK_NAME,
        block_number=verification_payload.get("block_number"),
        explorer_url=explorer,
        price_paid=DOCWALLET_NOTARIZATION_PRICE_NATIVE,
        currency=DOCWALLET_NATIVE_SYMBOL,
        status="confirmed" if verified else "pending_manual_review",
        verification_payload=verification_payload,
    )
    db.session.add(cert)

    if document:
        document.is_notarized = True
        document.certificate_id = certificate_id
        document.notarized_at = dt.datetime.utcnow()

    db.session.commit()
    audit("blockchain.confirm", request.user.id, "certificate", cert.id, {"tx_hash": tx_hash, "verified": verified})

    return jsonify({"success": True, "verified": verified, "certificate": certificate_to_dict(cert), "verification": verification_payload}), 201


@app.get("/api/blockchain/certificates")
@require_auth
def list_certificates():
    certs = Certificate.query.filter_by(user_id=request.user.id).order_by(Certificate.created_at.desc()).all()
    return jsonify({"success": True, "certificates": [certificate_to_dict(cert) for cert in certs]})


@app.get("/api/blockchain/certificates/<certificate_id>")
def get_certificate(certificate_id: str):
    cert = Certificate.query.filter_by(certificate_id=certificate_id).first()
    if not cert:
        return error_response("Certificado não encontrado.", 404)
    return jsonify({"success": True, "certificate": certificate_to_dict(cert)})


@app.get("/api/blockchain/verify/<file_hash>")
def verify_hash_route(file_hash: str):
    try:
        normalized_hash = normalize_hash(file_hash)
    except ValueError as exc:
        return error_response(str(exc))
    cert = Certificate.query.filter_by(file_hash=normalized_hash).order_by(Certificate.created_at.desc()).first()
    return jsonify({
        "success": True,
        "authentic": bool(cert),
        "certificate": certificate_to_dict(cert) if cert else None,
        "message": "Documento encontrado e certificado." if cert else "Hash não encontrado nos certificados DocWallet.",
    })


@app.post("/api/blockchain/verify-file")
def verify_file_route():
    if "file" not in request.files:
        return error_response("Arquivo não enviado.")
    file = request.files["file"]
    data = file.read()
    file_hash = sha256_bytes(data)
    cert = Certificate.query.filter_by(file_hash=file_hash).order_by(Certificate.created_at.desc()).first()
    return jsonify({
        "success": True,
        "file_hash": file_hash,
        "authentic": bool(cert),
        "certificate": certificate_to_dict(cert) if cert else None,
        "message": "Documento autêntico: certificado encontrado." if cert else "Documento não encontrado nos certificados DocWallet.",
    })


@app.get("/api/contracts/templates")
def contract_templates():
    return jsonify({"success": True, "templates": CONTRACT_TEMPLATES})


@app.post("/api/contracts/create")
@require_auth
def create_contract():
    data = request.get_json(silent=True) or {}
    contract_type = (data.get("type") or "prestacao_servicos").strip()
    party_a = (data.get("party_a") or "").strip()
    party_b = (data.get("party_b") or "").strip()
    description = (data.get("description") or "").strip()

    if not party_a or not party_b or not description:
        return error_response("Parte A, Parte B e descrição são obrigatórios.")

    title, content = generate_contract_content(contract_type, party_a, party_b, description)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    contract = Contract(
        user_id=request.user.id,
        contract_type=contract_type,
        title=title,
        party_a=party_a,
        party_b=party_b,
        description=description,
        content=content,
        content_hash=content_hash,
    )
    db.session.add(contract)
    db.session.commit()
    audit("contract.create", request.user.id, "contract", contract.id, {"content_hash": content_hash})

    return jsonify({"success": True, "contract": contract_to_dict(contract)}), 201


@app.get("/api/contracts")
@require_auth
def list_contracts():
    contracts = Contract.query.filter_by(user_id=request.user.id).order_by(Contract.created_at.desc()).all()
    return jsonify({"success": True, "contracts": [contract_to_dict(contract) for contract in contracts]})


@app.get("/api/contracts/<contract_id>")
@require_auth
def get_contract(contract_id: str):
    contract = Contract.query.filter_by(id=contract_id, user_id=request.user.id).first()
    if not contract:
        return error_response("Contrato não encontrado.", 404)
    return jsonify({"success": True, "contract": contract_to_dict(contract)})


@app.post("/api/contracts/<contract_id>/save-as-document")
@require_auth
def save_contract_as_document(contract_id: str):
    contract = Contract.query.filter_by(id=contract_id, user_id=request.user.id).first()
    if not contract:
        return error_response("Contrato não encontrado.", 404)

    user_dir = UPLOAD_DIR / request.user.id
    user_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"contract-{contract.id}.txt"
    path = user_dir / stored_filename
    path.write_text(contract.content, encoding="utf-8")

    document = Document(
        user_id=request.user.id,
        name=contract.title,
        original_filename=f"{contract.title}.txt",
        stored_filename=stored_filename,
        file_path=str(path),
        file_type="text/plain",
        file_size=path.stat().st_size,
        file_hash=contract.content_hash,
        doc_type="contract",
        category="contracts",
    )
    db.session.add(document)
    db.session.commit()
    audit("contract.save_as_document", request.user.id, "document", document.id, {"contract_id": contract.id})

    return jsonify({"success": True, "document": document_to_dict(document)}), 201


@app.errorhandler(413)
def too_large(_):
    return error_response(f"Arquivo muito grande. Limite: {MAX_UPLOAD_MB} MB.", 413)


@app.errorhandler(404)
def not_found(_):
    return error_response("Rota não encontrada.", 404)


@app.errorhandler(500)
def internal_error(exc):
    db.session.rollback()
    return error_response("Erro interno do servidor.", 500, {"detail": str(exc) if os.environ.get("FLASK_DEBUG") else None})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")
