
class AudioRouter {

  constructor(ctx) {
    this.ctx = ctx;
    this.source = null;
    this.nodes = [];
    this.nodeDest = null;
    this.taps = new Set(); // Store visualizers or Other kind of taps here

  }

  setNodes(nodes) {
    this.nodes = nodes;
    this.rebuild();
    return this;
  }

  getDestination() {
    return this.nodeDest;
  }

  getNode(nodeInstance) {
    return this.nodes.find(n => n instanceof nodeInstance) ?? null;
  }


  hasStream() {
    return this.source !== null;
  }

  attachStream(stream) {
    if (this.source) this.source.disconnect();

    this.source = this.ctx.createMediaStreamSource(stream);
    this.rebuild();
    return this;
  }

  // Use this for WebRTC tracks when you use an <audio> element hack
  attachElementSource(el) {

    // 1. Check if this element already has a source node attached to it
    // We store it as a custom property '_sourceNode' on the HTML element
    if (el._sourceNode) {
      this.source = el._sourceNode;
    } else {
      // 2. Only create it IF it doesn't exist
      this.source = this.ctx.createMediaElementSource(el);
      el._sourceNode = this.source; // Cache it for next time
    }

    this.rebuild();
    return this;
  }

  rebuild() {
    if (!this.source) return;

    // Disconnect everything first
    this.nodes.forEach(n => { try { n.disconnect(); } catch { } });
    this.taps.forEach(n => { try { n.disconnect(); } catch { } });

    let current = this.source;

    // Connect main chain
    for (const node of this.nodes) {
      current.connect(node);
      current = node;
    }

    // Connect to destination (speakers)
    if (this.nodeDest) {
      current.connect(this.nodeDest); // last node → speakers
    }

    // Connect taps to the end of the chain (or the source if no nodes)
    this.taps.forEach(tapNode => {
      current.connect(tapNode);
    });

    // Find which key this router belongs to (mic or remote)
    const type = Object.keys(window.audioRouters).find(key => window.audioRouters[key] === this);

    // Emit a custom event so visualizers know they can connect
    window.dispatchEvent(new CustomEvent(AppEvents.ROUTER_REBUILT, {
      detail: { router: this, type: type }
    }));

  }

  connectToDestination(nodeDest) {
    if (this.nodeDest) this.nodeDest.disconnect(); // Prevent audio leaks
    this.nodeDest = nodeDest;
    return this;
  }

  connectTap(node) {
    if (this.taps.has(node)) return; // Prevent double adding
    this.taps.add(node);

    // If we have a source, connect the tap immediately 
    // without triggering a full system rebuild.
    if (this.source) {
      const lastNode = this.nodes.length > 0
        ? this.nodes[this.nodes.length - 1]
        : this.source;

      try {
        lastNode.connect(node);
      } catch (e) {
        console.warn("Failed to connect tap:", e);
      }
    }
  }

  disconnectTap(node) {
    this.taps.delete(node);
    try { node.disconnect(); } catch { }
  }

  getContext() {
    return this.ctx;
  }
}

// GLOBAL INSTANCES
const sharedCtx = new (window.AudioContext || window.webkitAudioContext)();

//Warmp the mic router
/**
 * 🛠️ FIX: Create a proper MediaStream from an Oscillator 
 * This fulfills the requirement for createMediaStreamSource(stream)
 */
const createWarmupStream = (ctx) => {
  const dest = ctx.createMediaStreamDestination();
  const osc = ctx.createOscillator();
  const silence = ctx.createGain();

  silence.gain.value = 0.0; // Ensure it's truly silent

  osc.connect(silence);
  silence.connect(dest);
  osc.start();

  return dest.stream; // This is a real MediaStream object
};
destNode = sharedCtx.createMediaStreamDestination();

window.audioRouters = {
  mic: new AudioRouter(sharedCtx).setNodes([new GainNode(sharedCtx, { gain: 0.0 })]).connectToDestination(destNode),
  remote: new AudioRouter(sharedCtx).connectToDestination(sharedCtx.destination)
};

window.audioRouters.mic.attachStream(createWarmupStream(sharedCtx));


// 🔓 Unlock audio ONCE (global)
["click", "touchstart"].forEach(event => {
  document.addEventListener(event, async () => {
    if (sharedCtx.state !== "running") {
      await sharedCtx.resume();
      console.log("🔊 AudioContext unlocked");
    }
  }, { once: true });
});
