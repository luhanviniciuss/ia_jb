from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import sqlite3
import json
import os
import re
import google.generativeai as genai

app = Flask(__name__)
CORS(app) # Simplificado para o padrão

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = '*'
    response.headers['Access-Control-Allow-Methods'] = '*'
    return response

@app.before_request
def log_request_info():
    app.logger.info('Headers: %s', request.headers)
    # app.logger.info('Body: %s', request.get_data())

def get_context(query, history=None):
    db_path = r'c:\Users\luhan.vinicius\Desktop\modelo_ia\documentos.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Melhora na detecção de contexto
    if history:
        topic_context = ""
        for msg in reversed(history):
            content = msg.get('content', '')
            found = re.findall(r'\b[a-zA-Z]{2,4}\s?\d{0,3}\b', content)
            if found:
                topic_context = " ".join(found)
                break
        
        if topic_context and len(query.split()) <= 4:
            query = f"{topic_context} {query}"

    clean_query = query.lower().replace('?', '').replace('!', '').replace('.', '').replace(',', '').replace('-', ' ')
    words = clean_query.split()
    
    all_results = []
    stopwords = ["qual", "o", "a", "de", "do", "da", "em", "um", "uma", "para", "com", "no", "na", "os", "as", "dos", "das", "quanto", "tempo", "rota", "mais", "quem", "é", "motorista"]
    search_terms = [w for w in words if w not in stopwords and len(w) > 1]
    
    if len(words) >= 2:
        for i in range(len(words)-1):
            if words[i].isalpha() and len(words[i]) >= 2 and words[i+1].isdigit():
                search_terms.insert(0, words[i] + words[i+1])
    
    if not search_terms: search_terms = [clean_query]

    for w in search_terms:
        score_query = f"(CASE WHEN conteudo LIKE ? THEN 50 ELSE 0 END) + (CASE WHEN conteudo_limpo LIKE ? THEN 30 ELSE 0 END)"
        params = [f"%{w}%", f"%{w}%"]
        
        try:
            sql = f"SELECT conteudo FROM documentos WHERE ({score_query}) > 0 ORDER BY ({score_query}) DESC LIMIT 60"
            cursor.execute(sql, params + params)
            results = cursor.fetchall()
            for r in results:
                all_results.append(r[0])
        except Exception as e:
            print(f"Erro na busca por '{w}': {e}")
    
    conn.close()
    unique_results = list(dict.fromkeys(all_results))
    context = "\n\n".join(unique_results[:60])
    return context

# CONFIGURAÇÃO DO GEMINI
GEMINI_API_KEY = "AIzaSyBQuxkHrEJkn_CdrVDlv46QQ39HncKvgKw"
genai.configure(api_key=GEMINI_API_KEY)

@app.route('/ask', methods=['POST', 'OPTIONS'])
def ask():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    
    data = request.json
    question = data.get('question')
    history = data.get('history', [])
    
    if not question:
        return jsonify({"error": "Pergunta não fornecida"}), 400
    
    context = get_context(question, history)
    
    def generate():
        model = genai.GenerativeModel("gemini-flash-latest")
        
        history_text = ""
        if history:
            history_text = "HISTÓRICO RECENTE:\n"
            for msg in history:
                role = "Gestor" if msg.get('role') == 'user' else "IA"
                history_text += f"{role}: {msg.get('content', '')}\n"
        
        prompt = f"""
        Você é o Especialista em Logística e Processos do Grupo JB.
        
        {history_text}

        LIBERDADE E RIGIDEZ:
        1. Responda APENAS o campo específico que foi perguntado.
        2. Se pediu o parceiro, dê APENAS o parceiro. Se pediu os motoristas, dê APENAS os motoristas.
        3. É PROIBIDO listar informações extras que não foram solicitadas na pergunta atual.
        4. Use o histórico apenas para identificar de qual rota estamos falando.
        5. Sem introduções ou conclusões. Vá direto ao dado.
        6. Fidelidade total: JB Alerta às 13h (MNOP03, Pág 32).

        CONTEXTO OPERACIONAL:
        {context}

        PERGUNTA DO GESTOR:
        {question}
        """
        
        try:
            response = model.generate_content(prompt, stream=True)
            for chunk in response:
                if chunk.text:
                    yield f"data: {json.dumps({'text': chunk.text})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'text': f'Erro: {str(e)}'})}\n\n"
            yield "data: [DONE]\n\n"

    return Response(generate(), mimetype='text/event-stream')

@app.route('/')
def home():
    # Caminho para o index.html na raiz do projeto
    index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "API JB Intelligence está online! (index.html não encontrado)"

if __name__ == '__main__':
    print("Servidor completo rodando em http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
