const BASE_URL = window.location.origin;
const WS_INIT_URL    = `${BASE_URL}/ws/v1/sessions/initialize`;

const CONSTANTS = Object.freeze({
    BASE_URL: BASE_URL,
    WS_INIT_URL: WS_INIT_URL
});

const AppEvents = Object.freeze({
    // WebSocket Events
    WS_CONNECTING: 'ws:connecting',
    WS_CONNECTED: 'ws:connected',
    WS_MESSAGE: 'ws:message',
    WS_DISCONNECTED: 'ws:disconnected',
    WS_CLOSED: 'ws:closed',
    WS_ERROR: 'ws:error',

    // WebRTC Events
    RTC_STATECHANGE: 'rtc:statechange',
    RTC_CONNECTED: 'rtc:connected',

    // Microphone Events
    MIC_READY: 'mic:ready',
    MIC_ERROR: 'mic:error',
    MIC_UNMUTE: 'mic:unmute',
    MIC_MUTE: 'mic:mute',

    // Audio Router Events
    ROUTER_REBUILT: 'router:rebuilt',

    // UI Events

    // Connection Toggle Event
    CONN_TOGGLE: 'conn:toggle',

    // Chat Events
    CHAT_MESSAGE_SENT: 'chat:message:sent', // Chat Component Emit this message and Websocket listen to send to Server
    CHAT_MESSAGE_RECEIVED: 'chat:message:received', // Websocket listen to send this message to Chat Component
    CHAT_TYPING_START: 'chat:typing:start', 
    CHAT_TYPING_STOP: 'chat:typing:stop', 
    CHAT_CLEAR: 'chat:clear' 
});

