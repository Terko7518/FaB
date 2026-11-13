#!/usr/bin/env python3
"""Sincroniza y normaliza la base de cartas.

Fuentes, en orden de autoridad para LEGALIDAD:
  1. Lista canónica transcrita de fabtcg.com (policy/canonical_legality.json, texto literal) -> fuente de verdad
  2. Entradas manuales oficiales (supplement/official_manual_entries.json)
  3. Repositorio the-fab-cube/flesh-and-blood-cards                       -> datos base

Para DATOS DE CARTA (texto, costo, etc.): la entrada manual oficial prevalece sobre el
repo cuando ambas existen. Cada diferencia queda registrada.

Uso:
    python3 sync.py --repo <checkout> [--policy ...] [--supplement ...] [--out data]

Nunca se inventan datos: lo ausente queda null / [] / "unknown_*".
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from taxonomy import (CR_VERSION, DECK_CARD_TYPES, ARENA_CARD_TYPES, FORMATS,  # noqa: E402
                      RARITY_NAMES, classify_types)
import legality  # noqa: E402

# CLP › Silver Age: "every Flesh and Blood rare, common, and basic (formerly known as token) rarity card"
SA_RARITIES = {"C", "R", "B", "T"}

SCHEMA_VERSION = "2.0.0"
HONORIFICS = {"Dr.", "The", "Lord", "Lady", "Sir", "Professor", "Captain"}
REPO_URL = "https://github.com/the-fab-cube/flesh-and-blood-cards"
OFFICIAL_DOMAINS = ("fabtcg.com", "cdn.fabtcg.com", "cardvault.fabtcg.com", "cards.fabtcg.com",
                    "legendstory-production-s3-public.s3.amazonaws.com")
FMT_LIST = ("cc", "blitz", "silver_age", "ll")


# ----------------------------------------------------------------- utilidades
def _int_or_none(raw):
    raw = (raw or "").strip() if isinstance(raw, str) else raw
    if isinstance(raw, int):
        return raw
    return int(raw) if raw and re.fullmatch(r"-?\d+", raw) else None


def _raw_or_none(raw):
    if raw is None:
        return None
    raw = str(raw).strip()
    return raw or None


def _date(raw):
    return raw[:10] if raw else None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_info(repo):
    def run(*a):
        try:
            return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, check=True).stdout.strip()
        except Exception:  # noqa: BLE001
            return None
    commit = run("rev-parse", "HEAD")
    return {"commit": commit, "commit_date": run("log", "-1", "--format=%cI"),
            "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "tags_at_commit": (run("tag", "--points-at", "HEAD") or "").split() or [],
            "tree_url": f"{REPO_URL}/tree/{commit}" if commit else None}


def _load(repo, name):
    with open(os.path.join(repo, "json", "english", name), encoding="utf-8") as f:
        return json.load(f)


def parse_specialization(keywords):
    out = []
    for k in keywords:
        m = re.fullmatch(r"(.+) Specialization", k)
        if m:
            out.extend(p.strip() for p in m.group(1).split(" or "))
    return out or None


def parse_essence(keywords):
    out = []
    for k in keywords:
        m = re.fullmatch(r"Essence of (.+)", k)
        if m:
            out.extend(p.strip() for p in re.split(r",| and ", m.group(1)) if p.strip())
    return out or None


# CR 2.7.3 (DOM rules.fabtcg.com, 2026-11-13), ejemplos literales de la propia regla: «Ser Boltyn, Breaker of Dawn
# (Boltyn)», «The Librarian (The Librarian)». Solo se fijan los monikers que la CR da explícitamente; no se deducen
# otros por aproximación. Clave = prefijo exacto del nombre antes de la coma.
CR_MONIKER_EXAMPLES = {"Ser Boltyn": "Boltyn", "The Librarian": "The Librarian"}


def derive_moniker(name, spec_monikers):
    """CR 2.7.3. Moniker de un ejemplo literal de la CR, confirmado por una carta de Specialization o, si no, heurístico."""
    head_name = name.split(",")[0].strip()
    if head_name in CR_MONIKER_EXAMPLES:
        return CR_MONIKER_EXAMPLES[head_name], "cr_2.7.3_example"
    best = None
    for m in spec_monikers:
        if name == m or name.startswith(m + " ") or name.startswith(m + ","):
            if best is None or len(m) > len(best):
                best = m
    if best:
        return best, "specialization"
    head = [w for w in name.split(",")[0].split() if w not in HONORIFICS] or name.split()
    return (head[0] if head else name), "heuristic"


# ------------------------------------------------- capa suplementaria oficial
MANUAL_CARD_FIELDS = {
    "name": str, "color": (str, type(None)), "pitch_raw": (str, type(None)), "cost_raw": (str, type(None)),
    "power_raw": (str, type(None)), "defense_raw": (str, type(None)), "health_raw": (str, type(None)),
    "intellect_raw": (str, type(None)), "arcane_raw": (str, type(None)), "type_text": str, "types": list,
    "traits": list, "card_keywords": list, "functional_text": (str, type(None)), "played_horizontally": bool,
    "base_legality": dict,
}
MANUAL_PRINTING_FIELDS = {
    "print_code": str, "set_id": str, "set_name": str, "edition": str, "foiling": str, "rarity": str,
    "artists": list, "flavor_text": (str, type(None)), "image_url": (str, type(None)),
    "release_date": (str, type(None)), "art_variations": list,
}


def validate_manual_entry(e):
    """Devuelve lista de problemas; vacía = entrada aceptable. No se acepta nada sin fuente oficial."""
    probs = []
    if e.get("source") != "official_manual_entry":
        probs.append("source debe ser 'official_manual_entry'")
    url = e.get("official_url") or ""
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https" or not any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS):
        probs.append(f"official_url no es https en un dominio oficial: {url!r}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(e.get("consulted_on", ""))):
        probs.append("consulted_on debe ser AAAA-MM-DD")
    if not str(e.get("entered_by", "")).strip():
        probs.append("entered_by vacío")
    for blk, spec in (("card", MANUAL_CARD_FIELDS), ("printing", MANUAL_PRINTING_FIELDS)):
        obj = e.get(blk)
        if not isinstance(obj, dict):
            probs.append(f"falta bloque {blk}")
            continue
        for k, t in spec.items():
            if k not in obj:
                probs.append(f"{blk}.{k} ausente (usar null explícito si la fuente no lo da)")
            elif not isinstance(obj[k], t):
                probs.append(f"{blk}.{k} tipo inválido")
    bl = (e.get("card") or {}).get("base_legality") or {}
    for f in FMT_LIST:
        if f not in bl or bl[f] not in (True, False, None):
            probs.append(f"card.base_legality.{f} debe ser true/false/null")
    pr = e.get("printing") or {}
    if pr.get("rarity") not in RARITY_NAMES:
        probs.append("printing.rarity no es un código válido")
    return probs


def load_supplement(path):
    if not path or not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    accepted, rejected = [], []
    for i, e in enumerate(raw.get("entries", [])):
        probs = validate_manual_entry(e)
        (rejected if probs else accepted).append({"index": i, "entry": e, "problems": probs} if probs else e)
    return accepted, rejected


def manual_to_repo_shape(e):
    """Convierte una entrada manual a la forma del repo para reutilizar la normalización."""
    c, p = e["card"], e["printing"]
    key = f'{c["name"]}|{c["pitch_raw"] or "-"}'
    cid = "manual:" + hashlib.sha1(key.encode()).hexdigest()[:16]
    pid = "manual:" + hashlib.sha1(f'{p["print_code"]}|{p["edition"]}|{p["foiling"]}|{p["rarity"]}'.encode()).hexdigest()[:16]
    bl = c["base_legality"]
    card = {
        "unique_id": cid, "name": c["name"], "color": c["color"] or "", "pitch": c["pitch_raw"] or "",
        "cost": c["cost_raw"] or "", "power": c["power_raw"] or "", "defense": c["defense_raw"] or "",
        "health": c["health_raw"] or "", "intelligence": c["intellect_raw"] or "", "arcane": c["arcane_raw"] or "",
        "types": c["types"], "traits": c["traits"], "card_keywords": c["card_keywords"],
        "abilities_and_effects": [], "ability_and_effect_keywords": [], "granted_keywords": [],
        "removed_keywords": [], "interacts_with_keywords": [],
        "functional_text": c["functional_text"] or "", "functional_text_plain": c["functional_text"] or "",
        "type_text": c["type_text"], "played_horizontally": c["played_horizontally"],
        "cc_legal": bl["cc"], "blitz_legal": bl["blitz"], "silver_age_legal": bl["silver_age"], "ll_legal": bl["ll"],
        "_manual_base_legality": bl,
        "printings": [],
    }
    printing = {
        "unique_id": pid, "set_printing_unique_id": "manual:" + p["set_id"], "id": p["print_code"],
        "set_id": p["set_id"], "edition": p["edition"], "foiling": p["foiling"], "rarity": p["rarity"],
        "expansion_slot": False, "artists": p["artists"], "art_variations": p["art_variations"],
        "flavor_text": p["flavor_text"] or "", "flavor_text_plain": p["flavor_text"] or "",
        "image_url": p["image_url"], "image_rotation_degrees": 0,
        "_manual": {"set_name": p["set_name"], "release_date": p["release_date"]},
    }
    return card, printing


RECONCILE_FIELDS = [("color", "color"), ("pitch", "pitch_raw"), ("cost", "cost_raw"), ("power", "power_raw"),
                    ("defense", "defense_raw"), ("health", "health_raw"), ("intelligence", "intellect_raw"),
                    ("type_text", "type_text"), ("functional_text", "functional_text")]


def merge_supplement(cards_src, accepted):
    """Añade o reconcilia entradas manuales. La fuente oficial manual prevalece."""
    by_key = {(c["name"], c["pitch"]): c for c in cards_src}
    recon = []
    for e in accepted:
        mcard, mprint = manual_to_repo_shape(e)
        prov = {"source": "official_manual_entry", "official_url": e["official_url"],
                "consulted_on": e["consulted_on"], "entered_by": e["entered_by"]}
        existing = by_key.get((mcard["name"], mcard["pitch"]))
        if existing is None:
            mcard["printings"].append(mprint)
            mcard["_provenance"] = {"sources": ["official_manual_entry"], "manual_entries": [prov], "field_overrides": []}
            cards_src.append(mcard)
            by_key[(mcard["name"], mcard["pitch"])] = mcard
            continue
        p = existing.setdefault("_provenance", {"sources": ["repo"], "manual_entries": [], "field_overrides": []})
        if "official_manual_entry" not in p["sources"]:
            p["sources"].append("official_manual_entry")
        p["manual_entries"].append(prov)
        for repo_k, man_k in RECONCILE_FIELDS:
            rv = (existing.get(repo_k) or "").strip() if isinstance(existing.get(repo_k), str) else existing.get(repo_k)
            mv = (e["card"].get(man_k) or "")
            mv = mv.strip() if isinstance(mv, str) else mv
            if repo_k == "functional_text":
                rv = (existing.get("functional_text_plain") or existing.get("functional_text") or "").strip()
            if rv != mv:
                rec = {"card": existing["name"], "field": repo_k, "repo_value": rv, "official_value": mv,
                       "applied": "official_manual_entry", **prov}
                recon.append(rec)
                p["field_overrides"].append(rec)
                existing[repo_k] = mv
                if repo_k == "functional_text":
                    existing["functional_text_plain"] = mv
        same = [x for x in existing["printings"] if x["id"] == mprint["id"] and x["edition"] == mprint["edition"]
                and x["foiling"] == mprint["foiling"] and x["rarity"] == mprint["rarity"]]
        if not same:
            mprint["_provenance"] = prov
            existing["printings"].append(mprint)
    return recon


# ---------------------------------------------------------------- construcción
def _review_counts(raw):
    out = {}
    for e in raw["entries"] + raw["events"] + raw["format_rules"] + raw["named_determinations"]:
        out[e["status"]] = out.get(e["status"], 0) + 1
    return out


def build(repo, policy_path, out_dir, supplement_path=None):
    t0 = dt.datetime.now(dt.timezone.utc)
    cards_src = _load(repo, "card.json")
    sets_src = _load(repo, "set.json")
    canon = legality.Canonical(policy_path)
    accepted, rejected = load_supplement(supplement_path)
    reconciliation = merge_supplement(cards_src, accepted)

    set_printing_index, sets_out = {}, []
    for s in sets_src:
        for sp in s["printings"]:
            rec = {"set_id": s["id"], "set_name": s["name"], "set_unique_id": s["unique_id"],
                   "set_printing_id": sp["unique_id"], "edition": sp["edition"],
                   "start_card_id": sp.get("start_card_id"), "end_card_id": sp.get("end_card_id"),
                   "release_date": _date(sp.get("initial_release_date")), "out_of_print": sp.get("out_of_print"),
                   "source": "repo"}
            set_printing_index[sp["unique_id"]] = rec
            sets_out.append(rec)

    spec_monikers = set()
    for c in cards_src:
        spec_monikers.update(parse_specialization(c["card_keywords"]) or [])
    hero_monikers = {c["name"]: derive_moniker(c["name"], spec_monikers) for c in cards_src if "Hero" in c["types"]}
    all_monikers = {m for m, _ in hero_monikers.values()} | spec_monikers
    printing_to_card = {p["unique_id"]: c["unique_id"] for c in cards_src for p in c["printings"]}
    name_index = {}
    for c in cards_src:
        name_index.setdefault(c["name"], []).append(c)

    cards_out, printings_out = [], []
    for c in cards_src:
        tx = classify_types(c["types"], c["type_text"])
        hero_meta = [t for t in tx["unclassified"] if t in all_monikers]
        tx["unclassified"] = [t for t in tx["unclassified"] if t not in hero_meta]
        kws = c["card_keywords"]
        ct = set(tx["card_types"])
        is_hero, is_token, is_macro = "Hero" in ct, "Token" in ct, "Macro" in ct
        is_deck = bool(ct & DECK_CARD_TYPES) and not is_token
        is_arena = not (is_hero or is_token or is_deck or is_macro) and bool(ct & ARENA_CARD_TYPES)
        category = ("hero" if is_hero else "token" if is_token else "macro" if is_macro
                    else "deck" if is_deck else "arena" if is_arena else "other")
        rarities = sorted({p["rarity"] for p in c["printings"]})

        other_faces = {}
        for p in c["printings"]:
            for info in p.get("double_sided_card_info", []) or []:
                oc = printing_to_card.get(info["other_face_unique_id"])
                if oc and oc != c["unique_id"]:
                    other_faces[oc] = {"card_id": oc, "is_dfc": info["is_DFC"], "this_face_is_front": info["is_front"]}

        hero_block = None
        if is_hero:
            mon, src = hero_monikers[c["name"]]
            hero_block = {"is_young": "Young" in tx["subtypes"], "moniker": mon, "moniker_source": src,
                          "life": _int_or_none(c["health"]), "intellect": _int_or_none(c["intelligence"]),
                          "essence_of": parse_essence(kws), "supertypes": sorted(tx["classes"] + tx["talents"])}

        provenance = c.get("_provenance", {"sources": ["repo"], "manual_entries": [], "field_overrides": []})
        rec = {
            "card_id": c["unique_id"],
            "existence": "found",
            "provenance": provenance,
            "uniqueness_key": f'{c["name"]}|{c["pitch"] or "-"}',
            "name": c["name"], "color": c["color"] or None,
            "pitch": _int_or_none(c["pitch"]), "pitch_raw": _raw_or_none(c["pitch"]),
            "cost": _int_or_none(c["cost"]), "cost_raw": _raw_or_none(c["cost"]),
            "power": _int_or_none(c["power"]), "power_raw": _raw_or_none(c["power"]),
            "defense": _int_or_none(c["defense"]), "defense_raw": _raw_or_none(c["defense"]),
            "life": _int_or_none(c["health"]), "intellect": _int_or_none(c["intelligence"]),
            "arcane_raw": _raw_or_none(c["arcane"]),
            "type_text": c["type_text"], "types_raw": c["types"], "category": category,
            "classes": tx["classes"], "talents": tx["talents"], "card_types": tx["card_types"],
            "subtypes": tx["subtypes"], "set_metatypes": tx["metatypes"], "hero_metatypes": hero_meta,
            "unclassified_types": tx["unclassified"], "is_generic": tx["is_generic"],
            "hybrid_supertype_sets": tx["hybrid_supertype_sets"], "traits": c["traits"],
            "keywords": {"card": kws, "abilities_and_effects": c["abilities_and_effects"],
                         "ability_and_effect": c["ability_and_effect_keywords"], "granted": c["granted_keywords"],
                         "removed": c["removed_keywords"], "interacts_with": c["interacts_with_keywords"]},
            "construction": {"specialization": parse_specialization(kws),
                             "essence_of": parse_essence(kws) if is_hero else None,
                             "legendary": "Legendary" in kws, "unlimited": "Unlimited" in kws,
                             "ephemeral": "Ephemeral" in kws, "incarnate": "Incarnate" in kws,
                             "universal": "Universal" in kws},
            "hero": hero_block,
            "functional_text": c["functional_text"] or None,
            "functional_text_plain": c["functional_text_plain"] or None,
            "played_horizontally": c["played_horizontally"],
            "faces": {"is_double_sided": bool(other_faces), "other_faces": list(other_faces.values())},
            "references": {"referenced_cards": c.get("referenced_cards", []),
                           "referenced_by": c.get("cards_referenced_by", [])},
            "rarities_printed": rarities,
            "printing_ids": [p["unique_id"] for p in c["printings"]],
            "print_codes": sorted({p["id"] for p in c["printings"]}),
            "first_release_date": None,
            "legality": {},
        }
        dates = []
        for p in c["printings"]:
            man = p.get("_manual")
            sp = set_printing_index.get(p["set_printing_unique_id"], {})
            rel = sp.get("release_date") if not man else man["release_date"]
            dates.append(rel)
            ds = (p.get("double_sided_card_info") or [None])[0]
            printings_out.append({
                "printing_id": p["unique_id"], "card_id": c["unique_id"], "print_code": p["id"],
                "set_id": p["set_id"], "set_name": sp.get("set_name") if not man else man["set_name"],
                "set_printing_id": p["set_printing_unique_id"], "edition": p["edition"], "foiling": p["foiling"],
                "rarity": p["rarity"], "rarity_name": RARITY_NAMES.get(p["rarity"]),
                "expansion_slot": p["expansion_slot"], "art_variations": p["art_variations"],
                "artists": p["artists"], "flavor_text": p["flavor_text"] or None,
                "flavor_text_plain": p["flavor_text_plain"] or None, "image_url": p.get("image_url") or None,
                "image_rotation_degrees": p.get("image_rotation_degrees"),
                "tcgplayer_product_id": p.get("tcgplayer_product_id"), "tcgplayer_url": p.get("tcgplayer_url"),
                "release_date": rel,
                "double_sided": None if not ds else {
                    "other_face_printing_id": ds["other_face_unique_id"],
                    "other_face_card_id": printing_to_card.get(ds["other_face_unique_id"]),
                    "is_front": ds["is_front"], "is_dfc": ds["is_DFC"]},
                "printing_restrictions": [],
                "provenance": p.get("_provenance", {"source": "official_manual_entry"} if man else {"source": "repo"}),
            })
        dates = [d for d in dates if d]
        rec["first_release_date"] = min(dates) if dates else None
        rec["_src"] = c
        cards_out.append(rec)

    conflicts, policy_names_not_found = legality.apply_legality(cards_out, name_index, canon, SA_RARITIES, FORMATS)
    for rec in cards_out:
        rec.pop("_src")
    policy_index = legality.build_policy_index(canon)

    git = _git_info(repo)
    files = ["card.json", "set.json", "banned-cc.json", "banned-blitz.json", "banned-silver-age.json",
             "banned-ll.json", "living-legend-cc.json", "living-legend-blitz.json", "restricted-ll.json"]
    manifest = {
        "schema_version": SCHEMA_VERSION, "cr_version": CR_VERSION,
        "source": {"repo": REPO_URL, **git,
                   "file_sha256": {f: sha256_file(os.path.join(repo, "json", "english", f)) for f in files}},
        "policy": {"version": "canonical-" + canon.raw["consulted_on"], "checked_on": canon.raw["consulted_on"],
                   "path": os.path.relpath(policy_path, os.path.dirname(os.path.abspath(__file__))),
                   "page_modified": canon.raw["policy_page_modified"], "counts": canon.raw["counts"],
                   "review_status_counts": _review_counts(canon.raw), "file_sha256": sha256_file(policy_path)},
        "supplement": {"path": supplement_path, "accepted": len(accepted), "rejected": len(rejected),
                       "reconciled_fields": len(reconciliation),
                       "file_sha256": sha256_file(supplement_path) if supplement_path and os.path.exists(supplement_path) else None},
        "built_at": t0.isoformat(timespec="seconds"),
        "counts": {"cards": len(cards_out), "printings": len(printings_out), "set_printings": len(sets_out),
                   "sets": len({s["set_id"] for s in sets_out}),
                   "heroes": sum(1 for c in cards_out if c["category"] == "hero")},
        "latest_set_in_data": max((s for s in sets_out if s["release_date"]), key=lambda s: s["release_date"])["set_name"],
        "heuristic_monikers": sorted(n for n, (_, src) in hero_monikers.items() if src == "heuristic"),
        "policy_names_not_found": policy_names_not_found,
    }
    # text_incomplete: la fuente no trae texto funcional. Puede ser una carta sin texto (vanilla) o un texto que falta;
    # la base no permite distinguirlo, así que se marca y el validador lo hace visible (advertencia no bloqueante).
    for c in cards_out:
        c["text_incomplete"] = not (c.get("functional_text") or "").strip()
    manifest["counts"]["text_incomplete"] = sum(1 for c in cards_out if c["text_incomplete"])
    os.makedirs(out_dir, exist_ok=True)
    outputs = [("cards.json", cards_out), ("printings.json", printings_out), ("sets.json", sets_out),
               ("manifest.json", manifest), ("legality_conflicts.json", conflicts),
               ("policy_index.json", policy_index), ("supplement_rejected.json", rejected),
               ("supplement_reconciliation.json", reconciliation)]
    for fname, obj in outputs:
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=None if fname in ("printings.json", "cards.json") else 1)
    return manifest, conflicts


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--policy", default=os.path.join(here, "policy", "canonical_legality.json"))
    ap.add_argument("--supplement", default=os.path.join(here, "supplement", "official_manual_entries.json"))
    ap.add_argument("--out", default=os.path.join(here, "data"))
    a = ap.parse_args()
    m, conf = build(a.repo, a.policy, a.out, a.supplement)
    print(json.dumps({k: m[k] for k in ("source", "counts", "supplement", "latest_set_in_data", "built_at")},
                     indent=1, ensure_ascii=False))
    print("diferencias repo vs. política oficial:", len(conf))
    print("nombres de la política no encontrados:", m["policy_names_not_found"])
