"""Decisión del mes a partir de letras con posición y evidencia visual."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.models.schemas import FieldResult, Status
from app.utils.postprocess import MONTH_WORDS, _canonical_month, month_candidates

MIN_DECISIVE_CONFIDENCE = 0.35
WINNER_MARGIN = 1.5


@dataclass(frozen=True)
class MonthEvidence:
    word: str = ""
    confidence: float = 0.0
    candidates: tuple[str, ...] = ()
    decisive: tuple[int, ...] = ()


def letter_weight(observed: str, expected: str, position: int) -> float:
    """Conserva la letra original y puntúa sus equivalencias por separado."""
    if len(observed) != 1:
        return 0.0
    observed = observed.upper()
    if observed == expected:
        return 1.0
    equivalents = (
        {"J": "3I/"},
        {"U": "0V", "O": "0"},
        {"L": "1IC2"},
    )
    return 0.85 if observed in equivalents[position].get(expected, "") else 0.0


def rank_month_slots(readings: Sequence[tuple[str, float]],
                     support: Sequence[tuple[str, float]] = ()) -> MonthEvidence:
    """Compara posiciones; las vistas del mismo trazo no suman votos."""
    if len(readings) != 3:
        return MonthEvidence()
    views = [[(text.strip().upper(), max(0.0, min(1.0, conf)))]
             for text, conf in readings]
    if len(support) == 3:
        for view, (text, conf) in zip(views, support):
            view.append((text.strip().upper(), max(0.0, min(1.0, conf))))
    if sum(any(len(text) == 1 and conf > 0 for text, conf in view)
           for view in views) < 2:
        return MonthEvidence()
    by_month = {}
    for word, number in MONTH_WORDS:
        strengths = [max(conf * letter_weight(text, expected, index)
                         for text, conf in view)
                     for index, (view, expected) in enumerate(zip(views, word))]
        matches = sum(strength > 0 for strength in strengths)
        if matches < 2:
            continue
        score = 1.0
        for strength, view in zip(strengths, views):
            score *= max(0.05, strength) if strength else (
                0.03 if any(text for text, _ in view) else 0.05)
        entry = (score, word, strengths)
        if number not in by_month or score > by_month[number][0]:
            by_month[number] = entry
    if not by_month:
        return MonthEvidence()
    ranked = sorted(by_month.items(), key=lambda item: (-item[1][0], item[0]))
    number, (score, word, strengths) = ranked[0]
    candidates = tuple(_canonical_month(n) for n, _ in ranked)
    confidence = sum(strengths) / 3.0
    decisive = set()
    sufficient = True
    contenders = [candidates[0]]
    uncertain_positions = set()
    for other_number, (other_score, other_word, other_strengths) in ranked[1:]:
        different = [i for i in range(3) if word[i] != other_word[i]]
        decisive.update(different)
        strength = max((strengths[i] for i in different
                        if strengths[i] > other_strengths[i]), default=0.0)
        confidence = min(confidence, strength)
        if score < other_score * WINNER_MARGIN or strength < MIN_DECISIVE_CONFIDENCE:
            sufficient = False
            contenders.append(_canonical_month(other_number))
            uncertain_positions.update(different)
    if sufficient:
        return MonthEvidence(word, round(confidence, 3), candidates[:1], tuple(sorted(decisive)))
    return MonthEvidence(candidates=tuple(contenders), decisive=tuple(sorted(uncertain_positions)))


def resolve_month_cells(target: FieldResult, cells: Sequence[FieldResult]) -> None:
    """Fusiona posiciones sin usar una lectura contradictoria como ancla."""
    raw = (target.raw_value or "").strip()
    compact = "".join(ch for ch in raw if ch.isascii() and (ch.isalnum() or ch == "/"))
    readings = [((cell.raw_value or cell.value or "").strip(), cell.confidence)
                for cell in cells]
    # El máximo por posición conserva letras de cualquiera de las vistas,
    # sin promediar una discrepancia ni contar dos veces el mismo trazo.
    support = [(ch, target.confidence) for ch in compact] if len(compact) == 3 else []
    evidence = rank_month_slots(readings, support)
    cell_evidence = rank_month_slots(readings)
    global_candidates = month_candidates(raw)
    options = list(dict.fromkeys([
        *target.alternatives,
        *(_canonical_month(n) for n in global_candidates),
        *evidence.candidates,
    ]))
    candidate = _canonical_month(month_candidates(evidence.word)[0]) if evidence.word else None
    previous = target.value
    if previous and previous not in options:
        options.append(previous)
    conflict = bool(previous and candidate and previous != candidate
                    and target.status is not Status.ERROR)
    if candidate and not conflict:
        if previous == candidate and target.status is Status.OK and not target.alternatives:
            return
        target.value = candidate
        target.confidence = evidence.confidence
        target.status = Status.OK if evidence.confidence >= 0.5 else Status.WARNING
        target.source = "date_cells"
        target.inference_method = "date_cells"
        target.comment = f"Mes leído por casillas: {evidence.word}"
        # Las alternativas ya discriminadas no deben impedir que una
        # lectura completa y fiable aporte evidencia al libro.
        target.alternatives = []
        return
    if not candidate and previous and target.status is not Status.ERROR:
        cell_month = (_canonical_month(month_candidates(cell_evidence.word)[0])
                      if cell_evidence.word else None)
        disagreement = (cell_month and cell_month != previous
                        and cell_evidence.confidence >= MIN_DECISIVE_CONFIDENCE)
        # Una casilla vacía admite el mes global. Una letra distinta con
        # evidencia propia sí lo contradice y debe conservar ambas opciones.
        if not disagreement and (previous in evidence.candidates or not evidence.candidates):
            return
    if options:
        target.value = None
        target.status = Status.ERROR
        target.confidence = 0.0
        target.alternatives = options
        target.inference_method = "month_ambiguous"
        target.comment = "Mes ambiguo: " + ", ".join(options)
