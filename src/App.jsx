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
  Loader2, 
  Edit,
  ChevronRight,
  Brain,
  Lock
} from 'lucide-react';
import './App.css';

function App() {
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [user, setUser] = useState(null);
  const [loginData, setLoginData] = useState({ username: '', password: '' });
  const [loginError, setLoginError] = useState('');
  
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [theme, setTheme] = useState('light');
  const [conversations, setConversations] = useState([]);
  const [currentChatId, setCurrentChatId] = useState(null);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    if (isLoggedIn) {
      scrollToBottom();
      loadConversations();
    }
  }, [messages, isLoggedIn]);

  const loadConversations = async () => {
    try {
      const response = await fetch(`https://back-end-ia-jb.onrender.com/api/conversations?user_id=${user.id}`);
      const data = await response.json();
      setConversations(data);
    } catch (e) { console.error("Erro ao carregar conversas"); }
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    setLoginError('');
    try {
      const response = await fetch('https://back-end-ia-jb.onrender.com/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(loginData)
      });
      const data = await response.json();
      if (data.status === 'success') {
        setUser(data.user);
        setIsLoggedIn(true);
      } else {
        setLoginError(data.message || 'Usuário ou senha incorretos');
      }
    } catch (e) {
      setLoginError('Erro ao conectar ao servidor');
    }
  };

  const startNewChat = () => {
    setMessages([]);
    setCurrentChatId(null);
  };

  const loadChat = async (id) => {
    setCurrentChatId(id);
    setIsLoading(true);
    try {
      const response = await fetch(`https://back-end-ia-jb.onrender.com/api/messages/${id}`);
      const data = await response.json();
      setMessages(data.map(m => ({ text: m.content, isBot: m.role !== 'user' })));
    } catch (e) { console.error("Erro ao carregar mensagens"); }
    finally { setIsLoading(false); }
  };

  const teachIA = async (question) => {
    const answer = prompt("Qual a resposta correta para esta pergunta?");
    if (!answer) return;

    try {
      const response = await fetch('https://back-end-ia-jb.onrender.com/api/learn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pergunta: question, resposta: answer, admin_id: user.id })
      });
      const data = await response.json();
      alert(data.message);
    } catch (e) { alert("Erro ao salvar treinamento"); }
  };

  const handleSend = async (text = null) => {
    const question = text || input;
    if (!question.trim() || isLoading) return;

    if (!text) setInput('');
    setMessages(prev => [...prev, { text: question, isBot: false }]);
    setIsLoading(true);

    const currentHistory = [...messages];
    setMessages(prev => [...prev, { text: "", isBot: true, isStreaming: true }]);

    try {
      let chatId = currentChatId;
      if (!chatId) {
        const res = await fetch('https://back-end-ia-jb.onrender.com/api/conversations', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: user.id, titulo: question.substring(0, 30) })
        });
        const chatData = await res.json();
        chatId = chatData.id;
        setCurrentChatId(chatId);
        loadConversations();
      }

      const response = await fetch('https://back-end-ia-jb.onrender.com/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          question, 
          conversa_id: chatId,
          user_id: user.id,
          history: currentHistory.map(m => ({ role: m.isBot ? 'assistant' : 'user', content: m.text }))
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

  if (!isLoggedIn) {
    return (
      <div className="login-screen" data-theme={theme}>
        <div className="login-box">
          <div className="login-logo">
            <Brain size={48} className="logo-icon-img" />
            <h1>JB INTEL</h1>
          </div>
          <h2>Bem-vindo ao Gestor IA</h2>
          <form onSubmit={handleLogin}>
            <div className="input-group">
              <User size={18} />
              <input 
                type="text" 
                placeholder="Usuário" 
                value={loginData.username}
                onChange={e => setLoginData({...loginData, username: e.target.value})}
                required
              />
            </div>
            <div className="input-group">
              <Lock size={18} />
              <input 
                type="password" 
                placeholder="Senha" 
                value={loginData.password}
                onChange={e => setLoginData({...loginData, password: e.target.value})}
                required
              />
            </div>
            {loginError && <p className="error-msg">{loginError}</p>}
            <button type="submit" className="login-btn">Entrar no Sistema</button>
          </form>
          <p className="footer-text">Grupo JB - Inteligência Logística</p>
        </div>
      </div>
    );
  }

  return (
    <div className={`app-container ${theme}`} data-theme={theme}>
      <aside className="sidebar">
        <div className="logo">
          <div className="logo-icon"><Brain size={24} /></div>
          <span>JB INTEL</span>
        </div>
        
        <button className="new-chat-btn" onClick={startNewChat}>
          <Plus size={18} /> Novo Chat
        </button>

        <div className="nav-history">
          <div className="nav-title"><History size={14} /> Histórico</div>
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
          <div className="user-avatar">{user.username[0].toUpperCase()}</div>
          <div className="user-details">
            <span className="username">{user.username}</span>
            <span className="user-role">{user.role}</span>
          </div>
          <button className="logout-btn" onClick={() => setIsLoggedIn(false)}><LogOut size={16} /></button>
        </div>
      </aside>

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
                      <button className="correct-btn" onClick={() => teachIA(messages[idx-1]?.text)}>
                        <Edit size={12} /> Corrigir IA
                      </button>
                    )}
                  </div>
                </div>
              ))}
              {isLoading && (
                <div className="typing-indicator">
                  <Loader2 size={14} className="spin" /> Analisando documentos...
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
