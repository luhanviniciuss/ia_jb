import sqlite3
import requests
import json
import sys

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
        # Aumentado o timeout para 120s para garantir
        response = requests.post(url, json=data, timeout=120)
        if response.status_code == 200:
            return response.json().get('response', 'Sem resposta do modelo.')
        else:
            return f"Erro na API do Ollama: {response.status_code}"
    except Exception as e:
        return f"Erro: Verifique se o Ollama está rodando. Detalhe: {e}"

if __name__ == "__main__":
    if len(sys.argv) > 1:
        user_query = " ".join(sys.argv[1:])
        print(f"Buscando informações sobre: {user_query}...")
        context = get_context(user_query)
        
        if not context:
            context = get_context("Objetivo")
        
        if not context:
            print("Nenhuma informação relevante encontrada no banco de dados.")
        else:
            print("Conhecimento interno localizado. Gerando resposta (pode levar um momento)...\n")
            answer = ask_ollama(user_query, context)
            print("--- RESPOSTA DO MEU-BOT ---")
            print(answer)
            print("-" * 30)
    else:
        print("Uso: python ask_meu_bot.py 'sua pergunta'")
