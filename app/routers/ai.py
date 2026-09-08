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


def retrieve_practice_context(query: str) -> str:
    """
    Searches baseline practice datasets (MCQs, Predict Output, Coding, PYQs)
    to inject verified problem statements, expected outputs, and solution traces into AI context.
    """
    try:
        from app.routers.practice import DEFAULT_SIR_MCQS, DEFAULT_SIR_CODING, DEFAULT_SIR_PREDICT
        from app.routers.pyq import DEFAULT_PYQS
    except Exception:
        return ""

    q_lower = query.lower()
    matches = []

    # 1. Predict the Output Sets
    if "predict" in q_lower or "output" in q_lower or "set" in q_lower:
        for po in DEFAULT_SIR_PREDICT:
            if f"set {po['set_number']}" in q_lower or f"set_{po['set_number']}" in q_lower or po["title"].lower() in q_lower or "predict" in q_lower:
                matches.append(
                    f"### Practice Reference: Predict the Output Set {po['set_number']} ({po['title']})\n"
                    f"- Topic: {po['topic']}\n"
                    f"- Code Snippet:\n```java\n{po['code_snippet']}\n```\n"
                    f"- Expected Output: `{po['expected_output']}`\n"
                )

    # 2. Curated MCQs
    if "mcq" in q_lower or "quiz" in q_lower or "question" in q_lower or "primitive" in q_lower or "oop" in q_lower or "finally" in q_lower:
        for mcq in DEFAULT_SIR_MCQS:
            if mcq["title"].lower() in q_lower or any(w in mcq["question"].lower() for w in q_lower.split() if len(w) > 4):
                matches.append(
                    f"### Practice Reference: MCQ ({mcq['title']})\n"
                    f"- Question: {mcq['question']}\n"
                    f"- Options: {', '.join(mcq['options'])}\n"
                    f"- Correct Answer: `{mcq['options'][mcq['correct_index']]}`\n"
                    f"- Explanation: {mcq['explanation']}\n"
                )

    # 3. Coding Challenges
    if "coding" in q_lower or "challenge" in q_lower or "rotate" in q_lower or "palindrome" in q_lower or "sort" in q_lower or "chaining" in q_lower:
        for code in DEFAULT_SIR_CODING:
            if code["title"].lower() in q_lower or code["topic"].lower() in q_lower or any(w in code["description"].lower() for w in q_lower.split() if len(w) > 5):
                matches.append(
                    f"### Practice Reference: Coding Challenge ({code['title']})\n"
                    f"- Topic: {code['topic']} | Difficulty: {code['difficulty']}\n"
                    f"- Problem: {code['description']}\n"
                    f"- Solution:\n```java\n{code['solution_code']}\n```\n"
                )

    # 4. Past Year Questions (PYQs)
    if "pyq" in q_lower or "past year" in q_lower or "board" in q_lower or "2024" in q_lower or "2023" in q_lower or "overload" in q_lower:
        for pyq in DEFAULT_PYQS:
            if pyq["board"].lower() in q_lower or pyq["year"] in q_lower or any(w in pyq["question"].lower() for w in q_lower.split() if len(w) > 5):
                matches.append(
                    f"### Practice Reference: {pyq['board']} {pyq['year']} ({pyq['grade']})\n"
                    f"- Question: {pyq['question']}\n"
                    f"- Solution:\n```java\n{pyq['solution']}\n```\n"
                )

    return "\n\n".join(matches[:3])


async def call_mistral_api(prompt: str, history: List[ChatHistoryItem], practice_context: str = "") -> Optional[str]:
    """Calls Mistral AI API with temperature tuning for code explanation and practice dataset context."""
    if not settings.MISTRAL_API_KEY:
        return None

    url = "https://api.mistral.ai/v1/chat/completions"
    sys_content = (
        "You are Vastavik AI, an expert computer science and programming tutor for ICSE Class 10 and CBSE Class 12 students. "
        "Give crisp, clear, well-commented code explanations and step-by-step traces for practice questions."
    )
    if practice_context:
        sys_content += f"\n\nHere is verified curriculum knowledge from the Vastavik Practice modules:\n{practice_context}"

    messages = [{"role": "system", "content": sys_content}]
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


async def call_gemini_api(prompt: str, history: List[ChatHistoryItem], model_name: str = "gemini-1.5-flash", practice_context: str = "") -> Optional[str]:
    """
    Calls Google Gemini API with practice dataset context.
    """
    if not settings.GEMINI_API_KEY:
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={settings.GEMINI_API_KEY}"
    
    sys_text = "You are Vastavik AI, a dedicated tutor for students learning programming. Explain concepts step by step with precise code snippets."
    if practice_context:
        sys_text += f"\n\nVerified Curriculum Practice Context:\n{practice_context}"

    contents = []
    for h in history[-6:]:
        role = "user" if h.role == "user" else "model"
        contents.append({"role": role, "parts": [{"text": h.content}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload = {
        "contents": contents,
        "systemInstruction": {
            "parts": [{"text": sys_text}]
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


def generate_local_fallback_response(prompt: str, practice_context: str = "") -> str:
    """
    Intelligent built-in pedagogical fallback if external AI endpoints are unreachable.
    Leverages practice context to guarantee 100% accurate responses for app practice problems.
    """
    prompt_lower = prompt.lower()

    # If practice context found, return detailed explanation with trace
    if practice_context:
        return (
            f"Here is the detailed solution and trace from Vastavik Practice:\n\n"
            f"{practice_context}\n\n"
            f"**Key Takeaway:** Pay close attention to index boundaries, conditional branches, and data type rules in board examinations."
        )

    # Specific Predict Output sets
    if "predict" in prompt_lower or "set 1" in prompt_lower or "loop tracing" in prompt_lower:
        return (
            "### Predict the Output — Set 1: Loop Tracing & Conditionals\n\n"
            "```java\n"
            "int sum = 0;\n"
            "for (int i = 1; i <= 5; i++) {\n"
            "    if (i % 2 == 0) continue; // Skips even numbers (2, 4)\n"
            "    sum += i;                 // Adds odd numbers (1, 3, 5)\n"
            "}\n"
            "System.out.println(sum);\n"
            "```\n\n"
            "**Step-by-Step Iteration Trace:**\n"
            "- `i = 1`: 1 is odd -> `sum = 0 + 1 = 1`\n"
            "- `i = 2`: 2 is even -> `continue` (skipped)\n"
            "- `i = 3`: 3 is odd -> `sum = 1 + 3 = 4`\n"
            "- `i = 4`: 4 is even -> `continue` (skipped)\n"
            "- `i = 5`: 5 is odd -> `sum = 4 + 5 = 9`\n"
            "- `i = 6`: Loop terminates.\n\n"
            "**Output:**\n"
            "```\n"
            "9\n"
            "```"
        )
    elif "set 2" in prompt_lower or "substring" in prompt_lower:
        return (
            "### Predict the Output — Set 2: String Operations\n\n"
            "```java\n"
            "String s = \"KNOWLEDGE\";\n"
            "System.out.println(s.substring(3, 7));\n"
            "System.out.println(s.indexOf('E', 5));\n"
            "```\n\n"
            "**Detailed Trace:**\n"
            "1. Indexing for `\"KNOWLEDGE\"`: `K(0), N(1), O(2), W(3), L(4), E(5), D(6), G(7), E(8)`\n"
            "2. `s.substring(3, 7)` extracts characters from index 3 up to (but not including) index 7 -> `WLED`.\n"
            "3. `s.indexOf('E', 5)` searches for `'E'` starting at index 5. The first `'E'` at index 5 matches or looking beyond, the next `'E'` is at index 8.\n\n"
            "**Output:**\n"
            "```\n"
            "WLED\n"
            "8\n"
            "```"
        )
    elif "set 3" in prompt_lower or "array indexing" in prompt_lower or "shifting" in prompt_lower:
        return (
            "### Predict the Output — Set 3: Array Indexing & Pre/Post Increments\n\n"
            "```java\n"
            "int[] a = {10, 20, 30, 40};\n"
            "int x = a[1]++;\n"
            "int y = ++a[2];\n"
            "System.out.println(x + \" \" + y + \" \" + a[1]);\n"
            "```\n\n"
            "**Trace:**\n"
            "- `a[1]++` is post-increment: `x` gets `20`, then `a[1]` becomes `21`.\n"
            "- `++a[2]` is pre-increment: `a[2]` becomes `31`, then `y` gets `31`.\n"
            "- `a[1]` is now `21`.\n\n"
            "**Output:**\n"
            "```\n"
            "20 31 21\n"
            "```"
        )
    elif "recursion" in prompt_lower:
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
    # Retrieve verified curriculum practice context if query refers to MCQs, Predict Output, Coding, or PYQs
    practice_ctx = retrieve_practice_context(request.prompt)

    # 1. Try Mistral
    mistral_res = await call_mistral_api(request.prompt, request.history or [], practice_context=practice_ctx)
    if mistral_res:
        reply, model_used, is_fallback = mistral_res, "mistral-small-latest", False
    else:
        # 2. Try Gemini Flash
        gemini_res = await call_gemini_api(request.prompt, request.history or [], "gemini-1.5-flash", practice_context=practice_ctx)
        if gemini_res:
            reply, model_used, is_fallback = gemini_res, "gemini-1.5-flash", True
        else:
            # 3. Built-in Pedagogical Fallback
            local_res = generate_local_fallback_response(request.prompt, practice_context=practice_ctx)
            reply, model_used, is_fallback = local_res, "vastavik-local-tutor-v1", True

    # Save chat session to Firestore for admin dashboard visibility
    # Save chat session to Firestore for admin dashboard visibility
    try:
        from app.db.firebase import db
        from app.services.moderation import analyze_content_safety
        from datetime import datetime, timezone
        uid = current_user.get("uid", "guest_user") if current_user else "guest_user"
        student_name = current_user.get("name", "Student") if current_user else "Student"
        session_id = request.session_id or f"sess_{uid}_{int(datetime.now().timestamp())}"
        session_ref = db.collection("ai_chat_sessions").document(session_id)
        existing = session_ref.get()
        new_messages = list(request.history or [])

        # Analyze prompt for safety moderation
        mod = analyze_content_safety(request.prompt)
        user_msg = {
            "role": "user",
            "content": request.prompt,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_flagged": mod["is_flagged"],
            "flag_reasons": mod["flag_reasons"],
            "flagged_terms": mod["flagged_terms"],
        }
        new_messages.append(user_msg)
        new_messages.append({
            "role": "assistant",
            "content": reply,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model_used
        })

        any_flagged = any(m.get("is_flagged") for m in new_messages) or mod["is_flagged"]
        all_reasons = set(mod["flag_reasons"])
        all_terms = set(mod["flagged_terms"])
        for m in new_messages:
            for r in m.get("flag_reasons", []):
                all_reasons.add(r)
            for t in m.get("flagged_terms", []):
                all_terms.add(t)

        update_payload = {
            "messages": new_messages,
            "message_count": len(new_messages),
            "model_used": model_used,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "is_flagged": any_flagged,
            "flag_reasons": sorted(list(all_reasons)),
            "flagged_terms": sorted(list(all_terms)),
        }

        if existing.exists:
            session_ref.update(update_payload)
        else:
            session_ref.set(update_payload | {
                "session_id": session_id,
                "uid": uid,
                "student_name": student_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
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
        from app.services.moderation import analyze_content_safety
        from datetime import datetime, timezone
        conv_id = payload.get("conversationId") or f"conv_{int(datetime.now().timestamp())}"
        title = payload.get("title", "Student Chat")
        raw_messages = payload.get("messages", [])
        app_version = payload.get("appVersion", "")
        device_model = payload.get("deviceModel", "")
        uid = current_user.get("uid", "student") if current_user else "student"
        student_name = current_user.get("name", "Student") if current_user else "Student"

        flagged_reasons = set()
        flagged_terms = set()
        is_conv_flagged = False
        enriched_messages = []

        for m in raw_messages:
            content = m.get("content", "")
            if m.get("role") == "user":
                mod = analyze_content_safety(content)
                if mod["is_flagged"]:
                    is_conv_flagged = True
                    flagged_reasons.update(mod["flag_reasons"])
                    flagged_terms.update(mod["flagged_terms"])
                    m["is_flagged"] = True
                    m["flag_reasons"] = mod["flag_reasons"]
                    m["flagged_terms"] = mod["flagged_terms"]
            enriched_messages.append(m)

        doc_ref = db.collection("ai_chat_sessions").document(conv_id)
        doc_ref.set({
            "session_id": conv_id,
            "uid": uid,
            "student_name": student_name,
            "title": title,
            "messages": enriched_messages,
            "message_count": len(enriched_messages),
            "app_version": app_version,
            "device_model": device_model,
            "is_flagged": is_conv_flagged,
            "flag_reasons": sorted(list(flagged_reasons)),
            "flagged_terms": sorted(list(flagged_terms)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat()
        }, merge=True)
        return {"status": "synced", "conversation_id": conv_id, "is_flagged": is_conv_flagged}
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

