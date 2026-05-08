import sqlite3
import psycopg2
import os

# Configurações do Supabase (Postgres)
# Usando a URL que você forneceu
DB_URL = "postgres://postgres.vnxqokzkcjwuvnfdlupy:jn5zS2FWWqItLOcW@aws-1-sa-east-1.pooler.supabase.com:5432/postgres?sslmode=require"

def migrate():
    # 1. Conectar ao SQLite
    sqlite_conn = sqlite3.connect('documentos.db')
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()

    # 2. Conectar ao Postgres
    pg_conn = psycopg2.connect(DB_URL)
    pg_cursor = pg_conn.cursor()

    # 3. Listar tabelas do SQLite
    sqlite_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = [row['name'] for row in sqlite_cursor.fetchall()]

    print(f"Iniciando migração de {len(tables)} tabelas...")

    for table in tables:
        print(f"Migrando tabela: {table}...")
        
        # Pegar schema da tabela
        sqlite_cursor.execute(f"PRAGMA table_info({table});")
        columns = sqlite_cursor.fetchall()
        
        # Mapear tipos (simplificado)
        col_defs = []
        col_names = []
        for col in columns:
            name = col['name']
            ctype = col['type']
            pk = col['pk']
            notnull = col['notnull']
            dflt = col['dflt_value']
            
            pg_type = "TEXT"
            if "INT" in ctype:
                pg_type = "SERIAL" if pk and table != 'documentos' else "INTEGER"
            elif "DATETIME" in ctype or "TIMESTAMP" in ctype:
                pg_type = "TIMESTAMP"
            
            # Ajuste de PK para manter IDs originais
            pk_str = " PRIMARY KEY" if pk else ""
            nn_str = " NOT NULL" if notnull else ""
            
            # Tratamento especial para default timestamps
            if dflt and "CURRENT_TIMESTAMP" in dflt.upper():
                dflt_str = " DEFAULT CURRENT_TIMESTAMP"
            else:
                dflt_str = f" DEFAULT {dflt}" if dflt else ""
            
            col_defs.append(f"\"{name}\" {pg_type}{pk_str}{nn_str}{dflt_str}")
            col_names.append(f"\"{name}\"")

        # Criar tabela no Postgres
        pg_cursor.execute(f"DROP TABLE IF EXISTS \"{table}\" CASCADE;")
        pg_cursor.execute(f"CREATE TABLE \"{table}\" ({', '.join(col_defs)});")

        # Transferir dados
        sqlite_cursor.execute(f"SELECT * FROM \"{table}\";")
        rows = sqlite_cursor.fetchall()
        
        if rows:
            # Preparar insert
            placeholders = ["%s"] * len(col_names)
            insert_query = f"INSERT INTO \"{table}\" ({', '.join(col_names)}) VALUES ({', '.join(placeholders)})"
            
            # Converter linhas do SQLite (Row objects) para tuplas
            data = [tuple(row) for row in rows]
            
            # Insert em massa
            pg_cursor.executemany(insert_query, data)
            print(f"  - {len(rows)} linhas migradas.")
        else:
            print("  - Tabela vazia.")

        # Se for uma tabela com SERIAL, atualizar a sequência para não dar erro em novos inserts
        if any("SERIAL" in d for d in col_defs):
            pg_cursor.execute(f"SELECT setval(pg_get_serial_sequence('\"{table}\"', 'id'), MAX(id)) FROM \"{table}\";")

    # 4. Finalizar
    pg_conn.commit()
    sqlite_conn.close()
    pg_conn.close()
    print("\nMigração concluída com sucesso!")

if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:
        print(f"Erro na migração: {e}")
