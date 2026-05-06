import google.generativeai as genai

GEMINI_API_KEY = "AIzaSyBQuxkHrEJkn_CdrVDlv46QQ39HncKvgKw"
genai.configure(api_key=GEMINI_API_KEY)

try:
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(m.name)
except Exception as e:
    print(f"Erro ao listar modelos: {e}")
