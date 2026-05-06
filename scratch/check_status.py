import sqlite3

def check_db():
    db_path = 'documentos.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Checking tables...")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f"Tables: {tables}")
    
    for table in tables:
        name = table[0]
        cursor.execute(f"SELECT COUNT(*) FROM {name}")
        count = cursor.fetchone()[0]
        print(f"Table {name}: {count} rows")
        
        # Try to see sources if they exist
        try:
            cursor.execute(f"SELECT DISTINCT fonte FROM {name} LIMIT 10")
            fontes = cursor.fetchall()
            if fontes:
                print(f"  Sources in {name}: {fontes}")
        except:
            pass

    conn.close()

if __name__ == "__main__":
    check_db()
