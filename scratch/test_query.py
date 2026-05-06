import requests
import json

url = "http://localhost:5000/ask"
import sys
question = sys.argv[1] if len(sys.argv) > 1 else "qual horario do jb alerta?"
data = {"question": question}

try:
    response = requests.post(url, json=data)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
except Exception as e:
    print(f"Erro ao testar: {e}")
