import sqlite3
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()
# Busca por todos os motoristas que aparecem em linhas com "FORTALEZA" e "101"
# Tentando ser específico na busca por SUBROTA: xxx101
cursor.execute('SELECT conteudo FROM documentos WHERE (conteudo LIKE "%FORTALEZA%" OR conteudo LIKE "%FOR%") AND conteudo LIKE "%101%"')
rows = cursor.fetchall()

motoristas = set()
for r in rows:
    # Tenta extrair o nome do motorista do texto
    parts = r[0].split('|')
    for p in parts:
        if 'Motorista:' in p:
            name = p.split(':')[1].strip()
            motoristas.add(name)

print(f"Motoristas encontrados para Rota 101 / Fortaleza:")
for m in sorted(motoristas):
    print(f"- {m}")

conn.close()
