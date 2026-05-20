class WebRTCController {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.ws = null;
    this.pc = null;
    this.audioSender = null;
    this.videoSender = null;
    this.pendingMicTrack = null; // Mejor habilitar el microfono despues de la conexion
    this.remoteCandidatesQueue = [];
  }

  // ─── CONNECT ─────────────────────────────────────────────

  async connect() {

    // 1. Clean up existing connection if it exists
    if (this.ws) {
      console.log("Closing existing connection...");

      // Remove listeners to prevent them from firing during the close process
      this.ws.onopen = null;
      this.ws.onmessage = null;
      this.ws.onerror = null;
      this.ws.onclose = null;

      this.ws.close();
    }


    this.ws = new WebSocket(this.wsUrl);

    this.ws.onopen = async () => {
      await this.setupPeerConnection();
      await this.createAndSendOffer();
      this.setupEventListeners();
      window.dispatchEvent(new CustomEvent(AppEvents.WS_CONNECTED));
    };

    this.ws.onmessage = async (e) => {
      console.log("Received message from WS:", e.data);
      const msg = JSON.parse(e.data);
      if (msg.type === "offer" || msg.type === "answer" || msg.type === "candidate") {
        await this.handleSignal(msg);
      } else {
        this.handleChatMessage(msg);
      }
      /*
      how websocket is use for diff types of messages 
      handler each type according to the type of message
      */
      window.dispatchEvent(new CustomEvent(AppEvents.WS_MESSAGE, { detail: { msg } }));
    };

    this.ws.onclose = () => {
      this.disconnect();
      window.dispatchEvent(new CustomEvent(AppEvents.WS_CLOSED));
      
    };

  }


  disconnect() {
    if (this.ws) {
      this.ws.onopen = null;
      this.ws.onmessage = null;
      this.ws.onerror = null;
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    if (this.pc) {
      // Remove listeners to prevent them from firing during the close process
      this.pc.ontrack = null;
      this.pc.onicecandidate = null;
      this.pc.onconnectionstatechange = null;
      this.pc.close();

     // 3. MANUALLY trigger the event for your UI components
        console.log("WEBRTC state: locally closed");
        window.dispatchEvent(new CustomEvent(AppEvents.RTC_STATECHANGE, {
            detail: { state: 'closed' }
        })); 

      this.pc = null;
    }
    this.audioSender = null;
    this.videoSender = null;
  }



  // ─── SETUP PC ────────────────────────────────────────────

  async setupPeerConnection() {
    this.pc = new RTCPeerConnection({
      iceServers: [
        { urls: "stun:stun.cloudflare.com:3478" },
        { urls: "stun:stun.l.google.com:19302" }
      ]
    });

    // Get the persistent track from the router
    const warmTrack = window.audioRouters.mic.getDestination().stream.getAudioTracks()[0];

    // Add the transceiver with the WARM track
    const audioTx = this.pc.addTransceiver(warmTrack, {
      direction: "sendrecv"
    });

    this.audioSender = audioTx.sender;

    // 📹 VIDEO transceiver 
    const videoTx = this.pc.addTransceiver("video", {
      direction: "sendonly"
    });

    this.videoSender = videoTx.sender;

    // ─── RECEIVE TRACKS ─────────────────────────

    this.pc.ontrack = (e) => {
      if (e.track.kind === "audio") {
        console.log("🔊 received audio track");
        // 1. Create the stream once
        const remoteStream = new MediaStream([e.track]);

        // 2. Attach to the Router first
        window.audioRouters.remote.attachStream(remoteStream);

        // 3. Then use the sink ONLY to kickstart the decoder
        let sink = document.getElementById("remote-sink") || new Audio();
        sink.id = "remote-sink";
        sink.muted = true;
        sink.srcObject = remoteStream;
        sink.play();

        console.log("📡 Decoder started via sink, stream routed to WebAudio");
      }
    };

    // ─── ICE ─────────────────────────

    this.pc.onicecandidate = (e) => {
      if (e.candidate) {
        this.ws.send(JSON.stringify({
          type: "candidate",
          candidate: e.candidate
        }));
      }
    };

    this.pc.onconnectionstatechange = () => {
      console.log("WEBRTC state:", this.pc.connectionState);

      window.dispatchEvent(new CustomEvent(AppEvents.RTC_STATECHANGE, {
        detail: { state: this.pc.connectionState }
      }));


      if (this.pc.connectionState === "connected") {
        // Auto-enable mic when connection is established
        window.dispatchEvent(new CustomEvent(AppEvents.RTC_CONNECTED));
      }
      // Don't try to reconnect on failure, the user will handle it button
      if (this.pc.connectionState === "failed") {
        this.disconnect();
        //setTimeout(() => this.connect(), 2000);
      };

    };

    if (this.pendingMicTrack) {
      await this.setMicTrack(this.pendingMicTrack);
      this.pendingMicTrack = null;
    }
  }

  // ─── SIGNALING ───────────────────────────────────────────

  async createAndSendOffer() {
    const offer = await this.pc.createOffer({
      // FORCE the browser to act as a listener in the SDP
      offerToReceiveAudio: true,
      offerToReceiveVideo: false
    });

    await this.pc.setLocalDescription(offer);

    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(this.pc.localDescription));
    }
  }

  handleChatMessage(msg) {
    try {
      console.log("Received chat message from WS:", msg);
        window.dispatchEvent(new CustomEvent(AppEvents.CHAT_MESSAGE_RECEIVED, {
          detail: {
            id: msg.id,
            chunk: msg.chunk,
            done: msg.done
          },
          bubbles: true,
          composed: true
        }));
    } catch (error) {
      console.error("Error handling UI message:", error);
    }
  }

  async handleSignal(msg) {
    try {
      if (msg.type === "answer" || msg.type === "offer") {
        await this.pc.setRemoteDescription(new RTCSessionDescription(msg));

        // Now that the description is set, process any queued candidates
        while (this.remoteCandidatesQueue?.length > 0) {
          const candidate = this.remoteCandidatesQueue.shift();
          await this.pc.addIceCandidate(candidate);
        }

        if (msg.type === "offer") {
          const answer = await this.pc.createAnswer();
          await this.pc.setLocalDescription(answer);

          // Check if the SDP contract is correct
          console.log(this.pc.localDescription.sdp);

          if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(answer));
          }
        }
        return;
      }

      if (msg.type === "candidate") {
        const candidate = new RTCIceCandidate(msg.candidate);
        // Only add if we have a remote description, otherwise queue it
        if (this.pc.remoteDescription && this.pc.remoteDescription.type) {
          await this.pc.addIceCandidate(candidate);
        } else {
          this.remoteCandidatesQueue.push(candidate);
        }
        return;
      }

    } catch (error) {
      console.error("Error handling signal:", error);
    }
  }

  // ─── CHAT ───────────────────────────────────────────

  // Listen for events to need to send messages to the server
  setupEventListeners() {
    window.addEventListener(AppEvents.CHAT_MESSAGE_SENT, (e) => {
      console.log("Sending chat message:", e.detail.message);
      this.ws.send(JSON.stringify({type: "text", content: e.detail.message}));
    });
  }

  

}