import sqlite3
import unicodedata
import os
import re
import pandas as pd

def remover_acentos(texto):
    if not texto: return ""
    return "".join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn').lower()

def setup_db():
    db_path = 'documentos.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('DROP TABLE IF EXISTS documentos')
    cursor.execute('CREATE TABLE documentos (conteudo TEXT, conteudo_limpo TEXT, fonte TEXT)')
    
    txt_files = ['mnop02.txt', 'mnop03.txt']
    for txt_path in txt_files:
        if os.path.exists(txt_path):
            with open(txt_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                
            chunks = []
            current_chunk = ""
            for line in lines:
                line = line.strip()
                if not line: continue
                if re.match(r'\* \*RD\d+', line) or re.match(r'##', line):
                    if current_chunk: chunks.append(current_chunk)
                    current_chunk = line
                else:
                    current_chunk += "\n" + line
            
            if current_chunk: chunks.append(current_chunk)

            for chunk in chunks:
                if len(chunk) > 20:
                    cursor.execute('INSERT INTO documentos VALUES (?, ?, ?)', 
                                   (chunk, remover_acentos(chunk), txt_path))
            print(f"TXT {txt_path} processado: {len(chunks)} blocos.")

    xlsx_path = 'perguntas_IA.xlsx'
    if os.path.exists(xlsx_path):
        df = pd.read_excel(xlsx_path)
        count = 0
        for _, row in df.iterrows():
            txt = f"Atividade: {row.get('Atividade', '')}\nPergunta: {row.get('Pergunta', '')}\nResposta: {row.get('Resposta', '')}"
            cursor.execute('INSERT INTO documentos VALUES (?, ?, ?)', (txt, remover_acentos(txt), 'perguntas_IA.xlsx'))
            count += 1
        print(f"Excel processado: {count} linhas inseridas.")

    conn.commit()
    conn.close()
    print("Banco de Dados ATUALIZADO com MNOP02 e MNOP03!")

if __name__ == "__main__":
    setup_db()
