import os
import json
import time
import requests
import re
from pathlib import Path
import concurrent.futures
from typing import Dict, Optional, Tuple
import uuid
import tempfile
import shutil
import traceback
import logging

# Flask and Web Server related imports
from flask import Flask, request, jsonify, send_from_directory, redirect, url_for, render_template, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# PDF/Image/OCR Processing imports
import google.generativeai as genai
from PIL import Image
from PyPDF2 import PdfReader, PdfWriter, errors as PyPDF2Errors
import pytesseract
from pdf2image import convert_from_path, exceptions as PDF2ImageExceptions
import pdfkit
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError

# Flask-Login imports
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy

# --- Environment Variable Loading ---
from dotenv import load_dotenv
load_dotenv() # Loads .env file if present (for local development)

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Flask App Initialization ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change_this_secret')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Configuration ---
# Required
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
# S3 (Optional - only if using S3 output)
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_REGION = os.getenv('AWS_REGION')
# Remote URL (Optional - only if using remote URL output)
OUTPUT_SERVER_URL = os.getenv('OUTPUT_SERVER_URL')
# Local Output (Optional - for saving processed files locally in the container)
LOCAL_OUTPUT_DIR = os.getenv('LOCAL_OUTPUT_DIR', 'uploads')

# Configuration des types de fichiers autorisés
ALLOWED_EXTENSIONS = {
    'pdf': 'application/pdf',
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'gif': 'image/gif'
}

# Taille maximale de fichier (10MB)
MAX_CONTENT_LENGTH = 10 * 1024 * 1024

def allowed_file(filename: str) -> bool:
    """Vérifie si le type de fichier est autorisé."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def validate_file(file):
    if file is None:
        return False, "Aucun fichier n'a été fourni"
    if file.filename == '':
        return False, "Aucun fichier sélectionné"
    if not allowed_file(file.filename):
        return False, "Type de fichier non autorisé"
    if file.content_length and file.content_length > MAX_CONTENT_LENGTH:
        return False, "Fichier trop volumineux"
    return True, ""

# --- Gemini Configuration ---
if not GEMINI_API_KEY:
    app.logger.error("FATAL: GEMINI_API_KEY environment variable not set.")
else:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        app.logger.error(f"Failed to configure Google Generative AI: {e}")

# --- S3 Client Initialization ---
s3_client = None
s3_configured = all([S3_BUCKET_NAME, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION])

if s3_configured:
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION
        )
        app.logger.info(f"S3 client configured for bucket '{S3_BUCKET_NAME}' in region '{AWS_REGION}'.")
    except Exception as e:
        app.logger.error(f"Failed to initialize S3 client: {e}")
        s3_configured = False
# This initial warning is about the *combination* of output methods at startup
elif not OUTPUT_SERVER_URL and not LOCAL_OUTPUT_DIR:
     app.logger.warning("STARTUP WARNING: No S3, Remote URL, or Local Output Directory fully configured. Output may not be sent/saved unless one is properly set at runtime.")


# --- Constants & Prompt Templates ---
model_name = "gemini-2.0-flash"

TABLE_DETECTION_PROMPT_TEMPLATE = """
### INSTRUCTION ###
You are an expert document layout analysis system. Your task is to examine the provided text, which represents the content extracted from a PDF image page, and determine if it contains a table.
A table is specifically defined as a structured arrangement of data organized into distinct rows and columns.
Analyze the image provided below to determine if it contains a table.
Based on your analysis of the text for elements indicative of rows, columns, and structured data organization, determine if a table is present. Provide your response as a JSON object with the following structure and types:
### INPUT ###
{pdf_page_text}
### OUTPUT FORMAT ###
{{
  "tableDetected": true | false,
  "confidenceScore": float (0.0 - 1.0)
}}
Ensure your output is *only* the JSON object and nothing else. Calculate the confidence score based on the clarity and regularity of the detected row and column structure within the text.
"""

HTML_FROM_IMAGE_PROMPT_TEMPLATE="""
You are an expert HTML/CSS coder specializing in pixel-perfect visual replication of webpage designs from screenshots. Your primary goal is to transform a given screenshot of a webpage into a single, self-contained HTML file that accurately reproduces its visual appearance and structure in meticulous detail.
Your task is to replicate a webpage screenshot as a **single HTML file** using **embedded CSS** within a `<style>` tag. The result should match the screenshot as closely as possible in layout, typography, spacing, and color. Do not use JavaScript or external libraries.
You are an expert HTML and CSS developer. Your goal is to meticulously style the provided HTML content to match the design specifications derived from a set of French insurance and assistance policy documents. The aim is to replicate their professional, clear, and structured appearance in a web format.

**General Styling Principles:**

*   **Font:** Use "Arial", "Helvetica", or a similar clean, web-safe sans-serif font as the default for all text.
*   **Readability:** Prioritize clarity and readability. Ensure sufficient contrast between text and background.
*   **Layout:** The documents generally follow a single-column layout with clear distinctions for tables and special sections. The content should be centered on the page with a reasonable maximum width for document-like readability.
*   **CSS Embedding:** Embed ALL CSS rules within a single `<style>` tag in the `<head>` of the HTML document. Do not use inline styles unless absolutely necessary for a highly specific override that cannot be achieved with classes.
*   **Color Palette (Inferred):**
    *   Default Text: Black (e.g., `#000000` or `#333333`).
    *   Main Document Title (e.g., "ASSURANCE ANNULATION TRANSPORT"): A distinct color, potentially a vibrant magenta/purple (e.g., `#D9006C` or `#C71585` for "ANNULATION TRANSPORT", or `#4A4A4A` for "ASSISTANCE VOYAGE ETUDIANT" if it needs to be more subdued). Use Magenta for "ANNULATION TRANSPORT" and a dark gray for other main titles like "ASSISTANCE VOYAGE ETUDIANT".
    *   Section Header Backgrounds (e.g., "TABLEAU DES MONTANTS DES GARANTIES", "DISPOSITIONS GENERALES", "SOMMAIRE" title block): Medium Gray (e.g., `#686868` or `#707070`).
    *   Section Header Text (on gray backgrounds): White (e.g., `#FFFFFF`).
    *   "IMPORTANT" block border/title text: Black or dark gray.
    *   Hyperlinks: Standard blue (e.g., `#0000EE`). Email links too.
    *   Brand Color (HAVAS VOYAGES): Blue (e.g., `#0077CC` or `#0084C7`).
*   **Spacing:** Use consistent margins and padding to create a clean, organized look. General padding for sections might be 10-15px. Standard paragraph bottom margin around 10-12px.

**Specific Element Styling:**

**1. Page & Body Structure:**

*   `body`:
    *   `font-family: Arial, Helvetica, sans-serif;`
    *   `line-height: 1.5;`
    *   `color: #333333;`
    *   `background-color: #FFFFFF;`
    *   `margin: 0;`
    *   `padding: 20px;`
*   `.document-container`: (A wrapper for the whole content if you create one, or apply to body if content is directly in body)
    *   `max-width: 850px;`
    *   `margin: 20px auto;`
    *   `padding: 25px;`
    *   `background-color: #FFFFFF;`
    *   `box-shadow: 0 0 10px rgba(0,0,0,0.1);` (Optional: to give a slight "page" feel)

**2. Headings & Titles:**

*   `.cover-title-block`: (Wrapper for titles like "CONDITIONS GÉNÉRALES ET SPÉCIALES" and the main product title)
    *   `text-align: left;`
    *   `margin-bottom: 100px;` (Significant space before the large title)
    *   `padding-left: 50px;` (Indented from the left)
*   `.conditions-title`: (For "CONDITIONS GÉNÉRALES ET SPÉCIALES")
    *   `font-size: 20px;`
    *   `color: #666666;`
    *   `font-weight: normal;`
    *   `letter-spacing: 1px;`
    *   `margin-bottom: 5px;`
    *   `border-bottom: 1px solid #CCCCCC;`
    *   `padding-bottom: 10px;`
    *   `display: inline-block;`
*   `.document-main-title`: (For "ASSURANCE ANNULATION TRANSPORT" or "ASSISTANCE VOYAGE ETUDIANT")
    *   `font-size: 48px;`
    *   `font-weight: bold;`
    *   `color: #D9006C;` /* Magenta for ANNULATION */
    *   /* If 'ASSISTANCE VOYAGE ETUDIANT', use a different color, e.g., #4A4A4A and smaller font-size if appropriate, e.g., 40px */
    *   `line-height: 1.2;`
    *   `margin-top: 20px;`
*   `.document-main-title.student`: (Specific for "ASSISTANCE VOYAGE ETUDIANT" parts)
    *   `color: #4A4A4A;` /* Dark Gray */
*   `.document-main-title-highlight`: (For the "ETUDIANT" part if it's a different color)
    *   `color: #D9006C;` /* Magenta */
    *   `display: block;` /* To put it on its own line if needed */
*   `.contract-number-title`: (For "CONTRAT N° ............")
    *   `font-size: 24px;`
    *   `text-align: center;`
    *   `font-weight: bold;`
    *   `margin-top: 50px;`
    *   `margin-bottom: 5px;`
    *   `color: #555555;`
*   `.contract-subtitle`: (For "ANNULATION DE TRANSPORT" under "CONTRAT N°")
    *   `font-size: 18px;`
    *   `text-align: center;`
    *   `color: #666666;`
    *   `margin-bottom: 40px;`
    *   `border-bottom: 1px solid #AAAAAA;`
    *   `padding-bottom: 20px;`
*   `h1, .h1-style, .section-main-title`: (For top-level section titles like "TABLEAU DES MONTANTS DES GARANTIES", "DEFINITIONS", "SOMMAIRE" main block)
    *   `font-size: 16px;` /* Adjusted from 18px to match observed relative size */
    *   `font-weight: bold;`
    *   `background-color: #686868;` /* Medium Gray */
    *   `color: #FFFFFF;`
    *   `padding: 8px 12px;`
    *   `margin-top: 25px;`
    *   `margin-bottom: 15px;`
    *   `text-align: left;` /* Default, some tables might center it */
*   `h2, .h2-style, .definition-term`: (For sub-section titles like "PRESTATIONS", "MONTANTS TTC", definition terms like "Accident grave")
    *   `font-size: 14px;`
    *   `font-weight: bold;`
    *   `margin-top: 20px;`
    *   `margin-bottom: 8px;`
    *   `color: #111111;`
*   `h3, .h3-style, .sub-definition-term`: (For finer-grained sub-headings if any)
    *   `font-size: 13px;`
    *   `font-weight: bold;`
    *   `margin-top: 15px;`
    *   `margin-bottom: 5px;`
    *   `color: #222222;`

**3. Paragraphs & Text:**

*   `p`:
    *   `margin-bottom: 12px;`
    *   `font-size: 13px;`
*   `strong, .bold-text`:
    *   `font-weight: bold;`
*   `.imperatif-text`: (For "IMPERATIF" word)
    *   `font-weight: bold;`
*   `.underlined-text`:
    *   `text-decoration: underline;`
*   `.small-text, .legal-footer-text`: (For footer text, page numbers, legal disclaimers at the bottom of pages)
    *   `font-size: 10px;`
    *   `color: #555555;`
    *   `margin-top: 15px;`
*   `.phone-details`: (Wrapper for phone numbers section)
    *   `text-align: center;`
    *   `margin-top: 15px;`
    *   `margin-bottom: 25px;`
*   `.phone-details span`:
    *   `margin: 0 15px;`
    *   `font-size: 13px;`
*   `.phone-number`:
    *   `font-weight: bold;`
    *   `color: #D9006C;` /* Magenta, or a theme color */
*   `a[href^="mailto:"]`:
    *   `color: #0000EE;` /* Standard link blue */
    *   `text-decoration: underline;`

**4. Lists (`<ul>`, `<ol>`, `<li>`):**

*   `ul, ol`:
    *   `margin-left: 20px;`
    *   `padding-left: 15px;`
    *   `margin-bottom: 10px;`
    *   `font-size: 13px;`
*   `li`:
    *   `margin-bottom: 6px;`
*   `ul li::marker`:
    *   `content: "• ";`
    *   `font-size: 1.2em;` /* Slightly larger bullet */
*   `.arrow-list li::before`: (For lists with "→ Franchise")
    *   `content: "→ ";`
    *   `margin-right: 5px;`
    *   `font-weight: bold;`
*   `.arrow-list ul, .arrow-list`: /* To remove default bullet when using ::before */
    *   `list-style-type: none;`
    *   `padding-left: 5px;` /* Adjust if needed */

**5. Tables (`<table>`, `<thead>`, `<tbody>`, `<tr>`, `<th>`, `<td>`):**

*   `.styled-table`:
    *   `width: 100%;`
    *   `border-collapse: collapse;`
    *   `margin-top: 0px;` /* Table immediately follows its h1-style header */
    *   `margin-bottom: 20px;`
    *   `font-size: 12px;` /* Slightly smaller for table content */
*   `.styled-table th, .styled-table td`:
    *   `border: 1px solid #B0B0B0;`
    *   `padding: 8px 10px;`
    *   `text-align: left;`
    *   `vertical-align: top;`
*   `.styled-table thead th`: (For column headers like "PRESTATIONS", "MONTANTS TTC")
    *   `background-color: #DCDCDC;` /* Light gray for standard headers */
    *   `font-weight: bold;`
    *   `color: #000000;`
    *   `font-size: 13px;`
*   `.h1-style.table-title`: (For the "TABLEAU DES MONTANTS DES GARANTIES" full-width bar)
    *   `text-align: center;` /* Center the text in this specific header bar */
    *   `margin-bottom: 0;` /* Attach directly to table */
*   `.table-prestations-col`: (First column in benefits tables)
    *   `width: 65%;`
*   `.table-montants-col`: (Second column, for amounts)
    *   `width: 35%;`
    *   `text-align: left;` /* Default, some documents might right-align, check case-by-case */
*   `.table-montants-col.currency`:
    *   `text-align: right;` /* For currency values specifically */
*   `.franchise-item`: (For "→ Franchise" items, often indented or styled slightly differently within a cell)
    *   `padding-left: 15px;` /* Indent it */

**6. Special Sections/Blocks:**

*   `.important-notice`: (For "IMPORTANT" blocks)
    *   `border: 1px solid #AAAAAA;`
    *   `padding: 15px 20px;`
    *   `margin-top: 20px;`
    *   `margin-bottom: 20px;`
    *   `background-color: #F9F9F9;` /* Very subtle off-white */
*   `.important-notice .h2-style`: (If "IMPORTANT" itself is a styled title *inside* the box)
    *   `font-weight: bold;`
    *   `font-size: 15px;`
    *   `text-align: left;` /* As observed */
    *   `margin-top: 0;`
    *   `margin-bottom: 10px;`
    *   `color: #000000;`
*   `.sommaire-section .h1-style`: (For the "SOMMAIRE" title bar)
    *   `text-align: left;`
    *   `margin-bottom: 10px;`
*   `.sommaire-item`:
    *   `display: flex;`
    *   `justify-content: space-between;`
    *   `padding: 3px 0;`
    *   `font-size: 13px;`
    *   `border-bottom: 1px dotted #CCCCCC;` /* Dotted line for TOC items */
*   `.sommaire-item:last-child`:
    *   `border-bottom: none;`
*   `.sommaire-item .page-num`:
    *   `margin-left: 15px;`
    *   `font-weight: normal;`
    *   `color: #333333;`

**7. Page Footer Elements (Mimicking PDF Footers):**

*   `.page-footer-line`: (For elements like "Page | 3")
    *   `text-align: right;`
    *   `font-size: 12px;`
    *   `color: #555555;`
    *   `margin-top: 30px;`
    *   `padding-top: 10px;`
    *   `border-top: 1px solid #EEEEEE;`
*   `.cover-footer-brands`: (Wrapper for brand logos on cover pages)
    *   `display: flex;`
    *   `justify-content: space-between;`
    *   `align-items: center;`
    *   `padding: 20px 50px;` /* Match cover-title-block padding */
    *   `margin-top: 50px;` /* Space above footer brands */
    *   `border-top: 2px solid #686868;` /* Thick gray line like in one of the covers */
*   `.havas-voyages-logo`:
    *   `background-color: #0084C7;` /* Blue from image */
    *   `color: white;`
    *   `padding: 8px 15px;`
    *   `font-weight: bold;`
    *   `font-size: 18px;`
    *   `border-radius: 3px;`
*   `.assurever-logo-text`:
    *   /* Text styling for ASSUREVER logo if it's text-based */
    *   `font-size: 22px;`
    *   `font-weight: bold;`
    *   `color: #D9006C;` /* Magenta, or a similar brand color */
*   `.assurever-tagline`:
    *   `font-size: 10px;`
    *   `color: #666666;`
    *   `display: block;`
    *   `text-align: right;` /* If logo is right-aligned */

**Input HTML Structure Expectation:**

The input HTML will be largely semantic (using `<p>`, `<ul>`, `<table>`, etc., and headings `<h1>` etc. for section titles that get the gray background).
You will need to:
1.  Apply classes defined above to appropriate elements based on their visual role in the document (e.g., wrap "IMPORTANT" sections in `<div class="important-notice">`, use `.cover-title-block` for cover page titles).
2.  Tables for "TABLEAU DES MONTANTS..." should have their main title as an `<h1>` (or `div`) with class `.h1-style .table-title` directly preceding the `<table>` tag.
3.  For cover pages, the "CONDITIONS GÉNÉRALES ET SPÉCIALES" and the main product title (e.g., "ASSURANCE ANNULATION TRANSPORT") should be structured using `.cover-title-block`, `.conditions-title`, and `.document-main-title`.
4.  The "SOMMAIRE" section will have an `<h1>` (or `div`) with class `.h1-style .sommaire-section` for its title, followed by `div`s with class `.sommaire-item` for each entry.
5.  Recognize contact information blocks and style them using `.phone-details` and `.phone-number`.
6.  Page numbers from OCR (e.g., "Page | X") should be wrapped in a `div` with class `.page-footer-line`.
7.  Brand elements in footers (HAVAS VOYAGES, ASSUREVER) should use classes like `.cover-footer-brands`, `.havas-voyages-logo`, `.assurever-logo-text`, `.assurever-tagline`.

**Output Requirements:**

1.  Produce a **single, complete HTML file**.
2.  All CSS must be contained within a single `<style>` tag in the `<head>` of the HTML document.
3.  Ensure the HTML is well-formed and the CSS is valid.
4.  The visual output should closely mirror the layout, typography, and styling of the analyzed French insurance/assistance policy documents. Pay attention to details like text alignment in tables, font weights for emphasis, the distinct styling of "IMPORTANT" blocks, "SOMMAIRE" layout, and cover page elements.
5.  When multiple documents are presented, try to maintain consistency for common elements (like tables, "IMPORTANT" blocks) but adapt specific title stylings (e.g., cover page title colors) as indicated.
---
### Input:
- A screenshot or description of a webpage ({image_reference})
- OCR-extracted text from the page: {ocr_page_text}
### Output:
- One complete HTML file with all CSS inside a `<style>` tag in the `<head>`
---
### HTML Output Requirements:
1. **Structure**
   - Use semantic HTML5 elements
   - Reproduce the visible content exactly from {image_reference}, using the provided {ocr_page_text} as a guide but prioritizing the visual structure from the image.
   - Output only the complete HTML code, no explanations
2. **Style Fidelity Guidelines:**
   - **Box Model**: Match widths, heights, padding, margin, and borders as precisely as possible based on {image_reference}
   - **Typography**:
     - Match font families (with fallbacks), font sizes, weights, line heights, and letter spacing
     - Preserve the hierarchy of headers, subheaders, and body text
   - **Colors and Effects**:
     - Accurately extract background colors, text colors, borders, gradients, and shadows
     - Use HEX or rgba() approximations when needed
   - **Layout**:
     - Use `flexbox` or `CSS grid` to match visible layout and alignment
     - Maintain the relative position of content blocks, headers, sidebars, and footers
   - **Tables**:
     - Represent all tabular data using standard `<table>`, `<thead>`, `<tbody>`, `<tr>`, and `<td>` structure
     - Each piece of data must be placed in its own clearly bordered individual cell
     - Use `border-collapse: collapse` and clearly visible borders for all cells
     - Structure the table into a **multi-column layout** that reflects the visual grouping and alignment seen in the screenshot
     - Use `colspan`, `rowspan`, or nested `<table>` or layout strategies if the screenshot indicates grouped data blocks
   - **Z-Index**:
     - If elements visually overlap, use `z-index` and `position` to replicate the stacking order
3. **Precision Modeling Cue**
   - Assume the screenshot has been measured in browser dev tools
   - Estimate sizes and spacing as if you had access to pixel values
---

IMPORTANT REMINDER:
- A large bordered box filled with bullet points arranged in a visually columnar format (such as the attached screenshot) **must be interpreted and structured as a table**. Treat each bullet item as a distinct cell aligned by column context, and apply visible borders to all cells to reflect a tabular layout. Use a two-column table structure when left and right columns of bullet points are clearly visually distinguished.
- Use `border-collapse: collapse` and clearly visible borders for all cells
- Structure the table into a **multi-column layout** that reflects the visual grouping and alignment seen in the screenshot.

**Please Note: If you feel it as a table data, then redesign the table with orgininal contents nealy aligned in a tabular format.**

Please output only the final HTML file with embedded styles, no additional commentary.
"""

# --- Logging Functions ---
def log_component(name: str, data: dict):
    log_entry = {"component": name, **data, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
    app.logger.info(json.dumps(log_entry, default=str))

def log_error(component: str, error: Exception, context: Optional[Dict] = None):
    error_data = {
        "component": component,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        **(context or {})
    }
    app.logger.error(json.dumps(error_data, default=str))
    app.logger.debug(traceback.format_exc())


# --- Validation Function ---
def validate_table_detection_response(response: Dict) -> bool:
    if not isinstance(response, dict):
        app.logger.warning(f"[Validation WARN] Response is not a dict: {type(response)}")
        return False
    required_fields = ["tableDetected", "confidenceScore"]
    if not all(field in response for field in required_fields):
        app.logger.warning(f"[Validation WARN] Response missing required fields ({required_fields}). Got: {list(response.keys())}")
        return False
    if not isinstance(response["tableDetected"], bool):
        app.logger.warning(f"[Validation WARN] 'tableDetected' field is not bool: {type(response['tableDetected'])}")
        return False
    if not isinstance(response["confidenceScore"], (int, float)):
        app.logger.warning(f"[Validation WARN] 'confidenceScore' field is not int/float: {type(response['confidenceScore'])}")
        return False
    if not (0.0 <= response["confidenceScore"] <= 1.0):
        app.logger.warning(f"[Validation WARN] 'confidenceScore' out of range [0.0, 1.0]: {response['confidenceScore']}")
    return True


# --- Prompt Wrapper ---
def wrap_prompt(template: str, context: Dict[str, str]) -> str:
    try:
        return template.format(**context)
    except KeyError as e:
        raise KeyError(f"Missing key in context for prompt substitution: {e}")


# --- Gemini Call Function for JSON ---
def call_gemini_for_json(prompt: str, max_retries: int = 3, delay: int = 5) -> Dict:
    if not GEMINI_API_KEY:
         log_error("Gemini Call", ValueError("GEMINI_API_KEY not configured."), {})
         return {"error": "GEMINI_API_KEY not configured."}

    model_instance = genai.GenerativeModel(model_name)
    generation_config = genai.types.GenerationConfig(
        temperature=0.1, # Lower temperature for more deterministic JSON
        response_mime_type="application/json" # Request JSON directly
    )
    response_text_for_logging = "" # Initialize for logging in case of error before assignment
    for attempt in range(max_retries):
        try:
            app.logger.info(f"[API Call Attempt {attempt + 1}/{max_retries}] Calling Gemini for JSON...")
            response = model_instance.generate_content(
                prompt,
                generation_config=generation_config,
                stream=False
            )
            if not response.candidates or not response.candidates[0].content.parts:
                finish_reason = response.prompt_feedback.block_reason if response.prompt_feedback else 'Unknown'
                safety_ratings = response.prompt_feedback.safety_ratings if response.prompt_feedback else 'N/A'
                error_msg = f"Gemini response empty or blocked. Finish Reason: {finish_reason}, Safety: {safety_ratings}"
                log_error("Gemini Call Error", ValueError(error_msg), {"attempt": attempt + 1, "prompt_prefix": prompt[:100]})
                if finish_reason != 'STOP': # Non-retryable block
                    return {"error": error_msg}
                raise ValueError(error_msg) # Force retry or failure if STOP but no content

            response_text_for_logging = response.text
            app.logger.info(f"[API Call Success] Gemini returned text (attempt {attempt + 1}). Length: {len(response_text_for_logging)}")
            parsed_json = json.loads(response_text_for_logging)
            if not isinstance(parsed_json, dict):
                raise TypeError(f"Parsed response is not a dictionary (type: {type(parsed_json)})")
            return parsed_json # Success
        except (json.JSONDecodeError, TypeError) as json_err:
             log_error("Gemini JSON Parsing Error", json_err, {"attempt": attempt + 1, "response_text_prefix": response_text_for_logging[:500]})
             return {"error": f"Failed to parse Gemini response as JSON: {json_err}", "raw_text": response_text_for_logging}
        except Exception as e:
            log_error("Gemini API Call/Processing Error", e, {"attempt": attempt + 1, "prompt_prefix": prompt[:100]})
            if attempt < max_retries - 1:
                current_delay = delay * (2 ** attempt)
                app.logger.warning(f"Gemini call failed. Retrying in {current_delay} seconds...")
                time.sleep(current_delay)
            else:
                app.logger.error(f"Gemini API call failed after {max_retries} attempts.")
                return {"error": f"Gemini API call failed after {max_retries} attempts: {str(e)}"}
    return {"error": f"Gemini API call failed unexpectedly after {max_retries} attempts."}


# --- Core Processing Functions ---
def detect_table(page_text: str) -> Dict:
    start_time = time.time()
    result = {"response": {}, "response_time": 0, "error": None}
    if not page_text or not page_text.strip():
        app.logger.warning("[WARN] detect_table called with empty page_text.")
        result["error"] = "Input page text was empty."
        result["response"] = {"tableDetected": False, "confidenceScore": 0.0}
        result["response_time"] = round(time.time() - start_time, 2)
        log_component("detectTable_EmptyInput", {"page_text_len": len(page_text), **result})
        return result

    try:
        prompt = wrap_prompt(TABLE_DETECTION_PROMPT_TEMPLATE, {"pdf_page_text": page_text})
        parsed_data = call_gemini_for_json(prompt)
        result["response_time"] = round(time.time() - start_time, 2)
        if "error" in parsed_data:
             result["error"] = parsed_data["error"]
             result["response"] = parsed_data # Keep error details
             return result
        if not validate_table_detection_response(parsed_data):
             validation_error_msg = "Parsed table detection response has invalid format or values."
             log_error("Table Detection Validation", ValueError(validation_error_msg), {"parsed_response": parsed_data})
             result["error"] = validation_error_msg
             result["response"] = parsed_data # Include invalid data
             return result
        result["response"] = parsed_data
        return result
    except Exception as e:
        log_error("Table Detection Pipeline Error", e, {"page_text_snippet": page_text[:100]})
        result["error"] = str(e)
        result["response_time"] = round(time.time() - start_time, 2)
        return result

def extract_full_page_html_from_image(image_path: str, ocr_text: str) -> Dict:
    start_time = time.time()
    result = {"html": "", "response_time": 0, "error": None}
    img_pil = None # Initialize for finally block
    if not GEMINI_API_KEY:
        result["error"] = "GEMINI_API_KEY not configured."
        log_error("Full Page HTML Gen", ValueError(result["error"]), {})
        return result
    app.logger.info(f"Generating full-page HTML for image: {Path(image_path).name} using '{model_name}'")
    try:
        try:
            img_pil = Image.open(image_path)
            if img_pil.mode != 'RGB': img_pil = img_pil.convert('RGB') # Gemini prefers RGB
        except FileNotFoundError:
             result["error"] = f"Image file not found at path: {image_path}"
             log_error("Full Page HTML Image Load Error", FileNotFoundError(result["error"]), {"image_path": image_path})
             return result
        except Exception as img_err:
             result["error"] = f"Error opening/converting image file {image_path}: {img_err}"
             log_error("Full Page HTML Image Load Error", img_err, {"image_path": image_path})
             return result

        prompt_text = HTML_FROM_IMAGE_PROMPT_TEMPLATE.format(
            image_reference=f"the provided image ({Path(image_path).name})",
            ocr_page_text=ocr_text
        )
        model_instance = genai.GenerativeModel(model_name)
        app.logger.info(f"Sending image ({img_pil.width}x{img_pil.height}) and text ({len(prompt_text)} chars) to Gemini...")
        response = model_instance.generate_content([prompt_text, img_pil], stream=False)
        result["response_time"] = round(time.time() - start_time, 2)

        if not response.candidates or not response.candidates[0].content.parts:
             finish_reason = response.prompt_feedback.block_reason if response.prompt_feedback else 'Unknown'
             safety_ratings = response.prompt_feedback.safety_ratings if response.prompt_feedback else 'N/A'
             error_msg = f"Gemini response empty/blocked. Finish Reason: {finish_reason}, Safety: {safety_ratings}"
             log_error("Full Page HTML Generation Error", ValueError(error_msg), {"image_path": image_path, "finish_reason": finish_reason})
             result["error"] = error_msg
             try: result["html"] = response.text # Store partial if available
             except ValueError: pass
             return result

        html_code = response.text.strip()
        result["html"] = html_code
        if not html_code:
            app.logger.warning(f"Full page HTML generation resulted in empty content for {Path(image_path).name}.")
            log_component("extractFullPageHTML_EmptyResult", {"image_path": image_path})
        else:
            app.logger.info(f"Successfully generated {len(html_code)} chars of HTML for {Path(image_path).name}.")
        return result
    except Exception as e:
        log_error("Full Page HTML Generation Pipeline Error", e, {"image_path": image_path})
        result["error"] = str(e)
        result["response_time"] = round(time.time() - start_time, 2)
        if 'response' in locals() and hasattr(response, 'text'): # Check if response object exists
             try: result["html"] = response.text
             except ValueError: pass
        return result
    finally:
        if img_pil:
            try: img_pil.close()
            except Exception as close_err: app.logger.warning(f"Error closing PIL image: {close_err}")


# --- System Dependency Check ---
def check_system_dependencies():
    dependencies_ok = True
    # Check Tesseract
    try:
        pytesseract.get_tesseract_version()
        app.logger.info("[Dependency Check] Tesseract OCR found.")
    except pytesseract.TesseractNotFoundError:
        app.logger.error("[FATAL DEPENDENCY] Tesseract OCR engine not found or not in PATH.")
        dependencies_ok = False
    except Exception as e: app.logger.warning(f"[Dependency Check WARN] Error checking Tesseract version: {e}")
    # Check Poppler
    try:
        from io import BytesIO
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        buffer = BytesIO()
        p = canvas.Canvas(buffer, pagesize=letter); p.drawString(100, 750, "Poppler Check"); p.save(); buffer.seek(0)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as temp_pdf:
            with tempfile.TemporaryDirectory() as temp_img_dir: # pdf2image needs a dir to write to
                convert_from_path(temp_pdf.name, dpi=50, output_folder=temp_img_dir, first_page=1, last_page=1, thread_count=1)
            app.logger.info("[Dependency Check] Poppler utilities seem accessible via pdf2image.")
    except (PDF2ImageExceptions.PDFInfoNotInstalledError, PDF2ImageExceptions.PDFPageCountError) as poppler_err:
        app.logger.error(f"[FATAL DEPENDENCY] Poppler PDF utilities error: {poppler_err}")
        dependencies_ok = False
    except ImportError: app.logger.warning("[Dependency Check WARN] Cannot perform Poppler check: reportlab not installed.")
    except Exception as e: app.logger.warning(f"[Dependency Check WARN] Could not perform full Poppler check: {e}")
    # Check wkhtmltopdf
    try:
        wk_config = pdfkit.configuration()
        wk_path_bytes = wk_config.wkhtmltopdf
        wk_path_str = wk_path_bytes.decode() if isinstance(wk_path_bytes, bytes) else str(wk_path_bytes)

        if not Path(wk_path_str).is_file():
            # Try finding it in common system paths if config path fails or is empty
            found_wk = shutil.which("wkhtmltopdf")
            if found_wk:
                 app.logger.info(f"[Dependency Check] wkhtmltopdf found via shutil.which: {found_wk}")
            else:
                app.logger.error(f"[FATAL DEPENDENCY] wkhtmltopdf executable not found at configured path ('{wk_path_str}') or in system PATH.")
                dependencies_ok = False
        else:
             app.logger.info(f"[Dependency Check] wkhtmltopdf found at configured path: {wk_path_str}")
    except OSError: # This can happen if wkhtmltopdf command itself fails (e.g. not installed)
        app.logger.error("[FATAL DEPENDENCY] wkhtmltopdf not found or accessible. pdfkit cannot convert HTML to PDF.")
        dependencies_ok = False
    except Exception as e: app.logger.warning(f"[Dependency Check WARN] Error checking wkhtmltopdf: {e}")
    return dependencies_ok


# --- PDF/Image Handling ---
def save_pdf_page(reader, page_num: int, output_path: Path):
    """Saves individual PDF page to file using PyPDF2."""
    writer = PdfWriter()
    num_pages_total = len(reader.pages)
    if not (0 <= page_num < num_pages_total):
        raise IndexError(f"Page number {page_num} is out of range for PDF with {num_pages_total} pages.")
    try:
        writer.add_page(reader.pages[page_num])
        with open(output_path, 'wb') as output_file:
            writer.write(output_file)
    except Exception as e:
         log_error("Save PDF Page Error", e, {"page_index": page_num, "output_path": str(output_path)})
         raise RuntimeError(f"Failed to save page {page_num+1} to {output_path}") from e


# --- Main Processing Logic ---
def process_pdf_in_tempdir(input_pdf_path: Path, temp_dir_path: Path) -> Tuple[bool, Optional[str]]:
    start_time_total = time.time()
    # Define subdirectories within the temporary directory
    folders = {name: temp_dir_path / name for name in ["splitter", "pdfImages", "tableContainerHTML"]}
    try:
        for folder_path in folders.values():
            folder_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log_error("Create Temp Subdirs Error", e, {"temp_dir": str(temp_dir_path)})
        return False, f"Could not create temporary subdirectories: {e}"
    try:
        reader = PdfReader(str(input_pdf_path))
        num_pages = len(reader.pages)
        if num_pages == 0:
             app.logger.warning(f"PDF file '{input_pdf_path.name}' contains 0 pages.")
             return True, None # Treat as success with no output pages
    except PyPDF2Errors.PdfReadError as pdf_err:
         log_error("PDF Reading Error", pdf_err, {"pdf_path": str(input_pdf_path)})
         return False, f"Failed to read/parse PDF (corrupted/password?): {pdf_err}"
    except Exception as e:
        log_error("PDF Reading Unexpected Error", e, {"pdf_path": str(input_pdf_path)})
        return False, f"Unexpected error reading PDF: {e}"

    app.logger.info(f"Processing {num_pages} pages from '{input_pdf_path.name}' in temp dir: {temp_dir_path}")
    log_component("PipelineStart", {"pdf_name": input_pdf_path.name, "num_pages": num_pages, "temp_dir": str(temp_dir_path)})
    failed_pages_processing_count = 0
    overall_processing_error_message = None

    def process_single_page(page_index: int):
        page_num = page_index + 1 # 1-based index
        page_is_successful = True; page_specific_error_msg = None
        start_time_page = time.time()
        page_pdf_path = folders["splitter"] / f"page_{page_num}.pdf"
        page_image_path = folders["pdfImages"] / f"page_{page_num}.png" # Expected output path
        page_log_context = {"page": page_num, "pdf_name": input_pdf_path.name, "temp_dir": str(temp_dir_path)}
        try:
            save_pdf_page(reader, page_index, page_pdf_path) # Uses 0-based index
            app.logger.info(f"[Page {page_num}] Converting PDF page to image...")
            try:
                # pdf2image saves files directly, so we'll use output_file to name it
                convert_from_path(
                    str(page_pdf_path), dpi=300, output_folder=folders["pdfImages"],
                    output_file=f"page_{page_num}", fmt='png', thread_count=1
                )
                # After conversion, check if the specifically named file exists
                if not page_image_path.exists():
                    # Fallback: Check if a slightly different name was generated (e.g., with sequence like -001)
                    found_images = list(folders["pdfImages"].glob(f"page_{page_num}*.png"))
                    if found_images:
                        page_image_path = found_images[0] # Take the first match
                        app.logger.warning(f"[Page {page_num}] Adjusted image path to {page_image_path.name}")
                    else:
                        raise FileNotFoundError(f"Output image file {page_image_path.name} (or similar) not found after conversion.")
                app.logger.info(f"[Page {page_num}] Image saved: {page_image_path.name}")
            except Exception as convert_err: raise RuntimeError(f"Failed to convert page {page_num} to image: {convert_err}")

            app.logger.info(f"[Page {page_num}] Extracting text via OCR...")
            page_text = ""
            try:
                 with Image.open(page_image_path) as img_for_ocr:
                      page_text = pytesseract.image_to_string(img_for_ocr, timeout=60).strip()
                 app.logger.info(f"[Page {page_num}] OCR Success: Extracted {len(page_text)} chars.")
                 if not page_text: app.logger.warning(f"[Page {page_num}] WARN: OCR resulted in empty text.")
            except Exception as ocr_err: raise RuntimeError(f"OCR failed for page {page_num}: {ocr_err}")

            app.logger.info(f"[Page {page_num}] Detecting tables via Gemini...")
            detection_result = detect_table(page_text)
            log_component("detectTableResult", {**page_log_context, **detection_result})
            if detection_result.get("error"):
                err_msg = detection_result["error"]
                app.logger.error(f"[Page {page_num}] Table Detection Failed. Error: {err_msg}")
                if "Input page text was empty" not in err_msg: # Empty text is not a page failure
                     page_is_successful = False; page_specific_error_msg = f"Table Detection: {err_msg}"
            else:
                parsed_detection = detection_result.get("response", {})
                if not isinstance(parsed_detection.get("tableDetected"), bool):
                     err_msg = f"Table Detection invalid response format: {parsed_detection}"
                     app.logger.error(f"[Page {page_num}] {err_msg}")
                     page_is_successful = False; page_specific_error_msg = err_msg
                elif parsed_detection.get("tableDetected") is True:
                    app.logger.info(f"[Page {page_num}] Table detected, generating full page HTML...")
                    html_result = extract_full_page_html_from_image(str(page_image_path), page_text)
                    log_component("extractFullPageHTMLResult", {**page_log_context, **html_result})
                    if html_result.get("error"):
                         err_msg = html_result['error']
                         app.logger.error(f"[Page {page_num}] Generate Full Page HTML Failed. Error: {err_msg}")
                         page_is_successful = False; page_specific_error_msg = f"HTML Gen: {err_msg}"
                    else:
                        html_code = html_result.get("html", "")
                        app.logger.info(f"[Page {page_num}] Generate Full Page HTML: {len(html_code)} chars (Time: {html_result.get('response_time')}s)")
                        if html_code:
                            html_code = clean_ai_html_response(html_code)  # Clean the response
                            html_path = folders["tableContainerHTML"] / f"page_{page_num}_full.html"
                            try:
                                with open(html_path, "w", encoding="utf-8") as f: 
                                    f.write(html_code)
                                app.logger.info(f"[Page {page_num}] Full Page HTML saved: {html_path.name}")
                            except Exception as write_err:
                                 err_msg = f"HTML Save Failed: {write_err}"
                                 app.logger.error(f"[ERROR] {err_msg} for page {page_num}")
                                 page_is_successful = False; page_specific_error_msg = err_msg
                        else: app.logger.warning(f"[Page {page_num}] Full Page HTML generation resulted in empty content.")
                else: app.logger.info(f"[Page {page_num}] No table detected by Gemini. Skipping HTML generation.")
        except Exception as page_err:
             log_error("Process Single Page Unhandled Error", page_err, page_log_context)
             page_is_successful = False; page_specific_error_msg = f"Unhandled Page Error: {page_err}"
        finally:
            if page_pdf_path.exists():
                try: page_pdf_path.unlink()
                except Exception as unlink_err: app.logger.warning(f"[WARN] Error deleting temp PDF {page_pdf_path.name}: {unlink_err}")
            page_duration = round(time.time() - start_time_page, 2)
            app.logger.info(f"--- Finished Page {page_num} in {page_duration}s (Success: {page_is_successful}) ---")
            log_component("PageProcessEnd", {**page_log_context, "duration_sec": page_duration, "success": page_is_successful, "error": page_specific_error_msg})
            return page_is_successful, page_specific_error_msg

    max_workers = min(4, os.cpu_count() or 1)
    app.logger.info(f"Starting concurrent page processing with up to {max_workers} workers...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_single_page, i): i for i in range(num_pages)}
        for future in concurrent.futures.as_completed(futures):
            page_index = futures[future]
            try:
                page_success, page_err_msg_future = future.result()
                if not page_success:
                    failed_pages_processing_count += 1
                    if not overall_processing_error_message: # Capture first page error summary
                         overall_processing_error_message = f"Page {page_index + 1} error: {page_err_msg_future}"
            except Exception as e:
                failed_pages_processing_count += 1
                overall_processing_error_message = f"Critical failure in task for Page {page_index + 1}: {e}"
                log_error("Concurrent Execution Error", e, {"page_index": page_index, "pdf_name": input_pdf_path.name})

    total_duration = round(time.time() - start_time_total, 2)
    log_component("PipelineEnd", {"pdf_name": input_pdf_path.name, "total_duration_sec": total_duration, "failed_pages": failed_pages_processing_count})
    app.logger.info(f"Finished processing {input_pdf_path.name}. Total Time: {total_duration}s. Pages with critical errors: {failed_pages_processing_count}")
    # Overall success is if no critical executor errors AND no pages had critical processing failures.
    final_success = overall_processing_error_message is None and failed_pages_processing_count == 0
    final_error_summary = overall_processing_error_message or (f"{failed_pages_processing_count} pages encountered processing errors." if failed_pages_processing_count > 0 else None)
    return final_success, final_error_summary


# --- Final PDF Merging ---
def merge_final_pdf(original_pdf_path: Path, temp_dir_path: Path) -> Tuple[bool, Optional[Path], Optional[str]]:
    app.logger.info(f"Starting final PDF merge for '{original_pdf_path.name}'")
    final_output_pdf_path = temp_dir_path / "final_merged.pdf"
    merger = PdfWriter()
    merge_is_successful = True; overall_merge_error_message = None
    folders = {name: temp_dir_path / name for name in ["splitter", "pdfImages", "tableContainerHTML"]}
    try:
        input_pdf_reader = PdfReader(str(original_pdf_path))
        total_pages = len(input_pdf_reader.pages)
        for i in range(total_pages):
            page_num = i + 1
            html_file = folders["tableContainerHTML"] / f"page_{page_num}_full.html"
            page_to_add_path = None; source_description = ""
            if html_file.exists():
                converted_pdf_path = folders["tableContainerHTML"] / f"page_{page_num}_converted.pdf"
                try:
                    # Options for pdfkit, 'enable-local-file-access' is often needed for local CSS/images in HTML
                    pdfkit_options = {'enable-local-file-access': None, 'quiet': ''}
                    pdfkit.from_file(str(html_file), str(converted_pdf_path), options=pdfkit_options)
                    app.logger.info(f"[Merge Page {page_num}] Converted HTML to PDF: {converted_pdf_path.name}")
                    page_to_add_path = converted_pdf_path
                    source_description = "HTML conversion"
                except Exception as e:
                    err_msg = f"Page {page_num} HTML conversion failed: {e}"
                    log_error("HTML to PDF Conversion Error", e, {"page": page_num, "html_path": str(html_file)})
                    app.logger.error(f"[Merge Page {page_num}] Converting HTML to PDF failed: {e}. Using fallback.")
                    merge_is_successful = False # Mark potential issue but continue
                    if not overall_merge_error_message: overall_merge_error_message = err_msg
            # Fallback: If HTML conversion failed or no HTML existed, add original page
            if page_to_add_path is None:
                 try:
                     page_writer_for_original = PdfWriter()
                     page_writer_for_original.add_page(input_pdf_reader.pages[i])
                     # Save original page temporarily to merge it, ensures clean operation
                     fallback_pdf_path = folders["splitter"] / f"page_{page_num}_original_for_merge.pdf"
                     with open(fallback_pdf_path, "wb") as f_out:
                         page_writer_for_original.write(f_out)
                     page_to_add_path = fallback_pdf_path
                     source_description = "Original PDF"
                     app.logger.info(f"[Merge Page {page_num}] Added original page via temp file: {fallback_pdf_path.name}")
                 except Exception as e:
                      err_msg = f"Page {page_num} could not be extracted/added from original: {e}"
                      log_error("Add Original Page Error", e, {"page": page_num})
                      app.logger.error(f"[Merge Page {page_num}] {err_msg}")
                      merge_is_successful = False
                      if not overall_merge_error_message: overall_merge_error_message = err_msg
                      continue # Skip adding this page if extraction fails

            if page_to_add_path and page_to_add_path.exists():
                 try:
                     reader_for_page_to_add = PdfReader(str(page_to_add_path))
                     if len(reader_for_page_to_add.pages) > 0:
                          merger.add_page(reader_for_page_to_add.pages[0])
                          app.logger.debug(f"[Merge Page {page_num}] Successfully merged page from {source_description}.")
                     else:
                          app.logger.warning(f"[Merge Page {page_num}] PDF from {source_description} ({page_to_add_path.name}) was empty.")
                          merge_is_successful = False # Indicate potential issue
                          if not overall_merge_error_message: overall_merge_error_message = f"Page {page_num} from {source_description} was empty."
                 except Exception as merge_err:
                     err_msg = f"Page {page_num} merging failed from source {page_to_add_path.name}: {merge_err}"
                     log_error("PDF Page Merge Error", merge_err, {"page": page_num, "source_path": str(page_to_add_path)})
                     app.logger.error(f"[Merge Page {page_num}] {err_msg}")
                     merge_is_successful = False
                     if not overall_merge_error_message: overall_merge_error_message = err_msg
            elif page_to_add_path: # Path was set but file doesn't exist
                 app.logger.error(f"[Merge Page {page_num}] Source PDF path {page_to_add_path} not found for merging.")
                 merge_is_successful = False
                 if not overall_merge_error_message: overall_merge_error_message = f"Page {page_num} source PDF missing."

        if len(merger.pages) > 0:
            with open(final_output_pdf_path, "wb") as f_out:
                merger.write(f_out)
            app.logger.info(f"Final merged PDF created with {len(merger.pages)} pages: {final_output_pdf_path}")
        else:
            app.logger.error("No pages were successfully merged into the final PDF.")
            merge_is_successful = False
            if not overall_merge_error_message: overall_merge_error_message = "No pages could be merged."
            final_output_pdf_path = None # No output file generated
    except Exception as e:
        log_error("Final PDF Merge Unhandled Error", e, {"pdf_name": original_pdf_path.name})
        app.logger.error(f"Unhandled error during final merge: {e}")
        merge_is_successful = False
        overall_merge_error_message = f"Unhandled merge error: {e}"
        final_output_pdf_path = None
    return merge_is_successful, final_output_pdf_path, overall_merge_error_message


# --- Output Handling Functions ---
def save_to_local_directory(source_file_path: Path, original_filename: str, sub_folder_name: Optional[str], base_output_dir_str: str) -> Tuple[bool, Optional[str]]:
    app.logger.info(f"Attempting local save. Base dir env var: '{base_output_dir_str}', Source: '{source_file_path}', Original FN: '{original_filename}', Sub-folder req: '{sub_folder_name}'")
    if not base_output_dir_str or base_output_dir_str.strip() == "":
        app.logger.error("LOCAL_OUTPUT_DIR environment variable is not configured or is an empty string.")
        return False, "LOCAL_OUTPUT_DIR is not configured or is empty."
    try:
        base_output_dir = Path(base_output_dir_str)
        app.logger.info(f"Resolved base_output_dir path object: {base_output_dir}")
        # Create the base output directory if it doesn't exist
        base_output_dir.mkdir(parents=True, exist_ok=True)
        app.logger.info(f"Ensured base_output_dir exists: {base_output_dir}")

        destination_dir = base_output_dir
        if sub_folder_name:
            # Clean the sub_folder_name to make it a safe path component
            safe_sub_folder = secure_filename(sub_folder_name).strip()
            if safe_sub_folder: # Ensure it's not empty after cleaning
                destination_dir = base_output_dir / safe_sub_folder
                destination_dir.mkdir(parents=True, exist_ok=True)
                app.logger.info(f"Ensured sub-folder destination_dir exists: {destination_dir}")
            else:
                app.logger.info("Requested sub-folder name was empty after sanitization, using base_output_dir.")
        else:
            app.logger.info("No sub-folder name provided, using base_output_dir.")

        # Create a unique filename to avoid overwrites
        unique_id = uuid.uuid4()
        base_name, ext = os.path.splitext(original_filename)
        safe_base_name = secure_filename(base_name) # Sanitize original base name
        final_filename = f"{safe_base_name}_{unique_id}{ext}"
        destination_file_path = destination_dir / final_filename
        app.logger.info(f"Attempting to copy source '{source_file_path}' to destination '{destination_file_path}'")

        shutil.copy2(source_file_path, destination_file_path) # copy2 preserves metadata like timestamps

        app.logger.info(f"Successfully saved file to local path: {destination_file_path}")
        return True, str(destination_file_path)
    except OSError as e:
        # More detailed logging for OS errors (permissions, disk full, etc.)
        log_error("Local File Save OSError", e, {"destination_dir_attempt": str(base_output_dir), "errno": e.errno, "strerror": e.strerror})
        app.logger.error(f"OSError saving file locally to {base_output_dir} (errno {e.errno} - {e.strerror}): {e}")
        return False, f"OSError saving file locally (errno {e.errno}): {e.strerror}"
    except Exception as e:
        # Catch any other unexpected errors during local save
        log_error("Local File Save Unhandled Error", e, {"destination_path_attempt": str(destination_dir / original_filename if 'destination_dir' in locals() else base_output_dir_str)})
        app.logger.error(f"Unhandled error saving file locally: {e}")
        return False, f"Unhandled error saving file locally: {e}"

def upload_to_s3(file_path: Path, original_filename: str, folder_name: Optional[str]) -> Tuple[bool, Optional[str]]:
    if not s3_client or not S3_BUCKET_NAME:
        log_error("S3 Upload Error", ValueError("S3 client or bucket not configured"), {})
        return False, "S3 client or bucket not configured"
    s3_key = ""
    if folder_name:
        safe_folder_name = secure_filename(folder_name).strip()
        if safe_folder_name: s3_key += safe_folder_name + "/"
    base_name, ext = os.path.splitext(original_filename)
    safe_base_name = secure_filename(base_name)
    s3_key += f"{safe_base_name}_{uuid.uuid4()}{ext}"
    app.logger.info(f"Attempting to upload '{file_path.name}' to S3 bucket '{S3_BUCKET_NAME}' as key '{s3_key}'")
    try:
        with open(file_path, 'rb') as f:
            s3_client.upload_fileobj(f, S3_BUCKET_NAME, s3_key)
        app.logger.info(f"Successfully uploaded to s3://{S3_BUCKET_NAME}/{s3_key}")
        return True, f"s3://{S3_BUCKET_NAME}/{s3_key}"
    except (NoCredentialsError, PartialCredentialsError) as cred_err:
        log_error("S3 Upload Credentials Error", cred_err, {"s3_key": s3_key})
        return False, f"S3 credentials error: {cred_err}"
    except ClientError as e:
        log_error("S3 Upload ClientError", e, {"s3_key": s3_key, "error_code": e.response.get('Error', {}).get('Code')})
        return False, f"S3 ClientError: {e.response.get('Error', {}).get('Message', str(e))}"
    except Exception as e:
        log_error("S3 Upload Unhandled Error", e, {"s3_key": s3_key})
        return False, f"Unhandled S3 upload error: {e}"

def send_to_remote_url(file_path: Path, original_filename: str) -> Tuple[bool, Optional[str]]:
    if not OUTPUT_SERVER_URL:
        log_error("Remote URL Send Error", ValueError("OUTPUT_SERVER_URL not configured"), {})
        return False, "OUTPUT_SERVER_URL not configured"
    app.logger.info(f"Attempting to send '{file_path.name}' to remote URL: {OUTPUT_SERVER_URL}")
    try:
        with open(file_path, 'rb') as f:
            files = {'file': (original_filename, f, 'application/pdf')}
            response = requests.post(OUTPUT_SERVER_URL, files=files, timeout=60)
        response.raise_for_status()
        app.logger.info(f"Successfully sent file to {OUTPUT_SERVER_URL}. Status: {response.status_code}")
        return True, f"Sent successfully to {OUTPUT_SERVER_URL}. Status: {response.status_code}"
    except requests.exceptions.Timeout as e:
         log_error("Remote URL Send Timeout", e, {"url": OUTPUT_SERVER_URL})
         return False, f"Timeout sending file: {e}"
    except requests.exceptions.RequestException as e:
        log_error("Remote URL Send RequestException", e, {"url": OUTPUT_SERVER_URL})
        status_code = e.response.status_code if e.response is not None else "N/A"
        response_text = e.response.text[:500] if e.response is not None else "N/A"
        return False, f"Failed sending file (Status: {status_code}): {e}. Response: {response_text}"
    except Exception as e:
        log_error("Remote URL Send Unhandled Error", e, {"url": OUTPUT_SERVER_URL})
        return False, f"Unhandled error sending file: {e}"


# --- API Endpoint ---
@app.route('/', methods=['GET'])
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # Validation des champs
        if not username or not email or not password or not confirm_password:
            flash('Tous les champs sont obligatoires', 'error')
            return redirect(url_for('register'))
            
        if len(username) < 3:
            flash('Le nom d\'utilisateur doit contenir au moins 3 caractères', 'error')
            return redirect(url_for('register'))
            
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            flash('Format d\'email invalide', 'error')
            return redirect(url_for('register'))
            
        if len(password) < 8:
            flash('Le mot de passe doit contenir au moins 8 caractères', 'error')
            return redirect(url_for('register'))
            
        if password != confirm_password:
            flash('Les mots de passe ne correspondent pas', 'error')
            return redirect(url_for('register'))
        
        # Vérification des doublons
        if User.query.filter_by(username=username).first():
            flash('Ce nom d\'utilisateur est déjà pris', 'error')
            return redirect(url_for('register'))
            
        if User.query.filter_by(email=email).first():
            flash('Cet email est déjà utilisé', 'error')
            return redirect(url_for('register'))
        
        try:
            hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
            new_user = User(username=username, email=email, password=hashed_pw)
            db.session.add(new_user)
            db.session.commit()
            flash('Compte créé avec succès ! Vous pouvez maintenant vous connecter.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur lors de la création du compte: {str(e)}")
            flash('Une erreur est survenue lors de la création du compte', 'error')
            return redirect(url_for('register'))
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            flash('Veuillez remplir tous les champs', 'error')
            return redirect(url_for('login'))
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user)
            flash('Connexion réussie !', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        else:
            flash('Nom d\'utilisateur ou mot de passe incorrect', 'error')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Vous avez été déconnecté avec succès', 'success')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    user_folder = os.path.join('local_outputs', str(current_user.id))
    os.makedirs(user_folder, exist_ok=True)
    files = os.listdir(user_folder)
    return render_template('dashboard.html', files=files, user=current_user)

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files:
        flash('Aucun fichier sélectionné', 'error')
        return redirect(url_for('dashboard'))
        
    file = request.files['file']
    if not file or file.filename == '':
        flash('Aucun fichier sélectionné', 'error')
        return redirect(url_for('dashboard'))
        
    if not allowed_file(file.filename):
        flash('Type de fichier non autorisé', 'error')
        return redirect(url_for('dashboard'))
        
    try:
        filename = secure_filename(file.filename)
        user_folder = os.path.join('local_outputs', str(current_user.id))
        os.makedirs(user_folder, exist_ok=True)
        file.save(os.path.join(user_folder, filename))
        flash('Fichier uploadé avec succès', 'success')
    except Exception as e:
        app.logger.error(f"Erreur lors de l'upload: {str(e)}")
        flash('Une erreur est survenue lors de l\'upload du fichier', 'error')
        
    return redirect(url_for('dashboard'))

@app.route('/download/<filename>')
@login_required
def download_file(filename):
    user_folder = os.path.join('local_outputs', str(current_user.id))
    return send_from_directory(user_folder, filename, as_attachment=True)

@app.route('/delete/<filename>')
@login_required
def delete_file(filename):
    try:
        user_folder = os.path.join('local_outputs', str(current_user.id))
        file_path = os.path.join(user_folder, filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            flash('Fichier supprimé avec succès', 'success')
        else:
            flash('Fichier introuvable', 'error')
    except Exception as e:
        app.logger.error(f"Erreur lors de la suppression: {str(e)}")
        flash('Une erreur est survenue lors de la suppression du fichier', 'error')
        
    return redirect(url_for('dashboard'))

@app.route('/api/files', methods=['GET'])
@login_required
def api_list_files():
    user_folder = os.path.join('local_outputs', str(current_user.id))
    os.makedirs(user_folder, exist_ok=True)
    files = os.listdir(user_folder)
    return jsonify({'files': files})

@app.route('/api/upload', methods=['POST'])
@login_required
def api_upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier'}), 400
    file = request.files['file']
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        user_folder = os.path.join('local_outputs', str(current_user.id))
        os.makedirs(user_folder, exist_ok=True)
        file.save(os.path.join(user_folder, filename))
        return jsonify({'message': 'Fichier uploadé', 'filename': filename}), 200
    return jsonify({'error': 'Type de fichier non autorisé'}), 400

@app.route('/api/download/<filename>', methods=['GET'])
@login_required
def api_download_file(filename):
    user_folder = os.path.join('local_outputs', str(current_user.id))
    return send_from_directory(user_folder, filename, as_attachment=True)

@app.route('/api/delete/<filename>', methods=['DELETE'])
@login_required
def api_delete_file(filename):
    user_folder = os.path.join('local_outputs', str(current_user.id))
    file_path = os.path.join(user_folder, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        return jsonify({'message': 'Fichier supprimé'})
    return jsonify({'error': 'Fichier introuvable'}), 404

# --- Main Execution ---
if __name__ == '__main__':
    # Perform initial dependency check at startup for early warning
    startup_dependencies_ok = check_system_dependencies()
    if not startup_dependencies_ok:
        app.logger.fatal("CRITICAL STARTUP ERROR: System dependencies missing or misconfigured. API may not function correctly.")
        # Consider exiting if this is unacceptable for your deployment
    else:
        app.logger.info("System dependencies check passed at startup.")

    port = int(os.environ.get('PORT', 5001))
    # For production, Gunicorn is typically started by the Docker CMD line.
    # This app.run() is for convenience when running `python app.py` locally.
    app.logger.info(f"Starting Flask development server on http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=True) # Set debug=True ONLY for active development

def clean_ai_html_response(response: str) -> str:
    """Removes markdown code block syntax from AI-generated HTML responses."""
    if response.startswith("```html"):
        response = response.replace("```html", "", 1)
    if response.endswith("```"):
        response = response[:-3]
    return response.strip()