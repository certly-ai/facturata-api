"""Map friendly invoice JSON -> EN16931 BT-coded data_dict for facturx.generate_cii_xml.

All money math uses Decimal with ROUND_HALF_UP. The server computes every total
itself so user input can never produce arithmetically inconsistent invoices.
"""
from __future__ import annotations

import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

TWO = Decimal("0.01")

VAT_CATEGORIES = {"S", "Z", "E", "AE", "K", "G", "O", "L", "M"}
CATEGORIES_NEEDING_REASON = {"E", "AE", "K", "G", "O"}
DEFAULT_EXEMPTION_REASON = {
    "E": "Exempt from VAT",
    "AE": "Reverse charge: VAT liability transfers to the recipient of this invoice",
    "K": "Intra-Community supply: VAT exempt under Art. 138 Directive 2006/112/EC",
    "G": "Export outside the EU: VAT exempt under Art. 146 Directive 2006/112/EC",
    "O": "Not subject to VAT",
}

# French CTC 2026 (Flux 2) mandatory coded mentions (BR-FR-05): each French
# invoice must carry notes with subject codes PMD, PMT and AAB. Standard legal
# wording, injected for French sellers unless the caller supplies their own
# note with the same subject code (BR-FR-06 allows each code only once).
FR_MANDATORY_NOTES = {
    "PMD": "Penalites de retard : trois fois le taux d'interet legal annuel, "
           "exigibles sans qu'un rappel soit necessaire (art. L441-10 du Code de commerce).",
    "PMT": "Indemnite forfaitaire pour frais de recouvrement en cas de retard de paiement : "
           "40 EUR (art. D441-5 du Code de commerce).",
    "AAB": "Escompte pour paiement anticipe : neant.",
}


def r2(x: Decimal) -> Decimal:
    return x.quantize(TWO, rounding=ROUND_HALF_UP)


class Address(BaseModel):
    line1: Optional[str] = Field(None, examples=["12 Example Street"])
    line2: Optional[str] = None
    postcode: Optional[str] = Field(None, examples=["75001"])
    city: Optional[str] = Field(None, examples=["Paris"])
    country: str = Field(..., min_length=2, max_length=2,
                         description="ISO 3166-1 alpha-2, e.g. FR, DE, NL", examples=["FR"])
    state: Optional[str] = Field(None, description="Region/state code (BT-39/54)")

    @field_validator("country")
    @classmethod
    def upper_country(cls, v: str) -> str:
        return v.upper()


class Party(BaseModel):
    name: str = Field(..., examples=["ACME France SARL"])
    address: Address
    vat_id: Optional[str] = Field(None, description="VAT identifier incl. country prefix (BT-31/BT-48)",
                                  examples=["FR40303265045"])
    legal_id: Optional[str] = Field(None, description="Legal registration id, e.g. SIREN/SIRET/KvK/HRB (BT-30/BT-47). "
                                                      "For French parties a SIREN (9 digits) or SIRET (14 digits) also "
                                                      "derives the Flux 2 electronic address (scheme 0225).")
    email: Optional[str] = Field(None, description="Contact email")
    electronic_address: Optional[str] = Field(
        None, description="Electronic routing address (BT-34/BT-49). Defaults to the SIREN/SIRET for French parties.")
    electronic_address_scheme: Optional[str] = Field(
        None, description="Scheme of the electronic address (BT-34-1/BT-49-1), e.g. 0225 for the French "
                          "Flux 2 SIREN scheme or EM for email. Defaults to 0225 for digit-only addresses, else EM.")
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None


class Line(BaseModel):
    description: str = Field(..., examples=["Consulting services June 2026"])
    quantity: Decimal = Field(..., gt=0, examples=[10])
    unit: str = Field("C62", description="UN/ECE Rec 20 unit code. C62=piece, HUR=hour, DAY=day, KGM=kg, MTR=metre",
                      examples=["HUR"])
    unit_price: Decimal = Field(..., ge=0, description="Net price per unit (BT-146)", examples=["95.00"])
    vat_rate: Decimal = Field(..., ge=0, le=100, description="VAT percentage, e.g. 20 for 20%", examples=["20"])
    vat_category: str = Field("S", description="EN16931 VAT category: S standard, Z zero-rated, E exempt, "
                                               "AE reverse charge, K intra-community, G export, O out of scope")
    note: Optional[str] = None

    @field_validator("vat_category")
    @classmethod
    def check_cat(cls, v: str) -> str:
        v = v.upper()
        if v not in VAT_CATEGORIES:
            raise ValueError(f"vat_category must be one of {sorted(VAT_CATEGORIES)}")
        return v

    @model_validator(mode="after")
    def zero_rate_categories(self):
        if self.vat_category in ("Z", "E", "AE", "K", "G", "O") and self.vat_rate != 0:
            raise ValueError(
                f"vat_rate must be 0 when vat_category is '{self.vat_category}' "
                f"(got {self.vat_rate}). Only category 'S' carries a positive rate.")
        if self.vat_category == "S" and self.vat_rate <= 0:
            raise ValueError("vat_category 'S' (standard) requires vat_rate > 0. "
                             "Use 'Z' for zero-rated supplies.")
        return self


class Note(BaseModel):
    text: str = Field(..., min_length=1, description="Note content (BT-22)")
    subject_code: Optional[str] = Field(
        None, min_length=3, max_length=3,
        description="UNTDID 4451 subject qualifier (BT-21): PMD late-payment penalties, "
                    "PMT recovery-costs indemnity, AAB settlement discount, BAR invoicing framework, ...")

    @field_validator("subject_code")
    @classmethod
    def upper_code(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v


class Payment(BaseModel):
    iban: Optional[str] = Field(None, description="Payee IBAN (BT-84). Sets payment means to SEPA credit transfer.",
                                examples=["FR7630006000011234567890189"])
    bic: Optional[str] = None
    terms: Optional[str] = Field(None, description="Payment terms text (BT-20)", examples=["Payable within 30 days"])
    reference: Optional[str] = Field(None, description="Remittance information (BT-83)")


class ShipTo(BaseModel):
    name: Optional[str] = None
    country: str = Field(..., min_length=2, max_length=2, description="Deliver-to country (BT-80)")
    city: Optional[str] = None
    postcode: Optional[str] = None
    line1: Optional[str] = None

    @field_validator("country")
    @classmethod
    def up(cls, v: str) -> str:
        return v.upper()


class Invoice(BaseModel):
    invoice_number: str = Field(..., examples=["2026-0042"])
    issue_date: datetime.date = Field(..., examples=["2026-07-02"])
    due_date: Optional[datetime.date] = Field(None, examples=["2026-08-01"])
    delivery_date: Optional[datetime.date] = Field(
        None, description="Actual delivery / supply date (BT-72). Recommended when it differs from the issue date.")
    currency: str = Field("EUR", min_length=3, max_length=3, examples=["EUR"])
    type_code: Literal["380", "381", "384", "389", "751"] = Field(
        "380", description="380 invoice, 381 credit note, 384 corrective, 389 self-billed, 751 info only")
    seller: Party
    buyer: Party
    lines: List[Line] = Field(..., min_length=1, max_length=200)
    payment: Optional[Payment] = None
    buyer_reference: Optional[str] = Field(None, description="Buyer reference / Leitweg-ID for DE public sector (BT-10)")
    purchase_order: Optional[str] = Field(None, description="Purchase order reference (BT-13)")
    exemption_reason: Optional[str] = Field(
        None, description="Custom VAT exemption reason text (BT-120), for categories E/AE/K/G/O")
    prepaid_amount: Optional[Decimal] = Field(None, ge=0, description="Amount already paid (BT-113)")
    notes: Optional[List[Union[str, Note]]] = Field(
        None, description="Invoice notes (BG-1). Plain strings (BT-22) or objects "
                          "{text, subject_code} to also set the subject code (BT-21).")
    ship_to: Optional[ShipTo] = Field(None, description="Delivery address (BG-13). Required for intra-community supplies.")

    @field_validator("currency")
    @classmethod
    def upper_cur(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def business_rules(self):
        has_vat = any(l.vat_category == "S" for l in self.lines)
        if has_vat and not self.seller.vat_id:
            raise ValueError("seller.vat_id is required when any line uses vat_category 'S' (EN16931 BR-S-02).")
        if any(l.vat_category == "K" for l in self.lines):
            if not self.seller.vat_id or not self.buyer.vat_id:
                raise ValueError("Intra-community supply (category K) requires both seller.vat_id and buyer.vat_id.")
            if not self.ship_to:
                raise ValueError("Intra-community supply (category K) requires ship_to (deliver-to country, BT-80) "
                                 "and a delivery_date (EN16931 BR-IC-11/12).")
            if not self.delivery_date:
                raise ValueError("Intra-community supply (category K) requires delivery_date (EN16931 BR-IC-11).")
        return self


class Totals(BaseModel):
    lines_net: Decimal
    total_net: Decimal
    total_vat: Decimal
    total_gross: Decimal
    prepaid: Decimal
    amount_due: Decimal
    vat_breakdown: list


def compute_totals(inv: Invoice):
    groups: dict = {}
    lines_net = Decimal("0")
    for l in inv.lines:
        line_net = r2(l.quantity * l.unit_price)
        lines_net += line_net
        key = (l.vat_category, l.vat_rate)
        groups.setdefault(key, Decimal("0"))
        groups[key] += line_net

    vat_groups = {}
    total_vat = Decimal("0")
    for (cat, rate), basis in groups.items():
        tax = r2(basis * rate / Decimal("100")) if cat == "S" else Decimal("0.00")
        reason = None
        if cat in CATEGORIES_NEEDING_REASON:
            reason = inv.exemption_reason or DEFAULT_EXEMPTION_REASON[cat]
        vat_groups[(cat, rate)] = {"basis": r2(basis), "tax": tax, "reason": reason}
        total_vat += tax

    total_net = r2(lines_net)
    total_vat = r2(total_vat)
    total_gross = r2(total_net + total_vat)
    prepaid = r2(inv.prepaid_amount or Decimal("0"))
    amount_due = r2(total_gross - prepaid)

    totals = Totals(lines_net=total_net, total_net=total_net, total_vat=total_vat,
                    total_gross=total_gross, prepaid=prepaid, amount_due=amount_due,
                    vat_breakdown=[
                        {"category": cat, "rate": str(rate), "basis": str(v["basis"]),
                         "tax": str(v["tax"]), **({"exemption_reason": v["reason"]} if v["reason"] else {})}
                        for (cat, rate), v in sorted(vat_groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1])))
                    ])
    return totals, vat_groups


SELLER_BT = {"name": "BT-27", "vat": "BT-31", "legal": "BT-30", "eas": "BT-34",
             "addr1": "BT-35", "addr2": "BT-36", "post": "BT-38", "city": "BT-37",
             "country": "BT-40", "state": "BT-39"}
BUYER_BT = {"name": "BT-44", "vat": "BT-48", "legal": "BT-47", "eas": "BT-49",
            "addr1": "BT-50", "addr2": "BT-51", "post": "BT-53", "city": "BT-52",
            "country": "BT-55", "state": "BT-54"}


def fr_siren(party: Party):
    """(siren, full_digits) from a French party's legal_id (SIREN 9 or SIRET 14
    digits, spaces/dots tolerated), or (None, None)."""
    if party.address.country != "FR" or not party.legal_id:
        return None, None
    digits = party.legal_id.replace(" ", "").replace(".", "")
    if not digits.isdigit() or len(digits) not in (9, 14):
        return None, None
    return digits[:9], digits


def party_to_bt(party: Party, m: dict) -> dict:
    d = {m["name"]: party.name, m["country"]: party.address.country}
    if party.vat_id:
        d[m["vat"]] = party.vat_id
    siren, digits = fr_siren(party)
    if siren:
        # Normalized SIREN as legal id (BR-FR-10 wants exactly 9 digits);
        # ISO 6523 ICD 0002 = SIRENE registry. The generator only emits the
        # legal id when a scheme is present.
        d[m["legal"]] = siren
        d[m["legal"] + "-1"] = "0002"
    elif party.legal_id:
        d[m["legal"]] = party.legal_id
    # BT-34/BT-49 electronic address (URIUniversalCommunication). French CTC
    # 2026: SIREN/SIRET under scheme 0225 (BR-FR-12/13/21/23).
    eas = party.electronic_address
    if eas:
        d[m["eas"]] = eas
        d[m["eas"] + "-1"] = party.electronic_address_scheme or \
            ("0225" if eas.replace(" ", "").isdigit() else "EM")
    elif digits:
        d[m["eas"]] = digits
        d[m["eas"] + "-1"] = "0225"
    a = party.address
    if a.line1:
        d[m["addr1"]] = a.line1
    if a.line2:
        d[m["addr2"]] = a.line2
    if a.postcode:
        d[m["post"]] = a.postcode
    if a.city:
        d[m["city"]] = a.city
    if a.state:
        d[m["state"]] = a.state
    return d


def invoice_to_data_dict(inv: Invoice, level: str = "en16931"):
    totals, vat_groups = compute_totals(inv)

    d: dict = {"BT-1": inv.invoice_number, "BT-2": inv.issue_date,
               "BT-3": inv.type_code, "BT-5": inv.currency}
    if inv.due_date:
        d["BT-9"] = inv.due_date
    if inv.delivery_date:
        d["BT-72"] = inv.delivery_date
    if inv.ship_to:
        st = inv.ship_to
        d["BT-80"] = st.country
        # The generator only emits the ship-to party when it has a name (BT-70);
        # default to the buyer name so BT-80 (deliver-to country) survives.
        d["BT-70"] = st.name or inv.buyer.name
        if st.city:
            d["BT-77"] = st.city
        if st.postcode:
            d["BT-78"] = st.postcode
        if st.line1:
            d["BT-75"] = st.line1
    if inv.buyer_reference:
        d["BT-10"] = inv.buyer_reference
    if inv.purchase_order:
        d["BT-13"] = inv.purchase_order
    notes = []
    coded = set()
    for n in inv.notes or []:
        if isinstance(n, str):
            notes.append({"BT-22": n})
        else:
            e = {"BT-22": n.text}
            if n.subject_code:
                e["BT-21"] = n.subject_code
                coded.add(n.subject_code)
            notes.append(e)
    if inv.seller.address.country == "FR":
        for code, text in FR_MANDATORY_NOTES.items():
            if code not in coded:
                notes.append({"BT-21": code, "BT-22": text})
    if notes:
        d["BG-1"] = notes

    d.update(party_to_bt(inv.seller, SELLER_BT))
    d.update(party_to_bt(inv.buyer, BUYER_BT))

    if inv.seller.contact_name:
        d["BT-41"] = inv.seller.contact_name
    if inv.seller.contact_phone:
        d["BT-42"] = inv.seller.contact_phone
    if inv.seller.email:
        d["BT-43"] = inv.seller.email

    pay = inv.payment
    if pay and pay.iban:
        d["BT-81"] = "58"  # SEPA credit transfer
        d["BT-84"] = pay.iban.replace(" ", "")
        if pay.bic:
            d["BT-86"] = pay.bic
    if pay and pay.terms:
        d["BT-20"] = pay.terms

    d["BT-106"] = totals.lines_net
    d["BT-109"] = totals.total_net
    # facturx lib convention: BT-111(+ -1) = mandatory TaxTotalAmount in invoice
    # currency; BT-110 only when a different accounting currency is used.
    d["BT-111"] = totals.total_vat
    d["BT-111-1"] = inv.currency
    d["BT-112"] = totals.total_gross
    if totals.prepaid:
        d["BT-113"] = totals.prepaid
    d["BT-115"] = totals.amount_due

    d["BG-23"] = []
    for (cat, rate), v in vat_groups.items():
        g = {"BT-116": v["basis"], "BT-117": v["tax"], "BT-118": cat,
             "BT-119": format(rate, "f")}  # string so a 0 rate is still emitted (BR-48)
        if v["reason"]:
            g["BT-120"] = v["reason"]
        d["BG-23"].append(g)

    d["BG-25"] = []
    for i, l in enumerate(inv.lines, start=1):
        line_net = r2(l.quantity * l.unit_price)
        ld = {"BT-126": str(i), "BT-153": l.description[:100], "BT-129": l.quantity,
              "BT-130": l.unit, "BT-146": l.unit_price, "BT-131": line_net,
              "BT-151": l.vat_category,
              "BT-152": format(l.vat_rate, "f")}  # string so a 0 rate is still emitted (BR-AE-05 etc.)
        if l.note:
            ld["BT-127"] = l.note
        if len(l.description) > 100:
            ld["BT-154"] = l.description
        d["BG-25"].append(ld)

    return d, totals
