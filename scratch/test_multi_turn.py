import requests

url = "http://localhost:5000/ask"

# Passo 1
q1 = "qual parceiro de act?"
r1 = requests.post(url, json={"question": q1, "history": []})
a1 = r1.json().get('answer')
print(f"User: {q1}\nBot: {a1}\n")

# Passo 2
history = [
    {"role": "user", "content": q1},
    {"role": "assistant", "content": a1}
]
q2 = "quais os motoristas?"
r2 = requests.post(url, json={"question": q2, "history": history})
a2 = r2.json().get('answer')
print(f"User: {q2}\nBot: {a2}")
