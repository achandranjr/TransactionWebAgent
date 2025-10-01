from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Dict, Any
import logging
import os
import json
from datetime import datetime
from dotenv import load_dotenv
import uvicorn
import shutil
from bitwarden_sdk import BitwardenClient, DeviceType, ClientSettings
# Import your existing client
from client import ClaudePlaywrightAgent, DebugStdioMCPClient

load_dotenv()

# Configure logging
logging.basicConfig(
    filename="client.log",
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logger.info("POGGERS")
# Create FastAPI app
app = FastAPI(
    title="Claude Playwright Agent API",
    description="Automated Receipt & Refund Processing",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Browser configuration
PROFILE_DIR = "/app/browser-profiles/test-profile-3"
BROWSER_ARGS = [
    "--browser=firefox",
    f"--user-data-dir={PROFILE_DIR}"
]

# In-memory state for a persistent verification session
verification_client: DebugStdioMCPClient | None = None

# Request/Response Models
class TransactionRequest(BaseModel):
    transactionId: str
    clientEmail: EmailStr

class RefundRequest(BaseModel):
    transactionId: str
    refundAmount: float = Field(..., gt=0, le=99999.99, description="Refund amount in USD")

class StatusResponse(BaseModel):
    status: str
    bitwarden_connected: bool
    timestamp: str

class OperationResponse(BaseModel):
    success: bool
    message: str
    transaction_id: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None

class ErrorResponse(BaseModel):
    success: bool = False
    error: str

# Credential Manager
class CredentialManager:
    """Simple credential manager using environment variables"""
    
    def __init__(self):
        self.is_connected = False
        
    def get_credentials(self, secret_id):
        try:
            secrets = self.client.secrets().get_by_ids([secret_id])
            
            if not secrets:
                raise ValueError(f"No secret found with ID: {secret_id}")
            
            secret = json.loads(secrets.data.data[0].value)
            logger.info(f"Secret retrieved: {secret}")
            
            return secret
            
        except Exception as e:
            logger.error(f"Error retrieving credentials from Bitwarden: {e}")
    
    def connect(self) -> Dict[str, Any]:
        try:
            client_settings = ClientSettings(
                    api_url="https://api.bitwarden.com",
                    identity_url="https://identity.bitwarden.com",
                    device_type=DeviceType.SDK,
                    user_agent="bitwarden-sdk/python"
                )
                
            self.client = BitwardenClient(client_settings)

            self.client.auth().login_access_token(os.getenv("ACCESS_TOKEN"))
            self.is_connected = True
            return {
                "success": True,
                "message": "Using bitwarden credentials"
            }
        except Exception as e:
            logger.error(f"Bitwarden connection failed: {e}")
            return {
                "success": False,
                "message": f"Bitwarden connection failed: {e}"
            }

# Initialize credential manager
credential_manager = CredentialManager()

# Routes
@app.get("/")
async def serve_split_interface():
    """Serve the split-screen interface without auto-launching a browser.
    The browser will be launched on demand (e.g., for verification or task runs)."""
    # Do not overwrite cookies here; the profile directory is persistent.

    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    elif os.path.exists("split-interface.html"):
        return FileResponse("split-interface.html")
    else:
        raise HTTPException(status_code=404, detail="Split interface not found")

@app.get("/dashboard")
async def serve_dashboard():
    """Serve the dashboard"""
    if os.path.exists("static/dashboard.html"):
        return FileResponse("static/dashboard.html")
    elif os.path.exists("dashboard.html"):
        return FileResponse("dashboard.html")
    else:
        raise HTTPException(status_code=404, detail="Dashboard not found")

@app.get("/api/status", response_model=StatusResponse)
async def get_status():
    """Check backend and credential status"""
    return StatusResponse(
        status="running",
        bitwarden_connected=credential_manager.is_connected,
        timestamp=datetime.now().isoformat()
    )

@app.post("/api/bitwarden/connect")
async def connect_bitwarden():
    """Connect to credential manager (mock for env vars)"""
    result = credential_manager.connect()
    return JSONResponse(content=result)

@app.post("/api/verification/start")
async def start_verification():
    """Start a persistent browser session for manual device verification.
    This launches the Playwright MCP server with the persistent profile and opens the gateway URL.
    The session stays alive until /api/verification/finish is called."""
    global verification_client
    try:
        # If already running, stop existing session first
        if verification_client is not None:
            try:
                await verification_client.call_tool("browser_close", {})
            except Exception:
                pass
            try:
                await verification_client.__aexit__(None, None, None)
            except Exception:
                pass
            verification_client = None

        # Ensure profile dir exists
        os.makedirs(PROFILE_DIR, exist_ok=True)

        # Start a new persistent MCP client and navigate to the gateway
        verification_client = DebugStdioMCPClient(browser_args=BROWSER_ARGS)
        await verification_client.__aenter__()

        # Ensure the selected browser is installed
        try:
            await verification_client.call_tool("browser_install", {})
        except Exception as e:
            logger.warning(f"browser_install failed or was unnecessary: {e}")

        # Best-effort navigate to the transaction gateway
        try:
            await verification_client.call_tool("browser_navigate", {
                "url": "https://zero5.transactiongateway.com/merchants/"
            })
        except Exception as e:
            logger.warning(f"Failed initial navigate, session still started: {e}")

        return JSONResponse(content={
            "success": True,
            "message": "Verification session started. Use the VNC panel to complete the device check.",
            "profile_dir": PROFILE_DIR
        })
    except Exception as e:
        logger.error(f"Failed to start verification session: {e}", exc_info=True)
        return JSONResponse(content={"success": False, "message": str(e)}, status_code=500)

@app.post("/api/verification/finish")
async def finish_verification():
    """Finish the verification session and persist cookies (profile is already persistent).
    This will close the browser session and keep the updated cookies in the profile directory."""
    global verification_client
    try:
        if verification_client is None:
            return JSONResponse(content={
                "success": True,
                "message": "No active verification session. Nothing to do."
            })

        # Close the browser gracefully
        try:
            await verification_client.call_tool("browser_close", {})
        except Exception:
            pass
        finally:
            try:
                await verification_client.__aexit__(None, None, None)
            except Exception:
                pass
            verification_client = None

        # Do not copy/overwrite cookies; the profile dir is persistent and already updated
        return JSONResponse(content={
            "success": True,
            "message": "Verification finished. Updated cookies are saved in the persistent profile."
        })
    except Exception as e:
        logger.error(f"Failed to finish verification session: {e}", exc_info=True)
        return JSONResponse(content={"success": False, "message": str(e)}, status_code=500)

@app.post("/api/send_receipt", response_model=OperationResponse)
async def send_receipt(request: TransactionRequest):
    """Process send receipt request"""
    try:
        # Get credentials
        credentials = credential_manager.get_credentials(os.getenv("ZERO5_SECRET_ID"))
        
        # Create the task prompt
        task = f"""Go to zero5.transactiongateway.com/merchants/, 
        log in using username: {credentials.get("username")}, password: {credentials.get("password")}, 
        If an alert appears about a test account, close it,
        search for the transaction ID {request.transactionId},
        click on the id of the result, click email receipt, 
        wait for the tab to load, use {request.clientEmail} as the client's email 
        to send the receipt. After you send the receipt, return the response "receipt sent to {request.clientEmail}." 
        After closing the session, the next message must contain exactly 0 tool calls."""
        
        # Initialize Claude agent
        agent = ClaudePlaywrightAgent(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY")
        )
        
        # Execute the task
        logger.info(f"Processing receipt for transaction {request.transactionId}")
        result = await agent.browse(task, BROWSER_ARGS)
        
        # Log success
        logger.info(f"Successfully sent receipt to {request.clientEmail}")
        
        return OperationResponse(
            success=True,
            message=f"Receipt sent successfully to {request.clientEmail}",
            transaction_id=request.transactionId,
            result=result
        )
        
    except Exception as e:
        logger.error(f"Error processing receipt: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@app.post("/api/give_refund", response_model=OperationResponse)
async def give_refund(request: RefundRequest):
    """Process send receipt request"""
    try:
        # Get credentials
        credentials = credential_manager.get_credentials(os.getenv("ZERO5_SECRET_ID"))
        
        # Create the task prompt
        task = f"""Go to zero5.transactiongateway.com/merchants/, 
        log in using username: {credentials.get("username")}, password: {credentials.get("password")}, 
        If an alert appears about a test account, close it,
        click on the credit card icon in the top left
        click on the refund option
        input the transaction ID {request.transactionId}, then click on the field to input the refund
        after clicking on the amount field, triple click in the field (or just select/highlight all the numbers in the field) and input the refund amount {request.refundAmount:.2f}
        press refund
        close the session
        After closing the session, the next message must contain exactly 0 tool calls."""
        
        # Initialize Claude agent
        agent = ClaudePlaywrightAgent(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY")
        )
        
        # Execute the task
        logger.info(f"Processing receipt for transaction {request.transactionId}")
        result = await agent.browse(task, BROWSER_ARGS)
        
        # Log success
        logger.info(f"Refund for transaction: {request.transactionId} for amount {request.refundAmount:.2f} successful")
        
        return OperationResponse(
            success=True,
            message=f"Refund for transaction: {request.transactionId} for amount {request.refundAmount:.2f} successful",
            transaction_id=request.transactionId,
            result=result
        )
        
    except Exception as e:
        logger.error(f"Error processing receipt: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@app.get("/api/logs")
async def get_logs():
    """Get recent logs for debugging"""
    try:
        log_file = "client.log"
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                lines = f.readlines()
                return {
                    "success": True,
                    "logs": lines
                }
        else:
            return {
                "success": True,
                "logs": ["No log file found"]
            }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

# Exception handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error=exc.detail).dict()
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error="Internal server error").dict()
    )

# Health check endpoint
@app.get("/health")
async def health_check():
    """Simple health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    # Check for required environment variables
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.warning("ANTHROPIC_API_KEY not found in environment variables!")
    
    # Log startup information
    logger.info(f"Starting Claude Playwright Agent API...")
    
    # Run with uvicorn
    uvicorn.run(
        "main:app", 
        host="0.0.0.0",
        port=5000,
        reload=True,  # Enable auto-reload during development
        log_level="info"
    )