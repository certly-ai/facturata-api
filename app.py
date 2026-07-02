"""Facturata — EU e-invoicing API.

Generate and validate Factur-X / ZUGFeRD (EN 16931) e-invoices.
Stateless: no database, no document retention. Deterministic engine.
"""
from __future__ import annotations

import base64
import binascii
import os
import time
from typing import Optional, Literal

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from mapping import Invoice
import engine
from pdfgen import render_invoice_pdf

APP_NAME = os.environ.get("BRAND_NAME", "Facturata")
VERSION = "1.0.0"

RAPIDAPI_PROXY_SECRET = os.environ.get("RAPIDAPI_PROXY_SECRET", "")
DIRECT_API_KEYS = {k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()}
FREE_DAILY_LIMIT = int(os.environ.get("FREE_DAILY_LIMIT", "25"))
MAX_BODY_MB = 6

app = FastAPI(
    title=f"{APP_NAME} — EU e-Invoicing API (Factur-X / ZUGFeRD / EN 16931)",
    version=VERSION,
    description=(
        "Generate compliant Factur-X / ZUGFeRD e-invoices (PDF with embedded XML) from simple JSON, "
        "and validate existing e-invoices against the official EN 16931 XSD and schematron rules, "
        "including the French 2026 CTC profile. Stateless: documents are processed in memory and "
        "never stored."),
    contact={"name": APP_NAME},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # free validator page + docs; API endpoints are key-gated anyway
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

# ---------------- auth ----------------

def require_key(request: Request):
    """Accept requests from the RapidAPI gateway (proxy secret) or with a direct API key."""
    if not RAPIDAPI_PROXY_SECRET and not DIRECT_API_KEYS:
        return  # local/dev mode
    if RAPIDAPI_PROXY_SECRET and request.headers.get("X-RapidAPI-Proxy-Secret") == RAPIDAPI_PROXY_SECRET:
        return
    key = request.headers.get("X-Api-Key")
    if key and key in DIRECT_API_KEYS:
        return
    raise HTTPException(status_code=401, detail=(
        "Missing or invalid API key. Subscribe on RapidAPI and call through the RapidAPI gateway, "
        "or pass your direct key in the X-Api-Key header."))


# --------------- free-tool rate limit (in-memory, resets on restart) ---------------
_free_hits: dict = {}


def free_limit(request: Request):
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "?").split(",")[0].strip()
    today = time.strftime("%Y-%m-%d")
    key = (ip, today)
    _free_hits[key] = _free_hits.get(key, 0) + 1
    if len(_free_hits) > 20000:
        _free_hits.clear()
    if _free_hits[key] > FREE_DAILY_LIMIT:
        raise HTTPException(status_code=429, detail=(
            f"Free validator limit reached ({FREE_DAILY_LIMIT}/day). "
            "Use the API for unlimited validation."))


# ---------------- models ----------------

class GenerateOptions(BaseModel):
    profile: Literal["basicwl", "en16931", "extended"] = Field(
        "en16931", description="Factur-X profile. 'en16931' is the standard compliance level; "
                               "'basicwl' has no line items; 'extended' allows extra fields.")
    output: Literal["pdf", "xml"] = Field("pdf", description="'pdf' returns a Factur-X PDF (visual + embedded XML). "
                                                             "'xml' returns the CII XML only.")
    language: Literal["en", "de", "fr"] = Field("en", description="Language of the visual PDF.")
    accent_color: str = Field("#14532d", pattern="^#[0-9a-fA-F]{6}$",
                              description="Accent color of the visual PDF (hex).")


class GenerateRequest(BaseModel):
    invoice: Invoice
    options: GenerateOptions = GenerateOptions()


class ValidateRequest(BaseModel):
    file_base64: str = Field(..., description="Base64 of a Factur-X/ZUGFeRD PDF or a CII XML file.")
    check: Literal["base", "fr-ctc"] = Field("base", description="'base' = EN 16931 rules. "
                                                                 "'fr-ctc' adds the French 2026 CTC (Flux 2) rules.")


class ExtractRequest(BaseModel):
    pdf_base64: str = Field(..., description="Base64 of a Factur-X/ZUGFeRD PDF.")


# ---------------- helpers ----------------

def _b64_in(s: str, what: str) -> bytes:
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, f"{what} is not valid base64.")
    if len(raw) > MAX_BODY_MB * 1024 * 1024:
        raise HTTPException(413, f"{what} exceeds {MAX_BODY_MB} MB.")
    if not raw:
        raise HTTPException(422, f"{what} is empty.")
    return raw


def _validate_payload(raw: bytes, check: str) -> dict:
    is_pdf = raw[:5] == b"%PDF-"
    xml = raw
    extracted_from_pdf = False
    if is_pdf:
        fn, xml = engine.extract_from_pdf(raw)
        if xml is None:
            return {"valid": False, "file_type": "pdf", "error":
                    "No embedded e-invoice XML found in this PDF. It is not a Factur-X/ZUGFeRD document "
                    "(or the attachment is malformed)."}
        extracted_from_pdf = True
    flavor, level = engine.detect_flavor_level(xml)
    if not flavor:
        return {"valid": False, "file_type": "pdf" if is_pdf else "xml", "error":
                "Could not parse this as a CII e-invoice XML. UBL validation is not supported yet."}
    xsd = engine.xsd_report(xml, flavor=flavor, level=level or "autodetect")
    sch = {"passed": None, "errors": [], "warnings": [],
           "skipped": "Schematron skipped because XSD failed."}
    if xsd["passed"]:
        sch = engine.schematron_report(xml, flavor=flavor, level=level or "autodetect", check_option=check)
    valid = xsd["passed"] and bool(sch["passed"])
    return {
        "valid": valid,
        "file_type": "pdf" if is_pdf else "xml",
        "xml_extracted_from_pdf": extracted_from_pdf,
        "flavor": flavor, "profile": level,
        "checks": {"xsd": xsd, "schematron": sch, "ruleset": check},
        "summary": engine.parse_invoice_summary(xml),
    }


# ---------------- endpoints ----------------

@app.get("/", include_in_schema=False)
def index():
    return {"name": app.title, "version": VERSION, "docs": "/docs",
            "endpoints": ["/v1/invoices", "/v1/validate", "/v1/extract", "/health"]}


@app.get("/health", tags=["meta"])
def health():
    return {"ok": True, "version": VERSION}


@app.post("/v1/invoices", tags=["generate"], summary="Generate a Factur-X e-invoice from JSON")
def generate(req: GenerateRequest, request: Request):
    """Send invoice data as JSON, receive a compliant Factur-X / ZUGFeRD e-invoice.

    The engine computes all totals and the VAT breakdown itself, validates the
    result against the official EN 16931 XSD (hard gate) and schematron, and
    returns the document plus the validation report. Nothing is stored."""
    require_key(request)
    inv = req.invoice
    o = req.options
    try:
        xml, totals = engine.build_cii_xml(inv, level=o.profile)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"Could not build a valid invoice: {str(e)[:400]}")
    sch = engine.schematron_report(xml, flavor="factur-x", level=o.profile, check_option="base")
    resp = {
        "profile": o.profile,
        "totals": totals.model_dump(mode="json"),
        "validation": {"xsd": "passed",
                       "schematron": {"passed": sch["passed"], "errors": sch["errors"],
                                      "warnings": sch["warnings"]}},
        "xml_base64": base64.b64encode(xml).decode(),
    }
    if o.output == "pdf":
        try:
            visual = render_invoice_pdf(inv, totals, lang=o.language, accent=o.accent_color)
            fx_pdf = engine.embed_in_pdf(
                visual, xml, level=o.profile, lang=o.language,
                metadata={"author": inv.seller.name, "title": f"Invoice {inv.invoice_number}",
                          "subject": f"Factur-X invoice {inv.invoice_number}", "keywords": "Factur-X, invoice"})
        except Exception as e:
            raise HTTPException(500, f"PDF assembly failed: {str(e)[:300]}")
        resp["facturx_pdf_base64"] = base64.b64encode(fx_pdf).decode()
        resp["pdf_filename"] = f"{inv.invoice_number}.pdf"
    return JSONResponse(resp)


@app.post("/v1/validate", tags=["validate"], summary="Validate a Factur-X/ZUGFeRD PDF or CII XML")
def validate(req: ValidateRequest, request: Request):
    """Checks a document against the official EN 16931 XSD and schematron rules.
    'fr-ctc' additionally applies the French 2026 e-invoicing (Flux 2) ruleset."""
    require_key(request)
    raw = _b64_in(req.file_base64, "file_base64")
    return JSONResponse(_validate_payload(raw, req.check))


@app.post("/v1/extract", tags=["validate"], summary="Extract the embedded XML from a Factur-X PDF")
def extract(req: ExtractRequest, request: Request):
    require_key(request)
    raw = _b64_in(req.pdf_base64, "pdf_base64")
    if raw[:5] != b"%PDF-":
        raise HTTPException(422, "pdf_base64 does not look like a PDF file.")
    fn, xml = engine.extract_from_pdf(raw)
    if xml is None:
        raise HTTPException(404, "No embedded e-invoice XML found in this PDF.")
    flavor, level = engine.detect_flavor_level(xml)
    return JSONResponse({"filename": fn, "flavor": flavor, "profile": level,
                         "xml_base64": base64.b64encode(xml).decode(),
                         "summary": engine.parse_invoice_summary(xml)})


@app.post("/free/validate", tags=["free"], include_in_schema=False)
def free_validate(req: ValidateRequest, request: Request):
    """Backs the free web validator. IP rate-limited; not part of the paid API."""
    free_limit(request)
    raw = _b64_in(req.file_base64, "file_base64")
    if len(raw) > 3 * 1024 * 1024:
        raise HTTPException(413, "Free validator accepts files up to 3 MB.")
    return JSONResponse(_validate_payload(raw, req.check))
