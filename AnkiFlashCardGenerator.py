import os
import pdfplumber
import json
import requests
from flask import Flask, request, send_file, jsonify
from werkzeug.utils import secure_filename
from openai import OpenAI
from pydantic import BaseModel

# Creates a Flask application
app = Flask(__name__)

# ============================================
# OpenAI API Client
# ============================================
client = OpenAI()
OPENAI_API_KEY="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"


# Function to create a message in a thread
def create_message(client, thread_id, content):
    return client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=content
    )

# Function to create an assistant
def create_assistant(client, name, instructions, tools, model):
    return client.beta.assistants.create(
        name=name,
        instructions=instructions,
        tools=tools,
        model=model
    )

# Function to create a thread
def create_thread(client):
    return client.beta.threads.create()

# Function to create and poll a run
def create_and_poll_run(client, thread_id, assistant_id, instructions, response_format=None):
    return client.beta.threads.runs.create_and_poll(
        thread_id=thread_id,
        assistant_id=assistant_id,
        instructions=instructions,
        response_format=response_format
    )

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

    response = requests.post(url, json=payload, headers=headers, verify=True)
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
# OpenAI Tools for Structured Outputs
# ============================================
class CardReview(BaseModel):
    analysis: str
    score: int

class RefinedFlashcard(BaseModel):
    front: str
    back: str

reviewer_Tools = [{
    "type": "function",
    "function": {
        "name": "anaylze_flashcard",
        "description": "Provide an in depth analysis of the flashcard.",
        "parameters": {
            "type": "object",
            "properties": {
                "analysis": {
                    "type": "string",
                    "description": "Strenghts and weaknesses of the flashcard."
                }
            },
            "required": [
                "analysis"
            ],
            "additionalProperties": False
        },
        "strict": True
    }
},
{
    "type": "function",
    "function": {
        "name": "card_score",
        "description": "Provide a score for the flashcard.",
        "parameters": {
            "type": "object",
            "properties": {
                "score": {
                    "type": "integer",
                    "description": "Integer between 1-10 e.g. 4"
                }
            },
            "required": [
                "score"
            ],
            "additionalProperties": False
        },
        "strict": True
    }
}]


def aggregate_flashcard(analysis, score, flashcard: dict, delegate, thread_id) -> dict:
    # Build the aggregation message using the review analysis to refine the card
    content = (
        f"The current flashcard is:\n"
        f"Front: {flashcard['front']}\n"
        f"Back: {flashcard['back']}\n\n"
        f"Review Analysis: {analysis} with score {score}\n\n"
        "Please refine this flashcard."
    )

    response_format={
        'type': 'json_schema',
        'json_schema': 
        {
            "name":"whocares", 
            "schema": RefinedFlashcard.model_json_schema()
        }
    }

    run = create_and_poll_run(client, thread_id, delegate.id, content, response_format=response_format)
    
    messages = client.beta.threads.messages.list(thread_id=thread_id)
    # print(messages)
    for msg in reversed(messages.data):
        for content in msg.content:
            if content.type == 'text':
                text_content = json.loads(content.text.value)
                front = text_content.get('front')
                back = text_content.get('back')

    print(f"\n Refined flashcard: {front}, {back}\n")

    refined = {
    'front': front,
    'back': back
    }

    return refined

def review_flashcard(flashcard: dict, assistant, thread_id):
    # Build the review message
    content = (
        f"Review the following flashcard:\n"
        f"Front: {flashcard['front']}\n"
        f"Back: {flashcard['back']}\n\n"
        "Return 'analysis' (a brief review) and 'score' (an integer rating from 1 to 10)."
    )
    # print("Inside review_flashcard")

    response_format={
        'type': 'json_schema',
        'json_schema': 
        {
            "name":"whocares", 
            "schema": CardReview.model_json_schema()
        }
    } 

    run = create_and_poll_run(client, thread_id, assistant.id, content, response_format=response_format)
    # print(run)
    # Assume run returns a dict with a key 'response_text'
    
    # List all messages in the thread (Test)
    messages = client.beta.threads.messages.list(thread_id=thread_id)
    # print(messages)
    for msg in reversed(messages.data):
        for content in msg.content:
            if content.type == 'text':
                text_content = json.loads(content.text.value)
                analysis = text_content.get('analysis')
                score = text_content.get('score')

    # print(f"Analysis: {analysis}, Score: {score}")
    
    return analysis, score


def process_flashcard_refinement(flashcard: dict, delegate, assistant, threshold: int = 8) -> dict:
    # Create a unique thread for this flashcard's refinement ecosystem
    thread = create_thread(client)

    # print(thread)

    print(f"Original flashcard: {flashcard}")

    current_flashcard = flashcard

    while True:
        analysis, score = review_flashcard(current_flashcard, assistant, thread.id)
        print(f"\nReview output: {analysis}, {score}\n")
        if score >= threshold:
            break
        # Use delegate to refine the flashcard based on the review analysis
        current_flashcard = aggregate_flashcard(analysis, score, current_flashcard, delegate, thread.id)
    
    return current_flashcard


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
        # output_file = generate_anki_import_file(flat_flashcards)
        # print(f"✅ Flashcards successfully generated and saved to: {output_file}")
        return flat_flashcards
    except Exception as e:
        print(f"❌ Error occurred: {e}")


# Example usage:
if __name__ == "__main__":
    # flashcard_deck = test_local_pdf_processing("TFile1.pdf")  # Change filename as needed
    # print(flashcard_deck)

    flashcard_deck = [{'front': 'What is the course code for Database Management Systems?', 'back': 'CMPSC 431W'}, {'front': 'What is the main topic covered in the course CMPSC 431W?', 'back': 'Database Management Systems'}, {'front': "What is the focus of the section titled 'Conceptual Database Design'?", 'back': 'The focus is on designing databases at a conceptual level, often using models like the ER Model.'}, {'front': 'What model is used in Conceptual Database Design?', 'back': 'ER Model (Entity-Relationship Model)'}, {'front': 'Who are the contributors credited for the slides in the course?', 'back': 'Jiannan Wang@SFU and Dan Suciu@UW'}, {'front': 'What is a database?', 'back': 'A database is a collection of data designed to support applications.'}, {'front': 'What is the Relational Model?', 'back': 'The Relational Model is a common data model used by Database Management Systems (DBMS).'}, {'front': 'What are Relational Algebra and Relational Calculus?', 'back': 'Relational Algebra and Relational Calculus are formal query languages used in database systems.'}, {'front': 'What is SQL?', 'back': 'SQL (Structured Query Language) is a concrete way (Domain-Specific Language) to communicate with a DBMS.'}, {'front': 'What is a key consideration when designing a database for an application?', 'back': "A key consideration is how to design the database to effectively support the application's requirements."}, {'front': 'What are the key questions to consider when designing a database?', 'back': '1. How to figure out the database design? 2. What tables to create? 3. Which attributes should be added to each table? 4. What are the relationships between tables? 5. What constraints do tables have to follow?'}, {'front': 'What is the first step in figuring out a database design?', 'back': 'Understanding the requirements and purpose of the database.'}, {'front': 'What should be determined when deciding which tables to create?', 'back': 'The entities and their relationships that need to be represented in the database.'}, {'front': 'What is important when adding attributes to each table?', 'back': 'Ensuring that each attribute is relevant to the entity and supports the data requirements.'}, {'front': 'What needs to be identified regarding the relationships between tables?', 'back': 'The type of relationships (e.g., one-to-one, one-to-many, many-to-many) and how they are implemented (e.g., foreign keys).'}, {'front': 'What constraints should tables follow in a database design?', 'back': 'Constraints such as primary keys, foreign keys, unique constraints, and data type constraints to ensure data integrity.'}]

    # Create OpenAI Assistant (Reviewer) and Delegate (Aggregator) models   
    delegate_instructions = (
        "You are a flashcard aggregator agent. Based on the review analysis, refine the flashcard and output only "
        "the refined flashcard as JSON with keys 'front' and 'back'."
    )
    reviewer_instructions = (
        "You are a flashcard reviewer agent trained on the SuperMemo principles for effective learning. "
        "Review flashcards and output a JSON with keys 'analysis' (your review text) and card 'score' (an integer 1-10)."
    )

    card_Aggregator = create_assistant(client, "Flashcard Aggregator Agent", delegate_instructions, [], "gpt-4o")
    openai_Committee_Assistant1 = create_assistant(client, "Flashcard Reviewer Agent", reviewer_instructions, [], "gpt-4o")

    # print(card_Aggregator)
    # print(openai_Committee_Assistant1)

    # # Process each flashcard through its refinement ecosystem
    refined_deck = []
    for card in flashcard_deck:
        # print(f"Refining card: {card}")
        refined_card = process_flashcard_refinement(card, card_Aggregator, openai_Committee_Assistant1, threshold=8)
        refined_deck.append(refined_card)
    
    
    # print(f"Final refined card: {refined_card}\n")

    print(refined_deck)