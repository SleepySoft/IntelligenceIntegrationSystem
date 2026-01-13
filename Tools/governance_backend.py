import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, Dict, List
import os

# Import the core logic from the previous step
from governance_core import GovernanceManager, TaskType, Status

app = FastAPI(title="Crawler Governance System")

# Initialize the Core Manager (Singleton for this process)
# In a production environment, this manages the DB connection pool
governer = GovernanceManager(spider_name="master_node")

# Mount templates and static files
templates = Jinja2Templates(directory="templates")


# --- Pydantic Models for RPC Data ---

class TaskRegisterRequest(BaseModel):
    spider: str
    group: str
    url: str
    interval: int


class CrawlCheckRequest(BaseModel):
    url: str
    spider: str
    task_type: str  # LIST or ARTICLE


class CrawlReportRequest(BaseModel):
    spider: str
    group: str
    url: str
    task_type: str
    status: int
    http_code: int = 0
    duration: float = 0.0
    error_msg: Optional[str] = None
    # Note: Content upload would typically be a multipart upload,
    # simplified here as just reporting the path or omitted for RPC demo.


# --- 1. View & Monitor Endpoints (For UI) ---

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/dashboard/stats")
async def get_dashboard_stats():
    """Aggregates high-level statistics directly via the DB handler."""
    db = governer.db

    # Active spiders (last seen in 5 mins - theoretical implementation)
    # Here we just count unique spiders in logs
    spiders = db.fetch_one("SELECT count(DISTINCT spider) as cnt FROM crawl_log")

    # Success Rate (Last 1000 items)
    success = db.fetch_one("""
        SELECT 
            avg(CASE WHEN status=1 THEN 1 ELSE 0 END) as rate,
            count(*) as total
        FROM crawl_log ORDER BY id DESC LIMIT 1000
    """)

    # Network Errors (Temp Fail) today
    net_errors = db.fetch_one("""
        SELECT count(*) as cnt FROM crawl_log 
        WHERE status=2 AND created_at > date('now')
    """)

    return {
        "active_spiders": spiders['cnt'],
        "success_rate": round((success['rate'] or 0) * 100, 1),
        "total_requests": success['total'],
        "network_errors": net_errors['cnt']
    }


@app.get("/api/tasks")
async def get_tasks():
    """Fetch recurrent task registry."""
    cur = governer.db.conn.cursor()
    cur.execute("SELECT * FROM task_registry ORDER BY next_run ASC")
    tasks = [dict(row) for row in cur.fetchall()]
    return tasks


@app.get("/api/logs")
async def get_logs(limit: int = 100, status: Optional[int] = None):
    """Fetch streaming logs for the 'Waterfall' view."""
    query = "SELECT * FROM crawl_log"
    params = []

    if status is not None:
        query += " WHERE status = ?"
        params.append(status)

    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    cur = governer.db.conn.cursor()
    cur.execute(query, tuple(params))
    return [dict(row) for row in cur.fetchall()]


@app.get("/api/snapshot/{file_hash}")
async def get_snapshot(file_hash: str):
    """Serve the local HTML snapshot content."""
    # Security check: verify hash format to prevent directory traversal
    if not file_hash.isalnum():
        raise HTTPException(status_code=400, detail="Invalid hash")

    # In a real app, we need to find which folder it is in.
    # For optimization, we might store relative path in DB.
    # Here we search for demo purposes:
    import glob
    files = glob.glob(f"data/files/*/*/{file_hash}*")
    if not files:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return FileResponse(files[0])


# --- 2. RPC Endpoints (For External Spider Processes) ---
# These endpoints allow external python scripts to use the governance logic
# without touching the DB file directly.

@app.post("/rpc/register_task")
async def rpc_register_task(req: TaskRegisterRequest):
    governer.spider_name = req.spider  # Context switch
    governer.register_task(req.url, req.group, req.interval)
    return {"status": "ok"}


@app.post("/rpc/should_crawl")
async def rpc_should_crawl(req: CrawlCheckRequest):
    """
    External process asks: 'Should I crawl this?'
    Manager checks DB logic (intervals, retries, etc.)
    """
    t_type = TaskType.LIST if req.task_type == "LIST" else TaskType.ARTICLE
    governer.spider_name = req.spider
    result = governer.should_crawl(req.url, t_type)
    return {"should_crawl": result}


@app.post("/rpc/report_result")
async def rpc_report_result(req: CrawlReportRequest):
    """
    External process reports: 'I finished this task.'
    Manager writes to DB and handles retry logic.
    """
    t_type = TaskType.LIST if req.task_type == "LIST" else TaskType.ARTICLE

    # We manually commit using the internal method for RPC support
    # In a real RPC system, we might pass a transaction ID, but here we report atomic results
    governer._commit_transaction(
        task_type=t_type,
        spider=req.spider,
        group=req.group,
        url=req.url,
        status=Status(req.status),
        duration=req.duration,
        http_code=req.http_code,
        error_msg=req.error_msg,
        content_path=None  # File upload not implemented in simple RPC demo
    )
    return {"status": "acked"}


if __name__ == "__main__":
    # Ensure templates directory exists
    if not os.path.exists("templates"):
        os.makedirs("templates")

    print("Starting Governance API Server on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
