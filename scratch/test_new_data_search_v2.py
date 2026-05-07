import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')

def remover_acentos(texto):
    import unicodedata
    if not texto: return ""
    return "".join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn').lower()

def get_context(query):
    db_path = 'documentos.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    clean_query = query.lower().replace('?', '').replace('!', '').replace('.', '').replace(',', '').replace('-', ' ')
    words = clean_query.split()
    
    all_results = []
    stopwords = ["qual", "o", "a", "de", "do", "da", "em", "um", "uma", "para", "com", "no", "na", "os", "as", "dos", "das", "quanto", "tempo", "rota", "mais", "quem", "é", "motorista"]
    search_terms = [w for w in words if w not in stopwords and len(w) > 1]
    
    # Simula a lógica de detecção de código de subrota (ex: PTC 001)
    if len(words) >= 2:
        for i in range(len(words)-1):
            if words[i].isalpha() and len(words[i]) >= 2 and words[i+1].isdigit():
                search_terms.insert(0, words[i] + words[i+1])
    
    if not search_terms: search_terms = [clean_query]

    print(f"Termos de busca: {search_terms}")

    for w in search_terms:
        score_query = f"(CASE WHEN conteudo LIKE ? THEN 50 ELSE 0 END) + (CASE WHEN conteudo_limpo LIKE ? THEN 30 ELSE 0 END)"
        params = [f"%{w}%", f"%{w}%"]
        
        sql = f"SELECT conteudo FROM documentos WHERE ({score_query}) > 0 ORDER BY ({score_query}) DESC LIMIT 5"
        cursor.execute(sql, params + params)
        results = cursor.fetchall()
        for r in results:
            all_results.append(r[0])
    
    conn.close()
    unique_results = list(dict.fromkeys(all_results))
    return "\n\n".join(unique_results[:10])

test_query = "quais os dias de largada a subrota ptc?"
context = get_context(test_query)
print("\n--- CONTEXTO ENCONTRADO ---")
print(context)
