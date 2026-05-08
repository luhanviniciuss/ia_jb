import requests

def test_connection():
    url = "http://127.0.0.1:5000/conversations?user_id=1"
    try:
        response = requests.get(url)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
    except Exception as e:
        print(f"Erro ao conectar: {e}")

if __name__ == "__main__":
    test_connection()
