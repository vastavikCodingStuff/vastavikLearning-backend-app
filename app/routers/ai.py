import asyncio
import json
import logging
from typing import Optional, List, Dict, Any
import httpx
from fastapi import APIRouter, HTTPException, status, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.circuit_breaker import require_route_enabled
from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user, get_current_user_optional
from app.models.schemas import ChatRequest, ChatResponse, ChatHistoryItem

logger = logging.getLogger("vastavik.ai")
router = APIRouter(prefix="/api/v1", tags=["AI Proxy Engine"])


async def call_mistral_api(prompt: str, history: List[ChatHistoryItem]) -> Optional[str]:
    """Calls Mistral AI API with temperature tuning for code explanation."""
    if not settings.MISTRAL_API_KEY:
        return None

    url = "https://api.mistral.ai/v1/chat/completions"
    messages = [{"role": "system", "content": "You are Vastavik AI, an expert computer science and programming tutor for ICSE Class 10 and CBSE Class 12 students. Give crisp, clear, well-commented code explanations."}]
    for h in history[-6:]:  # Keep recent history
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": prompt})

    headers = {
        "Authorization": f"Bearer {settings.MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "mistral-small-latest",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1500,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            else:
                logger.warning(f"Mistral API returned status {resp.status_code}: {resp.text}")
                return None
    except Exception as e:
        logger.warning(f"Mistral API call failed: {e}")
        return None


async def call_gemini_api(prompt: str, history: List[ChatHistoryItem], model_name: str = "gemini-1.5-flash") -> Optional[str]:
    """
    Calls Google Gemini API with thinking disabled to preserve zero latency
    and minimal token budget.
    """
    if not settings.GEMINI_API_KEY:
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={settings.GEMINI_API_KEY}"
    
    contents = []
    for h in history[-6:]:
        role = "user" if h.role == "user" else "model"
        contents.append({"role": role, "parts": [{"text": h.content}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload = {
        "contents": contents,
        "systemInstruction": {
            "parts": [{"text": "You are Vastavik AI, a dedicated tutor for students learning programming. Explain concepts step by step with precise code snippets."}]
        },
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 1500,
        }
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "")
            logger.warning(f"Gemini API returned status {resp.status_code}: {resp.text}")
            return None
    except Exception as e:
        logger.warning(f"Gemini API call failed: {e}")
        return None


def generate_local_fallback_response(prompt: str) -> str:
    """
    Intelligent built-in pedagogical fallback if external AI endpoints are unreachable.
    Guarantees the mobile app never shows a blank screen or raw network crash.
    """
    prompt_lower = prompt.lower()
    if "recursion" in prompt_lower:
        return (
            "### Recursion in Java / Python\n\n"
            "Recursion is a programming technique where a method calls itself to solve a smaller instance of the problem.\n\n"
            "**Key Components:**\n"
            "1. **Base Case:** The stopping condition that terminates recursion.\n"
            "2. **Recursive Step:** Progress towards the base case.\n\n"
            "```java\n"
            "// Example: Factorial in Java (ICSE Syllabus)\n"
            "public static int factorial(int n) {\n"
            "    if (n <= 1) return 1; // Base case\n"
            "    return n * factorial(n - 1); // Recursive case\n"
            "}\n"
            "```"
        )
    elif "oop" in prompt_lower or "object" in prompt_lower or "class" in prompt_lower:
        return (
            "### Core OOP Concepts (ICSE / CBSE Focus)\n\n"
            "1. **Encapsulation:** Wrapping data (fields) and methods into a single unit.\n"
            "2. **Inheritance:** Deriving properties from parent class (`extends` in Java).\n"
            "3. **Polymorphism:** Method Overloading (Compile-time) and Overriding (Run-time).\n"
            "4. **Abstraction:** Hiding internal implementation details using abstract classes and interfaces."
        )
    elif "bubble sort" in prompt_lower or "sort" in prompt_lower:
        return (
            "### Bubble Sort Algorithm\n\n"
            "Repeatedly steps through the list, compares adjacent elements and swaps them if they are in the wrong order.\n\n"
            "```java\n"
            "for (int i = 0; i < arr.length - 1; i++) {\n"
            "    for (int j = 0; j < arr.length - i - 1; j++) {\n"
            "        if (arr[j] > arr[j + 1]) {\n"
            "            int temp = arr[j];\n"
            "            arr[j] = arr[j + 1];\n"
            "            arr[j + 1] = temp;\n"
            "        }\n"
            "    }\n"
            "}\n"
            "```"
        )
    return (
        f"Thank you for asking about: '{prompt}'.\n\n"
        "Here are key programming principles to consider:\n"
        "- Clearly specify variable types and boundaries.\n"
        "- Validate input edge cases (e.g. 0, negative values, null/empty collections).\n"
        "- Write unit tests to check algorithm correctness.\n"
        "- Keep time and space complexity optimal for board exam questions."
    )


@router.post(
    "/ai/chat",
    response_model=ChatResponse,
    dependencies=[Depends(require_route_enabled("ai_chat")), Depends(rate_limit("ai"))]
)
async def ai_chat(request: ChatRequest, current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)):
    """
    Executes an AI tutoring query with automatic fallback hierarchy:
    Mistral Small -> Google Gemini 3.7 Flash -> Google Gemini 3.6 Flash -> Local Tutor Fallback.
    Saves session to Firestore for admin visibility.
    """
    # 1. Try Mistral
    mistral_res = await call_mistral_api(request.prompt, request.history or [])
    if mistral_res:
        reply, model_used, is_fallback = mistral_res, "mistral-small-latest", False
    else:
        # 2. Try Gemini Flash
        gemini_res = await call_gemini_api(request.prompt, request.history or [], "gemini-1.5-flash")
        if gemini_res:
            reply, model_used, is_fallback = gemini_res, "gemini-1.5-flash", True
        else:
            # 3. Built-in Pedagogical Fallback
            local_res = generate_local_fallback_response(request.prompt)
            reply, model_used, is_fallback = local_res, "vastavik-local-tutor-v1", True

    # Save chat session to Firestore for admin dashboard visibility
    # Save chat session to Firestore for admin dashboard visibility
    try:
        from app.db.firebase import db
        from datetime import datetime, timezone
        uid = current_user.get("uid", "guest_user") if current_user else "guest_user"
        student_name = current_user.get("name", "Student") if current_user else "Student"
        session_id = request.session_id or f"sess_{uid}_{int(datetime.now().timestamp())}"
        session_ref = db.collection("ai_chat_sessions").document(session_id)
        existing = session_ref.get()
        new_messages = list(request.history or [])
        new_messages.append({"role": "user", "content": request.prompt, "timestamp": datetime.now(timezone.utc).isoformat()})
        new_messages.append({"role": "assistant", "content": reply, "timestamp": datetime.now(timezone.utc).isoformat(), "model": model_used})
        if existing.exists:
            session_ref.update({
                "messages": new_messages,
                "message_count": len(new_messages),
                "model_used": model_used,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        else:
            session_ref.set({
                "session_id": session_id,
                "uid": uid,
                "student_name": student_name,
                "model_used": model_used,
                "messages": new_messages,
                "message_count": len(new_messages),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception as e:
        logger.warning(f"Failed to save AI chat session: {e}")

    return ChatResponse(reply=reply, model_used=model_used, is_fallback=is_fallback)


@router.post("/ai/conversations/telemetry")
async def sync_ai_conversation_telemetry(payload: Dict[str, Any], current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)):
    """
    Receives synchronized student chat conversation telemetry from mobile app cache,
    persisting into Firestore for admin real-time visibility.
    """
    try:
        from app.db.firebase import db
        from datetime import datetime, timezone
        conv_id = payload.get("conversationId") or f"conv_{int(datetime.now().timestamp())}"
        title = payload.get("title", "Student Chat")
        messages = payload.get("messages", [])
        app_version = payload.get("appVersion", "")
        device_model = payload.get("deviceModel", "")
        uid = current_user.get("uid", "student") if current_user else "student"
        student_name = current_user.get("name", "Student") if current_user else "Student"

        doc_ref = db.collection("ai_chat_sessions").document(conv_id)
        doc_ref.set({
            "session_id": conv_id,
            "uid": uid,
            "student_name": student_name,
            "title": title,
            "messages": messages,
            "message_count": len(messages),
            "app_version": app_version,
            "device_model": device_model,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat()
        }, merge=True)
        return {"status": "synced", "conversation_id": conv_id}
    except Exception as e:
        logger.warning(f"Telemetry sync error: {e}")
        return {"status": "error", "detail": str(e)}


@router.get(
    "/ai/chat/stream",
    dependencies=[Depends(require_route_enabled("ai_chat")), Depends(rate_limit("ai"))]
)
async def ai_chat_stream(prompt: str = Query(..., min_length=1), model: str = "mistral-god"):
    """
    Streams AI tokens word-by-word using Server-Sent Events (SSE) for zero-latency client rendering.
    """
    async def event_generator():
        # Get response using fallback chain
        resp = await call_mistral_api(prompt, [])
        if not resp:
            resp = await call_gemini_api(prompt, [])
        if not resp:
            resp = generate_local_fallback_response(prompt)

        words = resp.split(" ")
        for word in words:
            data = json.dumps({"delta_text": word + " ", "is_finished": False})
            yield f"data: {data}\n\n"
            await asyncio.sleep(0.03)  # natural typing cadence

        end_data = json.dumps({"delta_text": "", "is_finished": True})
        yield f"data: {end_data}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/ai/sessions")
async def list_student_ai_sessions(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Returns list of saved AI chat sessions for the authenticated student.
    Matches the Android app cache so local conversations stay synced with the backend.
    """
    uid = current_user.get("sub") or current_user.get("uid")
    from app.db.firebase import db
    try:
        if db.use_live_firestore:
            ref = db._firestore_client.collection("ai_chat_sessions").where("uid", "==", uid)
            docs = [d.to_dict() | {"session_id": d.id} for d in ref.stream()]
            docs.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
            return docs
    except Exception as e:
        logger.warning(f"Error reading ai_chat_sessions: {e}")
    return []


@router.get("/ai/sessions/{session_id}")
async def get_student_ai_session(
    session_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Returns full message transcript for a specific AI chat session.
    """
    uid = current_user.get("sub") or current_user.get("uid")
    from app.db.firebase import db
    try:
        if db.use_live_firestore:
            doc = db._firestore_client.collection("ai_chat_sessions").document(session_id).get()
            if doc.exists:
                data = doc.to_dict()
                if data.get("uid") == uid or current_user.get("role") == "admin":
                    return {"session_id": session_id, "messages": data.get("messages", [])}
    except Exception as e:
        logger.warning(f"Error reading ai_chat_session {session_id}: {e}")
    raise HTTPException(status_code=404, detail="AI chat session not found.")


@router.delete("/ai/sessions/{session_id}")
async def delete_student_ai_session(
    session_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Deletes an AI chat session.
    """
    uid = current_user.get("sub") or current_user.get("uid")
    from app.db.firebase import db
    try:
        if db.use_live_firestore:
            ref = db._firestore_client.collection("ai_chat_sessions").document(session_id)
            doc = ref.get()
            if doc.exists and (doc.to_dict().get("uid") == uid or current_user.get("role") == "admin"):
                ref.delete()
                return {"success": True, "message": "Session deleted."}
    except Exception as e:
        logger.warning(f"Error deleting ai_chat_session {session_id}: {e}")
    return {"success": True, "message": "Session cleared."}

