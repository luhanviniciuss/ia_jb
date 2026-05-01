from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import sqlite3
import unicodedata
import httpx
import json
import os
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "documentos.db"))

def normalize_text(text: str) -> str:
    if not text: return ""
    text = "".join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9 ]', ' ', text.lower()).strip()

def get_context(query: str):
    clean_query = normalize_text(query)
    # Lista de ignore reduzida para não perder palavras como "mes"
    ignore = {'qual', 'quais', 'como', 'onde', 'quando', 'sao', 'pode', 'deve', 'esta', 'dos', 'das', 'uma', 'pelo', 'pela', 'seja', 'que'}
    words = [w for w in clean_query.split() if len(w) >= 3 and w not in ignore]
    
    if not words: return ""

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # BUSCA FLEXÍVEL: Damos muito peso se as palavras baterem
    # Usamos LIKE com curingas para captar variações
    score_parts = []
    params = []
    for w in words:
        score_parts.append("(CASE WHEN conteudo_limpo LIKE ? THEN 5 ELSE 0 END)")
        params.append(f"%{w[:5]}%")

    score_query = " + ".join(score_parts)

    sql = f"""
        SELECT conteudo, ({score_query}) as relevance 
        FROM documentos 
        WHERE relevance >= 5 
        ORDER BY relevance DESC 
        LIMIT 3
    """
    
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    
    context = "\n\n".join([r['conteudo'] for r in rows])
    conn.close()
    return context

@app.post("/ask")
async def ask(request: Request):
    data = await request.json()
    question = data.get("question")
    context = get_context(question)
    
    async def event_generator():
        # Prompt atualizado para permitir raciocínio com base no manual
        prompt = f"""
### CONTEXTO OFICIAL GRUPO JB:
{context}

### PERGUNTA DO USUÁRIO:
{question}

### REGRAS DE RESPOSTA:
1. Responda de forma direta e amigável.
2. Se a informação base estiver no contexto (ex: "4x ao dia"), você PODE fazer cálculos (ex: "120x ao mês") para ajudar o usuário.
3. Se o contexto não tiver NADA sobre o assunto, diga apenas "Não localizado".
"""
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", "http://localhost:11434/api/generate", 
                                   json={"model": "meu-bot", "prompt": prompt, "options": {"temperature": 0}}) as response:
                async for line in response.aiter_lines():
                    if line:
                        parsed = json.loads(line)
                        yield f"data: {json.dumps({'text': parsed.get('response', '')})}\n\n"
                        if parsed.get("done"):
                            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    print("Servidor v22 (Raciocínio Lógico) rodando...")
    uvicorn.run(app, host="0.0.0.0", port=8899)
