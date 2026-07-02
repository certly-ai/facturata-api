"""Core engine: friendly JSON -> validated CII XML -> Factur-X PDF/A-3.

Wraps the Akretion factur-x library (official XSDs + EN16931 schematron +
French CTC schematron bundled). Deterministic; no LLM at runtime.
"""
from __future__ import annotations

import io
import logging
import re as _re
from typing import Optional

from lxml import etree

import facturx
from facturx import generate_cii_xml, generate_from_binary, get_xml_from_pdf

from mapping import Invoice, invoice_to_data_dict, Totals

logger = logging.getLogger("engine")

XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"

LEVELS = {"minimum", "basicwl", "en16931", "extended"}


def _strip_nil(xml_bytes: bytes) -> bytes:
    """Remove xsi:nil artifacts the generator leaves on empty optional elements
    (e.g. ApplicableHeaderTradeDelivery), which the official XSD rejects."""
    root = etree.fromstring(xml_bytes)
    for el in root.iter():
        if XSI_NIL in el.attrib:
            del el.attrib[XSI_NIL]
    etree.cleanup_namespaces(root)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def build_cii_xml(inv: Invoice, level: str = "en16931"):
    """Generate CII XML for the given profile and hard-validate against the XSD."""
    data_dict, totals = invoice_to_data_dict(inv)
    xml = generate_cii_xml(data_dict, level=level, check_xsd=False, check_schematron=False)
    xml = _strip_nil(xml)
    if inv.payment and inv.payment.reference:
        xml = _inject_payment_reference(xml, inv.payment.reference)
    facturx.xml_check_xsd(xml, flavor="factur-x", level=level)
    return xml, totals


def _inject_payment_reference(xml_bytes: bytes, reference: str) -> bytes:
    """The facturx lib mishandles BT-83 (PaymentReference) as a date in its CII
    branch, so we insert the text element ourselves. Per the CII schema sequence
    it belongs at the start of ApplicableHeaderTradeSettlement, before
    InvoiceCurrencyCode (only CreditorReferenceID may precede it)."""
    root = etree.fromstring(xml_bytes)
    ram = NS["ram"]
    settlement = root.find(f".//{{{ram}}}ApplicableHeaderTradeSettlement")
    if settlement is None:
        return xml_bytes
    el = etree.Element(f"{{{ram}}}PaymentReference")
    el.text = reference[:140]
    settlement.insert(0, el)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def schematron_report(xml: bytes, flavor: str = "autodetect", level: str = "autodetect",
                      check_option: str = "base") -> dict:
    """Run schematron; return severity-aware finding lists.

    {"passed": bool, "errors": [...], "warnings": [...], "skipped": reason|None}
    Warning-severity asserts do not fail the document.
    """
    try:
        facturx.xml_check_schematron(xml, flavor=flavor, level=level, check_option=check_option)
        return {"passed": True, "errors": [], "warnings": [], "skipped": None}
    except Exception as e:
        errors, warnings = _parse_schematron_message(str(e))
        return {"passed": not errors, "errors": errors, "warnings": warnings, "skipped": None}


_ENTRY_RE = _re.compile(r"^\s*\d+\.\s+", _re.M)


def _parse_schematron_message(msg: str):
    errors, warnings = [], []
    body = msg.split("errors found:", 1)[-1]
    for raw in _ENTRY_RE.split(body):
        raw = raw.strip()
        if not raw:
            continue
        location = None
        if "Error location:" in raw:
            raw, _, loc = raw.partition("Error location:")
            location = loc.strip()[:300]
        text = raw.strip()
        is_warning = "(still status warning)" in text
        text = text.replace("(still status warning)", "").strip()
        rule = None
        for candidate in _re.findall(r"\[([A-Za-z0-9._-]+)\]", text):
            if candidate.upper().startswith(("BR-", "CII-", "UBL-", "FR-", "PEPPOL-", "BT-", "BG-")):
                rule = candidate
                break
        text = _re.sub(r"^(\[[^\]]+\]\s*)+-?", "", text).strip()
        entry = {"rule": rule, "text": text[:500]}
        if location:
            entry["location"] = location
        (warnings if is_warning else errors).append(entry)
    if not errors and not warnings:
        errors = [{"rule": None, "text": msg[:1000]}]
    return errors, warnings


def xsd_report(xml: bytes, flavor: str = "autodetect", level: str = "autodetect") -> dict:
    try:
        facturx.xml_check_xsd(xml, flavor=flavor, level=level)
        return {"passed": True, "errors": []}
    except Exception as e:
        return {"passed": False, "errors": [{"rule": "XSD", "text": str(e)[:1000]}]}


def embed_in_pdf(pdf_bytes: bytes, xml: bytes, level: str = "en16931",
                 lang: Optional[str] = None, metadata: Optional[dict] = None) -> bytes:
    """Embed CII XML into the visual PDF -> Factur-X (XMP set by the lib)."""
    result = generate_from_binary(
        pdf_bytes, xml, flavor="factur-x", level=level,
        check_xsd=False, check_schematron=False,
        pdf_metadata=metadata, lang=lang or "en-US",
        afrelationship="data")
    return result


def extract_from_pdf(pdf_bytes: bytes):
    """Return (filename, xml_bytes) of the embedded e-invoice XML, or (None, None)."""
    try:
        filename, xml = get_xml_from_pdf(io.BytesIO(pdf_bytes), check_xsd=False, check_schematron=False)
        if not xml:
            return None, None
        return filename, xml
    except Exception:
        return None, None


def detect_flavor_level(xml: bytes):
    try:
        root = etree.fromstring(xml)
        flavor = facturx.get_flavor(root)
        level = facturx.get_level(root, flavor=flavor)
        return flavor, level
    except Exception:
        return None, None


NS = {"ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
      "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
      "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"}


def parse_invoice_summary(xml: bytes) -> dict:
    """Pull key fields out of a CII invoice for the validation report."""
    try:
        root = etree.fromstring(xml)

        def x(path):
            r = root.xpath(path, namespaces=NS)
            return str(r[0]).strip() if r else None

        fields = {
            "invoice_number": x("//rsm:ExchangedDocument/ram:ID/text()"),
            "issue_date": x("//rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString/text()"),
            "type_code": x("//rsm:ExchangedDocument/ram:TypeCode/text()"),
            "seller": x("//ram:SellerTradeParty/ram:Name/text()"),
            "seller_vat": x("//ram:SellerTradeParty/ram:SpecifiedTaxRegistration/ram:ID[@schemeID='VA']/text()"),
            "buyer": x("//ram:BuyerTradeParty/ram:Name/text()"),
            "currency": x("//ram:InvoiceCurrencyCode/text()"),
            "total_without_vat": x("//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:TaxBasisTotalAmount/text()"),
            "total_vat": x("//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:TaxTotalAmount/text()"),
            "total_with_vat": x("//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:GrandTotalAmount/text()"),
            "amount_due": x("//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:DuePayableAmount/text()"),
        }
        return {k: v for k, v in fields.items() if v}
    except Exception:
        return {}
