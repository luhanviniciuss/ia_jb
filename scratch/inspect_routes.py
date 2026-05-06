import pandas as pd
try:
    df = pd.read_excel('d23v7.xlsx')
    print("Colunas:", df.columns.tolist())
    print("\nSample:\n", df.head(5))
except Exception as e:
    print("Erro:", e)
