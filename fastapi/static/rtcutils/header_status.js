class HeaderStatus extends HTMLElement {
    constructor() {
        super();
        this.attachShadow({ mode: "open" });
        // Bind methods so they can be removed correctly
        this.handleRTC = this.handleRTC.bind(this);
        this.handleWSOpen = this.handleWSOpen.bind(this);
        this.handleWSClose = this.handleWSClose.bind(this);
        this.handleManual = this.handleManual.bind(this);
    }

    connectedCallback() {
        this.render();
        this.statusDot = this.shadowRoot.querySelector(".status-dot");
        this.statusText = this.shadowRoot.querySelector("#statusText");

        // Attach listeners to window
        window.addEventListener(AppEvents.RTC_STATECHANGE, this.handleRTC);
        window.addEventListener(AppEvents.WS_CONNECTED, this.handleWSOpen);
        window.addEventListener(AppEvents.WS_CLOSED, this.handleWSClose);
        window.addEventListener(AppEvents.WS_ERROR, this.handleWSClose); // Treat error as offline
        window.addEventListener('headerStatus', this.handleManual);
    }

    disconnectedCallback() {
        // Essential to prevent memory leaks and "zombie" state updates
        window.removeEventListener(AppEvents.RTC_STATECHANGE, this.handleRTC);
        window.removeEventListener(AppEvents.WS_CONNECTED, this.handleWSOpen);
        window.removeEventListener(AppEvents.WS_CLOSED, this.handleWSClose);
        window.removeEventListener(AppEvents.WS_ERROR, this.handleWSClose);
        window.removeEventListener('headerStatus', this.handleManual);
    }

    handleRTC(e) {
        const { state } = e.detail;
        const mapping = {
            'connected': ['connected', 'RTC ACTIVE'],
            'disconnected': ['offline', 'OFFLINE'],
            'closed': ['offline', 'OFFLINE'],
            'failed': ['error', 'FAILED'],
            'connecting': ['connecting', 'CONNECTING...']
        };
        if (mapping[state]) this.updateState(...mapping[state]);
    }

    handleWSOpen() { this.updateState("connected", "CONNECTED"); }
    handleWSClose() { this.updateState("offline", "OFFLINE"); }
    handleManual(e) { this.updateState(e.detail.state, e.detail.label); }

    updateState(state, label) {
        if (!this.statusDot || !this.statusText) return;

        // Force a clean class reset
        this.statusDot.className = `status-dot ${state}`;
        this.statusText.textContent = label || state.toUpperCase();
        // Debugging: uncomment this to see what is changing the state
        console.log(`Status changed to: ${state} (${label})`);
    }

    render() {
        this.shadowRoot.innerHTML = `
        <style>
          :host { display: inline-block; }
          .header-status { display: flex; align-items: center; gap: 8px; font-size: 11px; font-family: sans-serif; }
          .status-dot { width: 6px; height: 6px; border-radius: 50%; background: #404952; transition: all 0.3s ease; }
          
          /* Specificity matters: define colors clearly */
          .status-dot.offline { background: #404952; box-shadow: none; }
          .status-dot.connected { background: #3fb950; box-shadow: 0 0 6px #3fb950; }
          .status-dot.connecting { background: #d29922; animation: pulse 1s infinite; }
          .status-dot.error, .status-dot.failed { background: #f85149; }

          @keyframes pulse { 50% { opacity: 0.4; } }
        </style>
        <div class="header-status">
          <div class="status-dot offline"></div>
          <span id="statusText">OFFLINE</span>
        </div>`;
    }
}

customElements.define("header-status", HeaderStatus);