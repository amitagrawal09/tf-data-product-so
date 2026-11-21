// Console runtime configuration. deploy.sh regenerates this file for Azure environments.
// auth.mode: "dev"   → sign-in page with a persona picker; the persona is sent as x-dev-persona (non-prod only)
//            "entra" → Microsoft Entra ID single sign-on (authorization code + PKCE); token sent as Authorization: Bearer
window.TFSO_CONFIG = {
  apiBase: "/api",
  subscriptionKey: "",
  apiKey: "",
  environment: "LOCAL",
  auth: {
    mode: "dev",
    tenantId: "",
    clientId: "",
    authority: "https://login.microsoftonline.com/<tenantId>",
    externalAuthority: "", // optional Entra External ID authority for customers / vendors
    scopes: ["api://<api-client-id>/access_as_user", "openid", "profile", "offline_access"],
    redirectUri: location.origin + location.pathname,
    postLogoutRedirectUri: location.origin + location.pathname
  }
};
