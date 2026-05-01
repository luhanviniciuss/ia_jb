import pandas as pd
try:
    df = pd.read_excel('perguntas_IA.xlsx')
    print("Colunas encontradas:", df.columns.tolist())
    print("\nPrimeiras linhas:\n", df.head(3))
except Exception as e:
    print("Erro ao ler Excel:", e)
