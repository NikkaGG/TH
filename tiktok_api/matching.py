import re
import unicodedata


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def match(description, terms, excluded):
    """Explainable lexical match on metadata; makes no claims about video/audio semantics."""
    text = normalize(description)

    def contains(term):
        needle = normalize(term).lstrip("#")
        return bool(needle) and re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", text) is not None

    blocked = [t for t in excluded if contains(t)]
    if blocked:
        return 0, [], "Excluded terms: " + ", ".join(blocked)
    matched = [t for t in terms if contains(t)]
    score = round(100 * len(matched) / len(terms))
    return score, matched, "Metadata keyword coverage; no visual/audio analysis"
