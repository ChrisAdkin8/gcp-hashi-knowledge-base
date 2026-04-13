# HashiCorp RAG — MCP Server

The MCP server (`mcp/server.py`) exposes the Vertex AI RAG corpus as a pair of
tools that any MCP-compatible client can call — most usefully Claude Code, which
gains the ability to look up HashiCorp documentation automatically during a
conversation without any manual copy-paste.

---

## Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Registering with Claude Code](#registering-with-claude-code)
- [Tool Reference](#tool-reference)
  - [search\_hashicorp\_docs](#search_hashicorp_docs)
  - [get\_corpus\_info](#get_corpus_info)
- [Testing](#testing)
- [Manual Usage (without Claude Code)](#manual-usage-without-claude-code)
- [Troubleshooting](#troubleshooting)

---

## Overview

```
Claude Code
    │
    │  MCP (stdio transport)
    ▼
mcp/server.py
    │
    │  Vertex AI SDK (google-cloud-aiplatform)
    ▼
Vertex AI RAG Engine
    │
    │  vector search + chunk retrieval
    ▼
HashiCorp documentation corpus
(Terraform providers, Vault, Consul, Nomad,
 Packer, Sentinel, GitHub issues, Discourse,
 blog posts — ~1 024-token chunks)
```

When Claude Code needs information about a HashiCorp product, it calls
`search_hashicorp_docs` with a natural language query. The server queries the
Vertex AI corpus and returns the most relevant document chunks. Claude then uses
those chunks — with full source attribution — to answer the question.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | provided by `.venv` |
| google-cloud-aiplatform | ≥ 1.74.0 | Vertex AI SDK with `vertexai.rag` |
| mcp | ≥ 1.3.0 | Model Context Protocol Python SDK |
| GCP ADC | — | `gcloud auth application-default login` |
| Corpus | deployed | created by `task up` or `task corpus:create` |

The virtual environment (`.venv`) is created by the existing preflight tasks
and already contains `google-cloud-aiplatform`. Only the `mcp` package needs to
be added.

---

## Installation

```bash
# 1. Install the mcp package into the existing venv
task mcp:install

# 2. Verify the installation
.venv/bin/python3 -c "import mcp; import vertexai; print('OK')"
```

---

## Configuration

The server reads three environment variables. All three are required at
runtime.

| Variable | Required | Default | Description |
|---|---|---|---|
| `VERTEX_PROJECT` | yes | — | GCP project ID. Also accepts `GOOGLE_CLOUD_PROJECT`. |
| `VERTEX_REGION` | no | `us-west1` | Vertex AI region where the corpus lives. |
| `VERTEX_CORPUS_ID` | yes | — | Numeric corpus ID (e.g. `6917529027641081856`). |

Authentication is handled entirely through Google Application Default
Credentials. Run `gcloud auth application-default login` once; no service
account key files are needed.

---

## Registering with Claude Code

The setup task writes the server entry into `.claude/settings.local.json` so
that Claude Code starts the MCP server automatically when it opens this project.

```bash
# Auto-detect corpus ID from Terraform output
task mcp:setup

# Or supply the corpus ID explicitly
task mcp:setup CORPUS_ID=6917529027641081856
```

After the task completes, **restart Claude Code**. The tools
`search_hashicorp_docs` and `get_corpus_info` will appear in the tool list
immediately.

### What the task writes

`task mcp:setup` adds the following block to `.claude/settings.local.json`:

```json
{
  "mcpServers": {
    "hashicorp-rag": {
      "command": "/abs/path/to/.venv/bin/python3",
      "args": ["/abs/path/to/mcp/server.py"],
      "env": {
        "VERTEX_PROJECT": "<project-id>",
        "VERTEX_REGION": "us-west1",
        "VERTEX_CORPUS_ID": "<corpus-id>"
      }
    }
  }
}
```

The absolute paths are resolved at setup time so the server works regardless
of the working directory from which Claude Code is launched.

### Updating after a corpus refresh

If you create a new corpus (e.g. after `task corpus:create`), re-run:

```bash
task mcp:setup CORPUS_ID=<new-corpus-id>
```

Then restart Claude Code.

---

## Tool Reference

### search\_hashicorp\_docs

Search the HashiCorp documentation corpus for semantically relevant chunks.

**Input schema**

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | yes | — | Natural language question or topic. |
| `top_k` | integer | no | `5` | Number of results to return. Range: 1–20. |
| `distance_threshold` | float | no | `0.35` | Relevance cutoff. Range: 0.1–1.0. Lower = stricter. |
| `product` | string | no | — | Filter by product name (see below). |
| `product_family` | string | no | — | Filter by product family (see below). |
| `source_type` | string | no | — | Filter by document source type (see below). |

**product values**

`aws`, `azure`, `google`, `vault`, `consul`, `nomad`, `packer`, `terraform`,
`boundary`, `waypoint` — and all other Terraform provider and module names
indexed in the corpus.

**product\_family values**

| Value | Covers |
|---|---|
| `terraform` | All Terraform providers, modules, Terraform CLI docs |
| `vault` | Vault docs, secrets engines, auth methods |
| `consul` | Consul service mesh, health checks, KV |
| `nomad` | Nomad job specs, schedulers, drivers |
| `packer` | Packer templates, builders, provisioners |
| `boundary` | Boundary targets, auth methods, sessions |
| `sentinel` | HashiCorp Sentinel policy framework |

**source\_type values**

| Value | Description |
|---|---|
| `provider` | Terraform provider documentation |
| `documentation` | Core product docs (Vault, Consul, Nomad, etc.) |
| `module` | Terraform Registry module READMEs |
| `sentinel` | Sentinel policy library docs |
| `issue` | GitHub issue threads |
| `discuss` | HashiCorp Discuss forum threads |
| `blog` | HashiCorp blog posts |

**Output format**

```
Found 3 result(s) for: "How do I configure the AWS provider?"

--- Result 1 ---
Source: https://github.com/hashicorp/terraform-provider-aws/blob/main/website/docs/...
Score:  0.8712
Text:
source_type: provider
product: aws
product_family: terraform
...full chunk text...

--- Result 2 ---
...
```

**Example calls**

```
# Basic search
search_hashicorp_docs("How do I configure the AWS Terraform provider?")

# Increase breadth (more results, looser threshold)
search_hashicorp_docs("Vault dynamic secrets", top_k=10, distance_threshold=0.5)

# Filter to Vault documentation only
search_hashicorp_docs("Enable PKI secrets engine", product_family="vault", source_type="documentation")

# Filter to GitHub issues about Consul
search_hashicorp_docs("Consul service discovery failing", product_family="consul", source_type="issue")

# Filter to Terraform module docs
search_hashicorp_docs("VPC module variables", product_family="terraform", source_type="module")
```

---

### get\_corpus\_info

Return configuration details of the active corpus. Takes no arguments.

**Output example**

```
HashiCorp RAG Corpus
========================================
Project:       hc-e96dc2a274054e128e6309abba6
Region:        us-west1
Corpus ID:     6917529027641081856
Resource name: projects/hc-e96dc2a274054e128e6309abba6/locations/us-west1/ragCorpora/6917529027641081856

Metadata filters available in search_hashicorp_docs:
  product:        aws | vault | consul | nomad | packer | terraform | boundary | waypoint
  product_family: terraform | vault | consul | nomad | packer | boundary | sentinel
  source_type:    provider | documentation | module | sentinel | issue | discuss | blog

Default retrieval settings:
  top_k:              5  (range 1–20)
  distance_threshold: 0.35  (range 0.1–1.0; lower = stricter)
```

---

## Testing

Run the smoke-test suite against the live corpus:

```bash
task mcp:test CORPUS_ID=6917529027641081856
```

This executes `mcp/test_server.py` which runs four checks:

| # | Check | Validates |
|---|---|---|
| 1 | `get_corpus_info` | Environment variables are set; resource name is correct |
| 2 | `search_hashicorp_docs` — basic query | Retrieval returns at least one result |
| 3 | `search_hashicorp_docs` — filtered query | Metadata filtering path is exercised |
| 4 | `search_hashicorp_docs` — no-results query | Edge case returns a friendly message |

Expected output for a healthy corpus:

```
============================================================
Test 1: get_corpus_info
============================================================
HashiCorp RAG Corpus
...
[PASS] corpus info — project set
[PASS] corpus info — corpus ID set

============================================================
Test 2: search_hashicorp_docs — basic query
============================================================
Found 3 result(s) for: "How do I configure the AWS Terraform provider?"
...
[PASS] basic search returns results

============================================================
Test 3: search_hashicorp_docs — filtered by product_family=vault
============================================================
Found 3 result(s) for: "How do I enable the PKI secrets engine in Vault?"
...
[PASS] filtered search returns results

============================================================
Test 4: search_hashicorp_docs — query with no expected results
============================================================
No results found for: "xyzzy_nonexistent_token_for_testing"
[PASS] no-results returns friendly message

All tests passed.
```

---

## Manual Usage (without Claude Code)

You can query the corpus from any terminal using the MCP inspector or by calling
the Python functions directly.

### Direct Python call

```bash
VERTEX_PROJECT=hc-e96dc2a274054e128e6309abba6 \
VERTEX_REGION=us-west1 \
VERTEX_CORPUS_ID=6917529027641081856 \
.venv/bin/python3 - <<'EOF'
import importlib.util, pathlib

spec = importlib.util.spec_from_file_location("rag_server", "mcp/server.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print(mod.get_corpus_info())
print()
print(mod.search_hashicorp_docs(
    "How do I enable Vault's AWS secrets engine?",
    top_k=3,
    product_family="vault",
))
EOF
```

### MCP Inspector (interactive)

```bash
VERTEX_PROJECT=hc-e96dc2a274054e128e6309abba6 \
VERTEX_REGION=us-west1 \
VERTEX_CORPUS_ID=6917529027641081856 \
npx @modelcontextprotocol/inspector .venv/bin/python3 mcp/server.py
```

The inspector opens a local web UI where you can call tools interactively and
inspect the JSON responses. Requires Node.js.

---

## Troubleshooting

### `Configuration error: VERTEX_PROJECT … is not set`

The server started but the environment variables were not injected. Check the
`env` block in `.claude/settings.local.json` and ensure the values match your
project. Re-run `task mcp:setup` if in doubt.

### `Retrieval error: …PERMISSION_DENIED…`

Application Default Credentials are not configured or do not have the
`aiplatform.ragsCorpora.query` permission. Run:

```bash
gcloud auth application-default login
```

### `Retrieval error: …NOT_FOUND…`

The corpus ID in `VERTEX_CORPUS_ID` does not match the corpus in the project/
region pair. Confirm the correct ID:

```bash
task output  # shows corpus_resource_name from Terraform
```

### Server does not appear in Claude Code

1. Confirm `.claude/settings.local.json` contains the `mcpServers.hashicorp-rag`
   block (run `task mcp:setup` to write it).
2. Restart Claude Code completely (quit and reopen, not just a new session).
3. Check the MCP server log in the Claude Code developer console for startup
   errors.

### `mcp` package not found

The `mcp` PyPI package is not installed in `.venv`. Run:

```bash
task mcp:install
```

### Filtered search returns fewer results than expected

Post-retrieval metadata filtering reduces the result count. The server
over-fetches (`top_k × 3`) when filters are active, but if the corpus contains
few documents matching the filter combination, the result count will be low.
Options:

- Raise `distance_threshold` to 0.5 or higher to widen the candidate set.
- Remove one of the filters (e.g. drop `source_type` and keep only
  `product_family`).
- Run `task pipeline:run` to re-ingest the corpus with updated documents.
