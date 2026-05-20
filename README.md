# JB Intelligence - Arquitetura PostgreSQL (Robusta)

Backend reestruturado para separar:
- Resposta estruturada (rotas/parceiro/motorista/dias/região) via SQL direto
- Resposta documental (manuais/PDFs) via busca + LLM
- Override administrativo (`/api/learn`) com prioridade máxima

## Stack
- API: FastAPI
- Banco: PostgreSQL 18
- Busca textual: PostgreSQL FTS (`tsvector`, `websearch_to_tsquery`) + `pg_trgm`
- IA: Gemini com fallback automático de modelo

## Banco local (configuração atual)
O backend usa por padrão:
`postgresql://postgres:2026@localhost:5432/postgres`

Você pode sobrescrever no `.env` via `DATABASE_URL`.

## Comandos principais

### 1) Subir tudo
```bash
npm run dev:full
```

### 2) Subir só backend
```bash
npm run dev:api
```

### 3) Treinar base com tabela + textos
```bash
npm run train:pg
```
Isso executa:
- `D23V7.xlsx` -> `route_facts`
- `mnop02.txt`, `mnop03.txt` -> `doc_chunks`

### 4) Ingerir PDFs diretamente
```bash
python scripts/train_pg.py --pdf "MNOP02 - 00 - MANUAL DE GESTÃO DE PEDIDOS CRÍTICOS.pdf" "MNOP03-00 -  SUPER ROTINA GESTOR DE OPERAÇÃO (1).pdf"
```

### 5) Treino flexível (novos arquivos)
```bash
python scripts/train_pg.py --xlsx NOVA_TABELA.xlsx --text novo_manual.txt --pdf novo_manual.pdf
```

## Como o motor decide respostas
1. Procura override treinado por admin (`qa_overrides`)
2. Detecta intenção estruturada (parceiro/motorista/dias/região + rota) e responde direto via SQL
3. Se não for estruturado, busca os melhores chunks no PostgreSQL (FTS+trigram)
4. Envia apenas contexto recuperado para o Gemini
5. Se não houver evidência suficiente, responde: `Informação não consta nos manuais ou tabelas disponíveis.`

## Tabelas principais
- `app_users`
- `conversations`
- `messages`
- `qa_overrides`
- `route_facts`
- `doc_sources`
- `doc_chunks`

## Credenciais iniciais
- `admin / admin123`
- `consultor / jb123`

## Endpoints
- `GET /api/health`
- `POST /api/login`
- `GET|POST /api/conversations`
- `GET /api/messages/{conversation_id}`
- `POST /api/learn`
- `POST /api/ask`

## Observação de qualidade
Para manter precisão com crescimento da base:
- Sempre ingerir planilhas operacionais como tabela estruturada
- Evitar depender de PDF para dados que já existem em coluna
- Atualizar por fonte (upsert), sem duplicar chunk antigo
- Validar periodicamente com um conjunto fixo de perguntas críticas
