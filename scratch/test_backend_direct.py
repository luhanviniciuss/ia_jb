import requests
import json

url = "http://localhost:5000/ask"
payload = {"question": "Quem é o motorista da rota FOR 101?"}
headers = {"Content-Type": "application/json"}

try:
    print(f"Testando conexão com {url}...")
    response = requests.post(url, json=payload, stream=True, timeout=10)
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        print("Recebendo stream:")
        for line in response.iter_lines():
            if line:
                print(line.decode('utf-8'))
    else:
        print(f"Erro: {response.text}")
except Exception as e:
    print(f"Falha na conexão: {e}")
