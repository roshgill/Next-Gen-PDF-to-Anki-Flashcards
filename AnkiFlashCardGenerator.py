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

def create_completion(client, messages, response_format=None):
    completion = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        response_format=response_format
    )

    # print(completion.choices[0].message)
    return completion

# ============================================
def clean_json_response(response_str):
    """Removes markdown code fences from the response."""
    lines = response_str.splitlines()
    cleaned_lines = [line for line in lines if not line.strip().startswith("```")]
    return "\n".join(cleaned_lines)    

# ============================================
# Synchronous Flashcard Generation
# ============================================
def generate_flashcards_for_page(page_text, card_Creator, threadId, response_format):
    """Generates flashcards for a given page's text."""
    if not page_text.strip():
        return []

    message = create_message(client, threadId, page_text)
    response = create_and_poll_run(client, threadId, card_Creator.id, page_text, response_format=response_format)

    # print(f"Response: {response}")

    messages = client.beta.threads.messages.list(thread_id=threadId)
    # print(f"Messages: {messages}")

    # print(f"Messages: {messages}")
    for msg in reversed(messages.data):
        if msg.role == "assistant":
            for block in msg.content:
                if block.type == "text":
                    try:
                        data = json.loads(block.text.value)
                        if "flashcards" in data:
                            flashcards = data["flashcards"]
                            break
                    except Exception as e:
                        print("Error parsing JSON:", e)

    # print(f"\n\nFlashcards: {flashcards}")

    # try:
    #     flashcards = response
    #     print(flashcards)
    # except json.JSONDecodeError:
    #     flashcards = []
    return flashcards

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
- Each object **must contain exactly two keys**: `"front"` and `"back"`.
- **Do not escape characters unnecessarily** (e.g., no `\'` for apostrophes).
- **No extra formatting or explanations.**

#### **Example Output:**
```json
[{"front":"What is an entity?","back":"A distinct real-world object."},{"front":"What is an attribute?","back":"A property of an entity."}]
"""

    initial_completion_message = [
        {
            "role": "user",
            "content": content_string
        },
        {
            "role": "user",
            "content": page_text
        }
    ]

    response = create_completion(client, initial_completion_message, response_format=response_format)

    # print(f"Response: {response}")

    flashcards = response.choices[0].message.content
    flashcards = json.loads(flashcards)

    # print(f"\nFlashcards = {flashcards}")

    return flashcards

    # DEBUG: Print messages to check structure
    # print("DEBUG: Messages Output:", messages)


    # print(f"Messages: {messages}")
    # for msg in reversed(messages.data):
    #     if msg.role == "assistant":
    #         for block in msg.content:
    #             if block.type == "text":
    #                 try:
    #                     data = json.loads(block.text.value)
    #                     if "flashcards" in data:
    #                         flashcards = data["flashcards"]
    #                         break
    #                 except Exception as e:
    #                     print("Error parsing JSON:", e)

    # print(f"\n\nFlashcards: {flashcards}")

    # try:
    #     flashcards = response
    #     print(flashcards)
    # except json.JSONDecodeError:
    #     flashcards = []

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
    "Processes the PDF, making synchronous API calls."
    pages = extract_pages_from_pdf(pdf_path)
    all_flashcards = []

    # print(pages)

#     prompt = f"""
# You are provided a document of text from a set of pdf notes. Your functionality is to analyze and understand the topic or info provided and output flashcards.

# General Guidelines for Flashcard Creation:

# * Concise: Each card should focus on a single concept, question, or fact.
# Incorrectly Formulated Knowledge - Complex and Wordy:
# Front: Where is the Dead Sea located, what is the lowest point on the Earth’s surface, and how does its salinity compare to the ocean?
# Back: The Dead Sea is located between Israel and Jordan, the Dead Shoreline is the lowest point on the Earth’s surface, and it is seven times as salty as the ocean.

# Well Formulated Knowledge - Simple and Specific:
# Front: Where is the Dead Sea located?
# Back: Between Israel and Jordan

# Front: What is the lowest point on the Earth’s surface?
# Back: The Dead Shoreline

# Front: How many times saltier is the Dead Shoreline compared to the ocean?
# Back: Seven times

# * Clear: Optimized wording will speed up learning.
# Incorrectly Formulated Knowledge - Less Optimum Item:
# Front: How do plants create their own energy from sunlight in a process involving chlorophyll and light reactions?
# Back: Photosynthesis

# Well Formulated Knowledge - More Optimum Item:
# Front: What process do plants use to make energy?
# Back: Photosynthesis
# """

#     response_format={
#         'type': 'json_schema',
#         'json_schema': 
#         {
#             "name":"whocares", 
#             "schema": FlashcardSet.model_json_schema()
#         }
#     }

    response_format= {
        'type': 'json_schema',
        'json_schema': 
        {
            "name":"whocares", 
            "schema": FlashcardSet.model_json_schema()
        }
    }

    # response_format = None

    # create_completion(client, response_format=None)

    # card_Creator = create_assistant(client, "Flashcard Creator Agent", prompt, [], "o1-mini")

    for page_number, page_text in pages:
        if page_text and page_text.strip():

            flashcards = generate_flashcards_for_page2(page_text, response_format)

            # thread = create_thread(client)

            # flashcards = generate_flashcards_for_page(page_text, card_Creator, thread.id, response_format)
            all_flashcards.append(flashcards)
    
    # print(f"All flashcards: {all_flashcards}")

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
class Flashcard(BaseModel):
    front: str
    back: str

class FlashcardSet(BaseModel):
    flashcards: list[Flashcard]

class CardReview(BaseModel):
    analysis: str
    score: int

class RefinedFlashcard(BaseModel):
    refactor_needed: bool
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
        f"{flashcard}\n"
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
    for msg in reversed(messages.data):
        for content in msg.content:
            if content.type == 'text':
                text_content = json.loads(content.text.value)
                front = text_content.get('front')
                back = text_content.get('back')

    refined = {
        'front': front,
        'back': back
    }

    print(f"\nRefined flashcard: {refined}\n")

    return refined

def review_flashcard(flashcard: dict, assistant, thread_id):
    # Build the review message
    content = (
        f"{flashcard}\n"
    )

    response_format={
        'type': 'json_schema',
        'json_schema': 
        {
            "name":"whocares", 
            "schema": CardReview.model_json_schema()
        }
    } 

    run = create_and_poll_run(client, thread_id, assistant.id, content, response_format=response_format)
    messages = client.beta.threads.messages.list(thread_id=thread_id)
    
    print(f"\n Review flashcard thread: {messages}\n")
    
    for msg in reversed(messages.data):
        for content in msg.content:
            if content.type == 'text':
                text_content = json.loads(content.text.value)
                analysis = text_content.get('analysis')
                score = text_content.get('score')
    
    return analysis, score


def process_flashcard_refinement(flashcard: dict, delegate, assistant, threshold: int = 8) -> dict:
    # Create a unique thread for this flashcard's refinement ecosystem
    thread = create_thread(client)

    print(f"\nOriginal flashcard: {flashcard}")
    current_flashcard = flashcard

    while True:
        analysis, score = review_flashcard(current_flashcard, assistant, thread.id)
        print(f"Review output: Analysis: {analysis}, Score: {score}")
        if score >= threshold:
            break
        # Use delegate to refine the flashcard based on the review analysis
        current_flashcard = aggregate_flashcard(analysis, score, current_flashcard, delegate, thread.id)
        print(f"Refined flashcard: {current_flashcard}")
    
    return current_flashcard

def review_refine_flashcard(flashcard: dict, assistant) -> dict:
    # Create a unique thread for this flashcard's refinement ecosystem
    thread = create_thread(client)

    print(f"\nOriginal flashcard: {flashcard}")

    content = (
        f"{flashcard}\n"
    )

    response_format={
        'type': 'json_schema',
        'json_schema': 
        {
            "name":"whocares",
            "schema": RefinedFlashcard.model_json_schema()
        }
    }

    run = create_and_poll_run(client, thread.id, assistant.id, content, response_format=response_format)
    
    messages = client.beta.threads.messages.list(thread_id=thread.id)
    
    print(f"\n Review flashcard thread: {messages}\n")
    
    for msg in reversed(messages.data):
        for content in msg.content:
            if content.type == 'text':
                text_content = json.loads(content.text.value)
                keep = text_content.get('refactor_needed')
                front = text_content.get('front')
                back = text_content.get('back')

    flashcard = {
    'front': front,
    'back': back
    }

    current_flashcard = flashcard

    print(f"Refactor needed: {keep}")
    print(f"Refined flashcard: {current_flashcard}")
    
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

    flashcard_deck = process_pdf_to_flashcards(pdf_filename)

    return flashcard_deck

    # try:
    #     nested_flashcards = process_pdf_to_flashcards(pdf_filename)
    #     if nested_flashcards is None:
    #         raise ValueError("process_pdf_to_flashcards returned None")
    #     # flat_flashcards = flatten_flashcards(nested_flashcards)
    #     # return flat_flashcards
    #     return nested_flashcards
    # except Exception as e:
    #     print(f"❌ Error occurred: {e}")




# Example usage:
if __name__ == "__main__":
   flashcard_deck = test_local_pdf_processing("TFile2.pdf")  # Change filename as needed

   print(f"Flashcard deck: {flashcard_deck}")

    # Test flashcard deck (From an actual output from local pdf processing)
    # flashcard_deck = [[{'front': 'What does ER stand for in the context of database design?', 'back': 'Entity-Relationship'}, {'front': 'What is an entity in an ER model?', 'back': 'An entity is a distinct object that can be identified in the domain being studied, such as a person, place, event, or concept.'}, {'front': 'What is an attribute in an ER model?', 'back': 'An attribute is a property or characteristic of an entity, such as a name or date.'}, {'front': 'What is a relationship in an ER model?', 'back': 'A relationship is an association among two or more entities.'}, {'front': 'What symbol is used to represent an entity in an ER diagram?', 'back': 'A rectangle.'}, {'front': 'What symbol is used to represent a relationship in an ER diagram?', 'back': 'A diamond.'}]]
# ,[{'front': 'What is a database?', 'back': 'A database is a collection of data organized to support applications.'}, {'front': 'What is the relational model?', 'back': 'The relational model is a common data model used by database management systems (DBMS) to organize data.'}, {'front': 'What are relational algebra and calculus?', 'back': 'Relational algebra and calculus are formal query languages used to define operations on data within the relational model.'}, {'front': 'What is SQL?', 'back': 'SQL, or Structured Query Language, is a concrete way (DSL) to communicate with a DBMS.'}, {'front': 'How do you design a database to support an application?', 'back': "You design a database by understanding the application's requirements, identifying entities, defining relationships and constraints, and structuring the database to optimize performance and support necessary operations."}]
# ,[{'front': 'Process for Determining Database Design', 'back': '1. Understand the requirements: Gather all the necessary requirements and user needs.\n2. Identify Entities: Identify key entities or objects of interest in your application (e.g., Users, Orders, Products).\n3. Determine Relationships: Determine how these entities relate to each other (e.g., a User places Orders).\n4. Define Attributes: For each entity, identify the attributes that should be stored (e.g., User has a name, email).\n5. Develop Schema: Based on entities and relationships, draft an initial database schema.\n6. Normalize Data: Ensure database is free of duplication and is efficient (Normalization process).\n7. Apply Constraints: Implement constraints (e.g., primary keys, foreign keys) to maintain data integrity.\n8. Iterate and Refine: Continuously improve based on testing and feedback.'}, {'front': 'Example Tables to Create', 'back': '1. Users - Attributes might include: UserID, Username, Password, Email, CreatedAt.\n2. Products - Attributes might include: ProductID, ProductName, Description, Price, StockQuantity.\n3. Orders - Attributes might include: OrderID, UserID, OrderDate, TotalAmount.\n4. OrderItems - Attributes might include: OrderItemID, OrderID, ProductID, Quantity.'}, {'front': 'Relationships Between Tables', 'back': '1. Users to Orders: One-to-Many relationship (A user can make multiple orders).\n2. Orders to OrderItems: One-to-Many relationship (An order can have multiple items).\n3. Products to OrderItems: Many-to-Many relationship (A product can be in many orders, and an order can contain many products). Typically resolved with a junction table like OrderItems.'}, {'front': 'Constraints for Each Table', 'back': '1. Primary Key: Each table should have a unique identifier, typically an ID field like UserID or ProductID.\n2. Foreign Key: Use foreign keys to establish relationships between tables, e.g., OrderID in the OrderItems table.\n3. Unique Constraints: Ensure certain fields remain unique, such as Email in the Users table.\n4. Check Constraints: Use to enforce domain integrity, such as ensuring Price in Products table is non-negative.'}, {'front': 'Common Mistakes in Database Design', 'back': '1. Redundancy: Do not have duplicate data, which normalization aims to prevent.\n2. Over-normalization: While normalization reduces redundancy, overdoing it can make the database complex and slow.\n3. Lack of Consistency: Ensure naming conventions and data types are consistent across tables.\n4. Ignoring Future Needs: Design with scalability and future needs in mind, allowing for feature expansion.'}]]

    # flashcard_deck = [[{'front': 'What is a database?', 'back': 'A database is a collection of data organized to support applications.'}, {'front': 'What is the relational model?', 'back': 'The relational model is a common data model used by database management systems (DBMS) to organize data.'}, {'front': 'What are relational algebra and calculus?', 'back': 'Relational algebra and calculus are formal query languages used to define operations on data within the relational model.'}, {'front': 'What is SQL?', 'back': 'SQL, or Structured Query Language, is a concrete way (DSL) to communicate with a DBMS.'}, {'front': 'How do you design a database to support an application?', 'back': "You design a database by understanding the application's requirements, identifying entities, defining relationships and constraints, and structuring the database to optimize performance and support necessary operations."}]]

    # # Create OpenAI Assistant (Reviewer) and Delegate (Aggregator) models   
    # # delegate_instructions = (
    # # "You are a flashcard aggregator agent. Your task is to refine flashcards based on analysis from the reviewer agent."
    # # )

    # reviewer_instructions = (
    # "You are a flashcard reviewer agent trained in the SuperMemo method. Your job is to make cards as SHORT as possible while ensuring they remain clear. Remember, a good flashcard is brief."
    # "\n\n### Rules for Refinement:"
    # "\n- **Shorten Aggressively**: If a card is longer than 10 words, CUT IT DOWN."
    # "\n- **Remove All Unnecessary Words**: No fluff, no explanations—just the key fact."
    # "\n- **No Expanding**: You are not allowed to 'improve' the explanation—ONLY shorten."
    # "\n\n### Examples of How to Fix Cards:"
    # "\n❌ Bad: 'A database is a structured collection of data stored electronically, organized to easily retrieve, manage, and update information, typically supporting applications and user queries.'"
    # "\n✅ Good: 'A database stores data.'"
    # "\n"
    # "\n❌ Bad: 'Relational algebra and calculus are formal query languages used to define operations on data within the relational model.'"
    # "\n✅ Good: 'Formal query languages for databases.'"
    # "\n"
    # "\n❌ Bad: 'How do you design a database to support an application?'"
    # "\n✅ Good: 'What is the first step in database design?'"
    # )

    # card_Aggregator = create_assistant(client, "Flashcard Aggregator Agent", delegate_instructions, [], "gpt-4o-mini")
    # openai_Committee_Assistant1 = create_assistant(client, "Flashcard Reviewer Agent", reviewer_instructions, [], "o1-mini")

    # print("\n--- Original Flashcards ---")
    # for card in flashcard_deck:
    #     print(card)

    # refined_deck = []
    # for deck in flashcard_deck:
    #     for card in deck:
    #         # refined_card = process_flashcard_refinement(card, openai_Committee_Assistant1, threshold=8)
    #         refined_card = review_refine_flashcard(card, openai_Committee_Assistant1)
    #         refined_deck.append(refined_card)

    # print("\n--- Final Refined Flashcards ---")
    # for card in refined_deck:
    #     print(card)
