# Enterprise Data Mart and SSO: binding contract (v1)

This file is the contract for the Enterprise Data Mart and SSO release. The backend, front end, infrastructure and documents all build to it.

## 1. Identity and authentication

| Mode (`TFSO_AUTH_MODE`) | Who uses it | How a request is authenticated |
|---|---|---|
| `apikey` | Legacy / smoke tests | `x-api-key` ∈ `TFSO_API_KEYS` → service principal |
| `dev` | Local and non-prod testing (**refused when `TFSO_ENV=prod`**) | `x-dev-persona: <personaId>` → that persona's principal. `x-api-key` → service principal |
| `entra` | Azure | `Authorization: Bearer <Entra access token>` (workforce tenant v2 issuer **or** Entra External ID issuer) → user principal. `x-api-key` → service principal (service-to-service only) |

Rules:

1. **A user identity always wins.** If a request carries a bearer token or a dev persona, it is authorised as that user even when an `x-api-key` is also present.
2. **No identity at all → 401.** `/health`, `/docs` and `/openapi.json` are always public, as is `GET /auth/v1/personas` in dev mode.
3. **The local gateway** injects `x-api-key` only when the request has neither `Authorization` nor `x-dev-persona` **and** `TFSO_AUTH_MODE=apikey`.
4. **APIM** in `entra` mode validates the JWT (`validate-jwt`) and forwards the `Authorization` header. It does **not** inject the backend key for user traffic.

Entra settings:

- `TFSO_ENTRA_TENANT_ID` — workforce tenant
- `TFSO_ENTRA_AUDIENCE` — API app id URI or client id
- `TFSO_ENTRA_EXTERNAL_ISSUER` — optional; the External ID issuer, `https://<tenant-id>.ciamlogin.com/<tenant-id>/v2.0`
- `TFSO_ENTRA_EXTERNAL_JWKS` — optional
- `TFSO_CLAIM_CUSTOMER` — default `extension_customerId`
- `TFSO_CLAIM_VENDOR` — default `extension_vendorId`
- `TFSO_CLAIM_REGIONS` — default `extension_regions`; comma-separated region ids

## 2. Principal (returned by `GET /exp/auth/v1/me`)

```json
{
  "id": "oid or persona id", "name": "Avery Admin", "email": "avery@…", "userType": "internal|external|service",
  "roles": ["DataMart.Admin", "…"], "authMode": "dev|entra|apikey", "persona": "admin|null",
  "domains": ["sales-order", "…"],
  "canExport": true, "canOperateOrders": true, "canReadOrders": true, "isAdmin": true, "isSteward": true, "piiAccess": true,
  "scope": {"customerIds": [], "vendorIds": [], "regions": []}
}
```

How the derived fields are worked out:

- **`domains`** is derived from the roles. `DataMart.Admin` gets every domain.
- **External customers** get the domains whose datasets allow the external customer audience. External vendors get the domains whose datasets allow the external vendor audience.
- **`scope`** is a row-level restriction. An empty list means unrestricted, which is only possible for internal users.

## 3. App roles (Entra app registration "Taylor Farms Data Mart API")

| Role value | Meaning |
|---|---|
| `DataMart.Admin` | Everything, including the Access & roles admin page |
| `DataMart.Steward` | Approve or reject access requests; see all domains' metadata |
| `DataMart.Export` | Export datasets; without it, users can preview only |
| `PII.Read` | See personal data unmasked |
| `Orders.Read` | Use the Order Status API and the Monitor pages (internal) |
| `Orders.Operate` | Create, correct, hold, cancel and resubmit orders; run the simulator |
| `Domain.SalesOrder.Read` | Read datasets in the Sales Order domain. The other domain roles work the same way: `Domain.Customer.Read`, `Domain.Product.Read`, `Domain.Logistics.Read`, `Domain.Vendor.Read`, `Domain.Procurement.Read`, `Domain.Finance.Read` |
| `External.Customer` | External customer; rows restricted to `scope.customerIds` |
| `External.Vendor` | External vendor; rows restricted to `scope.vendorIds` |

## 4. Dev personas (`GET /exp/auth/v1/personas`)

| id | Name | Type | Roles | Scope |
|---|---|---|---|---|
| `admin` | Avery Admin | internal | `DataMart.Admin` | – |
| `sales.analyst` | Sam Analyst | internal | `Orders.Read`, `Domain.SalesOrder.Read`, `Domain.Customer.Read`, `Domain.Product.Read`, `Domain.Logistics.Read`, `DataMart.Export` | – |
| `cs.agent` | Casey Service | internal | `Orders.Read`, `Orders.Operate`, `Domain.SalesOrder.Read`, `Domain.Customer.Read`, `PII.Read` | – |
| `regional.gm.west` | Morgan West | internal | `Orders.Read`, `Domain.SalesOrder.Read`, `Domain.Logistics.Read`, `Domain.Product.Read` | regions `[WEST]` |
| `procurement.buyer` | Pat Buyer | internal | `Domain.Procurement.Read`, `Domain.Vendor.Read`, `Domain.Product.Read`, `DataMart.Export` | – |
| `finance.controller` | Frankie Finance | internal | `Domain.Finance.Read`, `Domain.Vendor.Read`, `Domain.Procurement.Read`, `Domain.SalesOrder.Read`, `Domain.Customer.Read`, `DataMart.Export`, `PII.Read`, `DataMart.Steward` | – |
| `ext.customer.freshmart` | Pat Rivera (FreshMart) | external | `External.Customer` | customerIds `[C-1001]` |
| `ext.vendor.salinas` | Jordan Greens (Salinas Valley Greens) | external | `External.Vendor` | vendorIds `[V-1001]` |

Each persona also has an e-mail address. Persona item shape: `{id, name, email, userType, title, roles[], scope{}}`.

## 5. Domains and datasets

Domain ids, display names and their datasets (`*` = new in this release):

| Domain id | Name | Datasets |
|---|---|---|
| `sales-order` | Sales Order | `sales-orders`, `sales-order-lines`, `order-lifecycle-events`, `order-exceptions`, `orders-at-risk`, `order-kpis`, `regional-performance`, `price-agreements` |
| `customer` | Customer | `customers`, `ship-tos`, `trading-partners` |
| `product` | Product | `items`, `sites-inventory`, `item-costs`* |
| `logistics` | Logistics | `shipments`, `carriers`*, `carrier-loads`* |
| `vendor` | Vendor | `vendors`*, `vendor-scorecard`* |
| `procurement` | Procurement | `purchase-orders`*, `purchase-order-lines`*, `goods-receipts`* |
| `finance` | Finance | `invoices` (AR), `ap-invoices`*, `spend-by-category`* |

Each dataset card gains these fields:

- `domainId`, `domainName`
- `audiences`: any of `internal`, `external-customer`, `external-vendor`
- `rowScope`: `{"customer": "<field>"}` and/or `{"vendor": "<field>"}`
- `accessible`: boolean for the current user
- `lockedReason`: string when not accessible

Externally visible datasets:

| Audience | Datasets | Rows restricted by |
|---|---|---|
| External customers | `sales-orders`, `sales-order-lines`, `shipments`, `invoices`, `items` | `customerId`, except `items`, which is a public catalogue |
| External vendors | `vendors`, `vendor-scorecard`, `purchase-orders`, `purchase-order-lines`, `goods-receipts`, `ap-invoices` | `vendorId` |

How access is decided:

- **Internal users** need `Domain.<X>.Read`, or `DataMart.Admin`. Confidential datasets also need an approved access request, unless the user is admin or steward.
- **Inaccessible datasets** are still listed for internal users, marked `accessible:false` with a reason, so they can request access. They are hidden from external users.
- **Export** needs `DataMart.Export`, or external users exporting their own rows. Without it, preview is allowed and export returns 403.

## 6. API surface (new or changed)

Experience API:

| Method | Path | Purpose |
|---|---|---|
| GET | `/auth/v1/me` | Principal (section 2) |
| GET | `/auth/v1/personas` | Dev mode only; 404 otherwise |
| GET | `/admin/v1/roles` | `[{role, description, kind: platform\|domain\|external}]`; admin or steward |
| GET | `/admin/v1/role-matrix` | `{domains:[{domainId,name}], roles:[...], personas:[...] (dev)}`; admin or steward |
| GET | `/marketplace/v1/domains` | `[{domainId, name, description, owner, datasets, accessibleDatasets, accessible}]` |
| GET | `/marketplace/v1/datasets?domain=<domainId>` | Same as before, plus the new card fields; externals see only their datasets |
| GET | `/portal/v1/my/orders` | External customer's own orders (scope from the principal), and internal users with `Orders.Read` (all) |

System API:

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/mart/{dataset}?<field>=<value>&limit=` | Synthetic mart datasets (equality filters; comma-separated values mean IN) |
| GET | `/v1/mart` | Dataset list with row counts |

Process API authorisation:

- Users need `Orders.Read` for GET and `Orders.Operate` for POST, PATCH and DELETE. External users get 403.
- Users with `scope.regions` have every list, summary and KPI route restricted to those regions.

System API authorisation:

- Internal users or the service principal only.
- The simulator needs `Orders.Operate`.

## 7. Front-end config (`frontend/config.js`)

```js
window.TFSO_CONFIG = {
  apiBase: "/api", subscriptionKey: "", apiKey: "", environment: "LOCAL",
  auth: { mode: "dev",                       // "dev" | "entra"
          tenantId: "", clientId: "", authority: "https://login.microsoftonline.com/<tenantId>",
          externalAuthority: "",             // optional Entra External ID authority (customers/vendors)
          scopes: ["api://<api-client-id>/access_as_user", "openid", "profile", "offline_access"],
          redirectUri: location.origin + location.pathname, postLogoutRedirectUri: location.origin + location.pathname }
};
```

`dev`: a sign-in page with a persona picker; the chosen persona is sent as `x-dev-persona`. `entra`: authorisation-code flow with PKCE against the authority, and the access token is sent as `Authorization: Bearer`.
