import sqlite3
conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()
cursor.execute('SELECT conteudo FROM documentos WHERE conteudo LIKE "%JB Alerta%"')
rows = cursor.fetchall()
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
for r in rows:
    print("-" * 50)
    print(r[0])
conn.close()
