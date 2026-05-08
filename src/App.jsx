import React, { useState, useEffect, useRef } from 'react';
import { 
  Send, 
  Bot, 
  User, 
  Plus, 
  MessageSquare, 
  History, 
  LogOut, 
  Moon, 
  Sun, 
  CircleNotch,
  Edit,
  ChevronRight
} from 'lucide-react';
import './App.css';

function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [theme, setTheme] = useState('light');
  const [conversations, setConversations] = useState([]);
  const [currentChatId, setCurrentChatId] = useState(null);
  const [user, setUser] = useState({ id: 1, username: 'Gestor JB', role: 'admin' }); // Mock for now, sync with auth later
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    loadConversations();
  }, []);

  const loadConversations = async () => {
    try {
      const response = await fetch(`/api/conversations?user_id=${user.id}`);
      const data = await response.json();
      setConversations(data);
    } catch (e) { console.error("Erro ao carregar conversas"); }
  };

  const startNewChat = () => {
    setMessages([]);
    setCurrentChatId(null);
  };

  const loadChat = async (id) => {
    setCurrentChatId(id);
    setIsLoading(true);
    try {
      const response = await fetch(`/api/messages/${id}`);
      const data = await response.json();
      setMessages(data.map(m => ({ text: m.content, isBot: m.role !== 'user' })));
    } catch (e) { console.error("Erro ao carregar mensagens"); }
    finally { setIsLoading(false); }
  };

  const handleSend = async (text = null) => {
    const question = text || input;
    if (!question.trim() || isLoading) return;

    if (!text) setInput('');
    setMessages(prev => [...prev, { text: question, isBot: false }]);
    setIsLoading(true);

    const botMsgIndex = messages.length + 1;
    setMessages(prev => [...prev, { text: "", isBot: true, isStreaming: true }]);

    try {
      // Auto-create conversation if none
      let chatId = currentChatId;
      if (!chatId) {
        const res = await fetch('/api/conversations', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: user.id, titulo: question.substring(0, 30) })
        });
        const chatData = await res.json();
        chatId = chatData.id;
        setCurrentChatId(chatId);
        loadConversations();
      }

      const response = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          question, 
          conversa_id: chatId,
          user_id: user.id,
          history: messages.map(m => ({ role: m.isBot ? 'assistant' : 'user', content: m.text }))
        })
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
                  const newMsgs = [...prev];
                  newMsgs[newMsgs.length - 1].text = fullText;
                  return newMsgs;
                });
              }
            } catch (e) {}
          }
        }
      }
    } catch (error) {
      setMessages(prev => [...prev, { text: "Erro de conexão. Tente novamente.", isBot: true }]);
    } finally {
      setIsLoading(false);
      setMessages(prev => {
        const newMsgs = [...prev];
        if (newMsgs[newMsgs.length-1]) newMsgs[newMsgs.length-1].isStreaming = false;
        return newMsgs;
      });
    }
  };

  return (
    <div className={`app-container ${theme}`} data-theme={theme}>
      {/* Sidebar - Foto 1 Style */}
      <aside className="sidebar">
        <div className="logo">
          <div className="logo-icon"><Bot size={24} /></div>
          <span>JB INTEL</span>
        </div>
        
        <button className="new-chat-btn" onClick={startNewChat}>
          <Plus size={18} /> Novo Chat
        </button>

        <div className="nav-history">
          <div className="nav-title"><History size={14} /> Recentes</div>
          {conversations.map(conv => (
            <div 
              key={conv.id} 
              className={`nav-item ${currentChatId === conv.id ? 'active' : ''}`}
              onClick={() => loadChat(conv.id)}
            >
              <MessageSquare size={14} />
              <span>{conv.titulo}</span>
            </div>
          ))}
        </div>

        <div className="user-profile">
          <div className="user-avatar">{user.username[0]}</div>
          <div className="user-details">
            <span className="username">{user.username}</span>
            <span className="user-role">{user.role}</span>
          </div>
          <button className="logout-btn"><LogOut size={16} /></button>
        </div>
      </aside>

      {/* Main Chat Area */}
      <main className="main-content">
        <header className="main-header">
          <div className="header-actions">
            <button className="theme-toggle" onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>
              {theme === 'light' ? <Moon size={20} /> : <Sun size={20} />}
            </button>
            <div className="user-badge"><User size={20} /></div>
          </div>
        </header>

        <div className="chat-window">
          {messages.length === 0 ? (
            <div className="welcome-screen">
              <h1>Olá, como posso ajudar?</h1>
              <p>Sou o assistente inteligente do Grupo JB. Especialista em logística e gestão operacional.</p>
              
              <div className="quick-actions">
                <div className="action-card" onClick={() => handleSend("Quem é o motorista da rota FOR 101?")}>
                  <div className="card-icon"><ChevronRight size={18} /></div>
                  <div className="card-text">
                    <strong>Rota FOR 101</strong>
                    <span>Consulte motorista e parceiro rapidamente.</span>
                  </div>
                </div>
                <div className="action-card" onClick={() => handleSend("Como identificar pedidos críticos?")}>
                  <div className="card-icon"><ChevronRight size={18} /></div>
                  <div className="card-text">
                    <strong>Pedidos Críticos</strong>
                    <span>Lógica de priorização (MNOP02).</span>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div className="messages-list">
              {messages.map((msg, idx) => (
                <div key={idx} className={`message-wrapper ${msg.isBot ? 'bot' : 'user'}`}>
                  <div className="message-bubble">
                    {msg.text.split('\n').map((line, i) => (
                      <React.Fragment key={i}>
                        {line}
                        <br />
                      </React.Fragment>
                    ))}
                    {msg.isBot && user.role === 'admin' && !msg.isStreaming && (
                      <button className="correct-btn"><Edit size={12} /> Corrigir IA</button>
                    )}
                  </div>
                </div>
              ))}
              {isLoading && (
                <div className="typing-indicator">
                  <CircleNotch size={14} className="spin" /> Analisando documentos...
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        <div className="input-area">
          <div className="input-container">
            <textarea 
              placeholder="Pergunte ao JB Intelligence..." 
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && (e.preventDefault(), handleSend())}
              rows="1"
            />
            <button className="send-button" onClick={() => handleSend()} disabled={!input.trim() || isLoading}>
              <Send size={20} />
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
