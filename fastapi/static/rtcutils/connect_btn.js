

class ConnectBtn extends HTMLElement {
    constructor() {
        super();
        this.attachShadow({ mode: "open" });
        this.state = "offline"; // "offline", "connecting", "connected"
        this.rtcController = new WebRTCController(this.getWSUrl() || AppConfig.WS_URL);
    }

    getWSUrl() {
        // --- FIX STARTS HERE ---
        // 1. Get the base host (e.g., epiphanic-marriageable-keely.ngrok-free.dev)
        const host = window.location.host;

        // 2. Use wss if the page is https, otherwise use ws
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

        // 3. Construct the full URL
        const url = `${protocol}//${host}/ws/`;
        // --- FIX ENDS HERE ---
        return url;
    }

    connectedCallback() {
        this.render();
        this.btn = this.shadowRoot.querySelector("button");
        this.btn.addEventListener("click", () => this.toggle());

        // Listen to connection changes to update visual state automatically
        window.addEventListener(AppEvents.RTC_STATECHANGE, (e) => {
            const { state } = e.detail;
            if (state === "connected") {
                this.setState("connected");
            } else if (state === "disconnected" || state === "failed" || state === "closed") {
                this.setState("offline");
            }
        });

        window.addEventListener(AppEvents.WS_CLOSED, () => {
            this.setState("offline");
        });

        window.addEventListener(AppEvents.WS_ERROR, () => {
            this.setState("offline");
        });
    }

    setState(newState) {
        this.state = newState;
        this.update();
    }

    toggle() {
        if (this.state === "offline") {
            this.setState("connecting");
            if (this.rtcController) {
                this.rtcController.connect();
            }
        } else {
            this.setState("offline");
            if (this.rtcController) {
                this.rtcController.disconnect();
            }
        }
    }

    update() {
        if (!this.btn) return;

        if (this.state === "offline") {
            this.btn.textContent = "CONNECT";
            this.btn.classList.remove("disconnect");
        } else if (this.state === "connected") {
            this.btn.textContent = "DISCONNECT";
            this.btn.classList.add("disconnect");
        }
    }

    render() {
        this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: inline-block;
        }

        button {
          font-family: 'DM Mono', monospace;
          padding: 4px 12px;
          border-radius: var(--radius-xs, 4px);
          border: none;
          background: var(--accent, #63d2ff);
          color: var(--bg, #0d1117);
          font-weight: 500;
          font-size: 11px;
          letter-spacing: 0.05em;
          text-transform: uppercase;
          cursor: pointer;
          transition: all 0.2s ease;
          flex-shrink: 0;
        }

        button:hover {
          background: #8ae4ff;
        }

        button.disconnect {
          background: rgba(248,81,73,0.2);
          border: none;
          color: var(--error, #f85149);
        }

        button.disconnect:hover {
          background: rgba(248,81,73,0.3);
        }
      </style>
      <button part="button">CONNECT</button>
    `;
    }
}

customElements.define("connect-btn", ConnectBtn);
