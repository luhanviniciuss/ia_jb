import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()
cursor.execute("SELECT conteudo, fonte FROM documentos WHERE conteudo LIKE '%SPP%'")
for row in cursor.fetchall():
    print(row)
conn.close()
