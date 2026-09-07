import os
import sys
from datetime import datetime, timezone
import firebase_admin
from firebase_admin import credentials, firestore

CRED_PATH = "e:/vastavikCodingStuff/vastavikLearning-backend-app/serviceAccountKey.json"

if not firebase_admin._apps:
    cred = credentials.Certificate(CRED_PATH)
    firebase_admin.initialize_app(cred, {"projectId": "vastavikcomputers"})

db = firestore.client()

course_id = "1VXrhIpN1HOhgD3VtABV"
part_id = "NBwy5TP9FISB5iXfaCdu"
subpart_id = "8kdGIQMKX7dCbBv81m1D"

lessons_data = [
    {
        "id": "lesson_str_01",
        "title": "1. Introduction to Strings & Memory Representation",
        "description": "Understanding String immutability, String Constant Pool (SCP), and heap memory allocation in Java for ICSE Class 10.",
        "youtubeUrl": "https://www.youtube.com/watch?v=0r1Srnkg_40",
        "youtubeVideoId": "0r1Srnkg_40",
        "duration": "18:42",
        "durationSec": 1122,
        "videoFormat": "screen",
        "whiteboardImageUrl": "https://images.unsplash.com/photo-1516321318423-f06f85e504b3?w=800&auto=format&fit=crop&q=80",
        "codeSample": "public class StringMemory {\n    public static void main(String[] args) {\n        // String pool vs Heap object\n        String s1 = \"Vastavik\";\n        String s2 = \"Vastavik\";\n        String s3 = new String(\"Vastavik\");\n        \n        System.out.println(\"s1 == s2 : \" + (s1 == s2));       // true (same SCP reference)\n        System.out.println(\"s1 == s3 : \" + (s1 == s3));       // false (different references)\n        System.out.println(\"s1.equals(s3) : \" + s1.equals(s3)); // true (value equality)\n    }\n}",
        "notes": "Key ICSE Exam Point: == compares memory addresses / references, whereas .equals() compares the actual character contents of strings.",
        "order": 1,
        "isPremium": False,
        "isPublished": True,
        "createdAt": datetime.now(timezone.utc)
    },
    {
        "id": "lesson_str_02",
        "title": "2. Essential String Methods (length, charAt, substring)",
        "description": "Deep dive into core methods tested in ICSE Section B programming questions: charAt(), substring(), indexOf(), and toUpperCase().",
        "youtubeUrl": "https://www.youtube.com/watch?v=kqtD5dpn9C8",
        "youtubeVideoId": "kqtD5dpn9C8",
        "duration": "24:15",
        "durationSec": 1455,
        "videoFormat": "whiteboard",
        "whiteboardImageUrl": "https://images.unsplash.com/photo-1544717302-de2939b7ef71?w=800&auto=format&fit=crop&q=80",
        "codeSample": "public class StringMethodsDemo {\n    public static void main(String[] args) {\n        String str = \"Computer Applications\";\n        \n        System.out.println(\"Length: \" + str.length());\n        System.out.println(\"Char at index 3: \" + str.charAt(3));\n        System.out.println(\"Substring (0, 8): \" + str.substring(0, 8));\n        System.out.println(\"Index of 'A': \" + str.indexOf('A'));\n    }\n}",
        "notes": "Remember: substring(beginIndex, endIndex) is exclusive of endIndex. Index starts at 0.",
        "order": 2,
        "isPremium": False,
        "isPublished": True,
        "createdAt": datetime.now(timezone.utc)
    },
    {
        "id": "lesson_str_03",
        "title": "3. 60-Second Quick Recap: String Buffer vs String",
        "description": "Quick vertical summary of why StringBuffer is mutable and when to use it over immutable String.",
        "youtubeUrl": "https://www.youtube.com/shorts/5BwBkWYvYh0",
        "youtubeVideoId": "5BwBkWYvYh0",
        "duration": "00:59",
        "durationSec": 59,
        "videoFormat": "shorts",
        "whiteboardImageUrl": "https://images.unsplash.com/photo-1509228468518-180dd4864904?w=800&auto=format&fit=crop&q=80",
        "codeSample": "StringBuffer sb = new StringBuffer(\"Hello\");\nsb.append(\" World\");\nSystem.out.println(sb); // Outputs \"Hello World\" without creating a new object",
        "notes": "StringBuffer is thread-safe and mutable. StringBuilder is non-thread-safe but faster.",
        "order": 3,
        "isPremium": False,
        "isPublished": True,
        "createdAt": datetime.now(timezone.utc)
    }
]

subpart_ref = (
    db.collection("courses")
    .document(course_id)
    .collection("parts")
    .document(part_id)
    .collection("subparts")
    .document(subpart_id)
)

for lesson in lessons_data:
    lesson_id = lesson["id"]
    subpart_ref.collection("lessons").document(lesson_id).set(lesson)
    print(f"Successfully seeded lesson: {lesson_id} - {lesson['title']}")

print("All lessons seeded into Firestore successfully!")
