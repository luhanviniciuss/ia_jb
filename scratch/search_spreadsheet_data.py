import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()
cursor.execute("SELECT * FROM documentos WHERE conteudo LIKE '%ACT001%' OR conteudo LIKE '%Largada%' LIMIT 10")
for row in cursor.fetchall():
    print(row)
conn.close()
