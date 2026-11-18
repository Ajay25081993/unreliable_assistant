"""Pre-execution plugins: PII redactor and prompt-injection shield (both fail-closed)."""
import re, unicodedata

# Cyrillic/Greek lookalikes -> latin, plus zero-width removal
_CONF = str.maketrans({"а":"a","е":"e","о":"o","р":"p","с":"c","х":"x","у":"y","і":"i","ј":"j","ѕ":"s","һ":"h",
                       "ԁ":"d","ɡ":"g","ո":"n","ν":"v","ο":"o","α":"a","ε":"e","ι":"i","κ":"k","ρ":"p","τ":"t",
                       "Α":"A","Β":"B","Ε":"E","Ι":"I","Ο":"O","Ρ":"P","Τ":"T","Χ":"X","Ѕ":"S","Ј":"J"})
_ZW = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)

def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).translate(_ZW).translate(_CONF)
    return s

_AT = r"(?:@|\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|\s+at\s+)"
_DOT = r"(?:\.|\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\s+dot\s+)"
EMAIL = re.compile(rf"[A-Za-z0-9._%+\-]+\s*{_AT}\s*[A-Za-z0-9\-]+(?:\s*{_DOT}\s*[A-Za-z0-9\-]+)*\s*{_DOT}\s*[A-Za-z]{{2,}}", re.I)
SSN = re.compile(r"\b\d{3}[\s\-.]?\d{2}[\s\-.]?\d{4}\b")
PHONE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s\-.]?)?(?:\(\d{3}\)|\d{3})[\s\-.]?\d{3}[\s\-.]?\d{4}(?!\w)")
SPOKEN = re.compile(r"\b(?:(?:zero|one|two|three|four|five|six|seven|eight|nine|oh)[\s\-]+){6,}"
                    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|oh)\b", re.I)

def redact_pii(text: str):
    """Returns (redacted_text, count). Normalizes homoglyphs first so smuggled PII is caught."""
    t = normalize(text); n = 0
    for tag, rx in (("EMAIL", EMAIL), ("SSN", SSN), ("PHONE", PHONE), ("PHONE", SPOKEN)):
        t, k = rx.subn(f"[{tag}]", t); n += k
    return t, n

_INJ = [
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions|rules|prompts?)",
    r"disregard (?:all |any |the |your )?(?:previous|prior|above|system|safety)? ?(?:instructions|rules|guidelines|prompts?)",
    r"(?:reveal|show|print|repeat|output|leak|dump|tell me) (?:me )?(?:your |the )?(?:hidden |secret )?(?:system|initial|original) (?:prompt|instructions|message)",
    r"what (?:is|are|were) your (?:system|initial|original) (?:prompt|instructions)",
    r"\b(?:jailbreak|dan mode|developer mode|do anything now)\b",
    r"you are now (?:an? )?(?:unrestricted|unfiltered|evil|jailbroken|different)",
    r"pretend (?:you|to) (?:have no|are not|don't have|do not have) (?:rules|restrictions|limits|guidelines)",
    r"(?:act|behave|respond) as (?:if )?(?:you (?:have|had) )?no (?:restrictions|rules|filters|guidelines)",
    r"(?:ignore|disregard|forget) (?:the |all |any |your )?(?:polic(?:y|ies)|rules|guidelines|safety)",
    r"override (?:the |your )?(?:safety|policy|policies|guardrails?|restrictions)",
    r"bypass (?:the |your )?(?:safety|policy|policies|guardrails?|filters?|approval|hitl|review)",
    r"(?:skip|without|no need for|disable) (?:the )?(?:human )?(?:approval|review|hitl)",
    r"<\s*/?\s*(?:system|assistant|instructions?)\s*>", r"\[\s*(?:system|inst)\s*\]", r"###\s*(?:system|instruction)",
    r"new (?:instructions?|rules?)\s*:", r"(?:^|\n)\s*system\s*:",
    r"from now on,? (?:you|ignore|always|never)",
    r"(?:drop|delete|truncate|alter)\s+table", r"union\s+select", r"'\s*or\s*'?1'?\s*=\s*'?1", r";\s*--", r"\bxp_cmdshell\b",
    r"\bor\s+1\s*=\s*1\b", r"(?:\.\./){2,}", r"<script", r"\$\(.*\)|`[^`]*rm\s",
    r"(?:forward|send|email|exfiltrate|upload|post) (?:all |the |this )?(?:data|conversation|context|secrets?|credentials?|keys?|history) (?:to|at)\b",
    r"(?:api[_ -]?key|password|secret|token|credential)s?\b.{0,30}\b(?:reveal|print|show|give|share|send)",
    r"(?:reveal|print|show|give|share|send)\b.{0,30}\b(?:api[_ -]?key|password|secret|token|credential)s?\b",
    r"(?:as|since) (?:the )?(?:admin|administrator|ceo|root|developer)\b.{0,40}\b(?:approve|authorize|allow|execute|grant)",
    r"(?:approve|authorize|execute)\b.{0,30}\b(?:without|bypassing)\b",
    r"base64.{0,20}(?:decode|execute)", r"(?:rot13|hex)[- ]?(?:decode|encoded) (?:instructions?|payload)",
    r"hypothetical(?:ly)?.{0,60}(?:no (?:rules|restrictions)|ignore)", r"in a fictional (?:world|story).{0,60}(?:ignore|no rules)",
    r"(?:step[- ]by[- ]step|explain how).{0,30}(?:bypass|circumvent|disable) (?:the )?(?:approval|safety|audit|hitl)",
    r"(?:other|another|all) (?:tenants?|customers?|users?)(?:'s|’s)? (?:data|records|lots?|cache|sessions?)",
    r"(?:tenant|user)_?id\s*[=:]\s*(?!.*\bmy\b)\S+.{0,20}(?:instead|override|switch)",
]
INJ = [re.compile(p, re.I | re.S) for p in _INJ]
_SPACED = re.compile(r"(?:\b\w\b[\s.\-_]+){4,}\b\w\b")

def _squash(t):  # catch i.g.n.o.r.e / i g n o r e
    return _SPACED.sub(lambda m: re.sub(r"[\s.\-_]+", "", m.group(0)), t)

def injection_score(text: str) -> list[str]:
    t = _squash(normalize(text))
    t2 = re.sub(r"[*_`~]+", "", t)
    hits = [p.pattern[:40] for p in INJ if p.search(t) or p.search(t2)]
    # Heuristic: long argument overflow
    if len(text) > 1900 or text.count("'") > 8: hits.append("overflow")
    return hits

def screen_retrieved(chunk_text: str) -> bool:
    """Indirect injection check on retrieved content. True = safe."""
    return not injection_score(chunk_text)

class PluginError(Exception): ...

def pre_hook(text: str, plugins: dict | None = None):
    """Runs PII redactor (p10) then injection shield (p20); both fail closed. Returns (clean_text, info)."""
    on = plugins or {}
    info = {"pii": 0, "blocked": False, "hits": []}
    try:
        if on.get("pii_redactor", True):
            text, info["pii"] = redact_pii(text)
        if on.get("injection_shield", True):
            hits = injection_score(text)
            if hits: info.update(blocked=True, hits=hits)
    except Exception as e:  # fail closed
        info.update(blocked=True, hits=[f"plugin_error:{type(e).__name__}"])
    return text, info
