#!/usr/bin/env python3
import os
import json
import asyncio
import argparse
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv
import anthropic
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

load_dotenv()

# ---------- Utilities to adapt MCP <-> Anthropic tool schemas ----------

def mcp_tool_to_anthropic(tool: types.Tool) -> Dict[str, Any]:
    """Convert MCP tool schema to Anthropic tool schema."""
    # MCP tools already expose JSON Schema via tool.inputSchema
    schema = tool.inputSchema or {"type": "object", "properties": {}, "additionalProperties": True}
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": schema
    }

async def list_mcp_tools(session: ClientSession) -> List[types.Tool]:
    resp = await session.list_tools()
    return list(resp.tools)

async def call_mcp_tool(session: ClientSession, name: str, arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any] | None]:
    """
    Call an MCP tool and return (unstructured_text, structured_json).
    The MCP Python SDK may return both text content and structuredContent.
    """
    result = await session.call_tool(name, arguments=arguments)
    # Prefer structuredContent if present; also surface text if any
    text_parts = []
    for c in (result.content or []):
        if isinstance(c, types.TextContent):
            text_parts.append(c.text)
    unstructured_text = "\n".join(text_parts).strip()
    structured = result.structuredContent if result.structuredContent is not None else None
    return unstructured_text, structured

# ---------- Anthropic <-> MCP round-trip loop ----------

async def run_cli(prompt: str, headless: bool, caps: List[str], extra_args: List[str]) -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Please set ANTHROPIC_API_KEY in your environment.")

    client = anthropic.Anthropic(api_key=api_key)

    # Launch Playwright MCP via STDIO
    args = ["@playwright/mcp@latest"]
    if headless:
        args.append("--headless")
    if caps:
        args += ["--caps", ",".join(caps)]
    args += extra_args  # pass-through for any advanced flags you want

    server_params = StdioServerParameters(
        command="npx",
        args=args,
        env=os.environ.copy()
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as mcp_session:
            await mcp_session.initialize()

            # Discover all MCP tools and expose them to Claude
            mcp_tools = await list_mcp_tools(mcp_session)
            anth_tools = [mcp_tool_to_anthropic(t) for t in mcp_tools]

            # Start an Anthropic conversation where Claude can use those tools
            # Model choice can be changed as needed
            model = "claude-3-7-sonnet-20250219"

            messages: List[Dict[str, Any]] = [
                {"role": "user", "content": prompt}
            ]

            # First request with tools available
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                tools=anth_tools,
                messages=messages
            )

            # If Claude wants to use tools, we fulfill them until Claude is done
            while True:
                # Collect tool uses in the last assistant message (if any)
                last_msg = response
                tool_uses = []
                for block in last_msg.content:
                    if block.type == "tool_use":
                        tool_uses.append(block)

                if not tool_uses:
                    # No more tool calls — print final text and exit
                    final_text = []
                    for block in last_msg.content:
                        if block.type == "text":
                            final_text.append(block.text)
                    print("\n=== Assistant ===\n" + ("\n".join(final_text) if final_text else "(no text)"))
                    break

                # Execute tool calls via MCP
                tool_results_blocks = []
                for tu in tool_uses:
                    name = tu.name
                    args = tu.input or {}
                    try:
                        text_out, structured = await call_mcp_tool(mcp_session, name, args)
                        # Compose Anthropic tool_result block
                        content_blocks = []
                        if text_out:
                            content_blocks.append({"type": "text", "text": text_out})
                        if structured is not None:
                            content_blocks.append({"type": "json", "json": structured})
                        tool_results_blocks.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": content_blocks
                        })
                    except Exception as e:
                        tool_results_blocks.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "is_error": True,
                            "content": [{"type": "text", "text": f"Tool error: {e}"}]
                        })

                # Send a follow-up message with tool results so Claude can continue
                messages.append({
                    "role": "assistant",
                    "content": last_msg.content
                })
                messages.append({
                    "role": "user",
                    "content": tool_results_blocks
                })

                response = client.messages.create(
                    model=model,
                    max_tokens=1024,
                    tools=anth_tools,
                    messages=messages
                )

# ---------- Entry point ----------

def main():
    parser = argparse.ArgumentParser(description="Bridge Anthropic <-> Playwright MCP (STDIO).")
    parser.add_argument("prompt", help="Initial instruction for Claude (e.g. 'Open example.com and take a screenshot.')")
    parser.add_argument("--headed", action="store_true", help="Run browser headed (default: headless).")
    parser.add_argument("--caps", default="", help="Comma-separated extra caps, e.g. 'pdf,vision'")
    parser.add_argument("--", dest="extra", nargs=argparse.REMAINDER,
                        help="Pass-through flags to @playwright/mcp (e.g. --allowed-origins, --viewport-size).")
    args = parser.parse_args()

    caps = [c.strip() for c in args.caps.split(",") if c.strip()]
    extra = args.extra or []
    asyncio.run(run_cli(args.prompt, headless=not args.headed, caps=caps, extra_args=extra))

if __name__ == "__main__":
    main()
