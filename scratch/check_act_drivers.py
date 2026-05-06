import sqlite3
conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()
cursor.execute('SELECT DISTINCT conteudo FROM documentos WHERE (conteudo LIKE "%SUBROTA: ACT%") AND (conteudo LIKE "%FILIAL: FORTALEZA%")')
rows = cursor.fetchall()
print("Motoristas ACT Fortaleza:")
motoristas = set()
for r in rows:
    parts = r[0].split('|')
    for p in parts:
        if 'Motorista:' in p:
            motoristas.add(p.split(':')[1].strip())
for m in sorted(motoristas):
    print(f"- {m}")
conn.close()
