import pypdf
import os
import re

def super_clean_text(text):
    if not text: return ""
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        if len(re.findall(r'[A-Za-z]\s[A-Za-z]', line)) > 3:
            line = re.sub(r'([A-Za-zÃÕÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÇãõáéíóúàèìòùâêîôûç])\s(?=[A-Za-zÃÕÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÇãõáéíóúàèìòùâêîôûç])', r'\1', line)
        
        line = re.sub(r'\s+', ' ', line).strip()
        cleaned_lines.append(line)
        
    return "\n".join(cleaned_lines)

def extract_text():
    pdf_path = "MNOP03-00 -  SUPER ROTINA GESTOR DE OPERAÇÃO (1).pdf"
    output_path = "mnop03.txt"
    
    if not os.path.exists(pdf_path):
        print(f"Erro: Arquivo {pdf_path} não encontrado.")
        return

    print(f"Extraindo MNOP03: {pdf_path}")
    reader = pypdf.PdfReader(pdf_path)
    with open(output_path, "w", encoding="utf-8") as f:
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            fixed_text = super_clean_text(text)
            f.write(f"\n## PAGINA {i+1} MNOP03 ##\n")
            f.write(fixed_text)
            f.write("\n")
    print(f"Pronto! MNOP03 extraído em {output_path} ({len(reader.pages)} páginas)")

if __name__ == "__main__":
    extract_text()
