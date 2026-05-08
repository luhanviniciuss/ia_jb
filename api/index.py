from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import os
import re
import google.generativeai as genai
import hashlib

app = Flask(__name__)
app.secret_key = "jb_secret_key_intelligence"
CORS(app, supports_credentials=True)

# Helper para hash de senha
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_db_connection():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise Exception("DATABASE_URL não configurada no Vercel.")
    
    # Limpeza e SSL para o Supabase
    if "supabase.com" in db_url:
        if "sslmode" not in db_url:
            separator = "&" if "?" in db_url else "?"
            db_url += f"{separator}sslmode=require"
    
    return psycopg2.connect(db_url)

def get_context(query, history=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        clean_query = query.lower().replace('?', '').replace('!', '').replace('.', '').replace(',', '').replace('-', ' ')
        words = clean_query.split()
        
        all_results = []
        search_terms = [w for w in words if len(w) > 2]
        search_terms.reverse()
        
        if not search_terms: search_terms = [clean_query]

        # 1. BUSCA NO TREINAMENTO
        cursor.execute("SELECT resposta_correta FROM treinamento_ia WHERE %s LIKE '%%' || pergunta || '%%' LIMIT 1", (clean_query,))
        train_result = cursor.fetchone()
        if train_result:
            all_results.append(f"CONHECIMENTO VALIDADO: {train_result['resposta_correta']}")

        # 2. BUSCA NOS DOCUMENTOS
        for w in search_terms[:3]:
            cursor.execute("SELECT conteudo FROM documentos WHERE conteudo ILIKE %s LIMIT 10", (f"%{w}%",))
            results = cursor.fetchall()
            for r in results:
                all_results.append(r['conteudo'])
        
        conn.close()
        return "\n\n".join(list(dict.fromkeys(all_results))[:15])
    except:
        return ""

# CONFIG GEMINI
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

@app.route('/api/debug')
def debug():
    try:
        conn = get_db_connection()
        conn.close()
        return jsonify({"status": "Conectado!"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT id, username, role FROM usuarios WHERE username = %s AND password = %s", 
                       (username, hash_password(password)))
        user = cursor.fetchone()
        conn.close()
        
        if user:
            return jsonify({"status": "success", "user": user})
        return jsonify({"status": "error", "message": "Login inválido"}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ask', methods=['POST'])
def ask():
    data = request.json
    question = data.get('question')
    history = data.get('history', [])
    conversa_id = data.get('conversa_id')
    user_id = data.get('user_id')

    context = get_context(question, history)

    def generate():
        model = genai.GenerativeModel("gemini-flash-latest")
        prompt = f"Contexto: {context}\n\nPergunta: {question}"
        
        try:
            response = model.generate_content(prompt, stream=True)
            full_response = ""
            for chunk in response:
                if chunk.text:
                    full_response += chunk.text
                    yield f"data: {json.dumps({'text': chunk.text})}\n\n"
            
            if conversa_id:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("INSERT INTO mensagens (conversa_id, role, content) VALUES (%s, %s, %s)", 
                               (conversa_id, 'assistant', full_response))
                conn.commit()
                conn.close()
                
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'text': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream')

@app.route('/api/conversations', methods=['GET', 'POST'])
def conversations():
    user_id = request.args.get('user_id') or (request.json.get('user_id') if request.is_json else None)
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    if request.method == 'POST':
        titulo = request.json.get('titulo', 'Nova Conversa')
        cursor.execute("INSERT INTO conversas (user_id, titulo) VALUES (%s, %s) RETURNING id", (user_id, titulo))
        chat_id = cursor.fetchone()['id']
        conn.commit()
        conn.close()
        return jsonify({"id": chat_id})
    
    cursor.execute("SELECT id, titulo FROM conversas WHERE user_id = %s ORDER BY id DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return jsonify(rows)

@app.route('/api/messages/<int:id>')
def messages(id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT role, content FROM mensagens WHERE conversa_id = %s ORDER BY id ASC", (id,))
    rows = cursor.fetchall()
    conn.close()
    return jsonify(rows)

# Exportar para o Vercel
app = app
