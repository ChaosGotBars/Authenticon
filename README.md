# Authenticon: Document Compliance Assessment Tool

Evidence-based document authenticity / compliance assessment system.

## Principles

- **Evidence first.** Every finding is derived from actual file content, OCR output, pixel statistics, or metadata.
- **No fabrication.** Security features, live database checks, and advanced forensic claims that cannot be performed are reported as *Unable to Verify* / *Not Performed*.
- **Conservative classification.** When image quality or evidence is insufficient the system returns **UNABLE TO DETERMINE** rather than inventing a result.
- **Transparent scoring.** The 0–100 score and confidence are produced by a deterministic combination of measurable signals (resolution, focus, OCR confidence, field-pattern presence, etc.).

## Supported document types

Driver’s License, State ID, Passport, Social Security Card, Utility Bill, Bank Statement, Bank Letterhead, Mortgage Deed, Court Document, Paystub, Payroll Check.

## Pipeline stages (actual implementations)

1. Image quality metrics (resolution, Laplacian variance / focus, contrast, exposure, noise estimate, edge density)
2. Metadata / EXIF extraction
3. OCR via Tesseract (text + word boxes + confidence)
4. Basic MRZ pattern detection (passports)
5. Heuristic field candidate extraction (dates, ID-like tokens, amounts, etc.)
6. Typography / layout observations from OCR bounding boxes
7. Basic image statistics forensics
8. Document-type specific notes and expected-field heuristics
9. Honest security-feature status matrix (Unable to Verify / Not Visible when evidence insufficient)
10. Evidence aggregation → calibrated score, classification, confidence, explanations, limitations

## Running

```bash
cd backend
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000

## API

- `GET /api/health`
- `GET /api/document-types`
- `POST /api/upload` (multipart files, optional session_id)
- `POST /api/set-types` (session_id, document_ids, types)
- `POST /api/analyze` (session_id)
- `GET /api/session/{session_id}`
- `GET /api/result/{session_id}/{document_id}`
- `DELETE /api/session/{session_id}`
- `DELETE /api/document/{session_id}/{document_id}`

## Security / privacy notes

- Uploaded files are stored under `uploads/<session_id>/` with UUID names.
- Sessions can be deleted (files removed from disk).
- No public exposure of uploaded documents.
- In a true production deployment: add authentication, encrypted storage, malware scanning (ClamAV etc.), rate limiting, retention policies, and HTTPS.

## Important limitations (also shown in every result)

- Optical security features (holograms, microprinting, OVDs, security threads, color-shifting ink, etc.) **cannot** be reliably confirmed or denied from typical consumer photos or scans. Status is therefore *Unable to Verify* or *Not Visible*.
- No live queries to DMV, SSA, passport, bank, or court databases are performed.
- No advanced copy-move, splicing, CFA, or deep residual forensics models are applied.
- This tool does **not** replace professional forensic document examination or official verification channels.

## Tested journeys

Five consecutive start-to-finish user journeys (upload → type selection → analyze → results / delete) were executed successfully with zero errors against the live server using synthetic test images.

---

## Permanent Free Public URL (Recommended)

Cloudflare quick tunnels are temporary. For a **permanent free public URL** use one of these:

### Option A — Render.com (easiest, free permanent URL)

1. Create a free account at https://render.com
2. Push this project to a **public GitHub repository** (or private if you connect GitHub to Render)
3. In Render Dashboard → **New** → **Web Service**
4. Connect the repository
5. Settings:
   - **Runtime**: Docker
   - **Dockerfile Path**: `./Dockerfile`
   - **Plan**: Free
   - **Health Check Path**: `/api/health`
6. Click **Create Web Service**

Render will build the Docker image (includes Tesseract OCR) and give you a permanent URL like:

```
https://authenticon-xxxx.onrender.com
```

**Notes on free tier:**
- The service **spins down after ~15 minutes of inactivity**. First request after sleep takes 30–60 seconds to wake.
- URL itself stays permanent.
- 750 free instance-hours/month is usually enough for light personal use.

### Option B — Fly.io

```bash
# Install flyctl, then:
fly launch
fly deploy
```

Free allowance available; gives a permanent `*.fly.dev` URL.

### Option C — Railway / Hugging Face Spaces

Both support Docker. Hugging Face Spaces is especially good if you want a permanent free space with a public URL.

---

## Local Docker (optional)

```bash
docker build -t authenticon .
docker run -p 8000:8000 authenticon
```

---

## Project structure

```
authenticon/
├── backend/          # FastAPI + analysis pipeline
├── frontend/         # UI + PWA (logo, icons, service worker)
├── Dockerfile        # Production container (Tesseract included)
├── requirements.txt
├── render.yaml
└── README.md
```
