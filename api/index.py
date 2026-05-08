import sys
import os

# Adiciona a raiz do projeto ao path para conseguir importar o app.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

# O Vercel espera que o objeto se chame 'app'
if __name__ == "__main__":
    app.run()
