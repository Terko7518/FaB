"""Taxonomía del juego tomada del Comprehensive Rules v2.15.1.

Todas las listas citan la regla de origen. NO se infiere nada fuera de estas listas:
un token desconocido se marca como `unclassified` y genera advertencia.
"""

CR_VERSION = "2.15.1"

# CR 2.11.6a
CLASSES = {
    "Adjudicator", "Assassin", "Bard", "Brute", "Guardian", "Illusionist",
    "Mechanologist", "Merchant", "Necromancer", "Ninja", "Pirate", "Ranger",
    "Runeblade", "Shapeshifter", "Thief", "Warrior", "Wizard",
}
# CR 2.11.6b
TALENTS = {
    "Chaos", "Draconic", "Earth", "Elemental", "Ice", "Light", "Lightning",
    "Mystic", "Revered", "Reviled", "Royal", "Shadow",
}
SUPERTYPES = CLASSES | TALENTS

# CR 2.15.6a (el repo escribe "Demi-Hero")
CARD_TYPES = {
    "Action", "Attack Reaction", "Block", "Companion", "Defense Reaction",
    "Demi-Hero", "Equipment", "Hero", "Instant", "Macro", "Mentor",
    "Resource", "Token", "Weapon",
}
# CR 1.3.2c
DECK_CARD_TYPES = {"Action", "Attack Reaction", "Block", "Defense Reaction",
                   "Instant", "Mentor", "Resource"}
# CR 1.3.2d (cualquier carta no-héroe, no-token y no deck-card)
ARENA_CARD_TYPES = {"Equipment", "Weapon", "Demi-Hero", "Companion"}

# CR 2.10.6a / 2.10.6b (el repo escribe "1H"/"2H")
FUNCTIONAL_SUBTYPES = {
    "1H", "2H", "Affliction", "Ally", "Arrow", "Ash", "Attack", "Aura",
    "Construct", "Figment", "Invocation", "Item", "Landmark", "Off-Hand", "Quiver",
}
NON_FUNCTIONAL_SUBTYPES = {
    "Angel", "Arms", "Axe", "Base", "Book", "Bow", "Brush", "Cannon", "Chest",
    "Chi", "Claw", "Club", "Cog", "Dagger", "Demon", "Dragon", "Evo", "Fiddle",
    "Flail", "Gem", "Gun", "Hammer", "Head", "Legs", "Log", "Lute", "Mercenary",
    "Orb", "Pistol", "Pit-Fighter", "Polearm", "Rock", "Scepter", "Scroll",
    "Scythe", "Shuriken", "Song", "Staff", "Sword", "Trap", "Wrench", "Young",
    "Zombie",
    # Presentes en type.json del repo pero aún no listados en CR 2.10.6b.
    # Se tratan como subtipos no funcionales (ver supuestos S-07).
    "Disease", "Trap",
}
SUBTYPES = FUNCTIONAL_SUBTYPES | NON_FUNCTIONAL_SUBTYPES

# Tokens de type.json que no son tipo/supertipo/subtipo del CR.
# CR 2.6.6: metatipos de héroe (moniker) o de set (nombre del set).
SET_METATYPES = {"Rosetta", "High Seas", "Omens of the Third Age"}
# Marcadores del repo que no son reglas del juego.
NON_GAME_MARKERS = {"Event", "Placeholder Card", "Generic"}

EQUIPMENT_ZONES = ("Head", "Chest", "Arms", "Legs")

RARITY_NAMES = {
    "C": "Common", "R": "Rare", "S": "Super Rare", "M": "Majestic",
    "L": "Legendary", "F": "Fabled", "T": "Token", "B": "Basic",
    "V": "Marvel", "P": "Promo",
}
# Silver Age: "common, rare o basic (antes token)". Ver supuesto S-03 sobre "S".
SILVER_AGE_RARITIES = {"C", "R", "B", "T"}

FORMATS = {
    "cc": {
        "name": "Classic Constructed", "hero": "adult",
        "deck_min": 60, "deck_max": None, "deck_exact": None,
        "pool_max": 80, "copies": 3,
    },
    "ll": {
        "name": "Living Legend", "hero": "adult",
        "deck_min": 60, "deck_max": None, "deck_exact": None,
        "pool_max": 80, "copies": 3,
    },
    "blitz": {
        "name": "Blitz", "hero": "young",
        "deck_min": 40, "deck_max": 40, "deck_exact": 40,
        "pool_max": 52, "copies": 1,
    },
    "silver_age": {
        "name": "Silver Age", "hero": "young",
        "deck_min": 40, "deck_max": 40, "deck_exact": 40,
        "pool_max": 55, "copies": 2,
    },
}
FORMAT_ALIASES = {
    "cc": "cc", "classic constructed": "cc", "classic": "cc",
    "blitz": "blitz",
    "silver age": "silver_age", "silver_age": "silver_age", "sa": "silver_age",
    "ll": "ll", "living legend": "ll", "living_legend": "ll",
}


def classify_types(types, type_text):
    """Reparte la lista plana `types` del repo en clases/talentos/tipos/subtipos.

    Las cartas híbridas ("A / B Action") se detectan por el type_text (CR 2.14.1b).
    Las split-cards ("X // Y") conservan la unión, como hace el repo.
    """
    out = {"classes": [], "talents": [], "card_types": [], "subtypes": [],
           "metatypes": [], "unclassified": []}
    for t in types:
        if t in CLASSES:
            out["classes"].append(t)
        elif t in TALENTS:
            out["talents"].append(t)
        elif t in CARD_TYPES:
            out["card_types"].append(t)
        elif t in SUBTYPES:
            out["subtypes"].append(t)
        elif t in SET_METATYPES:
            out["metatypes"].append(t)
        elif t in NON_GAME_MARKERS:
            continue
        else:
            # Puede ser un metatipo de héroe (CR 2.6.6) como "Arakni" o "Scurv";
            # sync.py lo confirma contra los monikers de héroes existentes.
            out["unclassified"].append(t)

    hybrid_sets = None
    head = type_text.split(" - ")[0]
    if " / " in head and " // " not in type_text:
        left, right = head.split(" / ", 1)
        left_set = [t for t in left.split() if t in SUPERTYPES]
        right_set = [t for t in right.split() if t in SUPERTYPES]
        hybrid_sets = [left_set, right_set]
    out["hybrid_supertype_sets"] = hybrid_sets
    out["is_generic"] = not out["classes"] and not out["talents"]
    return out
