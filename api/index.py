from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route('/api/debug')
def debug():
    return jsonify({"status": "O ambiente Python do Vercel esta funcionando!", "msg": "Se voce ver isso, o erro 500 anterior era nas bibliotecas."})

@app.route('/api/login', methods=['POST', 'GET'])
def login():
    return jsonify({"msg": "Rota de login alcancada!"})

# O Vercel precisa desse objeto 'app'
app = app
