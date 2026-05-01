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
    
    txt_path = 'pdfemtexto.txt'
    if os.path.exists(txt_path):
        with open(txt_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            
        chunks = []
        current_chunk = ""
        
        for line in lines:
            line = line.strip()
            if not line: continue
            
            # Se encontrar uma nova atividade (RD), começa um novo bloco
            if re.match(r'\* \*RD\d+', line) or re.match(r'## \*Página', line):
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line
            else:
                # Se for conteúdo (Frequência, Descrição, etc), adiciona ao bloco atual
                current_chunk += "\n" + line
        
        # Adiciona o último bloco
        if current_chunk:
            chunks.append(current_chunk)

        for chunk in chunks:
            if len(chunk) > 20:
                cursor.execute('INSERT INTO documentos VALUES (?, ?, ?)', 
                               (chunk, remover_acentos(chunk), 'pdfemtexto.txt'))
        print(f"PDF processado: {len(chunks)} atividades agrupadas.")

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
    print("Banco de Dados RECONSTRUIDO COM AGRUPAMENTO!")

if __name__ == "__main__":
    setup_db()
