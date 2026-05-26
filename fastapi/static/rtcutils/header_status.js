class HeaderStatus extends HTMLElement {
    static get observedAttributes() {
        return ["label"];
    }

    constructor() {
        super();
        this.attachShadow({ mode: "open" });

        this.wsOpen = false;
        this.rtcState = "disconnected";

        this.handleRTC = this.handleRTC.bind(this);
        this.handleWSOpen = this.handleWSOpen.bind(this);
        this.handleWSClose = this.handleWSClose.bind(this);
    }

    connectedCallback() {
        this.render();
        this.statusDot = this.shadowRoot.querySelector(".status-dot");
        this.statusText = this.shadowRoot.querySelector("#statusText");

        window.addEventListener(AppEvents.RTC_STATECHANGE, this.handleRTC);
        window.addEventListener(AppEvents.WS_CONNECTED, this.handleWSOpen);
        window.addEventListener(AppEvents.WS_CLOSED, this.handleWSClose);
        window.addEventListener(AppEvents.WS_ERROR, this.handleWSClose);
    }

    disconnectedCallback() {
        window.removeEventListener(AppEvents.RTC_STATECHANGE, this.handleRTC);
        window.removeEventListener(AppEvents.WS_CONNECTED, this.handleWSOpen);
        window.removeEventListener(AppEvents.WS_CLOSED, this.handleWSClose);
        window.removeEventListener(AppEvents.WS_ERROR, this.handleWSClose);
    }

    handleWSOpen() {
        this.wsOpen = true;
        this.evaluate();
    }

    handleWSClose() {
        this.wsOpen = false;
        this.evaluate();
    }

    handleRTC(e) {
        this.rtcState = e.detail.state;
        this.evaluate();
    }

    evaluate() {
        let state = "offline";
        let label = "OFFLINE";

        // 1. RTC is truth
        if (this.rtcState === "connected") {
            state = "connected";
            label = "ONLINE";
        }

        else if (this.rtcState === "connecting") {
            state = "connecting";
            label = "CONNECTING...";
        }

        else if (this.rtcState === "failed") {
            state = "error";
            label = "FAILED";
        }

        // 2. WS only matters if RTC not ready yet
        else if (this.wsOpen) {
            state = "connecting";
            label = "SETTING UP...";
        }
        
        if (this.hasAttribute("label")) {
            this.updateState(state, label);
        } else {
            this.updateState(state, null);
        }
    }

    updateState(state, label) {
        if (!this.statusDot || !this.statusText) return;

        this.statusDot.className = `status-dot ${state}`;
        // only update text if label is provided
        if (label) {
            this.statusText.textContent = label;
        }

        console.log(`WS:${this.wsOpen} RTC:${this.rtcState} => ${state}`);
    }

    render() {
        this.shadowRoot.innerHTML = `
        <style>
          :host { display: inline-block; }
          .header-status { display: flex; align-items: center; gap: 8px; font-size: 11px; font-family: sans-serif; }
          .status-dot { width: 6px; height: 6px; border-radius: 50%; background: #404952; transition: all 0.3s ease; }

          .status-dot.offline { background: #404952; }
          .status-dot.connected { background: #3fb950; box-shadow: 0 0 6px #3fb950; }
          .status-dot.connecting { background: #d29922; animation: pulse 1s infinite; }
          .status-dot.error { background: #f85149; }

          @keyframes pulse { 50% { opacity: 0.4; } }
        </style>
        <div class="header-status">
          <div class="status-dot " label="offline"></div>
          <span id="statusText"></span>
        </div>`;
    }
}

customElements.define("header-status", HeaderStatus);