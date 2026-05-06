import sqlite3
import unicodedata

def remover_acentos(texto):
    if not texto: return ""
    return "".join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn').lower()

def setup_master_knowledge():
    db_path = 'documentos.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Criar tabela de conhecimento mestre (Prioridade Máxima)
    cursor.execute('CREATE TABLE IF NOT EXISTS conhecimento_mestre (assunto TEXT, conteudo TEXT, conteudo_limpo TEXT)')
    
    # Limpar para evitar duplicados
    cursor.execute('DELETE FROM conhecimento_mestre')

    # ENSINANDO OS ASSUNTOS CRÍTICOS
    conhecimentos = [
        ("Classificacao das Atividades", "As atividades do Grupo JB são classificadas em 3 tipos quanto à forma de atuação: AÇÃO (exige condução ativa do gestor), CHECK (verificação de indicadores ou processos) e REUNIÃO (alinhamento com a equipe)."),
        ("JB Alerta", "O horário fixo da reunião JB Alerta (RD43) é às 13h00 (13:00). É uma reunião diária obrigatória."),
        ("Objetivo Super Rotina", "O objetivo principal da Super Rotina do Gestor é estabelecer a ordem, periodicidade e responsabilidade das atividades críticas para garantir a excelência operacional do Grupo JB.")
    ]

    for assunto, conteudo in conhecimentos:
        cursor.execute('INSERT INTO conhecimento_mestre VALUES (?, ?, ?)', 
                       (assunto, conteudo, remover_acentos(conteudo)))

    conn.commit()
    conn.close()
    print("[OK] Conhecimento Mestre gravado com sucesso!")

if __name__ == "__main__":
    setup_master_knowledge()
