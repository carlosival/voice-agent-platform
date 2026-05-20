class MicController extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.enabled = false;
    this.micStream = null;
    this.micTrack = null;

  }


  async initMic() {
    if (this.micStream) return;
    try {
      this.micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        }
      });

      this.micTrack = this.micStream.getAudioTracks()[0];

      const ctx = window.audioRouters.mic.getContext();

      // 1. Setup Analyser (Tap)
      this.analyser = ctx.createAnalyser();
      this.analyser.fftSize = 64;
      this.data = new Uint8Array(this.analyser.frequencyBinCount);

      // 2. Attach to Router
      // This swaps the "Warm-up Silence" for the "Real Mic" inside the WebAudio graph
      window.audioRouters.mic.attachStream(this.micStream);

      // 3. Connect visualizer tap
      window.audioRouters.mic.connectTap(this.analyser);

      // 4. IMPORTANT: Ensure we start muted in the AudioRouter's GainNode
      const gain = window.audioRouters.mic.getNode(GainNode);
      if (gain && !this.enabled) {
        gain.gain.value = 0;
      }


     // window.audioRouters.mic.attachStream(new MediaStream([this.micTrack]));

      window.dispatchEvent(
        new CustomEvent(AppEvents.MIC_READY, { detail: { track: this.micTrack } })
      );


    } catch (err) {
      console.error("Mic error:", err);
      window.dispatchEvent(new CustomEvent(AppEvents.MIC_ERROR, { detail: err }));
    }
  }

  async connectedCallback() {
    this.render();  // 1. build DOM first
    this.btn = this.shadowRoot.querySelector("button");
    this.btn.addEventListener("click", () => this.toggle());

    await this.initMic(); // 2. await so micTrack exists before update()
    this.update();        // 3. now btn exists and enabled=false is reflected

    // Auto-enable when WebRTC connects
    window.addEventListener(AppEvents.RTC_CONNECTED, async () => {
      if (!this.enabled) {
        await this.enable();
      }
    });

    // Auto-disable when WebRTC disconnects
    window.addEventListener(AppEvents.RTC_STATECHANGE, async (e) => {
      if (e.detail.state === "disconnected" || e.detail.state === "failed") {
        await this.disable();
      }
    });
  }

  async enable() {

    const ctx = window.audioRouters.mic.getContext();
    if (ctx.state === "suspended") {
      await ctx.resume();
    }

    await this.initMic();
    this.enabled = true;

    const gain = window.audioRouters.mic.getNode(GainNode);
    if (gain) {
      const now = gain.context.currentTime;
      gain.gain.cancelScheduledValues(now);
      gain.gain.linearRampToValueAtTime(1, now + 0.01);
    }

    this.startShadowLoop();
    window.dispatchEvent(new CustomEvent(AppEvents.MIC_UNMUTE));
    this.update();
  }

  async disable() {
    if (!this.micStream) return;
    this.enabled = false;

    const gain = window.audioRouters.mic.getNode(GainNode);
    if (gain) {
      const now = gain.context.currentTime;
      gain.gain.cancelScheduledValues(now);
      gain.gain.linearRampToValueAtTime(0, now + 0.01);
    }

    this.stopShadowLoop();
    window.dispatchEvent(new CustomEvent(AppEvents.MIC_MUTE));
    this.update();
  }

  async toggle() {
    this.enabled ? await this.disable() : await this.enable();
  }


  // Optional: cleanup
  disconnect() {
    this.micStream?.getTracks().forEach(t => t.stop());
    this.micStream = null;
    this.micTrack = null;
  }

  startShadowLoop() {
    if (this._raf) return;
    const loop = () => {
      this.analyser.getByteFrequencyData(this.data);
      const avg = this.data.reduce((a, b) => a + b, 0) / this.data.length / 255;
      const spread = Math.round(4 + avg * 200);          // 4px silence → 64px loud
      const opacity = (0.1 + avg * 0.9).toFixed(2);     // 0.1 silence → 1.0 loud
      this.btn.style.boxShadow = `0 0 ${spread}px rgba(248,81,73,${opacity})`;
      this._raf = requestAnimationFrame(loop);
    };
    loop();
  }

  stopShadowLoop() {
    cancelAnimationFrame(this._raf);
    this._raf = null;
    this.btn.style.boxShadow = "";
  }

  update() {
    if (!this.btn) return;

    // "active" = mic is live, "recording" = pulsing red when muted
    this.btn.classList.toggle("active", !this.enabled);      // blue glow when muted
    this.btn.classList.toggle("recording", this.enabled);  // red pulse when live
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        /* =========================
           COPY OF YOUR ORIGINAL CSS
           (ctrl-btn ONLY)
        ========================== */

        :host {
          /*display: inline-block;*/
          display: inline-flex;
          width: 100%;
          height: 100%;
        }

        button {
          width: 100%;
          height: 100%;
          border-radius: 10px;
          border: 1px solid rgba(255,255,255,0.07);
          background: #0d1117;
          color: #7d8590;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: all 0.25s ease;
          position: relative;
          overflow: hidden;
        }

        button::before {
          content: '';
          position: absolute;
          inset: 0;
          background: #63d2ff;
          opacity: 0;
          transition: opacity 0.25s ease;
        }

        button:hover {
          border-color: rgba(99,210,255,0.35);
          color: #e6edf3;
        }

        button:hover::before {
          opacity: 0.08;
        }

        button.active {
          border-color: #63d2ff;
          color: #63d2ff;
          box-shadow: 0 0 12px rgba(99,210,255,0.15), inset 0 0 12px rgba(99,210,255,0.15);
        }

        button.recording {
          border-color: #f85149;
          color: #f85149;
          /*box-shadow: 0 0 12px rgba(248,81,73,0.2);*/
          /*animation: recordPulse 1.5s ease infinite;*/
        }

        button svg {
          position: relative;
          z-index: 1;
        }

        @keyframes recordPulse {
          0%,100% { box-shadow: 0 0 12px rgba(248,81,73,0.2); }
          50% { box-shadow: 0 0 20px rgba(248,81,73,0.5); }
        }

        
      </style>

      <button title="Toggle microphone">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
          <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"/>
        </svg>
      </button>
      
    `;
  }
}

customElements.define("mic-controller", MicController);