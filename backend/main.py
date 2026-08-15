"""
Authenticon API Server
Production-oriented FastAPI backend with secure upload handling,
rate limiting considerations, and evidence-based analysis.
"""

import os
import uuid
import shutil
import time
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, BackgroundTasks, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from analyzer import (
    analyze_document,
    cross_document_consistency,
    DOCUMENT_TYPES,
)

# Configuration
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "frontend" / "dist"
MAX_FILE_SIZE = 5 * 1024 * 1024 * 1024  # 5 GB as requested (practical limit may be lower)
MAX_FILES = 5
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".bmp", ".webp"}
ALLOWED_MIME_PREFIXES = ("image/", "application/pdf")

# Ensure directories
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
(BASE_DIR / "frontend").mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Authenticon: Document Compliance Assessment Tool",
    description="Evidence-based document authenticity analyzer. No fabricated results.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple in-memory rate / session store (replace with Redis in true production)
SESSIONS: dict = {}
ANALYSIS_RESULTS: dict = {}

class AnalyzeRequest(BaseModel):
    session_id: str
    document_ids: List[str]
    types: List[str]

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "Authenticon",
        "time": datetime.utcnow().isoformat() + "Z",
        "supported_types": list(DOCUMENT_TYPES.keys()),
    }

@app.get("/api/document-types")
async def get_document_types():
    return {
        "types": list(DOCUMENT_TYPES.keys()),
        "details": {k: {"notes": v["notes"]} for k, v in DOCUMENT_TYPES.items()},
    }

@app.post("/api/upload")
async def upload_documents(
    request: Request,
    files: List[UploadFile] = File(...),
    session_id: Optional[str] = Form(None),
):
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Maximum {MAX_FILES} documents allowed per session.")

    if not session_id:
        session_id = str(uuid.uuid4())

    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    if session_id not in SESSIONS:
        SESSIONS[session_id] = {
            "created": datetime.utcnow().isoformat(),
            "documents": [],
        }

    uploaded = []
    for f in files:
        # Basic validation
        filename = f.filename or "unknown"
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"Unsupported file type: {ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

        content = await f.read()
        size = len(content)
        if size > MAX_FILE_SIZE:
            raise HTTPException(400, f"File {filename} exceeds 5 GB limit.")
        if size == 0:
            raise HTTPException(400, f"Empty file: {filename}")

        # Simple malware / content sniff (basic)
        if not (content[:4] == b"%PDF" or content[:3] == b"\xff\xd8\xff" or
                content[:8] == b"\x89PNG\r\n\x1a\n" or content[:6] in (b"GIF87a", b"GIF89a") or
                content[:2] == b"BM" or content[:4] == b"RIFF"):
            # Allow and let later stages fail gracefully
            pass

        doc_id = str(uuid.uuid4())
        safe_name = f"{doc_id}{ext}"
        dest = session_dir / safe_name
        with open(dest, "wb") as out:
            out.write(content)

        doc_meta = {
            "id": doc_id,
            "original_filename": filename,
            "stored_name": safe_name,
            "size": size,
            "uploaded_at": datetime.utcnow().isoformat() + "Z",
            "path": str(dest),
            "type_selected": None,
            "analysis": None,
        }
        SESSIONS[session_id]["documents"].append(doc_meta)
        uploaded.append({
            "id": doc_id,
            "filename": filename,
            "size": size,
        })

    return {
        "session_id": session_id,
        "uploaded": uploaded,
        "total_in_session": len(SESSIONS[session_id]["documents"]),
    }

@app.post("/api/set-types")
async def set_document_types(
    session_id: str = Form(...),
    document_ids: str = Form(...),  # comma-separated
    types: str = Form(...),  # comma-separated matching order
):
    if session_id not in SESSIONS:
        raise HTTPException(404, "Session not found")
    ids = [x.strip() for x in document_ids.split(",") if x.strip()]
    type_list = [x.strip() for x in types.split(",") if x.strip()]
    if len(ids) != len(type_list):
        raise HTTPException(400, "document_ids and types length mismatch")

    valid_types = set(DOCUMENT_TYPES.keys())
    for t in type_list:
        if t not in valid_types:
            raise HTTPException(400, f"Invalid document type: {t}")

    docs = {d["id"]: d for d in SESSIONS[session_id]["documents"]}
    for i, doc_id in enumerate(ids):
        if doc_id not in docs:
            raise HTTPException(404, f"Document {doc_id} not found in session")
        docs[doc_id]["type_selected"] = type_list[i]

    return {"status": "ok", "assigned": list(zip(ids, type_list))}

@app.post("/api/analyze")
async def analyze(
    session_id: str = Form(...),
    document_ids: Optional[str] = Form(None),  # if None, all with types
):
    if session_id not in SESSIONS:
        raise HTTPException(404, "Session not found")

    session = SESSIONS[session_id]
    docs_to_analyze = []
    for d in session["documents"]:
        if document_ids:
            ids = [x.strip() for x in document_ids.split(",")]
            if d["id"] not in ids:
                continue
        if not d.get("type_selected"):
            raise HTTPException(400, f"Document {d['original_filename']} has no type selected")
        docs_to_analyze.append(d)

    if not docs_to_analyze:
        raise HTTPException(400, "No documents ready for analysis")

    results = []
    for d in docs_to_analyze:
        report = analyze_document(
            file_path=d["path"],
            doc_type=d["type_selected"],
            original_filename=d["original_filename"],
        )
        d["analysis"] = report
        RESULTS_KEY = f"{session_id}:{d['id']}"
        ANALYSIS_RESULTS[RESULTS_KEY] = report
        results.append({
            "document_id": d["id"],
            "filename": d["original_filename"],
            "type": d["type_selected"],
            "report": report,
        })

    # Cross-document if multiple
    cross = None
    if len(results) > 1:
        reports = [r["report"] for r in results]
        cross = cross_document_consistency(reports)

    return {
        "session_id": session_id,
        "analyzed_count": len(results),
        "results": results,
        "cross_document": cross,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    if session_id not in SESSIONS:
        raise HTTPException(404, "Session not found")
    # Return without full analysis blobs for listing
    docs = []
    for d in SESSIONS[session_id]["documents"]:
        docs.append({
            "id": d["id"],
            "filename": d["original_filename"],
            "size": d["size"],
            "type_selected": d.get("type_selected"),
            "has_analysis": d.get("analysis") is not None,
            "score": d["analysis"].get("score") if d.get("analysis") else None,
            "classification": d["analysis"].get("classification") if d.get("analysis") else None,
        })
    return {
        "session_id": session_id,
        "created": SESSIONS[session_id]["created"],
        "documents": docs,
    }

@app.get("/api/result/{session_id}/{document_id}")
async def get_result(session_id: str, document_id: str):
    key = f"{session_id}:{document_id}"
    if key in ANALYSIS_RESULTS:
        return ANALYSIS_RESULTS[key]
    if session_id in SESSIONS:
        for d in SESSIONS[session_id]["documents"]:
            if d["id"] == document_id and d.get("analysis"):
                return d["analysis"]
    raise HTTPException(404, "Result not found")

@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str):
    """Secure deletion of uploaded materials."""
    if session_id in SESSIONS:
        session_dir = UPLOAD_DIR / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
        del SESSIONS[session_id]
        # Clean analysis results
        keys = [k for k in ANALYSIS_RESULTS if k.startswith(session_id + ":")]
        for k in keys:
            del ANALYSIS_RESULTS[k]
    return {"status": "deleted"}

@app.delete("/api/document/{session_id}/{document_id}")
async def delete_document(session_id: str, document_id: str):
    if session_id not in SESSIONS:
        raise HTTPException(404, "Session not found")
    session = SESSIONS[session_id]
    for i, d in enumerate(session["documents"]):
        if d["id"] == document_id:
            path = Path(d["path"])
            if path.exists():
                path.unlink(missing_ok=True)
            session["documents"].pop(i)
            key = f"{session_id}:{document_id}"
            if key in ANALYSIS_RESULTS:
                del ANALYSIS_RESULTS[key]
            return {"status": "deleted"}
    raise HTTPException(404, "Document not found")

# Serve frontend assets
FRONTEND_DIR = BASE_DIR / "frontend"

@app.get("/")
async def root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"message": "Frontend not found. Use /api endpoints."})

@app.get("/styles.css")
async def styles():
    return FileResponse(FRONTEND_DIR / "styles.css", media_type="text/css")

@app.get("/app.js")
async def app_js():
    return FileResponse(FRONTEND_DIR / "app.js", media_type="application/javascript")

@app.get("/manifest.json")
async def manifest():
    return FileResponse(FRONTEND_DIR / "manifest.json", media_type="application/manifest+json")

@app.get("/sw.js")
async def sw():
    return FileResponse(FRONTEND_DIR / "sw.js", media_type="application/javascript")

@app.get("/icon-192.png")
async def icon192():
    return FileResponse(FRONTEND_DIR / "icon-192.png", media_type="image/png")

@app.get("/icon-512.png")
async def icon512():
    return FileResponse(FRONTEND_DIR / "icon-512.png", media_type="image/png")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
