import sqlite3
import sys

# Forçar saída em utf-8 para evitar erros de encodamento no console
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()
cursor.execute("SELECT * FROM documentos LIMIT 10")
cols = [description[0] for description in cursor.description]
print(f"Columns: {cols}")
for row in cursor.fetchall():
    print(row)
conn.close()
