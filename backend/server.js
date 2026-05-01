const express = require('express');
const cors = require('cors');
const sqlite3 = require('sqlite3').verbose();
const axios = require('axios');
const path = require('path');

const app = express();
app.use(cors());
app.use(express.json());

const dbPath = path.join(__dirname, '..', 'documentos.db');
const db = new sqlite3.Database(dbPath);

function normalizeText(text) {
    if (!text) return "";
    return text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
               .replace(/[^a-z0-9 ]/g, " ").trim();
}

const getContext = (query) => {
    return new Promise((resolve) => {
        const cleanQuery = normalizeText(query);
        // Filtro de "ruído" mais agressivo
        const ignore = ['sao', 'como', 'quanto', 'forma', 'das', 'dos', 'uma', 'pode', 'ser', 'qual', 'quais', 'pelo', 'pela'];
        const words = cleanQuery.split(/\s+/).filter(w => w.length >= 3 && !ignore.includes(w));
        
        if (words.length === 0) return resolve("");

        console.log(`[BUSCA V17] Buscando por: "${cleanQuery}"`);

        // 1. BUSCA POR FRASE EXATA (MUITO RÁPIDA)
        db.all(`SELECT conteudo FROM documentos WHERE conteudo_limpo LIKE ? LIMIT 3`, [`%${cleanQuery}%`], (err, phraseRows) => {
            
            if (phraseRows && phraseRows.length > 0) {
                console.log("[SISTEMA] Match exato de frase encontrado!");
                return resolve(phraseRows.map(r => r.conteudo).join('\n\n'));
            }

            // 2. SE NÃO ACHOU A FRASE, BUSCA PELAS PALAVRAS-CHAVE (AND - Mais preciso)
            const conditions = words.map(() => "conteudo_limpo LIKE ?").join(' AND ');
            const params = words.map(w => `%${w}%`);

            db.all(`SELECT conteudo FROM documentos WHERE ${conditions} LIMIT 5`, params, (err2, wordRows) => {
                
                if (wordRows && wordRows.length > 0) {
                    return resolve(wordRows.map(r => r.conteudo).join('\n\n'));
                }

                // 3. FALLBACK MESTRE (Se tudo falhar, tenta achar no mestre por qualquer palavra)
                const masterCond = words.map(() => "conteudo_limpo LIKE ?").join(' OR ');
                db.all(`SELECT conteudo FROM conhecimento_mestre WHERE ${masterCond} LIMIT 1`, params, (err3, masterRows) => {
                    resolve(masterRows ? masterRows[0].conteudo : "");
                });
            });
        });
    });
};

app.post('/ask', async (req, res) => {
    const { question } = req.body;
    try {
        const context = await getContext(question);
        
        const response = await axios({
            method: 'post',
            url: 'http://localhost:11434/api/generate',
            data: {
                model: 'meu-bot',
                prompt: `CONTEÚDO JB:\n${context}\n\nPERGUNTA: ${question}\n\nResponda apenas com base no conteúdo acima. Se não souber, diga 'Não localizado'.`,
                stream: true,
                options: { temperature: 0 }
            },
            responseType: 'stream'
        });

        res.setHeader('Content-Type', 'text/event-stream');
        response.data.on('data', chunk => {
            const lines = chunk.toString().split('\n');
            for (const line of lines) {
                if (!line.trim()) continue;
                try {
                    const parsed = JSON.parse(line);
                    if (parsed.response) res.write(`data: ${JSON.stringify({ text: parsed.response })}\n\n`);
                    if (parsed.done) { res.write('data: [DONE]\n\n'); res.end(); }
                } catch (e) {}
            }
        });
    } catch (error) { res.end(); }
});

app.listen(8899, () => console.log("Servidor v17 (Alta Velocidade) rodando"));
