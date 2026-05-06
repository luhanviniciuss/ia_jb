import sqlite3

db_path = 'documentos.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

query_word = "icapui"
sql = "SELECT conteudo FROM documentos WHERE conteudo_limpo LIKE ?"
cursor.execute(sql, (f'%{query_word}%',))
results = cursor.fetchall()

print(f"Resultados encontrados para '{query_word}': {len(results)}")
for r in results[:2]:
    print(f"- {r[0][:100]}...")

conn.close()
