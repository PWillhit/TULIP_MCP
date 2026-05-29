import logging
import json
from typing import Dict, Any, Optional
from tulip_client import TulipApiClient
from tool_def_loader import ToolDefinitionLoader

logger = logging.getLogger(__name__)


class TulipToolExecutor:
    """Execute Tulip tools by routing tool calls to appropriate API endpoints."""

    def __init__(self, api_client: TulipApiClient, tool_loader: ToolDefinitionLoader):
        self.api_client = api_client
        self.tool_loader = tool_loader
        self.tools_by_name = {tool["name"]: tool for tool in tool_loader.get_all_tools()}

    def execute_tool(
        self, tool_name: str, input_args: Dict[str, Any], preview_mode: bool = False
    ) -> str:
        """
        Execute a tool by name with the given input arguments.

        Args:
            tool_name: Name of the tool to execute
            input_args: Input arguments for the tool
            preview_mode: If True, only show what would happen (not implemented for read-only)

        Returns:
            JSON string with tool results or error message
        """
        try:
            tool_def = self.tool_loader.get_tool_by_name(tool_name)
            if not tool_def:
                return self._error_response(f"Tool not found: {tool_name}")

            # Build the request from the tool definition and input
            endpoint, method, body, query_params = self._build_request(
                tool_def, input_args
            )

            logger.info(
                f"Executing tool: {tool_name} with method {method} on {endpoint}"
            )

            # Execute the API call
            result = self.api_client.make_request(
                endpoint=endpoint,
                method=method,
                body=body,
                query_params=query_params,
            )

            # Return result in Bedrock-compatible format
            return self._success_response(result)

        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
            return self._error_response(str(e))

    def _build_request(
        self, tool_def: Dict[str, Any], input_args: Dict[str, Any]
    ) -> tuple:
        """
        Build HTTP request components from tool definition and input.

        Returns:
            Tuple of (endpoint, method, body, query_params)
        """
        endpoint = tool_def.get("url", "")
        method = tool_def.get("httpType", "GET").upper()
        body = None
        query_params = {}

        # Get the input schema to understand parameter types
        input_schema = tool_def.get("inputSchema", {})
        properties = input_schema.get("properties", {})

        # Separate path params, query params, and body params
        path_params = {}
        body_params = {}

        for param_name, param_value in input_args.items():
            if param_name not in properties:
                logger.warning(
                    f"Unknown parameter '{param_name}' for tool {tool_def.get('name')}"
                )
                continue

            # Check if this is a path parameter (referenced in the URL with :paramName)
            if f":{param_name}" in endpoint:
                path_params[param_name] = param_value
            else:
                # Determine if it should go in query params or body
                if method == "GET":
                    # GET requests use query params
                    query_params[param_name] = param_value
                else:
                    # POST/PUT/PATCH requests use body
                    body_params[param_name] = param_value

        # Interpolate path parameters into the endpoint
        for param_name, param_value in path_params.items():
            endpoint = endpoint.replace(f":{param_name}", str(param_value))

        # Set request body if needed
        if body_params and method in ["POST", "PUT", "PATCH"]:
            body = body_params

        # Handle special case: some POST endpoints might need path params in body
        if (
            tool_def.get("includePathParamsInBody")
            and method in ["POST", "PUT", "PATCH"]
        ):
            if not body:
                body = {}
            body.update(path_params)

        return endpoint, method, body, query_params if query_params else None

    def _success_response(self, result: Any) -> str:
        """Format successful tool result for Bedrock."""
        try:
            # If result is already a dict/list, convert to JSON string
            if isinstance(result, (dict, list)):
                result_str = json.dumps(result)
            else:
                result_str = str(result)
            return result_str
        except Exception as e:
            logger.error(f"Error formatting tool result: {e}")
            return json.dumps({"error": "Failed to format result", "details": str(e)})

    def _error_response(self, error_message: str) -> str:
        """Format error response for Bedrock."""
        return json.dumps({"error": error_message})
