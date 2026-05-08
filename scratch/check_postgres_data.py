import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

load_dotenv()

def check_postgres_data():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # Procurar por FOR 101 ou FOR101
    search_term = "%FOR%101%"
    print(f"Buscando por: {search_term}")
    
    cursor.execute("SELECT conteudo FROM documentos WHERE conteudo ILIKE %s LIMIT 5", (search_term,))
    rows = cursor.fetchall()
    
    if not rows:
        print("Nenhuma informação encontrada no Postgres para 'FOR 101'.")
    else:
        print(f"Encontrado {len(rows)} resultados:")
        for i, row in enumerate(rows):
            print(f"\n--- Resultado {i+1} ---\n{row['conteudo'][:500]}...")
            
    conn.close()

if __name__ == "__main__":
    check_postgres_data()
