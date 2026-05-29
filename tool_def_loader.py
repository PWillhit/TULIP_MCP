import os
import json
import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)


class ToolDefinitionLoader:
    """Load and manage Tulip tool definitions from JSON files."""

    def __init__(self, definitions_dir: str):
        self.definitions_dir = definitions_dir
        self.tools = {}
        self.all_tools = []
        self._load_definitions()

    def _load_definitions(self) -> None:
        """Load all tool definitions from JSON files in the definitions directory."""
        if not os.path.isdir(self.definitions_dir):
            logger.error(f"Definitions directory not found: {self.definitions_dir}")
            return

        # Find all JSON files (except index.js if it exists)
        for filename in sorted(os.listdir(self.definitions_dir)):
            if filename.endswith(".json"):
                filepath = os.path.join(self.definitions_dir, filename)
                try:
                    with open(filepath, "r") as f:
                        tool_def = json.load(f)

                    # Validate tool definition
                    if self._validate_tool_schema(tool_def):
                        tool_name = tool_def.get("name")
                        self.tools[tool_name] = tool_def
                        self.all_tools.append(tool_def)
                        logger.debug(f"Loaded tool: {tool_name}")
                    else:
                        logger.warning(f"Invalid tool definition: {filepath}")
                except Exception as e:
                    logger.error(f"Failed to load tool definition {filepath}: {e}")

        logger.info(f"Loaded {len(self.all_tools)} tool definitions")

    def _validate_tool_schema(self, tool_def: Dict[str, Any]) -> bool:
        """Validate that a tool definition has required fields."""
        required_fields = ["name", "description", "inputSchema"]
        for field in required_fields:
            if field not in tool_def:
                logger.debug(f"Missing required field '{field}' in tool definition")
                return False

        # Validate inputSchema structure
        input_schema = tool_def.get("inputSchema", {})
        if not isinstance(input_schema, dict):
            logger.debug("inputSchema must be a dictionary")
            return False

        return True

    def get_all_tools(self) -> List[Dict[str, Any]]:
        """Get all loaded tool definitions."""
        return self.all_tools

    def get_tool_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a specific tool definition by name."""
        return self.tools.get(name)

    def get_tools_by_category(self, category: str) -> List[Dict[str, Any]]:
        """
        Get tool definitions filtered by category.

        Args:
            category: One of "read-only", "write", "admin"

        Returns:
            List of tool definitions matching the category
        """
        return [
            tool
            for tool in self.all_tools
            if tool.get("category") == category
        ]

    def get_tools_by_type(self, tool_type: str) -> List[Dict[str, Any]]:
        """
        Get tool definitions filtered by type.

        Args:
            tool_type: One of "table", "station", "interface", "user", etc.

        Returns:
            List of tool definitions matching the type
        """
        return [
            tool for tool in self.all_tools
            if tool.get("type") == tool_type
        ]

    def get_available_categories(self) -> List[str]:
        """Get list of all available categories in the loaded tools."""
        categories = set()
        for tool in self.all_tools:
            if "category" in tool:
                categories.add(tool["category"])
        return sorted(list(categories))

    def get_available_types(self) -> List[str]:
        """Get list of all available types in the loaded tools."""
        types = set()
        for tool in self.all_tools:
            if "type" in tool:
                types.add(tool["type"])
        return sorted(list(types))
