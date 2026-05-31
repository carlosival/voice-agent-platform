class WebRTCController {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.ws = null;
    this.pc = null;
    this.audioSender = null;
    this.videoSender = null;
    this.remoteCandidatesQueue = [];
  }


  async getWSUrl(init_URL = CONSTANTS.WS_INIT_URL) {

        const response = await fetch(init_URL, {
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
        localStorage.setItem("session_id", data.session_id);
        localStorage.setItem("token", data.token);
        localStorage.setItem("connection_url", data.connection_url);
        return data.connection_url;
  }

  // ─── CONNECT Through WebSocket ─────────────────────────────────────────────

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

    try {
      this.wsUrl = await this.getWSUrl();
      console.log("WebSocket URL:", this.wsUrl);
      // 2. Create new WebSocket connection
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
      } 
      window.dispatchEvent(new CustomEvent(AppEvents.WS_MESSAGE, { detail: { msg } }));
    };

    this.ws.onclose = () => {
      if (this.ws) {
      this.ws.onopen = null;
      this.ws.onmessage = null;
      this.ws.onerror = null;
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
      window.dispatchEvent(new CustomEvent(AppEvents.WS_CLOSED));
      
    };

    this.ws.onerror = (err) => {
      console.error("WebSocket error:", err);
    };
    } catch (error) {
      console.error("Failed to get WS URL:", error);
      return;
    }

  }


  async connectHTTP(){
    
    // Get Token and URL
    const connection_url = await this.getWSUrl(AppConfig.HTTPS_INIT_URL)
    const token = localStorage.getItem("token");
    const session_id = localStorage.getItem("session_id");

    // 1. Fetch from your FastAPI endpoint running on your Tailscale node
    const ice_servers_response = await fetch(AppConfig.GET_ICE_SERVERS, {
      method: "GET",
      headers: {
        "Authorization": `Bearer ${token}`
      }
    });
    const ice_servers = await ice_servers_response.json();
    
    // config contains exactly: { iceServers: [...] }
    
    // 2. Instantiate PeerConnection directly with the response object
    // const pc = new RTCPeerConnection(ice_servers);
    
    console.log("WebRTC Peer Connection configured with Cloudflare TURN!");

    const peerConfig = {
      iceServers: ice_servers,
      transceivers: this.setupTransceivers(),
      handleRemoteTrack: this.handleRemoteTrack,
      handleIceCandidate: this.handleIceCandidateOnce
      
      

    };
    
    // Continue with your standard offer/answer logic...
    this.setupPeerConnection(peerConfig);
    
  }

  disconnect() {
    
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

  setupIceServers(ice_servers){
    this.pc = new RTCPeerConnection({
      iceServers: ice_servers || [
        { urls: "stun:stun.cloudflare.com:3478" },
        { urls: "stun:stun.l.google.com:19302" }
      ]
    });
  }

  setupTransceivers(){

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
  }
    
  handleRemoteTrack(e){
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
  }

  handleIceCandidateTricker(e){
    if (e.candidate) {
      console.log("ICE Candidate:", e.candidate);
      this.ws.send(JSON.stringify({
        type: "candidate",
        candidate: e.candidate
      }));
    }
  }

  handleIceCandidateONCE(e){
    if (e.candidate === null) {
        // Entire ICE gathering is complete! NOW send the payload to FastAPI
        const finalOffer = this.pc.localDescription;
        this.sendOfferHTTP(finalOffer);
    }
  }

  setupOnTrack(handler){
    this.pc.ontrack = handler;
  }

  setupOnIceCandidate(handler){
    this.pc.onicecandidate = handler;
  }

  setupOnConnectionStateChange(handler){
    this.pc.onconnectionstatechange = handler;
  }

  // ─── SETUP PeerConnection ────────────────────────────────────────────

  async setupPeerConnection(config) {
    
    this.setupIceServers(config.iceServers);

    this.setupTransceivers(config.transceivers);

    this.setupOnTrack(handler = config.handleRemoteTrack);

    this.setupOnIceCandidate(handler = config.handleIceCandidate);

    this.setupOnConnectionStateChange(handler = config.handleConnectionStateChange);

    // Get the persistent track from the router
    //const warmTrack = window.audioRouters.mic.getDestination().stream.getAudioTracks()[0];

    // Add the transceiver with the WARM track
    //const audioTx = this.pc.addTransceiver(warmTrack, {
    //  direction: "sendrecv"
    //});

    //this.audioSender = audioTx.sender;

    // 📹 VIDEO transceiver 
    //const videoTx = this.pc.addTransceiver("video", {
    //  direction: "sendonly"
    //});

    //this.videoSender = videoTx.sender;

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
      // Check if the WebSocket is actually open before trying to send
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({
        type: "candidate",
        candidate: e.candidate
      }));
    } else {
      console.log("⏳ ICE candidate gathered, but signaling WebSocket is already closed. Skipping trickle.");
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
      if (this.pc.connectionState === "failed" || this.pc.connectionState === "closed") {
        this.disconnect();
      };

    };

  }

  // ─── SIGNALING ───────────────────────────────────────────
 
  async createOffer(){
    const offer = await this.pc.createOffer({
      // FORCE the browser to act as a listener in the SDP
      offerToReceiveAudio: true,
      offerToReceiveVideo: false
    });

    await this.pc.setLocalDescription(offer);
  
  }

  async sendOfferWebSocket(){
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(this.pc.localDescription));
    }
  }

  async sendOfferHTTP(offer, token = null){
    const response = await fetch(AppConfig.API_BASE_URL + "/api/offer", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token ||localStorage.getItem("token")}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify(this.pc.localDescription)
    });
    const data = await response.json();
    // data is the answer from the server, Accept it
    await this.pc.setRemoteDescription(data);
  }


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


  closeWebSocket() {
  if (this.ws) {
    console.log("🔌 WebRTC connected! Client is closing the signaling WebSocket safely.");
    
    // Nullify listeners first so the onclose handler doesn't trigger 
    // accidental UI disconnection states
    this.ws.onopen = null;
    this.ws.onmessage = null;
    this.ws.onerror = null;
    this.ws.onclose = null;
    
    // Close with a standard normal closure code
    this.ws.close(1000); 
    this.ws = null;
    
    window.dispatchEvent(new CustomEvent(AppEvents.WS_CLOSED));
  }
}

  // This not belong here, Is not part fo the process of setup WebRtC peer connection
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

  handleICEWebsocket(e) {
    // Handle ICE through WebSocket and Trickle ICE
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({
        type: "candidate",
        candidate: e.candidate
      }));
    } else {
      console.log("⏳ ICE candidate gathered, but signaling WebSocket is already closed. Skipping trickle.");
    }
  }

  handleICEAllOnce(e) {
    // Handle ICE through WebSocket and Trickle ICE
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({
        type: "candidate",
        candidate: e.candidate
      }));
    } else {
      console.log("⏳ ICE candidate gathered, but signaling WebSocket is already closed. Skipping trickle.");
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