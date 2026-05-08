from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import os
import re
import google.generativeai as genai
import hashlib
from dotenv import load_dotenv

# Carregar variáveis de ambiente
load_dotenv()

app = Flask(__name__)
app.secret_key = "jb_secret_key_intelligence" # Chave para sessões
CORS(app, supports_credentials=True) 

# Helper para hash de senha
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

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

def get_db_connection():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

def get_context(query, history=None):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
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
    stopwords = [
        "qual", "quais", "o", "a", "de", "do", "da", "em", "um", "uma", "para", "com", "no", "na", "os", "as", "dos", "das", 
        "quanto", "quantos", "tempo", "rota", "mais", "quem", "é", "são", "como", "onde", "quando", "me", "nos", "lhe", 
        "pelo", "pela", "esta", "este", "isto", "isso", "seu", "sua", "subrota", "base"
    ]
    search_terms = [w for w in words if w not in stopwords and len(w) > 1]
    
    # Detecção de códigos combinados (ex: FOR 101 -> FOR101)
    if len(words) >= 2:
        for i in range(len(words)-1):
            if words[i].isalpha() and len(words[i]) >= 2 and words[i+1].isdigit():
                search_terms.insert(0, words[i] + words[i+1])
    
    # Inverter para priorizar termos do final da frase (geralmente mais específicos)
    search_terms.reverse()
    
    if not search_terms: search_terms = [clean_query]

    # 1. BUSCA NO TREINAMENTO (APRENDIZADO ADMIN) - Prioridade Máxima
    try:
        cursor.execute("SELECT resposta_correta FROM treinamento_ia WHERE %s LIKE '%%' || pergunta || '%%' OR pergunta LIKE '%%' || %s || '%%'", (clean_query, clean_query))
        train_result = cursor.fetchone()
        if train_result:
            all_results.append(f"CONHECIMENTO VALIDADO POR ADMIN: {train_result['resposta_correta']}")
    except Exception as e: 
        print(f"Erro no treinamento: {e}")

    for w in search_terms:
        score_query = f"(CASE WHEN conteudo ILIKE %s THEN 50 ELSE 0 END) + (CASE WHEN conteudo_limpo ILIKE %s THEN 30 ELSE 0 END)"
        params = [f"%{w}%", f"%{w}%"]
        
        try:
            sql = f"SELECT conteudo FROM documentos WHERE ({score_query}) > 0 ORDER BY ({score_query}) DESC LIMIT 60"
            cursor.execute(sql, params + params)
            results = cursor.fetchall()
            for r in results:
                all_results.append(r['conteudo'])
        except Exception as e:
            print(f"Erro na busca por '{w}': {e}")
    
    conn.close()
    unique_results = list(dict.fromkeys(all_results))
    context = "\n\n".join(unique_results[:15]) # Reduzido para evitar ruído
    return context

# CONFIGURAÇÃO DO GEMINI
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

@app.route('/ask', methods=['POST', 'OPTIONS'])
def ask():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    
    data = request.json
    question = data.get('question')
    history = data.get('history', [])
    user_id = data.get('user_id')
    conversa_id = data.get('conversa_id')
    
    if not question:
        return jsonify({"error": "Pergunta não fornecida"}), 400
    
    context = get_context(question, history)
    
    # Salvar pergunta do usuário se houver conversa_id
    if conversa_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO mensagens (conversa_id, role, content) VALUES (%s, %s, %s)", 
                    (conversa_id, 'user', question))
        conn.commit()
        conn.close()

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
        6. Você tem acesso aos manuais MNOP02, MNOP03 e à Tabela de Rotas D23 (motoristas, parceiros e dias de largada).
        7. Fidelidade total: JB Alerta às 13h (MNOP03, Pág 32).
        8. SE A INFORMAÇÃO NÃO ESTIVER NO CONTEXTO, responda: "Informação não consta nos manuais ou tabelas disponíveis." Nunca invente nomes ou dados.
        9. Não responda sobre temas que não sejam do Grupo JB ou de Logística.

        CONTEXTO OPERACIONAL:
        {context}

        PERGUNTA DO GESTOR:
        {question}
        """
        
        full_response = ""
        try:
            response = model.generate_content(prompt, stream=True)
            for chunk in response:
                if chunk.text:
                    full_response += chunk.text
                    yield f"data: {json.dumps({'text': chunk.text})}\n\n"
            
            # Salvar resposta da IA se houver conversa_id
            if conversa_id:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("INSERT INTO mensagens (conversa_id, role, content) VALUES (%s, %s, %s)", 
                            (conversa_id, 'assistant', full_response))
                conn.commit()
                conn.close()

            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'text': f'Erro: {str(e)}'})}\n\n"
            yield "data: [DONE]\n\n"

    return Response(generate(), mimetype='text/event-stream')

# --- ROTAS DE AUTENTICAÇÃO E GESTÃO ---

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT id, username, role FROM usuarios WHERE username = %s AND password = %s", 
                   (username, hash_password(password)))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        return jsonify({
            "status": "success",
            "user": {"id": user['id'], "username": user['username'], "role": user['role']}
        })
    return jsonify({"status": "error", "message": "Usuário ou senha inválidos"}), 401

@app.route('/conversations', methods=['GET', 'POST'])
def handle_conversations():
    user_id = request.args.get('user_id') if request.method == 'GET' else request.json.get('user_id')
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    if request.method == 'POST':
        titulo = request.json.get('titulo', 'Nova Conversa')
        cursor.execute("INSERT INTO conversas (user_id, titulo) VALUES (%s, %s) RETURNING id", (user_id, titulo))
        conversa_id = cursor.fetchone()['id']
        conn.commit()
        conn.close()
        return jsonify({"id": conversa_id, "titulo": titulo})
    
    cursor.execute("SELECT id, titulo, data_criacao FROM conversas WHERE user_id = %s ORDER BY data_criacao DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return jsonify([{"id": r['id'], "titulo": r['titulo'], "data": r['data_criacao']} for r in rows])

@app.route('/messages/<int:conversa_id>', methods=['GET'])
def get_messages(conversa_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT role, content FROM mensagens WHERE conversa_id = %s ORDER BY timestamp ASC", (conversa_id,))
    rows = cursor.fetchall()
    conn.close()
    return jsonify([{"role": r['role'], "content": r['content']} for r in rows])

@app.route('/learn', methods=['POST'])
def learn():
    data = request.json
    pergunta = data.get('pergunta')
    resposta = data.get('resposta')
    admin_id = data.get('admin_id')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO treinamento_ia (pergunta, resposta_correta, admin_id) VALUES (%s, %s, %s) ON CONFLICT (pergunta) DO UPDATE SET resposta_correta = EXCLUDED.resposta_correta", 
                    (pergunta, resposta, admin_id))
        conn.commit()
        return jsonify({"status": "success", "message": "IA aprendeu com sucesso!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()

@app.route('/')
def home():
    index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "API JB Intelligence Online"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
