# SLR Paper Fetcher

Existing FastAPI application for collecting authorized research PDFs by DOI.
Input: DOI text, CSV, XLSX or XLS. Output: validated PDFs and RIS citations.

## Start

Python 3.10+; use the project environment:

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
python app.py
```

On Windows, `run_app.bat` performs installation and starts the application from
its own directory. Default address: `http://127.0.0.1:8543`.
There is no Docker or external database requirement.

The local server runs with automatic reload enabled. Keep its terminal open while
editing Python, HTML, CSS or JavaScript files; the server restarts itself when
one changes. Refresh the browser page to load updated UI templates.

Keep the current `.env`; use `.env.example` only when creating a new setup.
Provide a real contact email for Unpaywall/PMC, VNU library credentials, and
an optional OpenAlex key. Automatic downloads do not use an Elsevier API key
or institution API token. Existing Elsevier settings are retained for compatibility.
The OpenAlex key is refreshed from `.env` before calls. Restart for other settings.
Credentials never appear in the rendered page.

## Download flow

1. Normalize/validate DOI; merge duplicates and retain invalid rows.
2. Follow DOI redirects and record publisher URL/hostname in SQLite.
3. Download supplied OA links or known author copies immediately when available.
4. Use the configured VNU/OpenAthens account in the browser for other articles.
   On ScienceDirect, follow organization sign-in, select the exact VNU institution,
   and open View PDF with the authenticated web session.
5. If institutional acquisition fails, search Unpaywall, Semantic Scholar,
   OpenAlex, PMC Open Access and matching arXiv versions.

No automatic download or metadata request is sent to Elsevier's developer API.

Publisher selection follows the resolved hostname, including Elsevier's
linking hub; a DOI prefix is not sufficient evidence of the provider.
Institution access remains subject to the article's actual subscription rights.
arXiv copies may be preprints rather than the final published version.
PMC automation uses its official identifier and OA services; it does not crawl
non-OA articles or bypass embargoes.

OA file acquisition runs at most two papers concurrently. A single dedicated
browser worker handles VNU articles while OA work continues independently.
Public requests share modest spacing and respect HTTP 429 Retry-After. Failed
publisher verification triggers a ten-minute host cooldown.
Direct downloads from public PDF URLs use `cloudscraper` with a browser-style
User-Agent; API metadata and VNU browser sessions keep their existing clients.

The dedicated browser uses a persistent profile, reusing cookies, local storage,
IndexedDB and consent between articles and process restarts. Missing unexpired
SSO cookies from trusted institution domains can be restored from the private
snapshot. Publisher/challenge cookies and storage are never imported into a
different browser. Ordinary article tabs close after attempts; challenge tabs
and verified article tabs stay open, retaining their SessionStorage. The next
DOI for that publisher reuses its verified tab and the same context.
The browser closes on application exit.
The browser is selected strictly by `VNU_BROWSER_CHANNEL`; it never switches
engines during startup or recovery. Windows defaults to Microsoft Edge. Set
`VNU_BROWSER_CHANNEL=chrome` for Google Chrome or `VNU_BROWSER_CHANNEL=chromium`
for Playwright's bundled Chromium. The selected browser must be installed.
Verification always uses a visible window. After a challenge, an unexpected
shutdown requires an explicit application restart.
Edge's hidden download UI is excluded from article cleanup.
Profile data stays in `.vnu-browser-profile/<browser-channel>/`, with a snapshot
in `.vnu-browser-state.json`. It does not use the personal Edge profile. Existing
state is migrated automatically. Cookies are accepted automatically.

SSO popup windows are followed through the organization and library login steps.
PDF responses, attachments, signed PDF URLs and accessible blob viewers are
captured inside the authorized session. ScienceDirect and any challenged session
use the actual browser network stack for observed PDF URLs, not a separate HTTP
client. Guessed ScienceDirect PDF endpoint retries have been removed. Browser
User-Agent is left at its native default; public HTTP clients identify the app.
Listeners are detached before closing article tabs.
Queued browser jobs recheck publisher cooldowns immediately before opening.

Use `VNU_ALLOW_MANUAL_VERIFICATION=true` for CAPTCHA/MFA in the visible browser.
CAPTCHA sets `CAPTCHA_REQUIRED` and pauses clicks/navigation/PDF requests until
the user verifies and the destination page has loaded and remained stable for
at least 5 seconds. Manual verification has no timeout. The same tab, persistent
context and profile are retained; the app does not reload, retry the DOI or
restart the browser during verification. The current DOI continues automatically
after verification, then later DOIs reuse the same browser context. OA work can
continue independently. MFA still uses
`VNU_VERIFICATION_WAIT_SECONDS` (default 180). Application shutdown cancels a
manual wait and closes the browser gracefully, keeping its persistent profile.
When CAPTCHA appears, the app selects the live challenge tab in its browser
window. The download row offers **Hiện tab xác minh** to bring that tab forward
again while the article waits for manual verification.
The application never solves or bypasses CAPTCHA/MFA. There is no Sci-Hub integration.

## AI paper analysis

Open **AI phân tích** to queue one downloaded PDF or all eligible PDFs. The
worker sends each PDF to a GROBID service, then asks the selected provider for a
structured review draft (summary, objective, method, data/population, topic,
outcomes, findings, limitations, conclusion and review relevance). Drafts and
queue states are stored in `data/papers.sqlite3`; use **Xem draft** to review a
completed analysis. Existing drafts are skipped by **Phân tích tất cả**; an
individual **Phân tích lại** reruns that paper.
Each analysis also stores GROBID sections, source-grounded propositions, proposition
types, CEW roles, candidate relations, reconstructed warrants, an AIF I/RA/CA graph,
validation output and review decisions. Proposition extraction runs in section-aware
12,000-character chunks. Every extracted quote must match the GROBID section text;
unmatched model output is discarded. AI-generated propositions and links begin in
`pending` review status. The paper detail modal has **Phân tích**, **Mệnh đề & nguồn**
and **AIF Graph** tabs with source quotes, edit/review controls and internal JSON export.
Older drafts remain readable and are not retrofitted with invented provenance or graphs.

The pipeline uses versioned prompts: `scientific_proposition_extraction`,
`argument_role_analysis`, `warrant_reconstruction`, and
`cross_paper_relationship_analysis`. Section parsing, graph construction and graph
validation are deterministic. Research questions/objectives remain contextual and
are excluded from I-nodes; Claim and Evidence are proposition roles, not node types.
Attack candidates keep their target type (undermine, undercut or rebut). Reconstructed
warrants are separate inferred I-nodes with explanation, source proposition IDs,
confidence, model and prompt version. Structural validation does not establish the
scientific correctness of a proposition.

Reviewers can compare propositions only after approving them individually. Cross-paper
outputs are pending candidates with both source quotes and explicit application-level
labels; a lexical shortlist only selects pairs to ask the model about. The review graph
export combines approved propositions from each latest approved paper analysis with
approved cross-paper relationships. Supports and contradicts become RA and CA structures;
other labels remain explicit semantic links. This app has no Study Selection state, so
the graph cannot filter propositions to included SLR studies.

Additive SQLite tables in `paper_store.py` retain analysis runs, sections, propositions,
argument relations, AIF nodes/edges, review history, and cross-paper candidates. Existing
`paper_analyses` drafts are retained; no existing paper or review data is deleted. Export
is the app's internal AIF-compatible graph JSON, not a claim of validated interoperable
AIF/xAIF interchange conformance.

This app reuses its existing `requests` and `lxml` dependencies; no additional
Python package is required. GROBID and Ollama are separate local services and
must be running. Configure `GROBID_URL`, `OLLAMA_URL` and `OLLAMA_MODEL` in
`.env`; the default Ollama model is `qwen2.5:7b`. Ollama is selected by default.
To use Gemini, set `GEMINI_API_KEY`; choose Gemini on the analysis page. The
Gemini key is sent only in the server-side request and is never rendered in the
page. Restart the app after changing these settings.

## Progress and resume

After a DOI scan, use **Cấu hình nguồn** above the results to select publishers
of interest. **Quan tâm** and **Khác** show separate lists with counts; bulk
download and RIS export follow the selected tab. Sources are identified from
the resolved DOI destination. Other publisher hosts found during a scan can
also be selected; unresolved DOIs remain in **Khác**. The selection is saved
in the current browser's local storage and is applied to restored results.

The fetch page lists the automatic acquisition channels separately from
publishers: public PDFs via supplied links, known author copies, Unpaywall,
Semantic Scholar, OpenAlex, PMC and arXiv; and institutional access through
VNU/OpenAthens when configured. Scan results show a download route label and
can be filtered by known OA link, VNU attempt, further OA search, downloaded
PDF or invalid DOI. These labels describe the next attempt, not a guarantee
that the article is accessible. Bulk download and RIS export follow both the
publisher tab and the route filter.

The UI distinguishes processed, successfully downloaded and failed articles.
Each row shows the current stage/progress. OA byte percentage is available when
the server reports a length; browser percentages describe workflow stages.
100% is emitted only after the complete PDF is saved and catalogued.

`data/papers.sqlite3` stores DOI resolution, metadata, file ownership, states
and an event history. Select **Restore saved list / Khoi phuc danh sach** to
resume after reopening the page. Missing or incomplete PDFs are not marked as
already downloaded. A valid PDF saved just before a process crash can be
recovered from the existing DOI/file record. Duplicate filenames cannot silently
replace another DOI's file. Existing untracked PDFs are preserved.

Automatic browser downloads are captured from their own article/session. The
previous arbitrary latest-file Windows Downloads synchronization was removed.
The legacy import endpoint now requires an exact source filename and DOI,
copies a validated file, and never deletes the user's original download.

## API and storage

- `POST /api/process`: streamed scan results; accepts text or CSV/Excel uploads.
- `POST /api/download-all`: streamed row progress and final counts.
- `POST /api/auto-vnu`: unified download for a single DOI.
- `POST /api/download`: requires DOI, filename and URL; uses the same pipeline.
- `GET /api/papers[?doi=...]`: persistent states, with file validation.
- `GET /api/health`: server/catalog availability.
- `GET /api/elsevier-status`: compatibility configuration booleans; automatic
  API download is disabled and strategy is `institutional_browser`.
- `GET /api/elsevier-search?doi=...`: retired, returns 410 without contacting Elsevier.
- RIS export/download routes validate filenames within the download folder.

PDF checks require a PDF header and EOF marker, reject HTML/range fragments,
and validate declared size for ordinary direct downloads. Files are saved using
an intermediate `.part` file and atomic replacement. This is transport/file
validation, not a full PDF parser or a content-similarity verification system.

A web VNU login and developer API authorization are separate. The application
now uses the account's web access without waiting for developer API permissions.
Actual article access is verified in the browser and by a complete PDF response.
CAPTCHA/MFA still requires human verification when the publisher requests it.

## Verification

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py downloader_engine.py browser_pdf.py browser_session.py browser_verification.py paper_io.py paper_store.py doi_resolver.py institutional_auth.py http_policy.py
```

Tests include real local Playwright fixtures (browser installation required),
mocked publisher/API failures, DOI/file validation, streamed counters, session
reuse, safe DOM rendering, human verification waits and recovery after a crash.
They never send test credentials to real institutions. See `AUDIT.md` for the
observed live download/API results and remaining external limitations.

`.env`, browser profile/state, SQLite data and downloaded PDFs must stay private.
The repository historically tracks `.env`; `.gitignore` does not remove an
existing tracked file. No commit is performed by this audit. Remove it from
tracking before any future publication and review staged changes carefully.

## Official references

- [Elsevier API authentication](https://dev.elsevier.com/tecdoc_api_authentication.html)
- [Article Retrieval API](https://dev.elsevier.com/documentation/ArticleRetrievalAPI.wadl)
- [Article Metadata API](https://dev.elsevier.com/documentation/ArticleMetadataAPI.wadl)
- [ScienceDirect Search migration](https://dev.elsevier.com/tecdoc_sdsearch_migration.html)
- [PMC ID Converter API](https://pmc.ncbi.nlm.nih.gov/tools/id-converter-api/)
- [PMC OA Service](https://pmc.ncbi.nlm.nih.gov/tools/oa-service/)
