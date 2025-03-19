from flask import Flask, request, jsonify, session, render_template, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from uuid import uuid4
import sqlite3
import os
import json
import requests
from datetime import datetime

# File processing and OCR imports
from werkzeug.utils import secure_filename
from pdf2image import convert_from_bytes
from PIL import Image
import pytesseract

# Configure Tesseract path if needed (adjust for your system)
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Change this to a secure secret key

# Use the generate endpoint for your local Ollama installation
MODEL_URL = "http://localhost:11434/api/generate"

def init_db():
    if not os.path.exists('chatbot.db'):
        conn = sqlite3.connect('chatbot.db')
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            username TEXT UNIQUE NOT NULL,
                            password TEXT NOT NULL
                          )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS chats (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER,
                            chat_id TEXT,
                            title TEXT,
                            messages TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY(user_id) REFERENCES users(id)
                          )''')
        # Create knowledge_base table for file uploads
        cursor.execute('''CREATE TABLE IF NOT EXISTS knowledge_base (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER,
                            content TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY(user_id) REFERENCES users(id)
                          )''')
        conn.commit()
        conn.close()

init_db()

def update_db():
    conn = sqlite3.connect('chatbot.db')
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE chats ADD COLUMN title TEXT")
        cursor.execute("ALTER TABLE chats ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Columns already exist
    conn.close()

update_db()

def build_prompt_from_history(history):
    """
    Concatenates the conversation history into a single prompt string.
    Each message is prefixed with its role.
    """
    prompt = ""
    for msg in history:
        if msg['role'] == "system":
            prompt += f"System: {msg['content']}\n"
        elif msg['role'] == "user":
            prompt += f"User: {msg['content']}\n"
        elif msg['role'] == "assistant":
            prompt += f"Assistant: {msg['content']}\n"
    return prompt

def chat_with_model(user_input, conversation_history):
    # On first call, add a system message if history is empty.
    if len(conversation_history) == 0:
        conversation_history.append({
            "role": "system", 
            "content": (
                "You are Dr. Akshay Karthick, a compassionate and concise doctor. "
                "Respond to patient queries with empathy and warmth using 1-2 complete sentences. "
                "Ask only 1-2 questions at a time to keep the conversation focused. "
                "Offer virtual medications when appropriate and suggest physical visits only rarely. "
                "If asked non-medical questions, respond with 'I'm a doctor, I can't answer those questions.' "
                "Keep responses under 50 words."
            )
        })
    # Append the user message
    conversation_history.append({"role": "user", "content": user_input})
    
    # Build a prompt string from the conversation history
    prompt = build_prompt_from_history(conversation_history)
    
    payload = {
        "model": "llama3.2:latest",  # Use the model name from your tags
        "prompt": prompt,
        "stream": False,
        "temperature": 0.8,
        "max_tokens": 100
    }
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(MODEL_URL, json=payload, headers=headers)
        response.raise_for_status()
        # Assuming the response JSON has a key 'response' that contains the generated text
        model_reply_text = response.json().get('response', '')
        if model_reply_text:
            # Append the assistant response to the conversation history
            conversation_history.append({"role": "assistant", "content": model_reply_text})
            return model_reply_text
        else:
            return "I'm having trouble understanding your request. Can you please rephrase it?"
    except requests.RequestException as e:
        return f"An error occurred: {e}"

@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('chat_page'))
    return render_template('index.html')

@app.route('/chat_page')
def chat_page():
    if 'user_id' not in session:
        return redirect(url_for('home'))
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    conn = sqlite3.connect('chatbot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username=?", (username,))
    user = cursor.fetchone()
    conn.close()

    if user and check_password_hash(user[2], password):
        session['user_id'] = user[0]
        session['username'] = username
        return jsonify({"success": True, "message": "Login successful"})
    return jsonify({"success": False, "message": "Invalid credentials"}), 401

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({"success": False, "message": "Username and password are required"}), 400

    hashed_password = generate_password_hash(password)
    
    conn = sqlite3.connect('chatbot.db')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
        conn.commit()
        return jsonify({"success": True, "message": "Registration successful"})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "message": "Username already exists"}), 400
    finally:
        conn.close()

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True, "message": "Logged out successfully"})

@app.route('/get_specific_chat/<chat_id>')
def get_specific_chat(chat_id):
    if 'user_id' not in session:
        return jsonify({"error": "Not logged in"}), 401

    conn = sqlite3.connect('chatbot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT messages FROM chats WHERE user_id = ? AND chat_id = ?", (session['user_id'], chat_id))
    chat = cursor.fetchone()
    conn.close()

    if chat:
        messages = json.loads(chat[0])
        session['current_chat_id'] = chat_id
        session['conversation_history'] = messages
        return jsonify({"success": True, "messages": messages})
    return jsonify({"success": False, "message": "Chat not found"}), 404

@app.route('/set_current_chat', methods=['POST'])
def set_current_chat():
    if 'user_id' not in session:
        return jsonify({"error": "Not logged in"}), 401
    
    data = request.get_json()
    chat_id = data.get('chat_id')
    
    conn = sqlite3.connect('chatbot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT messages FROM chats WHERE user_id = ? AND chat_id = ?", (session['user_id'], chat_id))
    chat = cursor.fetchone()
    conn.close()
    
    if chat:
        session['current_chat_id'] = chat_id
        session['conversation_history'] = json.loads(chat[0])
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "Chat not found"}), 404

@app.route('/chat', methods=['POST'])
def chat():
    if 'user_id' not in session:
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json()
    user_input = data.get('message')
    chat_id = data.get('chatId') or session.get('current_chat_id')
    
    # Get or create conversation history
    conn = sqlite3.connect('chatbot.db')
    cursor = conn.cursor()
    
    if not chat_id:
        chat_id = str(uuid4())
        session['current_chat_id'] = chat_id
        conversation_history = []
    else:
        cursor.execute("SELECT messages FROM chats WHERE user_id = ? AND chat_id = ?", (session['user_id'], chat_id))
        result = cursor.fetchone()
        conversation_history = json.loads(result[0]) if result else []

    # Get response from the model using the updated chat_with_model
    reply = chat_with_model(user_input, conversation_history)
    
    # Save the updated conversation history
    title = user_input[:30] + "..." if len(user_input) > 30 else user_input

    cursor.execute("SELECT COUNT(*) FROM chats WHERE user_id = ? AND chat_id = ?", (session['user_id'], chat_id))
    exists = cursor.fetchone()[0] > 0

    if exists:
        cursor.execute("UPDATE chats SET messages = ?, created_at = datetime('now') WHERE user_id = ? AND chat_id = ?",
                       (json.dumps(conversation_history), session['user_id'], chat_id))
    else:
        cursor.execute("INSERT INTO chats (user_id, chat_id, title, messages, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
                       (session['user_id'], chat_id, title, json.dumps(conversation_history)))
    
    conn.commit()
    conn.close()
    
    return jsonify({"success": True, "reply": reply, "chatId": chat_id})

@app.route('/get_chat_history', methods=['GET'])
def get_chat_history():
    if 'user_id' not in session:
        return jsonify({"error": "Not logged in"}), 401

    conn = sqlite3.connect('chatbot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, title, messages, created_at FROM chats WHERE user_id = ? ORDER BY created_at DESC", (session['user_id'],))
    chats = cursor.fetchall()
    conn.close()

    chat_history = []
    for chat in chats:
        chat_history.append({
            "chat_id": chat[0],
            "title": chat[1],
            "messages": json.loads(chat[2]),
            "created_at": chat[3]
        })

    return jsonify({"success": True, "chat_history": chat_history})

@app.route('/new_chat', methods=['POST'])
def new_chat():
    session['conversation_history'] = []
    session.pop('current_chat_id', None)
    return jsonify({"success": True, "message": "New chat started"})

# --------------------- File Upload and Text Extraction ------------------------

def extract_text_from_image(image):
    return pytesseract.image_to_string(image)

def extract_text_from_pdf(file):
    images = convert_from_bytes(file.read())
    text = ""
    for image in images:
        text += extract_text_from_image(image) + "\n"
    return text

def extract_text_from_docx(file):
    # For simplicity, treat the Word document as an image conversion (adjust as needed)
    return pytesseract.image_to_string(Image.open(file))

def extract_text_from_txt(file):
    return file.read().decode('utf-8')

def send_text_to_lm_studio(text):
    payload = {
        "model": "llama3.2:latest",
        "prompt": text,
        "stream": False,
        "temperature": 0.8,
        "max_tokens": 100
    }
    headers = {"Content-Type": "application/json"}
    response = requests.post(MODEL_URL, json=payload, headers=headers)
    return response.json()

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'user_id' not in session:
        return jsonify({"error": "Not logged in"}), 401

    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file provided"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "No file selected"}), 400

    file_extension = os.path.splitext(file.filename)[1].lower()
    try:
        if file_extension == '.pdf':
            text = extract_text_from_pdf(file)
        elif file_extension in ['.docx', '.doc']:
            text = extract_text_from_docx(file)
        elif file_extension == '.txt':
            text = extract_text_from_txt(file)
        elif file_extension in ['.jpg', '.jpeg', '.png', '.bmp', '.tiff']:
            text = extract_text_from_image(Image.open(file))
        else:
            return jsonify({"success": False, "message": "Unsupported file type"}), 400

        lm_response = send_text_to_lm_studio(text)
        if 'response' in lm_response:
            lm_reply = lm_response['response']
        else:
            return jsonify({"success": False, "message": "Failed to process text with the model"}), 500

        user_id = session['user_id']
        conn = sqlite3.connect('chatbot.db')
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO knowledge_base (user_id, content) VALUES (?, ?)", (user_id, text))
            conn.commit()
            return jsonify({"success": True, "message": "File uploaded and processed successfully", "lm_reply": lm_reply})
        except sqlite3.Error as e:
            return jsonify({"success": False, "message": str(e)}), 500
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"success": False, "message": f"Error processing file: {str(e)}"}), 500
        
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host="0.0.0.0", port=port, debug=True) 
