import sqlite3

def get_context_test(query):
    db_path = r'c:\Users\luhan.vinicius\Desktop\modelo_ia\documentos.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    clean_query = query.lower().replace('?', '').replace('!', '').replace('.', '').replace(',', '').replace('-', ' ')
    words = clean_query.split()
    
    all_results = []
    stopwords = ["qual", "o", "a", "de", "do", "da", "em", "um", "uma", "para", "com", "no", "na", "os", "as", "dos", "das", "quanto", "tempo", "rota", "mais", "quem", "é", "motorista"]
    search_terms = [w for w in words if w not in stopwords and len(w) > 1]
    
    if len(words) >= 2:
        for i in range(len(words)-1):
            if words[i].isalpha() and words[i+1].isdigit():
                search_terms.append(words[i] + words[i+1])
    
    if not search_terms: search_terms = [clean_query]

    print(f"DEBUG - Search terms: {search_terms}")

    for w in search_terms:
        score_query = "(CASE WHEN conteudo LIKE ? THEN 30 ELSE 0 END) + (CASE WHEN conteudo_limpo LIKE ? THEN 20 ELSE 0 END)"
        params = [f"%{w}%", f"%{w}%"]
        
        try:
            sql = f"SELECT conteudo FROM documentos WHERE ({score_query}) > 0 ORDER BY ({score_query}) DESC LIMIT 30"
            cursor.execute(sql, params)
            results = cursor.fetchall()
            print(f"DEBUG - Found {len(results)} for word '{w}'")
            for r in results:
                all_results.append(r[0])
        except Exception as e:
            print(f"Erro na busca por '{w}': {e}")
    
    conn.close()
    return all_results

results = get_context_test("quem é o motorista da rota for 101")
print(f"TOTAL RESULTS: {len(results)}")
if results:
    print("FIRST RESULT SAMPLE:")
    print(results[0][:200])
