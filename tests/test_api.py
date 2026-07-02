import base64, copy, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["RAPIDAPI_PROXY_SECRET"] = "test-secret"
os.environ["FREE_DAILY_LIMIT"] = "3"

from fastapi.testclient import TestClient
import app as appmod
from app import app

client = TestClient(app)
H = {"X-RapidAPI-Proxy-Secret": "test-secret"}

BASE = {
  "invoice_number": "2026-0042", "issue_date": "2026-07-02", "due_date": "2026-08-01",
  "delivery_date": "2026-06-30", "currency": "EUR",
  "seller": {"name": "ACME France SARL", "vat_id": "FR40303265045", "legal_id": "303265045",
             "email": "billing@acme.example",
             "address": {"line1": "12 Rue Exemple", "postcode": "75001", "city": "Paris", "country": "FR"}},
  "buyer": {"name": "Beispiel GmbH", "vat_id": "DE123456788",
            "address": {"line1": "Musterstr. 1", "postcode": "10115", "city": "Berlin", "country": "DE"}},
  "payment": {"iban": "FR7630006000011234567890189", "bic": "AGRIFRPP", "terms": "30 days",
              "reference": "INV-2026-0042"},
  "lines": [
    {"description": "Consulting", "quantity": 10, "unit": "HUR", "unit_price": "95.00", "vat_rate": "20"},
    {"description": "Licence", "quantity": 2, "unit": "C62", "unit_price": "150.00", "vat_rate": "20"}]}


def gen(payload=None, options=None):
    body = {"invoice": payload or BASE}
    if options: body["options"] = options
    return client.post("/v1/invoices", json=body, headers=H)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_auth_required():
    r = client.post("/v1/invoices", json={"invoice": BASE})
    assert r.status_code == 401


def test_generate_pdf_en16931():
    r = gen()
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["validation"]["xsd"] == "passed"
    assert j["validation"]["schematron"]["passed"] is True
    assert j["totals"]["total_gross"] == "1500.00"
    assert j["totals"]["total_vat"] == "250.00"
    pdf = base64.b64decode(j["facturx_pdf_base64"])
    assert pdf[:5] == b"%PDF-"
    assert j["pdf_filename"] == "2026-0042.pdf"


def test_generate_xml_only():
    r = gen(options={"output": "xml"})
    j = r.json()
    assert r.status_code == 200 and "facturx_pdf_base64" not in j
    xml = base64.b64decode(j["xml_base64"])
    assert b"CrossIndustryInvoice" in xml


def test_multi_rate_and_totals():
    p = copy.deepcopy(BASE)
    p["lines"].append({"description": "Books", "quantity": 3, "unit": "C62",
                       "unit_price": "10.00", "vat_rate": "5.5"})
    r = gen(p)
    j = r.json()
    assert r.status_code == 200
    assert j["totals"]["total_net"] == "1280.00"
    # 1250*0.20 + 30*0.055 = 250 + 1.65
    assert j["totals"]["total_vat"] == "251.65"
    assert len(j["totals"]["vat_breakdown"]) == 2
    assert j["validation"]["schematron"]["passed"] is True


def test_reverse_charge_ae():
    p = copy.deepcopy(BASE)
    for l in p["lines"]:
        l["vat_rate"] = "0"; l["vat_category"] = "AE"
    r = gen(p)
    j = r.json()
    assert r.status_code == 200, r.text
    assert j["totals"]["total_vat"] == "0.00"
    assert j["validation"]["schematron"]["passed"] is True, j["validation"]


def test_intracommunity_k():
    p = copy.deepcopy(BASE)
    p["ship_to"] = {"country": "DE", "city": "Berlin"}
    for l in p["lines"]:
        l["vat_rate"] = "0"; l["vat_category"] = "K"
    r = gen(p)
    assert r.status_code == 200, r.text
    assert r.json()["validation"]["schematron"]["passed"] is True


def test_k_requires_buyer_vat():
    p = copy.deepcopy(BASE)
    p["ship_to"] = {"country": "DE"}
    del p["buyer"]["vat_id"]
    for l in p["lines"]:
        l["vat_rate"] = "0"; l["vat_category"] = "K"
    r = gen(p)
    assert r.status_code == 422
    assert "buyer" in r.text


def test_s_requires_seller_vat():
    p = copy.deepcopy(BASE)
    del p["seller"]["vat_id"]
    r = gen(p)
    assert r.status_code == 422


def test_profiles_minimum_basicwl():
    for profile in ("basicwl",):
        r = gen(options={"profile": profile, "output": "xml"})
        assert r.status_code == 200, (profile, r.text)


def test_validate_roundtrip_and_frctc():
    j = gen().json()
    pdf_b64 = j["facturx_pdf_base64"]
    r = client.post("/v1/validate", json={"file_base64": pdf_b64}, headers=H)
    v = r.json()
    assert r.status_code == 200 and v["valid"] is True, v
    assert v["flavor"] == "factur-x" and v["profile"] == "en16931"
    assert v["summary"]["invoice_number"] == "2026-0042"
    # French CTC ruleset runs (result may include FR-specific findings; must not 500)
    r2 = client.post("/v1/validate", json={"file_base64": pdf_b64, "check": "fr-ctc"}, headers=H)
    assert r2.status_code == 200, r2.text
    assert "checks" in r2.json()


def test_validate_tampered_total():
    j = gen(options={"output": "xml"}).json()
    xml = base64.b64decode(j["xml_base64"]).decode()
    xml = xml.replace("<ram:GrandTotalAmount>1500.00</ram:GrandTotalAmount>",
                      "<ram:GrandTotalAmount>1400.00</ram:GrandTotalAmount>")
    r = client.post("/v1/validate", json={"file_base64": base64.b64encode(xml.encode()).decode()}, headers=H)
    v = r.json()
    assert v["valid"] is False
    rules = " ".join((e.get("rule") or "") + e["text"] for e in v["checks"]["schematron"]["errors"])
    assert "BR-CO" in rules  # arithmetic rule caught it


def test_validate_plain_pdf_clean_error():
    from reportlab.pdfgen import canvas as rc
    import io
    b = io.BytesIO(); c = rc.Canvas(b); c.drawString(100, 700, "just a pdf"); c.save()
    r = client.post("/v1/validate", json={"file_base64": base64.b64encode(b.getvalue()).decode()}, headers=H)
    v = r.json()
    assert r.status_code == 200 and v["valid"] is False
    assert "No embedded e-invoice XML" in v["error"]


def test_extract():
    j = gen().json()
    r = client.post("/v1/extract", json={"pdf_base64": j["facturx_pdf_base64"]}, headers=H)
    assert r.status_code == 200
    assert r.json()["filename"] == "factur-x.xml"


def test_free_validate_rate_limit():
    j = gen(options={"output": "xml"}).json()
    body = {"file_base64": j["xml_base64"]}
    codes = [client.post("/free/validate", json=body).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


def test_bad_base64():
    r = client.post("/v1/validate", json={"file_base64": "!!!notb64"}, headers=H)
    assert r.status_code == 422


def test_credit_note():
    p = copy.deepcopy(BASE)
    p["type_code"] = "381"
    r = gen(p)
    assert r.status_code == 200, r.text
    assert r.json()["validation"]["schematron"]["passed"] is True
