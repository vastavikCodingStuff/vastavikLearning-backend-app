import re
from typing import Dict, Any, List

# Prohibited / Sensitive / Bad query categories for educational platform moderation
MODERATION_RULES: Dict[str, List[str]] = {
    "Cheating and Exploits": [
        r"\b(hack|hacking|crack|cracking|exploit|exploits|bypass|bypassing|leak\s*paper|cheat\s*code|cheat\s*exam|exam\s*leak|paper\s*leak|sql\s*inject|drop\s*table|ddos|steal\s*token|steal\s*key|jailbreak|prompt\s*injection)\b",
    ],
    "Harm and Violence": [
        r"\b(kill|killing|suicide|self\s*harm|bomb|bombs|weapon|weapons|poison|terrorist|gun|guns|murder|hurt\s*myself)\b",
    ],
    "Explicit and Inappropriate": [
        r"\b(porn|pornography|nude|nudes|sex|xxx|nsfw|bitch|bastard|fuck|fucking|shit|asshole|cunt)\b",
    ],
    "Abuse and Harassment": [
        r"\b(idiot|stupid|retard|loser|hate\s*speech|kill\s*you)\b",
    ]
}

COMPILED_RULES = {
    category: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    for category, patterns in MODERATION_RULES.items()
}

def analyze_content_safety(text: str) -> Dict[str, Any]:
    """
    Scans query or chat text against educational safety rules.
    Returns:
        {
            "is_flagged": bool,
            "flag_reasons": List[str],
            "flagged_terms": List[str]
        }
    """
    if not text:
        return {"is_flagged": False, "flag_reasons": [], "flagged_terms": []}

    reasons = []
    matched_terms = set()

    for category, regexes in COMPILED_RULES.items():
        for regex in regexes:
            matches = regex.findall(text)
            if matches:
                reasons.append(category)
                for m in matches:
                    if isinstance(m, tuple):
                        for sub_m in m:
                            if sub_m:
                                matched_terms.add(sub_m.lower())
                    elif m:
                        matched_terms.add(m.lower())

    is_flagged = len(reasons) > 0
    return {
        "is_flagged": is_flagged,
        "flag_reasons": sorted(list(set(reasons))),
        "flagged_terms": sorted(list(matched_terms)),
    }
