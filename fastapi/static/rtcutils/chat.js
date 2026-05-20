/*function appendMessage(role, text) {
  emptyState.style.display = 'none';

  const msg = document.createElement('div');
  msg.className = `message ${role}`;

  const now = new Date();
  const time = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  msg.innerHTML = `
    <div class="avatar ${role}">${role === 'ai' ? 'AR' : 'ME'}</div>
    <div>
      <div class="bubble">${escapeHtml(text)}</div>
      <div class="bubble-meta">${time}</div>
    </div>
  `;

  chatArea.appendChild(msg);
  chatArea.scrollTop = chatArea.scrollHeight;
  messages.push({ role, text, time });
}

function showTyping() {
  const el = document.createElement('div');
  el.className = 'message ai';
  el.id = 'typingIndicator';
  el.innerHTML = `
    <div class="avatar ai">AR</div>
    <div class="bubble">
      <div class="typing-indicator">
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
      </div>
    </div>
  `;
  chatArea.appendChild(el);
  chatArea.scrollTop = chatArea.scrollHeight;
}

function hideTyping() {
  const el = document.getElementById('typingIndicator');
  if (el) el.remove();
}

function sendMessage() {
  const text = msgInput.value.trim();
  if (!text) return;

  appendMessage('user', text);
  msgInput.value = '';
  autoResize(msgInput);

  if (isConnected && ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'text', content: text }));
    showTyping();
    // typing indicator removed when AI replies
    setTimeout(hideTyping, 8000); // fallback
  } else {
    toast('Not connected to server', 'error');
    // Demo echo in offline mode
    setTimeout(() => {
      appendMessage('ai', `[Offline] Echo: ${text}`);
    }, 600);
  }
}

function handleKeyDown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 140) + 'px';
  const len = el.value.length;
  document.getElementById('charCount').textContent = len > 0 ? len + ' chars' : '';
}

function clearChat() {
  chatArea.innerHTML = '';
  chatArea.appendChild(emptyState);
  emptyState.style.display = 'flex';
  messages = [];
}

function insertSuggestion(text) {
  msgInput.value = text;
  msgInput.focus();
  autoResize(msgInput);
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}*/



class AriaChatComponent extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._messages = [];
    this._activeStreamEl = null;
  }

  connectedCallback() {
    this.render();
    this.setupEventListeners();
  }

  // Define properties to handle "Room Mode" vs "GPT Mode"
  static get observedAttributes() {
    return ['mode', 'placeholder', 'agent-name'];
  }

  render() {
    const mode = this.getAttribute('mode') || 'chat-room';
    const agentName = this.getAttribute('agent-name') || 'Fin';

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          height: 100%; /* CRITICAL: Host must have height */
          --accent: #63d2ff;
          --surface: #0d1117;
          --border: rgba(255, 255, 255, 0.1);
        }

        .chat-container {
          display: flex;
          flex-direction: column;
          height: 100%;
          min-height: 0; /* IMPORTANT for flex scroll */
          max-height: 100%; /* Prevent expanding beyond parent */
          background: #080c10;
          color: #e6edf3;
          border-radius: 12px;
          overflow: visible;
        }

        .chat-area {
          flex: 1;
          min-height: 0; /* THIS enables scrolling */
          overflow-y: auto; /* Enable scrolling */
          gap: 20px;
          padding: 24px;
          display: block;
          flex-direction: column;
          scrollbar-width: thin;
          scrollbar-color: var(--surface-2) transparent;
        }

        /* Webkit scrollbar styling */
        .chat-area::-webkit-scrollbar { width: 5px; }
        .chat-area::-webkit-scrollbar-track { background: transparent; }
        .chat-area::-webkit-scrollbar-thumb { 
          background: var(--surface-2); 
          border-radius: 10px; 
        }


        /* ─── The User Message Card ─── */
        .message-row.user {
          justify-content: flex-end;
          display: flex;
        }

        .message-card {
          background: var(--surface-2); /* Darker grey/black card */
          border: 1px solid var(--border);
          border-radius: 16px;
          padding: 12px 16px;
          max-width: 80%;
          color: white;
          box-shadow: 0 4px 12px rgba(0,0,0,0.2);
          position: relative;
        }

        /* ─── The System/AI Message (Standard) ─── */
        .message-row.system {
          justify-content: flex-start;
          display: flex;
        }

        .system-message {
          max-width: 90%;
          line-height: 1.6;
          color: #c9d1d9;
          padding: 8px 0;
        }

        .system-message b {
          color: var(--accent);
          display: block;
          margin-bottom: 4px;
          font-family: 'Syne', sans-serif;
        }

        .input-section {
          padding: 16px;
          border-top: 1px solid var(--border);
          background: #0d1117;
          border-radius: 0 0 12px 12px;
        }

        /* Fixed Space for Visualizer to prevent jumping */
        .viz-slot {
          min-height: 60px; /* Adjust based on your visualizer height */
          display: flex;
          justify-content: center;
          align-items: center;
          margin-bottom: 12px;
        }

        /* If slot is empty, it still takes up space but stays quiet */
        .viz-slot:empty {
          display: flex;
        }

        .textarea-wrapper {
          position: relative;
          background: #161b22;
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 10px;
        }

        textarea {
          width: 100%;
          background: transparent;
          border: none;
          color: white;
          outline: none;
          resize: none;
          font-family: 'DM Mono', monospace;
          font-size: 14px;
          min-height: 40px;
        }

        /* Controls Slot (Below Text) */
        .controls-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-top: 12px;
        }

        

        .media-group {
          display: flex;
          gap: 12px;
          align-items: center;
        }


        /* Unified class for internal and slotted buttons */
        .action-btn, 
        .media-group ::slotted(*) {
          width: 34px !important;
          height: 34px !important;
          min-width: 34px !important; /* Prevents shrinking */
          max-width: 34px !important;
          min-height: 34px !important;
          max-height: 34px !important;
          border-radius: var(--radius-xs);
          display: flex;
          align-items: center;
          justify-content: center;
          box-sizing: border-box;
        }

        /* Ensure SVG icons inside slotted buttons scale correctly */
        .media-group ::slotted(svg),
        .media-group ::slotted(button) svg {
          width: 16px !important;
          height: 16px !important;
        }

        .action-btn:hover {
          border-color: var(--accent);
          background: var(--border);
        }


        /* Specific style for the Send Button to match your SVG needs */
        .send-btn {
          background: var(--accent);
          color: var(--bg);
          border: none;
        }

        .send-btn:hover {
          filter: brightness(1.1);
          transform: translateY(-1px);
        }
        /* Message related CSS*/
         .message-row { 
        display: flex; 
        flex-direction: column; 
        margin-bottom: 12px; /* Tighter spacing for hidden meta rows */
        animation: fadeIn 0.2s ease-out;
      }
      @keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
      
      .message-row.user { align-items: flex-end; }
      .message-row.system { align-items: flex-start; }

      .bubble {
        max-width: 85%;
        padding: 12px 16px;
        font-size: 14.5px;
        line-height: 1.5;
        position: relative;
      }

      .user .bubble {
        background: #21262d;
        border: 1px solid var(--border);
        border-radius: 18px 18px 2px 18px;
        color: #f0f6fc;
      }

      .system .bubble {
        background: transparent;
        border-radius: 0;
        padding-left: 20px;
        color: #c9d1d9;
      }

      .message-row { 
        
      }

      /* Hide metadata by default */
      .meta {
        display: none;
        font-size: 11px;
        color: #8b949e;
        margin-top: 6px;
        font-family: 'DM Mono', monospace;
        text-transform: uppercase;
      }

      /* ONLY show metadata for the absolute last message in the list */
      .message-row:last-child {
        margin-bottom: 24px;
      }
      
      .message-row:last-child .meta {
        display: block;
      }
      
      .role-label {
        font-weight: 700;
        color: var(--accent);
        margin-bottom: 4px;
        font-size: 12px;
      }  
      </style>

      <div class="chat-container">
        <div class="chat-area" id="scroll-area">
          <slot name="empty-state"></slot>
          <div id="messages-list"></div>
        </div>

        <div class="input-section">
          <!-- SLOT 1: Above Text Area (Visualizer) -->
          <div class="viz-slot">
            <slot name="visualizer"></slot>
          </div>

          <div class="textarea-wrapper">
            <textarea id="userInput" placeholder="Message ARIA..."></textarea>
          </div>

          <div class="controls-row">
            <!-- SLOT 2: Below Text Area (Mic, Cam, Media) -->
            <div class="media-group">
              <slot name="mic-control"></slot>
              <slot name="camera-control"></slot>
              <slot name="media-upload"></slot>
              <slot name="generic-actions"></slot>
            </div>

            <button class="action-btn send-btn" id="sendBtn">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">

              <line x1="22" y1="2" x2="11" y2="13"></line>

              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>

            </svg>
            </button>
          </div>
        </div>
      </div>
    `;
  }

  setupEventListeners() {
    const input = this.shadowRoot.getElementById('userInput');
    const btn = this.shadowRoot.getElementById('sendBtn');
    const scrollArea = this.shadowRoot.getElementById('scroll-area');
    const list = this.shadowRoot.getElementById('messages-list');

    // 1. Handle Auto-resize Textarea
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = input.scrollHeight + 'px';
    });

    // 2. Handle Enter and Shift+Enter
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault(); // Prevents new line
        this.handleSendMessage();
      }
      // If Shift + Enter, browser default handles the new line automatically
    });

    // 3. Handle Send Button Click
    btn.addEventListener('click', () => this.handleSendMessage());


    // 4. Handle Message Reciving from Server
    window.addEventListener(AppEvents.CHAT_MESSAGE_RECEIVED, (e) => {
      console.log("Event Message to Chat Component: ", e);
      const { id, chunk, done } = e.detail;

      if (!this._activeStreamEl) {
        this._activeStreamEl = this.createStreamingMessage();
      }

      this.updateStreamingMessage(chunk);

      if (done) {
        this.finalizeStreamingMessage();
      }
    });
  }

  handleSendMessage() {
    const input = this.shadowRoot.getElementById('userInput');
    const text = input.value.trim();
    if (!text) return;

    this.appendMessage('user', text);

    // Dispatch event to parent
    this.dispatchEvent(new CustomEvent(AppEvents.CHAT_MESSAGE_SENT, {
      detail: { message: text },
      bubbles: true,
      composed: true
    }));

    input.value = '';
    input.style.height = 'auto';

  }

  appendMessage(role, text) {
    const list = this.shadowRoot.getElementById('messages-list');
    const msgRow = document.createElement('div');
    const scrollArea = this.shadowRoot.getElementById('scroll-area');
    msgRow.className = `message-row ${role}`;

    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    // Professional look: User messages have a subtle border/glow, 
    // System messages have a clean, indented look with an accent label.
    msgRow.innerHTML = `
    <div class="bubble">${text}</div>
    <div class="meta">${role === 'user' ? 'Sent' : 'Processed'} • ${time}</div>
  `;

    list.appendChild(msgRow);

    // 🔥 FORCE scroll AFTER DOM update
    requestAnimationFrame(() => {
      scrollArea.scrollTop = scrollArea.scrollHeight;
    });


  }


  createStreamingMessage() {
    const list = this.shadowRoot.getElementById('messages-list');
    const scrollArea = this.shadowRoot.getElementById('scroll-area');

    const msgRow = document.createElement('div');
    msgRow.className = `message-row system`;

    msgRow.innerHTML = `
    <div class="bubble"></div>
    <div class="meta">...</div>
  `;

    list.appendChild(msgRow);

    requestAnimationFrame(() => {
      scrollArea.scrollTop = scrollArea.scrollHeight;
    });

    return msgRow.querySelector('.bubble'); // 👈 return bubble only
  }


  updateStreamingMessage(chunk) {
    if (!this._activeStreamEl) return;

    console.log("chunk:", chunk);
    // Append instead of replace
    this._activeStreamEl.innerHTML += chunk;

    const scrollArea = this.shadowRoot.getElementById('scroll-area');
    scrollArea.scrollTop = scrollArea.scrollHeight;
  }

  finalizeStreamingMessage() {
    if (!this._activeStreamEl) return;

    const row = this._activeStreamEl.closest('.message-row');
    const meta = row.querySelector('.meta');

    const time = new Date().toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit'
    });

    meta.textContent = `Processed • ${time}`;

    this._activeStreamEl = null; // 👈 reset
  }

  async mockServerCall(userText) {
    // Random delay between 1-2 seconds
    const delay = Math.floor(Math.random() * 1000) + 1000;

    await new Promise(resolve => setTimeout(resolve, delay));

    const responses = [
      "Query received. Accessing encrypted database...",
      `Analysis of "${userText}" complete. No anomalies detected.`,
      "Systems nominal. Standing by for further instructions.",
      "I've updated the project logs with your latest entry."
    ];

    const html = `  <section>
    <h2>Main Content Section</h2>
    <div class="content-wrapper">
      <div>
        <p>This div contains sample content that you can use in your web development projects.</p>
      </div>
      <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.</p>
      <h2>Heading 3 (H2)</h2>
      <ol>
        <li>Step 4: First action</li>
        <li>Step 5: Second action</li>
        <li>Step 6: Final action</li>
      </ol>
      <div>
        <p>This div contains sample content that you can use in your web development projects.</p>
      </div>
      <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.</p>
      <h1>Heading 7 (H1)</h1>
      <ol>
        <li>Step 8: First action</li>
        <li>Step 9: Second action</li>
        <li>Step 10: Final action</li>
      </ol>
      <div>
        <p>This div contains sample content that you can use in your web development projects.</p>
      </div>
      <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.</p>
      <h3>Heading 11 (H3)</h3>
      <ol>
        <li>Step 12: First action</li>
        <li>Step 13: Second action</li>
        <li>Step 14: Final action</li>
      </ol>
      <div>
        <p>This div contains sample content that you can use in your web development projects.</p>
      </div>
      <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.</p>
      <h2>Heading 15 (H2)</h2>
      <ol>
        <li>Step 16: First action</li>
        <li>Step 17: Second action</li>
        <li>Step 18: Final action</li>
      </ol>
      <div>
        <p>This div contains sample content that you can use in your web development projects.</p>
      </div>      <a href="https://examplefile.com" title="Sample File" target="_blank"><img style="width: 100%; max-width:400px;" src="https://examplefile.com/images/logo.png" alt="ExampleFile Logo"></a>


      <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.</p>
      <h3>Heading 19 (H3)</h3>
      <ol>
        <li>Step 20: First action</li>
        <li>Step 21: Second action</li>
        <li>Step 22: Final action</li>
      </ol>
    </div>
  </section>`

    const randomMsg = responses[Math.floor(Math.random() * responses.length)];
    this.dispatchEvent(new CustomEvent(AppEvents.CHAT_MESSAGE_RECEIVED, {
      detail: { message: html },
      bubbles: true,
      composed: true
    }));
  }

  setupInputLogic() {
    const input = this.shadowRoot.getElementById('userInput');
    const btn = this.shadowRoot.getElementById('sendBtn');

    input.addEventListener('keydown', (e) => {
      // Shift + Enter: Allow default (newline)
      if (e.key === 'Enter' && e.shiftKey) {
        return;
      }

      // Enter: Send message
      if (e.key === 'Enter') {
        e.preventDefault();
        this.handleSendMessage();
      }
    });

    btn.addEventListener('click', () => this.handleSendMessage());

    // Auto-scroll logic: observe changes in the message slot
    const observer = new MutationObserver(() => {
      scrollArea.scrollTop = scrollArea.scrollHeight;
    });
    observer.observe(scrollArea, { childList: true, subtree: true });

  }


}

customElements.define('aria-chat-room', AriaChatComponent);