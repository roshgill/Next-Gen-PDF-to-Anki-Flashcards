import os
import pdfplumber
import json
import requests
from flask import Flask, request, send_file, jsonify
from werkzeug.utils import secure_filename

# Creates a Flask application
app = Flask(__name__)

# ============================================
# Synchronous Deepseek API Call
# ============================================
def deepseek_generate(prompt, page_text):
    url = "https://api.deepseek.com/chat/completions"
    headers = {"Authorization": f"Bearer sk-14060f17abef4c32ac3c9a4dc602b760"}
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": page_text},
        ],
        "stream": False
    }

    response = requests.post(url, json=payload, headers=headers)
    response_json = response.json()
    return response_json.get("choices", [{}])[0].get("message", {}).get("content", "")

# ============================================
def clean_json_response(response_str):
    """Removes markdown code fences from the response."""
    lines = response_str.splitlines()
    cleaned_lines = [line for line in lines if not line.strip().startswith("```")]
    return "\n".join(cleaned_lines)

# ============================================
# Synchronous Flashcard Generation
# ============================================
def generate_flashcards_for_page(page_number, page_text):
    """Generates flashcards for a given page's text."""
    if not page_text.strip():
        return []

    prompt = f"""
You are a highly skilled flashcard creator using the Deepseek AI model. Your task is to analyze the following text extracted from page {page_number} of a document and generate a series of flashcards to help with reviewing the material. Identify all key concepts, definitions, and essential details.

For each key point, create a flashcard with the following structure:
  - The "front" should be a concise question or prompt that encapsulates the key idea.
  - The "back" should be a succinct and accurate answer or explanation.

Your output must be in pure JSON format: a JSON array of objects, where each object has exactly two keys: "front" and "back".  
If there is no important information to review on the page, simply output an empty JSON array ([]).
"""
    response_json_str = deepseek_generate(prompt, page_text)
    cleaned_response = clean_json_response(response_json_str)
    print(cleaned_response)

    try:
        flashcards = json.loads(cleaned_response)
    except json.JSONDecodeError:
        flashcards = []
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
# Synchronous Processing Function
# ============================================
def process_pdf_to_flashcards(pdf_path):
    """Processes the PDF, making synchronous API calls."""
    pages = extract_pages_from_pdf(pdf_path)
    all_flashcards = []

    for page_number, page_text in pages:
        if page_text and page_text.strip():
            flashcards = generate_flashcards_for_page(page_number, page_text)
            all_flashcards.append(flashcards)
    
    return all_flashcards

def flatten_flashcards(nested_flashcards):
    """Flattens a nested list of flashcards into a single list."""
    return [flashcard for group in nested_flashcards for flashcard in group]

# ============================================
def generate_anki_import_file(flashcards, filename="anki_import.txt"):
    """Generates a .txt file for Anki import."""
    header = "#separator:tab\n#html:true\n#notetype column:1\n\n"

    with open(filename, "w", encoding="utf-8") as f:
        f.write(header)
        for card in flashcards:
            f.write(f"Basic\t{card.get('front', '')}\t{card.get('back', '')}\n")

    return filename

# ============================================
# Flask Endpoint
# ============================================
# @app.route('/upload', methods=['POST'])
# def upload_pdf():
#     if 'pdf' not in request.files:
#         return jsonify({'error': 'No PDF file provided'}), 400

#     file = request.files['pdf']
#     if file.filename == '':
#         return jsonify({'error': 'No selected file'}), 400

#     filename = secure_filename(file.filename)
#     file.save(filename)

#     try:
#         nested_flashcards = process_pdf_to_flashcards(filename)
#         flat_flashcards = flatten_flashcards(nested_flashcards)
#         output_file = generate_anki_import_file(flat_flashcards)
#     except Exception as e:
#         return jsonify({'error': str(e)}), 500
#     finally:
#         os.remove(filename)

#     return send_file(output_file, as_attachment=True)

# # ============================================
# # Run Flask App
# # ============================================
# if __name__ == '__main__':
#     app.run(debug=True)

def test_local_pdf_processing(pdf_filename):
    """
    Test function to process a local PDF in the same directory
    and generate flashcards without using Flask.
    """
    if not os.path.exists(pdf_filename):
        print(f"Error: File '{pdf_filename}' not found.")
        return

    print(f"Processing '{pdf_filename}'...\n")

    try:
        nested_flashcards = process_pdf_to_flashcards(pdf_filename)
        if nested_flashcards is None:
            raise ValueError("process_pdf_to_flashcards returned None")
        flat_flashcards = flatten_flashcards(nested_flashcards)
        output_file = generate_anki_import_file(flat_flashcards)
        print(f"✅ Flashcards successfully generated and saved to: {output_file}")
    except Exception as e:
        print(f"❌ Error occurred: {e}")

# Example usage:
if __name__ == "__main__":
    test_local_pdf_processing("TFile3.pdf")  # Change filename as needed
