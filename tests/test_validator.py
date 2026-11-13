"""Pruebas de casos límite con datos reales.

Ejecutar:   python3 tests/test_validator.py
Salida:     una línea por caso (OK/FAIL) y tests/resultados_pruebas.json + .md con
            entrada, resultado esperado y resultado obtenido de cada ejecución.

Los mazos "de relleno" se arman automáticamente con cartas legales y sin conflictos de
fuente para alcanzar el tamaño mínimo; son fixtures técnicos, NO propuestas de mazo.
Las pruebas de la capa suplementaria usan fixtures sintéticos en un directorio temporal;
nunca escriben en data/ ni en supplement/.
"""
import copy
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from validator import CardDB, validate  # noqa: E402
import sync  # noqa: E402

DB = CardDB(os.path.join(ROOT, "data"))
REPO = os.environ.get("FAB_REPO", os.path.join(os.path.dirname(ROOT), "src"))
KAYO = "Kayo, Underhanded Cheat"
SA_OK = {"C", "R", "B", "T"}
CASES = []


def case(cid, group, desc):
    def deco(fn):
        CASES.append((cid, group, desc, fn))
        return fn
    return deco


def card(name, color=None):
    return next(c for c in DB.cards if c["name"] == name and (color is None or c["color"] == color))


def filler(hero_name, fmt, n, copies, exclude=()):
    hero = next(c for c in DB.cards if c["name"] == hero_name)
    hs = set(hero["hero"]["supertypes"]) | set(hero["hero"]["essence_of"] or [])
    out, total = [], 0
    for c in DB.cards:
        if total >= n:
            break
        L = c["legality"][fmt]
        if (c["category"] != "deck" or not L["playable"] or L["conflicts"] or c["name"] in exclude
                or c["construction"]["specialization"] or c["construction"]["legendary"] or c["hero_metatypes"]
                or c["hybrid_supertype_sets"] or not set(c["classes"] + c["talents"]) <= hs or c["pitch"] is None):
            continue
        q = min(copies, n - total)
        out.append({"card_id": c["card_id"], "qty": q, "_filler": True})
        total += q
    return out


def summarize(deck):
    s = {"format": deck.get("format"), "hero": deck.get("hero")}
    for sec in ("deck", "arena", "inventory", "sideboard", "loadout", "registered_pool", "collection"):
        items = deck.get(sec)
        if items is None:
            continue
        fill = sum(x.get("qty", 1) for x in items if isinstance(x, dict) and x.get("_filler"))
        explicit = [{k: v for k, v in x.items() if k != "_filler"} for x in items
                    if not (isinstance(x, dict) and x.get("_filler"))]
        s[sec] = {"relleno_generado": fill, "explicitas": explicit[:8] + (["…"] if len(explicit) > 8 else [])}
    return s


def run(deck, mode="strict", expect_result=None, expect_errors=(), expect_unknowns=(), expect_warnings=(),
        forbid_errors=(), db=None, require_full_pool=False):
    rep = validate(deck, db or DB, mode, require_full_pool=require_full_pool)
    got = {"result": rep["result"],
           "errors": sorted({e["code"] for e in rep["errors"]}),
           "unknowns": sorted({e["code"] for e in rep["unknowns"]}),
           "warnings": sorted({e["code"] for e in rep["warnings"]})}
    exp = {"result": expect_result, "errors_include": list(expect_errors), "unknowns_include": list(expect_unknowns),
           "warnings_include": list(expect_warnings), "errors_exclude": list(forbid_errors)}
    ok = ((expect_result is None or (rep["result"] in expect_result if isinstance(expect_result, (set, tuple, list))
                                     else rep["result"] == expect_result))
          and set(expect_errors) <= set(got["errors"]) and set(expect_unknowns) <= set(got["unknowns"])
          and set(expect_warnings) <= set(got["warnings"]) and not (set(forbid_errors) & set(got["errors"])))
    if isinstance(exp["result"], (set, tuple)):
        exp["result"] = sorted(exp["result"])
    if require_full_pool:
        mode = mode + "+require_full_pool"
    return {"input": summarize(deck), "mode": mode, "expected": exp, "obtained": got, "passed": ok, "_report": rep}


def assertion(desc, cond, detail=None):
    return {"input": desc, "mode": "-", "expected": "verdadero", "obtained": {"value": bool(cond), "detalle": detail},
            "passed": bool(cond)}


# =========================================================== 25 casos originales
@case("T01", "base", "Mazo CC legal de 60 cartas")
def t01():
    return [run({"format": "Classic Constructed", "hero": KAYO, "deck": filler(KAYO, "cc", 60, 3)},
                expect_result={"VALIDO", "VALIDO_CON_ADVERTENCIAS"})]


@case("T02", "héroe", "Héroe young en CC")
def t02():
    return [run({"format": "cc", "hero": "Kayo", "deck": filler("Kayo", "cc", 60, 3)}, expect_result="INVALIDO",
                expect_errors=["HERO_AGE_MISMATCH"])]


@case("T03", "living legend", "Héroe Living Legend: ilegal en CC, legal en formato Living Legend")
def t03():
    h = "Kayo, Armed and Dangerous"
    return [run({"format": "cc", "hero": h, "deck": filler(h, "cc", 60, 3)}, expect_errors=["HERO_LIVING_LEGEND"]),
            run({"format": "ll", "hero": h, "deck": filler(h, "ll", 60, 3)}, forbid_errors=["HERO_LIVING_LEGEND"],
                expect_result={"VALIDO", "VALIDO_CON_ADVERTENCIAS"})]


@case("T04", "clase", "Carta de otra clase (Ninja con Kayo)")
def t04():
    return [run({"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 57, 3) +
                 [{"name": "Surging Strike", "color": "Red", "qty": 3}]}, expect_errors=["SUPERTYPE_MISMATCH"])]


@case("T05", "specialization", "Specialization de otro héroe")
def t05():
    vis = next(c for c in DB.cards if c["construction"]["specialization"] == ["Viserai"] and c["category"] == "deck")
    return [run({"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 59, 3) + [{"name": vis["name"], "color": vis["color"]}]},
                expect_errors=["SPECIALIZATION_MISMATCH"])]


@case("T06", "copias", "Límite por nombre + color (3+3+3 válido; 4 de un color no)")
def t06():
    base = filler(KAYO, "cc", 51, 3)
    sb = [{"name": "Sink Below", "color": c, "qty": 3} for c in ("Red", "Yellow", "Blue")]
    r1 = run({"format": "cc", "hero": KAYO, "deck": base + sb}, forbid_errors=["COPY_LIMIT"])
    sb[-1]["qty"] = 4
    return [r1, run({"format": "cc", "hero": KAYO, "deck": base + sb}, expect_errors=["COPY_LIMIT"])]


@case("T07", "copias", "Legendary con 2 copias")
def t07():
    leg = next(c for c in DB.cards if c["construction"]["legendary"] and c["is_generic"] and c["category"] == "deck"
               and c["legality"]["cc"]["playable"] and not c["legality"]["cc"]["conflicts"])
    return [run({"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 58, 3) + [{"name": leg["name"], "color": leg["color"], "qty": 2}]},
                expect_errors=["LEGENDARY_LIMIT"])]


@case("T08", "prohibidas", "Carta prohibida en CC (Belittle)")
def t08():
    return [run({"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 59, 3) + [{"name": "Belittle", "color": "Red"}]},
                mode=m, expect_errors=["CARD_BANNED"]) for m in ("strict", "exploratory")]


@case("T09", "tamaño", "Mazo de 59 en CC")
def t09():
    return [run({"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 59, 3)}, expect_errors=["DECK_TOO_SMALL"])]


@case("T10", "blitz", "Blitz: 2 copias y 41 cartas")
def t10():
    return [run({"format": "blitz", "hero": "Kayo", "deck": filler("Kayo", "blitz", 41, 2)}, expect_errors=["COPY_LIMIT", "DECK_SIZE"])]


@case("T11", "silver age", "SA: equipo de rareza Legendary y héroe en la banca")
def t11():
    return [run({"format": "silver age", "hero": "Kayo", "deck": filler("Kayo", "silver_age", 40, 2),
                 "arena": [{"name": "Fyendal's Spring Tunic"}]}, expect_errors=["CARD_NOT_LEGAL"]),
            run({"format": "silver age", "hero": "Oldhim", "deck": filler("Oldhim", "silver_age", 40, 2)}, expect_errors=["HERO_BENCHED"])]


@case("T12", "resolución", "Nombre ambiguo (error de entrada) y carta no encontrada (desconocida)")
def t12():
    return [run({"format": "cc", "hero": KAYO, "deck": [{"name": "Sink Below"}, {"name": "Viserai, the Forsaken"}]},
                expect_errors=["AMBIGUOUS_CARD"], expect_unknowns=["CARD_NOT_FOUND_IN_SOURCE"])]


@case("T13", "no encontrada", "Héroe no encontrado en la fuente -> INDETERMINADO, no ilegal")
def t13():
    return [run({"format": "cc", "hero": "Heroe Inexistente de Prueba", "deck": filler(KAYO, "cc", 60, 3)},
                expect_result="INDETERMINADO", expect_unknowns=["HERO_NOT_FOUND_IN_SOURCE"]),
            run({"format": "cc", "hero": "Viserai, the Forsaken", "deck": filler(KAYO, "cc", 60, 3)},
                expect_result="INDETERMINADO", expect_unknowns=["HERO_DATA_INCOMPLETE"],
                forbid_errors=["HERO_ILLEGAL_BY_POLICY_NOT_IN_SOURCE"])]


@case("T14", "híbridas", "Brute / Warrior pasa con Kayo; Guardian / Warrior no")
def t14():
    base = filler(KAYO, "cc", 59, 3)
    return [run({"format": "cc", "hero": KAYO, "deck": base + [{"name": "Agile Windup", "color": "Red"}]}, forbid_errors=["SUPERTYPE_MISMATCH"]),
            run({"format": "cc", "hero": KAYO, "deck": base + [{"name": "A Bit off the Side"}]}, expect_errors=["SUPERTYPE_MISMATCH"])]


@case("T15", "essence", "Carta de talento permitida por la Essence del héroe")
def t15():
    h = next(c for c in DB.cards if c["category"] == "hero" and not c["hero"]["is_young"] and c["hero"]["essence_of"]
             and c["legality"]["cc"]["playable"])
    tal = h["hero"]["essence_of"][-1]
    t = next(c for c in DB.cards if c["talents"] == [tal] and not c["classes"] and c["category"] == "deck"
             and c["legality"]["cc"]["playable"] and not c["construction"]["specialization"] and not c["legality"]["cc"]["conflicts"])
    return [run({"format": "cc", "hero": h["name"], "deck": filler(h["name"], "cc", 59, 3, exclude=(t["name"],)) +
                 [{"name": t["name"], "color": t["color"]}]},
                forbid_errors=["SUPERTYPE_MISMATCH"], expect_result={"VALIDO", "VALIDO_CON_ADVERTENCIAS"})]


@case("T16", "card-pool", "Card-pool > 80 y carta ausente del card-pool registrado")
def t16():
    deck = filler(KAYO, "cc", 60, 3)
    side = filler(KAYO, "cc", 81, 3)[20:]
    return [run({"format": "cc", "hero": KAYO, "deck": deck, "sideboard": side}, expect_errors=["POOL_TOO_LARGE"]),
            run({"format": "cc", "hero": KAYO, "deck": deck, "registered_pool": deck[:-1]}, expect_errors=["NOT_IN_REGISTERED_POOL"])]


@case("T17", "colección", "Colección insuficiente por carta y por impresión exacta")
def t17():
    deck = filler(KAYO, "cc", 60, 3)
    coll = [dict(x) for x in deck]
    coll[0]["qty"] = 1
    sb = card("Sink Below", "Yellow")
    p_first, p_last = sb["printing_ids"][0], sb["printing_ids"][-1]
    deck2 = filler(KAYO, "cc", 59, 3) + [{"printing_id": p_first}]
    coll2 = [dict(x) for x in deck2[:-1]] + [{"printing_id": p_last}]
    return [run({"format": "cc", "hero": KAYO, "deck": deck, "collection": coll}, expect_errors=["NOT_IN_COLLECTION"]),
            run({"format": "cc", "hero": KAYO, "deck": deck2, "collection": coll2}, expect_errors=["PRINTING_NOT_IN_COLLECTION"],
                forbid_errors=["NOT_IN_COLLECTION"])]


@case("T18", "secciones", "Equipo dentro del mazo y token en el card-pool")
def t18():
    return [run({"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 60, 3) + [{"name": "Fyendal's Spring Tunic"}],
                 "arena": [{"name": "Runechant"}]}, expect_errors=["ARENA_CARD_IN_DECK", "NOT_A_POOL_CARD"])]


@case("T19", "living legend (formato)", "LL: restringida de ciclo completo (roja + azul)")
def t19():
    h = "Kayo, Armed and Dangerous"
    return [run({"format": "ll", "hero": h, "deck": filler(h, "ll", 58, 3) + [
        {"name": "Electromagnetic Somersault", "color": "Red"}, {"name": "Electromagnetic Somersault", "color": "Blue"}]},
        expect_errors=["RESTRICTED_FULL_CYCLE"])]


@case("T20", "conflictos", "Diferencia repo/oficial: error en estricto, advertencia en exploratorio; LL solo oficial bloquea")
def t20():
    d = {"format": "cc", "hero": "Jarl Vetreiði", "deck": filler("Jarl Vetreiði", "cc", 59, 3) +
         [{"name": "Entwine Lightning", "color": "Red"}]}
    return [run(d, "strict", expect_errors=["LEGALITY_SOURCE_CONFLICT"]),
            run(d, "exploratory", expect_warnings=["LEGALITY_SOURCE_CONFLICT"], forbid_errors=["LEGALITY_SOURCE_CONFLICT"]),
            run({"format": "cc", "hero": "Oscilio, Constella Intelligence", "deck": []}, "exploratory",
                expect_errors=["HERO_LIVING_LEGEND"])]


@case("T21", "formato", "Formato no soportado (Commoner)")
def t21():
    return [run({"format": "Commoner", "hero": KAYO}, expect_result="INVALIDO", expect_errors=["UNSUPPORTED_FORMAT"])]


@case("T22", "loadout", "Dos armas 1H con Kayo, Underhanded Cheat (1 zona de arma)")
def t22():
    claw = next(c for c in DB.cards if "Brute" in c["classes"] and "Weapon" in c["card_types"] and "1H" in c["subtypes"]
                and c["legality"]["cc"]["playable"])
    return [run({"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 60, 3), "arena": [{"name": claw["name"], "qty": 2}],
                 "loadout": [{"name": claw["name"]}] * 2}, expect_errors=["LOADOUT_WEAPON_OVERFLOW"])]


@case("T23", "dos caras", "Código impreso de DFC resuelve al frente (UPR007 -> Invoke Tomeltai)")
def t23():
    c, _, err = DB.resolve({"print_code": "UPR007"})
    return [assertion({"print_code": "UPR007"}, err is None and c["name"] == "Invoke Tomeltai", c and c["name"])]


@case("T24", "metatipo", "Metatipo de héroe (Sticky Fingers solo para Scurv)")
def t24():
    return [run({"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 60, 3), "arena": [{"name": "Sticky Fingers"}]},
                expect_errors=["HERO_METATYPE_MISMATCH"])]


@case("T25", "acumulación", "Todos los errores se reportan en una sola pasada")
def t25():
    return [run({"format": "blitz", "hero": KAYO, "deck": [{"name": "Belittle", "color": "Red", "qty": 2},
                                                          {"name": "Surging Strike", "color": "Red"}]},
                expect_result="INVALIDO",
                expect_errors=["HERO_AGE_MISMATCH", "HERO_NOT_LEGAL", "COPY_LIMIT", "SUPERTYPE_MISMATCH", "DECK_SIZE"])]


# ===================================================================== nuevas
def _sa_rarity_fail(c):
    return not (set(c["rarities_printed"]) & SA_OK)


@case("N01", "silver age / super rare", "Ninguna carta sin impresión C/R/B es legal en Silver Age (toda la base)")
def n01():
    bad = [c["name"] for c in DB.cards if _sa_rarity_fail(c) and c["legality"]["silver_age"]["playable"]]
    sr_only = [c for c in DB.cards if "S" in c["rarities_printed"] and _sa_rarity_fail(c)]
    sr_blocked = [c for c in sr_only if c["legality"]["silver_age"]["base_legal"]]
    return [assertion("todas las cartas sin rareza C/R/B/T", not bad, bad[:5]),
            assertion("cartas Super Rare marcadas antes como legales por el repo, ahora ilegales (esperado: 70)",
                      len(sr_blocked) == 70 and all(not c["legality"]["silver_age"]["playable"] for c in sr_blocked),
                      len(sr_blocked))]


@case("N02", "silver age / super rare", "Una carta solo Super Rare no entra en un mazo SA (ambos modos)")
def n02():
    sr = next(c for c in DB.cards if c["rarities_printed"] == ["S"] and c["legality"]["silver_age"]["base_legal"]
              and c["category"] == "deck" and c["is_generic"])
    d = {"format": "silver age", "hero": "Kayo", "deck": filler("Kayo", "silver_age", 39, 2) + [{"name": sr["name"], "color": sr["color"]}]}
    return [run(d, m, expect_result="INVALIDO", expect_errors=["CARD_NOT_LEGAL"]) for m in ("strict", "exploratory")]


@case("N03", "silver age / super rare", "Toda carta jugable en SA tiene alguna impresión C/R/B/T")
def n03():
    bad = [c["name"] for c in DB.cards if c["legality"]["silver_age"]["playable"] and _sa_rarity_fail(c)]
    return [assertion("cartas jugables en SA", not bad, bad[:5])]


@case("N04", "no encontrada", "Carta no encontrada: estados unknown y resultado INDETERMINADO")
def n04():
    d = {"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 59, 3) + [{"name": "Carta Inexistente de Prueba"}]}
    r = run(d, expect_result="INDETERMINADO", expect_unknowns=["CARD_NOT_FOUND_IN_SOURCE"],
            forbid_errors=["CARD_NOT_LEGAL", "CARD_BANNED", "CARD_ILLEGAL_BY_POLICY_NOT_IN_SOURCE"])
    u = r["_report"]["unresolved_cards"][0]
    st = {k: u[k] for k in ("existence", "legality_state", "ban_state", "living_legend_state")}
    a = assertion("estados de la carta no encontrada", st == {"existence": "not_found_in_source", "legality_state": "unknown_legality",
                                                              "ban_state": "unknown_ban_status", "living_legend_state": "unknown_living_legend"}, st)
    return [r, a]


@case("N05", "no encontrada", "Carta no encontrada pero identificada por la política oficial -> ilegal (fixture de prueba)")
def n05():
    db = copy.copy(DB)
    db.policy_index = dict(DB.policy_index)
    db.policy_index["Carta Ficticia Prohibida (fixture)"] = {"cc": {"banned": {
        "value": True, "colors": None, "url": "https://fabtcg.com/rules-and-policy-center/card-legality-policy/",
        "section": "fixture", "rule": "R-BAN", "consulted_on": "2026-11-08"}}}
    d = {"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 59, 3) + [{"name": "Carta Ficticia Prohibida (fixture)"}]}
    return [run(d, expect_result="INVALIDO", expect_errors=["CARD_ILLEGAL_BY_POLICY_NOT_IN_SOURCE"], db=db)]


@case("N06", "no encontrada", "No encontrada en los 4 formatos: nunca 'illegal' sin evidencia oficial")
def n06():
    out = []
    for fmt in ("cc", "blitz", "silver_age", "ll"):
        st = DB.unresolved_states("Otra Carta Inexistente", None, fmt)
        out.append(assertion({"formato": fmt}, st["legality_state"] == "unknown_legality" and st["ban_state"] == "unknown_ban_status"
                             and st["living_legend_state"] in ("unknown_living_legend", "not_living_legend"), st))
    return out


@case("N07", "living legend", "Arma insignia Living Legend (Mandible Claw) en CC")
def n07():
    return [run({"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 60, 3), "arena": [{"name": "Mandible Claw"}]},
                m, expect_errors=["CARD_LIVING_LEGEND"]) for m in ("strict", "exploratory")]


@case("N08", "living legend", "LL solo según la política (Cindra): bloqueado en ambos modos")
def n08():
    h = "Cindra, Dracai of Retribution"
    return [run({"format": "cc", "hero": h, "deck": filler(h, "cc", 60, 3)}, "strict",
                expect_errors=["HERO_LIVING_LEGEND", "LEGALITY_SOURCE_CONFLICT"]),
            run({"format": "cc", "hero": h, "deck": filler(h, "cc", 60, 3)}, "exploratory",
                expect_errors=["HERO_LIVING_LEGEND"], expect_warnings=["LEGALITY_SOURCE_CONFLICT"])]


@case("N09", "living legend", "Blitz: estatus LL abolido; héroe 'Briar' (LL en el repo) es jugable")
def n09():
    return [run({"format": "blitz", "hero": "Briar", "deck": filler("Briar", "blitz", 40, 1)}, "strict",
                expect_errors=["LEGALITY_SOURCE_CONFLICT"], forbid_errors=["HERO_LIVING_LEGEND"]),
            run({"format": "blitz", "hero": "Briar", "deck": filler("Briar", "blitz", 40, 1)}, "exploratory",
                expect_result={"VALIDO_CON_ADVERTENCIAS"}, expect_warnings=["LEGALITY_SOURCE_CONFLICT"])]


@case("N10", "prohibidas", "Prohibida por la política aunque el repo la marque legal (Traverse the Universe, equipo Mystic; héroe Mystic adulto)")
def n10():
    t = card("Traverse the Universe")
    # Todos los héroes adultos Mystic son Living Legend en CC: su HERO_LIVING_LEGEND es esperado y ajeno a esta prueba.
    h = next(c for c in DB.cards if c["category"] == "hero" and not c["hero"]["is_young"] and "Mystic" in c["hero"]["supertypes"])
    return [assertion("repo la marca no prohibida en CC; es equipo (arena)", t["legality"]["cc"]["repo_flags"]["banned"] is False
                      and t["category"] == "arena"),
            *[run({"format": "cc", "hero": h["name"], "deck": filler(h["name"], "cc", 60, 3), "arena": [{"name": "Traverse the Universe"}]},
                  m, expect_errors=["CARD_BANNED"], forbid_errors=["ARENA_CARD_IN_DECK", "SUPERTYPE_MISMATCH"])
              for m in ("strict", "exploratory")]]


@case("N11", "conflictos", "Desbloqueo (repo prohíbe, oficial no): estricto bloquea, exploratorio permite con advertencia")
def n11():
    el = card("Entwine Lightning", "Red")
    sup = set(el["classes"] + el["talents"])
    h = next(c for c in DB.cards if c["category"] == "hero" and not c["hero"]["is_young"] and c["legality"]["cc"]["playable"]
             and sup <= set(c["hero"]["supertypes"]) | set(c["hero"]["essence_of"] or []))
    d = {"format": "cc", "hero": h["name"], "deck": filler(h["name"], "cc", 59, 3) + [{"name": "Entwine Lightning", "color": "Red"}]}
    return [run(d, "strict", expect_result="INVALIDO", expect_errors=["LEGALITY_SOURCE_CONFLICT"], forbid_errors=["CARD_BANNED"]),
            run(d, "exploratory", expect_result={"VALIDO", "VALIDO_CON_ADVERTENCIAS"}, expect_warnings=["LEGALITY_SOURCE_CONFLICT"])]


@case("N12", "impresiones", "Copias de una carta en impresiones distintas se suman")
def n12():
    sb = card("Sink Below", "Yellow")
    p1, p2 = sb["printing_ids"][0], sb["printing_ids"][1]
    d = {"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 56, 3) + [{"printing_id": p1, "qty": 2}, {"printing_id": p2, "qty": 2}]}
    return [assertion("las dos impresiones son distintas y de la misma carta",
                      p1 != p2 and DB.printing_by_id[p1]["card_id"] == DB.printing_by_id[p2]["card_id"]),
            run(d, expect_errors=["COPY_LIMIT"])]


@case("N13", "impresiones", "Mezclar nombre, card_id y printing_id de la misma carta cuenta como una")
def n13():
    sb = card("Sink Below", "Yellow")
    d = {"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 57, 3) + [
        {"name": "Sink Below", "color": "Yellow"}, {"card_id": sb["card_id"]}, {"printing_id": sb["printing_ids"][0], "qty": 2}]}
    return [run(d, expect_errors=["COPY_LIMIT"])]


@case("N14", "dos caras", "El reverso de una DFC (Tomeltai) no puede ir en el card-pool; el frente sí")
def n14():
    return [run({"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 60, 3), "sideboard": [{"name": "Tomeltai"}]},
                expect_errors=["NOT_A_POOL_CARD"]),
            assertion("Invoke Tomeltai es deck-card y enlaza su reverso",
                      card("Invoke Tomeltai")["category"] == "deck" and card("Invoke Tomeltai")["faces"]["is_double_sided"])]


@case("N15", "conflictos", "Cada diferencia registrada tiene procedencia completa")
def n15():
    with open(os.path.join(ROOT, "data", "legality_conflicts.json"), encoding="utf-8") as f:
        conf = json.load(f)
    req = ("name", "printings", "format", "repo_value", "official_value", "official_url", "consulted_on", "rule_applied",
           "policy_section", "category")
    bad = [c["id"] for c in conf if any(c.get(k) in (None, "", []) for k in req if k not in ("repo_value", "official_value"))
           or not c["official_url"].startswith("https://fabtcg.com/")]
    return [assertion(f"{len(conf)} diferencias con todos los campos", not bad and len(conf) == 170, bad[:5])]


def _build_tmp(entries):
    tmp = tempfile.mkdtemp(prefix="fabsupp_")
    sp = os.path.join(tmp, "supp.json")
    with open(sp, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f)
    sync.build(REPO, os.path.join(ROOT, "policy", "canonical_legality.json"), os.path.join(tmp, "out"), sp)
    load = lambda n: json.load(open(os.path.join(tmp, "out", n), encoding="utf-8"))  # noqa: E731
    return load


def _fixture_entry(**over):
    e = {"source": "official_manual_entry",
         "official_url": "https://fabtcg.com/rules-and-policy-center/release-notes/usurp-the-shadow-throne/",
         "consulted_on": "2026-11-08", "entered_by": "fixture-de-prueba",
         "card": {"name": "Carta Fixture De Prueba", "color": "Red", "pitch_raw": "1", "cost_raw": "1", "power_raw": "3",
                  "defense_raw": "2", "health_raw": None, "intellect_raw": None, "arcane_raw": None,
                  "type_text": "Generic Action - Attack", "types": ["Generic", "Action", "Attack"], "traits": [],
                  "card_keywords": [], "functional_text": "Texto de fixture.", "played_horizontally": False,
                  "base_legality": {"cc": None, "blitz": None, "silver_age": None, "ll": None}},
         "printing": {"print_code": "TST001", "set_id": "TST", "set_name": "Fixture", "edition": "N", "foiling": "S",
                      "rarity": "C", "artists": [], "flavor_text": None, "image_url": None, "release_date": "2026-09-25",
                      "art_variations": []}}
    for k, v in over.items():
        if "." in k:
            a, b = k.split(".")
            e[a] = dict(e[a])
            if v is KeyError:
                e[a].pop(b)
            else:
                e[a][b] = v
        elif v is KeyError:
            e.pop(k)
        else:
            e[k] = v
    return e


@case("N16", "suplementaria", "Entradas sin fuente verificable se rechazan (fixtures en dir temporal)")
def n16():
    entries = [_fixture_entry(official_url=KeyError), _fixture_entry(official_url="https://example.com/carta"),
               _fixture_entry(**{"card.functional_text": KeyError}), _fixture_entry(entered_by=""),
               _fixture_entry(source="repo")]
    load = _build_tmp(entries)
    rej, cards = load("supplement_rejected.json"), load("cards.json")
    return [assertion("5 entradas inválidas rechazadas y ninguna carga", len(rej) == 5 and
                      not any(c["name"] == "Carta Fixture De Prueba" for c in cards), [r["problems"][:1] for r in rej])]


@case("N17", "suplementaria", "Reconciliación: entrada oficial vs repo, prevalece la oficial y se registra")
def n17():
    e = _fixture_entry(**{"card.name": "Snatch", "card.color": "Red", "card.pitch_raw": "1", "card.cost_raw": "0",
                          "card.power_raw": "4", "card.defense_raw": "2", "card.type_text": "Generic Action - Attack",
                          "card.functional_text": "TEXTO OFICIAL DE PRUEBA (fixture)",
                          "card.base_legality": {"cc": True, "blitz": True, "silver_age": True, "ll": True},
                          "printing.print_code": "TST002"})
    load = _build_tmp([e])
    rec, cards = load("supplement_reconciliation.json"), load("cards.json")
    sn = next(c for c in cards if c["name"] == "Snatch" and c["color"] == "Red")
    return [assertion("diferencia registrada y valor oficial aplicado",
                      any(r["field"] == "functional_text" and r["applied"] == "official_manual_entry" for r in rec)
                      and sn["functional_text"] == "TEXTO OFICIAL DE PRUEBA (fixture)"
                      and "official_manual_entry" in sn["provenance"]["sources"], rec[:1])]


@case("N18", "suplementaria", "Carta nueva con legalidad base null queda unknown_legality con procedencia oficial")
def n18():
    load = _build_tmp([_fixture_entry()])
    c = next(x for x in load("cards.json") if x["name"] == "Carta Fixture De Prueba")
    return [assertion("procedencia y estados", c["provenance"]["sources"] == ["official_manual_entry"]
                      and c["provenance"]["manual_entries"][0]["official_url"].startswith("https://fabtcg.com/")
                      and all(c["legality"][f]["legality_state"] == "unknown_legality" for f in ("cc", "blitz", "silver_age", "ll")),
                      {f: c["legality"][f]["legality_state"] for f in ("cc", "silver_age")})]


@case("N19", "integridad", "printing_id único; print_code repetido (esperado)")
def n19():
    ids = [p["printing_id"] for p in DB.printings]
    codes = [p["print_code"] for p in DB.printings]
    return [assertion("sin duplicados de printing_id", len(ids) == len(set(ids)), {"total": len(ids), "unicos": len(set(ids))}),
            assertion("print_code no es único (por eso no se usa como clave)", len(codes) > len(set(codes)),
                      {"total": len(codes), "unicos": len(set(codes))})]


@case("N20", "conflictos", "Ninguna carta prohibida/LL por la política es jugable aunque el repo diga lo contrario")
def n20():
    bad = []
    for c in DB.cards:
        for f in ("cc", "blitz", "silver_age", "ll"):
            L = c["legality"][f]
            if L["playable"] and (L["ban_state"] == "banned" or L["living_legend_excludes_in_format"]):
                bad.append((c["name"], f))
    return [assertion("toda la base, 4 formatos", not bad, bad[:5])]


@case("N21", "loadout", "Arco 2H + quiver cabe (CR 8.2.15a); dos off-hands no (CR 8.2.10b)")
def n21():
    h = "Riptide, Lurker of the Deep"
    base = filler(h, "cc", 60, 3)
    ok = run({"format": "cc", "hero": h, "deck": base, "arena": [{"name": "Farflight Longbow"}, {"name": "Driftwood Quiver"}],
              "loadout": [{"name": "Farflight Longbow"}, {"name": "Driftwood Quiver"}]},
             forbid_errors=["LOADOUT_WEAPON_OVERFLOW", "LOADOUT_TOO_MANY_QUIVER"])
    two_oh = run({"format": "cc", "hero": h, "deck": base, "arena": [{"name": "Arcane Lantern"}, {"name": "Ornate Tessen"}],
                  "loadout": [{"name": "Arcane Lantern"}, {"name": "Ornate Tessen"}]},
                 expect_errors=["LOADOUT_TOO_MANY_OFF_HAND"])
    return [ok, two_oh]


# ============================================== fase 2b: lista canónica, Emperor, loadout
CANON = json.load(open(os.path.join(ROOT, "policy", "canonical_legality.json"), encoding="utf-8"))


def _lo(hero, items, **kw):
    """Mazo de relleno + arena/loadout con las cartas dadas; solo se miran los códigos LOADOUT_*."""
    base = filler(hero, "cc", 60, 3)
    arena = [{"name": n} for n in items]
    return run({"format": "cc", "hero": hero, "deck": base, "arena": arena, "loadout": arena}, **kw)


@case("N22", "canónica", "Orb-Weaver Spinneret (Blue/Yellow): NO prohibida en CC (desbloqueo oficial 2026-10-26)")
def n22():
    out = []
    for col in ("Blue", "Yellow"):
        L = card("Orb-Weaver Spinneret", col)["legality"]["cc"]
        ev = L["evidence"][0] if L["evidence"] else {}
        out.append(assertion(f"Orb-Weaver ({col}): not_banned por explicit_unban con texto literal",
                             L["ban_state"] == "not_banned" and L["ban_basis"] == "explicit_unban"
                             and ev.get("literal_text") == f"Orb-Weaver Spinneret ({col}) is unbanned."
                             and "26-10-26" in ev.get("official_url", ""), {"ban": L["ban_state"], "ev": ev}))
    out.append(assertion("no figura en la lista BANNED de CC de la CLP",
                         not any(e["name"] == "Orb-Weaver Spinneret" and e["list"] == "banned" for e in CANON["entries"])))
    return out


@case("N23", "canónica", "Toda carta de las listas BANNED de la CLP queda prohibida (ninguna omisión la vuelve legal)")
def n23():
    bad = []
    for e in CANON["entries"]:
        if e["list"] != "banned":
            continue
        cs = [c for c in DB.cards if c["name"] == e["name"] and (not e["colors"] or c["color"] in e["colors"])]
        if not cs or any(c["legality"][e["format"]]["playable"] or c["legality"][e["format"]]["ban_state"] != "banned" for c in cs):
            bad.append((e["format"], e["name"], e["colors"]))
    return [assertion(f'{sum(1 for e in CANON["entries"] if e["list"] == "banned")} entradas verificadas', not bad, bad[:5])]


@case("N24", "canónica", "Prohibición sin desbloqueo oficial: bloqueada en ambos modos (disputed)")
def n24():
    v = card("Volzar, the Lightning Rod")["legality"]["cc"]
    deck = {"format": "cc", "hero": KAYO, "deck": filler(KAYO, "cc", 60, 3), "arena": [{"name": "Winter's Wail"}]}
    return [assertion("Volzar CC: banned / disputed / no jugable", v["ban_state"] == "banned" and v["review_status"] == "disputed"
                      and not v["playable"], {k: v[k] for k in ("ban_state", "ban_basis", "status")}),
            run(deck, "strict", expect_result="INVALIDO", expect_errors=["CARD_DISPUTED"]),
            run(deck, "exploratory", expect_result="INVALIDO", expect_errors=["CARD_DISPUTED"])]


@case("N25", "canónica", "Promos de uso especial: ilegales en los 4 formatos aunque el repo diga legal")
def n25():
    out = []
    for n in ("Brutus, Summa Rudis", "Taipanis, Dracai of Judgement", "Squizzy & Floof"):
        c = card(n)
        out.append(assertion(n, all(c["legality"][f]["status"] == "special_use_promo" and not c["legality"][f]["playable"]
                                    for f in ("cc", "blitz", "silver_age", "ll")),
                             {f: c["legality"][f]["status"] for f in ("cc", "ll")}))
    return out


@case("N26", "canónica", "Normalización de nombres: apóstrofo tipográfico y alias documentados")
def n26():
    al = {a["source_name"]: a["normalized_name"] for a in CANON["aliases"]}
    briar = card("Briar, Warden of Thorns")["legality"]["cc"]
    r, _, err = DB.resolve({"name": "Ruu’di, Gem Keeper"})
    return [assertion("alias aprobados por el usuario; forma canónica pendiente porque las páginas oficiales difieren",
                      al.get("Briar, Warden of Thorn") == "Briar, Warden of Thorns" and al.get("Squizzy and Floof") == "Squizzy & Floof"
                      and all(a["status"] == "reviewed" and a["review"]["decided_by"] == "usuario"
                              and a["canonical_form_status"] == "pending_human_review"
                              and len({v["name"] for v in a["official_variants"]}) == 2
                              and a["source_url"].startswith("https://fabtcg.com/") for a in CANON["aliases"])),
            assertion("source_name conserva el texto literal de la fuente en cada entrada",
                      all(e["source_name"] == e["name_literal"] for e in CANON["entries"])
                      and any(e["source_name"] == "Briar, Warden of Thorn" and e["normalized_name"] == "Briar, Warden of Thorns"
                              for e in CANON["entries"])),
            assertion("Briar, Warden of Thorns es Living Legend en CC (vía alias)", briar["living_legend_state"] == "living_legend"),
            assertion("‘Ruu’di’ con apóstrofo tipográfico resuelve a la carta", r is not None and err is None)]


@case("N27", "canónica", "Viserai, the Forsaken: ausente de la fuente; determinación oficial: legal, no LL, no prohibido")
def n27():
    st = DB.unresolved_states("Viserai, the Forsaken", None, "cc")
    rep = run({"format": "cc", "hero": "Viserai, the Forsaken", "deck": []})
    return [assertion("estados", st["existence"] == "not_found_in_source" and st["legality_state"] == "legal"
                      and st["living_legend_state"] == "not_living_legend" and st["ban_state"] == "not_banned"
                      and st["review_status"] == "reviewed" and st["data_completeness"] == "incomplete",
                      st and {k: st[k] for k in ("legality_state", "ban_state", "living_legend_state", "data_completeness")}),
            assertion("el validador no lo reporta como ilegal y bloquea la construcción (HERO_DATA_INCOMPLETE)",
                      "HERO_ILLEGAL_BY_POLICY_NOT_IN_SOURCE" not in rep["obtained"]["errors"]
                      and "HERO_DATA_INCOMPLETE" in rep["obtained"]["unknowns"] and rep["obtained"]["result"] != "VALIDO",
                      rep["obtained"]),
            assertion("deck_construction_allowed = False mientras falten datos",
                      __import__("validator").deck_construction_allowed("Viserai, the Forsaken", DB)[0] is False
                      and __import__("validator").deck_construction_allowed("Kayo, Underhanded Cheat", DB)[0] is True)]


@case("N28", "canónica", "Toda entrada automática sigue pending_human_review salvo decisión humana registrada")
def n28():
    items = CANON["entries"] + CANON["events"] + CANON["format_rules"] + CANON["named_determinations"] + CANON["aliases"] + CANON["normalization_rules"]
    bad = [i.get("name") or i.get("source_name") for i in items
           if i["status"] != "pending_human_review" and not (i.get("review") or {}).get("decided_by")]
    rev = sum(i["status"] == "reviewed" for i in items)
    return [assertion(f"{len(items)} entradas; {rev} revisadas, todas con decisión registrada", not bad and rev == 3, bad)]


FORMATS_COPIES = 1  # Blitz: 1 copia por nombre + color (taxonomy.FORMATS["blitz"]["copies"])


def _red_filler(n):
    hero = card("Emperor, Dracai of Aesir")
    hs = set(hero["hero"]["supertypes"])
    out, total = [], 0
    for c in DB.cards:
        if total >= n:
            break
        L = c["legality"]["blitz"]
        if (c["category"] == "deck" and c["color"] == "Red" and L["playable"] and not L["conflicts"]
                and not c["construction"]["specialization"] and not c["construction"]["legendary"] and not c["hero_metatypes"]
                and not c["hybrid_supertype_sets"] and set(c["classes"] + c["talents"]) <= hs):
            out.append({"card_id": c["card_id"], "qty": FORMATS_COPIES})
            total += FORMATS_COPIES
    return out


def _db_with(extra):
    """Copia superficial de la base con cartas sintéticas añadidas (no toca data/)."""
    import collections as _c
    db = copy.copy(DB)
    db.cards = DB.cards + extra
    db.by_id = dict(DB.by_id, **{c["card_id"]: c for c in extra})
    db.by_name = _c.defaultdict(list, {k: list(v) for k, v in DB.by_name.items()})
    for c in extra:
        db.by_name[c["name"].casefold()].append(c)
    return db


def _synthetic_variant(base_name, new_name, **over):
    c = copy.deepcopy(card(base_name))
    c.update(card_id="SYN-" + new_name.replace(" ", "-"), name=new_name, uniqueness_key=new_name + "|-",
             print_codes=["SYN000"], printing_ids=[], **over)
    for f in c["legality"]:
        c["legality"][f]["conflicts"] = []
    return c


@case("N29", "emperor", "Emperor: mazo no rojo = error; arena sin color = advertencia de disputa NC-001; arena con color no rojo = error")
def n29():
    from validator import suggestion_allowed
    H = "Emperor, Dracai of Aesir"
    reds = _red_filler(40)
    nonred = next(c for c in DB.cards if c["category"] == "deck" and c["color"] == "Blue" and c["is_generic"]
                  and c["legality"]["blitz"]["playable"] and not c["legality"]["blitz"]["conflicts"] and not c["construction"]["legendary"])
    blue_eq = _synthetic_variant("Fyendal's Spring Tunic", "Tunica Sintetica Azul", color="Blue", pitch=3)
    sdb = _db_with([blue_eq])
    colorless = run({"format": "blitz", "hero": H, "deck": reds, "arena": [{"name": "Fyendal's Spring Tunic"}]},
                    expect_result="VALIDO_CON_ADVERTENCIAS", expect_warnings=["HERO_RULE_COLOR_SCOPE_DISPUTED"],
                    forbid_errors=["HERO_RULE_COLOR"])
    rep = colorless.pop("_report")
    w = next(x for x in rep["warnings"] if x["code"] == "HERO_RULE_COLOR_SCOPE_DISPUTED")
    srcs = {e["source"].split(" (")[0] for e in w["evidence"]}
    ok_ev = (w["resolution"] == "unresolved_normative_conflict" and w["conflict_id"] == "NC-001"
             and "CR 1.1.3a" in srcs and any("Ultimate Flex" in e["source"] for e in w["evidence"]))
    return [run({"format": "blitz", "hero": H, "deck": reds}, forbid_errors=["HERO_RULE_COLOR"],
                expect_result={"VALIDO", "VALIDO_CON_ADVERTENCIAS"}),
            run({"format": "blitz", "hero": H, "deck": reds[:-1] + [{"card_id": nonred["card_id"], "qty": 1}]},
                expect_result="INVALIDO", expect_errors=["HERO_RULE_COLOR"]),
            colorless,
            assertion("advertencia con resolución unresolved_normative_conflict, NC-001 y ambas citas", ok_ev, w.get("evidence")),
            run({"format": "blitz", "hero": H, "deck": reds, "arena": [{"name": "Dawnblade"}]},
                expect_warnings=["HERO_RULE_COLOR_SCOPE_DISPUTED"], forbid_errors=["HERO_RULE_COLOR"]),
            run({"format": "blitz", "hero": H, "deck": reds, "arena": [{"card_id": blue_eq["card_id"]}]},
                expect_result="INVALIDO", expect_errors=["HERO_RULE_COLOR"], db=sdb),
            assertion("suggestion_allowed bloquea equipo/armas de Emperor mientras NC-001 esté abierta",
                      suggestion_allowed(H, card("Dawnblade")) == (False, "NC-001")
                      and suggestion_allowed(H, card("Fyendal's Spring Tunic"))[0] is False
                      and suggestion_allowed(H, {"category": "deck"})[0] is True
                      and suggestion_allowed(KAYO, card("Dawnblade"))[0] is True)]


@case("N30", "loadout", "Matriz de regresión de zonas de arma y equipo (CR 8.2.2a/b, 8.2.10b, 8.2.15a/b, 8.3.39a, 4.1.4a)")
def n30():
    R, OV = "Riptide, Lurker of the Deep", ["LOADOUT_WEAPON_OVERFLOW"]
    return [
        _lo(R, ["Hatchet of Body", "Hatchet of Mind"], forbid_errors=OV),                                 # 1H + 1H
        _lo(R, ["Hatchet of Body", "Hatchet of Mind", "Ball Breaker"], expect_errors=OV),                 # 3 x 1H
        _lo(R, ["Talishar, the Lost Prince"], forbid_errors=OV),                                          # 2H solo
        _lo(R, ["Talishar, the Lost Prince", "Hatchet of Body"], expect_errors=OV),                       # 2H + 1H
        _lo(R, ["Farflight Longbow", "Driftwood Quiver"], forbid_errors=OV + ["LOADOUT_TOO_MANY_QUIVER"]),  # 2H arco + quiver
        _lo(R, ["Talishar, the Lost Prince", "Driftwood Quiver"], expect_errors=OV),                      # 2H no-arco + quiver
        _lo(R, ["Hatchet of Body", "Arcane Lantern"], forbid_errors=OV + ["LOADOUT_TOO_MANY_OFF_HAND"]),    # 1H + off-hand
        _lo(R, ["Arcane Lantern", "Ornate Tessen"], expect_errors=["LOADOUT_TOO_MANY_OFF_HAND"]),         # 2 off-hands
        _lo(R, ["Driftwood Quiver", "Enchanted Quiver"], expect_errors=["LOADOUT_TOO_MANY_QUIVER"]),      # 2 quivers
        _lo(R, ["Talishar, the Lost Prince", "Polly Cranka"], forbid_errors=OV),                          # 2H + Perched
        _lo(R, ["Arcanite Skullcap", "Balance of Justice"], expect_errors=["LOADOUT_ZONE_OVERFLOW"]),     # 2 cabezas
        _lo(R, ["Arcanite Skullcap", "Arcanite Fortress", "Blade Beckoner Gauntlets", "Base of the Mountain"],
            forbid_errors=["LOADOUT_ZONE_OVERFLOW"]),                                                     # equipo completo
        _lo(KAYO, ["Ball Breaker"], forbid_errors=OV),                                                    # 1 zona: 1H
        _lo(KAYO, ["Talishar, the Lost Prince"], expect_errors=OV),                                       # 1 zona: 2H
        _lo(KAYO, ["Ball Breaker", "Arcane Lantern"], expect_errors=OV),                                  # 1 zona: 1H + off-hand
    ]



def _w(name, *subtypes, keywords=()):
    """Carta sintética de zona de arma: solo los campos que usa la lógica de zonas."""
    return {"name": name, "subtypes": list(subtypes), "keywords": {"card": list(keywords)}}


@case("N31", "loadout-sintético", "Lógica pura de zonas con cartas sintéticas (combinaciones ausentes en los datos)")
def n31():
    from validator import evaluate_weapon_zones as ev
    bow1, bow2 = _w("Arco1H-sint", "Bow", "1H"), _w("Arco2H-sint", "Bow", "2H")
    quiv, quiv2 = _w("Quiver-sint", "Quiver"), _w("Quiver2-sint", "Quiver")
    oh, perch = _w("OffHand-sint", "Off-Hand"), _w("Perched-sint", "Off-Hand", keywords=["Perched"])
    sw1, sw2h = _w("Espada1H-sint", "Sword", "1H"), _w("Espada2H-sint", "Sword", "2H")
    codes = lambda r: sorted(c for c, _ in r["issues"])  # noqa: E731
    cases = [
        ("arco 1H + quiver, héroe de 2 zonas: cabe (2 zonas; 8.2.15a no aplica a 1H)", [bow1, quiv], 2, 2, []),
        ("arco 1H + quiver, héroe de 1 zona: overflow", [bow1, quiv], 1, 2, ["LOADOUT_WEAPON_OVERFLOW"]),
        ("arco 1H + quiver + espada 1H: overflow", [bow1, quiv, sw1], 2, 3, ["LOADOUT_WEAPON_OVERFLOW"]),
        ("arco 2H + quiver: cabe (CR 8.2.15a)", [bow2, quiv], 2, 2, []),
        ("arco 2H + 2 quivers: máximo 1 quiver (CR 8.2.15b) y el segundo no cabe", [bow2, quiv, quiv2], 2, 3, ["LOADOUT_TOO_MANY_QUIVER", "LOADOUT_WEAPON_OVERFLOW"]),
        ("espada 2H + quiver: overflow (no es arco)", [sw2h, quiv], 2, 3, ["LOADOUT_WEAPON_OVERFLOW"]),
        ("arco 1H + off-hand: cabe", [bow1, oh], 2, 2, []),
        ("arco 2H + Perched: cabe (CR 8.3.39a)", [bow2, perch], 2, 2, []),
        ("arco 2H + quiver + Perched: overflow (la zona libre admite UNA carta)", [bow2, quiv, perch], 2, 3, ["LOADOUT_WEAPON_OVERFLOW"]),
        ("espada 2H + Perched: cabe (CR 8.3.39a, cualquier 2H)", [sw2h, perch], 2, 2, []),
        ("quiver solo: 1 zona", [quiv], 2, 1, []),
    ]
    out = []
    for desc, cards_, mw, slots, exp in cases:
        r = ev(cards_, mw)
        out.append({"input": desc + " · " + " + ".join(c["name"] for c in cards_) + f" · zonas del héroe={mw}",
                    "mode": "-", "expected": {"slots": slots, "issues": exp},
                    "obtained": {"slots": r["slots"], "issues": codes(r)}, "passed": r["slots"] == slots and codes(r) == exp})
    return out


@case("N32", "disputas", "Disputas normativas: registro separado y bloqueo de Volzar / Winter's Wail")
def n32():
    nc = json.load(open(os.path.join(ROOT, "policy", "normative_conflicts.json"), encoding="utf-8"))["conflicts"]
    ids = {c["id"]: c for c in nc}
    conf = json.load(open(os.path.join(ROOT, "data", "legality_conflicts.json"), encoding="utf-8"))
    disp = [c for c in conf if c["category"] == "disputa_fuentes_oficiales"]
    return [assertion("NC-001..003 abiertas, con citas y estado pending_human_review",
                      set(ids) == {"NC-001", "NC-002", "NC-003"}
                      and all(c["resolution"] == "unresolved_normative_conflict" and c["status"] == "pending_human_review"
                              and len(c["positions"]) >= 2 for c in nc)),
            assertion("cada disputa de diferencias apunta a una entrada NC y sigue bloqueada",
                      len(disp) == 2 and all(d["conflict_id"] in ids and d["resolution"] == "unresolved_normative_conflict"
                                             and not card(d["name"])["legality"][d["format"]]["playable"] for d in disp),
                      [(d["name"], d["conflict_id"]) for d in disp])]



@case("N33", "esquemas", "Los datos generados cumplen los JSON Schema (regresión: estados special_use_promo y disputed)")
def n33():
    import subprocess
    r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "check_schemas.py")], capture_output=True, text=True)
    return [assertion("tools/check_schemas.py termina con código 0 y 0 errores", r.returncode == 0 and "errores de esquema: 0" in r.stdout
                      and r.stdout.count("errores de esquema: 0") == 3, r.stdout.strip()[-300:])]


RHINAR = "Rhinar, Reckless Rampage"
RHINAR_ARENA = [{"name": n} for n in ("Romping Club", "Monstrous Veil", "Savage Sash", "Hide Tanner", "Beaten Trackers")]


@case("N34", "card-pool", "Pool CC de 65 (60 + 5 arena): legal por TRP 7.1, pero bloqueado con la política del generador")
def n34():
    d = {"format": "cc", "hero": RHINAR, "deck": filler(RHINAR, "cc", 60, 3), "arena": RHINAR_ARENA}
    r1 = run(d, "strict", expect_result={"VALIDO", "VALIDO_CON_ADVERTENCIAS"}, forbid_errors=["POOL_INCOMPLETE"])
    r2 = run(d, "strict", expect_result="INVALIDO", expect_errors=["POOL_INCOMPLETE"], require_full_pool=True)
    pc = r2["_report"]["pool_composition"]
    return [r1, r2,
            assertion("composición 60 + 5 + 0 = 65, incompleto",
                      (pc["deck"], pc["arena"], pc["inventory"], pc["total"], pc["complete"]) == (60, 5, 0, 65, False), pc),
            assertion("sin la bandera se informa POOL_BELOW_MAX (info, no bloquea)",
                      "POOL_BELOW_MAX" in {i["code"] for i in r1["_report"]["infos"]})]


@case("N35", "card-pool", "Pool CC de 80 (60 + 5 arena + 15 inventario) pasa también con la política del generador")
def n35():
    deck = filler(RHINAR, "cc", 60, 3)
    used = {x["card_id"] for x in deck}
    inv = [x for x in filler(RHINAR, "cc", 200, 3) if x["card_id"] not in used]
    inv15, t = [], 0
    for x in inv:
        if t >= 15:
            break
        q = min(x["qty"], 15 - t)
        inv15.append({**x, "qty": q}); t += q
    d = {"format": "cc", "hero": RHINAR, "deck": deck, "arena": RHINAR_ARENA, "inventory": inv15}
    r = run(d, "strict", expect_result={"VALIDO", "VALIDO_CON_ADVERTENCIAS"}, forbid_errors=["POOL_INCOMPLETE"],
            require_full_pool=True)
    pc = r["_report"]["pool_composition"]
    d81 = dict(d, inventory=inv15 + [{"name": "Sink Below", "color": "Red", "qty": 1}])
    r81 = run(d81, "strict", expect_result="INVALIDO", expect_errors=["POOL_TOO_LARGE"], require_full_pool=True)
    return [r, r81, assertion("composición 60 + 5 + 15 = 80, completo",
                              (pc["deck"], pc["arena"], pc["inventory"], pc["total"], pc["complete"]) == (60, 5, 15, 80, True), pc)]


@case("N36", "card-pool", "'sideboard' es alias de 'inventory'; deck+arena+inventario debe cuadrar con el pool registrado")
def n36():
    deck = filler(RHINAR, "cc", 60, 3)
    d = {"format": "cc", "hero": RHINAR, "deck": deck, "arena": RHINAR_ARENA,
         "sideboard": [{"name": "Sink Below", "color": "Yellow", "qty": 3}]}
    r1 = run(d, "strict", expect_result={"VALIDO", "VALIDO_CON_ADVERTENCIAS"})
    pc = r1["_report"]["pool_composition"]
    reg = [{"card_id": x["card_id"], "qty": x["qty"]} for x in deck] + RHINAR_ARENA + \
          [{"name": "Sink Below", "color": "Yellow", "qty": 3}, {"name": "Fate Foreseen", "color": "Blue", "qty": 2}]
    d2 = {"format": "cc", "hero": RHINAR, "deck": deck, "arena": RHINAR_ARENA,
          "inventory": [{"name": "Sink Below", "color": "Yellow", "qty": 3}], "registered_pool": reg}
    r2 = run(d2, "strict", expect_result="INVALIDO", expect_errors=["REGISTERED_POOL_MISMATCH"])
    return [r1, r2, assertion("el alias cuenta como inventario (3)", pc["inventory"] == 3 and pc["total"] == 68, pc)]


def _pilot(name):
    return json.load(open(os.path.join(ROOT, "fase3", "pilotos", name + ".deck.json"), encoding="utf-8"))


@case("N37", "heurísticas", "Curva de recursos separada: turno propio vs turno rival; advertencia, nunca error")
def n37():
    out = []
    bravo = _pilot("bravo_cc_v1")
    r = run(bravo, "strict", expect_result="VALIDO_CON_ADVERTENCIAS", expect_warnings=["RESOURCE_CURVE_OWN_TURN"],
            forbid_errors=["RESOURCE_CURVE_OWN_TURN", "RESOURCE_CURVE_OPPONENT_TURN", "RESOURCE_CURVE"])
    rc = r["_report"]["counts"]["resource_curves"]
    total = sum((DB.resolve(e)[0]["cost"] or 0) * e["qty"] for e in bravo["deck"])
    out += [r, assertion("coste propio + coste de reacciones de defensa = coste total (sin mezclar)",
                         rc["own_turn"]["cost_total"] + rc["opponent_turn"]["cost_total"] == total
                         and rc["opponent_turn"]["cost_total"] > 0, rc),
            assertion("la advertencia antigua RESOURCE_CURVE ya no se emite",
                      "RESOURCE_CURVE" not in {w["code"] for w in r["_report"]["warnings"]})]
    dor = _pilot("dorinthea_cc_v1")
    r2 = run(dor, "strict", expect_result="VALIDO")
    rc2 = r2["_report"]["counts"]["resource_curves"]
    out += [r2, assertion("ambas curvas presentes con ratio numérico", rc2["own_turn"]["ratio"] and rc2["opponent_turn"]["ratio"], rc2)]
    return out


@case("N38", "datos", "text_incomplete: marcado en la base; advertencia no bloqueante en el pool y en cartas referenciadas")
def n38():
    lf = card("Lightning Flow")
    marked = sum(1 for c in DB.cards if c.get("text_incomplete"))
    d = {"format": "cc", "hero": RHINAR, "deck": filler(RHINAR, "cc", 57, 3) + [{"name": "Brutal Assault", "color": "Red", "qty": 3}],
         "arena": RHINAR_ARENA}
    r = run(d, "strict", expect_result={"VALIDO", "VALIDO_CON_ADVERTENCIAS"}, expect_warnings=["TEXT_INCOMPLETE"],
            forbid_errors=["TEXT_INCOMPLETE"])
    osc = run(_pilot("oscilio_cc_v1"), "strict", expect_result="VALIDO_CON_ADVERTENCIAS",
              expect_warnings=["TEXT_INCOMPLETE_REFERENCED"])
    ti = osc["_report"]["text_incomplete"]
    return [assertion("todas las cartas tienen el campo; Lightning Flow marcada; 38 en total",
                      all("text_incomplete" in c for c in DB.cards) and lf["text_incomplete"] and marked == 38, marked),
            r, osc,
            assertion("Oscilio: Lightning Flow aparece una sola vez como 'referenced'",
                      [x["card"] for x in ti if x["role"] == "referenced"] == ["Lightning Flow"], ti)]


import dependencies as DEPS  # noqa: E402


def _dep(hero, deck=(), arena=(), inventory=()):
    """Análisis de dependencias sobre secciones sintéticas (prueba unitaria del módulo, sin validar legalidad)."""
    sec = {k: [{"card": DB.resolve(e if isinstance(e, dict) else {"name": e})[0], "qty": 1} for e in v]
           for k, v in (("deck", deck), ("arena", arena), ("inventory", inventory))}
    h = DB.resolve({"name": hero})[0] if hero else None
    return DEPS.analyze(h, sec, DB)


def _st(findings, pattern, source_prefix):
    return [f["status"] for f in findings if f["pattern"] == pattern and f["source"].startswith(source_prefix)]


@case("N39", "dependencias", "K (keyword en otra carta): Beat Chest en Rhinar v1 / v2 / v3; Stealth sin habilitador")
def n39():
    v1 = run(_pilot("rhinar_cc_v1") | {"inventory": []}, "strict", expect_warnings=["DEPENDENCY_UNMET"],
             forbid_errors=["DEPENDENCY_UNMET"])
    v2 = run(_pilot("rhinar_cc_v2"), "strict", expect_warnings=["DEPENDENCY_INVENTORY_ONLY"],
             forbid_errors=["DEPENDENCY_INVENTORY_ONLY"])
    v3 = run(_pilot("rhinar_cc_v3"), "strict", expect_result="VALIDO_CON_ADVERTENCIAS")
    st3 = _st(v3["_report"]["dependencies"]["findings"], "K-BEATCHEST", "Alpha Instinct")
    f = _dep("Uzuri, Switchblade", deck=[{"name": "Razor's Edge", "color": "Red"}])
    f2 = _dep("Uzuri, Switchblade", deck=[{"name": "Razor's Edge", "color": "Red"}, {"name": "Back Stab", "color": "Red"}])
    return [v1, v2, v3,
            assertion("v3: Alpha Instinct satisfied y sin advertencias de dependencia",
                      st3 == ["satisfied"] and not any(w["code"].startswith("DEPENDENCY") for w in v3["_report"]["warnings"]), st3),
            assertion("Stealth: sin carta con stealth = unmet; con Back Stab = satisfied",
                      _st(f, "K-STEALTH", "Razor's Edge") == ["unmet"] and _st(f2, "K-STEALTH", "Razor's Edge") == ["satisfied"],
                      (_st(f, "K-STEALTH", "Razor"), _st(f2, "K-STEALTH", "Razor")))]


@case("N40", "dependencias", "S (estado): intimidated / boosted; el héroe cuenta como habilitador")
def n40():
    bm = {"name": "Beast Mode", "color": "Red"}
    a = _dep("Bravo, Showstopper", deck=[bm])
    b = _dep("Bravo, Showstopper", deck=[bm, {"name": "Alpha Rampage", "color": "Red"}])
    c = _dep("Rhinar, Reckless Rampage", deck=[bm])
    d = _dep("Maxx 'The Hype' Nitro", deck=[{"name": "Sink Below", "color": "Red"}], arena=["Achilles Accelerator"],
             inventory=[{"name": "Data Link", "color": "Red"}])
    return [assertion("Beast Mode sin intimidate = unmet", _st(a, "S-INTIMIDATED", "Beast Mode") == ["unmet"], a),
            assertion("con Alpha Rampage = satisfied", _st(b, "S-INTIMIDATED", "Beast Mode") == ["satisfied"], b),
            assertion("con el héroe Rhinar (Intimidate) = satisfied", _st(c, "S-INTIMIDATED", "Beast Mode") == ["satisfied"], c),
            assertion("boosted: Achilles Accelerator con Boost solo en el inventario (Data Link) = inventory_only",
                      _st(d, "S-BOOSTED", "Achilles Accelerator") == ["inventory_only"], d)]


@case("N41", "dependencias", "T (tipo): «Your next Guardian attack»; frase no reconocida = unknown_dependency")
def n41():
    ta = {"name": "Tear Asunder", "color": "Blue"}
    a = _dep("Bravo, Showstopper", deck=[ta])
    b = _dep("Bravo, Showstopper", deck=[ta], arena=["Anothos"])
    pred, desc = DB._type_parser.parse("6 or more", "card") if hasattr(DB, "_type_parser") else DEPS.TypeParser(DB).parse("6 or more", "card")
    p2, d2 = DEPS.TypeParser(DB).parse("Brute or Guardian", "attack")
    ok_or = p2(card("Anothos")) and p2(card("Alpha Rampage", "Red")) and not p2(card("Sink Below", "Red"))
    return [assertion("sin ataque Guardian = unmet", _st(a, "T-NEXT", "Tear Asunder") == ["unmet"], a),
            assertion("con Anothos (arma Guardian) = satisfied", _st(b, "T-NEXT", "Tear Asunder") == ["satisfied"], b),
            assertion("«6 or more» no se adivina: no verificable", pred is None and "6" in desc, desc),
            assertion("disyunción «Brute or Guardian attack»: Anothos y Alpha Rampage sí, Sink Below no", ok_or, d2)]


@case("N42", "dependencias", "N (carta concreta / token): combo, alternativas, token creado por el héroe")
def n42():
    wg = {"name": "Whelming Gustwave", "color": "Red"}
    a = _dep("Katsu, the Wanderer", deck=[wg])
    b = _dep("Katsu, the Wanderer", deck=[wg], inventory=[{"name": "Surging Strike", "color": "Red"}])
    c = _dep("Katsu, the Wanderer", deck=[{"name": "Bonds of Ancestry", "color": "Red"}, wg])
    d = _dep("Maxx 'The Hype' Nitro", deck=[{"name": "Hit the Gas", "color": "Blue"}])
    e = _dep("Bravo, Showstopper", deck=[{"name": "Hit the Gas", "color": "Blue"}])
    f = _dep("Bravo, Showstopper", arena=["Third Eye of the Sphinx"], deck=[{"name": "Sink Below", "color": "Red"}])
    g = _dep("Oscilio, Forked Continuum", arena=["Third Eye of the Sphinx"], deck=[{"name": "Sink Below", "color": "Red"}])
    sb = sorted(_st(c, "N-CARD", "Bonds of Ancestry"))
    return [assertion("Whelming Gustwave sin Surging Strike = unmet", _st(a, "N-CARD", "Whelming") == ["unmet"], a),
            assertion("Surging Strike solo en inventario = inventory_only", _st(b, "N-CARD", "Whelming") == ["inventory_only"], b),
            assertion("Bonds of Ancestry: Whelming presente; las otras Gustwave = satisfied_by_alternative",
                      sb == ["satisfied", "satisfied_by_alternative", "satisfied_by_alternative"], sb),
            assertion("Hit the Gas: Maxx crea Hyper Driver = satisfied; con Bravo = unmet",
                      _st(d, "N-CARD", "Hit the Gas") == ["satisfied"] and _st(e, "N-CARD", "Hit the Gas") == ["unmet"],
                      (_st(d, "N-CARD", "Hit"), _st(e, "N-CARD", "Hit"))),
            assertion("token Ponder: sin creador = unmet; con Oscilio (crea Ponder) = satisfied",
                      _st(f, "N-TOKEN", "Third Eye") == ["unmet"] and _st(g, "N-TOKEN", "Third Eye") == ["satisfied"],
                      (_st(f, "N-TOKEN", "Third"), _st(g, "N-TOKEN", "Third")))]


@case("N43", "dependencias", "Z (zonas) = unknown_dependency como info; nunca error ni INDETERMINADO; 9 mazos revalidados")
def n43():
    z = _dep("Bravo, Showstopper", deck=[{"name": "Unmovable", "color": "Red"}])
    out = [assertion("Unmovable: Z-ARSENAL = unknown_dependency", _st(z, "Z-ARSENAL", "Unmovable") == ["unknown_dependency"], z)]
    for k in ("rhinar_cc_v3", "dorinthea_cc_v1", "oscilio_cc_v1", "bravo_cc_v1", "katsu_cc_v1", "riptide_cc_v1",
              "vynnset_cc_v1", "maxx_cc_v1", "uzuri_cc_v1"):
        out.append(run(_pilot(k), "strict", expect_result={"VALIDO", "VALIDO_CON_ADVERTENCIAS"},
                       forbid_errors=["DEPENDENCY_UNMET", "DEPENDENCY_INVENTORY_ONLY", "DEPENDENCY_UNKNOWN"]))
    return out


@case("N44", "dependencias", "T-STAT / K-WITH: filtros por estadística o keyword (héroes Rhinar, Katsu, Bravo); lado rival excluido")
def n44():
    lo = [{"name": "Sink Below", "color": "Red"}, {"name": "Clearing Bellow", "color": "Blue"}]
    a = _dep("Rhinar, Reckless Rampage", deck=lo)
    b = _dep("Rhinar, Reckless Rampage", deck=lo + [{"name": "Alpha Rampage", "color": "Red"}])
    k = _dep("Katsu, the Wanderer", deck=[{"name": "Surging Strike", "color": "Red"}])
    k2 = _dep("Katsu, the Wanderer", deck=[{"name": "Whelming Gustwave", "color": "Red"}, {"name": "Surging Strike", "color": "Red"}])
    br = _dep("Bravo, Showstopper", deck=[{"name": "Pummel", "color": "Red"}])
    P = DEPS.TypeParser(DB)
    aeth = [r["pattern"] for r in DEPS.extract(card("Aetherize"), DB, P)]
    bb = [r["requires"] for r in DEPS.extract(card("Breakneck Battery", "Red"), DB, P) if r["pattern"] == "T-STAT"]
    return [assertion("Rhinar sin cartas de 6+ de poder = unmet; con Alpha Rampage = satisfied (copias contadas)",
                      _st(a, "T-STAT", "Rhinar") == ["unmet"] and _st(b, "T-STAT", "Rhinar") == ["satisfied"]
                      and [f["deck_copies"] for f in b if f["pattern"] == "T-STAT"] == [1], (a, b)),
            assertion("Katsu: coste 0 y combo; Surging Strike (coste 2, sin Combo) -> coste 0 unmet; con Whelming Gustwave (coste 0, Combo) ambos satisfied",
                      sorted(_st(k, "T-STAT", "Katsu")) == ["unmet"] and _st(k2, "T-STAT", "Katsu") == ["satisfied"]
                      and _st(k2, "K-WITH", "Katsu") == ["satisfied"], (k, k2)),
            assertion("Bravo: ataques de coste 3+; Pummel (reacción) no cuenta = unmet", _st(br, "T-STAT", "Bravo") == ["unmet"], br),
            assertion("Aetherize («Negate target instant card with cost 1 or less») es del rival: no genera dependencia",
                      "T-STAT" not in aeth, aeth),
            assertion("Breakneck Battery: «If the discarded card has 6 or more {p}» = T-STAT de poder", len(bb) == 1, bb)]


import suggestions as SUG  # noqa: E402


def _all_items(open_list):
    return [it for o in open_list for it in o["suggestions"]["items"]]


def _gate_ok(items, hero):
    import pilot_check as PC
    bad = []
    for it in items:
        c = DB.by_id[it["card_id"]]
        why = PC.gate_card(c, hero, "deck" if c["category"] == "deck" else "arena")
        if why or c.get("text_incomplete"):
            bad.append((it["card"], why))
    return bad


@case("N45", "sugerencias", "Caso 1 · dependencia abierta: cartas que la cierran (K, S, T, N), con texto y regla, todas elegibles y validadas")
def n45():
    out = []
    rv1 = _pilot("rhinar_cc_v1")
    o = SUG.open_dependencies(rv1, DB, limit=20)
    bc = [x for x in o if x["pattern"] == "K-BEATCHEST"]
    items = _all_items(bc)
    out.append(assertion("Rhinar v1: Alpha Instinct abierta; todas las candidatas tienen Beat Chest y la incluyen Bonebreaker Bellow / Pound Town",
                         len(bc) == 1 and items and all("Beat Chest" in DEPS._kw(DB.by_id[i["card_id"]]) for i in items)
                         and {"Bonebreaker Bellow", "Pound Town"} <= {DB.by_id[i["card_id"]]["name"] for i in items}
                         and all(i["explanation"]["rule"] == "CR 8.3.33" and i["validated"] for i in items), [i["card"] for i in items]))
    samples = {
        "S-BOOSTED": ({"format": "cc", "hero": "Maxx 'The Hype' Nitro", "deck": [{"name": "Sink Below", "color": "Red", "qty": 3}],
                       "arena": [{"name": "Achilles Accelerator", "qty": 1}]}, "Achilles", lambda c: "Boost" in DEPS._kw(c)),
        "T-NEXT": ({"format": "cc", "hero": "Bravo, Showstopper", "deck": [{"name": "Tear Asunder", "color": "Blue", "qty": 3}]},
                   "Tear Asunder", lambda c: "Guardian" in c["classes"] and ("Attack" in c["subtypes"] or "Weapon" in c["card_types"])),
        "N-CARD": ({"format": "cc", "hero": "Katsu, the Wanderer", "deck": [{"name": "Whelming Gustwave", "color": "Red", "qty": 3}]},
                   "Whelming", lambda c: c["name"] == "Surging Strike"),
        "N-TOKEN": ({"format": "cc", "hero": "Bravo, Showstopper", "deck": [{"name": "Sink Below", "color": "Red", "qty": 3}],
                     "arena": [{"name": "Third Eye of the Sphinx", "qty": 1}]}, "Third Eye", lambda c: "Ponder" in DEPS._text(c)),
    }
    for pat, (deck, src, ok_pred) in samples.items():
        o = [x for x in SUG.open_dependencies(deck, DB, limit=50) if x["pattern"] == pat and x["source"].startswith(src)]
        its = _all_items(o)
        out.append(assertion(f"{pat}: hay candidatas, todas cumplen el predicado, explicación con texto y regla",
                             len(o) == 1 and its and all(ok_pred(DB.by_id[i["card_id"]]) for i in its)
                             and all(i["explanation"]["why"] and i["explanation"]["rule"] for i in its),
                             [i["card"] for i in its][:8]))
        out.append(assertion(f"{pat}: ninguna candidata viola las compuertas de legalidad/disputas/promos/texto",
                             not _gate_ok(its, deck["hero"]), _gate_ok(its, deck["hero"])))
    return out


@case("N46", "sugerencias", "Caso 2 · satisfecha por alternativa: se explica qué carta nombrada la cubre, con el texto")
def n46():
    dor = SUG.explain_alternatives(_pilot("dorinthea_cc_v1"), DB)
    kat = SUG.explain_alternatives(_pilot("katsu_cc_v1"), DB)
    return [assertion("Dorinthea: 2 alternativas cubiertas por «Dawnblade»; el texto nombra Dawnblade",
                      len(dor) == 2 and all(a["covered_by"] and a["covered_by"][0]["named_card"] == "Dawnblade"
                                            and "Dawnblade" in a["source_text"] for a in dor), [(a["source"], a["target"]) for a in dor]),
            assertion("Katsu: 4 alternativas cubiertas por Whelming Gustwave; el texto dice «a card with Gustwave in its name»",
                      len(kat) == 4 and all(a["covered_by"][0]["named_card"] == "Whelming Gustwave"
                                            and "Gustwave in its name" in a["source_text"] for a in kat),
                      [(a["source"], a["target"]) for a in kat])]


@case("N47", "sugerencias", "Caso 3 · solo en inventario: carta que la cubre, validación del movimiento y valoración estructural")
def n47():
    v2 = SUG.inventory_moves(_pilot("rhinar_cc_v2"), DB)
    syn = {"format": "cc", "hero": "Maxx 'The Hype' Nitro", "deck": [{"name": "Sink Below", "color": "Red", "qty": 3}],
           "arena": [{"name": "Achilles Accelerator", "qty": 1}], "inventory": [{"name": "Data Link", "color": "Red", "qty": 1}]}
    m = SUG.inventory_moves(syn, DB)
    return [assertion("Rhinar v2: Alpha Instinct; mover Bonebreaker Bellow o Pound Town es válido y no cambia el pool; 3 copias dependientes",
                      len(v2) == 1 and {x["card"] for x in v2[0]["moves"]} == {"Bonebreaker Bellow (Red)", "Pound Town (Red)"}
                      and all(x["validator"]["ok"] and not x["validator"]["needs_swap"] for x in v2[0]["moves"])
                      and v2[0]["dependent_copies_in_deck"] == 3 and v2[0]["assessment"].startswith("Sí"), v2),
            assertion("Maxx sintético: Data Link (Boost) en inventario cubre Achilles Accelerator; se dice que es solo desde inventario",
                      [x["card"] for x in m[0]["moves"]] == ["Data Link (Red)"] and m[0]["status"] == "inventory_only", m)]


@case("N48", "sugerencias", "Caso 4 · dependencias típicas del héroe: estadística del catálogo elegible; zonas solo contadas")
def n48():
    a = SUG.hero_archetype("Rhinar, Reckless Rampage", DB)
    pats = {g["pattern"] for g in a["verifiable"]}
    hero_rows = [g for g in a["verifiable"] if g["from_hero"]]
    m = SUG.hero_archetype("Maxx 'The Hype' Nitro", DB)
    return [assertion("Rhinar: aparecen T-STAT (6+ poder, del héroe), S-INTIMIDATED y K-BEATCHEST con habilitadores > 0",
                      {"T-STAT", "S-INTIMIDATED", "K-BEATCHEST"} <= pats and hero_rows and hero_rows[0]["requires"].startswith("carta con 6")
                      and all(g["eligible_providers"] > 0 for g in a["verifiable"] if g["pattern"] in ("S-INTIMIDATED", "K-BEATCHEST")),
                      [(g["pattern"], g["requires"], g["source_count"]) for g in a["verifiable"]]),
            assertion("zonas (Z) nunca tienen habilitadores: van al bloque no verificable",
                      all(not g["pattern"].startswith("Z") for g in a["verifiable"]) and
                      any(k.startswith("Z") for k in a["not_verifiable"]["by_pattern"]), a["not_verifiable"]),
            assertion("Maxx: su habilidad pide S-BOOSTED; «Hyper Driver» no es dependencia del héroe y el héroe la cubre para las demás cartas",
                      any(g["from_hero"] and g["pattern"] == "S-BOOSTED" for g in m["verifiable"])
                      and not any(g["from_hero"] and g["pattern"] == "N-CARD" for g in m["verifiable"])
                      and any(g["pattern"] == "N-CARD" and g["requires"] == "carta «Hyper Driver»" and g["hero_provides"]
                              for g in m["verifiable"]),
                      [(g["pattern"], g["requires"], g["from_hero"]) for g in m["verifiable"]])]


@case("N49", "sugerencias", "Filtros: pool en 80 => sustitución; copias al máximo excluidas; no verificable sin propuesta; 9 mazos")
def n49():
    rv1 = _pilot("rhinar_cc_v1")
    full = dict(rv1, inventory=[{"name": "Scowling Flesh Bag", "qty": 1}, {"name": "Scabskin Leathers", "qty": 1},
                                {"name": "Torc of Vim", "qty": 1}, {"name": "Trampling Trackers", "qty": 1},
                                {"name": "Sink Below", "color": "Yellow", "qty": 3}, {"name": "Fate Foreseen", "color": "Yellow", "qty": 2},
                                {"name": "Sigil of Solace", "color": "Red", "qty": 3}, {"name": "Fate Foreseen", "color": "Blue", "qty": 3}])
    base = run(full, "strict", expect_result={"VALIDO", "VALIDO_CON_ADVERTENCIAS"}, require_full_pool=True)
    o = [x for x in SUG.open_dependencies(full, DB, limit=40) if x["pattern"] == "K-BEATCHEST"]
    its = _all_items(o)
    hero_r = DB.resolve({"name": "Rhinar, Reckless Rampage"})[0]
    v3 = _pilot("rhinar_cc_v3")
    f3 = [f for f in SUG._findings(v3, DB)[2] if f["pattern"] == "K-BEATCHEST"][0]
    c3 = SUG.candidates(f3, hero_r, v3, DB, limit=40)
    names3 = {DB.by_id[i["card_id"]]["name"] for i in c3["items"]}
    v2 = _pilot("rhinar_cc_v2")
    f2 = [f for f in SUG._findings(v2, DB)[2] if f["pattern"] == "K-BEATCHEST"][0]
    c2 = SUG.candidates(f2, hero_r, v2, DB, limit=3)
    z = {"format": "cc", "hero": "Bravo, Showstopper", "deck": [{"name": "Unmovable", "color": "Red", "qty": 1}]}
    zf = [f for f in SUG._findings(z, DB)[2] if f["pattern"].startswith("Z")]
    zc = SUG.candidates(zf[0], DB.resolve({"name": "Bravo, Showstopper"})[0], z, DB)
    nine = {k: SUG.deck_report(_pilot(k), DB) for k in ("rhinar_cc_v3", "dorinthea_cc_v1", "oscilio_cc_v1", "bravo_cc_v1",
                                                         "katsu_cc_v1", "riptide_cc_v1", "vynnset_cc_v1", "maxx_cc_v1", "uzuri_cc_v1")}
    return [base,
            assertion("pool completo (80): toda candidata nueva queda marcada como sustitución 1×1",
                      its and all("requiere sustituir" in i["pool_check"] for i in its), [i["pool_check"] for i in its][:3]),
            assertion("copias: con Bonebreaker Bellow R y Pound Town R ya a 3 en el mazo (v3), no se propone una 4.ª copia",
                      "Bonebreaker Bellow (Red)" not in {i["card"] for i in c3["items"]} and "Pound Town (Red)" not in {i["card"] for i in c3["items"]}
                      and c3["excluded"].get("ya al máximo de copias en el pool", 0) >= 2, c3["excluded"]),
            assertion("inventario a 3 copias (v2): se propone mover, no se excluye por copias",
                      [i["origin"] for i in c2["items"]][:2] == ["inventario (mover al mazo)"] * 2
                      and all("pool no cambia" in i["pool_check"] for i in c2["items"][:2]), c2["items"][:2]),
            assertion("Z (arsenal): no verificable, sin candidatas", zc["status"] == "not_verifiable" and not zc["items"], zc),
            assertion("9 mazos: 0 abiertas, 0 solo-inventario; alternativas solo en Dorinthea (2) y Katsu (4)",
                      all(not r["open"] and not r["inventory_only"] for r in nine.values())
                      and {k: len(r["alternatives"]) for k, r in nine.items() if r["alternatives"]} == {"dorinthea_cc_v1": 2, "katsu_cc_v1": 4},
                      {k: (len(r["open"]), len(r["alternatives"]), len(r["inventory_only"])) for k, r in nine.items()})]


@case("N50", "dependencias", "Producción vs condición: «create(s) X» no es dependencia; «if you've created X» sí")
def n50():
    P = DEPS.TypeParser(DB)
    maxx = [r["pattern"] for r in DEPS.extract(card("Maxx 'The Hype' Nitro"), DB, P)]
    td = [r["requires"] for r in DEPS.extract(card("Territorial Domain", "Blue"), DB, P) if r["family"] == "N"]
    clash = [r for r in DEPS.extract(card("Clash of Might", "Red"), DB, P) if r["family"] == "N"]
    return [assertion("Maxx crea Hyper Driver: no genera N-CARD (solo S-BOOSTED)", "N-CARD" not in maxx and "S-BOOSTED" in maxx, maxx),
            assertion("Territorial Domain («if you've created a Crouching Tiger»): sí es dependencia N", len(td) == 1, td),
            assertion("Clash of Might («The winner creates a Might token»): producción, no dependencia", not clash, clash)]


@case("N51", "dependencias", "Quién recibe el token (CR 8.5.2c): «the winner creates…» no satisface; own/shared sí; chosen condicional (N53); rival no")
def n51():
    sc = lambda name, tok, col=None: (DEPS.creator_scope(tok, card(name, col)) or ("—",))[0]
    table = {("Alpha Instinct", "Might"): sc("Alpha Instinct", "Might", "Blue"),
             ("Clash of Might", "Might"): sc("Clash of Might", "Might"),
             ("Fiddle-dee", "Might"): sc("Fiddle-dee", "Might"),
             ("Courageous Crossing", "Courage"): sc("Courageous Crossing", "Courage"),
             ("Take That!", "Might"): sc("Take That!", "Might"),
             ("Jinglewood (Might)", "Might"): sc("Jinglewood, Smash Hit", "Might"),
             ("Jinglewood (Copper)", "Copper"): sc("Jinglewood, Smash Hit", "Copper"),
             ("Midas Touch", "Gold"): sc("Midas Touch", "Gold"),
             ("Infect", "Bloodrot Pox"): sc("Infect", "Bloodrot Pox")}
    want = {("Alpha Instinct", "Might"): "own", ("Clash of Might", "Might"): "ambiguous", ("Fiddle-dee", "Might"): "shared",
            ("Courageous Crossing", "Courage"): "chosen", ("Take That!", "Might"): "rival",
            ("Jinglewood (Might)", "Might"): "rival", ("Jinglewood (Copper)", "Copper"): "own",
            ("Midas Touch", "Gold"): "ambiguous", ("Infect", "Bloodrot Pox"): "rival"}
    got = {f"{k[0]} → {k[1]}": v for k, v in table.items() if k in want}
    want = {f"{k[0]} → {k[1]}": v for k, v in want.items()}
    a = _dep("Rhinar, Reckless Rampage", deck=[{"name": "Clash of Might", "color": "Red"}], arena=["Raw Meat"])
    b = _dep("Rhinar, Reckless Rampage", deck=[{"name": "Clash of Might", "color": "Red"}, {"name": "Alpha Instinct", "color": "Blue"}],
             arena=["Raw Meat"])
    c = _dep("Rhinar, Reckless Rampage", deck=[{"name": "Take That!", "color": "Red"}], arena=["Raw Meat"])
    fa = [f for f in a if f["pattern"] == "N-TOKEN" and f["source"] == "Raw Meat" and f["target"] == "Might"]
    v = validate({"format": "cc", "hero": "Rhinar, Reckless Rampage", "deck": [{"name": "Clash of Might", "color": "Red", "qty": 3}],
                    "arena": [{"name": "Raw Meat", "qty": 1}]}, DB, "strict", require_full_pool=False)
    amb_info = [w for w in v["warnings"] if w["code"] == "DEPENDENCY_AMBIGUOUS" and "token Might" in w["message"]]
    unmet_might = [w for w in v["warnings"] if w["code"] == "DEPENDENCY_UNMET" and "token Might" in w["message"]]
    return [assertion("clasificación del creador según el texto", got == want, got),
            assertion("Raw Meat + solo Clash of Might («The winner creates a Might token») → ambiguous_provider, no satisfied",
                      [f["status"] for f in fa] == ["ambiguous_provider"] and fa[0]["providers"] == []
                      and fa[0]["providers_ambiguous"] == ["Clash of Might (Red)"], fa),
            assertion("validador: advertencia DEPENDENCY_AMBIGUOUS (desde prompt 8; antes info) y ninguna UNMET por Might (no es error)",
                      len(amb_info) == 1 and not unmet_might, (amb_info, unmet_might)),
            assertion("+ Alpha Instinct («create a Might token», propio) → satisfied, proveedor solo Alpha Instinct",
                      [(f["status"], f["providers"]) for f in b if f["pattern"] == "N-TOKEN" and f["target"] == "Might"]
                      == [("satisfied", ["Alpha Instinct (Blue)"])], b),
            assertion("solo Take That! («the defending hero creates» en un ataque = rival) → unmet, sin ambiguos",
                      [(f["status"], f.get("providers_ambiguous")) for f in c if f["pattern"] == "N-TOKEN" and f["target"] == "Might"]
                      == [("unmet", None)], c)]


@case("N52", "generación", "Compuerta de dependencias críticas: Rhinar v1/v2 fallan, v3 «no concluyente» (Z); ambigua anclada falla")
def n52():
    r1 = SUG.critical_dependencies(_pilot("rhinar_cc_v1"), DB)
    r2 = SUG.critical_dependencies(_pilot("rhinar_cc_v2"), DB)
    r3 = SUG.critical_dependencies(_pilot("rhinar_cc_v3"), DB)
    syn = {"format": "cc", "hero": "Rhinar, Reckless Rampage", "deck": [{"name": "Clash of Might", "color": "Red", "qty": 3}],
           "arena": [{"name": "Raw Meat", "qty": 1}]}
    ra = SUG.critical_dependencies(syn, DB, anchors=[("N-TOKEN", "creador del token Might")])
    rn = SUG.critical_dependencies(syn, DB)
    nine = {k: SUG.critical_dependencies(_pilot(k), DB)["verdict"] for k in
            ("rhinar_cc_v3", "dorinthea_cc_v1", "oscilio_cc_v1", "bravo_cc_v1", "katsu_cc_v1", "riptide_cc_v1",
             "vynnset_cc_v1", "maxx_cc_v1", "uzuri_cc_v1")}
    might = [x for x in ra["rows"] if x["requires"] == "creador del token Might"][0]
    might_n = [x for x in rn["rows"] if x["requires"] == "creador del token Might"][0]
    return [assertion("v1: Beat Chest crítica (C2, 3 copias de Alpha Instinct) y falla por unmet",
                      [(x["pattern"], x["statuses"]) for x in r1["critical_fail"]] == [("K-BEATCHEST", ["unmet"])], r1["critical_fail"]),
            assertion("v2: falla por inventory_only (solo inventario no basta para generar)",
                      [(x["pattern"], x["statuses"]) for x in r2["critical_fail"]] == [("K-BEATCHEST", ["inventory_only"])], r2["critical_fail"]),
            assertion("v3: ninguna crítica falla; la Z-HAND crítica queda «no verificable», no «pasa»",
                      not r3["critical_fail"] and r3["verdict"] == "no concluyente"
                      and [x["pattern"] for x in r3["critical_unverifiable"]] == ["Z-HAND"], r3["verdict"]),
            assertion("ancla C3 «creador del token Might» con solo Clash of Might → crítica y falla (ambiguous_provider)",
                      might["critical"] and "C3 ancla del arquetipo" in might["reasons"] and might["gate"] == "falla"
                      and might["statuses"] == ["ambiguous_provider"], might),
            assertion("sin ancla y 1 sola copia dependiente (Raw Meat): secundaria, no bloquea",
                      not might_n["critical"], might_n),
            assertion("9 mazos: ninguno falla la compuerta", "falla" not in nine.values(), nine)]


def _swap(deck, old, new):
    d = copy.deepcopy(deck)
    for e in d["deck"]:
        if (e["name"], e.get("color")) == old:
            e["name"], e["color"] = new
    return d


@case("N53", "dependencias", "Token «chosen» (under target hero's control) = condicional: advertencia, no cubre; alternativas documentadas")
def n53():
    bravo = _pilot("bravo_cc_v1")
    a = _swap(bravo, ("Blessing of Deliverance", "Blue"), ("Solid Ground", "Blue"))        # Solid Ground pide Seismic Surge
    b = _swap(a, ("Sink Below", "Blue"), ("Clash of Mountains", "Blue"))                    # + ambiguo («The winner creates»)
    c = _swap(a, ("Sink Below", "Blue"), ("Distant Rumbling", "Red"))                       # + propio («create 3 Seismic Surge»)
    ra, rb, rc = (validate(x, DB, "strict", require_full_pool=True) for x in (a, b, c))
    sg = lambda r: [(f["status"], f.get("providers"), f.get("providers_conditional"), f.get("providers_ambiguous"))
                    for f in r["dependencies"]["findings"] if f["source"].startswith("Solid Ground")]
    crit = SUG.critical_dependencies(a, DB, anchors=[("N-TOKEN", "creador del token Seismic Surge")])
    row = [x for x in crit["rows"] if x["requires"] == "creador del token Seismic Surge"][0]
    return [assertion("Star Struck se clasifica chosen («under any number of heroes' control»)",
                      DEPS.creator_scope("Seismic Surge", card("Star Struck", "Yellow"))[0] == "chosen", None),
            assertion("solo Star Struck cubre → conditionally_satisfied, sin proveedores firmes, sin errores",
                      sg(ra) == [("conditionally_satisfied", [], ["Star Struck (Yellow)"], None)] and not ra["errors"], sg(ra)),
            assertion("el validador emite la advertencia DEPENDENCY_CONDITIONAL → el mazo no puede quedar VALIDO limpio",
                      "DEPENDENCY_CONDITIONAL" in [w["code"] for w in ra["warnings"]] and ra["result"] != "VALIDO", ra["result"]),
            assertion("+ Clash of Mountains (ambiguo): sigue condicional y documenta la alternativa ambigua",
                      sg(rb) == [("conditionally_satisfied", [], ["Star Struck (Yellow)"], ["Clash of Mountains (Blue)"])], sg(rb)),
            assertion("+ Distant Rumbling (propio): satisfied, sin advertencia condicional; Star Struck queda documentada como alternativa",
                      sg(rc) == [("satisfied", ["Distant Rumbling (Red)"], ["Star Struck (Yellow)"], None)]
                      and "DEPENDENCY_CONDITIONAL" not in [w["code"] for w in rc["warnings"]], sg(rc)),
            assertion("compuerta de generación: una crítica solo condicional FALLA",
                      row["critical"] and row["gate"] == "falla" and row["statuses"] == ["conditionally_satisfied"], row),
            assertion("sugerencias: Star Struck no se propone como habilitadora (excluida como condicional)",
                      all("Star Struck" not in i["card"] for o in SUG.open_dependencies(_swap(a, ("Star Struck", "Yellow"), ("Sink Below", "Yellow")), DB, limit=50)
                          for i in o["suggestions"]["items"]), None)]


@case("N54", "generación", "critical_dependency_threshold configurable (heurística, por defecto 3)")
def n54():
    v3 = _pilot("rhinar_cc_v3")
    d3 = SUG.critical_dependencies(v3, DB)
    d4 = SUG.critical_dependencies(v3, DB, critical_dependency_threshold=4)
    d1 = SUG.critical_dependencies(v3, DB, critical_dependency_threshold=1)
    zc = lambda r: [x["pattern"] for x in r["rows"] if x["critical"] and x["pattern"].startswith("Z")]
    return [assertion("por defecto 3 = SUG.CRITICAL_DEPENDENCY_THRESHOLD y se informa en la salida",
                      SUG.CRITICAL_DEPENDENCY_THRESHOLD == 3 and d3["critical_dependency_threshold"] == 3, d3["critical_dependency_threshold"]),
            assertion("umbral 4: Z-HAND (3 copias) deja de ser crítica; umbral 1: hay más críticas que con 3",
                      zc(d3) == ["Z-HAND"] and zc(d4) == [] and d1["critical"] > d3["critical"] >= d4["critical"],
                      (d1["critical"], d3["critical"], d4["critical"])),
            assertion("C1 (héroe) no depende del umbral",
                      all(any("C1" in r for r in x["reasons"]) for x in d4["rows"] if x["critical"] and "C1 héroe" in x["reasons"])
                      and [x["requires"] for x in d4["rows"] if "C1 héroe" in x["reasons"]] ==
                          [x["requires"] for x in d3["rows"] if "C1 héroe" in x["reasons"]], None)]



def _dor_variant(red, blue):
    """Dorinthea v1 (VALIDO limpio) con Beckon Applause (pide Agility y Vigor) en brazos; Bracers pasa al inventario
    en lugar de Grains of Bloodspill (que crea Vigor); las dos Sink Below se cambian por `red` / `blue`."""
    d = copy.deepcopy(_pilot("dorinthea_cc_v1"))
    for sec in ("arena", "loadout"):
        for e in d.get(sec, []):
            if e["name"] == "Braveforge Bracers":
                e["name"] = "Beckon Applause"
    for e in d["inventory"]:
        if e["name"] == "Grains of Bloodspill":
            e["name"] = "Braveforge Bracers"
    for e in d["deck"]:
        if (e["name"], e.get("color")) == ("Sink Below", "Red"):
            e["name"], e["color"] = red
        if (e["name"], e.get("color")) == ("Sink Below", "Blue"):
            e["name"], e["color"] = blue
    return d


@case("N55", "dependencias", "Cobertura solo ambigua = advertencia DEPENDENCY_AMBIGUOUS (no válido limpio); cobertura propia = limpio")
def n55():
    base = validate(_pilot("dorinthea_cc_v1"), DB, "strict", require_full_pool=True)
    amb = _dor_variant(("Clash of Agility", "Red"), ("Clash of Vigor", "Blue"))       # «The winner creates…»
    own = _dor_variant(("Clash of Agility", "Red"), ("Goblet of Bloodrun Wine", "Blue"))  # «Create an Agility and a Vigor token»
    ra = validate(amb, DB, "strict", require_full_pool=True)
    ro = validate(own, DB, "strict", require_full_pool=True)
    bk = lambda r: sorted((f["target"], f["status"], tuple(f.get("providers") or []), tuple(f.get("providers_ambiguous") or []))
                          for f in r["dependencies"]["findings"] if f["source"] == "Beckon Applause")
    ga = SUG.critical_dependencies(amb, DB, anchors=[("N-TOKEN", "creador del token Agility")])
    row = [x for x in ga["rows"] if x["requires"] == "creador del token Agility"][0]
    go = SUG.critical_dependencies(own, DB, anchors=[("N-TOKEN", "creador del token Agility")])
    row_o = [x for x in go["rows"] if x["requires"] == "creador del token Agility"][0]
    return [assertion("punto de partida: Dorinthea v1 es VALIDO limpio", base["result"] == "VALIDO" and not base["warnings"], base["result"]),
            assertion("solo Clash of Agility / Clash of Vigor → ambiguous_provider en ambas",
                      bk(ra) == [("Agility", "ambiguous_provider", (), ("Clash of Agility (Red)",)),
                                 ("Vigor", "ambiguous_provider", (), ("Clash of Vigor (Blue)",))], bk(ra)),
            assertion("validador: 2 advertencias DEPENDENCY_AMBIGUOUS, sin errores → VALIDO_CON_ADVERTENCIAS (no limpio)",
                      [w["code"] for w in ra["warnings"]] == ["DEPENDENCY_AMBIGUOUS"] * 2 and not ra["errors"]
                      and ra["result"] == "VALIDO_CON_ADVERTENCIAS", (ra["result"], [w["code"] for w in ra["warnings"]], ra["errors"])),
            assertion("con Goblet of Bloodrun Wine (propio): satisfied; Clash of Agility documentada como alternativa ambigua",
                      bk(ro) == [("Agility", "satisfied", ("Goblet of Bloodrun Wine (Blue)",), ("Clash of Agility (Red)",)),
                                 ("Vigor", "satisfied", ("Goblet of Bloodrun Wine (Blue)",), ())], bk(ro)),
            assertion("con cobertura propia el mazo queda VALIDO limpio", ro["result"] == "VALIDO" and not ro["warnings"],
                      (ro["result"], [w["code"] for w in ro["warnings"]])),
            assertion("compuerta: ancla Agility solo ambigua → falla y exige sustitución por habilitador propio",
                      row["gate"] == "falla" and row["remedy"] and "sustituir" in row["remedy"][0], row),
            assertion("compuerta: con Goblet pasa y no pide remedio", row_o["gate"] == "pasa" and row_o["remedy"] == [], row_o)]



@case("N56", "dependencias", "«ally attack»: aliado con habilidad activada «…: Attack» habilita (CR 8.3.1, 1.4.3a); sin ella, no")
def n56():
    G = "Gravy Bones, Shipwrecked Looter"
    st = lambda fs, src: [(f["status"], f.get("providers")) for f in fs if f["source"].startswith(src) and f["pattern"] == "T-NEXT"]
    ok = _dep(G, deck=[{"name": "Avast Ye!", "color": "Blue"}, {"name": "Riggermortis", "color": "Yellow"}])
    no = _dep(G, deck=[{"name": "Avast Ye!", "color": "Blue"}, {"name": "Swindler's Grift", "color": "Red"}])
    polly = _dep(G, deck=[{"name": "Avast Ye!", "color": "Blue"}], arena=["Polly Cranka"])
    sticky_any = _dep(G, deck=[{"name": "Cutty Shark, Quick Clip", "color": "Yellow"}], arena=["Sticky Fingers"])
    sticky_pir = _dep(G, deck=[{"name": "Avast Ye!", "color": "Blue"}], arena=["Sticky Fingers"])
    cutty_self = _dep(G, deck=[{"name": "Cutty Shark, Quick Clip", "color": "Yellow"}])
    hero = DB.resolve({"name": G})[0]
    f = [x for x in SUG._findings({"format": "cc", "hero": G, "deck": [{"name": "Avast Ye!", "color": "Blue", "qty": 1}]}, DB)[2]
         if x["pattern"] == "T-NEXT"][0]
    cand = SUG.candidates(f, hero, {"format": "cc", "hero": G, "deck": [{"name": "Avast Ye!", "color": "Blue", "qty": 1}]}, DB,
                          limit=50, validate=False)
    pred = DEPS.TypeParser(DB).parse("ally", "attack")[0]
    return [assertion("Avast Ye! + Riggermortis («Action - {r}, {t}: Attack») → satisfied",
                      st(ok, "Avast Ye!") == [("satisfied", ["Riggermortis (Yellow)"])], st(ok, "Avast Ye!")),
            assertion("Avast Ye! sin aliados (solo Swindler's Grift, un ataque que no es aliado) → unmet",
                      st(no, "Avast Ye!") == [("unmet", [])], st(no, "Avast Ye!")),
            assertion("caso límite: Polly Cranka (Off-Hand Ally sin habilidad «Attack») no habilita → unmet",
                      st(polly, "Avast Ye!") == [("unmet", [])] and DEPS.ally_attack_ability(card("Polly Cranka")) is None,
                      st(polly, "Avast Ye!")),
            assertion("Sticky Fingers (aliado con «{t}: Attack», sin clase Pirate): cubre «ally attack» pero NO «Pirate ally attack»",
                      st(sticky_any, "Cutty Shark") == [("satisfied", ["Sticky Fingers"])]
                      and st(sticky_pir, "Avast Ye!") == [("unmet", [])], (st(sticky_any, "Cutty Shark"), st(sticky_pir, "Avast Ye!"))),
            assertion("Cutty Shark no se cubre a sí misma (la propia carta queda excluida)",
                      st(cutty_self, "Cutty Shark") == [("unmet", [])], st(cutty_self, "Cutty Shark")),
            assertion("aliados Dragon sin habilidad activada impresa (Azvolai) no cuentan: precisión antes que cobertura",
                      not pred(card("Azvolai")), None),
            assertion("«Pirate attack» SÍ incluye aliados Pirate con «Attack» desde el prompt 11 (herencia, CR 1.4.3a; ver N57)",
                      DEPS.TypeParser(DB).parse("Pirate", "attack")[0](card("Riggermortis", "Yellow")), None),
            assertion("sugerencias: 18 aliados Pirate elegibles, cada uno con su «…: Attack» y la regla citada",
                      cand["total"] == 18 and all(i["explanation"]["text"].endswith("Attack") and "CR 8.3.1" in i["explanation"]["rule"]
                                                  for i in cand["items"]), (cand["total"], cand["items"][:2]))]



@case("N57", "dependencias", "Herencia CR 1.4.3a: el ataque de un aliado con «…: Attack» es «[clase] ally attack», «[clase] attack» y «attack»")
def n57():
    P = DEPS.TypeParser(DB)
    f = lambda phrase: P.parse(phrase, "attack")[0]
    rig, sticky, polly, azv = card("Riggermortis", "Yellow"), card("Sticky Fingers"), card("Polly Cranka"), card("Azvolai")
    tok = [c for c in DB.cards if c["name"] == "Cintari Sellsword"][0]
    any_attack = lambda c: DEPS._cond(c, "attack_capable", None)   # «attack» a secas = capaz de atacar (sin más filtro)
    gravy = json.load(open(os.path.join(ROOT, "fase3", "generados", "gravy_cc_v1.deck.json")))
    hook = [x for x in SUG._findings(gravy, DB)[2] if x["source"] == "Gold-Baited Hook" and x["pattern"] == "T-NEXT"][0]
    return [assertion("aliado Pirate con «Attack» (Riggermortis): cubre «Pirate ally attack», «Pirate attack» y «attack»",
                      f("Pirate ally")(rig) and f("Pirate")(rig) and any_attack(rig), None),
            assertion("aliado no Pirate con «Attack» (Sticky Fingers): cubre «ally attack» y «attack», NO «Pirate attack» ni «Pirate ally attack»",
                      f("ally")(sticky) and any_attack(sticky) and not f("Pirate")(sticky) and not f("Pirate ally")(sticky), None),
            assertion("aliado sin «Attack» (Polly Cranka): no cubre nada",
                      not any(g(polly) for g in (f("ally"), f("Pirate ally"), f("Pirate"))) and not any_attack(polly), None),
            assertion("límite: Dragon sin habilidad impresa (Azvolai) no cuenta; aliado token (Cintari Sellsword) tiene «Attack» "
                      "pero no está en mazo/arena: el análisis solo mira cartas del pool",
                      not any_attack(azv) and tok["category"] == "token" and DEPS.ally_attack_ability(tok) is not None, None),
            assertion("Gravy: Gold-Baited Hook («Your next Pirate attack») ahora lo cubren también los 4 aliados Pirate del mazo",
                      hook["status"] == "satisfied" and {"Riggermortis (Yellow)", "Sawbones, Dock Hand (Yellow)", "Barnacle (Yellow)",
                                                         "Oysten, Heart of Gold (Yellow)"} <= set(hook["providers"]), hook["providers"]),
            assertion("explicación de la sugerencia para un aliado en «Pirate attack»: texto «…: Attack» + CR 8.3.1 / 1.4.3a",
                      "CR 1.4.3" in SUG.explain_provider(hook, rig)["rule"] and SUG.explain_provider(hook, rig)["text"].endswith("Attack"),
                      SUG.explain_provider(hook, rig))]



# ======================================================================================= prompt 14: correcciones A–E
import subprocess  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "tools"))
import pilot_check as PCK  # noqa: E402


def _pilot_check(deck, policy=None):
    """Ejecuta tools/pilot_check.py sobre un mazo (archivo temporal) y devuelve el JSON completo."""
    with tempfile.TemporaryDirectory() as td:
        src, dst = os.path.join(td, "d.json"), os.path.join(td, "o.json")
        json.dump(deck, open(src, "w"))
        cmd = [sys.executable, "-I", os.path.join(ROOT, "tools", "pilot_check.py"), src, "--out", dst]
        if policy:
            cmd += ["--policy", json.dumps(policy)]
        subprocess.run(cmd, capture_output=True, text=True)
        return json.load(open(dst))


@case("N58", "regla oficial · CR 2.7.5", "A · «with Tome in their name» = palabra completa: Invoke Tomeltai NO; Tome of… / Gorganian Tome SÍ")
def n58():
    L = "The Librarian, Magister of History"
    bad = validate({"format": "cc", "hero": L, "deck": [{"name": "Invoke Tomeltai", "qty": 1}]}, DB, "strict")
    ok = validate({"format": "cc", "hero": L, "deck": [{"name": "Tome of the Arknight", "qty": 1}]}, DB, "strict")
    import validator as VV
    return [assertion("Invoke Tomeltai (Draconic Illusionist) en Librarian → SUPERTYPE_MISMATCH",
                      "SUPERTYPE_MISMATCH" in [e["code"] for e in bad["errors"]], [e["code"] for e in bad["errors"]]),
            assertion("Tome of the Arknight (otra clase, «Tome» palabra completa) → sin SUPERTYPE_MISMATCH",
                      "SUPERTYPE_MISMATCH" not in [e["code"] for e in ok["errors"]], [e["code"] for e in ok["errors"]]),
            assertion("name_has_words: positivos y negativos (CR 2.7.5)",
                      VV.name_has_words("Gorganian Tome", "Tome") and VV.name_has_words("Tome of Harvests", "Tome")
                      and not VV.name_has_words("Invoke Tomeltai", "Tome") and not VV.name_has_words("Tomeltai", "Tome")
                      and not VV.name_has_words("Breaker Helm Protos", "Proto"), None)]


@case("N59", "regla oficial · CR 8.1.12a / 8.1.7a", "B · Block y Resource no tienen coste: no es «dato incompleto»; el resto de comprobaciones sigue")
def n59():
    g = lambda n, h, s="deck": PCK.gate_card([c for c in DB.cards if c["name"] == n][0], h, s)
    G = "Gravy Bones, Shipwrecked Looter"
    rh = validate({"format": "cc", "hero": "Rhinar, Reckless Rampage", "deck": [{"name": "Hoist 'Em Up", "qty": 1}]}, DB, "strict")
    return [assertion("Blocks Pirate (Hoist 'Em Up, Lost in Transit, Sunken Treasure) pasan la compuerta de datos",
                      g("Hoist 'Em Up", G) == [] and g("Lost in Transit", G) == [] and g("Sunken Treasure", G) == [], None),
            assertion("Resource sin coste (Arknight Shard) pasa la compuerta de datos", g("Arknight Shard", G) == [], g("Arknight Shard", G)),
            assertion("negativo: carta JUGABLE sin coste en datos (Night's Embrace, Attack Reaction) sigue rechazada",
                      "dato incompleto: cost_raw" in g("Night's Embrace", G), g("Night's Embrace", G)),
            assertion("se conservan las demás comprobaciones: Hoist 'Em Up (Pirate Necromancer) en Rhinar → SUPERTYPE_MISMATCH",
                      "SUPERTYPE_MISMATCH" in [e["code"] for e in rh["errors"]], [e["code"] for e in rh["errors"]]),
            assertion("las Blocks siguen necesitando pitch: sin pitch_raw sería rechazada",
                      PCK._required_fields({"card_types": ["Block"]}) == ("pitch_raw",), None)]


@case("N60", "regla oficial · CR 2.7.3", "C · Monikers de los ejemplos literales de la CR: Ser Boltyn → Boltyn; The Librarian → The Librarian")
def n60():
    b, l, g = card("Ser Boltyn, Breaker of Dawn"), card("The Librarian, Magister of History"), card("Gravy Bones, Shipwrecked Looter")
    el = {c["name"] for c in SUG._eligible(DB, b)}
    eld = {c["name"] for c in SUG._eligible(DB, card("Dorinthea Ironsong"))}
    return [assertion("Ser Boltyn: moniker «Boltyn» con fuente cr_2.7.3_example",
                      (b["hero"]["moniker"], b["hero"]["moniker_source"]) == ("Boltyn", "cr_2.7.3_example"), b["hero"]),
            assertion("The Librarian: moniker «The Librarian» con fuente cr_2.7.3_example",
                      (l["hero"]["moniker"], l["hero"]["moniker_source"]) == ("The Librarian", "cr_2.7.3_example"), l["hero"]),
            assertion("Specialization «Boltyn» (Lumina Ascension, V of the Vanguard) ahora elegible para Ser Boltyn",
                      {"Lumina Ascension", "V of the Vanguard"} <= el, None),
            assertion("negativo: siguen sin ser elegibles para otro héroe (Dorinthea)",
                      not ({"Lumina Ascension", "V of the Vanguard"} & eld), None),
            assertion("no se deducen otros monikers: Gravy Bones sigue «Gravy» heurístico",
                      (g["hero"]["moniker"], g["hero"]["moniker_source"]) == ("Gravy", "heuristic"), g["hero"])]


@case("N61", "regla oficial · CR 2.7.5", "D · «Gold» no es «Golden Cog», «Goldkiss Rum» ni «Goldfin Harpoon»; plurales y otros tokens siguen funcionando")
def n61():
    s = lambda n, tok, col=None: (DEPS.creator_scope(tok, card(n, col)) or (None,))[0]
    return [assertion("Cog in the Machine / Tip the Barkeep / Sea Legs / Marlynn NO crean Gold",
                      s("Cog in the Machine", "Gold") is None and s("Tip the Barkeep", "Gold", "Blue") is None
                      and s("Sea Legs", "Gold") is None and s("Marlynn, Treasure Hunter", "Gold") is None, None),
            assertion("positivo: Swindler's Grift y Break Open the Chests! («create 2 Gold tokens») siguen creando Gold",
                      s("Swindler's Grift", "Gold", "Red") == "own" and s("Break Open the Chests!", "Gold") == "own", None),
            assertion("positivo: Cog in the Machine sí crea «Golden Cog»", s("Cog in the Machine", "Golden Cog") == "own", None),
            assertion("Tip the Barkeep («give a Gold token you control») pasa a depender de un creador de Gold",
                      any(r["requires"] == "creador del token Gold" for r in DEPS.extract(card("Tip the Barkeep", "Blue"), DB, DEPS.TypeParser(DB))),
                      None)]


@case("N62", "regla oficial · CR 8.5.46b", "E · Wager: el ganador crea el premio → no exige tenerlo; producción posible, no garantizada (ambiguous)")
def n62():
    P = DEPS.TypeParser(DB)
    wm = DEPS.extract(card("Wage Might"), DB, P)
    ea = [r["pattern"] for r in DEPS.extract(card("Edge Ahead"), DB, P)]
    raw_w = [f for f in _dep("Rhinar, Reckless Rampage", deck=[{"name": "Wage Might", "color": "Red"}], arena=["Raw Meat"])
             if f["source"] == "Raw Meat" and f["target"] == "Might"]
    raw_a = [f for f in _dep("Rhinar, Reckless Rampage", deck=[{"name": "Wage Might", "color": "Red"}, {"name": "Alpha Instinct", "color": "Blue"}],
                             arena=["Raw Meat"]) if f["source"] == "Raw Meat" and f["target"] == "Might"]
    return [assertion("Wage Might («you may wager a Might token») ya no registra dependencia de un creador de Might",
                      not [r for r in wm if r["pattern"] == "N-TOKEN"], [(r["pattern"], r["requires"]) for r in wm]),
            assertion("Edge Ahead: se quita la falsa dependencia de Agility y se conserva T-NEXT «Warrior attack»",
                      ea == ["T-NEXT"], ea),
            assertion("como proveedora, la carta de wager es «ambiguous» (puede ganar el rival)",
                      DEPS.creator_scope("Might", card("Wage Might"))[0] == "ambiguous", None),
            assertion("Raw Meat + solo Wage Might → ambiguous_provider (producción posible, no garantizada)",
                      [f["status"] for f in raw_w] == ["ambiguous_provider"], raw_w),
            assertion("+ Alpha Instinct (propia) → satisfied; Wage Might queda como alternativa ambigua",
                      [(f["status"], f["providers"], f.get("providers_ambiguous")) for f in raw_a]
                      == [("satisfied", ["Alpha Instinct (Blue)"], ["Wage Might (Red)"])], raw_a)]


@case("N63", "política del generador", "Revisión humana pendiente: no vuelve ilegal una carta oficialmente legal; solo bloquea por política configurable")
def n63():
    u = _pilot("uzuri_cc_v1")
    d = copy.deepcopy(u)
    for e in d["deck"]:
        if (e["name"], e.get("color")) == ("Take Up the Mantle", "Yellow"):
            e["name"] = "Orb-Weaver Spinneret"
    ow = [c for c in DB.cards if c["name"] == "Orb-Weaver Spinneret" and c["color"] == "Yellow"][0]
    a = _pilot_check(d)
    b = _pilot_check(d, {"block_pending_review": False, "block_source_conflicts": False})
    lr, pr = PCK.split_reasons(PCK.gate_card(ow, "Uzuri, Switchblade", "deck"))
    return [assertion("Orb-Weaver Spinneret (Y): estado oficial legal (desbloqueo 2026-10-26) con revisión pendiente",
                      ow["legality"]["cc"]["status"] == "legal" and ow["legality"]["cc"]["review_status"] == "pending_human_review", None),
            assertion("compuerta: el motivo es de POLÍTICA, no de legalidad", lr == [] and pr != [], (lr, pr)),
            assertion("pilot_check por defecto: legalidad «válido», políticas «no cumple»",
                      a["resultado"]["legalidad"] == "válido" and a["resultado"]["politicas_generador"] == "no cumple", a["resultado"]),
            assertion("con block_pending_review=false y block_source_conflicts=false: políticas «cumple»",
                      b["resultado"]["legalidad"] == "válido" and b["resultado"]["politicas_generador"] == "cumple", b["resultado"]),
            assertion("negativo: una carta PROHIBIDA con revisión pendiente sigue siendo ilegal (motivo legal status=banned)",
                      any(x.startswith("status=banned") for x in PCK.split_reasons(PCK.gate_card(
                          [c for c in DB.cards if c["name"] == "Belittle"][0], "Uzuri, Switchblade", "deck"))[0]), None)]


@case("N64", "separación de resultados", "pilot_check: legalidad / políticas del generador / análisis de dependencias son independientes")
def n64():
    u = _pilot("uzuri_cc_v1")
    short = copy.deepcopy(u)
    short["inventory"] = []          # pool 65: legal por TRP 7.1, no cumple «exactamente 80»
    r1 = _pilot_check(short)
    r2 = _pilot_check(short, {"require_full_pool": False})
    rv1 = _pilot_check(_pilot("rhinar_cc_v1"))
    ill = copy.deepcopy(u)
    ill["deck"][0] = {"name": "Belittle", "color": "Red", "qty": 1}
    r3 = _pilot_check(ill)
    return [assertion("pool 65: legalidad «válido», políticas «no cumple» (pool), dependencias independientes",
                      r1["resultado"]["legalidad"] == "válido" and r1["resultado"]["politicas_generador"] == "no cumple"
                      and any("POOL" in m for m in r1["resultado"]["detalle"]["politicas_generador"]["motivos"]), r1["resultado"]["detalle"]["politicas_generador"]),
            assertion("pool 65 con require_full_pool=false: políticas «cumple»", r2["resultado"]["politicas_generador"] == "cumple",
                      r2["resultado"]),
            assertion("Rhinar v1 (Beat Chest sin habilitador): «problemas detectados» en dependencias, sin afectar a la legalidad",
                      rv1["resultado"]["analisis_dependencias"] == "problemas detectados"
                      and rv1["resultado"]["legalidad"] in ("válido", "inválido"), rv1["resultado"]),
            assertion("carta prohibida (Belittle): legalidad «inválido»", r3["resultado"]["legalidad"] == "inválido", r3["resultado"]),
            assertion("Gravy: dependencias «no concluyente» (Z crítica), legalidad «válido»: no concluyente ≠ ilegal",
                      _pilot_check(json.load(open(os.path.join(ROOT, "fase3", "generados", "gravy_cc_v1.deck.json"))))["resultado"]
                      == {**_pilot_check(json.load(open(os.path.join(ROOT, "fase3", "generados", "gravy_cc_v1.deck.json"))))["resultado"],
                          "legalidad": "válido", "analisis_dependencias": "no concluyente"}, None)]


def main():
    results, failed = [], 0
    for cid, group, desc, fn in CASES:
        try:
            runs = fn()
        except Exception as e:  # noqa: BLE001
            runs = [{"input": "-", "mode": "-", "expected": "sin excepción", "obtained": repr(e)[:300], "passed": False}]
        for r in runs:
            r.pop("_report", None)
        ok = all(r["passed"] for r in runs)
        failed += not ok
        print(("OK  " if ok else "FAIL"), cid, desc)
        results.append({"id": cid, "group": group, "description": desc, "passed": ok, "runs": runs})
    with open(os.path.join(HERE, "resultados_pruebas.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n{len(CASES) - failed}/{len(CASES)} casos pasaron")
    return failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
