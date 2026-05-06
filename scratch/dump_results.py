import sqlite3

conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()

cursor.execute("SELECT conteudo FROM documentos WHERE conteudo LIKE ?", ("%jb alerta%",))
rows = cursor.fetchall()
with open("scratch/search_results.txt", "w", encoding="utf-8") as f:
    for r in rows:
        f.write("-" * 20 + "\n")
        f.write(r[0] + "\n")

conn.close()
print(f"Saved {len(rows)} results to scratch/search_results.txt")
