import requests
import json

url = "http://localhost:5000/ask"
data = {"question": "Qual a rota para ARACATI?"}

try:
    response = requests.post(url, json=data)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
except Exception as e:
    print(f"Erro ao testar: {e}")
