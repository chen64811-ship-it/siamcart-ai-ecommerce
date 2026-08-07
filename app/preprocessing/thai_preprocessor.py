"""
Thai Text Preprocessing Module
Handles: Thai normalization, code-switching, entity extraction
References the PyThaiNLP ecosystem (Phatthiyaphaibun et al., 2023)
"""
import re
from typing import Dict, List, Optional, Tuple

try:
    from pythainlp.tokenize import word_tokenize
    from pythainlp.util import normalize as thai_normalize
    PYTHAINLP_AVAILABLE = True
except ImportError:
    PYTHAINLP_AVAILABLE = False
    print("WARNING: PyThaiNLP not installed. Using fallback tokenization.")


class ThaiTextPreprocessor:
    """Thai-language text preprocessing for e-commerce customer support."""

    # Common Thai e-commerce patterns
    ORDER_PATTERNS = [
        r'ORD[-: ]?(\d{4,8})',
        r'คำสั่งซื้อ[#:]?(\d{4,8})',
        r'ใบสั่งซื้อ[#:]?(\d{4,8})',
        r'TRK[-: ]?(\d{4,12})',
        r'(?:เลข)?(?:ติดตาม|tracking)[#:]?(\w{4,20})',
    ]

    # Common code-switching patterns (Thai + English mixed)
    CODE_SWITCH_PATTERNS = {
        'order': ['order', 'ออเดอร์', 'คำสั่งซื้อ'],
        'return': ['return', 'คืน', 'ส่งคืน', 'เคลม'],
        'refund': ['refund', 'เงินคืน', 'คืนเงิน'],
        'shipping': ['shipping', 'จัดส่ง', 'ส่งของ', 'ขนส่ง'],
        'tracking': ['tracking', 'ติดตาม', 'เช็คพัสดุ'],
        'policy': ['policy', 'นโยบาย', 'เงื่อนไข', 'กติกา'],
        'price': ['price', 'ราคา', 'เท่าไหร่'],
        'cancel': ['cancel', 'ยกเลิก'],
    }

    def __init__(self):
        self._compile_order_patterns()

    def _compile_order_patterns(self):
        self._order_regexes = [re.compile(p, re.IGNORECASE) for p in self.ORDER_PATTERNS]

    def normalize(self, text: str) -> str:
        """Normalize Thai text: normalize unicode, collapse whitespace."""
        if PYTHAINLP_AVAILABLE:
            text = thai_normalize(text)
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        # Normalize common chat abbreviations
        text = re.sub(r'(?<!\w)k+a+', 'ค่ะ', text, flags=re.IGNORECASE)
        text = re.sub(r'(?<!\w)(krub|ครับ)', 'ครับ', text, flags=re.IGNORECASE)
        return text

    def tokenize(self, text: str) -> List[str]:
        """Tokenize Thai text."""
        if PYTHAINLP_AVAILABLE:
            return word_tokenize(text, engine='newmm')
        # Simple fallback: split on whitespace and punctuation
        return re.findall(r'[\w]+', text)

    def extract_order_id(self, text: str) -> Optional[str]:
        """Extract order/tracking ID from text."""
        for regex in self._order_regexes:
            match = regex.search(text)
            if match:
                return match.group(1)
        return None

    def detect_intent_keywords(self, text: str) -> Dict[str, float]:
        """Detect intent category confidence based on keywords."""
        text_lower = text.lower()
        scores = {}
        for intent, keywords in self.CODE_SWITCH_PATTERNS.items():
            score = sum(1 for kw in keywords if kw.lower() in text_lower)
            scores[intent] = score / len(keywords) if keywords else 0
        return scores

    def detect_code_switching(self, text: str) -> Dict[str, float]:
        """Detect Thai/English code-switching ratio."""
        thai_chars = sum(1 for c in text if '\u0E00' <= c <= '\u0E7F')
        english_chars = sum(1 for c in text if c.isascii() and c.isalpha())
        total = thai_chars + english_chars
        if total == 0:
            return {"thai": 0, "english": 0, "mixed": False}
        thai_ratio = thai_chars / total
        return {
            "thai": thai_ratio,
            "english": 1 - thai_ratio,
            "mixed": 0.2 < thai_ratio < 0.8,
        }

    def extract_entities(self, text: str) -> Dict:
        """Extract all relevant entities from customer message."""
        order_id = self.extract_order_id(text)
        intent_scores = self.detect_intent_keywords(text)
        code_switch = self.detect_code_switching(text)

        # Detect amounts (THB)
        amount_pattern = re.compile(r'(?:บาท|THB|฿|baht)?\s*(\d{2,6})\s*(?:บาท|THB|฿|baht)?', re.IGNORECASE)
        amounts = [m.group(1) for m in amount_pattern.finditer(text)]

        return {
            "order_id": order_id,
            "intent_scores": intent_scores,
            "code_switching": code_switch,
            "amounts": amounts,
            "has_order_ref": order_id is not None,
            "has_amount_ref": len(amounts) > 0,
        }

    def preprocess(self, text: str) -> Dict:
        """Full preprocessing pipeline."""
        normalized = self.normalize(text)
        tokens = self.tokenize(normalized)
        entities = self.extract_entities(normalized)
        return {
            "original": text,
            "normalized": normalized,
            "tokens": tokens,
            "entities": entities,
        }
