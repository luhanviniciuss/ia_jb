import sqlite3
conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()
cursor.execute("SELECT * FROM documentos LIMIT 5")
cols = [description[0] for description in cursor.description]
print(f"Columns: {cols}")
for row in cursor.fetchall():
    print(row)
conn.close()
