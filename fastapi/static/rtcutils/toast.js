// ─── Toast ────────────────────────────────────────────────────────────────────
/*
function toast(msg, type = 'info') {
  const icons = {
    success: '✓',
    error: '✕',
    info: 'ℹ'
  };
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<span style="color:${type==='success'?'var(--success)':type==='error'?'var(--error)':'var(--accent)'}">${icons[type]}</span> ${msg}`;
  document.getElementById('toastArea').appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

function toggleSettings() {
  toast('Settings coming soon', 'info');
}
*/


class ToastContainer extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  connectedCallback() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          position: fixed;
          bottom: 100px;
          right: 20px;
          z-index: 200;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }

        .toast {
          padding: 10px 16px;
          background: var(--surface-2, #222);
          border: 1px solid var(--border, #444);
          border-radius: var(--radius-sm, 8px);
          font-size: 12px;
          color: var(--text, #fff);
          max-width: 280px;

          display: flex;
          align-items: center;
          gap: 8px;

          animation: slideInRight 0.3s ease, fadeOut 0.3s ease 2.7s forwards;
        }

        .toast.success { border-color: rgba(63,185,80,0.4); }
        .toast.error { border-color: rgba(248,81,73,0.4); }
        .toast.info { border-color: var(--border-active, #666); }

        @keyframes slideInRight {
          from { opacity: 0; transform: translateX(20px); }
          to { opacity: 1; transform: translateX(0); }
        }

        @keyframes fadeOut {
          from { opacity: 1; }
          to { opacity: 0; }
        }
      </style>

      <div id="container"></div>
    `;

    this.container = this.shadowRoot.getElementById("container");
    window.addEventListener(AppEvents.TOAST, this._handler);
  }


  _handler = (e) => {
    const { message, type, duration } = e.detail || {};
    this.show(message, type, duration);
  };

  disconnectedCallback() {
    window.removeEventListener(AppEvents.TOAST, this._handler);
  }

  show(message, type = "info", duration = 3000) {
    const icons = {
      success: "✓",
      error: "✕",
      info: "ℹ"
    };

    const toast = document.createElement("div");
    toast.className = `toast ${type}`;

    toast.innerHTML = `
      <span class="icon">${icons[type] || "ℹ"}</span>
      <span>${message}</span>
    `;

    this.container.appendChild(toast);

    setTimeout(() => {
      toast.remove();
    }, duration);
  }
}

customElements.define("toast-container", ToastContainer);