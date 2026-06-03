# Tulip Web App

A web app chatbot for Tulip API, has full CRUD operations on data if you choose to use it. By default it is read-only. It doesn't actually use an MCP server, it is a REST API with Claude integrated through aws bedrock converse API.

## Overview

A few important files:

1. **Web App** (`web_app.py`) - FastAPI REST interface for general purpose access with claude-powered queries.
2. **Tulip Client** (`tulip_client.py`) - HTTP client for Tulip. Makes requests.
3. **Tool Use** (`execute_tool.py`, `tool_def_loader.py`) - Load tools from json's in definitions folder, use tools with added timeout logic.

## Setup

### Install Dependencies

Probably use a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

On windows (zscaler cert is no longer a path):
```bash
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install python-certifi-win32
```

### Configure Environment

Create a `.env` file with your FIIX API credentials:

```
#ZSCALER_CERT_PATH=/path/to/zscaler_cert.pem
AWS_REGION=us-east-1
AWS_PROFILE=profile
BEDROCK_MODEL=claude_model_here
TULIP_API_KEY=api_key
TULIP_API_SECRET=api_secret_key
TULIP_BASE_URL=https://something.tulip.co
TULIP_WORKSPACE_ID=workspace_id # or leave blank if your api keys are workspace api keys
MCP_MAX_RETRIES=3
MCP_BASE_DELAY=1000
MCP_MAX_DELAY=30000
ENABLED_TOOLS=what-kind,of-tools,you-want
```

### Run Web App

```bash
python3 webapp.py
```

on the remote desktop machine I have it set up with NSSM:

```bash
nssm start TulipWeb # or whatever name I have it registered as
```

Starts on `http://0.0.0.0:8600` with:
- **POST /api/ask** - Natural language query with Claude
- **GET /api/history** - Returns the current conversation history
- **GET /api/clear-history** - Gets rid of current conversation history
- **GET /health** - Health check endpoint
- **GET /** - Homepage serving index.html
- **GET /shutdown** - Call to shutdown server gracefully

---
