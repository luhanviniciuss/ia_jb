import sqlite3

conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()

cursor.execute("SELECT conteudo FROM documentos WHERE conteudo LIKE '%alerta%'")
rows = cursor.fetchall()
for r in rows:
    print(f"RAW CONTENT: {repr(r[0])}")

conn.close()
