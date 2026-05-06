import sqlite3
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()
# Busca por variações de FOR 101
cursor.execute('SELECT conteudo FROM documentos WHERE conteudo LIKE "%FOR%101%"')
rows = cursor.fetchall()
print(f"Encontrados: {len(rows)}")
for r in rows[:5]:
    print("-" * 50)
    print(r[0])
conn.close()
