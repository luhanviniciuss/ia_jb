import sqlite3
import hashlib

def setup_auth_and_history():
    db_path = 'documentos.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Tabela de Usuários
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'consultor' -- 'admin' ou 'consultor'
    )
    ''')

    # 2. Tabela de Conversas
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS conversas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        titulo TEXT,
        data_criacao DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES usuarios(id)
    )
    ''')

    # 3. Tabela de Mensagens
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS mensagens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversa_id INTEGER NOT NULL,
        role TEXT NOT NULL, -- 'user' ou 'assistant'
        content TEXT NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (conversa_id) REFERENCES conversas(id) ON DELETE CASCADE
    )
    ''')

    # 4. Tabela de Treinamento da IA (Aprende com Admin)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS treinamento_ia (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pergunta TEXT UNIQUE NOT NULL,
        resposta_correta TEXT NOT NULL,
        admin_id INTEGER,
        data_treino DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (admin_id) REFERENCES usuarios(id)
    )
    ''')

    # Criar usuários iniciais se não existirem
    # Senha padrão: jb123 (Em um sistema real usaríamos salt e hashing robusto)
    def hash_password(password):
        return hashlib.sha256(password.encode()).hexdigest()

    admin_pass = hash_password('admin123')
    consultor_pass = hash_password('jb123')

    try:
        cursor.execute("INSERT INTO usuarios (username, password, role) VALUES (?, ?, ?)", 
                       ('admin', admin_pass, 'admin'))
        cursor.execute("INSERT INTO usuarios (username, password, role) VALUES (?, ?, ?)", 
                       ('consultor', consultor_pass, 'consultor'))
        print("Usuários iniciais criados!")
    except sqlite3.IntegrityError:
        print("Usuários já existem.")

    conn.commit()
    conn.close()
    print("Estrutura de Login, Histórico e Treinamento configurada!")

if __name__ == "__main__":
    setup_auth_and_history()
