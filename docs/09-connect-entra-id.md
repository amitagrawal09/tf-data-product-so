# Connect the Data Mart to Microsoft Entra ID (Azure AD), step by step

This guide switches the Enterprise Data Mart from **test personas** to **single sign-on with Microsoft Entra ID**
(formerly Azure Active Directory) **from the console's Settings page**. You need no redeploy and no terminal.

| | |
|---|---|
| **Time** | About 20 minutes |
| **You need** | An Entra ID account with the **Application Administrator** or **Cloud Application Administrator** role. A **Global Administrator** also works. |
| **You end with** | Everyone signs in with their Microsoft work account. What they see is decided by the Entra ID app roles you assign. |

> **Shortcut.** Part A (registering the two apps) can be scripted. In Azure Cloud Shell, run
> `scripts/entra-setup.sh --console-url <your console URL>`. It prints the three IDs, then you continue at **Part C**.
> The steps below are the manual equivalent in the Entra admin center.

The console shows the same five steps under **Settings → Single sign-on**:
**1 Register apps → 2 Enter IDs → 3 Check → 4 Test sign-in → 5 Enable**.

---

## Before you start: find your console URL

| Where the console runs | Console URL (this is the redirect URI) |
|---|---|
| Local (`./deploy.sh local` or `.\deploy.ps1 local`) | `http://localhost:8080/` |
| Azure | The **console URL** that `deploy.sh azure` prints at the end, e.g. `https://sttfsodevweb.z13.web.core.windows.net/` |

Open **Settings → Single sign-on** in the console and copy the URL shown in step 1. It is the exact value to register,
including the trailing `/`. Register both URLs if you use both.

---

## Part A — Register the API app ("Taylor Farms Data Mart API")

The API app represents the data mart's APIs. Its **app roles** are what grant access to domains, exports and PII.

1. Go to **https://entra.microsoft.com** and open **Identity → Applications → App registrations → New registration**.
2. Fill in the form:
   - **Name:** `Taylor Farms Data Mart API`
   - **Supported account types:** *Accounts in this organizational directory only (Single tenant)*
   - **Redirect URI:** leave empty
   - Click **Register**.
3. On the **Overview** page, copy two values. You enter them in the console later.
   - **Application (client) ID** → this is the **API application (client) ID**
   - **Directory (tenant) ID** → this is the **Directory (tenant) ID**
4. **Expose an API:**
   1. Next to **Application ID URI**, click **Add** and accept the default `api://<client-id>`, then **Save**.
   2. Click **Add a scope** and enter:
      - **Scope name:** `access_as_user`
      - **Who can consent:** *Admins and users*
      - **Admin consent display name:** `Access the Taylor Farms Data Mart`
      - **Admin consent description:** `Read data mart datasets as the signed-in user`
      - **State:** *Enabled*
   3. Click **Add scope**.
5. **Issue v2.0 tokens.** Open **Manifest**.
   - If the manifest has an `"api"` section, set `"requestedAccessTokenVersion": 2` inside it.
   - Otherwise (older manifest format), set `"accessTokenAcceptedVersion": 2`.
   - Click **Save**.
   - Without this step, tokens come from `sts.windows.net` and are rejected as *untrusted issuer*.
6. **Add the app roles.** Pick one of these:
   - **Fast:** in the console, go to **Settings → Single sign-on → step 1 → App roles for the API manifest → Copy appRoles JSON**.
     Paste it into the manifest's `"appRoles": [ ... ]` (replace the empty list), then **Save**. The ids match
     `scripts/entra-setup.sh`, so you can switch to the script later without changes.
   - **By hand:** open **App roles → Create app role** once per row below.
     - **Allowed member types:** *Users/Groups*. For roles other than `External.*`, choose *Both*.
     - **Value:** exactly as shown.
     - Tick **Do you want to enable this app role?**

   | Value | Grants |
   |---|---|
   | `DataMart.Admin` | Everything, including Settings → Single sign-on and Access & roles |
   | `DataMart.Steward` | Approve or reject access requests; read Confidential datasets without a request |
   | `DataMart.Export` | Export datasets (otherwise preview only) |
   | `PII.Read` | See personal data unmasked |
   | `Orders.Read` / `Orders.Operate` | Order Status API and Monitor pages / change orders |
   | `Domain.SalesOrder.Read` … `Domain.Finance.Read` | One per domain: Sales Order, Customer, Product, Logistics, Vendor, Procurement, Finance |
   | `External.Customer` / `External.Vendor` | Partner users (Entra External ID). Rows are limited to their own account. |

7. **Only assigned users can use the API.** Go to **Identity → Applications → Enterprise applications** and open
   **Taylor Farms Data Mart API → Properties**. Set **Assignment required?** to **Yes**, then **Save**.

## Part B — Register the console app ("Taylor Farms Data Mart Console")

The console is a single-page app. It signs users in with **authorization code + PKCE** and has **no client secret**.

1. Go to **App registrations → New registration**.
   - **Name:** `Taylor Farms Data Mart Console`
   - **Supported account types:** *Single tenant*
   - **Redirect URI:** choose the platform **Single-page application (SPA)** (not *Web*). Paste your console URL, e.g. `http://localhost:8080/`.
   - Click **Register**.
2. Copy the **Application (client) ID** from the Overview page. This is the **Console application (client) ID**.
3. **Authentication:**
   - Add any other console URL as another redirect URI under the **Single-page application** platform.
   - Leave **Implicit grant** (access tokens and ID tokens) **unchecked**.
4. **API permissions:**
   1. Click **Add a permission → APIs my organization uses**, then search for **Taylor Farms Data Mart API**.
   2. Choose **Delegated permissions**, tick **access_as_user**, then click **Add permissions**.
   3. Click **Grant admin consent for <your organization>** and confirm. The status turns green.
5. Do **not** create a client secret or certificate. The console never needs one.

## Part C — Give people access (assign app roles)

1. Go to **Enterprise applications → Taylor Farms Data Mart API → Users and groups → Add user/group**.
2. Select **yourself**, choose the role **DataMart.Admin**, then **Assign**.
   **Do this before testing.** Settings refuses to enable SSO unless your test sign-in carries `DataMart.Admin`, so you can't lock yourself out.
3. Assign everyone else, preferably through groups. Assigning groups needs Entra ID P1 or P2.

   | Suggested group | Roles | Typical members |
   |---|---|---|
   | `DM-Admins` | DataMart.Admin | Data platform team |
   | `DM-Stewards` | DataMart.Steward, PII.Read | Data owners and stewards |
   | `DM-Sales` | Domain.SalesOrder.Read, Domain.Customer.Read, Domain.Product.Read, Domain.Logistics.Read, Orders.Read, DataMart.Export | Commercial analysts |
   | `DM-CustomerService` | Orders.Read, Orders.Operate, Domain.SalesOrder.Read, Domain.Customer.Read, PII.Read | CS representatives |
   | `DM-Procurement` | Domain.Procurement.Read, Domain.Vendor.Read, Domain.Product.Read, DataMart.Export | Buyers |
   | `DM-Finance` | Domain.Finance.Read, Domain.Vendor.Read, Domain.Procurement.Read, Domain.SalesOrder.Read, Domain.Customer.Read, DataMart.Export, PII.Read | Controllers |

   Run `scripts/entra-setup.sh --create-groups` to create and assign these groups automatically.
   To limit regional staff to their regions, see *Row- and column-level security* in [docs/08](08-sso-and-access-control.md#5-row--and-column-level-security).

## Part D — Connect in the console (Settings → Single sign-on)

1. Open the console. While it still runs on test personas, sign in as **Avery Admin (Data platform administrator)**.
2. Open **Settings**. The **Single sign-on · Microsoft Entra ID** panel is near the bottom.
3. **Step 2 – Enter the IDs**
   - **Sign-in method:** *Microsoft Entra ID (single sign-on)*
   - **Directory (tenant) ID:** from Part A, step 3
   - **API application (client) ID:** from Part A, step 3
   - **Console application (client) ID:** from Part B, step 2
   - Click **Save draft**. The badge shows *Saved · not tested*. Nobody's sign-in has changed yet.
4. **Step 3 – Check the connection.** Click **Check**. The server fetches your tenant's sign-in metadata.
   - *Workforce tenant reachable* must show **OK**.
   - If it fails, compare the tenant ID with **Entra admin center → Overview**, and make sure the server can reach `login.microsoftonline.com` over HTTPS.
5. **Step 4 – Test sign-in.** Click **Sign in with Microsoft to test**.
   1. Microsoft asks you to pick your account and may ask for consent.
   2. You come back to Settings, which validates the token: signature, issuer, audience and the `DataMart.Admin` role.
   3. Look for **Passed** and the roles listed from your token.
   4. If it shows *does not carry DataMart.Admin*, finish Part C, step 2, then test again. New role assignments only appear in new tokens.
6. **Step 5 – Enable for everyone.** Click **Enable single sign-on** and confirm.
   - All three APIs enforce Entra ID within about 10 seconds.
   - You are signed out, and the sign-in page now shows **Sign in with Microsoft**.
   - Test personas stop working.
7. **Verify.** Sign in with Microsoft.
   - The user chip shows your work e-mail.
   - **My access** lists your roles and domains.
   - The **Data catalog** unlocks exactly the domains you were assigned.

Changing any ID later works the same way: edit, **Save draft**, **Test sign-in**, **Enable**. Any edit throws away
the earlier test, so the configuration that gets enabled is always one that was tested.

## Part E — Customers and vendors (optional, Entra External ID)

Partners sign in through a separate **Entra External ID** tenant.

1. Create the External ID tenant and a user flow, and add the custom attributes `customerId` and `vendorId`.
   [docs/08](08-sso-and-access-control.md#74-customers-and-vendors-with-external-id) lists the portal steps. `scripts/entra-setup.sh --external-tenant` registers the apps.
2. Back in **Settings → Single sign-on**, open **Customers & vendors (Entra External ID)** and fill in:
   - **External ID authority:** `https://<tenant-subdomain>.ciamlogin.com/<tenant-id>`
   - **External ID issuer:** `https://<tenant-id>.ciamlogin.com/<tenant-id>/v2.0`
   - **External API client ID:** the API app registered in the External ID tenant
   - **External console client ID:** fill in if the console app there has a different ID; otherwise leave it blank.
3. **Save draft → Check** (*External ID tenant reachable* must be **OK**) **→ Test sign-in → Enable**.
4. The sign-in page now also offers **Sign in with your partner account**.
5. In the External ID tenant, assign partner users `External.Customer` or `External.Vendor`. Set their `customerId` or `vendorId` attribute so that rows are limited to their own account.

## Part F — Azure environments and production

- **Redirect URI.** Register the Azure console URL in Part B, steps 1 and 3.
- **Network.** The Experience API must reach `login.microsoftonline.com` (and `*.ciamlogin.com` for External ID) over HTTPS. Azure Container Apps allow this by default.
- **Harden after enabling (recommended).** Settings makes the three APIs validate every token. To also make API Management reject bad tokens at the edge, re-run the deployment with the same IDs:
  ```bash
  TFSO_ENTRA_TENANT_ID=<tenant> TFSO_ENTRA_API_CLIENT_ID=<api> TFSO_ENTRA_SPA_CLIENT_ID=<console> ./deploy.sh azure
  ```
  Add `TFSO_SSO_SETTINGS=locked` to the container apps' settings if the deployment should own SSO and Settings should be read-only.
- **Production.** `TFSO_ENV=prod` never allows test personas, so you can't use Settings to bootstrap it.
  - Either prepare SSO in a dev or test environment first and reuse the same IDs,
  - or run `scripts/entra-setup.sh` and then `./deploy.sh azure --env prod`, as described in [docs/06](06-azure-one-click-deployment.md#enable-sso-microsoft-entra-id).
  - After that, an admin can make changes in Settings.

## Break glass: "I locked myself out"

The saved SSO settings can always be discarded with the deployment's service key. No user sign-in is needed.

```bash
./deploy.sh sso-reset            # local stack
```

```bash
./deploy.sh sso-reset azure      # Azure (uses ENVIRONMENT / RG, reads the APIM service key with az)
```

```powershell
.\deploy.ps1 sso-reset           # Windows, local stack
```

The APIs fall back to the deployment's sign-in mode within about 10 seconds. That is test personas locally, and
whatever `deploy.sh` configured in Azure.

## Troubleshooting

| Message | Cause | Fix |
|---|---|---|
| `AADSTS50011` redirect URI does not match | The console URL isn't registered, or its trailing `/` differs | Add the exact URL from Settings step 1 under **Single-page application** (Part B) |
| `AADSTS9002326` cross-origin token redemption only for SPA | The redirect URI was added under the **Web** platform | Remove it from *Web* and add it under **Single-page application** |
| `AADSTS65001` user or administrator has not consented | Admin consent is missing | Part B, step 4: **Grant admin consent** |
| `AADSTS500011` resource principal not found | The API has no Application ID URI, or the API client ID is wrong | Part A, step 4.1; re-check the API client ID in Settings |
| `AADSTS50105` user not assigned to a role | **Assignment required** is on, and the user has no role | Part C: assign the user or one of their groups |
| Test sign-in: *Untrusted token issuer https://sts.windows.net/…* | v1 tokens | Part A, step 5: `requestedAccessTokenVersion: 2` |
| Test sign-in: *Audience doesn't match* | The API client ID in Settings is not the API app | Use the API app's Application (client) ID, not the console's |
| Test sign-in: *does not carry DataMart.Admin* | Role not assigned yet, or the token predates the assignment | Part C, step 2, then **Test sign-in** again |
| Check: *Microsoft does not recognise tenant* | Wrong tenant ID | Copy the **Directory (tenant) ID** from Entra admin center → Overview |
| Check: *Could not reach login.microsoftonline.com* | Outbound HTTPS blocked | Allow the host in your proxy or firewall |
| Signed in but catalog locked | Your account has no `Domain.*.Read` roles | Assign domain roles; **My access** shows what your token carries |
| Everyone is locked out | Wrong settings were enabled | Break glass: `./deploy.sh sso-reset` (above) |

## How it works (for engineers)

- **Where the settings live.** The Process API stores them in the `sso_settings` table, in two slots: `draft` and `active`.
  - The Experience and System APIs read `active` every 10 seconds (`TFSO_SSO_REFRESH_SECONDS`).
  - The active record overlays the deployment's `TFSO_AUTH_MODE` / `TFSO_ENTRA_*` settings (`src/tfso/common/sso.py`).
- **How the console learns the method.** It reads `GET /exp/auth/v1/config`, a public endpoint that returns identifiers only, so it knows how to sign users in.
  - A test sign-in runs PKCE against the draft and keeps that token apart from the current session.
- **Admin API** (all require `DataMart.Admin`):
  - `GET|PUT /exp/admin/v1/sso`
  - `POST /exp/admin/v1/sso/check`
  - `POST /exp/admin/v1/sso/verify`
  - `POST /exp/admin/v1/sso/activate`
  - `POST /exp/admin/v1/sso/reset`
- **Safety rules:**
  - No secrets are stored. Entra identifiers are not secret.
  - Prod never allows personas.
  - Enabling requires a test sign-in by a `DataMart.Admin`, and the draft must not have changed since that test.
  - The store is service-only and covered by the break-glass reset.
  - Tests: `tests/test_sso_settings.py`.
