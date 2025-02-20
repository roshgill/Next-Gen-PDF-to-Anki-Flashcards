import os
import pdfplumber
import json
import requests
import asyncio
from flask import Flask, request, send_file, jsonify
from werkzeug.utils import secure_filename
from openai import OpenAI
from pydantic import BaseModel

# Creates a Flask application
app = Flask(__name__)

# ============================================
# OpenAI API Client & API Key
# ============================================
client = OpenAI(api_key="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")

# ============================================
# Helper Functions for Thread Management
# ============================================
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

async def create_and_poll_run(client, thread_id, assistant_id, instructions, response_format=None):
    return await client.beta.threads.runs.create_and_poll(
        thread_id=thread_id,
        assistant_id=assistant_id,
        instructions=instructions,
        response_format=response_format
    )

# ============================================
# Deepseek API Call for Flashcard Generation
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

def clean_json_response(response_str):
    """Removes markdown code fences from the response."""
    lines = response_str.splitlines()
    cleaned_lines = [line for line in lines if not line.strip().startswith("```")]
    return "\n".join(cleaned_lines)

# ============================================
# Flashcard Generation and PDF Extraction
# ============================================
def generate_flashcards_for_page(page_number, page_text):
    if not page_text.strip():
        return []
    prompt = f"""
You are a highly skilled flashcard creator using the Deepseek AI model. 
Your task is to analyze the text from page {page_number} and generate flashcards. 
For each key point, create an object with "front" and "back". 
Return a JSON array of such objects.
"""
    response_str = deepseek_generate(prompt, page_text)
    cleaned_response = clean_json_response(response_str)
    print("Generated flashcards:", cleaned_response)
    try:
        flashcards = json.loads(cleaned_response)
    except json.JSONDecodeError:
        flashcards = []
    return flashcards

def extract_pages_from_pdf(pdf_path):
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append((i, text))
    return pages

def process_pdf_to_flashcards(pdf_path):
    pages = extract_pages_from_pdf(pdf_path)
    all_flashcards = []
    for page_number, page_text in pages:
        if page_text.strip():
            flashcards = generate_flashcards_for_page(page_number, page_text)
            all_flashcards.append(flashcards)
    return all_flashcards

def flatten_flashcards(nested_flashcards):
    return [flashcard for group in nested_flashcards for flashcard in group]

def generate_anki_import_file(flashcards, filename="anki_import.txt"):
    header = "#separator:tab\n#html:true\n#notetype column:1\n\n"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(header)
        for card in flashcards:
            f.write(f"Basic\t{card.get('front', '')}\t{card.get('back', '')}\n")
    return filename

# ============================================
# Structured Response JSON Schema for Review
# ============================================
review_response_format = {
    "type": "json_schema",
    "json_schema": {
        "title": "FlashcardReview",
        "type": "object",
        "properties": {
            "analysis": {
                "type": "string",
                "description": "A brief analysis of the flashcard's strengths and weaknesses."
            },
            "score": {
                "type": "integer",
                "description": "An integer rating from 1 to 10."
            }
        },
        "required": ["analysis", "score"],
        "additionalProperties": False
    }
}

# ============================================
# Flashcard Refinement Process Using Structured Responses
# ============================================
async def review_flashcard(flashcard: dict, assistant, thread_id):
    content = (
        f"Review the flashcard:\n"
        f"Front: {flashcard['front']}\n"
        f"Back: {flashcard['back']}\n\n"
        "Return a JSON with keys 'analysis' (your review) and 'score' (an integer 1-10)."
    )
    run = await create_and_poll_run(client, thread_id, assistant.id, content, response_format=review_response_format)
    response_text = run.get('response_text', '')
    cleaned = clean_json_response(response_text)
    try:
        review_data = json.loads(cleaned)
    except Exception as e:
        print(f"Error parsing review: {e}")
        review_data = {"analysis": "Error in review", "score": 0}
    return review_data

async def process_flashcard_refinement(flashcard: dict, delegate, assistant, threshold: int = 8) -> dict:
    # Create a unique thread for this flashcard's refinement ecosystem
    thread = create_thread(client)
    current_flashcard = flashcard
    while True:
        review = await review_flashcard(current_flashcard, assistant, thread.id)
        print("Review output:", review)
        if review["score"] >= threshold:
            break
        # Delegate step: use the delegate agent to refine the flashcard
        content = (
            f"Refine the flashcard based on the following review analysis:\n"
            f"Review: {review['analysis']}\n"
            f"Current flashcard:\nFront: {current_flashcard['front']}\nBack: {current_flashcard['back']}\n\n"
            "Return a JSON with keys 'front' and 'back' for the refined flashcard."
        )
        delegate_run = await create_and_poll_run(client, thread.id, delegate.id, content)
        response_text = delegate_run.get('response_text', '')
        cleaned_response = clean_json_response(response_text)
        try:
            refined_card = json.loads(cleaned_response)
        except Exception as e:
            print(f"Error parsing delegate response: {e}")
            refined_card = current_flashcard
        current_flashcard = refined_card
    return current_flashcard

# ============================================
# Main Test Function (Async)
# ============================================
async def main():
    # For testing, we'll use a dummy flashcard deck.
    flashcard_deck = [{'front': 'What is the course code for Database Management Systems?', 'back': 'CMPSC 431W'}]

    delegate_instructions = (
        "You are a flashcard aggregator agent. Based on the review analysis, refine the flashcard. "
        "Return a JSON with keys 'front' and 'back' for the refined card."
    )
    reviewer_instructions = (
        "You are a flashcard reviewer agent trained on SuperMemo principles for effective learning. "
        "Review flashcards and output a JSON with keys 'analysis' and 'score'."
    )

    card_Aggregator = create_assistant(client, "Flashcard Aggregator Agent", delegate_instructions, None, "gpt-4o")
    reviewer_Assistant = create_assistant(client, "Flashcard Reviewer Agent", reviewer_instructions, None, "gpt-4o")

    refined_deck = []
    for card in flashcard_deck:
        refined_card = await process_flashcard_refinement(card, card_Aggregator, reviewer_Assistant, threshold=8)
        refined_deck.append(refined_card)
        print("Final refined card:", refined_card)

    output_file = generate_anki_import_file(refined_deck)
    print("Anki import file generated at:", output_file)

if __name__ == "__main__":
    asyncio.run(main())
