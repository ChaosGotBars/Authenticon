"""
Authenticon Document Analysis Pipeline
Evidence-based analysis using available tools. No fabricated results.
All findings derived from actual file content, OCR, image metrics, metadata.
Limitations clearly reported.
"""

import os
import io
import hashlib
import json
import re
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

import numpy as np
from PIL import Image, ExifTags, ImageStat, ImageFilter
import pytesseract
from pytesseract import Output
import cv2

# Optional PDF
try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

try:
    from pdf2image import convert_from_path, convert_from_bytes
    HAS_PDF2IMAGE = True
except ImportError:
    HAS_PDF2IMAGE = False

# Supported document types and expected patterns (public knowledge based)
DOCUMENT_TYPES = {
    "Driver's License": {
        "aliases": ["drivers license", "driver license", "dl", "license"],
        "expected_fields": ["name", "address", "dob", "license_number", "expiration", "state"],
        "mrz_possible": False,
        "barcode_possible": True,
        "notes": "US state-issued. Layout and security features vary significantly by state and year."
    },
    "State ID": {
        "aliases": ["state id", "identification card", "id card"],
        "expected_fields": ["name", "address", "dob", "id_number", "expiration", "state"],
        "mrz_possible": False,
        "barcode_possible": True,
        "notes": "Similar to DL but non-driving."
    },
    "Passport": {
        "aliases": ["passport", "travel document"],
        "expected_fields": ["name", "nationality", "passport_number", "dob", "expiration", "sex", "place_of_birth"],
        "mrz_possible": True,
        "barcode_possible": False,
        "notes": "ICAO 9303 compliant. MRZ is primary machine-readable zone."
    },
    "Social Security Card": {
        "aliases": ["ssn", "social security", "ss card"],
        "expected_fields": ["name", "ssn"],
        "mrz_possible": False,
        "barcode_possible": False,
        "notes": "US SSA. Official cards have specific security features; many presented are copies or older versions."
    },
    "Utility Bill": {
        "aliases": ["utility", "electric bill", "gas bill", "water bill"],
        "expected_fields": ["name", "address", "account_number", "billing_date", "amount", "provider"],
        "mrz_possible": False,
        "barcode_possible": True,
        "notes": "Varies widely by provider. Check for consistency of dates, amounts, provider logos/styles."
    },
    "Bank Statement": {
        "aliases": ["bank statement", "account statement"],
        "expected_fields": ["name", "address", "account_number", "statement_period", "bank_name", "balances"],
        "mrz_possible": False,
        "barcode_possible": False,
        "notes": "Institution-specific. Look for official branding, consistent formatting, transaction logic."
    },
    "Bank Letterhead": {
        "aliases": ["bank letter", "bank verification letter"],
        "expected_fields": ["bank_name", "name", "account_number", "date", "signature_or_officer"],
        "mrz_possible": False,
        "barcode_possible": False,
        "notes": "Often used for verification. Check letterhead authenticity indicators if visible."
    },
    "Mortgage Deed": {
        "aliases": ["deed", "mortgage", "property deed"],
        "expected_fields": ["parties", "property_description", "date", "recording_info", "signatures", "notary"],
        "mrz_possible": False,
        "barcode_possible": False,
        "notes": "Legal document. Recording stamps, notary seals, legal descriptions important."
    },
    "Court Document": {
        "aliases": ["court order", "judgment", "court filing"],
        "expected_fields": ["court_name", "case_number", "parties", "date", "judge_or_clerk", "seal"],
        "mrz_possible": False,
        "barcode_possible": False,
        "notes": "Official seals, case numbers, formatting vary by jurisdiction."
    },
    "Paystub": {
        "aliases": ["pay stub", "paycheck stub", "earnings statement"],
        "expected_fields": ["employee_name", "employer", "pay_period", "gross_pay", "net_pay", "deductions", "ytd"],
        "mrz_possible": False,
        "barcode_possible": False,
        "notes": "Employer-specific. Math consistency of pay calculations is a key check."
    },
    "Payroll Check": {
        "aliases": ["paycheck", "payroll check", "cheque"],
        "expected_fields": ["payee", "amount", "date", "employer", "check_number", "signature"],
        "mrz_possible": False,
        "barcode_possible": True,
        "notes": "MICR line often present. Check amount consistency (numeric vs written)."
    },
}

def compute_file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def extract_exif(img: Image.Image) -> Dict[str, Any]:
    meta = {}
    try:
        exif = img._getexif()
        if exif:
            for tag_id, value in exif.items():
                tag = ExifTags.TAGS.get(tag_id, tag_id)
                if isinstance(value, bytes):
                    try:
                        value = value.decode("utf-8", errors="ignore")
                    except Exception:
                        value = str(value)[:100]
                meta[str(tag)] = str(value)[:200]
    except Exception as e:
        meta["exif_error"] = str(e)
    return meta

def image_quality_metrics(img: Image.Image) -> Dict[str, Any]:
    """Compute objective image quality metrics from the actual pixels."""
    metrics = {}
    try:
        # Convert to RGB if needed
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        arr = np.array(img)
        h, w = arr.shape[:2]
        metrics["width"] = int(w)
        metrics["height"] = int(h)
        metrics["megapixels"] = round((w * h) / 1_000_000, 2)
        metrics["aspect_ratio"] = round(w / h, 3) if h else 0

        # Grayscale for some metrics
        if len(arr.shape) == 3:
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        else:
            gray = arr

        # Blur / focus: Laplacian variance
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        metrics["laplacian_variance"] = round(lap_var, 2)
        if lap_var < 50:
            metrics["focus_assessment"] = "Poor (likely blurry)"
        elif lap_var < 150:
            metrics["focus_assessment"] = "Moderate"
        else:
            metrics["focus_assessment"] = "Good"

        # Contrast
        metrics["contrast_std"] = round(float(np.std(gray)), 2)
        if metrics["contrast_std"] < 30:
            metrics["contrast_assessment"] = "Low contrast"
        elif metrics["contrast_std"] > 80:
            metrics["contrast_assessment"] = "High contrast"
        else:
            metrics["contrast_assessment"] = "Adequate"

        # Brightness
        metrics["mean_brightness"] = round(float(np.mean(gray)), 2)
        if metrics["mean_brightness"] < 40:
            metrics["exposure_assessment"] = "Underexposed"
        elif metrics["mean_brightness"] > 220:
            metrics["exposure_assessment"] = "Overexposed"
        else:
            metrics["exposure_assessment"] = "Acceptable"

        # Simple noise estimate (high-frequency energy)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        noise = np.abs(gray.astype(float) - blurred.astype(float))
        metrics["noise_estimate"] = round(float(np.mean(noise)), 2)

        # Edge density (rough proxy for detail / print quality)
        edges = cv2.Canny(gray, 50, 150)
        metrics["edge_density"] = round(float(np.mean(edges > 0)), 4)

        # Compression / JPEG artifact proxy is hard without original; skip claim
        metrics["notes"] = "Metrics derived solely from pixel statistics of the uploaded image. Perspective distortion, glare, and partial obstruction not fully quantified here."
    except Exception as e:
        metrics["error"] = str(e)
    return metrics

def run_ocr(img: Image.Image) -> Dict[str, Any]:
    """Perform OCR with Tesseract. Return text, confidence, boxes."""
    result = {
        "full_text": "",
        "mean_confidence": 0.0,
        "word_count": 0,
        "words": [],
        "raw_data": None,
        "error": None,
    }
    try:
        # Use image_to_data for boxes and confidences
        data = pytesseract.image_to_data(img, output_type=Output.DICT, config="--psm 6")
        texts = []
        confs = []
        words = []
        n = len(data["text"])
        for i in range(n):
            conf = int(data["conf"][i])
            text = data["text"][i].strip()
            if conf > 0 and text:
                texts.append(text)
                confs.append(conf)
                words.append({
                    "text": text,
                    "conf": conf,
                    "left": data["left"][i],
                    "top": data["top"][i],
                    "width": data["width"][i],
                    "height": data["height"][i],
                })
        result["full_text"] = " ".join(texts)
        result["word_count"] = len(words)
        result["mean_confidence"] = round(float(np.mean(confs)) if confs else 0.0, 1)
        result["words"] = words[:200]  # limit size
        # Also get full string with better config for some docs
        result["full_text_alt"] = pytesseract.image_to_string(img, config="--psm 4")
    except Exception as e:
        result["error"] = str(e)
    return result

def parse_mrz(text: str) -> Dict[str, Any]:
    """Basic MRZ detection and partial parse for passports (TD3)."""
    mrz = {"detected": False, "lines": [], "parsed": {}, "notes": ""}
    # Look for typical MRZ pattern: lines of 44 chars with < and alphanum
    lines = re.findall(r"[A-Z0-9<]{30,44}", text.upper().replace(" ", ""))
    if len(lines) >= 2:
        mrz["detected"] = True
        mrz["lines"] = lines[:3]
        # Very basic TD3 parse (passport)
        if len(lines[0]) >= 44 and lines[0].startswith("P"):
            try:
                mrz["parsed"]["document_type"] = lines[0][0]
                mrz["parsed"]["country"] = lines[0][2:5].replace("<", "")
                names = lines[0][5:].split("<<")
                mrz["parsed"]["surname"] = names[0].replace("<", " ").strip() if names else ""
                if len(names) > 1:
                    mrz["parsed"]["given_names"] = names[1].replace("<", " ").strip()
            except Exception:
                pass
        mrz["notes"] = "MRZ-like strings detected via OCR. Full cryptographic validation of check digits not performed in this environment. Visual MRZ quality depends on image resolution and focus."
    else:
        mrz["notes"] = "No clear MRZ pattern found in OCR text."
    return mrz

def basic_field_extraction(text: str, doc_type: str) -> Dict[str, Any]:
    """Heuristic extraction of common fields using regex. Evidence-based only."""
    fields = {}
    text_lower = text.lower()
    # Dates
    date_patterns = [
        r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
        r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b",
        r"\b(\d{4}-\d{2}-\d{2})\b",
    ]
    dates = []
    for p in date_patterns:
        dates.extend(re.findall(p, text, re.IGNORECASE))
    fields["possible_dates"] = list(set(dates))[:10]

    # SSN-like
    ssn = re.findall(r"\b\d{3}-\d{2}-\d{4}\b", text)
    if ssn:
        fields["possible_ssn_pattern"] = ssn[:3]

    # License / ID number heuristics (very loose)
    id_like = re.findall(r"\b[A-Z0-9]{6,15}\b", text.upper())
    fields["possible_id_like"] = list(set(id_like))[:8]

    # Amounts (for paystubs, checks, statements)
    amounts = re.findall(r"\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?", text)
    fields["possible_amounts"] = list(set(amounts))[:10]

    # Email / phone rough
    emails = re.findall(r"[\w\.-]+@[\w\.-]+\.\w+", text)
    phones = re.findall(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", text)
    if emails:
        fields["emails"] = emails[:3]
    if phones:
        fields["phones"] = phones[:3]

    fields["extraction_notes"] = "Field candidates derived solely from OCR text patterns. No external database lookup performed. False positives common."
    return fields

def assess_typography_and_layout(ocr_data: Dict, quality: Dict) -> Dict[str, Any]:
    """Basic typography/layout observations from OCR boxes."""
    findings = {
        "observations": [],
        "suspicious": [],
        "notes": "Limited to OCR bounding-box statistics. True font identification, kerning, and micro-typography require higher-resolution specialized analysis not available here."
    }
    words = ocr_data.get("words", [])
    if not words:
        findings["observations"].append("Insufficient OCR word data for layout analysis.")
        return findings

    heights = [w["height"] for w in words if w["height"] > 0]
    if heights:
        mean_h = np.mean(heights)
        std_h = np.std(heights)
        findings["mean_word_height_px"] = round(float(mean_h), 1)
        findings["word_height_std"] = round(float(std_h), 1)
        if std_h > mean_h * 0.6 and len(heights) > 10:
            findings["suspicious"].append("High variance in word heights may indicate mixed fonts, editing, or multi-source composition.")
        else:
            findings["observations"].append("Word height variance within moderate range for the detected text.")

    # Simple left-alignment check for first column-ish
    lefts = [w["left"] for w in words]
    if lefts:
        findings["left_margin_approx"] = int(min(lefts))
    findings["observations"].append(f"Detected {len(words)} word-level OCR regions.")
    return findings

def image_forensics_basic(img: Image.Image, quality: Dict) -> Dict[str, Any]:
    """Basic pixel-level observations. No claims of advanced splicing detection."""
    findings = {
        "observations": [],
        "suspicious": [],
        "notes": "Advanced copy-move, splicing, resampling, and CFA analysis require specialized forensic toolkits and often high-resolution originals. Results here are limited to statistical summaries of the provided image."
    }
    try:
        arr = np.array(img.convert("RGB"))
        # Channel correlation (simple)
        if arr.shape[2] == 3:
            r, g, b = arr[:,:,0].astype(float), arr[:,:,1].astype(float), arr[:,:,2].astype(float)
            corr_rg = np.corrcoef(r.flatten(), g.flatten())[0,1]
            findings["channel_corr_rg"] = round(float(corr_rg), 4)
        findings["observations"].append(f"Image dimensions: {quality.get('width')}x{quality.get('height')}")
        findings["observations"].append(f"Focus (Laplacian var): {quality.get('laplacian_variance')} — {quality.get('focus_assessment')}")
        if quality.get("laplacian_variance", 0) < 40:
            findings["suspicious"].append("Very low Laplacian variance suggests significant blur; fine security features and text edges may be unreliable.")
        if quality.get("noise_estimate", 0) > 15:
            findings["observations"].append("Elevated high-frequency energy (possible noise or fine texture).")
    except Exception as e:
        findings["error"] = str(e)
    return findings

def security_features_assessment(doc_type: str, quality: Dict, ocr: Dict) -> List[Dict[str, str]]:
    """Honest assessment: most features cannot be verified from typical user uploads."""
    results = []
    common = [
        ("Watermark / Guilloche / Security Background", "Unable to Verify"),
        ("Microprinting", "Unable to Verify"),
        ("Hologram / Optically Variable Device", "Unable to Verify"),
        ("Security Thread / Embossing", "Unable to Verify"),
        ("Color-shifting Ink", "Unable to Verify"),
        ("Ghost Image / Secondary Photo", "Unable to Verify"),
        ("Fine-line Printing", "Unable to Verify"),
        ("Official Seal / Emblem", "Unable to Verify"),
        ("Signature (handwritten)", "Unable to Verify"),
        ("Barcode / PDF417 / QR", "Unable to Verify"),
    ]
    # Override a few based on actual evidence
    text = (ocr.get("full_text") or "") + " " + (ocr.get("full_text_alt") or "")
    if re.search(r"\b(barcode|qr|pdf417)\b", text, re.I) or "barcode" in text.lower():
        pass  # still unable without decoder
    if quality.get("megapixels", 0) < 1.5:
        for i, (name, status) in enumerate(common):
            common[i] = (name, "Not Visible / Insufficient Resolution")
    # For passport, note MRZ
    if doc_type == "Passport":
        common.append(("Machine Readable Zone (MRZ)", "See dedicated MRZ analysis"))

    for name, status in common:
        results.append({
            "feature": name,
            "status": status,
            "explanation": "Detection of optical security features from consumer-grade photos or scans is unreliable. High-resolution specialized imaging under controlled lighting is typically required. Status reflects inability to positively confirm presence or absence from the provided evidence."
        })
    return results

def score_document(
    quality: Dict,
    ocr: Dict,
    fields: Dict,
    typography: Dict,
    forensics: Dict,
    mrz: Dict,
    doc_type: str,
    file_meta: Dict,
) -> Dict[str, Any]:
    """
    Calibrated scoring based ONLY on measurable evidence available in this pipeline.
    No random numbers. Low scores or UNABLE when evidence insufficient.
    """
    score = 50.0  # neutral start
    confidence = 0.3
    reasons = []
    indicators_auth = []
    indicators_susp = []
    limitations = []

    # 1. Image quality gate
    mp = quality.get("megapixels", 0)
    lap = quality.get("laplacian_variance", 0)
    if mp < 0.3 or lap < 25:
        score = 20
        confidence = 0.15
        reasons.append("Image quality too low (resolution or focus) for reliable authenticity assessment. Score reduced; classification leans UNABLE TO DETERMINE.")
        limitations.append("Insufficient image quality for fine-detail analysis.")
        classification = "UNABLE TO DETERMINE"
        return _finalize(score, classification, confidence, reasons, indicators_auth, indicators_susp, limitations)

    if lap < 80:
        score -= 10
        confidence -= 0.05
        reasons.append(f"Moderate blur (Laplacian {lap}). Fine features and text edges less reliable.")
        indicators_susp.append("Reduced focus quality")
    else:
        score += 5
        indicators_auth.append("Acceptable focus for basic text analysis")

    if mp >= 2.0:
        score += 5
        indicators_auth.append("Adequate resolution (≥2 MP)")
    elif mp < 1.0:
        score -= 8
        indicators_susp.append("Low resolution (<1 MP)")

    # 2. OCR success
    mean_conf = ocr.get("mean_confidence", 0)
    word_count = ocr.get("word_count", 0)
    if word_count < 5:
        score -= 15
        confidence -= 0.1
        reasons.append("Very little text successfully extracted. Document may be blank, heavily obstructed, or non-textual.")
        limitations.append("Minimal OCR output.")
    elif mean_conf >= 70 and word_count >= 15:
        score += 12
        confidence += 0.15
        indicators_auth.append(f"OCR mean confidence {mean_conf}% with {word_count} words")
        reasons.append(f"OCR produced usable text (mean conf {mean_conf}%).")
    elif mean_conf < 40:
        score -= 10
        indicators_susp.append(f"Low OCR confidence ({mean_conf}%)")
        reasons.append("Low OCR confidence may indicate poor print quality, heavy compression, or non-standard fonts.")

    # 3. Document-type specific
    if doc_type == "Passport" and mrz.get("detected"):
        score += 10
        confidence += 0.1
        indicators_auth.append("MRZ-like pattern detected in OCR")
        reasons.append("Passport-type document shows MRZ-like strings, consistent with ICAO expectations (full check-digit validation not performed).")
    elif doc_type == "Passport" and not mrz.get("detected"):
        score -= 5
        indicators_susp.append("No clear MRZ detected for passport-type document")
        reasons.append("Passport selected but no clear MRZ pattern found; may be incomplete image or non-standard document.")

    # 4. Field presence heuristics
    expected = DOCUMENT_TYPES.get(doc_type, {}).get("expected_fields", [])
    found_count = 0
    if fields.get("possible_dates"):
        found_count += 1
    if fields.get("possible_id_like") or fields.get("possible_ssn_pattern"):
        found_count += 1
    if fields.get("possible_amounts") and doc_type in ("Paystub", "Payroll Check", "Bank Statement", "Utility Bill"):
        found_count += 1
    if found_count >= 2:
        score += 8
        indicators_auth.append("Multiple expected field patterns present in OCR")
    elif found_count == 0 and word_count > 10:
        score -= 5
        indicators_susp.append("Few recognizable field patterns for declared document type")

    # 5. Typography variance
    if typography.get("suspicious"):
        score -= 8
        indicators_susp.extend(typography["suspicious"])
        reasons.append("Typography/layout observations raised flags (see details).")
    else:
        score += 3

    # 6. Metadata
    if file_meta.get("exif") and len(file_meta["exif"]) > 2:
        indicators_auth.append("EXIF metadata present")
        score += 2
        # Note: presence of EXIF does not prove authenticity; absence also common after editing/screenshots
    else:
        indicators_susp.append("Little or no EXIF metadata (common after screenshots, edits, or certain exports)")
        reasons.append("Limited metadata; many genuine documents also lack rich EXIF after scanning or phone capture.")

    # Clamp and map
    score = max(0, min(100, score))
    confidence = max(0.1, min(0.85, confidence))  # never claim high confidence without more evidence

    if score >= 75 and confidence >= 0.5:
        classification = "POSSIBLY REAL"
    elif score >= 55:
        classification = "POSSIBLY REAL"
    elif score >= 40:
        classification = "UNABLE TO DETERMINE"
    elif score >= 25:
        classification = "MOST LIKELY FAKE"
    else:
        classification = "UNABLE TO DETERMINE"  # prefer unable over false certainty on low evidence

    # Adjust classification for low confidence
    if confidence < 0.35:
        classification = "UNABLE TO DETERMINE"
        reasons.append("Overall confidence too low for a definitive authenticity classification.")

    # Always add core limitations
    limitations.extend([
        "No live verification against issuing-authority databases was performed (credentials and legal access required).",
        "Optical security features (holograms, microprint, OVDs, etc.) cannot be reliably confirmed or denied from typical uploaded images.",
        "Scoring is derived only from measurable image quality, OCR output, metadata, and heuristic field patterns available in this environment.",
        "This tool does not replace professional forensic document examination or official verification channels.",
    ])

    reasons.append(f"Final score {score:.0f} reflects weighted combination of image quality, OCR reliability, field-pattern presence, and absence of strong contradictory signals within the limits of the pipeline.")
    return _finalize(score, classification, confidence, reasons, indicators_auth, indicators_susp, limitations)

def _finalize(score, classification, confidence, reasons, auth, susp, limitations):
    return {
        "score": round(score, 1),
        "classification": classification,
        "confidence": round(confidence, 2),
        "confidence_label": "Low" if confidence < 0.4 else ("Moderate" if confidence < 0.65 else "Moderate-High"),
        "executive_conclusion": _build_conclusion(score, classification, confidence),
        "authenticity_indicators": auth,
        "suspicious_indicators": susp,
        "severity": "High" if classification in ("FRAUDULENT", "MOST LIKELY FAKE") else ("Medium" if classification == "UNABLE TO DETERMINE" else "Low"),
        "score_explanation": reasons,
        "limitations": limitations,
    }

def _build_conclusion(score, classification, confidence):
    if classification == "UNABLE TO DETERMINE":
        return f"Based on the available evidence (score {score:.0f}/100, confidence {confidence:.0%}), a reliable authenticity determination cannot be made. Image quality, OCR results, or missing reference data limit the analysis. Do not treat this as confirmation of authenticity or fraud."
    if classification == "POSSIBLY REAL":
        return f"Evidence is consistent with a genuine document at a basic level (score {score:.0f}/100). However, confidence remains limited because advanced security features and issuer databases were not verified. Further official verification is recommended for high-stakes use."
    if classification == "MOST LIKELY FAKE":
        return f"Multiple indicators suggest possible fabrication or significant alteration (score {score:.0f}/100). This is not definitive proof of fraud; professional examination and issuer confirmation are required."
    return f"Classification: {classification}. Score {score:.0f}."

def analyze_document(
    file_path: str,
    doc_type: str,
    original_filename: str = "",
    file_bytes: Optional[bytes] = None,
) -> Dict[str, Any]:
    """
    Full pipeline entry point. Returns structured evidence-based report.
    Never invents findings.
    """
    report = {
        "document_type": doc_type,
        "original_filename": original_filename,
        "analysis_timestamp": datetime.utcnow().isoformat() + "Z",
        "pipeline_version": "1.0.0-evidence",
        "status": "completed",
        "error": None,
    }

    try:
        if file_bytes is None:
            with open(file_path, "rb") as f:
                file_bytes = f.read()

        report["file_hash_sha256"] = compute_file_hash(file_bytes)
        report["file_size_bytes"] = len(file_bytes)

        # Determine if PDF or image
        is_pdf = original_filename.lower().endswith(".pdf") or file_bytes[:4] == b"%PDF"
        images = []
        text_from_pdf = ""

        if is_pdf:
            report["file_format"] = "PDF"
            if HAS_PYPDF:
                try:
                    reader = PdfReader(io.BytesIO(file_bytes))
                    report["pdf_page_count"] = len(reader.pages)
                    for page in reader.pages[:3]:  # limit
                        text_from_pdf += page.extract_text() or ""
                except Exception as e:
                    report["pdf_text_extract_error"] = str(e)
            if HAS_PDF2IMAGE:
                try:
                    images = convert_from_bytes(file_bytes, first_page=1, last_page=1, dpi=200)
                except Exception as e:
                    report["pdf_render_error"] = str(e)
            if not images and not text_from_pdf:
                report["status"] = "partial"
                report["error"] = "PDF could not be rendered or text-extracted with available tools."
        else:
            report["file_format"] = "Image"
            try:
                img = Image.open(io.BytesIO(file_bytes))
                images = [img]
            except Exception as e:
                report["status"] = "failed"
                report["error"] = f"Cannot open as image: {e}"
                return report

        if not images and not text_from_pdf:
            report["status"] = "failed"
            report["error"] = "No analyzable image or text content obtained."
            return report

        # Use first page/image for primary analysis
        primary_img = images[0] if images else None
        if primary_img:
            # Normalize orientation if EXIF
            try:
                primary_img = ImageOps_exif_transpose(primary_img)
            except Exception:
                pass
            if primary_img.mode not in ("RGB", "L"):
                primary_img = primary_img.convert("RGB")

        # Stages
        quality = image_quality_metrics(primary_img) if primary_img else {"error": "no image"}
        report["image_quality"] = quality

        ocr = run_ocr(primary_img) if primary_img else {"full_text": text_from_pdf, "mean_confidence": 0, "word_count": len(text_from_pdf.split()), "words": []}
        if text_from_pdf and not ocr.get("full_text"):
            ocr["full_text"] = text_from_pdf
        report["ocr"] = {
            "full_text": ocr.get("full_text", "")[:5000],
            "full_text_alt": ocr.get("full_text_alt", "")[:2000],
            "mean_confidence": ocr.get("mean_confidence"),
            "word_count": ocr.get("word_count"),
            "sample_words": ocr.get("words", [])[:30],
            "error": ocr.get("error"),
        }

        mrz = parse_mrz(ocr.get("full_text", "") + " " + ocr.get("full_text_alt", ""))
        report["mrz_qr_barcode"] = mrz

        fields = basic_field_extraction(ocr.get("full_text", "") + " " + ocr.get("full_text_alt", ""), doc_type)
        report["field_candidates"] = fields

        typography = assess_typography_and_layout(ocr, quality)
        report["typography_layout"] = typography

        forensics = image_forensics_basic(primary_img, quality) if primary_img else {"notes": "No image"}
        report["image_forensics"] = forensics

        # Metadata
        file_meta = {"exif": extract_exif(primary_img) if primary_img else {}}
        report["metadata"] = file_meta

        # Security features (honest)
        report["security_features"] = security_features_assessment(doc_type, quality, ocr)

        # Document type notes
        report["document_type_notes"] = DOCUMENT_TYPES.get(doc_type, {}).get("notes", "No specific notes.")

        # Scoring
        scoring = score_document(quality, ocr, fields, typography, forensics, mrz, doc_type, file_meta)
        report.update(scoring)

        # Internal consistency placeholder (single doc)
        report["internal_consistency"] = {
            "status": "Limited checks performed",
            "notes": "Cross-field date consistency and arithmetic (e.g., paystub totals) not fully automated for all types in this version. Review extracted candidates manually.",
        }

        # Reference comparison
        report["reference_comparisons"] = {
            "status": "Not Performed Live",
            "explanation": "No live queries to government, financial, or court authoritative databases were made. Publicly known structural expectations (e.g., presence of MRZ on modern passports) were considered heuristically only. Never claim a specific issuer record was checked.",
        }

        report["detailed_explanation"] = (
            "Score and classification are produced by a deterministic weighted combination of: "
            "(1) objective image quality metrics (resolution, Laplacian focus, contrast, brightness), "
            "(2) OCR success rate and mean confidence, "
            "(3) presence of expected field patterns for the selected document type, "
            "(4) basic typography variance from OCR boxes, "
            "(5) metadata richness, and "
            "(6) MRZ pattern detection when applicable. "
            "No machine-learning authenticity classifier trained on large labeled fraud datasets is used. "
            "No pixel-level deep-fake or advanced splicing detector is applied. "
            "Security feature statuses are set to Unable to Verify / Not Visible because consumer uploads rarely permit reliable optical-feature confirmation. "
            "If image quality is insufficient, the system deliberately returns UNABLE TO DETERMINE rather than inventing a result."
        )

    except Exception as e:
        report["status"] = "failed"
        report["error"] = str(e)
        report["score"] = 0
        report["classification"] = "UNABLE TO DETERMINE"
        report["confidence"] = 0.0
        report["executive_conclusion"] = f"Analysis failed due to processing error: {e}"

    return report


def ImageOps_exif_transpose(img: Image.Image) -> Image.Image:
    """Simple EXIF orientation fix without importing ImageOps if version issues."""
    try:
        from PIL import ImageOps
        return ImageOps.exif_transpose(img)
    except Exception:
        return img


def cross_document_consistency(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Optional cross-check across multiple analyzed documents."""
    if len(reports) < 2:
        return {"status": "Not applicable (fewer than 2 documents)", "matches": [], "mismatches": []}

    # Extract candidate names, dates, etc. from OCR texts
    all_texts = [r.get("ocr", {}).get("full_text", "") for r in reports]
    # Very simple: look for common date strings or name-like tokens
    # This is illustrative; real entity resolution is harder
    result = {
        "status": "Basic heuristic check only",
        "notes": "Cross-document entity matching is limited to shared date strings and high-frequency tokens. Full name/address normalization and fuzzy matching are not implemented. Manual review required.",
        "shared_date_strings": [],
        "observations": [],
    }
    date_sets = []
    for t in all_texts:
        dates = set(re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", t))
        date_sets.append(dates)
    if date_sets:
        common = set.intersection(*date_sets) if date_sets else set()
        result["shared_date_strings"] = list(common)[:10]
        if common:
            result["observations"].append(f"Found {len(common)} date string(s) appearing across documents.")
        else:
            result["observations"].append("No identical date strings shared across all documents.")
    return result
