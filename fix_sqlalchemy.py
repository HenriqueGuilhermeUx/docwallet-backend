from pathlib import Path

path = Path(__file__).resolve().parent / "app.py"
text = path.read_text(encoding="utf-8")

text = text.replace(
    "    metadata = db.Column(db.JSON, nullable=True)",
    "    details = db.Column(db.JSON, nullable=True)",
)

text = text.replace(
    "            metadata=metadata or {},",
    "            details=metadata or {},",
)

text = text.replace(
    "def current_user_from_request() -> Optional[User]:\n    auth = request.headers.get(\"Authorization\", \"\")\n    if not auth.startswith(\"Bearer \"):\n        return None\n    token = auth.replace(\"Bearer \", \"\", 1).strip()",
    "def current_user_from_request() -> Optional[User]:\n    auth = request.headers.get(\"Authorization\", \"\")\n    if not auth.startswith(\"Bearer \"):\n        query_session = request.args.get(\"s\") or \"\"\n        if query_session:\n            auth = \"Bearer \" + query_session\n        else:\n            return None\n    token = auth.replace(\"Bearer \", \"\", 1).strip()",
)

bridge = """

# BEGIN_DW_BRIDGE_INSTALL
from dw_bridge import install_bridge as _dw_install_bridge
_dw_install_bridge(app, db, User, hash_password, create_token, user_to_dict, error_response, audit)
# END_DW_BRIDGE_INSTALL
"""

if "BEGIN_DW_BRIDGE_INSTALL" not in text:
    text = text.replace('\n\nif __name__ == "__main__":', bridge + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding="utf-8")
print("DocWallet startup patch applied.")
