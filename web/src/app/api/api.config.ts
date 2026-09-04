/**
 * Where the browser should send API calls.
 *
 * `ng serve` on port 4200 talks to the local FastAPI dev server. Every other
 * build reads the origin baked in at build time: empty for same-origin
 * deployments (Docker Compose, where nginx proxies /v1/), or the API's absolute
 * URL for the Render static site, which has no proxy of its own.
 */
import { environment } from '../../environments/environment';

const isLocalDev = typeof window !== 'undefined' && window.location.port === '4200';
export const apiBaseUrl = isLocalDev ? 'http://127.0.0.1:8000' : environment.apiBaseUrl;
