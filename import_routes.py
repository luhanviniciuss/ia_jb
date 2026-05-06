import pandas as pd
import sqlite3
import unicodedata

def remove_accents(input_str):
    if not isinstance(input_str, str): return str(input_str)
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])

def import_routes():
    db_path = 'documentos.db'
    excel_path = 'd23v7.xlsx'
    
    print(f"Lendo {excel_path}...")
    df = pd.read_excel(excel_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Limpa dados anteriores desta fonte para evitar duplicatas
    cursor.execute("DELETE FROM documentos WHERE fonte = ?", (excel_path,))
    
    print(f"Importando {len(df)} rotas...")
    
    for index, row in df.iterrows():
        # Cria uma descrição amigável para a IA
        partes = []
        for col in df.columns:
            val = row[col]
            if pd.notna(val) and str(val).strip() != "":
                partes.append(f"{col}: {val}")
        
        conteudo = " | ".join(partes)
        conteudo_limpo = remove_accents(conteudo).lower()
        
        cursor.execute(
            "INSERT INTO documentos (conteudo, conteudo_limpo, fonte) VALUES (?, ?, ?)",
            (conteudo, conteudo_limpo, excel_path)
        )
    
    conn.commit()
    conn.close()
    print("Importação concluída com sucesso!")

if __name__ == "__main__":
    import_routes()
