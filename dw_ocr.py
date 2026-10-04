"""DocWallet OCR: native text first, PaddleOCR fallback for scans/images."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
_PIPELINE = None
_PIPELINE_LOCK = threading.Lock()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _clean(value: str) -> str:
    value = (value or "").replace("\x00", " ")
    value = re.sub(r"[ \t]+\n", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _char_count(value: str) -> int:
    return len(re.sub(r"\s+", "", value or ""))


@dataclass
class TextExtractionResult:
    text: str
    source: str
    provider: str
    used_ocr: bool
    native_chars: int = 0
    ocr_chars: int = 0
    pages: int = 0
    confidence: Optional[float] = None
    status: str = "ready"
    reason: Optional[str] = None
    error: Optional[str] = None

    def metadata(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "textExtractionSource": self.source,
            "ocrProvider": self.provider,
            "ocrUsed": self.used_ocr,
            "nativeTextChars": self.native_chars,
            "ocrTextChars": self.ocr_chars,
            "ocrPages": self.pages,
            "ocrStatus": self.status,
        }
        if self.confidence is not None:
            out["ocrConfidence"] = round(float(self.confidence), 4)
        if self.reason:
            out["ocrReason"] = self.reason
        if self.error:
            out["ocrError"] = self.error[:500]
        return out


def _read_pdf_native(path: Path, max_pages: int) -> Tuple[str, int]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    chunks: List[str] = []
    for page in reader.pages[:max_pages]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            chunks.append("")
    return _clean("\n".join(chunks)), len(reader.pages)


def _payload(result: Any) -> Dict[str, Any]:
    value = getattr(result, "json", None)
    if callable(value):
        value = value()
    if value is None and isinstance(result, dict):
        value = result
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = {}
    if not isinstance(value, dict):
        return {}
    return value["res"] if isinstance(value.get("res"), dict) else value


def _texts_scores(value: Dict[str, Any]) -> Tuple[List[str], List[float]]:
    texts = value.get("rec_texts")
    scores = value.get("rec_scores")
    if isinstance(texts, list):
        txt = [str(x).strip() for x in texts if str(x).strip()]
        scr: List[float] = []
        if isinstance(scores, list):
            for x in scores[:len(txt)]:
                try:
                    scr.append(float(x))
                except Exception:
                    pass
        return txt, scr
    if value.get("rec_text"):
        scr = []
        try:
            if value.get("rec_score") is not None:
                scr.append(float(value["rec_score"]))
        except Exception:
            pass
        return [str(value["rec_text"]).strip()], scr
    nested = value.get("overall_ocr_res")
    return _texts_scores(nested) if isinstance(nested, dict) else ([], [])


def _build_pipeline():
    from paddleocr import PaddleOCR
    return PaddleOCR(
        lang=os.environ.get("DOCUMENT_OCR_LANGUAGE", "pt"),
        ocr_version=os.environ.get("DOCUMENT_OCR_VERSION", "PP-OCRv6"),
        device=os.environ.get("DOCUMENT_OCR_DEVICE", "cpu"),
        use_doc_orientation_classify=_env_bool("DOCUMENT_OCR_USE_DOC_ORIENTATION", True),
        use_doc_unwarping=_env_bool("DOCUMENT_OCR_USE_DOC_UNWARPING", False),
        use_textline_orientation=_env_bool("DOCUMENT_OCR_USE_TEXTLINE_ORIENTATION", False),
    )


def _pipeline(factory: Optional[Callable[[], Any]] = None):
    global _PIPELINE
    if factory:
        return factory()
    if _PIPELINE is None:
        with _PIPELINE_LOCK:
            if _PIPELINE is None:
                _PIPELINE = _build_pipeline()
    return _PIPELINE


def _run_paddle(path: Path, factory: Optional[Callable[[], Any]] = None) -> Tuple[str, int, Optional[float]]:
    results: Iterable[Any] = _pipeline(factory).predict(str(path))
    pages: List[str] = []
    confidences: List[float] = []
    for result in results:
        texts, scores = _texts_scores(_payload(result))
        if texts:
            pages.append("\n".join(texts))
        confidences.extend(scores)
    text_value = _clean("\n\n".join(pages))
    return text_value, len(pages), mean(confidences) if confidences else None


def extract_document_text(document: Any, paddle_factory: Optional[Callable[[], Any]] = None) -> TextExtractionResult:
    path = Path(str(document.file_path))
    mime = (getattr(document, "file_type", None) or "").lower()
    suffix = path.suffix.lower()

    if not path.exists():
        return TextExtractionResult("", "missing", "none", False, status="error", reason="file_not_found")

    if mime.startswith("text/") or suffix == ".txt":
        value = _clean(path.read_text(encoding="utf-8", errors="ignore"))
        return TextExtractionResult(value, "native_text", "native", False, native_chars=_char_count(value))

    is_pdf = suffix == ".pdf" or mime == "application/pdf"
    is_image = suffix in _IMAGE_SUFFIXES or mime.startswith("image/")
    native_text, native_chars, pdf_pages = "", 0, 0
    max_native_pages = max(1, int(os.environ.get("DOCUMENT_OCR_MAX_NATIVE_PAGES", "40")))
    max_ocr_pages = max(1, int(os.environ.get("DOCUMENT_OCR_MAX_PDF_PAGES", "40")))
    min_native_chars = max(0, int(os.environ.get("DOCUMENT_OCR_MIN_NATIVE_CHARS", "120")))

    if is_pdf:
        try:
            native_text, pdf_pages = _read_pdf_native(path, max_native_pages)
            native_chars = _char_count(native_text)
        except Exception:
            pass
        if native_chars >= min_native_chars:
            return TextExtractionResult(native_text, "native_pdf", "pypdf", False, native_chars=native_chars, pages=min(pdf_pages, max_native_pages), reason="embedded_text_sufficient")
        if pdf_pages > max_ocr_pages:
            return TextExtractionResult(native_text, "native_pdf", "pypdf", False, native_chars=native_chars, pages=pdf_pages, status="skipped", reason="pdf_page_limit")

    if not (is_pdf or is_image):
        try:
            value = _clean(path.read_bytes()[:1_000_000].decode("utf-8", errors="ignore"))
        except Exception:
            value = ""
        return TextExtractionResult(value, "binary_fallback", "native", False, native_chars=_char_count(value))

    enabled = _env_bool("DOCUMENT_OCR_ENABLED", False)
    provider = os.environ.get("DOCUMENT_OCR_PROVIDER", "paddle_local").strip().lower()
    fail_open = _env_bool("DOCUMENT_OCR_FAIL_OPEN", True)
    if not enabled or provider in {"none", "disabled", "off"}:
        return TextExtractionResult(native_text, "native_pdf" if is_pdf else "image", "disabled", False, native_chars=native_chars, pages=pdf_pages, status="disabled", reason="ocr_disabled")

    if provider != "paddle_local":
        message = f"Unsupported OCR provider: {provider}"
        if not fail_open:
            raise RuntimeError(message)
        return TextExtractionResult(native_text, "native_pdf" if is_pdf else "image", provider, False, native_chars=native_chars, pages=pdf_pages, status="unavailable", reason="unsupported_provider", error=message)

    try:
        ocr_text, ocr_pages, confidence = _run_paddle(path, paddle_factory)
        ocr_chars = _char_count(ocr_text)
        if ocr_chars:
            return TextExtractionResult(ocr_text, "paddleocr", "paddle_local", True, native_chars=native_chars, ocr_chars=ocr_chars, pages=ocr_pages or pdf_pages, confidence=confidence, reason="image_input" if is_image else "native_text_insufficient")
        return TextExtractionResult(native_text, "native_pdf" if is_pdf else "paddleocr", "paddle_local", True, native_chars=native_chars, pages=ocr_pages or pdf_pages, confidence=confidence, status="empty", reason="ocr_returned_no_text")
    except Exception as exc:
        if not fail_open:
            raise
        return TextExtractionResult(native_text, "native_pdf" if is_pdf else "image", "paddle_local", False, native_chars=native_chars, pages=pdf_pages, status="unavailable", reason="ocr_runtime_error", error=str(exc))
