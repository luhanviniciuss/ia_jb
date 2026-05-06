import sqlite3

conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()

cursor.execute("SELECT conteudo FROM documentos WHERE conteudo LIKE '%alerta%'")
rows = cursor.fetchall()
with open("scratch/raw_dump.txt", "w", encoding="utf-8") as f:
    for r in rows:
        f.write(f"RAW CONTENT: {repr(r[0])}\n")

conn.close()
