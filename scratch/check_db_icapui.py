import sqlite3
conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()
cursor.execute("SELECT conteudo FROM documentos WHERE conteudo_limpo LIKE ?", ('%icapu%',))
rows = cursor.fetchall()
for r in rows:
    print(r[0])
conn.close()
