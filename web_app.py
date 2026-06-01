import os
import signal
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import json
import logging
import boto3
from typing import Optional, List, Dict, Any
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Rate limiter: 10 requests per minute per IP
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda request, exc: HTTPException(status_code=429, detail="Rate limit exceeded: 10 requests per minute"))

# In-memory conversation history
conversation_history = []

# Enable CORS for network access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# AWS Configuration with IAM Roles Anywhere
# Uses aws_signing_helper for automatic credential refresh
AWS_PROFILE = os.getenv("AWS_PROFILE", "manufacturing-bedrock")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_MODEL = os.getenv("BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-6")

bedrock_client = None
try:
    from botocore.config import Config
    session = boto3.Session(profile_name=AWS_PROFILE)
    config = Config(
        connect_timeout=10,
        read_timeout=60,
        retries={"max_attempts": 2}
    )
    bedrock_client = session.client("bedrock-runtime", region_name=AWS_REGION, config=config)
    logger.info(f"Bedrock client initialized with AWS profile '{AWS_PROFILE}' in region '{AWS_REGION}'")
    logger.info(f"Using Claude model: {BEDROCK_MODEL}")
except Exception as e:
    logger.error(f"Failed to initialize Bedrock client: {e}")
    logger.error(f"Ensure AWS_PROFILE is in ~/.aws/config and aws_signing_helper is configured")
    bedrock_client = None

# Import Tulip tool classes
from tulip_client import TulipApiClient
from tool_def_loader import ToolDefinitionLoader
from execute_tool import TulipToolExecutor

# Initialize Tulip tool infrastructure
try:
    tulip_api_client = TulipApiClient(
        api_key=os.getenv("TULIP_API_KEY", ""),
        api_secret=os.getenv("TULIP_API_SECRET", ""),
        base_url=os.getenv("TULIP_BASE_URL", ""),
        workspace_id=os.getenv("TULIP_WORKSPACE_ID", ""),
        max_retries=int(os.getenv("MCP_MAX_RETRIES", 3)),
        base_delay=int(os.getenv("MCP_BASE_DELAY", 1000)),
        max_delay=int(os.getenv("MCP_MAX_DELAY", 30000)),
    )
    tool_loader = ToolDefinitionLoader("definitions/")
    tool_executor = TulipToolExecutor(tulip_api_client, tool_loader)

    # Load tools based on ENABLED_TOOLS env var, default to read-only
    enabled_tools_str = os.getenv("ENABLED_TOOLS", "read-only") or "read-only"
    enabled_items = [item.strip() for item in enabled_tools_str.split(",")]

    TOOLS = []
    for item in enabled_items:
        # Try to get by category first, then by type, then by individual tool name
        category_tools = tool_loader.get_tools_by_category(item)
        if category_tools:
            TOOLS.extend(category_tools)
        else:
            type_tools = tool_loader.get_tools_by_type(item)
            if type_tools:
                TOOLS.extend(type_tools)
            else:
                # Try to get individual tool by name
                tool = tool_loader.get_tool_by_name(item)
                if tool:
                    TOOLS.append(tool)
                else:
                    logger.warning(f"Unknown tool/category/type: {item}")

    # Remove duplicates while preserving order
    seen = set()
    TOOLS = [t for t in TOOLS if not (t["name"] in seen or seen.add(t["name"]))]

    logger.info(f"Loaded {len(TOOLS)} {", ".join(enabled_items)} Tulip tools.")
except Exception as e:
    logger.error(f"Failed to initialize Tulip tools: {e}", exc_info=True)
    TOOLS = []
    tool_executor = None


def execute_mcp_tool(tool_name: str, tool_input: Dict[str, Any]) -> str:
    """Execute a Tulip tool and return the result."""
    if not tool_executor:
        return json.dumps({"error": "Tool executor not initialized"})
    return tool_executor.execute_tool(tool_name, tool_input)


class QueryRequest(BaseModel):
    question: str


def generate_system_prompt(tools: List[Dict[str, Any]]) -> str:
    """Generate system prompt with enabled tools information."""
    # Extract enabled categories and types from loaded tools
    categories = set()
    types = set()
    for tool in tools:
        if "category" in tool:
            categories.add(tool["category"])
        if "type" in tool:
            types.add(tool["type"])

    categories_str = ", ".join(sorted(categories)) if categories else "none"
    types_str = ", ".join(sorted(types)) if types else "none"

    return f"""You are an assistant that helps users query and analyze data from the Tulip platform.

IMPORTANT: You have access to real-time tools that query the Tulip API. When users ask questions about:
- Tables, records, and data in Tulip
- Stations, station groups, and infrastructure
- Users, roles, and permissions
- Applications, interfaces, and assignments
- Machine activities and archives

You MUST use the available tools to get current, accurate data. Never make up or guess data.

Guidelines:
1. Always query the Tulip API first when users ask for specific information
2. Use pagination (limit and offset parameters) to fetch data in chunks when dealing with large result sets
3. If results are limited or incomplete, let the user know and offer to fetch more with pagination
4. Present results clearly and organized in a readable format
5. The Tulip API has resource limits - be conservative with large queries
6. For table data, always specify reasonable limits (10-50 records) unless user requests otherwise
7. Include relevant metadata in responses (IDs, timestamps, status) to make results actionable

Error Handling:
- If a tool returns an error with "_do_not_retry": true, acknowledge the error to the user and do NOT attempt to call that tool again
- Tool errors indicate the API request failed; do not retry with different parameters
- Inform the user about the specific error and suggest alternatives if possible

Available in this session:
- Categories: {categories_str}
- Types: {types_str}

For all tools:
- Each tool has an input schema describing required and optional parameters
- Use the tool's description and input schema to understand what parameters are needed

When constructing queries:
- Refer to tool definitions for exact parameter names and types
- Use filters and sorting parameters when available to narrow results
- Handle pagination with limit and offset for large datasets
"""


# Generate system prompt with enabled tools
SYSTEM_PROMPT = generate_system_prompt(TOOLS)


@app.get("/shutdown")
async def shutdown():
    """Shutdown the server gracefully."""
    logger.info("shutdown requested, exiting...")
    os.kill(os.getpid(), signal.SIGTERM)
    return {"status": "shutting down"}


@app.post("/api/ask")
@limiter.limit("10/minute")
async def ask_question(request: Request, query: QueryRequest):
    """Accept a natural language question and return an answer from Claude via Bedrock."""

    if not bedrock_client:
        raise HTTPException(
            status_code=503,
            detail="Bedrock client not configured. Check AWS profile and aws_signing_helper setup."
        )

    if not query.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")


    try:
        # Add user message to history
        conversation_history.append({"role": "user", "content": query.question})

        # Build full context including conversation history
        history_text = ""
        if len(conversation_history) > 1:
            history_text = "Previous conversation:\n"
            for msg in conversation_history[:-1]:  # All but the current user message
                role = "Assistant" if msg["role"] == "assistant" else "User"
                history_text += f"{role}: {msg['content']}\n\n"
            history_text += "\n---\n\n"

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "text": f"{history_text}User: {query.question}"
                    }
                ]
            }
        ]

        # Convert tool definitions to Bedrock format once
        bedrock_tools = []
        for tool in TOOLS:
            bedrock_tools.append({
                "toolSpec": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "inputSchema": {
                        "json": tool["inputSchema"]
                    }
                }
            })

        # Keep calling Claude until no more tool calls
        while True:

            # Build Bedrock request using converse API
            bedrock_request = {
                "modelId": BEDROCK_MODEL,
                "system": [{"text": SYSTEM_PROMPT}],
                "toolConfig": {"tools": bedrock_tools},
                "messages": messages,
                "inferenceConfig": {"maxTokens": 4096}
            }

            try:
                response = bedrock_client.converse(**bedrock_request)
            except (ConnectTimeoutError, ReadTimeoutError):
                logger.error(f"Bedrock timeout error")
                return {
                    "answer": "Claude is taking too long to respond. Please try again.",
                    "success": False
                }
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "Unknown")
                if error_code == "ThrottlingException":
                    logger.warning(f"Bedrock throttled (rate limit)")
                    return {
                        "answer": "API quota exceeded. Please wait a moment and try again.",
                        "success": False
                    }
                elif error_code == "ValidationException":
                    logger.error(f"Invalid Bedrock request: {e}")
                    return {
                        "answer": "Invalid request configuration. Check your settings.",
                        "success": False
                    }
                elif error_code == "AccessDeniedException":
                    logger.error(f"AWS credentials invalid or expired")
                    return {
                        "answer": "Authentication failed. Check your AWS credentials.",
                        "success": False
                    }
                else:
                    logger.error(f"Bedrock client error ({error_code}): {e}")
                    return {
                        "answer": "Error communicating with Claude. Please try again.",
                        "success": False
                    }
            except Exception as e:
                logger.error(f"Unexpected Bedrock error: {e}")
                return {
                    "answer": "Unexpected error. Please try again.",
                    "success": False
                }

            # Check if we're done
            if response["stopReason"] == "end_turn":
                # Extract final text response (concatenate all text blocks)
                final_response = ""
                for block in response["output"]["message"]["content"]:
                    if block.get("text"):
                        final_response += block["text"]

                # Ensure we have a response
                if not final_response.strip():
                    logger.warning("Claude returned end_turn with no text content")
                    return {
                        "answer": "No response generated",
                        "success": False
                    }

                # Add assistant response to history
                conversation_history.append({"role": "assistant", "content": final_response})

                return {
                    "answer": final_response,
                    "success": True
                }

            # Process tool calls
            if response["stopReason"] == "tool_use":
                # Add assistant's response to messages (already in correct format from Bedrock)
                messages.append({
                    "role": "assistant",
                    "content": response["output"]["message"]["content"]
                })

                # Execute tool calls and collect results
                tool_results = []
                content_blocks = response["output"]["message"]["content"]

                for block in content_blocks:
                    if block.get("toolUse"):
                        tool_use = block["toolUse"]
                        tool_name = tool_use.get("name")
                        tool_use_id = tool_use.get("toolUseId")
                        tool_result = execute_mcp_tool(tool_name, tool_use.get("input", {}))

                        # Check if tool result is an error
                        try:
                            result_obj = json.loads(tool_result)
                            if isinstance(result_obj, dict) and "error" in result_obj:
                                # Wrap error with explicit "do not retry" signal
                                tool_result = json.dumps({
                                    "error": result_obj.get("error"),
                                    "details": result_obj.get("details"),
                                    "_do_not_retry": True,
                                    "_message": f"Tool '{tool_name}' failed. Do not attempt to call this tool again in this request."
                                })
                                logger.warning(f"Tool '{tool_name}' failed: {result_obj.get('error')}")
                        except (json.JSONDecodeError, TypeError):
                            pass  # Not JSON, treat as normal result

                        tool_results.append({
                            "toolUseId": tool_use_id,
                            "content": tool_result
                        })


                # Add tool results to messages only if there are results
                if tool_results:
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "toolResult": {
                                    "toolUseId": result["toolUseId"],
                                    "content": [{"text": result["content"]}]
                                }
                            }
                            for result in tool_results
                        ]
                    })
                else:
                    logger.warning("No tool results to add, but tool_use blocks were found")
            else:
                # Unexpected stop reason
                logger.warning(f"Unexpected stop reason: {response['stopReason']}")
                return {
                    "answer": f"Unexpected response from Claude: {response['stopReason']}",
                    "success": False
                }

    except Exception as e:
        logger.error(f"Error processing question: {e}", exc_info=True)
        # Remove the user message from history if it failed
        if conversation_history and conversation_history[-1]["role"] == "user":
            conversation_history.pop()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history")
async def get_history():
    """Get current conversation history."""
    return {"history": conversation_history}


@app.post("/api/clear-history")
async def clear_history():
    """Clear conversation history."""
    global conversation_history
    conversation_history = []
    return {"status": "cleared"}


@app.get("/")
async def get_homepage():
    """Serve the homepage."""
    return FileResponse("index.html")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "bedrock_configured": bool(bedrock_client),
        "aws_profile": AWS_PROFILE,
        "aws_region": AWS_REGION,
        "bedrock_model": BEDROCK_MODEL
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8500)
