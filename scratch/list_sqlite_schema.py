import sqlite3

def list_tables():
    conn = sqlite3.connect('documentos.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    for table in tables:
        print(f"\nTable: {table}")
        cursor.execute(f"PRAGMA table_info({table});")
        for col in cursor.fetchall():
            print(f"  {col}")
    conn.close()

if __name__ == "__main__":
    list_tables()
