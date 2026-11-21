#!/usr/bin/env bash
# =====================================================================================================
#  Taylor Farms Enterprise Data Mart - Microsoft Entra ID single sign-on setup (idempotent)
#
#  Creates or updates:
#    * "Taylor Farms Data Mart API"      - api://<appId>, scope access_as_user, the app roles of
#                                          docs/07 §3, v2 tokens, optional claims, assignment required
#    * "Taylor Farms Data Mart Console"  - single-page app (auth code + PKCE, no secret) that calls the API
#    * optional security groups DM-<Role> assigned to the app roles
#    * optional Entra External ID (customers / vendors) registration in a second tenant
#  and writes .deploy/<env>.entra.env, which deploy.sh picks up (docs/08-sso-and-access-control.md).
#
#  Run in Azure Cloud Shell (bash) or anywhere with az CLI >= 2.50 and python3, signed in as a user who
#  can create app registrations and grant admin consent (Cloud Application Administrator + Privileged
#  Role Administrator, or Global Administrator).
#
#  Usage:
#    scripts/entra-setup.sh --env dev --console-url https://<storage>.z13.web.core.windows.net/
#    scripts/entra-setup.sh --env prod --console-url https://console.example.com/ --create-groups
#    scripts/entra-setup.sh --env prod --external-tenant <external-tenant-id>   # customers / vendors
# =====================================================================================================
set -euo pipefail

ENVIRONMENT="dev"
CONSOLE_URLS=()
CREATE_GROUPS=false
EXTERNAL_TENANT=""
API_NAME="Taylor Farms Data Mart API"
SPA_NAME="Taylor Farms Data Mart Console"
GRANT_CONSENT=true
GRAPH="https://graph.microsoft.com/v1.0"
GRAPH_APP_ID="00000003-0000-0000-c000-000000000000"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$ROOT/.deploy"

# Fixed namespace so role/scope ids stay identical on every run and in every tenant.
ID_NAMESPACE="6f1c3b8e-2d47-4c1a-9e55-7a0b6c1d2e3f"

# value|display name|description - docs/07 §3 (binding)
ROLES=(
  "DataMart.Admin|Data Mart administrator|Everything, including the Access and roles admin page"
  "DataMart.Steward|Data steward|Approve or reject access requests; see all domains' metadata"
  "DataMart.Export|Data export|Export datasets; without it users can preview only"
  "PII.Read|Personal data reader|See personal data unmasked"
  "Orders.Read|Order status reader|Use the Order Status API and the Monitor pages"
  "Orders.Operate|Order operator|Create, correct, hold, cancel and resubmit orders; run the simulator"
  "Domain.SalesOrder.Read|Sales Order domain reader|Read datasets in the Sales Order domain"
  "Domain.Customer.Read|Customer domain reader|Read datasets in the Customer domain"
  "Domain.Product.Read|Product domain reader|Read datasets in the Product domain"
  "Domain.Logistics.Read|Logistics domain reader|Read datasets in the Logistics domain"
  "Domain.Vendor.Read|Vendor domain reader|Read datasets in the Vendor domain"
  "Domain.Procurement.Read|Procurement domain reader|Read datasets in the Procurement domain"
  "Domain.Finance.Read|Finance domain reader|Read datasets in the Finance domain"
  "External.Customer|External customer|External customer; rows restricted to the customer ids in the token"
  "External.Vendor|External vendor|External vendor; rows restricted to the vendor ids in the token"
)

c_bold=$'\033[1m'; c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_red=$'\033[31m'; c_off=$'\033[0m'
[[ -t 1 ]] || { c_bold=""; c_green=""; c_yellow=""; c_red=""; c_off=""; }
step() { printf '\n%s==> %s%s\n' "$c_bold" "$*" "$c_off"; }
ok()   { printf '  %s✓%s %s\n' "$c_green" "$c_off" "$*"; }
warn() { printf '  %s!%s %s\n' "$c_yellow" "$c_off" "$*"; }
die()  { printf '%sERROR:%s %s\n' "$c_red" "$c_off" "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: scripts/entra-setup.sh [options]

  --env <name>               dev | test | prod (default: dev); output goes to .deploy/<env>.entra.env
  --console-url <url>        console URL to register as an SPA redirect URI (repeatable).
                             http://localhost:8080/ is always added for local testing.
  --create-groups            create security groups DM-<Role> and assign each to its app role
  --external-tenant <id>     also register the API in this Entra External ID tenant (customers / vendors)
  --no-consent               skip the admin consent grant (an administrator grants it later)
  --api-name <name>          display name of the API app (default: "$API_NAME")
  --spa-name <name>          display name of the console app (default: "$SPA_NAME")
  -h, --help                 this help

Re-running is safe: existing apps are found by display name and updated in place.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) ENVIRONMENT="${2:?}"; shift 2 ;;
    --console-url) CONSOLE_URLS+=("${2:?}"); shift 2 ;;
    --create-groups) CREATE_GROUPS=true; shift ;;
    --external-tenant) EXTERNAL_TENANT="${2:?}"; shift 2 ;;
    --no-consent) GRANT_CONSENT=false; shift ;;
    --api-name) API_NAME="${2:?}"; shift 2 ;;
    --spa-name) SPA_NAME="${2:?}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "Unknown option: $1" ;;
  esac
done
[[ "$ENVIRONMENT" =~ ^[a-z0-9-]+$ ]] || die "--env must be lowercase letters, digits or hyphens"

command -v az >/dev/null || die "Azure CLI (az) is required - use Azure Cloud Shell"
command -v python3 >/dev/null || die "python3 is required"
az account show -o none 2>/dev/null || die "Not signed in - run: az login"

uuid5() { python3 -c 'import sys,uuid; print(uuid.uuid5(uuid.UUID(sys.argv[1]), sys.argv[2]))' "$ID_NAMESPACE" "$1"; }

# Writes a JSON body to a temp file and sends it with az rest (avoids quoting problems on Windows/WSL too).
graph() {
  local method="$1" url="$2" body="${3:-}"
  if [[ -n "$body" ]]; then
    local f; f="$(mktemp)"
    printf '%s' "$body" >"$f"
    az rest --method "$method" --url "$url" --headers "Content-Type=application/json" --body "@$f" -o json
    rm -f "$f"
  else
    az rest --method "$method" --url "$url" -o json
  fi
}

# Escape a value for an OData string literal in $filter.
odata() { printf '%s' "${1//\'/\'\'}"; }

find_app() {  # display name -> appId (empty if missing)
  az ad app list --filter "displayName eq '$(odata "$1")'" --query "[0].appId" -o tsv 2>/dev/null || true
}

ensure_app() {  # display name -> appId
  local name="$1" app_id
  app_id="$(find_app "$name")"
  if [[ -z "$app_id" ]]; then
    app_id="$(az ad app create --display-name "$name" --sign-in-audience AzureADMyOrg --query appId -o tsv)"
    ok "Created app registration '$name' ($app_id)" >&2
  else
    ok "Found app registration '$name' ($app_id)" >&2
  fi
  printf '%s' "$app_id"
}

ensure_sp() {  # appId -> service principal object id
  local app_id="$1" sp_id
  sp_id="$(az ad sp list --filter "appId eq '$app_id'" --query "[0].id" -o tsv 2>/dev/null || true)"
  if [[ -z "$sp_id" ]]; then
    sp_id="$(az ad sp create --id "$app_id" --query id -o tsv)"
  fi
  printf '%s' "$sp_id"
}

app_object_id() { az ad app show --id "$1" --query id -o tsv; }

# JSON for the app roles; existing role ids are preserved, new ones are deterministic.
app_roles_json() {
  local existing="$1" lines=() r value display desc id
  for r in "${ROLES[@]}"; do
    IFS='|' read -r value display desc <<<"$r"
    id="$(python3 -c 'import json,sys; m={x["value"]:x["id"] for x in json.loads(sys.argv[1] or "[]")}; print(m.get(sys.argv[2],""))' "$existing" "$value")"
    [[ -n "$id" ]] || id="$(uuid5 "approle:$value")"
    lines+=("$value|$display|$desc|$id")
  done
  python3 - "${lines[@]}" <<'PY'
import json, sys
roles = []
for line in sys.argv[1:]:
    value, display, desc, rid = line.split("|", 3)
    roles.append({"allowedMemberTypes": ["User", "Application"], "description": desc, "displayName": display,
                  "id": rid, "isEnabled": True, "value": value})
print(json.dumps(roles))
PY
}

# ------------------------------------------------------------------------------------------ the API
configure_api() {
  local tenant="$1"
  step "API app registration: $API_NAME (tenant $tenant)"
  API_APP_ID="$(ensure_app "$API_NAME")"
  local obj existing_roles existing_scope scope_id roles body
  obj="$(app_object_id "$API_APP_ID")"
  existing_roles="$(az ad app show --id "$API_APP_ID" --query appRoles -o json)"
  existing_scope="$(az ad app show --id "$API_APP_ID" --query "api.oauth2PermissionScopes[?value=='access_as_user'].id | [0]" -o tsv)"
  scope_id="${existing_scope:-$(uuid5 "scope:access_as_user")}"
  roles="$(app_roles_json "$existing_roles")"

  # Roles that exist but are no longer in the contract must be disabled before they can be removed;
  # this script only adds and updates, so stale roles are kept as they are.
  body="$(python3 - "$roles" "$existing_roles" <<'PY'
import json, sys
wanted = json.loads(sys.argv[1]); existing = json.loads(sys.argv[2] or "[]")
names = {r["value"] for r in wanted}
print(json.dumps({"appRoles": wanted + [r for r in existing if r.get("value") not in names]}))
PY
)"
  graph PATCH "$GRAPH/applications/$obj" "$body" >/dev/null
  ok "App roles: ${#ROLES[@]} (User + Application)"

  body="$(python3 - "$API_APP_ID" "$scope_id" <<'PY'
import json, sys
app_id, scope_id = sys.argv[1:]
print(json.dumps({
  "identifierUris": [f"api://{app_id}"],
  "api": {
    "requestedAccessTokenVersion": 2,
    "oauth2PermissionScopes": [{
      "id": scope_id, "isEnabled": True, "type": "User", "value": "access_as_user",
      "adminConsentDisplayName": "Access the Taylor Farms Data Mart",
      "adminConsentDescription": "Allows the console to call the Data Mart API as the signed-in user.",
      "userConsentDisplayName": "Access the Taylor Farms Data Mart",
      "userConsentDescription": "Allows the console to call the Data Mart API on your behalf."}]},
  "optionalClaims": {"accessToken": [
      {"name": n, "essential": False, "additionalProperties": []}
      for n in ("email", "preferred_username", "idtyp", "acct")],
    "idToken": [{"name": "email", "essential": False, "additionalProperties": []}]},
  "groupMembershipClaims": "ApplicationGroup",
  "info": {"marketingUrl": None}
}))
PY
)"
  graph PATCH "$GRAPH/applications/$obj" "$body" >/dev/null
  ok "Identifier URI api://$API_APP_ID, scope access_as_user, v2 tokens, optional claims"

  API_SP_ID="$(ensure_sp "$API_APP_ID")"
  graph PATCH "$GRAPH/servicePrincipals/$API_SP_ID" '{"appRoleAssignmentRequired": true, "tags": ["WindowsAzureActiveDirectoryIntegratedApp"]}' >/dev/null
  ok "Enterprise application: user assignment required (only assigned users and groups can sign in)"
  SCOPE_ID="$scope_id"
}

# ------------------------------------------------------------------------------------------ the SPA
configure_spa() {
  step "Console app registration: $SPA_NAME"
  SPA_APP_ID="$(ensure_app "$SPA_NAME")"
  local obj body
  obj="$(app_object_id "$SPA_APP_ID")"
  local redirects=("http://localhost:8080/" "${CONSOLE_URLS[@]}")
  body="$(python3 - "$API_APP_ID" "$SCOPE_ID" "$GRAPH_APP_ID" "${redirects[@]}" <<'PY'
import json, sys
api, scope, graph, *uris = sys.argv[1:]
norm = []
for u in uris:
    u = u.strip()
    if not u:
        continue
    if u.count("/") == 2:  # bare origin -> origin/
        u += "/"
    if u not in norm:
        norm.append(u)
print(json.dumps({
  "spa": {"redirectUris": norm},
  "web": {"redirectUris": [], "implicitGrantSettings": {"enableAccessTokenIssuance": False, "enableIdTokenIssuance": False}},
  "isFallbackPublicClient": False,
  "api": {"requestedAccessTokenVersion": 2},
  "requiredResourceAccess": [
    {"resourceAppId": api, "resourceAccess": [{"id": scope, "type": "Scope"}]},
    {"resourceAppId": graph, "resourceAccess": [
        {"id": "e1fe6dd8-ba31-4d61-89e7-88639da4683d", "type": "Scope"},   # User.Read
        {"id": "37f7f235-527c-4136-accd-4a02d197296e", "type": "Scope"},   # openid
        {"id": "14dad69e-099b-42c9-810b-d002981feec1", "type": "Scope"},   # profile
        {"id": "7427e0e9-2fba-42fe-b0c0-848c9e6a8182", "type": "Scope"}]}] # offline_access
}))
PY
)"
  graph PATCH "$GRAPH/applications/$obj" "$body" >/dev/null
  ok "SPA redirect URIs: ${redirects[*]} (auth code + PKCE, no client secret, implicit grant off)"
  SPA_SP_ID="$(ensure_sp "$SPA_APP_ID")"

  if [[ "$GRANT_CONSENT" == true ]]; then
    local grant
    grant="$(az rest --method get --url "$GRAPH/oauth2PermissionGrants?\$filter=clientId eq '$SPA_SP_ID' and resourceId eq '$API_SP_ID'" --query "value[0].id" -o tsv 2>/dev/null || true)"
    if [[ -z "$grant" ]]; then
      if graph POST "$GRAPH/oauth2PermissionGrants" \
        "{\"clientId\":\"$SPA_SP_ID\",\"consentType\":\"AllPrincipals\",\"resourceId\":\"$API_SP_ID\",\"scope\":\"access_as_user\"}" >/dev/null; then
        ok "Admin consent granted: console -> access_as_user"
      else
        warn "Could not grant admin consent (needs an administrator). Portal: App registrations > $SPA_NAME > API permissions > Grant admin consent"
      fi
    else
      graph PATCH "$GRAPH/oauth2PermissionGrants/$grant" '{"scope":"access_as_user"}' >/dev/null
      ok "Admin consent already present: console -> access_as_user"
    fi
    local graph_sp ggrant
    graph_sp="$(az ad sp list --filter "appId eq '$GRAPH_APP_ID'" --query "[0].id" -o tsv)"
    ggrant="$(az rest --method get --url "$GRAPH/oauth2PermissionGrants?\$filter=clientId eq '$SPA_SP_ID' and resourceId eq '$graph_sp'" --query "value[0].id" -o tsv 2>/dev/null || true)"
    if [[ -z "$ggrant" ]]; then
      graph POST "$GRAPH/oauth2PermissionGrants" \
        "{\"clientId\":\"$SPA_SP_ID\",\"consentType\":\"AllPrincipals\",\"resourceId\":\"$graph_sp\",\"scope\":\"openid profile offline_access User.Read\"}" >/dev/null \
        || warn "Could not grant consent for openid/profile/offline_access/User.Read"
    fi
  else
    warn "Admin consent skipped (--no-consent). An administrator must grant it before users sign in."
  fi
}

# ------------------------------------------------------------------------------------------ groups
configure_groups() {
  step "Security groups DM-<Role> -> app roles"
  printf '  %-32s %-38s %s\n' "Group" "Object id" "App role"
  local r value display desc group gid role_id assigned
  for r in "${ROLES[@]}"; do
    IFS='|' read -r value display desc <<<"$r"
    group="DM-${value}"
    gid="$(az ad group list --filter "displayName eq '$(odata "$group")'" --query "[0].id" -o tsv 2>/dev/null || true)"
    if [[ -z "$gid" ]]; then
      gid="$(az ad group create --display-name "$group" --mail-nickname "${group//./-}" \
        --description "Taylor Farms Data Mart: $display" --query id -o tsv)"
    fi
    role_id="$(az ad app show --id "$API_APP_ID" --query "appRoles[?value=='$value'].id | [0]" -o tsv)"
    assigned="$(az rest --method get --url "$GRAPH/groups/$gid/appRoleAssignments" \
      --query "value[?resourceId=='$API_SP_ID' && appRoleId=='$role_id'].id | [0]" -o tsv 2>/dev/null || true)"
    if [[ -z "$assigned" ]]; then
      graph POST "$GRAPH/groups/$gid/appRoleAssignments" \
        "{\"principalId\":\"$gid\",\"resourceId\":\"$API_SP_ID\",\"appRoleId\":\"$role_id\"}" >/dev/null
    fi
    printf '  %-32s %-38s %s\n' "$group" "$gid" "$value"
  done
  ok "Add users (or B2B guests) to these groups to grant access. Group role assignment needs Entra ID P1."
}

# ------------------------------------------------------------------------------------------ External ID
configure_external() {
  step "Entra External ID tenant $EXTERNAL_TENANT (customers and vendors)"
  warn "Switching az to the External ID tenant - you may be asked to sign in."
  az login --tenant "$EXTERNAL_TENANT" --allow-no-subscriptions -o none
  local workforce_api="$API_APP_ID" workforce_scope="$SCOPE_ID" workforce_spa="$SPA_APP_ID"
  configure_api "$EXTERNAL_TENANT"
  EXT_API_APP_ID="$API_APP_ID"
  configure_spa
  EXT_SPA_APP_ID="$SPA_APP_ID"
  API_APP_ID="$workforce_api"; SCOPE_ID="$workforce_scope"; SPA_APP_ID="$workforce_spa"

  # Custom user attributes live on the tenant's b2c-extensions-app; Graph names them
  # extension_<appIdWithoutDashes>_<name>. Create them if they do not exist.
  local ext_app ext_obj attr
  ext_app="$(az ad app list --filter "startswith(displayName,'b2c-extensions-app')" --query "[0].appId" -o tsv 2>/dev/null || true)"
  if [[ -n "$ext_app" ]]; then
    ext_obj="$(app_object_id "$ext_app")"
    for attr in customerId vendorId regions; do
      if ! az rest --method get --url "$GRAPH/applications/$ext_obj/extensionProperties" \
           --query "value[?name=='extension_${ext_app//-/}_${attr}'].id | [0]" -o tsv 2>/dev/null | grep -q .; then
        if graph POST "$GRAPH/applications/$ext_obj/extensionProperties" \
          "{\"name\":\"$attr\",\"dataType\":\"String\",\"targetObjects\":[\"User\"]}" >/dev/null; then
          ok "Custom attribute $attr"
        else
          warn "Could not create custom attribute $attr"
        fi
      else
        ok "Custom attribute $attr already exists"
      fi
    done
  else
    warn "b2c-extensions-app not found - create the custom attributes customerId, vendorId and regions in the portal"
  fi
  EXT_ISSUER="https://${EXTERNAL_TENANT}.ciamlogin.com/${EXTERNAL_TENANT}/v2.0"
  cat <<EOF

  Finish in the Microsoft Entra admin center (External ID tenant) - these steps have no stable API yet:
    1. External Identities > User flows > New user flow "SignUpSignIn": email one-time passcode or
       email + password; collect Display name. Add the application "$SPA_NAME".
    2. Custom authentication extensions / Token configuration: emit customerId, vendorId and regions as
       claims extension_customerId, extension_vendorId, extension_regions in the access token of
       "$API_NAME" (Enterprise applications > $API_NAME > Single sign-on > Attributes & Claims).
    3. Turn OFF self-service sign-up if partners must be invited, or keep it and approve them by
       assigning External.Customer / External.Vendor and setting customerId / vendorId on the user.
    4. Assign users to "$API_NAME" with the role External.Customer or External.Vendor.

  External issuer (TFSO_ENTRA_EXTERNAL_ISSUER):  $EXT_ISSUER
EOF
  warn "Switch az back to the workforce tenant: az login --tenant $TENANT_ID"
}

# ------------------------------------------------------------------------------------------ main
TENANT_ID="$(az account show --query tenantId -o tsv)"
step "Workforce tenant $TENANT_ID  (environment: $ENVIRONMENT)"
[[ ${#CONSOLE_URLS[@]} -gt 0 ]] || warn "No --console-url given: only http://localhost:8080/ is registered. Re-run with the console URL printed by deploy.sh."

configure_api "$TENANT_ID"
configure_spa
[[ "$CREATE_GROUPS" == true ]] && configure_groups

EXT_ISSUER=""; EXT_API_APP_ID=""; EXT_SPA_APP_ID=""
[[ -n "$EXTERNAL_TENANT" ]] && configure_external

AUTHORITY="https://login.microsoftonline.com/${TENANT_ID}"
EXT_AUTHORITY=""
[[ -n "$EXTERNAL_TENANT" ]] && EXT_AUTHORITY="https://${EXTERNAL_TENANT}.ciamlogin.com/${EXTERNAL_TENANT}"

# APIM accepts every value; the first (api://<workforce api>) is what the services use (docs/08 §9).
AUDIENCES="api://$API_APP_ID,$API_APP_ID"
[[ -n "$EXT_API_APP_ID" ]] && AUDIENCES="$AUDIENCES,api://$EXT_API_APP_ID,$EXT_API_APP_ID"

mkdir -p "$STATE_DIR"
OUT="$STATE_DIR/${ENVIRONMENT}.entra.env"
cat >"$OUT" <<EOF
# Written by scripts/entra-setup.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ). Read by deploy.sh. No secrets.
TFSO_ENTRA_TENANT_ID="$TENANT_ID"
TFSO_ENTRA_API_CLIENT_ID="$API_APP_ID"
TFSO_ENTRA_AUDIENCE="$AUDIENCES"
TFSO_ENTRA_SPA_CLIENT_ID="$SPA_APP_ID"
TFSO_ENTRA_AUTHORITY="$AUTHORITY"
TFSO_ENTRA_SCOPE="api://$API_APP_ID/access_as_user"
TFSO_ENTRA_EXTERNAL_ISSUER="$EXT_ISSUER"
TFSO_ENTRA_EXTERNAL_AUTHORITY="$EXT_AUTHORITY"
TFSO_ENTRA_EXTERNAL_API_CLIENT_ID="$EXT_API_APP_ID"
TFSO_ENTRA_EXTERNAL_SPA_CLIENT_ID="$EXT_SPA_APP_ID"
EOF

step "Done"
cat <<EOF
  tenantId      $TENANT_ID
  apiClientId   $API_APP_ID
  apiAudience   api://$API_APP_ID   (v2 tokens carry aud=$API_APP_ID; both are accepted)
  spaClientId   $SPA_APP_ID
  authority     $AUTHORITY
  scope         api://$API_APP_ID/access_as_user
EOF
[[ -n "$EXT_ISSUER" ]] && printf '  externalIssuer %s\n' "$EXT_ISSUER"
cat <<EOF

  Saved to $OUT
  Next:
    1. Assign people (or the DM-* groups) to app roles: Enterprise applications > $API_NAME > Users and groups.
    2. Deploy with SSO:   ./deploy.sh azure --env $ENVIRONMENT
    3. If the console URL changed, re-run this script with --console-url <url>.
  See docs/08-sso-and-access-control.md.
EOF
