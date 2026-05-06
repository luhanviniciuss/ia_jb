# JB Intelligence - Assistente Logístico Grupo JB

Assistente inteligente de alta performance para gestão de rotas, motoristas e processos operacionais do Grupo JB, utilizando a API do Google Gemini 1.5 Flash e RAG (Retrieval-Augmented Generation) com SQLite.

## 🚀 Tecnologias
- **Backend:** Flask (Python)
- **Frontend:** HTML5/CSS3/JS (Vanilla) com Design Premium
- **IA:** Google Gemini 1.5 Flash
- **Banco de Dados:** SQLite (documentos.db)
- **Deploy:** Linux Ubuntu + PM2

---

## 🛠️ Instalação (Local)

1. Clone o repositório:
   ```bash
   git clone https://github.com/GrupoJB/jb_assist.git
   cd jb_assist
   ```

2. Instale as dependências:
   ```bash
   pip install flask flask-cors google-generativeai
   ```

3. Execute o servidor:
   ```bash
   python app.py
   ```

4. Acesse: `http://localhost:5000`

---

## 🌐 Deploy no Linux Ubuntu (Produção)

Siga os passos abaixo para colocar o sistema online usando **PM2** e **Nginx**.

### 1. Preparar o Ambiente
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv -y
```

### 2. Configurar o Projeto
```bash
# Clone o projeto no servidor
git clone https://github.com/GrupoJB/jb_assist.git /var/www/jb_assist
cd /var/www/jb_assist

# Instale as dependências
pip install flask flask-cors google-generativeai gunicorn
```

### 3. Configurar o PM2 (Gerenciador de Processos)
O PM2 manterá o seu servidor Python rodando 24/7 e o reiniciará automaticamente em caso de falhas.

```bash
# Instalar PM2 via NodeJS
sudo apt install nodejs npm -y
sudo npm install -g pm2

# Iniciar o servidor com PM2
pm2 start "python3 app.py" --name jb-assistant

# Salvar para iniciar com o boot do sistema
pm2 save
pm2 startup
```

### 4. Configurar o Firewall
```bash
sudo ufw allow 5000
```

---

## 📂 Estrutura de Dados
O sistema utiliza o arquivo `documentos.db` para o contexto RAG. 
- **Rotas e Motoristas:** Extraídos da base D23.
- **Processos:** Baseados nos manuais MNOP02 e MNOP03.

## 📄 Licença
Propriedade do Grupo JB Transporte e Logística.
