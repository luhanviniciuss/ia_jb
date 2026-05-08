import requests
import json

def test_ask():
    url = "http://127.0.0.1:5000/ask"
    payload = {
        "question": "Quem é o motorista da rota FOR 101?",
        "user_id": 1,
        "conversa_id": 1
    }
    try:
        response = requests.post(url, json=payload, stream=True)
        print(f"Status Code: {response.status_code}")
        for line in response.iter_lines():
            if line:
                print(line.decode('utf-8'))
    except Exception as e:
        print(f"Erro ao conectar: {e}")

if __name__ == "__main__":
    test_ask()
