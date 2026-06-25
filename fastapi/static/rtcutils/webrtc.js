class WebRTCController {
  constructor() {
    this.wsUrl = wsUrl;
    this.ws = null;
    this.pc = null;
    this.audioSender = null;
    this.videoSender = null;
    this.remoteCandidatesQueue = [];


    // Bind all handlers once — `this` is always the instance
    this.handleRemoteTrack = this.handleRemoteTrack.bind(this);
    this.handleIceCandidateTricker = this.handleIceCandidateTricker.bind(this);
    this.handleIceCandidateONCE = this.handleIceCandidateONCE.bind(this);
    this.handleConnectionStateChange = this.handleConnectionStateChange.bind(this);
    this.handleChatMessage = this.handleChatMessage.bind(this);
    this.handleICEWebsocket = this.handleICEWebsocket.bind(this);


  }


  async getWSUrl(init_URL = CONSTANTS.INIT_URL) {

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
        localStorage.setItem("token", data.token);
        localStorage.setItem("connection_url", data.connection_url);
        return {url: data.connection_url, token: data.token};
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
      
      const { url, token }  = await this.getWSUrl(CONSTANTS.INIT_URL);

      this.wsUrl = url
      
      console.log("WebSocket URL:", this.wsUrl);
      
  
      // 1. Fetch from your server endpoint
    const ice_servers_response = await fetch(CONSTANTS.GET_ICE_SERVERS, {
      method: "GET",
      headers: {
        "Authorization": `Bearer ${token}`
      }
    });
    const ice_servers = await ice_servers_response.json();
       
    console.log("WebRTC Peer Connection configured with Cloudflare TURN!");

    const peerConfig = {
      iceServers: ice_servers.iceServers,
      handleRemoteTrack: this.handleRemoteTrack,
      handleIceCandidate: this.handleIceCandidateONCE,
      handleConnectionStateChange: this.handleConnectionStateChange

    };

     // ── Create WebSocket ──────────────────────────────────────────────
    this.ws = new WebSocket(this.wsUrl);

      this.ws.onopen = async () => {

        console.log('[WS] onopen fired');
        try {
          this.setupPeerConnection(peerConfig);
          console.log('[PC] created:', this.pc);

          // 1. Kickstart the ICE gathering process by creating the local description
          await this.createOffer(); 
          console.log('[SDP] Offer initialized. Gathering Vanilla ICE candidates...');

          this.setupEventListeners();
          window.dispatchEvent(new CustomEvent(AppEvents.WS_CONNECTED));
        } catch(err) {
          // This will show the real error
          console.error('[onopen] CRASHED — this is why the offer never sends:', err);
        }
    };

    this.ws.onmessage = async (e) => {
        const msg = JSON.parse(e.data);
        const label = {
          offer:     '📥 [WS RECV] Offer',
          answer:    '📥 [WS RECV] Answer',
          candidate: '📥 [WS RECV] ICE Candidate',
        }[msg.type] ?? `📥 [WS RECV] ${msg.type}`;

        if (msg.type === 'candidate') {
          const c = msg.candidate?.candidate;
          console.log(`%c${label}`, 'color: #8bc34a',
            c === '' ? '(end-of-candidates sentinel)' : c?.slice(0, 80)
          );
        } else {
          console.log(`%c${label}`, 'color: #8bc34a; font-weight: bold', msg);
        }
          if (msg.type === "offer" || msg.type === "answer" || msg.type === "candidate") {
            await this.handleSignal(msg);
          } 
          window.dispatchEvent(new CustomEvent(AppEvents.WS_MESSAGE, { detail: { msg } }));
    };

    this.ws.onclose = (e) => {
      console.log('%c[WS] Closed', 'color: #f44336; font-weight: bold', {
        code: e.code,
        reason: e.reason,
        wasClean: e.wasClean,
        timestamp: new Date().toISOString()
      });
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
    const connection_url = await this.getWSUrl(CONSTANTS.INIT_URL)
    const token = localStorage.getItem("token");
    const session_id = localStorage.getItem("session_id");

    // 1. Fetch from your server endpoint
    const ice_servers_response = await fetch(CONSTANTS.GET_ICE_SERVERS, {
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
      handleRemoteTrack: this.handleRemoteTrack,
      handleIceCandidate: this.handleIceCandidateONCE,
      handleConnectionStateChange: this.handleConnectionStateChange

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
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;

        if (!e.candidate) {
          // Send end-of-candidates sentinel — worker drain_client_ice needs this
          this.ws.send(JSON.stringify({
            type: "candidate",
            candidate: { candidate: "", sdpMid: "", sdpMLineIndex: 0 }
          }));
          return;
        }

        console.log("ICE Candidate:", e.candidate);
        this.ws.send(JSON.stringify({
          type: "candidate",
          candidate: e.candidate
        }));
  }

  async handleIceCandidateONCE(e){
    
        // The complete ice gathering is done when the candidate is null
        // Entire ICE gathering is complete! 
        // e.candidate === null means ICE gathering is 100% complete
        if (e.candidate === null) {
          console.log('%c[Vanilla ICE] Gathering complete. Sending bundled SDP Offer...', 'color: #4caf50; font-weight: bold');
          
          if (this.ws?.readyState === WebSocket.OPEN) {
            // this.pc.localDescription now includes all the gathered ice candidates within the SDP text block
            this.ws.send(JSON.stringify(this.pc.localDescription));
          } else {
            console.error('[SDP] WebSocket closed. Unable to send Vanilla ICE Offer.');
          }
        }
  }

  async handleConnectionStateChange(e){
    {
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

    }
  }


  // ─── SETUP PeerConnection ────────────────────────────────────────────

  async setupPeerConnection(config) {
    
      // Guard — don't double-init
  if (this.pc) {
    console.warn("PeerConnection already exists — call disconnect() first");
    return;
  }

  // 1. Create pc FIRST
  this.pc = new RTCPeerConnection({
    iceServers: config.iceServers || [
      { urls: "stun:stun.cloudflare.com:3478" },
      { urls: "stun:stun.l.google.com:19302" }
    ]
  });

  // 2. Transceivers — pc now guaranteed to exist
  this.setupTransceivers();

  // 3. Handlers — bound in constructor so this is always correct
  this.pc.onicecandidate = (e) => {
    console.log('%c[ICE] Local candidate', 'color: #ff9800',
      e.candidate ? e.candidate.candidate.slice(0, 80) : '(end-of-candidates)'
    );
    if (config.handleIceCandidate){
      config.handleIceCandidate(e);
    }
  };

  this.pc.onicegatheringstate = () => {
    console.log('%c[ICE] Gathering state', 'color: #ff9800', this.pc.iceGatheringState);
  };

  this.pc.oniceconnectionstatechange = () => {
    console.log('%c[ICE] Connection state', 'color: #e91e63', this.pc.iceConnectionState);
  };

  this.pc.onconnectionstatechange = () => {
    console.log('%c[PC] Connection state', 'color: #9c27b0; font-weight: bold', this.pc.connectionState);
    config.handleConnectionStateChange();
  };

  this.pc.onsignalingstatechange = () => {
    console.log('%c[PC] Signaling state', 'color: #607d8b', this.pc.signalingState);
  };

  this.pc.ontrack = (e) => {
    console.log('%c[PC] Remote track received', 'color: #4caf50', {
      kind: e.track.kind,
      id: e.track.id,
      readyState: e.track.readyState
    });
    config.handleRemoteTrack(e);
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
   console.log('%c[SDP] Local description set. Ice gathering state:', 'color: #ff9800', this.pc.iceGatheringState);
  
  }

  async sendOfferWebSocket(){
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(this.pc.localDescription));
    }
  }

  async sendOfferAndSetAnswerHTTP(){
    const token = localStorage.getItem("token") || null;
    const response = await fetch(AppConfig.API_BASE_URL + "/api/offer", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify(this.pc.localDescription)
    });
    const data = await response.json();
    // data is the answer from the server, Accept it
    await this.pc.setRemoteDescription(data);
  }


  async createAndSendOfferWebSocket() {
      console.log('%c[SDP] Creating offer...', 'color: #ff9800');

      const offer = await this.pc.createOffer({
        offerToReceiveAudio: true,
        offerToReceiveVideo: false
      });

      console.log('%c[SDP] Offer created', 'color: #ff9800', {
        type: offer.type,
        sdpLength: offer.sdp.length,
        audioLines: offer.sdp.match(/m=audio/g)?.length ?? 0,
        videoLines: offer.sdp.match(/m=video/g)?.length ?? 0,
      });

      await this.pc.setLocalDescription(offer);
      console.log('%c[SDP] Local description set', 'color: #ff9800');

      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify(this.pc.localDescription));
      } else {
        console.error('[SDP] WebSocket not open — offer not sent', this.ws?.readyState);
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
  /*
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
*/
  async handleSignal(msg) {
    try {
      if (msg.type === "answer" || msg.type === "offer") {
        console.log(`%c[SDP] Setting remote description (${msg.type})`, 'color: #3f51b5', {
          sdpLength: msg.sdp?.length,
          signalingState: this.pc.signalingState
        });
        await this.pc.setRemoteDescription(new RTCSessionDescription(msg));
        console.log('%c[SDP] Remote description set ✓', 'color: #3f51b5');

        // Now that the description is set, process any queued candidates
        while (this.remoteCandidatesQueue?.length > 0) {
          console.log(`%c[ICE] Flushing ${this.remoteCandidatesQueue.length} queued candidates`, 'color: #ff9800');
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
          console.log('%c[ICE] Adding remote candidate', 'color: #009688',
            msg.candidate?.candidate?.slice(0, 80)
          );
          await this.pc.addIceCandidate(candidate);
        } else {
          console.warn('%c[ICE] Remote description not set — queuing candidate', 'color: #ff5722');
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