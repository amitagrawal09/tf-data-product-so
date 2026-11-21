# One-click deployment on Azure

There are three ways to deploy the whole Sales Order data product. Each one deploys:

- the platform (Bicep);
- the container image, built in ACR;
- the three Container Apps;
- API Management;
- the Order Operations Console;
- optionally, a smoke test and demo data.

| Option | Best for | One-time setup | The "click" |
|---|---|---|---|
| **A. GitHub Actions** (recommended) | Teams; repeatable dev → test → prod with approvals | Run `scripts/azure-github-setup.sh` once in Azure Cloud Shell | GitHub → Actions → **Deploy to Azure** → Run workflow |
| **B. Azure portal (Template Spec)** | Anyone with Azure access, including when the repo is private | Publish the template spec once (`scripts/publish-template-spec.sh`) | Portal → Template specs → **tfso-sales-order** → Deploy |
| **C. "Deploy to Azure" button** | Public repositories / demos | None (the build workflow publishes `infra/azuredeploy.json`) | Click the button in the README |

All three run the same engine: `deploy.sh azure`. B and C run it inside an Azure **deployment script** (an Azure CLI container) under a deployer managed identity. That identity has Owner on the new resource group only.

## Prerequisites (all options)

- An Azure subscription where you are **Owner**, or **Contributor + User Access Administrator**. The platform assigns roles to its workload identity.
- Resource providers registered once per subscription. The GitHub setup script does this. For B and C, run it once in Cloud Shell:

  ```bash
  for p in Microsoft.App Microsoft.ApiManagement Microsoft.ContainerRegistry Microsoft.ServiceBus Microsoft.EventHub Microsoft.DBforPostgreSQL Microsoft.KeyVault Microsoft.CognitiveServices Microsoft.OperationalInsights Microsoft.Insights Microsoft.Storage Microsoft.ManagedIdentity Microsoft.ContainerInstance; do az provider register -n $p; done
  ```

- Duration:
  - The first deployment takes about 45–60 minutes with the APIM **Developer** SKU, which alone takes 30–45 minutes.
  - With **Consumption** or **BasicV2** it takes about 15–20 minutes.
  - Re-deployments take about 10 minutes.
- Cost: a dev environment with Developer APIM, B1ms PostgreSQL and scale-to-zero Container Apps runs to a few hundred USD per month. See the cost table in [04 reference architecture](04-azure-reference-architecture.md). Delete the resource group when you are done.

## A. GitHub Actions: one click per environment

**One-time setup** (about 2 minutes). Open [Azure Cloud Shell](https://shell.azure.com) (bash), then clone the repo:

```bash
git clone https://github.com/amitagrawal09/tf-data-product-so.git
```

```bash
cd tf-data-product-so
```

```bash
git checkout claude/dreamy-shannon-1j1v3d
```

Sign the GitHub CLI in to your account (optional; otherwise the script prints the values to add by hand):

```bash
gh auth login
```

Then run the setup script with your repository and your e-mail address:

```bash
./scripts/azure-github-setup.sh amitagrawal09/tf-data-product-so you@yourcompany.com
```

The setup script:

1. Registers the resource providers.
2. Creates `rg-tfso-dev`, `rg-tfso-test` and `rg-tfso-prod`.
3. Creates an Entra ID app **with GitHub OIDC federation**, so no client secret exists anywhere.
4. Grants that app Owner on those resource groups only.
5. Sets the GitHub secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`, and the variables `APIM_PUBLISHER_EMAIL`, `AZURE_LOCATION` and `TFSO_PREFIX`.
6. Creates the GitHub environments `dev`, `test` and `prod`.

**Deploy:** GitHub → **Actions** → **Deploy to Azure** → **Run workflow**, then choose:

- the environment;
- the region;
- the APIM SKU;
- whether to load demo data;
- whether to run the smoke test.

The workflow then:

1. Runs the test suite (a gate).
2. Logs in to Azure with OIDC.
3. Runs `./deploy.sh azure`.

The run summary shows the console URL, the API gateway URL and the Order Status API endpoint.

**Recommended:** in GitHub → Settings → Environments, add **required reviewers** to `test` and `prod` so that production deployments need an approval.

**Optional settings:**

| Kind | Name | Purpose |
|---|---|---|
| Secret | `POSTGRES_ADMIN_PASSWORD`, `TFSO_BACKEND_API_KEY` | Keep these stable across runs. Without them they are rotated on every deployment, consistently across Key Vault and the apps |
| Variable | `D365_URL`, `D365_TENANT_ID`, `D365_CLIENT_ID` | Connect live Dynamics 365 instead of the simulator |
| Secret | `D365_CLIENT_SECRET` | Credential for the live Dynamics 365 connection |

## B. Azure portal via a Template Spec (works with private repos)

Publish once, from Cloud Shell in the repo folder:

```bash
./scripts/publish-template-spec.sh
```

The script prints a portal link. Anyone with Reader on the template spec and the rights listed above can then go to **Portal → Template specs → tfso-sales-order → Deploy** and fill in the form:

| Field | What to enter |
|---|---|
| Subscription and region | Where to deploy |
| `prefix` | Short name for resources, e.g. `tfso` |
| `environment` | `dev`, `test` or `prod` |
| `apimPublisherEmail` | Required by API Management |
| `apimSku` | API Management tier |
| `repositoryUrl`, `repositoryRef` | The repository and the branch, tag or commit to deploy |
| `gitHubToken` | Only for a private repository: a fine-grained token with *Contents: read* on this repo |
| `loadDemoData`, `runSmokeTest` | Optional post-deployment steps |
| `d365*` | Optional live Dynamics 365 connection |

Review + create. When it finishes, the deployment **Outputs** tab shows `consoleUrl`, `apiGatewayUrl` and `orderStatusApi`.

The same template from the CLI:

```bash
az deployment sub create -l eastus2 -f infra/oneclick.bicep -p apimPublisherEmail=you@yourcompany.com
```

## C. "Deploy to Azure" button

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Famitagrawal09%2Ftf-data-product-so%2Fclaude%2Fdreamy-shannon-1j1v3d%2Finfra%2Fazuredeploy.json)

The button opens the same form as option B. Two requirements:

- **The repository must be public**, because the portal downloads `infra/azuredeploy.json` anonymously. For a private repository, use option B.
- **`infra/azuredeploy.json` must exist.** It is compiled from `infra/oneclick.bicep` and committed by the **Build one-click ARM template** workflow on every change to `infra/`. Run that workflow once (Actions → Build one-click ARM template → Run workflow) after pushing.

If you move the solution to another branch or repository, update the button URL and the `repositoryRef` default in `infra/oneclick.bicep`.

## What gets created

- **Resource group** `rg-<prefix>-<env>`, containing:
  - **Monitoring:** Log Analytics and Application Insights.
  - **Security:** Key Vault (RBAC) and a user-assigned workload identity.
  - **Build:** Azure Container Registry.
  - **Messaging:** Service Bus (3 topics, 5 subscriptions) and Event Hubs (`order-status-stream`).
  - **Data:** ADLS Gen2 (bronze, silver, gold) and PostgreSQL Flexible Server (`tfso_process`, `tfso_system`).
  - **AI:** AI Document Intelligence.
  - **Compute:** a Container Apps environment with the experience, process and system apps.
  - **API Management:** APIs `exp`, `process` and `system`; products; the console subscription.
  - **Front end:** a storage static website hosting the Order Operations Console.
- **Options B and C only:**
  - `id-<prefix>-<env>-deployer`: the deployer identity, with Owner on the resource group.
  - `ds-<prefix>-<env>-deploy`: the deployment script. Its logs are under **Resource group → ds-…-deploy → Logs**. The container is cleaned up on success and kept for one day (P1D) on failure.

## Enable SSO (Microsoft Entra ID)

Production always runs with Entra ID single sign-on. Dev and test use test personas until you enable it. The full guide is [docs/08-sso-and-access-control.md](08-sso-and-access-control.md).

1. Register the API and console apps. Run this once per environment, in Cloud Shell, as an administrator:

   ```bash
   scripts/entra-setup.sh --env prod --console-url https://<console-url>/ --create-groups
   ```

   This writes `.deploy/prod.entra.env`, which holds identifiers only.

2. Assign users or the `DM-*` groups to app roles. Go to **Enterprise applications → Taylor Farms Data Mart API → Users and groups**.

3. Deploy:
   - **Locally:** run `./deploy.sh azure --env prod`. It picks up the env file automatically.
   - **GitHub Actions (option A):** copy the values into the GitHub environment variables `TFSO_ENTRA_TENANT_ID`, `TFSO_ENTRA_API_CLIENT_ID` and `TFSO_ENTRA_SPA_CLIENT_ID`. Optionally also set `TFSO_ENTRA_AUDIENCE` and `TFSO_ENTRA_EXTERNAL_ISSUER`.

   - **Portal (options B and C):** fill in the **Entra Tenant Id**, **Entra Api Client Id** and **Entra Spa Client Id** parameters (optionally the External ID issuer and authority) on the deployment form.

A prod deployment without these settings stops with *"Production requires Entra ID single sign-on"*. Because the console's redirect URI is only known after the first deployment, run `scripts/entra-setup.sh` again with the final `--console-url` once the console URL is printed.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `could not download the repository` | Private repository: supply `gitHubToken`, or use option A |
| `MissingSubscriptionRegistration` | Register the providers (prerequisites above) |
| `AuthorizationFailed ... roleAssignments/write` | The deploying user or identity needs Owner, or User Access Administrator, at the target scope |
| Bicep step fails on the first attempt and then succeeds | RBAC propagation for Key Vault references and AcrPull; `deploy.sh` retries three times automatically |
| APIM name already in use or soft-deleted | Choose another `prefix`, or purge it with `az apim deletedservice purge` |
| `Production requires Entra ID single sign-on` | Run `scripts/entra-setup.sh` (see *Enable SSO* above) |
| Console sign-in fails with `AADSTS50011` | The console URL is not a registered SPA redirect URI. Re-run `scripts/entra-setup.sh --console-url <url>` |
| Smoke test fails right after deployment | Container Apps may still be scaling from zero. Re-run with `./deploy.sh smoke <apim-url>` and the `platform-service` subscription key |

## Tear down

```bash
./deploy.sh destroy
```

Or delete the resource group `rg-<prefix>-<env>`. Key Vault and APIM are soft-deleted; purge them if you want to reuse the same names straight away.
