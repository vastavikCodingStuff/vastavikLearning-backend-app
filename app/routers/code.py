import logging
import re
from typing import Dict, Any, List, Optional
import httpx
from fastapi import APIRouter, HTTPException, status, Depends

from app.core.config import settings
from app.core.circuit_breaker import require_route_enabled
from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user_optional
from app.models.schemas import (
    CodeExecutionRequest,
    CodeExecutionResponse,
    OcrCleanRequest,
    OcrCleanResponse,
)

logger = logging.getLogger("vastavik.code")
router = APIRouter(prefix="/api/v1", tags=["Online Judge & Code Execution"])

# Judge0 language IDs
LANGUAGE_IDS = {
    "java": 62,         # Java (OpenJDK 13/17)
    "python": 71,       # Python (3.8.1+)
    "cpp": 54,          # C++ (GCC 9.2.0)
    "c": 50,            # C (GCC 9.2.0)
    "javascript": 63,   # JavaScript (Node.js 12.14.0)
}


@router.post(
    "/code/execute",
    response_model=CodeExecutionResponse,
    dependencies=[Depends(require_route_enabled("code_execution")), Depends(rate_limit("code"))]
)
async def execute_code(
    request: CodeExecutionRequest,
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """
    Proxies code execution to Judge0 runner with a strict 10-second fail-fast timeout.
    Saves execution telemetry in Firestore for student history and admin analytics.
    """
    lang_key = request.language.lower().strip()
    language_id = LANGUAGE_IDS.get(lang_key)

    if not language_id:
        return CodeExecutionResponse(
            success=False,
            stderr=f"Unsupported language '{request.language}'. Supported languages: java, python, cpp, c, javascript.",
            status_description="Language Not Supported",
        )

    submission_payload = {
        "source_code": request.source_code,
        "language_id": language_id,
        "stdin": request.stdin or "",
        "cpu_time_limit": 5.0,
        "memory_limit": 128000,  # 128 MB max per execution
    }

    judge0_url = f"{settings.JUDGE0_URL.rstrip('/')}/submissions?base64_encoded=false&wait=true"
    headers = {"Content-Type": "application/json"}
    if settings.JUDGE0_AUTH_TOKEN:
        headers["X-Auth-Token"] = settings.JUDGE0_AUTH_TOKEN

    try:
        async with httpx.AsyncClient(timeout=settings.JUDGE0_TIMEOUT_SECONDS) as client:
            resp = await client.post(judge0_url, headers=headers, json=submission_payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                status_obj = data.get("status", {})
                status_id = status_obj.get("id", 0)
                status_desc = status_obj.get("description", "Executed")

                stdout = data.get("stdout")
                stderr = data.get("stderr") or data.get("compile_output")
                time_taken = f"{data.get('time', '0')}s"
                memory = data.get("memory")

                # Judge0 status 3 = Accepted
                success = (status_id == 3)

                # Persist execution log for admin dashboard & student telemetry
                try:
                    from app.db.firebase import db
                    from datetime import datetime, timezone
                    import uuid
                    exec_id = f"exec_{uuid.uuid4().hex[:10]}"
                    log_record = {
                        "id": exec_id,
                        "uid": current_user.get("sub") or current_user.get("uid") or "anonymous" if current_user else "anonymous",
                        "student_name": current_user.get("name", "Guest Student") if current_user else "Guest Student",
                        "language": lang_key,
                        "source_code": request.source_code[:4000],
                        "stdout": stdout or "",
                        "stderr": stderr or "",
                        "status_description": status_desc,
                        "execution_time": time_taken,
                        "memory_kb": memory or 0,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                    if db.use_live_firestore:
                        db._firestore_client.collection("code_executions").document(exec_id).set(log_record)
                except Exception as log_err:
                    logger.warning(f"Failed to log code execution: {log_err}")

                return CodeExecutionResponse(
                    success=success,
                    stdout=stdout,
                    stderr=stderr,
                    execution_time=time_taken,
                    memory_kb=memory,
                    status_description=status_desc,
                )
            else:
                logger.warning(f"Judge0 returned HTTP {resp.status_code}: {resp.text}")
                return CodeExecutionResponse(
                    success=False,
                    stderr="Code execution service reported an unexpected response.",
                    status_description="Runner Error",
                )
    except httpx.ConnectError:
        logger.error(f"Failed to connect to Judge0 VPS at {settings.JUDGE0_URL}")
        return CodeExecutionResponse(
            success=False,
            stderr="The isolated code execution sandbox is currently offline for scheduled maintenance. Your code remains saved.",
            status_description="EXECUTION_ENGINE_OFFLINE",
        )
    except httpx.TimeoutException:
        logger.error(f"Judge0 VPS timed out after {settings.JUDGE0_TIMEOUT_SECONDS}s")
        return CodeExecutionResponse(
            success=False,
            stderr="Execution timed out. Ensure your program does not contain infinite loops or unresolved input prompts.",
            status_description="Time Limit Exceeded",
        )
    except Exception as e:
        logger.error(f"Unexpected error executing code: {e}")
        return CodeExecutionResponse(
            success=False,
            stderr="An internal error occurred while communicating with the code runner.",
            status_description="Internal Error",
        )


@router.post("/code/clean-ocr", response_model=OcrCleanResponse)
async def clean_ocr_code(request: OcrCleanRequest):
    """
    Cleans up optical character recognition (OCR) glitches commonly produced
    when scanning handwritten or textbook code using mobile ML Kit.
    """
    raw = request.raw_ocr_text
    corrections: List[str] = []

    cleaned = raw

    # Replace smart quotes with straight ASCII quotes
    if "“" in cleaned or "”" in cleaned or "’" in cleaned or "‘" in cleaned:
        cleaned = cleaned.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
        corrections.append("Normalized smart typographical quotes to ASCII")

    # Fix common Java typos
    if request.language.lower() == "java":
        if "System.out.printin" in cleaned:
            cleaned = cleaned.replace("System.out.printin", "System.out.println")
            corrections.append("Fixed 'printin' typo to 'println'")
        if "String[]" not in cleaned and "String [" in cleaned:
            cleaned = re.sub(r'String\s+\[\s*\]', 'String[]', cleaned)
            corrections.append("Normalized array brackets")
        if "public static void main" in cleaned and "{" not in cleaned:
            cleaned += "\n}"
            corrections.append("Appended missing closing brace")

    # Fix Python indentation / colon typos
    if request.language.lower() == "python":
        # Replace semicolon at end of def/if/for with colon
        pattern = r'(def|if|elif|else|for|while)(.*);\s*$'
        if re.search(pattern, cleaned, re.MULTILINE):
            cleaned = re.sub(r'(def|if|elif|else|for|while)(.*);\s*$', r'\1\2:', cleaned, flags=re.MULTILINE)
            corrections.append("Replaced semicolon after statement header with colon")

    return OcrCleanResponse(cleaned_code=cleaned, corrections_applied=corrections)
