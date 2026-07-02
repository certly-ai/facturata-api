"""Visual invoice PDF via reportlab. Clean, deterministic, multilingual (en/de/fr).

The PDF is the human-readable half of a Factur-X document; engine.embed_in_pdf
adds the machine-readable XML.
"""
from __future__ import annotations

import io
from decimal import Decimal
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from mapping import Invoice, Totals

import os

_FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", os.path.join(os.path.dirname(__file__), "fonts")]
_REG, _BOLD = "Helvetica", "Helvetica-Bold"
for d in _FONT_DIRS:
    r, b = os.path.join(d, "DejaVuSans.ttf"), os.path.join(d, "DejaVuSans-Bold.ttf")
    if os.path.exists(r) and os.path.exists(b):
        pdfmetrics.registerFont(TTFont("DVS", r))
        pdfmetrics.registerFont(TTFont("DVS-Bold", b))
        _REG, _BOLD = "DVS", "DVS-Bold"
        break

L = {
    "nl": {"invoice": "FACTUUR", "credit": "CREDITNOTA", "bill_to": "Factuur aan", "from": "Afzender",
           "number": "Factuurnr.", "issued": "Factuurdatum", "due": "Vervaldatum", "delivered": "Leverdatum",
           "desc": "Omschrijving", "qty": "Aantal", "unit": "Eenheid", "price": "Stukprijs", "vat": "Btw %",
           "net": "Netto", "subtotal": "Totaal excl. btw", "vat_line": "Btw {rate}% over {basis}",
           "vat_exempt": "Btw {cat} over {basis}", "total": "Totaal incl. btw", "prepaid": "Reeds betaald",
           "due_amt": "Te betalen", "payment": "Betaling", "iban": "IBAN", "bic": "BIC", "ref": "Kenmerk",
           "terms": "Betalingsvoorwaarden", "notes": "Opmerkingen", "po": "Inkooporder", "buyer_ref": "Referentie koper",
           "vat_id": "Btw-nummer", "legal_id": "KvK/ondernemingsnr.", "page": "Pagina",
           "footer": "Factur-X / EN 16931 e-factuur: deze PDF bevat ingebedde machineleesbare XML."},
    "en": {"invoice": "INVOICE", "credit": "CREDIT NOTE", "bill_to": "Bill to", "from": "From",
           "number": "Invoice no.", "issued": "Issue date", "due": "Due date", "delivered": "Delivery date",
           "desc": "Description", "qty": "Qty", "unit": "Unit", "price": "Unit price", "vat": "VAT %",
           "net": "Net", "subtotal": "Total excl. VAT", "vat_line": "VAT {rate}% on {basis}",
           "vat_exempt": "VAT {cat} on {basis}", "total": "Total incl. VAT", "prepaid": "Prepaid",
           "due_amt": "Amount due", "payment": "Payment", "iban": "IBAN", "bic": "BIC", "ref": "Reference",
           "terms": "Terms", "notes": "Notes", "po": "Purchase order", "buyer_ref": "Buyer reference",
           "vat_id": "VAT ID", "legal_id": "Company ID", "page": "Page",
           "footer": "Factur-X / EN 16931 e-invoice: this PDF contains embedded machine-readable XML."},
    "de": {"invoice": "RECHNUNG", "credit": "GUTSCHRIFT", "bill_to": "Rechnungsempfänger", "from": "Aussteller",
           "number": "Rechnungsnr.", "issued": "Rechnungsdatum", "due": "Fällig am", "delivered": "Lieferdatum",
           "desc": "Beschreibung", "qty": "Menge", "unit": "Einheit", "price": "Einzelpreis", "vat": "USt. %",
           "net": "Netto", "subtotal": "Summe netto", "vat_line": "USt. {rate}% auf {basis}",
           "vat_exempt": "USt. {cat} auf {basis}", "total": "Gesamtbetrag brutto", "prepaid": "Bereits gezahlt",
           "due_amt": "Zahlbetrag", "payment": "Zahlung", "iban": "IBAN", "bic": "BIC", "ref": "Verwendungszweck",
           "terms": "Zahlungsbedingungen", "notes": "Hinweise", "po": "Bestellnummer", "buyer_ref": "Leitweg-ID/Referenz",
           "vat_id": "USt-IdNr.", "legal_id": "Handelsregister", "page": "Seite",
           "footer": "Factur-X / ZUGFeRD (EN 16931): Diese PDF enthält eingebettetes maschinenlesbares XML."},
    "fr": {"invoice": "FACTURE", "credit": "AVOIR", "bill_to": "Facturé à", "from": "Émetteur",
           "number": "Facture n°", "issued": "Date d'émission", "due": "Échéance", "delivered": "Date de livraison",
           "desc": "Description", "qty": "Qté", "unit": "Unité", "price": "Prix unitaire", "vat": "TVA %",
           "net": "Net", "subtotal": "Total HT", "vat_line": "TVA {rate}% sur {basis}",
           "vat_exempt": "TVA {cat} sur {basis}", "total": "Total TTC", "prepaid": "Déjà payé",
           "due_amt": "Net à payer", "payment": "Paiement", "iban": "IBAN", "bic": "BIC", "ref": "Référence",
           "terms": "Conditions", "notes": "Notes", "po": "Bon de commande", "buyer_ref": "Référence acheteur",
           "vat_id": "N° TVA", "legal_id": "SIREN/SIRET", "page": "Page",
           "footer": "Facture électronique Factur-X (EN 16931) : XML lisible par machine intégré au PDF."},
}

CUR_SYM = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF"}


def fmt_money(x: Decimal, cur: str, lang: str) -> str:
    q = f"{x:,.2f}"
    if lang in ("de", "nl"):
        q = q.replace(",", "X").replace(".", ",").replace("X", ".")
    elif lang in ("fr",):
        q = q.replace(",", " ").replace(".", ",")
    sym = CUR_SYM.get(cur)
    return f"{q} {sym or cur}"


def fmt_num(x: Decimal) -> str:
    s = format(x, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def fmt_qty(x: Decimal) -> str:
    return fmt_num(x)


def render_invoice_pdf(inv: Invoice, totals: Totals, lang: str = "en",
                       accent: str = "#14532d") -> bytes:
    t = L.get(lang, L["en"])
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    ML, MR, MT, MB = 18 * mm, 18 * mm, 16 * mm, 18 * mm
    acc = HexColor(accent)
    grey = HexColor("#5b6472")
    line_grey = HexColor("#d7dbe0")
    zebra = HexColor("#f4f6f8")

    title = t["credit"] if inv.type_code == "381" else t["invoice"]

    def header(page_no: int):
        c.setFillColor(acc)
        c.rect(0, H - 10 * mm, W, 10 * mm, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont(_BOLD, 10)
        c.drawString(ML, H - 7 * mm, inv.seller.name[:60])
        c.drawRightString(W - MR, H - 7 * mm, title)
        y = H - MT - 8 * mm
        c.setFillColor(HexColor("#111827"))
        c.setFont(_BOLD, 20)
        c.drawString(ML, y, title)
        c.setFont(_BOLD, 11)
        c.setFillColor(grey)
        c.drawString(ML, y - 6 * mm, f"{t['number']} {inv.invoice_number}")
        # meta block right
        c.setFont(_REG, 9)
        meta = [(t["issued"], inv.issue_date.isoformat())]
        if inv.delivery_date:
            meta.append((t["delivered"], inv.delivery_date.isoformat()))
        if inv.due_date:
            meta.append((t["due"], inv.due_date.isoformat()))
        if inv.purchase_order:
            meta.append((t["po"], inv.purchase_order))
        if inv.buyer_reference:
            meta.append((t["buyer_ref"], inv.buyer_reference))
        my = y
        for k, v in meta:
            c.setFillColor(grey); c.drawRightString(W - MR - 30 * mm, my, k)
            c.setFillColor(HexColor("#111827")); c.drawRightString(W - MR, my, v)
            my -= 4.6 * mm
        # addresses
        ay = y - 16 * mm
        c.setFillColor(grey); c.setFont(_BOLD, 8)
        c.drawString(ML, ay, t["from"].upper())
        c.drawString(ML + 85 * mm, ay, t["bill_to"].upper())
        c.setFont(_REG, 9); ay -= 4.5 * mm
        c.setFillColor(HexColor("#111827"))

        def party_lines(p):
            ls = [p.name]
            a = p.address
            if a.line1: ls.append(a.line1)
            if a.line2: ls.append(a.line2)
            city = " ".join(filter(None, [a.postcode, a.city]))
            if city: ls.append(city)
            ls.append(a.country)
            if p.vat_id: ls.append(f"{t['vat_id']}: {p.vat_id}")
            if p.legal_id: ls.append(f"{t['legal_id']}: {p.legal_id}")
            if p.email: ls.append(p.email)
            return ls

        sl, bl = party_lines(inv.seller), party_lines(inv.buyer)
        yy = ay
        for s in sl[:8]:
            c.drawString(ML, yy, s[:48]); yy -= 4.2 * mm
        yy = ay
        for s in bl[:8]:
            c.drawString(ML + 85 * mm, yy, s[:48]); yy -= 4.2 * mm
        table_top = min(yy, ay - len(sl) * 4.2 * mm) - 8 * mm
        return table_top

    def table_head(y):
        c.setFillColor(acc)
        c.rect(ML, y - 2 * mm, W - ML - MR, 7 * mm, stroke=0, fill=1)
        c.setFillColor(white); c.setFont(_BOLD, 8.5)
        c.drawString(ML + 2 * mm, y, t["desc"])
        c.drawRightString(ML + 102 * mm, y, t["qty"])
        c.drawString(ML + 105 * mm, y, t["unit"])
        c.drawRightString(ML + 138 * mm, y, t["price"])
        c.drawRightString(ML + 152 * mm, y, t["vat"])
        c.drawRightString(W - MR - 2 * mm, y, t["net"])
        return y - 8 * mm

    def footer(page_no):
        c.setStrokeColor(line_grey); c.setLineWidth(0.5)
        c.line(ML, MB - 4 * mm, W - MR, MB - 4 * mm)
        c.setFont(_REG, 7.2); c.setFillColor(grey)
        c.drawString(ML, MB - 8 * mm, t["footer"])
        c.drawRightString(W - MR, MB - 8 * mm, f"{t['page']} {page_no}")

    page = 1
    y = header(page)
    y = table_head(y)
    c.setFont(_REG, 9)
    from mapping import r2
    for idx, l in enumerate(inv.lines):
        if y < MB + 40 * mm:
            footer(page); c.showPage(); page += 1
            y = header(page); y = table_head(y); c.setFont(_REG, 9)
        if idx % 2 == 1:
            c.setFillColor(zebra)
            c.rect(ML, y - 1.8 * mm, W - ML - MR, 6 * mm, stroke=0, fill=1)
        c.setFillColor(HexColor("#111827"))
        desc = l.description if len(l.description) <= 52 else l.description[:51] + "…"
        c.drawString(ML + 2 * mm, y, desc)
        c.drawRightString(ML + 102 * mm, y, fmt_qty(l.quantity))
        c.drawString(ML + 105 * mm, y, l.unit)
        c.drawRightString(ML + 138 * mm, y, fmt_money(l.unit_price, inv.currency, lang))
        rate = fmt_num(l.vat_rate) if l.vat_category == "S" else l.vat_category
        c.drawRightString(ML + 152 * mm, y, rate)
        c.drawRightString(W - MR - 2 * mm, y, fmt_money(r2(l.quantity * l.unit_price), inv.currency, lang))
        y -= 6 * mm

    # totals block
    y -= 4 * mm
    if y < MB + 45 * mm:
        footer(page); c.showPage(); page += 1
        y = header(page)
    bx = ML + 95 * mm
    c.setStrokeColor(line_grey)
    c.line(bx, y + 2 * mm, W - MR, y + 2 * mm)

    def trow(label, value, bold=False, big=False):
        nonlocal y
        c.setFont(_BOLD if bold else _REG, 10.5 if big else 9)
        c.setFillColor(HexColor("#111827") if bold else grey)
        c.drawString(bx, y, label)
        c.drawRightString(W - MR - 2 * mm, y, value)
        y -= 6 * mm if big else 5 * mm

    trow(t["subtotal"], fmt_money(totals.total_net, inv.currency, lang))
    for g in totals.vat_breakdown:
        basis = fmt_money(Decimal(g["basis"]), inv.currency, lang)
        if g["category"] == "S":
            lbl = t["vat_line"].format(rate=g["rate"], basis=basis)
        else:
            lbl = t["vat_exempt"].format(cat=g["category"], basis=basis)
        trow(lbl, fmt_money(Decimal(g["tax"]), inv.currency, lang))
    c.setStrokeColor(acc); c.setLineWidth(1)
    c.line(bx, y + 3.2 * mm, W - MR, y + 3.2 * mm)
    trow(t["total"], fmt_money(totals.total_gross, inv.currency, lang), bold=True, big=True)
    if totals.prepaid:
        trow(t["prepaid"], fmt_money(-totals.prepaid, inv.currency, lang))
        trow(t["due_amt"], fmt_money(totals.amount_due, inv.currency, lang), bold=True, big=True)

    # payment + notes
    y -= 2 * mm
    pay = inv.payment
    info = []
    if pay:
        if pay.iban: info.append((t["iban"], pay.iban))
        if pay.bic: info.append((t["bic"], pay.bic))
        if pay.reference: info.append((t["ref"], pay.reference))
        if pay.terms: info.append((t["terms"], pay.terms))
    if info or inv.notes:
        c.setFillColor(grey); c.setFont(_BOLD, 8)
        c.drawString(ML, y, t["payment"].upper() if info else t["notes"].upper())
        y -= 4.5 * mm
        c.setFont(_REG, 9); c.setFillColor(HexColor("#111827"))
        for k, v in info:
            c.drawString(ML, y, f"{k}: {v}"[:95]); y -= 4.2 * mm
        if inv.notes:
            for n in inv.notes[:4]:
                c.drawString(ML, y, ("• " + n)[:100]); y -= 4.2 * mm

    footer(page)
    c.save()
    return buf.getvalue()
