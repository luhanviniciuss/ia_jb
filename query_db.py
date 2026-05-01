import sqlite3
import sys

def search(query):
    db_path = 'documentos.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Tenta busca FTS5 primeiro
    try:
        cursor.execute("SELECT conteudo FROM documentos WHERE documentos MATCH ? LIMIT 3", (query,))
    except sqlite3.OperationalError:
        # Fallback para busca LIKE se FTS5 falhou/não foi usado
        cursor.execute("SELECT conteudo FROM documentos WHERE conteudo LIKE ? LIMIT 3", (f'%{query}%',))

    results = cursor.fetchall()
    conn.close()
    return results

if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        results = search(query)
        if results:
            print(f"Resultados para: {query}\n")
            for i, res in enumerate(results, 1):
                print(f"--- Trecho {i} ---")
                print(res[0])
                print("-" * 20)
        else:
            print("Nenhum resultado encontrado.")
    else:
        print("Uso: python query_db.py 'sua pergunta ou palavra-chave'")
