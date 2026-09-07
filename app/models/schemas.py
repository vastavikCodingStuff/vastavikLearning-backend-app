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
    student_class: Optional[str] = Field(default="Class 10")
    languages: Optional[List[str]] = Field(default_factory=lambda: ["Java", "Python", "JavaScript", "SQL"])


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    student_class: Optional[str] = None
    board: Optional[str] = None
    preferred_language: Optional[str] = None
    languages: Optional[List[str]] = None


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
    student_class: Optional[str] = "Class 10"
    preferred_language: Optional[str] = "Java"
    languages: List[str] = Field(default_factory=lambda: ["Java", "Python", "JavaScript", "SQL"])
    streak_count: int = 0
    lessons_completed: int = 0
    completion_rate: float = 0.0
    subscription_expires_at: Optional[str] = None
    payment_details: List[Dict[str, Any]] = Field(default_factory=list)


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
    duration_sec: int = 0
    whiteboard_image_url: Optional[str] = ""
    code_sample: Optional[str] = ""
    notes: Optional[str] = ""
    is_premium: bool = False
    order: int = 0
    video_format: str = "screen_recording"  # "screen_recording" | "whiteboard" | "short"
    shorts_url: Optional[str] = None
    shorts_video_id: Optional[str] = None


class VisitedRequest(BaseModel):
    course_id: str
    part_id: str


class CourseProgressResponse(BaseModel):
    course_id: str
    course_title: str
    total_parts: int
    completed_parts: int
    completion_percent: float
    visited_part_ids: List[str] = Field(default_factory=list)


class ProgressSummaryResponse(BaseModel):
    total_courses_enrolled: int
    overall_completion_percent: float
    courses: List[CourseProgressResponse] = Field(default_factory=list)


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
    session_id: Optional[str] = None  # Client-provided session ID for continuity


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
# 5b. Conversations & Socket.IO Schemas (anytype messages)
# ==========================================

class ConversationType(str):
    DIRECT = "direct"
    GROUP = "group"
    CHANNEL = "channel"
    SUPPORT = "support"


class MessageType(str):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    AUDIO = "audio"
    VIDEO = "video"
    SYSTEM = "system"
    CODE = "code"
    LOCATION = "location"
    CUSTOM = "custom"


class CreateConversationRequest(BaseModel):
    type: str = Field(default="direct", description="direct|group|channel|support")
    title: Optional[str] = Field(default=None, max_length=120)
    participant_ids: List[str] = Field(default_factory=list, description="User IDs to add (excluding creator)")
    avatar_url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class AddParticipantsRequest(BaseModel):
    participant_ids: List[str]


class ConversationResponse(BaseModel):
    id: str
    type: str
    title: Optional[str] = None
    avatar_url: Optional[str] = None
    created_by: str
    participants: List[str]
    participant_count: int
    last_message: Optional[Dict[str, Any]] = None
    last_message_at: Optional[str] = None
    message_count: int = 0
    created_at: str
    updated_at: str
    is_archived: bool = False
    metadata: Optional[Dict[str, Any]] = None


class ConversationListResponse(BaseModel):
    conversations: List[ConversationResponse]
    total: int


class SendMessageRequest(BaseModel):
    type: str = Field(default="text", description="anytypes: text|image|file|audio|video|system|code|location|custom")
    content: Optional[str] = Field(default="", max_length=10000)
    payload: Optional[Dict[str, Any]] = Field(default=None, description="Arbitrary JSON for anytype (file_url, mime, etc)")
    reply_to: Optional[str] = None
    temp_id: Optional[str] = Field(default=None, description="Client temp ID for optimistic UI dedup")


class EditMessageRequest(BaseModel):
    content: Optional[str] = Field(default=None, max_length=10000)
    payload: Optional[Dict[str, Any]] = None


class ReactRequest(BaseModel):
    emoji: str = Field(..., max_length=10)


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    sender_name: Optional[str] = None
    sender_avatar: Optional[str] = None
    type: str
    content: str
    payload: Optional[Dict[str, Any]] = None
    reply_to: Optional[str] = None
    created_at: str
    edited_at: Optional[str] = None
    deleted_at: Optional[str] = None
    reactions: Optional[Dict[str, List[str]]] = None
    read_by: Optional[List[str]] = None
    temp_id: Optional[str] = None


class MessageListResponse(BaseModel):
    messages: List[MessageResponse]
    total: int
    has_more: bool
    next_cursor: Optional[str] = None


class TypingEvent(BaseModel):
    conversation_id: str
    is_typing: bool = True


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
    grade: Optional[str] = "Class 10"
    source: Optional[str] = "sir"  # "sir" | "board" | "ai"


# ==========================================
# 8. Practice [Practice Sir] Schemas
# ==========================================

class MCQItemResponse(BaseModel):
    id: str
    title: str
    sub: str
    question: Optional[str] = None
    options: List[str] = Field(default_factory=list)
    correct_index: int = 0
    explanation: Optional[str] = None
    subject: str = "Java"
    topic: str = "OOP"
    difficulty: str = "Easy"
    source: str = "sir"  # "sir" | "ai"


class CodingItemResponse(BaseModel):
    id: str
    title: str
    difficulty: str
    topic: str
    language: str = "java"
    description: Optional[str] = None
    starter_code: Optional[str] = None
    solution_code: Optional[str] = None
    test_cases: List[Dict[str, str]] = Field(default_factory=list)
    source: str = "sir"  # "sir" | "ai"


class PredictOutputItemResponse(BaseModel):
    id: str
    set_number: int
    title: str
    topic: str
    question_count: str
    difficulty: str
    code_snippet: str
    expected_output: Optional[str] = None
    source: str = "sir"  # "sir" | "ai"


class QuizSetResponse(BaseModel):
    id: str
    title: str
    subject: str
    question_count: int
    created_at: str
    course_id: Optional[str] = None


# ==========================================
# 9. AI Session History Schemas
# ==========================================

class AiSessionItemResponse(BaseModel):
    session_id: str
    title: Optional[str] = "AI Tutoring Session"
    model_used: str
    message_count: int
    created_at: str
    updated_at: str


class AiSessionDetailResponse(BaseModel):
    session_id: str
    model_used: str
    messages: List[Dict[str, Any]]
    created_at: str
    updated_at: str


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
