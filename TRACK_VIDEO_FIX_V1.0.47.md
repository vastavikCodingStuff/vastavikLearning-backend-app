# Vastavik Video Pipeline Fix — Track File for Token Expiry (v1.0.47)
> **Branch to be created:** `fix/video-refresh-playback-admin` (in `vastavikLearning-app`, `vastavikLearning-backend-app`, `vastavikLearning-admin-web`)
> **Next version:** `1.0.47` (app 47 / companion 15) — `1.0.46` is current Latest
> **Created:** 2026-09-08 — Tandem fix for: instant fetch on Learn re-click, video not playing, admin privacy + whiteboard/code/shorts, backend route + DB

---

## 1) Problem Snapshot (from user + screenshot)

- **Admin shows 4 courses** (`Java for ICSE Class 10`, `Python for CBSE Class 12`, `Data Structures & Algorithms`, `Kotlin Classes Begeineers`) but app's **Popular Topics** was hardcoded to `OOP Concepts / Arrays & Lists / Sorting Algorithms / File Handling` — fixed in 1.0.46 to fetch from `GET /api/v1/catalog/home`, but **Course Catalog grid** on Home still uses `sampleCourses` hardcoded (HomeScreen.kt:105-112) — visual inconsistency remains.
- **Upload not visible instantly:** `POST /admin/videos` does NOT call `invalidate_catalog_cache()` and Learn page does NOT refetch on tab re-click. `HomeTab LaunchedEffect(Unit)` and `LearningViewModel` only fetch once per process; pager `beyondViewportPageCount=1` keeps Learn alive with stale data. No pull-to-refresh.
- **Video not playing on test:** `GET /api/v1/lessons/{lessonId}` → `VastavikYouTubePlayer` fails if `youtubeVideoId` extraction fails, `is_premium` gate returns 403, or `video_format` mismatch (`screen_recording` vs `vscode`). No error toast, just blank.
- **Admin add video privacy:** UI says “unlisted, private or public” but no `privacy` field sent; backend has only `is_premium` + `isPublished=true` always.
- **Admin missing fields:** `whiteboard_image_url` and `code_sample` always `""` (UploadVideoModal.tsx:74-75), `shorts` only via `video_type` select, no dedicated `shorts` toggle/validation, no whiteboard image upload, no code editor.
- **Backend gaps:** No `privacy` column, `video_format` enum inconsistent (`screen` vs `screen_recording` vs `vscode` vs `shorts` vs `short`), `shorts_url` dead, `DELETE` doesn't cascade, no `is_published` check in `GET /lessons`, no cache invalidation after video create/delete/link.

---

## 2) Investigation Summary (read-only, 2026-09-08)

**Backend** `E:\vastavikCodingStuff\vastavikLearning-backend-app`
- `app/routers/admin_dashboard.py:1686` `GET /admin/videos` merges curriculum + flat `videos`; `1767` `POST /admin/videos` writes only flat `videos` with `VideoCreate` (`video_type`: `screen_recording|whiteboard|short`, no `privacy`, `whiteboard_image_url=""`, `code_sample=""`); `1803` `DELETE`; `1879` `POST /admin/courses/{cid}/parts/{pid}/lessons` links `lesson_id` to subpart.
- `app/routers/catalog.py:23` `CATALOG_CACHE_TTL_SECONDS=300`, `invalidate_catalog_cache()` only called after `POST /admin/courses` / `DELETE /admin/courses`, NOT after video create/delete/link; `60` `GET /lessons/{id}` uses `db.get_lesson()` (flat `videos` first, then `collection_group("lessons")`), premium gate at `73-86`, normalization at `89-106`.
- `app/db/firebase.py:472` `get_lesson()` precedence flat `videos` → nested `lessons`; `410` `get_home_catalog()` ; `440` `get_course_curriculum()` ; `339` `videos` flat.
- `app/models/schemas.py:145` `LessonResponse` has `video_format`, `shorts_url`, `shorts_video_id`, `is_premium`, `whiteboard_image_url`, `code_sample` — but `shorts_*` never populated.
- `app/main.py:188` mounts `admin`, `admin_dashboard`, `catalog` etc.; HMAC bypass includes `/admin`.

**Admin** `E:\vastavikCodingStuff\vastavikLearning-admin-web`
- `app/dashboard/videos/page.tsx:84` `GET /admin/videos` once on mount, no `refetch`, no `Refresh` button, optimistic `setVideos`; `104` delete optimistic; `UploadVideoModal.tsx:46` typeOptions 3 cards, `198` youtube URL input, `74-75` `whiteboard=""`, `code_sample=""` hardcoded, `51` `privacy` missing, `272` `is_premium` toggle, `257` `durationMins`.
- `types/api.ts:153` `VideoType = "screen_recording"|"whiteboard"|"short"`, `VideoLesson` has all fields but UI ignores `whiteboard`/`code`/`privacy`.
- No `hooks/useVideos.ts`.

**App** `E:\vastavikCodingStuff\vastavikLearning-app`
- `ui/screens/learning/LearningPathScreen.kt:54` Duolingo winding path `LearningPathWindingView`, `LearningViewModel.kt:132` `loadCurriculum()` via `GET /api/v1/courses/{id}/curriculum` → `streamParts` fallback leak, no pull-to-refresh.
- `ui/screens/video/VideoLessonScreen.kt:68` `loadLesson()` 3-stage fallback, `VastavikYouTubePlayer.kt:51` `YouTubePlayerView` `loadVideo`/`cueVideo`, `CourseModel.kt:134` `videoFormat="vscode"` vs backend `screen_recording`.
- `ui/screens/home/HomeScreen.kt:105` `sampleCourses` hardcoded for grid, `283` `fetchedCourses` only for Popular Topics (1.0.46), no force refresh on Learn re-click.
- No `SwipeRefresh` anywhere.

---

## 3) Implementation Plan (in order, after your approval)

### Phase A — Backend First (E:\vastavikCodingStuff\vastavikLearning-backend-app)
**Branch:** `fix/video-refresh-playback-admin` from `origin/main`

1. **DB Schema** `app/models/schemas.py:1741`
   - Add to `VideoCreate`: `privacy: str = "unlisted"` (`public|unlisted|private`), `is_published: bool = True`, keep `whiteboard_image_url`, `code_sample`, `shorts_url`, ensure `video_type` enum stays, add `order`.
   - Ensure `LessonResponse` already has `privacy`/`is_published` if not, add.

2. **Firebase** `app/db/firebase.py:339`
   - Ensure `videos` docs store `privacy`, `is_published`, `whiteboard_image_url`, `code_sample`, `shorts_url` as provided; no new collection.

3. **Admin Dashboard Router** `app/routers/admin_dashboard.py:1741,1767,1803`
   - `VideoCreate` include `privacy`, `is_published`, `whiteboard_image_url`, `code_sample`, `shorts_url` with extraction for shorts (`youtube.com/shorts/` → set `video_type="short"` if not already).
   - `POST /admin/videos`: call `invalidate_catalog_cache()` and also `invalidate_lesson_cache()` if exists; set `isPublished = body.is_published`.
   - `DELETE /admin/videos/{id}`: also try to delete nested `lessons` collection_group docs with same id and subpart refs (best-effort), then `invalidate_catalog_cache()`.
   - `POST /admin/courses/{cid}/parts/{pid}/lessons`: after linking, `invalidate_catalog_cache()` (already does for course create, add for lesson link).
   - Add query `?privacy=` filter to `GET /admin/videos` (optional).

4. **Catalog Router** `app/routers/catalog.py:23,60`
   - Add `GET /api/v1/lessons/{id}` to check `is_published`/`privacy`? For now return even if private (since admin wants it visible), but ensure `is_premium` + `privacy` both considered; add `shorts_url` population from `youtube_url` when `video_format=="short"`.
   - Ensure `invalidate` is called correctly; add `GET /api/v1/catalog/home?force=true` param to bypass cache when `learn` page requests `?force=true`.
   - Fix `video_format` normalization to handle `shorts` → `short`, `screen` → `screen_recording`, `vscode` → `screen_recording` (already at 103-106).

5. **Config** `app/core/config.py` — no new env.

6. **Tests** `tests/test_backend.py` / `tests/test_growth.py` — add `test_video_create_and_fetch_instant` and `test_video_privacy_and_whiteboard`.

### Phase B — Admin Web (E:\vastavikCodingStuff\vastavikLearning-admin-web)
**Branch:** `fix/video-refresh-playback-admin` from `origin/main`

1. **Types** `types/api.ts:153`
   - Add `privacy: "public"|"unlisted"|"private"` to `VideoLesson`, optional `is_published?: boolean`.

2. **Upload Modal** `components/courses/UploadVideoModal.tsx:46,74`
   - Replace `always ""` with real inputs: `whiteboard_image_url` (URL input + preview), `code_sample` (monaco/textarea), `shorts` is already `video_type==="short"` but add helper text “Paste youtube.com/shorts/... for vertical”.
   - Add **Privacy** select (Public / Unlisted / Private, default Unlisted) and **Published** toggle, plus **Shorts URL** auto-filled when `video_type==="short"`.
   - Show `course_id`, `part_id` selector when opened from curriculum.
   - On `onSubmit`, include `privacy`, `is_published`, `whiteboard_image_url`, `code_sample`, `shorts_url` in `POST /admin/videos` payload.

3. **Videos Page** `app/dashboard/videos/page.tsx:84`
   - Extract `fetchVideos` to `useCallback`, add `isOffline`, `error`, `refetch`, `Refresh` button (icon `RotateCw`), `useFocus`/`onClick` re-fetch, `invalidate` after upload/delete, show `privacy` pill, `whiteboard`/`code` indicators.

4. **useCourses Hook** `hooks/useCourses.ts` — add `invalidate` after `addLessonToPart`.

5. **No privacy toggle before:** now visible; admin can set public/unlisted/private per video.

### Phase C — Android App (E:\vastavikCodingStuff\vastavikLearning-app)
**Branch:** `fix/video-refresh-playback-admin` from `origin/main` (currently `1.0.46`)

1. **Home → Popular Topics already fixed in 1.0.46** — keep; **Course Catalog grid** still uses `sampleCourses` (HomeScreen.kt:105). Change to use `fetchedCourses` (same source as Popular Topics) so Home grid and Popular Topics both reflect backend courses.

2. **Learn Page Refresh** `ui/screens/learning/LearningPathScreen.kt:54` + `LearningViewModel.kt:132`
   - Add `SwipeRefresh` / `PullToRefreshBox` (Material3) wrapping `LazyColumn`, with `isRefreshing` from `LearningViewModel.isLoadingCurriculum`.
   - Add `viewModel.refreshCurriculum(force=true)` that calls `GET /api/v1/courses/{id}/curriculum?force=true` + `GET /api/v1/catalog/home?force=true` and re-collects `visitedParts`.
   - On `tabNav("learning_path")` (HomeScreen.kt:153) and on `LearningPathScreen` `LaunchedEffect` with `Lifecycle.RESTART`, trigger `refresh`.
   - Fix leak: `LearningViewModel.kt:162` `streamParts().collect` inside `launch` should be `first()` or `take(1)` not infinite collect.

3. **Video Playback Fix** `ui/screens/video/VideoLessonScreen.kt:68`
   - Ensure `VideoLessonViewModel.loadLesson()` tries `api.getLesson` with HMAC + JWT, and on `is_premium` 403 shows `Snackbar: Premium content — subscribe` with `onNavigate("payment")`, not blank.
   - Fix `CourseModel.kt:134` vs `BackendModels.kt:138` mismatch: `VastavikYouTubePlayer` should accept both `screen_recording` and `vscode` as same, and handle `short` as vertical player.
   - Add error UI: if `youtubeVideoId.isBlank()`, show `Text: Video URL invalid — contact admin` and `Button: Open Curriculum`.

4. **Video Format Handling**
   - `VideoLessonScreen.kt:252` `TabRow` currently static 3 tabs; make it dynamic: if `lesson.whiteboardImageUrl.isNotBlank()` show Whiteboard tab, if `lesson.codeSample.isNotBlank()` show Code tab, if `videoFormat=="short"` show Shorts tab (vertical player), else default.
   - Ensure `VastavikYouTubePlayer.kt:51` handles `short` as 9:16 wrapper (already at 530-539), add check for `privacy`? If `privacy=="private"` and user not admin, show `Private video — ask admin for access`.

5. **Whiteboard / Code / Shorts Display**
   - `WhiteboardTab` already handles `transformable` pinch; ensure `whiteboardImageUrl` is passed from backend (now admin can set it).
   - `CodeNotesTab` should use `VsCodeSnippetView.kt` for `codeSample` (currently plain Text fallback).
   - `ShortsTab` already exists; ensure it uses `shorts_url`/`youtube_url` when `videoFormat=="short"`.

### Phase D — Cross-Cutting

- **Cache invalidation:** After any admin video create/delete/link, backend calls `invalidate_catalog_cache()`; app's Learn page `refresh` bypasses cache via `?force=true`.
- **Privacy:** Admin can set `public|unlisted|private`; backend stores it, returns in `GET /lessons`; app checks `if (lesson.privacy=="private" && !is_admin) show private message`.
- **Code/Whiteboard/Shorts:** All three are now editable in admin modal, stored in `videos` flat collection, returned via `GET /lessons`, rendered in `VideoLessonScreen` tabs.

### Phase E — Testing Checklist (manual + pytest)

- [ ] Admin upload: create video with each `video_type` + `privacy` + `whiteboard` + `code` + `shorts` → appears in `GET /admin/videos` instantly.
- [ ] Admin edit: link video to course part → appears in app's Learn path after pull-to-refresh (without kill).
- [ ] App Learn: open app → Home → Learn (Duolingo path) → pull-to-refresh → new video appears (force fetch).
- [ ] App video play: tap testing video (each format) → `VastavikYouTubePlayer` loads `youtubeVideoId`, plays, milestones logged.
- [ ] Private video: set `privacy=private` → non-admin student `GET /lessons/{id}` shows private gate, admin can still play.
- [ ] Whiteboard: upload whiteboard image URL → student Whiteboard tab shows pinch-zoom image.
- [ ] Code: paste code sample → student Code tab shows `VsCodeSnippetView` with syntax highlight.
- [ ] Shorts: set `video_type=short` + `youtube.com/shorts/...` → student Shorts tab shows vertical 9:16 player.
- [ ] Refresh: Learn page re-click (pager tab) triggers `refreshCurriculum` and Duolingo path updates.

### Phase F — Git Workflow (per repo)

For each of `vastavikLearning-backend-app`, `vastavikLearning-admin-web`, `vastavikLearning-app`:
1. Branch `fix/video-refresh-playback-admin` from `origin/main`
2. Implement Phase A/B/C respectively
3. `git push -u origin fix/video-refresh-playback-admin`
4. `gh pr create --base main --head fix/video-refresh-playback-admin --title "fix(video): instant fetch, playback, privacy + whiteboard/code/shorts, v1.0.47" --body "..."`
5. `gh pr merge --squash --delete-branch`
6. `git checkout main && git pull && git branch -D fix/video-refresh-playback-admin`

Then for `vastavikLearning-app` only:
- Bump `app/build.gradle.kts:21-22` to `47 / 1.0.47` and `companion-codeoss/build.gradle.kts:15-16` to `15 / 1.0.47` (since 1.0.46 is current)
- `.\gradlew.bat :app:assembleRelease :companion-codeoss:assembleRelease` (clean first if packageRelease flake)
- Copy to `vastavikLearning-v1.0.47.apk` + `vastavik-codeoss-extension.apk` (keep extension version in sync)
- `gh release create v1.0.47 --title "v1.0.47 - Instant Learn Refresh, Video Playback Fix, Privacy + Whiteboard/Code/Shorts" --notes-file release_notes_v1.0.47.md ./vastavikLearning-v1.0.47.apk ./vastavik-codeoss-extension.apk` (no tar.gz/zip)

---

## 4) File Manifest (absolute paths, for other models)

**Backend:** `E:\vastavikCodingStuff\vastavikLearning-backend-app\app\routers\admin_dashboard.py:1741` `app\routers\catalog.py:23` `app\db\firebase.py:339,472` `app\models\schemas.py:145` `app\main.py:188` `app\core\config.py:97` `seed_firestore_lessons.py:19`

**Admin:** `E:\vastavikCodingStuff\vastavikLearning-admin-web\app\dashboard\videos\page.tsx:84` `components\courses\UploadVideoModal.tsx:46` `types\api.ts:153` `hooks\useCourses.ts:44` `app\dashboard\courses\page.tsx:62` `app\dashboard\courses\[id]\page.tsx:25`

**App:** `E:\vastavikCodingStuff\vastavikLearning-app\app\src\main\java\com\vastavik\computer\ui\screens\learning\LearningPathScreen.kt:54` `LearningViewModel.kt:132` `ui\screens\video\VideoLessonScreen.kt:68` `VideoLessonViewModel.kt:36` `data\repository\FirestoreRepository.kt:25` `data\repository\VastavikApiRepository.kt:33` `ui\screens\home\HomeScreen.kt:105,283` `data\api\VastavikApiService.kt:51` `data\api\model\BackendModels.kt:138` `data\model\CourseModel.kt:134` `ui\components\VastavikYouTubePlayer.kt:51` `ui\navigation\AppNavHost.kt:193`

---

## 5) Open Questions for User (ask before coding if needed)

- Should `private` videos be playable via YouTube unlisted link if student has direct URL, or should backend hard-block `GET /lessons` with 403 for non-admin? (Currently propose soft gate: show “Private — contact admin” but still allow if YouTube is unlisted.)
- For whiteboard image upload, should we store image in `uploads/` via `StaticFiles` (`app/main.py:184`) or just accept external URL? (Propose URL input + optional file upload to `/admin/upload/whiteboard` if needed.)
- For shorts, should admin also set `shorts_url` separate from `youtube_url`, or is `youtube_url` with `video_type=short` sufficient? (Propose single `youtube_url` + `video_type` as before, backend will duplicate to `shorts_url` for app compatibility.)
- Confirm `Razorpay`/`Firebase` creds still valid for new `whiteboard` image hosting if we add upload route.

---

*This file is the handoff. If token expires, open this file in any model, run the explore tasks above to re-verify, then execute Phase A→F in order. Do not skip the permission step from the original brief — ask for write access, branch, Razorpay, staging confirmation before writing code (but video fix already approved per your last message).*

---

## 6) Round 2 — 2026-09-11: what was actually wrong + what changed (branches, NOT merged)

Branches (pushed, awaiting your `PR UPDATE`):
- backend `fix/learn-firebase-v2` — `system.py` (+`/health/firestore`, +`/admin/uploads/whiteboard`), `catalog.py` (+empty-catalog warning log), `admin_dashboard.py` (+400 on bad YouTube link)
- admin-web `fix/learn-firebase-v2` — `UploadVideoModal.tsx` (whiteboard/code/shorts ALWAYS visible + whiteboard file upload), `videos/page.tsx` (privacy/whiteboard/code/draft pills)
- app `fix/update-banner-neo-square` (unmerged, now also holds v1.0.60 banner + all video fixes below)

### Why uploads were not visible / things not loading (root causes)
1. **Silent in-memory fallback.** `firebase.py:70-72` logs a warning and keeps serving seed data when no credentials are found. On Render without `FIREBASE_CREDENTIALS_BASE64`, admin uploads go to a per-process dict that is invisible to the app's direct Firestore listeners and lost on restart. Fix: `GET /api/v1/health/firestore` now reports `use_live_firestore`, project, and `courses/videos/users` counts with an explicit in-memory warning; `catalog.py` logs when home returns 0 courses.
2. **Stale cache + no force path.** `catalog.py` cached home for 5 min with no bypass; app fetched once per process. Fix (already in main via #20, kept): `?force=true` on home/curriculum + `invalidate_catalog_cache()` on video create/delete/link; app `LearningViewModel.refresh()` uses force.
3. **Two sources of truth.** App reads BOTH direct Firestore streams AND backend API. Admin writes ONLY via backend. When backend is in-memory, the two disagree. Fix direction: backend is source of truth; app Learn path now prefers `getLessonV1`/`getCurriculum(force)` and only falls back to one-shot Firestore reads.
4. **VideoLessonViewModel hung forever.** Step 3 did infinite `streamLessons().collect {}` so `isLoading` never cleared → endless spinner + `title ?: "Loading..."`. Fix: `getLessonV1` first (real title/desc/whiteboard/code), then legacy, then `withTimeoutOrNull(8000) { streamLessons().first() }`, always clearing `isLoading` with a clear error + Retry/Back-to-Learn buttons.
5. **Invalid links accepted silently.** `create_video` stored empty `extracted_id` with 200. Fix: 400 `Invalid video link: could not extract an 11-character YouTube video ID...`. App player invalid state now explains the fix + reports `onError("invalid_video_link")`.
6. **Catalog grid was still hardcoded.** `HomeScreen` `sampleCourses` drove the grid while only Popular Topics used backend. Fix: grid now maps `fetchedCourses` (Firestore stream + backend catalog) with rotating gradient colors; every card goes to `learning_path`.

### Per-request fixes in this round
- **Refresh button right of course chips:** `LearningPathScreen.kt:150` — chips `LazyRow(weight=1f)` + `FilledTonalButton(Refresh)` in the SAME row (was a separate full-width row below). Same `viewModel.refresh()` force path.
- **Admin screenshot vs student:** admin modal keeps live YouTube-nocookie iframe preview + whiteboard image preview (screenshots for admin); student `VastavikYouTubePlayer` shows video directly with no screenshot overlay (only the small anti-YouTube-logo watermark shield + new `Learn with Vastavik` header chip). No student-side screenshot.
- **Title/description from backend:** `VideoLessonScreen.kt:163` now renders backend `title`/`description` as soon as `getLessonV1` returns; error box has Retry + Back to Learn; header chip `Learn with Vastavik` + PRO badge; desc fallback kept only when backend sends blank.
- **Likes/dislikes/comments work:** previously in-memory only. Now persisted per-lesson in `SharedPreferences("lesson_feedback")` (`like_/dislike_/comments_<lessonId>`) + `ActivityLog.videoLike/video_dislike/video_comment` telemetry. Counts survive process death.
- **Admin code/whiteboard/shorts alongside link:** all three inputs now ALWAYS visible in `UploadVideoModal` (whiteboard was gated on `type==whiteboard`, shorts on `type==short`); whiteboard has URL + file upload (`POST /api/v1/admin/uploads/whiteboard`, 5 MB, jpg/png/webp → `/uploads/whiteboards/wb_*.ext`); shorts has helper text; code textarea always shown.
- **No-YouTube-branding:** player keeps `modestbranding/rel=0/ivLoadPolicy=3`, watermark shield, plus `Learn with Vastavik` header so students never see a bare YouTube frame.

## 7) Round 3 - delete options + Video not found for every lesson (branches, NOT merged)

Branches (pushed, awaiting your PR UPDATE):
- backend fix/lesson-delete-resolve - firebase.py get_lesson step 3 (subpart-link resolution) + get_lesson_by_subpart, catalog.py GET /api/v1/lessons/by-subpart/{course}/{part}/{subpart}, admin_dashboard.py DELETE subparts/{sid} (?delete_video=true) + DELETE parts/{pid} (49 pytest green)
- admin-web fix/lesson-delete-resolve - useCourses.ts removeLessonFromPart/removePart (optimistic + rollback + refetch), curriculum editor per-lesson trash button + per-part Delete Part button (both with confirm)
- app fix/lesson-delete-resolve - getLessonBySubpart API+repo, ViewModel step 1.5 subpart resolution, v1.0.61 (app 61 / companion 28), compileDebugKotlin green

Why every video said Video not found: get_course_curriculum returns lesson_id = field or subpart-doc-id. For manually created subparts with no lesson_id field, the app received the SUBPART id (e.g. 8kdGIQMKX7dCbBv81m1D) as the lesson id, but get_lesson only looked in flat videos + collection_group(lessons) - never at the subpart itself. Now step 3 checks collection_group(subparts) (video fields on the doc, nested lessons, or linked flat video) and the new by-subpart endpoint resolves via the exact course/part/subpart path the app already navigates with. If Render is in in-memory mode, /health/firestore will show it - set FIREBASE_CREDENTIALS_BASE64 so uploads persist.
