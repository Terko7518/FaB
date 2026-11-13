"""Legalidad por formato a partir de la lista canónica transcrita (policy/canonical_legality.json).

Reglas de decisión (en este orden) para la prohibición de una carta (nombre + color) en un formato:
  1. Listada en la sección BANNED de la Card Legality Policy (CLP)          -> banned        (clp_list)
  2. No listada, pero el último anuncio oficial que la menciona la prohíbe   -> banned        (announcement_without_later_unban)
     y ningún anuncio posterior la desbloquea                                  revisión: disputed
  3. Formato Blitz: "There are no banned cards in Blitz."                    -> not_banned    (blitz_no_banned_cards)
  4. El repo la marca prohibida y el último anuncio la desbloquea            -> not_banned    (explicit_unban)
  5. El repo la marca prohibida y la fuente oficial guarda silencio          -> banned        (repo_only_official_silent)
                                                                               revisión: disputed
  6. Resto                                                                    -> not_banned    (not_listed)
Los casos 2 y 5 evitan que una omisión (del extractor o de la página) vuelva legal una carta prohibida.
"""
import json
import re

FMT_LIST = ("cc", "blitz", "silver_age", "ll")
NC_IDS = {("cc", "Volzar, the Lightning Rod"): "NC-002", ("cc", "Winter's Wail"): "NC-003"}
CLP_URL = "https://fabtcg.com/rules-and-policy-center/card-legality-policy/"


def norm(s):
    if s is None:
        return None
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')).strip()


def _applies(colors, color):
    return not colors or color in colors


def _ev(e, rule):
    """Proveniencia compacta de una entrada / evento / regla."""
    return {"official_url": e["official_url"], "section": e.get("section") or (f'Anuncio {e["date"]}' if "date" in e else e.get("list")),
            "literal_text": e["literal_text"], "consulted_on": e["consulted_on"],
            "review_status": e["status"], "rule": rule}


class Canonical:
    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            self.raw = json.load(f)
        r = self.raw
        self.banned = {f: {} for f in FMT_LIST}
        self.restricted = {}
        self.ll = {}
        self.benched = {}
        self.special = {f: {} for f in FMT_LIST}
        for e in r["entries"]:
            n = norm(e["name"])
            if e["list"] == "banned":
                self.banned[e["format"]].setdefault(n, []).append(e)
            elif e["list"] == "restricted":
                self.restricted[n] = e
            elif e["list"] in ("living_legend_hero", "living_legend_weapon"):
                self.ll[n] = e
            elif e["list"] == "benched_hero":
                self.benched[n] = e
            elif e["list"] == "special_use_promo":
                self.special[e["format"]][n] = e
        self.events = {}
        for ev in sorted(r["events"], key=lambda x: x["date"]):
            self.events.setdefault((ev["format"], norm(ev["name"])), []).append(ev)
        rules = r["format_rules"]
        self.blitz_no_bans = next(x for x in rules if x["format"] == "blitz" and x["literal_text"] == "There are no banned cards in Blitz.")
        self.blitz_ll = next(x for x in rules if x["format"] == "blitz" and "Living Legend" in x["literal_text"])
        self.sa_rule = next(x for x in rules if x["format"] == "silver_age" and x["literal_text"].startswith("Silver Age includes"))
        self.determinations = {norm(d["name"]): d for d in r["named_determinations"]}

    def last_ban_event(self, fmt, name, color):
        last = None
        for ev in self.events.get((fmt, norm(name)), []):
            if ev["action"] in ("banned", "unbanned") and _applies(ev["colors"], color):
                last = ev
        return last

    def ban_decision(self, fmt, name, color, repo_banned):
        n = norm(name)
        listed = [e for e in self.banned[fmt].get(n, []) if _applies(e["colors"], color)]
        last = self.last_ban_event(fmt, n, color)
        if listed:
            return "banned", "clp_list", None, [_ev(listed[0], "R-BAN-CLP: listada en la sección BANNED de la CLP")]
        if last and last["action"] == "banned":
            return "banned", "announcement_without_later_unban", "disputed", [
                _ev(last, "R-BAN-TRAIL: el último anuncio oficial que la menciona la prohíbe; ningún anuncio posterior la desbloquea"),
                {"official_url": CLP_URL, "section": f"{fmt} › BANNED", "literal_text": "(no listada)",
                 "consulted_on": self.raw["consulted_on"], "review_status": "disputed",
                 "rule": "La CLP no la lista; fuentes oficiales en desacuerdo -> se bloquea hasta revisión humana"}]
        if fmt == "blitz":
            return "not_banned", "blitz_no_banned_cards", None, [_ev(self.blitz_no_bans, "R-BLITZ-NOBAN")]
        if repo_banned and last and last["action"] == "unbanned":
            return "not_banned", "explicit_unban", None, [_ev(last, "R-UNBAN: desbloqueo explícito en anuncio oficial")]
        if repo_banned:
            return "banned", "repo_only_official_silent", "disputed", [
                {"official_url": CLP_URL, "section": f"{fmt} › BANNED", "literal_text": "(no listada; sin anuncio de desbloqueo capturado)",
                 "consulted_on": self.raw["consulted_on"], "review_status": "disputed",
                 "rule": "R-NO-SILENT-UNBAN: el silencio oficial no desbloquea una carta que el repo marca prohibida"}]
        return "not_banned", "not_listed", None, []


def build_policy_index(canon):
    """Índice por nombre de lo que la política oficial dice (para cartas ausentes de la fuente)."""
    idx = {}

    def put(name, fmt, field, value, prov, colors=None):
        idx.setdefault(norm(name), {}).setdefault(fmt, {})[field] = {"value": value, "colors": colors, **prov}
    for f in FMT_LIST:
        for n, es in canon.banned[f].items():
            for e in es:
                put(n, f, "banned", True, _ev(e, "R-BAN-CLP"), e["colors"])
        for n, e in canon.special[f].items():
            put(n, f, "special_use_promo", True, _ev(e, "R-SPECIAL-USE"))
    for (f, n), evs in canon.events.items():
        last = [x for x in evs if x["action"] in ("banned", "unbanned")]
        if last and last[-1]["action"] == "banned" and f in FMT_LIST and "banned" not in idx.get(n, {}).get(f, {}):
            put(n, f, "banned", True, _ev(last[-1], "R-BAN-TRAIL (disputed)"), last[-1]["colors"])
    for n, e in canon.ll.items():
        put(n, "cc", "living_legend", True, _ev(e, "R-LL"))
    for n, e in canon.restricted.items():
        put(n, "ll", "restricted", True, _ev(e, "R-LL-RESTRICTED"))
    for n, e in canon.benched.items():
        put(n, "silver_age", "benched", {"hero": n, "from": e.get("from_literal"), "until_text": e.get("until_literal")}, _ev(e, "R-SA-BENCH"))
    for n, d in canon.determinations.items():
        idx.setdefault(n, {}).setdefault(d["format"], {})["determination"] = d
    return idx


def apply_legality(cards, name_index, canon, sa_rarities, formats_spec):
    name_index_n = {norm(n) for n in name_index}
    not_found = []
    for group, ns in [("cc.banned", canon.banned["cc"]), ("silver_age.banned", canon.banned["silver_age"]),
                      ("ll.banned", canon.banned["ll"]), ("ll.restricted", canon.restricted),
                      ("cc.living_legend", canon.ll), ("silver_age.benched_heroes", canon.benched),
                      ("special_use_promos", canon.special["cc"])]:
        not_found += [{"list": group, "name": n} for n in ns if n not in name_index_n]

    conflicts = []

    def conflict(rec, f, dimension, category, repo_v, off_v, ev, final_status, review=None):
        e0 = ev[0] if ev else {}
        c = {"id": None, "card_id": rec["card_id"], "name": rec["name"], "color": rec["color"],
             "printings": rec["print_codes"], "printing_count": len(rec["printing_ids"]),
             "format": f, "dimension": dimension, "category": category,
             "repo_value": repo_v, "official_value": off_v,
             "official_url": e0.get("official_url"), "policy_section": e0.get("section"),
             "literal_text": e0.get("literal_text"), "consulted_on": e0.get("consulted_on"),
             "rule_applied": (e0.get("rule") or "") + " | R-PRIORITY: la política oficial prevalece sobre el repositorio",
             "evidence": ev, "review_status": review or e0.get("review_status", "pending_human_review"),
             "conflict_id": NC_IDS.get((f, rec["name"])) if category == "disputa_fuentes_oficiales" else None,
             "resolution": "unresolved_normative_conflict" if category == "disputa_fuentes_oficiales" else None,
             "final_status": final_status,
             "severity": {"strict": "error", "exploratory": "error" if review == "disputed" else "warning"}}
        conflicts.append(c)
        return c

    for rec in cards:
        c = rec["_src"]
        n = norm(rec["name"])
        manual_only = rec["provenance"]["sources"] == ["official_manual_entry"]
        rarities = set(rec["rarities_printed"])
        L = {}
        for f in FMT_LIST:
            spec = formats_spec[f]
            repo = None if manual_only else {
                "legal": c[f"{f}_legal"], "banned": c.get(f"{f}_banned"),
                "banned_since": c.get(f"{f}_banned_start"),
                "suspended": c.get(f"{f}_suspended") if f in ("cc", "blitz") else None,
                "suspended_until_text": c.get(f"{f}_suspended_end"),
                "living_legend": c.get(f"{f}_living_legend") if f in ("cc", "blitz") else None,
                "living_legend_since": c.get(f"{f}_living_legend_start"),
                "restricted": c.get("ll_restricted") if f == "ll" else None,
                "restricted_full_cycle": c.get("ll_restricted_affects_full_cycle") if f == "ll" else None}
            base = c[f"{f}_legal"]
            pending = []  # (dimension, category, repo_v, off_v, evidence, review)
            evidence = []

            # --- prohibición ---
            repo_banned = bool(repo["banned"]) if repo else False
            ban_state, ban_basis, review, ev = canon.ban_decision(f, rec["name"], rec["color"], repo_banned)
            evidence += ev
            bench = None
            if f == "silver_age" and rec["category"] == "hero" and n in canon.benched:
                be = canon.benched[n]
                bench = {"hero": n, "from": be.get("from_literal"), "until_text": be.get("until_literal")}
                evidence.append(_ev(be, "R-SA-BENCH"))
            off_ban = ban_state == "banned"
            if repo is not None and not bench:
                if repo_banned != off_ban:
                    pending.append(("ban", "prohibicion" if off_ban else "desbloqueo", repo_banned, off_ban, ev, review))
                elif review == "disputed" and ban_basis == "announcement_without_later_unban":
                    pending.append(("ban", "disputa_fuentes_oficiales", repo_banned, "CLP: no listada / anuncio: banned", ev, review))
            if bench:
                ban_state = "banned"

            # --- Living Legend (solo excluye en CC; abolido en Blitz) ---
            ll_e = canon.ll.get(n)
            is_ll = ll_e is not None
            ll_state = "living_legend" if is_ll else "not_living_legend"
            if f == "cc":
                if is_ll:
                    evidence.append(_ev(ll_e, "R-LL"))
                if repo is not None and bool(repo["living_legend"]) != is_ll:
                    pending.append(("living_legend", "living_legend", bool(repo["living_legend"]), is_ll,
                                    [_ev(ll_e, "R-LL")] if ll_e else [{"official_url": CLP_URL, "section": "CLASSIC CONSTRUCTED › LIVING LEGEND HEROES",
                                                                       "literal_text": "(no listada)", "consulted_on": canon.raw["consulted_on"],
                                                                       "review_status": "pending_human_review", "rule": "R-LL"}], None))
            if f == "blitz":
                ll_state = "not_living_legend"
                if repo is not None and repo["living_legend"]:
                    pending.append(("living_legend", "living_legend", True, False, [_ev(canon.blitz_ll, "R-BLITZ-LL-ABOLISHED")], None))
            ll_excludes = f == "cc" and is_ll

            # --- restringidas (LL) ---
            restricted = (n in canon.restricted) if f == "ll" else None
            if f == "ll" and repo is not None and bool(repo["restricted"]) != restricted:
                pending.append(("restricted", "restriccion_formato", bool(repo["restricted"]), restricted,
                                [_ev(canon.restricted[n], "R-LL-RESTRICTED")] if restricted else [], None))
            full_cycle = (repo or {}).get("restricted_full_cycle") if restricted else None

            # --- promos de uso especial (excluidas de los 4 formatos) ---
            sp = canon.special[f].get(n)
            if sp:
                evidence.append(_ev(sp, "R-SPECIAL-USE"))
                if base:
                    pending.append(("format_pool", "promo_uso_especial", True, False, [_ev(sp, "R-SPECIAL-USE: Special use promos are not legal")], None))

            # --- rareza Silver Age ---
            rarity_ok = None
            if f == "silver_age":
                rarity_ok = bool(rarities & sa_rarities)
                if base and not rarity_ok and not sp:
                    pending.append(("rarity", "restriccion_rareza", True, False, [_ev(canon.sa_rule, "R-SA-RARITY")], None))

            # --- estado final ---
            suspended = bool(repo["suspended"]) if repo and repo["suspended"] is not None else None
            if base is None:
                legality_state, status = "unknown_legality", "unknown"
            elif sp:
                legality_state, status = "illegal", "special_use_promo"
            elif not base or (f == "silver_age" and not rarity_ok):
                legality_state, status = "illegal", "not_legal"
            elif bench:
                legality_state, status = "illegal", "benched"
            elif off_ban:
                legality_state, status = "illegal", ("disputed" if review == "disputed" else "banned")
            elif suspended:
                legality_state, status = "illegal", "suspended"
            elif ll_excludes:
                legality_state, status = "illegal", "living_legend"
            else:
                legality_state, status = "legal", ("restricted" if restricted else "legal")

            if rec["construction"]["unlimited"]:
                max_copies = None
            elif rec["construction"]["legendary"] or restricted:
                max_copies = 1
            else:
                max_copies = spec["copies"]

            recs = [conflict(rec, f, d, cat, rv, ov, e, status, rv_) for d, cat, rv, ov, e, rv_ in pending]
            L[f] = {
                "existence": "found",
                "legality_state": legality_state, "ban_state": ban_state, "ban_basis": ban_basis,
                "review_status": review or ("pending_human_review" if evidence else None),
                "living_legend_state": ll_state, "living_legend_excludes_in_format": ll_excludes,
                "status": status, "playable": legality_state == "legal",
                "base_legal": c[f"{f}_legal"], "silver_age_rarity_ok": rarity_ok,
                "special_use_promo": bool(sp),
                "banned": ban_state == "banned",
                "benched": bench, "suspended": suspended,
                "suspended_until_text": repo["suspended_until_text"] if suspended else None,
                "living_legend": is_ll if f != "blitz" else False,
                "restricted": restricted, "restricted_full_cycle": full_cycle,
                "max_copies": max_copies, "repo_flags": repo,
                "evidence": evidence,
                "conflicts": recs,
            }
        rec["legality"] = L
    for i, c in enumerate(conflicts, 1):
        c["id"] = f"D-{i:03d}"
    return conflicts, not_found
