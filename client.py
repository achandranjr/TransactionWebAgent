import asyncio
import json
from typing import Dict, List, Any, Optional
import logging
from anthropic import Anthropic
import sys
from dotenv import load_dotenv
import os

load_dotenv()
# Set up logging with more detail
logging.basicConfig(
    filename="client.log",
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DebugStdioMCPClient:
    """Stdio-based MCP client with extensive debugging"""
    
    def __init__(self, browser_args: List[str] = None):
        self.process = None
        self.message_id = 0
        self.browser_args = browser_args or []
        
    async def __aenter__(self):
        # Start the MCP server as subprocess
        cmd = ["npx", "@playwright/mcp@latest"] + self.browser_args
        logger.info(f"Starting MCP server with command: {' '.join(cmd)}")
        
        # Inherit environment so DISPLAY is available for headed browser
        env = os.environ.copy()
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        
        # Start stderr reader
        asyncio.create_task(self._read_stderr())
        
        # Give the server time to start
        await asyncio.sleep(2)
        
        # Initialize
        await self._initialize()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.process:
            logger.info("Terminating MCP server...")
            self.process.terminate()
            await self.process.wait()
    
    async def _read_stderr(self):
        """Read stderr for debugging"""
        try:
            while self.process and self.process.stderr:
                line = await self.process.stderr.readline()
                if line:
                    logger.info(f"MCP STDERR: {line.decode().strip()}")
                else:
                    break
        except Exception as e:
            logger.error(f"Error reading stderr: {e}")
    
    async def _send_request(self, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send request via stdio with debugging"""
        self.message_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.message_id,
            "method": method,
            "params": params or {}
        }
        
        # Send as single line
        request_line = json.dumps(request) + "\n"
        logger.debug(f"Sending request: {request_line.strip()}")
        
        self.process.stdin.write(request_line.encode())
        await self.process.stdin.drain()
        
        # Read response with timeout and debugging
        try:
            logger.debug("Waiting for response...")
            response_line = await asyncio.wait_for(
                self.process.stdout.readline(), 
                timeout=30.0
            )
            
            if not response_line:
                raise Exception("Empty response from MCP server")
                
            response_text = response_line.decode().strip()
            logger.debug(f"Raw response: {response_text[:500]}...")
            
            response = json.loads(response_text)
            
            if "error" in response:
                logger.error(f"MCP Error: {response['error']}")
                raise Exception(f"MCP Error: {response['error']}")
            
            logger.debug(f"Parsed response: {json.dumps(response, indent=2)[:500]}...")
            return response.get("result", {})
            
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for MCP response")
            # Check if process is still alive
            if self.process.returncode is not None:
                logger.error(f"MCP process died with code: {self.process.returncode}")
            raise Exception("Timeout waiting for MCP response")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.error(f"Raw response was: {response_line}")
            raise
    
    async def _initialize(self):
        logger.info("Initializing MCP connection...")
        init_request = {
            "jsonrpc": "2.0",
            "id": self.next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "claude-playwright-agent", "version": "1.0.0"}
            }
        }

        # Use _send_request to send init and get response
        response = await self._send_request("initialize", init_request["params"])
        logger.debug(f"Initialize response: {response}")

        # 🔑 Immediately send notifications/initialized
        init_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }
        notif_line = json.dumps(init_notification) + "\n"
        self.process.stdin.write(notif_line.encode())
        await self.process.stdin.drain()
        logger.info("Sent notifications/initialized")

        return response

    def next_id(self) -> int:
        self.message_id += 1
        return self.message_id

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Get the list of available tools"""
        logger.debug("Listing tools...")
        response = await self._send_request("tools/list")
        tools = response.get("tools", [])
        logger.info(f"Found {len(tools)} tools")
        return tools
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a specific tool"""
        logger.info(f"Calling tool: {tool_name} with args: {arguments}")
        response = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })
        
        # Extract content from response
        content = response.get("content", [])
        if isinstance(content, list) and content:
            result = content[0].get("text", content[0])
            logger.debug(f"Tool result preview: {str(result)[:200]}...")
            return result
        return content


class ClaudePlaywrightAgent:
    """Claude agent for browser automation with debugging"""
    
    def __init__(self, anthropic_api_key: str):
        self.client = Anthropic(api_key=anthropic_api_key)
        
    async def browse(self, task: str, browser_args: List[str] = None) -> str:
        """Execute a browsing task with debugging"""
        
        system_prompt = """You are a helpful assistant that can control web browsers.
        Use the available tools to navigate websites, interact with elements, and gather information.
        Always start by using browser_navigate to go to the URL, then use browser_snapshot to see the page structure.
        Be explicit about what you're doing at each step."""
        
        async with DebugStdioMCPClient(browser_args) as mcp:
            # Get available tools
            tools = await mcp.list_tools()
            
            # Convert to Claude format
            claude_tools = []
            for tool in tools:
                claude_tool = {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {
                        "type": "object",
                        "properties": {},
                        "required": []
                    })
                }
                claude_tools.append(claude_tool)
            
            logger.info(f"Available tools: {[t['name'] for t in claude_tools]}")
            
            # Start conversation
            messages = [{"role": "user", "content": task}]
            max_iterations = 30  # Prevent infinite loops
            iteration = 0
            
            while iteration < max_iterations:
                iteration += 1
                logger.info(f"Claude iteration {iteration}")
                
                # Get Claude's response
                response = self.client.messages.create(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=2048,
                    system=system_prompt,
                    messages=messages,
                    tools=claude_tools
                )
                
                # Process response
                text_content = ""
                tool_calls = []
                
                for block in response.content:
                    if block.type == "text":
                        text_content += block.text
                        logger.debug(f"Claude text: {text_content}")
                    elif block.type == "tool_use":
                        tool_calls.append(block)
                
                # Add assistant response to messages
                messages.append({"role": "assistant", "content": response.content})
                
                # If no tool calls, we're done
                if not tool_calls:
                    logger.info("No more tool calls, finishing...")
                    return text_content
                
                # Execute tool calls
                for tool_call in tool_calls:
                    logger.info(f"Executing: {tool_call.name}")
                    
                    try:
                        result = await mcp.call_tool(tool_call.name, tool_call.input)
                        tool_result = {
                            "type": "tool_result",
                            "tool_use_id": tool_call.id,
                            "content": str(result)
                        }
                    except Exception as e:
                        logger.error(f"Tool execution error: {e}", exc_info=True)
                        tool_result = {
                            "type": "tool_result",
                            "tool_use_id": tool_call.id,
                            "content": f"Error: {str(e)}",
                            "is_error": True
                        }
                    
                    messages.append({"role": "user", "content": [tool_result]})
            
            logger.warning(f"Reached max iterations ({max_iterations})")
            return text_content


# Test function to verify MCP communication
async def test_mcp_communication():
    """Test basic MCP communication"""
    logger.info("Testing MCP communication...")
    
    async with DebugStdioMCPClient() as mcp:
        # Test 1: List tools
        logger.info("Test 1: Listing tools")
        tools = await mcp.list_tools()
        logger.info(f"Successfully got {len(tools)} tools")
        
        # Test 2: Navigate
        logger.info("Test 2: Navigate to example.com")
        try:
            result = await mcp.call_tool("browser_navigate", {
                "url": "https://example.com"
            })
            logger.info(f"Navigation result: {result}")
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
        
        # Test 3: Snapshot
        logger.info("Test 3: Taking snapshot")
        try:
            result = await mcp.call_tool("browser_snapshot", {})
            logger.info(f"Snapshot length: {len(str(result))}")
        except Exception as e:
            logger.error(f"Snapshot failed: {e}")
        
        # Test 4: Close browser
        logger.info("Test 4: Closing browser")
        try:
            result = await mcp.call_tool("browser_close", {})
            logger.info("Browser closed successfully")
        except Exception as e:
            logger.error(f"Close failed: {e}")


# Example usage
async def main():
    # First run the test to ensure MCP is working
    print("=== Testing MCP Communication ===")
    logger.info("Getting tools")
    try:
        
        await test_mcp_communication()
        print("\nMCP test successful!\n")
    except Exception as e:
        print(f"\nMCP test failed: {e}\n")
        return
    
    # Now try with Claude
    agent = ClaudePlaywrightAgent(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY")
    )
    
    # Use the same browser args you were using
    browser_args = [
        "--browser=firefox",
        "--user-data-dir=/app/test-profile-2"
    ]
    
    # Example 1: Simple navigation
    print("=== Example 1: Simple Navigation ===")
    try:
        result = await agent.browse(
            """Search for poggers :D""",
            browser_args
        )
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")
        logger.error("Full error:", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())