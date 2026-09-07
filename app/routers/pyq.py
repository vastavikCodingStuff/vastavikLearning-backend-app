import time
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Query, Depends

from app.core.rate_limiter import rate_limit
from app.db.firebase import db
from app.models.schemas import PYQResponse

router = APIRouter(prefix="/api/v1", tags=["Past Year Questions (PYQs)"])

_pyq_cache: Dict[str, Any] = {}
_pyq_cache_timestamp: Dict[str, float] = {}
PYQ_CACHE_TTL = 600.0  # 10 minutes

DEFAULT_PYQS = [
    {
        "id": "icse_2024_1",
        "board": "ICSE",
        "year": "2024",
        "subject": "Computer Applications",
        "grade": "Class 10",
        "question": "Design a class to overload a function check() as follows:\n- void check(String str, char ch): count and print frequency of character ch in string str.\n- void check(String s1): check and display whether string is a Palindrome or not.",
        "solution": "class OverloadDemo {\n    void check(String str, char ch) {\n        int count = 0;\n        for (int i = 0; i < str.length(); i++) {\n            if (str.charAt(i) == ch) count++;\n        }\n        System.out.println(\"Frequency: \" + count);\n    }\n    void check(String s1) {\n        boolean isPal = true;\n        int n = s1.length();\n        for (int i = 0; i < n / 2; i++) {\n            if (s1.charAt(i) != s1.charAt(n - 1 - i)) { isPal = false; break; }\n        }\n        System.out.println(isPal ? \"Palindrome\" : \"Not Palindrome\");\n    }\n}",
        "marks": 15,
        "source": "sir",
    },
    {
        "id": "icse_2023_1",
        "board": "ICSE",
        "year": "2023",
        "subject": "Computer Applications",
        "grade": "Class 10",
        "question": "Define a class to accept 10 integers into a single dimensional array and search for an element using Binary Search technique.",
        "solution": "int binarySearch(int[] arr, int target) {\n    int low = 0, high = arr.length - 1;\n    while (low <= high) {\n        int mid = (low + high) / 2;\n        if (arr[mid] == target) return mid;\n        if (arr[mid] < target) low = mid + 1;\n        else high = mid - 1;\n    }\n    return -1;\n}",
        "marks": 15,
        "source": "sir",
    },
    {
        "id": "cbse_2024_1",
        "board": "CBSE",
        "year": "2024",
        "subject": "Computer Science",
        "grade": "Class 12",
        "question": "Differentiate between mutable and immutable data types in Python with suitable examples.",
        "solution": "Mutable objects can have their values modified in-place after creation (e.g. lists, dictionaries, sets). Example: l = [1, 2]; l.append(3)\nImmutable objects cannot be modified after creation (e.g. integers, floats, strings, tuples). Any modification creates a new object.",
        "marks": 2,
        "source": "sir",
    },
    {
        "id": "cbse_2024_2",
        "board": "CBSE",
        "year": "2024",
        "subject": "Informatics Practices",
        "grade": "Class 12",
        "question": "Consider a table 'STUDENT'. Write SQL queries to:\n1. Display all students with marks > 80 sorted by name.\n2. Count total students in each stream.",
        "solution": "1. SELECT * FROM STUDENT WHERE marks > 80 ORDER BY name ASC;\n2. SELECT stream, COUNT(*) FROM STUDENT GROUP BY stream;",
        "marks": 3,
        "source": "sir",
    },
]


@router.get("/pyqs", response_model=List[PYQResponse], dependencies=[Depends(rate_limit("general"))])
async def get_pyqs(
    board: Optional[str] = Query(None, description="ICSE, CBSE, ISC, WB Board"),
    year: Optional[str] = Query(None, description="Exam year e.g. 2024, 2023"),
    subject: Optional[str] = Query(None, description="Subject name e.g. Computer Applications, Computer Science"),
    grade: Optional[str] = Query(None, description="Class 10, Class 12"),
    source: Optional[str] = Query(None, description="'sir', 'board', or 'ai'"),
):
    """
    Fetches verified past year board exam questions and official solutions.
    Results are cached in-memory with 10-minute TTL for fast sub-5ms response times.
    """
    cache_key = f"{board}_{year}_{subject}_{grade}_{source}"
    now = time.time()
    if cache_key in _pyq_cache and (now - _pyq_cache_timestamp.get(cache_key, 0)) < PYQ_CACHE_TTL:
        return _pyq_cache[cache_key]

    items = list(DEFAULT_PYQS)

    try:
        if db.use_live_firestore:
            ref = db._firestore_client.collection("pyqs")
            if board:
                ref = ref.where("board", "==", board)
            docs = [d.to_dict() | {"id": d.id} for d in ref.stream()]
            if docs:
                items = docs + items
    except Exception:
        pass

    if board and board.lower() != "all":
        items = [i for i in items if i.get("board", "").lower() == board.lower()]
    if year and year.lower() != "all":
        items = [i for i in items if i.get("year", "") == str(year)]
    if subject:
        items = [i for i in items if subject.lower() in i.get("subject", "").lower()]
    if grade and grade.lower() != "all":
        items = [i for i in items if i.get("grade", "").lower() == grade.lower()]
    if source:
        items = [i for i in items if i.get("source", "sir").lower() == source.lower()]

    result = [PYQResponse(**i) for i in items]
    _pyq_cache[cache_key] = result
    _pyq_cache_timestamp[cache_key] = now
    return result
