import sqlite3
db = sqlite3.connect('documentos.db')
cursor = db.cursor()
query = 'jb alerta'
# Busca FTS5
cursor.execute("SELECT conteudo, rank FROM documentos WHERE documentos MATCH ? ORDER BY rank LIMIT 5", (query,))
rows = cursor.fetchall()
print("Resultados FTS5:")
for r in rows:
    print(f"Rank: {r[1]}\nConteudo: {r[0][:200]}...\n")

# Busca LIKE
cursor.execute("SELECT conteudo FROM documentos WHERE conteudo LIKE ?", ('%jb alerta%',))
rows = cursor.fetchall()
print("Resultados LIKE:")
for r in rows:
    print(f"Conteudo: {r[0][:200]}...\n")
db.close()
