import React, { useState, useEffect, useRef } from 'react';
import { Send, Bot, User, Loader2, Square, Edit2, RotateCcw } from 'lucide-react';
import './App.css';

function App() {
  const [messages, setMessages] = useState([
    { text: "Olá! Sou o assistente do Grupo JB. Como posso te ajudar hoje?", isBot: true }
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef(null);
  const abortControllerRef = useRef(null); // Para cancelar a requisição

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSend = async (overrideInput = null) => {
    const question = overrideInput || input;
    if (!question.trim() || isLoading) return;

    // Cancela requisição anterior se houver
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    abortControllerRef.current = new AbortController();

    if (!overrideInput) {
      setMessages(prev => [...prev, { text: question, isBot: false }]);
      setInput('');
    }
    
    setIsLoading(true);
    setMessages(prev => [...prev, { text: "", isBot: true, isStreaming: true }]);

    try {
      const response = await fetch('http://localhost:8899/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
        signal: abortControllerRef.current.signal // Conecta o sinal de cancelamento
      });

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let fullText = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const dataStr = line.replace('data: ', '').trim();
            if (dataStr === '[DONE]') break;

            try {
              const data = JSON.parse(dataStr);
              if (data.text) {
                fullText += data.text;
                setMessages(prev => {
                  const newMessages = [...prev];
                  newMessages[newMessages.length - 1].text = fullText;
                  return newMessages;
                });
              }
            } catch (e) { }
          }
        }
      }
    } catch (error) {
      if (error.name === 'AbortError') {
        setMessages(prev => {
          const newMessages = [...prev];
          newMessages[newMessages.length - 1].text += " [Geração cancelada pelo usuário]";
          return newMessages;
        });
      } else {
        setMessages(prev => [...prev, { text: "Erro ao conectar. Tente novamente.", isBot: true }]);
      }
    } finally {
      setIsLoading(false);
      abortControllerRef.current = null;
      setMessages(prev => {
        const newMessages = [...prev];
        if (newMessages[newMessages.length - 1]) {
          newMessages[newMessages.length - 1].isStreaming = false;
        }
        return newMessages;
      });
    }
  };

  const handleCancel = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  };

  const handleEdit = (text) => {
    setInput(text);
    // Opcional: focar no input
    document.querySelector('input').focus();
  };

  const handleRetry = (text) => {
    handleSend(text);
  };

  return (
    <div className="app-container">
      <header className="header">
        <h1>Meu-Bot Gestor <span className="speed-tag">V2</span></h1>
      </header>

      <div className="chat-window">
        {messages.map((msg, idx) => (
          <div key={idx} className={`message-wrapper ${msg.isBot ? 'bot' : 'user'}`}>
            <div className="icon-wrapper">
              {msg.isBot ? <Bot size={20} /> : <User size={20} />}
            </div>
            <div className="message-container">
              <div className="message-content">
                {msg.text || (msg.isStreaming ? "..." : "")}
              </div>
              {!msg.isBot && !isLoading && (
                <div className="message-actions">
                  <button className="action-btn" onClick={() => handleEdit(msg.text)} title="Editar">
                    <Edit2 size={14} />
                  </button>
                  <button className="action-btn" onClick={() => handleRetry(msg.text)} title="Tentar novamente">
                    <RotateCcw size={14} />
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="input-container">
        <div className="input-wrapper">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleSend()}
            placeholder="Pergunte qualquer coisa..."
            disabled={isLoading}
          />
          {isLoading ? (
            <button className="cancel-btn" onClick={handleCancel} title="Parar Geração">
              <Square size={20} fill="currentColor" />
            </button>
          ) : (
            <button className="send-btn" onClick={() => handleSend()} disabled={!input.trim()}>
              <Send size={20} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;
