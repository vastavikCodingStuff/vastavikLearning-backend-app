# Vastavik Learning Platform — Kotlin & Android Integration Guide

This guide provides everything needed to connect any Android or Kotlin application to the **Vastavik Learning Platform FastAPI Backend**.

---

## Table of Contents
1. [Base Configuration & Endpoints](#1-base-configuration--endpoints)
2. [Gradle Dependencies](#2-gradle-dependencies)
3. [HMAC-SHA256 Request Interceptor](#3-hmac-sha256-request-interceptor)
4. [JWT Authenticator & Token Refresh](#4-jwt-authenticator--token-refresh)
5. [OkHttp & Retrofit Setup](#5-okhttp--retrofit-setup)
6. [Complete Retrofit API Interface](#6-complete-retrofit-api-interface)
7. [Kotlin Data Models](#7-kotlin-data-models)
8. [AI Chat Token Streaming (SSE)](#8-ai-chat-token-streaming-sse)
9. [Real-Time WebSockets (Peer Chat & WebRTC)](#9-real-time-websockets-peer-chat--webrtc)
10. [Multipart File Uploads (Doubts & Bug Reports)](#10-multipart-file-uploads-doubts--bug-reports)
11. [Circuit Breaker (HTTP 503) Handling](#11-circuit-breaker-http-503-handling)

---

## 1. Base Configuration & Endpoints

| Environment | Base URL (HTTP) | WebSocket URL (WSS) |
| :--- | :--- | :--- |
| **Local Emulator** | `http://10.0.2.2:8000` | `ws://10.0.2.2:8000` |
| **Physical Device (LAN)** | `http://<YOUR_LAN_IP>:8000` | `ws://<YOUR_LAN_IP>:8000` |
| **Render Cloud** | `https://vastavik-backend.onrender.com` | `wss://vastavik-backend.onrender.com` |
| **Railway Cloud** | `https://vastavik-backend.up.railway.app` | `wss://vastavik-backend.up.railway.app` |
| **Production Domain** | `https://api.vastaviklearning.com` | `wss://api.vastaviklearning.com` |

---

## 2. Gradle Dependencies

Add the following to your app module's `build.gradle.kts`:

```kotlin
dependencies {
    // Retrofit & OkHttp
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")

    // Kotlin Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.0")

    // Security (EncryptedSharedPreferences)
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
}
```

---

## 3. HMAC-SHA256 Request Interceptor

All API requests must include HMAC verification headers. Below is the production-ready `HmacInterceptor.kt`:

```kotlin
package com.vastavik.learning.network

import okhttp3.Interceptor
import okhttp3.Response
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

class HmacInterceptor(
    private val apiKeyId: String = "vastavik_prod_v1",
    private val apiKeySecret: String = "super_secret_hmac_production_key_change_me_32char"
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val originalRequest = chain.request()
        val timestamp = (System.currentTimeMillis() / 1000).toString()
        val method = originalRequest.method.uppercase()
        val path = originalRequest.url.encodedPath

        // Formula: HMAC-SHA256(secret, timestamp + METHOD + path)
        val signature = computeHmacSha256(
            secret = apiKeySecret,
            message = "$timestamp$method$path"
        )

        val authenticatedRequest = originalRequest.newBuilder()
            .header("x-api-key-id", apiKeyId)
            .header("x-api-key-secret", apiKeySecret)
            .header("x-timestamp", timestamp)
            .header("x-hmac", signature)
            .build()

        return chain.proceed(authenticatedRequest)
    }

    private fun computeHmacSha256(secret: String, message: String): String {
        val sha256Hmac = Mac.getInstance("HmacSHA256")
        val secretKey = SecretKeySpec(secret.toByteArray(Charsets.UTF_8), "HmacSHA256")
        sha256Hmac.init(secretKey)
        val hash = sha256Hmac.doFinal(message.toByteArray(Charsets.UTF_8))
        return hash.joinToString("") { "%02x".format(it) }
    }
}
```

---

## 4. JWT Authenticator & Token Refresh

Handles silent token refreshing on `HTTP 401 Unauthorized`:

```kotlin
package com.vastavik.learning.network

import okhttp3.Authenticator
import okhttp3.Request
import okhttp3.Response
import okhttp3.Route

class TokenAuthenticator(
    private val tokenManager: TokenManager,
    private val authService: Lazy<VastavikApiService>
) : Authenticator {

    override fun authenticate(route: Route?, response: Response): Request? {
        // Prevent infinite loops if refresh fails
        if (response.request.header("Retry-Count") != null) {
            return null
        }

        val refreshToken = tokenManager.getRefreshToken() ?: return null

        // Synchronously call refresh endpoint
        val refreshCall = authService.get().refreshTokenSync(
            RefreshTokenRequest(refreshToken = refreshToken)
        ).execute()

        if (refreshCall.isSuccessful && refreshCall.body()?.success == true) {
            val newAccessToken = refreshCall.body()!!.accessToken!!
            tokenManager.saveTokens(newAccessToken, refreshToken)

            return response.request.newBuilder()
                .header("Authorization", "Bearer $newAccessToken")
                .header("Retry-Count", "1")
                .build()
        }

        // Token expired completely: log out user
        tokenManager.clearTokens()
        return null
    }
}
```

---

## 5. OkHttp & Retrofit Setup

```kotlin
package com.vastavik.learning.network

import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object NetworkClient {
    private const val BASE_URL = "https://api.vastaviklearning.com/" // Or "http://10.0.2.2:8000/" for emulator

    fun create(tokenManager: TokenManager): VastavikApiService {
        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BODY
        }

        val okHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .addInterceptor(HmacInterceptor())
            .addInterceptor { chain ->
                val builder = chain.request().newBuilder()
                tokenManager.getAccessToken()?.let { token ->
                    builder.header("Authorization", "Bearer $token")
                }
                chain.proceed(builder.build())
            }
            .addInterceptor(logging)
            .build()

        return Retrofit.Builder()
            .baseUrl(BASE_URL)
            .client(okHttpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(VastavikApiService::class.java)
    }
}
```

---

## 6. Complete Retrofit API Interface

```kotlin
package com.vastavik.learning.network

import okhttp3.MultipartBody
import okhttp3.RequestBody
import retrofit2.Call
import retrofit2.http.*

interface VastavikApiService {

    // --- Authentication ---
    @POST("api/v1/auth/signup")
    suspend fun signup(@Body request: SignupRequest): AuthResponse

    @POST("api/v1/auth/login")
    suspend fun login(@Body request: LoginRequest): AuthResponse

    @POST("api/v1/auth/refresh")
    suspend fun refreshToken(@Body request: RefreshTokenRequest): AuthResponse

    @POST("api/v1/auth/refresh")
    fun refreshTokenSync(@Body request: RefreshTokenRequest): Call<AuthResponse>

    @POST("api/v1/auth/oauth/google")
    suspend fun loginWithGoogle(@Body request: OAuthGoogleRequest): AuthResponse

    @POST("api/v1/auth/oauth/github")
    suspend fun loginWithGitHub(@Body request: OAuthGitHubRequest): AuthResponse

    @POST("api/v1/auth/device-verify")
    suspend fun verifyDevice(@Body request: DeviceVerifyRequest): CommonResponse

    @GET("api/v1/user/profile")
    suspend fun getUserProfile(): UserProfileResponse

    // --- Catalog & Curriculum ---
    @GET("api/v1/catalog/home")
    suspend fun getHomeCatalog(): HomeCatalogResponse

    @GET("api/v1/courses/{courseId}/curriculum")
    suspend fun getCurriculum(@Path("courseId") courseId: String): CurriculumResponse

    @GET("api/v1/lessons/{lessonId}")
    suspend fun getLesson(@Path("lessonId") lessonId: String): LessonResponse

    @POST("api/v1/progress/visited")
    suspend fun markPartVisited(@Body request: VisitedRequest): CommonResponse

    // --- AI Chat ---
    @POST("api/v1/ai/chat")
    suspend fun sendAiChat(@Body request: ChatRequest): ChatResponse

    // --- Code Runner ---
    @POST("api/v1/code/execute")
    suspend fun executeCode(@Body request: CodeExecutionRequest): CodeExecutionResponse

    @POST("api/v1/code/clean-ocr")
    suspend fun cleanOcrCode(@Body request: OcrCleanRequest): OcrCleanResponse

    // --- Notes ---
    @GET("api/v1/notes")
    suspend fun listNotes(): List<NoteResponse>

    @POST("api/v1/notes")
    suspend fun createNote(@Body request: NoteCreateRequest): NoteResponse

    @DELETE("api/v1/notes/{noteId}")
    suspend fun deleteNote(@Path("noteId") noteId: String): CommonResponse

    // --- Past Year Questions ---
    @GET("api/v1/pyqs")
    suspend fun getPyqs(
        @Query("board") board: String?,
        @Query("year") year: String?,
        @Query("subject") subject: String?
    ): List<PYQResponse>

    // --- Search ---
    @GET("api/v1/search")
    suspend fun searchCatalog(@Query("q") query: String): SearchResponse

    // --- Payments ---
    @POST("api/v1/payments/create-order")
    suspend fun createPaymentOrder(@Body request: CreateOrderRequest): CreateOrderResponse

    @GET("api/v1/payments/history")
    suspend fun getPaymentHistory(): List<Map<String, Any>>

    // --- System & Updates ---
    @GET("api/v1/system/app-update")
    suspend fun checkAppUpdate(): AppUpdateResponse

    @POST("api/v1/notifications/token")
    suspend fun registerFcmToken(@Body request: FcmTokenRequest): CommonResponse

    // --- Multipart Uploads ---
    @Multipart
    @POST("api/v1/doubts/submit")
    suspend fun submitDoubt(
        @Part("title") title: RequestBody,
        @Part("question") question: RequestBody,
        @Part("subject") subject: RequestBody,
        @Part file: MultipartBody.Part?
    ): Map<String, Any>

    @Multipart
    @POST("api/v1/system/bug-report")
    suspend fun submitBugReport(
        @Part("title") title: RequestBody,
        @Part("description") description: RequestBody,
        @Part("category") category: RequestBody,
        @Part("device_diagnostics") diagnostics: RequestBody,
        @Part media: List<MultipartBody.Part>?
    ): Map<String, Any>
}
```

---

## 7. Kotlin Data Models

```kotlin
package com.vastavik.learning.network

import com.google.gson.annotations.SerializedName

// --- Auth ---
data class SignupRequest(
    val email: String,
    val password: String,
    val name: String,
    val board: String = "ICSE",
    val language: String = "Java"
)

data class LoginRequest(
    val email: String,
    val password: String,
    @SerializedName("device_fingerprint") val deviceFingerprint: String? = null
)

data class RefreshTokenRequest(
    @SerializedName("refresh_token") val refreshToken: String
)

data class OAuthGoogleRequest(
    @SerializedName("id_token") val idToken: String
)

data class OAuthGitHubRequest(
    val code: String
)

data class DeviceVerifyRequest(
    @SerializedName("device_id") val deviceId: String,
    @SerializedName("is_rooted") val isRooted: Boolean = false,
    @SerializedName("is_emulator") val isEmulator: Boolean = false
)

data class AuthResponse(
    val success: Boolean,
    @SerializedName("access_token") val accessToken: String?,
    @SerializedName("refresh_token") val refreshToken: String?,
    @SerializedName("user_id") val userId: String?,
    val name: String?,
    val email: String?,
    val role: String?,
    @SerializedName("error_message") val errorMessage: String?
)

data class UserProfileResponse(
    @SerializedName("user_id") val userId: String,
    val name: String,
    val email: String,
    val role: String,
    @SerializedName("is_premium") val isPremium: Boolean,
    val board: String?,
    @SerializedName("preferred_language") val preferredLanguage: String?,
    @SerializedName("streak_count") val streakCount: Int,
    @SerializedName("lessons_completed") val lessonsCompleted: Int
)

// --- Catalog ---
data class HomeCatalogResponse(
    val courses: List<CourseItem>,
    val banners: List<BannerItem>,
    @SerializedName("popular_topics") val popularTopics: List<TopicItem>
)

data class CourseItem(
    val id: String,
    val title: String,
    val description: String,
    @SerializedName("icon_name") val iconName: String,
    val color: Long,
    val order: Int
)

data class BannerItem(
    val id: String,
    val title: String,
    @SerializedName("image_url") val imageUrl: String,
    @SerializedName("target_route") val targetRoute: String
)

data class TopicItem(
    val id: String,
    val name: String,
    val tag: String
)

data class CurriculumResponse(
    @SerializedName("course_id") val courseId: String,
    val parts: List<PartItem>
)

data class PartItem(
    @SerializedName("part_id") val partId: String,
    val title: String,
    val order: Int,
    val subparts: List<SubpartItem>
)

data class SubpartItem(
    @SerializedName("subpart_id") val subpartId: String,
    val title: String,
    @SerializedName("lesson_id") val lessonId: String
)

data class LessonResponse(
    val id: String,
    val title: String,
    val description: String,
    @SerializedName("youtube_url") val youtubeUrl: String,
    @SerializedName("youtube_video_id") val youtubeVideoId: String,
    @SerializedName("duration_sec") val durationSec: Int,
    @SerializedName("whiteboard_image_url") val whiteboardImageUrl: String,
    @SerializedName("code_sample") val codeSample: String,
    val notes: String,
    @SerializedName("is_premium") val isPremium: Boolean,
    val order: Int
)

data class VisitedRequest(
    @SerializedName("course_id") val courseId: String,
    @SerializedName("part_id") val partId: String
)

// --- AI ---
data class ChatRequest(
    val prompt: String,
    val model: String = "mistral-god",
    val history: List<ChatHistoryItem> = emptyList()
)

data class ChatHistoryItem(
    val role: String, // "user" or "assistant"
    val content: String
)

data class ChatResponse(
    val reply: String,
    @SerializedName("model_used") val modelUsed: String,
    @SerializedName("is_fallback") val isFallback: Boolean
)

// --- Code Runner ---
data class CodeExecutionRequest(
    val language: String, // "java", "python", "cpp", "javascript"
    @SerializedName("source_code") val sourceCode: String,
    val stdin: String = ""
)

data class CodeExecutionResponse(
    val success: Boolean,
    val stdout: String?,
    val stderr: String?,
    @SerializedName("execution_time") val executionTime: String?,
    @SerializedName("memory_kb") val memoryKb: Int?,
    @SerializedName("status_description") val statusDescription: String
)

data class OcrCleanRequest(
    @SerializedName("raw_ocr_text") val rawOcrText: String,
    val language: String = "java"
)

data class OcrCleanResponse(
    @SerializedName("cleaned_code") val cleanedCode: String,
    @SerializedName("corrections_applied") val correctionsApplied: List<String>
)

// --- Notes ---
data class NoteCreateRequest(
    val title: String,
    val content: String,
    val tag: String = "General"
)

data class NoteResponse(
    val id: String,
    val uid: String,
    val title: String,
    val content: String,
    val tag: String,
    @SerializedName("created_at") val createdAt: String
)

// --- PYQ ---
data class PYQResponse(
    val id: String,
    val board: String,
    val year: String,
    val subject: String,
    val question: String,
    val solution: String,
    val marks: Int
)

// --- Search ---
data class SearchResponse(
    val query: String,
    val courses: List<CourseItem>,
    val topics: List<TopicItem>,
    val lessons: List<LessonResponse>
)

// --- Payments ---
data class CreateOrderRequest(
    @SerializedName("plan_id") val planId: String,
    val amount: Double
)

data class CreateOrderResponse(
    @SerializedName("order_id") val orderId: String,
    val amount: Double,
    val currency: String,
    val checksum: String,
    @SerializedName("payment_url") val paymentUrl: String?
)

// --- Common ---
data class CommonResponse(
    val success: Boolean,
    val message: String
)

data class FcmTokenRequest(
    @SerializedName("fcm_token") val fcmToken: String
)

data class AppUpdateResponse(
    @SerializedName("version_name") val versionName: String,
    @SerializedName("version_code") val versionCode: Int,
    @SerializedName("download_url") val downloadUrl: String,
    val changelog: String,
    @SerializedName("is_mandatory") val isMandatory: Boolean
)
```

---

## 8. AI Chat Token Streaming (SSE)

For typing animation in your chat screen, stream tokens line-by-line via Server-Sent Events (SSE):

```kotlin
package com.vastavik.learning.ai

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.net.URLEncoder

class VastavikAiStreamer(private val okHttpClient: OkHttpClient) {

    fun streamChat(prompt: String, baseUrl: String = "https://api.vastaviklearning.com"): Flow<String> = flow {
        val encodedPrompt = URLEncoder.encode(prompt, "UTF-8")
        val request = Request.Builder()
            .url("$baseUrl/api/v1/ai/chat/stream?prompt=$encodedPrompt")
            .header("Accept", "text/event-stream")
            .build()

        okHttpClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw Exception("Stream failed: ${response.code}")

            val reader = response.body?.byteStream()?.bufferedReader() ?: return@flow
            var line: String?

            while (reader.readLine().also { line = it } != null) {
                if (line?.startsWith("data: ") == true) {
                    val jsonStr = line!!.substring(6).trim()
                    val json = JSONObject(jsonStr)
                    val delta = json.optString("delta_text", "")
                    val isFinished = json.optBoolean("is_finished", false)

                    if (delta.isNotEmpty()) {
                        emit(delta)
                    }
                    if (isFinished) {
                        break
                    }
                }
            }
        }
    }.flowOn(Dispatchers.IO)
}
```

---

## 9. Real-Time WebSockets (Peer Chat & WebRTC)

### A. Peer Chat Discussion Hub

```kotlin
package com.vastavik.learning.realtime

import okhttp3.*
import org.json.JSONObject

class PeerChatClient(
    private val client: OkHttpClient,
    private val wsUrl: String = "wss://api.vastaviklearning.com"
) : WebSocketListener() {

    private var webSocket: WebSocket? = null
    var onMessageReceived: ((sender: String, message: String) -> Unit)? = null

    fun connect(roomId: String, userId: String) {
        val request = Request.Builder()
            .url("$wsUrl/ws/peer-chat?room=$roomId&user_id=$userId")
            .build()
        webSocket = client.newWebSocket(request, this)
    }

    fun sendMessage(text: String) {
        val json = JSONObject().apply {
            put("type", "MESSAGE")
            put("text", text)
        }
        webSocket?.send(json.toString())
    }

    override fun onMessage(webSocket: WebSocket, text: String) {
        val json = JSONObject(text)
        if (json.optString("type") == "MESSAGE") {
            onMessageReceived?.invoke(
                json.optString("sender_id", "Unknown"),
                json.optString("text", "")
            )
        }
    }

    fun disconnect() {
        webSocket?.close(1000, "User Left")
    }
}
```

### B. Live Classroom WebRTC Signaling Relay

```kotlin
package com.vastavik.learning.realtime

import okhttp3.*
import org.json.JSONObject

class WebRtcSignalingClient(
    private val client: OkHttpClient,
    private val wsUrl: String = "wss://api.vastaviklearning.com"
) : WebSocketListener() {

    private var webSocket: WebSocket? = null
    var onSignalReceived: ((signalType: String, payloadJson: String, senderId: String) -> Unit)? = null

    fun connect(classId: String, userId: String, role: String = "student") {
        val request = Request.Builder()
            .url("$wsUrl/ws/signaling/$classId?user_id=$userId&role=$role")
            .build()
        webSocket = client.newWebSocket(request, this)
    }

    fun sendSignal(targetId: String?, signalType: String, payloadJson: String) {
        val json = JSONObject().apply {
            put("target_id", targetId)
            put("signal_type", signalType) // "OFFER", "ANSWER", "ICE", "WHITEBOARD"
            put("payload_json", payloadJson)
        }
        webSocket?.send(json.toString())
    }

    override fun onMessage(webSocket: WebSocket, text: String) {
        val json = JSONObject(text)
        val type = json.optString("signal_type")
        val payload = json.optString("payload_json")
        val sender = json.optString("sender_id")
        onSignalReceived?.invoke(type, payload, sender)
    }

    fun disconnect() {
        webSocket?.close(1000, "Class Ended")
    }
}
```

---

## 10. Multipart File Uploads (Doubts & Bug Reports)

Upload homework photos or device diagnostics bug reports:

```kotlin
package com.vastavik.learning.upload

import com.vastavik.learning.network.VastavikApiService
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File

suspend fun uploadDoubt(
    apiService: VastavikApiService,
    title: String,
    question: String,
    subject: String,
    imageFile: File?
) {
    val titlePart = title.toRequestBody("text/plain".toMediaTypeOrNull())
    val questionPart = question.toRequestBody("text/plain".toMediaTypeOrNull())
    val subjectPart = subject.toRequestBody("text/plain".toMediaTypeOrNull())

    val filePart = imageFile?.let {
        val reqFile = it.asRequestBody("image/jpeg".toMediaTypeOrNull())
        MultipartBody.Part.createFormData("file", it.name, reqFile)
    }

    val response = apiService.submitDoubt(titlePart, questionPart, subjectPart, filePart)
    println("Submitted Doubt Ticket: ${response["ticket_id"]}")
}
```

---

## 11. Circuit Breaker (HTTP 503) Handling

When a feature (like Judge0 runner or AI) is toggled offline by the admin for maintenance, the backend returns:

```json
HTTP/1.1 503 Service Unavailable
Retry-After: 300
Content-Type: application/json

{
  "status": "MAINTENANCE",
  "error": "ROUTE_TEMPORARILY_OFFLINE",
  "message": "This specific feature is undergoing scheduled maintenance. All other app services remain fully operational.",
  "feature": "code_execution"
}
```

Catch it gracefully in your Repository layer:

```kotlin
import retrofit2.HttpException
import org.json.JSONObject

suspend fun <T> safeApiCall(apiCall: suspend () -> T): Result<T> {
    return try {
        Result.success(apiCall())
    } catch (e: HttpException) {
        if (e.code() == 503) {
            val errorJson = e.response()?.errorBody()?.string()
            val message = try {
                JSONObject(errorJson ?: "").optString("message", "Feature undergoing maintenance.")
            } catch (_: Exception) {
                "Feature undergoing maintenance."
            }
            Result.failure(Exception(message))
        } else {
            Result.failure(e)
        }
    } catch (e: Exception) {
        Result.failure(e)
    }
}
```
