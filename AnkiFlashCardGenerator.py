import os
import pdfplumber
import json
import requests
import asyncio
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from openai import OpenAI
from pydantic import BaseModel

# Creates a Flask application
app = Flask(__name__)
CORS(app)

# ============================================
# OpenAI API Client
# ============================================
client = OpenAI()

def create_message(client, thread_id, content):
    return client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=content
    )

def create_assistant(client, name, instructions, tools, model):
    return client.beta.assistants.create(
        name=name,
        instructions=instructions,
        tools=tools,
        model=model
    )

def create_thread(client):
    return client.beta.threads.create()

def create_and_poll_run(client, thread_id, assistant_id, instructions, response_format=None):
    return client.beta.threads.runs.create_and_poll(
        thread_id=thread_id,
        assistant_id=assistant_id,
        instructions=instructions,
        response_format=response_format
    )

def create_completion(client, messages, response_format=None):
    completion = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        response_format=response_format
    )
    return completion

def clean_json_response(response_str):
    """Removes markdown code fences from the response."""
    lines = response_str.splitlines()
    cleaned_lines = [line for line in lines if not line.strip().startswith("```")]
    return "\n".join(cleaned_lines)

# ============================================
# Synchronous Flashcard Generation (Version 2)
# ============================================
def generate_flashcards_for_page2(page_text, response_format):
    """Generates flashcards for a given page's text."""
    if not page_text.strip():
        return []
    
    content_string = """Your job is to extract flashcards from the provided text.

### STRICT GUIDELINES:

1. **Concise:** Every card must be as **short as possible** while conveying the key idea.
   - ❌ **Bad:** "What is a composite attribute in an ER Model, and how does it function?"
   - ✅ **Good:** "What is a composite attribute?" → "An attribute split into subparts."

2. **Focused:** Each card must test **only one fact or concept.**
   - ❌ **Bad:** "What is a weak entity, and how does it relate to foreign keys?"
   - ✅ **Good:** "What is a weak entity?" → "An entity that lacks a unique identifier."

3. **No Meta Content:** Ignore slide credits, authors, or any irrelevant meta-information.

### OUTPUT REQUIREMENTS:
- **PURE JSON ONLY** (no markdown, no newlines, no extra spaces).  
- Must return a **valid JSON array of objects**.
- Each object **must contain exactly two keys**: "front" and "back".
- **Do not escape characters unnecessarily** (e.g., no `\'` for apostrophes).
- **No extra formatting or explanations.**

#### **Example Output:**
```json
[{"front":"What is an entity?","back":"A distinct real-world object."},{"front":"What is an attribute?","back":"A property of an entity."}]
"""
    initial_completion_message = [
        {"role": "user", "content": content_string},
        {"role": "user", "content": page_text}
    ]
    response = create_completion(client, initial_completion_message, response_format=response_format)
    flashcards = response.choices[0].message.content
    flashcards = json.loads(flashcards)
    return flashcards

# ============================================
# PDF Page Extraction
# ============================================
def extract_pages_from_pdf(pdf_path):
    """Extracts text from each page in a PDF."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append((i, text))
    return pages

# ============================================
# Asynchronous Processing Functions
# ============================================
async def generate_flashcards_for_page_async(page_text, response_format):
    """Asynchronously generates flashcards for a single page by running the synchronous function in a separate thread."""
    return await asyncio.to_thread(generate_flashcards_for_page2, page_text, response_format)

async def process_pdf_to_flashcards_async(pdf_path):
    """Processes the PDF asynchronously, concurrently generating flashcards for each page."""
    pages = extract_pages_from_pdf(pdf_path)
    response_format = {
        'type': 'json_schema',
        'json_schema': {
            "name": "whocares",
            "schema": FlashcardSet.model_json_schema()
        }
    }
    tasks = [
        generate_flashcards_for_page_async(page_text, response_format)
        for _, page_text in pages if page_text.strip()
    ]
    results = await asyncio.gather(*tasks)
    all_flashcards = []
    for res in results:
        all_flashcards.append(res)
    return all_flashcards

def process_pdf_to_flashcards(pdf_path):
    """Wrapper to run the asynchronous PDF processing function."""
    return asyncio.run(process_pdf_to_flashcards_async(pdf_path))

def flatten_flashcards(nested_flashcards):
    """Flattens a nested list of flashcards into a single list."""
    flat_flashcards = []
    for group in nested_flashcards:
        if 'flashcards' in group:
            flat_flashcards.extend(group['flashcards'])
    return flat_flashcards

def generate_anki_import_file(flashcards, filename="anki_import.txt"):
    """Generates a .txt file for Anki import."""
    header = "#separator:tab\n#html:true\n#notetype column:1\n\n"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(header)
        for card in flashcards:
            f.write(f"Basic\t{card.get('front', '')}\t{card.get('back', '')}\n")
    return filename

#// ============================================
# The following code converts the codebase into a Flask endpoint to process pdfs into flashcards and return them
@app.route('/process_pdf', methods=['POST'])
def process_pdf():
    if 'pdf' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['pdf']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    # Process PDF and return flashcards
    flashcards = process_pdf_to_flashcards(file)
    return jsonify(flashcards)

#// ============================================
# The following code converts the codebase into a Flask endpoint to process flashcards into an import file, and return it
@app.route('/import_file', methods=['POST'])
def create_import_file():
    data = request.get_json()
    nested_flashcards = data.get('flashcardPages')

    if not nested_flashcards:
        return jsonify({"error": "Flashcards data is required"}), 400

    try:
        flat_flashcards = flatten_flashcards(nested_flashcards)
        filename = generate_anki_import_file(flat_flashcards)
        return send_file(filename, as_attachment=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================
# Pydantic Models for Structured Outputs
# ============================================
class Flashcard(BaseModel):
    front: str
    back: str

class FlashcardSet(BaseModel):
    flashcards: list[Flashcard]

# ============================================
# Example Usage
# ============================================
if __name__ == "__main__":
    # flashcard_deck = process_pdf_to_flashcards("TFile1.pdf")  # Change filename as needed
    # flashcards = flatten_flashcards(flashcard_deck)
    # generate_anki_import_file(flashcards, filename="anki_import.txt")
    # print(f"Flashcard deck: {flashcard_deck}")
    app.run(debug=True)
