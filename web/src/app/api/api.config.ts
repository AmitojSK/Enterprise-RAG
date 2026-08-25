/**
 * In Docker, nginx proxies /v1/ to the API so no origin is needed.
 * For local `ng serve`, fall back to the FastAPI dev server.
 */
const isLocalDev = typeof window !== 'undefined' && window.location.port === '4200';
export const apiBaseUrl = isLocalDev ? 'http://127.0.0.1:8000' : '';
