from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, EmailStr


# ==========================================
# 1. Authentication & User Schemas
# ==========================================

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    name: str = Field(..., min_length=2)
    board: str = Field(default="ICSE")  # "ICSE" | "CBSE"
    language: str = Field(default="Java")  # "Java" | "Python"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    device_fingerprint: Optional[str] = None


class OAuthGoogleRequest(BaseModel):
    id_token: str


class OAuthGitHubRequest(BaseModel):
    code: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class DeviceVerifyRequest(BaseModel):
    device_id: str
    is_rooted: bool = False
    is_emulator: bool = False
    integrity_token: Optional[str] = None


class AuthResponse(BaseModel):
    success: bool
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    user_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    error_message: Optional[str] = None


class UserProfileResponse(BaseModel):
    user_id: str
    name: str
    email: str
    role: str
    is_premium: bool
    board: Optional[str] = "ICSE"
    preferred_language: Optional[str] = "Java"
    streak_count: int = 0
    lessons_completed: int = 0
    subscription_expires_at: Optional[str] = None


# ==========================================
# 2. Courses & Curriculum Schemas
# ==========================================

class CourseItem(BaseModel):
    id: str
    title: str
    description: str
    icon_name: str
    color: int
    order: int
    is_published: bool = True


class BannerItem(BaseModel):
    id: str
    title: str
    image_url: str
    target_route: str


class TopicItem(BaseModel):
    id: str
    name: str
    tag: str


class HomeCatalogResponse(BaseModel):
    courses: List[CourseItem]
    banners: List[BannerItem]
    popular_topics: List[TopicItem]


class SubpartItem(BaseModel):
    subpart_id: str
    title: str
    lesson_id: str


class PartItem(BaseModel):
    part_id: str
    title: str
    order: int
    subparts: List[SubpartItem]


class CurriculumResponse(BaseModel):
    course_id: str
    parts: List[PartItem]


class LessonResponse(BaseModel):
    id: str
    title: str
    description: str
    youtube_url: str
    youtube_video_id: str
    duration_sec: int
    whiteboard_image_url: str
    code_sample: str
    notes: str
    is_premium: bool
    order: int


class VisitedRequest(BaseModel):
    course_id: str
    part_id: str


# ==========================================
# 3. AI Proxy Schemas
# ==========================================

class ChatHistoryItem(BaseModel):
    role: str  # "user", "assistant", "system"
    content: str


class ChatRequest(BaseModel):
    prompt: str
    model: Optional[str] = "mistral-god"  # "mistral-god", "gemini-3.7-flash", "gemini-3.6-flash"
    history: Optional[List[ChatHistoryItem]] = []


class ChatResponse(BaseModel):
    reply: str
    model_used: str
    is_fallback: bool = False


# ==========================================
# 4. Code Execution (Judge0) Schemas
# ==========================================

class CodeExecutionRequest(BaseModel):
    language: str  # "java", "python", "cpp", "javascript"
    source_code: str
    stdin: Optional[str] = ""


class CodeExecutionResponse(BaseModel):
    success: bool
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    execution_time: Optional[str] = None
    memory_kb: Optional[int] = None
    status_description: str


class OcrCleanRequest(BaseModel):
    raw_ocr_text: str
    language: Optional[str] = "java"


class OcrCleanResponse(BaseModel):
    cleaned_code: str
    corrections_applied: List[str] = []


# ==========================================
# 5. Real-Time WebRTC / Socket Schemas
# ==========================================

class SignalPacket(BaseModel):
    class_id: str
    sender_id: str
    target_id: Optional[str] = None
    signal_type: str  # "OFFER", "ANSWER", "ICE", "WHITEBOARD"
    payload_json: str


# ==========================================
# 6. Payments & Subscriptions
# ==========================================

class CreateOrderRequest(BaseModel):
    plan_id: str
    amount: float


class CreateOrderResponse(BaseModel):
    order_id: str
    amount: float
    currency: str = "INR"
    checksum: str
    payment_url: Optional[str] = None


class PaymentWebhookPayload(BaseModel):
    order_id: str
    transaction_id: str
    status: str  # "success", "failed"
    signature: str
    uid: Optional[str] = None
    amount: Optional[float] = None


# ==========================================
# 7. Notes & PYQs
# ==========================================

class NoteCreateRequest(BaseModel):
    title: str
    content: str
    tag: Optional[str] = "General"


class NoteResponse(BaseModel):
    id: str
    uid: str
    title: str
    content: str
    tag: str
    created_at: str


class PYQResponse(BaseModel):
    id: str
    board: str
    year: str
    subject: str
    question: str
    solution: str
    marks: int


# ==========================================
# 8. System & Diagnostics Schemas
# ==========================================

class AppUpdateResponse(BaseModel):
    version_name: str
    version_code: int
    download_url: str
    changelog: str
    is_mandatory: bool = False


class FcmTokenRequest(BaseModel):
    fcm_token: str


class CommonResponse(BaseModel):
    success: bool
    message: str


class HealthResponse(BaseModel):
    status: str
    uptime_seconds: float
    route_status: Dict[str, bool]
    environment: str
