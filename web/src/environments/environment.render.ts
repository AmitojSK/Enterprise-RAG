/**
 * Build target for the Render static site. A static site has no reverse proxy
 * in front of it, so the browser calls the API's own origin directly and the
 * API must list this site's URL in ALLOWED_ORIGINS.
 *
 * Replace the value below with the API web service's real URL after creating
 * it on Render, then commit — this is a public URL, not a secret.
 */
export const environment = {
  apiBaseUrl: 'https://enterprise-rag-zkot.onrender.com',
};
