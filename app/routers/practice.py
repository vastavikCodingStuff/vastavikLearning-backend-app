"""
Student-Facing Practice Router [Practice Sir & AI]
Serves:
- MCQs (Sir-generated & AI-generated)
- Coding Exercises with test cases & starter code
- Predict the Output sets
- Curated Quizzes
All cached in memory with 10-minute TTL for ultra-low latency & 0 unnecessary DB reads.
"""
import time
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Query, Depends

from app.core.rate_limiter import rate_limit
from app.db.firebase import db
from app.models.schemas import (
    MCQItemResponse,
    CodingItemResponse,
    PredictOutputItemResponse,
    QuizSetResponse,
)

router = APIRouter(prefix="/api/v1/practice", tags=["Practice [Practice Sir]"])

# In-memory TTL caches for fast sub-5ms responses
_cache: Dict[str, Any] = {}
_cache_timestamps: Dict[str, float] = {}
PRACTICE_CACHE_TTL = 600.0  # 10 minutes


def get_cached(key: str) -> Optional[Any]:
    now = time.time()
    if key in _cache and (now - _cache_timestamps.get(key, 0)) < PRACTICE_CACHE_TTL:
        return _cache[key]
    return None


def set_cached(key: str, data: Any):
    _cache[key] = data
    _cache_timestamps[key] = time.time()


# Curated "Practice Sir" Baseline Data (matches ICSE Class 10 & CBSE Class 12 Syllabus)
DEFAULT_SIR_MCQS = [
    {
        "id": "sir_mcq_1",
        "title": "Java Fundamentals",
        "sub": "20 questions",
        "question": "Which of the following is not a primitive data type in Java?",
        "options": ["int", "float", "String", "boolean"],
        "correct_index": 2,
        "explanation": "String is a class / reference type in Java, while int, float, and boolean are primitive types.",
        "subject": "Java",
        "topic": "Fundamentals",
        "difficulty": "Easy",
        "source": "sir",
    },
    {
        "id": "sir_mcq_2",
        "title": "OOP Deep-Dive",
        "sub": "18 questions",
        "question": "Which OOP principle allows a class to acquire properties and behaviors of another class?",
        "options": ["Encapsulation", "Inheritance", "Polymorphism", "Abstraction"],
        "correct_index": 1,
        "explanation": "Inheritance enables a subclass to inherit fields and methods from a superclass using the 'extends' keyword.",
        "subject": "Java",
        "topic": "OOP",
        "difficulty": "Medium",
        "source": "sir",
    },
    {
        "id": "sir_mcq_3",
        "title": "Exception Handling",
        "sub": "12 questions",
        "question": "Which block in Java always executes whether an exception is handled or not?",
        "options": ["try", "catch", "finally", "throw"],
        "correct_index": 2,
        "explanation": "The finally block always executes after try-catch, commonly used for resource cleanup.",
        "subject": "Java",
        "topic": "Exception Handling",
        "difficulty": "Easy",
        "source": "sir",
    },
    {
        "id": "sir_mcq_4",
        "title": "Python Basics & Lists",
        "sub": "15 questions",
        "question": "What is the output of print([1, 2, 3] * 2)?",
        "options": ["[2, 4, 6]", "[1, 2, 3, 1, 2, 3]", "[[1, 2, 3], [1, 2, 3]]", "Error"],
        "correct_index": 1,
        "explanation": "Multiplying a list by an integer n repeats the list n times in Python.",
        "subject": "Python",
        "topic": "Lists",
        "difficulty": "Easy",
        "source": "sir",
    },
    {
        "id": "sir_mcq_5",
        "title": "SQL Queries & Aggregates",
        "sub": "14 questions",
        "question": "Which SQL function is used to return the highest value in a column?",
        "options": ["TOP()", "MAX()", "HIGHEST()", "COUNT()"],
        "correct_index": 1,
        "explanation": "MAX() returns the maximum value in the specified column.",
        "subject": "SQL",
        "topic": "Queries",
        "difficulty": "Easy",
        "source": "sir",
    },
]

DEFAULT_SIR_CODING = [
    {
        "id": "sir_code_1",
        "title": "Array Rotation",
        "difficulty": "Easy",
        "topic": "Arrays",
        "language": "java",
        "description": "Rotate an array to the right by k steps, where k is non-negative.",
        "starter_code": "public class Solution {\n    public static void rotate(int[] nums, int k) {\n        // Your code here\n    }\n}",
        "solution_code": "public class Solution {\n    public static void rotate(int[] nums, int k) {\n        k %= nums.length;\n        reverse(nums, 0, nums.length - 1);\n        reverse(nums, 0, k - 1);\n        reverse(nums, k, nums.length - 1);\n    }\n    private static void reverse(int[] nums, int s, int e) {\n        while (s < e) {\n            int t = nums[s]; nums[s++] = nums[e]; nums[e--] = t;\n        }\n    }\n}",
        "test_cases": [{"input": "[1,2,3,4,5], k=2", "expected_output": "[4,5,1,2,3]"}],
        "source": "sir",
    },
    {
        "id": "sir_code_2",
        "title": "Palindrome Check",
        "difficulty": "Easy",
        "topic": "Strings",
        "language": "java",
        "description": "Determine if a string is a palindrome ignoring case and non-alphanumeric characters.",
        "starter_code": "public class Solution {\n    public static boolean isPalindrome(String s) {\n        // Your code here\n        return false;\n    }\n}",
        "solution_code": "public class Solution {\n    public static boolean isPalindrome(String s) {\n        int i = 0, j = s.length() - 1;\n        while (i < j) {\n            while (i < j && !Character.isLetterOrDigit(s.charAt(i))) i++;\n            while (i < j && !Character.isLetterOrDigit(s.charAt(j))) j--;\n            if (Character.toLowerCase(s.charAt(i++)) != Character.toLowerCase(s.charAt(j--))) return false;\n        }\n        return true;\n    }\n}",
        "test_cases": [{"input": "\"racecar\"", "expected_output": "true"}, {"input": "\"hello\"", "expected_output": "false"}],
        "source": "sir",
    },
    {
        "id": "sir_code_3",
        "title": "Custom Sort",
        "difficulty": "Medium",
        "topic": "Sorting",
        "language": "java",
        "description": "Implement Bubble Sort or Insertion Sort to sort numbers in ascending order.",
        "starter_code": "public class Solution {\n    public static void sort(int[] arr) {\n        // Your code here\n    }\n}",
        "solution_code": "public class Solution {\n    public static void sort(int[] arr) {\n        int n = arr.length;\n        for (int i = 0; i < n - 1; i++) {\n            for (int j = 0; j < n - i - 1; j++) {\n                if (arr[j] > arr[j + 1]) {\n                    int temp = arr[j]; arr[j] = arr[j + 1]; arr[j + 1] = temp;\n                }\n            }\n        }\n    }\n}",
        "test_cases": [{"input": "[5, 2, 9, 1]", "expected_output": "[1, 2, 5, 9]"}],
        "source": "sir",
    },
    {
        "id": "sir_code_4",
        "title": "Constructor Chaining",
        "difficulty": "Medium",
        "topic": "OOP",
        "language": "java",
        "description": "Demonstrate constructor chaining using this() and super() in Java.",
        "starter_code": "class Parent {\n    Parent(String msg) { System.out.println(\"Parent: \" + msg); }\n}\nclass Child extends Parent {\n    Child() {\n        // invoke parent constructor\n    }\n}",
        "solution_code": "class Parent {\n    Parent(String msg) { System.out.println(\"Parent: \" + msg); }\n}\nclass Child extends Parent {\n    Child() {\n        super(\"Hello\");\n        System.out.println(\"Child initialized\");\n    }\n}",
        "test_cases": [{"input": "new Child()", "expected_output": "Parent: Hello\nChild initialized"}],
        "source": "sir",
    },
]

DEFAULT_SIR_PREDICT = [
    {
        "id": "sir_po_1",
        "set_number": 1,
        "title": "Loop Tracing & Conditionals",
        "topic": "Loops & Control Flow",
        "question_count": "12 Questions",
        "difficulty": "Easy",
        "code_snippet": "int sum = 0;\nfor (int i = 1; i <= 5; i++) {\n    if (i % 2 == 0) continue;\n    sum += i;\n}\nSystem.out.println(sum);",
        "expected_output": "9",
        "source": "sir",
    },
    {
        "id": "sir_po_2",
        "set_number": 2,
        "title": "String Operations & Substrings",
        "topic": "Strings & Characters",
        "question_count": "15 Questions",
        "difficulty": "Medium",
        "code_snippet": "String s = \"KNOWLEDGE\";\nSystem.out.println(s.substring(3, 7));\nSystem.out.println(s.indexOf('E', 5));",
        "expected_output": "WLED\n8",
        "source": "sir",
    },
    {
        "id": "sir_po_3",
        "set_number": 3,
        "title": "Array Indexing & Shifting",
        "topic": "1D & 2D Arrays",
        "question_count": "10 Questions",
        "difficulty": "Medium",
        "code_snippet": "int[] a = {10, 20, 30, 40};\nint x = a[1]++;\nint y = ++a[2];\nSystem.out.println(x + \" \" + y + \" \" + a[1]);",
        "expected_output": "20 31 21",
        "source": "sir",
    },
]


@router.get("/mcq", response_model=List[MCQItemResponse], dependencies=[Depends(rate_limit("general"))])
async def get_mcqs(
    subject: Optional[str] = Query(None, description="Java, Python, SQL"),
    topic: Optional[str] = Query(None),
    difficulty: Optional[str] = Query(None),
    source: Optional[str] = Query(None, description="'sir' or 'ai'"),
):
    """
    Fetches practice MCQs. Defaults to curated Sir's questions, with support for AI-generated items.
    """
    cache_key = f"mcq_{subject}_{topic}_{difficulty}_{source}"
    cached = get_cached(cache_key)
    if cached is not None:
        return cached

    items = list(DEFAULT_SIR_MCQS)

    # If live Firestore available, query and merge
    try:
        if db.use_live_firestore:
            ref = db._firestore_client.collection("mcqs")
            if subject:
                ref = ref.where("subject", "==", subject)
            docs = [d.to_dict() | {"id": d.id} for d in ref.stream()]
            if docs:
                items = docs + items
    except Exception:
        pass

    # Filter
    if subject:
        items = [i for i in items if i.get("subject", "").lower() == subject.lower()]
    if topic:
        items = [i for i in items if topic.lower() in i.get("topic", "").lower()]
    if source:
        items = [i for i in items if i.get("source", "sir").lower() == source.lower()]

    result = [MCQItemResponse(**i) for i in items]
    set_cached(cache_key, result)
    return result


@router.get("/coding", response_model=List[CodingItemResponse], dependencies=[Depends(rate_limit("general"))])
async def get_coding_exercises(
    language: Optional[str] = Query(None, description="java, python, cpp, javascript"),
    difficulty: Optional[str] = Query(None),
    source: Optional[str] = Query(None, description="'sir' or 'ai'"),
):
    """
    Fetches curated coding challenges with starter code and test cases.
    """
    cache_key = f"coding_{language}_{difficulty}_{source}"
    cached = get_cached(cache_key)
    if cached is not None:
        return cached

    items = list(DEFAULT_SIR_CODING)

    try:
        if db.use_live_firestore:
            ref = db._firestore_client.collection("coding_exercises")
            docs = [d.to_dict() | {"id": d.id} for d in ref.stream()]
            if docs:
                items = docs + items
    except Exception:
        pass

    if language:
        items = [i for i in items if i.get("language", "").lower() == language.lower()]
    if difficulty:
        items = [i for i in items if i.get("difficulty", "").lower() == difficulty.lower()]
    if source:
        items = [i for i in items if i.get("source", "sir").lower() == source.lower()]

    result = [CodingItemResponse(**i) for i in items]
    set_cached(cache_key, result)
    return result


@router.get("/predict-output", response_model=List[PredictOutputItemResponse], dependencies=[Depends(rate_limit("general"))])
async def get_predict_output_sets(
    topic: Optional[str] = Query(None),
    source: Optional[str] = Query(None, description="'sir' or 'ai'"),
):
    """
    Fetches 'Predict the Output' problem sets with code snippets.
    """
    cache_key = f"po_{topic}_{source}"
    cached = get_cached(cache_key)
    if cached is not None:
        return cached

    items = list(DEFAULT_SIR_PREDICT)

    if topic:
        items = [i for i in items if topic.lower() in i.get("topic", "").lower()]
    if source:
        items = [i for i in items if i.get("source", "sir").lower() == source.lower()]

    result = [PredictOutputItemResponse(**i) for i in items]
    set_cached(cache_key, result)
    return result


@router.get("/quiz", response_model=List[QuizSetResponse], dependencies=[Depends(rate_limit("general"))])
async def get_quizzes(
    subject: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
):
    """
    Fetches practice quiz sets.
    """
    cache_key = f"quiz_{subject}_{course_id}"
    cached = get_cached(cache_key)
    if cached is not None:
        return cached

    items = [
        {"id": "quiz_1", "title": "ICSE Java Mastery", "subject": "Java", "question_count": 20, "created_at": "2024-01-01T00:00:00Z"},
        {"id": "quiz_2", "title": "OOP Concepts Quiz", "subject": "Java", "question_count": 15, "created_at": "2024-01-01T00:00:00Z"},
        {"id": "quiz_3", "title": "Python Control Flow", "subject": "Python", "question_count": 12, "created_at": "2024-01-01T00:00:00Z"},
    ]

    try:
        if db.use_live_firestore:
            ref = db._firestore_client.collection("practice_quizzes")
            docs = [d.to_dict() | {"id": d.id} for d in ref.stream()]
            if docs:
                items = docs + items
    except Exception:
        pass

    if subject:
        items = [i for i in items if i.get("subject", "").lower() == subject.lower()]

    result = [QuizSetResponse(**i) for i in items]
    set_cached(cache_key, result)
    return result
