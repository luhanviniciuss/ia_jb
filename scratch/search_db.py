import sqlite3

conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()

queries = ["jb alerta", "horario", "alerta"]
for q in queries:
    print(f"\nSearching for: {q}")
    cursor.execute(f"SELECT conteudo FROM documentos WHERE conteudo LIKE ?", (f'%{q}%',))
    rows = cursor.fetchall()
    print(f"Found {len(rows)} results.")
    for r in rows:
        print("-" * 20)
        print(r[0][:200])

conn.close()
