# func-secure-score

Azure Function that generates Microsoft Secure Score PDF reports using Playwright for HTML-to-PDF rendering. Runs in a custom Docker container with pre-baked Chromium browser binaries.

There are x2 branches in active development.

- `main:` Quarterly Secure Score Workflow with nice report generated and emailed via workflow.
- `weekly:` Weekly Secure Score Workflows; very similar to the above but a weekly short scorecard version

---

## Quick Start

### 1. Build the container

```bash
make docker-build
```

### 2. Run the container locally

```bash
make docker-run
```

_Your containerized functions will be active at `http://localhost:7071`._

---

## Project Structure

| File / Directory                             | Purpose                                                               |
| -------------------------------------------- | --------------------------------------------------------------------- |
| [`Dockerfile`](Dockerfile)                   | Custom Azure Functions image with Playwright + Chromium pre-installed |
| [`Makefile`](Makefile)                       | Convenient build and run targets                                      |
| [`secure_score_graph/`](secure_score_graph/) | Azure Function code, HTML templates, and report logic                 |
| [`infra/`](infra/)                           | Azure Bicep IaC for ACR + Container App deployment                    |
| [`startup.sh`](startup.sh)                   | Container startup script for Playwright installation                  |

---

## Docker Image

### Dockerfile Design Decisions

- **Base Image**: `mcr.microsoft.com/azure-functions/python:4-python3.11`
- **Global Playwright Browsers Path**: `ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` with `chmod -R 777` to prevent permission issues when the Azure Functions host runs under a non-root security context.
- **Pre-baked Browser & System Dependencies**: Chromium and all system-level dependencies (`libgbm`, `libasound`, etc.) are installed during the Docker build phase, ensuring instant container startup with no runtime downloads.

### .dockerignore

Excludes local virtual environments, caches, and `local.settings.json` to prevent secrets from being baked into the image.

---

## Deploying to Azure

### Prerequisites

- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) installed and authenticated
- A resource group created: `az group create --name rg-secure-score --location northeurope`

### Infrastructure (Bicep)

The `infra/` directory contains modular Bicep templates that deploy:

| Resource                                  | Module                                                                 |
| ----------------------------------------- | ---------------------------------------------------------------------- |
| Azure Container Registry (ACR)            | [`infra/modules/acr.bicep`](infra/modules/acr.bicep)                   |
| Container App Environment + Container App | [`infra/modules/containerapp.bicep`](infra/modules/containerapp.bicep) |
| User-Assigned Managed Identity (AcrPull)  | Included in `containerapp.bicep`                                       |
| Log Analytics Workspace                   | Included in `containerapp.bicep`                                       |

#### Deploy infrastructure

```bash
az deployment group create \
  --resource-group rg-secure-score \
  --template-file infra/main.bicep \
  --parameters infra/main.bicepparam
```

#### Override parameters at deploy time

```bash
az deployment group create \
  --resource-group rg-secure-score \
  --template-file infra/main.bicep \
  --parameters infra/main.bicepparam \
  --parameters location=westeurope acrSku=Standard maxReplicas=5
```

### Push your Docker image to ACR

```bash
# Get the ACR login server from deployment outputs
ACR_LOGIN=$(az deployment group show \
  --resource-group rg-secure-score \
  --name deploy-acr \
  --query properties.outputs.acrLoginServer.value -o tsv)

# Login, tag, and push
az acr login --name funcsecurescore
docker tag func-secure-score:latest ${ACR_LOGIN}/func-secure-score:latest
docker push ${ACR_LOGIN}/func-secure-score:latest
```

---

### Images of Report Template

![Secure Score Report](docs\images\quarterly_template_snippet.png)
![Secure Score Report](docs\images\quarterly_template_details4.png)
![Secure Score Report](docs\images\quarterly_template_details5.png)
![Secure Score Report](docs\images\quarterly_template_details2.png)

![Secure Score Report](docs\images\weekly.png)

---

## API Usage

### GET `/api/secure-score-graph`

Returns an HTML form for pasting JSON and generating PDF reports interactively.

### POST `/api/secure-score-graph`

Accepts JSON body and returns a base64-encoded PDF report. This is used by a workflow software so that the encoded report can be attached via email using the content type

**Body schema:**

```json
{
  "tenant_name": "Contoso Ltd",
  "result": [
    {
      "createdDateTime": "2024-01-01T00:00:00Z",
      "currentScore": 75,
      "maxScore": 100
    }
  ],
  "control_profiles": [
    {
      "rank": 1,
      "title": "Enable MFA",
      "controlCategory": "Identity",
      "maxScore": 9,
      "implementationCost": "low",
      "userImpact": "moderate",
      "threats": ["accountBreach", "phishingOrWhaling"],
      "remediation": "Require MFA for all users via Conditional Access.",
      "actionUrl": "https://portal.azure.com/#blade/..."
    }
  ]
}
```

Add `?weekly=true` query parameter for the weekly digest format.

---

## License

MIT
