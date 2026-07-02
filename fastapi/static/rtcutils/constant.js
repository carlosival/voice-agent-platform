const env = 'dev';
const BASE_URL = env === 'prod' ? 'https://cadoflow.xyz' : window.location.origin;
const INIT_URL = `${BASE_URL}/v1/api/get_token`;
const GET_ICE_SERVERS = `${BASE_URL}/v1/api/get_ice_servers`;

const CONSTANTS = Object.freeze({
    BASE_URL: BASE_URL,
    INIT_URL: INIT_URL,
    GET_ICE_SERVERS: GET_ICE_SERVERS
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
    RTC_DISCONNECTED: 'rtc:disconnected',
    RTC_FAILED: 'rtc:failed',
    RTC_CLOSED: 'rtc:closed',
    RTC_ERROR: 'rtc:error',
    RTC_OFFER_SENT: 'rtc:offer:sent',
    RTC_ANSWER_RECEIVED: 'rtc:answer:received',
    RTC_CANDIDATE_SENT: 'rtc:candidate:sent',
    RTC_CANDIDATE_RECEIVED: 'rtc:candidate:received',

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

