/**
 * Default build target: the frontend is served from the same origin as the API.
 * This is true for `ng serve` (proxied to the local FastAPI server by
 * api.config.ts) and for the Docker Compose stack, where nginx proxies /v1/.
 */
export const environment = {
  apiBaseUrl: '',
};
