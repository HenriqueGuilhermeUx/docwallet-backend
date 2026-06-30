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

path.write_text(text, encoding="utf-8")
print("DocWallet startup patch applied.")
