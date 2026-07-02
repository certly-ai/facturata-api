# Facturata — EU e-Invoicing API (Factur-X / ZUGFeRD / EN 16931)

Generate compliant **Factur-X / ZUGFeRD** e-invoices from simple JSON, and validate
existing e-invoices against the official **EN 16931** XSD + schematron rules,
including the **French 2026 CTC (Flux 2)** ruleset.

- **Free web validator + docs:** https://facturata.com
- **Hosted API (keys, billing):** see the RapidAPI listing linked from the site
- Stateless by design: documents are processed in memory, never stored.

## Endpoints
| Method | Path | What it does |
|---|---|---|
| POST | `/v1/invoices` | Invoice JSON → Factur-X PDF (embedded XML) or raw CII XML. Totals + VAT computed server-side, official validation before output. |
| POST | `/v1/validate` | Factur-X/ZUGFeRD PDF or CII XML → XSD + schematron report (`base` or `fr-ctc`). |
| POST | `/v1/extract` | Factur-X PDF → embedded XML + parsed summary. |
| GET | `/health` | Liveness. |

Interactive docs at `/docs` (OpenAPI at `/openapi.json`).

## Run it yourself
```bash
pip install -r requirements.txt
uvicorn app:app --reload
# optional: export RAPIDAPI_PROXY_SECRET=... API_KEYS=key1,key2
python -m pytest tests/ -q
```
Built on the excellent [Akretion factur-x](https://github.com/akretion/factur-x) library
(official XSD + schematron artifacts), FastAPI and reportlab.

## Compliance notes
- Profiles: generation at `basicwl`, `en16931` (default), `extended`; validation auto-detects Factur-X/ZUGFeRD profiles incl. `minimum`.
- Visual PDF in EN/DE/FR. VAT categories S, Z, E, AE (reverse charge), K (intra-community), G, O with EN 16931 business-rule enforcement.
- Facturata performs technical processing. It does not provide legal or tax advice and does not replace a French registered PDP.

License: source-available for review and self-hosting evaluation; the hosted service is the supported product.
