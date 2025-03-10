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
import redis
import base64

# Creates a Flask application
app = Flask(__name__)
CORS(app)

# r = redis.Redis(
#   host='charmed-crayfish-51052.upstash.io',
#   port=6379,
#   password='AcdsAAIjcDFjYWEwNjVmMmRiODg0Yjg1OTI3ZjEzZDgzOTdkNDQ3NXAxMA',
#   ssl=True
# )

# Increase maximum request size (e.g., 50MB)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024 * 10  # 500MB

# upstash_key = os.environ["UPSTASH_KEY"]
openai_api_key = os.environ["OPENAI_API_KEY"]
# ============================================
# OpenAI API Client
# ============================================
client = OpenAI(api_key=openai_api_key)

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
        model="o1",
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
def generate_flashcards_for_page2(flashcard_type, page_text, response_format):
    """Generates flashcards for a given page's text."""
    if not page_text.strip():
        return []

    if flashcard_type == "basic":
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
    else:
        content_string = """You will create **cloze-style flashcards** from the text I provide.

### OBJECTIVE:
- **Apply the Minimum Information Principle**: each card should contain only **one small piece** of knowledge (avoid overloading a single card).
- Focus on **key information, definitions, or essential terms** in the text.
- Generate **concise, natural-sounding statements** (avoid question formats).
- Each flashcard must hide a **single, short word or phrase** inside `{{c1::...}}`.

### AVOID:
1. **Overly Long Deletions**: The hidden text should typically be **a single word or short phrase**.
2. **Questions**: Do not produce lines like "What is X?"; instead, use declarative statements.

### STRICT GUIDELINES:
1. **Cloze Format**:
   - Enclose the hidden content in `{{c1:: ... }}`.
   - Hide **exactly one** significant term or short phrase per card.
   - Avoid trivial deletions (e.g., hiding “the,” “and,” or single letters).

2. **Output Requirements**:
   - Return **pure JSON only**: a valid **JSON array** of objects.
   - Each object has a `"front"` key, for example:  
     `{"front": "Sentence with {{c1::hidden content}}."}`
   - **No extra formatting**: no Markdown, no code fences, and no explanations.
   - **No unnecessary escaping** (e.g., don’t escape apostrophes).
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

        total_pages = len(pdf.pages)
        if total_pages > 100:
            return {"error": f"PDF has {total_pages} pages. Limit is 100."}

        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append((i, text))
    return pages

# ============================================
# Asynchronous Processing Functions
# ============================================
async def generate_flashcards_for_page_async(flashcard_type, page_text, response_format):
    """Asynchronously generates flashcards for a single page by running the synchronous function in a separate thread."""
    return await asyncio.to_thread(generate_flashcards_for_page2, flashcard_type, page_text, response_format)

async def process_pdf_to_flashcards_async(flashcard_type, pdf_path):
    """Processes the PDF asynchronously, concurrently generating flashcards for each page."""
    pages = extract_pages_from_pdf(pdf_path)
    if (flashcard_type == "basic"):
        schema = FlashcardSet.model_json_schema()
    else:
        schema = ClozeFlashcardSet.model_json_schema()

    response_format = {
        'type': 'json_schema',
        'json_schema': {
            "name": "whocares",
            "schema": schema
        }
    }
    tasks = [
        generate_flashcards_for_page_async(flashcard_type, page_text, response_format)
        for _, page_text in pages if page_text.strip()
    ]
    results = await asyncio.gather(*tasks)
    all_flashcards = []
    for res in results:
        all_flashcards.append(res)
    return all_flashcards

async def generate_flashcards_for_image_async(flashcard_type, image, response_format):
    """Generates flashcards for a single image."""
    return await asyncio.to_thread(generate_flashcards_for_image, flashcard_type, image, response_format)

def generate_flashcards_for_image(flashcard_type, image, response_format):
    if flashcard_type == "basic":
        content_string = """Your job is to extract flashcards from the provided image.

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
    else:
        content_string = """You will create **cloze-style flashcards** from the image I provide.

    ### OBJECTIVE:
    - **Apply the Minimum Information Principle**: each card should contain only **one small piece** of knowledge (avoid overloading a single card).
    - Focus on **key information, definitions, or essential terms** in the text.
    - Generate **concise, natural-sounding statements** (avoid question formats).
    - Each flashcard must hide a **single, short word or phrase** inside `{{c1::...}}`.

    ### AVOID:
    1. **Overly Long Deletions**: The hidden text should typically be **a single word or short phrase**.
    2. **Questions**: Do not produce lines like "What is X?"; instead, use declarative statements.

    ### STRICT GUIDELINES:
    1. **Cloze Format**:
    - Enclose the hidden content in `{{c1:: ... }}`.
    - Hide **exactly one** significant term or short phrase per card.
    - Avoid trivial deletions (e.g., hiding “the,” “and,” or single letters).

    2. **Output Requirements**:
    - Return **pure JSON only**: a valid **JSON array** of objects.
    - Each object has a `"front"` key, for example:  
        `{"front": "Sentence with {{c1::hidden content}}."}`
    - **No extra formatting**: no Markdown, no code fences, and no explanations.
    - **No unnecessary escaping** (e.g., don’t escape apostrophes).
    """
    initial_completion_message = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": content_string,
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image}"},
                },
            ],
        }
    ]

    response = create_completion(client, initial_completion_message, response_format=response_format)
    flashcards = response.choices[0].message.content
    flashcards = json.loads(flashcards)
    return flashcards
    

async def process_images_to_flashcards_async(flashcard_type, images):
    """Processes images into flashcards."""
    if (flashcard_type == "basic"):
        schema = FlashcardSet.model_json_schema()
    else:
        schema = ClozeFlashcardSet.model_json_schema()
    
    response_format = {
        'type': 'json_schema',
        'json_schema': {
            "name": "whocares",
            "schema": schema
        }
    }

    tasks = [
        generate_flashcards_for_image_async(flashcard_type, image, response_format)
        for image in images
    ]

    results = await asyncio.gather(*tasks)
    all_flashcards = []
    for res in results:
        all_flashcards.append(res)
    return all_flashcards


def process_pdf_to_flashcards(flashcard_type, pdf_path):
    """Wrapper to run the asynchronous PDF processing function."""
    return asyncio.run(process_pdf_to_flashcards_async(flashcard_type, pdf_path))

def process_images_to_flashcards(flashcard_type, images):
#     """Processes images into flashcards."""
    return asyncio.run(process_images_to_flashcards_async(flashcard_type, images))

def flatten_flashcards(nested_flashcards):
    """Flattens a nested list of flashcards into a single list."""
    flat_flashcards = []
    for group in nested_flashcards:
        if 'flashcards' in group:
            flat_flashcards.extend(group['flashcards'])
    return flat_flashcards

def generate_anki_import_file(flashcards, card_type, filename="anki_import.txt"):
    """Generates a .txt file for Anki import."""
    header = "#separator:tab\n#html:true\n#notetype column:1\n\n"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(header)
        for card in flashcards:
            if card_type == "cloze":
                f.write(f"Cloze\t{card.get('front', '')}\n")
            else:
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

    card_type = request.form.get('cardType')
    if card_type not in ['basic', 'cloze']:
        return jsonify({"error": "Invalid card type"}), 400

    # Process PDF and return flashcards
    flashcards = process_pdf_to_flashcards(card_type, file)
    return jsonify(flashcards)

#// ============================================
# The following code converts the codebase into a Flask endpoint to process flashcards into an import file, and return it
@app.route('/import_file', methods=['POST'])
def create_import_file():
    try:
        # Receive form data from frontend
        flashcards_str = request.form.get('flashcardPages')
        card_type = request.form.get('cardType')

        print("Received flashcards:", flashcards_str)  # Debugging
        print("Received card type:", card_type)  # Debugging

        if not flashcards_str:
            return jsonify({"error": "Flashcards data is required"}), 400

        if card_type not in ['basic', 'cloze']:
            return jsonify({"error": "Invalid card type"}), 400        

        # Parse flashcards from form data
        nested_flashcards = json.loads(flashcards_str)
        print("Parsed flashcards:", nested_flashcards)  # Debugging

        # Flatten flashcards
        flat_flashcards = flatten_flashcards(nested_flashcards)
        print("Flattened flashcards:", flat_flashcards)  # Debugging

        # Define output path for Anki import file
        upload_dir = "/home/RoshanAnkiX/mysite/uploads"
        # upload_dir = "/Users/roshgill/Desktop/venv/uploads"
        
        os.makedirs(upload_dir, exist_ok=True)  # Ensure directory exists
        output_file = os.path.join(upload_dir, "anki_import.txt")

        # Generate Anki import file
        generate_anki_import_file(flat_flashcards, card_type, filename=output_file)
        print("Generated Anki file:", output_file)  # Debugging

        # Ensure the file exists before sending
        if not os.path.exists(output_file):
            return jsonify({"error": "Generated file not found"}), 500

        # Return the generated Anki import file
        return send_file(output_file, as_attachment=True)

    except Exception as e:
        print("Error occurred:", str(e))  # Debugging
        return jsonify({"error": str(e)}), 500

#// ============================================
# The following code takes in a JSON package of base64 encoded images and returns flashcards (mostly for handwritten notes and similar use cases)
@app.route('/process_images', methods=['POST'])
def process_images():
    uploaded_files = request.files.getlist("images")  # Get multiple files
    card_type = request.form.get('cardType')

    if not uploaded_files:
        return jsonify({"error": "No images uploaded"}), 400

    images_b64 = []
    for file in uploaded_files:
        encoded = base64.b64encode(file.read()).decode("utf-8")  # Convert to base64 on the backend
        images_b64.append(encoded)

    print("Received images:", images_b64)  # Debugging

    # Process images and return flashcards
    flashcards = process_images_to_flashcards(card_type, images_b64)
    print("Generated flashcards:", flashcards)  # Debugging
    return jsonify(flashcards)


# ============================================
# Pydantic Models for Structured Outputs
# ============================================
class Flashcard(BaseModel):
    front: str
    back: str

class FlashcardSet(BaseModel):
    flashcards: list[Flashcard]

class ClozeFlashcard(BaseModel):
    front: str

class ClozeFlashcardSet(BaseModel):
    flashcards: list[ClozeFlashcard]

# ============================================
# Example Usage
# ============================================
if __name__ == "__main__":
    # image = process_pdf_to_flashcards("cloze", "ExampleImage.png")  # Change filename as needed
    # image1 = open("Image1.jpeg", "rb")
    # image2 = open("Image2.jpeg", "rb")
    # image3 = open("Image3.jpeg", "rb")
    # images = [
    #     base64.b64encode(image1.read()).decode("utf-8")
    #     # base64.b64encode(image2.read()).decode("utf-8"),
    #     # base64.b64encode(image3.read()).decode("utf-8")
    # ]

    # print(f"Generated flashcards: {images}")


    # flashcards = process_images_to_flashcards("basic", images)
    # print(f"Generated flashcards: {flashcards}")

    # encoded_images = base64.b64encode(image.read()).decode("utf-8")
    # print(encoded_images)

    # response_format = {
    #     'type': 'json_schema',
    #     'json_schema': {
    #         "name": "whocares",
    #         "schema": FlashcardSet.model_json_schema()
    #     }
    # }

    # messages= [
    #     {
    #         "role": "user",
    #         "content": [
    #             {
    #                 "type": "text",
    #                 "text": "Create flashcards from this image.",
    #             },
    #             {
    #                 "type": "image_url",
    #                 "image_url": {"url": f"data:image/jpeg;base64,{encoded_images}"},
    #             },
    #         ],
    #     }
    # ]

    # completion = create_completion(client, messages, response_format=response_format)
    # print(completion.choices[0].message.content)

    # flashcards = flatten_flashcards(flashcard_deck)
    # generate_anki_import_file(flashcards, "cloze", filename="anki_import.txt")
    # print(f"Flashcard deck: {flashcard_deck}")
    app.run(debug=True)
