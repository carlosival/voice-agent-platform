// ─── Audio Visualizer ─────────────────────────────────────────────────────────
/*
function startVisualizer(stream) {
  audioContext = new (window.AudioContext || window.webkitAudioContext)();
  analyser = audioContext.createAnalyser();
  analyser.fftSize = 64;
  const src = audioContext.createMediaStreamSource(stream);
  src.connect(analyser);
  visualizer.style.display = 'flex';
  animateVisualizer();
}

function animateVisualizer() {
  const data = new Uint8Array(analyser.frequencyBinCount);
  function draw() {
    vizAnimId = requestAnimationFrame(draw);
    analyser.getByteFrequencyData(data);
    vizBars.forEach((bar, i) => {
      const val = data[i] || 0;
      bar.style.height = Math.max(4, (val / 255) * 28) + 'px';
    });
  }
  draw();
}

function stopVisualizer() {
  if (vizAnimId) cancelAnimationFrame(vizAnimId);
  if (audioContext) { audioContext.close(); audioContext = null; }
  vizBars.forEach(b => b.style.height = '4px');
  visualizer.style.display = 'none';
}*/

/** use <audio-viz source="remote"></audio-viz> or <audio-viz source="mic"></audio-viz> */

class AudioViz extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });

  }

  async connectedCallback() {

    this.sourceType = this.getAttribute("source") || "remote";
    this.router = window.audioRouters?.[this.sourceType];
    console.log("Visualizer source:", this.sourceType, "Router:", this.router);
    if (!this.router) return;

    // Move to the constructor and bind when router is ready
    this.ctx = this.router.getContext();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 64;
    this.data = new Uint8Array(this.analyser.frequencyBinCount);

    this.render();

    this._onUnmute = () => this.updateState(true);
    this._onMute = () => this.updateState(false);

    // 3. Logic: If remote, it's always "unmuted". If mic, wait for events.
    if (this.sourceType === "remote") {
      this.updateState(true);
    } else {
      window.addEventListener(AppEvents.MIC_UNMUTE, this._onUnmute);
      window.addEventListener(AppEvents.MIC_MUTE, this._onMute);
    }

    // 2. Listen for audio router rebuilds (so we know a stream is ready)
    window.addEventListener(AppEvents.ROUTER_REBUILT, (e) => {
      const { router, type } = e.detail;
      // Only care about the router matching our type 
      if (type === this.sourceType) {
        this.bindToRouter(router);
      }
    });

    // Check if router already exists and has a stream
    const existing = window.audioRouters?.[this.getAttribute("source")];
    if (existing?.hasStream()) this.bindToRouter(existing);

  }

  bindToRouter(router) {

    this.router = router;
    this.router.connectTap(this.analyser);

    if (this.hasAttribute("active") || this.sourceType === "remote") {
      this.start();
    }
  }


  disconnectedCallback() {
    this.stop();
    if (this.sourceType === "mic") {
      window.removeEventListener(AppEvents.MIC_UNMUTE, this._onUnmute);
      window.removeEventListener(AppEvents.MIC_MUTE, this._onMute);
    }
  }

  getData() {
    this.analyser.getByteFrequencyData(this.data);
    // If this stays 0 while you hear sound, the tap isn't connected.
    //if (this.data[0] > 0) console.log("Viz data flowing!"); // for debugging
    return this.data;
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: none; }
        :host([active]) {
          display: inline-flex;
          gap: var(--viz-gap, 2px);
          align-items: var(--viz-align, flex-end);
          width: 100%;
          height: 100%;
        }
        .bar {
          width: var(--viz-bar-width, 3px);
          min-height: 2px;
          height: 4px;
          flex-shrink: 0;                /* ← don't squeeze bars */
          background: var(--viz-color, ${this.sourceType === "mic" ? "#f85149" : "#63d2ff"});
          border-radius: var(--viz-radius, 1px);
        }
      </style>
    `;

    this.bars = [];
    for (let i = 0; i < 32; i++) {
      const bar = document.createElement("div");
      bar.className = "bar";
      this.shadowRoot.appendChild(bar);
      this.bars.push(bar);
    }
  }

  start() {
    // If we are already running, don't start a second loop!
    if (this.raf) return;

    const draw = () => {
      const data = this.getData();

      this.bars.forEach((bar, i) => {
        const v = data[i] || 0;
        bar.style.height = `${Math.max(4, (v / 255) * 38)}px`;
      });

      this.raf = requestAnimationFrame(draw);
    };

    draw();
  }

  stop() {
    if (this.raf) {
      cancelAnimationFrame(this.raf);
      this.raf = null;
    }
    this.bars?.forEach(b => b.style.height = "4px");
  }

  updateState(shouldBeActive) {
    if (shouldBeActive) {
      this.setAttribute("active", "");
      this.start();
    } else {
      this.removeAttribute("active");
      this.stop();
    }
  }

}



customElements.define("audio-viz", AudioViz);