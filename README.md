# Autonomo Tax Helper

**English** · [Русский](README.ru.md) · [Español](README.es.md)

[![Tests](https://github.com/nikita-kotov/AutonomoTaxHelper/actions/workflows/tests.yml/badge.svg)](https://github.com/nikita-kotov/AutonomoTaxHelper/actions/workflows/tests.yml)

A local web app for Spanish *autónomos*: bookkeeping of income and expenses, **modelo 303** (IVA), **modelo 130** (IRPF), data for the **Renta (modelo 100)**, and a filing tracker with deadlines and statuses.
The server listens on `127.0.0.1` only and your data stays on your computer. The interface is available in English, Russian and Spanish.

## Contents

- [Features](#features)
- [Scope and status](#scope-and-status)
- [Install and start](#install-and-start)
- [Guides](#guides)
- [What can be configured per autónomo](#what-can-be-configured-per-autónomo)
- [Data, privacy and backups](#data-privacy-and-backups)
- [Interface languages](#interface-languages)
- [Development](#development)
- [Support the author](#support-the-author)
- [License](#license)
- [Disclaimer](#disclaimer)

## Features

| Area | What it does |
| --- | --- |
| Dashboard | Year figures, a chart by quarter (income, deductible expenses, IRPF, IVA) and an estimate of the Renta result: IRPF on the general scale minus withholdings and the quarterly payments |
| Entries | Journal of issued and received invoices; attachments (PDF and images open in the browser, other types download); invoices in any currency with ECB rates; copy an entry for monthly subscriptions; CSV import with a preview and CSV export |
| Contacts | Clients and suppliers with country, tax ID, address, email and defaults (IVA treatment, category, rates); a new entry starts from the saved contact |
| Books for the AEAT | One Excel file with the income and expense books (libros registro) in the AEAT format, checked in AEAT's validation service; Renta WEB, the modelo 130 form and Pre303 import it and fill their casillas |
| 303 | IVA devengado and deducible by casilla, reverse charge (EU and non-EU), credit notes (casillas 14–15 and 40–41), carry-forward of a negative result |
| 130 | Cumulative calculation of casillas 01–07 and 12–19, gastos de difícil justificación, withholdings, minoración |
| 115 | Optional: IRPF you withhold from a landlord (rent of business premises), per quarter: recipients, base, retentions, amount to pay |
| Renta | Income, expenses by category, net yield, optional 20% start-of-activity reduction, withholdings and paid pagos fraccionados |
| Filings | Calendar of 303, 130, 390, Renta and your own models; statuses done / not done / overdue, date, justificante, PDF; dashboard summary; restore deleted standard entries |
| AEAT files | Builds the 130 and 303 files in the official format, validates them in the AEAT service and sends them to the AEAT test environment |
| Settings and categories | Taxpayer profile, deduction rules, annual category caps, backups |

## Scope and status

**Scope.** IVA régimen general, quarterly periods, estimación directa, common territory.

**Status.** An early version. Modelo 130 and 303 files are built and can be validated in the AEAT service. Sending to the AEAT is implemented for the test environment only; it has been covered by tests but not yet tried on the live test server, and sending to the production environment is not enabled.

## Install and start

**Requirements.** Python 3.12. `run.bat` (Windows) and `run.sh` (macOS, Linux) create the virtual environment and install the packages on the first start. Signing declarations with a certificate (the Submission page) works on Windows only.

Double-click `run.bat`: the app starts in the background with an icon in the system tray (no console window) and opens in your browser at <http://127.0.0.1:5057/>. Double-click the icon to open the page again; its right-click menu also opens the data folder and stops the app. **Exit** in the page's top bar stops it too. If the app is already running, `run.bat` just opens the page. Start-up problems are written to `app.log` in the data folder.

On macOS and Linux run `./run.sh` in a terminal (or `sh run.sh`): the app runs in that console, opens the same address in your browser and stops with **Exit** in the page or Ctrl+C. It has been tested on Windows.

First steps in the app: **Settings** → NIF/NIE, surname and first name (exactly as in the AEAT census), activity start date, payment method, home-office percentage from modelo 036 → **Entries** → your first expense.

## Guides

### Entering amounts

Type amounts the way your interface language writes them: `1.234,56` (Russian, Spanish) or `1,234.56` (English). If a single separator is followed by exactly three digits (`1.500`), it is read as a thousands separator when it differs from your language's decimal sign: in the Spanish interface `1.500` means 1,500 €, and `1,5` means 1.50 €. Two different separators are unambiguous: the last one is the decimal sign.

### Clients and suppliers

**Contacts** keeps the companies and people you invoice or buy from: name, country, tax ID, address, email and the values a new entry should start from (IVA treatment, category, IVA rate, IRPF withholding, description). In the entry form, typing a saved name fills those fields in, except the ones you have already changed by hand. A new entry can save its party as a contact with one tick, **Add from entries** creates a contact for every name already in the ledger, and CSV import takes blank cells from the saved contact of the same name.

### Importing entries from CSV

**Entries → Import CSV** takes a file with one row per invoice and shows a preview before anything is saved: each row is marked ready, duplicate (same date, party, number and amount as an existing entry or an earlier row) or error with the reason, and you tick the rows to import. Rows are checked by the same rules as the entry form, the import is all-or-nothing, and foreign-currency rows get the ECB rate of the invoice date.

Required columns are `date` and `base` (or `orig_amount` with `currency`); the others are optional and blank cells take the defaults from Settings: `kind` (expense or income), `number`, `counterparty`, `country`, `vat_id`, `description`, `category` (code or name, required for expenses), `regime`, `vat_rate`, `vat_amount`, `withholding_rate`, `notes`. Dates are `YYYY-MM-DD` or `DD/MM/YYYY`, amounts are written like `1234.56`, the separator is `;`, `,` or a tab. A template can be downloaded on the import page, and the app's own CSV export can be read back.

### Invoices in foreign currency

For an invoice currency other than EUR the entry form shows "Amount in invoice currency" and "Rate (units per 1 EUR)". The euro base is amount ÷ rate rounded to the cent and feeds every tax calculation. You can correct it by hand: if it differs from the conversion, the app warns and keeps what you entered.

- **Rate source.** The button "Get ECB rate for the invoice date" (also run automatically when you change currency or date) fills in the ECB reference rate. For weekends and holidays the last published day is used and its date is saved. Rates found are cached in the database. In manual mode (Settings) you enter the rate yourself.
- **What is stored:** currency, amount in currency, rate, rate date and source (`ecb` or `manual`); all visible in the entries list and in the CSV.
- **Basis.** For IVA (art. 79 LIVA) the base is converted at the Banco de España selling rate on the date of devengo, usually the invoice date. Banco de España publishes its rate based on the ECB reference rate, so figures normally match.

### Home office and subscriptions

- **AI and SaaS subscriptions** use the "Software and subscriptions" category, 100% an IRPF expense. IVA depends on the invoice regime:
  - Spanish supplier with IVA → "Spanish supplier", IVA is deducted;
  - EU supplier without IVA → "EU reverse charge": 303 casillas 10/11 and 36/37, and modelo 349 is also due;
  - non-EU supplier without IVA → "non-EU reverse charge": casillas 12/13 and 28/29;
  - foreign VAT on the invoice → "No IVA": the whole amount goes into base and expense.
- **The affected percentage** is entered exactly as in modelo 036 (casilla 423, for example 11%); the dwelling area (casilla 422) is stored for reference.
- **IRPF rules are chosen independently for two groups** (Settings → Home office; the 30% multiplier is shared by default):
  - supplies (electricity, gas, water, internet): by default cost × % × 30% (art. 30.2.5ª LIRPF); the "% only" option suits cases where a higher share can be proven;
  - rent, IBI, community fees, home insurance: by default cost × % (the percentage from modelo 036), what the law allows; the "cost × % × 30%" option is the cautious approach many gestors take.
- A category belongs to a group by its IRPF mode; custom categories can be placed in either group.
- **IVA on supplies** is a separate setting: by default IVA × affected %, with an "IVA × affected % × 30%" option.
- Non-recoverable IVA is included in the IRPF expense.

### Expense categories

Every expense belongs to a category, and the category decides how the expense reaches the tax figures: the IRPF rule (in full, home supplies, home rent and IBI, or not deductible), the IVA treatment, an optional annual deduction cap (for example health insurance, 500 € per person) and the expense concept in the AEAT books. The standard categories cover the usual expenses of an autónomo, so most people never change them. Open **Settings → Expense categories** to set a cap, change a rule or add your own category: it gets its own line in the Renta breakdown and is reported in the AEAT books as another deductible expense.

### AEAT files and validation

The **303** and **130** pages have a card **"AEAT file and validation"**:

1. The app builds the fixed-width file following the official *diseño de registro* (130: "Ejercicios 2019 y siguientes", 303: "Ejercicio 2026 y siguientes").
2. The button "Validate the file in the AEAT service" sends it to `ServValiDos`. The reply is a PDF with the pre-filled form or a list of errors with codes.
3. "Download file" gives `NIF-year-period.130` or `.303` for upload at the Sede.

The validation result is tied to the file content: after your data changes the card marks it as outdated. Validation needs NIF/NIE, surname and first name in Settings exactly as in the AEAT census.

The 303 file contains pages 01 (régimen general), 03 (additional information and result) and DID (bank account).

### Books for Renta WEB, modelo 130 and Pre303

**Books** builds the unified books of IVA and IRPF as one `.xlsx` file in the format published by the AEAT (sheets `EXPEDIDAS_INGRESOS` and `RECIBIDAS_GASTOS`, file name `<year><NIF>T<name>.xlsx`). The same file is imported by:

- **Renta WEB**: Rendimientos de actividades económicas en estimación directa → *Importar Libros Registro* (the whole year, 4T). It fills the income, the deductible expenses and the withholdings.
- **Modelo 130** and **Pre303 (303)**: the form's *Importar Libros Registro*, with the entries from 1 January to the end of the quarter (choose the quarter on the page).

The button **Validate the file in the AEAT service** sends the file to AEAT's books validator and shows its verdict and the periods it accepts. Before the first use, fill in the activity key and the IAE group or epigraph in **Settings** (the epigraph without the section prefix: `763`, not `2*763`).

How entries become book lines: the deductible expense is the amount the app already uses for the 130 (home-office shares and category caps included); each category maps to an AEAT expense concept (for example professional services `G19`, social security of the owner `G45`, rent `G12`, electricity `G14`); reverse-charge purchases carry the self-assessed IVA; expenses without IVA (social security, rent receipts) are accounting vouchers (`F6`); an invoice whose IVA covers only part of it becomes two lines. The page lists what to complete before importing, for example a missing invoice number or the tax ID of a foreign supplier, which the AEAT requires.

### Submission to the AEAT (test environment)

Unlike the validation above, which only checks a file and needs no certificate, **Submission** sends the declaration itself. It is available for the AEAT **test environment** only (`prewww1.aeat.es`), where nothing has legal effect.

- **Certificate.** Windows only. Pick your digital certificate from the Windows store on the Submission page; Windows signs the connection (and may ask for the PIN). The certificate holder must be the taxpayer of the settings.
- **Gates.** The file must have been validated in the AEAT service in its current form, the certificate must be valid, and a review screen shows the figures and asks for an explicit confirmation. A declaration with a payment asks for the NRC of the payment.
- **Result.** The answer (secure verification code, receipt number, warnings or errors) and the receipt PDF, when AEAT provides it, are kept in the list of sent declarations.
- **Test environment rules.** It answers on working days from 8:00 to 15:00 (Spain); after a successful test of a period, further tests for that period must be complementary declarations, so use another period for the next test.

## What can be configured per autónomo

| Where | Parameter |
| --- | --- |
| Tax profile | activity start date, IRPF method (simplificada / normal), modelo 130 rate (20%; 8% for Ceuta and Melilla), IVA registration, tracked models, own holidays |
| Taxpayer | NIF/NIE, surname and first name, payment method (bank + NRC, or direct debit + IBAN; with direct debit the deadlines are earlier) |
| Defaults | IVA rate and withholding for new entries; currency rate mode |
| Home office | affected percentage, dwelling area, multiplier, rules for supplies and rent, IVA mode for supplies |
| IRPF | difícil justificación (percentage and cap), previous-year income for the minoración, optional 20% reduction for starting an activity, personal minimum and regional scale for the Renta estimate, IVA balance to compensate |
| Settings → Expense categories | IRPF and IVA modes, annual deduction cap (for example health insurance 500 € per person), names, notes, custom categories |
| Filings | any deadline can be edited by hand and is not recalculated; custom models |

## Data, privacy and backups

### Where the data lives

The database and documents (`taxhelper.db`, `documents/`) contain financial records, your NIF and invoices, so they are kept in your user profile:

- **Location:** Windows `%LOCALAPPDATA%\TaxHelper`, macOS `~/Library/Application Support/TaxHelper`, Linux `~/.local/share/taxhelper`. Set the `TAXHELPER_DATA` environment variable to choose another place.
- Nothing leaves your computer except what you choose to send: backups go to the folder you pick, and the validation and submission buttons send the generated file to the AEAT.

### Backups

1. **Settings** → **Backup** → a folder of your choice, for example `D:\Backups\TaxHelper`.
2. The app writes `taxhelper-backup-YYYYMMDD-HHMM.zip` (database and documents) there on start and after data changes, at most once every 6 hours; after a failed attempt the next one waits at least 15 minutes. The 30 latest copies and the first copy of each month are kept.
3. Manual backup: "Back up now" or the "Download backup" link at the bottom of the page. A failed automatic backup is shown on the dashboard.

**Restore:** **Settings** → **Restore from a backup**: choose a zip file or a backup from the backup folder. The zip is checked first, and the current data is saved as a zip in the `before-restore` folder inside the data folder (the last three are kept); then the database and the documents are replaced.

## Interface languages

English, Russian, Spanish. Pick the language in the header or **Settings → Language** ("automatic" follows the browser language, with English as the fallback). Date and money formats follow the language: `1 234,56 €`, `€1,234.56`, `1.234,56 €`.

## Development

Install the packages with `pip install -r requirements-dev.txt` and run the tests with `pytest` and the type check with `mypy`; the tests use their own empty data folder. The application is the `taxhelper` package: `calc.py` holds the tax rules, `aeat/` the AEAT files and submission, `web/` the Flask views and forms; all interface texts are in `locales/`. Bug reports and pull requests are welcome.

## Support the author

If the app saves you time, you can say thanks with a voluntary payment: **[Support the author](https://buy.stripe.com/28E9ATgho9Z98Hqft51wY00)** (the same link is in the banner at the top of every page).

## License

Copyright (C) 2026 Nikita Kotov. Autonomo Tax Helper is free software under the **GNU Affero General Public License v3.0 or later** (see [LICENSE](LICENSE)): you may use, study, change and share it. If you distribute a modified version, or let others use it as a network service, you must give them the source code under the same license.

## Disclaimer

The software is provided **as is, without warranty of any kind** (see [LICENSE](LICENSE)). It is not tax or legal advice, and the author is not liable for errors in the figures, files or declarations produced with it. You are responsible for the data you enter and for what you file: check the final figures with your gestor or with the AEAT pre-fill before filing.
