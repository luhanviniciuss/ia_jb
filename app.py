from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import requests
import json

app = Flask(__name__)
CORS(app) # Permite que o frontend acesse o backend

def get_context(query):
    db_path = 'documentos.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    words = [w for w in query.split() if len(w) > 3]
    if not words: words = [query]
    
    context_parts = []
    for word in words:
        try:
            cursor.execute("SELECT conteudo FROM documentos WHERE documentos MATCH ? LIMIT 2", (word,))
        except sqlite3.OperationalError:
            cursor.execute("SELECT conteudo FROM documentos WHERE conteudo LIKE ? LIMIT 2", (f'%{word}%',))
        
        results = cursor.fetchall()
        for r in results:
            if r[0] not in context_parts:
                context_parts.append(r[0])
    
    conn.close()
    return "\n\n".join(context_parts)

def ask_ollama(question, context):
    url = "http://localhost:11434/api/generate"
    prompt = f"""
Você é um assistente da empresa Grupo JB. Use o contexto abaixo para responder à pergunta do usuário de forma profissional e precisa.

CONTEXTO:
{context}

PERGUNTA:
{question}
"""
    
    data = {
        "model": "meu-bot",
        "prompt": prompt,
        "stream": False
    }

    try:
        response = requests.post(url, json=data, timeout=120)
        if response.status_code == 200:
            return response.json().get('response', 'Sem resposta do modelo.')
        else:
            return f"Erro na API do Ollama: {response.status_code}"
    except Exception as e:
        return f"Erro: Verifique se o Ollama está rodando. {e}"

@app.route('/ask', methods=['POST'])
def ask():
    data = request.json
    question = data.get('question')
    if not question:
        return jsonify({"error": "Pergunta não fornecida"}), 400
    
    context = get_context(question)
    if not context:
        context = get_context("Objetivo") # Fallback genérico
        
    answer = ask_ollama(question, context)
    return jsonify({"answer": answer})

if __name__ == '__main__':
    print("Servidor rodando em http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)
