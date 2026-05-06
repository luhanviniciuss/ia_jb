import sqlite3
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = sqlite3.connect('documentos.db')
cursor = conn.cursor()
# Busca específica por SUBROTA terminando em 101 e FILIAL: FORTALEZA
cursor.execute('SELECT DISTINCT conteudo FROM documentos WHERE (conteudo LIKE "%SUBROTA:%101%") AND (conteudo LIKE "%FILIAL: FORTALEZA%")')
rows = cursor.fetchall()

result = {}
for r in rows:
    parts = r[0].split('|')
    subrota = ""
    motorista = ""
    for p in parts:
        if 'SUBROTA:' in p: subrota = p.split(':')[1].strip()
        if 'Motorista:' in p: motorista = p.split(':')[1].strip()
    
    if subrota and motorista:
        if subrota not in result: result[subrota] = set()
        result[subrota].add(motorista)

print(f"Resultados para FILIAL: FORTALEZA / Rota 101:")
for sub, mods in result.items():
    print(f"Sub-rota {sub}: {', '.join(sorted(mods))}")

conn.close()
