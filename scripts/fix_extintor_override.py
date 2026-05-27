import re
import unicodedata
import psycopg

DB='postgresql://postgres:2026@localhost:5432/postgres'

def norm(text: str) -> str:
    text=''.join(c for c in unicodedata.normalize('NFD', text or '') if unicodedata.category(c)!='Mn')
    text=re.sub(r'[^a-z0-9 ]',' ',text.lower())
    return re.sub(r'\s+',' ',text).strip()

questions=[
    'Quatro itens sao obrigatorios para que um veiculo saia em rota sem risco de autuacao: triangulo de sinalizacao, macaco e estepe. Qual item esta faltando?',
    'Quatro itens sao obrigatorios para um veiculo sair em rota: triangulo, macaco e estepe. Qual item falta?',
    'Qual item esta faltando entre triangulo, macaco e estepe para o veiculo sair em rota?',
]
answer='Extintor de incêndio.'

with psycopg.connect(DB) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM app_users WHERE username='admin' LIMIT 1")
        row=cur.fetchone()
        admin_id=row[0] if row else None
        for q in questions:
            qn=norm(q)
            cur.execute(
                '''
                INSERT INTO qa_overrides (question_norm, question_raw, answer, admin_user_id)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (question_norm) DO UPDATE
                SET question_raw=EXCLUDED.question_raw,
                    answer=EXCLUDED.answer,
                    admin_user_id=EXCLUDED.admin_user_id,
                    updated_at=NOW()
                ''',
                (qn,q,answer,admin_id)
            )
            cur.execute('DELETE FROM qa_cache WHERE question_norm=%s',(qn,))
    conn.commit()

print('upserted',len(questions))
