import pypdf
import os
import re

def super_clean_text(text):
    # Esta função é mais agressiva: ela procura letras sozinhas e as une
    # Ex: "F l u x o s" -> "Fluxos"
    
    # 1. Tenta unir sequências de letras (Maiúsculas ou Minúsculas) separadas por espaços
    # Detecta se há uma alta densidade de espaços entre letras
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        # Se a linha tem muitos espaços entre letras (ex: P e d i d o)
        if len(re.findall(r'[A-Za-z]\s[A-Za-z]', line)) > 3:
            # Remove espaços que estão entre letras, mas mantém os que separam palavras
            # Truque: Se houver 2 ou mais espaços, é separação de palavra. Se for 1, é erro de PDF.
            line = re.sub(r'([A-Za-zÃÕÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÇãõáéíóúàèìòùâêîôûç])\s(?=[A-Za-zÃÕÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÇãõáéíóúàèìòùâêîôûç])', r'\1', line)
        
        # Remove espaços triplos/duplos
        line = re.sub(r'\s+', ' ', line).strip()
        cleaned_lines.append(line)
        
    return "\n".join(cleaned_lines)

def extract_text():
    pdf_path = "MNOP02 - 00 - MANUAL DE GESTÃO DE PEDIDOS CRÍTICOS.pdf"
    output_path = "mnop02.txt"
    
    if not os.path.exists(pdf_path):
        files = os.listdir('.')
        for f in files:
            if "MNOP02" in f and f.endswith(".pdf"):
                pdf_path = f
                break

    print(f"Extração de Elite: {pdf_path}")
    reader = pypdf.PdfReader(pdf_path)
    with open(output_path, "w", encoding="utf-8") as f:
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            # Limpeza ultra-profunda
            fixed_text = super_clean_text(text)
            f.write(f"\n## PAGINA {i+1} MNOP02 ##\n")
            f.write(fixed_text)
            f.write("\n")
    print(f"Pronto! MNOP02 reconstruído com perfeição em {output_path}")

if __name__ == "__main__":
    extract_text()
