#!/usr/bin/env python3
"""Validador de mazos de Flesh and Blood.

Uso:
    python3 validator.py deck.json [--data data] [--mode strict|exploratory]

Modos (solo cambian cómo se reportan las DIFERENCIAS repo vs. política oficial):
  strict       (por defecto) cada diferencia que afecta a una carta del mazo es error bloqueante.
  exploratory  cada diferencia es advertencia.
En ambos modos la política oficial es la fuente de verdad: una carta prohibida, Living Legend
(en CC) o fuera de rareza (SA) según la política NUNCA se permite.

Cartas no encontradas: no se concluye nada negativo. Sus estados son unknown_* salvo que la
política oficial las identifique por nombre. El resultado global puede ser INDETERMINADO.

No sugiere reemplazos (solo coincidencias ortográficas de nombres no encontrados).
"""
import argparse
import collections
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from taxonomy import FORMATS, FORMAT_ALIASES, EQUIPMENT_ZONES, RARITY_NAMES  # noqa: E402
import dependencies as DEPS  # noqa: E402

MODES = ("strict", "exploratory")
HEURISTICS = {
    "min_blue_share": 0.15,
    "max_single_type_share": 0.80,
    "min_resource_ratio": 0.9,
    "min_interaction_cards": 1,
}
HERO_RULES = {
    # Texto oficial (CardVault DYN001): "You may only have red cards in your deck."
    # deck-cards: rojas siempre (lectura literal del texto y de las notas de Dynasty).
    # arena-cards (equipo/armas, sin franja de color, CR 2.1.2a): DISPUTADO entre fuentes oficiales:
    #   CR 1.1.3a + 1.1.3 + 5.4.3 -> el "deck" del texto meta-static es el card-pool -> también deberían ser rojas;
    #   artículo oficial "The Ultimate Flex" (2023-04-12) publica listas de Emperor con equipo y armas sin color.
    #   -> disputa normativa NC-001 (policy/normative_conflicts.json). Política fijada por el usuario:
    #      deck-card no roja = error; arena-card sin color = ADVERTENCIA (resolution = unresolved_normative_conflict);
    #      arena-card de color no rojo = error; suggestion_allowed() prohíbe sugerir equipo/armas mientras siga abierta.
    "Emperor, Dracai of Aesir": {"only_red_deck_cards": True, "red_arena_cards_disputed": True,
                                 "suggestion_block": {"categories": ["arena"], "conflict_id": "NC-001"}},
    "Brevant, Civic Protector": {"unlimited_names": ["Chivalry"]},
    "Shiyana, Diamond Gemini": {"any_specialization": True},
    "Brutus, Summa Rudis": {"any_supertype_if_keyword": "Clash"},
    "The Librarian, Magister of History": {"any_supertype_if_name_contains": "Tome"},
    "Taylor": {"any_supertype_equipment": True, "distinct_equipment_names": True},
}
META_TEXT = re.compile(r"(in your deck|in your inventory|card-pool|only have)", re.I)
ERROR, WARNING, INFO, UNKNOWN = "error", "warning", "info", "unknown"


class CardDB:
    def __init__(self, data_dir):
        def load(n):
            with open(os.path.join(data_dir, n), encoding="utf-8") as f:
                return json.load(f)
        self.cards = load("cards.json")
        self.printings = load("printings.json")
        self.manifest = load("manifest.json")
        self.policy_index = load("policy_index.json")
        self.by_id = {c["card_id"]: c for c in self.cards}
        self.by_name = collections.defaultdict(list)
        for c in self.cards:
            self.by_name[_norm(c["name"]).casefold()].append(c)
        self.printing_by_id = {p["printing_id"]: p for p in self.printings}
        self.printings_by_code = collections.defaultdict(list)
        for p in self.printings:
            self.printings_by_code[p["print_code"].upper()].append(p)

    def resolve(self, entry):
        """-> (card, printing, err). err = (code, msg[, matches]). Nunca adivina entre candidatos."""
        if isinstance(entry, str):
            entry = {"name": entry}
        if entry.get("printing_id"):
            p = self.printing_by_id.get(entry["printing_id"])
            if not p:
                return None, None, ("NOT_FOUND_IN_SOURCE", f"printing_id {entry['printing_id']} no está en la fuente")
            return self.by_id[p["card_id"]], p, None
        if entry.get("card_id"):
            c = self.by_id.get(entry["card_id"])
            return (c, None, None) if c else (None, None, ("NOT_FOUND_IN_SOURCE", f"card_id {entry['card_id']} no está en la fuente"))
        if entry.get("print_code"):
            ps = self.printings_by_code.get(entry["print_code"].upper(), [])
            cids = {p["card_id"] for p in ps}
            if len(cids) > 1:
                fronts = {p["card_id"] for p in ps if not p["double_sided"] or p["double_sided"]["is_front"]}
                cids = fronts if len(fronts) == 1 else cids
            if not cids:
                return None, None, ("NOT_FOUND_IN_SOURCE", f"código {entry['print_code']} no está en la fuente")
            if len(cids) > 1:
                return None, None, ("AMBIGUOUS_CARD", f"código {entry['print_code']} corresponde a varias cartas")
            c = self.by_id[cids.pop()]
            cand = [p for p in ps if p["card_id"] == c["card_id"]]
            return c, (cand[0] if len(cand) == 1 else None), None
        name = _norm(entry.get("name") or "")
        cands = self.by_name.get(name.casefold(), [])
        if entry.get("color"):
            cands = [c for c in cands if (c["color"] or "").casefold() == entry["color"].casefold()]
        if entry.get("pitch") is not None:
            cands = [c for c in cands if c["pitch"] == entry["pitch"]]
        if not cands:
            close = difflib.get_close_matches(name.casefold(), list(self.by_name), n=3, cutoff=0.8)
            return None, None, ("NOT_FOUND_IN_SOURCE",
                                f'"{name}" no está en la fuente (snapshot {self.manifest["source"]["commit"][:7]})',
                                [self.by_name[k][0]["name"] for k in close])
        if len(cands) > 1:
            colors = sorted(c["color"] or "sin color" for c in cands)
            return None, None, ("AMBIGUOUS_CARD", f'"{name}" existe en varios colores {colors}; indica color o pitch')
        return cands[0], None, None

    def unresolved_states(self, name, color, fmt):
        """Estados de una carta ausente de la fuente: unknown salvo que la política la identifique."""
        name = _norm(name)
        pol = self.policy_index.get(name, {}).get(fmt, {})
        det = pol.get("determination")
        if det:
            return {"existence": "not_found_in_source", "legality_state": det["legality_state"],
                    "ban_state": det["ban_state"], "living_legend_state": det["living_legend_state"],
                    "review_status": det["status"], "data_completeness": det.get("data_completeness", "incomplete"),
                    "deck_construction_allowed": det.get("deck_construction_allowed", False),
                    "deck_construction_requires": det.get("deck_construction_requires"),
                    "policy_evidence": det["evidence"]}
        evidence = []
        ban = "unknown_ban_status"
        b = pol.get("banned")
        if b and (b["colors"] is None or color in (b["colors"] or [])):
            ban = "banned"
            evidence.append(b)
        if pol.get("special_use_promo"):
            ban = "special_use_promo"
            evidence.append(pol["special_use_promo"])
        if pol.get("benched"):
            ban = "banned"
            evidence.append(pol["benched"])
        ll = "unknown_living_legend"
        cc_ll = self.policy_index.get(name, {}).get("cc", {}).get("living_legend")
        if fmt == "blitz":
            ll = "not_living_legend"  # abolido para todo el formato: no depende de la carta
        elif cc_ll:
            ll = "living_legend"
            evidence.append(cc_ll)
        legality = "unknown_legality"
        if ban in ("banned", "special_use_promo") or (fmt == "cc" and ll == "living_legend"):
            legality = "illegal"
        return {"existence": "not_found_in_source", "legality_state": legality,
                "ban_state": ban, "living_legend_state": ll, "policy_evidence": evidence}


EMPEROR_SCOPE_EVIDENCE = [
    {"source": "CR 1.1.3a", "url": "https://rules.fabtcg.com/en/cr/1/", "consulted_on": "2026-11-08",
     "literal_text": "Card text of meta-static abilities refers to the player's card-pool as the player's \"deck.\""},
    {"source": "CR 1.1.3", "url": "https://rules.fabtcg.com/en/cr/1/", "consulted_on": "2026-11-08",
     "literal_text": "A player's card-pool is a collection of deck-cards and arena-cards."},
    {"source": "CR 5.4.3", "url": "https://rules.fabtcg.com/en/cr/5/", "consulted_on": "2026-11-08",
     "literal_text": "A meta-static ability is a static ability that generates effects that apply to the rules outside of the game."},
    {"source": "Dynasty release notes › Emperor, Dracai of Aesir",
     "url": "https://fabtcg.com/rules-and-policy-center/release-notes/dynasty/", "consulted_on": "2026-11-08",
     "literal_text": "You may have any Draconic cards, Warrior cards, Wizard cards etc. in your deck as long as they are red."},
    {"source": "The Ultimate Flex (artículo oficial, 2023-04-12)", "url": "https://fabtcg.com/articles/ultimate-flex/",
     "consulted_on": "2026-11-08", "literal_text": "Equipment/Weapons … Crown of Providence … Dawnblade (lista de Emperor)"},
]


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").replace("‘", "'")).strip()


def _label(card):
    return card["name"] + (f' ({card["color"]})' if card["color"] else "")


def validate(deck, db, mode="strict", require_full_pool=False):
    """require_full_pool=True: política del GENERADOR (fase 3), no regla del juego. La TRP 7.1 fija un
    máximo («combined maximum of 80»); un pool < 80 es legal. Con la bandera, un pool incompleto es error."""
    assert mode in MODES, mode
    errors, warnings, infos, unknowns = [], [], [], []
    problem = collections.defaultdict(list)
    checks = []
    discrepancies = []

    def add(sev, code, msg, card=None, section=None, extra=None):
        item = {"code": code, "message": msg}
        if card:
            item["card"] = _label(card)
            item["card_id"] = card["card_id"]
        if section:
            item["section"] = section
        if extra:
            item.update(extra)
        {ERROR: errors, WARNING: warnings, INFO: infos, UNKNOWN: unknowns}[sev].append(item)
        if card and sev in (ERROR, WARNING, UNKNOWN):
            problem[_label(card)].append(f"[{code}] {msg}")

    def check(rule, passed):
        checks.append({"rule": rule, "passed": passed})  # True / False / None (no evaluable)

    fmt_key = FORMAT_ALIASES.get(str(deck.get("format", "")).strip().casefold())
    if not fmt_key:
        add(ERROR, "UNSUPPORTED_FORMAT",
            f'Formato "{deck.get("format")}" no soportado. Soportados: Classic Constructed, Blitz, Silver Age, Living Legend.')
        return _report(deck, None, None, mode, errors, warnings, infos, unknowns, problem, {}, checks, None, [], [], db)
    spec = FORMATS[fmt_key]
    check("formato soportado", True)

    def legality_issue(card, L, section=None):
        """Bloquea según la política oficial y registra las diferencias con el repo según el modo."""
        if not L["playable"]:
            add(ERROR, ("HERO_" if card["category"] == "hero" and section == "hero" else "CARD_") + L["status"].upper(),
                _status_msg(L, spec), card, section,
                {"states": {k: L[k] for k in ("existence", "legality_state", "ban_state", "living_legend_state")}})
        for d in L["conflicts"]:
            discrepancies.append(d)
            sev = ERROR if mode == "strict" else WARNING
            add(sev, "LEGALITY_SOURCE_CONFLICT",
                f'{d["id"]} [{d["category"]}] repo={d["repo_value"]} oficial={d["official_value"]}; '
                f'aplicada la política oficial ({d["final_status"]}).', card, section,
                {"discrepancy": {k: d[k] for k in ("id", "format", "dimension", "category", "repo_value", "official_value",
                                                    "official_url", "policy_section", "consulted_on", "rule_applied",
                                                    "printings")}})

    # ----------------------------------------------------------------- héroe
    hero, hero_rules, hero_unresolved = None, {}, None
    h_entry = deck.get("hero")
    if not h_entry:
        add(ERROR, "HERO_MISSING", "No se indicó héroe.")
    else:
        h, _, err = db.resolve(h_entry)
        if err and err[0] == "NOT_FOUND_IN_SOURCE":
            hname = h_entry if isinstance(h_entry, str) else h_entry.get("name", str(h_entry))
            st = db.unresolved_states(hname, None, fmt_key)
            hero_unresolved = {"input": h_entry, "name": hname, **st}
            if st.get("deck_construction_allowed") is False:
                add(UNKNOWN, "HERO_DATA_INCOMPLETE",
                    f'"{hname}": legalidad confirmada por la política oficial ({st["legality_state"]}, '
                    f'{st["living_legend_state"]}, {st["ban_state"]}), pero data_completeness=incomplete. No se puede '
                    f'construir ni aprobar un mazo completo hasta tener: {", ".join(st.get("deck_construction_requires") or [])}.',
                    extra={"states": st})
            elif st["legality_state"] == "illegal":
                add(ERROR, "HERO_ILLEGAL_BY_POLICY_NOT_IN_SOURCE",
                    f'"{hname}" no está en la fuente, pero la política oficial lo identifica: {st["ban_state"]}, {st["living_legend_state"]}.',
                    extra={"states": st})
            else:
                add(UNKNOWN, "HERO_NOT_FOUND_IN_SOURCE",
                    f'{err[1]}. Legalidad, prohibición y Living Legend: desconocidas (no se concluye nada negativo).',
                    extra={"states": st})
        elif err:
            add(ERROR, "HERO_" + err[0], err[1])
        elif h["category"] != "hero":
            add(ERROR, "NOT_A_HERO", f'"{_label(h)}" no es una carta de héroe ({h["type_text"]}).', h)
        else:
            hero = h
    check("héroe identificado en la fuente", True if hero else (None if hero_unresolved else False))

    if hero:
        hb = hero["hero"]
        want_young = spec["hero"] == "young"
        age_ok = hb["is_young"] == want_young
        if not age_ok:
            add(ERROR, "HERO_AGE_MISMATCH",
                f'{spec["name"]} requiere héroe {"young" if want_young else "adulto"}; {hero["name"]} es '
                f'{"young" if hb["is_young"] else "adulto"}.', hero, "hero")
        check(f'héroe {"young" if want_young else "adulto"}', age_ok)
        legality_issue(hero, hero["legality"][fmt_key], "hero")
        check("héroe legal en el formato (política oficial)", hero["legality"][fmt_key]["playable"])
        hero_rules = HERO_RULES.get(hero["name"], {})
        if hero["functional_text_plain"] and META_TEXT.search(hero["functional_text_plain"]) and not hero_rules:
            add(WARNING, "HERO_RULE_UNMODELED",
                "El héroe tiene texto de construcción no modelado; revisión humana: "
                + hero["functional_text_plain"].split("\n")[0], hero, "hero")
        if hb["moniker_source"] == "heuristic":
            add(INFO, "HERO_MONIKER_HEURISTIC", f'Moniker "{hb["moniker"]}" deducido por heurística.')
    elif hero_unresolved:
        check(f'héroe {spec["hero"]}', None)
        check("héroe legal en el formato (política oficial)",
              False if hero_unresolved["legality_state"] == "illegal" else None)

    # -------------------------------------------------------------- resolución
    # Secciones del card-pool: mazo, arena (armas/equipo) e inventario (CR 4.1.7).
    # "sideboard" es el nombre antiguo del inventario (TRP: «Inventory (formerly Sideboard)»): se acepta como alias.
    sections = {s: [] for s in ("deck", "arena", "inventory")}
    unresolved = []
    raw_sections = {"deck": deck.get("deck") or [], "arena": deck.get("arena") or [],
                    "inventory": list(deck.get("inventory") or []) + list(deck.get("sideboard") or [])}
    if deck.get("sideboard"):
        add(INFO, "SIDEBOARD_ALIAS", "'sideboard' se trata como 'inventory' (nombre actual en la TRP).", section="inventory")
    for s in sections:
        for raw in raw_sections[s]:
            entry = {"name": raw} if isinstance(raw, str) else dict(raw)
            qty = int(entry.get("qty", 1))
            card, printing, err = db.resolve(entry)
            if err and err[0] == "NOT_FOUND_IN_SOURCE":
                nm = entry.get("name") or entry.get("print_code") or entry.get("card_id") or entry.get("printing_id")
                st = db.unresolved_states(entry.get("name") or "", entry.get("color"), fmt_key)
                u = {"input": entry, "section": s, "qty": qty, **st}
                if len(err) > 2 and err[2]:
                    u["spelling_matches_not_replacements"] = err[2]
                unresolved.append(u)
                if st["legality_state"] == "illegal":
                    add(ERROR, "CARD_ILLEGAL_BY_POLICY_NOT_IN_SOURCE",
                        f'"{nm}" no está en la fuente, pero la política oficial la identifica: {st["ban_state"]}, '
                        f'{st["living_legend_state"]}.', section=s, extra={"input": entry, "states": st})
                else:
                    add(UNKNOWN, "CARD_NOT_FOUND_IN_SOURCE",
                        f'{err[1]}. Estados: {st["legality_state"]}, {st["ban_state"]}, {st["living_legend_state"]}.',
                        section=s, extra={"input": entry, "states": st})
                problem[str(nm)].append("[NOT_FOUND_IN_SOURCE] estados desconocidos")
                continue
            if err:
                errors.append({"code": err[0], "message": err[1], "section": s, "input": entry})
                problem[entry.get("name") or str(entry)].append(f"[{err[0]}] {err[1]}")
                continue
            if qty < 1:
                add(ERROR, "INVALID_QTY", f"Cantidad {qty} inválida.", card, s)
                continue
            sections[s].append({"card": card, "printing": printing, "qty": qty})
    ambiguous = any(e["code"] == "AMBIGUOUS_CARD" for e in errors)
    check("todas las cartas identificadas en la fuente sin ambigüedad",
          False if ambiguous else (None if unresolved else True))

    pool = sections["deck"] + sections["arena"] + sections["inventory"]
    unresolved_deck_qty = sum(u["qty"] for u in unresolved if u["section"] == "deck")
    unresolved_pool_qty = sum(u["qty"] for u in unresolved)
    deck_size = sum(x["qty"] for x in sections["deck"]) + unresolved_deck_qty
    pool_size = sum(x["qty"] for x in pool) + unresolved_pool_qty

    # -------------------------------------------------- categoría por sección
    cat_ok, seen = True, set()
    for s, items in sections.items():
        for x in items:
            c = x["card"]
            if (c["card_id"], s) in seen:
                continue
            seen.add((c["card_id"], s))
            cat = c["category"]
            if cat in ("hero", "token", "macro", "other"):
                add(ERROR, "NOT_A_POOL_CARD", f'Categoría "{cat}": no puede formar parte del card-pool (CR 1.1.3, 1.3.2).', c, s)
                cat_ok = False
            elif s == "deck" and cat != "deck":
                add(ERROR, "ARENA_CARD_IN_DECK", "Las arena-cards no pueden estar en el mazo (CR 4.1.6a).", c, s)
                cat_ok = False
            elif s == "arena" and cat != "arena":
                add(ERROR, "DECK_CARD_IN_ARENA_SECTION", "Es una deck-card; va en 'deck' o 'inventory'.", c, s)
                cat_ok = False
            if c["construction"]["ephemeral"] or c["construction"]["incarnate"]:
                add(ERROR, "CANNOT_BE_IN_POOL", "Ephemeral/Incarnate: no puede incluirse en el card-pool.", c, s)
                cat_ok = False
    check("cada carta en la sección correcta (mazo / arena)", cat_ok)

    # ------------------------------------------------------ legalidad por carta
    unique = {}
    for x in pool:
        unique.setdefault(x["card"]["card_id"], x["card"])
    legal_ok = super_ok = spec_ok = meta_ok = True
    for c in unique.values():
        L = c["legality"][fmt_key]
        if not L["playable"]:
            legal_ok = False
        legality_issue(c, L)
        if hero:
            ok, why = _supertypes_ok(c, hero, hero_rules)
            if not ok:
                super_ok = False
                add(ERROR, "SUPERTYPE_MISMATCH", why, c)
            sp = c["construction"]["specialization"]
            if sp and not hero_rules.get("any_specialization") and hero["hero"]["moniker"] not in sp:
                spec_ok = False
                add(ERROR, "SPECIALIZATION_MISMATCH", f'Specialization de {sp}; el héroe es {hero["hero"]["moniker"]}.', c)
            if c["hero_metatypes"] and hero["hero"]["moniker"] not in c["hero_metatypes"]:
                meta_ok = False
                add(ERROR, "HERO_METATYPE_MISMATCH", f'Solo para héroes {c["hero_metatypes"]} (CR 2.6.6).', c)
            if hero_rules.get("only_red_deck_cards") and c["category"] == "deck" and c["color"] != "Red":
                super_ok = False
                add(ERROR, "HERO_RULE_COLOR",
                    f'{hero["name"]}: "You may only have red cards in your deck." Esta deck-card es '
                    f'{c["color"] or "sin color"} (CR 2.1.2: solo la franja roja hace roja una carta; CR 1.0.2a).', c)
            elif hero_rules.get("red_arena_cards_disputed") and c["category"] == "arena":
                if c["color"] and c["color"] != "Red":
                    super_ok = False
                    add(ERROR, "HERO_RULE_COLOR",
                        f'{hero["name"]}: arena-card de color {c["color"]}. Con cualquier lectura de «You may only have red '
                        f'cards in your deck» una carta con color no rojo no cumple (CR 2.1.2, 1.0.2a).', c)
                elif not c["color"]:
                    add(WARNING, "HERO_RULE_COLOR_SCOPE_DISPUTED",
                        f'{hero["name"]}: arena-card sin color (CR 2.1.2a). Disputa normativa abierta NC-001: CR 1.1.3a '
                        f'(«deck» de un texto meta-static = card-pool) frente al artículo oficial «The Ultimate Flex», que '
                        f'publica listas de Emperor con equipo y armas sin color. Una lista publicada no es una aclaración '
                        f'normativa; no se resuelve en ninguna dirección.', c,
                        extra={"resolution": "unresolved_normative_conflict", "conflict_id": "NC-001",
                               "evidence": EMPEROR_SCOPE_EVIDENCE})
        if c["unclassified_types"]:
            add(WARNING, "UNCLASSIFIED_TYPE", f'Tipos no reconocidos por el CR: {c["unclassified_types"]}.', c)
    if any(u["legality_state"] == "illegal" for u in unresolved):
        legal_ok = False
    check("ninguna carta ilegal según la política oficial",
          legal_ok if (legal_ok is False or not unresolved) else None)
    def tri(ok):
        """False si ya falló; None si no se puede evaluar todo (sin héroe o cartas no encontradas); True si pasó."""
        if not ok:
            return False
        return None if (not hero or unresolved) else True
    check("clase/talento ⊆ héroe (híbridas, Essence)", tri(super_ok))
    check("Specialization coincide con el héroe", tri(spec_ok))
    check("metatipos de héroe coinciden", tri(meta_ok))

    # ------------------------------------------------------ texto incompleto
    # Carta sin texto en la fuente (text_incomplete): legal y utilizable, pero su texto no pudo verificarse.
    # También se avisa de las cartas referenciadas por el texto (p. ej. tokens) que no tienen texto.
    text_incomplete = []
    seen_ref = set()
    for c in unique.values():
        if c.get("text_incomplete"):
            text_incomplete.append({"card": _label(c), "card_id": c["card_id"], "role": "pool"})
            add(WARNING, "TEXT_INCOMPLETE", "La fuente no trae texto funcional para esta carta (puede ser una carta sin "
                "texto o un texto ausente; la base no lo distingue). Legal y no bloqueante, pero su texto no pudo verificarse.", c)
        for rid in (c.get("references") or {}).get("referenced_cards") or []:
            r = db.by_id.get(rid)
            if r and r.get("text_incomplete") and rid not in seen_ref and rid not in unique:
                seen_ref.add(rid)
                text_incomplete.append({"card": _label(r), "card_id": rid, "role": "referenced", "referenced_by": _label(c)})
                add(WARNING, "TEXT_INCOMPLETE_REFERENCED",
                    f'"{_label(r)}" ({r["type_text"]}), referenciada por el texto de esta carta, no tiene texto en la fuente. '
                    f"No bloqueante; las interacciones que dependan de su texto no pueden verificarse.", c)

    # --------------------------------------------------------------- copias
    copies_ok = True
    by_key, key_card = collections.Counter(), {}
    for x in pool:
        k = x["card"]["uniqueness_key"]
        by_key[k] += x["qty"]
        key_card[k] = x["card"]
    for k, n in by_key.items():
        c = key_card[k]
        mx = c["legality"][fmt_key]["max_copies"]
        if any(c["name"] == u for u in hero_rules.get("unlimited_names", [])):
            mx = None
        if mx is not None and n > mx:
            copies_ok = False
            code = "LEGENDARY_LIMIT" if c["construction"]["legendary"] else (
                "RESTRICTED_LIMIT" if c["legality"][fmt_key].get("restricted") else "COPY_LIMIT")
            add(ERROR, code, f"{n} copias (sumando todas las impresiones); máximo {mx} en {spec['name']} (nombre + color).", c)
    if fmt_key == "ll":
        by_name = collections.Counter()
        for x in pool:
            if x["card"]["legality"]["ll"].get("restricted_full_cycle"):
                by_name[x["card"]["name"]] += x["qty"]
        for name, n in by_name.items():
            if n > 1:
                copies_ok = False
                add(ERROR, "RESTRICTED_FULL_CYCLE", f'"{name}": ciclo completo restringido, máximo 1 entre todos los colores ({n}).')
    if hero_rules.get("distinct_equipment_names"):
        for name, n in collections.Counter(x["card"]["name"] for x in pool if "Equipment" in x["card"]["card_types"]).items():
            if n > 1:
                copies_ok = False
                add(ERROR, "HERO_RULE_DISTINCT_EQUIPMENT", f'{hero["name"]}: equipo repetido "{name}".')
    unresolved_dupes = collections.Counter((u["input"].get("name"), u["input"].get("color")) for u in unresolved
                                           for _ in range(u["qty"]))
    if any(n > spec["copies"] for n in unresolved_dupes.values()):
        copies_ok = False
        add(ERROR, "COPY_LIMIT", "Una carta no encontrada supera el límite de copias del formato.")
    check(f"copias dentro del límite del formato ({spec['copies']})", copies_ok)

    # --------------------------------------------------------------- tamaños
    size_ok = True
    if spec["deck_exact"] is not None and deck_size != spec["deck_exact"]:
        size_ok = False
        add(ERROR, "DECK_SIZE", f"El mazo tiene {deck_size} cartas; {spec['name']} exige exactamente {spec['deck_exact']}.")
    elif deck_size < spec["deck_min"]:
        size_ok = False
        add(ERROR, "DECK_TOO_SMALL", f"El mazo tiene {deck_size} cartas; mínimo {spec['deck_min']}.")
    check("tamaño del mazo", size_ok)
    pool_ok = pool_size <= spec["pool_max"]
    if not pool_ok:
        add(ERROR, "POOL_TOO_LARGE", f"El card-pool tiene {pool_size} cartas; máximo {spec['pool_max']}.")
    check(f"card-pool ≤ {spec['pool_max']}", pool_ok)
    # Composición del pool: mazo + arena + inventario. Las tres partes se cuentan por separado.
    part = {s: sum(x["qty"] for x in sections[s]) + sum(u["qty"] for u in unresolved if u["section"] == s)
            for s in sections}
    sum_ok = part["deck"] + part["arena"] + part["inventory"] == pool_size
    check("mazo + arena + inventario = card-pool", sum_ok)
    full = pool_size == spec["pool_max"]
    pool_composition = {"deck": part["deck"], "arena": part["arena"], "inventory": part["inventory"],
                        "total": pool_size, "format_max": spec["pool_max"], "complete": full,
                        "require_full_pool": require_full_pool,
                        "basis": "TRP 7.1: «a combined maximum of %d arena-cards and deck-cards»" % spec["pool_max"]}
    if not full and pool_size < spec["pool_max"]:
        if require_full_pool:
            add(ERROR, "POOL_INCOMPLETE",
                f"Card-pool de {pool_size} (mazo {part['deck']} + arena {part['arena']} + inventario {part['inventory']}); "
                f"la política del generador exige {spec['pool_max']}. Faltan {spec['pool_max'] - pool_size}.")
        else:
            add(INFO, "POOL_BELOW_MAX",
                f"Card-pool de {pool_size} de {spec['pool_max']} posibles: legal según TRP 7.1 (máximo), "
                f"pero quedan {spec['pool_max'] - pool_size} plazas de inventario sin usar.")
    if require_full_pool:
        check(f"card-pool completo = {spec['pool_max']} (política del generador)", full)

    # ------------------------------------------ card-pool registrado / colección
    collection_check = {"registered_pool_checked": False, "collection_checked": False,
                        "missing_from_registered_pool": [], "missing_from_collection": []}
    if deck.get("registered_pool") is not None:
        collection_check["registered_pool_checked"] = True
        reg, reg_size = collections.Counter(), 0
        for raw in deck["registered_pool"]:
            e = {"name": raw} if isinstance(raw, str) else dict(raw)
            c, _, err = db.resolve(e)
            q = int(e.get("qty", 1))
            reg_size += q
            if err:
                add(UNKNOWN if err[0] == "NOT_FOUND_IN_SOURCE" else ERROR, "REGISTERED_POOL_" + err[0], err[1],
                    section="registered_pool")
                continue
            reg[c["card_id"]] += q
        if reg_size > spec["pool_max"]:
            add(ERROR, "REGISTERED_POOL_TOO_LARGE", f"Card-pool registrado de {reg_size}; máximo {spec['pool_max']}.")
        pool_composition["registered_total"] = reg_size
        if raw_sections["inventory"] and reg_size != pool_size:
            add(ERROR, "REGISTERED_POOL_MISMATCH",
                f"mazo + arena + inventario = {pool_size}, pero el card-pool registrado tiene {reg_size} (CR 4.1.7).")
        need = collections.Counter()
        for x in pool:
            need[x["card"]["card_id"]] += x["qty"]
        for cid, n in need.items():
            if n > reg[cid]:
                c = db.by_id[cid]
                collection_check["missing_from_registered_pool"].append({"card": _label(c), "needed": n, "registered": reg[cid]})
                add(ERROR, "NOT_IN_REGISTERED_POOL", f"Se usan {n} copia(s); el card-pool registrado tiene {reg[cid]}.", c)
        check("todo sale del card-pool registrado", not collection_check["missing_from_registered_pool"])

    if deck.get("collection") is not None:
        collection_check["collection_checked"] = True
        own_card, own_print = collections.Counter(), collections.Counter()
        for raw in deck["collection"]:
            e = {"name": raw} if isinstance(raw, str) else dict(raw)
            c, p, err = db.resolve(e)
            if err:
                add(WARNING, "COLLECTION_" + err[0], err[1], section="collection")
                continue
            q = int(e.get("qty", 1))
            own_card[c["card_id"]] += q
            if p:
                own_print[p["printing_id"]] += q
        need_card, need_print = collections.Counter(), collections.Counter()
        for x in pool:
            need_card[x["card"]["card_id"]] += x["qty"]
            if x["printing"]:
                need_print[x["printing"]["printing_id"]] += x["qty"]
        for cid, n in need_card.items():
            if n > own_card[cid]:
                c = db.by_id[cid]
                collection_check["missing_from_collection"].append({"card": _label(c), "needed": n, "owned": own_card[cid]})
                add(ERROR, "NOT_IN_COLLECTION", f"Se necesitan {n}; en la colección hay {own_card[cid]}.", c)
        for pid, n in need_print.items():
            if n > own_print[pid]:
                p = db.printing_by_id[pid]
                c = db.by_id[p["card_id"]]
                collection_check["missing_from_collection"].append(
                    {"card": _label(c), "printing": p["print_code"], "printing_id": pid, "needed": n, "owned": own_print[pid]})
                add(ERROR, "PRINTING_NOT_IN_COLLECTION",
                    f'Impresión {p["print_code"]} ({p["rarity_name"]}, foil {p["foiling"]}, ed. {p["edition"]}): '
                    f'se necesitan {n}, hay {own_print[pid]}.', c)
        check("todo el card-pool disponible en la colección", not collection_check["missing_from_collection"])

    if deck.get("loadout") is not None and hero:
        _check_loadout(deck["loadout"], sections["arena"], hero, db, add, check)

    counts = _counts(sections, pool, unresolved)
    if sections["deck"]:
        _heuristics(sections, counts, add)
    arena_zones = {z for x in sections["arena"] for z in x["card"]["subtypes"] if z in EQUIPMENT_ZONES}
    if sections["arena"] or sections["deck"]:
        if not any("Weapon" in x["card"]["card_types"] for x in sections["arena"]):
            add(WARNING, "NO_WEAPON", "El card-pool no incluye armas.")
        missing = [z for z in EQUIPMENT_ZONES if z not in arena_zones]
        if missing:
            add(WARNING, "EMPTY_EQUIPMENT_ZONES", f"No hay equipo para: {missing}.")

    # ------------------------------------------------ dependencias entre cartas
    # Nunca bloquea ni deja el resultado INDETERMINADO: unmet / inventory_only / conditional / ambiguous -> advertencia; unknown -> info.
    dep_findings = []
    if hero and sections["deck"]:
        if not hasattr(db, "_type_parser"):
            db._type_parser = DEPS.TypeParser(db)
        dep_findings = DEPS.analyze(hero, sections, db, db._type_parser)
        for f in dep_findings:
            if f["status"] in ("unmet", "inventory_only"):
                code = "DEPENDENCY_UNMET" if f["status"] == "unmet" else "DEPENDENCY_INVENTORY_ONLY"
                where = ("no hay ninguna carta que la cumpla en el card-pool" if f["status"] == "unmet" else
                         "solo la cumplen cartas del inventario: " + ", ".join(f["providers_inventory"]))
                warnings.append({"code": code, "card": f["source"], "pattern": f["pattern"],
                                 "message": f'{f["source"]} necesita {f["requires"]} ({f["pattern"]}, {f["rule"]}); {where}. '
                                            f'Evidencia: «{f["evidence"]}»'})
        for f in dep_findings:
            if f["status"] == "conditionally_satisfied":
                extra = (" Cubierta de forma propia solo en el inventario: " + ", ".join(f["providers_inventory"]) + "."
                         if f.get("providers_inventory") else "")
                warnings.append({"code": "DEPENDENCY_CONDITIONAL", "card": f["source"], "pattern": f["pattern"],
                                 "message": f'{f["source"]} necesita {f["requires"]} ({f["pattern"]}); solo la cubren cartas que '
                                            f'crean el token bajo el control de un héroe elegido ({", ".join(f["providers_conditional"])}): '
                                            f'cubre la dependencia solo si te eliges a ti. POLÍTICA DE ANÁLISIS conservadora (no es una regla oficial; '
                                            f'la regla aplicada es CR 8.5.2c). No afecta a la legalidad.{extra} Evidencia: «{f["evidence"]}»'})
        for f in dep_findings:
            if f["status"] == "ambiguous_provider":
                extra = (" Cubierta de forma propia solo en el inventario: " + ", ".join(f["providers_inventory"]) + "."
                         if f.get("providers_inventory") else "")
                warnings.append({"code": "DEPENDENCY_AMBIGUOUS", "card": f["source"], "pattern": f["pattern"],
                                 "message": f'{f["source"]} necesita {f["requires"]} ({f["pattern"]}); solo la cubren cartas cuyo '
                                            f'token puede acabar en el rival o en nadie ({", ".join(f["providers_ambiguous"])}; '
                                            f'p. ej. «the winner creates…», CR 8.5.45c; wager, CR 8.5.46b). No cuenta como cubierta. POLÍTICA DE ANÁLISIS '
                                            f'conservadora (no es una regla oficial; reglas aplicadas: CR 8.5.2c). No afecta a la legalidad.{extra} '
                                            f'Evidencia: «{f["evidence"]}»'})
        unk = [f for f in dep_findings if f["status"] == "unknown_dependency"]
        if unk:
            by = collections.Counter(f["pattern"] for f in unk)
            add(INFO, "DEPENDENCY_UNKNOWN", f"{len(unk)} dependencia(s) no verificables con los datos actuales: "
                + ", ".join(f"{k} ×{n}" for k, n in sorted(by.items())) + " (ver report.dependencies).")

    rep = _report(deck, fmt_key, hero, mode, errors, warnings, infos, unknowns, problem, counts, checks,
                  collection_check, unresolved, discrepancies, db, hero_unresolved)
    rep["dependencies"] = {"findings": dep_findings, "summary": DEPS.summarize(dep_findings)}
    ids = sorted({w["conflict_id"] for w in warnings if w.get("conflict_id")} |
                 {d.get("conflict_id") for d in discrepancies if d.get("conflict_id")} - {None})
    blk = hero_rules.get("suggestion_block")
    rep["pool_composition"] = pool_composition
    rep["text_incomplete"] = text_incomplete
    rep["construction_blocks"] = ([{"hero": hero_unresolved["name"], "reason": "data_completeness=incomplete",
                                    "requires": hero_unresolved.get("deck_construction_requires")}]
                                  if hero_unresolved and hero_unresolved.get("deck_construction_allowed") is False else [])
    rep["normative_conflicts"] = {
        "triggered": ids,
        "suggestion_blocks": ([{"hero": hero["name"], "categories": blk["categories"], "conflict_id": blk["conflict_id"],
                                "resolution": "unresolved_normative_conflict"}]
                              if hero and blk and blk["conflict_id"] in open_normative_conflicts() else [])}
    return rep


def _status_msg(L, spec):
    st = L["status"]
    if st == "not_legal":
        if L.get("silver_age_rarity_ok") is False:
            return "Ilegal en Silver Age: ninguna impresión es common, rare o basic (R-SA-RARITY)."
        return f'No pertenece al pool legal de {spec["name"]}.'
    if st == "banned":
        return f'Prohibida en {spec["name"]} por la política oficial.'
    if st == "benched":
        b = L["benched"]
        return f'Héroe en la banca en {spec["name"]} desde {b["from"]} hasta {b["until_text"]}.'
    if st == "disputed":
        return (f'Bloqueada en {spec["name"]}: estado de prohibición en disputa ({L.get("ban_basis")}); '
                f'una omisión en la fuente oficial no la vuelve legal. Requiere revisión humana.')
    if st == "special_use_promo":
        return f'Promo de uso especial: no es legal en ningún formato (Special Use Promos).'
    if st == "suspended":
        return f'Suspendida en {spec["name"]}.'
    if st == "living_legend":
        return f'Living Legend: excluida de {spec["name"]} por la política oficial.'
    if st == "unknown":
        return "Legalidad desconocida en la fuente."
    return f"Estado {st}."


def _name_words(s):
    return [w.casefold() for w in re.findall(r"[\w'’.-]+", _norm(s).replace("’", "'"))]


def name_has_words(name, part):
    """CR 2.7.5: «A name or part of a name is equal to another name or part of a name only if it is an exact
    case-insensitive match of each whole word in order.» -> «Tome» está en «Tome of Fyendal» y en «Gorganian Tome»,
    pero NO en «Invoke Tomeltai». Se usa solo donde el texto dice «with X in its/their name»."""
    w, p = _name_words(name), _name_words(part)
    return any(w[i:i + len(p)] == p for i in range(len(w) - len(p) + 1)) if p else False


def _supertypes_ok(card, hero, rules):
    if card["is_generic"]:
        return True, None
    hero_super = set(hero["hero"]["supertypes"]) | set(hero["hero"]["essence_of"] or [])
    if card["construction"]["universal"]:
        hero_super |= set(hero["classes"])
    if rules.get("any_supertype_if_keyword"):
        kw = rules["any_supertype_if_keyword"]
        if any(k == kw or k.startswith(kw + " ") for k in card["keywords"]["card"] + card["keywords"]["ability_and_effect"]):
            return True, None
    if rules.get("any_supertype_if_name_contains") and name_has_words(card["name"], rules["any_supertype_if_name_contains"]):
        return True, None
    if rules.get("any_supertype_equipment") and "Equipment" in card["card_types"]:
        return True, None
    sets = card["hybrid_supertype_sets"] or [card["classes"] + card["talents"]]
    if any(set(s) <= hero_super for s in sets):
        return True, None
    need = " o ".join("/".join(s) for s in sets)
    return False, f"Supertipos [{need}] no son subconjunto de los del héroe {sorted(hero_super)} (CR 1.1.3)."


NORMATIVE_CONFLICTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policy", "normative_conflicts.json")


def open_normative_conflicts():
    with open(NORMATIVE_CONFLICTS_PATH, encoding="utf-8") as f:
        return {c["id"]: c for c in json.load(f)["conflicts"] if c["resolution"] == "unresolved_normative_conflict"}


def deck_construction_allowed(hero_name, db, fmt="cc"):
    """Compuerta para la fase 3: False si el héroe no tiene datos completos en la fuente."""
    c, _, err = db.resolve({"name": hero_name})
    if c and not err:
        return True, None
    st = db.unresolved_states(hero_name, None, fmt)
    if st.get("deck_construction_allowed") is False:
        return False, "data_completeness=incomplete: faltan " + ", ".join(st.get("deck_construction_requires") or [])
    return False, "héroe no encontrado en la fuente"


def suggestion_allowed(hero_name, card):
    """Compuerta para la fase 3 (generador). False si sugerir la carta tocaría una disputa normativa abierta."""
    block = HERO_RULES.get(hero_name, {}).get("suggestion_block")
    if block and card.get("category") in block["categories"] and block["conflict_id"] in open_normative_conflicts():
        return False, block["conflict_id"]
    return True, None


def evaluate_weapon_zones(weapon_cards, max_weapon):
    """Lógica pura de zonas de arma (sin base de datos): sirve para cartas reales y para fixtures sintéticos.

    weapon_cards: dicts con "subtypes" (lista) y "keywords" {"card": [...]} (armas, off-hands, quivers).
    CR 8.2.2b: un arma 2H ocupa dos zonas de arma. CR 8.2.10b / 8.2.15b: máximo 1 off-hand y 1 quiver.
    CR 8.2.15a: un quiver puede ir en la zona no ocupada de un arco 2H (no suma). CR 8.3.39a: una carta con
    Perched puede ir en la zona no ocupada de un arma 2H (no suma). Cualquier otro caso suma 1 zona; en
    particular un quiver junto a un arco 1H ocupa su propia zona (8.2.15a solo habla de arcos 2H).
    """
    issues = []
    for sub, rule in (("Off-Hand", "CR 8.2.10b"), ("Quiver", "CR 8.2.15b")):
        n = sum(1 for c in weapon_cards if sub in c["subtypes"])
        if n > 1:
            issues.append(("LOADOUT_TOO_MANY_" + sub.upper().replace("-", "_"), f"{n} cartas {sub}; máximo 1 ({rule})."))
    # Cada arma 2H deja UNA zona no ocupada; en ella cabe UNA sola carta compañera:
    # un quiver si el arma es un arco (8.2.15a) o una carta con Perched (8.3.39a).
    twoh = [c for c in weapon_cards if "2H" in c["subtypes"]]
    free = [("Bow" in c["subtypes"]) for c in twoh]  # una zona libre por arma 2H; True = admite quiver
    slots = 2 * len(twoh)
    for c in weapon_cards:
        if "2H" in c["subtypes"]:
            continue
        if "Quiver" in c["subtypes"] and True in free:
            free.remove(True)
            continue
        if "Perched" in c["keywords"]["card"] and free:
            free.remove(False if False in free else True)
            continue
        slots += 1
    if slots > max_weapon:
        issues.append(("LOADOUT_WEAPON_OVERFLOW", f"Armas/off-hands/quivers ocupan {slots} zonas; el héroe tiene {max_weapon}."))
    return {"slots": slots, "max_weapon": max_weapon, "issues": issues}


def _check_loadout(loadout, arena_items, hero, db, add, check):
    arena_ids = collections.Counter()
    for x in arena_items:
        arena_ids[x["card"]["card_id"]] += x["qty"]
    zones, ok = collections.Counter(), True
    max_weapon = 1 if "1 weapon zone" in (hero["functional_text_plain"] or "") else 2
    weapon_cards = []
    for raw in loadout:
        e = {"name": raw} if isinstance(raw, str) else dict(raw)
        c, _, err = db.resolve(e)
        if err:
            add(ERROR, "LOADOUT_" + err[0], err[1], section="loadout")
            ok = False
            continue
        if arena_ids[c["card_id"]] < 1:
            add(ERROR, "LOADOUT_NOT_IN_ARENA", "La carta del loadout no está en 'arena'.", c, "loadout")
            ok = False
        arena_ids[c["card_id"]] -= 1
        if "Modular" not in c["keywords"]["card"]:
            for z in EQUIPMENT_ZONES:
                if z in c["subtypes"]:
                    zones[z] += 1
        if "Weapon" in c["card_types"] or {"Off-Hand", "Quiver"} & set(c["subtypes"]):
            weapon_cards.append(c)
    for z, n in zones.items():
        if n > 1:
            ok = False
            add(ERROR, "LOADOUT_ZONE_OVERFLOW", f"{n} cartas para {z}; máximo 1 (CR 4.1.4a).")
    for code, msg in evaluate_weapon_zones(weapon_cards, max_weapon)["issues"]:
        ok = False
        add(ERROR, code, msg)
    check("loadout cabe en las zonas del héroe", ok)


def _counts(sections, pool, unresolved):
    def tally(items, fn):
        t = collections.Counter()
        for x in items:
            for k in fn(x):
                t[k] += x["qty"]
        return dict(sorted(t.items(), key=lambda kv: str(kv[0])))
    deck = sections["deck"]

    def type_label(x):
        c = x["card"]
        if "Attack" in c["subtypes"] and "Action" in c["card_types"]:
            return ["Attack Action"]
        if "Action" in c["card_types"]:
            return ["Non-attack Action"]
        return [" ".join(c["card_types"]) or "?"]
    return {
        "deck_size": sum(x["qty"] for x in deck) + sum(u["qty"] for u in unresolved if u["section"] == "deck"),
        "deck_size_resolved": sum(x["qty"] for x in deck),
        "unresolved_qty": sum(u["qty"] for u in unresolved),
        "arena_size": sum(x["qty"] for x in sections["arena"]),
        "inventory_size": sum(x["qty"] for x in sections["inventory"]),
        "pool_size": sum(x["qty"] for x in pool) + sum(u["qty"] for u in unresolved),
        "deck": {
            "by_type": tally(deck, type_label),
            "by_color": tally(deck, lambda x: [x["card"]["color"] or "sin color"]),
            "by_pitch": tally(deck, lambda x: [x["card"]["pitch"] if x["card"]["pitch"] is not None else "sin pitch"]),
            "by_cost": tally(deck, lambda x: [x["card"]["cost"] if x["card"]["cost"] is not None else (x["card"]["cost_raw"] or "sin costo")]),
            "by_class": tally(deck, lambda x: x["card"]["classes"] or ["Generic"]),
            "by_talent": tally(deck, lambda x: x["card"]["talents"] or ["sin talento"]),
            "by_rarity": tally(deck, lambda x: [RARITY_NAMES[x["printing"]["rarity"]] if x["printing"] else "sin impresión especificada"]),
        },
        "arena": {"by_slot": tally(sections["arena"], lambda x: [z for z in x["card"]["subtypes"]
                                                                 if z in EQUIPMENT_ZONES + ("1H", "2H", "Off-Hand", "Quiver")] or ["otro"])},
    }


def resource_curves(deck_items):
    """EXPERIMENTAL (no validada con partidas). Limitaciones conocidas: no incluye costes de activación (ataques de
    aliados, armas, equipo), ni que una carta no puede pitchearse y jugarse a la vez. Umbral HEURISTICS['min_resource_ratio'].
    Dos métricas separadas (heurísticas, no reglas del juego):
    - own_turn: pitch total del mazo vs coste total de las cartas que se juegan en el turno propio
      (acciones, ataques, reacciones de ataque, instants y demás no-defensivas).
    - opponent_turn: pitch medio por carta vs coste medio de una reacción de defensa (cuántos recursos
      hay que pitchear para pagar una reacción en el turno rival). Se usa la media porque el total del
      mazo frente a unas pocas reacciones no dice nada útil.
    El mismo umbral global (HEURISTICS['min_resource_ratio']) para ambas; sin umbrales por arquetipo."""
    n = sum(x["qty"] for x in deck_items) or 1
    pitch = sum((x["card"]["pitch"] or 0) * x["qty"] for x in deck_items)
    dr = [x for x in deck_items if "Defense Reaction" in " ".join(x["card"]["card_types"])]
    own = [x for x in deck_items if x not in dr]
    own_cost = sum((x["card"]["cost"] or 0) * x["qty"] for x in own)
    dr_n = sum(x["qty"] for x in dr)
    dr_cost = sum((x["card"]["cost"] or 0) * x["qty"] for x in dr)
    avg_pitch = round(pitch / n, 2)
    avg_dr = round(dr_cost / dr_n, 2) if dr_n else None
    curve = lambda items: dict(sorted(collections.Counter(
        (x["card"]["cost"] if x["card"]["cost"] is not None else (x["card"]["cost_raw"] or "—")) for x in items
        for _ in range(x["qty"])).items(), key=lambda kv: str(kv[0])))
    return {
        "pitch_total": pitch, "pitch_distribution": dict(sorted(collections.Counter(
            x["card"]["pitch"] for x in deck_items for _ in range(x["qty"])).items(), key=lambda kv: str(kv[0]))),
        "own_turn": {"cards": n - dr_n, "pitch_total": pitch, "cost_total": own_cost, "cost_curve": curve(own),
                     "ratio": round(pitch / own_cost, 2) if own_cost else None},
        "opponent_turn": {"cards": dr_n, "cost_total": dr_cost, "cost_curve": curve(dr), "avg_pitch": avg_pitch,
                          "avg_cost": avg_dr, "ratio": round(avg_pitch / avg_dr, 2) if avg_dr else None},
    }


def _heuristics(sections, counts, add):
    d, n = counts["deck"], counts["deck_size_resolved"] or 1
    blue = d["by_color"].get("Blue", 0)
    if blue / n < HEURISTICS["min_blue_share"]:
        add(WARNING, "LOW_BLUE_COUNT", f"Solo {blue}/{n} cartas azules (heurística).")
    for t, k in d["by_type"].items():
        if k / n > HEURISTICS["max_single_type_share"]:
            add(WARNING, "TYPE_CONCENTRATION", f'"{t}" es {k}/{n} del mazo (heurística).')
    rc = resource_curves(sections["deck"])
    counts["resource_curves"] = rc
    thr = HEURISTICS["min_resource_ratio"]
    own, opp = rc["own_turn"], rc["opponent_turn"]
    if own["ratio"] is not None and own["ratio"] < thr:
        add(WARNING, "RESOURCE_CURVE_OWN_TURN",
            f"Turno propio: pitch total {own['pitch_total']} vs coste de acciones, ataques, reacciones de ataque e "
            f"instants {own['cost_total']} ({own['ratio']:.2f} < {thr}) (heurística EXPERIMENTAL, no validada con partidas; no incluye "
            f"costes de activación de aliados, armas ni equipo; no afecta a la legalidad).")
    if opp["ratio"] is not None and opp["ratio"] < thr:
        add(WARNING, "RESOURCE_CURVE_OPPONENT_TURN",
            f"Turno rival: pitch medio por carta {opp['avg_pitch']} vs coste medio de las reacciones de defensa "
            f"{opp['avg_cost']} ({opp['ratio']:.2f} < {thr}) (heurística EXPERIMENTAL, no validada con partidas; no afecta a la legalidad).")
    inter = sum(x["qty"] for x in sections["deck"] if set(x["card"]["card_types"]) & {"Instant", "Defense Reaction", "Attack Reaction"})
    if inter < HEURISTICS["min_interaction_cards"]:
        add(WARNING, "NO_INTERACTION", "Sin instants ni reacciones (heurística).")
    xcost = [x for x in sections["deck"] if x["card"]["cost"] is None and x["card"]["cost_raw"]]
    if xcost:
        add(INFO, "VARIABLE_COST", f"{len(xcost)} carta(s) con costo variable excluidas de la curva.")


def _report(deck, fmt_key, hero, mode, errors, warnings, infos, unknowns, problem, counts, checks,
            collection_check, unresolved, discrepancies, db, hero_unresolved=None):
    if errors:
        result = "INVALIDO"
    elif unknowns:
        result = "INDETERMINADO"
    elif warnings:
        result = "VALIDO_CON_ADVERTENCIAS"
    else:
        result = "VALIDO"
    m = db.manifest
    return {
        "format": FORMATS[fmt_key]["name"] if fmt_key else deck.get("format"),
        "format_key": fmt_key, "mode": mode,
        "hero": (None if not hero else {
            "name": hero["name"], "card_id": hero["card_id"], "moniker": hero["hero"]["moniker"],
            "supertypes": hero["hero"]["supertypes"], "essence_of": hero["hero"]["essence_of"],
            "young": hero["hero"]["is_young"], "life": hero["hero"]["life"], "intellect": hero["hero"]["intellect"],
            "states": {k: hero["legality"][fmt_key][k] for k in ("existence", "legality_state", "ban_state", "living_legend_state")}}
                 ) if hero else hero_unresolved,
        "result": result,
        "summary": {"errors": len(errors), "unknowns": len(unknowns), "warnings": len(warnings), "infos": len(infos)},
        "errors": errors, "unknowns": unknowns, "warnings": warnings, "infos": infos,
        "unresolved_cards": unresolved,
        "source_discrepancies": discrepancies,
        "problem_cards": [{"card": k, "issues": v} for k, v in problem.items()],
        "counts": counts, "collection_check": collection_check, "format_checks": checks,
        "data_snapshot": {"repo_commit": m["source"]["commit"], "repo_date": m["source"]["commit_date"],
                          "policy_version": m["policy"]["version"], "policy_checked_on": m["policy"]["checked_on"],
                          "latest_set_in_data": m["latest_set_in_data"]},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("deck")
    ap.add_argument("--data", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    ap.add_argument("--mode", choices=MODES, default="strict")
    ap.add_argument("--require-full-pool", action="store_true",
                    help="política del generador: card-pool = máximo del formato (80 en CC); si no, error bloqueante")
    a = ap.parse_args()
    with open(a.deck, encoding="utf-8") as f:
        dk = json.load(f)
    rep = validate(dk, CardDB(a.data), a.mode, require_full_pool=a.require_full_pool)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    sys.exit({"INVALIDO": 1, "INDETERMINADO": 2}.get(rep["result"], 0))
