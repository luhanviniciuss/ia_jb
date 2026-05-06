import requests

def test_ask():
    url = "http://localhost:5000/ask"
    question = "O que é a Super Rotina do Gestor?"
    
    try:
        response = requests.post(url, json={"question": question})
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_ask()
