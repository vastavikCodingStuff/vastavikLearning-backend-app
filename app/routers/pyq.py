from typing import Optional, List
from fastapi import APIRouter, Query, Depends

from app.core.rate_limiter import rate_limit
from app.db.firebase import db
from app.models.schemas import PYQResponse

router = APIRouter(prefix="/api/v1", tags=["Past Year Questions (PYQs)"])


@router.get("/pyqs", response_model=List[PYQResponse], dependencies=[Depends(rate_limit("general"))])
async def get_pyqs(
    board: Optional[str] = Query(None, description="ICSE or CBSE"),
    year: Optional[str] = Query(None, description="Exam year e.g. 2023, 2024"),
    subject: Optional[str] = Query(None, description="Subject name e.g. Computer Applications, Computer Science"),
):
    """
    Fetches verified past year board exam questions and official solutions.
    """
    pyqs = await db.get_pyqs(board=board, year=year, subject=subject)
    return pyqs
