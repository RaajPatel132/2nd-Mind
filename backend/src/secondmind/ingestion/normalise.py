"""Normalisation in code, after the model (S2.6, §6.1): categories, subtypes, predicates and
relations are matched against the workspace's existing slugs and aliases, insensitive to case,
plurals and separators, then by embedding similarity. A match is reused and recorded
("reused entertainment instead of new shows"); a new slug is created only when nothing matches.
"""

import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from secondmind.core import Normalisation

# Built-in vocabulary, so the most common slugs never drift ("husband_of" -> "spouse_of").
BUILTIN: dict[str, dict[str, tuple[str, ...]]] = {
    "relation": {
        "spouse_of": ("husband_of", "wife_of", "married_to"),
        "partner_of": ("boyfriend_of", "girlfriend_of", "dating"),
        "parent_of": ("mother_of", "father_of", "mom_of", "mum_of", "dad_of"),
        "child_of": ("son_of", "daughter_of"),
        "sibling_of": ("brother_of", "sister_of"),
        "friend_of": ("best_friend_of",),
        "colleague_of": ("coworker_of", "works_with"),
        "manager_of": ("boss_of",),
        "reports_to": ("managed_by",),
        "works_at": ("employed_by", "works_for", "employee_of"),
        "lives_in": ("based_in", "resides_in"),
        "member_of": ("part_of",),
    },
    "predicate": {
        "lives_in": ("resides_in", "based_in", "home_city", "city"),
        "works_at": ("employer", "works_for", "job"),
        "located_at": ("kept_in", "stored_in", "location", "is_in"),
        "birthday": ("born_on", "date_of_birth", "dob"),
        "diet": ("dietary_preference",),
        "allergic_to": ("allergy",),
    },
    "subtype": {
        "watch": ("to_watch", "watchlist"),
        "read": ("to_read", "reading_list"),
        "learn": ("study",),
        "buy": ("purchase", "shopping"),
        "visit": ("travel_to",),
        "gift": ("gift_idea", "present"),
        "measurement": ("workout", "exercise_log"),
        "appointment": ("appt",),
        "idea": ("thought",),
    },
}

SIMILARITY_THRESHOLD = 0.8

# Given a proposed label and candidate labels: the best candidate index and its similarity.
Similarity = Callable[[str, Sequence[str]], Awaitable[tuple[int, float] | None]]


@dataclass(frozen=True, slots=True)
class Term:
    slug: str
    aliases: tuple[str, ...] = ()
    builtin: bool = False


def slugify(text: str, *, path: bool = False) -> str:
    """ "Personal Finance" -> "personal_finance"; a category path keeps its "/" segments."""
    sep = "-" if path else "_"
    segments = text.split("/") if path else [text]
    out = []
    for segment in segments:
        words = re.findall(r"[a-z0-9]+", segment.lower().replace("&", " and "))
        if words:
            out.append(sep.join(words))
    return "/".join(out)


def match_key(text: str) -> str:
    """Insensitive to case, separators and simple plurals: "TV-Shows" == "tv show"."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "".join(_singular(w) for w in words)


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("es") and word[-3] in "sxz":
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def terms_for(vocab: str, stored: Iterable[tuple[str, Sequence[str]]]) -> list[Term]:
    """The workspace's terms, then the built-in ones it doesn't have yet."""
    terms = [Term(slug, tuple(aliases)) for slug, aliases in stored]
    have = {t.slug for t in terms}
    terms.extend(
        Term(slug, aliases, builtin=True)
        for slug, aliases in BUILTIN.get(vocab, {}).items()
        if slug not in have
    )
    return terms


Vocab = Literal["category", "subtype", "predicate", "relation"]


async def normalise(
    proposed: str,
    terms: Sequence[Term],
    *,
    vocab: Vocab,
    similarity: Similarity | None = None,
    path: bool = False,
) -> tuple[str, Normalisation]:
    """The slug to store for ``proposed``, and how it was chosen."""
    slug = slugify(proposed, path=path)
    key = match_key(slug)
    for term in terms:
        keys = {match_key(term.slug), *(match_key(a) for a in term.aliases)}
        if path and "/" not in slug:
            keys.add(match_key(term.slug.rsplit("/", 1)[-1]))
        if key in keys:
            if term.slug == slug:
                how = "built-in term" if term.builtin else "existing term"
            else:
                how = f"reused {term.slug} instead of new {slug}"
            return term.slug, Normalisation(
                vocab=vocab, proposed=proposed, chosen=term.slug, reused=True, how=how
            )
    if similarity is not None and terms:
        labels = [t.slug.replace("_", " ").replace("/", " ") for t in terms]
        best = await similarity(slug.replace("_", " ").replace("/", " "), labels)
        if best is not None and best[1] >= SIMILARITY_THRESHOLD:
            term = terms[best[0]]
            return term.slug, Normalisation(
                vocab=vocab,
                proposed=proposed,
                chosen=term.slug,
                reused=True,
                how=f"reused {term.slug} instead of new {slug} (similarity {best[1]:.2f})",
            )
    return slug, Normalisation(
        vocab=vocab, proposed=proposed, chosen=slug, reused=False, how="new: nothing similar"
    )
