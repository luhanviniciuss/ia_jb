import sqlite3
import unicodedata

def remover_acentos(txt):
    if not txt: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', txt) if unicodedata.category(c) != 'Mn').lower()

conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()

# Atualiza todos os registros para garantir que o conteudo_limpo esteja realmente limpo e buscável
cursor.execute("SELECT ROWID, conteudo FROM documentos")
rows = cursor.fetchall()

for row_id, conteudo in rows:
    limpo = remover_acentos(conteudo)
    cursor.execute("UPDATE documentos SET conteudo_limpo = ? WHERE ROWID = ?", (limpo, row_id))

conn.commit()
print(f"Atualizados {len(rows)} registros com sucesso.")
conn.close()
