"""Étape 5 : résume chaque article d'une une et en extrait les thèmes.

LLM local (GGUF via llama-cpp-python, GPU). Stratégie map-reduce, imposée par les
4 Go de VRAM : un prompt court par article, puis le résumé global se calcule SUR LES
RÉSUMÉS d'articles, jamais sur le texte brut.

Les thèmes sont contraints à une taxonomie fermée pour rester comparables d'une date
à l'autre ; ce que le modèle invente en plus bascule dans les mots-clés libres.

    python summarize.py <blocks.json> <summary.json>

Les imports lourds (llama_cpp) sont paresseux : le module s'importe sans GPU.
"""
import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

from articles import _fold, article_text, group_articles
from blocks_util import paper_date, paper_name

MODEL_DIR = Path(__file__).resolve().parent / "models" / "llm"
MODEL_PRIMARY = "qwen2.5-3b-instruct-q4_k_m.gguf"     # Qwen/Qwen2.5-3B-Instruct-GGUF
MODEL_FALLBACK = "qwen2.5-1.5b-instruct-q4_k_m.gguf"  # repli si la VRAM manque

MAX_BODY_CHARS = 1800
MAX_SUMMARY_CHARS = 400
MAX_GLOBAL_CHARS = 900
MAX_KEYWORDS = 8
MAX_THEMES = 3                # le modèle a tendance à tout cocher : on garde les 3 premiers
MIN_BODY_CHARS = 120          # en dessous, pas la peine de déranger le modèle
# La réduction globale doit tenir dans n_ctx : 19 résumés × 400 car. + 512 tokens de
# génération dépassaient 3072 tokens, et le modèle rendait n'importe quoi.
GLOBAL_ITEM_CHARS = 200
GLOBAL_MAX_ITEMS = 15

TAXONOMY = [
    "politique intérieure", "politique étrangère", "guerre", "économie",
    "faits divers", "justice", "société", "culture", "sciences",
    "sport", "nécrologie", "publicité", "religion", "colonies",
]
# clés repliées (sans accents, minuscules) -> libellé canonique
SYNONYMS = {
    "diplomatie": "politique étrangère", "affaires etrangeres": "politique étrangère",
    "international": "politique étrangère", "finance": "économie", "bourse": "économie",
    "commerce": "économie", "industrie": "économie", "social": "société",
    "crime": "faits divers", "criminalite": "faits divers", "accident": "faits divers",
    "proces": "justice", "tribunal": "justice", "theatre": "culture",
    "litterature": "culture", "spectacle": "culture", "arts": "culture",
    "obseques": "nécrologie", "deces": "nécrologie", "reclame": "publicité",
    "annonces": "publicité", "politique": "politique intérieure",
    "parlement": "politique intérieure", "gouvernement": "politique intérieure",
    "militaire": "guerre", "armee": "guerre", "front": "guerre",
    "science": "sciences", "technique": "sciences", "eglise": "religion",
    "empire": "colonies", "outre-mer": "colonies",
}
_TAXO_FOLDED = {_fold(t): t for t in TAXONOMY}

SYSTEM = ("Tu es un archiviste de la presse française ancienne. "
          "Tu réponds uniquement par un objet JSON valide, rédigé en français.")

_SCHEMA_LINE = ('- "summary" : {sum_spec}\n'
                '- "themes"  : 1 à 3 thèmes, les PLUS PERTINENTS seulement, choisis '
                'STRICTEMENT dans cette liste : {taxo}\n'
                '- "keywords": 3 à 6 mots-clés libres (noms propres, lieux, sujets).')

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "themes": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "themes", "keywords"],
}


# ───────────────────────────── prompts (purs) ───────────────────────────

def _schema_block(sum_spec):
    return _SCHEMA_LINE.format(sum_spec=sum_spec, taxo=", ".join(TAXONOMY))


def build_article_prompt(headline, body, *, max_chars=MAX_BODY_CHARS, strict=False):
    """Prompt de résumé d'un article. `strict` = réessai après un JSON invalide."""
    body = (body or "").strip()[:max_chars]
    head = (headline or "").strip() or "(sans titre)"
    p = ("Voici un article de la une d'un journal français ancien, transcrit par OCR "
         "(le texte peut contenir des erreurs de lecture).\n\n"
         f"TITRE : {head}\n"
         f"TEXTE : {body}\n\n"
         "Rends un objet JSON avec exactement ces clés :\n"
         + _schema_block("un résumé factuel de 1 à 2 phrases, en français.") + "\n\n"
         "N'invente rien qui ne figure pas dans le texte.")
    if strict:
        p += ("\n\nATTENTION : réponds UNIQUEMENT par l'objet JSON, "
              "sans aucun texte autour et sans balises de code.")
    return p


def build_global_prompt(article_summaries, date, *, strict=False,
                        item_chars=GLOBAL_ITEM_CHARS, max_items=GLOBAL_MAX_ITEMS):
    """Réduction : synthèse de la une à partir des résumés d'articles (pas du texte brut).

    Le nom du journal est volontairement ABSENT du prompt : quand il y figurait, le
    modèle se contentait de le recopier comme résumé (« Le Temps »). Les résumés sont
    tronqués et plafonnés en nombre pour rester dans la fenêtre de contexte.
    """
    items = [" ".join(s.split())[:item_chars]
             for s in (article_summaries or []) if s and s.strip()][:max_items]
    lines = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(items))
    when = f", le {date}" if date else ""
    p = (f"Résumés des articles parus en une{when} :\n\n{lines}\n\n"
         "Rends un objet JSON avec exactement ces clés :\n"
         + _schema_block("une synthèse de 3 à 5 phrases des principaux sujets traités "
                         "dans cette une. Commence par « Cette une ». N'emploie ni "
                         "« l'article » ni « le texte » (il s'agit d'une page entière) "
                         "et n'énumère pas les thèmes dans le résumé.") + "\n\n"
         "Appuie-toi uniquement sur les résumés ci-dessus.")
    if strict:
        p += ("\n\nATTENTION : réponds UNIQUEMENT par l'objet JSON, "
              "sans aucun texte autour et sans balises de code.")
    return p


# ───────────────────────────── sortie structurée (pur) ──────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S | re.I)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _first_object(s):
    """Extrait le premier {...} équilibré, en ignorant les accolades dans les chaînes."""
    start = s.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def parse_llm_json(raw):
    """Parse tolérant : balises ```json, prose autour, virgules traînantes. None si échec."""
    if not raw or not isinstance(raw, str):
        return None
    m = _FENCE_RE.search(raw)
    if m:
        raw = m.group(1)
    chunk = _first_object(raw)
    if chunk is None:
        return None
    for candidate in (chunk, _TRAILING_COMMA_RE.sub(r"\1", chunk)):
        try:
            out = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        return out if isinstance(out, dict) else None
    return None


def normalize_themes(raw_themes, raw_keywords=()):
    """Garantit themes ⊆ TAXONOMY. Un thème non reconnu n'est pas jeté : il devient
    un mot-clé. Renvoie (themes ordonnés selon la taxonomie, keywords dédupliqués)."""
    found, extra = set(), []
    for t in (raw_themes or []):
        if not isinstance(t, str):
            continue
        key = _fold(t).strip()
        if key in _TAXO_FOLDED:
            found.add(_TAXO_FOLDED[key])
        elif key in SYNONYMS:
            found.add(SYNONYMS[key])
        elif key:
            extra.append(t.strip().lower())

    kws, seen = [], set()
    for k in list(raw_keywords or []) + extra:
        if not isinstance(k, str):
            continue
        k = " ".join(k.split()).lower()
        if k and k not in seen:
            seen.add(k)
            kws.append(k)
    themes = [t for t in TAXONOMY if t in found][:MAX_THEMES]
    return themes, kws[:MAX_KEYWORDS]


def coerce_summary(parsed, *, max_chars=MAX_SUMMARY_CHARS):
    """Répare une sortie de modèle : clés manquantes, types faux, résumé trop long."""
    parsed = parsed if isinstance(parsed, dict) else {}
    s = parsed.get("summary")
    summary = " ".join(str(s).split())[:max_chars] if isinstance(s, (str, int, float)) else ""
    themes_raw = parsed.get("themes")
    kw_raw = parsed.get("keywords")
    themes, keywords = normalize_themes(
        themes_raw if isinstance(themes_raw, list) else [],
        kw_raw if isinstance(kw_raw, list) else [])
    return {"summary": summary, "themes": themes, "keywords": keywords}


def first_sentence(text, *, max_chars=300):
    """Repli extractif quand le modèle échoue."""
    t = " ".join((text or "").split())
    if not t:
        return ""
    m = re.search(r"(.+?[.!?])(\s|$)", t)
    return (m.group(1) if m else t)[:max_chars]


# ───────────────────────────── orchestration (generate injecté) ─────────

def summarize_article(generate, headline, body):
    """generate(prompt) -> str. Un réessai strict, puis repli extractif : jamais d'exception."""
    parsed = parse_llm_json(generate(build_article_prompt(headline, body)))
    if parsed is None:
        parsed = parse_llm_json(generate(build_article_prompt(headline, body, strict=True)))
    if parsed is None:
        return {"summary": first_sentence(body), "themes": [], "keywords": [], "degraded": True}
    out = coerce_summary(parsed)
    out["degraded"] = False
    return out


def summarize_global(generate, summaries, date):
    parsed = parse_llm_json(generate(build_global_prompt(summaries, date)))
    if parsed is None:
        parsed = parse_llm_json(generate(build_global_prompt(summaries, date, strict=True)))
    if parsed is None:
        return {"summary": " ".join(summaries)[:MAX_GLOBAL_CHARS],
                "themes": [], "keywords": [], "degraded": True}
    out = coerce_summary(parsed, max_chars=MAX_GLOBAL_CHARS)
    out["degraded"] = False
    return out


def summarize_page(data, generate, *, model_name="unknown"):
    """blocks.json (dict) + generate(prompt)->str  ->  dict summary.json."""
    slug = data["slug"]
    grouped = group_articles(data["blocks"], data["img_w"], data["img_h"])

    articles = []
    for a in grouped["articles"]:
        body = article_text(a)
        headline = (a["headline"] or "").replace("\n", " ").strip()
        if len(body) < MIN_BODY_CHARS and not headline:
            continue                                  # bribe sans intérêt
        if len(body) < MIN_BODY_CHARS:
            res = {"summary": first_sentence(body) or headline,
                   "themes": [], "keywords": [], "degraded": True}
        else:
            res = summarize_article(generate, headline, body)
        articles.append({"id": len(articles), "headline": headline,
                         "summary": res["summary"], "themes": res["themes"],
                         "keywords": res["keywords"], "degraded": res["degraded"],
                         "block_ids": a["block_ids"], "box": a["box"],
                         "columns": a["columns"], "n_chars": len(body)})

    paper, date = paper_name(slug), paper_date(slug)
    glob = summarize_global(generate, [a["summary"] for a in articles], date)

    return {
        "slug": slug, "paper": paper, "date": date,
        "model": model_name,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "global": {k: glob[k] for k in ("summary", "themes", "keywords")},
        "articles": articles,
        "meta": {"n_articles": len(articles), "n_columns": len(grouped["columns"]),
                 "n_blocks_in": len(data["blocks"]), "n_dropped_noise": len(grouped["dropped"]),
                 "n_degraded": sum(1 for a in articles if a["degraded"])},
    }


# ───────────────────────────── LLM (imports paresseux) ──────────────────

def _load_llm(model_path=None):
    """Charge le GGUF sur GPU. Replis successifs : moins de contexte, puis modèle 1,5B.
    4 Go de VRAM partagés avec le bureau Windows -> la marge est mince."""
    from llama_cpp import Llama                       # lazy : pas de GPU à l'import

    candidates = []
    if model_path:
        candidates.append((Path(model_path), 3072))
    else:
        candidates += [(MODEL_DIR / MODEL_PRIMARY, 3072), (MODEL_DIR / MODEL_PRIMARY, 2048),
                       (MODEL_DIR / MODEL_FALLBACK, 3072)]
    last = None
    for path, n_ctx in candidates:
        if not path.exists():
            last = FileNotFoundError(f"modèle absent : {path}")
            continue
        try:
            llm = Llama(model_path=str(path), n_ctx=n_ctx, n_gpu_layers=-1,
                        seed=0, verbose=False)
            print(f"  LLM : {path.name} (n_ctx={n_ctx})", flush=True)
            return llm, path.stem.lower()
        except Exception as e:                        # OOM CUDA, wheel cassée…
            print(f"  échec {path.name} n_ctx={n_ctx} : {e}", flush=True)
            last = e
    raise RuntimeError(f"aucun modèle chargeable dans {MODEL_DIR} ({last})")


def make_generator(llm, *, max_tokens=768):
    """Renvoie generate(prompt)->str, décodage déterministe + grammaire JSON.
    max_tokens généreux : si la génération est coupée, le JSON est tronqué donc invalide."""
    def generate(prompt):
        r = llm.create_chat_completion(
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": prompt}],
            temperature=0.0, top_k=1, max_tokens=max_tokens,
            response_format={"type": "json_object", "schema": JSON_SCHEMA},
        )
        return r["choices"][0]["message"]["content"]
    return generate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("blocks")
    ap.add_argument("out")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    data = json.loads(Path(args.blocks).read_text(encoding="utf-8"))
    llm, model_name = _load_llm(args.model)
    summary = summarize_page(data, make_generator(llm), model_name=model_name)

    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    m = summary["meta"]
    print(f"  {m['n_articles']} articles résumés ({m['n_degraded']} dégradés) "
          f"-> {Path(args.out).name}", flush=True)
    print(f"  thèmes de la une : {', '.join(summary['global']['themes']) or '(aucun)'}", flush=True)


if __name__ == "__main__":
    main()
