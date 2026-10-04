import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from reportlab.pdfgen import canvas

from dw_ocr import extract_document_text


class FakeResult:
    def __init__(self, payload):
        self.json = payload


class FakePipeline:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def predict(self, path):
        self.calls.append(path)
        return self.pages


def make_pdf(path: Path, text_value: str = ""):
    pdf = canvas.Canvas(str(path))
    if text_value:
        pdf.drawString(72, 720, text_value)
    pdf.save()


def main():
    previous = dict(os.environ)
    try:
        os.environ["DOCUMENT_OCR_ENABLED"] = "true"
        os.environ["DOCUMENT_OCR_PROVIDER"] = "paddle_local"
        os.environ["DOCUMENT_OCR_MIN_NATIVE_CHARS"] = "20"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            text_file = root / "note.txt"
            text_file.write_text("Contrato simples com vencimento em 10/10/2027", encoding="utf-8")
            result = extract_document_text(SimpleNamespace(file_path=str(text_file), file_type="text/plain"))
            assert result.source == "native_text"
            assert result.used_ocr is False

            native_pdf = root / "native.pdf"
            make_pdf(native_pdf, "CONTRATO DE PRESTACAO DE SERVICOS VALOR R$ 1000")
            never = FakePipeline([FakeResult({"res": {"rec_texts": ["SHOULD NOT RUN"]}})])
            result = extract_document_text(
                SimpleNamespace(file_path=str(native_pdf), file_type="application/pdf"),
                lambda: never,
            )
            assert result.source == "native_pdf"
            assert result.used_ocr is False
            assert never.calls == []

            image = root / "scan.jpg"
            image.write_bytes(b"fake-image-for-mocked-paddle")
            paddle = FakePipeline([
                FakeResult({
                    "res": {
                        "page_index": 0,
                        "rec_texts": ["CONTRATO DE LOCACAO", "Valor R$ 1.500,00"],
                        "rec_scores": [0.98, 0.92],
                    }
                })
            ])
            result = extract_document_text(
                SimpleNamespace(file_path=str(image), file_type="image/jpeg"),
                lambda: paddle,
            )
            assert result.source == "paddleocr"
            assert result.used_ocr is True
            assert "CONTRATO DE LOCACAO" in result.text
            assert result.confidence > 0.9
            assert result.metadata()["ocrProvider"] == "paddle_local"

            blank_pdf = root / "blank.pdf"
            make_pdf(blank_pdf)
            paddle = FakePipeline([
                FakeResult({"res": {"rec_texts": ["NOTA FISCAL", "CNPJ 12.345.678/0001-00"], "rec_scores": [0.96, 0.94]}})
            ])
            result = extract_document_text(
                SimpleNamespace(file_path=str(blank_pdf), file_type="application/pdf"),
                lambda: paddle,
            )
            assert result.used_ocr is True
            assert result.reason == "native_text_insufficient"
            assert "NOTA FISCAL" in result.text

            unavailable = extract_document_text(
                SimpleNamespace(file_path=str(image), file_type="image/jpeg"),
                lambda: (_ for _ in ()).throw(RuntimeError("model missing")),
            )
            assert unavailable.status == "unavailable"
            assert unavailable.used_ocr is False
            assert unavailable.reason == "ocr_runtime_error"

        print("DocWallet OCR smoke: OK")
    finally:
        os.environ.clear()
        os.environ.update(previous)


if __name__ == "__main__":
    main()
