

class ConnectBtn extends HTMLElement {
    constructor() {
        super();
        this.attachShadow({ mode: "open" });
        this.state = "offline"; // "offline", "connecting", "connected"
        this.rtcController = null;
    }

    async getWSUrl() {

        const response = await fetch(CONSTANTS.INIT_URL, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            agent_id: AppConfig.AGENT_ID || "test-agent",
            pk: AppConfig.PK || "test-pk",
        }),
        });

        const data = await response.json();

        return data.connection_url;
    }

    async connectedCallback() {

        try {

            
            this.rtcController = new WebRTCController();

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

        } catch (err) {
            console.error("Failed to initialize RTC:", err);
        }
        
        /*
        window.addEventListener(AppEvents.WS_CLOSED, () => {
            this.setState("offline");
        });

        window.addEventListener(AppEvents.WS_ERROR, () => {
            this.setState("offline");
        });
        */
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
